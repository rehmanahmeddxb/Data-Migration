-- 0001_restore_entry_auto_bill_unique_index.sql
--
-- Restores the partial UNIQUE index on entry.auto_bill_no that the old-data
-- migration relaxes while loading, because the 2026 legacy data carries two
-- duplicate GRN bill numbers.  Nothing is deleted to make room for it:
--
--     entry ids  9084 / 10116  ->  SB-GRN-1024   (9084 is_void = 1)
--     entry ids  9830 / 10117  ->  SB-GRN-1042   (9830 is_void = 1)
--
-- In each pair one row is a VOIDED row, so the migration's void policy removes
-- it and the index becomes satisfiable again.  Since 2026-09-10 the migration
-- tool re-creates the index itself once the purge has run (its report says
-- "INDEX RESTORED"), which is why this file normally finds the index already
-- present and simply records itself in migration_history.
--
-- It remains the safety net for the two cases that still arrive with live
-- duplicates -- a --keep-voided bit-for-bit archive, or legacy data loaded by
-- some other route.  Then:
--   * this file fails at CREATE UNIQUE INDEX, is logged, and is retried at the
--     next boot (documented auto_migrate behaviour -- boot is never blocked);
--   * the boot helper _ensure_auto_bill_unique_indexes() already skips the
--     index with a logged warning while duplicates exist, so the app stays
--     consistent either way;
--   * clearing such a duplicate is a business decision (keep one row per bill
--     number, delete the other, e.g. via tools/repair_controlled/ after a
--     backup + --confirm) -- never done by this migration.
--
-- The same index already exists in the v4.4 template; it is the only index the
-- migration tool relaxes (see the migration report, RELAXED list).
--
-- Re-run safety: IF NOT EXISTS makes the DDL idempotent, so this file is safe
-- to run on a database where the index is already in force.

CREATE UNIQUE INDEX IF NOT EXISTS uq_entry_auto_bill_no
    ON entry(auto_bill_no)
    WHERE auto_bill_no IS NOT NULL AND TRIM(auto_bill_no) <> '';
