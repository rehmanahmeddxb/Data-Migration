"""Void purge — remove every voided / cancelled row from an AMS database.

POLICY (owner decision, 2026-09-10)
-----------------------------------
**The v4.4 database holds no voided data.**  The legacy application never
deleted a record: it set ``is_void = 1`` (and cancelled entries as
``type='CANCEL'``).  The v4.4 application deletes for real, so a flagged row is
a record that should no longer exist.

Every delete path in the app now removes the row (see
``app/services/void_rebuild.hard_delete_*``).  This module is the *guarantee*:
it removes any voided row that still exists — including rows inherited from a
legacy database — together with the children that would otherwise be orphaned.

The contract is identical to the migration tool's purge
(``migrate tool/migrate_engine.py::_purge_voided``) and to the retired Excel
pipeline (``tools/migrate/_migrate_common.py``):

* every row with ``is_void = 1`` is deleted
* ``entry`` rows that are ``CANCEL`` are deleted even when ``is_void = 0``
* children of deleted parents are deleted (cascade), so no orphan references
  are left behind
* rows whose parent never existed are deleted as well

Deliberately **stdlib only** (sqlite3): it must run inside the Flask app at
boot, from the command line, and in CI — with or without Flask installed.
"""
from __future__ import annotations

import sqlite3
from pathlib import Path

# (child, child_fk, parent, extra SQL condition or None)
CASCADE_RULES = (
    ("booking_item", "booking_id", "booking", None),
    ("direct_sale_item", "sale_id", "direct_sale", None),
    ("booking_allocation", "sale_id", "direct_sale", None),
    ("booking_allocation", "sale_item_id", "direct_sale_item", None),
    ("booking_allocation", "booking_item_id", "booking_item", None),
    ("entry", "source_id", "direct_sale", "source_table = 'direct_sale'"),
    ("pending_bill", "source_id", "direct_sale", "source_table = 'direct_sale'"),
    ("pending_bill", "source_id", "booking", "source_table = 'booking'"),
    ("delivery_rent", "sale_id", "direct_sale", None),
    ("sale_delivery_persons", "sale_id", "direct_sale", None),
    ("waive_off", "payment_id", "payment", None),
    ("material_return", "payment_id", "payment", None),
    ("grn_item", "grn_id", "grn", None),
    ("material_return_item", "material_return_id", "material_return", None),
    ("follow_up_reminder", "pending_bill_id", "pending_bill", None),
    ("follow_up_contact", "pending_bill_id", "pending_bill", None),
    ("delivery_person_payment", "sale_id", "direct_sale", None),
    ("delivery_person_payment", "allocation_id", "sale_delivery_persons", None),
    ("delivery_person_payment", "delivery_person_id", "delivery_person", None),
    ("direct_sale_item", "grn_item_id", "grn_item", None),
)

# (child, child_fk, parent) — rows whose parent does not exist at all
MISSING_PARENT_RULES = (
    ("booking_allocation", "booking_item_id", "booking_item"),
)

# Cancelled rows that do not carry is_void = 1.
CANCEL_RULES = (
    ("entry", "type", "CANCEL"),
    ("entry", "transaction_category", "CANCEL"),
)


def _q(name: str) -> str:
    return '"' + str(name).replace('"', '""') + '"'


