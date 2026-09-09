# AMSCOPY9 Full Refresh — 2026-09-09 (clean all data → load fresh data)

This folder documents the **full data refresh of the AMSCOPY9 app** done on
2026-09-09:

> AMSCOPY9 was **cleaned of all data first**, then the full data set from
> `FIRST CLASS DATA/ahmed_cement_migrated.db` (the earlier old→new v4.4
> migration result) was copied into it completely — schema verified equal,
> users included (full replace), verified PASS.

## Why a new approach was introduced

Full data moves no longer use Excel (`ALLEXPORT` XLSX).  Excel is a display
format and repeatedly caused problems (sheet limits, date/number coercion,
text-as-formula, NULL-vs-blank ambiguity, sheet/column drift, missing-sheet
semantics, heavy pandas/openpyxl dependency chain, no integrity checks).
Full data now moves as a **SQLite database file** — the same format the app
runs on.  Details & operating rules: `AMSCOPY9/docs/FULL_DB_SQLITE_SYNC.md`.

## Artifacts in this folder

| File | Meaning |
|---|---|
| `ahmed_cement_v44_fresh.db` | The **ready-to-run AMSCOPY9 database** with the full fresh data (after app smoke boots; only app-managed operational rows were added — see below). |
| `AMS_FULL_20260909-133924.amsdb` | Portable, verified **SQLite snapshot** of the loaded database (29,266 rows, exact copy, single file). |
| `import_report.json` | Full import report (verification PASS, inserted rows, relaxed unique index). |
| `export_report.json` | Snapshot export report. |
| `verify_report.json` | Snapshot verification report. |

## Numbers (import verification)

| Metric | Value |
|---|---|
| Source | `data lab for migration old to new/FIRST CLASS DATA/ahmed_cement_migrated.db` |
| Target | `AMSCOPY9/instance/ahmed_cement_v44_fresh.db` (current v4.4 app schema) |
| Mode | `clean_replace` — every existing row deleted first, then full copy |
| Rows inserted | **29,266** |
| Per-table parity vs source | **all 69 tables equal** (checked table by table) |
| `PRAGMA integrity_check` | `ok` |
| `PRAGMA foreign_key_check` | 0 violations |
| Users carried over | 9 (Admin, Adnan Ahmed + 7 legacy users, ids 1–9) |
| Relaxed unique index | `uq_entry_auto_bill_no` (legacy data has 2 duplicate `entry.auto_bill_no` values — same documented compromise as the original migration; the app's own startup also skips re-creating it) |
| Automatic pre-load backup | `AMSCOPY9/instance/pre_full_db_import_ahmed_cement_v44_fresh_20260909-133856.db` |

Business volumes inside the loaded DB (matches the old production data):
clients 323 · direct_sales 2,719 · sales/entry rows 5,259 · invoices 2,378 ·
bookings 427 · pending bills 1,702 · payments 917.

The app was booted against the loaded database and smoke-tested (login
`Admin` works, dashboard + pages render).  Running the app afterwards added
only its own operational rows — a few `audit_log` lines, a login session and
the auto-created `OPEN-KHATA` support client (id 324) — none of which change
the migrated business data (the `.amsdb` snapshot remains the exact 29,266-row
copy).

## How to repeat this on the live server (PythonAnywhere)

1. Export the current live DB first (Import/Export → Full .db Snapshot →
   Export) as a safety copy.
2. Stop/quiet the app, then either:
   - replace `instance/ahmed_cement_v44_fresh.db` with the
     `ahmed_cement_v44_fresh.db` in this folder, or
   - upload `AMS_FULL_20260909-133924.amsdb` via
     Import/Export → Full Database Snapshot → Import (Full sync).
3. Restart and verify: log in and open clients, sales, ledgers, reports.

CLI equivalent of the whole refresh (re-runnable, `--confirm` guards writes):

```bash
cd AMSCOPY9
# clean everything in the target app DB first (schema preserved)
python -m full_db_sync clean --db instance/ahmed_cement_v44_fresh.db --confirm
# then load the full data set (users included, auto backup first)
python -m full_db_sync import \
    --source "../data lab for migration old to new/FIRST CLASS DATA/ahmed_cement_migrated.db" \
    --db instance/ahmed_cement_v44_fresh.db --mode clean_replace --confirm
```
