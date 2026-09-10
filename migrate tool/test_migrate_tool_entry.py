#!/usr/bin/env python3
"""Regression tests for the *entry point* of migrate_tool.py (the GUI/headless switch).

These exist because of a real crash on Termux/Android, where ``import tkinter``
succeeds but ``tk.Tk()`` dies with "no display name and no $DISPLAY environment
variable". The old check only tested the import, so a plain ``python
migrate_tool.py`` handed the user a traceback instead of a working tool.

Stdlib only. Run:  python3 test_migrate_tool_entry.py
"""
from __future__ import annotations

import os
import shutil
import sqlite3
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

HERE = Path(__file__).resolve().parent
TOOL = HERE / "migrate_tool.py"

FAKE_TK = '''
"""Minimal stand-in for a tkinter that imports but cannot open a window."""


class TclError(Exception):
    pass


def Tk(*args, **kwargs):
    raise TclError("no display name and no $DISPLAY environment variable")


class _Dummy:
    """Anything the GUI body would touch, so an import never masks the real cause."""

    def __getattr__(self, name):
        raise TclError("no display name and no $DISPLAY environment variable")


filedialog = messagebox = ttk = _Dummy()
'''


class _Sandbox(unittest.TestCase):
    """Runs the tool as a subprocess in a temp folder, as a user would."""

    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="migtool-entry-"))
        for name in ("migrate_tool.py", "migrate_engine.py"):
            shutil.copy2(HERE / name, self.tmp / name)
        (self.tmp / "drop").mkdir()
        (self.tmp / "output").mkdir()
        self.fakelib = self.tmp / "fakelib"
        (self.fakelib / "tkinter").mkdir(parents=True)
        (self.fakelib / "tkinter" / "__init__.py").write_text(FAKE_TK)
        # real databases, small enough to be built inline
        self.old = self._make_db(self.tmp / "ahmed_cement.db", v44=False)
        self.new = self._make_db(self.tmp / "ahmed_cement_v44_fresh.db", v44=True)

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    @staticmethod
    def _make_db(path: Path, v44: bool) -> Path:
        con = sqlite3.connect(str(path))
        con.executescript(
            "CREATE TABLE user (id INTEGER PRIMARY KEY, username TEXT, password TEXT"
            + (", access_mode TEXT DEFAULT 'full'" if v44 else "") + ");"
            "CREATE TABLE client (id INTEGER PRIMARY KEY, name TEXT);"
            "CREATE TABLE entry (id INTEGER PRIMARY KEY, qty REAL);"
            + ("CREATE TABLE cash_day_account_position (id INTEGER PRIMARY KEY);"
               if v44 else "")
        )
        con.execute("INSERT INTO user (username, password) VALUES ('admin','x')")
        con.execute("INSERT INTO client (name) VALUES ('Ali')")
        con.execute("INSERT INTO entry (qty) VALUES (7.5)")
        con.commit()
        con.close()
        return path

    def run_tool(self, *argv, tty=False, fake_tk=True, display=None):
        env = dict(os.environ)
        env.pop("DISPLAY", None)
        env.pop("WAYLAND_DISPLAY", None)
        if fake_tk:
            env["PYTHONPATH"] = str(self.fakelib) + os.pathsep + env.get("PYTHONPATH", "")
        if display is not None:
            env["DISPLAY"] = display
        stdin = subprocess.DEVNULL if not tty else subprocess.PIPE
        p = subprocess.Popen(
            [sys.executable, str(self.tmp / "migrate_tool.py"), *argv],
            cwd=str(self.tmp), stdin=stdin, stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT, text=True, env=env,
        )
        out, _ = p.communicate(timeout=180)
        return p.returncode, out


class TestHeadlessFallback(_Sandbox):
    def test_plain_run_without_display_does_not_traceback(self):
        """The Termux case: tkinter imports, no display — must not crash."""
        code, out = self.run_tool()
        self.assertNotIn("Traceback", out, out)
        self.assertNotIn("TclError", out.split("\n")[-1], out)
        self.assertIn("running headless", out, out)
        self.assertEqual(code, 2, out)  # no files given, no tty -> tell how

    def test_headless_notice_explains_the_reason(self):
        _code, out = self.run_tool()
        self.assertIn("$DISPLAY", out, out)

    def test_gui_flag_fails_loudly_with_the_x_server_help(self):
        code, out = self.run_tool("--gui", display=":0")
        self.assertEqual(code, 2, out)
        self.assertIn("no display available", out, out)
        self.assertIn("termux-x11", out, out)
        self.assertNotIn("Traceback", out, out)

    def test_cli_flag_with_full_pairing_runs_the_engine(self):
        out_db = self.tmp / "result.db"
        code, out = self.run_tool(
            "--cli", "--old", str(self.old), "--new", str(self.new),
            "--out", str(out_db),
        )
        self.assertIn("RESULT: PASS", out, out)
        self.assertEqual(code, 0, out)
        self.assertTrue(out_db.exists(), out)

    def test_no_display_with_pairing_still_migrates(self):
        """No --cli, no display, but both files given: the fallback must be usable."""
        out_db = self.tmp / "result2.db"
        code, out = self.run_tool(
            "--old", str(self.old), "--new", str(self.new), "--out", str(out_db),
        )
        self.assertEqual(code, 0, out)
        self.assertIn("RESULT: PASS", out, out)

    def test_missing_file_names_the_path_not_just_says_nothing_exists(self):
        code, out = self.run_tool(
            "--cli", "--old", str(self.tmp / "nope.db"), "--new", str(self.new)
        )
        self.assertEqual(code, 2, out)
        self.assertIn("nope.db", out, out)

    def test_non_tty_without_files_prints_a_runnable_command(self):
        shutil.copy2(self.old, self.tmp / "drop" / self.old.name)
        shutil.copy2(self.new, self.tmp / "drop" / self.new.name)
        code, out = self.run_tool("--no-gui")
        self.assertEqual(code, 2, out)
        self.assertIn("--cli --old", out, out)
        self.assertIn(self.old.name, out, out)


class TestInspectionHelpers(_Sandbox):
    def test_suggest_pair_labels_the_v44_file_as_new(self):
        sys.path.insert(0, str(HERE))
        try:
            import migrate_tool as mt
            found = [self.old, self.new]
            suggested_old, suggested_new = mt._suggest_pair(found)
            self.assertEqual(Path(suggested_old), self.old)
            self.assertEqual(Path(suggested_new), self.new)
        finally:
            sys.path.pop(0)

    def test_probe_gui_reports_why_not(self):
        sys.path.insert(0, str(HERE))
        try:
            for var in ("DISPLAY", "WAYLAND_DISPLAY"):
                os.environ.pop(var, None)
            import importlib
            import migrate_tool as mt
            importlib.reload(mt)  # cached check must not leak between cases
            ok, why = mt._gui_available()
            self.assertFalse(ok, why)
            self.assertTrue(why, "a reason must be reported to the user")
        finally:
            sys.path.pop(0)


if __name__ == "__main__":
    unittest.main(verbosity=2)
