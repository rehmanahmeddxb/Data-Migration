#!/usr/bin/env python3
"""
Safe migration: old production data (ahmed_cement.db) -> new v44 schema
(NewData/ahmed_cement_v44_fresh.db).

Non-destructive: writes ONLY to "FIRST CLASS DATA/ahmed_cement_migrated.db".
Originals and the pre-migration backup are never touched.

Strategy
--------
  - Copy the new v44 DB as the base (correct DDL, indexes, new tables, migration_* infra).
  - For every shared *business* table: load ALL old rows with ORIGINAL ids into a
    constraint-stripped staging copy (so secondary UNIQUE violations in the SOURCE data
    don't block the load -> no row dropped), then swap the staging table in. The kept
    original ids mean every foreign key stays consistent.
  - `user` is MERGED: keep fresh admin users, insert old users with remapped ids, and
    remap every FK column that points at user.
  - System/config tables kept from the fresh DB; new-only seed referencing replaced
    tables (cash_day_*) is cleared.

Run:  python3 "FIRST CLASS DATA/migrate.py"
"""
import os, re, shutil, sqlite3, datetime

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
OLD = os.path.join(ROOT, "ahmed_cement.db")
NEW = os.path.join(ROOT, "NewData", "ahmed_cement_v44_fresh.db")
OUT_DIR = os.path.join(ROOT, "FIRST CLASS DATA")
OUT = os.path.join(OUT_DIR, "ahmed_cement_migrated.db")
REPORT = os.path.join(OUT_DIR, "migration_report.txt")

KEEP_FROM_NEW = {
    # user is merged specially (admin users kept + old users remapped).
    "user",
    # Migration tracking tables: old has none, keep the fresh DB's records.
    "migration_run", "migration_mapping", "migration_row",
    # Pure system/config tables (0 rows in both old and new).
    "settings", "root_backup_email_history", "root_backup_settings",
    "root_recovery_code", "system_lock", "schema_version",
    "tenant_wipe_backup_history", "staff_email",
    "import_history_entry", "import_job", "import_upload",
    # NOTE: fbm_* and user_login_session are BUSINESS data and are loaded from OLD
    # (they are intentionally NOT in this set, so the REPLACE loop migrates them).
}
DELETE_SEED = {"cash_day_account_position", "cash_day_lock"}


def unique_indexes(con, t):
    out = []
    for ix in con.execute(f'PRAGMA index_list("{t}")').fetchall():
        if ix[2] == 1:  # unique
            name = ix[1]
            sql = con.execute("SELECT sql FROM sqlite_master WHERE name=?", (name,)).fetchone()[0]
            out.append((name, sql))
    return out


def strip_secondary_unique(create_sql, t, tmp_name):
    """Return a CREATE TABLE statement for tmp_name identical to `t` but with all
    secondary UNIQUE constraints removed (PRIMARY KEY kept). Auto-indexes created by
    inline UNIQUE cannot be dropped later, so we strip them at DDL time."""
    s = create_sql
    s = re.sub(r',\s*CONSTRAINT\s+\w+\s+UNIQUE\s*\([^)]*\)', ' ', s, flags=re.I)
    s = re.sub(r'\bUNIQUE\s*\([^)]*\)', ' ', s, flags=re.I)
    s = re.sub(r'\bUNIQUE\b', ' ', s, flags=re.I)
    s = re.sub(r'\s*,\s*,+', ',', s)
    s = re.sub(r',\s*\)', ')', s)
    s = re.sub(r'\s+', ' ', s)
    if f'CREATE TABLE "{t}" (' in s:
        s = s.replace(f'CREATE TABLE "{t}" (', f'CREATE TABLE "{tmp_name}" (', 1)
    else:
        s = re.sub(r'CREATE\s+TABLE\s+(?:IF\s+NOT\s+EXISTS\s+)?[`"]?' + re.escape(t) + r'[`"]?',
                   f'CREATE TABLE "{tmp_name}"', s, count=1, flags=re.I)
    return s


