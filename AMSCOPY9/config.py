"""
================================================================
            AMS — CENTRAL DEPLOYMENT CONTROL CENTER
================================================================

This is the SINGLE SOURCE OF TRUTH for the deployment target.

Everything that decides *where* code comes from and *where* it is
deployed lives here:

    GitHub          -> where the code is pulled from
    PythonAnywhere  -> which account / domain / paths are live
    Deployment      -> which automatic steps run

To point this application at a different repository or a different
PythonAnywhere server, change the values BELOW and nothing else.

    config.py
       |
   +---+-------------------+
   |                       |
 GitHub Actions      PythonAnywhere webhook deployer
 (deploy/ + .github) (deploy/deploy.py, run inside the live app)

SECRETS RULE
------------
This file is committed to Git, so it must NEVER contain real
credentials.  Secrets are provided by the environment (GitHub
Secrets -> Actions, and the PythonAnywhere web-app environment).
This file only declares the *names* of the environment variables
that hold them (see ``ENV_*`` below) and `validate_config` verifies
they are present where they are required.

Non-secret per-installation overrides (e.g. a PythonAnywhere
username that differs from the default) can be supplied through
environment variables named ``AMS_*`` — every loader reads the
environment first, then falls back to the defaults in this file.
"""
from __future__ import annotations

import os
import re
from pathlib import Path

# ============================================================
# APPLICATION CONFIGURATION
# ============================================================

APP_NAME = "AMS - Ahmed Management System"

# The Flask application object created by the factory
# ("from app import create_app; app = create_app()").
APP_FACTORY_MODULE = "app"
APP_FACTORY_CALLABLE = "create_app"

# WSGI entry module (the file PythonAnywhere imports). It must expose
# ``application`` (and ``app``) after calling the factory.
APP_ENTRY_POINT = "wsgi:application"

# Where the source code lives locally (this repository root).
BASE_DIR = Path(__file__).resolve().parent

# The SQLite database file name that holds live production data.
# Used by the deployer to back up before any code sync and to keep
# the live data directory protected across resets.
RUNTIME_DB_NAME = "ahmed_cement_v44_fresh.db"

# Directory (relative to BASE_DIR) that holds live runtime data which
# must NEVER be overwritten by a code deployment.
RUNTIME_DATA_DIR = "instance"

# ============================================================
# GITHUB CONFIGURATION
# ============================================================
# Change GITHUB_REPOSITORY / GITHUB_BRANCH to point the whole
# deployment system at a different source. Every consumer
# (webhook deployer, GitHub Actions, health checks) derives the
# remote URL and ref from these values — nothing else is hard-coded.

GITHUB_OWNER = "rehmanahmedca-source"
GITHUB_REPOSITORY = "AMSCOPY9"
GITHUB_BRANCH = "main"
GITHUB_REMOTE = "origin"

def _github_repo_url() -> str:
    return f"https://github.com/{GITHUB_OWNER}/{GITHUB_REPOSITORY}.git"

GITHUB_REPO_URL = _github_repo_url()

# ============================================================
# PYTHONANYWHERE CONFIGURATION
# ============================================================
# These values describe the LIVE server. They are used both on the
# server (the webhook deployer reads its own target) and in GitHub
# Actions (to reload the API and health-check the public domain).
#
# Each value can be overridden with an AMS_* environment variable,
# so the same committed config.py works across installs while the
# real machine-specific values can be injected privately.

PYTHONANYWHERE_USERNAME = "ahmedrehmanahmed1"
PYTHONANYWHERE_DOMAIN = "ahmedrehmanahmed1.pythonanywhere.com"

# Absolute paths ON the PythonAnywhere server.
PYTHONANYWHERE_PROJECT_PATH = f"/home/{PYTHONANYWHERE_USERNAME}/AMSCOPY9"
PYTHONANYWHERE_VENV_PATH = f"/home/{PYTHONANYWHERE_USERNAME}/.virtualenvs/ams-venv"
# The WSGI file PythonAnywhere imports; "touching" it reloads the app.
PYTHONANYWHERE_WSGI_PATH = f"/var/www/{PYTHONANYWHERE_USERNAME}_pythonanywhere_com_wsgi.py"

