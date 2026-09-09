# Report — Automatic Versioned Database Migrations for AMS

> **Date:** 2026-09-09
> **Author:** Arena coding session (branch `arena/01a0871c-data-migration`)
> **Commit:** `67e8353` — "Automatic versioned schema migrations run at every app boot"
> **Scope:** Database migration strategy for AMSCOPY9 — how old data moves into the v4.4 schema today, and how future schema changes migrate automatically.

---

## 1. Background (why this work exists)

The user's requirements, as raised during this session:

1. **Which database does the app run on?** — Confirm the live DB file and what the last PR changed.
2. **"If I do a complete data reset via Settings and then import my old DB file, will it load cleanly?"** — Understand the old-data restore path.
3. **"If we add new modules in future, will the schema/database auto-migrate?"** — The core goal behind this report.
4. **"How do we migrate the old `ahmed_cement.db` (non-v4.4 schema) to the new v4.4 database?"** — Manual or automatic, and exactly how.
5. **Which approach is better — a one-click old-file import (Option 1) or automatic versioned migrations (Option 2)?**

---

## 2. Current state (audited findings)

### 2.1 The application database

| Item | Value |
|---|---|
| Live database file | `AMSCOPY9/instance/ahmed_cement_v44_fresh.db` |
| Format | SQLite, single file, v4.4 schema |
| Tables | 69 |
| Retired files the app deletes at boot | `ahmed_cement.db`, `ahmed_cement_v44.db` (+ sidecars) |
| Old-format files (data lab) | `data lab for migration old to new/ahmed_cement.db` — 64 tables, 29,263 rows, Django-era schema, WAL-checkpointed backup under `backups/pre_migration_2026-09-09_121818/` |

### 2.2 Schema bootstrap before this change

| Change | Behaviour before this change |
|---|---|
| New model → new table | ✅ created automatically at boot (`db.create_all()`) |
| New column on existing table | ✅ added automatically (nullable, no default) by `_ensure_model_columns()` |
| Index / unique constraint on existing tables | ❌ never created |
| Renames / type changes / backfills | ❌ hand-written one-time helpers |
| Version record | ❌ `schema_version` table empty; SQLite `user_version = 0` |
| Consequence | Two databases could silently drift apart with no way to detect it. One real case: `uq_entry_auto_bill_no` was relaxed during the old-data import and never re-created anywhere. |

---

## 3. Question 2 & 4 — moving the old data (tested end-to-end)

### 3.1 Can the raw old `ahmed_cement.db` be imported directly?

**No — by design it refuses safely.** The sync engine (`full_db_sync/engine.py`) treats the target (app) schema as authoritative: it checks every shared table's columns and aborts **before deleting or inserting anything** if the file lacks columns the app needs.

Real test result (copies, originals untouched):

```
Source: old ahmed_cement.db — integrity ok, 0 FK violations, 29,263 rows, 64 tables
Result: REFUSED — "Table 'user' in the target needs columns the source lacks:
        ['access_mode']. Export a fresh snapshot from the same AMS version..."
Target rows after: unchanged (nothing deleted, nothing half-imported)
```

Why it refuses — the schema really differs:

| Difference | Detail |
|---|---|
| Tables only in the app | 5 — `migration_run`, `migration_row`, `migration_mapping`, `cash_day_account_position`, `cash_day_lock` |
| Columns the app needs but the old file lacks | 20 across 6 tables — e.g. `user.access_mode`; `account` wallet/cash/linked-entity columns (14); `account_transaction.reason`, `idempotency_key`; `idempotency_payload_hash` on `direct_sale`, `payment`, `supplier_payment` |
| Columns old file has that the app lacks | 0 |

### 3.2 The working old-data migration path (2 stages, ~1 second total)

**Stage 1 — schema conversion (manual start, automatic work):**

```
data lab for migration old to new/
├── ahmed_cement.db                       ← input (old)
├── NewData/ahmed_cement_v44_fresh.db     ← v4.4 base (schema template + seed)
└── FIRST CLASS DATA/migrate.py           ← converter (idempotent)
```

