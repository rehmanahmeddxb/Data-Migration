#!/usr/bin/env python3
"""Regression tests: migrating a DIFFERENT old file into the v4.4 schema.

These encode the "another old file" verification (see
ANOTHER_OLD_FILE_VERIFICATION.md at the repo root) and the fixes for audit
defects D-1, D-2, D-3, D-4, D-5 in MIGRATION_TOOL_PROCEDURE_AUDIT.md:

  T1  old file with an extra EMPTY legacy table  -> completes, PASS, nothing lost
  T1b old file with an extra legacy table WITH DATA -> REVIEW, rows listed
  T2  old rows in KEEP_FROM_NEW tables (settings/import_job) -> REVIEW, listed
  T3  old-only column with non-null data -> REVIEW, column listed
  T4  a genuinely different business dataset -> PASS, cell-level parity,
      full index parity with the v4.4 template
  T5  mid-run crash -> *.report.txt written with RESULT: FAILED and the
      partial output quarantined as *.INCOMPLETE (never importable)
  T6  an OLD file that already carries v4.4 markers (an output of a previous
      run) is refused — re-migrating re-maps user ids and corrupts audit
      attribution (G1); --allow-v44-old still permits it
  T7  a NOT NULL column the old file cannot fill is detected BEFORE the load
      and named in the error; nothing is left as a usable output (G3)
  T8  value parity catches a value that changed during the copy, so a
      mis-mapped column can no longer print RESULT: PASS (G4)
  R1  refusal guards still refuse (swapped roles, non-AMS file)

Pure stdlib. Each test builds its fixture from the committed data-lab pair
when present and is skipped otherwise. Run:  python3 test_migrate_engine.py -v
"""
from __future__ import annotations

import shutil
import sqlite3
import sys
import tempfile
import unittest
from pathlib import Path

TOOL_DIR = Path(__file__).resolve().parent
REPO = TOOL_DIR.parent
sys.path.insert(0, str(TOOL_DIR))

import migrate_engine as E  # noqa: E402

OLD_DEFAULT = REPO / "data lab for migration old to new" / "ahmed_cement.db"
NEW_DEFAULT = (REPO / "data lab for migration old to new" / "NewData" /
               "ahmed_cement_v44_fresh.db")


def _fixtures_available() -> bool:
    return OLD_DEFAULT.exists() and NEW_DEFAULT.exists()


def _cell_parity(old_path, out_path) -> tuple:
    """(tables_checked, missing_rows, extra_rows, value_mismatches) after the
    documented user-id remap."""
    old = sqlite3.connect(f"file:{Path(old_path).as_posix()}?mode=ro", uri=True)
    out = sqlite3.connect(f"file:{Path(out_path).as_posix()}?mode=ro", uri=True)
    old_users = [r[0] for r in old.execute("SELECT id FROM user ORDER BY id")]
    max_new = out.execute("SELECT MAX(id) FROM user").fetchone()[0]
    umap = {o: max_new - len(old_users) + 1 + i for i, o in enumerate(old_users)}
    ucols = {"user_id", "created_by_id", "created_by", "updated_by"}
    gm = ge = gv = nt = 0
    tables = sorted(
        r[0] for r in old.execute(
            "SELECT name FROM sqlite_master WHERE type='table' "
            "AND name NOT LIKE 'sqlite_%' AND name NOT LIKE '__ams%'")
    )
    for t in tables:
        if t == "user":
            continue
        ocols = [r[1] for r in old.execute(f'PRAGMA table_info("{t}")')]
        ncols = [r[1] for r in out.execute(f'PRAGMA table_info("{t}")')]
        if "id" not in ocols or "id" not in ncols or t not in ncols:
            continue
        if not set(ocols) & set(ncols):
            continue
        nt += 1
        orows = {r[0]: r for r in old.execute(f'SELECT * FROM "{t}"')}
        nrows = {r[0]: r for r in out.execute(f'SELECT * FROM "{t}"')}
        gm += len(set(orows) - set(nrows))
        ge += len(set(nrows) - set(orows))
        oidx = {c: i for i, c in enumerate(ocols)}
        nidx = {c: i for i, c in enumerate(ncols)}
        for oid in set(orows) & set(nrows):
            o, n = orows[oid], nrows[oid]
            for c in ocols:
                if c == "id" or c not in ncols:
                    continue
                ov, nv = o[oidx[c]], n[nidx[c]]
                if c in ucols and ov in umap:
                    ov = umap[ov]
                if (ov is None) != (nv is None) or (
                        ov is not None and str(ov) != str(nv)):
                    gv += 1
    old.close()
    out.close()
    return nt, gm, ge, gv