# PythonAnywhere REST API (constructed from the username — never
# hard-code the username inside the URL).
PYTHONANYWHERE_API_BASE = "https://www.pythonanywhere.com/api/v0"
def _pa_reload_endpoint() -> str:
    return (
        f"{PYTHONANYWHERE_API_BASE}/user/{PYTHONANYWHERE_USERNAME}"
        f"/webapps/{PYTHONANYWHERE_DOMAIN}/reload/"
    )
PYTHONANYWHERE_RELOAD_ENDPOINT = _pa_reload_endpoint()

# Public base URL of the live application (for the health check).
def _app_base_url() -> str:
    return f"https://{PYTHONANYWHERE_DOMAIN}"
APP_BASE_URL = _app_base_url()
HEALTH_ENDPOINT_PATH = "/health"

# ============================================================
# SECRETS — environment variable names only, never values
# ============================================================
# The webhook deployer authenticates GitHub with this shared token.
ENV_WEBHOOK_TOKEN = "AMS_WEBHOOK_TOKEN"
# The PythonAnywhere API token used by GitHub Actions to reload the
# web app (Dashboard -> Account -> API token).
ENV_PYTHONANYWHERE_API_TOKEN = "PYTHONANYWHERE_API_TOKEN"

# ============================================================
# DEPLOYMENT CONFIGURATION (switches)
# ============================================================

AUTO_RELOAD = True            # reload the web app after a successful deploy
INSTALL_REQUIREMENTS = True   # pip install -r requirements.txt when changed
RUN_MIGRATIONS = True         # ensure DB schema matches models (app import)
RUN_HEALTH_CHECK = True       # poll /health until healthy after reload
CREATE_DB_BACKUP = True       # copy the live SQLite DB aside before deploy

# Number of previous commits to keep for rollback.
ROLLBACK_HISTORY = 5
# Health-check polling after reload (seconds).
HEALTH_CHECK_ATTEMPTS = 20
HEALTH_CHECK_INTERVAL = 6
# pip install timeout (seconds).
PIP_TIMEOUT = 600


# ============================================================
# CENTRAL CONFIGURATION LOADER
# ============================================================

def _env(name: str, default=None):
    """Read an optional AMS_* override from the environment."""
    return os.environ.get(name, default)