```bash
cd "/home/user/Data-Migration/data lab for migration old to new"
python3 "FIRST CLASS DATA/migrate.py"
```

What it does automatically (fresh run on copies — `RESULT: PASS`, 0.5 s):
- Starts from the v4.4 fresh DB → correct new schema, 5 new tables, indexes.
- Loads **every old row with original IDs** through constraint-stripped staging → duplicates cannot drop a row.
- **Duplicates handled:** `entry` has 2 duplicate bill numbers (`SB-GRN-1024`, `SB-GRN-1042`). All 5,259 `entry` rows kept; only `uq_entry_auto_bill_no` relaxed (`RELAXED=['uq_entry_auto_bill_no']`); every other unique index re-created.
- **Users merged:** 2 fresh admins kept + 7 old users inserted = 9; all FK references to `user` remapped.
- **Verification built in:** per-table parity `old=… migrated=… expect=… [OK]` for all 69 tables, FK orphan check, logical user-FK check → `migration_report.txt`.
- Non-destructive: originals never touched; writes only `FIRST CLASS DATA/ahmed_cement_migrated.db`.

**Stage 2 — load into the live app DB (click or CLI, same engine):**

- **Click:** Import/Export Center → Full Database Snapshot (.db) → Import → select `FIRST CLASS DATA/ahmed_cement_migrated.db` → "Full sync — clean all data first".
- **CLI:**
  ```bash
  cd /home/user/Data-Migration/AMSCOPY9
  python3 -m full_db_sync import \
    --source "/home/user/Data-Migration/data lab for migration old to new/FIRST CLASS DATA/ahmed_cement_migrated.db" \
    --db /home/user/Data-Migration/AMSCOPY9/instance/ahmed_cement_v44_fresh.db \
    --backup-dir instance/migration --json import_report.json --confirm
  ```
- Real test result: `verification: PASS` — 69/69 tables shared, 29,270 rows deleted, 29,266 inserted, exact row-count parity, 0 FK violations, automatic backup created first.
- Users are replaced by the file's users (full sync). Use `append` mode or `--keep-users` to keep users added in the app afterwards.

**Note on the Settings reset:** not required — the full-db import itself wipes all rows first (and takes a backup). The Settings "Granular Data Wipe" keeps users/settings/audit, which makes it only a partial reset for this purpose.

---

## 4. Question 5 — which option is better, and why Option 2 was chosen

| | Option 1 — one-click old-file import inside the app | Option 2 — automatic versioned migrations ✅ |
|---|---|---|
| Problem it solves | A **one-time transition** that already has a proven 2-command tool | The **permanent goal**: future modules/schema changes auto-migrate everywhere |
| Gap it addresses today | Convenience only | Real gap: no versioning, no index/rename/backfill migration path |
| Risk | Re-embedding a 11k-line legacy converter into the app (duplicate code, fragile, hard to test) | Small, safe, reversible, unit-testable |
| Future value | — | Version stamping also enables Option 1 later (exact old-format detection by version) |

**Decision: Option 2**, because it fixes the actual requirement (auto-migrating schema going forward), is low-risk and fully testable, and is the foundation Option 1 would need anyway.

---

## 5. What was implemented (Option 2)

### 5.1 New/changed files

| File | Purpose |
|---|---|
| `AMSCOPY9/app/services/auto_migrate.py` | New — versioned SQL migration runner (pure stdlib sqlite3, no Flask dependency for tests) |
| `AMSCOPY9/app/__init__.py` | Modified — `MIGRATIONS_DIR` / `MIGRATIONS_ALLOW_DESTRUCTIVE` config (env-overridable) + run migrations after every bootstrap; report stored in `app.config["AMS_MIGRATION_REPORT"]` |
| `AMSCOPY9/app/migrations/README.md` | New — how to add a future migration |
| `AMSCOPY9/docs/AUTO_DATABASE_MIGRATIONS.md` | New — design document + future-module recipe |
| `AMSCOPY9/tests/test_auto_migrations.py` | New — 9 unit tests |

### 5.2 Runner behaviour (`auto_migrate.py`)

