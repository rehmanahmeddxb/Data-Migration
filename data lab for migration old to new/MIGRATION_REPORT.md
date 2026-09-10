# Migration Report — Old Data → New (v44) Schema

**Project:** `Data-Migration` (ahmed_cement — Django business app for a cement company)
**Report date:** 2026-09-09
**Folder:** `data lab for migration old to new`
**Status:** ✅ COMPLETE & VERIFIED (no data loss, no broken references)
**Owner/contact:** Rehman Ahmed (repo owner)

> **STATUS (2026-09-10, second update — FINAL OLD DATA, current):** the owner
> replaced `ahmed_cement.db` with the **final old data** (md5 `74d9f4e7…`,
> 29,669 rows — production continued after the 09-09 copy: 22 tables grew).
> The migration was **re-run with the "migrate tool"**: `RESULT: PASS`,
> 29,669 → **29,586 rows** (93 voided/cancelled purged), value parity 0
> mismatches, **0 relaxed indexes** (`uq_entry_auto_bill_no` restored after
> the purge), then loaded into the app via `full_db_sync`
> (clean_replace, verification PASS, 0 FK violations). `instance/health_snapshot.json`
> was deleted before the first boot (runbook) and the **app runs with this
> data** (75/75 pages, real login, 154/154 tests). Dated backup of this run:
> **`FIRST CLASS DATA/2026-09-10/`** · plan + full commands:
> **`MIGRATION_PLAN.md`**.

> **STATUS (2026-09-10, first update):** this report records the 2026-09-09 event and
> remains the history of record. Two things have changed since:
> 1. `FIRST CLASS DATA/migrate.py` is now a wrapper around the packaged
>    **"migrate tool"** (repo root) with all audit fixes (D-1…D-6, G1…G5), and
>    the committed output `ahmed_cement_migrated.db` was **regenerated with
>    the no-void purge policy** — 29,179 rows (this report's §8 counts, e.g.
>    `entry 5259` / `payment 917`, are the pre-purge numbers; post-purge:
>    `entry 5204`, `payment 904`, `account_transaction 1046`, `waive_off 421`,
>    `material_return(_item) 81/110` — 87 voided/cancelled rows removed by
>    policy). The pre-policy archive is reproducible with `--keep-voided`.
> 2. The §11 security TODOs are addressed: the secret key was rotated and the
>    key files + duplicate data copies were removed from git tracking
>    (`.gitignore`); **purging the old key from git *history* still requires a
>    one-time `git filter-repo` on `main`** (manual step).

---

## 0. How to read this report (future-work tracking)
This document is the single source of truth for the migration. If you return to this later:
- **Re-run the migration:** `python3 "data lab for migration old to new/FIRST CLASS DATA/migrate.py"`
- **Per-table verification numbers:** `data lab for migration old to new/FIRST CLASS DATA/migration_report.txt`
- **Short notes:** `data lab for migration old to new/FIRST CLASS DATA/MIGRATION_NOTES.md`
- **Input DBs:** `data lab for migration old to new/ahmed_cement.db` (old) and
  `data lab for migration old to new/NewData/ahmed_cement_v44_fresh.db` (new schema)
- **Output DB:** `data lab for migration old to new/FIRST CLASS DATA/ahmed_cement_migrated.db`
- **Pre-migration backup:** `data lab for migration old to new/backups/pre_migration_2026-09-09_121818/`

> Open decisions still pending are listed in **Section 11 (TODO / Open items)**.

---

## 1. What kind of database is this?
- **Engine:** SQLite (single-file, serverless, file-based). No separate server process.
- **Application:** A **Django** business application ("ahmed_cement") for a cement trading/business:
  chart of accounts & accounting, direct sales, invoices, payments, bookings, GRN / goods receiving,
  deliveries & delivery rents, materials, clients, suppliers, follow-ups, and extensive audit logging.
- **Two databases involved:**
  | Role | File | Mode | Size | Rows (approx) |
  |------|------|------|------|---------------|
  | OLD (production data) | `ahmed_cement.db` | was WAL | 7.68 MB | clients 323, sales 2719, sale_items 5048, invoices 2378, entries 5259, payments 917, users 7, … |
  | NEW (v44 schema) | `NewData/ahmed_cement_v44_fresh.db` | delete | 2.81 MB | fresh post-wipe install with seed + new tables |
- The new schema is a **strict superset** of the old one: every old table and column still exists;
  the new schema only **adds** columns (e.g. `idempotency_key`, `idempotency_payload_hash`,
  `access_mode`, account wallet/cash fields) and 5 new tables
  (`cash_day_account_position`, `cash_day_lock`, `migration_run`, `migration_row`, `migration_mapping`).
  **No columns were renamed or removed** → the migration is a clean column-copy.