def deployment_config() -> dict:
    """Return the fully-resolved deployment configuration.

    Environment overrides (per-install, kept out of Git) take
    precedence over the committed defaults so a machine can describe
    itself without editing this file.
    """
    gh_owner = _env("AMS_GITHUB_OWNER", GITHUB_OWNER)
    gh_repo = _env("AMS_GITHUB_REPOSITORY", GITHUB_REPOSITORY)
    gh_branch = _env("AMS_GITHUB_BRANCH", GITHUB_BRANCH)
    gh_remote = _env("AMS_GITHUB_REMOTE", GITHUB_REMOTE)
    gh_url = f"https://github.com/{gh_owner}/{gh_repo}.git"

    pa_user = _env("AMS_PA_USERNAME", PYTHONANYWHERE_USERNAME)
    pa_domain = _env("AMS_PA_DOMAIN", PYTHONANYWHERE_DOMAIN)
    project_path = _env("AMS_PA_PROJECT_PATH", f"/home/{pa_user}/{gh_repo}")
    venv_path = _env("AMS_PA_VENV_PATH", f"/home/{pa_user}/.virtualenvs/ams-venv")
    wsgi_path = _env(
        "AMS_PA_WSGI_PATH",
        f"/var/www/{pa_user}_pythonanywhere_com_wsgi.py",
    )
    api_base = _env("AMS_PA_API_BASE", PYTHONANYWHERE_API_BASE)
    app_base = _env("AMS_APP_BASE_URL", f"https://{pa_domain}")
    return {
        "app": {
            "name": APP_NAME,
            "factory_module": APP_FACTORY_MODULE,
            "factory_callable": APP_FACTORY_CALLABLE,
            "entry_point": APP_ENTRY_POINT,
            "base_dir": str(BASE_DIR),
            "runtime_db_name": RUNTIME_DB_NAME,
            "runtime_data_dir": RUNTIME_DATA_DIR,
        },
        "github": {
            "owner": gh_owner,
            "repository": gh_repo,
            "branch": gh_branch,
            "remote": gh_remote,
            "repo_url": gh_url,
        },
        "pythonanywhere": {
            "username": pa_user,
            "domain": pa_domain,
            "project_path": project_path,
            "venv_path": venv_path,
            "wsgi_path": wsgi_path,
            "api_base": api_base,
            "reload_endpoint": f"{api_base}/user/{pa_user}/webapps/{pa_domain}/reload/",
            "app_base_url": app_base,
            "health_url": f"{app_base}{HEALTH_ENDPOINT_PATH}",
        },
        "secrets": {
            "webhook_token_env": ENV_WEBHOOK_TOKEN,
            "pa_api_token_env": ENV_PYTHONANYWHERE_API_TOKEN,
        },
        "deploy": {
            "auto_reload": AUTO_RELOAD,
            "install_requirements": INSTALL_REQUIREMENTS,
            "run_migrations": RUN_MIGRATIONS,
            "run_health_check": RUN_HEALTH_CHECK,
            "create_db_backup": CREATE_DB_BACKUP,
            "rollback_history": ROLLBACK_HISTORY,
            "health_attempts": HEALTH_CHECK_ATTEMPTS,
            "health_interval": HEALTH_CHECK_INTERVAL,
            "pip_timeout": PIP_TIMEOUT,
        },
    }


# A lazily-built, shared singleton so every consumer gets identical config.
_CONFIG: dict | None = None


def get_config() -> dict:
    global _CONFIG
    if _CONFIG is None:
        _CONFIG = deployment_config()
    return _CONFIG


# ============================================================
# CONFIGURATION VALIDATOR
# ============================================================

class ConfigError(Exception):
    """Raised when the deployment configuration is incomplete/invalid."""


_BRANCH_RE = re.compile(r"^[A-Za-z0-9._/-]+$")
_NAME_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]*$")


