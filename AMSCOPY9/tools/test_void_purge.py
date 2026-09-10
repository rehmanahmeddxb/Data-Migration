#!/usr/bin/env python3
"""Regression tests for the no-void purge contract.

Runs on stdlib only (no Flask, no SQLAlchemy) so it can run in CI and in this
sandbox.  Builds a small AMS-shaped fixture, voids rows in it, and checks that
the purge removes exactly the right things.

    python3 tools/test_void_purge.py -v
"""
from __future__ import annotations

import importlib.util
import sqlite3
import sys
import tempfile
import unittest
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
SERVICE = REPO / "app" / "services" / "void_purge.py"


def _load():
    spec = importlib.util.spec_from_file_location("_ams_void_purge", SERVICE)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


vp = _load()

SCHEMA = """
CREATE TABLE client (id INTEGER PRIMARY KEY, name TEXT, code TEXT);
CREATE TABLE delivery_person (id INTEGER PRIMARY KEY, name TEXT);
CREATE TABLE direct_sale (id INTEGER PRIMARY KEY, client_name TEXT, amount REAL,
                          is_void INTEGER DEFAULT 0);
CREATE TABLE direct_sale_item (id INTEGER PRIMARY KEY, sale_id INTEGER, qty REAL);
CREATE TABLE entry (id INTEGER PRIMARY KEY, source_table TEXT, source_id INTEGER,
                    bill_no TEXT, type TEXT, transaction_category TEXT,
                    nimbus_no TEXT, qty REAL, is_void INTEGER DEFAULT 0);
CREATE TABLE payment (id INTEGER PRIMARY KEY, client_name TEXT, amount REAL,
                      is_void INTEGER DEFAULT 0);
CREATE TABLE material_return (id INTEGER PRIMARY KEY, payment_id INTEGER,
                              amount REAL, is_void INTEGER DEFAULT 0);
CREATE TABLE material_return_item (id INTEGER PRIMARY KEY, material_return_id INTEGER);
CREATE TABLE waive_off (id INTEGER PRIMARY KEY, payment_id INTEGER, is_void INTEGER DEFAULT 0);
CREATE TABLE booking (id INTEGER PRIMARY KEY, amount REAL, is_void INTEGER DEFAULT 0);
CREATE TABLE booking_item (id INTEGER PRIMARY KEY, booking_id INTEGER);
CREATE TABLE booking_allocation (id INTEGER PRIMARY KEY, booking_item_id INTEGER,
                                 sale_id INTEGER, sale_item_id INTEGER);
CREATE TABLE pending_bill (id INTEGER PRIMARY KEY, source_table TEXT, source_id INTEGER,
                           is_void INTEGER DEFAULT 0);
CREATE TABLE delivery_rent (id INTEGER PRIMARY KEY, sale_id INTEGER);
CREATE TABLE sale_delivery_persons (id INTEGER PRIMARY KEY, sale_id INTEGER,
                                    is_void INTEGER DEFAULT 0);
CREATE TABLE delivery_person_payment (id INTEGER PRIMARY KEY, sale_id INTEGER,
                                      allocation_id INTEGER, delivery_person_id INTEGER);
CREATE TABLE follow_up_reminder (id INTEGER PRIMARY KEY, pending_bill_id INTEGER);
CREATE TABLE follow_up_contact (id INTEGER PRIMARY KEY, pending_bill_id INTEGER);
CREATE TABLE grn (id INTEGER PRIMARY KEY, auto_bill_no TEXT, is_void INTEGER DEFAULT 0);
CREATE TABLE grn_item (id INTEGER PRIMARY KEY, grn_id INTEGER);
CREATE TABLE audit_log (id TEXT PRIMARY KEY, user_id INTEGER);
"""


