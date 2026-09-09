"""Tests for the automatic versioned SQL migrations (app/services/auto_migrate).

These run without Flask: the runner accepts explicit paths, which is exactly
how the app factory invokes it after bootstrap (with the app's config).
"""
from __future__ import annotations

import sqlite3

import pytest

from app.services.auto_migrate import (
    list_migration_files,
    run_sql_migrations,
)


def _make_db(path) -> None:
    con = sqlite3.connect(str(path))
    con.execute("CREATE TABLE existing_one (id INTEGER PRIMARY KEY)")
    con.commit()
    con.close()


def _sqlite_master(db_path, obj_type, name) -> bool:
    con = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
    try:
        row = con.execute(
            "SELECT 1 FROM sqlite_master WHERE type=? AND name=?",
            (obj_type, name),
        ).fetchone()
        return row is not None
    finally:
        con.close()


def _history_rows(db_path) -> list:
    con = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
    try:
        con.execute(
            "CREATE TABLE IF NOT EXISTS migration_history "
            "(filename TEXT PRIMARY KEY, applied_at TEXT)"
        )
        return sorted(
            r[0]
            for r in con.execute(
                "SELECT filename FROM migration_history"
            ).fetchall()
        )
    finally:
        con.close()


def _version_row(db_path):
    con = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
    try:
        try:
            return con.execute(
                "SELECT id, version FROM schema_version"
            ).fetchone()
        except sqlite3.OperationalError:
            return None
    finally:
        con.close()


def test_numbered_migrations_apply_in_order_once_and_stamp_version(
    tmp_path,
):
    db = tmp_path / "app.db"
    _make_db(db)
    root = tmp_path / "migrations"
    root.mkdir()
    (root / "0001_add_widget.sql").write_text(
        "-- first migration\n"
        "CREATE TABLE widget (id INTEGER PRIMARY KEY, code TEXT);\n"
    )
    (root / "0002_widget_unique_code.sql").write_text(
        "CREATE UNIQUE INDEX uq_widget_code ON widget(code);\n"
    )
    (root / "README.md").write_text("not sql — ignored")

    report = run_sql_migrations(
        migrations_root=root, db_path=db, allow_destructive=False
    )
    assert report["applied"] == 2
    assert report["skipped"] == 0
    assert report["files"] == 2
    assert report["changed"] is True
    assert report["version"] == 2
    assert _sqlite_master(db, "table", "widget") is True
    assert _sqlite_master(db, "index", "uq_widget_code") is True
    assert _history_rows(db) == ["0001_add_widget.sql", "0002_widget_unique_code.sql"]
    assert _version_row(db) == (1, 2)

    # Second run: nothing pending, nothing double-applied.
    report = run_sql_migrations(
        migrations_root=root, db_path=db, allow_destructive=False
    )
    assert report["applied"] == 0
    assert report["skipped"] == 2
    assert report["changed"] is False
    assert report["version"] == 2
    assert _history_rows(db) == ["0001_add_widget.sql", "0002_widget_unique_code.sql"]


def test_unnumbered_sql_files_apply_but_do_not_stamp_version(tmp_path):
    db = tmp_path / "app.db"
    _make_db(db)
    root = tmp_path / "migrations"
    root.mkdir()
    (root / "baseline_helpers.sql").write_text(
        "CREATE TABLE helper_a (id INTEGER PRIMARY KEY);\n"
    )
    report = run_sql_migrations(migrations_root=root, db_path=db)
    assert report["applied"] == 1
    assert report["version"] is None
    assert _sqlite_master(db, "table", "helper_a") is True


def test_destructive_sql_blocked_by_default(tmp_path):
    db = tmp_path / "app.db"
    _make_db(db)
    root = tmp_path / "migrations"
    root.mkdir()
    (root / "0001_drop_wrong.sql").write_text("DELETE FROM existing_one;\n")
    with pytest.raises(ValueError, match="Destructive SQL blocked"):
        run_sql_migrations(migrations_root=root, db_path=db)
    # Nothing recorded, nothing stamped.
    assert _history_rows(db) == []
    assert _version_row(db) is None


def test_destructive_sql_allowed_with_override(tmp_path):
    db = tmp_path / "app.db"
    _make_db(db)
    root = tmp_path / "migrations"
    root.mkdir()
    (root / "0001_clear_staging.sql").write_text(
        "DELETE FROM existing_one;\n"
    )
    report = run_sql_migrations(
        migrations_root=root, db_path=db, allow_destructive=True
    )
    assert report["applied"] == 1
    assert report["version"] == 1


def test_no_migrations_directory_is_a_quiet_noop(tmp_path):
    db = tmp_path / "app.db"
    _make_db(db)
    report = run_sql_migrations(
        migrations_root=tmp_path / "does_not_exist", db_path=db
    )
    assert report == {
        "applied": 0,
        "skipped": 0,
        "files": 0,
        "dir": str(tmp_path / "does_not_exist"),
        "version": None,
        "history_count": 0,
        "changed": False,
    }


def test_missing_database_raises(tmp_path):
    root = tmp_path / "migrations"
    root.mkdir()
    (root / "0001_x.sql").write_text("SELECT 1;\n")
    with pytest.raises(FileNotFoundError):
        run_sql_migrations(
            migrations_root=root, db_path=tmp_path / "nope.db"
        )


def test_failing_migration_is_not_recorded_and_reports_filename(tmp_path):
    db = tmp_path / "app.db"
    _make_db(db)
    root = tmp_path / "migrations"
    root.mkdir()
    (root / "0001_bad.sql").write_text(
        "CREATE TABLE ok_part (id INTEGER PRIMARY KEY);\n"
        "THIS IS NOT SQL;\n"
    )
    with pytest.raises(ValueError, match="0001_bad.sql"):
        run_sql_migrations(migrations_root=root, db_path=db)
    assert _history_rows(db) == []
    assert _version_row(db) is None


def test_after_failure_idempotent_fixed_file_applies_on_next_run(tmp_path):
    db = tmp_path / "app.db"
    _make_db(db)
    root = tmp_path / "migrations"
    root.mkdir()
    bad = root / "0001_bad.sql"
    bad.write_text(
        "CREATE TABLE ok_part (id INTEGER PRIMARY KEY);\nBROKEN;\n"
    )
    with pytest.raises(ValueError):
        run_sql_migrations(migrations_root=root, db_path=db)
    # The statements before the failure stayed applied and the file was NOT
    # recorded — so the golden rule applies: fix the file idempotently and
    # the next boot retries it cleanly.
    bad.write_text(
        "CREATE TABLE IF NOT EXISTS ok_part (id INTEGER PRIMARY KEY);\n"
    )
    report = run_sql_migrations(migrations_root=root, db_path=db)
    assert report["applied"] == 1
    assert _sqlite_master(db, "table", "ok_part") is True
    assert _history_rows(db) == ["0001_bad.sql"]


def test_list_migration_files_ignores_non_sql(tmp_path):
    root = tmp_path / "migrations"
    root.mkdir()
    (root / "0001_a.sql").write_text("SELECT 1;")
    (root / "0002_b.sql").write_text("SELECT 1;")
    (root / "README.md").write_text("hi")
    assert list_migration_files(root) == ["0001_a.sql", "0002_b.sql"]
    assert list_migration_files(tmp_path / "absent") == []
