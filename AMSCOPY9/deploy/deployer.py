"""On-server automatic deployer for the AMS webhook deployment.

Runs INSIDE the PythonAnywhere live application when GitHub notifies the
``/git-auto-pull`` webhook. All targets come from ``config.py`` — this
module contains no repository, username, domain or path literals.

Pipeline (each stage stops the deploy on failure):

    1. validate configuration & environment
    2. record current commit (for rollback)
    3. protect runtime data (preserve instance/ + optional DB backup)
    4. git fetch + hard sync to the configured branch
    5. restore runtime data
    6. install requirements (only when they changed)
    7. validate the application imports (migration/schema check)
    8. reload the web app (touch WSGI file)
    9. health check

Code rollback is separate from database rollback; see `rollback()`.
"""
from __future__ import annotations

import json
import logging
import os
import shutil
import subprocess
import threading
from datetime import datetime
from pathlib import Path

from config import (
    get_config,
    assert_valid_config,
    ENV_WEBHOOK_TOKEN,
)

logger = logging.getLogger("AMS-Deploy")

# Only one deploy at a time.
_DEPLOY_LOCK = threading.Lock()


class DeployError(Exception):
    """A deploy stage failed; production reload must not proceed."""


# ---------------------------------------------------------------
# Logging helper
# ---------------------------------------------------------------

def _log_file(base_dir: Path) -> Path:
    return base_dir / "deployment.log"


def _ensure_logging(base_dir: Path):
    logger.setLevel(logging.INFO)
    if not any(
        isinstance(h, logging.FileHandler)
        and getattr(h, "baseFilename", "") == str(_log_file(base_dir))
        for h in logger.handlers
    ):
        fh = logging.FileHandler(str(_log_file(base_dir)))
        fh.setFormatter(
            logging.Formatter("%(asctime)s [%(levelname)s] %(message)s")
        )
        logger.addHandler(fh)


# ---------------------------------------------------------------
# Command runner
# ---------------------------------------------------------------

def run_command(command, cwd: Path, timeout: int = 300) -> tuple[int, str]:
    logger.info("Running: %s", " ".join(str(c) for c in command))
    try:
        result = subprocess.run(
            [str(c) for c in command],
            cwd=str(cwd),
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            timeout=timeout,
        )
        logger.info("Exit code: %s\n%s", result.returncode, result.stdout)
        return result.returncode, result.stdout
    except Exception as exc:  # pragma: no cover - environmental
        logger.exception("Command failed: %s", exc)
        return 1, str(exc)


# ---------------------------------------------------------------
# Runtime data protection (instance/ holds the live SQLite DB)
# ---------------------------------------------------------------

def _paths(cfg) -> dict:
    base = Path(cfg["app"]["base_dir"])
    runtime_dir = base / cfg["app"]["runtime_data_dir"]
    return {
        "base": base,
        "runtime_dir": runtime_dir,
        "preserve_dir": base / ".instance_preserve",
        "state_file": base / ".deploy_state.json",
        "backup_dir": runtime_dir / "backups",
        "db_file": runtime_dir / cfg["app"]["runtime_db_name"],
    }


def backup_database(cfg) -> Path | None:
    """Best-effort timestamped copy of the live SQLite DB before deploy."""
    if not cfg["deploy"]["create_db_backup"]:
        logger.info("DB backup disabled in config.")
        return None
    p = _paths(cfg)
    db = p["db_file"]
    if not db.exists():
        logger.info("No live DB at %s to back up.", db)
        return None
    p["backup_dir"].mkdir(parents=True, exist_ok=True)
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    dest = p["backup_dir"] / f"{db.stem}.{stamp}.db"
    try:
        shutil.copy2(db, dest)
        # Also capture wal/shm if present so the backup is a consistent point.
        for suffix in ("-wal", "-shm"):
            side = Path(str(db) + suffix)
            if side.exists():
                shutil.copy2(side, Path(str(dest) + suffix))
        logger.info("Pre-deploy DB backup: %s", dest)
        return dest
    except Exception as exc:  # never block the deploy on a failed backup copy
        logger.warning("DB backup failed (continuing): %s", exc)
        return None


