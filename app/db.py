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
"""
PG_SCHEMA = SQLITE_SCHEMA.replace("REAL", "DOUBLE PRECISION")

@contextmanager
def conn():
    if PG:
        import psycopg
        from psycopg.rows import dict_row
        c = psycopg.connect(DATABASE_URL, row_factory=dict_row)
        c.execute(PG_SCHEMA)
    else:
        config.DB_PATH.parent.mkdir(exist_ok=True)
        c = sqlite3.connect(config.DB_PATH); c.row_factory = sqlite3.Row
        c.executescript(SQLITE_SCHEMA)
    try:
        yield c; c.commit()
    finally:
        c.close()

def _q(sql):
    return sql.replace("?", "%s") if PG else sql

def _exec(c, sql, args=()):
    return c.execute(_q(sql), list(args))

JSON_COLS = ("transcript", "lead", "result", "cost", "integrations", "slot")

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
