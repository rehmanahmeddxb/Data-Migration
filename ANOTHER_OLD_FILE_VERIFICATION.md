# "What if we give the tool ANOTHER old file?" — Verification + Fixes

**Date:** 2026-09-10 · **Branch:** `arena/01a0898a-data-migration`
**Companion to:** `MIGRATION_TOOL_PROCEDURE_AUDIT.md` (the full 63-procedure audit).
**Question answered:** *if we hand the migrate tool a different legacy file (not the exact `ahmed_cement.db` it was built on), will it shift every row into the v4.4 schema precisely, lose nothing, and display correctly in the app?*

---

## 0. Verdict

| | Before this work | After this work |
|---|---|---|
| A different old file with **any extra table** (even empty) | **Hard crash** mid-run (`migrate_engine.py:609`), no verdict, no report, and a fully-loaded output left on disk with no sidecar — importable downstream by mistake | No crash: empty extra table → `PASS` with a `[LEFT BEHIND]` line; table **with data** → `REVIEW` naming the table and row count |
| Old config rows (`settings`, `import_job`, …) | **Silently discarded, still `RESULT: PASS`** — silent data loss | `REVIEW` with *"rows in KEEP_FROM_NEW tables were NOT carried … merge explicitly if they matter"* |
| Data the old file carries in shared tables | already provably faithful | unchanged, re-proved again cell-by-cell on a **different dataset**: 63 tables, 0 rows missing, 0 extra, 0 cell mismatches after the documented user-id remap |
| Indexes | **209 of 261 template indexes lost**; `entry` left with zero; rescued only if the app later boots | **Full index parity** — 262 explicit indexes in the output (every template index present; only `uq_entry_auto_bill_no` relaxed, because the old data really has duplicate bill numbers — all rows kept) |
| Mid-run failure | half-written `*_migrated.db`, no report, GUI said "no output was produced" | `*.report.txt` written with `RESULT: FAILED`; partial output renamed `*.INCOMPLETE` so it cannot be imported |
| Default output path | landed next to NEW → re-detected as an input candidate by the GUI watcher | prefers the tool's own `output/` folder, which the watcher already excludes |
| End-to-end into the app | — | verified live: snapshot → verify `PASS` → import (24,797 rows, 0 FK violations) → real Flask boot → login, `/clients` renders **323/323** migrated clients, `/direct_sales` and `/ledger/1` render, and **every money figure and bill number on the rendered page exists in the migrated data** |

**Answer:** Yes — *now*. Before the fixes applied in this change (audit defects D-1, D-2, D-3, D-4, D-5, plus D-6/D-12 hardening), a different old file would either crash the run or silently lose config rows while reporting PASS. Nine stdlib regression tests in `migrate tool/test_migrate_engine.py` now lock all of this in (9/9 pass, ~4 s).

---

## 1. How this was verified (everything below was executed live)

Fixtures were built from the committed data-lab pair by *mutating* it, because the tool is data-agnostic — what changes with "another old file" is schema drift and failure behaviour, not the copy mechanics.

| # | "Another old file" scenario | Before fixes (reproduced) | After fixes |
|---|---|---|---|
| T1 | +1 extra **empty** legacy table (`report_cache`) | `OperationalError: no such table` after loading; exit 1 | `PASS`, line: `report_cache old=0 … [LEFT BEHIND]` |
| T1b | +1 extra legacy table **with 500 rows** | same crash; output existed fully loaded with **no report** | `REVIEW`: *"old tables not in new schema carried rows: report_cache (500 rows)"* |
| T2 | old file's own company config (1 `settings` + 1 `import_job` row) | **`PASS`** while both rows were discarded — silent loss | `REVIEW`: *"rows in KEEP_FROM_NEW tables were NOT carried … settings (1 row(s)), import_job (1 row(s))"* |
| T3 | old-only column `client.legacy_credit_note`, 40 non-null | `REVIEW` (correct) | `REVIEW` (unchanged) — the data is *not* carried, but it is named |
| T4 | genuinely **different business dataset**: clients renamed `BR2-CLIENT-*`, amounts changed, 909 sales + children deleted, users 6–7 removed, one column dropped | `PASS` (copy was already faithful) | `PASS` + cell parity 63 tables **0/0/0** + index parity full |
| T5 | injected mid-run failure at table 8 | 4.5 MB half-loaded output, integrity `ok`, **no report** — would import cleanly downstream | report written (`RESULT: FAILED — run aborted…`), output quarantined `*.INCOMPLETE`, raw name gone |
| R1 | swapped OLD/NEW · non-AMS file | refused | refused (unchanged) |

Baseline regression: the real `ahmed_cement.db → v4.4` run still reports **`PASS` in ~0.9 s**, is still byte-faithful (re-verified cell-by-cell: 0 missing / 0 extra / 0 mismatches outside the documented 1,880-value user-id remap), and now also proves **index parity** in its own report.

---

## 2. What was changed (all in `migrate tool/`)

