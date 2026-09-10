# Is the migration tool sufficient to move old data into the new (v4.4) schema?

**Date:** 2026-09-10 · **Branch:** `arena/01a0899f-data-migration`
**Question asked:** *read every migration-related file and judge whether `migrate tool` is good
enough to migrate the old database into the new schema.*
**Method:** everything below was re-executed on this branch, not copied from the earlier reports.
All scratch output lives in `/tmp`.

> **Update (same day, after this assessment):** gaps **G1, G2, G3, G4** are now fixed in
> `migrate tool/` and **G5** is fixed app-side (the Import screen re-baselines the health
> snapshot) and documented in the tool's report. A **G0-style pre-flight**,
> `check_template_sync.py`, was added for the template-drift risk in §2.6. Regression suite
> grew 9 → **13 tests, all passing**. §3 below keeps the original gap descriptions for the
> record; each heading now carries its status.

---

## 0. Verdict

> **YES — the tool is sufficient for the migration it is meant to do, with two conditions and
> seven known gaps (one of them new and serious).**

| Question | Answer | Evidence |
|---|---|---|
| Does it move **every row** into the v4.4 schema? | **Yes** — 29,263 source rows → 29,266 output rows, **0 lost, 0 unexpected, 0 cell mismatches** | §2.2 |
| Does it change any value? | **No**, except the documented user-id remap (1,880 values in 3 audit tables). Money totals identical to the paisa | §2.2, §2.3 |
| Is the output a *valid v4.4 database*? | **Yes** — 69/69 tables, integrity `ok`, 0 FK violations, 0 orphans, index parity with the template, and it round-trips through the app's own snapshot/verify pipeline with `ok: true` | §2.4, §2.5 |
| Is the v4.4 template still the right target? | **Yes — freshly re-proved**: the template matches today's ORM models exactly (69/69 tables, **0 missing columns**). It is **not** a stale snapshot | §2.6 |
| Is it safe when something goes wrong? | **Yes** — 7 refusal guards, and any mid-run failure writes `RESULT: FAILED` and quarantines the partial file as `*.INCOMPLETE` | §2.7 |
| Can I just run it and open the app? | **Not quite** — see the two conditions in §1 | §1 |

### The two conditions (both are pipeline steps, not tool defects)

1. **The app must boot on the result before the data is fully v4.4.** The tool copies the
   *intersection of columns by name*, so the 19 columns the v4.4 schema adds (and the old file
   lacks) arrive **NULL** — most importantly `account.class_category`, `class_subcategory`,
   `class_account_type`, `channel`, `account_status`, on which the Accounts module depends.
   `app/services/schema.py::_bootstrap_database()` back-fills all 14 of them at every boot
   (`_ensure_account_classification_columns`). The tool + boot = complete; the tool alone = NULL.
2. **Take the app's data-loss guard into account on migration day.** After a migration the app can
   refuse to start: `health.py::_db_health_check_after_bootstrap` hard-fails when row counts drop
   by ≥ `DB_HEALTH_DROP_MIN` (default **50**) or below `DB_HEALTH_DROP_RATIO` (default **0.8**) of
   the last `instance/health_snapshot.json`, or when the DB path changed while the old path still
   exists. Migrating a *smaller/older* file into a target that previously held more rows will trip
   it. Nothing in the tool's README mentions this.

---

## 1. What "sufficient" covers — and what it does not

| In scope (proven) | Out of scope (not covered by this tool) |
|---|---|
| Old AMS SQLite (same table/column names) → fresh v4.4 template | Old files with **renamed** tables (e.g. a Django `auth_user`/`app_entry` schema) — those tables are reported `[LEFT BEHIND]`, not translated |
| Row-complete, id-preserving copy, user merge, index restoration | Semantic upgrades of *values* (money-mirror derivation, account classification, opening-balance recompute) — done by app boot, not the tool |
| Structural verification (integrity, FK, orphans, duplicates, index parity, counts) | Business validation (negative stock, voided rows, orphan invoices) — `tools/consistency_report.py` |
| Producing a file the app's importer accepts | Deciding the void/cancel policy (see §4 G6) |

---

## 2. Evidence (all re-run today)

