-- 0001_restore_entry_auto_bill_unique_index.sql
--
-- Restores the partial UNIQUE index on entry.auto_bill_no that was RELAXED
-- during the old-data migration because the 2026 legacy data carries two
-- duplicate GRN bill numbers (all four rows were kept, none dropped):
--
--     entry ids  9084 / 10116  ->  SB-GRN-1024
--     entry ids  9830 / 10117  ->  SB-GRN-1042
--
-- The same index already exists in the v4.4 template; it is the only index
-- the migration tool relaxes (see the migration report, RELAXED list).
--
-- BEFORE THIS FILE CAN APPLY, the two duplicate bill numbers must be
-- resolved by the business (keep one row per bill number, delete the other,
-- e.g. via tools/repair_controlled/ after backup + --confirm).  Until then:
--   * this file fails at CREATE UNIQUE INDEX, is logged, and is retried at
--     the next boot (documented auto_migrate behaviour — boot is never
--     blocked);
--   * the boot helper _ensure_auto_bill_unique_indexes() already skips the
--     index with a logged warning while duplicates exist, so the app stays
--     consistent either way.
--
-- Re-run safety: IF NOT EXISTS makes the DDL idempotent.
CREATE UNIQUE INDEX IF NOT EXISTS uq_entry_auto_bill_no
    ON entry(auto_bill_no)
    WHERE auto_bill_no IS NOT NULL AND TRIM(auto_bill_no) <> '';
