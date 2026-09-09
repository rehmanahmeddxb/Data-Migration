"""Health check against the configured production domain.

Reads the target URL from ``config.py`` so the same code works for any
repository / PythonAnywhere server.
"""
from __future__ import annotations

import json
import time
import urllib.error
import urllib.request

from config import get_config


def check_health_once(url: str, timeout: float = 15.0) -> tuple[bool, str]:
    """Return (healthy, detail) for a single probe of ``url``."""
    req = urllib.request.Request(url, headers={"User-Agent": "AMS-Deploy/1.0"})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            status = resp.status
            body = resp.read(4096).decode("utf-8", "replace")
    except urllib.error.HTTPError as exc:
        return False, f"HTTP {exc.code}"
    except Exception as exc:  # network / DNS / timeout
        return False, f"{type(exc).__name__}: {exc}"

    if status != 200:
        return False, f"HTTP {status}"
    # The endpoint is expected to serve JSON containing status=healthy.
    try:
        data = json.loads(body)
        if str(data.get("status", "")).lower() == "healthy":
            return True, "healthy"
        return False, f"unexpected payload: {body[:120]}"
    except (ValueError, TypeError):
        # A 200 with a non-JSON body still proves the app imported and is
        # serving; treat as healthy but note it.
        return True, "200 (non-JSON)"


def wait_for_healthy(
    attempts: int | None = None,
    interval: int | None = None,
    log=print,
) -> bool:
    """Poll the configured health URL until healthy or attempts exhausted."""
    cfg = get_config()
    pa = cfg["pythonanywhere"]
    dep = cfg["deploy"]
    url = pa["health_url"]
    attempts = dep["health_attempts"] if attempts is None else attempts
    interval = dep["health_interval"] if interval is None else interval

    log(f"[health] Polling {url} (up to {attempts} attempts)")
    for i in range(1, attempts + 1):
        ok, detail = check_health_once(url)
        if ok:
            log(f"[health] Healthy on attempt {i}: {detail}")
            return True
        log(f"[health] Attempt {i}/{attempts} not healthy: {detail}")
        if i < attempts:
            time.sleep(interval)
    return False


if __name__ == "__main__":
    ok, detail = check_health_once(get_config()["pythonanywhere"]["health_url"])
    print("healthy" if ok else f"not healthy: {detail}")
    raise SystemExit(0 if ok else 1)
