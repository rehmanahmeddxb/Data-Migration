"""Public deployment routes registered on the Flask app.

* ``/health``        — unauthenticated liveness/readiness probe used by the
                       deploy pipeline (and by PythonAnywhere / GitHub
                       Actions) to confirm the app imported and the DB is
                       reachable. No business data is exposed.
* ``/git-auto-pull`` — GitHub push webhook that triggers the config-driven
                       automatic deployer (deploy.deployer).

Authentication for POST ``/git-auto-pull`` (any ONE of, in this order):

1. **GitHub native webhook signature** — the ``X-Hub-Signature-256`` header
   (legacy ``X-Hub-Signature`` sha1 also accepted) is the HMAC of the raw
   request body keyed with the same secret configured in the webhook's
   "secret" field.  This is what a GitHub repository webhook *actually
   sends*, so with ``webhook secret == AMS_WEBHOOK_TOKEN`` a plain payload
   URL of ``https://<domain>/git-auto-pull`` works with no token in the URL.
2. **Shared token** — ``?token=...`` query string or ``X-Deploy-Token``
   header, compared in constant time.  Used by the GitHub Actions trigger
   and by the documented ``?token=`` payload-URL variant.

Both are registered in the app factory so they work regardless of whether
the process is started via ``wsgi.py`` (PythonAnywhere) or ``main.py``
(local). All deployment settings come from ``config.py``.
"""
from __future__ import annotations

import hashlib
import hmac
import logging
import threading

from flask import jsonify, request

logger = logging.getLogger("AMS-Deploy")


def _github_signature_ok(body: bytes, secret: str) -> bool:
    """True when the request carries a valid GitHub HMAC signature.

    GitHub signs the raw payload with the webhook's "secret" and sends the
    digest in ``X-Hub-Signature-256`` (and the legacy sha1 ``X-Hub-Signature``).
    When both headers are present, sha256 is authoritative.
    """
    sig256 = (request.headers.get("X-Hub-Signature-256") or "").strip()
    if sig256.startswith("sha256="):
        expected = hmac.new(secret.encode("utf-8"), body, hashlib.sha256).hexdigest()
        return hmac.compare_digest(sig256[len("sha256="):], expected)
    sig1 = (request.headers.get("X-Hub-Signature") or "").strip()
    if sig1.startswith("sha1="):
        expected = hmac.new(secret.encode("utf-8"), body, hashlib.sha1).hexdigest()
        return hmac.compare_digest(sig1[len("sha1="):], expected)
    return False


def register_deploy_routes(app):
    from config import get_config
    from deploy import deployer

    cfg = get_config()
    gh = cfg["github"]
    branch_ref = f"refs/heads/{gh['branch']}"

    @app.route("/health")
    def health():
        """Lightweight public health check (no secrets, no sensitive data)."""
        status = "healthy"
        db_ok = True
        try:
            from models import db
            from sqlalchemy import text

            db.session.execute(text("SELECT 1")).scalar()
        except Exception as exc:  # pragma: no cover - environmental
            db_ok = False
            status = "degraded"
            logger.warning("Health DB probe failed: %s", exc)
        try:
            from config import get_config as _gc
            import subprocess

            head = subprocess.run(
                ["git", "rev-parse", "--short", "HEAD"],
                capture_output=True,
                text=True,
                timeout=5,
            )
            commit = head.stdout.strip() if head.returncode == 0 else None
        except Exception:
            commit = None
        return (
            jsonify(
                {
                    "status": status if db_ok else "unhealthy",
                    "database": "ok" if db_ok else "error",
                    "app": cfg["app"]["name"],
                    "branch": gh["branch"],
                    "commit": commit,
                }
            ),
            200 if db_ok else 503,
        )

    @app.route("/git-auto-pull", methods=["GET", "POST"])
    def git_auto_pull():
        token = (
            request.args.get("token", "", type=str).strip()
            or (request.headers.get("X-Deploy-Token") or "").strip()
        )
        expected = deployer.webhook_token()
        if request.method == "GET":
            # A browser/monitor probe: report online without revealing whether
            # the deploy secret is present (no deployment can be triggered by
            # GET, so this is safe to expose).
            return jsonify(
                {"success": True, "service": "AMS Git Auto Pull", "status": "online"}
            ), 200
        # POST actually triggers a deploy and must be authenticated.
        if not expected:
            # No token configured on the server -> refuse rather than deploy.
            logger.error("Webhook called but %s is not set.", cfg["secrets"]["webhook_token_env"])
            return jsonify({"success": False, "message": "Deploy token not configured"}), 503

        # Authenticate: prefer the GitHub native webhook signature (what a
        # repository webhook actually sends), fall back to the shared token.
        raw_body = request.get_data(cache=True)
        if _github_signature_ok(raw_body, expected):
            authorized = True
        elif token and hmac.compare_digest(
            token.encode("utf-8"), expected.encode("utf-8")
        ):
            authorized = True
        else:
            authorized = False
        if not authorized:
            logger.warning(
                "Unauthorized deployment request (missing/invalid token or "
                "GitHub signature). X-GitHub-Event=%r",
                request.headers.get("X-GitHub-Event"),
            )
            return jsonify({"success": False, "message": "Unauthorized"}), 403

        event = request.headers.get("X-GitHub-Event", "")
        if event and event != "push":
            return jsonify({"success": True, "message": "Event ignored"}), 200

        payload = request.get_json(silent=True) or {}
        ref = payload.get("ref", "")
        # Only deploy pushes to the configured branch.
        if ref and ref != branch_ref:
            return jsonify({"success": True, "message": "Branch ignored"}), 200

        if deployer._DEPLOY_LOCK.locked():
            return jsonify({"success": True, "message": "Deployment already running"}), 202

        def _run():
            try:
                deployer.deploy()
            except Exception:  # pragma: no cover - defensive
                logger.exception("Background deploy crashed.")

        threading.Thread(target=_run, daemon=True).start()
        return jsonify({"success": True, "message": "Deployment started"}), 202
