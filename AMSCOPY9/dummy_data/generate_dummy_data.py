#!/usr/bin/env python3
"""
AMS (Fazal Building Materials) — Dummy Data Generator
=====================================================

Builds a single XLSX workbook that can be imported 100% through the app's
built-in  Import & Export  →  "Import Full XLSX"  option (the Literal Full
Raw restore path).  The workbook uses exactly the same format the app's own
"Export Full XLSX" produces:

  * one sheet per physical database table (sheet name = table name)
  * headers = exact physical column names
  * `__AMS_META__` sheet with export_kind = literal_all
  * date/time values in portable ISO strings
  * booleans as 0/1

Contents (minimum 100 clients — 120 here):
  clients, materials + categories, suppliers, delivery persons, accounts,
  GRNs (stock receiving, cash & credit) + items + FIFO allocations,
  supplier payments, dispatch entries (IN/OUT), bookings + items +
  allocations (cash & credit, booked), direct sales + items (cash, credit,
  booked delivery, mixed, open khata), invoices, pending bills, client
  payments (receipts / refunds / material-return / waive-off), waive-offs,
  material returns + items (normal & booked), deliveries + items,
  delivery rents, driver allocations + driver payments, account
  transactions (full cash flow ledger), cash-flow categories /
  subcategories / parties / entries (+audit), cash drawer entries +
  categories, physical-cash reconciliations, account reconciliations,
  FBM rentals (items, clients, rentals), staff emails, follow-ups,
  recon basket, audit logs, sale drafts and bill counters.

Run:  python3 dummy_data/generate_dummy_data.py
Out:  dummy_data/AMS_DUMMY_DATA_FULL.xlsx
"""

from __future__ import annotations

import os
import sys
import json
import random
from datetime import datetime, timedelta, date

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, REPO_ROOT)

from app import create_app  # noqa: E402
from models import db  # noqa: E402
from sqlalchemy import ForeignKeyConstraint  # noqa: E402

random.seed(20260826)

BASE_NOW = datetime.now().replace(minute=0, second=0, microsecond=0)
OUT_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "AMS_DUMMY_DATA_FULL.xlsx")

# ----------------------------------------------------------------------------
# helpers
# ----------------------------------------------------------------------------


def ago(days: float, hour=None, minute=None) -> datetime:
    """Timestamp `days` back from now (naive, PK local time)."""
    dt = BASE_NOW - timedelta(days=days)
    if hour is not None:
        dt = dt.replace(hour=int(hour) % 24)
    if minute is not None:
        dt = dt.replace(minute=int(minute) % 60)
    return dt.replace(second=random.randint(0, 59), microsecond=0)


def money(x) -> float:
    return round(float(x) + 1e-9, 2)


DATA = {}  # table_name -> list[dict]


def put(table: str, **kw):
    DATA.setdefault(table, []).append(kw)


def next_id(table: str) -> int:
    return len(DATA.get(table, [])) + 1



def pk_now_str(dt=None) -> str:
    return (dt or BASE_NOW).strftime("%Y-%m-%d %H:%M")


# ----------------------------------------------------------------------------
# reference pools
# ----------------------------------------------------------------------------

FIRST = ["Muhammad", "Ahmed", "Ali", "Hassan", "Hussain", "Usman", "Umar", "Bilal",
         "Hamza", "Imran", "Kashif", "Naveed", "Rizwan", "Shahid", "Zahid", "Faisal",
         "Adnan", "Tariq", "Waqas", "Yasin", "Zubair", "Salman", "Saqib", "Junaid",
         "Kamran", "Danish", "Fahad", "Sohail", "Ahsan", "Asif", "Ijaz", "Sajid",
         "Nadeem", "Arshad", "Rashid", "Nazir", "Shakir", "Mukhtar", "Abdul Rehman",
         "Ghulam Mustafa"]
LAST = ["Khan", "Butt", "Malik", "Sheikh", "Chaudhry", "Awan", "Rana", "Cheema",
        "Sandhu", "Gill", "Bhatti", "Rajput", "Qureshi", "Siddiqui", "Hashmi",
        "Farooqi", "Zaidi", "Baig", "Mirza", "Abbasi", "Satti", "Amin", "Tarar",
        "Joyia"]
BIZ1 = ["Al-Madina", "Bismillah", "Al-Karam", "Al-Fateh", "Shahbaz", "Baba Farid",
        "Data Sahib", "Ghousia", "Hafiz", "Iqbal", "Jamia", "Khadija", "Madni",
        "Noor", "Qadir", "Rehman", "Sadiq", "Tayyab", "Umar Farooq", "Zaitoon",
        "New Iqbal", "Pak", "Ideal", "Royal", "Star", "Crescent", "National",
        "Central", "Citi", "Metro"]
BIZ2 = ["Traders", "Hardware & Paint Store", "Construction Co.", "Builders",
        "Suppliers", "Building Materials", "Hardware Store", "Enterprises",
        "Sanitary & Hardware", "Steel House", "Cement Store", "Depot"]
AREAS = ["Mohallah Islamia Street No.3", "Muhammadi Colony Near Jamia Masjid",
         "Katchery Road Opposite DC Office", "Railway Road Near Old Station",
         "Main Bazaar in front of Bank", "Sabzi Mandi Road", "Grourney Road",
         "Jinnah Colony Block-A", "Shahbazpur Road", "Chak No.12 NB",
         "Chak No.48 NB", "Lahore Road Near Petrol Pump"]
TOWNS = ["Jalalpur Soibtian", "Bhalwal", "Sargodha", "Kot Momin", "Shahpur",
         "Miani", "Bhera", "Khushab Road"]
CLIENT_CATS = ["General", "Cement Customer", "Steel Customer", "Contractor",
               "Retailer", "Dealer", "Bricks Customer"]

PHONE_PREFIX = ["0300", "0301", "0302", "0307", "0311", "0312", "0313", "0321",
                "0322", "0331", "0332", "0333", "0334", "0335", "0336", "0345",
                "0347", "0349"]


def phone() -> str:
    return f"{random.choice(PHONE_PREFIX)}-{random.randint(2000000, 9899999):07d}"


def make_name() -> str:
    return f"{random.choice(FIRST)} {random.choice(LAST)}"


def make_biz() -> str:
    return f"{random.choice(BIZ1)} {random.choice(BIZ2)}"


# ----------------------------------------------------------------------------
# 1. material categories + materials
# ----------------------------------------------------------------------------

CATS = ["Cement", "Steel & Girders", "Bricks", "Sand & Crush", "Concrete Blocks",
        "Tiles & Marble", "Paint & Distemper", "Hardware & Tools", "Plumbing",
        "Electrical"]
MCAT_ID_BASE = 200  # fresh DBs auto-seed a 'General' category with id=1
for i, cname in enumerate(CATS, MCAT_ID_BASE):
    put("material_category", id=i, name=cname, is_active=1, created_at=ago(200, 9))
CAT_ID = {c: i for i, c in enumerate(CATS, MCAT_ID_BASE)}

# (name, category, unit, unit_price)
MATERIALS = [
    ("DG Cement OPC (Bags)", "Cement", "Bags", 1355),
    ("Lucky Cement OPC (Bags)", "Cement", "Bags", 1348),
    ("Maple Leaf OPC (Bags)", "Cement", "Bags", 1332),
    ("Bestway Cement OPC (Bags)", "Cement", "Bags", 1326),
    ("Pakcem Cement (Bags)", "Cement", "Bags", 1312),
    ("Flying Cement (Bags)", "Cement", "Bags", 1288),
    ("DG Cement SRC (Bags)", "Cement", "Bags", 1322),
    ("Ittefaq Steel 3/8 (Ton)", "Steel & Girders", "Ton", 268500),
    ("Mughal Steel 3/8 (Ton)", "Steel & Girders", "Ton", 271000),
    ("Amreli Steel 4/8 (Ton)", "Steel & Girders", "Ton", 264000),
    ("MM Steel 3/8 (Ton)", "Steel & Girders", "Ton", 262500),
    ("Ittefaq City 3/8 (Ton)", "Steel & Girders", "Ton", 266000),
    ("Girder 4x4 (FT)", "Steel & Girders", "FT", 1150),
    ("Girder 6x6 (FT)", "Steel & Girders", "FT", 2150),
    ("C-Channel 4inch (FT)", "Steel & Girders", "FT", 980),
    ("Awwal Brick (Pcs)", "Bricks", "Pcs", 23),
    ("Doem Brick (Pcs)", "Bricks", "Pcs", 20),
    ("Nihai Brick (Pcs)", "Bricks", "Pcs", 17),
    ("Chenab Sand (CFT)", "Sand & Crush", "CFT", 68),
    ("Ravi Sand (CFT)", "Sand & Crush", "CFT", 56),
    ("Sargodha Crush (CFT)", "Sand & Crush", "CFT", 96),
    ("Margalla Crush (CFT)", "Sand & Crush", "CFT", 112),
    ("Solid Block 6inch (Pcs)", "Concrete Blocks", "Pcs", 96),
    ("Hollow Block 8inch (Pcs)", "Concrete Blocks", "Pcs", 115),
    ("Solid Block 4inch (Pcs)", "Concrete Blocks", "Pcs", 78),
    ("Floor Tile 24x24 (Pcs)", "Tiles & Marble", "Pcs", 148),
    ("Wall Tile 12x18 (Pcs)", "Tiles & Marble", "Pcs", 88),
    ("Ziarat White Marble (CFT)", "Tiles & Marble", "CFT", 1850),
    ("Diamond Emulsion Paint (Ltr)", "Paint & Distemper", "Ltr", 1460),
    ("Brighto Weather Coat (Ltr)", "Paint & Distemper", "Ltr", 1620),
    ("Master Distemper (Bag)", "Paint & Distemper", "Bag", 1750),
    ("Cement Nail (Kg)", "Hardware & Tools", "Kg", 385),
    ("Door Hinge 4inch (Pcs)", "Hardware & Tools", "Pcs", 125),
    ("Tower Bolt 8inch (Pcs)", "Hardware & Tools", "Pcs", 255),
    ("Door Handle Set (Pcs)", "Hardware & Tools", "Pcs", 640),
    ("PVC Pipe 1inch (FT)", "Plumbing", "FT", 215),
    ("PVC Pipe 2inch (FT)", "Plumbing", "FT", 420),
    ("PVC Elbow 1inch (Pcs)", "Plumbing", "Pcs", 88),
    ("Bib Cock Tap (Pcs)", "Plumbing", "Pcs", 365),
    ("Copper Wire 3/29 Coil (Pcs)", "Electrical", "Pcs", 12850),
    ("2-Gang Switch Board (Pcs)", "Electrical", "Pcs", 485),
    ("Energy Saver Bulb (Pcs)", "Electrical", "Pcs", 520),
]
for i, (name, cat, unit, price) in enumerate(MATERIALS, 1):
    put("material",
        id=i, code=f"MAT-{1000 + i}", name=name, category_id=CAT_ID[cat],
        unit_price=money(price), total=0, unit=unit, is_active=1,
        created_at=ago(190 - i, 9, 15))
MAT = {m[0]: (i, m[2], m[3]) for i, m in enumerate(MATERIALS, 1)}  # name -> (id, unit, price)
MAT_NAMES = list(MAT.keys())
CEMENT_NAMES = [m[0] for m in MATERIALS if m[1] == "Cement"]
STEEL_NAMES = [m[0] for m in MATERIALS if m[1] == "Steel & Girders"]

# ----------------------------------------------------------------------------
# 2. clients (120)
# ----------------------------------------------------------------------------

