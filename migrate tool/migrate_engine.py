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
import hashlib
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

# ---------------------------------------------------------------------------
# Purge policy: NO voided or cancelled data is carried into the new database.
#
# The v4.4 application deletes for real (app/services/void_rebuild.py::
# hard_delete_transaction removes the row and its children); it does not create
# voided rows.  The legacy `is_void` / cancelled rows are therefore dead weight
# that would only inflate ledgers, stock and reports.  This contract is ported
# from the retired Excel pipeline (tools/migrate/_migrate_common.py::
# compute_clean_frames) so both migration paths mean the same thing by "clean":
#   * every row with is_void = 1 is dropped
#   * entry rows that are CANCEL are dropped even when is_void = 0
#   * children of dropped parents are dropped too (cascade), so no orphan
#     foreign keys survive
#   * rows pointing at a parent that never existed are dropped as well
# ---------------------------------------------------------------------------

# (table, column, value) — cancelled rows that do not carry is_void = 1.
CANCEL_RULES = (
    ("entry", "type", "CANCEL"),
    ("entry", "transaction_category", "CANCEL"),
)

# (child, child_fk, parent, extra SQL condition or None)
CASCADE_RULES = (
    ("booking_item", "booking_id", "booking", None),
    ("direct_sale_item", "sale_id", "direct_sale", None),
    ("booking_allocation", "sale_id", "direct_sale", None),
    ("booking_allocation", "sale_item_id", "direct_sale_item", None),
    ("booking_allocation", "booking_item_id", "booking_item", None),
    ("entry", "source_id", "direct_sale", "source_table = 'direct_sale'"),
    ("pending_bill", "source_id", "direct_sale", "source_table = 'direct_sale'"),
    ("pending_bill", "source_id", "booking", "source_table = 'booking'"),
    ("delivery_rent", "sale_id", "direct_sale", None),
    ("sale_delivery_persons", "sale_id", "direct_sale", None),
    ("waive_off", "payment_id", "payment", None),
    ("material_return", "payment_id", "payment", None),
    ("grn_item", "grn_id", "grn", None),
    ("material_return_item", "material_return_id", "material_return", None),
    ("follow_up_reminder", "pending_bill_id", "pending_bill", None),
    ("follow_up_contact", "pending_bill_id", "pending_bill", None),
    ("delivery_person_payment", "sale_id", "direct_sale", None),
    ("delivery_person_payment", "allocation_id", "sale_delivery_persons", None),
    ("delivery_person_payment", "delivery_person_id", "delivery_person", None),
    ("direct_sale_item", "grn_item_id", "grn_item", None),
)

