# AMS Data-Migration Tool — Complete Procedure Inventory + Working / Non-Working Audit

**Audit date:** 2026-09-10
**Audited by:** Arena agent session, branch `arena/01a08975-data-migration`
**Scope read in full:** `migrate tool/` (7 files) · `data lab for migration old to new/` (23 files) · `AMSCOPY9/` (527 files — all migration-, import-, audit- and schema-related code plus every audit document)
**Method:** every claim in every document was re-checked against the actual database files and re-run live. Nothing below is taken on trust from a report.

---

## 0. Verdict

| Question asked | Answer |
|---|---|
| Is the migrate tool effective / good? | **Yes for the job it was used for** — on the real `ahmed_cement.db → v4.4` data it is provably correct: 29,263 source rows → 29,266 rows, **zero rows lost, zero values changed** except the 1,880 `user_id` values it was *supposed* to remap. It ran in **0.74 s** and produced byte-equal data to the hand-made "FIRST CLASS DATA" result. |
| Is it verified that no mistake was made? | **The data itself: yes — I re-proved it cell by cell (63 tables, every id, every shared column).** But the *tool's own* verification is weaker than its documentation: it checks row **counts**, not values, and it does **not** check index parity, `KEEP_FROM_NEW` discards, or business-level consistency. 5 documented guarantees are not actually enforced. |
| Working vs non-working | **Core migration engine: WORKING.** **10 real defects found (+2 minor)** — 1 hard crash, 2 silent-data-loss/verification holes, 1 silent 80 % index loss, and 6 broken/unusable gates or doc-vs-artifact contradictions. Details in §4 and §5. All 63 procedures of the whole pipeline are inventoried in §2. |
| Should it be trusted for the next migration? | Yes, **after** applying fixes D-1…D-4 (all are small; each has a one-line fix in §5). Do not import a `*_migrated.db` whose `.report.txt` sidecar is missing — that is the signature of a crashed run (§5 D-3). |

---

## 1. What "the migration tool" actually is in this repo — 4 cooperating stages

```
OLD production SQLite            FRESH v4.4 SQLite (schema + seed)
  ahmed_cement.db                  NewData/ahmed_cement_v44_fresh.db
  64 tables / 29,263 rows              69 tables / 3,558 rows
        │                                   │
        └────────────┬───────────────────────┘
                     ▼
  STAGE 1  "migrate tool"  (migrate_tool.py GUI + migrate_engine.py)      ← the tool in question
           also identical: data lab/…/FIRST CLASS DATA/migrate.py
           RESULT: ahmed_cement_migrated.db   69 tables / 29,266 rows   [PASS in 0.74 s]
                     │
                     ▼
  STAGE 2  AMSCOPY9/full_db_sync  (engine.py + CLI + Import/Export UI)    ← app-side loader
           export → .amsdb snapshot → verify → clean → import → verify
           RESULT: instance/ahmed_cement_v44_fresh.db  [verification: PASS, 69/69 tables]
                     │
                     ▼
  STAGE 3  app boot  (app/services/schema.py _ensure_* + auto_migrate.py)
           re-creates lost indexes, bill counters, OPEN-KHATA seed, password hardening
                     │
                     ▼
  STAGE 4  audits:  tools/consistency_report.py · tools/health/preflight_check.py ·
                    tools/post_migration_audit/audit_findings.py · tools/migrate/04_run_post_import_audit.py
                    (a retired Excel-era pipeline: tools/migrate/01→05)
```

Two **incompatible** migration philosophies coexist in the repo and nothing reconciles them:

| | Excel path `tools/migrate/01–05` | SQLite path `migrate tool` + `full_db_sync` |
|---|---|---|
| Transport | `.xlsx`, one sheet per table | SQLite file / `.amsdb` |
| Voided / cancelled rows | **purged** (11,687 of 36,272 removed) | **copied verbatim** |
| Opening balances recomputed | yes (planned in `06_opening_state_migration.py`) | no — full history is carried |
| Verification | 3 gates (01 → 03 → 04) | integrity + count parity + FK + duplicates |
| Status | superseded, `06` **never written** | what actually produced today's DB |

---

## 2. COMPLETE LIST OF PROCEDURES (what the tool does, step by step)

Legend: ✅ verified working by me · ⚠️ works but unverified/weak · ❌ broken

### STAGE 1 — `migrate tool/migrate_engine.py::run_migration()`

