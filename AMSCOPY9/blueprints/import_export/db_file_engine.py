"""SQLite .db import/export engine for AMS (replaces xlsx as the transfer format).

Why a .db file instead of a workbook:
  * exact schema, native types, original primary keys, real indexes;
  * no pandas/Excel row-type coercion, no 1,048,576-row sheet limit,
    no date/number mangling, no per-sheet column drift;
  * verification is native: ``PRAGMA integrity_check`` + row counts + FKs.

Two operations, implemented with pure ``sqlite3``:

``export_db_snapshot_bytes()``
    Consistent copy of the live DB via ``VACUUM INTO`` while the app keeps
    running.  Runtime-noise tables (login sessions, locks, migration rows)
    are emptied in the snapshot and provenance metadata is written to a
    ``db_transfer_meta`` table.  The returned bytes carry a magic header so
    the download is self-describing.

``run_db_snapshot_import(raw, mode, selected_tables=None)``
    mode='replace':  DELETE every row of every AMS table the snapshot carries
                     (the "clean all data" step), then replace those tables
                     with the snapshot's rows (original ids, DDL swap so
                     unique indexes are re-proven and relaxed only where the
                     data cannot satisfy them).  The acting admin's row is
                     re-added when missing so the session survives.
    mode='append':   copy rows whose primary key does not collide
                     (INSERT OR IGNORE semantics), never touching others.

Everything runs inside ONE transaction on a raw sqlite3 handle; on error the
transaction is rolled back so the live DB is untouched.  The ORM session is
expired afterwards so no request ever serves stale rows.
"""
from __future__ import annotations

import io
import json
import logging
import os
import re
import sqlite3
import uuid
from datetime import datetime, timezone

from ._common import db, current_app, current_user

LOG = logging.getLogger(__name__)

# Tables whose rows are runtime noise of the exporting instance — they are
# emptied from every snapshot (meta is rebuilt on import).
SNAPSHOT_EXCLUDE_TABLES = {
    "db_transfer_meta",
    "user_login_session",
    "tenant_wipe_backup_history",
    "system_lock",
    "migration_run",
    "migration_row",
    "migration_mapping",
}

# Tables that are NOT restored from a snapshot (local-instance state).
RESTORE_EXCLUDE_TABLES = SNAPSHOT_EXCLUDE_TABLES | {
    "import_upload",
    "import_job",
    "import_history_entry",
}

META_TABLE = "db_transfer_meta"
MAGIC = b"AMS_SQLITE_DB_TRANSFER_V1"
REQUIRED_CORE_TABLES = {"user", "client", "direct_sale", "entry"}


# ------------------------------------------------------------------ helpers
def _live_db_path() -> str | None:
    uri = current_app.config.get("SQLALCHEMY_DATABASE_URI") or ""
    if not str(uri).startswith("sqlite:///"):
        return None
    return str(uri)[len("sqlite:///"):]


def _table_list(con: sqlite3.Connection) -> list[str]:
    rows = con.execute(
        "SELECT name FROM sqlite_master WHERE type='table' "
        "AND name NOT LIKE 'sqlite_%' AND name NOT LIKE 'tmp_%' "
        "ORDER BY name").fetchall()
    return [r[0] for r in rows]


def _table_counts(con: sqlite3.Connection, tables) -> dict[str, int]:
    out = {}
    for t in tables:
        try:
            out[t] = int(con.execute(f'SELECT COUNT(*) FROM "{t}"').fetchone()[0])
        except sqlite3.Error:
            out[t] = 0
    return out


def _remove_temp_db(tmp: str) -> None:
    """Remove a temp SQLite file plus any -wal/-shm/-journal sidecars."""
    for suffix in ("", "-wal", "-shm", "-journal"):
        p = tmp + suffix
        try:
            if os.path.exists(p):
                os.remove(p)
        except OSError:
            pass


def _read_meta(con: sqlite3.Connection) -> dict:
    try:
        rows = con.execute(
            f'SELECT key, value FROM "{META_TABLE}"').fetchall()
        return dict(rows)
    except sqlite3.Error:
        return {}


