#!/usr/bin/env python3
"""
migrate_engine.py — core migration logic for the AMS Migration Tool.

Takes an OLD database file (legacy/older AMS schema, e.g. ahmed_cement.db) and a
NEW database file (fresh v4.4 template with the correct schema), copies the NEW
file as the output base, *empties its seeded business data*, then loads EVERY
old row into it with original ids — merging users, relaxing exactly the unique
indexes the old data violates, and verifying with integrity / FK / parity /
duplicate checks so no row is lost, dropped or left behind.

Pure stdlib (sqlite3 only). No third-party packages, no Flask. Source files are
never modified: the old file is read through a consistent staging copy (so a
WAL-mode source with a pending -wal file is captured completely) and the output
is a brand-new file.
"""
from __future__ import annotations

import datetime
import json
import os
import re
import shutil
import sqlite3
import sys
import tempfile
import time
from pathlib import Path

# ---------------------------------------------------------------------------
# Constants (same semantics as the proven FIRST CLASS DATA/migrate.py)
# ---------------------------------------------------------------------------

# Tables whose data is KEPT from the NEW template instead of replaced by old
# rows (old has no meaningful rows in them; migration-run infra + pure config).
# user is merged specially below.
KEEP_FROM_NEW = {
    "migration_run", "migration_mapping", "migration_row",
    "settings", "root_backup_email_history", "root_backup_settings",
    "root_recovery_code", "system_lock", "schema_version",
    "tenant_wipe_backup_history", "staff_email",
    "import_history_entry", "import_job", "import_upload",
}

# New-only seed tables that reference replaced tables — cleared (no FK orphans).
DELETE_SEED = {"cash_day_account_position", "cash_day_lock"}

# Columns that hold user ids (old Django schema defines no DB-level FKs).
USER_REF_COLS = {"user_id", "created_by_id"}

AMS_PROBE = ("user", "client", "entry")  # minimal AMS business-database probe


class MigrationError(Exception):
    """Raised when migration cannot run safely (input problems)."""


# ---------------------------------------------------------------------------
# Small helpers
# ---------------------------------------------------------------------------

def _q(name: str) -> str:
    return '"' + str(name).replace('"', '""') + '"'


def _open_ro(path) -> sqlite3.Connection:
    uri = "file:" + Path(path).as_posix() + "?mode=ro"
    return sqlite3.connect(uri, uri=True, timeout=120)


def _now() -> str:
    return datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def _user_tables(con: sqlite3.Connection) -> list:
    return sorted(
        r[0]
        for r in con.execute(
            "SELECT name FROM sqlite_master WHERE type='table' "
            "AND name NOT LIKE 'sqlite_%' AND name NOT LIKE '__ams%'"
        )
    )


def _table_cols(con: sqlite3.Connection, t: str) -> list:
    return [r[1] for r in con.execute(f'PRAGMA table_info({_q(t)})')]


def _integrity_ok(con: sqlite3.Connection) -> tuple:
    rows = [r[0] for r in con.execute("PRAGMA integrity_check")]
    return rows == ["ok"], rows[:5]


def _fk_violations(con: sqlite3.Connection) -> int:
    try:
        return len(con.execute("PRAGMA foreign_key_check").fetchall())
    except sqlite3.OperationalError:
        return 0


# ---------------------------------------------------------------------------
# Inspection / classification (used by the GUI's auto-detect)
# ---------------------------------------------------------------------------

V44_MARKERS = (
    # (table, column) pairs that exist only in the v4.4 schema.
    ("user", "access_mode"),
    ("account", "class_category"),
    ("account_transaction", "idempotency_key"),
)


