#!/usr/bin/env python3
"""
Safe migration: old production data (ahmed_cement.db) -> new v44 schema
(NewData/ahmed_cement_v44_fresh.db).

SUPERSEDED ENTRY POINT — kept as the documented re-run command.

This was the original hand-written converter (2026-09-09).  On 2026-09-10 it
was replaced by a thin wrapper around the packaged **"migrate tool"** engine
(`migrate tool/migrate_engine.py`), which carries the audit fixes (D-1…D-6),
the gap fixes (G1…G5: v4.4-OLD refusal, NOT NULL pre-flight, value parity,
NULL-column report) and the decided **no-void purge policy** (the new
database holds no voided/cancelled rows — 87 rows on this dataset).  The
wrapper keeps the SAME inputs, output path and report artifact as the
original script, so everything documented in MIGRATION_NOTES.md /
MIGRATION_REPORT.md still holds — except the output now reflects the purge
policy (29,179 rows instead of 29,266).

Non-destructive: writes ONLY to "FIRST CLASS DATA/ahmed_cement_migrated.db"
(+ .report.txt / .report.json sidecars copied into migration_report.txt).
Originals and the pre-migration backup are never touched.

Run:  python3 "FIRST CLASS DATA/migrate.py"
Exit: 0 = RESULT: PASS · 3 = REVIEW · 2 = input error
"""
import os
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent  # "data lab for migration old to new"
REPO = ROOT.parent

OLD = ROOT / "ahmed_cement.db"
NEW = ROOT / "NewData" / "ahmed_cement_v44_fresh.db"
OUT = HERE / "ahmed_cement_migrated.db"
REPORT = HERE / "migration_report.txt"

sys.path.insert(0, str(REPO / "migrate tool"))
from migrate_engine import MigrationError, run_migration  # noqa: E402


def main() -> int:
    if not OLD.exists() or not NEW.exists():
        print(f"ERROR: missing input: {OLD if not OLD.exists() else NEW}")
        return 2
    try:
        report = run_migration(
            OLD,
            NEW,
            out_path=OUT,
            overwrite=True,          # idempotent: deletes any prior output
            purge_voided=True,       # policy: no voided data in the new database
            progress=lambda pct, msg: print(f"[{pct:3d}%] {msg}"),
        )
    except MigrationError as e:
        print(f"ERROR: {e}")
        return 2

    # Keep the documented report artifact next to the output.
    text = report["text"]
    try:
        REPORT.write_text(text, encoding="utf-8")
    except OSError:
        pass

    print()
    print(text)
    print()
    print("Output : ", OUT)
    print("Report : ", REPORT)
    print("STATUS : ", report["status"])
    return 0 if report["ok"] else 3


if __name__ == "__main__":
    sys.exit(main())