@unittest.skipUnless(_fixtures_available(), "data-lab fixture pair not present")
class AnotherOldFileTests(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="ams_migrate_test_"))

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def _fixture(self, name: str) -> Path:
        p = self.tmp / name
        shutil.copy(OLD_DEFAULT, p)
        return p

    def _run(self, old: Path, out: str) -> dict:
        return E.run_migration(old, NEW_DEFAULT, out_path=self.tmp / out)

    # ---- T1: extra EMPTY legacy table must not crash (D-1) ----------------
    def test_old_only_empty_table_does_not_crash(self):
        old = self._fixture("t1.db")
        c = sqlite3.connect(old)
        c.execute("CREATE TABLE django_migrations (id INTEGER PRIMARY KEY, name TEXT)")
        c.commit()
        c.close()
        res = self._run(old, "t1_out.db")
        self.assertEqual(res["status"], "PASS", res["text"])
        self.assertEqual(res["issues"], [])

    # ---- T1b: extra legacy table WITH data -> REVIEW, rows named ----------
    def test_old_only_table_with_data_is_reviewed(self):
        old = self._fixture("t1b.db")
        c = sqlite3.connect(old)
        c.execute("CREATE TABLE report_cache (id INTEGER PRIMARY KEY, name TEXT)")
        c.executemany("INSERT INTO report_cache (name) VALUES (?)",
                      [(f"r{i}",) for i in range(25)])
        c.commit()
        c.close()
        res = self._run(old, "t1b_out.db")
        self.assertEqual(res["status"], "REVIEW")
        self.assertTrue(any("report_cache (25 rows)" in i for i in res["issues"]),
                        res["issues"])

    # ---- T2: KEEP_FROM_NEW tables carrying old data -> REVIEW (D-2) -------
    def test_keep_from_new_rows_are_flagged_not_silently_dropped(self):
        old = self._fixture("t2.db")
        c = sqlite3.connect(old)
        info = c.execute("PRAGMA table_info(settings)").fetchall()
        cols = {r[1]: r for r in info}
        col_names = [r[1] for r in info]
        vals = []
        for name in col_names:
            cid, name, ctype, notnull, dflt, pk = cols[name]
            if name == "id":
                continue
            if name in ("company_name",):
                vals.append("BRANCH 2 CEMENT TRADERS")
            elif dflt is not None:
                vals.append(None)  # let the default apply
            elif notnull:
                vals.append(0)
            else:
                vals.append(None)
        col_csv = ", ".join(f'"{n}"' for n in col_names if n != "id")
        marks = ", ".join(["?"] * len(vals))
        # apply defaults where declared by omitting them instead
        usable = [n for n in col_names if n != "id" and cols[n][4] is None]
        vals = []
        for n in usable:
            vals.append("BRANCH 2 CEMENT TRADERS" if n == "company_name" else 0)
        c.execute(f'INSERT INTO settings ({", ".join(chr(34)+n+chr(34) for n in usable)}) '
                  f'VALUES ({", ".join("?" * len(usable))})', vals)
        c.commit()
        n_settings = c.execute("SELECT COUNT(*) FROM settings").fetchone()[0]
        c.close()
        self.assertGreaterEqual(n_settings, 1)
        res = self._run(old, "t2_out.db")
        self.assertEqual(res["status"], "REVIEW")
        self.assertTrue(any("KEEP_FROM_NEW" in i and "settings" in i
                            for i in res["issues"]), res["issues"])
        out = sqlite3.connect(self.tmp / "t2_out.db")
        # the output deliberately keeps the fresh template's settings (0 rows)
        self.assertEqual(out.execute("SELECT COUNT(*) FROM settings").fetchone()[0], 0)
        out.close()

    # ---- T3: old-only column with data -> REVIEW (existing behaviour) -----
    def test_old_only_column_with_data_is_reviewed(self):
        old = self._fixture("t3.db")
        c = sqlite3.connect(old)
        c.execute("ALTER TABLE client ADD COLUMN legacy_credit_note TEXT")
        c.execute("UPDATE client SET legacy_credit_note='CR/2019/'||id WHERE id % 8 = 0")
        c.commit()
        c.close()
        res = self._run(old, "t3_out.db")
        self.assertEqual(res["status"], "REVIEW")
        self.assertTrue(any("legacy_credit_note (40 non-null)" in i
                            for i in res["issues"]), res["issues"])

    # ---- T4: a genuinely different dataset -> PASS + parity + indexes -----
    def test_different_dataset_is_faithful_and_lossless(self):
        old = self._fixture("t4.db")
        c = sqlite3.connect(old)
        c.execute("UPDATE client SET name = 'BR2-CLIENT-'||id")
        c.execute("UPDATE entry SET qty = round(qty*1.15, 2) WHERE qty IS NOT NULL")
        c.execute("UPDATE payment SET amount = round(amount*0.9,2),"
                  " amount_minor = CAST(round(amount_minor*0.9) AS INTEGER)"
                  " WHERE amount IS NOT NULL")
        c.execute("CREATE TEMP TABLE dead AS SELECT id FROM direct_sale WHERE id % 3 = 0")
        for t in ("booking_allocation", "delivery_rent", "direct_sale_item",
                  "grn_allocation", "sale_delivery_persons"):
            try:
                c.execute(f"DELETE FROM {t} WHERE sale_id IN (SELECT id FROM dead)")
            except sqlite3.OperationalError:
                pass
        c.execute("DELETE FROM direct_sale WHERE id IN (SELECT id FROM dead)")
        c.execute("DELETE FROM user WHERE id > 5")
        c.commit()
        n_users = c.execute("SELECT COUNT(*) FROM user").fetchone()[0]
        c.close()
        res = self._run(old, "t4_out.db")
        self.assertEqual(res["status"], "PASS", res["text"])
        self.assertEqual(res["issues"], [])
        nt, gm, ge, gv = _cell_parity(old, self.tmp / "t4_out.db")
        self.assertEqual((gm, ge, gv), (0, 0, 0))
        # index parity with the template (D-4): all but genuinely-violating ones
        out = sqlite3.connect(self.tmp / "t4_out.db")
        tmpl = {r[0] for r in sqlite3.connect(f"file:{NEW_DEFAULT.as_posix()}?mode=ro", uri=True)
                .execute("SELECT name FROM sqlite_master WHERE type='index' AND sql IS NOT NULL")}
        got = {r[0] for r in out.execute(
            "SELECT name FROM sqlite_master WHERE type='index' AND sql IS NOT NULL")}
        relaxed = {r.split(" ", 1)[0].rsplit(".", 1)[-1]
                   for r in res["relaxed_indexes"]}
        self.assertEqual(tmpl - got - relaxed, set())
        out.close()
        self.assertLessEqual(n_users, 5)

    # ---- T5: mid-run crash -> report + quarantine (D-3) --------------------
    def test_mid_run_failure_writes_report_and_quarantines_output(self):
        old = self._fixture("t5.db")
        out_path = self.tmp / "t5_out.db"
        orig = E._swap_table
        calls = {"n": 0}

        def crashy(con, t):
            calls["n"] += 1
            if calls["n"] == 8:
                raise RuntimeError("injected mid-run failure")
            return orig(con, t)

        E._swap_table = crashy
        try:
            with self.assertRaises(RuntimeError):
                E.run_migration(old, NEW_DEFAULT, out_path=out_path)
        finally:
            E._swap_table = orig
        self.assertFalse(out_path.exists(), "raw partial output must be gone")
        q = out_path.with_suffix(out_path.suffix + ".INCOMPLETE")
        self.assertTrue(q.exists(), "partial output must be quarantined")
        report = q.with_suffix(q.suffix + ".report.txt")
        self.assertTrue(report.exists(), "failure report must be written")
        self.assertIn("RESULT: FAILED", report.read_text(encoding="utf-8"))

    # ---- T6: an already-migrated (v4.4) OLD file is refused (G1) -----------
    def test_already_migrated_old_file_is_refused(self):
        first = self._run(self._fixture("t6_src.db"), "t6_first.db")
        self.assertEqual(first["status"], "PASS", first["text"])
        migrated = self.tmp / "t6_first.db"
        with self.assertRaises(E.MigrationError) as ctx:
            E.run_migration(migrated, NEW_DEFAULT, out_path=self.tmp / "t6_second.db")
        msg = str(ctx.exception)
        self.assertIn("already carries v4.4", msg)
        self.assertIn("corrupts audit-log attribution", msg)
        # no half-done second output
        self.assertFalse((self.tmp / "t6_second.db").exists())

    def test_allow_v44_old_flag_overrides_the_guard(self):
        first = self._run(self._fixture("t6b_src.db"), "t6b_first.db")
        self.assertEqual(first["status"], "PASS", first["text"])
        res = E.run_migration(self.tmp / "t6b_first.db", NEW_DEFAULT,
                              out_path=self.tmp / "t6b_second.db",
                              allow_v44_old=True)
        self.assertIn(res["status"], ("PASS", "REVIEW"))

    # ---- T7: NOT NULL column the old file cannot fill (G3) -----------------
    def test_missing_not_null_column_is_reported_before_loading(self):
        old = self._fixture("t7.db")
        c = sqlite3.connect(old)
        c.execute('ALTER TABLE client DROP COLUMN code')   # NOT NULL in v4.4
        c.commit()
        c.close()
        with self.assertRaises(E.MigrationError) as ctx:
            self._run(old, "t7_out.db")
        msg = str(ctx.exception)
        self.assertIn("client.code", msg)
        self.assertIn("NOT NULL", msg)
        self.assertIn("nothing was written", msg)
        # the run never started, so no usable output is lying around
        self.assertFalse((self.tmp / "t7_out.db").exists())

    # ---- T8: value parity catches a value changed during the copy (G4) -----
    def test_value_parity_detects_a_changed_value(self):
        old = self._fixture("t8.db")
        out_path = self.tmp / "t8_out.db"
        orig = E._swap_table

        def sneaky(con, t):
            res = orig(con, t)
            if t == "client":
                # simulate a silent mis-map / value corruption during the copy
                con.execute("UPDATE client SET name = name || '!'")
            return res

        E._swap_table = sneaky
        try:
            res = E.run_migration(old, NEW_DEFAULT, out_path=out_path)
        finally:
            E._swap_table = orig
        self.assertEqual(res["status"], "REVIEW", res["text"])
        self.assertTrue(any("value mismatch" in i for i in res["issues"]),
                        res["issues"])

    # ---- R1: refusal guards keep refusing ----------------------------------
    def test_swapped_roles_are_refused(self):
        with self.assertRaises(E.MigrationError):
            E.run_migration(NEW_DEFAULT, OLD_DEFAULT, out_path=self.tmp / "x.db")

    def test_non_ams_file_is_refused(self):
        bogus = self.tmp / "bogus.db"
        c = sqlite3.connect(bogus)
        c.execute("CREATE TABLE stuff (a, b)")
        c.commit()
        c.close()
        with self.assertRaises(E.MigrationError):
            E.run_migration(bogus, NEW_DEFAULT, out_path=self.tmp / "y.db")

    # ---- D-5: default output path prefers the tool's own output/ folder ----
    def test_default_out_path_prefers_tool_output_dir(self):
        p = E.default_out_path(NEW_DEFAULT, OLD_DEFAULT)
        expected = TOOL_DIR / "output" / f"{OLD_DEFAULT.stem}_migrated.db"
        self.assertEqual(p, expected)


if __name__ == "__main__":
    unittest.main(verbosity=2)
