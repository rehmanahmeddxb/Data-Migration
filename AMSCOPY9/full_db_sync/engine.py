"""Core full-database snapshot engine — pure stdlib (sqlite3 only).

Design notes
------------
* A **snapshot** (``.amsdb``) is a SQLite copy of a live AMS database plus one
  small metadata table (``__ams_full_db_meta__``).  It is produced with the
  SQLite online-backup API, so it is consistent even when the source database
  is in WAL mode (pending ``-wal`` transactions are included automatically).
* **Import** performs a *clean-and-copy*: every data row of the target is
  deleted first (schema, indexes and app-level seed/metadata stay), then every
  row of the source file is inserted with its original primary keys, so all
  foreign-key relationships stay internally consistent.  Users are copied too
  (full replace) — that is the documented behaviour of ``clean_replace``.
* The engine never guesses values and never needs pandas/openpyxl.  It is used
  by both the Flask UI routes and the command-line tools.
"""
from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, List, Optional, Sequence

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------
AMS_META_TABLE = "__ams_full_db_meta__"
SNAPSHOT_SPEC_VERSION = "1"
TOOL_NAME = "full_db_sync"
NON_DATA_TABLES = (AMS_META_TABLE,)

# Minimal "is this an AMS business database?" probe.  The AMS v4.4 schema
# always contains these three tables.
_AMS_PROBE_TABLES = ("user", "client", "entry")

_BATCH_SIZE = 1000


class FullDbSyncError(Exception):
    """Raised when a snapshot/import cannot be performed safely."""


# ---------------------------------------------------------------------------
# Small sqlite helpers
# ---------------------------------------------------------------------------
def _open(path: Path, mode: str = "ro", timeout: float = 120.0) -> sqlite3.Connection:
    """Open a sqlite connection.  ``mode`` in {ro, rw, rwc} via file: URI."""
    uri = f"file:{path.as_posix()}?mode={mode}"
    con = sqlite3.connect(uri, uri=True, timeout=timeout, isolation_level=None)
    con.execute(f"PRAGMA busy_timeout={int(timeout * 1000)}")
    return con


def _q(name: str) -> str:
    return '"' + name.replace('"', '""') + '"'


def list_user_tables(con: sqlite3.Connection) -> List[str]:
    """All user tables (sorted), excluding sqlite internals + sync metadata."""
    rows = con.execute(
        "SELECT name FROM sqlite_master "
        "WHERE type='table' AND name NOT LIKE 'sqlite_%' ORDER BY name"
    ).fetchall()
    return [r[0] for r in rows if r[0] not in NON_DATA_TABLES]


def table_columns(con: sqlite3.Connection, table: str) -> List[str]:
    return [r[1] for r in con.execute(f'PRAGMA table_info({_q(table)})')]


def table_rows(con_or_path, table: Optional[str] = None):
    """Row counts.  With a table name -> one int; else -> {table: count} dict."""
    owns = False
    if isinstance(con_or_path, (str, Path)):
        con = _open(Path(con_or_path))
        owns = True
    else:
        con = con_or_path
    try:
        if table is not None:
            row = con.execute(f'SELECT COUNT(*) FROM {_q(table)}').fetchone()
            return int(row[0]) if row else 0
        out: Dict[str, int] = {}
        for t in list_user_tables(con):
            out[t] = table_rows(con, t)
        return out
    finally:
        if owns:
            con.close()


def _integrity_detail(con: sqlite3.Connection):
    rows = [r[0] for r in con.execute("PRAGMA integrity_check")]
    ok = len(rows) == 1 and rows[0] == "ok"
    return ok, rows


def _fk_violations(con: sqlite3.Connection) -> List[tuple]:
    try:
        return con.execute("PRAGMA foreign_key_check").fetchall()
    except sqlite3.OperationalError:
        return []


def _looks_like_ams(con: sqlite3.Connection):
    tables = set(list_user_tables(con))
    missing = [t for t in _AMS_PROBE_TABLES if t not in tables]
    return (not missing), missing


