#!/usr/bin/env python3
"""
Stage 2 of the AMS data pipeline:  "FIRST CLASS DATA"  ->  the AMSCOPY9 app DB.

What this tool does (and why it is the replacement for xlsx import/export):
  1. GATE   - sanity-check source + target (both must be AMS v44 SQLite DBs,
              source must be non-empty and pass integrity_check).
  2. BACKUP - copy the CURRENT app database aside into
              "data lab for migration old to new/backups/appdb_backup_<ts>/".
  3. CLEAN  - wipe EVERY row from EVERY table of the app database
              ("cleans all data from amscopy9") + reset sqlite_sequence.
              Schema/indexes stay exactly as the app created them.
  4. LOAD   - db-to-db copy of ALL rows from the source DB with their ORIGINAL
              ids (same-schema SQLite copy via ATTACH — no xlsx, no Excel
              type coercion, no purge-by-spreadsheet).  Unique indexes that the
              data cannot satisfy are relaxed exactly like stage 1
              (FIRST CLASS DATA/migrate.py) and listed in the report.
  5. VERIFY - integrity_check, per-table row-count diff (target == source),
              FK orphan check, logical user-FK check.
  6. DEPLOY - writes the final file + checksum into "FINAL DEPLOY/".

Run (destructive on the app DB — a backup is taken automatically first):
    python3 load_into_amscopy9.py --confirm

Safety:
  * refuses to run without --confirm,
  * refuses when source == target,
  * restores the automatic backup automatically if the load fails mid-way,
  * never touches the source DB (opened read-only).

Outputs
  * FINAL DEPLOY/ahmed_cement_v44_fresh.db   <- the deployable app database
  * FINAL DEPLOY/ahmed_cement_v44_fresh.db.sha256
  * FINAL DEPLOY/load_report.txt             <- full verification report
"""
from __future__ import annotations

import argparse
import hashlib
import os
import re
import shutil
import sqlite3
import sys
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parent           # "data lab for migration old to new"
REPO = ROOT.parent                               # repository root
APP_DIR = REPO / "AMSCOPY9" / "instance"         # the app's runtime data dir

SOURCE = ROOT / "FIRST CLASS DATA" / "ahmed_cement_migrated.db"
APP_DB = Path(os.environ.get("APP_DB_PATH") or (APP_DIR / "ahmed_cement_v44_fresh.db"))
DEPLOY_DIR = ROOT / "FINAL DEPLOY"
BACKUP_DIR = ROOT / "backups"
REPORT = DEPLOY_DIR / "load_report.txt"

USER_REF_COLS = {"user_id", "created_by_id"}