| # | Procedure | Self-verification built into the tool | Verdict |
|---|---|---|---|
| **P-01** | Reject if OLD file missing / NEW file missing / OLD == NEW | raises `MigrationError` (exit 2) | ✅ |
| **P-02** | Read-only probe of both files (`inspect_database`): table list, per-table counts, total rows, `integrity_check`, `foreign_key_check`, `journal_mode` | used for every later gate | ⚠️ opens each connection **twice** — leaks a handle per call (`migrate_engine.py:118-127`) |
| **P-03** | AMS-ness test: both files must contain `user`, `client`, `entry` | refuses anything else | ✅ my T2 proved refusal |
| **P-04** | **Refuses a corrupt source** (`integrity != ok` on either file) | explicit hard stop | ✅ my T3 proved refusal |
| **P-05** | v4.4 detection: `user.access_mode`, `account.class_category`, `account_transaction.idempotency_key` (≥2) or `cash_day_account_position` | drives the swap guard | ✅ |
| **P-06** | `classify()` advisory warnings: roles look swapped / OLD has tables NEW lacks / neither file is v4.4 | surfaced in GUI + `NOTE:` lines | ✅ |
| **P-07** | **Hard role guard**: refuses if OLD looks v4.4 and NEW does not; refuses if NEW is not v4.4 | cannot be overridden | ✅ my T1 proved refusal |
| **P-08** | Output path: default naming, refuse to overwrite unless allowed, refuse `out ∈ {old,new}`, create parent dir | `MigrationError` | ✅ my T6/T8 proved both refusals · ⚠️ default path contradicts the README (D-5) |
| **P-09** | **Consistent staging copy of OLD** via `sqlite3` backup API → folds committed `-wal` content into a temp file; source never opened for writing | staging is the only source the loader reads | ✅ verified (D-9 note): a row committed only in the WAL appeared in the output |
| **P-10** | **Output = copy of NEW** (never the NEW file itself) via the same backup API | both inputs left byte-identical | ✅ verified: md5 of both inputs unchanged after a full run |
| **P-11** | `PRAGMA foreign_keys=OFF` + `ATTACH` staging as `old` | — | ✅ |
| **P-12** | Table classification: `shared = old ∩ new`; `KEEP_FROM_NEW` (14 infra/config tables) keep the NEW rows; `user` handled specially; new-only tables left alone | report lines per table | ⚠️ see D-2 |
| **P-13** | Per business table: read old `table_info` and target `table_info`, copy the **intersection of columns by name** | skipped-column detection | ✅ rename/order-safe; verified `entry/direct_sale/client/payment` byte-identical |
| **P-14** | Measure OLD-only columns that carry **non-NULL data** → recorded | becomes a `REVIEW` reason | ✅ my T5 proved REVIEW with `client.legacy_credit_note (40 non-null)` |
| **P-15** | Save the table's **UNIQUE** index DDL *before* dropping the table | so it can be re-created | ⚠️ saves **only** unique indexes — see D-4 |
| **P-16** | Build `tmp_<table>` from the target DDL with secondary `UNIQUE` stripped | — | ⚠️ regex-based; can mangle string literals — D-6 |
| **P-17** | `INSERT INTO tmp SELECT … FROM old.<table>` — **every** old row, **original primary keys**, no dedup, no merge | row count of tmp vs old | ✅ duplicates provably kept: `entry` ids 9084/10116 (`SB-GRN-1024`) and 9830/10117 (`SB-GRN-1042`) all present |
| **P-18** | `DROP` original, `ALTER TABLE tmp RENAME TO` original | — | ✅ no `tmp_*` residue in any output |
| **P-19** | Re-create each saved unique index; if the old data violates it → **relax only that index**, record it | `RELAXED=[…]` per table + summary | ✅ exactly 1 relaxed on real data: `entry.uq_entry_auto_bill_no`; all others re-created |
| **P-20** | Clear NEW-only seed tables that would point at replaced rows (`cash_day_account_position`, `cash_day_lock`) | `[CLEAR] seed=n -> 0` | ✅ |
| **P-21** | **User merge**: keep the fresh admins, insert every old user at `max(id)+1` | `[MERGE] kept / added / id map` | ✅ 2 kept + 7 added = 9; id map `{1:3 … 7:9}` matches the docs exactly |
| **P-22** | Username collisions renamed `X_legacy`, `X_legacy2`, … | — | ✅ `Admin_legacy`, `Adnan Ahmed_legacy` as documented |
| **P-23** | Remap every `user_id` / `created_by_id` in every table with **one atomic `CASE` UPDATE** | `user-id refs remapped: [...]` | ✅ single-statement `CASE` avoids the classic chained-remap corruption; only those 3 tables changed, only that column |
| **P-24** | Reset `sqlite_sequence` for `AUTOINCREMENT` tables so new inserts don't collide | — | ⚠️ dead code here: **0** `AUTOINCREMENT` tables exist in this schema (harmless) |
| **P-25** | `COMMIT` | — | ⚠️ no rollback on later failure — D-3 |
| **P-26** | **Verification A — per-table row parity**: `old` vs `new_seed` vs `migrated` vs computed `expect`, per table | `MISMATCH` ⇒ REVIEW | ⚠️ counts only, and `expect` for `KEEP_FROM_NEW` tables is the NEW count — D-2 |
| **P-27** | **Verification B** `PRAGMA integrity_check` on the output | ⇒ REVIEW | ✅ |
| **P-28** | **Verification C** `PRAGMA foreign_key_check` | ⇒ REVIEW | ✅ 0 violations |
| **P-29** | **Verification D — duplicate scan** over every remaining UNIQUE index, honouring partial-index `WHERE` predicates and the NULL-distinct rule | ⇒ REVIEW | ✅ correct logic (it deliberately ignores expression indexes) |
| **P-30** | **Verification E — FK orphan scan** via `foreign_key_list`, per column | ⇒ REVIEW | ✅ none |
| **P-31** | **Verification F — logical user-FK scan** (`user_id`→`user.id`) because the Django DDL declares no FKs | ⇒ REVIEW | ✅ none — this is the check most tools forget; good design |
| **P-32** | **Verification G — old-only tables left behind** (with row counts) | ⇒ REVIEW | ❌ **unreachable**: it sits after the loop that crashes — D-1 |
| **P-33** | Verdict: `RESULT: PASS` only if no issue class fired, else `REVIEW` + reasons + relaxed-index list | exit 0 / 3 | ⚠️ "no data loss" is over-claimed: 6 of 69 tables are exempt from parity — D-2 |
| **P-34** | Write `*.report.txt` + `*.report.json` sidecars; `DETACH`; delete staging | — | ⚠️ not written when a run raises — D-3 |
| **P-35** | GUI: drop-folder watcher (1.5 s), auto-detect `.db/.sqlite/.sqlite3/.amsdb`, `Use as OLD/NEW`, live swap warning, progress %, colour-coded log, Open report / Open folder, one-shot result dialog | — | ⚠️ code is sound but **cannot run here** (no `tkinter`); needs Python-Tk on the target machine |
| **P-36** | Headless modes: `--cli --old --new [--out] [--no-overwrite]`, `--scan`, and **auto-fallback to CLI when tkinter is absent** | exit codes 0/2/3 | ✅ `--scan` and `--cli` both verified working |
| **P-37** | Inputs are read-only (`file:…?mode=ro`) | — | ⚠️ true for content, but a read-only open of a WAL DB still creates `-wal`/`-shm` sidecars next to the **source** (observed) — cosmetic, but it dirties the input folder |

