# Data-Migration

AMS (ahmed_cement) **old → new v4.4** data migration: the plan, the tool, the
data lab, and the app that runs the result.

## Start here

| What | Where |
|---|---|
| **Migration plan** (operating plan + runbook + latest run record) | `data lab for migration old to new/MIGRATION_PLAN.md` |
| **Migration tool** (GUI / `--cli`, stdlib-only) | `migrate tool/` — README with 30-second pre-flight |
| **Migration history of record** (the 2026-09-09 event + status addenda) | `data lab for migration old to new/MIGRATION_REPORT.md` |
| **Current output** | `data lab for migration old to new/FIRST CLASS DATA/ahmed_cement_migrated.db` |
| **Dated backups of each run** | `data lab for migration old to new/FIRST CLASS DATA/<date>/` (latest: `2026-09-10/`) |
| **The app** (Flask ERP that runs the migrated data) | `AMSCOPY9/` — `main.py`, instance DB `instance/ahmed_cement_v44_fresh.db` |

## Current state (2026-09-10)

The **final old data** (`ahmed_cement.db`, 29,669 rows) was migrated into the
v4.4 schema with the packaged tool — `RESULT: PASS`, 29,586 rows (93
voided/cancelled purged by policy), value parity 0 mismatches, 0 relaxed
indexes — loaded into the app through `full_db_sync` (verification PASS),
and the **app runs with this data**: 75/75 pages render, real login works,
154/154 app tests pass. One day-1 item is open: save `/settings` once in the
app (see `MIGRATION_PLAN.md` §6).

## The pipeline in four stages

```
0 PRE-FLIGHT  migrate tool/check_template_sync.py   → template IN SYNC
1 MIGRATE     migrate tool/migrate_tool.py --cli …  → RESULT: PASS + sidecar report
2 LOAD        AMSCOPY9: python -m full_db_sync export|verify|import
3 BOOT+CHECK  rm instance/health_snapshot.json → start app → live_smoke,
              preflight_check, consistency_report, pytest
```

Full commands, decisions and verification results: `data lab for migration
old to new/MIGRATION_PLAN.md`.

## Audit trail (what was checked, fixed and re-proved)

- `MIGRATION_TOOL_PROCEDURE_AUDIT.md` — 63-procedure audit of the tool (10 defects found)
- `ANOTHER_OLD_FILE_VERIFICATION.md` — tool proven on a *different* old file, end-to-end into the app
- `MIGRATION_SUFFICIENCY_ASSESSMENT.md` — what "sufficient" covers; G1…G7 gaps and fixes
- `FINAL_MIGRATION_REPORT.md` — verdict + ship check; open items S1–S4
