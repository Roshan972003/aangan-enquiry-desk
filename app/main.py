from datetime import datetime
from typing import Optional
from pathlib import Path
from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import FileResponse, JSONResponse, Response
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel
from app import agent, db, stats, config, gemini
from app.vaani import router as vaani_router

app = FastAPI(title="Aangan Enquiry Desk")

# Optional password gate. Any username, password = APP_PASSWORD. Open when APP_PASSWORD is unset.
OPEN_PATHS = ("/api/twilio/", "/api/health", "/api/vaani/")

@app.middleware("http")
async def password_gate(request, call_next):
    pw = config.env("APP_PASSWORD")
    if request.url.path.startswith(OPEN_PATHS):
        return await call_next(request)
    if not pw:
        return await call_next(request)
    import base64, secrets
    h = request.headers.get("authorization", "")
    if h.lower().startswith("basic "):
        try:
            given = base64.b64decode(h[6:]).decode().split(":", 1)[1]
            if secrets.compare_digest(given.encode(), pw.encode()):
                return await call_next(request)
        except Exception:
            pass
    return Response("Password required", status_code=401, headers={"WWW-Authenticate": 'Basic realm="Aangan Enquiry Desk"'})
STATIC = Path(__file__).parent / "static"
app.include_router(vaani_router)

class StartIn(BaseModel):
    caller_label: str = "Simulated caller"
    call_time: Optional[str] = None      # ISO, lets the demo pretend it is 11pm

class TurnIn(BaseModel):
    call_id: str
    text: str

class EndIn(BaseModel):
    call_id: str

@app.exception_handler(gemini.LLMUnavailable)
async def llm_down(_, exc):
    return JSONResponse({"error": str(exc)}, status_code=503)

@app.get("/api/health")
def health():
    return {"llm": "mock" if config.MOCK_LLM else ("gemini" if config.GEMINI_API_KEY else "missing key"),
            "hubspot": bool(config.HUBSPOT_TOKEN), "smtp": bool(config.SMTP_HOST), "vaani_key_set": bool(config.env("VAANI_API_KEY")),
            "cal": bool(config.env("CAL_API_KEY") and config.env("CAL_EVENT_TYPE_ID")), "db": "neon" if db.PG else "sqlite"}

@app.post("/api/call/start")
def call_start(b: StartIn):
    when = datetime.fromisoformat(b.call_time) if b.call_time else datetime.now()
    cid, reply, slots = agent.start_call(b.caller_label, when)
    return {"call_id": cid, "reply": reply, "slots": slots}

@app.post("/api/call/turn")
def call_turn(b: TurnIn):
    if not agent.exists(b.call_id):
        raise HTTPException(404, "Unknown call")
    reply, status, lead = agent.turn(b.call_id, b.text)
    return {"reply": reply, "status": status, "lead": lead}

@app.post("/api/call/end")
def call_end(b: EndIn):
    if not agent.exists(b.call_id):
        raise HTTPException(404, "Unknown call")
    return agent.end_call(b.call_id)

@app.get("/api/calls/{cid}")
def call_get(cid: str):
    c = db.get_call(cid)
    if not c:
        raise HTTPException(404, "Not found")
    c["effective_verdict"] = stats.eff_verdict(c)
    c["stage"] = stats.stage_of(c)
    c["has_audio"] = bool(c.get("external_id")) and bool(config.env("VAANI_API_KEY"))
    c["audit"] = db.audit_list(cid, 30)
    return c

class StageIn(BaseModel):
    stage: str

class AssignIn(BaseModel):
    designer: str

class OverrideIn(BaseModel):
    verdict: str
    note: str = ""

class CallbackIn(BaseModel):
    status: str = "called_back"

class SettingsIn(BaseModel):
    close_rate_pct: Optional[float] = None
    minutes_saved_per_call: Optional[float] = None
    project_low_lakh: Optional[float] = None
    project_high_lakh: Optional[float] = None

def _actor(request: Request):
    return request.headers.get("x-actor") or "dashboard user"

def _must(cid):
    c = db.get_call(cid)
    if not c:
        raise HTTPException(404, "Not found")
    return c

@app.post("/api/calls/{cid}/stage")
def set_stage(cid: str, b: StageIn, request: Request):
    c = _must(cid)
    if b.stage not in [k for k, _ in stats.STAGES]:
        raise HTTPException(400, "Unknown stage")
    before = stats.stage_of(c)
    db.update_ops(cid, {"stage": b.stage})
    db.audit_add(cid, "stage", {"from": before, "to": b.stage}, _actor(request))
    from app import integrations as integ
    hs = integ.hubspot_move_stage((c.get("integrations") or {}).get("hubspot", {}).get("deal_id"), b.stage)
    return {"ok": True, "hubspot": hs}

