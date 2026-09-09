# Data Load Verification Report — old `ahmed_cement.db` → v4.4 new database

> **Date:** 2026-09-09 (verification run read-only against the actual files — nothing was modified)
> **Checked by:** Arena coding session, branch `arena/01a0871c-data-migration`

---

## 1. Files verified

| Role | File |
|---|---|
| OLD (source) | `data lab for migration old to new/ahmed_cement.db` |
| MIGRATED (migration output) | `data lab for migration old to new/FIRST CLASS DATA/ahmed_cement_migrated.db` |
| FINAL (new DB, as the app runs it) | `data lab for migration old to new/AMSCOPY9_FULL_REFRESH_2026-09-09/ahmed_cement_v44_fresh.db` |
| SNAPSHOT (export artifact) | `data lab for migration old to new/AMSCOPY9_FULL_REFRESH_2026-09-09/AMS_FULL_20260909-133924.amsdb` |

---

## 2. VERDICT — summary

> ✅ **ALL data loaded — zero rows lost.**
> ✅ **Duplicates managed — all duplicate rows kept, none dropped, every other unique rule enforced.**

---

## 3. Integrity & health checks (all 3 databases)

| Check | OLD | MIGRATED | FINAL |
|---|---|---|---|
| `PRAGMA integrity_check` | ok | ok | ok |
| `PRAGMA foreign_key_check` violations | 0 | 0 | 0 |
| Total rows | 29,263 | 29,266 | 29,270 |
| Tables | 64 | 69 | 69 |

The FINAL database is a superset of OLD **only** by design (see §5): +5 new v4.4 tables (all empty except the migration-run record) and the user merge.

## 4. Per-table row counts — OLD → MIGRATED → FINAL

**58 of 64 old tables carry over with identical counts, and all old business tables are identical between MIGRATED and FINAL.** The only tables where counts differ are the expected, intentional ones:

| Table | OLD | MIGRATED | FINAL | Explanation |
|---|---|---|---|---|
| `user` | 7 | 9 | 9 | Intentional merge: 7 old users + 2 fresh admin users kept (see §6) |
| `migration_run` | — | 1 | 1 | v4.4 template carries 1 completed `CLIENTS` migration-run history record (present in `NewData` template) |
| `client` | 323 | 323 | 324 | +1 = `OPEN-KHATA` (id 324) auto-seeded by the app at first boot after refresh (`ensure_open_khata_client`) |
| `user_login_session` | 28 | 28 | 29 | +1 = the Admin login session on 2026-09-09 18:40 (after refresh) |
| `audit_log` | 1,630 | 1,630 | 1,632 | +2 = that same login's audit entries (`auth.login`, `http.post.auth.login`) |
| `cash_day_account_position`, `cash_day_lock`, `migration_mapping`, `migration_row` | — | 0 | 0 | New v4.4 tables — legitimately empty |
| all other **58** old tables | = | = | = | **identical counts OLD→MIGRATED→FINAL** |

**Row-level proof (id sets, not just counts):**
- Every `id` from every old table exists in FINAL — checked **58 tables with integer `id` columns → 0 missing rows**.
- MIGRATED vs FINAL id sets identical on **65/65** id-bearing shared tables; the only additions in FINAL are the 4 explained rows above (client 324, session 29, 2 audit entries — all created by the app itself after the refresh, not by the migration).
- FINAL vs `.amsdb` snapshot: identical tables; snapshot carries the same migrated data (the 4 extra app rows happened after the snapshot export time 13:39 — same-day Admin login at 18:40).

## 5. "Missed tables" question

- **Every one of the 64 old tables exists in the new DB** — none dropped, none renamed out of existence.
- 5 new tables exist only in the new schema (migration audit + cash-day infra) — they are empty by design, which is correct.

## 6. Users (merge result — intended)

| OLD user | In FINAL as |
|---|---|
| Admin | `Admin_legacy` (id 3) — renamed because v4.4 keeps its own fresh `Admin` (id 1) with the known password |
| Rehman Ahmed | id 4 ✔ (original id) |
| Rizwan Ahmed | id 5 ✔ |
| Adnan Ahmed | `Adnan Ahmed_legacy` (id 6) — renamed because the fresh DB already has `Adnan Ahmed` (id 2) |
| Shujaat Muzaffar | id 7 ✔ |
| Ahmed Hassan | id 8 ✔ |
| Mohsan Javed | id 9 ✔ |

All 7 old users present; all FK references (`audit_log`, `accounting_audit_log`, `user_login_session`) remapped and re-checked (0 orphans — checked by the migrator and by `PRAGMA foreign_key_check`).

## 7. Duplicates — fully managed

| Check | Result |
|---|---|
| Duplicate values in **any UNIQUE index** of OLD | none |
| Duplicate values in **any UNIQUE index** of MIGRATED | none |
| Duplicate values in **any UNIQUE index** of FINAL | none |
| Known case: `entry.auto_bill_no` duplicates (`SB-GRN-1024` ×2, `SB-GRN-1042` ×2) | present in OLD **and** FINAL with the **same 4 row ids** — `9084, 9830, 10116, 10117` — **all kept, none dropped** |
| `uq_entry_auto_bill_no` index | Present in the v4.4 template; **relaxed (absent)** in MIGRATED and FINAL — the documented compromise so the 4 duplicate rows could be kept. All other unique indexes (e.g. `uq_payment_idempotency_key`, `uq_grn_manual_bill_no`, …) exist and hold. |

**Meaning:** the only duplicate values in the whole data set are those 4 `entry` rows, they were **kept, not merged or deleted**, and only the one unique index that would have rejected them was relaxed. Restoring that index is a follow-up that requires cleaning those 2 bill numbers first (documented in `AUTO_DATABASE_MIGRATIONS.md` §5).

## 8. How this state is reproduced anywhere (if the app instance ever needs reloading)

The FINAL file is the exact database the refresh PR (#5) produced; the `.amsdb` snapshot next to it is the portable export. To reproduce on any machine:

```bash
cd /home/user/Data-Migration/AMSCOPY9
python3 -m full_db_sync import \
  --source "/home/user/Data-Migration/data lab for migration old to new/AMSCOPY9_FULL_REFRESH_2026-09-09/AMS_FULL_20260909-133924.amsdb" \
  --db <APP INSTANCE DB PATH> --backup-dir instance/migration --json import_report.json --confirm
```

Expected result (verified when this was last executed): `verification: PASS`, 69/69 tables, 0 FK violations, exact parity.

---

## 9. Bottom line

1. **All data loaded** — every old row (by id) exists in the new v4.4 database; no table, no row, no user lost.
2. **Duplicates managed** — the 2 duplicate bill numbers (4 rows) are all present, untouched; every other uniqueness rule is enforced; the only relaxed index is documented and trackable.
3. The small FINAL-vs-source differences (+4 rows) are the app's own post-refresh activity (auto OPEN-KHATA seed, one Admin login + audit), not data changes.
