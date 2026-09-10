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
| `check_template_sync.py` | Pre-flight: is the v4.4 template still in sync with the app's models? |
| `test_migrate_engine.py` | Regression suite (20 stdlib tests, ~16 s) |
| `run_tool.bat` | Windows double-click launcher |
| `drop/` | *(auto-created)* put any database file here — the app detects it |
| `output/` | *(auto-created)* default home for the migrated result + reports |

No third-party packages are needed — just Python 3 (with tkinter, which ships
with the normal python.org Windows installer).

## Void policy — the new database keeps no voided data

The old application never deleted a record: it marked it `is_void = 1` (and
cancelled entries as `type='CANCEL'`). The v4.4 application deletes for real
(`hard_delete_transaction` removes the row and its children), so those rows are
dead weight — they would sit in the new ledgers, stock and reports forever.

The tool therefore **purges them by default**, using the same contract as the
retired Excel pipeline (`tools/migrate/_migrate_common.py`):

| Removed | Rule |
|---|---|
| `is_void = 1` | every row in every table that carries the flag |
| Cancelled entries | `entry.type = 'CANCEL'` or `entry.transaction_category = 'CANCEL'`, even when `is_void = 0` |
| Cascade | children of a purged parent (sale → its items, entries, pending bills, rents, allocations; payment → its waive-offs and material returns; …), so no *keyed* reference survives — see the one exception below |
| Dangling rows | rows whose parent never existed (e.g. `booking_allocation.booking_item_id`) |

Every removal is counted per table and printed in a **PURGE** section of the
report (and in `.report.json` under `purge`). Nothing is removed silently.

On the real data that is **87 rows** out of 29,266 (61 voided, 24 cancelled,
2 cascaded) — `payment −18,664`, `account_transaction −446,114`,
`entry qty −17,547`, `material_return −35,328`, `waive_off −279`. Those are
amounts the old reports already excluded, and the app's own consistency report
returns **identical results before and after** the purge.

*(2026-09-10, final old data — the production file at cutover: 29,669 rows in,
**93 purged** — 62 voided + 26 cancelled + 5 cascaded — → 29,586 rows out,
and the duplicate-bill unique index `uq_entry_auto_bill_no` no longer needs
relaxing: its duplicates were voided rows the purge removes, so the index is
re-created. Dated backup: `data lab for migration old to new/FIRST CLASS
DATA/2026-09-10/`.)*

Run with `--keep-voided` (or untick the *Purge voided / cancelled rows* box in
the GUI) if you ever need a bit-for-bit archive instead.

The cascade set is **complete for keyed links by construction** (2026-09-10):
besides the hard-coded rules for the polymorphic `source_id` links, every
*declared* foreign key of the v4.4 schema (e.g. `grn_allocation → direct_sale /
direct_sale_item / grn_item`) is derived automatically from
`PRAGMA foreign_key_list`, so children of purged parents can never be left
dangling even when the schema grows new child tables. Parent links that exist
only as **free text** (bill numbers) are the one gap — see
"The one link the cascade cannot see" below.

## Before you migrate — the 30-second pre-flight

The tool copies the old rows into whatever columns the **template** has, so the
run only proves "old → output", never "output → what the app expects today".
Check the template first:

```bash
python3 "check_template_sync.py" --template <your v4.4 template>.db
# IN SYNC → safe to migrate.  DRIFT → rebuild the template first.
```

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
6. **Voided and cancelled rows are purged** — the new database keeps **no**
   voided data (see "Void policy" below).
7. **A relaxation the purge makes unnecessary is undone**: if the rows that
   violated a unique index turn out to be voided/cancelled rows that step 6
   just removed, the index is re-created before the run finishes (logged as
   `INDEX RESTORED`), so the output never ships a weakened constraint because
   of data the policy did not want anyway. On the production file this is what
   happens to `entry.uq_entry_auto_bill_no`: its two duplicate GRN numbers
   each had one voided twin, so after the purge the index holds and is
   restored — **no manual de-duplication is needed** (only a `--keep-voided`
   archive keeps the duplicates, and then the report says so).
8. **Verification is automatic**: `PRAGMA integrity_check`, per-table parity
   (old count → migrated count → expected count), **value parity** (every copied
   value compared with the source, so a mis-mapped column cannot pass), index
   parity, FK orphan check, logical user-id check and a full duplicate scan.
   The run finishes with `RESULT: PASS` only when all checks are clean.
7. Outputs: `<name>_migrated.db`, `<name>_migrated.db.report.txt` (human
   report) and `.report.json` (machine summary). The report also lists
   **new-schema columns that arrive NULL** (v4.4 columns the old file never
   had — the app back-fills the ones it needs on its first start) and a
   **NEXT STEPS** block for loading the result.

## If a file is put into the folder

The app watches this folder and the `drop/` subfolder every 1.5 s: any
`*.db`, `*.sqlite`, `*.sqlite3` or `*.amsdb` file that appears is detected,
listed and ready to assign as OLD or NEW. Files inside `output/` are ignored
so results are never mistaken for inputs. (Tip: don't keep two copies with
the same name in the folder at once — you can assign either one; or use
separate folders with Browse….)

## Headless / command-line (same engine, for automation or servers)

