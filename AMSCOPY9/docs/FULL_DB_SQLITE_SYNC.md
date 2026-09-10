# AMS Full-Data Sync — SQLite `.db` Snapshots (replaces ALLEXPORT XLSX)

> **Date:** 2026-09-09 — refresh performed into AMSCOPY9 (see last section).
> **Decision:** full-data moves in AMS now use **SQLite database files**,
> never Excel. The XLSX full-raw export/import is kept only as a deprecated
> legacy path for old workbooks.

---

## 1. Why Excel was the wrong transport (the "many problems" list)

The old full-data path turned every table into an `.xlsx` sheet (`ALLEXPORT`) and
restored from that workbook.  Excel is a *display* format, not a *storage*
format, so whole-database transfers through it repeatedly caused:

1. **Row/sheet limits** — Excel sheets have a hard row limit; big tables must
   be split or are silently truncated.
2. **Type coercion** — dates become datetimes or strings, floats lose
   precision, text that *looks* like a number/date (bill no. `7001`, codes
   like `SB-GRN-1042`) is converted on the way in, and text starting with `=`
   is treated as a formula.
3. **NULL vs blank ambiguity** — blank cells mean "empty", not "NULL";
   imported data can come back with different defaults.
4. **Sheet-name / column-name drift** — the app's sheet and column names must
   match exactly; any rename breaks whole sections silently.
5. **Duplicate / missing sheets** — a sheet missing from the workbook means
   "keep existing data", which is *not* what a full replace means; you can end
   up with mixed old/new data and nobody notices.
6. **Heavy dependency chain** — pandas + openpyxl + numpy, with their own
   version-specific behaviour.
7. **No integrity story** — Excel cannot verify that what you exported equals
   what you imported; there is no checksum, no FK concept, no audit trail
   inside the file.

The SQLite snapshot fixes all of these: the transport is **the same format the
app itself runs on**, so a byte can never be coerced, lost to a sheet limit, or
renamed.

---

## 2. The new model

```
 LIVE APP DB  ──export──▶  AMS_FULL_<ts>.amsdb   (one SQLite file)
     ▲                          │  = exact DB copy
     │                          │  + __ams_full_db_meta__ table
     │                          │    (time, tool, per-table row counts)
     │                          ▼
   after clean        verify_snapshot()  (integrity, row-count parity,
   + full copy             FK check, AMS probe)
```

**Import = clean-and-copy.**  Importing a snapshot into a database:

1. takes an **automatic backup** of the current database
   (`instance/pre_full_db_import_*.db` or `instance/migration/`), and
2. **deletes every existing data row** — "clean all data in AMSCOPY9 first"
   — while keeping the schema, indexes and app metadata, then
3. **copies every row of the file in with its original primary keys**, so all
   client/sale/entry/… relationships stay intact (users included: full replace).
4. verifies afterwards: `PRAGMA integrity_check`, `PRAGMA foreign_key_check`,
   and per-table row-count parity against the source.

### What "clean all data" means exactly
- All 69 business/runtime tables are emptied, including `user` and
  `user_login_session` (full replace semantics — the app becomes an exact data
  copy of the snapshot).  `--keep-users` exists for merge style loads.
- The schema itself is **not** dropped; the app keeps working and keeps its
  own seeded role/default rows (e.g. the `OPEN-KHATA` support client is
  re-created automatically by the app on next boot if missing — that is normal
  app behaviour, not migration data).

### Known legacy constraint
The 2026 legacy data contains **two duplicate `entry.auto_bill_no` values**
(`SB-GRN-1024`, `SB-GRN-1042`) that violate the current schema's partial
UNIQUE index `uq_entry_auto_bill_no`.  When the source data violates a target
UNIQUE index, the engine **relaxes exactly that index** (drops it) before the
load so no row is ever lost — the same compromise the original
old→v4.4 migration documented — and lists it in the import report
(`unique_indexes_relaxed`).  The app's own startup also detects this and skips
re-creating that index.  Every other UNIQUE index is kept and enforced.

---

## 3. Components added

