# full_db_sync — AMS full-database SQLite snapshots

Full-data moves in AMS use **SQLite database files** (`.amsdb`) instead of the
legacy ALLEXPORT XLSX workbook.  A snapshot is an exact, verifiable SQLite
copy of a whole AMS database; an import **cleans all data out of the target
first** (schema stays) and then copies the file's rows in with their original
primary keys.  See `../docs/FULL_DB_SQLITE_SYNC.md` for the full design,
operating rules and the 2026-09-09 refresh report.

The engine is pure stdlib (`sqlite3` only) — it runs inside the Flask app, in
the CLI, and on any machine that can read a SQLite file.  No pandas, no
openpyxl.

## Quick reference

```bash
# export the live (or any) database into a portable .amsdb snapshot
python -m full_db_sync export --db instance/ahmed_cement_v44_fresh.db

# read-only verification (integrity, row-count parity, FK check)
python -m full_db_sync verify --db AMS_FULL_xxx.amsdb

# FULL SYNC: clean the target first, then load the snapshot (users included)
python -m full_db_sync import \
    --source AMS_FULL_xxx.amsdb \
    --db instance/ahmed_cement_v44_fresh.db \
    --confirm

# clean all data rows out of a database (schema preserved)
python -m full_db_sync clean --db instance/ahmed_cement_v44_fresh.db --confirm

# consistent online backup copy
python -m full_db_sync backup --db instance/ahmed_cement_v44_fresh.db
```

Write commands (`clean`, `import`) require `--confirm`.

## Module layout

| File | Responsibility |
|---|---|
| `engine.py` | `export_snapshot`, `verify_snapshot`, `clean_all_data`, `import_snapshot`, `backup_database` |
| `cli.py` | command-line interface (`export | verify | clean | import | backup`) |
| `__main__.py` | lets the package run via `python -m full_db_sync` |

## Behaviour notes

* **Full replace**: import mode `clean_replace` (default) wipes *every* data
  row — users included — and copies the source in.  `--keep-users` / append
  exist for merge-style loads.
* **Automatic backup**: imports always back the target up first
  (`pre_full_db_import_*.db` next to the target unless `--backup-dir`).
* **Legacy duplicates**: if the source data violates a UNIQUE index the
  current schema enforces (known case: duplicate `entry.auto_bill_no`), the
  engine relaxes exactly that index before loading so no row is lost, and
  reports it (`unique_indexes_relaxed`).  All other indexes stay enforced.
* **Verification**: every import ends with `PRAGMA integrity_check`,
  `PRAGMA foreign_key_check`, and per-table row-count parity vs the source;
  every export ends with a `verify_snapshot` of the produced file.
