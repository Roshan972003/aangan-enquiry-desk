import re, statistics, time
from collections import Counter, defaultdict
from datetime import datetime, timedelta, date
import calendar
from app import config, db, guard

_CACHE = {"t": 0, "v": None}
STAGES = [("qualified", "Qualified"), ("consultation_booked", "Consultation booked"),
          ("site_visit", "Site visit"), ("closed_won", "Closed / won")]
_LEGACY = {"consultation_scheduled": "consultation_booked", "site_visit_scheduled": "consultation_booked", "proposal_sent": "site_visit", "consultation_done": "site_visit"}
AREAS = ["Kothrud", "Baner", "Aundh", "Wakad", "Koregaon Park", "Kalyani Nagar", "Viman Nagar", "Hadapsar", "Magarpatta", "NIBM", "Kondhwa",
         "Undri", "Shivane", "Warje", "Erandwane", "Deccan", "Pimpri", "Chinchwad", "Pimple Saudagar", "Pimple Nilakh", "Ravet", "Hinjewadi",
         "Pashan", "Kharadi", "Nanded"]

def invalidate():
    _CACHE["t"] = 0

def settings():
    v, _ = db.kv_get("settings")
    out = dict(config.DEFAULT_SETTINGS)
    out.update(v or {})
    return out

def eff_verdict(c):
    return ((c.get("ops") or {}).get("override") or {}).get("verdict") or c["verdict"]

def stage_of(c):
    ops = c.get("ops") or {}
    st = _LEGACY.get(ops.get("stage"), ops.get("stage"))
    return st or ("consultation_booked" if c.get("slot") else "qualified")

def prop_type(lead):
    t = f"{lead.get('property_type') or ''} {lead.get('scope') or ''}".lower()
    m = re.search(r"(\d)\s*-?\s*bhk", t)
    if m:
        return f"{m.group(1)}BHK"
    if re.search(r"villa|bungalow|independent", t):
        return "Villa / house"
    if re.search(r"office|commercial|clinic|studio|workspace|fit-?out|startup", t):
        return "Commercial"
    return "Other / unclear"

def area_of(lead):
    loc = str(lead.get("location") or "")
    for a in AREAS:
        if a.lower() in loc.lower():
            return a
    return loc.split(",")[0].strip().title() if loc.strip() else "Not given"

def win_chance(c):
    """Rules-based estimate of the chance a qualified lead becomes a project. Transparent factors, not a trained model."""
    lead, r, ops = c.get("lead") or {}, c.get("result") or {}, c.get("ops") or {}
    score, why = 25, ["base 25%"]
    def add(pts, text):
        nonlocal score
        score += pts; why.append(("+" if pts >= 0 else "") + str(pts) + "% " + text)
    if c.get("slot"): add(15, "consultation booked")
    dm = str(lead.get("decision_maker") or "").lower()
    if dm and any(w in dm for w in ("owner", "myself", "husband", "wife", "decision", "founder", "yes")): add(10, "decision-maker on the call")
    if lead.get("referral"): add(10, "came by referral")
    t = str(lead.get("timeline") or "").lower()
    if t and not re.search(r"no rush|flexible|someday|not sure", t): add(8, "has a clear timeline")
    elif re.search(r"no rush|flexible|someday", t): add(-5, "no urgency")
    sq = lead.get("sqft") or 0
    try:
        sq = float(sq)
    except (TypeError, ValueError):
        sq = 0
    if sq >= 1000: add(7, "larger home or office")
    if lead.get("budget_volunteered"): add(8, "volunteered a budget")
    sc = r.get("sentiment_score")
    if sc is not None:
        if sc >= 70: add(8, "positive mood")
        elif sc < 40: add(-12, "unhappy mood")
    if r.get("mood") == "hesitant": add(-8, "hesitant")
    if r.get("uncertainties"): add(-4, "open questions")
    stage = stage_of(c)
    if stage == "site_visit": add(12, "site visit stage")
    if stage == "closed_won": return 100, ["closed won"]
    return max(5, min(95, score)), why

def workload(calls):
    """Active leads per designer (qualified, assigned, not yet closed) with a light / balanced / heavy badge relative to the team average."""
    active = Counter()
    unassigned = 0
    for c in calls:
        if eff_verdict(c) != "qualified" or stage_of(c) == "closed_won":
            continue
        who = (c.get("ops") or {}).get("assigned_to")
        if who:
            active[who] += 1
        else:
            unassigned += 1
    names = [d["name"] for d in config.DESIGNERS]
    titles = {d["name"]: d.get("title", "") for d in config.DESIGNERS}
    avg = sum(active[n] for n in names) / max(1, len(names))
    out = []
    for n in names:
        a = active[n]
        level = "light" if a == 0 or a < avg * 0.7 else "heavy" if (a >= 3 and a > avg * 1.3) else "balanced"
        out.append({"name": n, "title": titles.get(n, ""), "active": a, "level": level})
    return {"designers": out, "unassigned": unassigned, "average": round(avg, 1)}