def inspect_database(path) -> dict:
    """Read-only info about a database file (for auto-detection + validation)."""
    path = Path(path)
    out = {
        "path": str(path),
        "name": path.name,
        "exists": path.exists(),
        "tables": [],
        "counts": {},
        "integrity": None,
        "fk_violations": None,
        "journal_mode": None,
        "is_sqlite": False,
        "is_ams": False,
        "v44_markers": 0,
        "looks_v44": False,
        "total_rows": None,
    }
    if not path.exists() or path.stat().st_size == 0:
        return out
    try:
        con = sqlite3.connect(f"file:{path.as_posix()}?mode=ro", uri=True)
        con.execute("SELECT 1 FROM sqlite_master").fetchone()
    except sqlite3.DatabaseError:
        return out
    con = sqlite3.connect(f"file:{path.as_posix()}?mode=ro", uri=True)
    try:
        tables = _user_tables(con)
        out["tables"] = tables
        out["counts"] = {
            t: con.execute(f'SELECT COUNT(*) FROM {_q(t)}').fetchone()[0]
            for t in tables
        }
        out["total_rows"] = sum(out["counts"].values())
        ok, _ = _integrity_ok(con)
        out["integrity"] = "ok" if ok else "FAIL"
        out["fk_violations"] = _fk_violations(con)
        out["journal_mode"] = con.execute("PRAGMA journal_mode").fetchone()[0]
        out["is_sqlite"] = True
        missing = [t for t in AMS_PROBE if t not in tables]
        out["is_ams"] = not missing
        marks = 0
        for t, c in V44_MARKERS:
            if t in tables and c in _table_cols(con, t):
                marks += 1
        out["v44_markers"] = marks
        out["looks_v44"] = marks >= 2 or "cash_day_account_position" in tables
    except sqlite3.DatabaseError as e:
        out["error"] = str(e)
    finally:
        con.close()
    return out


def classify(old_path, new_path) -> dict:
    """Compare two files and warn if the selection looks reversed/incompatible."""
    a = inspect_database(old_path)
    b = inspect_database(new_path)
    warnings = []
    if not a["is_ams"]:
        warnings.append(f"Old file does not look like an AMS database: {a['name']}")
    if not b["is_ams"]:
        warnings.append(f"New file does not look like an AMS database: {b['name']}")
    if a["is_ams"] and b["is_ams"]:
        if a["looks_v44"] and not b["looks_v44"]:
            warnings.append(
                "It looks like OLD and NEW are swapped: the 'old' file has v4.4 "
                "schema markers and the 'new' file does not."
            )
        old_only = [t for t in a["tables"] if t not in b["tables"]]
        nonempty_old_only = [
            t for t in old_only if (a["counts"].get(t) or 0) > 0
        ]
        if nonempty_old_only:
            warnings.append(
                "The old file has tables the new schema does not have, with data: "
                + ", ".join(sorted(nonempty_old_only)[:6])
                + " — those rows cannot be carried into the new schema."
            )
        if not a["looks_v44"] and not b["looks_v44"]:
            warnings.append(
                "Neither file has v4.4 schema markers — the 'new' file should be "
                "a fresh v4.4 AMS database (e.g. NewData/ahmed_cement_v44_fresh.db)."
            )
    return {"a": a, "b": b, "warnings": warnings}


def default_out_path(new_path: Path, old_path: Path) -> Path:
    """Default output: <folder-of-new>/<old-stem>_migrated.db"""
    out_dir = new_path.parent if new_path.parent.exists() else Path.cwd()
    return out_dir / f"{old_path.stem}_migrated.db"


# ---------------------------------------------------------------------------
# The migration itself
# ---------------------------------------------------------------------------

def _consistent_copy(src: Path, dst: Path) -> None:
    """Byte-consistent copy via the sqlite backup API (includes WAL content)."""
    s = _open_ro(src)
    d = sqlite3.connect(str(dst), timeout=120)
    try:
        s.backup(d)
    finally:
        d.close()
        s.close()


