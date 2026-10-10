"""CSV and PDF reports for the owner. The PDF is a full management report: summary, performance, ROI, pipeline, people, risks and a per-call appendix."""
import csv, io
from datetime import datetime
from app import config, stats, db

CRIT = ["real_project", "service_area", "timeline", "budget", "decision_maker"]
CRIT_LABEL = {"real_project": "Real project", "service_area": "In service area", "timeline": "Timeline", "budget": "Budget", "decision_maker": "Decision maker"}

def _integ(c, k):
    v = (c.get("integrations") or {}).get(k)
    return (v.get("status") if isinstance(v, dict) else v) or ""

def _crit(c):
    return {x["id"]: x for x in ((c.get("result") or {}).get("criteria") or [])}

def csv_bytes():
    out = io.StringIO(); w = csv.writer(out)
    w.writerow(["call_id", "when", "source", "caller", "name", "email", "outcome", "ai_outcome", "overridden", "stage", "assigned_to", "property", "area", "sqft",
                "scope", "timeline", "decision_maker", "referral", "budget_volunteered", *[f"crit_{k}" for k in CRIT], "mood", "sentiment", "sentiment_score",
                "win_chance_pct", "answered_in_s", "in_hours", "price_asked", "duration_s", "cost_inr", "gemini_inr", "telephony_inr", "speech_inr", "tokens_in", "tokens_out",
                "decline_reason", "uncertainties", "first_question_for_designer", "consultation_slot", "designer_email_status", "client_invite_status", "hubspot_status", "gist"])
    for c in db.all_calls():
        lead = c.get("lead") or {}; ops = c.get("ops") or {}; r = c.get("result") or {}; cost = c.get("cost") or {}; cr = _crit(c)
        ev = stats.eff_verdict(c)
        win = stats.win_chance(c)[0] if ev == "qualified" else ""
        w.writerow([c["id"], c["started_at"], c.get("source"), c["caller_label"], lead.get("name") or "", lead.get("email") or "", ev, c["verdict"], int(ev != c["verdict"]),
                    stats.stage_of(c) if ev == "qualified" else "", ops.get("assigned_to") or "", stats.prop_type(lead), stats.area_of(lead), lead.get("sqft") or "",
                    lead.get("scope") or "", lead.get("timeline") or "", lead.get("decision_maker") or "", lead.get("referral") or "", lead.get("budget_volunteered") or "",
                    *[(cr.get(k) or {}).get("status", "") for k in CRIT], r.get("mood") or "", r.get("sentiment") or "", r.get("sentiment_score") or "", win,
                    c["ring_to_answer_s"], c.get("in_hours"), int(bool(r.get("price_asked"))), round(c["duration_s"] or 0), c["cost_inr"],
                    cost.get("gemini", ""), cost.get("telephony", ""), cost.get("speech", ""), cost.get("tokens_in", ""), cost.get("tokens_out", ""),
                    r.get("decline_reason") or "", "; ".join(r.get("uncertainties") or []), r.get("first_question_for_designer") or "",
                    (c.get("slot") or {}).get("label", ""), _integ(c, "designer_email"), _integ(c, "client_invite"), _integ(c, "hubspot"), c["gist"]])
    return out.getvalue().encode("utf-8")