class VoidPurgeTests(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="ams_void_purge_"))
        self.db = self.tmp / "fixture.db"
        con = sqlite3.connect(self.db)
        con.executescript(SCHEMA)
        # two clean sales, one of which will be voided
        con.execute("INSERT INTO client VALUES (1,'C1','CC1'),(2,'C2','CC2')")
        con.execute("INSERT INTO delivery_person VALUES (1,'Driver')")
        con.execute("INSERT INTO direct_sale VALUES (1,'C1',100,0),(2,'C2',200,0),(3,'C2',300,0)")
        con.execute("INSERT INTO direct_sale_item VALUES (1,1,5),(2,2,7),(3,3,9)")
        con.execute("INSERT INTO entry VALUES (1,'direct_sale',1,'SB-1','OUT','Sale',NULL,5,0),"
                    "(2,'direct_sale',2,'SB-2','OUT','Sale',NULL,7,0),"
                    "(3,NULL,NULL,'BK-9','CANCEL','Cancel',NULL,3,0)")
        con.execute("INSERT INTO payment VALUES (1,'C1',50,0),(2,'C2',80,0)")
        con.execute("INSERT INTO material_return VALUES (1,1,10,0),(2,2,20,0)")
        con.execute("INSERT INTO material_return_item VALUES (1,1),(2,2)")
        con.execute("INSERT INTO waive_off VALUES (1,1,0),(2,2,0)")
        con.execute("INSERT INTO booking VALUES (1,500,0)")
        con.execute("INSERT INTO booking_item VALUES (1,1)")
        con.execute("INSERT INTO booking_allocation VALUES (1,1,NULL,NULL),(2,NULL,1,1)")
        con.execute("INSERT INTO pending_bill VALUES (1,'direct_sale',1,0),"
                    "(2,'booking',1,0),(3,NULL,NULL,0)")
        con.execute("INSERT INTO delivery_rent VALUES (1,1),(2,2)")
        con.execute("INSERT INTO sale_delivery_persons VALUES (1,1,0),(2,2,0)")
        con.execute("INSERT INTO delivery_person_payment VALUES (1,1,1,1),(2,2,2,1)")
        con.execute("INSERT INTO follow_up_reminder VALUES (1,1),(2,2)")
        con.execute("INSERT INTO follow_up_contact VALUES (1,1),(2,2)")
        con.execute("INSERT INTO grn VALUES (1,'GRN-1',0)")
        con.execute("INSERT INTO grn_item VALUES (1,1)")
        con.commit()
        con.close()

    def rows(self, table):
        con = sqlite3.connect(self.db)
        try:
            return con.execute(f'SELECT COUNT(*) FROM "{table}"').fetchone()[0]
        finally:
            con.close()

    def void(self, sql, *args):
        con = sqlite3.connect(self.db)
        con.execute(sql, args)
        con.commit()
        con.close()

    # ---- the purge removes voided rows and nothing else --------------------
    def test_purges_voided_row(self):
        self.void("UPDATE payment SET is_void=1 WHERE id=1")
        rep = vp.purge_voided_rows(self.db)
        # payment(void) + waive_off(cascade) + material_return(cascade) +
        # material_return_item(cascade) + the fixture's cancelled entry
        self.assertEqual(rep["deleted"], 5, rep["tables"])
        self.assertEqual(self.rows("payment"), 1)
        self.assertEqual(self.rows("waive_off"), 1)
        self.assertEqual(rep["tables"]["payment"]["removed_void"], 1)

    # ---- children of a purged parent go with it ----------------------------
    def test_cascades_to_children(self):
        self.void("UPDATE direct_sale SET is_void=1 WHERE id=1")
        rep = vp.purge_voided_rows(self.db)
        self.assertEqual(self.rows("direct_sale"), 2)
        self.assertEqual(self.rows("direct_sale_item"), 2)          # sale items
        self.assertEqual(self.rows("delivery_rent"), 1)             # rents
        self.assertEqual(self.rows("sale_delivery_persons"), 1)     # delivery rows
        self.assertEqual(self.rows("delivery_person_payment"), 1)   # driver payments
        # the sale's own stock entry (source_table/source_id) is purged, and
        # so is the fixture's CANCEL entry (always removed by the contract)
        self.assertEqual(self.rows("entry"), 1)
        # the pending bill raised by that sale goes as well
        self.assertEqual(self.rows("pending_bill"), 2)
        self.assertTrue(rep["totals"]["removed_cascade"] > 0)

    # ---- cancelled entries go even when is_void = 0 ------------------------
    def test_removes_cancelled_entries(self):
        self.assertEqual(self.rows("entry"), 3)
        rep = vp.purge_voided_rows(self.db)
        self.assertEqual(self.rows("entry"), 2)
        self.assertEqual(rep["tables"]["entry"]["removed_cancel"], 1)

    # ---- a row that is both void and cancelled is counted once -------------
    def test_no_double_counting(self):
        self.void("UPDATE entry SET is_void=1 WHERE id=3")  # the CANCEL row
        rep = vp.purge_voided_rows(self.db)
        entry = rep["tables"]["entry"]
        self.assertEqual(entry["total"] - entry["kept"], 1)
        self.assertEqual(entry["removed_void"] + entry["removed_cancel"], 1)

    # ---- rows whose parent never existed are removed -----------------------
    def test_removes_dangling_allocations(self):
        self.void("UPDATE booking_allocation SET booking_item_id=999 WHERE id=1")
        rep = vp.purge_voided_rows(self.db)
        self.assertEqual(rep["tables"]["booking_allocation"]["removed_missing_parent"], 1)
        self.assertEqual(self.rows("booking_allocation"), 1)

    # ---- dry run changes nothing -------------------------------------------
    def test_dry_run_is_read_only(self):
        self.void("UPDATE payment SET is_void=1 WHERE id=1")
        before = self.rows("payment")
        rep = vp.purge_voided_rows(self.db, dry_run=True)
        self.assertEqual(rep["deleted"], 5)
        self.assertEqual(self.rows("payment"), before)
        self.assertTrue(rep["dry_run"])

    # ---- nothing left behind, and nothing innocent lost --------------------
    def test_clean_database_is_untouched(self):
        before = {t: self.rows(t) for t in (
            "direct_sale", "entry", "payment", "pending_bill", "booking")}
        # the fixture already holds one cancelled entry, so remove it first
        vp.purge_voided_rows(self.db)
        rep = vp.purge_voided_rows(self.db)
        self.assertEqual(rep["deleted"], 0, rep["tables"])
        after = {t: self.rows(t) for t in before}
        self.assertEqual(after["direct_sale"], before["direct_sale"])
        self.assertEqual(after["payment"], before["payment"])
        self.assertEqual(after["booking"], before["booking"])
        self.assertEqual(after["pending_bill"], before["pending_bill"])

    # ---- census helper ------------------------------------------------------
    def test_count_voided_rows(self):
        self.void("UPDATE payment SET is_void=1 WHERE id=1")
        census = vp.count_voided_rows(self.db)
        self.assertEqual(census.get("payment", {}).get("is_void"), 1)
        self.assertEqual(census.get("entry", {}).get("cancelled"), 1)


if __name__ == "__main__":
    unittest.main(verbosity=2)
