"""Endpoints Vaani calls: two live tools during the call and one post-call webhook.
Set VAANI_WEBHOOK_SECRET and send it as header X-Webhook-Secret (or ?key=) so strangers cannot post here."""
import re, secrets, time
from datetime import datetime, timedelta, timezone
from typing import Any, Optional
from fastapi import APIRouter, HTTPException, Request
from app import agent, config, db, integrations as integ, slots as slotmod

router = APIRouter(prefix="/api/vaani")

def _auth(request: Request):
    """Tool calls need the secret when one is set. (The webhook is checked against Vaani itself, see _confirmed.)"""
    want = config.env("VAANI_WEBHOOK_SECRET")
    if not want:
        return
    got = request.headers.get("x-webhook-secret") or request.query_params.get("key") or ""
    if not secrets.compare_digest(got, want):
        raise HTTPException(401, "bad webhook secret")

def _has_secret(request: Request) -> bool:
    want = config.env("VAANI_WEBHOOK_SECRET")
    got = request.headers.get("x-webhook-secret") or request.query_params.get("key") or ""
    return bool(want) and secrets.compare_digest(got, want)

def _confirmed(call_id):
    """Ask Vaani whether this call really exists, so a stranger cannot post a fake one. Returns the real transcript or None."""
    key = config.env("VAANI_API_KEY")
    if not key or config.MOCK_LLM:
        return None
    import httpx
    try:
        r = httpx.get(f"https://api.vaanivoice.ai/api/call_details/{call_id}", headers={"X-API-Key": key}, timeout=20)
        if r.status_code == 200:
            t = r.json().get("transcription")
            if t and "not available" not in str(t).lower():
                return t
    except httpx.HTTPError:
        pass
    return None

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
            m = re.match(r"\s*(?:\[[^\]]*\]\s*)?(agent|assistant|bot|ai|user|caller|customer|human)\s*[:\-]\s*(.*)", line, re.I)
            if m and m.group(2).strip():
                out.append({"who": "agent" if m.group(1).lower() in ("agent", "assistant", "bot", "ai") else "caller", "text": m.group(2).strip()})
            elif out and line.strip():
                out[-1]["text"] += " " + line.strip()
    return out

def _process(call_id, transcript, started, duration_s, phone, summary_hint=None):
    """Run one finished Vaani call through the pipeline unless it was already handled."""
    cid = "V" + str(call_id)[-14:].upper().replace("-", "")
    if db.get_call(cid):
        return cid, "already processed"
    def find_booking(lead):
        e = (lead.get("email") or "").lower()
        v, _ = db.kv_get("vaani_booking:" + e) if e else (None, None)
        return v
    rec = agent.finalize(transcript, started, duration_s, "vaani", str(phone or "Vaani caller"), call_id=cid, phone=phone,
                         ring_to_answer_s=2.0, find_booking=find_booking, missed=not transcript, external_id=str(call_id))
    return rec["id"], rec["verdict"]

@router.post("/webhook")
async def post_call(request: Request):
    """Vaani webhook. Only the call_postprocessing event carries the transcript, so the others are acknowledged and ignored."""
    b = await _body(request)
    event = b.get("event")
    db.kv_set("webhook_last", {"event": event, "at": datetime.now().isoformat(timespec="seconds")}, datetime.now().isoformat(timespec="seconds"))
    if event and event != "call_postprocessing":
        return {"ok": True, "ignored": event}
    data = b.get("data") if isinstance(b.get("data"), dict) else b
    transcript = _turns(_pick(data, "transcript", "transcription", "messages", "conversation", default=[]))
    try:
        dur_ms = float(_pick(data, "call_duration", "duration_ms", "duration", default=0) or 0)
    except (TypeError, ValueError):
        dur_ms = 0.0
    duration_s = max(5.0, dur_ms / 1000.0) if dur_ms else 120.0
    ts = _pick(b, "timestamp") or _pick(data, "timestamp", "started_at", "start_time")
    try:
        end = datetime.fromisoformat(str(ts).replace("Z", "+00:00"))
        if end.tzinfo:
            end = end.astimezone(timezone(timedelta(hours=5, minutes=30))).replace(tzinfo=None)
        started = end - timedelta(seconds=duration_s)
    except (ValueError, TypeError):
        started = datetime.now()
    phone = _pick(data, "from_number", "from", "caller", "phone_number", "phone", "customer_number")
    call_id = _pick(data, "call_id", "room_name", "id") or _pick(b, "call_id") or int(time.time())
    if not _has_secret(request):
        real = _confirmed(call_id)
        if real is None and not config.MOCK_LLM:
            raise HTTPException(401, "call not confirmed by Vaani")
        if real:
            transcript = _turns(real)   # trust Vaani's copy, not the posted one
    cid, verdict = _process(call_id, transcript, started, duration_s, phone)
    return {"ok": True, "call_id": cid, "verdict": verdict}

VAANI_API = "https://api.vaanivoice.ai/api"

@router.post("/sync")
async def sync_from_vaani(request: Request, limit: int = 20):
    """Backup for the webhook: read recent finished calls from Vaani's call history and process any we have not seen."""
    _auth(request)
    key = config.env("VAANI_API_KEY")
    if not key:
        raise HTTPException(503, "VAANI_API_KEY is not set")
    import httpx
    h = {"X-API-Key": key}
    done = []
    with httpx.Client(timeout=30, headers=h) as c:
        r = c.get(f"{VAANI_API}/call-history", params={"page": 1, "page_size": min(limit, 50)})
        if r.status_code != 200:
            return {"ok": False, "error": "Vaani call history is unavailable for this account (" + r.text[:140] + "). The webhook still works."}
        for item in r.json().get("data", []):
            if "aangan" not in str(item.get("agent_name", "")).lower():
                continue
            if str(item.get("post_processing_status", "")).lower() != "completed":
                continue
            cid_probe = "V" + str(item["call_id"])[-14:].upper().replace("-", "")
            if db.get_call(cid_probe):
                continue
            t = c.get(f"{VAANI_API}/call_details/{item['call_id']}")
            text = t.json().get("transcription", "") if t.status_code == 200 else ""
            try:
                started = datetime.fromisoformat(item["Start_time"])
            except (KeyError, ValueError):
                started = datetime.now()
            dur = float(item.get("duration_ms") or 0)
            dur_s = dur / 1000.0 if dur > 600 else dur   # the API reports small values in seconds, larger in ms
            cid, verdict = _process(item["call_id"], _turns(text), started, max(5.0, dur_s) if dur_s else 120.0,
                                    item.get("from_number") or item.get("to_number"))
            done.append({"call_id": cid, "verdict": verdict})
    return {"ok": True, "processed": done}