def _write_meta(con: sqlite3.Connection, table_list: list[str]) -> None:
    con.execute(f'DROP TABLE IF EXISTS "{META_TABLE}"')
    con.execute(f'CREATE TABLE "{META_TABLE}" (key TEXT PRIMARY KEY, value TEXT)')
    kv = {
        "snapshot_id": uuid.uuid4().hex,
        "created_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "app": str(current_app.config.get("APP_NAME") or "AMS"),
        "schema_version": str(os.environ.get("AMS_SCHEMA_VERSION") or "v44"),
        "channel": "sqlite-db-file",
        "serializer": "vacuum-into-v1",
        "tables": str(len(table_list)),
        "table_list": json.dumps(table_list),
        "row_total": str(sum(_table_counts(con, table_list).values())),
        "notes": "AMS SQLite DB snapshot (VACUUM INTO). Not an xlsx file.",
    }
    con.executemany(
        f'INSERT OR REPLACE INTO "{META_TABLE}" (key, value) VALUES (?, ?)',
        [(k, v) for k, v in kv.items() if v is not None])


# ------------------------------------------------------------------ export
def export_db_snapshot_bytes() -> bytes:
    """Consistent snapshot of the live DB as self-describing bytes."""
    db_path = _live_db_path()
    if not db_path or not os.path.exists(db_path):
        raise RuntimeError("Live SQLite database file not found — export unavailable.")
    tmp = os.path.join(current_app.instance_path, ".tmp",
                       f"db_export_{uuid.uuid4().hex}.db")
    try:
        con = sqlite3.connect(db_path, timeout=30)
        try:
            con.execute("PRAGMA busy_timeout=30000")
            con.execute(f"VACUUM INTO '{tmp}'")
        finally:
            con.close()
        # Tidy the SNAPSHOT copy (never the live DB): empty runtime-noise
        # tables, then write provenance metadata.
        snap = sqlite3.connect(tmp, timeout=30)
        try:
            snap.execute("PRAGMA busy_timeout=30000")
            for t in SNAPSHOT_EXCLUDE_TABLES - {META_TABLE}:
                try:
                    snap.execute(f'DELETE FROM "{t}"')
                except sqlite3.Error:
                    pass
            keep = [t for t in _table_list(snap) if t != META_TABLE]
            _write_meta(snap, keep)
            snap.commit()
        finally:
            snap.close()
        with open(tmp, "rb") as f:
            payload = f.read()
    finally:
        _remove_temp_db(tmp)
    return MAGIC + payload


# ------------------------------------------------------------------ import
def _normalize_db_bytes(raw: bytes) -> bytes:
    """Return the bare SQLite payload of an upload.

    Accepts a raw .db payload, our MAGIC-prefixed download, or a zip/tgz
    container holding exactly one .db file.
    """
    if raw is None or not raw:
        raise ValueError("Empty upload.")
    if raw.startswith(MAGIC):
        return raw[len(MAGIC):]
    if raw[:2] == b"PK":  # zip container
        import zipfile
        with zipfile.ZipFile(io.BytesIO(raw)) as zf:
            dbs = [n for n in zf.namelist()
                   if not n.endswith("/") and n.lower().endswith(".db")]
            if len(dbs) != 1:
                raise ValueError(
                    f"ZIP must contain exactly one .db file (found {len(dbs)}).")
            return zf.read(dbs[0])
    if raw[:2] == b"\x1f\x8b":  # gzip/tgz container
        import tarfile
        with tarfile.open(fileobj=io.BytesIO(raw), mode="r:gz") as tf:
            dbs = [m for m in tf.getmembers()
                   if m.isfile() and m.name.lower().endswith(".db")]
            if len(dbs) != 1:
                raise ValueError(
                    f"TGZ must contain exactly one .db file (found {len(dbs)}).")
            f = tf.extractfile(dbs[0])
            return f.read() if f else b""
    if raw[:16] != b"SQLite format 3\x00":
        raise ValueError(
            "Uploaded file is not an AMS SQLite database snapshot "
            "(expected a .db file, an AMS .db export download, or a zip/tgz of one).")
    return raw


def _open_upload(payload: bytes):
    """Persist the payload to a temp file; return (read-only conn, tmp path)."""
    db_path = _live_db_path()
    if not db_path or not os.path.exists(db_path):
        raise RuntimeError("Live SQLite database file not found.")
    tmp = os.path.join(current_app.instance_path, ".tmp",
                       f"db_upload_{uuid.uuid4().hex}.db")
    with open(tmp, "wb") as f:
        f.write(payload)
    try:
        con = sqlite3.connect(f"file:{tmp}?mode=ro", uri=True)
        con.execute("PRAGMA busy_timeout=30000")
        return con, tmp
    except Exception:
        _remove_temp_db(tmp)
        raise


