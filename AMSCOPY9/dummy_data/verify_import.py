#!/usr/bin/env python3
"""
Round-trip verification: import the generated dummy workbook through the
REAL app import engine (`_run_full_raw_import_bytes` — the exact function
behind  Import & Export → Import Full XLSX)  into a throwaway database and
assert every row lands.

Run:  APP_DB_PATH=/tmp/ams_verify/app.db python3 dummy_data/verify_import.py
"""

import os
import sys
import io
import tempfile

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, REPO_ROOT)

XLSX = os.path.join(os.path.dirname(os.path.abspath(__file__)), "AMS_DUMMY_DATA_FULL.xlsx")

with open(XLSX, "rb") as fh:
    file_bytes = fh.read()

from app import create_app  # noqa: E402

app = create_app()

with app.app_context():
    from models import db
    from blueprints.import_export.engine import _run_full_raw_import_bytes
    from blueprints.import_export.misc_helpers import _detect_transfer_workbook_kind

    kind = _detect_transfer_workbook_kind(file_bytes)
    print(f"workbook auto-detected as: {kind}")
    assert kind == "literal_all", f"detection failed: {kind}"

    scope_ctx = {
        "scope": "single_store", "target_tenant_id": None,
        "target_tenant_name": None, "role": "admin",
    }
    report, report_name = _run_full_raw_import_bytes(
        file_bytes=file_bytes,
        scope_ctx=scope_ctx,
        mode="append",
        source_file_name="AMS_DUMMY_DATA_FULL.xlsx",
        allowed_tables=None,
    )

    print(f"\nimport status : {report['status']}")
    print(f"inserted      : {report['inserted']}")
    print(f"updated       : {report['updated']}")
    print(f"skipped       : {report['skipped']}")
    print(f"failed        : {report['failed']}")
    print(f"warnings      : {report['warnings']}")
    print(f"tables        : {report['tables']}")

    bad = [t for t in report["table_results"] if t.get("failed")]
    if bad:
        print("\nFAILED TABLES:")
        for t in bad:
            print(f"  {t['name']}: failed={t['failed']} error={t['error'][:300]}")
        for d in (report.get("error_details") or [])[:20]:
            print("  detail:", d[:300])
        sys.exit(1)

    # verify key business counts inside the DB
    from models import (Client, Material, Booking, BookingItem, DirectSale,
                        DirectSaleItem, Entry, Payment, GRN, GRNItem,
                        MaterialReturn, MaterialReturnItem, Account,
                        AccountTransaction, CashFlowEntry, Invoice,
                        PendingBill, SupplierPayment, DeliveryRent,
                        WaiveOff, BookingAllocation, GRNAllocation)
    checks = {
        "clients": Client.query.count(),
        "materials": Material.query.count(),
        "bookings": Booking.query.count(),
        "booking_items": BookingItem.query.count(),
        "direct_sales": DirectSale.query.count(),
        "direct_sale_items": DirectSaleItem.query.count(),
        "dispatch_entries": Entry.query.count(),
        "payments": Payment.query.count(),
        "grns": GRN.query.count(),
        "grn_items": GRNItem.query.count(),
        "material_returns": MaterialReturn.query.count(),
        "material_return_items": MaterialReturnItem.query.count(),
        "accounts": Account.query.count(),
        "account_transactions": AccountTransaction.query.count(),
        "cash_flow_entries": CashFlowEntry.query.count(),
        "invoices": Invoice.query.count(),
        "pending_bills": PendingBill.query.count(),
        "supplier_payments": SupplierPayment.query.count(),
        "delivery_rents": DeliveryRent.query.count(),
        "waive_offs": WaiveOff.query.count(),
        "booking_allocations": BookingAllocation.query.count(),
        "grn_allocations": GRNAllocation.query.count(),
    }
    print("\nDB counts after import:")
    for k, v in checks.items():
        print(f"  {k:24s} {v}")

    assert checks["clients"] >= 100, "expected 100+ clients"

    # spot-check FK integrity in the imported DB
    from sqlalchemy import text as _sql
    fk_violations = db.session.execute(_sql("PRAGMA foreign_key_check")).fetchall()
    if fk_violations:
        print("FK VIOLATIONS:", fk_violations[:10])
        sys.exit(1)

    print("\nALL CHECKS PASSED — workbook imports 100% with zero failed rows.")