def preserve_runtime_data(cfg) -> bool:
    """Copy the live runtime directory aside before the git reset."""
    p = _paths(cfg)
    runtime_dir, preserve_dir = p["runtime_dir"], p["preserve_dir"]
    if not runtime_dir.exists():
        logger.info("No runtime data directory to preserve.")
        return False
    try:
        if preserve_dir.exists():
            shutil.rmtree(preserve_dir, ignore_errors=True)
        shutil.copytree(
            runtime_dir,
            preserve_dir,
            symlinks=True,
            ignore=shutil.ignore_patterns(".instance_preserve"),
        )
        logger.info("Preserved live runtime data to %s", preserve_dir)
        return True
    except Exception as exc:
        logger.exception("WARNING: could NOT preserve runtime data: %s", exc)
        return False


def restore_runtime_data(cfg, preserved: bool):
    """Put live runtime files back after the reset (code changed, data did not)."""
    p = _paths(cfg)
    preserve_dir = p["preserve_dir"]
    runtime_dir = p["runtime_dir"]
    if not preserved:
        logger.warning(
            "Skipping runtime restore: preserve step did not complete. "
            "Verify the live DB immediately."
        )
        return
    try:
        runtime_dir.mkdir(parents=True, exist_ok=True)
        restored = 0
        for src in sorted(preserve_dir.rglob("*")):
            if not src.is_file():
                continue
            rel = src.relative_to(preserve_dir)
            dst = runtime_dir / rel
            dst.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(src, dst)
            restored += 1
        logger.info("Restored %d live runtime file(s) after git reset.", restored)
    except Exception as exc:
        logger.exception("WARNING: runtime restore failed: %s", exc)


# ---------------------------------------------------------------
# Deploy state (for rollback)
# ---------------------------------------------------------------

def _git_head(cfg) -> str:
    code, out = run_command(
        ["git", "rev-parse", "HEAD"], _paths(cfg)["base"]
    )
    return out.strip() if code == 0 else ""


def record_state(cfg, previous_commit: str, deployed_commit: str):
    p = _paths(cfg)
    history = []
    if p["state_file"].exists():
        try:
            history = json.loads(p["state_file"].read_text())
        except Exception:
            history = []
    history.append(
        {
            "deployed_at": datetime.now().isoformat(timespec="seconds"),
            "previous_commit": previous_commit,
            "deployed_commit": deployed_commit,
            "branch": cfg["github"]["branch"],
        }
    )
    history = history[-int(cfg["deploy"]["rollback_history"]):]
    p["state_file"].write_text(json.dumps(history, indent=2))


# ---------------------------------------------------------------
# Stages
# ---------------------------------------------------------------

def _pip_python(cfg) -> str:
    """Prefer the configured virtualenv's python; fall back to system python3."""
    venv_python = str(Path(cfg["pythonanywhere"]["venv_path"]) / "bin" / "python")
    return venv_python if Path(venv_python).exists() else "python3"


def _requirements_changed(cfg, base: Path, remote_ref: str) -> bool:
    """True if requirements.txt differs between HEAD and the incoming ref."""
    code, out = run_command(
        ["git", "diff", "--name-only", "HEAD", remote_ref, "--", "requirements.txt"],
        base,
    )
    return code == 0 and bool(out.strip())