def _strip_secondary_unique(create_sql: str, t: str, tmp_name: str) -> str:
    """CREATE TABLE for tmp_name identical to t but without secondary UNIQUE
    constraints (PK kept). Inline-UNIQUE auto-indexes cannot be dropped later,
    so they are stripped at DDL time; normal UNIQUE indexes are recreated after
    the swap and only relaxed when the old data genuinely violates them."""
    s = create_sql
    s = re.sub(r",\s*CONSTRAINT\s+\w+\s+UNIQUE\s*\([^)]*\)", " ", s, flags=re.I)
    s = re.sub(r"\bUNIQUE\s*\([^)]*\)", " ", s, flags=re.I)
    s = re.sub(r"\bUNIQUE\b", " ", s, flags=re.I)
    s = re.sub(r"\s*,\s*,+", ",", s)
    s = re.sub(r",\s*\)", ")", s)
    s = re.sub(r"\s+", " ", s)
    if f'CREATE TABLE "{t}" (' in s:
        s = s.replace(f'CREATE TABLE "{t}" (', f'CREATE TABLE "{tmp_name}" (', 1)
    else:
        s = re.sub(
            r"CREATE\s+TABLE\s+(?:IF\s+NOT\s+EXISTS\s+)?[`\"]?" + re.escape(t) + r"[`\"]?",
            f'CREATE TABLE "{tmp_name}"',
            s,
            count=1,
            flags=re.I,
        )
    return s


def _swap_table(con: sqlite3.Connection, t: str) -> tuple:
    """Replace table t (schema + all rows) with every old row, ids preserved.

    Returns (seed_count, loaded_count, recreated_indexes, relaxed_indexes,
    skipped_extra_columns).
    """
    out_cols = _table_cols(con, t)
    # real old columns come from the attached 'old' schema:
    old_cols = [r[1] for r in con.execute(f'PRAGMA old.table_info({_q(t)})')]
    inter = [c for c in old_cols if c in out_cols]
    extra = [c for c in old_cols if c not in out_cols]
    skipped = []
    if extra:
        for c in extra:
            n = con.execute(
                f'SELECT COUNT(*) FROM old.{_q(t)} WHERE {_q(c)} IS NOT NULL'
            ).fetchone()[0]
            if n:
                skipped.append((c, n))
    seed_before = con.execute(f'SELECT COUNT(*) FROM {_q(t)}').fetchone()[0]
    orig_sql = con.execute(
        "SELECT sql FROM sqlite_master WHERE type='table' AND name=?", (t,)
    ).fetchone()[0]
    # Capture the table's unique indexes BEFORE the swap: dropping the table
    # drops its indexes too, so they are re-created afterwards from this list.
    saved_unique = []
    for row in con.execute(f'PRAGMA index_list({_q(t)})').fetchall():
        name, uniq, origin = row[1], row[2], row[3]
        if not uniq or origin == "pk":
            continue
        sql_row = con.execute(
            "SELECT sql FROM sqlite_master WHERE type='index' AND name=?",
            (name,),
        ).fetchone()
        saved_unique.append((name, sql_row[0] if sql_row else None, origin))
    tmp = "tmp_" + t
    con.execute(f'DROP TABLE IF EXISTS {_q(tmp)}')
    con.execute(_strip_secondary_unique(orig_sql, t, tmp))
    if inter:
        col_csv = ", ".join(_q(c) for c in inter)
        con.execute(
            f'INSERT INTO {_q(tmp)} ({col_csv}) SELECT {col_csv} FROM old.{_q(t)}'
        )
    n = con.execute(f'SELECT COUNT(*) FROM {_q(tmp)}').fetchone()[0]
    con.execute(f'DROP TABLE IF EXISTS {_q(t)}')
    con.execute(f'ALTER TABLE {_q(tmp)} RENAME TO {_q(t)}')

    recreated, relaxed = [], []
    for name, index_sql, origin in saved_unique:
        if not index_sql:
            continue  # autoindex of an inline unique — stripped from DDL
        try:
            con.execute(index_sql)
            recreated.append(name)
        except (sqlite3.OperationalError, sqlite3.IntegrityError) as e:
            relaxed.append(f"{name} ({str(e)[:80]})")
    return seed_before, n, recreated, relaxed, skipped