N_CLIENTS = 120
CLIENT_ID_BASE = 200  # avoid ids the app auto-seeds on fresh databases
used_names = set()
for i in range(1, N_CLIENTS + 1):
    if i <= 70:
        name = make_biz()
        while name in used_names:
            name = make_biz()
    else:
        name = make_name()
        while name in used_names:
            name = f"{random.choice(FIRST)} {random.choice(LAST)} {random.choice(['S/o Ghulam','S/o Muhammad','Junior'])}"
    used_names.add(name)
    cat = random.choice(CLIENT_CATS)
    opening = random.choice([0, 0, 0, 5000, 12000, 18500, 25000, 40000])
    open_dt = ago(random.randint(170, 400), 11)
    put("client",
        id=CLIENT_ID_BASE + i - 1,
        code=f"FBMCL-{i:05d}",
        name=name,
        phone=phone(),
        address=f"{random.choice(AREAS)}, {random.choice(TOWNS)}",
        category=cat,
        opening_balance=money(opening),
        opening_balance_date=open_dt,
        is_active=0 if i == 118 else 1,
        transferred_to_id=None,
        require_manual_invoice=1 if i % 17 == 0 else 0,
        book_no=f"B-{(i - 1) // 20 + 1}",
        financial_page=f"P-{random.randint(1, 60)}",
        cement_page=f"P-{random.randint(1, 30)}",
        steel_page=f"P-{random.randint(1, 25)}",
        financial_book_no=f"FB-{(i - 1) // 30 + 1}",
        cement_book_no=f"CB-{(i - 1) // 40 + 1}",
        steel_book_no=f"SB-{(i - 1) // 40 + 1}",
        location_url=f"https://maps.google.com/?q={random.uniform(31.5, 32.3):.6f},{random.uniform(72.0, 73.2):.6f}",
        page_notes=random.choice(["", "", "Regular weekly payment client",
                                  "Collect payment every Friday",
                                  "Prefers Lucky Cement only",
                                  "Site delivery before 10am"]),
        created_at=open_dt)
# special OPEN KHATA walk-in account used by the app
OPEN_KHATA_ID = CLIENT_ID_BASE + N_CLIENTS + 1
put("client",
    id=OPEN_KHATA_ID, code="OPEN-KHATA", name="OPEN KHATA", phone="", address="",
    category="Open Khata", opening_balance=0, opening_balance_date=ago(180, 9),
    is_active=1, transferred_to_id=None, require_manual_invoice=0,
    book_no="", financial_page="", cement_page="", steel_page="",
    financial_book_no="", cement_book_no="", steel_book_no="",
    location_url="", page_notes="Walk-in open khata (system)", created_at=ago(180, 9))
CLIENT_IDS = list(range(CLIENT_ID_BASE, CLIENT_ID_BASE + N_CLIENTS))
client_name_of = {r["id"]: r["name"] for r in DATA["client"]}
client_code_of = {r["id"]: r["code"] for r in DATA["client"]}
client_cat_of = {r["id"]: r["category"] for r in DATA["client"]}

# ----------------------------------------------------------------------------
# 3. suppliers
# ----------------------------------------------------------------------------

SUPPLIERS = [
    "Bestway Cement Depot Rawalpindi", "Lucky Cement Distribution Sargodha",
    "Maple Leaf Bulk Stock Bhalwal", "Mughal Steel Mills Dealer",
    "Ittefaq Steel Trader Lahore", "Amreli Steel Distributor Faisalabad",
    "Chenab Sand Supplier Miani", "Sargodha Crush Supplier Kohistan",
    "Awwal Brick Kiln Kot Momin", "Al-Karam Tiles Wholesaler",
    "Diamond Paints Dealer Sargodha", "PVC Pipes & Sanitary Traders",
]
for i, s in enumerate(SUPPLIERS, 1):
    put("supplier",
        id=i, name=s, phone=phone(),
        address=f"{random.choice(AREAS)}, {random.choice(TOWNS)}",
        opening_balance=money(random.choice([0, 0, 15000, 52000, 88000])),
        opening_balance_date=ago(random.randint(170, 300), 10),
        is_active=1, created_at=ago(random.randint(170, 300), 10))
SUP_IDS = list(range(1, len(SUPPLIERS) + 1))

# ----------------------------------------------------------------------------
# 4. delivery persons
# ----------------------------------------------------------------------------

DRIVERS = ["Muhammad Ashraf (Riksha)", "Sajjad Hussain (Suzuki)", "Nasir Ali (Truck)",
           "Zulfiqar Khan (Suzuki)", "Imran Sadiq (Truck)", "Waheed Murad (Riksha)",
           "Shakeel Ahmed (Suzuki)", "Younas Butt (Truck)"]
for i, d in enumerate(DRIVERS, 1):
    put("delivery_person",
        id=i, name=d, phone=phone(),
        opening_balance=money(random.choice([0, 0, 500, 1200, -300])),
        opening_balance_date=ago(random.randint(150, 300), 9),
        is_active=1, created_at=ago(random.randint(150, 300), 9))
DRIVER_IDS = list(range(1, len(DRIVERS) + 1))

# ----------------------------------------------------------------------------
# 5. accounts (chart of accounts) + categories
# ----------------------------------------------------------------------------

for i, nm in enumerate(["Operations", "Salaries & Wages", "Utilities", "Transport"], 1):
    put("account_category", id=i, name=nm,
        note=f"Business grouping for {nm.lower()} flows", is_active=1, created_at=ago(200, 9))

# (id, name, channel, class_sub, class_type, opening, extra)
ACCOUNTS = [
    (1, "Main Cash", "cash", "Cash", "Main Cash", 250000,
     {"cash_location": "Shop counter drawer", "cash_responsible": "Rehman Ahmed"}),
    (2, "Petty Cash", "cash", "Cash", "Petty Cash", 35000,
     {"cash_location": "Office small box", "cash_responsible": "Admin"}),
    (3, "Meezan Bank Current", "bank", "Bank", "Operating Bank", 850000,
     {"bank_name": "Meezan Bank", "account_holder_name": "Fazal Building Materials",
      "account_number": "PK36MEZN0001230123456789", "branch_code": "0123"}),
    (4, "HBL Current", "bank", "Bank", "Collection Bank", 420000,
     {"bank_name": "Habib Bank Limited", "account_holder_name": "Fazal Building Materials",
      "account_number": "1234-5678-9012-3456", "branch_code": "0456"}),
    (5, "Bank Alfalah Savings", "bank", "Bank", "Savings Bank", 300000,
     {"bank_name": "Bank Alfalah", "account_holder_name": "Rehman Ahmed",
      "account_number": "5544-8899-0022", "branch_code": "0789"}),
    (6, "EasyPaisa Wallet", "digital_wallet", "Digital Wallet", "Mobile Wallet", 60000,
     {"wallet_provider": "EasyPaisa", "wallet_number": "0345-1234567",
      "wallet_holder": "Rehman Ahmed"}),
    (7, "JazzCash Wallet", "digital_wallet", "Digital Wallet", "Payment App", 45000,
     {"wallet_provider": "JazzCash", "wallet_number": "0300-7654321",
      "wallet_holder": "Fazal Building Materials"}),
]
for acc in ACCOUNTS:
    aid, name, channel, sub, typ, opening, extra = acc
    put("account",
        id=aid, name=name,
        type=typ,                       # legacy back-compat column
        category=channel,               # legacy back-compat column
        source_category=None,
        account_type=typ,
        balance=money(opening),         # recomputed below after ledger build
        balance_minor=int(opening * 100),
        opening_balance=money(opening),
        opening_balance_minor=int(opening * 100),
        opening_balance_date=ago(200, 9),
        is_active=1,
        class_category="Assets", class_subcategory=sub, class_account_type=typ,
        channel=channel,
        linked_entity_type="none", linked_client_id=None, linked_supplier_id=None,
        linked_party_name=None,
        account_status="active",
        note=f"{typ} account — opening balance carried from 200 days ago",
        created_at=ago(200, 9), updated_by="Admin",
        **extra)
ACC_IDS = [a[0] for a in ACCOUNTS]
MAIN_CASH, PETTY_CASH, MEZAN, HBL, ALFALAH, EASYPAISA, JAZZCASH = ACC_IDS
CASH_ACCOUNTS = [MAIN_CASH, PETTY_CASH]
BANK_ACCOUNTS = [MEZAN, HBL, ALFALAH]
WALLET_ACCOUNTS = [EASYPAISA, JAZZCASH]

# running balances (authoritative ledger rebuilt below)
acc_balance = {a[0]: float(a[5]) for a in ACCOUNTS}
acc_min_balance = {a[0]: float(a[5]) for a in ACCOUNTS}  # lowest running point
acc_tx_no = [0]  # global counter for description refs

# ----------------------------------------------------------------------------
# account transaction ledger helpers (single source of truth for balances)
# ----------------------------------------------------------------------------

TX = []  # account_transaction rows get collected via acct_tx()


def acct_tx(from_id, to_id, amount, tx_type, description, dt, note,
            source_type=None, source_id=None, **kw):
    amount = money(amount)
    tx_id = kw.pop("id", None)
    if tx_id is None:
        acc_tx_no[0] += 1
        tx_id = acc_tx_no[0]
    if from_id:
        acc_balance[from_id] -= amount
    if to_id:
        acc_balance[to_id] += amount
    for aid in (from_id, to_id):
        if aid:
            acc_min_balance[aid] = min(acc_min_balance[aid], acc_balance[aid])
    put("account_transaction",
        id=tx_id,
        from_account_id=from_id, to_account_id=to_id,
        amount=amount, amount_minor=int(amount * 100),
        description=description[:200], date_posted=dt,
        is_void=0, note=note,
        transaction_type=tx_type, source_type=source_type, source_id=source_id,
        reconciliation_id=None, reason=None, idempotency_key=None,
        created_by="Admin", voided_by=None, voided_at=None,
        created_at=dt, **kw)
    return tx_id


# ----------------------------------------------------------------------------
# 6. GRN (material receiving) + items + FIFO allocations + supplier payments
# ----------------------------------------------------------------------------

grn_item_pool = []       # (grn_item_id, mat_name, remaining_qty, rate, grn_dt)
supplier_open_balance = {sid: {} for sid in SUP_IDS}  # supplier -> {grn_id: remaining}