### STAGE 2 — `AMSCOPY9/full_db_sync/` (the loader that put the data into the app)

| # | Procedure | Verdict |
|---|---|---|
| Q-01 | Export: integrity gate on source **before** copying | ✅ |
| Q-02 | Export via backup API (WAL-safe) | ✅ |
| Q-03 | Embed self-describing `__ams_full_db_meta__` (tool, spec_version, exported_at, source, total_rows, per-table counts) | ✅ present in the 2026-09-09 snapshot |
| Q-04 | Seal the snapshot: `wal_checkpoint(TRUNCATE)` → `journal_mode=DELETE` → delete `-wal/-shm/-journal` | ✅ snapshot is a genuine single file (70 tables, 29,273 rows incl. meta) |
| Q-05 | Export ends with a full `verify_snapshot`, raising if it isn't clean | ✅ this is the single best safety feature in the whole system |
| Q-06 | Verify: integrity + `foreign_key_check` + AMS probe + meta presence + per-table count vs meta | ✅ ran clean against the committed snapshot |
| Q-07 | Clean: backup first, **FK topological order**, children-before-parents, single transaction, rollback, `sqlite_sequence` reset | ✅ |
| Q-08 | Import: refuse unless target exists; refuse `source == target`; validate mode | ✅ |
| Q-09 | **Column-compatibility abort before any write** — this is why the raw old DB can't be imported directly | ✅ verified by `AUTO_MIGRATION_REPORT.md §3.1` and reproducible |
| Q-10 | Unique-index plan: **relax** only the violating droppable indexes; **hard-block** if an inline `sqlite_autoindex` would be violated | ✅ the `blocked` path is a real guard the migrate tool lacks |
| Q-11 | Automatic pre-load backup (`pre_full_db_import_*.db`) | ⚠️ code works; the 2026-09-09 backup is **not in the repo** (`instance/*.db` is gitignored) so it cannot be verified |
| Q-12 | Load with original primary keys, batches of 1,000; `append` mode skips existing pks | ✅ |
| Q-13 | Post-import verification: integrity + FK + per-table parity vs source → `verification: PASS/FAIL` | ✅ but parity is measured against *itself*, so a bad input file verifies PASS — see D-3 |
| Q-14 | `--confirm` required for every write command | ✅ |
| Q-15 | UI routes + 13 tests in `tests/test_full_db_snapshot.py` (incl. tamper detection) | ⚠️ could not execute (no Flask/pytest in this sandbox) |

### STAGE 4 — audits that are supposed to prove "no mistake"

| # | Procedure | Verdict |
|---|---|---|
| R-01 | `tools/consistency_report.py` — 9 read-only business checks (account balances vs ledger, material totals vs entries, orphan payments, orphan account transactions, sales missing stock entries, credit sales missing pending bills, orphan invoices, bookings missing pending bills, health-snapshot freshness) + `--json` + `--fail-on-error` for CI | ⚠️ **works only if you know to set `APP_DB_PATH`** — the default path is the retired `instance/ahmed_cement.db`, so as documented it exits 1 |
| R-02 | `tools/health/preflight_check.py --db … --json` | ✅ accepts `--db`, best-behaved audit tool |
| R-03 | `tools/post_migration_audit/audit_findings.py` — 8 post-migration defect scans | ❌ hardcoded `instance/ahmed_cement.db`, no CLI, no `--db`; crashes on the first query **and returns exit 0**; also **creates an empty `instance/ahmed_cement.db`** in the repo as a side effect |
| R-04 | `tools/migrate/01_audit_legacy.py` — purge profile, cascade orphans, dangling FKs, account/material ledger pre-checks, 11 money sums, duplicate natural keys, bill-counter safety, inactive-master counts | ⚠️ requires pandas + openpyxl (not installed here); logic reviewed statically, is sound |
| R-05 | `02_build_clean_export.py` → clean xlsx + `purge_report.json` (machine-readable removal log) | ✅ artifact exists and is internally consistent with the workbook (below) |
| R-06 | `03_verify_clean_export.py` — 7 leak gates on the clean workbook | ✅ I re-ran the two hardest gates myself without pandas → **0 voided rows, 0 cancelled entries** in the committed `ALLEXPORT-CLEAN-17-08-2026.xlsx` (51 sheets, 24,585 data rows + 6 meta rows) |
| R-07 | `04_run_post_import_audit.py` — executes 10 queries from `post_import_audit.sql` against the migrated DB and compares 13 money totals to `EXPECTED_TOTALS` | ❌ **gate is unsatisfiable today: 11 of 13 hard-coded baselines do not match the committed clean export** (details §6) |
| R-08 | `05_load_app_db.py` — backup → app's own full-raw importer (`replace_tenant_data`) → `client_code` backfill → `post_import_enrichment.sql` | ⚠️ functional but requires the whole Flask app; `MIGRATION_AUDIT.md §J` promised to replace it with a deprecation stub — **never done** |
| R-09 | `06_opening_state_migration.py` + `_opening_state_common.py` (22 verification checks incl. "old final balance = new opening balance", `paid=amount` per carry-forward booking, `balance_minor = opening_balance_minor`) | ❌ **planned in `MIGRATION_AUDIT.md §H/§I/§N`, never created.** The audit ends with "I will proceed to implementation as soon as the user approves this audit document" — approval/implementation never happened |
| R-10 | App boot self-repair: `_ensure_model_columns`, `_ensure_performance_indexes`, `_ensure_auto_bill_unique_indexes` (skips + logs where data has duplicates), `_ensure_*_idempotency_index`, `ensure_open_khata_client`, `retire_legacy_database_files` | ✅ this is what rescued the index loss — and it is undocumented as a migration dependency |
| R-11 | `app/services/auto_migrate.py` — numbered `NNNN_*.sql` applied at every boot, recorded in `migration_history`, stamped into `schema_version`, destructive SQL blocked unless `MIGRATIONS_ALLOW_DESTRUCTIVE=1`, concurrency-tolerant | ✅ reviewed; `app/migrations/` is intentionally empty (no migration shipped yet, incl. the promised `uq_entry_auto_bill_no` restore) |

