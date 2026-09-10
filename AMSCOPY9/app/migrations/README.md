# app/migrations — automatic schema migrations

Numbered SQL files here are applied **automatically at every application
start** (which includes every deployment reload), by
`app/services/auto_migrate.py`.

## How to add a migration (future module / schema change)

1. Create a file named `NNNN_short_description.sql` (zero-padded number,
   higher than any existing file). Example: `0001_add_vendor_module.sql`.
2. Write plain SQLite DDL/DML — e.g. `CREATE TABLE ...`, `ALTER TABLE ...
   ADD COLUMN ...`, `CREATE UNIQUE INDEX ...`.
3. Restart the app (or deploy). The runner applies pending files in sorted
   order, records each in the `migration_history` table, and stamps the
   highest applied number into `schema_version`.

## Rules

- **Never edit an already-applied file.** Migration history records what ran
  on each database; changing an old file does not re-run it. Add a new
  higher-numbered file instead.
- Keep statements **safe to re-run** (`IF NOT EXISTS`, `INSERT OR IGNORE`):
  if a file fails mid-way, the statements before the failure stay applied and
  the file runs again on the next boot.
- **Destructive SQL is blocked by default**: `DROP TABLE`, `TRUNCATE TABLE`,
  `DELETE FROM` require `MIGRATIONS_ALLOW_DESTRUCTIVE=1` on the server (set
  deliberately, then remove).
- Files that are not `*.sql` (like this README) are ignored.
- New ORM tables also get created by `db.create_all()` on boot — migrations
  are for everything `create_all` cannot do: new columns with defaults,
  indexes / unique constraints on existing tables, renames, backfills.

## Current state

- `0001_restore_entry_auto_bill_unique_index.sql` — restores the partial
  UNIQUE index `uq_entry_auto_bill_no` that the old-data migration relaxes
  while loading, because the legacy data carries two duplicate GRN bill
  numbers (`SB-GRN-1024`, `SB-GRN-1042`; entry ids 9084/10116 and
  9830/10117).

  **This normally applies on the first start with nothing to do**: in each
  pair one row is a *voided* row, and the migration's void policy purges it, so
  no duplicate survives and the index can be created. Re-verified 2026-09-10 on
  the committed production pair — the migration tool now recreates the index
  itself (`INDEX RESTORED` in its report), and this file is the safety net that
  keeps `migration_history` consistent for any database that still ships with
  the relaxation, e.g. a `--keep-voided` archive.

  Only if a file *still* holds live duplicates (bit-for-bit archive, or legacy
  data re-imported) does it fail at `CREATE UNIQUE INDEX`, log, and retry at
  the next boot — boot is never blocked, and
  `_ensure_auto_bill_unique_indexes()` skips the index with a warning while
  duplicates exist. Resolving such a duplicate is a business decision (keep one
  row per bill number via `tools/repair_controlled/` after a backup).

The next schema change should ship as `0002_*.sql` here instead of a new
helper function.
