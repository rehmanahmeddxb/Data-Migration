#!/usr/bin/env python3
"""Deployment command-line interface (config-driven).

Usage
-----
    python deploy/deploy.py --check          # validate config + secrets only
    python deploy/deploy.py --dry-run        # deploy pipeline without changes
    python deploy/deploy.py --deploy         # run the full deploy (on server)
    python deploy/deploy.py --rollback       # roll code back to previous commit
    python deploy/deploy.py --health         # probe the configured health URL
    python deploy/deploy.py --show           # print the deployment control panel

Every setting comes from ``config.py`` (with optional AMS_* environment
overrides). Nothing here hard-codes a repository or server.
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

# Ensure the project root is importable when run as ``python deploy/deploy.py``.
ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from config import (  # noqa: E402
    get_config,
    assert_valid_config,
    ConfigError,
    render_control_panel,
)


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description="AMS deployment CLI")
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("--check", action="store_true", help="validate config/secrets")
    group.add_argument("--dry-run", action="store_true", help="pipeline, no changes")
    group.add_argument("--deploy", action="store_true", help="full deploy on server")
    group.add_argument("--rollback", action="store_true", help="code rollback")
    group.add_argument("--health", action="store_true", help="probe health URL")
    group.add_argument("--show", action="store_true", help="print control panel")
    parser.add_argument(
        "--to-commit",
        default=None,
        help="explicit commit for --rollback (default: previous deployed)",
    )
    parser.add_argument(
        "--paths",
        action="store_true",
        help="with --check, also verify server paths exist",
    )
    args = parser.parse_args(argv)

    if args.show:
        print(render_control_panel())
        return 0

    if args.check:
        print(render_control_panel())
        try:
            assert_valid_config(require_secrets=True, check_paths=args.paths)
        except ConfigError as exc:
            print("\n[Ahmed] Deployment Failed ✖")
            print("Stage: configuration")
            print(f"Reason:\n{exc}")
            return 1
        print("\n[Ahmed] Configuration valid ✔")
        return 0

    # The remaining actions run on the server and import the deployer.
    from deploy import deployer
    from deploy import health_check

    try:
        assert_valid_config(require_secrets=True, check_paths=args.paths)
    except ConfigError as exc:
        print(f"[Ahmed] Configuration invalid:\n{exc}")
        return 1

    if args.dry_run:
        print("[Ahmed] DRY RUN — validating configuration only (no changes)")
        result = deployer.deploy(dry_run=True)
        return 0 if result["ok"] else 1

    if args.deploy:
        result = deployer.deploy()
        if result["ok"]:
            print("[Ahmed] Deployment Complete ✔")
            if result.get("deployed_commit"):
                print("Commit:", result["deployed_commit"][:8])
            return 0
        print("[Ahmed] Deployment Failed ✖")
        print("Reason:", result.get("error"))
        return 1

    if args.rollback:
        result = deployer.rollback(args.to_commit)
        if result["ok"]:
            print("[Ahmed] Code rolled back to", result["rolled_back_to"][:8], "✔")
            print("Note: database data is NOT rolled back automatically.")
            return 0
        print("[Ahmed] Rollback Failed ✖:", result.get("error"))
        return 1

    if args.health:
        ok, detail = health_check.check_health_once(
            get_config()["pythonanywhere"]["health_url"]
        )
        print("[Ahmed] Health:", "healthy ✔" if ok else f"FAILED ✖ ({detail})")
        return 0 if ok else 1

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
