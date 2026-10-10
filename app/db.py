import sqlite3, json, os
from contextlib import contextmanager
from app import config

DATABASE_URL = os.getenv("DATABASE_URL", "")   # Neon Postgres connection string. Empty means local SQLite.
PG = DATABASE_URL.startswith("postgres")

COLS = ["id", "started_at", "source", "caller_label", "ring_to_answer_s", "in_hours", "duration_s", "missed", "transcript", "lead",
        "result", "verdict", "gist", "cost", "cost_inr", "integrations", "slot"]

SQLITE_SCHEMA = """
CREATE TABLE IF NOT EXISTS calls (
  id TEXT PRIMARY KEY, started_at TEXT, source TEXT, caller_label TEXT,
  ring_to_answer_s REAL, in_hours INTEGER, duration_s REAL, missed INTEGER DEFAULT 0,
  transcript TEXT, lead TEXT, result TEXT, verdict TEXT, gist TEXT,
  cost TEXT, cost_inr REAL, integrations TEXT, slot TEXT
);
CREATE TABLE IF NOT EXISTS kv (k TEXT PRIMARY KEY, v TEXT, updated_at TEXT);
CREATE TABLE IF NOT EXISTS sessions (id TEXT PRIMARY KEY, data TEXT, updated_at TEXT);
CREATE TABLE IF NOT EXISTS audit (id TEXT PRIMARY KEY, ts TEXT, call_id TEXT, actor TEXT, action TEXT, detail TEXT);
"""
PG_SCHEMA = SQLITE_SCHEMA.replace("REAL", "DOUBLE PRECISION")

_READY = False   # set up tables and columns once per process so views do not run extra queries

def _setup(c):
    global _READY
    if _READY:
        return
    if PG:
        c.execute(PG_SCHEMA)
        c.execute("ALTER TABLE calls ADD COLUMN IF NOT EXISTS ops TEXT")
        c.execute("ALTER TABLE calls ADD COLUMN IF NOT EXISTS external_id TEXT")
    else:
        c.executescript(SQLITE_SCHEMA)
        have = {r[1] for r in c.execute("PRAGMA table_info(calls)")}
        for col in ("ops", "external_id"):
            if col not in have:
                c.execute(f"ALTER TABLE calls ADD COLUMN {col} TEXT")
    _READY = True

@contextmanager
def conn():
    if PG:
        import psycopg
        from psycopg.rows import dict_row
        c = psycopg.connect(DATABASE_URL, row_factory=dict_row)
    else:
        config.DB_PATH.parent.mkdir(exist_ok=True)
        c = sqlite3.connect(config.DB_PATH); c.row_factory = sqlite3.Row
    _setup(c)
    try:
        yield c; c.commit()
    finally:
        c.close()

def _q(sql):
    return sql.replace("?", "%s") if PG else sql

def _exec(c, sql, args=()):
    return c.execute(_q(sql), list(args))

JSON_COLS = ("transcript", "lead", "result", "cost", "integrations", "slot", "ops")

def save_call(rec: dict):
    row = {k: (json.dumps(v) if k in JSON_COLS else v) for k, v in rec.items()}
    cols = ",".join(row); qs = ",".join("?" * len(row))
    upd = ",".join(f"{k}=EXCLUDED.{k}" for k in row if k != "id")
    with conn() as c:
        _exec(c, f"INSERT INTO calls ({cols}) VALUES ({qs}) ON CONFLICT (id) DO UPDATE SET {upd}", row.values())
    from app import stats
    stats.invalidate()

def _load(r):
    d = dict(r)
    for k in JSON_COLS:
        if d.get(k):
            d[k] = json.loads(d[k])
    return d

def all_calls():
    with conn() as c:
        return [_load(r) for r in _exec(c, "SELECT * FROM calls ORDER BY started_at DESC").fetchall()]

def get_call(cid):
    with conn() as c:
        r = _exec(c, "SELECT * FROM calls WHERE id=?", (cid,)).fetchone()
        return _load(r) if r else None

def kv_get(k):
    with conn() as c:
        r = _exec(c, "SELECT v, updated_at FROM kv WHERE k=?", (k,)).fetchone()
        return (json.loads(r["v"]), r["updated_at"]) if r else (None, None)

def kv_set(k, v, ts):
    with conn() as c:
        _exec(c, "INSERT INTO kv (k, v, updated_at) VALUES (?,?,?) ON CONFLICT (k) DO UPDATE SET v=EXCLUDED.v, updated_at=EXCLUDED.updated_at", (k, json.dumps(v), ts))

# Live-call state lives in the database so any serverless instance can serve the next turn.
def session_save(cid, data, ts):
    with conn() as c:
        _exec(c, "INSERT INTO sessions (id, data, updated_at) VALUES (?,?,?) ON CONFLICT (id) DO UPDATE SET data=EXCLUDED.data, updated_at=EXCLUDED.updated_at", (cid, json.dumps(data), ts))

def session_load(cid):
    with conn() as c:
        r = _exec(c, "SELECT data FROM sessions WHERE id=?", (cid,)).fetchone()
        return json.loads(r["data"]) if r else None

def session_delete(cid):
    with conn() as c:
        _exec(c, "DELETE FROM sessions WHERE id=?", (cid,))

def update_ops(cid, changes: dict):
    """Merge staff actions (stage, assignee, override, callback) into the call's ops record."""
    c = get_call(cid)
    if not c:
        return None
    ops = dict(c.get("ops") or {})
    ops.update(changes)
    with conn() as k:
        _exec(k, "UPDATE calls SET ops=? WHERE id=?", (json.dumps(ops), cid))
    from app import stats
    stats.invalidate()
    return ops

# Append-only log of staff actions. There is deliberately no update or delete function.
def audit_add(call_id, action, detail, actor="dashboard"):
    import uuid
    from datetime import datetime
    with conn() as c:
        _exec(c, "INSERT INTO audit (id, ts, call_id, actor, action, detail) VALUES (?,?,?,?,?,?)",
              (uuid.uuid4().hex, datetime.now().isoformat(timespec="seconds"), call_id, (actor or "dashboard")[:60], action, json.dumps(detail)))

def audit_list(call_id=None, limit=50):
    with conn() as c:
        if call_id:
            rows = _exec(c, "SELECT * FROM audit WHERE call_id=? ORDER BY ts DESC LIMIT ?", (call_id, limit)).fetchall()
        else:
            rows = _exec(c, "SELECT * FROM audit ORDER BY ts DESC LIMIT ?", (limit,)).fetchall()
    out = []
    for r in rows:
        d = dict(r); d["detail"] = json.loads(d["detail"]) if d.get("detail") else None; out.append(d)
    return out

def update_result(cid, result: dict):
    with conn() as k:
        _exec(k, "UPDATE calls SET result=? WHERE id=?", (json.dumps(result), cid))
    from app import stats
    stats.invalidate()
