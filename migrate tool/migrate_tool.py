#!/usr/bin/env python3
"""
migrate_tool.py — AMS Database Migration Tool (GUI)

Pick:
  1. OLD database file  (the legacy / older AMS database, e.g. ahmed_cement.db)
  2. NEW database file  (the fresh v4.4 AMS database that provides the schema)

The tool copies the NEW file, empties its seeded business data, loads EVERY
old row into it (original ids, users merged, duplicates kept by relaxing only
the exact unique indexes the old data violates), then verifies: integrity,
per-table parity, FK orphans, user references and a duplicate scan.

Output: <name>_migrated.db  (+ .report.txt / .report.json next to it).

Drop-folder support: any *.db / *.sqlite / *.amsdb file placed into THIS
folder (or its "drop" subfolder) is detected automatically and offered in
the dropdowns below. The "output" subfolder is never scanned.

Run:
    python migrate_tool.py            (GUI)
    python migrate_tool.py --cli --old OLD.db --new NEW.db [--out OUT.db]
                                      (headless, same engine)
Only the Python standard library is needed (tkinter + sqlite3).
"""
from __future__ import annotations

import argparse
import os
import queue
import subprocess
import sys
import threading
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

from migrate_engine import (  # noqa: E402
    MigrationError,
    classify,
    default_out_path,
    inspect_database,
    run_migration,
)

DROP_DIRS = (HERE, HERE / "drop")
OUTPUT_DIR = HERE / "output"
DB_SUFFIXES = (".db", ".sqlite", ".sqlite3", ".amsdb")


# ---------------------------------------------------------------------------
# Folder auto-detection helpers
# ---------------------------------------------------------------------------

def _scan_databases() -> list:
    """All candidate DB files in this folder + drop/ (output excluded)."""
    found = {}
    for d in DROP_DIRS:
        if not d.is_dir():
            continue
        for p in sorted(d.iterdir()):
            if p.suffix.lower() not in DB_SUFFIXES:
                continue
            if p.resolve().parent == OUTPUT_DIR.resolve():
                continue
            if not p.is_file():
                continue
            found[str(p)] = p
    return sorted(found.values(), key=lambda p: p.name.lower())


def _describe(path: Path) -> str:
    info = inspect_database(path)
    if not info["is_ams"]:
        tag = "not an AMS db"
    elif info["looks_v44"]:
        tag = "v4.4 schema (NEW template)"
    else:
        tag = "legacy/old schema (OLD data)"
    return f"{path.name}  [{tag} — {info['total_rows']} rows, {len(info['tables'])} tables]"


def _open_folder(path: Path) -> None:
    path = Path(path)
    try:
        if sys.platform.startswith("win"):
            os.startfile(str(path))  # type: ignore[attr-defined]
        elif sys.platform == "darwin":
            subprocess.Popen(["open", str(path)])
        else:
            subprocess.Popen(["xdg-open", str(path)])
    except Exception:
        pass


# ---------------------------------------------------------------------------
# GUI
# ---------------------------------------------------------------------------

