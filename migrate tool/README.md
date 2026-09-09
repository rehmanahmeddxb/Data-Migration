# AMS Database Migration Tool (`migrate tool`)

A small GUI program that migrates your **OLD database file** into your **NEW
v4.4 AMS database file** — emptying the new file's seeded data, loading
**every** old row into it with original ids, and producing one clean migration
file. Verified end-to-end with no data lost, no rows dropped, no duplicates
created.

## What is in this folder

| File | Purpose |
|---|---|
| `migrate_tool.py` | The GUI app (double-click or `python migrate_tool.py`) |
| `migrate_engine.py` | The migration logic (pure Python standard library — sqlite3 only) |
| `run_tool.bat` | Windows double-click launcher |
| `drop/` | *(auto-created)* put any database file here — the app detects it |
| `output/` | *(auto-created)* default home for the migrated result + reports |

No third-party packages are needed — just Python 3 (with tkinter, which ships
with the normal python.org Windows installer).

## How to use (GUI)

1. Open the folder and double-click `migrate_tool.py` (or `run_tool.bat`), or
   run `python migrate_tool.py`.
2. Choose the two files — easiest way: **copy your DB files into this folder
   (or the `drop` subfolder)**. Within 1–2 seconds the app detects them and
   lists them under *Detected files*. Then press **Use as OLD** / **Use as
   NEW**, or use the **Browse…** buttons.

   - **OLD data db** = the old database with the data (e.g. the legacy
     `ahmed_cement.db`, 64 tables).
   - **NEW v4.4 db** = the fresh v4.4 database that supplies the correct
     schema (e.g. `NewData/ahmed_cement_v44_fresh.db`).
   - The app colour-checks the pairing and warns if it looks swapped.
3. The **output file** is suggested automatically
   (`output/<oldname>_migrated.db`). Change it if you like.
4. Press **START MIGRATION**.

### What happens (guaranteed by design)

1. The NEW file is **copied** (consistent copy, includes WAL) to the output
   path — the original NEW file is never modified.
2. The OLD file is read through a **staging copy** — the original OLD file is
   never opened for writing either.
3. The output is **emptied** of the new file's seeded business data
   (schema, indexes and migration-run infrastructure are kept), then **every
   old row is loaded with its original id** — table by table, in a way that
   cannot drop a row even when the old data has duplicate values.
4. **Users are merged**: fresh admin users of the new file are kept, every old
   user is added (renamed `_legacy` only when the name already exists), and
   every reference to the old user ids is remapped.
5. **Duplicates are handled**: rows are never deleted or merged. Only the
   exact unique index(es) the old data violates are relaxed (reported in the
   log); every other unique index is re-created and verified.
6. **Verification is automatic**: `PRAGMA integrity_check`, per-table parity
   (old count → migrated count → expected count), FK orphan check, logical
   user-id check and a full duplicate scan. The run finishes with
   `RESULT: PASS` only when all checks are clean.
7. Outputs: `<name>_migrated.db`, `<name>_migrated.db.report.txt` (human
   report) and `.report.json` (machine summary).

## If a file is put into the folder

The app watches this folder and the `drop/` subfolder every 1.5 s: any
`*.db`, `*.sqlite`, `*.sqlite3` or `*.amsdb` file that appears is detected,
listed and ready to assign as OLD or NEW. Files inside `output/` are ignored
so results are never mistaken for inputs. (Tip: don't keep two copies with
the same name in the folder at once — you can assign either one; or use
separate folders with Browse….)

## Headless / command-line (same engine, for automation or servers)

```bash
python migrate_tool.py --cli --old OLD.db --new NEW_v44.db [--out result.db]
python migrate_tool.py --scan        # list files detected in this folder/drop
```

Exit code: `0` = PASS, `3` = REVIEW (verification found an issue — read the
report), `2` = input error.

## After migration — loading the result into the AMS app

In the AMS application open **Import/Export Center → Full Database Snapshot
(.db) → Import**, select the produced `*_migrated.db` and use mode **"Full
sync — clean all data first"**. The app takes its own backup, loads the file
and verifies parity again. (That is the exact path the last AMS refresh used,
with `verification: PASS` on all 69 tables.)

## Safety notes

- Input files are **never modified** — only read. Test it on copies first if
  you like.
- The migration is only as good as the pair: the NEW file must be an AMS v4.4
  (or schema-superset) database. If the OLD file has whole tables or columns
  the NEW schema does not have, and they carry data, the tool finishes with
  `REVIEW` and lists exactly what would be left behind — nothing is hidden.
- Typical run on the production data set (≈29 k rows): **about 1 second**.
