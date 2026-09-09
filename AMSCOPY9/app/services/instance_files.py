"""Ensure instance/ runtime files exist without touching live data.

Rules (on every server start):
- Empty instance folder / missing runtime ``.db`` → create a new empty SQLite
  file (schema is applied later; business tables stay at 0 rows).
- Runtime ``.db`` already present → leave it completely alone and use it.
- ``.db`` deleted but leftover ``-wal`` / ``-shm`` / ``-journal`` files remain
  → drop those lock leftovers, then recreate an empty database.
- Never invent extra backup copies or lock directories here.
"""
from __future__ import annotations

import logging
import sqlite3
from dataclasses import dataclass, field
from pathlib import Path

LOG = logging.getLogger(__name__)

RUNTIME_DB_NAME = "ahmed_cement_v44_fresh.db"

INSTANCE_SUBDIRS = (
    "logs",
    "storage/backups",
    "storage/temp",
    ".tmp/import_uploads",
    ".tmp/import_reports",
    "import_reports",
)

_SQLITE_SIDECAR_SUFFIXES = ("-wal", "-shm", "-journal")
_ALLOWED_JOURNAL_MODES = {"WAL", "DELETE", "TRUNCATE", "PERSIST", "MEMORY", "OFF"}


@dataclass
class InstanceRuntimeStatus:
    created: bool = False
    reused: bool = False
    cleaned: list[str] = field(default_factory=list)


def sqlite_sidecar_paths(db_path: Path) -> list[Path]:
    raw = str(db_path)
    return [Path(raw + suffix) for suffix in _SQLITE_SIDECAR_SUFFIXES]


def is_usable_sqlite_file(path: Path) -> bool:
    """True when *path* is a non-empty regular file (an existing database)."""
    try:
        return path.is_file() and path.stat().st_size > 0
    except OSError:
        return False


def ensure_instance_layout(instance_dir: Path) -> None:
    instance_dir.mkdir(parents=True, exist_ok=True)
    for rel in INSTANCE_SUBDIRS:
        (instance_dir / rel).mkdir(parents=True, exist_ok=True)


def remove_orphan_sqlite_sidecars(db_path: Path) -> list[str]:
    """Remove WAL/SHM/journal leftovers only when the main ``.db`` is gone.

    SQLite cannot open a database when leftover ``-wal``/``-shm`` files exist
    without the matching ``.db``. Those leftovers are a common cause of
    ``unable to open database file`` / ``database is locked`` after a deleted
    database.  When the main file still exists, SQLite owns the sidecars —
    leave them alone.
    """
    removed: list[str] = []
    if is_usable_sqlite_file(db_path):
        return removed
    for side in sqlite_sidecar_paths(db_path):
        try:
            if side.exists() or side.is_symlink():
                side.unlink()
                removed.append(str(side))
        except OSError:
            LOG.warning("Could not remove orphan SQLite sidecar %s", side, exc_info=True)
    # A 0-byte placeholder is not a real database; drop it so we can recreate.
    try:
        if db_path.exists() and db_path.is_file() and db_path.stat().st_size == 0:
            db_path.unlink()
            removed.append(str(db_path))
    except OSError:
        LOG.warning("Could not remove empty database placeholder %s", db_path, exc_info=True)
    return removed


def _normalise_journal_mode(journal_mode: str) -> str:
    mode = (journal_mode or "DELETE").strip().upper()
    if mode not in _ALLOWED_JOURNAL_MODES:
        return "DELETE"
    return mode


def _create_empty_sqlite(db_path: Path, journal_mode: str) -> None:
    db_path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(db_path), timeout=30)
    try:
        conn.execute("PRAGMA busy_timeout=8000")
        conn.execute(f"PRAGMA journal_mode={_normalise_journal_mode(journal_mode)}")
        conn.execute("PRAGMA user_version=0")
        conn.commit()
    finally:
        conn.close()


def ensure_instance_runtime(
    *,
    instance_dir: Path,
    db_path: Path,
    journal_mode: str = "DELETE",
) -> InstanceRuntimeStatus:
    """Create missing runtime files; never overwrite an existing database.

    Returns whether the runtime file was created or reused.
    """
    status = InstanceRuntimeStatus()
    ensure_instance_layout(instance_dir)
    db_path = Path(db_path)
    db_path.parent.mkdir(parents=True, exist_ok=True)

    status.cleaned = remove_orphan_sqlite_sidecars(db_path)
    if is_usable_sqlite_file(db_path):
        status.reused = True
        LOG.info("Using existing database %s (not recreating)", db_path)
        return status

    _create_empty_sqlite(db_path, journal_mode)
    status.created = True
    LOG.info("Created empty runtime database at %s", db_path)
    return status