| Path | Purpose |
|---|---|
| `full_db_sync/` | Pure-stdlib engine + CLI (`python -m full_db_sync`). No Flask/pandas/openpyxl needed. |
| `full_db_sync/engine.py` | `export_snapshot`, `verify_snapshot`, `clean_all_data`, `import_snapshot` (+ `backup_database`). |
| `full_db_sync/cli.py` | CLI: `export`, `verify`, `clean`, `import`, `backup` (write ops require `--confirm`). |
| `blueprints/import_export/_pages_full_db.py` | UI routes `/import_export/full_db_export`, `/full_db_import`, `/full_db_import_report/<name>`. |
| `templates/import_export_new.html` | New "Full Database Snapshot (.db)" cards (recommended); XLSX full cards marked Legacy. |
| `tests/test_full_db_snapshot.py` | 13 tests: engine + UI routes (export valid file, clean-and-copy, append, relaxation, tamper detection). |

Safety toggle (default **on**): `FULL_DB_SYNC_ENABLED=1`.
Only `admin`/`root` can export/import (same rule as the XLSX full-raw path).

---

## 4. Usage

### 4.1 From the app UI (recommended for day-to-day)
1. **Import / Export Center** → **Full Database Snapshot (.db) — recommended**.
2. *Export*: download `AMS_FULL_<ts>.amsdb` (a verified SQLite copy).
3. *Import*: choose **Full sync — clean all data first, then copy** (default),
   upload the `.amsdb`/`.db`.  The app backs itself up, wipes all rows, loads
   the file fully, verifies, and shows a report (downloadable JSON).

### 4.2 From the command line
```bash
# 1) Export the live DB into a portable snapshot:
python -m full_db_sync export --db instance/ahmed_cement_v44_fresh.db

# 2) Verify a snapshot file:
python -m full_db_sync verify --db AMS_FULL_xxx.amsdb

# 3) FULL SYNC — clean the target first, then load the snapshot:
python -m full_db_sync import \
    --source AMS_FULL_xxx.amsdb \
    --db    instance/ahmed_cement_v44_fresh.db \
    --confirm

# (options: --mode append | clean_replace, --keep-users,
#  --no-backup, --backup-dir DIR, --allow-no-sidecar, --json report.json, --verbose)

# Clean-only (wipe all rows, keep schema):
python -m full_db_sync clean --db instance/ahmed_cement_v44_fresh.db --confirm

# Plain backup copy:
python -m full_db_sync backup --db instance/ahmed_cement_v44_fresh.db
```

### 4.3 Accepting any AMS sqlite file
An import does **not** require the file to have been exported by this tool:
any AMS v4.4 SQLite database (e.g. the earlier
`FIRST CLASS DATA/ahmed_cement_migrated.db`) can be used as the source —
that is exactly how today's refresh was run.

**Sidecar rule (2026-09-10):** for a **plain `.db`** source, the importer
now requires the `.report.txt` sidecar with `RESULT: PASS` next to the file
(the AMS migration tool writes exactly that sidecar, and renames aborted
runs to `*.INCOMPLETE`).  This makes the quarantine airtight: a crashed
migration output can no longer be imported by explicit path.  `.amsdb`
snapshots are self-verifying and exempt.  `--allow-no-sidecar` bypasses the
gate for automation that verifies on its own; the app's upload UI passes
`require_sidecar=False` because a browser upload is a single file by nature
(admin-only, with its own backup + verify + tamper detection).

---

## 5. Operating rules (follow these every time)

1. **Export before you wipe.** Always take a `.amsdb` snapshot (or at least a
   `backup`) of the current database *before* importing — the import also
   auto-backs-up, but keep the export as your "before" artifact.
2. **Never import while the app is mid-write.** Prefer importing when the app
   is idle or stopped (the engine uses busy-timeouts and transactional
   wipe+load, but a quiet database is the safest).
3. **Same schema generation.** Export from the same AMS version you import
   into.  The engine refuses when the target needs columns the source lacks,
   and reports (never guesses) when a source table is missing.
4. **Keep the report JSON.** Every import stores a full report in
   `instance/import_reports/full_db_import_report_<ts>.json` — keep it with
   the snapshot as your audit trail.
5. **SQLite backups are file copies.** After any big import, make a plain file
   copy (or `backup`) and store it off the server (download the `.amsdb`).

---

## 6. Deprecation of the XLSX full-raw path