def _read_meta(con: sqlite3.Connection) -> Optional[dict]:
    has = con.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (AMS_META_TABLE,)
    ).fetchone()
    if not has:
        return None
    try:
        rows = con.execute(f'SELECT key, value FROM {_q(AMS_META_TABLE)}').fetchall()
    except sqlite3.OperationalError:
        return None
    meta: Dict[str, object] = {}
    for key, value in rows:
        try:
            meta[key] = json.loads(value)
        except (TypeError, json.JSONDecodeError):
            meta[key] = value
    return meta or None


def _fk_dependency_order(con: sqlite3.Connection, tables: Sequence[str]) -> List[str]:
    """Parents-before-children topological order (best-effort)."""
    wanted = set(tables)
    if not wanted:
        return []
    children: Dict[str, set] = {t: set() for t in wanted}
    parents: Dict[str, set] = {t: set() for t in wanted}
    for t in sorted(wanted):
        for row in con.execute(f'PRAGMA foreign_key_list({_q(t)})'):
            parent = row[2]  # referenced table
            if parent in wanted and parent != t:
                children[parent].add(t)
                parents[t].add(parent)
    ordered: List[str] = []
    pending = sorted(t for t in wanted if not parents[t])
    remaining = {t: len(parents[t]) for t in wanted}
    while pending:
        t = pending.pop(0)
        ordered.append(t)
        for child in sorted(children[t]):
            remaining[child] -= 1
            if remaining[child] == 0:
                pending.append(child)
    if len(ordered) != len(wanted):  # cycle → deterministic alphabetical order
        return sorted(wanted)
    return ordered


def _delete_one(con: sqlite3.Connection, table: str) -> int:
    before = con.total_changes
    con.execute(f'DELETE FROM {_q(table)}')
    return con.total_changes - before


def _unique_index_plan(con_target: sqlite3.Connection, con_source: sqlite3.Connection,
                       tables: Sequence[str]):
    """Compare the target's UNIQUE indexes against the source's data.

    The target schema is *stricter* than a snapshot can be: an older AMS
    export (or a legacy migration) may legitimately contain duplicate values
    in a column the *current* app schema marks UNIQUE (the known example is
    ``entry.auto_bill_no`` in the legacy data).  To load such data without
    losing rows we relax — drop — exactly the unique indexes whose column set
    the source data violates, and keep every other unique index intact.

    Returns ``(drop_list, blocked_list, checked)``:

    * ``drop_list``   — names of UNIQUE indexes to DROP before loading;
    * ``blocked_list``— inline UNIQUE constraints (sqlite_autoindex_*, cannot
      be dropped) whose data would violate the constraint.  When non-empty the
      import must stop with an explanation.
    """
    drop_list: List[dict] = []
    blocked: List[dict] = []
    checked = 0
    for t in sorted(tables):
        index_rows = con_target.execute(f'PRAGMA index_list({_q(t)})').fetchall()
        for row in index_rows:
            name, unique, origin, partial = row[1], row[2], row[3], row[4]
            if not unique or origin == "pk":
                continue
            info = con_target.execute(f'PRAGMA index_info({_q(name)})').fetchall()
            # index_info rows are (seqno, cid, name); cid < 0 marks an
            # expression index which we never guess about.
            if any(r[1] < 0 or r[2] is None for r in info):
                continue
            cols = [r[2] for r in info]
            sql_row = con_target.execute(
                "SELECT sql FROM sqlite_master WHERE type='index' AND name=?",
                (name,),
            ).fetchone()
            predicate = "1"
            if partial and sql_row and sql_row[0] and " WHERE " in sql_row[0]:
                predicate = sql_row[0].split(" WHERE ", 1)[1]
            # SQLite UNIQUE treats NULL keys as distinct, so only non-NULL
            # key combinations can form a violation.
            guard = " AND ".join(f'{_q(c)} IS NOT NULL' for c in cols) or "1"
            group_cols = ", ".join(_q(c) for c in cols)
            q = (
                f"SELECT 1 FROM {_q(t)} WHERE ({predicate}) AND ({guard}) "
                f"GROUP BY {group_cols} HAVING COUNT(*) > 1 LIMIT 1"
            )
            try:
                hit = con_source.execute(q).fetchone()
            except sqlite3.OperationalError as e:
                # Predicate references a column the source lacks (different
                # schema generation) — column check will surface that later.
                continue
            checked += 1
            if not hit:
                continue
            entry = {
                "table": t,
                "name": name,
                "columns": cols,
                "origin": origin,
                "sql": sql_row[0] if sql_row else None,
            }
            if origin == "u":
                # Inline UNIQUE column constraint → created as sqlite_autoindex,
                # cannot be dropped without recreating the table.
                blocked.append(entry)
            else:
                drop_list.append(entry)
    return drop_list, blocked, checked


