from datetime import datetime
from typing import Optional
from pathlib import Path
from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse, JSONResponse, Response
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel
from app import agent, db, stats, config, gemini

app = FastAPI(title="Aangan Enquiry Desk")
STATIC = Path(__file__).parent / "static"

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
            "hubspot": bool(config.HUBSPOT_TOKEN), "smtp": bool(config.SMTP_HOST), "vaani_key_set": bool(config.env("VAANI_API_KEY"))}

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
    return c

@app.get("/api/stats")
def get_stats():
    return stats.compute()

@app.get("/api/brief")
def get_brief():
    v, ts = db.kv_get("brief")
    return {"brief": v, "updated_at": ts}

@app.post("/api/brief")
def make_brief():
    s = stats.compute()
    gists = "\n".join(f"- [{c['verdict']}] {c['gist']}" for c in db.all_calls()[:40])
    text, usage = gemini.generate(
        "You write a short brief for the founder of an interior design studio about how the AI phone desk did. Plain sentences, no markdown, "
        "no price figures. Start with one lead sentence, then 3 to 4 short bullet lines starting with '- '. Cover: volume and speed, who qualified, why others were declined, cost to run.",
        [{"role": "user", "text": f"Stats: {s}\n\nCall gists:\n{gists}"}], None, 0.3)
    now = datetime.now().isoformat(timespec="seconds")
    db.kv_set("brief", text, now)
    return {"brief": text, "updated_at": now}

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