def _validate_snapshot(con: sqlite3.Connection, meta: dict, *, require_meta: bool):
    """Structural validation; raises ValueError with a human message."""
    if require_meta:
        if not meta:
            raise ValueError(
                "This .db file has no AMS transfer metadata — it is not an "
                "AMS database export.")
        if meta.get("channel") != "sqlite-db-file":
            raise ValueError("This file was not produced by the AMS DB-file exporter.")
        if meta.get("schema_version") and meta.get("schema_version") != "v44":
            raise ValueError(
                f"Snapshot schema version '{meta.get('schema_version')}' is not "
                "supported by this app build (v44).")
    tables = _table_list(con)
    if not REQUIRED_CORE_TABLES.issubset(tables):
        raise ValueError(
            "Snapshot is missing core AMS tables: "
            f"{sorted(REQUIRED_CORE_TABLES - set(tables))}.")
    if con.execute("PRAGMA integrity_check").fetchone()[0] != "ok":
        raise ValueError("Snapshot fails SQLite integrity_check — the file is corrupt.")


def _strip_secondary_unique(create_sql: str, table: str, tmp_name: str) -> str:
    """Same DDL minus secondary UNIQUEs (PRIMARY KEY kept) for the tmp table,
    so source-data unique violations never block the row load.  Unique
    indexes are re-created afterwards and relaxed only where needed."""
    s = create_sql
    s = re.sub(r',\s*CONSTRAINT\s+\w+\s+UNIQUE\s*\([^)]*\)', ' ', s, flags=re.I)
    s = re.sub(r'\bUNIQUE\s*\([^)]*\)', ' ', s, flags=re.I)
    s = re.sub(r'\bUNIQUE\b', ' ', s, flags=re.I)
    s = re.sub(r'\s*,\s*,+', ',', s)
    s = re.sub(r',\s*\)', ')', s)
    s = re.sub(r'\s+', ' ', s)
    s = s.replace(f'CREATE TABLE "{table}" (', f'CREATE TABLE "{tmp_name}" (', 1) \
        if f'CREATE TABLE "{table}" (' in s else \
        re.sub(r'CREATE\s+TABLE\s+(?:IF\s+NOT\s+EXISTS\s+)?[`"]?' + re.escape(table)
               + r'[`"]?', f'CREATE TABLE "{tmp_name}"', s, count=1, flags=re.I)
    return s


def _recreate_upload_indexes(live: sqlite3.Connection, upload: sqlite3.Connection,
                             table: str) -> tuple[int, list[str]]:
    """Recreate on `live` the indexes `upload` has for `table`.

    Returns (created_count, relaxed_errors).  Indexes that already exist are
    skipped (not relaxed); a unique index the data cannot satisfy is relaxed —
    the row data is never sacrificed for it.
    """
    existing = {r[0] for r in live.execute("SELECT name FROM sqlite_master "
                                           "WHERE type='index'")}
    created, relaxed = 0, []
    for (name, sql) in upload.execute(
            "SELECT name, sql FROM sqlite_master WHERE type='index' "
            "AND tbl_name=? AND sql IS NOT NULL", (table,)).fetchall():
        if name in existing:
            continue
        try:
            live.execute(sql)
            created += 1
        except sqlite3.Error as e:
            relaxed.append(f"{table}: {str(e)[:90]}")
    return created, relaxed


def _replace_table_with_upload(live: sqlite3.Connection, upload: sqlite3.Connection,
                               table: str) -> dict:
    """Replace `table` on live with the upload's rows (original ids).

    Proven DDL-swap method (same as the data-lab migrate/load tools):
    stage into a constraint-stripped twin, drop the live table, rename, then
    recreate the upload's indexes — relaxing only those the data violates.
    """
    ddl = upload.execute(
        "SELECT sql FROM sqlite_master WHERE type='table' AND name=?",
        (table,)).fetchone()
    if not ddl or not ddl[0]:
        raise RuntimeError(f"Snapshot has no CREATE TABLE for '{table}'.")
    src_cols = [r[1] for r in upload.execute(f'PRAGMA table_info("{table}")')]
    live_cols = {r[1] for r in live.execute(f'PRAGMA table_info("{table}")')}
    missing = [c for c in src_cols if c not in live_cols]
    if missing:
        raise RuntimeError(
            f"table '{table}': snapshot columns missing from this app: {missing}.")
    col_csv = ", ".join(f'"{c}"' for c in src_cols)
    src_total = int(upload.execute(f'SELECT COUNT(*) FROM "{table}"').fetchone()[0])

    tmp = "tmp_" + table
    live.execute(f'DROP TABLE IF EXISTS "{tmp}"')
    live.execute(_strip_secondary_unique(ddl[0], table, tmp))
    live.execute(
        f'INSERT INTO "{tmp}" ({col_csv}) SELECT {col_csv} FROM "upload"."{table}"')
    live.execute(f'DROP TABLE IF EXISTS "{table}"')
    live.execute(f'ALTER TABLE "{tmp}" RENAME TO "{table}"')
    recreated, relaxed = _recreate_upload_indexes(live, upload, table)
    return {
        "table": table,
        "source_rows": src_total,
        "inserted": src_total,
        "indexes_recreated": recreated,
        "relaxed": relaxed,
    }


