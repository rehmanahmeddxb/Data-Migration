# Data Migration — Old data → new v44 schema

**Date:** 2026-09-09  
**Source (old):** `ahmed_cement.db`  (production data, SQLite, ~7.6 MB)  
**Schema (new):** `NewData/ahmed_cement_v44_fresh.db`  (v44 fresh install + seed)  
**Result:** `FIRST CLASS DATA/ahmed_cement_migrated.db`  (this folder)  
**Script:** `FIRST CLASS DATA/migrate.py`  (re-runnable, idempotent)  
**Report:** `FIRST CLASS DATA/migration_report.txt`

> **STATUS (2026-09-10):** `migrate.py` is now a thin wrapper around the
> packaged **"migrate tool"** engine (same inputs / output / report), and the
> committed output was **regenerated with the current no-void purge policy**:
> it now holds **29,179 rows** (the 2026-09-09 file held 29,266 including 87
> voided/cancelled rows, which the policy removes: 61 `is_void=1` + 24
> cancelled `entry` + 2 cascaded children).  Everything below remains true;
> wherever a row count is quoted, the regenerated (post-purge) numbers apply.
> Re-run any time with the same command — the original pre-policy output can
> be reproduced with the tool's `--keep-voided` flag.

---

## 1. What kind of database is this?

- **SQLite** (single-file, serverless). It is a **Django** business application for a cement
  company ("ahmed_cement"): accounting, chart of accounts, direct sales, invoices, payments,
  bookings, GRN / receiving, deliveries & rents, materials, clients, suppliers, audit logs, etc.
- The **old DB** (`ahmed_cement.db`) was in **WAL mode** with `-wal`/`-shm` files present — that
  means the most recent committed transactions were sitting in the write-ahead log, NOT yet folded
  into the main file. If you had copied the `.db` alone you would have **lost** those last
  transactions. We checkpointed the WAL (`PRAGMA wal_checkpoint(TRUNCATE)`) before doing anything.
- The **new DB** is a **v44 schema** fresh instance. It is a *superset* of the old schema: every
  old table/column still exists; the new schema only **adds** columns (e.g. `idempotency_key`,
  `idempotency_payload_hash`, `access_mode`, account wallet/cash fields) and 5 new tables
  (`cash_day_account_position`, `cash_day_lock`, `migration_run`, `migration_row`,
  `migration_mapping`). No columns were renamed or removed → a clean column-copy migration.

## 2. How this migration was done (safely, no data loss)

1. **Backup first.** A faithful restore-point was copied to `backups/pre_migration_2026-09-09_121818/`
   (both DBs + metadata) *after* the WAL was folded in. Originals and the fresh DB were never modified.
2. **Non-destructive output.** The result is written to a **new file** in this folder; the old DB,
   the fresh DB, and the backup are all untouched. You swap the app over only after verifying.
3. **Schema base = new v44.** The output starts as an exact copy of `ahmed_cement_v44_fresh.db`,
   so it has the correct DDL, indexes, the 5 new tables, and the migration-tracking tables.
4. **Business tables loaded from OLD with original IDs.** For every shared business table we
   inserted all old rows using their **original primary keys**, so every foreign-key relationship
   stays internally consistent (no broken links).
5. **Users are merged, not overwritten.** The fresh DB's 2 admin users (`Admin`, `Adnan Ahmed`) are
   kept so login still works. The 7 old users are inserted with remapped IDs (3–9); username
   collisions were renamed to `*_legacy`. All `user_id` references in `audit_log`,
   `accounting_audit_log`, and `user_login_session` were remapped to the new IDs.
   (Note: these Django tables define **no DB-level foreign keys**, so references were remapped by
   column name and verified with a logical check.)
6. **Source data-quality issue handled.** The old `entry.auto_bill_no` (and a few other columns)
   contain duplicate values that already violate the source's own UNIQUE constraint. We load
   **every** row by staging through a constraint-stripped copy, then re-create the unique indexes
   where the data permits. The relaxed constraints are listed in `migration_report.txt`.

### Verification (all PASS)
- `PRAGMA integrity_check` → `ok`
- Per-table row counts: migrated == old for every business table (user == 2 new + 7 old = 9)
- Logical user-FK check: 0 orphaned `user_id` references
- No leftover staging tables

## 3. Important: what was NOT carried over (decisions you may want to revisit)

- For tables present in **both** DBs, the migrated DB uses the **OLD (real) data**. The fresh DB's
  first-run placeholder rows were **not** merged. The only tables where the fresh DB had rows the
  old DB lacked are seed/demo from the post-wipe init:
  - `cash_flow_entry` (537 rows), `cash_flow_entry_audit` (569), `cash_flow_party` (316) — cleared
  - `cash_day_account_position` (5), `cash_day_lock` (1) — cleared (new v44 feature, no old equivalent)
  - `migration_run` (1) — **kept** (validation-only record)
  If you actually want those fresh-DB seed rows preserved, tell me and I will switch to a
  "keep-all + remap" merge instead.

## 4. To put the migrated DB into production

The app expects a specific filename/path (the health snapshot shows
`/home/ahmedrehmanahmed1/instance/ahmed_cement_v44_fresh.db`). After you verify the result:
1. Stop the app.
2. Replace the app's DB file with `FIRST CLASS DATA/ahmed_cement_migrated.db`
   (keep a copy of the current one — you already have `backups/...`).
3. Restart the app and smoke-test: log in as `Admin`, open a client/invoice/sale, run a report.

## 5. What to do in the future (recommendations)

- **Never migrate by copying the `.db` file alone while it is in WAL mode.** Always
  `PRAGMA wal_checkpoint(TRUNCATE)` (or use the backup API) first, or you silently lose transactions.
- **Treat migrations as code.** Keep `migrate.py` (or proper Django
  `migrations` + a data-migration script) in version control; run it in staging before prod.
- **Use a real DB engine for production** (PostgreSQL/MySQL). SQLite is fine for single-user/desktop
  use but has no concurrency control, no online backups, and weaker constraint enforcement — which is
  exactly why the duplicate-`auto_bill_no` problem went unnoticed.
- **Add a `schema_version`/migration-history table** (already present) and record each run.
- **Automate verifications**: row-count diffs, `integrity_check`, and logical FK checks after every
  migration (the report does this).
- **Backups**: schedule periodic DB copies + `VACUUM`; keep the pre-migration backup until the next
  successful cutover.
- **Security**: `secret_key.txt` is committed in this repo and you said the repo is open/public.
  Committed secrets in a public repo are a leak — add it to `.gitignore`, remove it from git history,
  and rotate the key (this requires updating app config and re-encrypting any encrypted data).