# (child, child_fk, parent) — drop rows whose parent does not exist at all
# (legacy dangling references, not caused by this purge).
MISSING_PARENT_RULES = (
    ("booking_allocation", "booking_item_id", "booking_item"),
)

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
    """Default output: <this-tool>/output/<old-stem>_migrated.db when the
    tool's own output/ folder exists (keeps results out of the watched input
    folder so a finished report is never re-detected as an input candidate);
    otherwise next to the NEW file as before."""
    tool_output = Path(__file__).resolve().parent / "output"
    if tool_output.is_dir():
        return tool_output / f"{Path(old_path).stem}_migrated.db"
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
    so they are stripped at DDL time and re-created as explicit unique indexes
    after the swap (relaxed only if the old data genuinely violates them);
    normal UNIQUE indexes are recreated after the swap as well.

    Single-quoted string literals are masked before the regex passes so a
    column default such as DEFAULT 'UNIQUE SIZE' can never be corrupted
    (audit defect D-6)."""
    literals: dict = {}

    def _stash(m: "re.Match") -> str:
        key = f"\x00lit{len(literals)}\x00"
        literals[key] = m.group(0)
        return f"'{key}'"

    s = re.sub(r"'(?:[^']|'')*'", _stash, create_sql)
    s = re.sub(r",\s*CONSTRAINT\s+\w+\s+UNIQUE\s*\([^)]*\)", " ", s, flags=re.I)
    s = re.sub(r"\bUNIQUE\s*\([^)]*\)", " ", s, flags=re.I)
    s = re.sub(r"\bUNIQUE\b", " ", s, flags=re.I)
    s = re.sub(r"\s*,\s*,+", ",", s)
    s = re.sub(r",\s*\)", ")", s)
    s = re.sub(r"\s+", " ", s)
    for key, lit in literals.items():
        s = s.replace(f"'{key}'", lit)
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
    # Capture EVERY index of the table BEFORE the swap: dropping the table
    # drops its indexes too (audit defect D-4 lost 209 of 261). origin 'c' =
    # explicit CREATE INDEX (unique or not) — re-created from its saved SQL.
    # origin 'u' = inline UNIQUE table constraint (auto-index, no SQL) — the
    # constraint is stripped from the tmp DDL above, so it is re-created as an
    # explicit unique index on the same columns. origin 'pk' is the rowid PK.
    saved_indexes = []
    for row in con.execute(f'PRAGMA index_list({_q(t)})').fetchall():
        name, uniq, origin = row[1], row[2], row[3]
        if origin == "pk":
            continue
        if origin == "u":
            cols = [r[2] for r in con.execute(f'PRAGMA index_info({_q(name)})').fetchall()
                    if r[2] is not None]
            saved_indexes.append((name, None, origin, uniq, cols))
            continue
        sql_row = con.execute(
            "SELECT sql FROM sqlite_master WHERE type='index' AND name=?",
            (name,),
        ).fetchone()
        if sql_row and sql_row[0]:
            saved_indexes.append((name, sql_row[0], origin, uniq, None))
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
    for name, index_sql, origin, uniq, cols in saved_indexes:
        if origin == "u":
            # inline UNIQUE constraint of the NEW template — restore as an
            # explicit unique index on the same columns. SQLite reserves the
            # "sqlite_autoindex_" name prefix, so generated auto-index names
            # are mapped to a legal "uq_..." name (same guarantee, own name).
            if not cols:
                continue
            name_final = name
            if name.startswith("sqlite_autoindex_"):
                name_final = "uq_" + name[len("sqlite_autoindex_"):]
            col_csv = ", ".join(_q(c) for c in cols)
            index_sql = f'CREATE UNIQUE INDEX {_q(name_final)} ON {_q(t)} ({col_csv})'
            name = name_final
        if not index_sql:
            continue
        try:
            con.execute(index_sql)
            recreated.append(name)
        except (sqlite3.OperationalError, sqlite3.IntegrityError) as e:
            # unique indexes may be genuinely violated by the old data (relaxed
            # and recorded); a plain index should never fail, but if it does it
            # is recorded the same way so the index-parity check reports it.
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


def _purge_voided(con: sqlite3.Connection, tables: list, say) -> dict:
    """Delete every voided / cancelled row (and its children) from the output.

    Policy: the new database carries **no** voided data.  Returns a per-table
    report ``{table: {total, kept, removed_void, removed_cancel,
    removed_cascade, removed_missing_parent}}`` for the migration report.
    """
    present = set(tables)

    def has(t: str) -> bool:
        return t in present

    def cols(t: str) -> set:
        return {r[1] for r in con.execute(f'PRAGMA table_info({_q(t)})')}

    def all_ids(t: str) -> set:
        if not has(t) or "id" not in cols(t):
            return set()
        return {r[0] for r in con.execute(f'SELECT id FROM {_q(t)}')}

    dropped: dict = {}
    void_ids: dict = {}
    cancel_ids: dict = {}

    def mark(table: str, ids: set) -> None:
        if ids:
            dropped.setdefault(table, set()).update(ids)

    # ---- pass 1: is_void = 1 -----------------------------------------------
    for t in sorted(present):
        if "is_void" not in cols(t):
            continue
        ids = {r[0] for r in con.execute(
            f'SELECT id FROM {_q(t)} WHERE COALESCE(CAST({_q("is_void")} AS INTEGER), 0) = 1'
        )}
        if ids:
            void_ids[t] = ids
            mark(t, ids)

    # ---- pass 2: cancelled entries (is_void may still be 0) ----------------
    for t, col, value in CANCEL_RULES:
        if not has(t) or col not in cols(t):
            continue
        ids = {r[0] for r in con.execute(
            f'SELECT id FROM {_q(t)} WHERE UPPER(TRIM(COALESCE({_q(col)}, \'\'))) = ?',
            (value.upper(),)
        )}
        if ids:
            cancel_ids.setdefault(t, set()).update(ids)
            mark(t, ids)

    # ---- pass 3: cascade (repeat until stable — chains are possible) -------
    parent_ids = {t: all_ids(t) for t in
                  {p for _, _, p, _ in CASCADE_RULES} |
                  {p for _, _, p in MISSING_PARENT_RULES}}
    while True:
        before = sum(len(v) for v in dropped.values())
        for child, col, parent, cond in CASCADE_RULES:
            if not has(child) or not has(parent) or col not in cols(child):
                continue
            gone = parent_ids.get(parent, set()) & dropped.get(parent, set())
            if not gone:
                continue
            placeholders = ", ".join("?" for _ in gone)
            sql = (f'SELECT id FROM {_q(child)} WHERE {_q(col)} IN ({placeholders})')
            args = list(gone)
            if cond:
                sql += f' AND ({cond})'
            mark(child, {r[0] for r in con.execute(sql, args)})
        if sum(len(v) for v in dropped.values()) == before:
            break

    # ---- pass 4: rows whose parent never existed ---------------------------
    missing: dict = {}
    for child, col, parent in MISSING_PARENT_RULES:
        if not has(child) or not has(parent) or col not in cols(child):
            continue
        alive = parent_ids.get(parent, set()) - dropped.get(parent, set())
        rows = con.execute(
            f'SELECT id, {_q(col)} FROM {_q(child)} WHERE {_q(col)} IS NOT NULL'
        ).fetchall()
        ids = {r[0] for r in rows if r[1] not in alive and
               r[0] not in dropped.get(child, set())}
        if ids:
            missing[child] = ids
            mark(child, ids)

    # ---- delete ------------------------------------------------------------
    report = {}
    for t in sorted(dropped):
        ids = dropped[t]
        total = con.execute(f'SELECT COUNT(*) FROM {_q(t)}').fetchone()[0]
        v = len(void_ids.get(t, set()))
        # a row can be BOTH void and cancelled — count it once (the Excel
        # pipeline's purge_report double-counted 16 such rows)
        c = len(cancel_ids.get(t, set()) - void_ids.get(t, set()))
        m = len(missing.get(t, set()))
        con.executemany(
            f'DELETE FROM {_q(t)} WHERE id = ?', [(i,) for i in ids])
        kept = con.execute(f'SELECT COUNT(*) FROM {_q(t)}').fetchone()[0]
        report[t] = {
            "total": total,
            "kept": kept,
            "removed_void": v,
            "removed_cancel": c,
            "removed_cascade": max(0, len(ids) - v - c - m),
            "removed_missing_parent": m,
        }
    return report


def _required_column_problems(con: sqlite3.Connection, tables: list) -> list:
    """New-schema columns that would break the load, detected *before* loading.

    A target column that is ``NOT NULL`` with no default and that the old file
    does not carry (or carries entirely NULL) makes the copy abort with a raw
    ``sqlite3.IntegrityError: NOT NULL constraint failed: tmp_<t>.<col>`` in the
    middle of the run — after dozens of tables have already been swapped.  Catch
    it up-front so the operator gets an actionable message instead (G3).

    Returns a list of ``(table, column, reason)`` tuples.
    """
    problems = []
    for t in tables:
        out_info = con.execute(f'PRAGMA table_info({_q(t)})').fetchall()
        old_info = con.execute(f'PRAGMA old.table_info({_q(t)})').fetchall()
        if not old_info:
            continue
        old_cols = {r[1] for r in old_info}
        try:
            old_rows = con.execute(f'SELECT COUNT(*) FROM old.{_q(t)}').fetchone()[0]
        except sqlite3.OperationalError:
            continue
        if not old_rows:
            continue  # nothing to copy -> nothing can violate NOT NULL
        for cid, name, ctype, notnull, dflt, pk in out_info:
            if not notnull or pk or dflt is not None:
                continue
            if name not in old_cols:
                problems.append((t, name, "column does not exist in the old file"))
                continue
            try:
                non_null = con.execute(
                    f'SELECT COUNT(*) FROM old.{_q(t)} WHERE {_q(name)} IS NOT NULL'
                ).fetchone()[0]
            except sqlite3.OperationalError:
                continue
            if non_null == 0:
                problems.append((t, name, f"NULL in all {old_rows} old row(s)"))
    return problems


def _table_fingerprint(con: sqlite3.Connection, table: str, cols: list,
                       prefix: str = "", user_map: dict = None,
                       only_ids_from: bool = False) -> tuple:
    """Order-independent md5 over every copied value of a table.

    Row counts prove *how many* rows arrived; this proves *which values*
    arrived.  Two tables with the same cardinality but a mis-mapped column
    (or a value silently defaulted by SQLite) get different fingerprints (G4).

    ``user_map`` applies the documented old-id -> new-id user remap to the
    user-reference columns first, so the *only* transformation the migration
    performs is treated as expected (pass it for the OLD side only).

    ``only_ids_from`` restricts the scan to ids that still exist in the *output*
    table (used for the OLD side after a purge, so intentionally removed rows
    are not reported as value mismatches).
    """
    h = hashlib.md5()
    if not cols:
        return 0, h.hexdigest()
    col_csv = ", ".join(_q(c) for c in cols)
    remap_at = [i for i, c in enumerate(cols) if c in USER_REF_COLS]
    sql = f'SELECT {col_csv} FROM {prefix}{_q(table)}'
    if only_ids_from:
        sql += f' WHERE {_q("id")} IN (SELECT {_q("id")} FROM {_q(table)})'
    rows = []
    for r in con.execute(sql):
        vals = list(r)
        if user_map and remap_at:
            for i in remap_at:
                v = vals[i]
                if v is not None and v in user_map:
                    vals[i] = user_map[v]
        rows.append(repr(tuple(vals)))
    rows.sort()
    for r in rows:
        h.update(r.encode("utf-8", "surrogatepass"))
    return len(rows), h.hexdigest()


# ---------------------------------------------------------------------------
# Public entry point
# ---------------------------------------------------------------------------

def run_migration(old_path, new_path, out_path=None, overwrite=True,
                  progress=None, allow_v44_old: bool = False,
                  purge_voided: bool = True) -> dict:
    """Migrate old database data into the new v4.4 schema file.

    ``purge_voided`` (default True) removes every voided / cancelled row — and
    its children — from the result, because the v4.4 app hard-deletes and must
    not inherit the legacy soft-delete rows.  Pass False (CLI ``--keep-voided``)
    only when the archive must be preserved bit-for-bit.

    Returns a dict report: status in {PASS, REVIEW}, ok (bool),
    text (full report), summary lines, files, rows, totals, purge, indexes,
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
    # G1: an OLD file that already carries v4.4 markers is, in practice, an
    # output of a previous run (or the fresh template itself).  Migrating it a
    # second time is NOT a no-op: the user merge appends every old user at
    # max(id)+1 again and remaps user_id/created_by_id a second time, which
    # silently re-points audit rows at the wrong person (measured on the real
    # data: 785 of 1,630 audit_log rows changed owner, and users duplicated as
    # 'Admin_legacy_legacy').  Refuse unless the caller opts in explicitly.
    if info_old["looks_v44"] and not allow_v44_old:
        raise MigrationError(
            f"The OLD file ({old.name}) already carries v4.4 schema markers — it "
            "looks like the output of a previous migration, not a legacy file. "
            "Running it again is not idempotent: users are merged a second time "
            "and every user_id/created_by_id reference is remapped again, which "
            "corrupts audit-log attribution. Pick the original legacy database as "
            "OLD. (Override only if you know what you are doing: --allow-v44-old.)"
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

            # ---- pre-flight: columns that would abort the load mid-run ------
            # G3: a NOT NULL target column the old file cannot fill used to blow
            # up half-way through with a raw SQLite error.  Name it up-front.
            to_load = [t for t in shared if t not in KEEP_FROM_NEW]
            problems = _required_column_problems(con, to_load)
            if problems:
                con.close()
                detail = "; ".join(f"{t}.{c} ({why})" for t, c, why in problems[:8])
                raise MigrationError(
                    f"The old file cannot fill {len(problems)} NOT NULL column(s) "
                    f"of the new schema: {detail}"
                    + (" …" if len(problems) > 8 else "")
                    + ". Back-fill them in the old file (or give the column a "
                    "default in the v4.4 template) and run again — the load was "
                    "not started, so nothing was written."
                )

            # ---- business tables: replace with every old row ---------------
            rows_detail = []
            relaxed_global = []
            relaxed_names = set()
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
                    relaxed_names.add(r.split(" ", 1)[0])
                for col, cnt in skipped:
                    skipped_cols.append(f"{t}.{col} ({cnt} non-null)")
                extra = ""
                if rec:
                    extra += f"  indexes_recreated={len(rec)}"
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

            # ---- purge: no voided / cancelled data in the new database ------
            prog(90, "Purging voided / cancelled rows ...")
            purge_report = {}
            if purge_voided:
                purge_report = _purge_voided(con, out_tables, say)
                say("")
                say("=" * 78)
                say("PURGE — NO VOIDED OR CANCELLED DATA IS CARRIED")
                say("=" * 78)
                if not purge_report:
                    say("  nothing to purge — the old file carried no voided or "
                        "cancelled rows.")
                else:
                    say(f"  {'table':30} {'total':>7} {'void':>6} {'cancel':>7} "
                        f"{'cascade':>8} {'orphan':>7} {'kept':>7}")
                    tot = {"total": 0, "kept": 0, "removed_void": 0,
                           "removed_cancel": 0, "removed_cascade": 0,
                           "removed_missing_parent": 0}
                    for t, r in sorted(purge_report.items()):
                        say(f"  {t:30} {r['total']:7} {r['removed_void']:6} "
                            f"{r['removed_cancel']:7} {r['removed_cascade']:8} "
                            f"{r['removed_missing_parent']:7} {r['kept']:7}")
                        for k in tot:
                            tot[k] += r[k]
                    say(f"  {'TOTAL':30} {tot['total']:7} {tot['removed_void']:6} "
                        f"{tot['removed_cancel']:7} {tot['removed_cascade']:8} "
                        f"{tot['removed_missing_parent']:7} {tot['kept']:7}")
                    say("  Children of purged rows are purged with them, so no "
                        "orphan foreign keys are left behind.")
            else:
                say("")
                say("PURGE SKIPPED (--keep-voided): voided and cancelled rows were "
                    "carried over as-is.")

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
            left_behind = []
            dropped_keep = []
            for t in sorted(old_set | out_set):
                outn = 0
                if t in out_set:
                    outn = con.execute(f'SELECT COUNT(*) FROM {_q(t)}').fetchone()[0]
                if t not in old_set:  # exists only in new schema
                    n = new_ro.execute(f'SELECT COUNT(*) FROM {_q(t)}').fetchone()[0]
                    say(f"  {t:32} (new-schema only) seed={n:6} -> {outn:6} [OK by design]")
                    continue
                o = old_ro.execute(f'SELECT COUNT(*) FROM {_q(t)}').fetchone()[0]
                if t not in out_set:
                    # old-only table (audit D-1): report it, never crash on it.
                    say(f"  {t:32} old={o:6} new_seed=   n/a migrated=     0 expect=     0 [LEFT BEHIND]")
                    if o:
                        left_behind.append(f"{t} ({o} rows)")
                    continue
                n = new_ro.execute(f'SELECT COUNT(*) FROM {_q(t)}').fetchone()[0]
                if t == "user":
                    expect = o + n
                elif t in KEEP_FROM_NEW:
                    expect = n
                    if o:
                        # audit D-2: KEEP_FROM_NEW discards old rows — surface it
                        dropped_keep.append(f"{t} ({o} row(s))")
                else:
                    # rows removed by the purge policy are expected to be gone
                    expect = o - purge_report.get(t, {}).get("total", 0) \
                        + purge_report.get(t, {}).get("kept", 0)
                st = "OK" if outn == expect else "MISMATCH"
                if outn != expect:
                    mismatches.append(t)
                say(f"  {t:32} old={o:6} new_seed={n:6} migrated={outn:6} expect={expect:6} [{st}]")
            tmpl_idx = {r[0] for r in new_ro.execute(
                "SELECT name FROM sqlite_master WHERE type='index' AND sql IS NOT NULL")}
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

            # ---- value parity (G4) ----------------------------------------
            # Row counts prove HOW MANY rows arrived; this proves WHICH VALUES
            # arrived. A mis-mapped column with the same cardinality used to
            # sail through every gate and still print RESULT: PASS.
            say("")
            say("=== VALUE PARITY (every copied value, old vs migrated) ===")
            value_mismatch = []
            fp_tables = 0
            for t in shared:
                if t in KEEP_FROM_NEW or t == "user":
                    continue
                old_cols = [r[1] for r in con.execute(
                    f'PRAGMA old.table_info({_q(t)})')]
                inter = [c for c in old_cols if c in _table_cols(con, t)]
                if not inter:
                    continue
                fp_tables += 1
                # compare only the rows that survived the purge (purged rows are
                # an intentional removal, not a value mismatch)
                survivor_only = bool(purge_report.get(t)) and "id" in inter
                a = _table_fingerprint(con, t, inter, prefix="old.", user_map=umap,
                                       only_ids_from=survivor_only)
                b = _table_fingerprint(con, t, inter)
                if a != b:
                    value_mismatch.append(f"{t} (old {a[0]} rows vs out {b[0]} rows)")
                    say(f"  MISMATCH {t}: old={a[0]}/{a[1][:8]} out={b[0]}/{b[1][:8]}")
            if value_mismatch:
                say(f"value parity    : MISMATCH -> {value_mismatch[:5]}")
            else:
                say(f"value parity    : identical — {fp_tables} tables, every "
                    "copied value matches the old file")
            say("  (user excluded: it is merged, not copied — see [MERGE] above)")

            # ---- new-schema columns that arrive NULL (G2) ------------------
            filled_null = []
            for t in shared:
                if t in KEEP_FROM_NEW:
                    continue
                old_cols = {r[1] for r in con.execute(
                    f'PRAGMA old.table_info({_q(t)})')}
                new_only = [c for c in _table_cols(con, t) if c not in old_cols]
                if not new_only:
                    continue
                total = con.execute(f'SELECT COUNT(*) FROM {_q(t)}').fetchone()[0]
                if not total:
                    continue
                for c in new_only:
                    nulls = con.execute(
                        f'SELECT COUNT(*) FROM {_q(t)} WHERE {_q(c)} IS NULL'
                    ).fetchone()[0]
                    if nulls == total:
                        filled_null.append(f"{t}.{c}")
            if filled_null:
                say("")
                say("=== NEW-SCHEMA COLUMNS FILLED NULL (informational) ===")
                say(f"  {len(filled_null)} column(s) exist in v4.4 but not in the old "
                    "file, so every migrated row is NULL there:")
                for c in filled_null[:12]:
                    say(f"    - {c}")
                if len(filled_null) > 12:
                    say(f"    … and {len(filled_null) - 12} more")
                say("  Not an error: the AMS app back-fills the ones it needs on its")
                say("  next start (e.g. _ensure_account_classification_columns).")

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

            # old-only tables with data were collected during the parity loop
            # (audit D-1 fix) — nothing to recompute here.

            # index parity (audit D-4 fix): every explicit index of the v4.4
            # template must exist in the output, except indexes explicitly
            # relaxed because the old data violates them.
            out_idx = {r[0] for r in con.execute(
                "SELECT name FROM sqlite_master WHERE type='index' AND sql IS NOT NULL")}
            lost_indexes = sorted(tmpl_idx - out_idx - relaxed_names)
            say("")
            if lost_indexes:
                say(f"index parity    : MISSING {len(lost_indexes)} -> {lost_indexes[:8]}")
            else:
                say("index parity    : every template index exists in the output"
                    + (f" ({len(relaxed_names)} relaxed by design)" if relaxed_names else ""))

            say("")
            say("=== NEXT STEPS ===")
            say("  1. Only this file with 'RESULT: PASS' may be imported. A run that")
            say("     aborted leaves <name>.INCOMPLETE — never import that.")
            say("  2. Load it through the app's importer (Import/Export Center ->")
            say("     Full Database Snapshot -> Import, mode 'Full sync'), or headless:")
            say("       python3 -m full_db_sync export --db <this file> --out AMS.amsdb")
            say("       python3 -m full_db_sync verify --db AMS.amsdb")
            say("       python3 -m full_db_sync import --source AMS.amsdb \\")
            say("               --db instance/ahmed_cement_v44_fresh.db --confirm")
            say("  3. Before the app's FIRST start after the import, delete")
            say("     instance/health_snapshot.json (or start once with")
            say("     ALLOW_DB_DROP=1). The startup data-loss guard compares row")
            say("     counts against that snapshot and refuses to start when they")
            say("     drop by >= 50 rows or below 80%.")
            say("  4. That first start back-fills the new v4.4 columns listed above")
            say("     (account classification, counters, Open-Khata client, indexes).")

            say("")
            issues = []
            if mismatches:
                issues.append(f"count mismatches: {mismatches}")
            if value_mismatch:
                issues.append(
                    "value mismatch — copied values differ from the old file: "
                    + ", ".join(value_mismatch[:6])
                )
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
            if dropped_keep:
                issues.append(
                    "rows in KEEP_FROM_NEW tables were NOT carried into the "
                    "output (fresh-template values kept) — merge explicitly "
                    "if they matter: " + ", ".join(dropped_keep[:6])
                )
            if lost_indexes:
                issues.append(
                    f"{len(lost_indexes)} template index(es) missing from the "
                    "output: " + ", ".join(lost_indexes[:6])
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
                "purge": {
                    "enabled": bool(purge_voided),
                    "tables": purge_report,
                    "rows_removed": sum(
                        r["total"] - r["kept"] for r in purge_report.values()),
                },
                "relaxed_indexes": relaxed_global,
                "warnings": classify_warnings,
                "issues": issues,
                "value_parity": {
                    "tables_checked": fp_tables,
                    "mismatches": value_mismatch,
                },
                "new_columns_filled_null": filled_null,
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
    except BaseException as exc:
        # Failure hygiene (audit D-3): never leave a silent half-loaded output.
        # Write the report even on failure and quarantine any partial output so
        # it cannot be mistaken for a finished migration (or imported downstream).
        say("")
        say(f"RESULT: FAILED — run aborted: {exc}")
        say("  the output file is INCOMPLETE and must not be imported.")
        quarantine = None
        try:
            if out.exists():
                quarantine = out.with_suffix(out.suffix + ".INCOMPLETE")
                os.replace(out, quarantine)
                say(f"  partial output quarantined as: {quarantine.name}")
        except OSError:
            quarantine = None
        try:
            report_target = (quarantine or out)
            report_target.with_suffix(report_target.suffix + ".report.txt").write_text(
                "\n".join(report_lines), encoding="utf-8")
        except OSError:
            pass
        raise
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
    ap.add_argument(
        "--keep-voided", action="store_true",
        help="carry voided/cancelled rows over instead of purging them "
             "(default is to purge: the new database must hold no voided data)",
    )
    ap.add_argument(
        "--allow-v44-old", action="store_true",
        help="permit an OLD file that already carries v4.4 markers (experts only:"
             " re-migrating re-maps user ids and corrupts audit attribution)",
    )
    args = ap.parse_args()

    def _prog(pct, msg):
        print(f"[{pct:3d}%] {msg}")

    try:
        report = run_migration(
            args.old, args.new,
            out_path=args.out,
            overwrite=not args.no_overwrite,
            progress=_prog,
            allow_v44_old=args.allow_v44_old,
            purge_voided=not args.keep_voided,
        )
        _print_summary(report)
        sys.exit(0 if report["ok"] else 3)
    except MigrationError as e:
        print(f"ERROR: {e}", file=sys.stderr)
        sys.exit(2)
    except Exception as e:  # pragma: no cover
        print(f"UNEXPECTED ERROR: {e}", file=sys.stderr)
        raise