GRN_N = 60
for i in range(1, GRN_N + 1):
    gid = i
    sup_id = random.choice(SUP_IDS)
    dt = ago(random.randint(2, 170), random.randint(8, 18), random.choice([5, 20, 35, 50]))
    # pick 1-4 materials appropriate to the supplier type
    if "Cement" in SUPPLIERS[sup_id - 1]:
        pool = CEMENT_NAMES
    elif "Steel" in SUPPLIERS[sup_id - 1]:
        pool = STEEL_NAMES
    elif "Sand" in SUPPLIERS[sup_id - 1] or "Crush" in SUPPLIERS[sup_id - 1]:
        pool = [m for m in MAT_NAMES if "Sand" in m or "Crush" in m]
    elif "Brick" in SUPPLIERS[sup_id - 1]:
        pool = [m for m in MAT_NAMES if "Brick" in m]
    elif "Tile" in SUPPLIERS[sup_id - 1]:
        pool = [m for m in MAT_NAMES if "Tile" in m or "Marble" in m]
    elif "Paint" in SUPPLIERS[sup_id - 1]:
        pool = [m for m in MAT_NAMES if "Paint" in m or "Distemper" in m]
    elif "PVC" in SUPPLIERS[sup_id - 1]:
        pool = [m for m in MAT_NAMES if "PVC" in m or "Tap" in m]
    else:
        pool = MAT_NAMES
    items = random.sample(pool, k=min(len(pool), random.randint(1, 4)))
    is_void = 1 if i in (58, 59) else 0
    pay_type = random.choices(["Cash", "Credit", "Cheque"], weights=[38, 52, 10])[0]
    put("grn",
        id=gid, supplier_id=sup_id, supplier=SUPPLIERS[sup_id - 1],
        manual_bill_no=f"MB NO.{7000 + i}" if i % 3 else None,
        auto_bill_no=f"SB-GRN-{1000 + i}",
        photo_path=None, photo_url=None,
        loading_cost=money(random.choice([0, 0, 500, 800, 1200])),
        freight_cost=money(random.choice([0, 1500, 2500, 4000, 6500])),
        other_expense=money(random.choice([0, 0, 200, 350])),
        adjustment_amount=0, discount=money(random.choice([0, 0, 0, 500, 1000])),
        paid_amount=0,  # filled below
        payment_type=pay_type,
        payment_account_id=None,  # filled below
        tax_percent=0, tax_amount=0, tax_type=None,
        bank_name=(random.choice(["Meezan Bank", "Habib Bank Limited"]) if pay_type == "Cheque" else None),
        account_name=("Fazal Building Materials" if pay_type == "Cheque" else None),
        account_no=(f"{random.randint(1000, 9999)}-8877" if pay_type == "Cheque" else None),
        supplier_invoice_no=f"SINV-{random.randint(10000, 99999)}",
        due_date=(dt + timedelta(days=random.choice([7, 15, 30]))).date() if pay_type == "Credit" else None,
        bill_date=dt.date(), date_posted=dt,
        is_void=is_void,
        note=random.choice(["Truck loaded from mill", "Direct factory supply",
                            "", "Weight bridge slip attached", "Rate as per deal"]))
    total = 0.0
    for mi, mname in enumerate(items):
        _, unit, price = MAT[mname]
        qty = random.choice([100, 200, 300, 400, 500, 600, 800, 1000]) if unit == "Bags" \
            else random.choice([5, 10, 15, 20, 25]) if unit == "Ton" \
            else random.choice([50, 100, 150, 200, 300, 500])
        rate = money(price * random.uniform(0.93, 1.0))
        item_total = money(qty * rate)
        total += item_total
        put("grn_item",
            id=next_id("grn_item"),
            grn_id=gid, mat_name=mname, qty=qty, price_at_time=rate,
            is_void=is_void, is_locked=0)
        if not is_void:
            grn_item_pool.append((len(DATA["grn_item"]), mname, qty, rate, dt))
    total = money(total + DATA["grn"][-1]["loading_cost"] + DATA["grn"][-1]["freight_cost"]
                  + DATA["grn"][-1]["other_expense"] + DATA["grn"][-1]["tax_amount"]
                  - DATA["grn"][-1]["discount"])
    DATA["grn"][-1]["supplier_id"] = sup_id
    if is_void:
        DATA["grn"][-1]["paid_amount"] = 0
        continue
    if pay_type == "Cash":
        acc = random.choice(CASH_ACCOUNTS if random.random() < 0.7 else BANK_ACCOUNTS)
        DATA["grn"][-1]["paid_amount"] = total
        DATA["grn"][-1]["payment_account_id"] = acc
        acct_tx(acc, None, total, "Payment",
                f"GRN {DATA['grn'][-1]['auto_bill_no']} — {SUPPLIERS[sup_id - 1]}",
                dt, f"[SRC:GRN:{gid}] payment for stock receiving",
                source_type="GRN", source_id=gid)
    elif pay_type == "Cheque":
        acc = random.choice([MEZAN, HBL])
        DATA["grn"][-1]["paid_amount"] = total
        DATA["grn"][-1]["payment_account_id"] = acc
        acct_tx(acc, None, total, "Payment",
                f"GRN {DATA['grn'][-1]['auto_bill_no']} cheque — {SUPPLIERS[sup_id - 1]}",
                dt, f"[SRC:GRN:{gid}] cheque payment",
                source_type="GRN", source_id=gid)
    else:  # Credit — full or partial open balance for supplier payment later
        if random.random() < 0.35:
            part = money(total * random.uniform(0.2, 0.5))
            acc = random.choice(CASH_ACCOUNTS)
            DATA["grn"][-1]["paid_amount"] = part
            DATA["grn"][-1]["payment_account_id"] = acc
            acct_tx(acc, None, part, "Payment",
                    f"GRN {DATA['grn'][-1]['auto_bill_no']} advance — {SUPPLIERS[sup_id - 1]}",
                    dt, f"[SRC:GRN:{gid}] part payment",
                    source_type="GRN", source_id=gid)
        supplier_open_balance[sup_id][gid] = money(
            total - float(DATA["grn"][-1]["paid_amount"] or 0))

# supplier payments against remaining GRN credit
sp_seq = 1000
sp_id = 0
for sup_id, open_map in supplier_open_balance.items():
    for gid, remaining in list(open_map.items()):
        if remaining <= 0 or random.random() < 0.18:
            continue  # some supplier credit intentionally still open
        pay = money(remaining if random.random() < 0.6 else remaining * random.uniform(0.3, 0.7))
        if pay < 1:
            continue
        method = random.choices(["Cash", "Bank", "Check"], weights=[50, 40, 10])[0]
        grn_dt = DATA["grn"][gid - 1]["date_posted"]
        max_lag = max(1, int((BASE_NOW - grn_dt).days) - 1)
        dt = grn_dt + timedelta(days=random.randint(1, max(1, min(45, max_lag))),
                                hours=random.randint(0, 6))
        if dt > BASE_NOW:
            dt = BASE_NOW - timedelta(hours=random.randint(1, 48))
        acc = random.choice(CASH_ACCOUNTS if method == "Cash" else BANK_ACCOUNTS)
        sp_seq += 1
        sp_id += 1
        manual = f"MB NO.{8200 + sp_id}" if sp_id % 4 == 0 else None
        bank_name, acct_name, acct_no = (None, None, None)
        if method == "Check":
            bank_name, acct_name, acct_no = "Habib Bank Limited", "Fazal Building Materials", "3312-778899"
        put("supplier_payment",
            id=sp_id, supplier_id=sup_id, amount=pay,
            amount_minor=int(pay * 100), method=method, payment_type="Payment",
            source_type="GRN", source_id=gid,
            date_posted=dt,
            note=f"Payment against GRN {DATA['grn'][gid - 1]['auto_bill_no']} ({SUPPLIERS[sup_id - 1]})",
            is_void=0, bank_name=bank_name, account_name=acct_name, account_no=acct_no,
            payment_account_id=acc, manual_bill_no=manual,
            auto_bill_no=f"SB-SP-{sp_seq}",
            idempotency_key=None, idempotency_payload_hash=None, revision=1,
            created_by="Admin", updated_by="Admin",
            created_at=dt, updated_at=dt)
        acct_tx(acc, None, pay, "Supplier Payment",
                f"Supplier payment — {SUPPLIERS[sup_id - 1]}",
                dt, f"[SRC:SupplierPayment:{sp_id}] GRN:{DATA['grn'][gid - 1]['auto_bill_no']}",
                source_type="SupplierPayment", source_id=sp_id)
        open_map[gid] = money(remaining - pay)

# ----------------------------------------------------------------------------
# 7. cash drawer (FBM) categories + entries
# ----------------------------------------------------------------------------

DRAWER_CATS = ["Site Expense", "Tea & Refreshment", "Fuel", "Loading Labor",
               "Salary Advance", "Utility Bill", "Repair & Maintenance",
               "Miscellaneous", "Owner Deposit", "Extra Collection"]
for i, c in enumerate(DRAWER_CATS, 1):
    put("fbm_cash_drawer_category", id=i, name=c, is_active=1, created_at=ago(190, 9))

drawer_in_total = 0.0
for i in range(1, 91):
    is_out = random.random() < 0.62
    dt = ago(random.randint(0, 170), random.randint(8, 19), random.choice([10, 25, 40, 55]))
    if is_out:
        cat = random.choice(DRAWER_CATS[:8])
        amt = money(random.choice([150, 300, 500, 800, 1200, 2500, 3500]))
    else:
        cat = random.choice(DRAWER_CATS[8:])
        amt = money(random.choice([5000, 10000, 15000, 20000, 30000]))
    method = random.choices(["Cash", "Bank", "Check"], weights=[80, 15, 5])[0]
    is_void = 1 if i in (89, 90) else 0
    put("fbm_cash_drawer_entry",
        id=i, entry_type="out" if is_out else "in", amount=amt, category=cat,
        method=method,
        note=random.choice(["", "Petty site purchase", "Weekly collection",
                            "Loader tea & lunch", "Generator diesel", ""]),
        source="manual",
        date_posted=dt, created_by="Admin", is_void=is_void)
    # cash drawer 'in' deposits move cash into the drawer account; 'out' spends it
    if not is_void and method == "Cash":
        if is_out:
            acct_tx(PETTY_CASH, None, amt, "Expense",
                    f"Cash drawer out — {cat}", dt, f"[SRC:CashDrawer:{i}]",
                    source_type="CashDrawer", source_id=i)
        else:
            acct_tx(None, MAIN_CASH, amt, "Receipt",
                    f"Cash drawer in — {cat}", dt, f"[SRC:CashDrawer:{i}]",
                    source_type="CashDrawer", source_id=i)

# ----------------------------------------------------------------------------
# 8. cash flow config + entries (+ linked account transactions)
# ----------------------------------------------------------------------------

CF_CATS = [
    ("Sales Collection", "in"), ("Supplier Payment", "out"),
    ("Operating Expense", "out"), ("Salary & Wages", "out"),
    ("Utility Bills", "out"), ("Rent & Lease", "out"),
    ("Owner Drawings", "out"), ("Bank / Wallet Transfer", "both"),
]
for i, (nm, dr) in enumerate(CF_CATS, 1):
    put("cash_flow_category", id=i, name=nm, direction=dr, is_active=1,
        sort_order=i, notes=None, created_at=ago(190, 9), updated_at=ago(190, 9))
CF_CAT_ID = {nm: i for i, (nm, _) in enumerate(CF_CATS, 1)}

CF_SUBS = [
    ("Operating Expense", "Fuel & Diesel"), ("Operating Expense", "Loading Labor"),
    ("Operating Expense", "Tea & Refreshment"), ("Operating Expense", "Repairs"),
    ("Salary & Wages", "Staff Salary"), ("Salary & Wages", "Driver Salary"),
    ("Utility Bills", "WAPDA Electricity"), ("Utility Bills", "Sui Gas"),
    ("Utility Bills", "Internet & Phone"), ("Rent & Lease", "Shop Rent"),
    ("Rent & Lease", "Vehicle Rent"), ("Sales Collection", "Counter Collection"),
    ("Sales Collection", "Online Collection"), ("Bank / Wallet Transfer", "Cash to Bank"),
    ("Bank / Wallet Transfer", "Bank to Wallet"), ("Supplier Payment", "On-Account Payment"),
    ("Owner Drawings", "Owner Drawing"),
]
for i, (cat, sub) in enumerate(CF_SUBS, 1):
    put("cash_flow_subcategory", id=i, category_id=CF_CAT_ID[cat], name=sub,
        is_active=1, notes=None, created_at=ago(190, 9), updated_at=ago(190, 9))
CF_SUB_ID = {s: i for i, (_, s) in enumerate(CF_SUBS, 1)}

CF_PARTIES = [("WAPDA Sargodha", "utility"), ("Sui Northern Gas", "utility"),
              ("PTCL / Telenor", "utility"), ("Fuel Station Malik", "business"),
              ("Labor Party Munshi", "person"), ("Owner Rehman Ahmed", "owner"),
              ("Staff Salary Pool", "group"), ("Shop Landlord Haji Sahib", "person"),
              ("Loaders Team", "group"), ("Generator Mechanic Sadiq", "person")]
for i, (nm, typ) in enumerate(CF_PARTIES, 1):
    put("cash_flow_party", id=i, name=nm, party_type=typ,
        phone=phone() if typ in ("person", "business") else None,
        note=None, is_active=1, created_at=ago(190, 9), updated_at=ago(190, 9))
CF_PARTY_ID = {nm: i for i, (nm, _) in enumerate(CF_PARTIES, 1)}

