"""Mock designer calendar. Replace available_slots() with a Google Calendar free/busy call later."""
import hashlib
from datetime import datetime, timedelta
import httpx
from app import config
from app.config import DESIGNERS

TIMES = [(11, 0), (15, 0), (17, 30)]

def _busy(designer_email: str, dt: datetime) -> bool:
    h = hashlib.md5(f"{designer_email}{dt:%Y%m%d%H%M}".encode()).hexdigest()
    return int(h[:2], 16) % 4 == 0   # about a quarter of slots taken

def _cal_slots(after: datetime, n: int):
    k, et = config.env("CAL_API_KEY"), config.env("CAL_EVENT_TYPE_ID")
    start = (after + timedelta(days=1)).date()
    r = httpx.get("https://api.cal.com/v2/slots", headers={"Authorization": f"Bearer {k}", "cal-api-version": "2024-09-04"},
                  params={"eventTypeId": et, "start": start.isoformat(), "end": (start + timedelta(days=8)).isoformat(), "timeZone": "Asia/Kolkata"}, timeout=20)
    r.raise_for_status()
    out = []
    for day, items in sorted(r.json()["data"].items()):
        times = [datetime.fromisoformat(i["start"]) for i in items]
        pick = [t for t in times if (t.hour, t.minute) in ((11, 0), (15, 0))] or times[:2]
        for t in pick:
            out.append({"id": len(out) + 1, "start": t.isoformat(), "label": t.strftime("%A %d %B, %I:%M %p").replace(" 0", " "),
                        "designer": "Aangan designer", "designer_email": DESIGNERS[0]["email"], "source": "cal"})
            if len(out) >= n:
                return out
    return out

def available_slots(after: datetime, n: int = 6, commercial: bool = False):
    if config.env("CAL_API_KEY") and config.env("CAL_EVENT_TYPE_ID"):
        try:
            got = _cal_slots(after, n)
            if got:
                return got
        except Exception:
            pass   # fall back to the mock calendar so a call never fails on Cal.com
    return _mock_slots(after, n, commercial)

def _mock_slots(after: datetime, n: int = 6, commercial: bool = False):
    out, day = [], (after + timedelta(days=1)).replace(hour=0, minute=0, second=0, microsecond=0)
    pool = [d for d in DESIGNERS if d.get("commercial") or not commercial]
    while len(out) < n and (day - after).days < 21:
        if day.weekday() != 6:  # no Sundays
            for hh, mm in TIMES:
                dt = day.replace(hour=hh, minute=mm)
                free = [d for d in pool if not _busy(d["email"], dt)]
                if free:
                    out.append({"id": len(out) + 1, "start": dt.isoformat(), "label": dt.strftime("%A %d %B, %I:%M %p").replace(" 0", " "),
                                "designer": free[0]["name"], "designer_email": free[0]["email"], "source": "mock"})
                if len(out) >= n:
                    break
        day += timedelta(days=1)
    return out
