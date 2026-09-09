# FINAL DEPLOY — the app database with fresh FIRST CLASS DATA (stage 2 result)

**Produced:** 2026-09-09 by `../load_into_amscopy9.py --confirm`
**Provenance chain:**

```
OLD production data (ahmed_cement.db)
   └─> stage 1: FIRST CLASS DATA/migrate.py        -> FIRST CLASS DATA/ahmed_cement_migrated.db   (PASS)
        └─> stage 2: load_into_amscopy9.py         -> THIS FOLDER                                   (PASS)
             GATE -> BACKUP -> CLEAN (wipes ALL rows of the app DB)
             -> LOAD (db-to-db, original ids, no xlsx) -> VERIFY -> DEPLOY
```

## Files

| File | Meaning |
|---|---|
| `ahmed_cement_v44_fresh.db` | **THE deployable app database.** Same name the app expects in its `instance/` dir. |
| `ahmed_cement_v44_fresh.db.sha256` | checksum — always verify before cutover |
| `load_report.txt` | full per-table proof: clean → load → verify, `RESULT: PASS` |
| `README.md` | this file |

## Content at a glance (verified row-for-row against the source)

- 69 tables, 29,266 rows copied with **original ids** (source total; the running app adds its
  standard "Open Khata" client on first boot → 324 clients, by design)
- clients 323 · direct sales 2,719 · sale items 5,048 · invoices 2,378 · entries 5,259 ·
  payments 917 · bookings 427 · pending bills 1,702 · account transactions 1,059 · users 9
- users 1–9: Admin, Adnan Ahmed (kept app admins) + 7 legacy users remapped 3–9
  (login as you do on the live system — same hashes were carried over)
- `integrity_check: ok` · declared-FK orphans: 0 · logical user-FK orphans: 0

## Verify before you use it

```bash
sha256sum -c ahmed_cement_v44_fresh.db.sha256
# ahmed_cement_v44_fresh.db: OK
```

## Cutover to the live server (PythonAnywhere)

See `../FUTURE_MIGRATION_PLAN.md` §5 for the full runbook. Short version:

1. Stop the app.
2. Back up the live DB (keep it!).
3. Replace the live DB file with this file (same filename: `ahmed_cement_v44_fresh.db`).
4. Start the app → `/health` shows `"database": "ok"`.
5. Log in and spot-check; run `tools/consistency_report.py` and compare with the known baseline
   (4 sales missing entries / 87 orphaned invoices / 100 bookings missing pending bills —
   identical in the old DB and every migrated copy; see FUTURE_MIGRATION_PLAN.md §6).

## This folder was produced by a reproducible script

Re-run anytime:

```bash
python3 "../data lab for migration old to new/load_into_amscopy9.py" --confirm
```

The script backs the current app DB up into `../backups/appdb_backup_<ts>/` **before** cleaning it,
and restores that backup automatically if anything fails.
