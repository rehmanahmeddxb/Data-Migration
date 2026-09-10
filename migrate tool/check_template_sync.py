#!/usr/bin/env python3
"""Check that a v4.4 template database still matches the application's models.

The migration tool copies the OLD rows into whatever columns the template has.
If the app gained a table or a column since the template was created, a
migration into that template produces a database that is missing it — the run
still prints RESULT: PASS, because the tool only compares old vs output.

This is the missing pre-flight check.  Run it before every migration:

    python3 "migrate tool/check_template_sync.py" \
        --template "data lab for migration old to new/NewData/ahmed_cement_v44_fresh.db"

Exit codes: 0 = in sync · 1 = drift (template is behind the models) · 2 = usage
or read error.  Pure stdlib — no Flask, no SQLAlchemy, no pandas.
"""
from __future__ import annotations

import argparse
import ast
import json
import re
import sqlite3
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
DEFAULT_MODELS = REPO / "AMSCOPY9" / "models"
DEFAULT_TEMPLATE = (
    REPO / "data lab for migration old to new" / "NewData" /
    "ahmed_cement_v44_fresh.db"
)


def _snake(name: str) -> str:
    """Flask-SQLAlchemy's implicit table name (CamelCase -> camel_case)."""
    s1 = re.sub(r"(.)([A-Z][a-z]+)", r"\1_\2", name)
    return re.sub(r"([a-z0-9])([A-Z])", r"\1_\2", s1).lower()


def parse_models(models_dir: Path) -> dict:
    """{table_name: {column, ...}} from the ORM model sources (static parse).

    Running the app would be exact but needs Flask + SQLAlchemy installed; a
    static read of ``db.Column`` assignments is enough to detect drift and runs
    anywhere.
    """
    out: dict = {}
    for path in sorted(models_dir.glob("*.py")):
        try:
            tree = ast.parse(path.read_text(encoding="utf-8"))
        except (OSError, SyntaxError):
            continue
        for node in ast.walk(tree):
            if isinstance(node, ast.ClassDef):
                table, cols = None, set()
                for stmt in node.body:
                    if not isinstance(stmt, ast.Assign):
                        continue
                    target = stmt.targets[0]
                    name = getattr(target, "id", None)
                    value = stmt.value
                    if (name == "__tablename__" and isinstance(value, ast.Constant)
                            and isinstance(value.value, str)):
                        table = value.value
                    elif isinstance(value, ast.Call):
                        fn = value.func
                        fname = getattr(fn, "attr", None) or getattr(fn, "id", None)
                        if fname == "Column" and name:
                            cols.add(name)
                if cols:
                    out[table or _snake(node.name)] = cols
            elif isinstance(node, ast.Assign) and isinstance(node.value, ast.Call):
                # association tables declared as db.Table('name', col, col, ...)
                fn = node.value.func
                fname = getattr(fn, "attr", None) or getattr(fn, "id", None)
                if fname != "Table" or not node.value.args:
                    continue
                first = node.value.args[0]
                if not (isinstance(first, ast.Constant) and isinstance(first.value, str)):
                    continue
                cols = set()
                for arg in node.value.args[1:]:
                    if isinstance(arg, ast.Call):
                        aname = getattr(arg.func, "attr", None)
                        if aname == "Column":
                            cols.add(arg.args[0].value if arg.args and
                                     isinstance(arg.args[0], ast.Constant) else "?")
                out[first.value] = cols
    return out


def read_template(template: Path) -> dict:
    con = sqlite3.connect(f"file:{template.as_posix()}?mode=ro", uri=True)
    try:
        tables = [r[0] for r in con.execute(
            "SELECT name FROM sqlite_master WHERE type='table' "
            "AND name NOT LIKE 'sqlite_%'")]
        return {
            t: {r[1] for r in con.execute(f'PRAGMA table_info("{t}")')}
            for t in tables
        }
    finally:
        con.close()


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--template", default=str(DEFAULT_TEMPLATE),
                    help="v4.4 template database (default: the data-lab one)")
    ap.add_argument("--models", default=str(DEFAULT_MODELS),
                    help="directory with the ORM models (default: AMSCOPY9/models)")
    ap.add_argument("--json", action="store_true", help="machine-readable output")
    args = ap.parse_args(argv)

    template = Path(args.template).expanduser()
    models_dir = Path(args.models).expanduser()
    if not template.exists():
        print(f"ERROR: template not found: {template}", file=sys.stderr)
        return 2
    if not models_dir.is_dir():
        print(f"ERROR: models directory not found: {models_dir}", file=sys.stderr)
        return 2

    models = parse_models(models_dir)
    schema = read_template(template)

    missing_tables = sorted(set(models) - set(schema))
    extra_tables = sorted(set(schema) - set(models) - {"sqlite_sequence"})
    missing_cols = {}
    for t in sorted(set(models) & set(schema)):
        miss = sorted(models[t] - schema[t])
        if miss:
            missing_cols[t] = miss

    ok = not (missing_tables or missing_cols)
    result = {
        "ok": ok,
        "template": str(template),
        "models_dir": str(models_dir),
        "model_tables": len(models),
        "template_tables": len(schema),
        "missing_tables": missing_tables,
        "missing_columns": missing_cols,
        "template_only_tables": extra_tables,
    }

    if args.json:
        print(json.dumps(result, indent=2))
        return 0 if ok else 1

    print(f"template : {template}")
    print(f"models   : {models_dir}  ({len(models)} tables)")
    print(f"schema   : {len(schema)} tables in the template")
    print()
    if missing_tables:
        print(f"  DRIFT — {len(missing_tables)} table(s) in the models are missing "
              "from the template:")
        for t in missing_tables[:15]:
            print(f"    - {t}")
    if missing_cols:
        print(f"  DRIFT — {sum(len(v) for v in missing_cols.values())} column(s) in "
              "the models are missing from the template:")
        for t, cols in list(missing_cols.items())[:15]:
            print(f"    - {t}: {', '.join(cols[:12])}")
    if extra_tables:
        print(f"  note — {len(extra_tables)} table(s) exist only in the template "
              "(not defined by a model): " + ", ".join(extra_tables[:15]))
    if ok:
        print("  IN SYNC — the template has every table and column the models define.")
        print("  Migrating into it loses nothing to schema drift.")
    else:
        print()
        print("  A migration into this template would leave the missing table(s)/"
              "column(s) empty.")
        print("  Build a fresh v4.4 template (or add the columns) before migrating.")
    return 0 if ok else 1


if __name__ == "__main__":
    try:
        sys.exit(main())
    except Exception as exc:  # a crash must never look like "in sync"
        print(f"ERROR: template sync check failed: {exc!r}", file=sys.stderr)
        sys.exit(2)
