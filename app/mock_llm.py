"""Plumbing-only stand-in for Gemini. Not a model: canned lines and keyword rules."""
import re

OUT_OF_AREA = ["nashik", "mumbai", "talegaon", "lonavala", "nagpur", "bangalore", "delhi"]
OUT_OF_SCOPE = ["restaurant", "hotel", "gym", "retail", "cafe", "shop"]

def _caller_text(history):
    return " ".join(h["text"] for h in history if h["role"] == "user").lower()

def respond(system, history, schema):
    if schema is None:
        return "Headline: MOCK digest for plumbing tests.\nWins:\n- Calls answered quickly.\nLost leads:\n- Out-of-area enquiries declined.\nActions:\n- Call back the escalation."
    props = (schema or {}).get("properties", {})
    if "matches" in props:
        return {"matches": []}
    if "verdict" in props:
        return _final(history)
    users = [h for h in history if h["role"] == "user"]
    n = len(users)
    txt = _caller_text(history)
    lead = {k: None for k in props["lead"]["properties"]}
    m = re.search(r"\b(\d[\d,]{2,5})\s*(?:sq|square)", txt)
    if m: lead["sqft"] = float(m.group(1).replace(",", ""))
    asked = bool(re.search(r"how much|cost|price|rate|per sq", txt))
    status, slot, reply = "continue", 0, "Thanks. What are you looking to design, and where is the property?"
    if any(w in txt for w in OUT_OF_AREA + OUT_OF_SCOPE):
        status, reply = "end_declined", "Thank you for calling. This sounds like it may not be the right fit for us right now, but feel free to reach out if things change."
    elif n == 1:
        reply = "Good to hear. Could you tell me the area and the size of the home?"
    elif n == 2:
        reply = "And when would you like the project complete?"
    elif n == 3:
        reply = "Are you the owner, or deciding together with someone?"
    elif n == 4:
        reply = "Great. Could you share your email so I can send the invite?"
    elif n == 5:
        reply = "Thank you. I can offer slot 1 or slot 2. Which would you like?"
    else:
        status, slot, reply = "end_qualified", 1, "Booked. You will get an invite shortly. Thank you for calling Aangan Studio."
    if asked and status == "continue":
        reply = "Pricing depends on the site, the materials and the scope. Your designer will walk you through it at the consultation. " + reply
    return {"reply": reply, "lead": lead, "asked_price": asked, "status": status, "slot_choice": slot}

def _final(history):
    txt = " ".join(h["text"] for h in history).lower()
    crit = lambda i, s, e: {"id": i, "status": s, "evidence": e}
    area_fail = any(w in txt for w in OUT_OF_AREA)
    scope_fail = any(w in txt for w in OUT_OF_SCOPE) or "just looking for ideas" in txt or "suggestions" in txt
    complaint = "not acceptable" in txt or "hasn't replied" in txt
    v = "escalate" if complaint else "declined" if (area_fail or scope_fail) else "qualified"
    return {
        "verdict": v,
        "criteria": [crit("real_project", "fail" if scope_fail else "pass", "keyword rule"),
                     crit("service_area", "fail" if area_fail else "pass", "keyword rule"),
                     crit("timeline", "unclear", "mock"), crit("budget", "unclear", "mock"), crit("decision_maker", "unclear", "mock")],
        "lead": {k: None for k in ["name","email","property_type","location","sqft","scope","timeline","budget_volunteered","decision_maker","referral"]},
        "price_asked": bool(re.search(r"how much|cost|price|rate", txt)),
        "gist": f"MOCK gist: call judged {v} by keyword rules. Use Gemini for real summaries.",
        "decline_reason": "mock rule" if v == "declined" else None,
        "uncertainties": [], "dont_reask": ["scope", "area"], "first_question_for_designer": "Confirm scope and timeline.",
        "needs_human_review": False, "language": "English",
    }
