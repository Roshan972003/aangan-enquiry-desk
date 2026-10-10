"""Add sentiment scores and key-event markers to calls recorded before those fields existed.
   .venv/bin/python backfill.py   (uses DATABASE_URL from .env, so it updates the live database when that is set)"""
from app import db, gemini
from app.agent import attach_event_times

SCHEMA = {"type": "OBJECT", "properties": {
    "sentiment": {"type": "STRING", "enum": ["positive", "neutral", "negative"]}, "sentiment_score": {"type": "INTEGER"},
    "key_events": {"type": "ARRAY", "items": {"type": "OBJECT", "properties": {"turn": {"type": "INTEGER"},
        "type": {"type": "STRING", "enum": ["price_question", "email_confirmed", "booking_made", "complaint", "objection", "decline_reason", "referral", "escalation"]},
        "label": {"type": "STRING"}}, "required": ["turn", "type", "label"]}}},
    "required": ["sentiment", "sentiment_score", "key_events"]}
SYSTEM = ("You review a phone call to an interior design studio. Rate the caller's mood across the call from 0 (very unhappy) to 100 (very happy) and label it. "
          "Lines are numbered like [3]. List up to 8 key moments (price question, email confirmed, booking made, complaint, objection, the reason for a decline, a referral, an escalation) "
          "with the line number as turn and a short label of a few words.")

done = 0
for c in db.all_calls():
    r = c.get("result") or {}
    tr = c.get("transcript") or []
    if r.get("sentiment_score") is not None or not tr:
        continue
    numbered = "\n".join(f"[{i}] {t['who'].upper()}: {t['text']}" for i, t in enumerate(tr))
    out, _ = gemini.generate(SYSTEM, [{"role": "user", "text": numbered}], SCHEMA, 0.1)
    r["sentiment"], r["sentiment_score"] = out["sentiment"], out["sentiment_score"]
    r["key_events"] = attach_event_times(out.get("key_events"), tr, c["duration_s"] or 120)
    db.update_result(c["id"], r)
    done += 1
    print(c["id"], r["sentiment"], r["sentiment_score"], len(r["key_events"]), "events")
print("updated", done, "calls")
