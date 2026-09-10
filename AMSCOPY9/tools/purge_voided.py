#!/usr/bin/env python3
"""Purge every voided / cancelled row from an AMS database.

POLICY: the v4.4 database holds **no voided data**.  A database created before
this policy (for example one imported with the migration tool's ``--keep-voided``)
can be cleaned here.

    python3 tools/purge_voided.py --db instance/ahmed_cement_v44_fresh.db
        # report only — nothing is changed until you pass --apply

    python3 tools/purge_voided.py --db instance/ahmed_cement_v44_fresh.db --apply
        # backs the file up, purges, then verifies (integrity + FK + no voids)

What is removed
---------------
* every row with ``is_void = 1``
* ``entry`` rows marked ``CANCEL`` even when ``is_void = 0``
* children of removed parents (cascade), so no orphan references are left
* rows whose parent never existed

Stdlib only — no Flask, no SQLAlchemy, no pandas.
"""
from __future__ import annotations

import argparse
import importlib.util
import json
import os
import shutil
import sqlite3
import sys
from datetime import datetime
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
SERVICE = REPO_ROOT / "app" / "services" / "void_purge.py"


def _load_service():
    """Load app/services/void_purge.py without importing the Flask package."""
    spec = importlib.util.spec_from_file_location("_ams_void_purge", SERVICE)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _default_db() -> str:
    return (os.environ.get("APP_DB_PATH", "").strip()
            or str(REPO_ROOT / "instance" / "ahmed_cement_v44_fresh.db"))


def _verify(path: Path) -> dict:
    con = sqlite3.connect(f"file:{path.as_posix()}?mode=ro", uri=True)
    try:
        integrity = con.execute("PRAGMA integrity_check").fetchone()[0]
        try:
            fk = len(con.execute("PRAGMA foreign_key_check").fetchall())
        except sqlite3.OperationalError:
            fk = 0
        remaining = 0
        for (t,) in con.execute(
                "SELECT name FROM sqlite_master WHERE type='table' "
                "AND name NOT LIKE 'sqlite_%'"):
            cols = {r[1] for r in con.execute(f'PRAGMA table_info("{t}")')}
            if "is_void" in cols:
                remaining += con.execute(
                    f'SELECT COUNT(*) FROM "{t}" '
                    'WHERE COALESCE(CAST(is_void AS INTEGER), 0) = 1'
                ).fetchone()[0]
        cancels = con.execute(
            "SELECT COUNT(*) FROM entry WHERE "
            "UPPER(TRIM(COALESCE(type, ''))) = 'CANCEL' OR "
            "UPPER(TRIM(COALESCE(transaction_category, ''))) = 'CANCEL'"
        ).fetchone()[0]
        return {"integrity": integrity, "fk_violations": fk,
                "voided_rows_left": remaining, "cancelled_entries_left": cancels}
    finally:
        con.close()


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(
        description="Purge voided / cancelled rows from an AMS database")
    ap.add_argument("--db", default=_default_db(),
                    help="database file (default: $APP_DB_PATH, else the live v4.4 file)")
    ap.add_argument("--apply", action="store_true",
                    help="actually delete (default is a dry run)")
    ap.add_argument("--no-backup", action="store_true",
                    help="skip the automatic backup (not recommended)")
    ap.add_argument("--json", action="store_true", help="machine-readable output")
    args = ap.parse_args(argv)

    path = Path(args.db).expanduser()
    if not path.exists():
        print(f"ERROR: database not found: {path}", file=sys.stderr)
        return 2

    svc = _load_service()
    # Always plan first: the displayed report is the pre-purge plan, and the
    # backup (made below) must still contain the voided rows.
    report = svc.purge_voided_rows(path, dry_run=True)
    totals = report["totals"]

    if args.json:
        print(json.dumps(report, indent=2))
        if not args.apply:
            print("-- dry run: nothing was changed --", file=sys.stderr)
        return 0

    print(f"database : {path}")
    print(f"mode     : {'APPLY' if args.apply else 'DRY RUN (pass --apply to delete)'}")
    print()
    if not report["tables"]:
        print("  Nothing to purge — this database already holds no voided data.")
        return 0
    print(f"  {'table':30} {'total':>7} {'void':>6} {'cancel':>7} "
          f"{'cascade':>8} {'orphan':>7} {'kept':>7}")
    for t, r in sorted(report["tables"].items()):
        print(f"  {t:30} {r['total']:7} {r['removed_void']:6} "
              f"{r['removed_cancel']:7} {r['removed_cascade']:8} "
              f"{r['removed_missing_parent']:7} {r['kept']:7}")
    print(f"  {'TOTAL':30} {'':>7} {totals['removed_void']:6} "
          f"{totals['removed_cancel']:7} {totals['removed_cascade']:8} "
          f"{totals['removed_missing_parent']:7} {totals['rows_removed']:7}")
    print()

    if not args.apply:
        print("  Dry run only — no row was changed. Re-run with --apply to purge.")
        return 0

    # Back up FIRST — the safety net must hold the pre-purge data.
    if not args.no_backup:
        stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
        backup = path.with_name(f"{path.stem}.pre_void_purge_{stamp}{path.suffix}")
        shutil.copy2(path, backup)
        print(f"  backup   : {backup}")

    result = svc.purge_voided_rows(path, dry_run=False)
    print(f"  removed  : {result['deleted']} rows "
          f"(report above was the pre-purge plan: {totals['rows_removed']})")
    check = _verify(path)
    print(f"  integrity: {check['integrity']}   fk_violations: {check['fk_violations']}")
    print(f"  left over: {check['voided_rows_left']} voided rows, "
          f"{check['cancelled_entries_left']} cancelled entries")
    if check["integrity"] != "ok" or check["fk_violations"] or \
            check["voided_rows_left"] or check["cancelled_entries_left"]:
        print("  VERIFY FAILED — check the database before using it.", file=sys.stderr)
        return 1
    print("  VERIFY OK — this database now holds no voided data.")
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except Exception as exc:
        print(f"ERROR: void purge failed: {exc!r}", file=sys.stderr)
        sys.exit(1)
