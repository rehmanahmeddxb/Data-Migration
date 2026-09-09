# FUTURE DATA-MIGRATION PLAN — AMS / AMSCOPY9 (ahmed_cement)

**Date:** 2026-09-09
**Applies to:** `AMSCOPY9/` (the app) + `data lab for migration old to new/` (the data lab)
**Status:** ✅ Approved to follow from the 2026-09-09 cutover onward
**Decision that started this plan:** *"Import/export by xlsx file is not good" — stop using Excel
workbooks as the data-migration channel.*

---

## 0. TL;DR — the rules from today forward

1. **Migrations & restores are done at the SQLite-database level, never via xlsx.**
   Same-schema DB-to-DB copy (`sqlite3 ATTACH`), IDs preserved, constraints re-proven.
2. **Every load starts with a FULL CLEAN of the target app database** (backup first, always).
   `load_into_amscopy9.py` encodes this: GATE → BACKUP → CLEAN → LOAD → VERIFY → DEPLOY.
3. **xlsx stays only for human eyes** (reports, templates, small master lists).
   It is never again the system-of-record path into the app DB.
4. **Every data event ends with the same three gates:**
   `integrity_check = ok`, per-table counts == source, 0 orphan FKs
   (+ the app's own `tools/consistency_report.py` with only *known* warnings).
5. **The deployable artifact is a single file:** `FINAL DEPLOY/ahmed_cement_v44_fresh.db`
   (+ `.sha256`). Verify the checksum before every cutover.

---

## 1. Why xlsx import/export is being retired as a migration channel

Evidence collected in this repo, 2026-08/09:

| Problem | Proof in the repo |
|---|---|
| Whole purge contract lives in spreadsheet code, re-implemented twice | `tools/migrate/_migrate_common.py` CASCADE_RULES and `tools/migrate/02_build_clean_export.py`; 11,663 of 35,717 rows (≈33%) were *removed by a script*, not by the business |
| Excel is lossy for money/ids | floats → text/rounding, big-number mangling, date formats; original primary keys not trustworthy after a workbook round-trip |
| Repeat imports multiply rows | `instance/import_reports/` shows multiple full-raw import runs (2026-08-17, 08-27 ×5 …) — each xlsx re-import is a new chance for duplication |
| No integrity on spreadsheets | no FK checks, no uniqueness, no `integrity_check`; verification has to be rebuilt after every export |
| Two export formats drifted | app "full raw export" vs legacy ALLEXPORT tooling produced different sheets/columns (51 sheets) |
| Not idempotent by construction | importing twice ≠ importing once; needs custom dedupe on every run |

Conclusion: an xlsx file is a *presentation* format. Using it as the transfer format between two
SQLite databases inserts a lossy, unverifiable middle-man for zero benefit — both sides are the
same v44 schema, so the copy can be done by SQL directly with **original IDs, original types,
original constraints**.

---

## 2. The three channels (and when to use each)

| Channel | Tool / artifact | Used for | Never for |
|---|---|---|---|
| **A. DB-level migration / cutover** | `FIRST CLASS DATA/migrate.py` (old schema/history → v44) + `load_into_amscopy9.py` (clean + copy into the app DB) | Bringing real data into a fresh app DB; moving between same-schema DBs; restoring a snapshot | — |
| **B. Backups & restores** | SQLite online copy / `VACUUM INTO` / app's own backup infra (`app/services/backup.py`, `instance/storage/backups`) | Daily/weekly protection, rollback, restore drills | — |
| **C. Human exports** | app Import & Export UI → xlsx/csv/pdf **read-only** downloads; small master templates (e.g. price lists) | Reading data in Excel, sharing numbers, tiny controlled uploads of master data | Whole-tenant moves, migrations, restores |

### Channel A is now two stage-one tools (already proven on real data)

```
STAGE 1 (done 2026-09-09, verified PASS)
  ahmed_cement.db (OLD history) ──> FIRST CLASS DATA/migrate.py
      schema base = NewData/ahmed_cement_v44_fresh.db (v44)
      ──> FIRST CLASS DATA/ahmed_cement_migrated.db     29,266 rows, users merged 1–9

STAGE 2 (done 2026-09-09, verified PASS)
  FIRST CLASS DATA/ahmed_cement_migrated.db ──> data lab …/load_into_amscopy9.py
      GATE → BACKUP → CLEAN (wipes ALL rows of the app DB) → LOAD (db→db, original ids)
      → VERIFY (integrity / counts / FK / user-FK) → DEPLOY copy
      ──> FINAL DEPLOY/ahmed_cement_v44_fresh.db      (== source row-for-row)
           ├── .sha256
           └── load_report.txt                        (full per-table proof)
```

---

## 3. Standard "clean → load" procedure (the new default for any fresh-data event)

For **any** future "take fresh data fully into the app" request, do exactly this:

1. **Produce the source DB first** (stage 1, `migrate.py`), never skip to loading a raw workbook.
2. **Run stage 2** (`load_into_amscopy9.py --confirm`). The tool itself:
   - refuses to run without `--confirm`;
   - refuses when source == target or source fails `integrity_check`;
   - copies the current app DB into `backups/appdb_backup_<ts>/` (+ sha256) *before touching it*;
   - wipes **every row of every table** of the app DB (schema untouched, then `VACUUM`);
   - loads all rows from the source with original IDs; unique indexes the data cannot satisfy
     are relaxed and **listed in the report** (same rule as stage 1);
   - verifies: `integrity_check`, per-table count == source, declared-FK orphans = 0,
     logical user-FK orphans = 0;
   - on any failure **restores the pre-run backup automatically**;
   - refreshes `FINAL DEPLOY/ahmed_cement_v44_fresh.db` + `.sha256` + `load_report.txt`.
3. **Gate:** report must print `RESULT: PASS`.
4. **Smoke test on the running app** (login, clients page, a sale, cash flow, a report).
   The loaded DB self-heals only its two standard seeds on first boot:
   default admin (if missing) and the "Open Khata" client (+1 client row, by design).
5. **Cutover on the live server:** stop app → backup live DB → replace file → verify checksum
   → start app → run `tools/consistency_report.py`.

### Checklist (print and tick)

- [ ] `FIRST CLASS DATA/migration_report.txt` → RESULT PASS
- [ ] `FINAL DEPLOY/load_report.txt` → RESULT PASS (fresh, same timestamp as the .db)
- [ ] `sha256sum -c FINAL DEPLOY/ahmed_cement_v44_fresh.db.sha256` → OK
- [ ] Live DB backed up (keep pre-cutover file + sha in `backups/`)
- [ ] App started, `/health` → `"database": "ok"`
- [ ] Login + spot-check: clients, sales, bookings, pending bills, account balances
- [ ] `tools/consistency_report.py` shows only *known* warnings (see §6)

---

## 4. Backups & restore (protection between migrations)

**Immediate (this week):**
- [ ] Daily automated copy of the live DB: `sqlite3 live.db ".backup 'backup_<date>.db'"` (safe while
      app is running; the app also has its own backup service — wire one of them into a cron).
- [ ] Keep **7 daily + 4 weekly** snapshots, each with `.sha256`; prune older.
- [ ] Never keep the only copy of anything on the same disk as the app DB.

**Monthly:**
- [ ] Restore drill: boot a scratch AMSCOPY9 checkout against last week's backup and run the
      stage-2 VERIFY gates + `consistency_report.py`. A backup that has never been restored is a hope, not a backup.

**Rollback procedure (after any bad cutover):**
1. Stop the app.
2. Restore the pre-cutover DB file from `backups/` (or from the app's own backup dir).
3. Start the app; `/health` green; confirm counts match the pre-cutover `load_report`/health snapshot.

---

## 5. Cutover runbook — live server (PythonAnywhere), 2026-09-09 state

Target paths (from `config.py` + health snapshot):

| Thing | Where |
|---|---|
| Live app DB | `/home/ahmedrehmanahmed1/instance/ahmed_cement_v44_fresh.db` |
| Code | `/home/ahmedrehmanahmed1/AMSCOPY9` (GitHub `rehmanahmedca-source/AMSCOPY9` main) |
| Public URL | `https://ahmedrehmanahmed1.pythonanywhere.com` |

Steps:
1. Download `FINAL DEPLOY/ahmed_cement_v44_fresh.db` from this repo (get it from the workspace
   copy if you cannot pull the branch — content identical).
2. `sha256sum -c ahmed_cement_v44_fresh.db.sha256`.
3. Stop the web app in PythonAnywhere (or set maintenance).
4. Back up the live DB to `~/instance/backups/pre_cutover_2026-09-09.db` (+ sha) — do not delete.
5. Replace the live DB file with the downloaded one.
6. Start the app. First boot auto-adds its standard seeds only (see §3 step 4).
7. Check `/health` → `"database": "ok"`, log in, spot-check numbers against `load_report.txt`.
8. Run `python tools/consistency_report.py` on the server and compare warnings to §6 baseline.

> If anything looks wrong: repeat rollback steps in §4. The old file is untouched in step 4.

---

## 6. Known warnings carried by the data (baseline — decide once)

The app's own consistency report flags three classes, **identical in the old DB, the migrated DB
and the loaded app DB** (i.e. the migration added/removed nothing):

| Warning | Count | Meaning |
|---|---|---|
| Sales missing entries | 4 | legacy sales without derived entry rows |
| Orphaned invoices | 87 | invoices whose sale reference is gone/voided in history |
| Bookings missing pending bills | 100 | legacy bookings without derived pending-bill rows |

**Options (pick one, record the decision in this file):**
- **A. Keep as history (recommended for now).** Numbers are consistent with every previous audit
  in this repo (`ORPHAN_SCENARIO_AUDIT.md`, `QA_FULL_AUDIT.md`). No business data is lost; these
  are *derived* rows that legacy history never generated.
- **B. Repair once with the app's own controlled tools** (`tools/repair_controlled/repair_erp_consistency.py --confirm`
  and friends — they back up first and audit every change) on a **staging copy**, verify the
  derived ledgers, then repeat on live. Do this only after the cutover settles.

Rule for the future: every consistency report is compared against this baseline; **new** warnings
are investigated the same day, known ones only on the cadence below.

---

## 7. Roadmap

### P0 — Hygiene (do now, low effort)
- [ ] Remove `secret_key.txt` files from the repo/git history + rotate key (open item from
      `MIGRATION_REPORT.md` §11, still open).
- [ ] Add `.gitignore` entries / move large `.db` artifacts out of git if the repo should stay
      light (the data-lab DBs are committed today by convention; decide consciously).
- [ ] Back up the live DB now (before any cutover) and store off-server.

### P1 — Make the pipeline the default (this plan is already the tooling; finish the edges)
- [ ] Run one scheduled auto-backup of the live DB + one restore drill (see §4).
- [ ] Record the §6 baseline decision (A or B) here after the live cutover.
- [ ] Add a `CHAIN` manifest to each stage-2 run: source sha256 → load report → deployed sha256,
      so any DB can be traced to its parent (one file, committed next to `load_report.txt`).

### P2 — App-level changes (AMSCOPY9 code — go through the app's own repo/deploy flow)
- [ ] In the Import & Export UI: relabel full-raw xlsx import as **"restore only from a verified
      snapshot"** (or remove it from normal use), so a human can no longer accidentally wipe/replace
      tenant data with a spreadsheet.
- [ ] Add a **maintenance "replace DB" route** that only accepts a *verified* stage-2 artifact
      (checksum + PASS report) — or document that the file-swap in §5 is the sanctioned path.
- [ ] Export page: keep xlsx/csv/PDF export but mark it *reporting only*.
- [ ] Make `tools/consistency_report.py` output machine-readable baseline diffs (exit code != 0 on
      new warnings) so a post-cutover CI check can be wired to it.
- [ ] Evaluate: move to PostgreSQL (concurrency, online backups, real FK enforcement). SQLite
      remains fine for single-tenant; revisit when multi-user writes are needed. (Existing reports
      recommend this; not a prerequisite for any of the above.)

### P3 — Periodic (every cutover or quarterly)
- [ ] Re-run stage-1 `migrate.py` + stage-2 load in a scratch folder to prove reproducibility with
      today's code (idempotence check), then discard the scratch files.
- [ ] Restore drill from the oldest retained backup.
- [ ] Review the purge/relax rules: any unique index that got relaxed (`RELAXED=` lines in the
      reports) is a data-quality debt — fix the data, don't keep the relaxation.

---

## 8. Definition of done for any future data event

A migration/restore is **done** only when all of these hold:

1. Source was produced by a script in this repo (no hand-edited DB, no xlsx as source).
2. Stage-2 report exists for this exact run and prints `RESULT: PASS`.
3. Deployed artifact sha256 matches the committed `.sha256`.
4. App boots against the artifact; `/health` reports `"database": "ok"`.
5. `consistency_report.py` shows no **new** warnings vs the §6 baseline.
6. The pre-event backup is retained and its location is written in the report/README.
7. The whole event is described in one short note in this repo (like `MIGRATION_REPORT.md` style),
   so a future session can answer "what did we do and why" in 2 minutes.