cf_ref = 5000
cf_id = 0
for i in range(1, 141):
    direction = random.choices(["in", "out", "transfer"], weights=[30, 52, 18])[0]
    dt = ago(random.randint(0, 165), random.randint(8, 19), random.choice([15, 30, 45]))
    cf_id += 1
    if direction == "in":
        cat, sub = "Sales Collection", random.choice(["Counter Collection", "Online Collection"])
        amount = money(random.choice([5000, 8000, 12000, 20000, 35000, 50000]))
        acc = random.choice([MAIN_CASH, MEZAN, HBL, EASYPAISA, JAZZCASH])
        party = random.choice(["Staff Salary Pool"]) if False else None
        party_name = None
        desc = "Manual collection entry"
        acct_tx(None, acc, amount, "Receipt", desc, dt, f"[SRC:MANUAL_CASH_FLOW:{cf_id}]",
                source_type="MANUAL_CASH_FLOW", source_id=cf_id)
        tx_id = acc_tx_no[0]
        cf_ref += 1
        put("cash_flow_entry",
            id=cf_id, direction="in", amount=amount, amount_minor=int(amount * 100),
            account_id=acc, destination_account_id=None,
            category_id=CF_CAT_ID[cat], subcategory_id=CF_SUB_ID[sub],
            party_id=None, party_name=None, party_type=None,
            description=desc, note="", reference=f"CF-{cf_ref}",
            date_posted=dt, created_by="Admin", updated_by=None,
            source_type="MANUAL_CASH_FLOW", source_id=None,
            account_tx_id=tx_id, is_void=0, voided_at=None, voided_by=None,
            void_reason=None, idempotency_key=None, revision=1,
            created_at=dt, updated_at=dt)
    elif direction == "out":
        cat = random.choice(["Operating Expense", "Salary & Wages", "Utility Bills",
                             "Rent & Lease", "Owner Drawings", "Supplier Payment"])
        sub = {"Operating Expense": random.choice(["Fuel & Diesel", "Loading Labor", "Tea & Refreshment", "Repairs"]),
               "Salary & Wages": random.choice(["Staff Salary", "Driver Salary"]),
               "Utility Bills": random.choice(["WAPDA Electricity", "Sui Gas", "Internet & Phone"]),
               "Rent & Lease": random.choice(["Shop Rent", "Vehicle Rent"]),
               "Owner Drawings": "Owner Drawing",
               "Supplier Payment": "On-Account Payment"}[cat]
        amount = money({"Operating Expense": random.choice([800, 1500, 3000, 5500]),
                        "Salary & Wages": random.choice([25000, 38000, 45000]),
                        "Utility Bills": random.choice([6500, 12000, 18500]),
                        "Rent & Lease": random.choice([15000, 25000]),
                        "Owner Drawings": random.choice([20000, 40000, 60000]),
                        "Supplier Payment": random.choice([10000, 25000, 50000])}[cat])
        acc = random.choice(CASH_ACCOUNTS + BANK_ACCOUNTS)
        party_nm = {"Utility Bills": random.choice(["WAPDA Sargodha", "Sui Northern Gas", "PTCL / Telenor"]),
                    "Salary & Wages": "Staff Salary Pool",
                    "Rent & Lease": "Shop Landlord Haji Sahib",
                    "Operating Expense": random.choice(["Fuel Station Malik", "Labor Party Munshi", "Loaders Team"]),
                    "Owner Drawings": "Owner Rehman Ahmed",
                    "Supplier Payment": None}[cat]
        desc = {"Utility Bills": "Monthly utility bill paid",
                "Salary & Wages": "Monthly salary disbursement",
                "Rent & Lease": "Rent payment",
                "Operating Expense": "Site/shop operating expense",
                "Owner Drawings": "Owner personal drawing",
                "Supplier Payment": "On-account supplier payment"}[cat]
        tx_id = acct_tx(acc, None, amount, "Expense", desc, dt,
                        f"[SRC:MANUAL_CASH_FLOW:{cf_id}]",
                        source_type="MANUAL_CASH_FLOW", source_id=cf_id)
        cf_ref += 1
        put("cash_flow_entry",
            id=cf_id, direction="out", amount=amount, amount_minor=int(amount * 100),
            account_id=acc, destination_account_id=None,
            category_id=CF_CAT_ID[cat], subcategory_id=CF_SUB_ID[sub],
            party_id=(CF_PARTY_ID[party_nm] if party_nm else None),
            party_name=party_nm,
            party_type=(next(t for n, t in CF_PARTIES if n == party_nm) if party_nm else None),
            description=desc, note=random.choice(["", "Cash paid at counter", ""]),
            reference=f"CF-{cf_ref}",
            date_posted=dt, created_by="Admin", updated_by=None,
            source_type="MANUAL_CASH_FLOW", source_id=None,
            account_tx_id=tx_id, is_void=1 if cf_id == 139 else 0,
            voided_at=(ago(3, 12) if cf_id == 139 else None),
            voided_by=("Admin" if cf_id == 139 else None),
            void_reason=("Duplicate entry — voided" if cf_id == 139 else None),
            idempotency_key=None, revision=1, created_at=dt, updated_at=dt)
        if cf_id == 139:
            # void the mirror account tx too
            for t in DATA["account_transaction"]:
                if t["id"] == tx_id:
                    t["is_void"] = 1
                    t["voided_by"] = "Admin"
                    t["voided_at"] = ago(3, 12)
            acc_balance[acc] += amount  # undo movement
    else:  # transfer
        amount = money(random.choice([10000, 25000, 50000, 80000]))
        src, dst = random.sample(ACC_IDS, 2)
        desc = random.choice(["Cash deposited to bank", "Bank to wallet top-up",
                              "Cash withdrawal for shop", "Wallet to bank sweep"])
        tx_id = acct_tx(src, dst, amount, "Transfer", desc, dt,
                        f"[SRC:MANUAL_CASH_FLOW:{cf_id}]",
                        source_type="MANUAL_CASH_FLOW", source_id=cf_id)
        cf_ref += 1
        put("cash_flow_entry",
            id=cf_id, direction="transfer", amount=amount, amount_minor=int(amount * 100),
            account_id=src, destination_account_id=dst,
            category_id=CF_CAT_ID["Bank / Wallet Transfer"],
            subcategory_id=random.choice([CF_SUB_ID["Cash to Bank"], CF_SUB_ID["Bank to Wallet"]]),
            party_id=None, party_name=None, party_type=None,
            description=desc, note="", reference=f"CF-{cf_ref}",
            date_posted=dt, created_by="Admin", updated_by=None,
            source_type="MANUAL_CASH_FLOW", source_id=None,
            account_tx_id=tx_id, is_void=0, voided_at=None, voided_by=None,
            void_reason=None, idempotency_key=None, revision=1,
            created_at=dt, updated_at=dt)

# audit rows for a handful of cash flow entries
for i in range(1, 21):
    entry = DATA["cash_flow_entry"][i - 1]
    put("cash_flow_entry_audit",
        id=i, entry_id=entry["id"],
        action=random.choice(["created", "edited", "edited"]),
        before_json=None if i % 2 else json.dumps({"amount": entry["amount"] + 500}),
        after_json=json.dumps({"amount": entry["amount"]}),
        reason="Initial capture" if i % 2 else "Amount corrected from receipt",
        changed_by="Admin", changed_at=entry["date_posted"])

# ----------------------------------------------------------------------------
# 9. physical cash reconciliation (per-day) + audit
# ----------------------------------------------------------------------------

for i in range(1, 13):
    day = (BASE_NOW - timedelta(days=i * 14)).date()
    calculated = money(random.uniform(120000, 260000))
    diff = money(random.choice([0, 0, 0, -250, 400, -1000]))
    physical = money(calculated + diff)
    put("cash_flow_difference_adjustment",
        id=i, adjustment_date=day,
        amount=diff, note="Physical cash matched" if diff == 0 else "Shortage adjusted",
        physical_cash_available=physical, calculated_closing=calculated,
        difference=diff,
        reason="Drawer matched perfectly" if diff == 0 else
               random.choice(["Tea expense not recorded", "Change round-off", "Missing receipt slip"]),
        old_physical_cash=None, edited_by=None, edited_date=None, edit_count=0,
        created_by="Admin", created_at=datetime.combine(day, datetime.min.time()),
        updated_at=datetime.combine(day, datetime.min.time()))
    put("cash_flow_reconciliation_audit",
        id=i, reconciliation_id=i, adjustment_date=day, change_type="CREATE",
        old_physical_cash=None, new_physical_cash=physical,
        old_difference=None, new_difference=diff,
        old_reason=None, new_reason="Day-end reconciliation", changed_by="Admin",
        changed_at=datetime.combine(day, datetime.min.time()))

# ----------------------------------------------------------------------------
# 10. account reconciliations (carry chain)
# ----------------------------------------------------------------------------

prev_id = None
for i, acc in enumerate([MAIN_CASH, MEZAN, HBL, MAIN_CASH, MEZAN, ALFALAH,
                         EASYPAISA, MAIN_CASH], 1):
    rdate = (BASE_NOW - timedelta(days=(8 - i) * 21)).date()
    opening = money(random.uniform(150000, 400000))
    tx_in = money(random.uniform(100000, 500000))
    tx_out = money(random.uniform(80000, 450000))
    net = money(tx_in - tx_out)
    expected = money(opening + net)
    difference = money(random.choice([0, 0, 0, -350, 500]))
    actual = money(expected + difference)
    put("account_reconciliation",
        id=i, account_id=acc, previous_reconciliation_id=prev_id,
        adjustment_transaction_id=None, reconciliation_date=rdate,
        period_start_at=datetime.combine(rdate - timedelta(days=21), datetime.min.time()),
        period_end_at=datetime.combine(rdate, datetime.min.time()),
        previous_balance=opening, opening_balance=opening,
        transaction_in=tx_in, transaction_out=tx_out, transaction_net=net,
        expected_balance=expected, actual_balance=actual, difference=difference,
        adjustment_amount=difference, final_reconciled_balance=actual,
        previous_balance_minor=int(opening * 100), opening_balance_minor=int(opening * 100),
        transaction_in_minor=int(tx_in * 100), transaction_out_minor=int(tx_out * 100),
        transaction_net_minor=int(net * 100), expected_balance_minor=int(expected * 100),
        actual_balance_minor=int(actual * 100), difference_minor=int(difference * 100),
        final_reconciled_balance_minor=int(actual * 100),
        difference_type="Matched" if difference == 0 else ("Loss" if difference < 0 else "Excess"),
        status="Reconciled", note="Monthly physical reconciliation",
        created_by_id=1, created_by="Admin", created_ip="192.168.10.5",
        session_id=None,
        created_at=datetime.combine(rdate, datetime.min.time()),
        updated_at=datetime.combine(rdate, datetime.min.time()))
    prev_id = i

# ----------------------------------------------------------------------------
# 11. bookings (300) + booking items + pending bills + allocations
# ----------------------------------------------------------------------------