@app.post("/api/calls/{cid}/assign")
def assign(cid: str, b: AssignIn, request: Request):
    """Reassign a lead to a designer and email them the handoff note."""
    from app import integrations as integ
    c = _must(cid)
    d = next((x for x in config.DESIGNERS if x["name"] == b.designer), None)
    if not d:
        raise HTTPException(400, "Unknown designer")
    slot = dict(c.get("slot") or {})
    slot.update({"designer": d["name"], "designer_email": d["email"], "label": slot.get("label") or "not booked yet, call the client"})
    text = "\n".join(f"{t['who'].upper()}: {t['text']}" for t in (c.get("transcript") or []))
    mail = integ.designer_email(cid, c.get("lead") or {}, c.get("result") or {}, slot if c.get("slot") else None, text, True)
    mail["to"] = d["email"]
    before = (c.get("ops") or {}).get("assigned_to")
    db.update_ops(cid, {"assigned_to": d["name"], "assign_email": mail.get("status")})
    db.audit_add(cid, "assign", {"from": before, "to": d["name"]}, _actor(request))
    return {"ok": True, "email": mail.get("status")}

@app.post("/api/calls/{cid}/email")
def email_lead(cid: str, request: Request):
    """Resend the lead summary to the designer (the assigned one, or the first on the list) by email."""
    from app import integrations as integ
    c = _must(cid)
    name = (c.get("ops") or {}).get("assigned_to")
    d = next((x for x in config.DESIGNERS if x["name"] == name), config.DESIGNERS[0])
    slot = dict(c.get("slot") or {})
    slot.update({"designer": d["name"], "designer_email": d["email"], "label": slot.get("label") or "not booked yet, call the client"})
    text = "\n".join(f"{t['who'].upper()}: {t['text']}" for t in (c.get("transcript") or []))
    mail = integ.designer_email(cid, c.get("lead") or {}, c.get("result") or {}, slot if c.get("slot") else None, text, True)
    sent = mail.get("mode") == "live"
    db.audit_add(cid, "email", {"to": mail.get("to"), "message": mail.get("status")}, _actor(request))
    db.update_ops(cid, {"emailed": datetime.now().isoformat(timespec="seconds"), "assigned_to": d["name"]})
    return JSONResponse({"ok": sent, "message": ("Emailed " + str(mail.get("to"))) if sent else "Saved, not sent: Gmail is not connected yet (" + str(mail.get("status")) + ")"})

@app.post("/api/calls/{cid}/override")
def override(cid: str, b: OverrideIn, request: Request):
    """Staff change a call's outcome. Moving a lead to qualified also sends the designer handoff email."""
    from datetime import datetime as _dt
    from app import integrations as integ
    c = _must(cid)
    if b.verdict not in ("qualified", "declined", "not_now", "escalate"):
        raise HTTPException(400, "Unknown outcome")
    changes = {"override": {"verdict": b.verdict, "note": b.note, "original": c["verdict"], "at": _dt.now().isoformat(timespec="seconds")}}
    msg = "Outcome changed"
    if b.verdict == "qualified" and c["verdict"] != "qualified":
        text = "\n".join(f"{t['who'].upper()}: {t['text']}" for t in (c.get("transcript") or []))
        mail = integ.designer_email(cid, c.get("lead") or {}, c.get("result") or {}, c.get("slot"), text, True)
        changes["assign_email"] = mail.get("status")
        msg = "Outcome changed to qualified. Handoff email: " + str(mail.get("status"))
    db.update_ops(cid, changes)
    db.audit_add(cid, "outcome", {"from": stats.eff_verdict(c), "to": b.verdict, "note": b.note, "ai_said": c["verdict"]}, _actor(request))
    return {"ok": True, "message": msg}

@app.post("/api/calls/{cid}/override/clear")
def override_clear(cid: str, request: Request):
    c = _must(cid)
    db.update_ops(cid, {"override": None})
    db.audit_add(cid, "outcome", {"from": stats.eff_verdict(c), "to": c["verdict"], "note": "back to the AI outcome"}, _actor(request))
    return {"ok": True}

@app.post("/api/calls/{cid}/callback")
def callback(cid: str, b: CallbackIn, request: Request):
    _must(cid)
    db.update_ops(cid, {"callback": b.status})
    db.audit_add(cid, "callback", {"status": b.status}, _actor(request))
    return {"ok": True}

