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

No numbered migration files exist yet: the current v4.4 schema was built by
the ORM bootstrap and the `_ensure_*` helpers. The first future schema change
should ship as `0001_*.sql` here instead of a new helper function.