# ---------------------------------------------------------------------------
# Backup helper
# ---------------------------------------------------------------------------
def backup_database(db_path: Path, backup_dir=None,
                    prefix: str = "pre_") -> Optional[Path]:
    """Consistent online backup of *db_path*; returns path or None if absent."""
    if not db_path.exists() or db_path.stat().st_size == 0:
        return None
    directory = Path(backup_dir) if backup_dir else db_path.parent
    directory.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    out = directory / f"{prefix}{db_path.stem}_{stamp}.db"
    src = _open(db_path, mode="ro")
    dst = sqlite3.connect(str(out), timeout=60, isolation_level=None)
    try:
        src.backup(dst)
    finally:
        dst.close()
        src.close()
    return out


# ---------------------------------------------------------------------------
# Export (make a snapshot)
# ---------------------------------------------------------------------------
def export_snapshot(source_db_path, out_path=None, *, overwrite: bool = False,
                    meta_extra: Optional[dict] = None) -> dict:
    """Copy *source_db_path* into a portable ``.amsdb`` SQLite snapshot.

    Returns a report dict with ``ok``, ``out_path``, per-table row counts,
    export time and the integrity result.  The metadata table added to the
    snapshot is transport-only — imports ignore it.
    """
    src = Path(source_db_path).expanduser().resolve()
    if not src.exists():
        raise FullDbSyncError(f"Source database not found: {src}")
    if not out_path:
        out_path = src.parent / f"AMS_FULL_{datetime.now().strftime('%Y%m%d-%H%M%S')}.amsdb"
    out = Path(out_path).expanduser()
    if out.exists() and not overwrite:
        raise FullDbSyncError(
            f"Output snapshot already exists (use overwrite=True): {out}"
        )
    out.parent.mkdir(parents=True, exist_ok=True)
    if out.resolve() == src:
        raise FullDbSyncError("Output path must differ from the source database.")

    scon = _open(src, mode="ro")
    try:
        ok, rows = _integrity_detail(scon)
        if not ok:
            raise FullDbSyncError(f"Source integrity check failed: {rows[:5]}")
        counts = table_rows(scon)
    finally:
        scon.close()

    # Consistent byte-level copy via the sqlite backup API.
    scon = _open(src, mode="ro")
    dcon = sqlite3.connect(str(out), timeout=120, isolation_level=None)
    try:
        scon.backup(dcon)
    finally:
        dcon.close()
        scon.close()

    # Append self-describing metadata.
    meta = {
        "tool": TOOL_NAME,
        "spec_version": SNAPSHOT_SPEC_VERSION,
        "exported_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "source": src.name,
        "total_rows": sum(counts.values()),
        "tables": counts,
    }
    if meta_extra:
        meta.update(meta_extra)
    dcon = _open(out, mode="rw")
    try:
        dcon.execute(
            f"CREATE TABLE IF NOT EXISTS {_q(AMS_META_TABLE)} "
            "(key TEXT PRIMARY KEY, value TEXT NOT NULL)"
        )
        dcon.executemany(
            f"INSERT OR REPLACE INTO {_q(AMS_META_TABLE)} (key, value) VALUES (?, ?)",
            [(k, json.dumps(v)) for k, v in sorted(meta.items())],
        )
        # Seal the container as a single portable file: fold any WAL state back
        # into the main file and switch to DELETE journal mode so no -wal/-shm
        # sidecars are left behind next to the .amsdb.
        dcon.execute("PRAGMA wal_checkpoint(TRUNCATE)")
        dcon.execute("PRAGMA journal_mode=DELETE")
    finally:
        dcon.close()
    for side in (str(out) + "-wal", str(out) + "-shm", str(out) + "-journal"):
        try:
            Path(side).unlink(missing_ok=True)
        except OSError:
            pass

    v = verify_snapshot(out)
    if not v["ok"]:
        raise FullDbSyncError(f"Snapshot verification failed: {v.get('issues')}")
    return {
        "ok": True,
        "tool": TOOL_NAME,
        "spec_version": SNAPSHOT_SPEC_VERSION,
        "exported_at": meta["exported_at"],
        "source": src.name,
        "out_path": str(out),
        "bytes": out.stat().st_size,
        "tables": counts,
        "total_rows": sum(counts.values()),
        "integrity": "ok",
    }