---

## 3. Independent proof that no mistake happened **in the data** (this is the part the reports only claim)

I did not use the tool's report. I re-derived everything:

### 3.1 Cell-level parity, OLD → migrated output

| Check | Result |
|---|---|
| Tables compared (shared, int-id) | 63 |
| Row-count differences | **0** |
| Ids present in OLD but missing in output (data loss) | **0** |
| Unexpected extra ids | **0** |
| **Value (cell) mismatches on shared ids** | **1,880 rows, all in exactly one column: `user_id`** — `audit_log` 1,629 · `accounting_audit_log` 223 · `user_login_session` 28 |
| `entry` (5,259), `direct_sale` (2,719), `client` (323), `payment` (917) | **identical on every shared column** |
| Every other table | **identical on every shared column** |

→ The only mutation is the documented user-id remap. **This is a genuinely faithful copy.**

### 3.2 The GUI/CLI tool vs the hand-made "FIRST CLASS DATA" result

| Check | Result |
|---|---|
| All 69 tables' full contents | **identical** (0 tables differ) |
| Index set | **identical** (59 explicit, same names, same SQL) |
| `uq_entry_auto_bill_no` | absent in both — the documented compromise matches |
| Re-run twice (idempotency) | **identical content both times** |

→ The packaged tool reproduces the proven manual migration exactly. It is a safe re-implementation, not a regression.

### 3.3 Migrated DB → the FINAL app DB (after `full_db_sync` import + app boot)

| Check | Result |
|---|---|
| 66 shared tables compared cell-by-cell | **65 completely identical** |
| `account` (12 rows) | 7 columns changed by the **app's own boot**, not the migration: `class_category`, `class_subcategory`, `class_account_type`, `channel`, `account_status` backfilled from NULL, `revision` +1, `updated_at` stamped |
| Added rows | `client` +1 (OPEN-KHATA id 324), `audit_log` +2 (that login) — exactly as `DATA_LOAD_VERIFICATION_REPORT.md` states ✅ that report is accurate |
| `PRAGMA integrity_check` | `ok` in OLD, MIGRATED, FINAL and the `.amsdb` |
| `PRAGMA foreign_key_check` | 0 in all |
| Duplicate values in any existing UNIQUE index | **0 in all** |

### 3.4 Financial / referential sanity the tools never check — I checked instead

| Check | Result |
|---|---|
| `bill_counter` vs highest bill number actually in the data | **safe**: `BK 1439>1438`, `CP 1926>1925`, `GRN 1059>1058`, `RTN 1095>1094`, `SL 3831>3830`; `GEN 1000`/`SP 1001` unused → **no new bill can collide with a migrated one** |
| Money mirror columns `amount_minor` / `balance_minor` / `*_minor` vs float, all tables, all 3 DBs | **0 NULL, 0 mismatch** — the `*_minor` source-of-truth stayed consistent through both stages |
| `password_plain` in `user` | **empty in OLD, MIGRATED, FINAL and the snapshot** → the legacy plaintext-password exposure did **not** travel with the data |
| `sqlite_sequence` / AUTOINCREMENT | no AUTOINCREMENT tables → no hidden pk-collision risk |
| WAL content at read time | a row committed only in the WAL **was captured** by the staging copy (P-09) |

### 3.5 The migrated data is a faithful copy **of imperfect data** — identical in all 3 generations

| Business condition | OLD | MIGRATED | FINAL app |
|---|---|---|---|
| Materials with **negative stock** | 55 / 68 | 55 / 68 | 55 / 68 |
| Orphaned invoices (no parent sale) | 87 | 87 | 87 |
| Voided rows carried (`is_void=1`) | payment 13, entry 31 | same | same |
| Cancelled `entry` rows carried | 25 | 25 | 25 |
| Sales without stock entries | 6 | 6 | 6 |
| Bookings with due > 0 but no pending bill | 1 | 1 | 1 |

