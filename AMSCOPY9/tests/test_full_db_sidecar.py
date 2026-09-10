"""Sidecar-gate tests for full_db_sync.import_snapshot (added 2026-09-10).

A plain ``.db`` source must carry the ``RESULT: PASS`` sidecar written by the
AMS migration tool (``<name>.db.report.txt`` next to the file) so that a
quarantined ``*.INCOMPLETE`` run can never be imported by explicit path.
``.amsdb`` snapshots are self-verifying and exempt; ``require_sidecar=False``
(CLI ``--allow-no-sidecar`` / the app upload UI) bypasses the gate for
automation that verifies on its own.

Pure stdlib — no Flask, no pandas.  Skips cleanly when the data-lab template
is absent.  Run:  python3 tests/test_full_db_sidecar.py -v
"""
from __future__ import annotations

import shutil
import sys
import tempfile
import unittest
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from full_db_sync.engine import FullDbSyncError, import_snapshot  # noqa: E402

TEMPLATE = (ROOT.parent / "data lab for migration old to new" / "NewData"
            / "ahmed_cement_v44_fresh.db")


@unittest.skipUnless(TEMPLATE.exists(), "data-lab v4.4 template not present")
class SidecarGateTests(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="ams_sidecar_test_"))
        self.target = self.tmp / "target.db"
        self.source = self.tmp / "source.db"
        shutil.copy(TEMPLATE, self.target)
        shutil.copy(TEMPLATE, self.source)
        self.rows_before = self._count()

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def _count(self) -> int:
        import sqlite3
        con = sqlite3.connect(f"file:{self.target}?mode=ro", uri=True)
        try:
            return sum(
                con.execute(f'SELECT COUNT(*) FROM "{t}"').fetchone()[0]
                for t in [r[0] for r in con.execute(
                    "SELECT name FROM sqlite_master WHERE type='table' "
                    "AND name NOT LIKE 'sqlite_%'")]
            )
        finally:
            con.close()

    def _import(self, **kw) -> dict:
        return import_snapshot(
            self.source, self.target,
            backup_target=False, require_sidecar=True, **kw)

    def test_plain_db_without_sidecar_is_refused(self):
        with self.assertRaises(FullDbSyncError) as ctx:
            self._import()
        msg = str(ctx.exception)
        self.assertIn("RESULT: PASS", msg)
        self.assertIn("source.db.report.txt", msg)
        self.assertEqual(self._count(), self.rows_before,
                         "the target must be untouched when the gate refuses")

    def test_quarantined_incomplete_file_is_refused(self):
        # The migration tool renames aborted runs to <name>.db.INCOMPLETE —
        # naming one explicitly must still be refused.
        quarantined = self.source.with_name(self.source.name + ".INCOMPLETE")
        self.source.rename(quarantined)
        with self.assertRaises(FullDbSyncError):
            import_snapshot(quarantined, self.target,
                            backup_target=False, require_sidecar=True)

    def test_review_sidecar_is_refused(self):
        sidecar = self.source.with_name(self.source.name + ".report.txt")
        sidecar.write_text("AMS DATABASE MIGRATION REPORT\nRESULT: REVIEW\n",
                           encoding="utf-8")
        with self.assertRaises(FullDbSyncError) as ctx:
            self._import()
        self.assertIn("RESULT: PASS", str(ctx.exception))

    def test_pass_sidecar_allows_the_import(self):
        sidecar = self.source.with_name(self.source.name + ".report.txt")
        sidecar.write_text(
            "AMS DATABASE MIGRATION REPORT\n... verification ...\n"
            "RESULT: PASS\n", encoding="utf-8")
        report = self._import()
        self.assertEqual(report["verification"], "PASS")
        self.assertIn("RESULT: PASS", report["sidecar"])

    def test_amsdb_snapshot_is_exempt(self):
        amsdb = self.source.with_name(self.source.name + ".amsdb")
        self.source.rename(amsdb)
        report = import_snapshot(amsdb, self.target,
                                 backup_target=False, require_sidecar=True)
        self.assertEqual(report["verification"], "PASS")
        self.assertIn("exempt", report["sidecar"])

    def test_require_sidecar_false_bypasses_the_gate(self):
        report = import_snapshot(self.source, self.target,
                                 backup_target=False, require_sidecar=False)
        self.assertEqual(report["verification"], "PASS")


if __name__ == "__main__":
    unittest.main(verbosity=2)
