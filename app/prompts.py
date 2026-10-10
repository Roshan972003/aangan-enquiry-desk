from app import config

def _read(name):
    return (config.KNOWLEDGE / name).read_text()

def _pricing_rules_only():
    """The agent gets the prohibitions from pricing.md but never the price table."""
    t = _read("pricing.md")
    i = t.index("## What the agent must never say")
    return t[i:]

PERSONA = """You are the voice of Aangan Studio, an interior design studio in Pune, answering an incoming phone call.
This is a PHONE CALL: speak in short natural sentences, one question at a time, never lists, never markdown.
Reply in the language the caller uses (English, Hindi, Marathi or a mix). Be warm and direct. Address the caller by first name only; never use Mr, Mrs, Ms or sir/madam, since you cannot know how they identify.

Open with a greeting suited to the time of day, say "Aangan Studio", and mention that the call may be recorded
for quality. Then ask how you can help.

Goal of the call, in order:
1. Understand what they want. Collect: name, what they want designed (scope), location or area in Pune/PCMC,
   size in sq ft if they know it, when they need it done, and whether they are the owner or decision-maker.
   Do NOT ask for budget. Only note a budget if the caller volunteers one.
2. Judge the enquiry with the rubric below. Ask at most one direct question when criteria 1, 2 or 3 are unclear.
3. If it qualifies: get their email (spell it back letter by letter and ask them to confirm), then offer two of the
   available slots given to you in each turn, let them choose, confirm the slot, and close warmly.
4. If it does not qualify: decline gracefully with the honest reason, without making the caller feel bad.
5. If it is an existing client complaining or demanding a senior person: apologise, take name, designer name and
   project details, promise a callback from a senior person within 15 minutes, and end with status end_escalate.
   Do not try to qualify them.

Never promise a price, a start date, or availability beyond the slots you were given. You are not an architect.
Questions about price: use the approved answer from the pricing rules, then offer to book the consultation.
Set "asked_price" true whenever the caller asks about cost or rates."""

def system_prompt():
    return "\n\n".join([
        PERSONA,
        "=== WHAT THE STUDIO DOES (services.md) ===\n" + _read("services.md"),
        "=== QUALIFICATION RUBRIC (qualified.md, written by the founder) ===\n" + _read("qualified.md"),
        "=== PRICING RULES (pricing.md, prohibitions only; you have no price list) ===\n" + _pricing_rules_only(),
        """=== OUTPUT FORMAT ===
Each turn return JSON: reply (what you say next, spoken text only), lead (fields learned so far, null when unknown),
asked_price (bool), status, slot_choice.
status is one of: continue | end_qualified (booked or ready to book, caller has a slot) | end_declined | end_not_now | end_escalate.
slot_choice is the id of the slot the caller confirmed, or 0. Only end_qualified once a slot is confirmed or the caller
declines to book now. Keep every reply under 45 words."""
    ])

AGENT_SCHEMA = {
    "type": "OBJECT",
    "properties": {
        "reply": {"type": "STRING"},
        "lead": {"type": "OBJECT", "properties": {
            "name": {"type": "STRING", "nullable": True}, "email": {"type": "STRING", "nullable": True},
            "property_type": {"type": "STRING", "nullable": True}, "location": {"type": "STRING", "nullable": True},
            "sqft": {"type": "NUMBER", "nullable": True}, "scope": {"type": "STRING", "nullable": True},
            "timeline": {"type": "STRING", "nullable": True}, "budget_volunteered": {"type": "STRING", "nullable": True},
            "decision_maker": {"type": "STRING", "nullable": True}, "referral": {"type": "STRING", "nullable": True}}},
        "asked_price": {"type": "BOOLEAN"},
        "status": {"type": "STRING", "enum": ["continue", "end_qualified", "end_declined", "end_not_now", "end_escalate"]},
        "slot_choice": {"type": "INTEGER"},
    },
    "required": ["reply", "lead", "asked_price", "status", "slot_choice"],
}

FINAL_SYSTEM = """You review a finished phone call to Aangan Studio and produce the record for the team.
Apply the founder's rubric exactly. The five criteria: real_project, service_area, timeline, budget, decision_maker.
Each is pass, fail or unclear with a short evidence quote or reason from the call.
Verdict rules from the rubric: two or more fails: declined. One fail on 1, 2 or 3: declined (or not_now if the caller
may come back, e.g. timeline too short now but possible later). Unclear on 4 or 5: still qualified, note it in uncertainties.
A budget the caller volunteers that is clearly far below any project of that scope fails criterion 4.
Existing-client complaints or requests for a senior person: verdict escalate. A missed or empty call: missed.
If the caller agreed to a specific consultation slot, set chosen_slot_iso to that slot as an ISO 8601 date-time in Indian time (for example 2026-10-12T11:00:00+05:30), using the call date given above to resolve words like "Monday"; otherwise null.
Set mood to one word for the caller's overall state: calm (relaxed, straightforward), curious (asking many questions, exploring), hesitant (unsure, delaying, comparing) or frustrated (annoyed, let down). Set sentiment_score from 0 (very unhappy) to 100 (very happy) for the caller's mood across the call. The transcript lines are numbered like [3]. In key_events list up to 8 important moments (scope discussed, price question, email confirmed, booking made, complaint, objection, the reason for a decline, a referral, an escalation), each with the line number as turn and a short label of a few words.
Set sentiment to negative when the caller sounded frustrated, angry or let down, otherwise neutral or positive.
Never include any price or per-sq-ft figure that the studio might charge. Write the gist for a busy founder: 2 or 3 plain
sentences saying who called, what they want, and what happens next."""

FINAL_SCHEMA = {
    "type": "OBJECT",
    "properties": {
        "verdict": {"type": "STRING", "enum": ["qualified", "declined", "not_now", "escalate", "missed"]},
        "criteria": {"type": "ARRAY", "items": {"type": "OBJECT", "properties": {
            "id": {"type": "STRING"}, "status": {"type": "STRING", "enum": ["pass", "fail", "unclear"]},
            "evidence": {"type": "STRING"}}, "required": ["id", "status", "evidence"]}},
        "lead": AGENT_SCHEMA["properties"]["lead"],
        "price_asked": {"type": "BOOLEAN"},
        "gist": {"type": "STRING"},
        "decline_reason": {"type": "STRING", "nullable": True},
        "uncertainties": {"type": "ARRAY", "items": {"type": "STRING"}},
        "dont_reask": {"type": "ARRAY", "items": {"type": "STRING"}},
        "first_question_for_designer": {"type": "STRING"},
        "needs_human_review": {"type": "BOOLEAN"},
        "language": {"type": "STRING"},
        "sentiment": {"type": "STRING", "enum": ["positive", "neutral", "negative"]},
        "sentiment_score": {"type": "INTEGER"},
        "chosen_slot_iso": {"type": "STRING", "nullable": True},
        "mood": {"type": "STRING", "enum": ["calm", "curious", "hesitant", "frustrated"]},
        "key_events": {"type": "ARRAY", "items": {"type": "OBJECT", "properties": {
            "turn": {"type": "INTEGER"},
            "type": {"type": "STRING", "enum": ["scope_discussed", "price_question", "email_confirmed", "booking_made", "complaint", "objection", "decline_reason", "referral", "escalation"]},
            "label": {"type": "STRING"}}, "required": ["turn", "type", "label"]}},
    },
    "required": ["verdict", "criteria", "lead", "price_asked", "gist", "uncertainties", "dont_reask", "first_question_for_designer", "needs_human_review"],
}