```bash
python migrate_tool.py --cli --old OLD.db --new NEW_v44.db [--out result.db] \
    [--keep-voided] [--carry-settings] [--allow-v44-old] [--no-overwrite]
python migrate_tool.py --scan        # list files detected in this folder/drop
```

`--carry-settings` loads the OLD file's `settings` row (company name / tax /
bill prefixes) when the NEW template's settings table is empty — use it for a
real business migration so you don't re-enter the company settings (default:
keep the template's settings and flag the old rows `REVIEW`).

`--allow-v44-old` is the only escape hatch: by default the tool **refuses** an
OLD file that already carries v4.4 markers, because that is an output of a
previous run — migrating it again merges the users a second time and re-maps
`user_id` twice, which silently re-points audit rows at the wrong person.

Exit code: `0` = PASS, `3` = REVIEW (verification found an issue — read the
report), `2` = input error.

## After migration — loading the result into the AMS app

In the AMS application open **Import/Export Center → Full Database Snapshot
(.db) → Import**, select the produced `*_migrated.db` and use mode **"Full
sync — clean all data first"**. The app takes its own backup, loads the file
and verifies parity again. (That is the exact path the last AMS refresh used,
with `verification: PASS` on all 69 tables.) Or headless:

> **Sidecar rule (2026-09-10):** when importing a **plain `.db`** via the CLI,
> the app importer now *requires* the `.report.txt` sidecar with
> `RESULT: PASS` to sit next to the file — exactly the one this tool writes —
> so a quarantined `*.INCOMPLETE` run can never be imported by explicit path.
> `.amsdb` snapshots are self-verifying and exempt; `--allow-no-sidecar`
> exists for automation that verifies on its own.

```bash
python3 -m full_db_sync export --db <migrated>.db --out AMS.amsdb
python3 -m full_db_sync verify --db AMS.amsdb
python3 -m full_db_sync import --source AMS.amsdb \
        --db instance/ahmed_cement_v44_fresh.db --confirm
```

**Then, before the app's first start after the import**, delete
`instance/health_snapshot.json` (or start once with `ALLOW_DB_DROP=1`). The
startup data-loss guard compares row counts against that snapshot and refuses
to start when they drop by ≥ 50 rows or below 80 % — which a smaller/older
legacy file legitimately does. (The Import screen now refreshes that baseline
automatically; this is the fallback for the headless path.)

That first start also back-fills the new v4.4 columns the migration left NULL
(account classification, counters, Open-Khata client, performance indexes).

### The one link the cascade cannot see (known, reported not fixed)

Cascade rules follow **keys**: declared foreign keys plus the polymorphic
`source_id` links. A legacy row can also point at its parent by **bill number
text** with no key at all — the case that exists on the production file is an
`entry` stock movement for a material return (`entry.nimbus_no = 'Material
Return'`, `source_id IS NULL`, matched to `material_return.bill_no`). When the
voided parent is purged, that live child row is **kept**, because deleting a
stock movement would silently change stock totals — a far worse surprise than a
kept row.

On the committed data exactly **1** such row exists after the purge (153.6
units, entry id 10656 / `MB NO.12200`). It is listed by the post-migration
audit, so nothing stays invisible:

```bash
python3 AMSCOPY9/tools/post_migration_audit/audit_findings.py --db <migrated>.db   # check 24
```

Decide it as a business question (void the movement too, or keep it as history)
before the first stock report is signed off.

## Day-1 checklist — read this before the shop opens

Two things the migration cannot fix, because they are not in the old file:

1. **No `settings` row.** If the old database's `settings` table is empty
   (the shipped legacy `ahmed_cement.db` is), `--carry-settings` has nothing
   to carry and the migrated file has no company row either. Before the first
   transaction, log in and open **/settings → Save once**: company name, tax
   rate and bill prefixes come from that row.
2. **Negative stock + `allow_global_negative_stock`.** While that row is
   missing, the flag reads as OFF, and the app then **rejects new sales of
   every material that is already in negative stock** — on the real legacy
   data that is 55 materials, including `12MM STEEL` and `ISM 12MM STEEL`.
   So either tick *allow negative stock* in /settings, or reconcile stock
   (`tools/inventory/reconcile_stock.py`) — otherwise day 1 stops at the
   counter.

Confirm both with the app's read-only pre-flight, which is the check the
business flow itself uses:

```bash
python3 tools/health/preflight_check.py --db <migrated>.db --quiet
```

Verified on the production pair 2026-09-10: the migrated file answers
`RESULT: BLOCK  (1 blocker)`, and **saving one settings row with
`allow_global_negative_stock = 1` is enough to answer `RESULT: OK`** — the
blocker is that single missing row, nothing else.

`RESULT: BLOCK` is not a migration failure — it is legacy data the old app
tolerated and the new one will not. The same run lists the leftovers to
review (duplicate manual bill numbers, invoices with no linked sale, sales
with no stock entry).

## Safety notes

- Input files are **never modified** — only read. Test it on copies first if
  you like.
- The migration is only as good as the pair: the NEW file must be an AMS v4.4
  (or schema-superset) database. If the OLD file has whole tables or columns
  the NEW schema does not have, and they carry data, the tool finishes with
  `REVIEW` and lists exactly what would be left behind — nothing is hidden.
- Typical run on the production data set (≈29 k rows): **about 1 second**.