1. Runs inside the app factory **after** `db.create_all()` and the `_ensure_*` helpers — model tables already exist; migrations add what models cannot express.
2. Reads `*.sql` files from `app/migrations/` (or `MIGRATIONS_DIR`) in sorted order; only files named `NNNN_*.sql` (numeric prefix) are version-stamped.
3. Applies **only pending files** — those not recorded in `migration_history` (the same table the release-deploy SQL runner uses, so both paths share one record of truth).
4. Records each applied file; stamps the highest numeric prefix into `schema_version` (id=1) → `schema_version.version`.
5. **Idempotent + concurrency-safe:** two processes booting at once — the loser sees the file already recorded and moves on.
6. **Destructive SQL blocked** by default (`DROP TABLE` / `TRUNCATE TABLE` / `DELETE FROM`) unless `MIGRATIONS_ALLOW_DESTRUCTIVE=1`.
7. **Never blocks app start:** on failure the file is logged with `exc_info`, not recorded, and retried on the next boot.

### 5.3 Migration file rules (for future developers)

- Name: `NNNN_short_description.sql` (e.g. `0001_add_vendor_module.sql`).
- **Never edit an applied file** — add a higher-numbered one instead.
- Write idempotent SQL (`IF NOT EXISTS`, `INSERT OR IGNORE`) so a mid-file failure can retry cleanly on the next boot.

---

## 6. Verification evidence

### 6.1 Unit tests

```
tests/test_auto_migrations.py        → 9 passed in 0.34 s
tests/test_instance_bootstrap_and_crud.py + tests/test_full_db_snapshot.py → 19 passed
Full suite (backend, incl. above)    → 148 passed in ~5.5 min
```

Covered: apply-once ordering + version stamping; unnumbered files apply but don't stamp; destructive guard (default + override); empty/no migrations dir is a no-op; missing DB raises; failing migration not recorded and filename surfaced; idempotent retry after fix; non-SQL files ignored.

### 6.2 Live boot simulation (future-module scenario, on a copy of the real populated refresh DB)

Migration file used: `0001_future_module.sql` → `CREATE TABLE future_widget …` + `CREATE UNIQUE INDEX uq_future_widget_name …`

```
BOOT 1  → applied: 1, skipped: 0, version: 1, changed: True
          future_widget table: created | unique index: created
          schema_version rows: [(1, 1)] | history: [('0001_future_module.sql',)]
BOOT 2  → applied: 0, skipped: 1, version: 1, changed: False
          history count: 1  (no double-apply)
```

The same mechanism runs on tests, local starts and **every deployment reload**, so all installations converge on the same schema automatically.

---

## 7. Future-module recipe (from now on)

1. Add the SQLAlchemy model — table appears automatically via `create_all()`.
2. Add `app/migrations/0001_<module>.sql` for what `create_all` cannot do (indexes/unique constraints on existing tables, columns with defaults, renames, backfills).
3. Restart or deploy — every installation applies pending files once, records them, and stamps `schema_version`.

---

## 8. Known items / follow-ups (not done — by design)

| Item | Status |
|---|---|
| `uq_entry_auto_bill_no` still missing on live data | Because 2 duplicate `entry.auto_bill_no` values (`SB-GRN-1024`, `SB-GRN-1042`) exist in the old data. Restore as a `000N_*.sql` migration **after** the duplicates are cleaned — then it can never fail on live data. |
| Option 1 (one-click in-app import of old-format `.db`) | Not implemented. Version stamping now makes it easy later: a file's `schema_version` identifies its generation exactly. |
| Automatic re-creation of indexes relaxed during import | The import engine relaxes only exact violating indexes and reports them; restoring them is a deliberate follow-up (see first row). |

---

## 9. Conclusion

- The app runs on one SQLite v4.4 file; old-format DBs are refused safely, and the supported migration path is **`migrate.py` → full-db snapshot import** (verified PASS end-to-end on both stages).
- Going forward, schema changes are **automatic, versioned and recorded on every boot**, so new modules never again depend on a one-off hand-written helper or silent drift.
