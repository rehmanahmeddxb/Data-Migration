"""Regression tests for the Daily Cash & Bank Reconciliation board and the
Financial Tracking Filter Matrix (reference layouts), including the
lock -> next-day-opening carry-forward behaviour.
"""
from __future__ import annotations

from datetime import date, datetime, timedelta

ADMIN = {"username": "Admin", "password": "Admin@fbm12345"}


def _login(client):
    return client.post("/login", data=dict(ADMIN), follow_redirects=True)


def _mk_account(db, Account, name, category, opening):
    acc = Account(
        name=name,
        type="CASH" if category == "cash" else "BANK",
        account_type="company",
        category=category,
        balance=opening,
        opening_balance=opening,
        opening_balance_date=datetime(2026, 1, 1),
        is_active=True,
    )
    db.session.add(acc)
    db.session.commit()
    return acc


def _record(direction, amount, account, day, dest=None):
    from app.services.cash_flow_svc import save_manual_cash_flow_entry
    entry, created = save_manual_cash_flow_entry(
        direction=direction,
        amount=amount,
        account_id=account.id,
        destination_account_id=dest.id if dest else None,
        category_name="SALE" if direction == "in" else ("TRANSFER" if direction == "transfer" else "EXPENSE"),
        party_name="TEST PARTY",
        party_type="person",
        date_posted=datetime(day.year, day.month, day.day, 12, 0),
        create_missing=True,
    )
    assert created
    return entry


def test_reconciliation_page_renders(app, client):
    from models import db, Account
    with app.app_context():
        _mk_account(db, Account, "FBM CASH IN HAND", "cash", 1000.0)
    _login(client)
    rv = client.get("/daily_reconciliation")
    assert rv.status_code == 200
    body = rv.get_data(as_text=True)
    assert "Daily Cash &amp; Bank Reconciliation" in body
    assert "OPEN FOR RECONCILIATION" in body
    assert "ACCOUNT POSITIONS FOR" in body
    assert "FBM CASH IN HAND" in body


def test_financial_tracker_renders_and_exports(app, client):
    from models import db, Account
    with app.app_context():
        acc = _mk_account(db, Account, "FBM CASH IN HAND", "cash", 500.0)
        _record("in", 200, acc, date.today())
        db.session.commit()
    _login(client)
    rv = client.get("/financial_tracker")
    assert rv.status_code == 200
    body = rv.get_data(as_text=True)
    assert "FINANCIAL TRACKING FILTER MATRIX" in body
    assert "Matching Records" in body
    csv = client.get("/financial_tracker?export_csv=1")
    assert csv.status_code == 200
    assert "text/csv" in csv.headers["Content-Type"]


def test_lock_carries_counted_to_next_day_opening(app, client):
    from models import db, Account
    from app.services import cash_day_recon as recon

    with app.app_context():
        acc = _mk_account(db, Account, "FBM CASH IN HAND", "cash", 1000.0)
        today = date.today()
        _record("in", 500, acc, today)
        _record("out", 100, acc, today)
        db.session.commit()

    _login(client)
    day = date.today().isoformat()

    # expected closing = 1000 + 500 - 100 = 1400
    with app.app_context():
        pos = recon.account_positions_for_date(day)
        cash = [p for p in pos if p["account_name"].startswith("FBM CASH IN HAND")][0]
        assert cash["expected_closing"] == 1400.0
        acc_id = cash["account_id"]

    # Count 1,390 (Rs.10 short) then lock the day via the page route.
    rv = client.post("/daily_reconciliation", data={
        "action": "save_count", "day": day, "account_id": acc_id, "counted": "1390",
    }, follow_redirects=True)
    assert rv.status_code == 200
    rv = client.post("/daily_reconciliation", data={
        "action": "lock", "day": day, "note": "close",
    }, follow_redirects=True)
    assert rv.status_code == 200
    assert "locked" in rv.get_data(as_text=True).lower()

    tomorrow = (date.today() + timedelta(days=1)).isoformat()
    with app.app_context():
        lock = recon.get_day_lock(day)
        assert lock is not None
        assert lock.total_counted == 1390.0
        npos = recon.account_positions_for_date(tomorrow)
        ncash = [p for p in npos if p["account_name"].startswith("FBM CASH IN HAND")][0]
        # The locked counted closing (1,390) becomes tomorrow's opening.
        assert ncash["opening"] == 1390.0
        # clean up lock so other tests unaffected
        recon.unlock_day(day)