def build_gui() -> None:
    import tkinter as tk
    from tkinter import filedialog, messagebox, ttk

    root = tk.Tk()
    root.title("AMS Database Migration Tool — old data -> new v4.4 schema")
    root.geometry("880x720")
    root.minsize(820, 640)

    style = ttk.Style(root)
    try:
        style.theme_use("clam")
    except tk.TclError:
        pass

    # ------------------------- state -------------------------------------
    state = {
        "old": None,
        "new": None,
        "running": False,
    }
    msgs = queue.Queue()

    # ------------------------- header -------------------------------------
    header = tk.Frame(root, bg="#0d2b45", padx=16, pady=10)
    header.pack(fill="x")
    tk.Label(
        header, text="AMS DATABASE MIGRATION TOOL",
        bg="#0d2b45", fg="white", font=("Segoe UI", 14, "bold"),
    ).pack(anchor="w")
    tk.Label(
        header,
        text="1) pick OLD data db   2) pick NEW v4.4 schema db   3) Start — "
             "output is a brand-new migrated db. No source file is ever modified.",
        bg="#0d2b45", fg="#9fc5e8", font=("Segoe UI", 9),
    ).pack(anchor="w")

    body = tk.Frame(root, padx=14, pady=8)
    body.pack(fill="both", expand=True)

    # ------------------------- inputs -------------------------------------
    inp = tk.LabelFrame(body, text="  1.  Input files  ", padx=10, pady=8, font=("Segoe UI", 10, "bold"))
    inp.pack(fill="x")

    var_old = tk.StringVar()
    var_new = tk.StringVar()
    var_out = tk.StringVar()

    def _browse(kind: str) -> None:
        start = str(HERE)
        if kind == "old" and state.get("old"):
            start = str(state["old"])
        elif kind == "new" and state.get("new"):
            start = str(state["new"])
        path = filedialog.askopenfilename(
            title=("Choose the OLD database file (legacy data)"
                   if kind == "old" else "Choose the NEW v4.4 database file"),
            initialdir=start,
            filetypes=[
                ("Database files", "*.db *.sqlite *.sqlite3 *.amsdb"),
                ("All files", "*.*"),
            ],
        )
        if path:
            set_input(kind, Path(path))

    def set_input(kind: str, path: Path) -> None:
        if kind == "old":
            var_old.set(str(path))
            state["old"] = path
        else:
            var_new.set(str(path))
            state["new"] = path
        _refresh_auto_out()
        _update_classify_note()

    row_old = tk.Frame(inp); row_old.pack(fill="x", pady=3)
    tk.Label(row_old, text="OLD data db  ", width=14, anchor="w",
             font=("Segoe UI", 9, "bold")).pack(side="left")
    tk.Entry(row_old, textvariable=var_old, state="readonly").pack(side="left", fill="x", expand=True)
    ttk.Button(row_old, text="Browse…", width=9,
               command=lambda: _browse("old")).pack(side="left", padx=(6, 0))

    row_new = tk.Frame(inp); row_new.pack(fill="x", pady=3)
    tk.Label(row_new, text="NEW v4.4 db  ", width=14, anchor="w",
             font=("Segoe UI", 9, "bold")).pack(side="left")
    tk.Entry(row_new, textvariable=var_new, state="readonly").pack(side="left", fill="x", expand=True)
    ttk.Button(row_new, text="Browse…", width=9,
               command=lambda: _browse("new")).pack(side="left", padx=(6, 0))

    # classification note
    note_var = tk.StringVar(value="Drop database files into this folder or the 'drop' folder — they appear below.")
    tk.Label(inp, textvariable=note_var, fg="#555555", font=("Segoe UI", 8), anchor="w", justify="left").pack(fill="x", pady=(6, 0), padx=2)

    # drop candidates
    cand_row = tk.Frame(inp); cand_row.pack(fill="x", pady=(6, 2))
    tk.Label(cand_row, text="Detected files  ", width=14, anchor="w").pack(side="left")
    cand_combo = ttk.Combobox(cand_row, state="readonly")
    cand_combo.pack(side="left", fill="x", expand=True)
    cand_entries = []

    def _refresh_candidates(select: Path | None = None) -> None:
        nonlocal cand_entries
        cand_entries = _scan_databases()
        names = [_describe(p) for p in cand_entries]
        cand_combo["values"] = names
        if select is not None:
            for i, p in enumerate(cand_entries):
                if p == select:
                    cand_combo.current(i)
                    break
        elif names:
            cand_combo.current(0)
        cand_lbl_var.set(
            f"{len(cand_entries)} database file(s) detected in this folder / drop/"
            if cand_entries else "No db files found in this folder or 'drop' yet."
        )

    def _use_candidate(kind: str) -> None:
        idx = cand_combo.current()
        if idx < 0 or idx >= len(cand_entries):
            messagebox.showinfo("Migration tool", "No detected file selected.")
            return
        path = cand_entries[idx]
        other = state.get("new" if kind == "old" else "old")
        set_input(kind, path)
        # Auto-fill the other role from the other detected file when possible.
        if other is None and cand_entries:
            others = [p for p in cand_entries if p != path]
            if len(others) == 1:
                set_input("new" if kind == "old" else "old", others[0])
            elif others:
                # Prefer: old role gets the legacy file, new role the v4.4 file.
                for p in others:
                    info = inspect_database(p)
                    if info["looks_v44"] and kind == "new":
                        set_input("new", p)
                        break
                    if not info["looks_v44"] and kind == "old":
                        break

    tk.Button(cand_row, text="Use as OLD", command=lambda: _use_candidate("old")).pack(side="left", padx=(6, 0))
    tk.Button(cand_row, text="Use as NEW", command=lambda: _use_candidate("new")).pack(side="left", padx=(6, 0))
    cand_lbl_var = tk.StringVar()
    tk.Label(inp, textvariable=cand_lbl_var, fg="#0b7a3b", font=("Segoe UI", 8), anchor="w").pack(fill="x", padx=2)

    # ------------------------- output -------------------------------------
    out_frame = tk.LabelFrame(body, text="  2.  Output migration file  ", padx=10, pady=8, font=("Segoe UI", 10, "bold"))
    out_frame.pack(fill="x", pady=(8, 0))
    out_row = tk.Frame(out_frame); out_row.pack(fill="x")
    tk.Entry(out_row, textvariable=var_out).pack(side="left", fill="x", expand=True)
    ttk.Button(out_row, text="Browse…", width=9, command=lambda: _browse_out()).pack(side="left", padx=(6, 0))

    def _browse_out() -> None:
        path = filedialog.asksaveasfilename(
            title="Where to write the migrated database",
            initialdir=str(OUTPUT_DIR if OUTPUT_DIR.is_dir() else HERE),
            defaultextension=".db",
            filetypes=[("Database files", "*.db"), ("All files", "*.*")],
        )
        if path:
            var_out.set(path)

    def _refresh_auto_out() -> None:
        if var_out.get().strip():
            return  # keep a user-typed path
        old = state.get("old")
        new = state.get("new")
        if old and new:
            var_out.set(str(default_out_path(new, old)))

    overwrite_var = tk.BooleanVar(value=True)
    purge_var = tk.BooleanVar(value=True)
    carry_var = tk.BooleanVar(value=False)
    ttk.Checkbutton(
        out_frame, text="Overwrite the output file if it already exists",
        variable=overwrite_var,
    ).pack(anchor="w", pady=(6, 0))
    ttk.Checkbutton(
        out_frame,
        text="Purge voided / cancelled rows (recommended — the new app keeps "
             "no voided data; children are purged with their parents)",
        variable=purge_var,
    ).pack(anchor="w", pady=(6, 0))
    ttk.Checkbutton(
        out_frame,
        text="Carry the OLD file's company settings (name / tax / bill prefixes) "
             "when the NEW template has none",
        variable=carry_var,
    ).pack(anchor="w", pady=(6, 0))
    tk.Label(
        out_frame,
        text="How it works: a copy of the NEW file becomes the base and is emptied of its "
             "seeded business data; every OLD row is loaded with original ids. Users are "
             "merged (fresh admins kept + old users added), duplicate bill numbers are "
             "KEPT and only the exact unique index that rejects them is relaxed. "
             "A report + verification is written next to the output.",
        fg="#555555", font=("Segoe UI", 8), justify="left", wraplength=820,
    ).pack(fill="x", pady=(6, 0))

    # ------------------------- run + log ----------------------------------
    run_frame = tk.Frame(body); run_frame.pack(fill="x", pady=(10, 2))
    start_btn = ttk.Button(run_frame, text="START MIGRATION", command=lambda: _start())
    start_btn.pack(side="left")
    open_out_btn = ttk.Button(run_frame, text="Open output folder", command=lambda: _open_folder(OUTPUT_DIR))
    open_out_btn.pack(side="left", padx=6)
    open_rep_btn = ttk.Button(run_frame, text="Open report", command=lambda: _open_last_report())
    open_rep_btn.pack(side="left")
    status_var = tk.StringVar(value="Ready.")
    tk.Label(run_frame, textvariable=status_var, fg="#0b7a3b", font=("Segoe UI", 9, "bold")).pack(side="left", padx=12)

    prog = ttk.Progressbar(body, mode="determinate", maximum=100)
    prog.pack(fill="x", pady=4)

    log = tk.Text(body, height=14, state="disabled", wrap="none", font=("Consolas", 8))
    log.pack(fill="both", expand=True, pady=(4, 0))
    log.tag_configure("err", foreground="#b00020")
    log.tag_configure("ok", foreground="#0b7a3b")

    def _log(text: str, tag=None) -> None:
        log.configure(state="normal")
        log.insert("end", text + "\n", tag or ())
        log.see("end")
        log.configure(state="disabled")

    last_report = {"path": None}

    def _open_last_report() -> None:
        if last_report["path"] and Path(last_report["path"]).exists():
            try:
                if sys.platform.startswith("win"):
                    os.startfile(str(last_report["path"]))  # type: ignore[attr-defined]
                elif sys.platform == "darwin":
                    subprocess.Popen(["open", str(last_report["path"])])
                else:
                    subprocess.Popen(["xdg-open", str(last_report["path"])])
            except Exception:
                messagebox.showinfo("Report", str(last_report["path"]))
        else:
            messagebox.showinfo("Report", "No report yet — run a migration first.")

    def _update_classify_note() -> None:
        old, new = state.get("old"), state.get("new")
        if old and new:
            info = classify(old, new)
            if info["warnings"]:
                note_var.set("; ".join(info["warnings"]))
            else:
                note_var.set("Looks good — ready to migrate.")
        else:
            note_var.set("Drop database files into this folder or the 'drop' folder — they appear below.")

    def _start() -> None:
        if state["running"]:
            return
        old, new = state.get("old"), state.get("new")
        if not old or not new:
            messagebox.showwarning("Migration tool", "Pick the OLD data file and the NEW v4.4 file first.")
            return
        if not Path(old).exists() or not Path(new).exists():
            messagebox.showwarning("Migration tool", "One of the chosen files no longer exists.")
            return
        out = Path(var_out.get().strip() or default_out_path(new, old))
        var_out.set(str(out))
        if out.exists() and not overwrite_var.get():
            messagebox.showwarning("Migration tool", "Output exists and 'overwrite' is off.")
            return
        if out.resolve() in (Path(old).resolve(), Path(new).resolve()):
            messagebox.showwarning("Migration tool", "Output must be a different file from the inputs.")
            return

        state["running"] = True
        start_btn.configure(state="disabled")
        status_var.set("Migrating…")
        status_var.configure(fg="#b35400")
        prog["value"] = 0
        log.configure(state="normal")
        log.delete("1.0", "end")
        log.configure(state="disabled")
        for p, t in ((old, "OLD"), (new, "NEW")):
            info = inspect_database(p)
            _log(f"{t}: {p}  ({info['total_rows']} rows, {len(info['tables'])} tables, "
                 f"integrity {info['integrity']})")

        def _progress(pct: float, msg: str) -> None:
            msgs.put(("p", int(pct), msg))

        def worker() -> None:
            try:
                report = run_migration(
                    old, new,
                    out_path=out,
                    overwrite=overwrite_var.get(),
                    purge_voided=purge_var.get(),
                    carry_settings=carry_var.get(),
                    progress=_progress,
                )
                msgs.put(("done", report))
            except MigrationError as e:
                msgs.put(("error", str(e)))
            except Exception as e:  # pragma: no cover
                msgs.put(("error", f"Unexpected failure: {e!r}"))

        threading.Thread(target=worker, daemon=True).start()
        root.after(80, _poll)

    def _poll() -> None:
        try:
            while True:
                kind, *payload = msgs.get_nowait()
                if kind == "p":
                    prog["value"] = payload[0]
                elif kind == "done":
                    report = payload[0]
                    last_report["path"] = report["report_path"]
                    _finish(report)
                    return
                elif kind == "error":
                    _finish_error(payload[0])
                    return
        except queue.Empty:
            pass
        if state["running"]:
            root.after(80, _poll)

    def _finish(report: dict) -> None:
        state["running"] = False
        start_btn.configure(state="normal")
        prog["value"] = 100
        text = report["text"]
        for line in text.splitlines():
            tag = None
            low = line.lower()
            if "mismatch" in low or "orphan" in low or "violation" in low or "review reason" in low:
                tag = "err"
            elif "[ok]" in low or "result: pass" in low or "none" in low:
                tag = "ok"
            _log(line, tag)
        ok = report["status"] == "PASS"
        status_var.configure(
            text=f"RESULT: {report['status']} — output: {report['out_path']}",
            fg="#0b7a3b" if ok else "#b00020",
        )
        msgs.queue.clear()
        if ok:
            _log("", "ok")
            _log("Migration complete — every old row is in the new database with original ids; "
                 "verification passed (integrity / parity / FK / duplicate scan).", "ok")
            _log(f"Output : {report['out_path']}", "ok")
            _log(f"Report : {report['report_path']}", "ok")
            _log("Next step: in the AMS app open Import/Export Center → Full Database Snapshot (.db) "
                 "→ Import, choose this output file, mode 'Full sync'.", "ok")
        else:
            _log("", "err")
            _log("Verification found issues (REVIEW) — read the lines above. The output file was still "
                 "written but must not be imported until the reasons are fixed.", "err")
        messagebox.showinfo("Migration tool", f"Migration finished: {report['status']}")

    def _finish_error(msg: str) -> None:
        state["running"] = False
        start_btn.configure(state="normal")
        status_var.configure(text="FAILED", fg="#b00020")
        _log("", "err")
        _log(f"ERROR: {msg}", "err")
        _log("No source file was modified. If the run had already produced a "
             "partial output, it was quarantined as *.INCOMPLETE (never import "
             "that file) and a *.report.txt failure report was written next to it.", "err")
        messagebox.showerror("Migration tool", f"Migration could not run:\n{msg}")

    # ------------------------- folder watching ----------------------------
    seen = {str(p) for p in _scan_databases()}

    def _watch() -> None:
        if not state["running"]:
            now = {str(p) for p in _scan_databases()}
            if now != seen:
                old_names = {Path(p).name for p in seen}
                fresh = [Path(p) for p in now if Path(p).name not in old_names]
                seen.clear(); seen.update(now)
                _refresh_candidates()
                if fresh:
                    _log(f"Detected file(s) placed in the folder: {', '.join(p.name for p in fresh)}")
                    messagebox.showinfo(
                        "File detected",
                        f"Detected:\n{chr(10).join(p.name for p in fresh)}\n\n"
                        "Select it in the list and press 'Use as OLD' or 'Use as NEW'.",
                    )
        root.after(1500, _watch)

    _refresh_candidates()
    _watch()
    _log("Ready. Drop your database files into this folder (or the 'drop' subfolder) "
         "and they will be detected automatically, or use Browse….")
    root.mainloop()