bk_manual_seq, bk_auto_seq, bk_id = 4100, 1000, 0
booking_pool = []  # (booking_id, booking_item_id, mat_name, remaining_qty, price, client_id)
booking_bill_of = {}
N_BOOKINGS = 300
for i in range(1, N_BOOKINGS + 1):
    bk_id = i
    cid = random.choice(CLIENT_IDS)
    cname = client_name_of[cid]
    dt = ago(random.randint(0, 165), random.randint(9, 19), random.choice([5, 22, 38, 52]))
    n_items = random.randint(1, 3)
    items = random.sample(MAT_NAMES, k=n_items)
    manual = f"MB NO.{bk_manual_seq + i}" if i % 5 else None
    auto = f"SB-BK-{bk_auto_seq + i}"
    is_void = 1 if i in (299, 300) else 0
    total = 0.0
    for mi, mname in enumerate(items):
        _, unit, price = MAT[mname]
        qty = random.choice([50, 100, 200, 300, 400, 600]) if unit == "Bags" \
            else random.choice([2, 4, 6, 8, 10]) if unit == "Ton" \
            else random.choice([40, 80, 120, 250, 400])
        rate = money(price * random.uniform(0.97, 1.02))
        total += qty * rate
        put("booking_item",
            id=next_id("booking_item"), booking_id=bk_id,
            material_name=mname, qty=qty, price_at_time=rate)
        if not is_void:
            booking_pool.append((bk_id, len(DATA["booking_item"]), mname, qty, rate, cid))
    discount = money(random.choice([0, 0, 0, 0, 500, 1000, 2000]))
    amount = money(total - discount)
    # advance behaviour: paid in full / advance / nothing
    roll = random.random()
    if roll < 0.25:
        paid = amount                      # fully paid booking
    elif roll < 0.75:
        paid = money(amount * random.choice([0.2, 0.3, 0.5, 0.7]))
    else:
        paid = 0.0                         # pure credit booking
    recv_acc = None
    if paid > 0:
        recv_acc = random.choice([MAIN_CASH, MAIN_CASH, MEZAN, HBL, EASYPAISA])
    booking_bill_of[bk_id] = manual or auto
    put("booking",
        id=bk_id, client_name=cname, amount=amount, paid_amount=paid,
        manual_bill_no=manual, auto_bill_no=auto,
        photo_path=None, photo_url=None, date_posted=dt,
        is_void=is_void,
        note=random.choice(["Material booked for site", "Rate locked for 15 days",
                            "Client will confirm dispatch date", "",
                            "Deliver in two tranches"]),
        discount=discount,
        discount_reason=("Old client rate adjustment" if discount else None),
        receive_in_account_id=recv_acc)
    if not is_void:
        pending = money(amount - paid)
        if manual and pending > 0:
            put("pending_bill",
                id=next_id("pending_bill"),
                client_code=client_code_of[cid], client_name=cname,
                bill_no=manual, bill_kind="MB", nimbus_no=None,
                amount=pending,
                reason=f"Booking: {', '.join(items[:2])}{'…' if len(items) > 2 else ''}",
                photo_url=None, photo_path=None,
                is_paid=1 if pending <= 0 else 0,
                is_cash=0, is_manual=1,
                created_at=pk_now_str(dt), created_by="Admin", is_void=0,
                note=None,
                source_module="bookings", source_table="booking", source_id=bk_id,
                source_bill_no=manual, transaction_type="Booking")
        elif manual and pending <= 0:
            put("pending_bill",
                id=next_id("pending_bill"),
                client_code=client_code_of[cid], client_name=cname,
                bill_no=manual, bill_kind="MB", nimbus_no=None,
                amount=0, reason=f"Booking: {', '.join(items[:2])}{'…' if len(items) > 2 else ''}",
                photo_url=None, photo_path=None, is_paid=1, is_cash=0, is_manual=1,
                created_at=pk_now_str(dt), created_by="Admin", is_void=0, note=None,
                source_module="bookings", source_table="booking", source_id=bk_id,
                source_bill_no=manual, transaction_type="Booking")

# ----------------------------------------------------------------------------
# 12. direct sales (400) + items + entries + invoices + pending bills
# ----------------------------------------------------------------------------

N_SALES = 400
sl_auto_seq = 1000
sale_seq_manual = 2000
inv_auto_base = int(BASE_NOW.strftime("%Y%m")) * 1000000  # auto invoice no base
pending_bill_by_ref = {}   # bill_ref -> pending_bill row id
sale_alloc_candidates = []  # (sale_id, sale_item_id, mat_name, qty, client_id, dt)

for s in range(1, N_SALES + 1):
    dt = ago(random.randint(0, 160), random.randint(8, 20), random.choice([0, 15, 30, 45]))
    # OPEN KHATA walk-in sales for some rows
    roll = random.random()
    if roll < 0.03:
        cid, cname, ccode = OPEN_KHATA_ID, "OPEN KHATA", "OPEN-KHATA"
        category = "Open Khata"
    else:
        cid = random.choice(CLIENT_IDS)
        cname, ccode = client_name_of[cid], client_code_of[cid]
        profile = random.random()
        if profile < 0.38:
            category = "Cash"
        elif profile < 0.70:
            category = "Credit Customer"
        elif profile < 0.85:
            category = "Booking Delivery"
        else:
            category = "Mixed Transaction"
    driver = random.choice(DRIVERS) if random.random() < 0.8 else None
    driver_id = DRIVERS.index(driver) + 1 if driver else None
    n_items = random.randint(1, 4)
    if category == "Booking Delivery" and booking_pool:
        # consume a booking line if available
        cand = [b for b in booking_pool if b[5] == cid and b[3] > 0]
        items = []
        for b in cand[:n_items]:
            items.append((b[2], b[3], b[4]))  # deliver full booked qty
        if not items:
            items = [(random.choice(MAT_NAMES), random.choice([50, 100, 200]), MAT[random.choice(MAT_NAMES)][2]) for _ in range(1)]
    else:
        if roll < 0.03:
            items_pool = CEMENT_NAMES
        else:
            items_pool = MAT_NAMES
        items = []
        for mname in random.sample(items_pool, k=min(len(items_pool), n_items)):
            _, unit, price = MAT[mname]
            qty = random.choice([25, 50, 100, 200, 300]) if unit == "Bags" \
                else random.choice([1, 2, 3, 5]) if unit == "Ton" \
                else random.choice([30, 60, 100, 150])
            items.append((mname, qty, money(price * random.uniform(0.98, 1.03))))
    total = money(sum(q * r for _, q, r in items))
    discount = money(random.choice([0, 0, 0, 0, 0, 250, 500, 1000]))
    # payment behaviour per category
    if category == "Cash":
        paid = money(total - discount)
    elif category == "Booking Delivery":
        paid = money((total - discount) * random.choice([0.5, 1.0, 1.0]))
    elif category == "Open Khata":
        paid = 0.0
    else:
        r2 = random.random()
        paid = money((total - discount) * random.choice([0, 0.2, 0.3, 0.5, 0.75, 1.0])) if r2 < 0.8 \
            else money(total - discount)
    manual = None
    if category in ("Credit Customer", "Mixed Transaction") and random.random() < 0.5:
        sale_seq_manual += 1
        manual = f"MB NO.{sale_seq_manual}"
    auto = f"SB-SL-{sl_auto_seq + s}"
    method = random.choices(["Cash", "Bank", "Check"], weights=[70, 25, 5])[0]
    pay_acc = None
    if paid > 0:
        if category == "Cash":
            pay_acc = random.choice([MAIN_CASH, MAIN_CASH, MAIN_CASH, PETTY_CASH, MEZAN])
        else:
            pay_acc = random.choice(CASH_ACCOUNTS + BANK_ACCOUNTS + WALLET_ACCOUNTS)
    is_void = 1 if s in (398, 399, 400) else 0
    rent_rev = money(random.choice([0, 0, 0, 300, 500, 800]))
    rent_cost = money(rent_rev * random.uniform(0.5, 0.9)) if rent_rev else 0
    bank_name = acct_name = acct_no = None
    if method in ("Bank", "Check") and paid > 0:
        bank_name = random.choice(["Meezan Bank", "Habib Bank Limited"])
        acct_name = "Fazal Building Materials"
        acct_no = f"{random.randint(1000, 9999)}-{random.randint(1000000, 9999999)}"
    put("direct_sale",
        id=s,
        idempotency_key=None, idempotency_payload_hash=None,
        client_name=cname, client_code=ccode, category=category,
        amount=total, paid_amount=paid if not is_void else 0,
        discount=discount,
        discount_reason=("Loyalty discount" if discount else None),
        manual_bill_no=manual, auto_bill_no=auto,
        photo_path=None, photo_url=None, invoice_id=None,  # set below
        date_posted=dt, is_void=is_void,
        note=random.choice(["", "Delivered at site", "Second instalment", "Urgent supply"]),
        driver_name=driver,
        rent_item_revenue=rent_rev, delivery_rent_cost=rent_cost,
        rent_variance_loss=money(random.choice([0, 0, 0, 50, 120])),
        payment_method=method if paid > 0 else None,
        payment_account_id=pay_acc if not is_void else None,
        bank_name=bank_name, account_name=acct_name, account_no=acct_no)
    # ---- items + FIFO from GRN lots
    for mname, qty, rate in items:
        lot = None
        if not is_void:
            for gi, gmat, rem, grate, gdt in grn_item_pool:
                if gmat == mname and rem >= qty and gdt <= dt:
                    lot = (gi, grate)
                    break
        put("direct_sale_item",
            id=next_id("direct_sale_item"), sale_id=s,
            product_name=mname, qty=qty, price_at_time=rate,
            grn_item_id=lot[0] if lot else None,
            cost_rate_at_sale=lot[1] if lot else None)
        si_id = len(DATA["direct_sale_item"])
        if lot:
            for idx, (gi, gmat, rem, grate, gdt) in enumerate(grn_item_pool):
                if gi == lot[0]:
                    grn_item_pool[idx] = (gi, gmat, rem - qty, grate, gdt)
                    put("grn_allocation",
                        id=next_id("grn_allocation"), sale_id=s,
                        sale_item_id=si_id, grn_item_id=gi, qty=qty,
                        cost_rate=grate, is_void=0)
                    for g in DATA["grn_item"]:
                        if g["id"] == gi:
                            g["is_locked"] = 1
                    break
        if category == "Booking Delivery":
            sale_alloc_candidates.append((s, si_id, mname, qty, cid, dt))
    # ---- invoice for credit-ish categories
    invoice_id = None
    balance = money(max(0.0, total - discount - (paid if not is_void else 0)))
    if category in ("Credit Customer", "Mixed Transaction") and not is_void:
        if manual:
            invoice_no, is_manual_inv = manual, 1
        else:
            inv_n = inv_auto_base + s
            invoice_no, is_manual_inv = f"INV-{inv_n}", 0
        status = "PAID" if balance <= 0 else ("PARTIAL" if paid > 0 else "OPEN")
        invoice_id = next_id("invoice")
        put("invoice",
            id=invoice_id, client_code=ccode, client_name=cname,
            invoice_no=invoice_no, is_manual=is_manual_inv,
            date=dt.date(), total_amount=total, balance=balance, status=status,
            is_cash=0, created_at=pk_now_str(dt), created_by="Admin",
            is_void=0, note=None)
        DATA["direct_sale"][-1]["invoice_id"] = invoice_id
        bill_ref = manual or auto
    elif category == "Open Khata" and not is_void:
        bill_ref = auto
    elif category == "Cash":
        bill_ref = auto
    else:  # booking delivery
        bill_ref = manual or auto
    # ---- pending bill (mirrors _sync_direct_sale_pending_bill)
    if not is_void and (balance > 0 or category in ("Cash", "Open Khata")):
        pending_amt = balance
        pb_bill = manual or (auto if category in ("Cash", "Open Khata") else auto)
        pb_kind = "MB" if (manual or "").startswith("MB") else "SB"
        reason = f"Direct Sale ({category}): {items[0][0]}"
        pb_id = next_id("pending_bill")
        put("pending_bill",
            id=pb_id,
            client_code=("OPEN-KHATA" if category == "Open Khata" else ccode),
            client_name=("OPEN KHATA" if category == "Open Khata" else cname),
            bill_no=pb_bill, bill_kind=pb_kind, nimbus_no=None,
            amount=pending_amt, reason=reason,
            photo_url=None, photo_path=None,
            is_paid=1 if pending_amt <= 0 else 0,
            is_cash=1 if category == "Cash" else 0,
            is_manual=1 if manual else 0,
            created_at=pk_now_str(dt), created_by="Admin", is_void=0, note=None,
            source_module="sales", source_table="direct_sale", source_id=s,
            source_bill_no=bill_ref, transaction_type=category)
        pending_bill_by_ref[bill_ref] = pb_id
    # ---- ledger entry rows (one per item, mirrors app behaviour; the app
    # leaves entry.auto_bill_no NULL — bill ref lives in bill_no + source cols)
    for (mname, qty, rate), si in zip(items, [d for d in DATA["direct_sale_item"] if d["sale_id"] == s]):
        item_cat = category
        if category == "Mixed Transaction":
            item_cat = "Credit Customer"
        tx_cat = "Unbilled" if category == "Cash" else "Billed"
        put("entry",
            id=next_id("entry"),
            date=dt.strftime("%Y-%m-%d"), time=dt.strftime("%H:%M:%S"),
            type="OUT", material=mname, client=cname, client_code=ccode,
            client_category=item_cat, qty=qty, bill_no=bill_ref,
            auto_bill_no=None, nimbus_no="Direct Sale",
            invoice_id=invoice_id, created_by="Admin",
            created_at=dt, is_void=1 if is_void else 0,
            transaction_category=tx_cat, driver_name=driver,
            note=DATA["direct_sale"][-1]["note"], booked_material=None,
            is_alternate=0, source_module="sales", source_table="direct_sale",
            source_id=s, source_bill_no=bill_ref, transaction_type=item_cat)
    # ---- account movements for the paid part
    if not is_void and paid > 0 and pay_acc:
        acct_tx(None, pay_acc, paid, "Receipt",
                f"Direct sale {bill_ref} — {cname}", dt,
                f"[SRC:DirectSale:{s}]",
                source_type="DirectSale", source_id=s)
    # ---- driver allocation + delivery rent
    if driver and not is_void:
        rent_share = money(rent_cost if rent_cost else random.choice([80, 120, 200, 350]))
        put("sale_delivery_persons",
            id=next_id("sale_delivery_persons"), sale_id=s,
            delivery_person_id=driver_id,
            bags_delivered=sum(q for mname, q, _r in items if MAT[mname][1] == "Bags"),
            rent_amount=rent_share, created_at=dt, is_void=0)
        put("delivery_rent",
            id=next_id("delivery_rent"), sale_id=s,
            delivery_person_name=driver, bill_no=bill_ref,
            amount=rent_share,
            note=random.choice(["", "Round trip", "Two trips"]),
            date_posted=dt, created_by="Admin", is_void=0)

