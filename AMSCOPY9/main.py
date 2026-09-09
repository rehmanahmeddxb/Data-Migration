"""AMS application entrypoint.

Deployment has been centralised into ``config.py`` + the ``deploy/``
package. This file is now only:

  * ``app`` / ``application``  — the Flask app (created by the factory)
  * a local development server

The GitHub webhook route and the entire auto-deploy pipeline live in
``app/deploy_routes.py`` and ``deploy/deployer.py`` and are registered on
the app in the factory, so they work identically under ``wsgi.py``
(PythonAnywhere) and here. No repository, token, server or path is
hard-coded — edit ``config.py`` to change the deployment target.
"""
from __future__ import annotations

import os

from app import create_app

# The WSGI application (tools and external loaders import ``app``).
app = create_app()
application = app


if __name__ == "__main__":
    # Werkzeug's debugger allows arbitrary code execution for anyone who can
    # reach the port, and this server binds 0.0.0.0 (the whole LAN). Keep it
    # off unless AMS_DEBUG=1 is set explicitly.
    debug_mode = (os.environ.get("AMS_DEBUG") or "").strip() == "1"

    app.run(
        host="0.0.0.0",
        port=int(os.environ.get("PORT", "5000") or "5000"),
        debug=debug_mode,
        use_reloader=False,
    )