# ---------------------------------------------------------------------------
# Headless CLI (also used for tests / automation)
# ---------------------------------------------------------------------------

def run_cli(args: argparse.Namespace) -> int:
    old = Path(args.old)
    new = Path(args.new)
    if not old.exists() or not new.exists():
        print("One of the input files does not exist.", file=sys.stderr)
        return 2
    info_old = inspect_database(old)
    info_new = inspect_database(new)
    print(f"OLD  : {old}  ({info_old['total_rows']} rows, {len(info_old['tables'])} tables, "
          f"integrity {info_old['integrity']})")
    print(f"NEW  : {new}  ({info_new['total_rows']} rows, {len(info_new['tables'])} tables, "
          f"integrity {info_new['integrity']})")
    for w in classify(old, new)["warnings"]:
        print(f"NOTE: {w}")

    def _prog(pct, msg):
        print(f"[{pct:3d}%] {msg}")

    try:
        report = run_migration(
            old, new,
            out_path=Path(args.out) if args.out else None,
            overwrite=not args.no_overwrite,
            progress=_prog,
            allow_v44_old=getattr(args, "allow_v44_old", False),
            purge_voided=not getattr(args, "keep_voided", False),
            carry_settings=getattr(args, "carry_settings", False),
        )
    except MigrationError as e:
        print(f"ERROR: {e}", file=sys.stderr)
        return 2
    print()
    print(report["text"])
    print("\nOutput :", report["out_path"])
    print("Report :", report["report_path"])
    print("STATUS :", report["status"])
    return 0 if report["ok"] else 3