### 2.1 The run

```
python3 "migrate tool/migrate_tool.py" --cli \
  --old "data lab for migration old to new/ahmed_cement.db" \
  --new "data lab for migration old to new/NewData/ahmed_cement_v44_fresh.db" \
  --out /tmp/audit_run/out.db
→ RESULT: PASS   in 0.90 s   (29,266 rows, 69 tables)
```
Inputs: OLD 64 tables / 29,263 rows · NEW template 69 tables / 3,558 rows.
Regression suite: `python3 "migrate tool/test_migrate_engine.py" -v` → **9/9 pass in 3.9 s**.

### 2.2 Independent cell-level parity (my own check, not the tool's)

63 tables compared id-by-id, every shared column, ignoring only the documented user-id remap:

| Metric | Result |
|---|---|
| Ids present in OLD but missing in output | **0** |
| Unexpected extra ids | **0** |
| Value mismatches | **0** |
| User merge | 2 fresh admins kept + 7 old users added; map `{1:3, 2:4, 3:5, 4:6, 5:7, 6:8, 7:9}` — exactly as documented |
| Old-only columns carrying data that got dropped | **0** |

### 2.3 Money is untouched

| Table | Rows | `SUM(amount)` old vs migrated |
|---|---|---|
| `direct_sale` | 2,719 | 27,038,903.30 = 27,038,903.30 |
| `payment` | 917 | 65,518,360.35 = 65,518,360.35 |
| `booking` | 427 | 150,389,618.73 = 150,389,618.73 |
| `account_transaction` | 1,059 | 313,853,931.63 = 313,853,931.63 |
| `entry` / `invoice` | 5,259 / 2,378 | 0.00 = 0.00 (amounts live on child rows) |

### 2.4 Schema + referential integrity of the output

`PRAGMA integrity_check` = `ok` · `foreign_key_check` = 0 · FK orphan scan = 0 · logical
`user_id → user.id` orphan scan = 0 · duplicate scan on every remaining unique index = **no violations**.

**Index parity:** template has 261 explicit indexes, output has 262.
Missing: `uq_entry_auto_bill_no` — **relaxed by design** because the old data genuinely contains two
duplicate GRN bill numbers (`SB-GRN-1024`, `SB-GRN-1042`); both rows are kept.
Extra: `uq_cash_flow_difference_adjustment_1`, `uq_delivery_person_1` — inline `UNIQUE` constraints
re-created as explicit indexes under a legal name (same guarantee, new name).

### 2.5 It loads into the app pipeline

```
python3 -m full_db_sync export  --db /tmp/audit_run/out.db --out /tmp/audit_run/AMS.amsdb
python3 -m full_db_sync verify  --db /tmp/audit_run/AMS.amsdb
→ ok: true, issues: [], integrity: ok, fk_violations: 0, total_rows: 29,266 (69/69 tables)
```
The produced output is also **table-for-table identical** to the hand-made reference migration in
`data lab for migration old to new/FIRST CLASS DATA/ahmed_cement_migrated.db` (69/69 tables, 0
differences) — the packaged tool reproduces the proven manual result exactly.

### 2.6 The v4.4 template is in sync with the app today (new check)

Parsed `AMSCOPY9/models/*.py` with `ast` (69 model tables) and compared against the template file:

* model tables missing from the template: **none**
* template tables with no model: **none**
* model columns missing from the template: **0**

So `NewData/ahmed_cement_v44_fresh.db` is a valid target *as of this commit*. **This is the check
that must be repeated whenever the app's models change** — it is not automated anywhere. If someone
adds a column to a model and re-uses the old template, the tool will silently load data into a
schema the app no longer matches (the app's `_ensure_model_columns` will then add the column at
boot, NULL-filled, with no back-fill guarantee).

### 2.7 Failure behaviour

| Scenario | Result |
|---|---|
| Swapped OLD/NEW · non-AMS file · corrupt source · out == input · missing file | refused with a clear `MigrationError` (exit 2) |
| Old file has an extra table (empty) | `PASS` + `[LEFT BEHIND]` line |
| Old file has an extra table **with rows** | `REVIEW`, table and row count named |
| Old file has a column the new schema lacks, with data | `REVIEW`, column and non-null count named |
| Old rows in `KEEP_FROM_NEW` tables | `REVIEW`, "merge explicitly if they matter" |
| Mid-run crash | `RESULT: FAILED` report written, partial output renamed `*.INCOMPLETE` — cannot be imported by accident |

