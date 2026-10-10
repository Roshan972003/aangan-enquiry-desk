"""Copy the local SQLite call log into Neon. Run once after setting DATABASE_URL in .env."""
import sqlite3, os
from dotenv import load_dotenv
load_dotenv()
from app import config, db
assert db.PG, "Set DATABASE_URL in .env first"
src = sqlite3.connect(config.DB_PATH); src.row_factory = sqlite3.Row
rows = [dict(r) for r in src.execute("SELECT * FROM calls")]
with db.conn() as c:
    for r in rows:
        cols = ",".join(r); qs = ",".join(["%s"] * len(r)); upd = ",".join(f"{k}=EXCLUDED.{k}" for k in r if k != "id")
        c.execute(f"INSERT INTO calls ({cols}) VALUES ({qs}) ON CONFLICT (id) DO UPDATE SET {upd}", list(r.values()))
print(f"Copied {len(rows)} calls to Neon")