def _reset_auto_increment(con: sqlite3.Connection) -> None:
    """Keep sqlite_sequence in sync with the largest loaded ids so the app can
    keep inserting without primary-key collisions after the migration."""
    try:
        con.execute("DELETE FROM sqlite_sequence")
    except sqlite3.OperationalError:
        return
    for (t, sql) in con.execute(
        "SELECT name, sql FROM sqlite_master WHERE type='table' "
        "AND sql LIKE '%AUTOINCREMENT%'"
    ):
        cols = _table_cols(con, t)
        if "id" not in cols:
            continue
        try:
            max_id = con.execute(f'SELECT COALESCE(MAX(id),0) FROM {_q(t)}').fetchone()[0]
            con.execute(
                f'INSERT OR REPLACE INTO sqlite_sequence (name, seq) VALUES (?, ?)',
                (t, max_id),
            )
        except sqlite3.OperationalError:
            pass


def _duplicate_scan(con: sqlite3.Connection) -> list:
    """Duplicate values in every remaining UNIQUE index (final proof)."""
    found = []
    for t in _user_tables(con):
        for row in con.execute(f'PRAGMA index_list({_q(t)})').fetchall():
            name, uniq, origin = row[1], row[2], row[3]
            if not uniq or origin == "pk":
                continue
            info = con.execute(f'PRAGMA index_info({_q(name)})').fetchall()
            if any(r[1] < 0 or r[2] is None for r in info):
                continue
            cols = [r[2] for r in info]
            sql_row = con.execute(
                "SELECT sql FROM sqlite_master WHERE type='index' AND name=?", (name,)
            ).fetchone()
            pred = "1"
            if sql_row and sql_row[0] and " WHERE " in sql_row[0]:
                pred = sql_row[0].split(" WHERE ", 1)[1]
            guard = " AND ".join(f"{_q(c)} IS NOT NULL" for c in cols)
            grp = ", ".join(_q(c) for c in cols)
            try:
                hits = con.execute(
                    f"SELECT {grp} FROM {_q(t)} WHERE ({pred}) AND ({guard}) "
                    f"GROUP BY {grp} HAVING COUNT(*) > 1 LIMIT 1"
                ).fetchall()
            except sqlite3.OperationalError:
                continue
            if hits:
                found.append((t, name, cols))
    return found


# ---------------------------------------------------------------------------
# Public entry point
# ---------------------------------------------------------------------------