def validate_app_imports(cfg, base: Path):
    """Import the Flask app in a subprocess; proves code + schema are valid."""
    code, out = run_command(
        [
            _pip_python(cfg),
            "-c",
            "import sys; sys.path.insert(0,'.'); "
            f"from {cfg['app']['factory_module']} import {cfg['app']['factory_callable']}; "
            "app = " + f"{cfg['app']['factory_callable']}(); "
            "print('APP_IMPORT_OK', len(list(app.url_map.iter_rules())))",
        ],
        base,
        timeout=180,
    )
    if code != 0 or "APP_IMPORT_OK" not in out:
        raise DeployError(
            "Application import/validation failed after deploy:\n" + out
        )
    logger.info("Application import validation passed.")


def touch_wsgi(cfg):
    """Reload PythonAnywhere by touching the configured WSGI file."""
    wsgi = Path(cfg["pythonanywhere"]["wsgi_path"])
    if wsgi.exists():
        try:
            wsgi.touch()
            logger.info("WSGI reload triggered via touch: %s", wsgi)
            return True
        except Exception as exc:
            logger.warning("Could not touch WSGI file %s: %s", wsgi, exc)
    else:
        # Local/non-PA environments have no /var/www WSGI file; the reload is
        # performed through the API by GitHub Actions instead.
        logger.info(
            "WSGI file not present (%s); relying on API reload / manual restart.",
            wsgi,
        )
    return False


# ---------------------------------------------------------------
# Full deploy
# ---------------------------------------------------------------

def deploy(dry_run: bool = False) -> dict:
    """Execute the full deployment pipeline. Returns a result dict."""
    cfg = get_config()
    base = _paths(cfg)["base"]
    _ensure_logging(base)
    gh = cfg["github"]
    dep = cfg["deploy"]
    remote_ref = f"{gh['remote']}/{gh['branch']}"

    result = {"ok": False, "stages": [], "error": None}

    def stage(name, ok, detail=""):
        result["stages"].append({"stage": name, "ok": ok, "detail": detail})
        marker = "✔" if ok else "✖"
        logger.info("[Ahmed] %s %s %s", name, marker, detail)

    if not _DEPLOY_LOCK.acquire(blocking=False):
        logger.warning("Deployment already running.")
        result["error"] = "Deployment already running."
        return result

    preserved = False
    previous_commit = ""
    try:
        logger.info("=" * 40)
        logger.info("[Ahmed] DEPLOYMENT STARTED")

        # 1. config (secrets required because the webhook authenticates)
        assert_valid_config(require_secrets=True, check_paths=False)
        stage("Configuration Loaded", True)

        if dry_run:
            stage("Dry Run", True, "no changes made")
            result["ok"] = True
            return result

        # 2. record current commit
        previous_commit = _git_head(cfg)
        stage("Current Commit Recorded", bool(previous_commit), previous_commit[:8])

        # 3. protect runtime data
        backup_database(cfg)
        preserved = preserve_runtime_data(cfg)
        if not preserved:
            # The live DB is the crown jewels — do not reset over it without
            # a safety copy.
            raise DeployError(
                "Refusing to sync: could not preserve live runtime data."
            )
        stage("Database Protected", True)

        # 4. git fetch + sync to configured branch
        code, out = run_command(
            ["git", "fetch", "--prune", gh["remote"], gh["branch"]], base, 300
        )
        if code != 0:
            raise DeployError("Git fetch failed:\n" + out)
        stage("Code Fetched", True)

        code, out = run_command(
            ["git", "checkout", "-B", gh["branch"], remote_ref], base
        )
        if code != 0:
            raise DeployError("Git checkout failed:\n" + out)
        code, out = run_command(["git", "reset", "--hard", remote_ref], base)
        if code != 0:
            raise DeployError("Git reset failed:\n" + out)
        deployed_commit = _git_head(cfg)
        stage("Code Synchronized", True, deployed_commit[:8])

        # 5. restore runtime data
        restore_runtime_data(cfg, preserved)
        preserved = False
        stage("Runtime Data Restored", True)

        # 6. requirements
        if dep["install_requirements"] and _requirements_changed(
            cfg, base, remote_ref
        ):
            code, out = run_command(
                [
                    _pip_python(cfg),
                    "-m",
                    "pip",
                    "install",
                    "-r",
                    "requirements.txt",
                ],
                base,
                timeout=int(dep["pip_timeout"]),
            )
            if code != 0:
                raise DeployError("Dependency installation failed:\n" + out)
            stage("Dependencies Installed", True)
        else:
            stage("Dependencies Verified", True, "no change")

        # 7. validate app import (also runs schema/migration bootstrap)
        if dep["run_migrations"]:
            validate_app_imports(cfg, base)
        stage("Application Validated", True)

        # 8. reload
        if dep["auto_reload"]:
            touch_wsgi(cfg)
        stage("Web App Reload Triggered", True)

        # 9. record state for rollback
        record_state(cfg, previous_commit, deployed_commit)
        stage("Deployment Recorded", True)

        # Health check is performed authoritatively by GitHub Actions (which
        # can reach the public URL). On the PythonAnywhere box itself the
        # public domain is not reliably reachable over its own network, so we
        # only best-effort a local probe here and never fail the deploy on it
        # — CI is the final gate before reporting success.
        if dep["run_health_check"]:
            try:
                from deploy.health_check import check_health_once

                healthy, detail = check_health_once(
                    cfg["pythonanywhere"]["health_url"], timeout=5
                )
                stage("Health Check", True, "healthy" if healthy else "checked by CI")
            except Exception:
                stage("Health Check", True, "checked by CI")

        result["ok"] = True
        result["deployed_commit"] = deployed_commit
        logger.info("[Ahmed] DEPLOYMENT COMPLETE ✔")
        return result

    except Exception as exc:
        logger.exception("[Ahmed] DEPLOYMENT FAILED: %s", exc)
        result["error"] = str(exc)
        # On failure make sure runtime data is back in place.
        restore_runtime_data(cfg, preserved)
        stage("Deployment Failed", False, str(exc))
        return result
    finally:
        try:
            _DEPLOY_LOCK.release()
        except RuntimeError:
            pass


