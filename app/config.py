import os, json
from pathlib import Path
from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parent.parent
load_dotenv(ROOT / ".env")

def env(k, d=""):
    return os.getenv(k, d)

GEMINI_API_KEY = env("GEMINI_API_KEY")
GEMINI_MODEL = env("GEMINI_MODEL", "gemini-3.5-flash")
MOCK_LLM = env("MOCK_LLM", "0") == "1"
HUBSPOT_TOKEN = env("HUBSPOT_TOKEN")
SMTP_HOST, SMTP_PORT = env("SMTP_HOST"), int(env("SMTP_PORT", "587") or 587)
SMTP_USER, SMTP_PASS = env("SMTP_USER"), env("SMTP_PASS")
STUDIO_EMAIL = env("STUDIO_EMAIL", "studio@example.com")
STUDIO_HEAD_EMAIL = env("STUDIO_HEAD_EMAIL", "nikhil@example.com")

INR_PER_USD = float(env("INR_PER_USD", "88"))
GEMINI_IN = float(env("GEMINI_IN_USD_PER_M", "0.30"))
GEMINI_OUT = float(env("GEMINI_OUT_USD_PER_M", "2.50"))
TELEPHONY_PER_MIN = float(env("TELEPHONY_INR_PER_MIN", "1.2"))
SPEECH_PER_MIN = float(env("SPEECH_INR_PER_MIN", "0.8"))

DB_PATH = ROOT / "data" / "aangan.db"
OUTBOX = ROOT / "outbox"
KNOWLEDGE = ROOT / "knowledge"
DESIGNERS = json.loads((ROOT / "designers.json").read_text())

OPEN_HOUR, CLOSE_HOUR = 10, 19   # front desk hours, from the brief
AVG_PROJECT_LAKH = 11            # midpoint of the Rs 8-14 lakh average in the brief

# Assumptions behind the ROI and time-saved numbers. Editable on the dashboard and stored in the database.
DEFAULT_SETTINGS = {"close_rate_pct": 10, "minutes_saved_per_call": 6, "project_low_lakh": 8, "project_high_lakh": 14, "offhour_loss_pct": 48}