@app.get("/api/calls/{cid}/audio")
def audio(cid: str):
    """Play the call recording. Vaani needs the API key, so the server fetches it and passes it on."""
    import httpx
    c = _must(cid)
    key = config.env("VAANI_API_KEY")
    if not (c.get("external_id") and key):
        raise HTTPException(404, "No recording for this call")
    r = httpx.get(f"https://api.vaanivoice.ai/api/stream/{c['external_id']}", headers={"X-API-Key": key}, timeout=60)
    if r.status_code != 200:
        raise HTTPException(404, "Recording not available yet")
    return Response(r.content, media_type=r.headers.get("content-type", "audio/mpeg"))

@app.get("/api/settings")
def get_settings():
    return stats.settings()

@app.post("/api/settings")
def save_settings(b: SettingsIn):
    cur = stats.settings()
    cur.update({k: v for k, v in b.dict().items() if v is not None and v >= 0})
    db.kv_set("settings", cur, datetime.now().isoformat(timespec="seconds"))
    stats.invalidate()
    return cur

@app.get("/api/stats")
def get_stats():
    return stats.compute()

@app.get("/api/brief")
def get_brief():
    v, ts = db.kv_get("brief")
    return {"brief": v, "updated_at": ts, "today": bool(ts and ts[:10] == datetime.now().date().isoformat())}

@app.post("/api/brief")
def make_brief():
    """Daily executive digest for the founder, written by Gemini from the stats and the latest call gists."""
    s = stats.compute()
    calls = db.all_calls()[:40]
    gists = "\n".join(f"- [{stats.eff_verdict(c)}] {c['gist']}" for c in calls)
    slim = {"pipeline_lakh_mid": s["pipeline"]["mid_lakh"], "today": s["today"], **{k: s[k] for k in ("calls", "missed", "qualified", "median_answer_s", "within_5_min_pct", "containment_pct", "price_asked", "price_leaks",
                              "cost_total_inr", "cost_per_qualified_inr", "time_saved_hours", "verdicts")}}
    text, usage = gemini.generate(
        "You write a short daily digest for the founder of an interior design studio about how the AI phone desk did. Plain text only, no markdown, "
        "never mention any price per square foot. Use exactly this shape:\n"
        "Headline: one sentence stating total calls, the qualified pipeline value in rupees (lakhs), how many were declined, and how many urgent escalations.\nWins:\n- two or three short bullets\nLost leads:\n- one to three short bullets on who was declined or missed and why\n"
        "Actions:\n- two or three concrete things the team should do today, such as call back a named complaint or follow up a lead.",
        [{"role": "user", "text": f"Stats: {slim}\n\nRecent call gists:\n{gists}"}], None, 0.3)
    now = datetime.now().isoformat(timespec="seconds")
    db.kv_set("brief", text, now)
    return {"brief": text, "updated_at": now, "today": True}

@app.get("/api/audit")
def audit_all(limit: int = 100):
    return db.audit_list(None, min(limit, 300))

class SearchIn(BaseModel):
    q: str

@app.post("/api/search")
def search(b: SearchIn):
    """Natural-language search over every call. Gemini reads compact call summaries and returns the matching ones."""
    q = b.q.strip()
    if not q:
        return {"results": []}
    calls = db.all_calls()[:200]
    corpus = []
    for c in calls:
        lead = c.get("lead") or {}
        talk = " ".join(t["text"] for t in (c.get("transcript") or []) if t["who"] == "caller")[:500]
        corpus.append(f"[{c['id']}] outcome={stats.eff_verdict(c)}; {stats.prop_type(lead)} in {stats.area_of(lead)}; {c['gist']} CALLER SAID: {talk}")
    schema = {"type": "OBJECT", "properties": {"matches": {"type": "ARRAY", "items": {"type": "OBJECT", "properties": {
        "id": {"type": "STRING"}, "why": {"type": "STRING"}}, "required": ["id", "why"]}}}, "required": ["matches"]}
    out, _ = gemini.generate("You search a phone-call archive for an interior design studio. Return only calls that genuinely match the request, best first, "
                             "at most 10, each with a short reason. Use the exact ids given in square brackets. Return an empty list when nothing matches.",
                             [{"role": "user", "text": "REQUEST: " + q + "\n\nCALLS:\n" + "\n".join(corpus)}], schema, 0.1)
    by = {c["id"]: c for c in calls}
    res = []
    for m in out.get("matches", []):
        c = by.get(m["id"])
        if c:
            res.append({"id": c["id"], "name": (c.get("lead") or {}).get("name") or c["caller_label"], "verdict": stats.eff_verdict(c),
                        "when": c["started_at"], "why": m["why"], "gist": c["gist"]})
    return {"results": res}

