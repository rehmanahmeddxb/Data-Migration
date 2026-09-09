# AMS Dummy Data (Full Import Workbook)

**File:** `AMS_DUMMY_DATA_FULL.xlsx`  ·  51 sheets  ·  ~7,500 rows  ·  ~0.6 MB

This workbook was generated from the app's **own database schema** and uses the
exact format of the app's *Export Full XLSX* backup. It imports **100%**
(0 skipped, 0 failed — verified against the real import engine in both
Append and Overwrite modes) through the built-in import option.

---

## How to import (Import & Export page)

1. Log in as **admin** and open **Import / Export Center**.
2. Use the **"Import Full XLSX"** card (or "Restore Selected Modules" — the file
   is auto-detected as a Literal Full Raw workbook and both go to the same engine).
3. Pick the file `AMS_DUMMY_DATA_FULL.xlsx`.
4. Choose the import mode:
   * **Overwrite — replace tables supplied by workbook**  ← recommended for a
     clean dummy-data load. Replaces the business tables with the workbook data.
     Your users, settings and system configuration are **never** touched
     (those sheets are intentionally not in the file).
   * **Append — keep existing rows** — adds the dummy data alongside whatever is
     already in the database (safe; duplicate primary keys would be skipped).
5. Click **Import Full XLSX**. You should get: status `ok`, inserted ≈ 7,491,
   skipped 0, failed 0, warnings 0.

> The import can take a minute or two — it writes row-by-row with per-row
> savepoints and gives a per-table report at the end.

## What's inside (per module)

| Module / Page | Data included |
|---|---|
| **Clients** | 121 clients (`FBMCL-00001`…`FBMCL-00120` + the system `OPEN KHATA` walk-in row), with phones, addresses, categories, opening balances, book/page refs, location links — 1 inactive client included |
| **Materials** | 10 categories, 42 materials (cement brands, steel, bricks, sand/crush, blocks, tiles, paint, hardware, plumbing, electrical) with live stock totals |
| **Suppliers** | 12 suppliers + **19 supplier payments** (cash/bank/cheque, `SB-SP-…` / `MB NO.8…` bills) |
| **GRN (receiving)** | 60 GRNs + 149 GRN items — cash-paid, cheque-paid, part-paid and open-credit receivings, loading/freight/discount costs, supplier invoice numbers + 348 FIFO `grn_allocation` lots |
| **Dispatch (entries)** | 1,091 stock entries — `OUT` dispatches for every sale line + standalone market dispatches (billed `CEMENT+BILL`, unbilled `CEMENT`/`STEEL`, `OPEN KHATA`) + `IN` receiving/adjustment rows |
| **Bookings** | 300 bookings + 577 booking items — fully-paid advances, part-advance and pure-credit bookings (`SB-BK-…`, `MB NO.41…`), discounts, receive-in accounts + 115 booking→sale allocations |
| **Direct Sales** | 400 sales + 936 items — **Cash** (fully paid), **Credit Customer** (part/unpaid), **Booking Delivery**, **Mixed Transaction**, **Open Khata**; drivers, rent revenue/cost, discounts, payment methods/accounts |
| **Invoices / Bills** | 170 invoices (`MB NO.2xxx` manual + `INV-…` auto) with OPEN / PARTIAL / PAID status |
| **Pending Bills** | 582 receivables — open, part-paid, fully-paid and waived bills linked to their sales/dispatches/bookings |
| **Payments (Receipts)** | 230 client payments — **Receipts** (cash/bank/cheque against bills), **Refunds**, **Material Return** refunds, **Waive-Offs** (+ 10 waive-off records) |
| **Material Returns** | 70 returns (normal & booked) + 147 return items with rent rates and refund payments |
| **Delivery** | 30 delivery slips + items, 311 driver rent rows, 311 driver allocations, 48 driver payments |
| **Accounts (Khata)** | 7 accounts (Main Cash, Petty Cash, Meezan, HBL, Alfalah, EasyPaisa, JazzCash) with the full classification hierarchy + **909 account transactions** (receipts, payments, supplier payments, transfers, expenses, refunds, driver payments, owner capital) — closing balances match the ledger |
| **Cash Flow** | 8 categories, 17 subcategories, 10 parties, 140 manual cash-flow entries (in / out / transfer, linked ledger transactions), 20 audit rows |
| **Reconciliation** | 12 daily physical-cash reconciliations (+ audit) and 8 account reconciliations with the full carry chain |
| **Cash Drawer** | 10 categories, 90 drawer in/out entries (2 voided) |
| **Rentals (FBM)** | 5 rental items, 6 rental clients, 12 active/returned rentals with payments |
| **Notifications / Follow-ups** | 3 staff emails, 8 reminders, 13 contact logs |
| **Data Lab** | 6 reconciliation-basket rows |
| **Audit** | 12 audit-log + 12 accounting-audit-log rows, 3 sale drafts |
| **Bill Counters** | `GEN/SL/BK/CP/SP/RTN/GRN/EN` advanced past every dummy bill number so future auto-numbering never collides |

Also included on purpose: a few **voided** sales, bookings, payments, GRNs,
returns, drawer and cash-flow rows so void/restore flows have samples.

## Files

| File | Purpose |
|---|---|
| `AMS_DUMMY_DATA_FULL.xlsx` | The importable workbook (this is the file you import) |
| `generate_dummy_data.py` | Regenerates the workbook from the live schema (`python3 dummy_data/generate_dummy_data.py`) |
| `verify_import.py` | Round-trips the workbook through the real import engine into a throwaway DB and asserts 0 failures (`APP_DB_PATH=/tmp/x/app.db python3 dummy_data/verify_import.py`) |

## Notes

* Sheet names = physical table names; headers = physical column names; dates are
  portable ISO strings; booleans are 0/1 — exactly what the import engine expects.
* `settings`, `schema_version`, `system_lock`, `user` (kept headers-only so your
  login/users are never altered), import/migration bookkeeping and backup tables
  are intentionally **not** shipped, so an Overwrite restore can never erase
  configuration or users.
* Client ids start at 200 and category ids at 200 so they can never collide with
  the rows the app auto-seeds on a fresh database (OPEN-KHATA client, General
  category).