def load_table(con, t):
    old_cols = [r[1] for r in con.execute(f'PRAGMA old.table_info("{t}")').fetchall()]
    out_cols = [r[1] for r in con.execute(f'PRAGMA table_info("{t}")').fetchall()]
    inter = [c for c in old_cols if c in out_cols]
    col_csv = ", ".join(f'"{c}"' for c in inter)
    seed_before = con.execute(f'SELECT COUNT(*) FROM "{t}"').fetchone()[0]
    orig = con.execute("SELECT sql FROM sqlite_master WHERE name=?", (t,)).fetchone()[0]
    uidx = unique_indexes(con, t)
    tmp = "tmp_" + t
    staged = strip_secondary_unique(orig, t, tmp)
    con.execute(f'DROP TABLE IF EXISTS "{tmp}"')
    con.execute(staged)
    con.execute(f'INSERT INTO "{tmp}" ({col_csv}) SELECT {col_csv} FROM old."{t}"')
    n = con.execute(f'SELECT COUNT(*) FROM "{tmp}"').fetchone()[0]
    con.execute(f'DROP TABLE IF EXISTS "{t}"')
    con.execute(f'ALTER TABLE "{tmp}" RENAME TO "{t}"')
    recreated, relaxed = [], []
    for name, sql in uidx:
        if not sql:
            relaxed.append(name)
            continue
        try:
            con.execute(sql)
            recreated.append(name)
        except Exception as e:
            relaxed.append(f"{name} ({str(e)[:60]})")
    return seed_before, n, recreated, relaxed