def _cli_scan() -> int:
    files = _scan_databases()
    if not files:
        print("No db files found in this folder or the 'drop' subfolder.")
        return 0
    for p in files:
        print(_describe(p))
    return 0


def main(argv=None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)
    if argv and argv[0] == "--scan":
        return _cli_scan()
    ap = argparse.ArgumentParser(
        description="AMS Database Migration Tool — GUI by default; pass --cli for headless."
    )
    ap.add_argument("--cli", action="store_true", help="headless mode")
    ap.add_argument("--old", help="old/legacy database file (cli)")
    ap.add_argument("--new", help="new v4.4 template database file (cli)")
    ap.add_argument("--out", help="output migration file (cli)")
    ap.add_argument("--no-overwrite", action="store_true", help="refuse to overwrite output (cli)")
    ap.add_argument(
        "--allow-v44-old", action="store_true",
        help="permit an OLD file that already carries v4.4 markers (experts only)",
    )
    ap.add_argument(
        "--keep-voided", action="store_true",
        help="carry voided/cancelled rows over instead of purging them "
             "(the default is to purge them)",
    )
    ap.add_argument(
        "--carry-settings", action="store_true",
        help="load the OLD file's settings row when the NEW template's "
             "settings table is empty",
    )
    args = ap.parse_args(argv)

    if args.cli or not _tk_available():
        if not args.old or not args.new:
            ap.error("--cli requires --old and --new")
        return run_cli(args)

    build_gui()
    return 0


def _tk_available() -> bool:
    try:
        import tkinter  # noqa: F401
        return True
    except Exception:
        return False


if __name__ == "__main__":
    sys.exit(main())