→ **Not a migration error** (nothing changed) — but it is the reason `tools/consistency_report.py` returns `⚠ WARN / 4 issues` on the migrated DB, and it proves that *"no rows lost"* ≠ *"business-clean"*. The Excel pipeline (R-04…R-06) was explicitly built to purge these; the SQLite pipeline has no purge step, and no document says which policy wins.

---

## 4. WORKING vs NON-WORKING

### ✅ WORKING (verified by execution)

| Item | Evidence |
|---|---|
| `migrate tool` end-to-end CLI run | `RESULT: PASS`, 29,266 rows, 69 tables, **0.74 s**, exit 0 |
| Source files untouched | md5 of OLD and NEW identical before/after |
| Data fidelity | §3.1 — 0 lost ids, 0 unexpected value changes |
| Equivalence to the proven manual script | §3.2 — 0 differing tables, identical indexes |
| Idempotent re-run | §3.2 |
| Refusal guards (7 of them) | swapped roles, non-AMS, corrupt source, non-v4.4 target, out==input, overwrite-off, missing file — all verified to refuse |
| Duplicate handling | only `entry.uq_entry_auto_bill_no` relaxed; all 4 duplicate rows kept; 0 violations on every other unique index |
| User merge + user-id remap | exactly as documented (`{1:3…7:9}`, `*_legacy` renames), 0 orphan refs |
| Extra-column detection | REVIEW raised with correct count (40 non-null) |
| `full_db_sync` verify against the committed snapshot | `integrity ok`, `fk 0`, meta parity, 29,266 rows |
| `full_db_sync` safety design | pre-write column check, unique-index `blocked` path, always-on backup, export self-verifies |
| Bill-counter safety, money mirrors, password hygiene, WAL capture | §3.4 |
| `tools/health/preflight_check.py` | accepts `--db`, runs |
| `tools/consistency_report.py` | runs and correctly flags 4 business issues when `APP_DB_PATH` is given |

### ❌ NON-WORKING / BROKEN

| # | What fails | Severity | Evidence |
|---|---|---|---|
| **D-1** | `migrate tool` **crashes instead of reporting REVIEW** whenever the OLD file contains any table the NEW schema lacks — **even an empty one** | **High** — the tool's headline promise is "nothing is hidden" | `OperationalError: no such table: legacy_widget` / `… django_migrations`; `migrate_engine.py:609`; reproduced 2× |
| **D-2** | Rows in `KEEP_FROM_NEW` tables are **silently discarded and the run still reports `RESULT: PASS`** | **High** for config-bearing migrations (settings, import_job, staff_email…) | injected 1 old `settings` row → `[MERGE]` output `settings in OUTPUT: []`, `RESULT: PASS, issues: []` |
| **D-3** | **No rollback**: any mid-run exception leaves a half-loaded `*_migrated.db` with **no report sidecar**, while the GUI prints "no output was produced" | **High** — an unverified partial DB can be imported downstream and will verify `PASS` | failure injected at table 8 → 2.8 MB output, `entry=1, client=1, user=2` (i.e. the seed data), `integrity_check: ok`, no `.report.txt` |
| **D-4** | **209 indexes are lost** by the swap and never checked: output has 52 explicit indexes vs 261 in the v4.4 template; `entry` ends with **zero** indexes; every `ix_*` on every business table is gone | **Medium-High** — rescued only because the app re-creates them at first boot (§3.3); a `*_migrated.db` handed over without a boot is degraded | index-set diff, §5 D-4 list |
| **D-5** | README output-path claim is wrong: docs say `output/<name>_migrated.db`, code writes `default_out_path = <NEW's folder>/<old_stem>_migrated.db`. Follow the README (drop DBs in the tool folder) and the result lands in the **input** folder and is then **re-detected as an input candidate**, mislabelled "v4.4 schema (NEW template)" | Medium — self-feeding input/output loop, easy to pick your own output as the schema source | `default_out_path()` → `…/NewData/ahmed_cement_migrated.db`; `--scan` listed my output as a candidate |
| **D-6** | `_strip_secondary_unique` is a regex over DDL and **corrupts string literals**: a column default of `'UNIQUE SIZE'` becomes `' SIZE'` | Low likelihood, silent schema change | direct call, before/after in §5 |
| **D-7** | `04_run_post_import_audit.py` (gate 3) **cannot pass**: 11 of 13 hard-coded `EXPECTED_TOTALS` don't match the committed clean export | Medium — the documented gate is decorative | §6 table |
| **D-8** | `tools/post_migration_audit/audit_findings.py` cannot be pointed at anything: hardcoded retired path, no args, **exits 0 while raising a traceback**, and **creates a 0-byte `instance/ahmed_cement.db`** in the repo | Medium — the "post-migration audit" is unusable | run output + `ls` (I removed the stray file) |
| **D-9** | `tools/consistency_report.py` — the documented "one-command health check" — exits 1 as shipped (default DB is the retired `ahmed_cement.db`); the fix (`APP_DB_PATH`) is undocumented | Low-Medium | `ERROR: Database not found at …/instance/ahmed_cement.db` |
| **D-10** | The Excel-era pipeline is **unrunnable as delivered**: `01/02/03` need pandas+openpyxl, `05` needs the full Flask app; no `requirements.txt` is referenced from `tools/migrate/README.md`, and the promised replacement `06_opening_state_migration.py` + `_opening_state_common.py` **do not exist** | Medium — the documented 3-gate process cannot be re-run | import errors; `ls tools/migrate/` |
| D-11 | GUI cannot launch where `tkinter` is absent (falls back to CLI, which then *errors* unless `--old/--new` are given); `run_tool.bat` needs "Add python.exe to PATH" ticked | Info for Windows users | sandbox has no `_tkinter` |
| D-12 | `inspect_database` opens every connection twice and never closes the first → handle leak on every GUI refresh (called on each detected file, every 1.5 s scan) | Low | `migrate_engine.py:118-127` |