def run_migration(old_path, new_path, out_path=None, overwrite=True,
                  progress=None) -> dict:
    """Migrate old database data into the new v4.4 schema file.

    Returns a dict report: status in {PASS, REVIEW}, ok (bool),
    text (full report), summary lines, files, rows, totals, indexes,
    warnings/errors, out_path, report_path.
    """
    started = time.time()

    def prog(pct, msg):
        if progress:
            progress(pct, msg)

    report_lines: list = []

    def say(msg=""):
        report_lines.append(str(msg))

    # ---------------- validate inputs -------------------------------------
    old = Path(old_path).expanduser()
    new = Path(new_path).expanduser()
    if not old.exists():
        raise MigrationError(f"Old database file not found: {old}")
    if not new.exists():
        raise MigrationError(f"New database file not found: {new}")
    if old.resolve() == new.resolve():
        raise MigrationError("Old and new files are the same file — refusing.")
    info_old = inspect_database(old)
    info_new = inspect_database(new)
    if not info_old["is_ams"]:
        raise MigrationError(
            f"Old file is not a readable AMS database: {old.name} "
            "(missing tables user/client/entry)."
        )
    if not info_new["is_ams"]:
        raise MigrationError(
            f"New file is not a readable AMS database: {new.name} "
            "(missing tables user/client/entry)."
        )
    if info_old["integrity"] != "ok":
        raise MigrationError(
            f"Old file failed its integrity check: {old.name} — repair/export "
            "it first (the tool refuses to migrate a corrupt source)."
        )
    if info_new["integrity"] != "ok":
        raise MigrationError(
            f"New file failed its integrity check: {new.name} — pick a fresh "
            "v4.4 database."
        )
    classify_warnings = classify(old, new)["warnings"]
    for w in classify_warnings:
        say(f"NOTE: {w}")

    # Hard role guards: NEW must be the fresh v4.4 schema provider.
    if info_old["looks_v44"] and not info_new["looks_v44"]:
        raise MigrationError(
            "The selection looks SWAPPED: the 'old' file has v4.4 schema markers "
            "and the 'new' file does not. OLD = the legacy database with the "
            "data; NEW = the fresh v4.4 database (e.g. NewData/ahmed_cement_v44_fresh.db)."
        )
    if not info_new["looks_v44"]:
        raise MigrationError(
            "The NEW file shows no v4.4 schema markers (user.access_mode / "
            "cash_day_account_position). This tool migrates INTO a fresh v4.4 AMS "
            "database — pick that file as NEW."
        )

    if out_path is None:
        out_path = default_out_path(new, old)
    out = Path(out_path).expanduser()
    if out.exists() and not overwrite:
        raise MigrationError(f"Output file already exists: {out}")
    if out.resolve() in (old.resolve(), new.resolve()):
        raise MigrationError("Output path must differ from the input files.")
    out.parent.mkdir(parents=True, exist_ok=True)

    prog(4, "Validated input files (old + new).")

    # ---------------- stage a consistent copy of the OLD file --------------
    # A WAL-mode source keeps committed rows in -wal; sqlite's backup API folds
    # them into the staging copy, so nothing is ever missed and the original is
    # never opened for writing.
    fd, staging = tempfile.mkstemp(suffix=".db", prefix="ams_migrate_old_stage_")
    os.close(fd)
    try:
        _consistent_copy(old, Path(staging))
        prog(10, "Staged a consistent read-only copy of the old file.")

        # ---------------- build the output from the NEW file ----------------
        if out.exists():
            out.unlink()
        _consistent_copy(new, out)
        prog(15, f"Copied the new v4.4 file as the base: {out.name}")

        con = sqlite3.connect(str(out), timeout=120)
        con.execute("PRAGMA foreign_keys=0")
        con.execute(f'ATTACH DATABASE ? AS old', (staging,))
        try:
            out_tables = _user_tables(con)
            old_tables = [r[0] for r in con.execute(
                "SELECT name FROM old.sqlite_master WHERE type='table' "
                "AND name NOT LIKE 'sqlite_%' AND name NOT LIKE '__ams%'")]
            shared = sorted(set(out_tables) & set(old_tables))

            say("=" * 78)
            say("AMS DATABASE MIGRATION REPORT")
            say("=" * 78)
            say(f"run at        : {_now()}")
            say(f"OLD (source)  : {old}")
            say(f"NEW (schema)  : {new}")
            say(f"OUT (result)  : {out}")
            say(f"old tables    : {len(old_tables)}   new-schema tables: {len(out_tables)}")
            say("")

            # ---- business tables: replace with every old row ---------------
            rows_detail = []
            relaxed_global = []
            skipped_cols = []
            n_tables = len([t for t in shared if t not in KEEP_FROM_NEW and t != "user"])
            done = 0
            for t in shared:
                if t in KEEP_FROM_NEW or t == "user":
                    continue
                done += 1
                prog(15 + int(done / max(1, n_tables) * 70), f"Loading table {t} ...")
                seed, n, rec, relaxed, skipped = _swap_table(con, t)
                rows_detail.append((t, seed, n))
                for r in relaxed:
                    relaxed_global.append(f"{t}.{r}")
                for col, cnt in skipped:
                    skipped_cols.append(f"{t}.{col} ({cnt} non-null)")
                extra = ""
                if rec:
                    extra += f"  uniq_recreated={len(rec)}"
                if relaxed:
                    extra += f"  RELAXED={relaxed}"
                say(f"[LOAD] {t:32} seed_cleared={seed:6} -> old_rows={n:6}{extra}")

            # ---- new-only seed tables: clear -------------------------------
            for t in DELETE_SEED:
                if t in out_tables:
                    before = con.execute(f'SELECT COUNT(*) FROM {_q(t)}').fetchone()[0]
                    con.execute(f'DELETE FROM {_q(t)}')
                    say(f"[CLEAR] {t:32} seed={before:6} -> 0")

            # ---- merge users: keep fresh admins, add old users -------------
            prog(88, "Merging users (fresh admins kept, old users added)...")
            out_user_cols = _table_cols(con, "user")
            old_user_rows = con.execute(
                'SELECT * FROM old."user"'
            ).fetchall()
            old_user_cols = [d[0] for d in con.execute(
                'SELECT * FROM old."user"').description]
            new_usernames = {
                r[0] for r in con.execute('SELECT username FROM "user"')
            }
            max_id = con.execute(
                'SELECT COALESCE(MAX(id),0) FROM "user"'
            ).fetchone()[0]
            umap: dict = {}
            for u in old_user_rows:
                rec = dict(zip(old_user_cols, u))
                max_id += 1
                uname = rec.get("username")
                if uname in new_usernames:
                    base, cand, i = f"{uname}_legacy", f"{uname}_legacy", 1
                    while cand in new_usernames:
                        i += 1
                        cand = f"{base}{i}"
                    uname = cand
                    new_usernames.add(cand)
                vals = []
                for c in out_user_cols:
                    if c == "id":
                        vals.append(max_id)
                    elif c == "username":
                        vals.append(uname)
                    elif c in rec:
                        vals.append(rec[c])
                    else:
                        vals.append(None)
                col_csv = ", ".join(_q(c) for c in out_user_cols)
                marks = ", ".join("?" for _ in out_user_cols)
                con.execute(
                    f'INSERT INTO "user" ({col_csv}) VALUES ({marks})', vals
                )
                umap[rec["id"]] = max_id
            total_users_now = con.execute(
                'SELECT COUNT(*) FROM "user"'
            ).fetchone()[0]
            kept_users = total_users_now - len(old_user_rows)
            say(
                f"[MERGE] user kept_from_new={kept_users} "
                f"old_users_added={len(old_user_rows)}"
                + (f"  ids remapped: {umap}" if umap else "")
            )

            # ---- remap user-id references ----------------------------------
            remapped_refs = []
            if umap:
                for t in out_tables:
                    for c in _table_cols(con, t):
                        if c in USER_REF_COLS:
                            whens = " ".join(
                                f"WHEN {o} THEN {n}" for o, n in umap.items()
                            )
                            keys = ", ".join(str(o) for o in umap)
                            try:
                                con.execute(
                                    f'UPDATE {_q(t)} SET {_q(c)} = CASE {_q(c)} '
                                    f"{whens} ELSE {_q(c)} END "
                                    f"WHERE {_q(c)} IN ({keys})"
                                )
                            except sqlite3.OperationalError:
                                continue
                            remapped_refs.append(f"{t}.{c}")
            if remapped_refs:
                say(f"      user-id refs remapped: {sorted(set(remapped_refs))}")

            # ---- finalise counters + autoincrement -------------------------
            _reset_auto_increment(con)
            con.commit()

            # ---------------- verification ----------------------------------
            prog(93, "Verifying ...")
            say("")
            say("=" * 78)
            say("VERIFICATION")
            say("=" * 78)
            old_ro = _open_ro(staging)
            new_ro = _open_ro(new)
            old_set = set(old_tables)
            out_set = set(out_tables)
            mismatches = []
            for t in sorted(old_set | out_set):
                outn = 0
                if t in out_set:
                    outn = con.execute(f'SELECT COUNT(*) FROM {_q(t)}').fetchone()[0]
                if t not in old_set:  # exists only in new schema
                    n = new_ro.execute(f'SELECT COUNT(*) FROM {_q(t)}').fetchone()[0]
                    say(f"  {t:32} (new-schema only) seed={n:6} -> {outn:6} [OK by design]")
                    continue
                o = old_ro.execute(f'SELECT COUNT(*) FROM {_q(t)}').fetchone()[0]
                n = new_ro.execute(f'SELECT COUNT(*) FROM {_q(t)}').fetchone()[0]
                if t == "user":
                    expect = o + n
                elif t in KEEP_FROM_NEW:
                    expect = n
                else:
                    expect = o
                st = "OK" if outn == expect else "MISMATCH"
                if outn != expect:
                    mismatches.append(t)
                say(f"  {t:32} old={o:6} new_seed={n:6} migrated={outn:6} expect={expect:6} [{st}]")
            old_ro.close()
            new_ro.close()

            integrity_ok, _ = _integrity_ok(con)
            say(f"integrity_check : {'ok' if integrity_ok else 'FAIL'}")
            fk = _fk_violations(con)
            say(f"fk_violations   : {fk}")

            dup = _duplicate_scan(con)
            if dup:
                say(f"duplicate scan  : VIOLATIONS -> {dup[:8]}")
            else:
                say("duplicate scan  : none — every remaining unique index holds")

            say("")
            say("=== FOREIGN KEY ORPHAN CHECK ===")
            orphan_fk = []
            for t in out_tables:
                for fk_row in con.execute(f'PRAGMA foreign_key_list({_q(t)})').fetchall():
                    ref, fcol, tcol = fk_row[2], fk_row[3], fk_row[4]
                    if ref not in out_tables:
                        continue
                    bad = con.execute(
                        f'SELECT COUNT(*) FROM {_q(t)} WHERE {_q(fcol)} IS NOT NULL '
                        f'AND {_q(fcol)} NOT IN (SELECT {_q(tcol)} FROM {_q(ref)})'
                    ).fetchone()[0]
                    if bad:
                        orphan_fk.append((t, fcol, ref, bad))
                        say(f"  ORPHAN {t}.{fcol} -> {ref}.{tcol}: {bad}")
            if not orphan_fk:
                say("  none (all FK references resolve)")

            say("")
            say("=== LOGICAL USER-FK CHECK (schema defines no FK constraints) ===")
            orphan_user = []
            for t in out_tables:
                for c in _table_cols(con, t):
                    if c in USER_REF_COLS:
                        bad = con.execute(
                            f'SELECT COUNT(*) FROM {_q(t)} WHERE {_q(c)} IS NOT NULL '
                            f'AND {_q(c)} NOT IN (SELECT id FROM "user")'
                        ).fetchone()[0]
                        if bad:
                            orphan_user.append((t, c, bad))
                            say(f"  ORPHAN {t}.{c} -> user.id: {bad}")
            if not orphan_user:
                say("  none (all user_id references resolve to a real user)")

            # old-only tables left behind? old-only columns with data?
            left_behind = []
            old_ro2 = _open_ro(staging)
            for t in old_tables:
                if t not in out_tables:
                    cnt = old_ro2.execute(
                        f'SELECT COUNT(*) FROM {_q(t)}'
                    ).fetchone()[0]
                    if cnt:
                        left_behind.append(f"{t} ({cnt} rows)")
            old_ro2.close()

            say("")
            issues = []
            if mismatches:
                issues.append(f"count mismatches: {mismatches}")
            if not integrity_ok:
                issues.append("integrity check failed")
            if fk:
                issues.append(f"{fk} FK violations")
            if dup:
                issues.append(f"duplicate values: {dup[:8]}")
            if orphan_fk:
                issues.append(f"FK orphans: {orphan_fk[:5]}")
            if orphan_user:
                issues.append(f"user-FK orphans: {orphan_user[:5]}")
            if skipped_cols:
                issues.append(
                    "old columns not in new schema carried non-null data: "
                    + ", ".join(skipped_cols[:6])
                )
            if left_behind:
                issues.append(
                    "old tables not in new schema carried rows (left behind): "
                    + ", ".join(left_behind[:6])
                )
            status = "PASS" if not issues else "REVIEW"
            say("")
            say(f"RESULT: {status}")
            if issues:
                for i in issues:
                    say(f"  review reason: {i}")
            else:
                say("  no data loss, no broken FKs, no duplicates left.")
            if relaxed_global:
                say("  relaxed unique index(es) (all duplicate rows kept):")
                for r in relaxed_global:
                    say(f"    - {r}")

            con.commit()
            con.execute("DETACH DATABASE old")
            con.close()

            # ---------------- write sidecar report files --------------------
            text = "\n".join(report_lines)
            report_path = out.with_suffix(out.suffix + ".report.txt")
            json_path = out.with_suffix(out.suffix + ".report.json")
            try:
                report_path.write_text(text, encoding="utf-8")
            except OSError:
                report_path = None
            result = {
                "status": status,
                "ok": status == "PASS",
                "run_at": _now(),
                "old_path": str(old),
                "new_path": str(new),
                "out_path": str(out),
                "report_path": str(report_path) if report_path else None,
                "old_rows": sum(o for _, _, o in rows_detail),
                "old_tables": len(old_tables),
                "new_schema_tables": len(out_tables),
                "users_added": len(old_user_rows),
                "tables_loaded": len(rows_detail),
                "relaxed_indexes": relaxed_global,
                "warnings": classify_warnings,
                "issues": issues,
                "elapsed_seconds": round(time.time() - started, 2),
                "text": text,
            }
            try:
                json_path.write_text(
                    json.dumps({k: v for k, v in result.items() if k != "text"},
                               indent=2, ensure_ascii=False),
                    encoding="utf-8",
                )
            except OSError:
                pass
            prog(100, f"Done — {status}. Output: {out}")
            return result
        finally:
            try:
                con.close()
            except Exception:
                pass
    finally:
        try:
            Path(staging).unlink(missing_ok=True)
        except OSError:
            pass