def least_loaded(calls, area=None, commercial=False):
    """Pick a designer: prefer those who cover the lead's area (or commercial work), then the one with the fewest active leads."""
    wl = {d["name"]: d["active"] for d in workload(calls)["designers"]}
    pool = config.DESIGNERS
    if area:
        match = [d for d in pool if any(area.lower() == a.lower() for a in d.get("areas", []))]
        pool = match or pool
    if commercial:
        match = [d for d in pool if d.get("commercial")]
        pool = match or pool
    return min(pool, key=lambda d: wl.get(d["name"], 0))

def compute():
    """Cached for 60 s so dashboard views do not wake the database each time."""
    if _CACHE["v"] is not None and time.time() - _CACHE["t"] < 60:
        return _CACHE["v"]
    v = _compute()
    _CACHE["v"], _CACHE["t"] = v, time.time()
    return v

def _price_leaks(c):
    """Agent lines in the transcript that state a price. Echoing the caller's own number is allowed."""
    tr = c.get("transcript") or []
    caller = " ".join(t["text"] for t in tr if t["who"] == "caller")
    return sum(1 for t in tr if t["who"] == "agent" and guard.violates(t["text"], caller))

def _compute():
    st = settings()
    calls = db.all_calls()
    n = len(calls)
    ev = {c["id"]: eff_verdict(c) for c in calls}
    by_v = Counter(ev.values())
    q_calls = [c for c in calls if ev[c["id"]] == "qualified"]
    q = len(q_calls)
    resp = [c["ring_to_answer_s"] for c in calls if c.get("ring_to_answer_s") is not None]
    cost = sum(c["cost_inr"] or 0 for c in calls)
    comp = {"gemini": 0.0, "telephony": 0.0, "speech": 0.0}
    tok_in = tok_out = 0
    tok_day = defaultdict(int)
    for c in calls:
        cc = c.get("cost") or {}
        for k in comp:
            comp[k] += cc.get(k, 0)
        tok_in += cc.get("tokens_in", 0); tok_out += cc.get("tokens_out", 0)
        tok_day[c["started_at"][:10]] += cc.get("tokens_in", 0) + cc.get("tokens_out", 0)
    minutes = sum((c["duration_s"] or 0) for c in calls) / 60.0
    days, hours = defaultdict(lambda: {"calls": 0, "qualified": 0, "cost": 0.0, "within5": 0}), [0] * 24
    for c in calls:
        d = datetime.fromisoformat(c["started_at"])
        days[d.date().isoformat()]["calls"] += 1
        days[d.date().isoformat()]["qualified"] += ev[c["id"]] == "qualified"
        days[d.date().isoformat()]["cost"] += c["cost_inr"] or 0
        days[d.date().isoformat()]["within5"] += (c.get("ring_to_answer_s") or 0) <= 300
        hours[d.hour] += 1
    answered = [c for c in calls if ev[c["id"]] != "missed"]
    contained = [c for c in calls if ev[c["id"]] not in ("escalate", "missed")]
    asked = [c for c in calls if (c.get("result") or {}).get("price_asked")]
    leaks = sum(_price_leaks(c) for c in calls)
    # lead quality
    types, areas = Counter(), Counter()
    for c in calls:
        if ev[c["id"]] in ("qualified", "declined", "not_now") and (c.get("lead") or {}):
            types[prop_type(c["lead"])] += 1
            areas[area_of(c["lead"])] += 1
    # money
    lo, hi = st["project_low_lakh"], st["project_high_lakh"]
    mid = (lo + hi) / 2
    pipe_lakh = q * mid
    pipe_inr = pipe_lakh * 1e5
    expected_inr = pipe_inr * st["close_rate_pct"] / 100.0
    saved_min = minutes + n * st["minutes_saved_per_call"]
    def lead_card(c):
        lead = c.get("lead") or {}
        ops = c.get("ops") or {}
        wc, why = win_chance(c)
        ended = (datetime.fromisoformat(c["started_at"]) + timedelta(seconds=c["duration_s"] or 0)).isoformat()
        return {"win": wc, "win_why": why, "ended_at": ended, "ended_ts": int((datetime.fromisoformat(ended) - timedelta(hours=5, minutes=30)).replace(tzinfo=__import__("datetime").timezone.utc).timestamp()), "first_touch": ops.get("first_touch"), "id": c["id"], "name": lead.get("name") or "Caller", "type": prop_type(lead), "area": area_of(lead), "sqft": lead.get("sqft"),
                "timeline": lead.get("timeline"), "stage": stage_of(c), "emailed": bool(ops.get("emailed")), "assigned_to": ops.get("assigned_to"), "gist": c["gist"],
                "slot": (c.get("slot") or {}).get("label"), "started_at": c["started_at"], "overridden": bool(ops.get("override"))}
    escal = [{"id": c["id"], "name": (c.get("lead") or {}).get("name") or c["caller_label"], "phone": c["caller_label"], "gist": c["gist"],
              "started_at": c["started_at"], "callback": ((c.get("ops") or {}).get("callback") or "pending")}
             for c in calls if ev[c["id"]] == "escalate"]
    # red flags: anything a human should look at now
    now = config.now_ist(); flags = []
    for c in calls:
        e = ev[c["id"]]; ops = c.get("ops") or {}; when = datetime.fromisoformat(c["started_at"])
        who = (c.get("lead") or {}).get("name") or c["caller_label"]
        done = ops.get("callback") == "called_back"
        if e == "escalate":
            flags.append({"id": c["id"], "kind": "Escalation", "name": who, "phone": c["caller_label"], "gist": c["gist"], "at": c["started_at"], "done": done})
        elif (c.get("result") or {}).get("sentiment") == "negative" and e != "escalate":
            flags.append({"id": c["id"], "kind": "Negative sentiment", "name": who, "phone": c["caller_label"], "gist": c["gist"], "at": c["started_at"], "done": done})
        elif e == "missed":
            flags.append({"id": c["id"], "kind": "Missed call", "name": who, "phone": c["caller_label"], "gist": "No voicemail. Call them back.", "at": c["started_at"], "done": done})
        elif e == "qualified" and not ops.get("assigned_to") and not ops.get("stage") and now - when > timedelta(hours=24):
            flags.append({"id": c["id"], "kind": "Follow-up overdue", "name": who, "phone": c["caller_label"], "gist": "Qualified more than a day ago and still unassigned.", "at": c["started_at"], "done": done})
    flags.sort(key=lambda f: f["at"], reverse=True)
    flags.sort(key=lambda f: f["done"])
    # forecast: simple run-rate, not machine learning
    last30 = [c for c in calls if now - datetime.fromisoformat(c["started_at"]) <= timedelta(days=30)]
    q30 = sum(1 for c in last30 if ev[c["id"]] == "qualified")
    span_days = max(1, min(30, (now - min((datetime.fromisoformat(c["started_at"]) for c in last30), default=now)).days + 1))
    per_day = q30 / span_days
    dim = calendar.monthrange(now.year, now.month)[1]
    left = dim - now.day
    month_q = sum(1 for c in calls if ev[c["id"]] == "qualified" and datetime.fromisoformat(c["started_at"]).month == now.month and datetime.fromisoformat(c["started_at"]).year == now.year)
    proj_q = month_q + per_day * left
    won_now = len([c for c in q_calls if stage_of(c) == "closed_won"])
    forecast = {"leads_per_day": round(per_day, 2), "days_left": left, "projected_qualified_month": round(proj_q, 1),
                "projected_pipeline_lakh": round(proj_q * mid, 1), "projected_revenue_lakh": round(proj_q * mid * st["close_rate_pct"] / 100.0, 1),
                "won_to_date": won_now, "basis": f"{q30} qualified leads in the last {span_days} days, at a {st['close_rate_pct']}% assumed close rate"}
    wsum = sum(win_chance(c)[0] for c in q_calls if stage_of(c) != "closed_won") / 100.0
    forecast["weighted_open_leads"] = round(wsum, 1)
    forecast["weighted_revenue_lakh"] = round(wsum * mid, 1)
    forecast["win_basis"] = "sum of each open lead's win chance x average project value"
    moods = Counter((c.get("result") or {}).get("mood") for c in calls if (c.get("result") or {}).get("mood"))
    today_s = now.date().isoformat()
    td = [c for c in calls if c["started_at"][:10] == today_s]
    today = {"calls": len(td), "qualified": sum(1 for c in td if ev[c["id"]] == "qualified"), "declined": sum(1 for c in td if ev[c["id"]] in ("declined", "not_now")),
             "escalations": sum(1 for c in td if ev[c["id"]] == "escalate")}
    # revenue saved on off-hours calls: qualified leads that arrived outside the desk's hours, which would otherwise have waited or been lost
    loss = st.get("offhour_loss_pct", 48) / 100.0
    per_lead_inr = mid * 1e5 * st["close_rate_pct"] / 100.0 * loss
    off_q = [c for c in calls if not c["in_hours"] and ev[c["id"]] == "qualified"]
    offhour = {"calls": sum(1 for c in calls if not c["in_hours"]), "qualified": len(off_q), "saved_inr": round(len(off_q) * per_lead_inr),
               "per_lead_inr": round(per_lead_inr), "loss_pct": st.get("offhour_loss_pct", 48),
               "recent": [{"id": c["id"], "name": (c.get("lead") or {}).get("name") or c["caller_label"], "at": c["started_at"]} for c in off_q[:5]]}
    invites = sum(1 for c in calls if (c["integrations"] or {}).get("client_invite", {}).get("mode") in ("live", "outbox"))
    return {
        "calls": n, "answered": len(answered), "missed": by_v["missed"],
        "median_answer_s": statistics.median(resp) if resp else None, "max_answer_s": max(resp) if resp else None,
        "within_5_min_pct": round(100 * sum(1 for r in resp if r <= 300) / len(resp)) if resp else None,
        "verdicts": dict(by_v), "qualified": q, "invites": invites,
        "after_hours": sum(1 for c in calls if not c["in_hours"]),
        "contained": len(contained), "containment_pct": round(100 * len(contained) / n) if n else None,
        "price_asked": len(asked), "price_leaks": leaks, "price_deflected": len(asked) - sum(1 for c in asked if _price_leaks(c)),
        "pipeline": {"qualified": q, "low_lakh": q * lo, "high_lakh": q * hi, "mid_lakh": round(pipe_lakh, 1), "expected_lakh": round(expected_inr / 1e5, 1)},
        "cost_total_inr": round(cost, 2), "cost_per_call_inr": round(cost / n, 2) if n else 0,
        "cost_per_qualified_inr": round(cost / q, 2) if q else None, "cost_components": {k: round(v, 2) for k, v in comp.items()},
        "voice_minutes": round(minutes, 1), "tokens_in": tok_in, "tokens_out": tok_out,
        "closed_won": won_now, "closed_revenue_lakh": round(won_now * mid, 1), "roi_closed_x": round(won_now * mid * 1e5 / cost) if cost and won_now else None,
        "cpql_inr": round(cost / q, 2) if q else None,
        "roi_pipeline_x": round(pipe_inr / cost) if cost else None, "roi_expected_x": round(expected_inr / cost) if cost else None,
        "time_saved_hours": round(saved_min / 60.0, 1), "settings": st,
        "days": [{"date": k, **v} for k, v in sorted(days.items())], "hours": hours,
        "types": types.most_common(8), "areas": areas.most_common(8),
        "kanban": {key: [lead_card(c) for c in q_calls if stage_of(c) == key] for key, _ in STAGES},
        "stages": STAGES, "moods": dict(moods), "escalations": escal, "offhour": offhour, "workload": workload(calls), "red_flags": flags, "forecast": forecast, "today": today,
        "tokens_per_day": [{"date": k, "tokens": t} for k, t in sorted(tok_day.items())],
        "designers": [d["name"] for d in config.DESIGNERS],
        "gmail": bool(config.SMTP_HOST and config.SMTP_USER),
        "recent": [{"id": c["id"], "started_at": c["started_at"], "caller": c["caller_label"],
                    "name": (c.get("lead") or {}).get("name"), "verdict": ev[c["id"]], "original": c["verdict"],
                    "overridden": ev[c["id"]] != c["verdict"], "gist": c["gist"], "answer_s": c["ring_to_answer_s"],
                    "cost_inr": c["cost_inr"], "in_hours": c["in_hours"], "source": c["source"],
                    "price_asked": bool((c.get("result") or {}).get("price_asked")),
                    "mood": (c.get("result") or {}).get("mood"), "sentiment": (c.get("result") or {}).get("sentiment"), "sentiment_score": (c.get("result") or {}).get("sentiment_score"),
                    "hubspot": (c["integrations"] or {}).get("hubspot", {}).get("mode"),
                    "designer_email": (c["integrations"] or {}).get("designer_email", {}).get("mode"),
                    "invite": (c["integrations"] or {}).get("client_invite", {}).get("mode")} for c in calls[:60]],
    }
