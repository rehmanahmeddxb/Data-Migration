# Automatic Database Migrations (schema versioning)

> **Date:** 2026-09-09
> **What:** versioned, numbered SQL migrations that apply themselves at every
> application start — so future modules and schema changes migrate
> automatically, and the live schema version is always recorded.

---

## 1. The problem this solves

The ORM bootstrap (`db.create_all()` + the `_ensure_*` helpers in
`app/services/schema.py`) is automatic but **limited and unversioned**:

| Change                              | Today (before this doc)           | After this framework                |
| ----------------------------------- | --------------------------------- | ----------------------------------- |
| New model → new table               | ✅ auto at boot (`create_all`)    | ✅ auto at boot                     |
| New column on existing table        | ✅ auto, nullable, no default     | ✅ via `NNNN_*.sql` (full control)  |
| Index / unique constraint on an existing table | ❌ never created      | ✅ via `NNNN_*.sql`                 |
| Renames / type changes / backfills  | ❌ hand-written once              | ✅ versioned, applied everywhere    |
| Versioned record of the schema      | ❌ `schema_version` empty, `user_version=0` | ✅ stamped on every boot |

Because nothing was recorded, two databases that look alike could silently
have drifted apart (one known case already happened: `uq_entry_auto_bill_no`
was relaxed during the old-data import and was never re-created anywhere).

## 2. How it works

```
 app starts (or deploy reloads the app)
        │
        ▼
 db.create_all()  +  _ensure_* helpers   (ORM bootstrap, unchanged)
        │
        ▼
 app/services/auto_migrate.py.run_sql_migrations()
        │   reads app/migrations/*.sql  in sorted order
        │   applies only files NOT in migration_history
        │   (shared table with the release-deploy SQL runner)
        ▼
 records file in migration_history  →  stamps highest numeric prefix
 into schema_version (id=1)  →  report saved in app.config
```

* Runs inside the app factory after the ORM bootstrap — so tests, local runs,
  and **every deployment reload on the server** all get the migrations.
* Uses its own sqlite3 connection with a busy timeout; safe with WAL and
  DELETE journal modes, and tolerant of two processes booting at once (the
  loser sees the file already recorded and moves on).
* Destructive SQL (`DROP TABLE` / `TRUNCATE TABLE` / `DELETE FROM`) is
  blocked unless `MIGRATIONS_ALLOW_DESTRUCTIVE=1`.
* A migration failure is logged with `exc_info`; boot is **not** blocked
  (consistent with the app's other bootstrap helpers), and the file simply
  runs again on the next start.

## 3. Rules for writing a migration file

1. Name it `NNNN_short_description.sql` (four-digit prefix first), e.g.
   `0001_add_vendor_payments.sql`.
2. Never edit a file that has already been applied to any database — add a
   new higher-numbered file instead. `migration_history` is the record of
   truth.
3. Write it so a re-run is safe (`CREATE TABLE IF NOT EXISTS`,
   `INSERT OR IGNORE`, `UPDATE ... WHERE` guards). A failure mid-file leaves
   the earlier statements applied, and the file retries on the next boot.
4. Test it against a copy of a real database before deploying
   (`python3 -c` with sqlite3, or a pytest case like
   `tests/test_auto_migrations.py`).

## 4. Recipe for a future module

Say you add a `Vendor` + `VendorPayment` module next month:

1. Add the SQLAlchemy models (tables appear automatically via
   `create_all` — that part already works).
2. Add `app/migrations/0001_vendor_module.sql` containing whatever
   `create_all` cannot do:
   ```sql
   -- example only — indexes on existing tables:
   CREATE UNIQUE INDEX IF NOT EXISTS uq_vendor_code ON vendor(code);
   ALTER TABLE vendor ADD COLUMN credit_limit REAL;
   ```
3. Commit both. Every installation — this PC, the yard PCs, the live
   PythonAnywhere server — upgrades itself on its next app start/reload,
   recorded in `migration_history` and stamped into `schema_version`.
4. Check the result in the logs: `Applied schema migration 0001_vendor_module.sql`
   and `app.config["AMS_MIGRATION_REPORT"]` at runtime.

## 5. Relation to the old-data (.db) import path

* Old-format database files (the pre-v4.4 `ahmed_cement.db`) are **not**
  importable directly — the sync engine refuses them with a clear message
  before touching the target (column probe). That flow is unchanged: convert
  with `data lab for migration old to new/FIRST CLASS DATA/migrate.py`,
  then import the result via Import/Export Center → Full Database Snapshot.
* The framework's version stamp (`schema_version.version`) gives any future
  "upload a .db file" check an exact, deterministic way to detect which
  schema generation a file belongs to — the groundwork for a one-click
  old-format import later.
* The known relaxed unique index `uq_entry_auto_bill_no` **is** now shipped
  as `app/migrations/0001_restore_entry_auto_bill_unique_index.sql`.  It
  applies automatically on the first start **after** the duplicate
  `entry.auto_bill_no` values (`SB-GRN-1024`, `SB-GRN-1042` — entry ids
  9084/10116 and 9830/10117) are cleaned up; until then it retries at every
  boot (logged, boot not blocked) and the boot helper
  `_ensure_auto_bill_unique_indexes()` skips the index with a warning.

## 6. Configuration

| Key / env var                  | Default             | Meaning                                  |
| ------------------------------ | ------------------- | ---------------------------------------- |
| `MIGRATIONS_DIR`               | `app/migrations/`   | Folder with `NNNN_*.sql` files           |
| `MIGRATIONS_ALLOW_DESTRUCTIVE` | `0`                 | Permit `DROP TABLE` / `DELETE FROM` … in migration files |
