"""full_db_sync command-line tool.

Export / verify / clean / import a full AMS database as a SQLite snapshot
(``.amsdb``).  Pure stdlib — no Flask, no pandas, no openpyxl.

Examples
--------
    # Export the live (or a working) database into a portable snapshot:
    python -m full_db_sync export --db instance/ahmed_cement_v44_fresh.db

    # Verify any snapshot / database file:
    python -m full_db_sync verify --db /path/to/AMS_FULL_xxx.amsdb

    # Clean every data row out of the target app database (schema stays):
    python -m full_db_sync clean --db instance/ahmed_cement_v44_fresh.db --confirm

    # Full sync: clean the target first, then load the snapshot fully
    # (users included).  The target is backed up automatically first.
    python -m full_db_sync import --source /path/to/AMS_FULL_xxx.amsdb \
        --db instance/ahmed_cement_v44_fresh.db --confirm

Writing commands require ``--confirm`` (repo convention for anything that
writes to the database).
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from full_db_sync.engine import (  # noqa: E402
    FullDbSyncError,
    backup_database,
    clean_all_data,
    export_snapshot,
    import_snapshot,
    verify_snapshot,
)


def _summarize(report: dict, verbose: bool = False) -> str:
    lines = []
    if report.get("mode"):
        lines.append(f"mode            : {report['mode']}")
    if report.get("verification"):
        lines.append(f"verification    : {report['verification']}")
    for key in ("out_path", "backup_path", "source_path", "target_path"):
        if report.get(key):
            lines.append(f"{key:<15}: {report[key]}")
    for key in ("rows_inserted_total", "rows_deleted_total", "total_rows",
                "fk_violations", "bytes"):
        if key in report:
            lines.append(f"{key:<15}: {report[key]}")
    if report.get("tables_only_target"):
        lines.append(f"tables only in target (left empty): {report['tables_only_target']}")
    if report.get("tables_only_source"):
        lines.append(f"tables only in source (skipped)   : {report['tables_only_source']}")
    if report.get("unique_indexes_relaxed"):
        lines.append("unique indexes relaxed (legacy duplicates):")
        for name in report["unique_indexes_relaxed"]:
            lines.append(f"    - {name}")
    if report.get("count_mismatches"):
        lines.append(f"count mismatches : {report['count_mismatches']}")
    if report.get("issues"):
        lines.append(f"issues          : {report['issues']}")
    if verbose and report.get("tables"):
        for t, n in sorted(report["tables"].items()):
            lines.append(f"    {t:<34} {n}")
    if verbose and report.get("rows_inserted"):
        lines.append("rows inserted per table:")
        for t, n in sorted(report["rows_inserted"].items()):
            lines.append(f"    {t:<34} {n}")
    return "\n".join(lines)


def _emit(report: dict, json_path: str, verbose: bool) -> int:
    if json_path:
        Path(json_path).write_text(json.dumps(report, indent=2, default=str))
    print(_summarize(report, verbose=verbose))
    if report.get("ok") is False or report.get("verification") == "FAIL":
        return 1
    return 0


def _cmd_export(a) -> int:
    try:
        report = export_snapshot(
            a.db,
            out_path=a.out,
            overwrite=a.overwrite,
            meta_extra={"requested_by": "cli"},
        )
    except FullDbSyncError as e:
        print(f"ERROR: {e}")
        return 2
    return _emit(report, a.json, a.verbose)


def _cmd_verify(a) -> int:
    report = verify_snapshot(a.db, expect_meta=a.expect_meta)
    return _emit(report, a.json, a.verbose)


def _cmd_clean(a) -> int:
    if not a.confirm:
        print("Refusing to run without --confirm (this wipes every data row).")
        return 2
    try:
        report = clean_all_data(
            a.db,
            backup=not a.no_backup,
            backup_dir=a.backup_dir,
            include_users=not a.keep_users,
        )
    except FullDbSyncError as e:
        print(f"ERROR: {e}")
        return 2
    return _emit(report, a.json, a.verbose)


def _cmd_import(a) -> int:
    if not a.confirm:
        print("Refusing to run without --confirm (this replaces the target data).")
        return 2
    try:
        report = import_snapshot(
            a.source,
            a.db,
            mode=a.mode,
            backup_target=not a.no_backup,
            backup_dir=a.backup_dir,
            include_users=not a.keep_users,
        )
    except FullDbSyncError as e:
        print(f"ERROR: {e}")
        return 2
    return _emit(report, a.json, a.verbose)


def _cmd_backup(a) -> int:
    out = backup_database(Path(a.db), backup_dir=a.backup_dir, prefix=a.prefix)
    if not out:
        print(f"ERROR: no database at {a.db}")
        return 2
    report = {"ok": True, "backup_path": str(out)}
    return _emit(report, a.json, a.verbose)


def main(argv=None) -> int:
    p = argparse.ArgumentParser(
        prog="full_db_sync",
        description="AMS full-database SQLite snapshot export / verify / clean / import.",
    )
    sub = p.add_subparsers(dest="command", required=True)

    def add_common(sp):
        sp.add_argument("--json", default="", help="write the full report to this JSON file")
        sp.add_argument("--verbose", action="store_true", help="print per-table detail")

    ex = sub.add_parser("export", help="export a database into a portable .amsdb snapshot")
    ex.add_argument("--db", required=True, help="source sqlite database (e.g. the app runtime DB)")
    ex.add_argument("--out", default="", help="output .amsdb path (default: next to the source)")
    ex.add_argument("--overwrite", action="store_true")
    add_common(ex)
    ex.set_defaults(func=_cmd_export)

    vf = sub.add_parser("verify", help="read-only verification of a snapshot/database")
    vf.add_argument("--db", required=True)
    vf.add_argument("--no-expect-meta", dest="expect_meta", action="store_false",
                    help="allow plain AMS database files without snapshot metadata")
    add_common(vf)
    vf.set_defaults(func=_cmd_verify)

    cl = sub.add_parser("clean", help="delete every data row from a database (schema stays)")
    cl.add_argument("--db", required=True)
    cl.add_argument("--no-backup", action="store_true")
    cl.add_argument("--backup-dir", default="")
    cl.add_argument("--keep-users", action="store_true",
                    help="do not delete user / user_login_session rows")
    cl.add_argument("--confirm", action="store_true", required=True,
                    help="required: this wipes the database")
    add_common(cl)
    cl.set_defaults(func=_cmd_clean)

    im = sub.add_parser("import", help="full sync: clean target first, then load the snapshot")
    im.add_argument("--source", required=True, help=".amsdb snapshot (or any AMS sqlite file)")
    im.add_argument("--db", required=True, help="target app database (must already have the AMS schema)")
    im.add_argument("--mode", default="clean_replace", choices=["clean_replace", "append"])
    im.add_argument("--no-backup", action="store_true")
    im.add_argument("--backup-dir", default="")
    im.add_argument("--keep-users", action="store_true",
                    help="do not wipe/import user rows (append/merge semantics)")
    im.add_argument("--confirm", action="store_true", required=True,
                    help="required: this replaces the target data")
    add_common(im)
    im.set_defaults(func=_cmd_import)

    bk = sub.add_parser("backup", help="make a consistent online backup copy of a database")
    bk.add_argument("--db", required=True)
    bk.add_argument("--backup-dir", default="")
    bk.add_argument("--prefix", default="pre_")
    add_common(bk)
    bk.set_defaults(func=_cmd_backup)

    args = p.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())