def _ensure_actor_row(live: sqlite3.Connection, upload: sqlite3.Connection,
                      report: dict) -> None:
    """After a replace-restore, guarantee the acting admin still exists.

    Snapshot users are authoritative, but the person running the restore must
    keep being able to log in — if their row is not in the snapshot it is
    re-added (original id kept when free).
    """
    actor_id = int(getattr(current_user, "id", 0) or 0)
    actor_name = (getattr(current_user, "username", None) or "").strip() or "Admin"
    if live.execute(
            'SELECT 1 FROM "user" WHERE lower(username) = lower(?) LIMIT 1',
            (actor_name,)).fetchone():
        return
    src = upload.execute(
        'SELECT * FROM "user" WHERE lower(username) = lower(?) LIMIT 1',
        (actor_name,)).fetchone()
    if src is None:
        src = upload.execute('SELECT * FROM "user" ORDER BY id LIMIT 1').fetchone()
    if src is None:
        report["errors"].append(
            "Snapshot has no users at all; restore finished without an "
            "administrator. Run the app bootstrap or re-import a full snapshot.")
        return
    cols = [d[0] for d in upload.execute('PRAGMA table_info("user")')]
    vals = list(src)
    if actor_id and not live.execute(
            'SELECT 1 FROM "user" WHERE id = ?', (actor_id,)).fetchone():
        vals[cols.index("id")] = actor_id
    col_csv = ", ".join(f'"{c}"' for c in cols)
    live.execute(
        f'INSERT INTO "user" ({col_csv}) VALUES ({", ".join("?" for _ in cols)})',
        vals)
    report["note_actor"] = (
        f"Acting user '{actor_name}' was not in the snapshot and was added "
        "back so login keeps working.")


def _ams_table_names() -> set[str]:
    try:
        return {t.name for t in db.metadata.sorted_tables}
    except Exception:
        return set()


# ------------------------------------------------------------------ public
def preview_db_snapshot(raw: bytes) -> dict:
    """Validate an upload and summarise it (no writes)."""
    payload = _normalize_db_bytes(raw)
    db_path = _live_db_path()
    if not db_path or not os.path.exists(db_path):
        raise RuntimeError("Live SQLite database file not found.")
    con, tmp = _open_upload(payload)
    try:
        meta = _read_meta(con)
        _validate_snapshot(con, meta, require_meta=False)
        ams = _ams_table_names()
        uploadable = [t for t in _table_list(con) if t in ams]
        counts = _table_counts(con, uploadable)
        return {
            "ok": True,
            "is_ams_export": bool(meta and meta.get("channel") == "sqlite-db-file"),
            "meta": meta,
            "tables": counts,
            "row_total": sum(counts.values()),
            "incompatible": [t for t in uploadable if t not in
                             set(_table_list(sqlite3.connect(
                                 f"file:{db_path}?mode=ro", uri=True)))],
        }
    finally:
        con.close()
        _remove_temp_db(tmp)


