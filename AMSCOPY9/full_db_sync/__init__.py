"""full_db_sync — reliable full-database snapshot import/export for AMS.

The legacy full-raw workflow moved data through an ``.xlsx`` workbook (one
sheet per table).  Excel is a *display* format, not a *storage* format: sheet
row limits, type coercion (dates, floats, text starting with ``=``), duplicate
sheet names, missing-sheet semantics and pandas/openpyxl version drift all make
it a fragile transport for a whole accounting database.

This package replaces that transport with a **plain SQLite database file**
(``.amsdb``), which is the same format the live application already uses:

* a snapshot is a byte-faithful SQLite copy of the live database plus a tiny
  ``__ams_full_db_meta__`` table (export time, tool version, per-table row
  counts) so the file is self-describing and verifiable;
* an import performs a **clean-and-copy**: first every row of the target
  database is deleted (schema and app metadata are kept), then every row of
  the source file is inserted with its original primary keys, so every
  foreign-key relationship survives intact;
* verification is built in (``PRAGMA integrity_check``, row-count parity per
  table, ``PRAGMA foreign_key_check``) and every import is preceded by an
  automatic backup of the target database.

The engine is pure stdlib (``sqlite3`` only) on purpose — the same code runs
inside the Flask app, in the CLI, and on any machine that can read a SQLite
file, with no dependency on pandas/openpyxl.

Public API (see each function's docstring):

    export_snapshot(source_db_path, out_path=None, meta=None) -> dict
    verify_snapshot(db_path, expect_meta=True) -> dict
    clean_all_data(db_path, backup=True, backup_dir=None) -> dict
    import_snapshot(source_path, target_db_path, ...) -> dict
"""
from .engine import (
    AMS_META_TABLE,
    SNAPSHOT_SPEC_VERSION,
    clean_all_data,
    export_snapshot,
    import_snapshot,
    table_rows,
    verify_snapshot,
)

__all__ = [
    "AMS_META_TABLE",
    "SNAPSHOT_SPEC_VERSION",
    "clean_all_data",
    "export_snapshot",
    "import_snapshot",
    "table_rows",
    "verify_snapshot",
]
