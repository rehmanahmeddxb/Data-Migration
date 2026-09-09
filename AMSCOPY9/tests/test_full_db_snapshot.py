"""Full-database SQLite snapshot (.amsdb) engine + UI route tests.

The full-data path must be exactly as safe as promised:

* export produces a portable, verifiable SQLite copy of a database;
* import (``clean_replace``) deletes EVERY row of the target first — users
  included — then loads the source rows with their original primary keys;
* append keeps existing primary keys and only adds new ones;
* the engine never requires pandas/openpyxl;
* an automatic backup of the target is always taken before an import.

Note: the engine is deliberately schema-agnostic (it needs the three AMS probe
tables only), so the unit tests here use small synthetic AMS-shaped databases.
"""
from __future__ import annotations

import io
import sqlite3
from pathlib import Path

import pytest

from tests.conftest import make_csrf_client


# ---------------------------------------------------------------------------
# Synthetic AMS-shaped database helpers
# ---------------------------------------------------------------------------
DDL = """
CREATE TABLE user(id INTEGER PRIMARY KEY, username TEXT UNIQUE, role TEXT);
CREATE TABLE client(id INTEGER PRIMARY KEY, code TEXT UNIQUE, name TEXT, is_active INTEGER DEFAULT 1);
CREATE TABLE entry(id INTEGER PRIMARY KEY, client_id INTEGER, amount REAL, note TEXT);
CREATE TABLE settings(k TEXT PRIMARY KEY, v TEXT);
"""


def _make_db(path, users=(), clients=(), entries=()):
    con = sqlite3.connect(str(path))
    con.executescript(DDL)
    for uid, username, role in users:
        con.execute("INSERT INTO user (id, username, role) VALUES (?, ?, ?)", (uid, username, role))
    for cid, code, name in clients:
        con.execute("INSERT INTO client (id, code, name) VALUES (?, ?, ?)", (cid, code, name))
    for eid, cid, amount, note in entries:
        con.execute("INSERT INTO entry (id, client_id, amount, note) VALUES (?, ?, ?, ?)",
                    (eid, cid, amount, note))
    con.commit()
    con.close()


def _rows(db_path, table, order_by="id"):
    con = sqlite3.connect(str(db_path))
    try:
        return con.execute(f"SELECT * FROM {table} ORDER BY {order_by}").fetchall()
    finally:
        con.close()


def _count(db_path, table):
    con = sqlite3.connect(str(db_path))
    try:
        return con.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]
    finally:
        con.close()


# ---------------------------------------------------------------------------
# Engine tests
# ---------------------------------------------------------------------------
@pytest.fixture()
def sample_source(tmp_path):
    src = tmp_path / "source.db"
    _make_db(
        src,
        users=[(1, "Admin", "admin"), (2, "Rehman", "admin"), (3, "Rizwan", "admin")],
        clients=[(1, "C-1", "First"), (2, "C-2", "Second")],
        entries=[(10, 1, 5000.5, "sale"), (11, 2, -200.0, "return")],
    )
    return src


def test_export_verify_roundtrip(sample_source, tmp_path):
    from full_db_sync import export_snapshot, verify_snapshot

    out = tmp_path / "snap.amsdb"
    report = export_snapshot(sample_source, out_path=out)
    assert report["ok"] is True
    assert report["total_rows"] == 7  # 3 users + 2 clients + 2 entries
    assert out.exists() and out.stat().st_size > 0
    # snapshots must be portable single files — no WAL/SHM/journal sidecars
    assert not any(Path(str(out) + suffix).exists()
                   for suffix in ("-wal", "-shm", "-journal"))

    check = verify_snapshot(out)
    assert check["ok"] is True
    assert check["meta_present"] is True
    assert check["total_rows"] == 7
    assert check["integrity"] == "ok"
    assert check["fk_violations"] == 0


def test_clean_replace_wipes_users_too(sample_source, tmp_path):
    from full_db_sync import export_snapshot, import_snapshot

    target = tmp_path / "target.db"
    _make_db(
        target,
        users=[(1, "Admin", "admin"), (9, "Stale", "user")],
        clients=[(100, "OLD", "Old client")],
        entries=[(900, 100, 1.0, "old entry")],
    )
    snap = export_snapshot(sample_source, out_path=tmp_path / "snap.amsdb")["out_path"]
    backup_dir = tmp_path / "backups"
    report = import_snapshot(snap, target, mode="clean_replace", backup_dir=backup_dir)
    assert report["verification"] == "PASS"
    assert report["rows_deleted_total"] == 4
    assert report["rows_inserted_total"] == 7
    assert report["integrity"] == "ok"
    assert report["fk_violations"] == 0
    assert report["count_mismatches"] == {}
    # full replace: users copied, stale rows gone
    assert _rows(target, "user") == _rows(sample_source, "user")
    assert _rows(target, "client") == _rows(sample_source, "client")
    assert _rows(target, "entry") == _rows(sample_source, "entry")
    # automatic backup exists and holds the pre-import data
    backups = list(backup_dir.glob("pre_full_db_import_*.db"))
    assert len(backups) == 1
    assert _rows(backups[0], "client") == [(100, "OLD", "Old client", 1)]