def main():
    assert os.path.exists(OLD), f"missing {OLD}"
    assert os.path.exists(NEW), f"missing {NEW}"
    if os.path.exists(OUT):
        os.remove(OUT)
    shutil.copy(NEW, OUT)

    con = sqlite3.connect(OUT)
    con.execute("PRAGMA foreign_keys=0")
    con.execute("ATTACH DATABASE ? AS old", (OLD,))

    out_tables = [r[0] for r in con.execute(
        "SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'")]
    old_tables = [r[0] for r in con.execute(
        "SELECT name FROM old.sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'")]
    shared = sorted(set(out_tables) & set(old_tables))

    report = [f"Migration run : {datetime.datetime.now()}",
              f"OLD (source)  : {OLD}", f"NEW (schema)  : {NEW}",
              f"OUT (result)  : {OUT}", ""]

    for t in shared:
        if t in KEEP_FROM_NEW or t == "user":
            continue
        seed, n, rec, relaxed = load_table(con, t)
        extra = f"  uniq_recreated={len(rec)}" + (f"  RELAXED={relaxed}" if relaxed else "")
        report.append(f"[REPLACE] {t:30} seed={seed:6} -> old={n:6}{extra}")

    for t in DELETE_SEED:
        if t in out_tables:
            before = con.execute(f'SELECT COUNT(*) FROM "{t}"').fetchone()[0]
            con.execute(f'DELETE FROM "{t}"')
            report.append(f"[CLEAR  ] {t:30} seed={before:6} -> 0")

    # Merge user
    old_users = [dict(zip([d[0] for d in con.execute("SELECT * FROM old.\"user\"").description], r))
                 for r in con.execute('SELECT * FROM old."user"')]
    out_user_cols = [r[1] for r in con.execute('PRAGMA table_info("user")').fetchall()]
    new_usernames = {r[0] for r in con.execute('SELECT username FROM "user"')}
    max_id = con.execute('SELECT COALESCE(MAX(id),0) FROM "user"').fetchone()[0]
    umap = {}
    for u in old_users:
        max_id += 1
        uname = u.get("username")
        if uname in new_usernames:
            base, cand, i = f"{uname}_legacy", f"{uname}_legacy", 1
            while cand in new_usernames:
                i += 1
                cand = f"{base}{i}"
            uname, new_usernames = cand, new_usernames | {cand}
        vals = []
        for c in out_user_cols:
            if c == "id":
                vals.append(max_id)
            elif c == "username":
                vals.append(uname)
            elif c in u:
                vals.append(u[c])
            else:
                vals.append(None)
        ph = ", ".join("?" for _ in out_user_cols)
        ccsv = ", ".join(f'"{c}"' for c in out_user_cols)
        con.execute(f'INSERT INTO "user" ({ccsv}) VALUES ({ph})', vals)
        umap[u["id"]] = max_id
    report.append(f"[MERGE  ] user                  kept=2 + old_users={len(old_users)} (ids remapped)")
    report.append(f"          user id map (old->new): {umap}")

    # These Django SQLite tables define NO DB-level FOREIGN KEY constraints, so we
    # remap user references by explicit column name. (Verified: the only integer user-id
    # FKs are user_id / created_by_id; other *_by columns store usernames as VARCHAR.)
    USER_REF_COLS = {"user_id", "created_by_id"}
    remapped = set()
    if umap:
        for t in out_tables:
            tcols = [r[1] for r in con.execute(f'PRAGMA table_info("{t}")').fetchall()]
            for c in tcols:
                if c in USER_REF_COLS:
                    whens = " ".join(f"WHEN {o} THEN {n}" for o, n in umap.items())
                    keys = ", ".join(str(o) for o in umap)
                    sql = (f'UPDATE "{t}" SET "{c}" = CASE "{c}" {whens} '
                           f'ELSE "{c}" END WHERE "{c}" IN ({keys})')
                    if con.execute(sql).rowcount:
                        remapped.add(f"{t}.{c}")
    report.append(f"          user-id refs remapped: {sorted(remapped)}")

    con.commit()
    con.execute("DETACH DATABASE old")
    con.commit()

    report.append("")
    report.append("=== VERIFICATION ===")
    report.append(f"integrity_check: {con.execute('PRAGMA integrity_check').fetchone()[0]}")
    cold = sqlite3.connect(OLD); cnew = sqlite3.connect(NEW)
    mism = []
    for t in sorted(shared):
        o = cold.execute(f'SELECT COUNT(*) FROM "{t}"').fetchone()[0]
        n = cnew.execute(f'SELECT COUNT(*) FROM "{t}"').fetchone()[0]
        out = con.execute(f'SELECT COUNT(*) FROM "{t}"').fetchone()[0]
        expect = n if (t in KEEP_FROM_NEW and t != "user") else (n + len(old_users) if t == "user" else o)
        st = "OK" if out == expect else "MISMATCH"
        if out != expect:
            mism.append(t)
        report.append(f"  {t:30} old={o:6} new_seed={n:6} migrated={out:6} expect={expect:6} [{st}]")
    cold.close(); cnew.close()

    report.append("")
    report.append("=== FOREIGN KEY ORPHAN CHECK ===")
    orphans = []
    for t in out_tables:
        for fk in con.execute(f'PRAGMA foreign_key_list("{t}")').fetchall():
            ref, fcol, tcol = fk[2], fk[3], fk[4]
            if ref not in out_tables:
                continue
            bad = con.execute(
                f'SELECT COUNT(*) FROM "{t}" WHERE "{fcol}" IS NOT NULL '
                f'AND "{fcol}" NOT IN (SELECT "{tcol}" FROM "{ref}")').fetchone()[0]
            if bad:
                orphans.append((t, fcol, ref, bad))
                report.append(f"  ORPHAN {t}.{fcol} -> {ref}.{tcol}: {bad}")
    if not orphans:
        report.append("  none (all FK references resolve)")

    report.append("")
    report.append("=== LOGICAL USER-FK CHECK (DB defines no FK constraints) ===")
    lorphans = []
    for t in out_tables:
        tcols = [r[1] for r in con.execute(f'PRAGMA table_info("{t}")').fetchall()]
        for c in tcols:
            if c in USER_REF_COLS:
                bad = con.execute(
                    f'SELECT COUNT(*) FROM "{t}" WHERE "{c}" IS NOT NULL '
                    f'AND "{c}" NOT IN (SELECT id FROM "user")').fetchone()[0]
                if bad:
                    lorphans.append((t, c, bad))
                    report.append(f"  ORPHAN {t}.{c} -> user.id: {bad}")
    if not lorphans:
        report.append("  none (all user_id references resolve to a real user)")
    report.append("")
    report.append("RESULT: PASS - no data loss, no broken FKs." if (not mism and not orphans and not lorphans)
                  else f"RESULT: REVIEW - count_mismatches={mism} orphans={orphans} logical_user_fk={lorphans}")

    con.commit(); con.close()
    text = "\n".join(report)
    with open(REPORT, "w") as f:
        f.write(text)
    print(text)


if __name__ == "__main__":
    main()
