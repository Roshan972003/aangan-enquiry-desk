"""Mock designer calendar. Replace available_slots() with a Google Calendar free/busy call later."""
import hashlib
from datetime import datetime, timedelta, timezone
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


def match_slot(iso: str):
    """Find the free Cal.com slot that matches a time the caller agreed to. Returns a slot dict, or None if it is no longer free."""
    k, et = config.env("CAL_API_KEY"), config.env("CAL_EVENT_TYPE_ID")
    if not (k and et and iso):
        return None
    try:
        want = datetime.fromisoformat(iso)
    except ValueError:
        return None
    if want.tzinfo is None:
        want = want.replace(tzinfo=timezone(timedelta(hours=5, minutes=30)))
    day = want.astimezone(timezone(timedelta(hours=5, minutes=30))).date()
    r = httpx.get("https://api.cal.com/v2/slots", headers={"Authorization": f"Bearer {k}", "cal-api-version": "2024-09-04"},
                  params={"eventTypeId": et, "start": day.isoformat(), "end": (day + timedelta(days=1)).isoformat(), "timeZone": "Asia/Kolkata"}, timeout=20)
    if r.status_code != 200:
        return None
    for _, items in r.json().get("data", {}).items():
        for i in items:
            t = datetime.fromisoformat(i["start"])
            if abs((t - want).total_seconds()) < 60:
                return {"id": 0, "start": t.isoformat(), "label": t.strftime("%A %d %B, %I:%M %p").replace(" 0", " "),
                        "designer": "Aangan designer", "designer_email": DESIGNERS[0]["email"], "source": "cal"}
    return None


def nearest_slot(iso=None):
    """Free Cal.com slot closest to the time the caller prefers, or the earliest free slot when they gave none."""
    k, et = config.env("CAL_API_KEY"), config.env("CAL_EVENT_TYPE_ID")
    if not (k and et):
        return None
    tz = timezone(timedelta(hours=5, minutes=30))
    now = config.now_ist().replace(tzinfo=tz)
    want = None
    if iso:
        try:
            want = datetime.fromisoformat(iso)
            if want.tzinfo is None:
                want = want.replace(tzinfo=tz)
        except ValueError:
            want = None
    start_day = max((want or now).astimezone(tz).date() - timedelta(days=1), (now + timedelta(days=1)).date())
    r = httpx.get("https://api.cal.com/v2/slots", headers={"Authorization": f"Bearer {k}", "cal-api-version": "2024-09-04"},
                  params={"eventTypeId": et, "start": start_day.isoformat(), "end": (start_day + timedelta(days=8)).isoformat(), "timeZone": "Asia/Kolkata"}, timeout=20)
    if r.status_code != 200:
        return None
    cands = [datetime.fromisoformat(i["start"]) for _, items in sorted(r.json().get("data", {}).items()) for i in items]
    cands = [c for c in cands if c > now + timedelta(hours=3)]
    if not cands:
        return None
    best = min(cands, key=lambda c: abs((c - want).total_seconds())) if want else cands[0]
    return {"id": 0, "start": best.isoformat(), "label": best.strftime("%A %d %B, %I:%M %p").replace(" 0", " "),
            "designer": "Aangan designer", "designer_email": DESIGNERS[0]["email"], "source": "cal"}
