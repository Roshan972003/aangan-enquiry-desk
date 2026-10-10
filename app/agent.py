import uuid, time
from datetime import datetime
from app import config, db, gemini, guard, prompts, slots as slotmod, integrations as integ

def _save(sess):
    d = dict(sess); d["started_at"] = sess["started_at"].isoformat()
    db.session_save(sess["id"], d, config.now_ist().isoformat(timespec="seconds"))

def load(cid):
    d = db.session_load(cid)
    if d:
        d["started_at"] = datetime.fromisoformat(d["started_at"])
    return d

def exists(cid):
    return db.session_load(cid) is not None
_SYSTEM = None

def system():
    global _SYSTEM
    if _SYSTEM is None:
        _SYSTEM = prompts.system_prompt()
    return _SYSTEM

def in_hours(dt: datetime) -> bool:
    return config.OPEN_HOUR <= dt.hour < config.CLOSE_HOUR

def _turn_system(sess):
    lines = "\n".join(f"  slot {s['id']}: {s['label']}" for s in sess["slots"][:4])
    return (system() + f"\n\n=== THIS CALL ===\nCall time (IST): {sess['started_at']:%A %d %B %Y, %I:%M %p}.\n"
            f"Lead so far: {sess['lead']}\nAvailable consultation slots (offer two at a time):\n{lines}")

def _agent_turn(sess):
    _t = time.time()
    out, usage = gemini.generate(_turn_system(sess), sess["history"], prompts.AGENT_SCHEMA, temperature=0.4)
    sess["llm_ms"] = round((time.time() - _t) * 1000)
    for k in ("in", "out"):
        sess["usage"][k] += usage[k]
    caller_text = " ".join(h["text"] for h in sess["history"] if h["role"] == "user")
    reply, blocked = guard.clean(out["reply"], caller_text)
    if blocked:
        sess["guard_blocks"] += 1
    out["reply"] = reply
    for k, v in (out.get("lead") or {}).items():
        if v not in (None, ""):
            sess["lead"][k] = v
    sess["price_asked"] = sess["price_asked"] or bool(out.get("asked_price"))
    sess["history"].append({"role": "model", "text": reply})
    sess["transcript"].append({"who": "agent", "text": reply, "t": round(time.time() - sess["t0"], 1)})
    if out.get("slot_choice"):
        sess["slot_choice"] = out["slot_choice"]
    sess["status"] = out["status"]
    return out

def start_call(caller_label="Simulated caller", started_at=None, phone=None):
    t0 = time.time()
    when = started_at or config.now_ist()
    cid = "C" + uuid.uuid4().hex[:8].upper()
    sess = {"id": cid, "started_at": when, "t0": t0, "history": [], "transcript": [], "lead": {}, "usage": {"in": 0, "out": 0},
            "guard_blocks": 0, "price_asked": False, "slot_choice": 0, "status": "continue", "caller_label": caller_label,
            "phone": phone, "slots": slotmod.available_slots(when, 6)}
    sess["history"].append({"role": "user", "text": "(The caller has just been connected. Greet them.)"})
    out = _agent_turn(sess)
    sess["history"].pop(0)           # hidden kickoff turn, keep the greeting as the first model turn
    sess["ring_to_answer_s"] = round(time.time() - t0 + 1.5, 1)   # ~1.5 s of ringing before pickup
    _save(sess)
    return cid, out["reply"], sess["slots"]

def turn(cid, text):
    sess = load(cid)
    sess["history"].append({"role": "user", "text": text})
    sess["transcript"].append({"who": "caller", "text": text, "t": round(time.time() - sess["t0"], 1)})
    out = _agent_turn(sess)
    _save(sess)
    return out["reply"], sess["status"], sess["lead"]

