"""Endpoints Vaani calls: two live tools during the call and one post-call webhook.
Set VAANI_WEBHOOK_SECRET and send it as header X-Webhook-Secret (or ?key=) so strangers cannot post here."""
import re, secrets, time
from datetime import datetime, timedelta
from typing import Any, Optional
from fastapi import APIRouter, HTTPException, Request
from app import agent, config, db, integrations as integ, slots as slotmod

router = APIRouter(prefix="/api/vaani")

def _auth(request: Request):
    want = config.env("VAANI_WEBHOOK_SECRET")
    if not want:
        return
    got = request.headers.get("x-webhook-secret") or request.query_params.get("key") or ""
    if not secrets.compare_digest(got, want):
        raise HTTPException(401, "bad webhook secret")

async def _body(request: Request) -> dict:
    try:
        b = await request.json()
    except Exception:
        b = {}
    return b if isinstance(b, dict) else {}

def _pick(d: dict, *names, default=None):
    """Vaani field names are not documented here, so accept the common spellings."""
    for n in names:
        for k, v in d.items():
            if k.lower().replace("-", "_") == n and v not in (None, ""):
                return v
    return default

@router.post("/tools/availability")
async def tool_availability(request: Request):
    """Vaani tool: check_availability. Returns the next free consultation slots from Cal.com."""
    _auth(request)
    out = slotmod.available_slots(datetime.now(), 6)
    return {"slots": [{"id": s["id"], "label": s["label"], "start": s["start"]} for s in out],
            "say": "Offer the caller two of these. Use the start value exactly when booking."}

@router.post("/tools/book")
async def tool_book(request: Request):
    """Vaani tool: book_appointment. Books on Cal.com, which emails the client the invite."""
    _auth(request)
    b = await _body(request)
    name, email, start = _pick(b, "caller_name", "name", "full_name"), _pick(b, "caller_email", "email", "email_address"), _pick(b, "slot_start", "start", "start_time", "slot", "datetime")
    if not (email and start):
        return {"ok": False, "say": "I still need the caller's email and the slot start time. Ask for the email and spell it back."}
    slot = {"start": start, "label": str(start), "designer": "Aangan designer", "designer_email": config.DESIGNERS[0]["email"], "source": "cal"}
    try:
        dt = datetime.fromisoformat(str(start).replace("Z", "+00:00"))
        slot["label"] = dt.strftime("%A %d %B, %I:%M %p").replace(" 0", " ")
        res = integ.cal_book(_pick(b, "call_id", default="vaani"), {"name": name, "email": email}, slot,
                             {"gist": _pick(b, "call_notes", "notes", "summary", default="")})
    except Exception as e:
        msg = str(e).replace(config.env("CAL_API_KEY") or "x", "***")
        if "cannot receive mail" in msg or "400" in msg:
            return {"ok": False, "say": "That slot or email did not work. Ask the caller to repeat the email letter by letter, or offer another slot."}
        return {"ok": False, "say": "Booking failed. Tell the caller a designer will call them to confirm."}
    db.kv_set("vaani_booking:" + str(email).lower(), {"slot": slot, "invite": res}, datetime.now().isoformat(timespec="seconds"))
    return {"ok": True, "say": f"Booked for {slot['label']}. The caller will get an email with the calendar invite."}

def _turns(raw: Any):
    """Accepts a list of {role/speaker, text/content/message} or a plain 'Agent: ... User: ...' string."""
    out = []
    if isinstance(raw, list):
        for m in raw:
            if isinstance(m, dict):
                who = str(_pick(m, "role", "speaker", "from", default="")).lower()
                text = _pick(m, "text", "content", "message", "transcript", default="")
                if text:
                    out.append({"who": "agent" if who in ("agent", "assistant", "bot", "ai", "model") else "caller", "text": str(text)})
    elif isinstance(raw, str):
        for line in raw.splitlines():
            m = re.match(r"\s*(agent|assistant|bot|ai|user|caller|customer|human)\s*[:\-]\s*(.*)", line, re.I)
            if m and m.group(2).strip():
                out.append({"who": "agent" if m.group(1).lower() in ("agent", "assistant", "bot", "ai") else "caller", "text": m.group(2).strip()})
            elif out and line.strip():
                out[-1]["text"] += " " + line.strip()
    return out

@router.post("/webhook")
async def post_call(request: Request):
    """Vaani post-call webhook: judge the call, write the gist, sync HubSpot, send emails, log for the dashboard."""
    _auth(request)
    b = await _body(request)
    data = b.get("data") if isinstance(b.get("data"), dict) else b
    transcript = _turns(_pick(data, "transcript", "messages", "conversation", "call_transcript", default=[]))
    when = _pick(data, "started_at", "start_time", "created_at", "timestamp")
    try:
        started = datetime.fromisoformat(str(when).replace("Z", "+00:00")).replace(tzinfo=None) if when else datetime.now()
    except ValueError:
        started = datetime.now()
    try:
        duration = float(_pick(data, "duration", "duration_seconds", "call_duration", default=0) or 0) or 120.0
    except (TypeError, ValueError):
        duration = 120.0
    phone = _pick(data, "from", "from_number", "caller", "phone", "phone_number", "customer_number")
    cid = "V" + str(_pick(data, "call_id", "id", "conversation_id", default=int(time.time())))[:12].upper()
    def find_booking(lead):
        e = (lead.get("email") or "").lower()
        v, _ = db.kv_get("vaani_booking:" + e) if e else (None, None)
        return v
    rec = agent.finalize(transcript, started, duration, "vaani", str(phone or "Vaani caller"), call_id=cid, phone=phone,
                         ring_to_answer_s=float(_pick(data, "ring_to_answer_s", "answer_time", default=2.0) or 2.0),
                         find_booking=find_booking, missed=not transcript)
    return {"ok": True, "call_id": rec["id"], "verdict": rec["verdict"]}