def pdf_bytes():
    from reportlab.lib.pagesizes import A4
    from reportlab.lib import colors
    from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
    from reportlab.platypus import SimpleDocTemplate, Paragraph, Spacer, Table, TableStyle, PageBreak, KeepTogether
    from reportlab.graphics.shapes import Drawing
    from reportlab.graphics.charts.barcharts import VerticalBarChart
    from reportlab.graphics.charts.piecharts import Pie
    from xml.sax.saxutils import escape as esc

    s = stats.compute(); calls = sorted(db.all_calls(), key=lambda c: c["started_at"]); st = getSampleStyleSheet()
    ACC, INK, MUTED = colors.HexColor("#4F46E5"), colors.HexColor("#111827"), colors.HexColor("#6B7280")
    H2 = ParagraphStyle("H2x", parent=st["Heading2"], textColor=ACC, spaceBefore=10, spaceAfter=4)
    H3 = ParagraphStyle("H3x", parent=st["Heading3"], textColor=INK, spaceBefore=6, spaceAfter=2)
    B = ParagraphStyle("Bx", parent=st["Normal"], fontSize=9.5, leading=13)
    SM = ParagraphStyle("SMx", parent=st["Normal"], fontSize=8, leading=10.5, textColor=MUTED)
    CELL = ParagraphStyle("Cx", parent=st["Normal"], fontSize=8, leading=10)
    P = lambda t, sty=B: Paragraph(esc(str(t)), sty)
    inr = lambda x: "Rs " + format(round(x or 0, 2), ",")
    lk = lambda x: f"Rs {x:,.1f} L"
    pct = lambda x: "-" if x is None else f"{x}%"

    def tbl(rows, widths=None, head=True, wrap=False):
        if wrap: rows = [[Paragraph(esc(str(x)), CELL) if not hasattr(x, "wrap") else x for x in r] for r in rows]
        t = Table(rows, colWidths=widths, repeatRows=1 if head else 0)
        sty = [("FONTSIZE", (0, 0), (-1, -1), 8.5), ("GRID", (0, 0), (-1, -1), 0.25, colors.HexColor("#D1D5DB")), ("VALIGN", (0, 0), (-1, -1), "TOP"),
               ("ROWBACKGROUNDS", (0, 1), (-1, -1), [colors.white, colors.HexColor("#F9FAFB")])]
        if head: sty += [("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#EEEAF8")), ("FONTNAME", (0, 0), (-1, 0), "Helvetica-Bold")]
        t.setStyle(TableStyle(sty)); return t

    def bar(labels, vals, w=500, h=120, color=ACC):
        d = Drawing(w, h); c = VerticalBarChart(); c.x, c.y, c.width, c.height = 30, 22, w - 45, h - 40
        c.data = [vals]; c.categoryAxis.categoryNames = labels; c.bars[0].fillColor = color
        c.categoryAxis.labels.fontSize = 6; c.valueAxis.labels.fontSize = 7; c.valueAxis.valueMin = 0
        c.valueAxis.valueMax = max(max(vals or [1]), 1) * 1.15; c.valueAxis.visibleGrid = True; c.valueAxis.gridStrokeColor = colors.HexColor("#E5E7EB")
        d.add(c); return d

    def pie(items, w=240, h=130):
        d = Drawing(w, h); p = Pie(); p.x, p.y, p.width, p.height = 10, 10, 110, 110
        p.data = [v for _, v in items] or [1]; p.labels = [f"{k} ({v})" for k, v in items]; p.slices.fontSize = 7
        pal = ["#10B981", "#F43F5E", "#F59E0B", "#6366F1", "#9CA3AF", "#06B6D4"]
        for i in range(len(items)): p.slices[i].fillColor = colors.HexColor(pal[i % len(pal)])
        p.sideLabels = True; d.add(p); return d

    def footer(cv, doc):
        cv.saveState(); cv.setFont("Helvetica", 7.5); cv.setFillColor(MUTED)
        cv.drawString(36, 22, "Aangan Studio - Enquiry Desk report"); cv.drawRightString(A4[0] - 36, 22, f"Page {doc.page}"); cv.restoreState()

    ev = {c["id"]: stats.eff_verdict(c) for c in calls}
    first, last = (calls[0]["started_at"][:10], calls[-1]["started_at"][:10]) if calls else ("-", "-")
    p, f, off, cc = s["pipeline"], s["forecast"], s["offhour"], s["cost_components"]
    buf = io.BytesIO(); doc = SimpleDocTemplate(buf, pagesize=A4, leftMargin=36, rightMargin=36, topMargin=36, bottomMargin=36)
    el = [Paragraph("Aangan Studio, Pune", st["Title"]), Paragraph("Enquiry Desk: management report (phone enquiries)", st["Heading2"]),
          P(f"Generated {config.now_ist().strftime('%d %B %Y, %H:%M')} IST. Covers {s['calls']} incoming calls from {first} to {last}.", SM), Spacer(1, 8)]

    # 1 executive summary
    top_area = s["areas"][0][0] if s["areas"] and s["areas"][0][0] != "Not given" else (s["areas"][1][0] if len(s["areas"]) > 1 else "-")
    el += [Paragraph("1. Executive summary", H2),
           P(f"The AI desk handled {s['calls']} calls ({s['answered']} answered, {s['missed']} missed). {s['qualified']} were qualified "
             f"({round(100 * s['qualified'] / s['calls']) if s['calls'] else 0}% of calls), worth about {lk(p['mid_lakh'])} of potential pipeline "
             f"(range {lk(p['low_lakh'])} to {lk(p['high_lakh'])}). {pct(s['within_5_min_pct'])} of calls were answered within 5 minutes with a median of "
             f"{s['median_answer_s']} s. {pct(s['containment_pct'])} were handled without a human."),
           Spacer(1, 3),
           P(f"Price discipline: {s['price_asked']} callers asked for a price; all {s['price_deflected']} were steered to the free consultation and "
             f"{s['price_leaks']} price figures were spoken. {s['after_hours']} calls arrived outside office hours; an estimated {inr(off['saved_inr'])} of revenue was "
             f"protected that would otherwise have been lost to no-reply. Total running cost was {inr(s['cost_total_inr'])} "
             f"({inr(s['cost_per_call_inr'])} per call, {inr(s['cost_per_qualified_inr'])} per qualified lead)."), Spacer(1, 6)]
    el += [tbl([["Calls handled", s["calls"], "Qualified leads", s["qualified"]],
                ["Answered within 5 min", pct(s["within_5_min_pct"]), "Handled by the AI", pct(s["containment_pct"])],
                ["Median / slowest answer", f"{s['median_answer_s']} s / {s['max_answer_s']} s", "After-hours calls", s["after_hours"]],
                ["Price questions deflected", f"{s['price_deflected']} of {s['price_asked']}", "Price figures spoken", s["price_leaks"]],
                ["Escalations needing a human", s["verdicts"].get("escalate", 0), "Missed calls", s["missed"]]], [140, 100, 140, 100], head=False)]

    # 2 outcomes and volume
    el += [Paragraph("2. Call outcomes and volume", H2)]
    ov = sorted(s["verdicts"].items(), key=lambda x: -x[1])
    el += [Table([[pie([(k.replace("_", " ").title(), v) for k, v in ov]), tbl([["Outcome", "Calls", "Share"]] + [[k.replace("_", " ").title(), v, f"{round(100 * v / s['calls'])}%"] for k, v in ov], [90, 40, 45])]],
                 colWidths=[260, 240]), Spacer(1, 4)]
    if s["days"]:
        el += [P("Calls per day", H3), bar([d["date"][5:] for d in s["days"]], [d["calls"] for d in s["days"]]), P("Qualified per day", H3),
               bar([d["date"][5:] for d in s["days"]], [d["qualified"] for d in s["days"]], color=colors.HexColor("#10B981"))]
    el += [P("Calls by hour of day (IST)", H3), bar([f"{h:02d}" for h in range(24)], s["hours"], color=colors.HexColor("#6366F1"))]

    # 3 response speed
    answered = [c for c in calls if c.get("ring_to_answer_s") is not None]
    buckets = [("Under 1 min", sum(1 for c in answered if c["ring_to_answer_s"] < 60)), ("1 to 5 min", sum(1 for c in answered if 60 <= c["ring_to_answer_s"] <= 300)),
               ("Over 5 min", sum(1 for c in answered if c["ring_to_answer_s"] > 300))]
    el += [Paragraph("3. Response speed (target: under 5 minutes, day and night)", H2),
           tbl([["Band", "Calls"]] + [list(b) for b in buckets] + [["Median answer time", f"{s['median_answer_s']} s"], ["Slowest answer", f"{s['max_answer_s']} s"],
                ["After-hours calls", s["after_hours"]]], [200, 100])]

    # 4 pricing guard
    pq = [c for c in calls if (c.get("result") or {}).get("price_asked")]
    el += [Paragraph("4. Pricing discipline", H2), P("The agent never quotes a price. Every figure is blocked by a code guard before it is spoken, and the callers are steered to the free consultation."),
           tbl([["Price questions", s["price_asked"], "Deflected to consultation", s["price_deflected"], "Figures spoken", s["price_leaks"]]], [90, 40, 130, 40, 90, 40], head=False)]
    if pq: el += [Spacer(1, 4), tbl([["Call", "Caller", "Outcome"]] + [[c["id"], (c.get("lead") or {}).get("name") or c["caller_label"], ev[c["id"]].replace("_", " ")] for c in pq], [80, 200, 120])]

    # 5 money
    el += [PageBreak(), Paragraph("5. Pipeline, cost and return", H2),
           tbl([["Qualified leads", s["qualified"]], ["Pipeline value (qualified x average project)", lk(p["mid_lakh"])], ["Range", f"{lk(p['low_lakh'])} to {lk(p['high_lakh'])}"],
                ["Expected revenue at assumed close rate", f"{lk(p['expected_lakh'])} ({s['settings']['close_rate_pct']}% close rate)"],
                ["Closed and won so far", f"{s['closed_won']} deals, {lk(s['closed_revenue_lakh'])}"],
                ["Running cost, total", inr(s["cost_total_inr"])], ["   Gemini AI", inr(cc["gemini"])], ["   Telephony", inr(cc["telephony"])], ["   Speech", inr(cc["speech"])],
                ["Cost per call / per qualified lead", f"{inr(s['cost_per_call_inr'])} / {inr(s['cost_per_qualified_inr'])}"],
                ["Voice minutes / AI tokens", f"{s['voice_minutes']} min / {s['tokens_in'] + s['tokens_out']:,}"],
                ["Front-desk time saved", f"{s['time_saved_hours']} h ({s['settings']['minutes_saved_per_call']} min per call)"],
                ["Month-end projection (run-rate)", f"{f['projected_qualified_month']} qualified leads, {lk(f['projected_pipeline_lakh'])} pipeline, {lk(f['projected_revenue_lakh'])} expected"]],
               [230, 270], head=False), Spacer(1, 4),
           P(f"Assumptions: average project {s['settings']['project_low_lakh']} to {s['settings']['project_high_lakh']} lakh, close rate {s['settings']['close_rate_pct']}%, "
             f"{s['settings']['offhour_loss_pct']}% of enquiries lost when no one replies within 48 hours. Pipeline is potential value, not revenue. Costs are estimates from configured rates, not vendor bills.", SM)]
    el += [Paragraph("Off-hours recovery", H3), P(f"{off['calls']} calls arrived out of hours, {off['qualified']} qualified. Estimated revenue protected: {inr(off['saved_inr'])}.")]
    orows = [[o.get("id"), (o.get("name") or ""), str(o.get("at", ""))[:16].replace("T", " "), o.get("verdict", "")] for o in off.get("recent", [])]
    if orows: el += [tbl([["Call", "Caller", "When", "Outcome"]] + orows, [60, 160, 130, 90])]

    # 6 sources and mood
    el += [Paragraph("6. Where leads come from and how callers feel", H2),
           Table([[tbl([["Property type", "Leads"]] + [list(x) for x in s["types"]], [130, 50]), tbl([["Area", "Leads"]] + [list(x) for x in s["areas"]], [130, 50])]], colWidths=[250, 250]), Spacer(1, 4),
           tbl([["Mood (from transcript)", "Calls"]] + [[k.title(), v] for k, v in s["moods"].items()], [200, 60]),
           P("Mood is inferred from the words of the transcript, not from vocal tone.", SM)]

    # 7 people and pipeline board
    el += [Paragraph("7. Designer workload and pipeline stages", H2),
           tbl([["Designer", "Designation", "Active leads", "Load"]] + [[d["name"], d.get("title", ""), d["active"], d["level"]] for d in s["workload"]["designers"]], [130, 150, 80, 80]), Spacer(1, 4),
           tbl([["Stage", "Leads"]] + [[lbl, len(s["kanban"].get(k, []))] for k, lbl in s["stages"]], [250, 80])]

    # 8 risks
    el += [Paragraph("8. Escalations and follow-up risks", H2)]
    esc_rows = [[e["id"], e.get("name") or "", (e.get("gist") or "")[:420]] for e in s["escalations"]]
    el += [tbl([["Call", "Caller", "Why it needs a human"]] + esc_rows, [50, 110, 340], wrap=True) if esc_rows else P("No open escalations.")]
    if s["red_flags"]:
        el += [Spacer(1, 4), tbl([["Call", "Flag", "Caller", "Detail"]] + [[r["id"], r["kind"], r.get("name") or "", (r.get("gist") or "")[:140]] for r in s["red_flags"]], [45, 110, 110, 235], wrap=True)]

    # 9 registers
    q = [c for c in calls if ev[c["id"]] == "qualified"]
    el += [PageBreak(), Paragraph("9. Qualified lead register", H2)]
    rows = [["Call", "Name", "Property / scope", "Area", "Sq ft", "Timeline", "Stage", "Designer", "Win"]]
    for c in q:
        lead = c.get("lead") or {}; ops = c.get("ops") or {}
        try: win = f"{stats.win_chance(c)[0]}%"
        except Exception: win = ""
        rows.append([c["id"], lead.get("name") or c["caller_label"], f"{stats.prop_type(lead)}: {lead.get('scope') or ''}"[:70], stats.area_of(lead), lead.get("sqft") or "-",
                     lead.get("timeline") or "-", dict(s["stages"]).get(stats.stage_of(c), ""), ops.get("assigned_to") or "Unassigned", win])
    el += [tbl(rows, [32, 62, 118, 62, 30, 58, 60, 60, 28], wrap=True)]
    other = [c for c in calls if ev[c["id"]] != "qualified"]
    el += [Paragraph("10. Declined, not-now, escalated and missed calls", H2),
           tbl([["Call", "Caller", "Outcome", "Reason"]] + [[c["id"], (c.get("lead") or {}).get("name") or c["caller_label"], ev[c["id"]].replace("_", " "),
                 ((c.get("result") or {}).get("decline_reason") or c["gist"] or "")[:200]] for c in other], [38, 90, 62, 310], wrap=True)]

    # 11 appendix
    el += [PageBreak(), Paragraph("Appendix: call-by-call detail", H2), P("Each call: AI summary, the five qualification criteria with the evidence heard on the call, open questions and what happened downstream.", SM)]
    for c in calls:
        lead = c.get("lead") or {}; r = c.get("result") or {}; cr = _crit(c); ops = c.get("ops") or {}
        head = f"{c['id']}  |  {lead.get('name') or c['caller_label']}  |  {ev[c['id']].replace('_', ' ').upper()}  |  {c['started_at'][:16].replace('T', ' ')}"
        meta = (f"Answered in {c['ring_to_answer_s']} s, {round((c['duration_s'] or 0) / 60, 1)} min, {'in' if c.get('in_hours') else 'after'} hours | "
                f"Mood {r.get('mood') or '-'} ({r.get('sentiment') or '-'}, {r.get('sentiment_score') or '-'}/100) | Price asked: {'yes' if r.get('price_asked') else 'no'} | Cost {inr(c['cost_inr'])}")
        blk = [Paragraph(esc(head), H3), P(meta, SM), P(c["gist"] or "")]
        if cr:
            blk += [tbl([["Criterion", "Result", "Evidence"]] + [[CRIT_LABEL.get(k, k), (cr[k]["status"]).upper(), cr[k].get("evidence") or ""] for k in CRIT if k in cr], [90, 50, 360], wrap=True)]
        if lead.get("email") or lead.get("referral") or lead.get("budget_volunteered"):
            blk += [P(f"Email: {lead.get('email') or '-'} | Referral: {lead.get('referral') or '-'} | Budget volunteered: {lead.get('budget_volunteered') or '-'}", SM)]
        if r.get("uncertainties"): blk += [P("Open questions: " + "; ".join(r["uncertainties"]), SM)]
        if r.get("first_question_for_designer") and ev[c["id"]] == "qualified": blk += [P("First question for the designer: " + r["first_question_for_designer"], SM)]
        if r.get("decline_reason"): blk += [P("Reason: " + r["decline_reason"], SM)]
        slot = (c.get("slot") or {}).get("label")
        blk += [P(f"Assigned: {ops.get('assigned_to') or '-'} | Consultation: {slot or 'not booked'} | Designer email: {_integ(c, 'designer_email') or '-'} | "
                  f"Client invite: {_integ(c, 'client_invite') or '-'} | HubSpot: {_integ(c, 'hubspot') or '-'}", SM), Spacer(1, 6)]
        el += [KeepTogether(blk)]
    doc.build(el, onFirstPage=footer, onLaterPages=footer)
    return buf.getvalue()
