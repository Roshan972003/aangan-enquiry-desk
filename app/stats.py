import statistics
from collections import Counter, defaultdict
from datetime import datetime
from app import config, db

import time
_CACHE = {"t": 0, "v": None}

def invalidate():
    _CACHE["t"] = 0

def compute():
    """Cached for 60 s so dashboard views do not wake the database each time."""
    if _CACHE["v"] is not None and time.time() - _CACHE["t"] < 60:
        return _CACHE["v"]
    v = _compute()
    _CACHE["v"], _CACHE["t"] = v, time.time()
    return v

def _compute():
    calls = db.all_calls()
    answered = [c for c in calls if not c["missed"]]
    by_v = Counter(c["verdict"] for c in calls)
    q = by_v["qualified"]
    resp = [c["ring_to_answer_s"] for c in calls if c.get("ring_to_answer_s") is not None]
    cost = sum(c["cost_inr"] or 0 for c in calls)
    comp = {"gemini": 0.0, "telephony": 0.0, "speech": 0.0}
    for c in calls:
        for k in comp:
            comp[k] += (c.get("cost") or {}).get(k, 0)
    days, hours = defaultdict(lambda: {"calls": 0, "qualified": 0}), [0] * 24
    for c in calls:
        d = datetime.fromisoformat(c["started_at"])
        days[d.date().isoformat()]["calls"] += 1
        days[d.date().isoformat()]["qualified"] += c["verdict"] == "qualified"
        hours[d.hour] += 1
    invites = sum(1 for c in calls if (c["integrations"] or {}).get("client_invite", {}).get("mode") in ("live", "outbox"))
    synced = sum(1 for c in calls if (c["integrations"] or {}).get("hubspot", {}).get("mode") in ("live", "outbox"))
    n = len(calls)
    return {
        "calls": n, "answered": len(answered), "missed": by_v["missed"],
        "median_answer_s": statistics.median(resp) if resp else None,
        "within_5_min_pct": round(100 * sum(1 for r in resp if r <= 300) / len(resp)) if resp else None,
        "verdicts": dict(by_v), "qualified": q, "invites": invites, "hubspot_synced": synced,
        "after_hours": sum(1 for c in calls if not c["in_hours"]),
        "price_asked": sum(1 for c in calls if (c["result"] or {}).get("price_asked")),
        "pipeline_lakh": q * config.AVG_PROJECT_LAKH,
        "cost_total_inr": round(cost, 2), "cost_per_call_inr": round(cost / n, 2) if n else 0,
        "cost_per_qualified_inr": round(cost / q, 2) if q else None, "cost_components": {k: round(v, 2) for k, v in comp.items()},
        "days": [{"date": k, **v} for k, v in sorted(days.items())], "hours": hours,
        "recent": [{"id": c["id"], "started_at": c["started_at"], "caller": c["caller_label"], "verdict": c["verdict"], "gist": c["gist"],
                    "answer_s": c["ring_to_answer_s"], "cost_inr": c["cost_inr"], "in_hours": c["in_hours"], "source": c["source"],
                    "hubspot": (c["integrations"] or {}).get("hubspot", {}).get("mode"),
                    "designer_email": (c["integrations"] or {}).get("designer_email", {}).get("mode"),
                    "invite": (c["integrations"] or {}).get("client_invite", {}).get("mode")} for c in calls[:25]],
    }
