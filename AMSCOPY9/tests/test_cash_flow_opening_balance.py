"""Regression test: Cash Flow automatic opening must include the opening
balances of company CASH accounts so that

    opening + received - spent == cash account balance

which is exactly the reconciliation the dashboard promises.

Mirrors the real-world report where FBM CASH IN HAND had opening_balance
2,230 and period movements of +271,600 / -21,500, but the Cash Flow report
showed Opening Rs. 0 and Closing Rs. 250,100 while the account balance was
Rs. 252,330 — a 2,230 gap.
"""
from __future__ import annotations

from datetime import date, datetime, timedelta


def test_automatic_opening_includes_cash_account_opening_balance(app):
    from models import db, Account
    from app.services.cash_flow_svc import (
        _automatic_cash_opening_balance,
        _cash_accounts_opening_total,
    )

    with app.app_context():
        acc = Account(
            name="FBM CASH IN HAND",
            type="CASH",
            account_type="company",
            category="cash",
            balance=2230.0,
            opening_balance=2230.0,
            opening_balance_date=datetime(2026, 1, 1),
            is_active=True,
        )
        db.session.add(acc)
        db.session.commit()

        assert _cash_accounts_opening_total(date(2026, 7, 28)) == 2230.0

        # No transactions before the period: opening must equal the
        # account's opening balance, not zero.
        opening = _automatic_cash_opening_balance(date(2026, 7, 28))
        assert opening == 2230.0

        # closing = opening + in - out must tie to the account balance
        received, spent = 271600.0, 21500.0
        closing = opening + received - spent
        assert closing == 252330.0  # == account balance after movements


def test_opening_dated_after_period_start_is_excluded(app):
    from models import db, Account
    from app.services.cash_flow_svc import _cash_accounts_opening_total

    with app.app_context():
        acc = Account(
            name="NEW TILL",
            type="CASH",
            account_type="company",
            category="cash",
            balance=5000.0,
            opening_balance=5000.0,
            opening_balance_date=datetime(2026, 9, 15),
            is_active=True,
        )
        db.session.add(acc)
        db.session.commit()

        # Period starting before the account opening date must not count it.
        assert _cash_accounts_opening_total(date(2026, 7, 28)) == 0.0
        # ...but a period starting on/after the opening date does.
        assert _cash_accounts_opening_total(date(2026, 9, 15)) == 5000.0


def test_bank_opening_balances_are_not_counted_as_cash(app):
    from models import db, Account
    from app.services.cash_flow_svc import _cash_accounts_opening_total

    with app.app_context():
        db.session.add(Account(
            name="BANK ALFLAH",
            type="BANK",
            account_type="company",
            category="bank",
            balance=190000.0,
            opening_balance=190000.0,
            is_active=True,
        ))
        db.session.commit()

        assert _cash_accounts_opening_total(date(2026, 7, 28)) == 0.0


def test_physical_count_anchor_does_not_double_count_opening(app):
    """Once a physical cash count exists, it is the absolute cash figure;
    the account opening balance must NOT be added on top of it."""
    from models import db, Account, CashFlowDifferenceAdjustment
    from app.services.cash_flow_svc import _automatic_cash_opening_balance

    with app.app_context():
        db.session.add(Account(
            name="FBM CASH IN HAND",
            type="CASH",
            account_type="company",
            category="cash",
            balance=252330.0,
            opening_balance=2230.0,
            is_active=True,
        ))
        db.session.add(CashFlowDifferenceAdjustment(
            adjustment_date=date(2026, 8, 27),
            amount=0.0,
            physical_cash_available=252330.0,
        ))
        db.session.commit()

        opening = _automatic_cash_opening_balance(date(2026, 8, 28))
        assert opening == 252330.0