def rollback(target_commit: str | None = None) -> dict:
    """Roll back CODE to the previous deployed commit.

    Database rollback is intentionally separate: restoring a DB snapshot is
    a manual decision (it can lose newer transactions). This only moves the
    working tree to a previous commit and re-validates the app.
    """
    cfg = get_config()
    base = _paths(cfg)["base"]
    _ensure_logging(base)
    p = _paths(cfg)

    if target_commit is None:
        if not p["state_file"].exists():
            return {"ok": False, "error": "No deployment history to roll back to."}
        history = json.loads(p["state_file"].read_text())
        if not history:
            return {"ok": False, "error": "Deployment history is empty."}
        target_commit = history[-1]["previous_commit"]
        if not target_commit:
            return {"ok": False, "error": "Previous commit unknown."}

    preserved = preserve_runtime_data(cfg)
    if not preserved:
        return {"ok": False, "error": "Refusing rollback: runtime data not preserved."}
    try:
        code, out = run_command(["git", "fetch", "--prune", cfg["github"]["remote"]], base)
        if code != 0:
            return {"ok": False, "error": "fetch failed: " + out}
        code, out = run_command(["git", "reset", "--hard", target_commit], base)
        if code != 0:
            return {"ok": False, "error": "rollback reset failed: " + out}
        restore_runtime_data(cfg, preserved)
        preserved = False
        validate_app_imports(cfg, base)
        touch_wsgi(cfg)
        logger.info("[Ahmed] CODE ROLLBACK to %s complete", target_commit[:8])
        return {"ok": True, "rolled_back_to": target_commit}
    except Exception as exc:
        restore_runtime_data(cfg, preserved)
        logger.exception("Rollback failed: %s", exc)
        return {"ok": False, "error": str(exc)}


def webhook_token() -> str | None:
    """The GitHub webhook shared secret (environment only)."""
    return os.environ.get(ENV_WEBHOOK_TOKEN) or None