def test_recon_board_forms_carry_server_rendered_csrf(app, client):
    """Regression: the save/clear/lock/unlock forms must render a hidden
    ``_csrf_token`` server-side. The board posts via fetch(FormData); if the
    layout JS token injection ever fails (stale page, JS error on the large
    cash_flow page), a token-less post was rejected with 400 and the row just
    showed a red "Error" with no explanation."""
    import re
    from models import db, Account
    with app.app_context():
        acc = _mk_account(db, Account, "FBM CASH IN HAND", "cash", 1000.0)
        acc_id = acc.id

    _login(client)
    rv = client.get("/daily_reconciliation")
    body = rv.get_data(as_text=True)
    assert rv.status_code == 200

    # Every POST form in the board must have the token baked in.
    post_forms = re.findall(r'<form method="POST".*?</form>', body, re.S)
    assert post_forms
    for f in post_forms:
        assert 'name="_csrf_token"' in f

    # The embedded board on /cash_flow must carry the token too.
    body2 = client.get("/cash_flow?recon_day=" + date.today().isoformat()).get_data(as_text=True)
    embed_forms = re.findall(r'<form method="POST"[^>]*data-dr-(?:count|clear)-form.*?</form>', body2, re.S)
    assert embed_forms
    for f in embed_forms:
        assert 'name="_csrf_token"' in f

    # Raw client (no automatic X-CSRF-Token header / body injection): the only
    # token sent is the server-rendered field inside the form, exactly like a
    # browser whose layout JS did not patch fetch.
    raw = app.test_client()
    with raw.session_transaction() as sess:
        sess["_csrf_token"] = "recon-raw-csrf"
    raw.post("/login", data={
        "username": "Admin", "password": "Admin@fbm12345",
        "_csrf_token": "recon-raw-csrf",
    })
    board = raw.get("/daily_reconciliation").get_data(as_text=True)
    form_token = re.search(
        r'data-dr-count-form>(?:(?!</form>).)*?name="_csrf_token" value="([^"]+)"',
        board, re.S,
    ).group(1)
    rv = raw.post(
        "/daily_reconciliation",
        data={
            "action": "save_count", "day": date.today().isoformat(),
            "account_id": acc_id, "counted": "999",
            "_csrf_token": form_token,
        },
        headers={"X-Requested-With": "XMLHttpRequest", "Accept": "application/json"},
    )
    assert rv.status_code == 200
    payload = rv.get_json()
    assert payload["ok"] is True

    # A stale/garbage token must still be rejected (CSRF gate stays effective).
    rv = raw.post(
        "/daily_reconciliation",
        data={
            "action": "save_count", "day": date.today().isoformat(),
            "account_id": acc_id, "counted": "1000",
            "_csrf_token": "not-the-real-token",
        },
        headers={"X-Requested-With": "XMLHttpRequest", "Accept": "application/json"},
    )
    assert rv.status_code == 400
    assert rv.get_json().get("ok") is not True


def test_save_count_without_action_field_still_persists(app, client):
    """Regression: fetch(FormData) omits the submit button. Enter or an
    unfocused Save click posted counted+account_id with no ``action``, the
    server rejected it, and the row showed a red Error with no save."""
    from models import db, Account, CashDayAccountPosition

    with app.app_context():
        acc = _mk_account(db, Account, "FBM CASH IN HAND", "cash", 250000.0)
        acc_id = acc.id

    _login(client)
    day = date.today().isoformat()
    rv = client.post(
        "/daily_reconciliation",
        data={
            "day": day,
            "account_id": acc_id,
            "counted": "250000",
        },
        headers={"X-Requested-With": "XMLHttpRequest", "Accept": "application/json"},
    )
    assert rv.status_code == 200
    payload = rv.get_json()
    assert payload["ok"] is True
    cash = [p for p in payload["positions"] if p["account_id"] == acc_id][0]
    assert cash["counted"] == 250000.0
    assert cash["difference"] == 0.0

    with app.app_context():
        row = CashDayAccountPosition.query.filter_by(
            position_date=date.today(), account_id=acc_id
        ).first()
        assert row is not None
        assert float(row.counted) == 250000.0


def test_recon_count_forms_include_hidden_action(app, client):
    from models import db, Account

    with app.app_context():
        _mk_account(db, Account, "FBM CASH IN HAND", "cash", 1000.0)

    _login(client)
    body = client.get("/daily_reconciliation").get_data(as_text=True)
    assert 'data-dr-count-form' in body
    assert 'name="action" value="save_count"' in body
    assert 'name="action" value="clear_count"' in body
    # JS must not pick the action from whatever field currently has focus.
    assert "document.activeElement && document.activeElement.form === form" not in body
    assert "ev.submitter" in body