---

## 3. The gaps (7), ordered by risk

### G1 — 🔴 HIGH · NEW: no guard against an *already migrated* OLD file → silent corruption  ✅ FIXED

`classify()` only warns when the OLD looks v4.4 **and the NEW does not**. Since the NEW file is
always v4.4, feeding the tool a v4.4 OLD file (a re-run, or picking yesterday's output as today's
input) gets **no warning at all** — and the user merge is not idempotent.

Measured: re-running the tool on its own output

| | before | after |
|---|---|---|
| `user` rows | 9 | **11** — `Admin_legacy_legacy`, `Adnan Ahmed_legacy_legacy` appear |
| `audit_log` rows whose `user_id` now points at a **different person** | – | **785 of 1,630** |

The verdict was `REVIEW`, but for an unrelated reason (`migration_run` rows in a `KEEP_FROM_NEW`
table) — nothing tells the operator that the audit trail was re-pointed.

**Fix (small):** in `run_migration`, if `info_old["looks_v44"]` → raise/warn *"the OLD file already
carries v4.4 markers — if it was produced by this tool, migrating it again re-maps user ids and
corrupts audit attribution"*. Better: make the merge idempotent by matching existing usernames
instead of always appending.

### G2 — 🟠 MEDIUM-HIGH: new-schema columns arrive NULL, and the report never says so  ✅ FIXED

19 columns of the target schema do not exist in the old file, so they land NULL in the output
(14 on `account`, 2 on `account_transaction`, 3 `idempotency_payload_hash` columns). The app heals
the `account.*` set at boot; if the migrated file is ever used without a boot — or a future column's
back-fill is not written — the database is silently incomplete. The tool's report lists
*[LEFT BEHIND] tables* and *dropped columns*, but **not filled-with-NULL columns**.

**Fix:** add a `NEW COLUMNS FILLED NULL` section to the report (informational, not a `REVIEW`
reason) and state the required app-boot step in the README.

### G3 — 🟠 MEDIUM: schema drift fails late and unhelpfully  ✅ FIXED

An old file missing a column that the new schema declares `NOT NULL` fails mid-load with a raw
SQLite error: `IntegrityError: NOT NULL constraint failed: tmp_client.code`. The safety net works
(no output left, `*.INCOMPLETE` + `RESULT: FAILED` written), but an operator cannot act on that
message, and the failure happens after ~12 tables have already been swapped.

**Fix:** pre-flight check before loading — for every shared table, list new columns that are
`NOT NULL`, have no default, and are missing from the old file; raise an actionable `MigrationError`
naming `table.column` and how to fix it (back-fill in the old file, or ship a template default).

### G4 — 🟠 MEDIUM: `RESULT: PASS` proves counts, not values  ✅ FIXED

The tool verifies row counts, integrity, FKs, orphans, duplicates and indexes — **not** that the
values copied are the values that were there. I proved it externally (§2.2, 0 mismatches), but a
column mis-map with equal cardinality would still print `PASS`.

**Fix:** add a per-table checksum parity step (`COUNT` + `SUM`/hash over all copied columns, old vs
output) and fail on mismatch. This is recommendation #9 from the earlier audit, still open.

### G5 — 🟡 MEDIUM (operational): the app can refuse to boot after a migration  ✅ FIXED (app-side) + documented

See condition 2 in §0. `instance/health_snapshot.json` + `DB_HEALTH_DROP_MIN=50` /
`DB_HEALTH_DROP_RATIO=0.8` / the DB-path-changed rule can abort startup with *"Refusing to start to
prevent partial data loss."*

**Fix:** document the migration-day runbook (delete `instance/health_snapshot.json` before the
first boot, or start once with `ALLOW_DB_DROP=1`), and print that hint in the tool's final report.

### G6 — 🟡 LOW-MEDIUM: policy gaps carried over from the earlier audit

