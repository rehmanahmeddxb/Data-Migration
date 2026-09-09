"""
Automatic, versioned SQL migrations — run at every application start.

Why this exists
---------------
``db.create_all()`` creates *missing* tables and the hand-written
``_ensure_*`` helpers add *missing nullable columns*, but neither can express
renames, backfills, index/unique-constraint changes on existing tables, or a
recorded history of what changed.  From now on, every future schema change
(new module tables, new columns with defaults, new indexes, …) ships as a
numbered ``NNNN_*.sql`` file in ``app/migrations/`` and is applied
automatically on the next application start (which also covers every
deployment, because the deployer reloads the app).

How it works
------------
1. Runs inside the app factory *after* ``db.create_all()``, so ORM tables
   already exist and migrations only add what models cannot.
2. Reads ``*.sql`` files from the migrations directory in sorted order.
3. Applies only files not yet recorded in the ``migration_history`` table
   (shared with the release-deploy SQL runner so both paths see one record).
4. Records each successfully applied file, then stamps the largest numeric
   prefix of the applied set into ``schema_version`` (id=1) so any tool (e.g.
   a future "is this file an old-format DB?" import check) can compare schema
   versions deterministically.
5. Idempotent and concurrency-tolerant: two processes booting at once both
   try to apply the same file; the loser sees it already recorded and moves
   on (SQLite raises on the second DDL run and we re-check the history).

Rules for migration files
-------------------------
* Name: ``NNNN_short_description.sql`` (zero-padded numeric prefix first).
  The largest applied prefix becomes ``schema_version.version``.
* Never edit a file that was already applied — add a new higher-numbered
  file instead (migration_history records what ran where).
* Keep each file safe to retry (``IF NOT EXISTS`` / ``INSERT OR IGNORE``):
  a failure mid-file leaves earlier statements of that file applied.
* Destructive statements (``DROP TABLE`` / ``TRUNCATE TABLE`` /
  ``DELETE FROM``) are blocked unless ``MIGRATIONS_ALLOW_DESTRUCTIVE=1`` —
  same guard as the release-deploy runner.
"""
from __future__ import annotations

import logging
import os
import re
import sqlite3
from datetime import datetime
from pathlib import Path
from typing import Optional

log = logging.getLogger("app.auto_migrate")

# Shared with blueprints/import_export/deploy.py's SQL runner so the startup
# path and the release-deploy path never double-apply a file.
HISTORY_TABLE = "migration_history"
VERSION_TABLE = "schema_version"
_FILE_RE = re.compile(r"^(\d+)[_\-].*\.sql$")
_DESTRUCTIVE_RE = re.compile(
    r"\b(drop\s+table|truncate\s+table|delete\s+from)\b", re.IGNORECASE
)
_TRUE_VALUES = {"1", "true", "on", "yes"}


def _config(key: str, default=None):
    try:
        from flask import current_app

        return current_app.config.get(key, default)
    except Exception:  # pragma: no cover - running without an app context
        return os.environ.get(key, default)


def migrations_dir() -> Path:
    """The directory that holds numbered ``NNNN_*.sql`` migration files."""
    configured = _config("MIGRATIONS_DIR")
    if configured:
        return Path(configured)
    try:
        from flask import current_app

        base = Path(current_app.root_path)
    except Exception:  # pragma: no cover - running without an app context
        base = Path(__file__).resolve().parents[1]
    return base / "migrations"


def list_migration_files(directory) -> list:
    """Sorted ``*.sql`` file names inside *directory* (empty if absent)."""
    path = Path(directory) if directory else None
    if path is None or not path.is_dir():
        return []
    return sorted(p.name for p in path.glob("*.sql"))


def _now() -> str:
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def _open_db(db_path: Path) -> sqlite3.Connection:
    con = sqlite3.connect(str(db_path), timeout=120, isolation_level=None)
    con.execute(f"PRAGMA busy_timeout={120 * 1000}")
    return con