# ---------------------------------------------------------------------------
# Verify
# ---------------------------------------------------------------------------
def verify_snapshot(db_path, expect_meta: bool = True) -> dict:
    """Read-only verification of a snapshot file (or any AMS database)."""
    path = Path(db_path).expanduser()
    if not path.exists():
        return {"ok": False, "issues": [f"File not found: {path}"]}
    con = _open(path, mode="ro")
    try:
        integrity_ok, integrity_rows = _integrity_detail(con)
        fk_violations = _fk_violations(con)
        ams_ok, ams_missing = _looks_like_ams(con)
        meta = _read_meta(con)
        counts = table_rows(con)
    finally:
        con.close()

    issues: List[str] = []
    if not integrity_ok:
        issues.append(f"integrity_check: {integrity_rows[:5]}")
    if fk_violations:
        issues.append(f"foreign_key_check: {len(fk_violations)} violations")
    if not ams_ok:
        issues.append(f"not an AMS database (missing tables: {ams_missing})")
    if meta and isinstance(meta.get("tables"), dict):
        diffs = []
        for t, expected in sorted(meta["tables"].items()):
            if counts.get(t) != expected:
                diffs.append(f"{t}: expected {expected}, found {counts.get(t)}")
        if diffs:
            issues.append(f"row counts differ from snapshot metadata: {diffs[:10]}")
    if expect_meta and not meta:
        issues.append("file has no __ams_full_db_meta__ table (not an AMS snapshot)")

    return {
        "ok": not issues,
        "issues": issues,
        "integrity": "ok" if integrity_ok else "FAIL",
        "fk_violations": len(fk_violations),
        "meta": meta,
        "meta_present": bool(meta),
        "looks_like_ams": ams_ok,
        "tables": counts,
        "total_rows": sum(counts.values()),
    }


# ---------------------------------------------------------------------------
# Clean (wipe all data, keep schema)
# ---------------------------------------------------------------------------
def clean_all_data(db_path, *, backup: bool = True, backup_dir=None,
                   backup_prefix: str = "pre_wipe_",
                   include_users: bool = True) -> dict:
    """Delete every data row from *db_path*; schema/indexes are preserved.

    This is the "clean all data first" step made available on its own.
    ``include_users=False`` keeps the ``user``/``user_login_session`` rows.
    """
    path = Path(db_path).expanduser()
    if not path.exists():
        raise FullDbSyncError(f"Target database not found: {path}")
    backup_path = None
    if backup:
        backup_path = backup_database(
            path, backup_dir=Path(backup_dir) if backup_dir else None,
            prefix=backup_prefix,
        )
    con = _open(path, mode="rw")
    try:
        tables = list_user_tables(con)
        if not include_users:
            tables = [t for t in tables if t not in ("user", "user_login_session")]
        order = _fk_dependency_order(con, tables)
        con.execute("PRAGMA foreign_keys=OFF")
        con.execute("BEGIN")
        deleted: Dict[str, int] = {}
        for t in reversed(order):  # children before parents
            deleted[t] = _delete_one(con, t)
        try:
            con.execute("DELETE FROM sqlite_sequence")
        except sqlite3.OperationalError:
            pass  # no AUTOINCREMENT tables
        con.execute("COMMIT")
        con.execute("PRAGMA foreign_keys=ON")
    except Exception:
        con.execute("ROLLBACK")
        con.execute("PRAGMA foreign_keys=ON")
        raise
    finally:
        con.close()
    return {
        "ok": True,
        "mode": "clean_all_data",
        "backup_path": str(backup_path) if backup_path else None,
        "deleted": deleted,
        "total_deleted": sum(deleted.values()),
        "cleaned_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
    }