# booking allocations for booking-delivery sales
alloc_seen = set()
for (sale_id, si_id, mname, qty, cid, dt) in sale_alloc_candidates:
    for idx, (bk_id2, bi_id, bmat, brem, bprice, bclient) in enumerate(booking_pool):
        if bclient == cid and bmat == mname and brem > 0 and (sale_id, bi_id) not in alloc_seen:
            use = min(qty, brem)
            if use <= 0:
                continue
            put("booking_allocation",
                id=next_id("booking_allocation"), sale_id=sale_id,
                sale_item_id=si_id, booking_item_id=bi_id, qty=use, is_void=0)
            booking_pool[idx] = (bk_id2, bi_id, bmat, brem - use, bprice, bclient)
            alloc_seen.add((sale_id, bi_id))
            break

# lock GRN lots that are fully consumed
for g in DATA["grn_item"]:
    consumed = sum(a["qty"] for a in DATA["grn_allocation"] if a["grn_item_id"] == g["id"])
    if not g["is_void"] and consumed >= float(g["qty"] or 0) and consumed > 0:
        g["is_locked"] = 1

# ----------------------------------------------------------------------------
# 13. standalone dispatch entries (manual imports / open-market dispatches)
# ----------------------------------------------------------------------------

disp_bill_seq = 9000
nimbus_seq = 2300
for i in range(1, 116):
    dt = ago(random.randint(0, 175), random.randint(7, 19), random.choice([5, 20, 35, 50]))
    open_khata = random.random() < 0.08
    if open_khata:
        cid, cname, ccode, ccat = OPEN_KHATA_ID, "OPEN KHATA", "OPEN-KHATA", "Open Khata"
    else:
        cid = random.choice(CLIENT_IDS)
        cname, ccode, ccat = client_name_of[cid], client_code_of[cid], client_cat_of[cid]
    mname = random.choice(MAT_NAMES)
    _, unit, price = MAT[mname]
    qty = random.choice([25, 50, 100, 150, 200, 400]) if unit == "Bags" \
        else random.choice([1, 2, 3, 5]) if unit == "Ton" \
        else random.choice([40, 80, 120])
    billed = random.random() < 0.45 and not open_khata
    if billed:
        disp_bill_seq += 1
        bill = f"MB NO.{disp_bill_seq}"
        tx_cat = "CEMENT+BILL" if "Cement" in mname else "STEEL+BILL"
    elif open_khata:
        bill = ""
        tx_cat = "OPEN KHATA"
    else:
        bill = ""
        tx_cat = "CEMENT" if "Cement" in mname else "STEEL"
    nimbus_seq += 1
    driver = random.choice(DRIVERS) if random.random() < 0.6 else None
    put("entry",
        id=next_id("entry"),
        date=dt.strftime("%Y-%m-%d"), time=dt.strftime("%H:%M:%S"),
        type="OUT", material=mname, client=cname, client_code=ccode,
        client_category=ccat, qty=qty, bill_no=bill,
        auto_bill_no=None, nimbus_no=f"NB-{nimbus_seq}",
        invoice_id=None, created_by="Admin", created_at=dt, is_void=0,
        transaction_category=tx_cat, driver_name=driver,
        note=random.choice(["", "Mukadam present", "Old dispatch import",
                            "Rate as per deal"]), booked_material=None,
        is_alternate=0, source_module=None, source_table=None,
        source_id=None, source_bill_no=None, transaction_type=None)
    if billed:
        pb_id = next_id("pending_bill")
        amount = money(qty * price)
        put("pending_bill",
            id=pb_id, client_code=ccode, client_name=cname, bill_no=bill,
            bill_kind="MB", nimbus_no=f"NB-{nimbus_seq}",
            amount=amount, reason=f"Imported Dispatch: {qty} {mname}",
            photo_url=None, photo_path=None, is_paid=0, is_cash=0, is_manual=1,
            created_at=pk_now_str(dt), created_by="Admin", is_void=0, note=None,
            source_module="dispatch", source_table="entry",
            source_id=DATA["entry"][-1]["id"], source_bill_no=bill,
            transaction_type="Dispatch")
        pending_bill_by_ref[bill] = pb_id

# a few IN entries (stock receiving / adjustments)
for i in range(1, 41):
    dt = ago(random.randint(0, 160), random.randint(8, 17), random.choice([10, 30, 50]))
    mname = random.choice(MAT_NAMES)
    qty = random.choice([20, 40, 60, 100])
    put("entry",
        id=next_id("entry"),
        date=dt.strftime("%Y-%m-%d"), time=dt.strftime("%H:%M:%S"),
        type="IN", material=mname, client="Stock Receiving", client_code="",
        client_category="", qty=qty, bill_no=f"MB NO.{7500 + i}",
        auto_bill_no=None, nimbus_no="Stock In", invoice_id=None,
        created_by="Admin", created_at=dt, is_void=0,
        transaction_category="Received" if i % 2 else "Return",
        driver_name=None, note="Manual stock in / adjustment",
        booked_material=None, is_alternate=0, source_module="inventory",
        source_table="grn", source_id=None, source_bill_no=None,
        transaction_type="Stock In")

# ----------------------------------------------------------------------------
# 14. client payments (receipts / refunds / material returns / waive-offs)
# ----------------------------------------------------------------------------

cp_seq = 1000
pay_id = 0
code_to_client = {c["code"]: c for c in DATA["client"]}
# 14a. receipts against open pending bills
open_bills = [pb for pb in DATA["pending_bill"]
              if not pb["is_void"] and float(pb["amount"] or 0) > 0 and not pb["is_paid"]]
random.shuffle(open_bills)
for pb in open_bills[:180]:
    if random.random() < 0.18:
        continue
    pay_id += 1
    cp_seq += 1
    bill_amt = float(pb["amount"])
    if random.random() < 0.45:
        amt = bill_amt
        fully = True
    else:
        amt = money(bill_amt * random.choice([0.25, 0.4, 0.5, 0.7]))
        fully = False
    if amt < 100:
        pay_id -= 1
        cp_seq -= 1
        continue
    method = random.choices(["Cash", "Bank", "Check"], weights=[62, 30, 8])[0]
    bill_dt = datetime.strptime(pb["created_at"], "%Y-%m-%d %H:%M")
    lag = max(1, int((BASE_NOW - bill_dt).days))
    dt = bill_dt + timedelta(days=random.randint(1, max(1, min(60, lag))),
                             hours=random.randint(0, 5))
    if dt > BASE_NOW:
        dt = BASE_NOW - timedelta(hours=random.randint(1, 48))
    acc = random.choice(CASH_ACCOUNTS if method == "Cash" else BANK_ACCOUNTS + WALLET_ACCOUNTS)
    cli = code_to_client.get(pb["client_code"])
    bank_name = acct_name = acct_no = None
    if method == "Check":
        bank_name, acct_name, acct_no = "Habib Bank Limited", "Fazal Building Materials", f"{random.randint(1000,9999)}-{random.randint(1000000,9999999)}"
    put("payment",
        id=pay_id,
        client_id=(cli["id"] if cli else None),
        client_name=pb["client_name"],
        amount=amt, amount_minor=int(amt * 100), method=method,
        payment_type="Receipt", source_type="PendingBill", source_id=pb["id"],
        manual_bill_no=pb["bill_no"] if random.random() < 0.8 else None,
        auto_bill_no=f"SB-CP-{cp_seq}",
        photo_path=None, photo_url=None, date_posted=dt, is_void=0,
        note=random.choice(["", "Part payment", "Full settlement", "Collected by rider", ""]),
        discount=0, discount_minor=None, discount_reason=None,
        bank_name=bank_name, account_name=acct_name, account_no=acct_no,
        payment_account_id=acc, idempotency_key=None,
        idempotency_payload_hash=None, revision=1,
        created_by="Admin", updated_by="Admin", created_at=dt, updated_at=dt)
    acct_tx(None, acc, amt, "Receipt",
            f"Payment from {pb['client_name']} — {pb['bill_no']}", dt,
            f"[SRC:Payment:{pay_id}]",
            source_type="Payment", source_id=pay_id)
    pb["amount"] = money(bill_amt - amt)
    if fully or pb["amount"] <= 0:
        pb["is_paid"] = 1
        pb["amount"] = 0  # fully settled — outstanding is zero (app behaviour)
# 14b. refunds (few)
for i in range(1, 6):
    pay_id += 1
    cp_seq += 1
    cid = random.choice(CLIENT_IDS)
    amt = money(random.choice([2000, 3500, 5000]))
    dt = ago(random.randint(5, 100), 14, 30)
    acc = random.choice(CASH_ACCOUNTS + BANK_ACCOUNTS)
    put("payment",
        id=pay_id, client_id=cid, client_name=client_name_of[cid],
        amount=amt, amount_minor=int(amt * 100), method="Cash",
        payment_type="Refund", source_type=None, source_id=None,
        manual_bill_no=None, auto_bill_no=f"SB-CP-{cp_seq}",
        photo_path=None, photo_url=None, date_posted=dt, is_void=0,
        note="Advance refund — order cancelled", discount=0, discount_minor=None,
        discount_reason=None, bank_name=None, account_name=None, account_no=None,
        payment_account_id=acc, idempotency_key=None,
        idempotency_payload_hash=None, revision=1,
        created_by="Admin", updated_by="Admin", created_at=dt, updated_at=dt)
    acct_tx(acc, None, amt, "Refund",
            f"Refund to {client_name_of[cid]}", dt, f"[SRC:Payment:{pay_id}]",
            source_type="Payment", source_id=pay_id)