# ---------------------------------------------------------------------------
# Headless entry point (also used by tests)
# ---------------------------------------------------------------------------

def _print_summary(res: dict) -> None:
    print(res["text"])
    print("\n--- machine summary ---")
    print(json.dumps({k: v for k, v in res.items() if k != "text"}, indent=2, default=str))


if __name__ == "__main__":
    import argparse

    ap = argparse.ArgumentParser(
        description="AMS migration engine (headless). Old DB -> new v4.4 schema DB."
    )
    ap.add_argument("--old", required=True, help="old/legacy database file")
    ap.add_argument("--new", required=True, help="new v4.4 template database file")
    ap.add_argument("--out", help="output migration file (default: next to NEW)")
    ap.add_argument("--no-overwrite", action="store_true")
    args = ap.parse_args()

    def _prog(pct, msg):
        print(f"[{pct:3d}%] {msg}")

    try:
        report = run_migration(
            args.old, args.new,
            out_path=args.out,
            overwrite=not args.no_overwrite,
            progress=_prog,
        )
        _print_summary(report)
        sys.exit(0 if report["ok"] else 3)
    except MigrationError as e:
        print(f"ERROR: {e}", file=sys.stderr)
        sys.exit(2)
    except Exception as e:  # pragma: no cover
        print(f"UNEXPECTED ERROR: {e}", file=sys.stderr)
        raise