- The UI now labels the XLSX full export/import cards **Legacy — Deprecated**
  and points to the `.db` snapshot path.
- The XLSX machinery stays in the code base for two reasons:
  1. old `ALLEXPORT-CLEAN-*.xlsx` workbooks may still need to be read once;
  2. small, human-editable lists (clients, materials, prices) are still fine
     as Excel for *manual* edits.
- New full-data moves must use `.amsdb`.  A future cleanup can remove the
  full-raw XLSX engine after a quarter without legacy imports.

---

## 7. What was done today (2026-09-09) — AMSCOPY9 full refresh

Goal: AMSCOPY9 must start from **completely clean data**, then receive the
full data set produced by the earlier migration
(`data lab for migration old to new/FIRST CLASS DATA/ahmed_cement_migrated.db`).

Steps executed (evidence artifacts are in
`data lab for migration old to new/AMSCOPY9_FULL_REFRESH_2026-09-09/`):

1. **Schema bootstrap** — created
   `AMSCOPY9/instance/ahmed_cement_v44_fresh.db` with the *current* app schema
   (69 tables, columns verified identical to the source, 0 differences).
2. **Clean-all-data** — the seeded/empty runtime DB was fully wiped first
   (schema preserved).  This is the "clean all data from AMSCOPY9 first" step.
3. **Full copy** — loaded every row of
   `FIRST CLASS DATA/ahmed_cement_migrated.db` with original IDs:
   - verification **PASS**, `integrity_check ok`, **0 FK violations**,
   - **29,266 rows** inserted across 69 tables, per-table row counts equal to
     the source (checked table by table),
   - users 1–9 carried over (Admin, Adnan Ahmed, + the 7 legacy users exactly
     as the earlier migration merged them),
   - exactly one UNIQUE index relaxed (`uq_entry_auto_bill_no`) because the
     legacy data contains two duplicate `auto_bill_no` values — same
     documented compromise as the earlier migration.
4. **Automatic backup** of the previous (pre-load) database:
   `AMSCOPY9/instance/pre_full_db_import_ahmed_cement_v44_fresh_20260909-133856.db`.
5. **Smoke-tested the app** on the loaded DB: boots cleanly, login
   `Admin` works, dashboard renders with live counts
   (clients 323+, direct_sales 2,719, entries 5,259, invoices 2,378, …).
   Running the app adds only its own operational rows afterwards
   (audit-log lines, login sessions, the auto-created `OPEN-KHATA` support
   client) — the business data itself is an exact copy.
6. **Portable snapshot** of the loaded DB:
   `AMSCOPY9/instance/migration/AMS_FULL_20260909-133924.amsdb`
   (29,266 rows, single file, verified).

### Files produced
| File | Meaning |
|---|---|
| `AMSCOPY9/instance/ahmed_cement_v44_fresh.db` | **Runtime DB of AMSCOPY9 with the full fresh data** (this checkout; app runs on it). |
| `data lab for migration old to new/AMSCOPY9_FULL_REFRESH_2026-09-09/ahmed_cement_v44_fresh.db` | Same DB copied here for transfer/keeping. |
| `data lab for migration old to new/AMSCOPY9_FULL_REFRESH_2026-09-09/AMS_FULL_20260909-133924.amsdb` | Portable SQLite snapshot (exact copy, 29,266 rows). |
| `data lab for migration old to new/AMSCOPY9_FULL_REFRESH_2026-09-09/import_report.json` | Import report (verification PASS, relaxed index list). |
| `data lab for migration old to new/AMSCOPY9_FULL_REFRESH_2026-09-09/export_report.json`, `verify_report.json` | Snapshot export/verify evidence. |

### Moving this to the live PythonAnywhere server
1. (Recommended) On the server, first export a `.amsdb` of the current live
   DB through Import/Export → Full Database Snapshot → Export.
2. Stop/quiet the app, then either:
   - **replace** `instance/ahmed_cement_v44_fresh.db` with the file
     `ahmed_cement_v44_fresh.db` from the refresh folder, or
   - upload `AMS_FULL_20260909-133924.amsdb` through
     Import/Export → Full .db Snapshot → Import (Full sync).
3. Restart and verify: log in, open a client, a sale, a ledger, a report.