# 14c. voided payment samples
for i in range(1, 4):
    pay_id += 1
    cp_seq += 1
    cid = random.choice(CLIENT_IDS)
    amt = money(random.choice([1500, 3000, 8000]))
    dt = ago(random.randint(20, 90), 12, 0)
    put("payment",
        id=pay_id, client_id=cid, client_name=client_name_of[cid],
        amount=amt, amount_minor=int(amt * 100), method="Cash",
        payment_type="Receipt", source_type=None, source_id=None,
        manual_bill_no=None, auto_bill_no=f"SB-CP-{cp_seq}",
        photo_path=None, photo_url=None, date_posted=dt, is_void=1,
        note="Voided — wrong entry", discount=0, discount_minor=None,
        discount_reason=None, bank_name=None, account_name=None, account_no=None,
        payment_account_id=MAIN_CASH, idempotency_key=None,
        idempotency_payload_hash=None, revision=1,
        created_by="Admin", updated_by="Admin", created_at=dt, updated_at=dt)

# ----------------------------------------------------------------------------
# 15. waive-offs
# ----------------------------------------------------------------------------

wf_candidates = [pb for pb in DATA["pending_bill"]
                 if not pb["is_void"] and float(pb["amount"] or 0) > 0 and not pb["is_paid"]][:10]
for i, pb in enumerate(wf_candidates, 1):
    pay_id += 1
    cp_seq += 1
    amt = money(pb["amount"])
    dt = ago(random.randint(3, 90), 15, 45)
    put("payment",
        id=pay_id, client_id=None, client_name=pb["client_name"],
        amount=0, amount_minor=0, method="Cash", payment_type="Waive-Off",
        source_type="PendingBill", source_id=pb["id"],
        manual_bill_no=pb["bill_no"], auto_bill_no=f"SB-CP-{cp_seq}",
        photo_path=None, photo_url=None, date_posted=dt, is_void=0,
        note=None, discount=amt, discount_minor=int(amt * 100),
        discount_reason=random.choice(["Old pending dispute", "Relationship concession",
                                       "Short qty issue settled"]),
        bank_name=None, account_name=None, account_no=None,
        payment_account_id=None, idempotency_key=None,
        idempotency_payload_hash=None, revision=1,
        created_by="Admin", updated_by="Admin", created_at=dt, updated_at=dt)
    put("waive_off",
        id=i, payment_id=pay_id, client_code=pb["client_code"],
        client_name=pb["client_name"], bill_no=pb["bill_no"], amount=amt,
        reason=pb.get("note") or random.choice(["Pending cleared by owner approval",
                                                "Rate difference settled"]),
        date_posted=dt, created_by="Admin",
        note="Waived against old balance", is_void=0)
    pb["is_paid"] = 1
    pb["amount"] = 0

# ----------------------------------------------------------------------------
# 16. material returns (normal + booked) + items + refund payments
# ----------------------------------------------------------------------------

rtn_seq = 1000
rtn_id = 0
mr_pay_seq = pay_id
for i in range(1, 71):
    rtn_id = i
    dt = ago(random.randint(0, 140), random.randint(9, 18), random.choice([10, 30, 50]))
    return_type = "booked" if random.random() < 0.35 else "normal"
    cid = random.choice(CLIENT_IDS)
    cname, ccode = client_name_of[cid], client_code_of[cid]
    n_items = random.randint(1, 3)
    items = random.sample(MAT_NAMES, k=n_items)
    total = 0.0
    total_rent = 0.0
    for mname in items:
        _, unit, price = MAT[mname]
        qty = random.choice([5, 10, 20, 25, 50]) if unit == "Bags" \
            else random.choice([1, 2]) if unit == "Ton" \
            else random.choice([10, 20, 40])
        rate = money(price * random.uniform(0.95, 1.0))
        rent_rate = money(random.choice([0, 0, 5, 10])) if return_type == "booked" else 0
        total += qty * rate
        total_rent += qty * rent_rate
        put("material_return_item",
            id=next_id("material_return_item"),
            material_return_id=rtn_id, material_name=mname, qty=qty,
            unit_rate=rate, rent_rate=rent_rate,
            price_at_time=money(qty * rate + qty * rent_rate))
    total = money(total + total_rent)
    is_void = 1 if i in (69, 70) else 0
    refund = total if return_type == "booked" else money(total * random.choice([1.0, 0.5]))
    pay_link = None
    if refund > 0 and not is_void:
        mr_pay_seq += 1
        pay_link = mr_pay_seq
        acc = random.choice(CASH_ACCOUNTS + BANK_ACCOUNTS)
        put("payment",
            id=pay_link, client_id=cid, client_name=cname,
            amount=refund, amount_minor=int(refund * 100), method="Cash",
            payment_type="Material Return", source_type="MaterialReturn",
            source_id=rtn_id,
            manual_bill_no=None, auto_bill_no=f"SB-CP-{cp_seq + mr_pay_seq}",
            photo_path=None, photo_url=None, date_posted=dt, is_void=0,
            note=f"Refund for material return SB-RTN-{1000 + rtn_id}",
            discount=0, discount_minor=None, discount_reason=None,
            bank_name=None, account_name=None, account_no=None,
            payment_account_id=acc, idempotency_key=None,
            idempotency_payload_hash=None, revision=1,
            created_by="Admin", updated_by="Admin", created_at=dt, updated_at=dt)
        acct_tx(acc, None, refund, "Refund",
                f"Material return refund — {cname}", dt,
                f"[SRC:Payment:{pay_link}]",
                source_type="Payment", source_id=pay_link)
    put("material_return",
        id=rtn_id, client_name=cname, return_type=return_type,
        amount=total if not is_void else 0,
        manual_bill_no=f"MB NO.{6100 + i}" if i % 4 == 0 else None,
        auto_bill_no=f"SB-RTN-{1000 + i}",
        date_posted=dt,
        note=("Booked material returned to stock" if return_type == "booked"
              else "Excess bags returned by client"),
        payment_id=pay_link, is_void=is_void)

# ----------------------------------------------------------------------------
# 17. deliveries module (dispatch slips without billing)
# ----------------------------------------------------------------------------

for i in range(1, 31):
    dt = ago(random.randint(0, 150), random.randint(8, 18), random.choice([5, 25, 45]))
    cid = random.choice(CLIENT_IDS)
    put("delivery",
        id=i, client_name=client_name_of[cid],
        manual_bill_no=f"DL-{100 + i}", auto_bill_no=None,
        photo_path=None, date_posted=dt)
    for j in range(random.randint(1, 3)):
        mname = random.choice(MAT_NAMES)
        _, unit, _p = MAT[mname]
        qty = random.choice([10, 20, 40, 60]) if unit == "Bags" else random.choice([2, 4, 6])
        put("delivery_item",
            id=next_id("delivery_item"), delivery_id=i,
            product=mname, qty=qty)

# ----------------------------------------------------------------------------
# 18. driver payments
# ----------------------------------------------------------------------------

dp_id = 0
for driver_id in DRIVER_IDS:
    for _ in range(random.randint(3, 7)):
        dp_id += 1
        amt = money(random.choice([500, 800, 1200, 2000, 3500]))
        waive = money(random.choice([0, 0, 0, 100, 200]))
        dt = ago(random.randint(1, 130), random.randint(10, 18), 20)
        method = random.choices(["Cash", "Bank"], weights=[85, 15])[0]
        acc = random.choice(CASH_ACCOUNTS if method == "Cash" else BANK_ACCOUNTS)
        sale = random.choice(DATA["direct_sale"][:380])
        put("delivery_person_payment",
            id=dp_id, delivery_person_id=driver_id,
            sale_id=sale["id"] if random.random() < 0.6 else None,
            allocation_id=None,
            amount_paid=amt, amount_paid_minor=int(amt * 100),
            waive_off_amount=waive, waive_off_minor=int(waive * 100),
            payment_account_id=acc, method=method,
            reference=f"DP-{2000 + dp_id}",
            note=random.choice(["", "Weekly settlement", "Trip rent paid", ""]),
            date_posted=dt, idempotency_key=None, revision=1,
            created_by="Admin", updated_by="Admin", created_at=dt, updated_at=dt,
            is_void=0)
        acct_tx(acc, None, amt, "Driver Payment",
                f"Driver payment — {DRIVERS[driver_id - 1]}", dt,
                f"[SRC:DeliveryPersonPayment:{dp_id}]",
                source_type="DeliveryPersonPayment", source_id=dp_id)

# ----------------------------------------------------------------------------
# 19. rentals (FBM)
# ----------------------------------------------------------------------------

RENT_ITEMS = [("Shuttering Ply (Pcs)", 400, 380, 25), ("Steel Props (Pcs)", 200, 195, 40),
              ("Centering Plates (Pcs)", 300, 285, 20), ("Scaffolding Pipes (FT)", 1000, 950, 5),
              ("Wheelbarrow (Pcs)", 20, 18, 60)]
for i, (nm, opening, avail, rate) in enumerate(RENT_ITEMS, 1):
    put("fbm_rental_item",
        id=i, name=nm, opening_qty=opening, available_qty=avail,
        rent_per_day=money(rate), is_void=0, created_at=ago(160, 9),
        updated_at=ago(30, 9))
RENT_CLIENTS = [("Haji Muhammad Aslam Contractor", "CNIC 33102-1234567-1"),
                ("Rana Construction Company", "CNIC 33102-7654321-9"),
                ("Mian Abdul Sattar", "CNIC 33102-9988776-3"),
                ("Chaudhry Property & Builders", "CNIC 33102-5544332-5"),
                ("Malik Site Services", "CNIC 33102-1122334-7"),
                ("Sheikh Renting House", "CNIC 33102-6677889-2")]
for i, (nm, cnic) in enumerate(RENT_CLIENTS, 1):
    put("fbm_client",
        id=i, full_name=nm, address=f"{random.choice(AREAS)}, {random.choice(TOWNS)}",
        phone=phone(), identity_card=cnic, is_active=1,
        created_at=ago(150, 10), updated_at=ago(20, 10))
for i in range(1, 13):
    item = random.randint(1, len(RENT_ITEMS))
    client = random.randint(1, len(RENT_CLIENTS))
    qty = random.choice([10, 20, 30, 50])
    rate = RENT_ITEMS[item - 1][3]
    start = ago(random.randint(3, 120), 11)
    active = i > 7
    days = random.randint(4, 30)
    returned = None if active else start + timedelta(days=days)
    total = money(days * rate * qty)
    paid = money(total * random.choice([1.0, 0.5, 0.4])) if not active else money(total * 0.3)
    put("fbm_rental",
        id=i, client_id=client, item_id=item, qty=qty, rent_per_unit=money(rate),
        total_amount=total, qty_returned=0 if active else qty,
        paid_amount=paid, discount_amount=0,
        start_datetime=start, return_datetime=returned,
        status="active" if active else "returned",
        payment_account_id=MAIN_CASH if paid > 0 else None,
        note="Site rental — shuttering material" if item in (1, 2, 3) else "Rental",
        created_at=start, updated_at=returned or ago(1, 11))
    if paid > 0:
        acct_tx(None, MAIN_CASH, paid, "Receipt",
                f"Rental charge — {RENT_CLIENTS[client - 1][0]}", start,
                f"[SRC:FBMRental:{i}]", source_type="FBMRental", source_id=i)

# ----------------------------------------------------------------------------
# 20. notifications, follow-ups, recon basket, drafts, audit, misc
# ----------------------------------------------------------------------------

for i, em in enumerate(["manager@fazalbuilders.pk", "owner@fazalbuilders.pk",
                        "store@fazalbuilders.pk"], 1):
    put("staff_email", id=i, email=em, is_active=1, created_at=ago(150, 9))

