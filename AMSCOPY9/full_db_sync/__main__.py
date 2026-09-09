"""CLI entrypoint for ``python -m full_db_sync``."""
from .cli import main

if __name__ == "__main__":
    raise SystemExit(main())