def validate_config(require_secrets: bool = False, check_paths: bool = False) -> list:
    """Validate the deployment configuration.

    Returns a list of human-readable problems (empty == valid).

    :param require_secrets: require the secret env vars to actually be
        present in this environment (used on the server / in CI).
    :param check_paths: verify the PythonAnywhere project/venv/wsgi
        paths exist on THIS machine (used by the on-server deployer).
    """
    cfg = get_config()
    problems: list = []

    gh = cfg["github"]
    if not gh["owner"]:
        problems.append("GitHub owner is missing.")
    if not gh["repository"]:
        problems.append("GitHub repository is missing.")
    if not gh["branch"]:
        problems.append("GitHub branch is missing.")
    elif not _BRANCH_RE.match(gh["branch"]):
        problems.append(f"Invalid GitHub branch name: {gh['branch']!r}.")
    if not gh["remote"]:
        problems.append("Git remote name is missing.")
    if not str(gh["repo_url"]).startswith("https://github.com/"):
        problems.append(f"GitHub repository URL looks wrong: {gh['repo_url']}.")

    pa = cfg["pythonanywhere"]
    if not pa["username"]:
        problems.append("PythonAnywhere username is missing.")
    elif not _NAME_RE.match(pa["username"]):
        problems.append(f"Invalid PythonAnywhere username: {pa['username']!r}.")
    if not pa["domain"]:
        problems.append("PythonAnywhere domain is missing.")
    elif "." not in pa["domain"]:
        problems.append(f"Invalid PythonAnywhere domain: {pa['domain']!r}.")
    for key in ("project_path", "venv_path", "wsgi_path"):
        if not pa[key]:
            problems.append(f"PythonAnywhere {key.replace('_', ' ')} is missing.")
    if not pa["api_base"].startswith("https://"):
        problems.append("PythonAnywhere API base must be https.")
    if not pa["reload_endpoint"].startswith(pa["api_base"]):
        problems.append("Reload endpoint is not derived from the API base.")
    if not pa["app_base_url"].startswith("https://"):
        problems.append("Application base URL must be https.")

    if require_secrets:
        if not os.environ.get(ENV_WEBHOOK_TOKEN):
            problems.append(
                f"Required secret env var {ENV_WEBHOOK_TOKEN} is not set "
                "(the GitHub webhook token)."
            )
        # The API token is only strictly needed where the API reload is
        # performed (GitHub Actions); the on-server touch-reload does not
        # need it, so this is a warning-level check handled by callers.

    if check_paths:
        # Only meaningful when run on the PythonAnywhere server.
        proj = Path(pa["project_path"])
        if not proj.exists():
            problems.append(f"Project path does not exist on this server: {proj}")
        elif not (proj / ".git").exists():
            problems.append(f"Project path is not a git checkout: {proj}")
        venv_python = Path(pa["venv_path"]) / "bin" / "python"
        if not venv_python.exists():
            problems.append(
                f"Virtualenv python not found: {venv_python} "
                "(create it per DEPLOYMENT.md)."
            )
        # The WSGI file lives in /var/www which the deploy user can read;
        # absence means the web app has not been created yet.
        if not Path(pa["wsgi_path"]).exists():
            problems.append(
                f"WSGI file not found: {pa['wsgi_path']} "
                "(create the PythonAnywhere web app first)."
            )

    return problems


def assert_valid_config(require_secrets: bool = False, check_paths: bool = False):
    """Fail early with a clear message if configuration is invalid."""
    problems = validate_config(
        require_secrets=require_secrets, check_paths=check_paths
    )
    if problems:
        msg = "Invalid deployment configuration:\n  - " + "\n  - ".join(problems)
        raise ConfigError(msg)
    return get_config()


def render_control_panel() -> str:
    """Human-readable summary of the deployment target (the 'control panel')."""
    cfg = get_config()
    gh, pa, dep = cfg["github"], cfg["pythonanywhere"], cfg["deploy"]
    lines = [
        "=" * 48,
        "              DEPLOYMENT CONTROL",
        "=" * 48,
        "",
        "GITHUB",
        f"    Repository : {gh['repo_url']}",
        f"    Branch     : {gh['branch']}",
        f"    Remote     : {gh['remote']}",
        "",
        "PYTHONANYWHERE",
        f"    Username   : {pa['username']}",
        f"    Domain     : {pa['domain']}",
        f"    Project    : {pa['project_path']}",
        f"    Virtualenv : {pa['venv_path']}",
        f"    WSGI       : {pa['wsgi_path']}",
        f"    Reload API : {pa['reload_endpoint']}",
        f"    Health URL : {pa['health_url']}",
        "",
        "DEPLOYMENT",
        f"    Auto reload      : {dep['auto_reload']}",
        f"    Install deps     : {dep['install_requirements']}",
        f"    Run migrations   : {dep['run_migrations']}",
        f"    Health check     : {dep['run_health_check']}",
        f"    DB backup        : {dep['create_db_backup']}",
        "",
        "SECRETS (from environment, never stored here)",
        f"    Webhook token    : ${cfg['secrets']['webhook_token_env']}",
        f"    PA API token     : ${cfg['secrets']['pa_api_token_env']}",
        "=" * 48,
    ]
    return "\n".join(lines)


if __name__ == "__main__":
    import sys

    print(render_control_panel())
    problems = validate_config(
        require_secrets="--secrets" in sys.argv,
        check_paths="--paths" in sys.argv,
    )
    if problems:
        print("\nCONFIGURATION PROBLEMS:")
        for p in problems:
            print("  -", p)
        sys.exit(1)
    print("\nConfiguration valid.")