# ---------------------------------------------------------------------------
# Import (clean-and-copy / append)
# ---------------------------------------------------------------------------
def import_snapshot(source_path, target_db_path, *, mode: str = "clean_replace",
                    backup_target: bool = True, backup_dir=None,
                    include_users: bool = True, require_sidecar: bool = True) -> dict:
    """Load the full data of *source_path* into *target_db_path*.

    ``mode="clean_replace"``  (default; the documented full sync)
        Every data row of the target is deleted first, then all rows of the
        source are inserted with their original primary keys.  Users are
        copied too (the target becomes an exact data copy of the source).
    ``mode="append"``
        Source rows are inserted, skipping any primary key that already
        exists in the target.

    The target schema is authoritative.  Tables present in the target but
    absent from the source stay empty; columns present in the target but
    absent from the source abort the import with a clear message (export a
    snapshot from the same schema generation instead).  An automatic backup
    of the target is always taken first unless ``backup_target=False``.

    ``require_sidecar`` (default True) — plain ``.db`` sources must carry the
    ``RESULT: PASS`` sidecar written by the AMS migration tool
    (``<name>.db.report.txt`` next to the file) so a quarantined
    ``*.INCOMPLETE`` run can never be imported by explicit path.  ``.amsdb``
    snapshots are self-verifying and exempt.  Pass ``require_sidecar=False``
    (CLI ``--allow-no-sidecar``) for automation that produces its own
    verification; the app's upload UI passes False because a browser upload
    is a single file by nature (it keeps its own verify + tamper detection).
    """
    src = Path(source_path).expanduser().resolve()
    tgt = Path(target_db_path).expanduser().resolve()
    if not src.exists():
        raise FullDbSyncError(f"Source snapshot not found: {src}")
    if not tgt.exists():
        raise FullDbSyncError(
            f"Target database not found: {tgt}. Start the AMS app once so the "
            "target exists with the current schema before importing into it."
        )
    if src == tgt:
        raise FullDbSyncError("Source and target are the same file — refusing.")

    # ---- Sidecar gate: a plain .db must have PROVEN itself (2026-09-10) ----
    # The migration tool writes <name>.db.report.txt with RESULT: PASS next to
    # its output, and renames aborted runs to *.INCOMPLETE.  Without this gate
    # an explicit path could import a partial/crashed file, because the
    # verification below is measured against the file itself.
    sidecar_state = "exempt (.amsdb snapshot)"
    if src.suffix.lower() != ".amsdb" and require_sidecar:
        sidecar = src.with_name(src.name + ".report.txt")
        if not sidecar.exists():
            raise FullDbSyncError(
                f"Refusing to import {src.name} without its migration report: "
                f"{sidecar.name} (containing 'RESULT: PASS') was not found next "
                "to it. Plain .db files are only imported with the sidecar the "
                "AMS migration tool writes — export an .amsdb snapshot instead, "
                "or pass --allow-no-sidecar if you verified this file yourself."
            )
        try:
            text = sidecar.read_text(encoding="utf-8", errors="replace")
        except OSError as exc:
            raise FullDbSyncError(f"Cannot read the sidecar report {sidecar}: {exc}")
        if "RESULT: PASS" not in text:
            raise FullDbSyncError(
                f"Sidecar report {sidecar.name} does not contain 'RESULT: PASS' "
                "(it reports a REVIEW/FAILED run). Do not import this file — "
                "read the report and fix the listed reasons, then re-run the "
                "migration."
            )
        sidecar_state = f"verified ({sidecar.name}: RESULT: PASS)"

    mode = (mode or "clean_replace").strip().lower()
    if mode not in ("clean_replace", "append"):
        raise FullDbSyncError(f"Unknown import mode: {mode}")

    # ---- Validate the source file ---------------------------------------
    scon = _open(src, mode="ro")
    try:
        ok, detail = _integrity_detail(scon)
        if not ok:
            raise FullDbSyncError(f"Source integrity check failed: {detail[:5]}")
        ams_ok, ams_missing = _looks_like_ams(scon)
        if not ams_ok:
            raise FullDbSyncError(
                f"Source is not an AMS database (missing tables: {ams_missing})"
            )
        source_counts = table_rows(scon)
        source_meta = _read_meta(scon)
    finally:
        scon.close()

    # ---- Automatic backup of the current target --------------------------
    backup_path = None
    if backup_target:
        backup_path = backup_database(
            tgt, backup_dir=Path(backup_dir) if backup_dir else None,
            prefix="pre_full_db_import_",
        )

    # ---- Target schema (authoritative) -----------------------------------
    tcon = _open(tgt, mode="rw")
    scon = _open(src, mode="ro")
    try:
        target_tables = list_user_tables(tcon)
        if not target_tables:
            raise FullDbSyncError(
                f"Target {tgt} has no tables — bootstrap the AMS schema first."
            )
        if not include_users:
            target_tables = [t for t in target_tables
                             if t not in ("user", "user_login_session")]
        source_set = set(source_counts) - (set() if include_users else
                                           {"user", "user_login_session"})
        shared = [t for t in target_tables if t in source_set]
        only_target = [t for t in target_tables if t not in source_set]
        only_source = sorted(source_set - set(target_tables))
        order = _fk_dependency_order(tcon, shared)

        # Column compatibility: abort if the target needs columns the source
        # does not carry (different schema generations).
        for t in order:
            tgt_cols = table_columns(tcon, t)
            src_cols = table_columns(scon, t)
            missing = [c for c in tgt_cols if c not in src_cols]
            if missing:
                raise FullDbSyncError(
                    f"Table '{t}' in the target needs columns the source lacks: "
                    f"{missing}. Export a fresh snapshot from the same AMS "
                    "version, or start the target app once so its schema "
                    "bootstrap adds the columns."
                )

        # ---- Unique-index relaxation -------------------------------------
        # Target UNIQUE indexes whose column set the source data violates are
        # dropped first so no source row is lost (documented legacy case:
        # duplicate entry.auto_bill_no).  Inline UNIQUE constraints cannot be
        # dropped — if the data would violate one, stop with an explanation.
        relaxed, blocked, ui_checked = _unique_index_plan(tcon, scon, order)
        if blocked:
            detail = "; ".join(
                f"{b['table']}.{b['name']} ({', '.join(b['columns'])})"
                for b in blocked
            )
            raise FullDbSyncError(
                "Source data violates inline UNIQUE constraints on the target "
                f"that cannot be relaxed automatically: {detail}. Rebuild the "
                "source without those duplicate values, or relax the "
                "constraints on the target manually."
            )
        for idx in relaxed:
            tcon.execute(f'DROP INDEX IF EXISTS {_q(idx["name"])}')

        # ---- Pass 1: clean (delete every row) -----------------------------
        deleted: Dict[str, int] = {}
        if mode == "clean_replace":
            wipe_tables = list_user_tables(tcon)
            if not include_users:
                wipe_tables = [t for t in wipe_tables
                               if t not in ("user", "user_login_session")]
            wipe_order = _fk_dependency_order(tcon, wipe_tables)
            tcon.execute("PRAGMA foreign_keys=OFF")
            tcon.execute("BEGIN")
            try:
                for t in reversed(wipe_order):
                    deleted[t] = _delete_one(tcon, t)
                try:
                    tcon.execute("DELETE FROM sqlite_sequence")
                except sqlite3.OperationalError:
                    pass
            except Exception:
                tcon.execute("ROLLBACK")
                raise
            else:
                tcon.execute("COMMIT")

        # ---- Pass 2: insert source rows with original ids ----------------
        inserted: Dict[str, int] = {}
        tcon.execute("PRAGMA foreign_keys=OFF")
        tcon.execute("BEGIN")
        try:
            for t in order:
                cols = table_columns(tcon, t)  # == src cols for shared tables
                col_sql = ", ".join(_q(c) for c in cols)
                marks = ", ".join("?" for _ in cols)
                ins_sql = f"INSERT INTO {_q(t)} ({col_sql}) VALUES ({marks})"
                sel_sql = f"SELECT {col_sql} FROM {_q(t)}"
                n = 0
                if mode == "clean_replace":
                    cur = scon.execute(sel_sql)
                else:
                    # Append: skip primary keys that already exist.
                    pk = [r[1] for r in tcon.execute(f'PRAGMA table_info({_q(t)})')
                          if r[5] > 0]
                    cur = scon.execute(sel_sql)
                    if pk:
                        existing = {tuple(r) for r in tcon.execute(
                            f'SELECT {", ".join(_q(c) for c in pk)} FROM {_q(t)}')}
                        pk_idx = [cols.index(c) for c in pk]
                        while True:
                            batch = cur.fetchmany(_BATCH_SIZE)
                            if not batch:
                                break
                            keep = [row for row in batch
                                    if tuple(row[i] for i in pk_idx) not in existing]
                            if keep:
                                tcon.executemany(ins_sql, keep)
                                n += len(keep)
                        continue
                while True:
                    batch = cur.fetchmany(_BATCH_SIZE)
                    if not batch:
                        break
                    tcon.executemany(ins_sql, batch)
                    n += len(batch)
                inserted[t] = n
        except Exception:
            tcon.execute("ROLLBACK")
            raise
        else:
            tcon.execute("COMMIT")
        tcon.execute("PRAGMA foreign_keys=ON")

        # ---- Verify -------------------------------------------------------
        integrity_ok, integrity_rows = _integrity_detail(tcon)
        fk_rows = _fk_violations(tcon)
        after_counts = table_rows(tcon)
        # Row-count parity against the source only applies to clean_replace
        # (append intentionally keeps pre-existing rows, so totals differ).
        count_mismatches = {}
        if mode == "clean_replace":
            count_mismatches = {
                t: {"source": source_counts.get(t), "target": after_counts.get(t)}
                for t in shared if source_counts.get(t) != after_counts.get(t)
            }
    finally:
        scon.close()
        tcon.close()

    return {
        "ok": (integrity_ok and not fk_rows and not count_mismatches),
        "mode": mode,
        "tool": TOOL_NAME,
        "imported_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "source_path": str(src),
        "target_path": str(tgt),
        "sidecar": sidecar_state,
        "backup_path": str(backup_path) if backup_path else None,
        "source_meta": source_meta,
        "tables_shared": len(shared),
        "tables_only_target": only_target,
        "tables_only_source": only_source,
        "rows_deleted": deleted,
        "rows_deleted_total": sum(deleted.values()),
        "rows_inserted": inserted,
        "rows_inserted_total": sum(inserted.values()),
        "integrity": "ok" if integrity_ok else "FAIL",
        "fk_violations": len(fk_rows),
        "count_mismatches": count_mismatches,
        "unique_indexes_checked": ui_checked,
        "unique_indexes_relaxed": [i["name"] for i in relaxed],
        "unique_indexes_blocked": [i["name"] for i in blocked],
        "verification": "PASS"
        if (integrity_ok and not fk_rows and not count_mismatches)
        else "FAIL",
    }