* ✅ **Voided/cancelled rows — POLICY DECIDED AND IMPLEMENTED.** The new database carries **no**
  voided data. The tool purges by default (§3c): 61 `is_void = 1` rows + 24 cancelled `entry` rows
  + 2 cascaded children = **87 rows** removed, children purged with their parents, and every
  removal counted in the report. `--keep-voided` / a GUI checkbox preserves the archive instead.
  ✅ *Former open sub-item — RESOLVED (verified in code 2026-09-10):* the app's **Payment** delete
  path now **truly hard-deletes** (`void_rebuild.hard_delete_payment` — "Policy (owner decision):
  the database holds no voided rows"), so the no-voided-rows rule also holds for future deletions.
  Audit trail moves to the append-only `audit_log` / `accounting_audit_log` — see §3c.
* **`KEEP_FROM_NEW` drops the old `settings` row** (company name, tax rate, bill prefixes) — now
  flagged `REVIEW`, and (added 2026-09-10) the tool's `--carry-settings` flag / GUI checkbox loads
  the old settings row when the template's settings table is empty, so a real migration can carry
  company settings without re-entering them.
* **`uq_entry_auto_bill_no` stays relaxed** — the two duplicate `SB-GRN` bill numbers are kept, and
  the app's boot skips re-creating the index while duplicates exist. Clean the two rows, then ship
  the promised `app/migrations/0001_*.sql` to restore it.

### G7 — ⚪ LOW: cosmetic / hygiene

* A read-only open of a WAL-mode source still leaves `-wal`/`-shm` next to the user's original file
  (content is unaffected; breaks on read-only mounts).
* `secret_key.txt` is still committed in three tracked paths, and ~30 MB of real business data
  (client names, balances, ledgers) is committed in 7 DB files — both flagged as open TODOs in
  `MIGRATION_REPORT.md §11` and never actioned.
* The GUI needs `tkinter`; headless `--cli`/`--scan` work everywhere (that is how I ran it).

---

## 3b. What was implemented after this assessment (all on this branch)

`migrate tool/migrate_engine.py`
- **G1** `run_migration(..., allow_v44_old=False)` now refuses an OLD file that already carries
  v4.4 markers, with the reason spelled out (`--allow-v44-old` / `allow_v44_old=True` overrides).
- **G3** `_required_column_problems()` pre-flight: before any table is swapped, every target
  column that is `NOT NULL`, has no default and cannot be filled from the old file (missing, or
  NULL in every row) is collected, and the run aborts naming `table.column` — *"nothing was
  written"* instead of a raw `IntegrityError` after a dozen tables.
- **G4** `_table_fingerprint()` + a **VALUE PARITY** section: order-independent md5 over every
  copied value, old vs output, per table (52 tables on the real data). The documented user-id
  remap is applied to the old side first, so it is treated as expected. A value that changes
  during the copy is now a `REVIEW` reason — `RESULT: PASS` means values, not just counts.
- **G2** a **NEW-SCHEMA COLUMNS FILLED NULL** section lists the 19 columns that arrive NULL and
  says the app back-fills them on its first start.
- **G5** every report ends with a **NEXT STEPS** block: don't import anything but `RESULT: PASS`,
  the export → verify → import commands, and the `instance/health_snapshot.json` step.
- JSON sidecar gained `value_parity` and `new_columns_filled_null`.

`migrate tool/check_template_sync.py` (new, stdlib)
- Compares the v4.4 template against `AMSCOPY9/models` (static AST parse, no Flask needed) and
  exits **1** on drift: *"IN SYNC"* today (69/69 tables, 0 missing columns); verified to flag a
  dropped table and a dropped column on a mutated copy.

`migrate tool/test_migrate_engine.py` — 9 → **13 tests** (all pass in ~10 s): T6 refuses an
already-migrated OLD file (+ the override still runs), T7 names `client.code` before loading,
T8 proves value parity catches an injected value change.

`AMSCOPY9/app/services/health.py` + `blueprints/import_export/_pages_full_db.py` (G5 app-side)
- New `rebaseline_after_full_import()` writes the health snapshot from the freshly imported data
  (raw sqlite3, never raises) and marks it `intentional_reset / reset_source='full_db_import'`.
  The Import screen calls it after a `PASS` import, so the app can no longer refuse to start
  after a migration. Guard now accepts `INTENTIONAL_RESET_SOURCES = ('granular_wipe',
  'full_db_import')`.

`AMSCOPY9/tools/consistency_report.py`
- `--db` flag; default changed from the retired `instance/ahmed_cement.db` to the live
  `instance/ahmed_cement_v44_fresh.db`; errors exit 1 instead of printing a traceback.

`AMSCOPY9/tools/post_migration_audit/audit_findings.py`
- Was unusable (hard-coded retired path, ran at import, exited 0 on a crash, created an empty
  `instance/ahmed_cement.db`). Now: `--db` (or `$APP_DB_PATH`), **read-only** open, body wrapped
  in `main()`, `sys.exit(1)` on error, and the NULL-money `TypeError` that aborted the last
  section is fixed — the audit now runs to `DONE.` on the migrated file.

**Still open:** G6 (void/cancel policy, `settings` not carried, `uq_entry_auto_bill_no`
restoration) and G7 (committed `secret_key.txt` + ~30 MB of real business data in git).

---

## 3c. The void policy (decided 2026-09-10): **no voided data in the new database**

The old app never deleted anything — it set `is_void = 1` (and marked cancelled entries as
`type='CANCEL'`). The v4.4 app deletes for real: `app/services/void_rebuild.py::
hard_delete_transaction` removes the row *and* its children, and **no code path in `app/` or
`blueprints/` ever writes `is_void = 1`** (the only writer is the dummy-data generator). So the
legacy voided rows are dead weight that would sit in the new ledgers, stock and reports forever.

**Implemented:** the migration tool now purges them (ported from the retired Excel pipeline's
contract in `tools/migrate/_migrate_common.py`) — `is_void = 1` everywhere, cancelled `entry` rows,
cascade to children of purged parents, and rows whose parent never existed. Default **on**;
`--keep-voided` / GUI checkbox for a bit-for-bit archive.

| Measured on the real file | Rows |
|---|---|
| `is_void = 1` | 61 (`entry` 31, `account_transaction` 13, `payment` 13, `waive_off` 4) |
| Cancelled `entry` (not already void) | 24 |
| Cascaded children | 2 (`material_return` + `material_return_item` of a voided payment) |
| **Removed** | **87 of 29,266** |

**Verified after the purge:** 0 `is_void = 1` rows in any table · 0 cancelled entries · 0 dangling
parent references across all 22 parent/child pairs · integrity `ok` · `fk_violations 0` · value
parity identical · index parity · `RESULT: PASS`. And the app's own `consistency_report.py`
returns **byte-identical results** before and after (Account Balances OK, Material Totals OK, same
4 pre-existing source-data warnings) — because the app already excluded voided rows from those
computations, so nothing shifts.

Money that leaves with them (all previously excluded from reports anyway): `payment −18,664` ·
`account_transaction −446,114` · `entry qty −17,547.3` · `material_return −35,328` ·
`waive_off −279.1`.

**Former app-side exception — now RESOLVED (verified in code 2026-09-10):**
`hard_delete_transaction` now hard-deletes Booking, DirectSale **and Payment** alike
(`hard_delete_payment`: reverses the accounting / pending-bill / waive-off effects, deletes the
generated ledger entries, then removes the payment row; "Policy (owner decision): the database
holds no voided rows"). The audit trail is written to the append-only `audit_log` /
`accounting_audit_log` tables — which is where an audit trail belongs, not in a flagged
transaction row — so the *no voided rows at all* rule holds for future deletions too.

---

## 4. Bottom line for the next migration

**Go** — for an old file of this lineage (same table/column names) the tool is provably complete,
fast (<1 s) and safe, and the target template matches the app's models today.

Run it as a **three-step pipeline**, not a one-step tool:

```bash
# 1. migrate
python3 "migrate tool/migrate_tool.py" --cli \
  --old <OLD>.db --new <v4.4 template>.db --out output/<name>_migrated.db
#    → only accept RESULT: PASS (never a *.INCOMPLETE file)

# 2. load through the app's own importer (self-verifying, backs up first)
cd AMSCOPY9
python3 -m full_db_sync export --db ../output/<name>_migrated.db --out /tmp/AMS.amsdb
python3 -m full_db_sync verify --db /tmp/AMS.amsdb
python3 -m full_db_sync import --source /tmp/AMS.amsdb --db instance/ahmed_cement_v44_fresh.db --confirm

# 3. before the first boot, reset the data-loss baseline, then start the app
#    (the boot back-fills the 14 account classification columns)
rm -f instance/health_snapshot.json       # or: ALLOW_DB_DROP=1 for the first start
```

Before running it on **production**, do these four things — **1–4 are done** (see §3b), so today
the list is:

1. ✅ **G1** — refuse when OLD already looks v4.4 (prevents silent audit-trail corruption).
2. ✅ **G3** — pre-flight `NOT NULL` column check with an actionable error.
3. ✅ **G4** — value parity, so `PASS` means values, not just counts.
4. ✅ **G2 + G5** — NULL-filled columns reported, boot-day runbook printed, and the Import
   screen now re-baselines the health snapshot so the app cannot refuse to start.
5. ✅ **Void policy** — purged by default; the new database holds no voided data (§3c).
6. ⬜ **Run `check_template_sync.py`** — the §2.6 template-vs-models check, now one command.
   Re-run it whenever the app's models change (and consider wiring it into CI).
7. ✅ **App-side Payment delete** — now a real hard delete in `hard_delete_payment`
   (verified 2026-09-10); no voided rows are left by any delete path.
8. ✅ **Old `settings` policy** — now a decision *in the tool*: `--carry-settings` (GUI
   checkbox) loads the old file's company settings when the template's settings table is empty;
   default unchanged (template's kept, old rows flagged `REVIEW` for explicit merge).
9. ✅ **`uq_entry_auto_bill_no` restoration shipped** —
   `app/migrations/0001_restore_entry_auto_bill_unique_index.sql` applies automatically on the
   first start after the two duplicate `SB-GRN` bill numbers are cleaned (operator decision:
   keep one row per bill number); until then it retries at boot (logged) and the boot helper
   skips the index with a warning.

---

## 5. Files read for this assessment

* `migrate tool/` — `migrate_engine.py` (906 lines), `migrate_tool.py` (563), `test_migrate_engine.py` (274), `README.md`, `run_tool.bat`
* `data lab for migration old to new/` — `MIGRATION_REPORT.md`, `README.md`, `FIRST CLASS DATA/migrate.py` + `MIGRATION_NOTES.md` + `migration_report.txt`, `NewData/*`, `backups/*`, `AMSCOPY9_FULL_REFRESH_2026-09-09/*` (incl. `import_report.json`, `verify_report.json`, `export_report.json`)
* Earlier audits — `MIGRATION_TOOL_PROCEDURE_AUDIT.md` (63-procedure inventory, D-1…D-12), `ANOTHER_OLD_FILE_VERIFICATION.md`
* `AMSCOPY9/` — `models/*.py` (all 15 modules), `app/services/schema.py`, `health.py`, `constants.py`, `v44_schema.py`, `auto_migrate.py`, `legacy_migration.py`, `full_db_sync/*`, `docs/*` (incl. `FULL_DB_SQLITE_SYNC.md`, `legacy_migration_mapping.md`, `DATA_LOAD_VERIFICATION_REPORT.md`), `tools/migrate/*`, `tools/consistency_report.py`, `tools/health/preflight_check.py`, `tools/post_migration_audit/*`
* Databases inspected read-only: `ahmed_cement.db` (old), `NewData/ahmed_cement_v44_fresh.db` (template), `FIRST CLASS DATA/ahmed_cement_migrated.db` (reference output), `AMS_FULL_20260909-133924.amsdb` (snapshot)

**Environment:** Python 3.11 / SQLite 3.40.1, stdlib only (no Flask/SQLAlchemy/pandas installed), so
the app could not be booted here — the post-boot behaviour above was established by reading
`app/services/schema.py::_bootstrap_database` and `app/services/health.py`, not by execution.