`migrate_engine.py`
- **D-1**: the verification parity loop no longer queries the NEW file for old-only tables; it reports `[LEFT BEHIND]` and — when they carry rows — raises a `REVIEW` issue. The previously *unreachable* left-behind report is now the live path.
- **D-2**: rows present in old `KEEP_FROM_NEW` tables are reported as an issue (`dropped_keep`) instead of verifying against an `expect = new_seed` that hides them.
- **D-3**: the whole load is wrapped by an exception handler that writes the report (`RESULT: FAILED`) and quarantines any partial output as `*.INCOMPLETE` before re-raising.
- **D-4**: `_swap_table` now captures **every** index of the table (not only explicit unique ones): explicit indexes are re-created from their SQL; inline `UNIQUE` constraints (auto-indexes, no SQL) are re-created as explicit `CREATE UNIQUE INDEX` under a legal name (`sqlite_autoindex_x → uq_x`, since SQLite reserves the prefix). A new verification step **index parity** fails the run if any template index is missing without being explicitly relaxed. On the real data this restores the previously-silent `uq_cash_flow_difference_adjustment` and `uq_delivery_person` guarantees and re-creates all 9 `entry` indexes.
- **D-5**: `default_out_path` prefers the tool's own `output/` folder (which the GUI watcher already excludes from candidates), ending the output-re-detected-as-input loop. `README`'s output-path claim is now true.
- **D-6**: `_strip_secondary_unique` masks single-quoted string literals before the regex passes, so a `DEFAULT 'UNIQUE SIZE'` can no longer be corrupted.
- **D-12**: `inspect_database` opens each file once, not twice (GUI scan handle leak).

`migrate_tool.py`
- failure message no longer claims "no output was produced"; it names the `.INCOMPLETE` quarantine and the failure report.

`test_migrate_engine.py` (new)
- 9 stdlib tests: T1, T1b, T2, T3, T4 (parity + index parity), T5, the two refusal guards, and the D-5 path rule. Skips cleanly if the data-lab fixtures are absent.

---

## 3. "Shows in app accurately" — the full chain was run against a *different* old file

`branch2_T4` output (a different business's data) was pushed through the app's own pipeline:

1. `full_db_sync export` → snapshot, `total_rows 24,797`
2. `full_db_sync verify` → **`verification: PASS`**, integrity ok, `fk_violations 0`
3. `full_db_sync import --confirm` into a fresh app DB copy → `rows_inserted_total 24,797`, `fk_violations 0`, auto pre-import backup created
4. Real Flask app (`main.py`, production factory) booted against it:
   boot self-repair rebuilt the indexes (260 → all but the documented relaxed one), boot summary read the migrated data (`client_total 324, direct_sale 1,810, entry 5,259, payment 917`)
5. Logged in through the real `/login` (CSRF + scrypt) and rendered pages:
   - `/clients` → **323/323 migrated `BR2-CLIENT-*` names**
   - `/direct_sales` → renders; **every money figure and bill number on the page traces to a migrated row** (the only "extra" numbers are the next-bill preview from `bill_counter` and GRN bills the page legitimately shows — both also migrated rows)
   - `/ledger/1` → renders with the migrated client name and its payments

---

## 4. What "no data loss" means — and the two things it does *not* cover

1. **KEEP_FROM_NEW policy**: the tool intentionally keeps the *fresh template's* config tables (settings, migration infra, import history). That is the right default (a fresh v4.4 install's config should not be clobbered by the old file's), but it *is* a decision: after this fix the run goes `REVIEW` whenever the old file actually had config rows, and the operator must merge them explicitly (e.g. re-enter company name / tax / prefixes, or copy the row). If you'd rather auto-carry old `settings` when the template's is empty, that is a one-line policy change in `run_migration` — say the word.
2. **Business-clean ≠ lossless**: the tool copies the old file *faithfully*, including its warts (negative stock on 55 materials, voided/cancelled rows, the 2 duplicate `SB-GRN` bill numbers — which is why `uq_entry_auto_bill_no` relaxes). `tools/consistency_report.py` and `tools/health/preflight_check.py` flag those on any migrated DB — including the production one. They are source-data conditions, not migration errors.

---

## 5. Re-run everything

```bash
# regression suite (stdlib only, ~4 s)
python3 "migrate tool/test_migrate_engine.py" -v

# a real run on the proven pair
python3 "migrate tool/migrate_tool.py" --cli \
  --old "data lab for migration old to new/ahmed_cement.db" \
  --new "data lab for migration old to new/NewData/ahmed_cement_v44_fresh.db" \
  --out /tmp/out.db          # RESULT: PASS, index parity line included

# app chain
cd AMSCOPY9
python3 -m full_db_sync export --db /tmp/out.db --out /tmp/AMS.amsdb
python3 -m full_db_sync verify --db /tmp/AMS.amsdb
python3 -m full_db_sync import --source /tmp/AMS.amsdb --db /tmp/app.db --confirm
APP_DB_PATH=/tmp/app.db python3 tools/consistency_report.py
```