---

## 5. The defects in detail, each with a minimal fix

**D-1 — crash on any old-only table (`migrate_engine.py:600-616`)**
The parity loop queries the **NEW** file for every table in `old ∪ out`:
```python
for t in sorted(old_set | out_set):
    ...
    o = old_ro.execute(...)          # fine
    n = new_ro.execute(f'SELECT COUNT(*) FROM {_q(t)}')   # ← table may not exist in NEW
```
Any legacy file carrying e.g. `django_migrations`, `django_session`, `auth_permission*`, `celery_taskmeta` (the normal case for a Django-era DB) aborts the run **after** the load, **before** the `left_behind` report at line 640 — so the "lists exactly what is left behind" path is dead code.
```python
# fix: guard the new-file probe, and treat old-only tables as data instead of crashing
if t not in new_set:
    n = None
    say(f"  {t:32} old={o:6} new_seed=   n/a migrated={outn:6} expect=0 [LEFT BEHIND]")
    if o: left_behind.append(f"{t} ({o} rows)")
    continue
```
Also make `REVIEW` include a `left_behind` reason *before* any exception path, and add a regression test for "old has an extra empty table".

**D-2 — `KEEP_FROM_NEW` discards old rows but reports PASS (P-12/P-26)**
`expect = n` for those 14 tables, so old rows there are dropped with a green verdict. In the 2026-09-09 run this was **harmless** (all 14 were empty in the source — verified in `migration_report.txt`), but it is a silent-loss hole.
```python
# fix: fail the verdict when a kept-from-new table actually had data
elif t in KEEP_FROM_NEW:
    expect = n
    if o:  issues.append(f"{o} row(s) in old table '{t}' were not carried "
                         f"(KEEP_FROM_NEW); merge them explicitly if they matter")
```

**D-3 — no rollback, and a false "no output was produced" (P-25/P-34, `migrate_tool.py::_finish_error`)**
Wrap the load in an explicit transaction with rollback-to-template, or at minimum: write `*.report.txt` **in a `finally`** with `RESULT: FAILED`, and rename an incomplete output to `*.INCOMPLETE.do-not-import.db`. Without it, `full_db_sync` will happily import the corpse — its only source-side checks are `integrity_check` + AMS probe + row parity *against that same file*, which trivially holds.
```python
# fix in migrate_engine.run_migration — surround the load+verify block with:
except BaseException as exc:
    say(f"RESULT: FAILED — run aborted: {exc}")
    say("  the output file is INCOMPLETE and must not be imported")
    try:                                   # write the report even on failure
        out.with_suffix(out.suffix + ".report.txt").write_text("\n".join(report_lines))
    except OSError:
        pass
    try:                                   # quarantine, or delete, the partial file
        os.replace(out, out.with_suffix(out.suffix + ".INCOMPLETE"))
    except OSError:
        pass
    raise
```

**D-4 — indexes are dropped and never restored (`_swap_table`)**
`saved_unique` keeps `origin != 'pk' and uniq` **only**. Every non-unique `ix_*`/`idx_*` (and `uq_*` on *empty* new-only tables) is destroyed with the table. Measured loss: **261 → 52 explicit indexes; 209 lost**, e.g. `ix_entry_material`, `ix_entry_type`, `ix_direct_sale_client_code`, `ix_payment_date_posted`, `ix_account_transaction_to_account_id`, `ix_pending_bill_client_code` … `entry` is left with **no index at all**.
```python
# fix: capture and re-create EVERY index of the table, not just unique ones
saved = con.execute(f'PRAGMA index_list({_q(t)})').fetchall()      # keep uniq AND non-uniq
...
for name, index_sql, origin in saved:                               # after the rename
    if index_sql:
        con.execute(index_sql)          # unique ones may fail -> relax; plain ones must never
```
and add a verification step: `index_parity = set(new) - set(out)` must be empty except the explicitly relaxed list. Two index losses survive to today's production DB — `uq_entry_auto_bill_no` (documented) **and `uq_cash_flow_entry_idempotency_key`, which is documented nowhere**; it is only re-created lazily by `cash_flow_svc._cf_ensure_indexes()` when someone opens the cash-flow report, so until then cash-flow **idempotency/double-post protection is off** in a money table.

**D-5 — README/code output-path mismatch** — change `default_out_path` to prefer `Path(__file__).parent/"output"` when it exists (or correct the README). Both are one line; today they disagree and the disagreement is what creates the input/output re-detection loop.

**D-6 — regex DDL editing** — replace the three `re.sub(r"…UNIQUE…")` calls with `PRAGMA index_list` + column-constraint parsing, or at minimum mask single-quoted string literals before stripping:
```python
protected = re.sub(r"'(?:[^']|'')*'", lambda m: m.group(0).replace('U','\x00'), s)  # restore after
```

