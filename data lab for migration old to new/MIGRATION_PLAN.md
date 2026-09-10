# MIGRATION PLAN — old data → new v4.4 (final old data)

**Owner:** Rehman Ahmed · **Plan date:** 2026-09-10 · **Status:** ✅ EXECUTED — PASS
**Companion reports:** `MIGRATION_REPORT.md` (the 2026-09-09 event + history) ·
`../MIGRATION_TOOL_PROCEDURE_AUDIT.md` · `../MIGRATION_SUFFICIENCY_ASSESSMENT.md` ·
`../FINAL_MIGRATION_REPORT.md` · `../ANOTHER_OLD_FILE_VERIFICATION.md`

> This is the operating plan for every **old → new** data move, plus the record
> of the run executed on **2026-09-10** with the owner's **final old data**
> (`ahmed_cement.db`, md5 `74d9f4e7…` — the production file as it stood at
> cutover; 29,669 rows, 64 tables). The v4.4 schema template is
> `NewData/ahmed_cement_v44_fresh.db` (md5 `79775c52…` — unchanged since the
> 2026-09-09 migration, re-proven `IN SYNC` with the app's models).
> The dated backup of this run: `FIRST CLASS DATA/2026-09-10/`.

---

## 0. The plan in one page

```
STAGE 0  PRE-FLIGHT   check_template_sync.py  → template must be IN SYNC
                      integrity_check on both files (the tool refuses corrupt input)

STAGE 1  MIGRATE      "migrate tool" (GUI or --cli)   old + template → *_migrated.db
                      only accept RESULT: PASS · never import a *.INCOMPLETE file

STAGE 2  LOAD         AMSCOPY9 full_db_sync:  export → .amsdb → verify → import
                      (clean_replace into instance/ahmed_cement_v44_fresh.db;
                       auto pre-import backup is written first)

STAGE 3  BOOT         delete instance/health_snapshot.json, then start the app —
                      the boot back-fills the 19 new v4.4 columns, restores
                      indexes/counters, seeds OPEN-KHATA, applies 0001_*.sql

STAGE 4  VERIFY       live smoke (login + every page) · preflight_check ·
                      consistency_report (must equal the OLD file's findings) ·
                      app test suite
```

**Golden rules** (each one is a real failure this repo has already seen):
1. Never copy a WAL-mode `.db` without the tool's backup-API staging — plain
   file copies silently lose the last committed transactions.
2. Only import files whose sidecar report says `RESULT: PASS`.
3. Before the app's **first** start after an import, delete
   `instance/health_snapshot.json` (or start once with `ALLOW_DB_DROP=1`) —
   the data-loss guard compares row counts against the old snapshot and
   refuses to start when they drop ≥ 50 rows / below 80 %.
4. The migrated DB is not "fully v4.4" until the app has booted on it once
   (NULL back-fill of the new columns happens at boot).

---

## 1. Inputs (locked for this run)

| Role | File | md5 | Size | Rows |
|---|---|---|---|---|
| OLD — **final old data** | `ahmed_cement.db` | `74d9f4e74d5565373ac75ac5c7ebc7cc` | 7.4 MB | 29,669 / 64 tables |
| NEW — v4.4 template | `NewData/ahmed_cement_v44_fresh.db` | `79775c520e792d572c3b88fd81d7bf5a` | 2.8 MB | 3,558 / 69 tables |
| Output | `FIRST CLASS DATA/ahmed_cement_migrated.db` (+ dated copy in `FIRST CLASS DATA/2026-09-10/`) | — | 7.5 MB | 29,586 / 69 tables |

The final old data is **the 2026-09-09 file plus six more weeks of business**
(vs the 09-09 backup): +1 client, +38 direct sales, +71 entries, +5 payments,
+16 pending bills, +3 bookings, … (22 tables grew; 42 unchanged). It arrived in
WAL mode with `-wal`/`-shm` sidecars — the tool's staging copy folds the WAL
in via the SQLite backup API and the source file is never opened for writing.

## 2. Decisions (unchanged from the 2026-09-09/10 sessions)

| Decision | Choice |
|---|---|
| Voided / cancelled rows | **Purged** (no voided data in the new database). `--keep-voided` reproduces a bit-for-bit archive. |
| Primary keys | **Original IDs kept** for every business table → no broken references. |
| Users | **Merged**: 2 template admins kept, 7 old users added (ids 3–9, collisions renamed `*_legacy`), all `user_id`/`created_by_id` remapped atomically. |
| Config/infra tables | `KEEP_FROM_NEW` (fresh template's settings/migration infra win). The old file carries no `settings` row → nothing to carry; see §6 day-1 item. |
| Duplicate bill numbers | Not an operator task any more: the duplicates were voided rows, the purge removes them and the tool **re-creates** `uq_entry_auto_bill_no` itself (`INDEX RESTORED`). |
| Transport | SQLite file (`.db`/`.amsdb`) only — the Excel path is retired (see `MIGRATION_TOOL_PROCEDURE_AUDIT.md` §1). |

## 3. Executed run — 2026-09-10 (all commands, verbatim)

```bash
# STAGE 0 — pre-flight
python3 "migrate tool/check_template_sync.py" \
        --template "data lab for migration old to new/NewData/ahmed_cement_v44_fresh.db"
#   → IN SYNC — 69/69 tables, 0 missing columns
python3 -m unittest test_migrate_engine        # (in "migrate tool/") → 20/20 OK

# STAGE 1 — migrate (output straight into the dated backup folder)
mkdir -p "data lab for migration old to new/FIRST CLASS DATA/2026-09-10"
python3 "migrate tool/migrate_tool.py" --cli \
  --old "data lab for migration old to new/ahmed_cement.db" \
  --new "data lab for migration old to new/NewData/ahmed_cement_v44_fresh.db" \
  --out "data lab for migration old to new/FIRST CLASS DATA/2026-09-10/ahmed_cement_migrated.db"
#   → RESULT: PASS · value parity 52 tables / 0 mismatches · index parity · 93 rows purged

# STAGE 2 — load through the app's own importer
cd AMSCOPY9
python3 -m full_db_sync export \
  --db "../data lab for migration old to new/FIRST CLASS DATA/2026-09-10/ahmed_cement_migrated.db" \
  --out  "../data lab for migration old to new/FIRST CLASS DATA/2026-09-10/AMS_FULL_20260910_migrated.amsdb"
python3 -m full_db_sync verify \
  --db  "../data lab for migration old to new/FIRST CLASS DATA/2026-09-10/AMS_FULL_20260910_migrated.amsdb"
python3 -m full_db_sync import \
  --source "../data lab for migration old to new/FIRST CLASS DATA/2026-09-10/AMS_FULL_20260910_migrated.amsdb" \
  --db instance/ahmed_cement_v44_fresh.db --confirm
#   → verification PASS · 29,586 rows in · 0 FK violations · auto backup:
#     instance/pre_full_db_import_ahmed_cement_v44_fresh_20260910-163051.db

# STAGE 3 — reset the data-loss baseline, then boot  (the "delete health snapshot" step)
rm -f instance/health_snapshot.json

# STAGE 4 — verify the app runs with this data
AMS_SMOKE_USER=Admin AMS_SMOKE_PASSWORD='…' python3 tools/live_smoke.py   # 75/75 pages
python3 tools/health/preflight_check.py --db instance/ahmed_cement_v44_fresh.db
python3 tools/consistency_report.py    --db instance/ahmed_cement_v44_fresh.db
python3 -m pytest -q                                                        # 154/154
```

(The instance DB was bootstrapped once with `PYTHONPATH=. python -m tools.init_v44`
+ one app boot before the import, because `full_db_sync import` requires a
target that already carries the current schema.)

## 4. Results of the 2026-09-10 run

| Check | Result |
|---|---|
| Template sync | **IN SYNC** (69/69 tables, 0 missing columns) |
| Tool regression suite | **20/20 pass** |
| Migration | **RESULT: PASS** in ~1.4 s · 69/69 tables |
| Rows | 29,669 old → **29,586** migrated (93 voided/cancelled purged: 62 void + 26 cancel + 5 cascade) |
| Value parity | 52 tables, **0 mismatches** (only the documented user-id remap) |
| Indexes | **Full template parity, 0 relaxed** — `uq_entry_auto_bill_no` restored after the purge |
| FK / integrity | 0 orphan FKs · 0 logical user-FK orphans · `integrity_check ok` at every stage |
| Import into app | clean_replace, **verification PASS**, 29,586 inserted, 0 FK violations |
| Boot on loaded DB | OK — 19 new v4.4 columns back-filled (0 NULLs), 0001_*.sql applied, counts read from migrated data (325 clients incl. OPEN-KHATA seed, 2,757 sales, 5,273 entries, 908 payments) |
| Live HTTP | real `/login` (CSRF) → `/`, `/clients`, `/direct_sales`, `/ledger/1`, `/settings` all 200; `/api/clients/search` returns the migrated names |
| Consistency report | **same findings as the OLD file** (legacy anomalies only; the migration added none — 3 issues vs the old file's 4, the health-snapshot one is now OK) |
| App test suite | **154/154 pass** |

Key counts old → migrated: `client 324` · `direct_sale 2,757` ·
`direct_sale_item 5,114` · `entry 5,330→5,273` · `invoice 2,389` ·
`payment 922→908` · `pending_bill 1,718` · `booking 430` ·
`account_transaction 1,071→1,058` · `user 2+7=9`.

## 5. Where everything lives

```
data lab for migration old to new/
├── MIGRATION_PLAN.md                 ← THIS DOCUMENT (the plan + 2026-09-10 record)
├── MIGRATION_REPORT.md               ← history of record (2026-09-09 event + addenda)
├── ahmed_cement.db                   ← FINAL OLD DATA (source of truth, never modified)
├── NewData/ahmed_cement_v44_fresh.db ← v4.4 schema template
├── FIRST CLASS DATA/
│   ├── ahmed_cement_migrated.db      ← current migration output (always latest run)
│   ├── migrate.py                    ← one-command re-run (wrapper around the tool)
│   ├── migration_report.txt          ← latest run's report
│   ├── MIGRATION_NOTES.md            ← short notes
│   └── 2026-09-10/                   ← DATED BACKUP of this run (db + .amsdb + reports
│                                        + byte-exact input copies in inputs/)
├── backups/pre_migration_2026-09-09_121818/   ← restore point of the 09-09 run
└── AMSCOPY9_FULL_REFRESH_2026-09-09/          ← the 09-09 full-refresh record
AMSCOPY9/instance/ahmed_cement_v44_fresh.db    ← the RUNNING app database (loaded 2026-09-10)
```

## 6. Day-1 runbook (before real business use)

1. ~~Delete `instance/health_snapshot.json` before the first boot~~ — **done**
   (2026-09-10); the app re-baselelines the snapshot itself on boot.
2. **Open `/settings` in the app and save once.** The database has **no
   `settings` row** (the old file never had one), so company name, tax rate
   and bill prefixes are unset and `allow_global_negative_stock` reads as
   **OFF** while 55 materials sit in negative stock — new sales of those
   materials are refused until the row is saved (preflight flags this as the
   only blocker). This is a legacy data condition, not a migration error.
3. Accept or repair the known legacy anomalies (identical before/after the
   migration, listed by `tools/consistency_report.py` and
   `tools/post_migration_audit/audit_findings.py`): 87 orphaned invoices,
   4 sales without stock entries, ~1.5k re-used manual bill numbers,
   2 duplicate client names, negative stock on 55 materials.
4. Keep the pre-migration inputs until the next successful cutover; scheduled
   backups (DB copy + `VACUUM`) remain a TODO (see `MIGRATION_REPORT.md` §11).

## 7. Re-running / changing your mind

- **Re-run the whole migration** (idempotent, same inputs):
  `python3 "data lab for migration old to new/FIRST CLASS DATA/migrate.py"`
  — but prefer the dated-folder form of §3 so each run keeps its own backup.
- **Keep voided rows** (bit-for-bit archive instead of the purge): add
  `--keep-voided`.
- **Restore this exact run**: see `FIRST CLASS DATA/2026-09-10/README.md`.
- **A newer old file arrives**: replace `ahmed_cement.db`, make a new dated
  folder, run §3 unchanged — the tool is data-agnostic for this schema
  lineage (proven on a different dataset, see `ANOTHER_OLD_FILE_VERIFICATION.md`).