---

## 2. Why a naive copy would have lost data (the WAL trap)
The old DB was in **WAL (Write-Ahead Log) mode** with `-wal`/`-shm` files present. That means the
most recent committed transactions were sitting in the log, **not yet folded into the main `.db` file**.
- ❌ Copying `ahmed_cement.db` alone → those last transactions are **silently lost**.
- ✅ Fix: `PRAGMA wal_checkpoint(TRUNCATE)` run against the live DB before any copy, folding the WAL
  into the main file. After this the `-wal`/`-shm` files disappeared and `integrity_check` returned `ok`
  with row counts identical to the pre-checkpoint reads.

---

## 3. Pre-migration safety steps (all done)
1. **WAL checkpoint** on `ahmed_cement.db` (folded pending transactions into the file).
2. **Full backup** copied to
   `backups/pre_migration_2026-09-09_121818/` containing both DBs + `secret_key.txt`,
   `health_snapshot.json`, `README.md`. This is the restore point; originals were never modified.
3. **Non-destructive target:** the migrated result is written to a **new file**
   (`FIRST CLASS DATA/ahmed_cement_migrated.db`). The old DB, the fresh DB, and the backup remain intact.

---

## 4. Migration strategy
Goal: bring the OLD business data into the NEW v44 schema **without breaking anything and with zero
data loss**. Approach:

1. **Base = new v44 schema.** Copy `ahmed_cement_v44_fresh.db` to the output file, so the result has
   the exact v44 DDL, indexes, the 5 new tables, and the migration-tracking tables.
2. **Business tables ← OLD, original IDs.** For every shared business table, insert **all** old rows
   using their **original primary keys**. Keeping IDs identical means every foreign-key relationship
   stays internally consistent (no broken links, no remap needed for most tables).
3. **Users merged (not overwritten).** The fresh DB's 2 admin users are kept (so login still works).
   The 7 old users are inserted with remapped IDs (3–9); username collisions renamed to `*_legacy`.
   All `user_id` references in `audit_log`, `accounting_audit_log`, `user_login_session` are remapped.
4. **System/config tables kept from the fresh DB** (migration infra, settings, locks, schema_version).
5. **New-only seed referencing replaced tables cleared** (`cash_day_*`).

> NOTE: These Django SQLite tables define **no DB-level FOREIGN KEY constraints** (Django enforces
> relations in Python). So references were remapped by explicit column name and verified with a
> *logical* FK check, not `PRAGMA foreign_key_list`.

---

## 5. Handling the source's own data-quality defect
The old `entry.auto_bill_no` column (and a few others) already contains **duplicate values that violate
the source's own UNIQUE constraint**. A plain `INSERT` fails on the first duplicate. To guarantee no row
loss:
- Each business table is loaded via a **constraint-stripped staging copy** (the secondary UNIQUE
  constraints are removed at DDL time, the data inserted, then the table is swapped in).
- Unique indexes are then re-created **where the data permits**. Any constraint that still cannot be
  applied (because the source data violates it) is **relaxed** and listed explicitly in
  `migration_report.txt` under `RELAXED`. Example: `uq_entry_auto_bill_no` (duplicate `auto_bill_no`).

---

## 6. User merge details
Final `user` table (9 rows):
| id | username | role | status | origin |
|----|----------|------|--------|--------|
| 1 | Admin | admin | active | NEW (kept) |
| 2 | Adnan Ahmed | admin | active | NEW (kept) |
| 3 | Admin_legacy | admin | active | OLD (renamed; collided with NEW `Admin`) |
| 4 | Rehman Ahmed | admin | active | OLD |
| 5 | Rizwan Ahmed | admin | active | OLD |
| 6 | Adnan Ahmed_legacy | admin | active | OLD (renamed; collided with NEW `Adnan Ahmed`) |
| 7 | Shujaat Muzaffar | admin | active | OLD |
| 8 | Ahmed Hassan | admin | inactive | OLD |
| 9 | Mohsan Javed | user | active | OLD |

`user_id` remap applied: old→new = {1→3, 2→4, 3→5, 4→6, 5→7, 6→8, 7→9}.

---

## 7. What was intentionally NOT carried over (decisions)
For tables present in **both** DBs, the migrated DB uses the **OLD (real) data**; the fresh DB's
first-run placeholder rows were **not** merged. The only tables where the fresh DB had rows the old DB
lacked are seed/demo from the post-wipe init:
- `cash_flow_entry` (537), `cash_flow_entry_audit` (569), `cash_flow_party` (316) — cleared
- `cash_day_account_position` (5), `cash_day_lock` (1) — cleared (new v44 feature, no old equivalent)
- `migration_run` (1) — **kept** (validation-only record preserved for traceability)