**D-7 / D-10 — the Excel-era gates** — either delete `tools/migrate/01–05` (they are superseded and unrunnable), or pin the deps (`pandas`, `openpyxl`) in a `requirements-migrate.txt` and refresh `EXPECTED_TOTALS` from the artifact. Do not leave a "must print PASS" gate that cannot pass.

**D-8 / D-9 — audit tool usability** — add `--db` (default `instance/ahmed_cement_v44_fresh.db`, honouring `APP_DB_PATH`) to `consistency_report.py` and `audit_findings.py`; open them `file:…?mode=ro` so they cannot create files; and `sys.exit(1)` on exception instead of dying with `exit 0`.

---

## 6. Documentation vs the actual artifacts (the audit contradictions)

The **databases are consistent**; several **documents are not**. Each row below was measured from the committed files.

| Claim (source) | Actual artifact | Status |
|---|---|---|
| `tools/migrate/README.md`: "Rows in source / kept / removed **35,717 / 24,054 / 11,663**"; "verified on the 2026-08-14 export"; "Do not load `ALLEXPORT-14-08-2026_05-51PM.xlsx`" | committed `purge_report.json` = **36,272 / 24,585 / 11,687**, generated from **`ALLEXPORT-17-08-2026_01-54PM.xlsx`**; workbook itself has 24,585 data rows | ❌ gate table describes a different, un-committed export (`CONTINUATION_SUMMARY.md` has the correct 36,272→24,585 figures) |
| `purge_report.json` per-table breakdown | `void 11,236 + cancel 70 + cascade 232 + missing-parent 165 = 11,703` vs `rows_removed 11,687` | ⚠️ 16 rows counted by two rules; the report has no self-consistency assertion |
| `04_run_post_import_audit.py` `EXPECTED_TOTALS` | clean export: `material_return.amount` **1,261,293.80** vs expected 1,225,363.30; `booking.amount` 137,265,708.43 vs 133,672,972.73; `payment.amount` 56,727,355.65 vs 53,923,466.95; `account_transaction.amount` +2,770,942.60 delta; `direct_sale.amount` +301,285.30; `invoice.*` +300,855.30; `pending_bill` +678,065.20; `delivery_rent` +5,795; `waive_off` +0.60; `booking.paid_amount` +560,000 | ❌ **11 of 13 stale** → gate 3 must FAIL. `MIGRATION_AUDIT.md §F.11` explicitly promised "will be refreshed to 1,261,293.80 in the new audit script" — **never done** |
| `AUTO_MIGRATION_REPORT.md:105`: "**29,270 rows deleted**, 29,266 inserted, automatic backup created first" | `import_report.json`: `rows_deleted_total: **0**`; `backup_path` → file **not in repo** (gitignored) | ❌ number wrong; and the referenced safety net cannot be verified or restored from the repo |
| `MIGRATION_AUDIT.md §J`: "`instance/import_reports/` (already removed)" | still present with `full_raw_import_report_20260817_140353_480002.csv` | ⚠️ stale |
| `MIGRATION_AUDIT.md §N`: 5 file actions (2 CREATE, 3 EDIT) | `06_opening_state_migration.py` ✗ absent · `_opening_state_common.py` ✗ absent · `05` not stubbed · `post_import_enrichment.sql` not deprecated · `04` not rewritten | ❌ 0 of 5 done |
| `DATA_LOAD_VERIFICATION_REPORT.md` §3–§7 (counts, +4 app rows, the 4 duplicate entry ids 9084/9830/10116/10117, 0 dups in any unique index) | measured identical | ✅ **the most accurate document in the repo** |
| `MIGRATION_REPORT.md` §1–§8 (WAL trap, superset schema, user merge table, relaxed index, 0 orphans) | measured identical | ✅ |
| `migrate tool/README.md` "no data lost, no rows dropped, no duplicates created", "≈1 second", "never modified", "REVIEW lists what's left behind" | 0.74 s ✅ · md5 unchanged ✅ · no loss ✅ · **"nothing is hidden" ❌ (D-1)** · "only when all checks are clean" ⚠️ (D-2) | mixed |
| `migrate tool/README.md` "output file is suggested automatically (`output/…`)" | `default_out_path()` → NEW's folder | ❌ (D-5) |
| `tools/migrate/README.md` "pure pandas + stdlib, runs on any machine that can open the xlsx" | pandas/openpyxl absent; no requirements file pinned | ⚠️ (D-10) |

---

## 7. Security & repo hygiene (carried over from the audits, still open)

| Finding | Evidence | Status |
|---|---|---|
| **`secret_key.txt` committed, identical in 3 tracked paths**, and `MIGRATION_REPORT.md §11` itself lists "rotate + purge from history" as an **open TODO** | `AMSCOPY9/instance/secret_key`, `data lab…/secret_key.txt`, `data lab…/NewData/secret_key.txt` — all three md5 `c80b7cb0…` | ❌ never actioned |
| Full production business data committed (client names, balances, all ledgers) in 5 `.db`/`.amsdb` files + 2 xlsx ≈ **30 MB** | `git ls-files` sizes | ❌ if the repo is public this is a disclosure, independent of the secret key |
| Hard-coded deploy webhook token `PakistanZindabad1947-2026` | now appears **only in the audit `.md` files**, not in code | ✅ removed from code (residual exposure in docs history) |
| `password_plain` | empty in OLD / MIGRATED / FINAL / snapshot | ✅ no plaintext passwords travelled |
| `uq_entry_auto_bill_no` permanently absent rather than scheduled for restore | `app/migrations/` is empty | ⚠️ duplicate bill numbers can still be committed; needs the promised `0001_*.sql` after cleaning 2 rows |
| Reading a WAL-mode source leaves `-wal`/`-shm` next to the user's original file | observed twice during this audit (main file byte-identical each time; I removed the sidecars) | ⚠️ cosmetic, but breaks on read-only mounts |