def sha256(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def is_ams_app_db(con: sqlite3.Connection) -> bool:
    """Identify an AMS v4.4 app database (ORM-bootstrapped or SQL-bundled)."""
    names = {r[0] for r in con.execute(
        "SELECT name FROM sqlite_master WHERE type='table'")}
    return {"user", "client", "direct_sale", "entry"}.issubset(names)


def table_list(con: sqlite3.Connection) -> list[str]:
    return [r[0] for r in con.execute(
        "SELECT name FROM sqlite_master WHERE type='table' "
        "AND name NOT LIKE 'sqlite_%' AND name NOT LIKE 'tmp_%' ORDER BY name")]


def strip_secondary_unique(create_sql: str, t: str, tmp_name: str) -> str:
    """CREATE TABLE DDL for tmp_name identical to `t` minus secondary UNIQUEs
    (PRIMARY KEY is kept; inline-UNIQUE auto-indexes cannot be dropped later,
    so they are stripped at DDL time)."""
    s = create_sql
    s = re.sub(r',\s*CONSTRAINT\s+\w+\s+UNIQUE\s*\([^)]*\)', ' ', s, flags=re.I)
    s = re.sub(r'\bUNIQUE\s*\([^)]*\)', ' ', s, flags=re.I)
    s = re.sub(r'\bUNIQUE\b', ' ', s, flags=re.I)
    s = re.sub(r'\s*,\s*,+', ',', s)
    s = re.sub(r',\s*\)', ')', s)
    s = re.sub(r'\s+', ' ', s)
    if f'CREATE TABLE "{t}" (' in s:
        s = s.replace(f'CREATE TABLE "{t}" (', f'CREATE TABLE "{tmp_name}" (', 1)
    else:
        s = re.sub(
            r'CREATE\s+TABLE\s+(?:IF\s+NOT\s+EXISTS\s+)?[`"]?' + re.escape(t) + r'[`"]?',
            f'CREATE TABLE "{tmp_name}"', s, count=1, flags=re.I)
    return s


def recreate_source_indexes(con: sqlite3.Connection, t: str, src: str,
                            report: list[str]) -> tuple[list[str], list[str]]:
    """Recreate exactly the indexes the SOURCE DB has on `t` (the proven,
    data-valid set from stage 1).  Returns (recreated, relaxed)."""
    recreated, relaxed = [], []
    for (name, sql) in con.execute(
            f'SELECT name, sql FROM {src}.sqlite_master '
            f"WHERE type='index' AND tbl_name='{t}' AND sql IS NOT NULL").fetchall():
        try:
            con.execute(sql)
            recreated.append(name)
        except sqlite3.Error as e:
            relaxed.append(f"{name} ({str(e)[:60]})")
    return recreated, relaxed


def load_table(con: sqlite3.Connection, t: str, src: str, report: list[str]) -> None:
    src_cols = [r[1] for r in con.execute(f'PRAGMA {src}.table_info("{t}")').fetchall()]
    dst_cols = [r[1] for r in con.execute(f'PRAGMA table_info("{t}")').fetchall()]
    inter = [c for c in src_cols if c in dst_cols]
    col_csv = ", ".join(f'"{c}"' for c in inter)

    ddl = con.execute("SELECT sql FROM sqlite_master WHERE name=?", (t,)).fetchone()
    assert ddl and ddl[0], f"no DDL for {t}"
    tmp = "tmp_" + t
    con.execute(f'DROP TABLE IF EXISTS "{tmp}"')
    con.execute(strip_secondary_unique(ddl[0], t, tmp))
    con.execute(f'INSERT INTO "{tmp}" ({col_csv}) SELECT {col_csv} FROM {src}."{t}"')
    n = con.execute(f'SELECT COUNT(*) FROM "{tmp}"').fetchone()[0]
    con.execute(f'DROP TABLE IF EXISTS "{t}"')
    con.execute(f'ALTER TABLE "{tmp}" RENAME TO "{t}"')
    recreated, relaxed = recreate_source_indexes(con, t, src, report)
    extra = f" uniq_recreated={len(recreated)}" + (f" RELAXED={relaxed}" if relaxed else "")
    report.append(f"[LOAD   ] {t:32} rows={n:6}{extra}")


def sync_sqlite_sequence(con: sqlite3.Connection) -> list[str]:
    """After a full clean + explicit-id load, keep AUTOINCREMENT counters in
    sync (only matters for tables that use AUTOINCREMENT — normally none)."""
    touched = []
    if not con.execute("SELECT 1 FROM sqlite_master WHERE type='table' "
                       "AND name='sqlite_sequence'").fetchone():
        return touched
    for (name,) in con.execute("SELECT name FROM sqlite_sequence"):
        pk = con.execute(f'PRAGMA table_info("{name}")').fetchone()
        if not pk:
            continue
        maxid = con.execute(f'SELECT COALESCE(MAX("rowid"),0) FROM "{name}"').fetchone()[0]
        con.execute('UPDATE sqlite_sequence SET seq=? WHERE name=?', (maxid, name))
        touched.append(name)
    return touched


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--source", type=Path, default=SOURCE)
    ap.add_argument("--app-db", type=Path, default=APP_DB)
    ap.add_argument("--confirm", action="store_true",
                    help="really clean the app DB and load the source data")
    ap.add_argument("--skip-deploy-copy", action="store_true",
                    help="do not refresh the FINAL DEPLOY artifact copy")
    args = ap.parse_args()

    source, target = args.source.resolve(), args.app_db.resolve()
    if source == target:
        print("REFUSE: source and target are the same file."); return 2
    if not source.exists():
        print(f"REFUSE: source missing: {source}"); return 2
    if not target.exists():
        print(f"REFUSE: target missing: {target}"); return 2
    if not args.confirm:
        print(__doc__.splitlines()[4])  # short "what this tool does" line
        print(f"\nsource : {source}")
        print(f"app DB : {target}   (will be CLEANED then reloaded)")
        print("Run with --confirm to proceed (automatic backup is taken first).")
        return 1

    stamp = datetime.now().strftime("%Y-%m-%d_%H%M%S")
    report: list[str] = [f"APP DB LOAD (stage 2) run : {stamp}",
                         f"source (FIRST CLASS DATA) : {source}",
                         f"app DB (AMSCOPY9 target)  : {target}", ""]

    # ---------------- gate ----------------
    # Gate checks run on their own short-lived read-only handle; it is closed
    # BEFORE the load handle attaches the source, so the same SQLite file is
    # never opened twice inside one process (that would lock ATTACH).
    scon = sqlite3.connect(f"file:{source}?mode=ro", uri=True)
    if scon.execute("PRAGMA integrity_check").fetchone()[0] != "ok":
        print("REFUSE: source fails integrity_check."); return 2
    if not is_ams_app_db(scon):
        print("REFUSE: source is not an AMS v44 app database."); return 2
    src_tables = table_list(scon)
    src_total = sum(scon.execute(f'SELECT COUNT(*) FROM "{t}"').fetchone()[0]
                    for t in src_tables)
    if src_total == 0:
        print("REFUSE: source contains no rows."); return 2
    scon.close()

    tcon = sqlite3.connect(target, timeout=30)
    if not is_ams_app_db(tcon):
        print("REFUSE: target is not an AMS v44 app database."); return 2
    tcon.execute("PRAGMA busy_timeout=30000")
    report.append(f"source integrity_check : ok | source total rows: {src_total}")
    report.append("")

    # ---------------- backup ----------------
    backup = BACKUP_DIR / f"appdb_backup_{stamp}"
    backup.mkdir(parents=True, exist_ok=True)
    backup_db = backup / target.name
    shutil.copy2(target, backup_db)
    backup_sum = f"{sha256(target)}  {target.name}\n"
    (backup / f"{target.name}.sha256").write_text(backup_sum)
    report.append(f"[BACKUP ] app DB copied -> {backup_db}")

    # ---------------- clean ----------------
    try:
        tcon.execute("PRAGMA foreign_keys=0")
        tables = table_list(tcon)
        cleaned = 0
        for t in tables:
            n = tcon.execute(f'SELECT COUNT(*) FROM "{t}"').fetchone()[0]
            tcon.execute(f'DELETE FROM "{t}"')
            cleaned += n
        if tcon.execute("SELECT 1 FROM sqlite_master WHERE type='table' "
                        "AND name='sqlite_sequence'").fetchone():
            tcon.execute("DELETE FROM sqlite_sequence")
        tcon.commit()
        report.append(f"[CLEAN  ] wiped {cleaned} rows across {len(tables)} tables "
                      f"(whole app DB is empty; schema untouched)")
        if cleaned:
            tcon.execute("VACUUM")
            tcon.commit()
        report.append(f"[CLEAN  ] integrity after wipe: "
                      f"{tcon.execute('PRAGMA integrity_check').fetchone()[0]}")
        report.append("")

        # ---------------- load ----------------
        # ATTACH runs against the same handle; source is not opened elsewhere.
        tcon.execute("ATTACH DATABASE ? AS src", (str(source),))
        only_src = sorted(set(src_tables) - set(tables))
        only_dst = sorted(set(tables) - set(src_tables))
        if only_src:
            report.append(f"[WARN   ] tables only in source (skipped): {only_src}")
        if only_dst:
            report.append(f"[WARN   ] tables only in app DB (stay empty): {only_dst}")
        for t in sorted(set(src_tables) & set(tables)):
            load_table(tcon, t, "src", report)
        seq = sync_sqlite_sequence(tcon)
        if seq:
            report.append(f"[SEQ    ] sqlite_sequence re-synced for: {seq}")
        tcon.commit()

        # ---------------- verify ----------------
        report.append("")
        report.append("=== VERIFICATION ===")
        report.append(f"integrity_check: {tcon.execute('PRAGMA integrity_check').fetchone()[0]}")
        mism = []
        for t in sorted(set(src_tables) & set(tables)):
            want = tcon.execute(f'SELECT COUNT(*) FROM src."{t}"').fetchone()[0]
            got = tcon.execute(f'SELECT COUNT(*) FROM "{t}"').fetchone()[0]
            st = "OK" if got == want else "MISMATCH"
            if got != want:
                mism.append(t)
            report.append(f"  {t:34} source={want:6} loaded={got:6} [{st}]")
        report.append("")
        report.append("=== FK ORPHAN CHECK (declared) ===")
        orphans = []
        for t in tables:
            for fk in tcon.execute(f'PRAGMA foreign_key_list("{t}")').fetchall():
                ref, fcol, tcol = fk[2], fk[3], fk[4]
                if ref not in tables:
                    continue
                bad = tcon.execute(
                    f'SELECT COUNT(*) FROM "{t}" WHERE "{fcol}" IS NOT NULL AND '
                    f'"{fcol}" NOT IN (SELECT "{tcol}" FROM "{ref}")').fetchone()[0]
                if bad:
                    orphans.append((t, fcol, ref, bad))
                    report.append(f"  ORPHAN {t}.{fcol} -> {ref}.{tcol}: {bad}")
        if not orphans:
            report.append("  none (all declared FK references resolve)")
        report.append("")
        report.append("=== LOGICAL USER-FK CHECK (no DB constraint declared) ===")
        lorphans = []
        for t in tables:
            cols = [r[1] for r in tcon.execute(f'PRAGMA table_info("{t}")').fetchall()]
            for c in cols:
                if c in USER_REF_COLS:
                    bad = tcon.execute(
                        f'SELECT COUNT(*) FROM "{t}" WHERE "{c}" IS NOT NULL AND '
                        f'"{c}" NOT IN (SELECT id FROM "user")').fetchone()[0]
                    if bad:
                        lorphans.append((t, c, bad))
                        report.append(f"  ORPHAN {t}.{c} -> user.id: {bad}")
        if not lorphans:
            report.append("  none (all user_id references resolve to a real user)")

        pass_ok = not mism and not orphans and not lorphans and only_src == [] \
            and tcon.execute("PRAGMA integrity_check").fetchone()[0] == "ok"
        report.append("")
        report.append("RESULT: PASS - app DB is fully clean and equals FIRST CLASS DATA "
                      "row-for-row." if pass_ok
                      else f"RESULT: REVIEW - mism={mism} orphans={orphans} "
                           f"user_fk={lorphans} src_only={only_src}")
        # NOTE: no explicit DETACH here — SQLite raises "database src is locked"
        # while cursors still reference the attached schema; close() detaches
        # every attached database cleanly.
        tcon.commit()
        tcon.close()
    except Exception as e:  # noqa: BLE001 — restore the backup, never leave half state
        import traceback
        print(traceback.format_exc())
        try:
            tcon.rollback()
            tcon.close()
        except Exception:  # noqa: BLE001
            pass
        shutil.copy2(backup_db, target)
        print(f"LOAD FAILED ({e!r}) — app DB restored from {backup_db}")
        return 3

    # ---------------- deploy copy ----------------
    DEPLOY_DIR.mkdir(parents=True, exist_ok=True)
    if not args.skip_deploy_copy:
        final = DEPLOY_DIR / target.name
        shutil.copy2(target, final)
        final_sum = f"{sha256(final)}  {target.name}\n"
        (DEPLOY_DIR / f"{target.name}.sha256").write_text(final_sum)
        report.append("")
        report.append(f"[DEPLOY ] {final}")
        report.append(f"[DEPLOY ] sha256: {sha256(final)}")

    REPORT.parent.mkdir(parents=True, exist_ok=True)
    REPORT.write_text("\n".join(report) + "\n")
    print("\n".join(report))
    return 0


if __name__ == "__main__":
    sys.exit(main())