def test_clean_replace_with_keep_users(sample_source, tmp_path):
    from full_db_sync import export_snapshot, import_snapshot

    target = tmp_path / "target.db"
    _make_db(
        target,
        users=[(1, "Admin", "admin"), (9, "Stale", "user")],
        clients=[(100, "OLD", "Old client")],
        entries=[],
    )
    snap = export_snapshot(sample_source, out_path=tmp_path / "snap.amsdb")["out_path"]
    report = import_snapshot(snap, target, include_users=False)
    assert report["verification"] == "PASS"
    # users untouched, business tables replaced
    assert _count(target, "user") == 2
    assert _count(target, "client") == 2
    assert _count(target, "entry") == 2


def test_append_skips_existing_pks(sample_source, tmp_path):
    from full_db_sync import import_snapshot

    target = tmp_path / "target.db"
    _make_db(
        target,
        users=[(1, "Admin", "admin")],
        clients=[(1, "C-1", "Old name"), (5, "C-5", "Extra")],
        entries=[],
    )
    report = import_snapshot(sample_source, target, mode="append", backup_target=False)
    assert report["verification"] == "PASS"
    # client id 1 keeps the original row; id 2 is added; id 5 untouched
    assert _rows(target, "client") == [
        (1, "C-1", "Old name", 1), (2, "C-2", "Second", 1), (5, "C-5", "Extra", 1),
    ]
    # user ids 2 and 3 added, id 1 kept
    assert _count(target, "user") == 3