---

## 8. Recommended next actions (priority order)

1. **P0 — fix D-1** (`migrate_engine.py:600-616`) and re-add a "left-behind tables" gate; add the two regression tests (old-only *empty* table, old-only table *with* data). Without it the tool cannot be offered to anyone whose legacy file isn't exactly this schema.
2. **P0 — fix D-3** (write the report in `finally`, mark incomplete output un-importable, correct the GUI's "no output was produced" message). Then have `full_db_sync import` **refuse any `*.db` lacking a `RESULT: PASS` sidecar**, or require `.amsdb` snapshots (which are self-describing).
3. **P1 — fix D-4**: re-create *all* indexes; verify index parity; then ship `app/migrations/0001_restore_relaxed_unique_indexes.sql` to restore `uq_cash_flow_entry_idempotency_key` and, after cleaning `SB-GRN-1024`/`SB-GRN-1042`, `uq_entry_auto_bill_no`.
4. **P1 — fix D-2**: make `KEEP_FROM_NEW` tables with old data a `REVIEW` reason.
5. **P1 — decide the void/cancel policy** (§3.5): 44 voided + 25 cancelled rows are live in production today because the SQLite path has no purge step. Either state "full history, including voided, is intended" in `docs/FULL_DB_SQLITE_SYNC.md`, or port `_migrate_common.py`'s purge contract to a `--purge-voided` flag on the migrate tool.
6. **P2 — resolve the doc contradictions** (§6): correct `tools/migrate/README.md` to the 17-08 numbers, refresh or delete `EXPECTED_TOTALS`, fix the `29,270 deleted` line, and either implement `06_opening_state_migration.py` or mark `MIGRATION_AUDIT.md` as **proposal, not record** so nobody re-runs a plan that was never built.
7. **P2 — make the audit tools usable** (D-8/D-9): `--db` + read-only open + non-zero exit on crash.
8. **P2 — security close-out**: rotate `secret_key`, add `*secret_key*` + `*.db`/`*.amsdb` to `.gitignore`, and keep the two `backups/pre_migration_*` DBs out of git (they're already a 10 MB duplicate of the sources).
9. **P3 — add a `values` parity check** to the migrate tool (e.g. per-table `sum(abs(hash(row)))` vs source) so `RESULT: PASS` means *values* match, not just *counts*. Today a column mis-mapping with the same cardinality would pass every gate.
10. **P3 — CI**: the repo has `tests/test_full_db_snapshot.py` and `test_auto_migrations.py`; add an end-to-end `migrate tool` test on the data-lab fixture pair (runs in <1 s, stdlib only — no pandas/Flask needed, so it is CI-cheap).

---

## 9. How to re-run this audit yourself

```bash
# Stage 1 — the tool, headless, on copies (source files untouched, exit 0 = PASS)
python3 "migrate tool/migrate_tool.py" --cli \
  --old "data lab for migration old to new/ahmed_cement.db" \
  --new "data lab for migration old to new/NewData/ahmed_cement_v44_fresh.db" \
  --out /tmp/out.db                    # → RESULT: PASS in ~0.75 s, 29,266 rows

# Independent cell-level parity (no deps, ~1 s)  → see §3.1/§3.2
python3 /tmp/verify_parity.py           # script used for this report

# Snapshot + import verification (stdlib only)
cd AMSCOPY9
python3 -m full_db_sync verify --db "../data lab for migration old to new/AMSCOPY9_FULL_REFRESH_2026-09-09/AMS_FULL_20260909-133924.amsdb"
APP_DB_PATH="../data lab for migration old to new/FIRST CLASS DATA/ahmed_cement_migrated.db" \
    python3 tools/consistency_report.py          # → ⚠ WARN 4 business issues (§3.5)
python3 tools/health/preflight_check.py --db <DB> --json

# Reproduce D-1 in 5 seconds
python3 -c "
import sqlite3,shutil,sys; sys.path.insert(0,'migrate tool'); import migrate_engine as E
shutil.copy('data lab for migration old to new/ahmed_cement.db','/tmp/x.db')
c=sqlite3.connect('/tmp/x.db'); c.execute('CREATE TABLE django_migrations(a)'); c.commit()
E.run_migration('/tmp/x.db','data lab for migration old to new/NewData/ahmed_cement_v44_fresh.db',out_path='/tmp/xo.db')"
# → sqlite3.OperationalError: no such table: django_migrations   (expected: RESULT: REVIEW + left-behind list)
```

**Environment note:** this sandbox has Python 3.11 / SQLite 3.40 and stdlib only — `pandas`, `openpyxl`, `flask`, `pytest` and `tkinter` are **not installed**, so Stage-1/Stage-2/`verify`/`consistency_report`/`preflight` were executed for real, while `tools/migrate/01–05`, the GUI and the Flask test suite were reviewed by reading + logic analysis and are marked accordingly. No repo file was modified by this audit (git status clean); the only stray artifacts created by the audited tools (`instance/ahmed_cement.db`, WAL sidecars) were removed.
