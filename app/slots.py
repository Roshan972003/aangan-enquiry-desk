"""Mock designer calendar. Replace available_slots() with a Google Calendar free/busy call later."""
import hashlib
from datetime import datetime, timedelta
from app.config import DESIGNERS

TIMES = [(11, 0), (15, 0), (17, 30)]

def _busy(designer_email: str, dt: datetime) -> bool:
    h = hashlib.md5(f"{designer_email}{dt:%Y%m%d%H%M}".encode()).hexdigest()
    return int(h[:2], 16) % 4 == 0   # about a quarter of slots taken

def available_slots(after: datetime, n: int = 6, commercial: bool = False):
    out, day = [], (after + timedelta(days=1)).replace(hour=0, minute=0, second=0, microsecond=0)
    pool = [d for d in DESIGNERS if d.get("commercial") or not commercial]
    while len(out) < n and (day - after).days < 21:
        if day.weekday() != 6:  # no Sundays
            for hh, mm in TIMES:
                dt = day.replace(hour=hh, minute=mm)
                free = [d for d in pool if not _busy(d["email"], dt)]
                if free:
                    out.append({"id": len(out) + 1, "start": dt.isoformat(), "label": dt.strftime("%A %d %B, %I:%M %p").replace(" 0", " "),
                                "designer": free[0]["name"], "designer_email": free[0]["email"]})
                if len(out) >= n:
                    break
        day += timedelta(days=1)
    return out
