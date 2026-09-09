"""Pages for the SQLite .db backup/restore channel (import_export blueprint).

Routes
------
``/db_transfer``        — the SQLite DB file center page (export + restore UI)
``/db/export``          — POST: download a full DB snapshot (.db)
``/db/restore/preview`` — POST: validate an upload, return a JSON summary
``/db/restore``         — POST: execute a restore (mode = replace | append)

All routes are admin-only via the blueprint's ``before_request`` guard and
every operation is written to the audit log.
"""
from __future__ import annotations

import io
import json
import logging
import os

from flask import jsonify, render_template, request, send_file

from ._common import (
    import_export_bp,
    login_required,
    current_user,
    flash,
    redirect,
    url_for,
)
from .db_file_engine import (
    export_db_snapshot_bytes,
    preview_db_snapshot,
    run_db_snapshot_import,
)
from .hash_io import _download_filename
from utils.audit import audit_log

LOG = logging.getLogger(__name__)


def _wants_json():
    if (request.args.get("format") or request.form.get("format") or "").lower() == "json":
        return True
    if request.headers.get("X-Requested-With") == "fetch":
        return True
    accept = (request.headers.get("Accept") or "").lower()
    return "application/json" in accept


def _audit(action: str, details: dict):
    try:
        audit_log(current_user, action, json.dumps(details, sort_keys=True)[:1200])
    except Exception as e:  # noqa: BLE001 — audit must never break the action
        LOG.warning("audit failed for %s: %s", action, e)


def _snapshot_summary():
    """Read-only numbers for the page header (live DB size/rows/tables)."""
    import sqlite3
    from .db_file_engine import _live_db_path, _table_list, _table_counts
    path = _live_db_path()
    if not path:
        return {"available": False}
    if not os.path.exists(path):
        return {"available": False}
    try:
        con = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
        try:
            tables = _table_list(con)
            return {
                "available": True,
                "size_mb": round(os.path.getsize(path) / (1024 * 1024), 2),
                "tables": len(tables),
                "rows": sum(_table_counts(con, tables).values()),
                "integrity": con.execute("PRAGMA integrity_check").fetchone()[0],
            }
        finally:
            con.close()
    except Exception as e:  # noqa: BLE001
        return {"available": False, "error": str(e)}


@import_export_bp.route("/db_transfer")
@login_required
def db_transfer_page():
    return render_template(
        "db_file_transfer.html",
        summary=_snapshot_summary(),
        active_module="import_export",
    )


@import_export_bp.route("/db/export", methods=["POST"])
@login_required
def db_export():
    try:
        payload = export_db_snapshot_bytes()
    except Exception as e:  # noqa: BLE001
        LOG.exception("db export failed")
        if _wants_json():
            return jsonify({"ok": False, "error": str(e)}), 500
        flash(f"Database export failed: {e}", "danger")
        return redirect(url_for("import_export.db_transfer_page"))
    fname = _download_filename("AMSDB", "db")
    _audit("data.db_snapshot_export", {"file": fname, "bytes": len(payload)})
    return send_file(
        io.BytesIO(payload),
        as_attachment=True,
        download_name=fname,
        mimetype="application/x-sqlite3",
    )


@import_export_bp.route("/db/restore/preview", methods=["POST"])
@login_required
def db_restore_preview():
    f = request.files.get("file")
    if not f:
        return jsonify({"ok": False, "error": "No file uploaded."}), 400
    try:
        raw = f.read()
        if not raw:
            return jsonify({"ok": False, "error": "Uploaded file is empty."}), 400
        out = preview_db_snapshot(raw)
        return jsonify(out)
    except Exception as e:  # noqa: BLE001
        LOG.exception("db restore preview failed")
        return jsonify({"ok": False, "error": str(e)}), 400


@import_export_bp.route("/db/restore", methods=["POST"])
@login_required
def db_restore():
    f = request.files.get("file")
    if not f:
        return jsonify({"ok": False, "error": "No file uploaded."}), 400
    mode = (request.form.get("mode") or "append").strip().lower()
    if mode not in ("replace", "replace_tenant_data"):
        mode = "append"
    if mode.startswith("replace"):
        confirm = (request.form.get("confirm") or "").strip().upper()
        if confirm != "REPLACE":
            return jsonify({
                "ok": False,
                "error": "Type REPLACE to confirm the clean-and-restore. "
                         "This wipes the selected app tables first.",
            }), 400
    try:
        raw = f.read()
        report = run_db_snapshot_import(raw, mode)
    except Exception as e:  # noqa: BLE001
        LOG.exception("db restore failed")
        try:
            from ._common import db
            db.session.rollback()
        except Exception:  # noqa: BLE001
            pass
        return jsonify({"ok": False, "error": str(e)}), 400

    _audit("data.db_snapshot_restore", {
        "mode": report.get("mode"),
        "file": f.filename or "",
        "inserted": report.get("inserted"),
        "skipped": report.get("skipped"),
        "tables": len(report.get("tables") or []),
        "relaxed_count": len(report.get("relaxed") or []),
        "ok": report.get("ok"),
    })
    if not report.get("ok"):
        report["flash"] = "warning"
        return jsonify(report), 200
    if _wants_json():
        return jsonify(report)
    flash(
        f"DB restore {report.get('mode')} complete: "
        f"{report.get('inserted')} rows restored.",
        "success")
    return redirect(url_for("import_export.db_transfer_page"))
