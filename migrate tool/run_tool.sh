#!/bin/sh
# AMS Database Migration Tool launcher — Linux / macOS / Termux (Android).
# Counterpart of run_tool.bat. No arguments: opens the GUI when there is a
# display, and asks for the two files in the terminal when there is none.
# Arguments are passed straight through, e.g.  ./run_tool.sh --cli --old ... --new ...
set -e
cd "$(dirname "$0")"

PY=""
for c in python3 python; do
    if command -v "$c" >/dev/null 2>&1; then PY="$c"; break; fi
done

if [ -z "$PY" ]; then
    echo "Python 3 was not found on PATH." >&2
    if [ -n "$PREFIX" ] && echo "$PREFIX" | grep -q com.termux; then
        echo "In Termux install it with:   pkg install python sqlite" >&2
        echo "Optional GUI toolkit:         pkg install python-tkinter" >&2
    else
        echo "Install Python from https://python.org (Windows/macOS) or with your package manager." >&2
    fi
    exit 1
fi

# Nothing is pip-installed: the tool is standard library only (see requirements.txt).
exec "$PY" migrate_tool.py "$@"
