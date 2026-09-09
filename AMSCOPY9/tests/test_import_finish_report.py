"""Import finish dialog payload.

The progress dialog must hand off to a result dialog that can tell the user:
* 100% success (nothing missed), or
* exactly which rows/entries were missed (skipped / failed / unavailable),
  with the downloadable issue report name for the full CSV.

These tests cover the JSON payload the dialog renders from.
"""
from __future__ import annotations

import io

import pandas as pd
import pytest

from tests.conftest import make_csrf_client


@pytest.fixture()
def import_app(app_factory):
    return app_factory(FULL_RAW_IMPORT_ENABLED="1")


@pytest.fixture()
def client(import_app):
    return make_csrf_client(import_app)


def login(client, username="Admin", password="Admin@fbm12345"):
    resp = client.post(
        "/login",
        data={"username": username, "password": password},
        follow_redirects=False,
    )
    assert resp.status_code in (302, 303), resp.get_data(as_text=True)[:300]


def _workbook(sheets):
    buf = io.BytesIO()
    with pd.ExcelWriter(buf, engine="openpyxl") as writer:
        for name, frame in sheets.items():
            frame.to_excel(writer, sheet_name=name, index=False)
        pd.DataFrame([{"key": "export_kind", "value": "literal_all"}]).to_excel(
            writer, sheet_name="__AMS_META__", index=False
        )
    return buf.getvalue()


def test_partial_import_payload_lists_missed_rows(import_app, client):
    login(client)
    from models import Material

    wb = _workbook({
        "material_category": pd.DataFrame([
            {"id": 8, "name": "Steel", "is_active": 1},
        ]),
        "material": pd.DataFrame([
            {"id": 11, "code": "FBMCEM-000011", "name": "OPC", "category_id": 8,
             "unit_price": 0, "total": 0, "unit": "Bags", "is_active": 1},
            {"id": 11, "code": "FBMCEM-000011", "name": "OPC DUP", "category_id": 8,
             "unit_price": 0, "total": 0, "unit": "Bags", "is_active": 1},
            {"id": 12, "code": "FBMCEM-000012", "name": "BAD FK", "category_id": 999,
             "unit_price": 0, "total": 0, "unit": "Bags", "is_active": 1},
        ]),
    })

    resp = client.post(
        "/import_export/transfer/import",
        data={
            "file": (io.BytesIO(wb), "partial.xlsx"),
            "sections": "literal_all",
            "mode": "append",
            "format": "json",
        },
        content_type="multipart/form-data",
        follow_redirects=False,
    )
    assert resp.status_code == 200, resp.get_data(as_text=True)[:500]
    payload = resp.get_json()

    # Condition: partial — the dialog must show the misses, not 100%.
    assert payload["ok"] is False
    assert payload["status"] == "partial"
    assert payload["inserted"] == 2          # category + first material row
    assert payload["skipped"] == 1           # duplicate primary key
    assert payload["failed"] >= 1            # FK-violating row

    # Row-level details for the finish dialog.
    issues = payload["issue_rows"]
    assert payload["issue_rows_count"] >= 3
    skipped = [i for i in issues if i["table"] == "material" and i["status"] == "skipped"]
    failed = [i for i in issues if i["table"] == "material" and i["status"] == "failed"]
    assert skipped and skipped[0]["reason"] == "duplicate_primary_key"
    assert skipped[0]["sheet_row"] == "3"    # Excel row number (header = row 1)
    assert failed and "FOREIGN KEY" in failed[0]["reason"].upper()
    assert failed[0]["sheet_row"] == "4"

    # Full CSV report is persisted and downloadable for the missed rows.
    assert payload.get("report_name"), payload
    csv_resp = client.get(
        "/import_export/full_raw_import_report/" + payload["report_name"],
        follow_redirects=True,
    )
    assert csv_resp.status_code == 200
    csv_text = csv_resp.get_data(as_text=True)
    assert "duplicate_primary_key" in csv_text
    assert "FOREIGN KEY" in csv_text.upper()

    # Valid rows were actually saved.
    with import_app.app_context():
        assert Material.query.filter_by(id=11).first() is not None
        assert Material.query.filter_by(id=12).first() is None


def test_import_result_payload_survives_empty_and_missing_issue_rows():
    """Empty reports must not raise TypeError: object of type 'int' has no len().

    The finish-dialog helper used ``len(report.get('issue_rows') or 0)``. When
    issue_rows was missing or empty, that became ``len(0)`` and crashed both
    the success JSON path and the JSON error handler that retries with {}.
    """
    from blueprints.import_export._pages_transfer_import import (
        _import_result_payload,
    )

    empty = _import_result_payload(False, "Import failed", {})
    assert empty["ok"] is False
    assert empty["headline"] == "Import failed"
    assert empty["issue_rows"] == []
    assert empty["issue_rows_count"] == 0
    assert empty["error_details"] == []

    listed = _import_result_payload(True, "ok", {
        "issue_rows": [
            {"table": "material", "status": "skipped", "reason": "duplicate_primary_key"},
        ],
    })
    assert listed["issue_rows_count"] == 1
    assert listed["issue_rows"][0]["reason"] == "duplicate_primary_key"


def test_json_import_without_file_returns_error_payload(client):
    """POST /transfer/import with no file used to 500 while building JSON."""
    login(client)
    resp = client.post(
        "/import_export/transfer/import",
        data={"format": "json", "sections": "literal_all"},
        follow_redirects=False,
    )
    assert resp.status_code == 400, resp.get_data(as_text=True)[:500]
    payload = resp.get_json()
    assert payload["ok"] is False
    assert payload["issue_rows"] == []
    assert payload["issue_rows_count"] == 0
    assert "upload" in (payload.get("headline") or "").lower()


def test_full_success_payload_reports_100_percent(import_app, client):
    login(client)
    from models import Material

    wb = _workbook({
        # Empty (header-only) user sheet: a literal full backup that has no
        # user sheet at all would add a "user sheet missing" warning, which is
        # not what this 100% test is about.
        "user": pd.DataFrame(columns=["username", "role", "status"]),
        "material_category": pd.DataFrame([
            {"id": 8, "name": "Steel", "is_active": 1},
        ]),
        "material": pd.DataFrame([
            {"id": 11, "code": "FBMCEM-000011", "name": "OPC", "category_id": 8,
             "unit_price": 0, "total": 0, "unit": "Bags", "is_active": 1},
        ]),
    })

    resp = client.post(
        "/import_export/transfer/import",
        data={
            "file": (io.BytesIO(wb), "clean.xlsx"),
            "sections": "literal_all",
            "modules": "materials",
            "mode": "append",
            "format": "json",
        },
        content_type="multipart/form-data",
        follow_redirects=False,
    )
    assert resp.status_code == 200, resp.get_data(as_text=True)[:500]
    payload = resp.get_json()

    # Condition: 100% — every row saved, nothing missed.
    assert payload["ok"] is True
    assert payload["status"] == "ok"
    assert payload["inserted"] == 2
    assert payload["updated"] == 0
    assert payload["skipped"] == 0
    assert payload["failed"] == 0
    assert payload["warnings"] == 0
    # Only informational entries: the user sheet was not in the selected
    # module, so it was left untouched (no rows missed).
    assert payload["issue_rows"] == [{
        "table": "user", "sheet_row": "", "status": "skipped_not_selected",
        "reason": "Sheet was not selected for this restore; existing data was kept.",
        "primary_key": "", "label": "",
    }]
    assert not payload.get("report_name")    # nothing to download

    with import_app.app_context():
        assert Material.query.filter_by(id=11).first() is not None