def test_clean_only_preserves_schema(sample_source, tmp_path):
    from full_db_sync import clean_all_data

    target = tmp_path / "target.db"
    _make_db(target, users=[(1, "Admin", "admin")], clients=[(1, "C", "X")], entries=[])
    report = clean_all_data(target, backup=False)
    assert report["total_deleted"] == 2
    assert _count(target, "user") == 0
    assert _count(target, "client") == 0
    # schema still present and writable
    con = sqlite3.connect(str(target))
    assert "client" in {r[0] for r in con.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    con.execute("INSERT INTO client (id, code, name) VALUES (7, 'C-7', 'new')")
    con.commit()
    con.close()
    assert _count(target, "client") == 1


def test_clean_only_keep_users(sample_source, tmp_path):
    from full_db_sync import clean_all_data

    target = tmp_path / "target.db"
    _make_db(target, users=[(1, "Admin", "admin")], clients=[(1, "C", "X")], entries=[])
    report = clean_all_data(target, backup=False, include_users=False)
    assert report["total_deleted"] == 1
    assert _count(target, "user") == 1
    assert _count(target, "client") == 0


def test_same_file_refused(sample_source):
    from full_db_sync import export_snapshot, import_snapshot
    from full_db_sync.engine import FullDbSyncError

    with pytest.raises(FullDbSyncError):
        import_snapshot(sample_source, sample_source)
    with pytest.raises(FullDbSyncError):
        export_snapshot(sample_source, out_path=sample_source)


def test_rejects_non_ams_database(tmp_path):
    from full_db_sync import import_snapshot
    from full_db_sync.engine import FullDbSyncError

    bogus = tmp_path / "bogus.db"
    con = sqlite3.connect(str(bogus))
    con.execute("CREATE TABLE whatever(x TEXT)")
    con.commit()
    con.close()
    target = tmp_path / "target.db"
    _make_db(target, users=[(1, "Admin", "admin")], clients=[], entries=[])
    with pytest.raises(FullDbSyncError):
        import_snapshot(bogus, target)


def test_relaxes_unique_index_the_data_violates(tmp_path):
    """Legacy duplicate values (e.g. entry.auto_bill_no) must not lose rows:
    the violated target UNIQUE index is dropped and reported, not fatal.

    Mirrors the real app: the UNIQUE is declared as a partial index
    (``WHERE col IS NOT NULL AND TRIM(col) <> ''``), not as an inline column
    constraint, so it can be dropped when the legacy data cannot honour it.
    """
    from full_db_sync import export_snapshot, import_snapshot

    DDL_NO_INLINE_UNIQUE = """
    CREATE TABLE user(id INTEGER PRIMARY KEY, username TEXT UNIQUE, role TEXT);
    CREATE TABLE client(id INTEGER PRIMARY KEY, code TEXT, name TEXT, is_active INTEGER DEFAULT 1);
    CREATE TABLE entry(id INTEGER PRIMARY KEY, client_id INTEGER, amount REAL, note TEXT);
    CREATE TABLE settings(k TEXT PRIMARY KEY, v TEXT);
    """
    src = tmp_path / "legacy.db"
    con = sqlite3.connect(str(src))
    con.executescript(DDL_NO_INLINE_UNIQUE)
    con.execute("INSERT INTO user (id, username, role) VALUES (1, 'Admin', 'admin')")
    con.execute("INSERT INTO client (id, code, name) VALUES (1, 'SB-GRN-1024', 'One')")
    con.execute("INSERT INTO client (id, code, name) VALUES (2, 'SB-GRN-1024', 'Duplicate')")
    con.commit()
    con.close()

    target = tmp_path / "target.db"
    con = sqlite3.connect(str(target))
    con.executescript(DDL_NO_INLINE_UNIQUE)
    con.execute("INSERT INTO user (id, username, role) VALUES (1, 'Admin', 'admin')")
    con.execute("INSERT INTO client (id, code, name) VALUES (1, 'OLD', 'Old')")
    con.commit()
    # current schema enforces a partial unique index the legacy data violates
    con.execute(
        "CREATE UNIQUE INDEX uq_client_code ON client(code) "
        "WHERE code IS NOT NULL AND TRIM(code) <> ''"
    )
    con.commit()
    con.close()

    snap = export_snapshot(src, out_path=tmp_path / "snap.amsdb")["out_path"]
    report = import_snapshot(snap, target, mode="clean_replace", backup_target=False)
    assert report["verification"] == "PASS"
    assert report["unique_indexes_relaxed"] == ["uq_client_code"]
    assert _rows(target, "client") == [
        (1, "SB-GRN-1024", "One", 1), (2, "SB-GRN-1024", "Duplicate", 1),
    ]
    # the relaxed index is really gone
    con = sqlite3.connect(str(target))
    still = con.execute(
        "SELECT name FROM sqlite_master WHERE type='index' AND name='uq_client_code'"
    ).fetchall()
    con.close()
    assert still == []


def test_verify_detects_tampering(sample_source, tmp_path):
    from full_db_sync import export_snapshot, verify_snapshot

    out = tmp_path / "snap.amsdb"
    export_snapshot(sample_source, out_path=out)
    # delete a row behind the snapshot's back
    con = sqlite3.connect(str(out))
    con.execute("DELETE FROM client WHERE id=2")
    con.commit()
    con.close()
    check = verify_snapshot(out)
    assert check["ok"] is False
    assert any("row counts differ" in issue for issue in check["issues"])


# ---------------------------------------------------------------------------
# UI route tests (against the real Flask app schema)
# ---------------------------------------------------------------------------
@pytest.fixture()
def import_app(app_factory):
    return app_factory(FULL_DB_SYNC_ENABLED="1")


@pytest.fixture()
def client(import_app):
    return make_csrf_client(import_app)


def _login(client):
    resp = client.post(
        "/login",
        data={"username": "Admin", "password": "Admin@fbm12345"},
        follow_redirects=False,
    )
    assert resp.status_code in (302, 303), resp.get_data(as_text=True)[:300]


def test_export_route_downloads_valid_snapshot(import_app, client, tmp_path):
    _login(client)
    resp = client.get("/import_export/full_db_export")
    assert resp.status_code == 200
    assert "AMS_FULL_" in resp.headers.get("Content-Disposition", "")
    blob = tmp_path / "downloaded.amsdb"
    blob.write_bytes(resp.get_data())
    assert blob.read_bytes()[:16] == b"SQLite format 3\x00"
    con = sqlite3.connect(str(blob))
    try:
        tables = {r[0] for r in con.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        assert "user" in tables and "client" in tables
        assert "__ams_full_db_meta__" in tables
    finally:
        con.close()


def test_import_route_clean_replace(import_app, client, tmp_path):
    from full_db_sync import export_snapshot

    db_path = import_app.config["APP_DB_PATH"]
    # 1) snapshot the freshly-bootstrapped app database
    snap = export_snapshot(db_path, out_path=tmp_path / "snap.amsdb")["out_path"]
    before_user = _count(db_path, "user")
    before_client = _count(db_path, "client")

    # 2) inject a stray client row directly into the live database
    con = sqlite3.connect(str(db_path))
    max_id = con.execute("SELECT COALESCE(MAX(id),0) FROM client").fetchone()[0]
    con.execute("INSERT INTO client (id, code, name) VALUES (?, 'STRAY-9', 'STRAY')", (max_id + 1,))
    con.commit()
    con.close()
    assert _count(db_path, "client") == before_client + 1

    # 3) upload the snapshot (taken before the stray row) → must clean & restore
    _login(client)
    with open(snap, "rb") as handle:
        resp = client.post(
            "/import_export/full_db_import",
            data={"file": handle, "mode": "clean_replace"},
            content_type="multipart/form-data",
            follow_redirects=True,
        )
    assert resp.status_code == 200
    text = resp.get_data(as_text=True)
    assert "Full database import complete" in text
    assert _count(db_path, "user") == before_user
    assert _count(db_path, "client") == before_client, "stray row must have been cleaned"


def test_import_route_rejects_non_sqlite(import_app, client, tmp_path):
    _login(client)
    bogus = tmp_path / "not.db"
    bogus.write_text("this is not a sqlite file at all" * 100)
    with open(bogus, "rb") as handle:
        resp = client.post(
            "/import_export/full_db_import",
            data={"file": handle, "mode": "clean_replace"},
            content_type="multipart/form-data",
            follow_redirects=True,
        )
    assert resp.status_code == 200
    text = resp.get_data(as_text=True)
    assert "refused" in text or "failed" in text