def _applied_history(con: sqlite3.Connection) -> set:
    """Filename set of already-applied migrations (table created on demand)."""
    con.execute(
        f"CREATE TABLE IF NOT EXISTS {HISTORY_TABLE} "
        "(filename TEXT PRIMARY KEY, applied_at TEXT)"
    )
    return {
        r[0]
        for r in con.execute(f"SELECT filename FROM {HISTORY_TABLE}")
    }


def _stamp_version(con: sqlite3.Connection, applied: set) -> Optional[int]:
    """Record the highest applied numeric prefix into ``schema_version``."""
    best = None
    for name in applied:
        m = _FILE_RE.match(name)
        if m:
            value = int(m.group(1))
            if best is None or value > best:
                best = value
    if best is None:
        return None
    con.execute(
        f"CREATE TABLE IF NOT EXISTS {VERSION_TABLE} "
        "(id INTEGER PRIMARY KEY, version INTEGER, applied_at DATETIME)"
    )
    con.execute(
        f"INSERT OR REPLACE INTO {VERSION_TABLE} (id, version, applied_at) "
        "VALUES (1, ?, ?)",
        (best, _now()),
    )
    return best


def run_sql_migrations(migrations_root=None, db_path=None,
                       allow_destructive: Optional[bool] = None) -> dict:
    """Apply every pending numbered SQL migration; returns a report dict.

    All arguments are optional and default to the running app's
    configuration (``MIGRATIONS_DIR`` + ``APP_DB_PATH`` +
    ``MIGRATIONS_ALLOW_DESTRUCTIVE``), which makes the function directly
    testable without Flask.

    Report keys: applied, skipped, files, dir, version, history_count,
    changed.
    """
    root = Path(migrations_root) if migrations_root else migrations_dir()
    files = list_migration_files(root)
    base = {
        "applied": 0,
        "skipped": 0,
        "files": len(files),
        "dir": str(root),
        "version": None,
        "history_count": 0,
        "changed": False,
    }
    if not files:
        return base

    if db_path is None:
        configured = _config("APP_DB_PATH")
        db_path = Path(configured) if configured else None
        if not db_path:
            raise ValueError(
                "APP_DB_PATH is not configured and db_path was not passed."
            )
    db_path = Path(db_path)
    if not db_path.exists():
        raise FileNotFoundError(f"Database not found: {db_path}")

    if allow_destructive is None:
        allow_destructive = (
            str(_config("MIGRATIONS_ALLOW_DESTRUCTIVE", "0"))
            .strip()
            .lower()
            in _TRUE_VALUES
        )

    con = _open_db(db_path)
    try:
        applied = _applied_history(con)
        pending = [f for f in files if f not in applied]
        applied_count = 0
        for name in pending:
            sql_text = (root / name).read_text(encoding="utf-8")
            if not sql_text.strip():
                continue
            if not allow_destructive and _DESTRUCTIVE_RE.search(sql_text):
                raise ValueError(
                    f"Destructive SQL blocked in migration '{name}'. "
                    "Set MIGRATIONS_ALLOW_DESTRUCTIVE=1 to allow it "
                    "explicitly."
                )
            try:
                con.executescript(sql_text)
                con.execute(
                    f"INSERT OR IGNORE INTO {HISTORY_TABLE} "
                    "(filename, applied_at) VALUES (?, ?)",
                    (name, _now()),
                )
            except sqlite3.OperationalError as exc:
                # A concurrent boot may have applied this exact file between
                # our history read and our DDL run — treat that as success.
                if name in _applied_history(con):
                    continue
                raise ValueError(
                    f"Migration '{name}' failed: {exc}"
                ) from exc
            applied_count += 1
            applied.add(name)
            log.info("Applied schema migration %s", name)

        version = _stamp_version(con, applied)
        return {
            "applied": applied_count,
            "skipped": len(files) - applied_count,
            "files": len(files),
            "dir": str(root),
            "version": version,
            "history_count": len(applied),
            "changed": applied_count > 0,
        }
    finally:
        con.close()