If these fresh-DB seed rows must be preserved, switch the script to a "keep-all + remap" merge
(see Section 11).

---

## 8. Verification (PASS)
- `PRAGMA integrity_check` → **ok**
- Per-table counts: migrated == old for every business table; `user` = 2 (new) + 7 (old) = 9.
  (Full table in `FIRST CLASS DATA/migration_report.txt`.)
- **Logical user-FK check:** 0 orphaned `user_id` references in `audit_log`, `accounting_audit_log`,
  `user_login_session`.
- No leftover staging (`tmp_*`) tables; journal mode = `delete`.

Key counts (old → migrated):
`client 323`, `direct_sale 2719`, `direct_sale_item 5048`, `invoice 2378`, `entry 5259`,
`payment 917`, `account_transaction 1059`, `user 9`.

---

## 9. How to reproduce / re-run
```bash
# From repo root
python3 "data lab for migration old to new/FIRST CLASS DATA/migrate.py"
```
The script is **idempotent** (deletes any prior output, copies the fresh schema, reloads old data).
It writes a fresh `migration_report.txt` and prints the verification summary.
Inputs are read from `ahmed_cement.db` (old) and `NewData/ahmed_cement_v44_fresh.db` (new schema);
output goes to `FIRST CLASS DATA/ahmed_cement_migrated.db`.

---

## 10. How to deploy (cutover)
1. Stop the application.
2. Replace the app's DB file with `FIRST CLASS DATA/ahmed_cement_migrated.db`
   (keep a copy of the current one — you already have `backups/pre_migration_2026-09-09_121818/`).
   The app expects a specific path/filename (health snapshot shows
   `/home/ahmedrehmanahmed1/instance/ahmed_cement_v44_fresh.db`).
3. Restart the app and smoke-test: log in as `Admin`, open a client / invoice / sale, run a report.

---

## 11. TODO / Open items (tracking for future)
- [ ] **Confirm seed-row decision (Section 7).** Keep excluded fresh-DB seed or merge it?
- [x] **secret_key.txt security** — *addressed 2026-09-10 (partial):* the key was **rotated**
      (new random value in all copies) and the key files + duplicate data copies were **removed
      from git tracking** (root `.gitignore`). ⬜ *Still manual:* purge the **old** key value from
      git *history* with a one-time `git filter-repo` on `main` (destructive to history — do it
      deliberately, then force-push and rotate again if the repo is public).
- [x] **Add a `.gitignore`** — *done 2026-09-10:* root `.gitignore` now excludes `*secret_key*`,
      SQLite journal sidecars (`*.db-wal/-shm/-journal`), `*.INCOMPLETE` quarantine files, and the
      duplicate backup/refresh data copies (the regression-suite fixture pair stays tracked).
- [ ] **Production engine.** Consider PostgreSQL/MySQL for concurrency + stronger constraint enforcement
      (the duplicate-`auto_bill_no` defect went unnoticed precisely because SQLite enforces little).
      Note: the restoration migration `AMSCOPY9/app/migrations/0001_restore_entry_auto_bill_unique_index.sql`
      now ships and applies once the 2 duplicate `SB-GRN` bill numbers are cleaned.
- [x] **Automate verifications** — *in place since 2026-09-10:* `migrate tool/test_migrate_engine.py`
      (18 stdlib tests: counts, values, indexes, FK orphans, purge cascade, failure quarantine) and
      `AMSCOPY9/tests/test_full_db_sidecar.py` (6 stdlib tests for the import sidecar gate) — wire
      them into CI whenever a CI runner exists.
- [ ] **Scheduled backups** (DB copy + `VACUUM`); keep pre-migration backup until next successful cutover.

## 12. File inventory (this folder)
```
data lab for migration old to new/
├── ahmed_cement.db                         # OLD production data (source)
├── secret_key.txt                          # app secret (see Section 11 security TODO)
├── README.md
├── NewData/
│   ├── ahmed_cement_v44_fresh.db           # NEW v44 schema (source)
│   ├── b.txt
│   ├── health_snapshot.json                # post-wipe health snapshot
│   └── secret_key.txt
├── FIRST CLASS DATA/
│   ├── ahmed_cement_migrated.db            # ✅ MIGRATED OUTPUT (use this)
│   ├── migrate.py                          # re-runnable migration script
│   ├── migration_report.txt                # full per-table verification
│   └── MIGRATION_NOTES.md                  # short notes
├── backups/
│   └── pre_migration_2026-09-09_121818/     # restore point (both DBs + metadata)
└── MIGRATION_REPORT.md                     # THIS REPORT
```
