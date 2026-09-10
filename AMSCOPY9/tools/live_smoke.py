"""Read-only live smoke test: boot the app against the migrated DB, log in,
and GET every page. Reports any 500 / traceback / BuildError."""
from __future__ import annotations

import os
import re
from pathlib import Path

import sys
ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
os.chdir(ROOT)

os.environ.setdefault("ALLOW_EMPTY_DB", "1")
os.environ.setdefault("ALLOW_DB_DROP", "1")

from app import create_app
from models import db, Client, Material, Account, Supplier

GET_PAGES = [
    "/", "/clients", "/materials", "/suppliers", "/delivery_persons",
    "/bookings", "/payments", "/direct_sales", "/material_returns",
    "/pending_bills", "/grn", "/dispatching", "/tracking",
    "/ledger", "/decision_ledger", "/financial_details",
    "/cash_flow", "/cash_flow_differences", "/profit_reports",
    "/unpaid_transactions", "/mixed_transactions",
    "/daily_transactions", "/delivery_rents",
    "/notifications", "/notifications/upcoming",
    "/settings", "/settings/activity",
    "/import_export/", "/import_export/history", "/import_export/uploads",
    "/inventory/stock_summary", "/inventory/daily_transactions",
    "/stock_summary",
    "/accounts/", "/accounts/accounts", "/accounts/accounts/add",
    "/accounts/receipts", "/accounts/transfers", "/accounts/transfers/add",
    "/accounts/expenditures", "/accounts/payments/clients",
    "/accounts/payments/suppliers", "/accounts/audit",
    "/accounts/kpi/cash_money", "/accounts/kpi/bank_accounts",
    "/accounts/kpi/cash_accounts", "/accounts/kpi/client_payments",
    "/accounts/kpi/supplier_payments", "/accounts/kpi/expenditures",
    "/accounts/kpi/receipts", "/accounts/kpi/company_money",
    "/pay_supplier",
    "/admin/", "/admin/modules", "/admin/api/health", "/admin/api/modules",
    "/system_report", "/void_audit",
    "/api/notifications/due", "/api/client_next_code", "/api/material_next_code",
    "/api/clients/search", "/api/ui/theme",
]


def is_login_redirect(rv) -> bool:
    """True when the app bounced this request to the sign-in page.

    Every page here used to be fetched with follow_redirects=True and only
    judged on "is it a 200".  An anonymous request 302s to /login and then
    renders 200 — so a broken login was reported as "SMOKE PASS — all pages
    load".  Checking the redirect target is deterministic (no HTML heuristic:
    /settings legitimately contains username and password fields of its own).
    """
    if rv.status_code not in (301, 302, 303, 307, 308):
        return False
    return "/login" in (rv.headers.get("Location") or "")


def main():
    app = create_app()
    raw = app.test_client()
    c = raw
    problems = []

    # The app enforces the session-bound CSRF token on POST /login, so a bare
    # form post is rejected with 400.  Inject the token the way a browser
    # carries it, and take the credentials from the environment instead of
    # guessing the default admin password.
    with raw.session_transaction() as sess:
        token = sess.get("_csrf_token") or "live-smoke-csrf"
        sess["_csrf_token"] = token
    user = os.environ.get("AMS_SMOKE_USER") or os.environ.get("DEFAULT_ADMIN_USER") or "Admin"
    password = (os.environ.get("AMS_SMOKE_PASSWORD")
                or os.environ.get("DEFAULT_ADMIN_PASSWORD") or "Admin@fbm12345")
    rv = raw.post("/login", data={"username": user, "password": password,
                                  "remember_me": "1", "_csrf_token": token},
                  follow_redirects=False)
    if rv.status_code not in (302, 303):
        # A failed login makes every later page result meaningless: stop here.
        body = rv.get_data()[:400].decode("utf-8", "replace")
        print(f"LOGIN FAILED: HTTP {rv.status_code} — {user!r} could not sign in. "
              f"Set AMS_SMOKE_USER / AMS_SMOKE_PASSWORD. Body: {body[:180]}")
        return 2
    print(f"LOGIN OK ({user})")

    def check(path, *, allow_redirect=False):
        """GET *path* and judge it. Returns True when the page is broken.

        Binary responses (PDF/Excel downloads) are decoded leniently — a
        UnicodeDecodeError in the harness used to be the only thing this could
        report on such a route, and a redirect to the sign-in form counts as a
        failure because it means the request was never authenticated.
        """
        nonlocal checked
        rv = c.get(path, follow_redirects=False)
        checked += 1
        if is_login_redirect(rv):
            return "not authenticated (redirected to /login)"
        if rv.status_code not in (200, 302):
            return f"HTTP {rv.status_code}"
        if rv.status_code == 200:
            # binary routes (PDF / Excel downloads) are decoded leniently
            html = rv.get_data().decode("utf-8", "replace")
            if any(x in html for x in (
                    "Traceback (most recent call last)", "BuildError",
                    "jinja2.exceptions", "Internal Server Error")):
                return "traceback in body"
        return None

    checked = 0
    for path in GET_PAGES:
        problem = check(path)
        if problem:
            problems.append(f"GET {path}: {problem}")
            print(f"  FAIL {path} -> {problem}")
        else:
            print(f"  ok   {path}")

    # dynamic pages with live IDs
    with app.app_context():
        cli = Client.query.filter(Client.is_active == True).first()
        mat = Material.query.first()
        acc = Account.query.filter(Account.is_active == True).first()
        sup = Supplier.query.first()
    extra = []
    if cli:
        extra += [f"/ledger/{cli.id}", f"/client_ledger/{cli.id}", f"/financial_ledger/{cli.id}"]
    if mat:
        extra.append(f"/material_ledger/{mat.id}")
    if acc:
        extra += [f"/accounts/ledger/{acc.id}"]
    if sup:
        extra += [f"/supplier_ledger/{sup.id}"]
    for path in extra:
        problem = check(path)
        print(f"  {'ok  ' if not problem else 'FAIL'} {path}"
              + (f" -> {problem}" if problem else ""))
        if problem:
            problems.append(f"GET {path}: {problem}")

    # key data sanity
    with app.app_context():
        counts = {
            "clients": Client.query.count(),
            "materials": Material.query.count(),
            "accounts": Account.query.count(),
            "suppliers": Supplier.query.count(),
        }
    print("COUNTS:", counts)

    print("=" * 60)
    if problems:
        print(f"SMOKE FAILURES: {len(problems)}")
        for p in problems:
            print("  -", p)
    else:
        print("SMOKE PASS — all pages load, no 500s")
    return 0 if not problems else 1


if __name__ == "__main__":
    raise SystemExit(main())