@app.get("/api/system")
def system_health():
    """Live check of each connection with response times. Runs only when asked, so it does not wake the database on every page view."""
    import time, httpx
    out = []
    def timed(name, fn):
        t = time.time()
        try:
            ok, note = fn()
        except Exception as e:
            ok, note = False, str(e)[:80]
        out.append({"name": name, "ok": ok, "ms": round((time.time() - t) * 1000), "note": note})
    def get(url, **kw):
        r = httpx.get(url, timeout=15, **kw)
        return r.status_code == 200, "HTTP %s" % r.status_code
    key, ck = config.env("VAANI_API_KEY"), config.env("CAL_API_KEY")
    def vaani_ping():
        r = httpx.get("https://api.vaanivoice.ai/api/call_details/health-check", headers={"X-API-Key": key}, timeout=15)
        return r.status_code in (200, 404), "HTTP %s (404 means reachable, call not found)" % r.status_code
    timed("Vaani API", lambda: vaani_ping() if key else (False, "no key"))
    timed("Database", lambda: (db.kv_get("settings") is not None, "neon" if db.PG else "sqlite"))
    timed("Cal.com", lambda: get("https://api.cal.com/v2/me", headers={"Authorization": "Bearer " + ck}) if ck else (False, "no key"))
    timed("HubSpot", lambda: get("https://api.hubapi.com/crm/v3/objects/contacts", params={"limit": 1}, headers={"Authorization": "Bearer " + config.HUBSPOT_TOKEN}) if config.HUBSPOT_TOKEN else (False, "no token"))
    last, _ = db.kv_get("webhook_last")
    return {"checks": out, "webhook_last": last, "gemini_model": config.GEMINI_MODEL}

@app.get("/api/export.csv")
def export_csv():
    from app import exports
    return Response(exports.csv_bytes(), media_type="text/csv", headers={"Content-Disposition": "attachment; filename=aangan-calls.csv"})

@app.get("/api/export.pdf")
def export_pdf():
    from app import exports
    return Response(exports.pdf_bytes(), media_type="application/pdf", headers={"Content-Disposition": "attachment; filename=aangan-report.pdf"})

@app.get("/api/cron/weekly")
def cron_weekly(request: Request):
    """Sunday-night report. Vercel Cron calls this; set CRON_SECRET so strangers cannot. Emailing needs SMTP, otherwise the files are saved."""
    want = config.env("CRON_SECRET")
    if want and request.headers.get("authorization") != "Bearer " + want:
        raise HTTPException(401, "bad cron secret")
    from email.message import EmailMessage
    from app import exports, integrations as integ
    m = EmailMessage(); m["From"] = config.STUDIO_EMAIL; m["To"] = config.STUDIO_HEAD_EMAIL
    m["Subject"] = "Aangan Enquiry Desk: weekly report"
    m.set_content("This week's report is attached: a PDF summary and a CSV of every call.")
    m.add_attachment(exports.pdf_bytes(), maintype="application", subtype="pdf", filename="aangan-report.pdf")
    m.add_attachment(exports.csv_bytes(), maintype="text", subtype="csv", filename="aangan-calls.csv")
    r = integ._send(m, "weekly-report", datetime.now().strftime("%Y%m%d"), True)
    return {"ok": True, "delivery": r.get("status")}


# --- Twilio-style webhook stub (untested until a number exists) ---
@app.post("/api/twilio/voice")
def twilio_voice():
    cid, reply, _ = agent.start_call("Phone caller")
    return Response(_twiml(reply, cid), media_type="text/xml")

def _twiml(say, cid):
    say = say.replace("&", "and").replace("<", "")
    return (f'<?xml version="1.0" encoding="UTF-8"?><Response><Gather input="speech" action="/api/twilio/turn?cid={cid}" speechTimeout="auto" language="en-IN">'
            f"<Say>{say}</Say></Gather></Response>")

@app.post("/api/twilio/turn")
async def twilio_turn(cid: str, SpeechResult: str = ""):
    reply, status, _ = agent.turn(cid, SpeechResult or "(silence)")
    if status != "continue":
        agent.end_call(cid)
        return Response(f"<?xml version='1.0'?><Response><Say>{reply}</Say><Hangup/></Response>", media_type="text/xml")
    return Response(_twiml(reply, cid), media_type="text/xml")

@app.get("/")
def dashboard():
    return FileResponse(STATIC / "dashboard.html")

@app.get("/simulator")
def simulator():
    return FileResponse(STATIC / "simulator.html")

app.mount("/static", StaticFiles(directory=STATIC), name="static")