def finalize(transcript, started_at, duration_s, source, caller_label, call_id=None, phone=None, slot_choice=0, slots=None,
             usage=None, ring_to_answer_s=3.0, live_integrations=True, price_hint=None, missed=False, find_booking=None, external_id=None):
    """Post-call pipeline: judge the call, write the gist, sync HubSpot, send email and invite."""
    cid = call_id or "C" + uuid.uuid4().hex[:8].upper()
    usage = dict(usage or {"in": 0, "out": 0})
    text = "\n".join(f"{t['who'].upper()}: {t['text']}" for t in transcript)
    if missed or not transcript:
        result = {"verdict": "missed", "criteria": [], "lead": {}, "price_asked": False, "gist": "Missed call with no voicemail. An AI callback is queued.",
                  "uncertainties": [], "dont_reask": [], "first_question_for_designer": "", "needs_human_review": False}
    else:
        numbered = "\n".join(f"[{i}] {t['who'].upper()}: {t['text']}" for i, t in enumerate(transcript))
        result, u = gemini.generate(prompts.FINAL_SYSTEM, [{"role": "user", "text": f"Call time: {started_at:%A %d %B %Y %I:%M %p}\n\nTRANSCRIPT\n{numbered}"}],
                                    prompts.FINAL_SCHEMA, temperature=0.1)
        usage["in"] += u["in"]; usage["out"] += u["out"]
    result["key_events"] = attach_event_times(result.get("key_events"), transcript, duration_s)
    if result.get("sentiment_score") is None:
        result["sentiment_score"] = {"negative": 25, "neutral": 60, "positive": 85}.get(result.get("sentiment"), None)
    lead = result.get("lead") or {}
    slot = None
    if slots and slot_choice:
        slot = next((s for s in slots if s["id"] == slot_choice), None)
    booked_after_call = False
    if result["verdict"] == "qualified" and not slot and lead.get("email") and live_integrations and config.LIVE_INTEGRATIONS:
        try:
            slot = slotmod.match_slot(result["chosen_slot_iso"]) or slotmod.nearest_slot(result["chosen_slot_iso"])
            booked_after_call = bool(slot)
        except Exception:
            slot = None
    # a qualified lead gets a designer email even without a booked slot (designer calls the client)
    verdict = result["verdict"]
    ints = {}
    chosen = None
    if verdict == "qualified":
        from app import stats as _st
        _lead = result.get("lead") or {}
        chosen = _st.least_loaded(db.all_calls(), _st.area_of(_lead), _st.prop_type(_lead) == "Commercial")
        if slot:
            slot = dict(slot); slot.update({"designer": chosen["name"], "designer_email": chosen["email"]})
    if verdict in ("qualified",):
        ints["designer_email"] = integ.designer_email(cid, lead, result, slot, text, live_integrations, phone)
        booking = find_booking(lead) if find_booking else None
        if booking:
            slot = booking["slot"]
            ints["client_invite"] = booking["invite"]
        else:
            ints["client_invite"] = integ.client_invite(cid, lead, slot, live_integrations, result)
            if booked_after_call:
                ints["client_invite"]["status"] = "booked right after the call. " + str(ints["client_invite"].get("status"))
    elif verdict == "escalate":
        ints["escalation_email"] = integ.escalation_email(cid, lead, result, live_integrations)
    elif verdict in ("declined", "not_now"):
        ints["decline_email"] = integ.decline_email(cid, lead, live_integrations)
    ints["hubspot"] = integ.hubspot_sync(cid, lead, result, {"phone": phone, "slot": slot["label"] if slot else None, "designer": (chosen or {}).get("name"), "transcript": transcript}, live_integrations)
    mins = duration_s / 60.0
    cost = {"gemini": round(gemini.cost_inr(usage), 3), "telephony": round(mins * config.TELEPHONY_PER_MIN, 3),
            "speech": round(mins * config.SPEECH_PER_MIN, 3), "tokens_in": usage["in"], "tokens_out": usage["out"]}
    total = round(cost["gemini"] + cost["telephony"] + cost["speech"], 3)
    rec = {"id": cid, "started_at": started_at.isoformat(), "source": source, "caller_label": caller_label,
           "ring_to_answer_s": ring_to_answer_s, "in_hours": int(in_hours(started_at)), "duration_s": duration_s, "missed": int(verdict == "missed"),
           "transcript": transcript, "lead": lead, "result": result, "verdict": verdict, "gist": result["gist"],
           "cost": cost, "cost_inr": total, "integrations": ints, "slot": slot, "external_id": external_id}
    if chosen:
        rec["ops"] = {"assigned_to": chosen["name"], "auto_assigned": True}
    db.save_call(rec)
    return rec

def end_call(cid):
    sess = load(cid)
    db.session_delete(cid)
    return finalize(sess["transcript"], sess["started_at"], max(5.0, time.time() - sess["t0"]), "live", sess["caller_label"], call_id=cid,
                    phone=sess.get("phone"), slot_choice=sess["slot_choice"], slots=sess["slots"], usage=sess["usage"],
                    ring_to_answer_s=sess.get("ring_to_answer_s", 3.0))


def attach_event_times(events, transcript, duration_s):
    """Give each key event a time in seconds: the real timestamp when the transcript has one, otherwise its position in the call."""
    out = []
    n = len(transcript)
    for e in events or []:
        i = e.get("turn")
        if not isinstance(i, int) or not (0 <= i < n):
            continue
        t = transcript[i].get("t")
        if t is None:
            t = round((i / max(1, n - 1)) * duration_s, 1)
        out.append({"turn": i, "type": e.get("type"), "label": e.get("label"), "t": t})
    return out