def purge_voided_rows(db_path, *, dry_run: bool = False) -> dict:
    """Delete every voided / cancelled row from *db_path*.

    Returns ``{table: {total, kept, removed_void, removed_cancel,
    removed_cascade, removed_missing_parent}}`` plus ``totals`` and
    ``deleted`` (total rows removed).  With ``dry_run=True`` nothing is
    written — the report shows what *would* go.

    The whole purge runs in a single transaction and leaves the database's
    schema, indexes and every non-voided row untouched.
    """
    path = Path(db_path).expanduser()
    con = sqlite3.connect(str(path), timeout=120)
    con.execute("PRAGMA foreign_keys=0")
    try:
        tables = [r[0] for r in con.execute(
            "SELECT name FROM sqlite_master WHERE type='table' "
            "AND name NOT LIKE 'sqlite_%'")]
        present = set(tables)

        def cols(t: str) -> set:
            return {r[1] for r in con.execute(f'PRAGMA table_info({_q(t)})')}

        def all_ids(t: str) -> set:
            if t not in present or "id" not in cols(t):
                return set()
            return {r[0] for r in con.execute(f'SELECT id FROM {_q(t)}')}

        dropped: dict = {}
        void_ids: dict = {}
        cancel_ids: dict = {}

        def mark(table: str, ids: set) -> None:
            if ids:
                dropped.setdefault(table, set()).update(ids)

        # pass 1: is_void = 1
        for t in sorted(present):
            if "is_void" not in cols(t):
                continue
            ids = {r[0] for r in con.execute(
                f'SELECT id FROM {_q(t)} '
                'WHERE COALESCE(CAST(is_void AS INTEGER), 0) = 1')}
            if ids:
                void_ids[t] = ids
                mark(t, ids)

        # pass 2: cancelled entries
        for t, col, value in CANCEL_RULES:
            if t not in present or col not in cols(t):
                continue
            ids = {r[0] for r in con.execute(
                f'SELECT id FROM {_q(t)} WHERE '
                f"UPPER(TRIM(COALESCE({_q(col)}, ''))) = ?", (value.upper(),))}
            if ids:
                cancel_ids.setdefault(t, set()).update(ids)
                mark(t, ids)

        # pass 3: cascade until stable
        parent_ids = {t: all_ids(t) for t in
                      {p for _, _, p, _ in CASCADE_RULES} |
                      {p for _, _, p in MISSING_PARENT_RULES}}
        while True:
            before = sum(len(v) for v in dropped.values())
            for child, col, parent, cond in CASCADE_RULES:
                if child not in present or parent not in present:
                    continue
                if col not in cols(child):
                    continue
                gone = parent_ids.get(parent, set()) & dropped.get(parent, set())
                if not gone:
                    continue
                sql = (f'SELECT id FROM {_q(child)} WHERE {_q(col)} IN '
                       f'({", ".join("?" for _ in gone)})')
                if cond:
                    sql += f' AND ({cond})'
                mark(child, {r[0] for r in con.execute(sql, list(gone))})
            if sum(len(v) for v in dropped.values()) == before:
                break

        # pass 4: rows whose parent never existed
        missing: dict = {}
        for child, col, parent in MISSING_PARENT_RULES:
            if child not in present or parent not in present:
                continue
            if col not in cols(child):
                continue
            alive = parent_ids.get(parent, set()) - dropped.get(parent, set())
            ids = {r[0] for r in con.execute(
                f'SELECT id, {_q(col)} FROM {_q(child)} '
                f'WHERE {_q(col)} IS NOT NULL')
                if r[1] not in alive and r[0] not in dropped.get(child, set())}
            if ids:
                missing[child] = ids
                mark(child, ids)

        # apply
        report: dict = {}
        deleted = 0
        for t in sorted(dropped):
            ids = dropped[t]
            total = con.execute(f'SELECT COUNT(*) FROM {_q(t)}').fetchone()[0]
            v = len(void_ids.get(t, set()))
            c = len(cancel_ids.get(t, set()) - void_ids.get(t, set()))
            m = len(missing.get(t, set()))
            if not dry_run:
                con.executemany(f'DELETE FROM {_q(t)} WHERE id = ?',
                                [(i,) for i in ids])
                kept = con.execute(
                    f'SELECT COUNT(*) FROM {_q(t)}').fetchone()[0]
            else:
                kept = total - len(ids)
            deleted += len(ids)
            report[t] = {
                "total": total,
                "kept": kept,
                "removed_void": v,
                "removed_cancel": c,
                "removed_cascade": max(0, len(ids) - v - c - m),
                "removed_missing_parent": m,
            }

        if not dry_run:
            con.commit()
        else:
            con.rollback()

        totals = {
            "rows_removed": deleted,
            "removed_void": sum(r["removed_void"] for r in report.values()),
            "removed_cancel": sum(r["removed_cancel"] for r in report.values()),
            "removed_cascade": sum(r["removed_cascade"] for r in report.values()),
            "removed_missing_parent": sum(
                r["removed_missing_parent"] for r in report.values()),
            "tables": len(report),
        }
        return {"tables": report, "totals": totals, "deleted": deleted,
                "dry_run": dry_run, "db_path": str(path)}
    finally:
        con.close()


def count_voided_rows(db_path) -> dict:
    """Cheap read-only census: how many voided/cancelled rows are in there."""
    path = Path(db_path).expanduser()
    con = sqlite3.connect(f"file:{path.as_posix()}?mode=ro", uri=True)
    try:
        out = {}
        for (t,) in con.execute(
            "SELECT name FROM sqlite_master WHERE type='table' "
            "AND name NOT LIKE 'sqlite_%' ORDER BY name"):
            cols = {r[1] for r in con.execute(f'PRAGMA table_info({_q(t)})')}
            if "is_void" not in cols:
                continue
            n = con.execute(
                f'SELECT COUNT(*) FROM {_q(t)} '
                'WHERE COALESCE(CAST(is_void AS INTEGER), 0) = 1').fetchone()[0]
            if n:
                out[t] = {"is_void": n}
        cancels = con.execute(
            "SELECT COUNT(*) FROM entry WHERE "
            "UPPER(TRIM(COALESCE(type, ''))) = 'CANCEL' OR "
            "UPPER(TRIM(COALESCE(transaction_category, ''))) = 'CANCEL'"
        ).fetchone()[0]
        if cancels:
            out.setdefault("entry", {})["cancelled"] = cancels
        return out
    finally:
        con.close()