def run_db_snapshot_import(raw: bytes, mode: str, *, selected_tables=None) -> dict:
    """Execute a restore; returns a per-table report dict.

    One transaction on a raw sqlite3 handle; on error the transaction is
    rolled back and the live DB is untouched.  The ORM session is expired at
    the end so pooled connections never serve pre-restore rows.
    """
    payload = _normalize_db_bytes(raw)
    mode = "replace" if str(mode or "").strip().lower() in \
        ("replace", "replace_tenant_data") else "append"
    db_path = _live_db_path()
    if not db_path or not os.path.exists(db_path):
        raise RuntimeError("Live SQLite database file not found.")
    try:
        db.session.rollback()
    except Exception:
        pass

    live = sqlite3.connect(db_path, timeout=60)
    live.execute("PRAGMA busy_timeout=60000")
    live.execute("PRAGMA foreign_keys=0")
    upload, tmp = _open_upload(payload)
    live.execute("ATTACH DATABASE ? AS upload", (tmp,))
    report = {
        "mode": mode,
        "ok": False,
        "inserted": 0,
        "skipped": 0,
        "tables": [],
        "relaxed": [],
        "errors": [],
        "note_actor": None,
        "restored_as_generic": False,
    }
    try:
        meta = _read_meta(upload)
        # AMS exports carry provenance meta; raw .db files (e.g. copies made
        # by the data-lab pipeline) are accepted too — with stricter checks.
        _validate_snapshot(upload, meta, require_meta=False)
        report["restored_as_generic"] = not bool(meta)
        ams = _ams_table_names()
        upload_tables = [t for t in _table_list(upload)
                         if t in ams and t not in RESTORE_EXCLUDE_TABLES]
        if selected_tables:
            wanted = set(selected_tables)
            upload_tables = [t for t in upload_tables if t in wanted]
        if not upload_tables:
            raise ValueError("The snapshot contains no restorable AMS tables.")
        carry = []
        for t in upload_tables:
            up_cols = {r[1] for r in upload.execute(f'PRAGMA table_info("{t}")')}
            live_cols = {r[1] for r in live.execute(f'PRAGMA table_info("{t}")')}
            if not up_cols.issubset(live_cols):
                report["errors"].append(
                    f"table {t}: snapshot has columns missing from this app "
                    f"({sorted(up_cols - live_cols)}); table skipped")
                continue
            if not meta and up_cols != live_cols:
                # Raw file without AMS metadata: require an exact schema
                # match so a different app build cannot sneak in.
                report["errors"].append(
                    f"table {t}: raw .db (no AMS metadata) must match this "
                    f"app's schema exactly; table skipped")
                continue
            carry.append(t)
        if not carry:
            raise ValueError("No compatible tables to restore (see errors).")

        live.execute("BEGIN")
        if mode == "replace":
            # ---- CLEAN: delete every row of every carried AMS table ----
            for t in carry:
                live.execute(f'DELETE FROM "{t}"')
            # ---- LOAD: copy snapshot rows (original ids) ----
            # user goes last so the actor safeguard can run against the
            # final user table content.
            order = sorted((t for t in carry if t != "user"), key=lambda x: x)
            for t in order:
                res = _replace_table_with_upload(live, upload, t)
                report["tables"].append(res)
                report["inserted"] += res["inserted"]
                report["relaxed"].extend(res["relaxed"])
            if "user" in carry:
                res = _replace_table_with_upload(live, upload, "user")
                report["tables"].append(res)
                report["inserted"] += res["inserted"]
                report["relaxed"].extend(res["relaxed"])
                _ensure_actor_row(live, upload, report)
        else:
            # ---- APPEND: insert rows whose PK does not collide ----
            for t in sorted(carry):
                cols = [r[1] for r in upload.execute(f'PRAGMA table_info("{t}")')]
                live_cols = {r[1] for r in live.execute(f'PRAGMA table_info("{t}")')}
                col_csv = ", ".join(f'"{c}"' for c in cols if c in live_cols)
                src_total = int(upload.execute(
                    f'SELECT COUNT(*) FROM "{t}"').fetchone()[0])
                before = int(live.execute(f'SELECT COUNT(*) FROM "{t}"').fetchone()[0])
                live.execute(
                    f'INSERT OR IGNORE INTO "{t}" ({col_csv}) '
                    f'SELECT {col_csv} FROM "upload"."{t}"')
                after = int(live.execute(f'SELECT COUNT(*) FROM "{t}"').fetchone()[0])
                inserted = after - before
                skipped = max(src_total - inserted, 0)
                recreated, relaxed = _recreate_upload_indexes(live, upload, t)
                report["tables"].append({
                    "table": t, "source_rows": src_total,
                    "inserted": inserted, "skipped": skipped,
                    "indexes_recreated": recreated, "relaxed": relaxed,
                })
                report["inserted"] += inserted
                report["skipped"] += skipped
                report["relaxed"].extend(relaxed)
        live.execute("COMMIT")
        report["ok"] = not report["errors"]
    except Exception:
        try:
            live.execute("ROLLBACK")
        except sqlite3.Error:
            pass
        raise
    finally:
        try:
            live.close()  # close() detaches any attached schema
        except sqlite3.Error:
            pass
        try:
            upload.close()
        except sqlite3.Error:
            pass
        _remove_temp_db(tmp)
        try:
            db.session.expire_all()
        except Exception:
            pass
    return report