fu_id = 0
fc_id = 0
remindable = [pb for pb in DATA["pending_bill"]
              if not pb["is_void"] and not pb["is_paid"] and float(pb["amount"] or 0) > 0][:8]
for pb in remindable:
    fu_id += 1
    put("follow_up_reminder",
        id=fu_id, pending_bill_id=pb["id"],
        remind_at=ago(-random.randint(1, 10), 10),  # future
        note=f"Call {pb['client_name']} for {pb['bill_no']} payment",
        is_done=0, alerted_at=None, acknowledged_at=None, created_at=ago(5, 9))
    for _ in range(random.randint(1, 2)):
        fc_id += 1
        put("follow_up_contact",
            id=fc_id, pending_bill_id=pb["id"], reminder_id=fu_id,
            contacted_at=ago(random.randint(1, 15), 12),
            channel=random.choice(["Call", "WhatsApp", "Visit"]),
            response=random.choice(["Promised next week", "Asked for more time",
                                    "Will pay after Eid", "Phone not received"]),
            note=None, created_by="Admin", created_at=ago(random.randint(1, 15), 12))

for i in range(1, 7):
    dt = ago(random.randint(30, 150), 10)
    put("recon_basket",
        id=i, bill_no=f"MB NO.{random.randint(2001, 2400)}",
        inv_date=dt.date(),
        inv_client=random.choice([c["name"] for c in DATA["client"][:40]]),
        fin_client=random.choice([c["name"] for c in DATA["client"][:40]]),
        inv_material=random.choice(MAT_NAMES), inv_qty=random.choice([50, 100, 200]),
        status=random.choice(["MATCHED", "UNMATCHED", "REVIEW"]),
        match_score=random.choice([100, 85, 60]), created_at=dt)

for i in range(1, 4):
    cid = random.choice(CLIENT_IDS)
    dt = ago(random.randint(0, 4), random.randint(9, 18))
    payload = {
        "client_code": client_code_of[cid], "client_name": client_name_of[cid],
        "category": "Cash", "driver_name": random.choice(DRIVERS),
        "manual_bill_no": "", "items": [
            {"product_name": random.choice(CEMENT_NAMES),
             "qty": 50, "price_at_time": MAT[CEMENT_NAMES[0]][2]}],
        "paid_amount": 50 * MAT[CEMENT_NAMES[0]][2],
    }
    put("direct_sale_draft",
        id=i, client_code=client_code_of[cid], client_name=client_name_of[cid],
        manual_client_name=None, category="Cash",
        driver_name=random.choice(DRIVERS), manual_bill_no=None,
        item_count=1, total_qty=50,
        total_amount=money(50 * MAT[CEMENT_NAMES[0]][2]),
        payload=json.dumps(payload), created_by="Admin",
        created_at=dt, updated_at=dt)

for i in range(1, 13):
    dt = ago(random.randint(0, 60), random.randint(8, 20))
    put("audit_log",
        id=f"{i:08x}-aaaa-4bbb-8ccc-dddddddddddd"[:36],
        user_id=1, username="Admin",
        action=random.choice(["login.success", "sale.create", "payment.create",
                              "client.update", "grn.create", "booking.create"]),
        details=random.choice(["LAN 192.168.10.5", "LAN 192.168.10.8", ""]),
        timestamp=dt)

for i in range(1, 13):
    dt = ago(random.randint(0, 60), random.randint(8, 20))
    module = random.choice(["sales", "payments", "bookings", "accounts", "grn"])
    entity = {"sales": "DirectSale", "payments": "Payment", "bookings": "Booking",
              "accounts": "AccountTransaction", "grn": "GRN"}[module]
    put("accounting_audit_log",
        id=f"{i:08x}-bbbb-4ccc-8ddd-eeeeeeeeeeee"[:36],
        module=module, action=random.choice(["CREATE", "UPDATE", "VOID"]),
        entity_type=entity, entity_id=random.randint(1, 300),
        user_id=1, username="Admin", ip_address="192.168.10.5",
        session_id=None,
        before_json=json.dumps({"amount": 1000}), after_json=json.dumps({"amount": 1500}),
        amount_before_minor=100000, amount_after_minor=150000,
        account_before_id=None, account_after_id=None,
        party_before_id=None, party_after_id=None,
        reason="Dummy audit trail", created_at=dt)

COUNTERS = [("GEN", 1000), ("SL", sl_auto_seq + N_SALES), ("BK", bk_auto_seq + N_BOOKINGS),
            ("CP", 2000), ("SP", 1200), ("RTN", 1200), ("GRN", 1200), ("EN", 1000)]
for i, (ns, cnt) in enumerate(COUNTERS, 1):
    put("bill_counter", id=i, namespace=ns, count=cnt)

# ----------------------------------------------------------------------------
# 20b. owner capital injections — keep every account positive across the
# whole 6-month ledger (covers the deepest running deficit + working buffer)
# ----------------------------------------------------------------------------

inj_id = len(DATA["account_transaction"]) + 1000
for acc in ACCOUNTS:
    aid, name = acc[0], acc[1]
    need = max(0.0, -acc_min_balance[aid]) + 250000  # deficit + working buffer
    if need <= 0:
        continue
    need = money(round(need, -3))  # round to thousands
    inj_id += 1
    acct_tx(None, aid, need, "Receipt",
            f"Owner capital injection — {name}", ago(198, 9, 30),
            f"[SRC:OwnerCapital:{inj_id}] opening working capital",
            source_type="OwnerCapital", source_id=inj_id)

# ----------------------------------------------------------------------------
# 21. material stock totals (rebuild like the app does)
# ----------------------------------------------------------------------------

stock = {m: 0.0 for m in MAT_NAMES}
for gi in DATA["grn_item"]:
    if not gi["is_void"]:
        stock[gi["mat_name"]] += float(gi["qty"] or 0)
for e in DATA["entry"]:
    if e["type"] == "OUT" and not e["is_void"] and e["material"] in stock:
        stock[e["material"]] -= float(e["qty"] or 0)
    if e["type"] == "IN" and not e["is_void"] and e["material"] in stock:
        stock[e["material"]] += float(e["qty"] or 0)
for mri in DATA["material_return_item"]:
    mr = next(r for r in DATA["material_return"] if r["id"] == mri["material_return_id"])
    if not mr["is_void"] and mri["material_name"] in stock:
        stock[mri["material_name"]] += float(mri["qty"] or 0)
for m in DATA["material"]:
    m["total"] = max(0.0, money(stock.get(m["name"], 0)))

# ----------------------------------------------------------------------------
# write workbook
# ----------------------------------------------------------------------------

import pandas as pd  # noqa: E402

app = create_app()


def _cell(value, col):
    """Serialize one python value for Excel the way the app's export does."""
    if value is None:
        return None
    if isinstance(value, bool):
        return 1 if value else 0
    if isinstance(value, datetime):
        return value.replace(tzinfo=None, microsecond=0).strftime("%Y-%m-%d %H:%M:%S")
    if isinstance(value, date):
        return value.strftime("%Y-%m-%d")
    if isinstance(value, float):
        return round(value, 6)
    return value


def finalize_rows(table_name):
    table = db.metadata.tables[table_name]
    rows = []
    for r in DATA.get(table_name, []):
        rows.append({c.name: _cell(r.get(c.name), c) for c in table.columns})
    return rows


# Tables intentionally NOT shipped in the workbook:
#   * user_login_session / tenant_wipe_backup_history — excluded by the app itself
#   * settings / schema_version / system_lock / root_* — live app configuration;
#     a headers-only sheet would erase them in Overwrite mode
#   * import_* / migration_* / booking_allocation_repair_archive — operational
#     bookkeeping that must never be replaced by dummy rows
INFRA_EXCLUDED = {
    "user_login_session", "tenant_wipe_backup_history",
    "settings", "schema_version", "system_lock", "root_recovery_code",
    "root_backup_settings", "root_backup_email_history",
    "future_account_audit_log", "booking_allocation_repair_archive",
    "import_upload", "import_job", "import_history_entry",
    "migration_run", "migration_row", "migration_mapping",
}

with app.app_context():
    tables = [t for t in db.metadata.sorted_tables if t.name not in INFRA_EXCLUDED]

    # ---- validation before writing -----------------------------------------
    errors = []

    # 1) non-nullable columns must be present
    for t in tables:
        for r in DATA.get(t.name, []):
            for c in t.columns:
                v = r.get(c.name)
                if (not c.nullable) and c.name not in {p.name for p in t.primary_key.columns} and v is None:
                    errors.append(f"{t.name}.{c.name}: NULL in non-nullable column")
            if v is None and not any(r.get(c.name) is not None for c in t.columns):
                errors.append(f"{t.name}: empty row")

    # 2) PK + FK integrity inside the workbook
    pk_index = {}
    fk_missing = []
    for t in tables:
        pk_cols = [c.name for c in t.primary_key.columns]
        seen = set()
        for r in DATA.get(t.name, []):
            key = tuple(r.get(c) for c in pk_cols)
            if len(key) == 1:
                key = key[0]
            if key in seen:
                errors.append(f"{t.name}: duplicate PK {key}")
            seen.add(key)
        pk_index[t.name] = seen
    for t in tables:
        for fk in t.foreign_keys:
            parent = fk.column.table.name
            for r in DATA.get(t.name, []):
                v = r.get(fk.parent.name)
                if v is not None and v not in pk_index.get(parent, set()):
                    fk_missing.append(f"{t.name}.{fk.parent.name}={v} -> {parent}")
    errors.extend(fk_missing[:30])

    # 3) unique-ish business keys
    for tname, col in [("delivery_person", "name"), ("staff_email", "email"),
                       ("cash_flow_difference_adjustment", "adjustment_date"),
                       ("system_lock", "name")]:
        vals = [r.get(col) for r in DATA.get(tname, [])]
        if len(vals) != len(set(vals)):
            errors.append(f"{tname}.{col}: duplicate values")
    for tname, col in [("direct_sale", "auto_bill_no"), ("booking", "auto_bill_no"),
                       ("invoice", "invoice_no"), ("material_return", "auto_bill_no"),
                       ("grn", "auto_bill_no"), ("payment", "auto_bill_no"),
                       ("supplier_payment", "auto_bill_no")]:
        vals = [r.get(col) for r in DATA.get(tname, []) if r.get(col)]
        if len(vals) != len(set(vals)):
            errors.append(f"{tname}.{col}: duplicate bill numbers")

    if errors:
        print("VALIDATION FAILED:")
        for e in errors[:50]:
            print("  -", e)
        sys.exit(1)

    # ---- write sheets -------------------------------------------------------
    from openpyxl import Workbook

    wb = Workbook(write_only=True)
    total_rows = 0
    for t in tables:
        ws = wb.create_sheet(title=t.name[:31])
        headers = [c.name for c in t.columns]
        ws.append(headers)
        rows = finalize_rows(t.name)
        for r in rows:
            ws.append([r[h] for h in headers])
        total_rows += len(rows)
        print(f"  sheet {t.name[:31]:36s} rows={len(rows)}")
    # NOTE: no __AMS_META__ sheet on purpose — the Import page auto-detects
    # this workbook as "Literal Full Raw" via its physical-table sheet names,
    # and omitting the meta sheet keeps the per-table report free of
    # "expected sheet missing" warnings for the infra tables above.
    os.makedirs(os.path.dirname(OUT_PATH), exist_ok=True)
    wb.save(OUT_PATH)

    print(f"\nWrote {OUT_PATH}")
    print(f"Total data rows: {total_rows}")
    print("\nPer-table summary:")
    for t in tables:
        n = len(DATA.get(t.name, []))
        if n:
            print(f"  {t.name:42s} {n:>6}")
    print("\nAccount balances after dummy ledger:")
    for a in DATA["account"]:
        print(f"  {a['name']:28s} closing={acc_balance[a['id']]:>14,.2f}")
