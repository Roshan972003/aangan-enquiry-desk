"""CSV and PDF reports for the owner."""
import csv, io
from datetime import datetime
from app import config, stats, db

def csv_bytes():
    out = io.StringIO(); w = csv.writer(out)
    w.writerow(["call_id", "when", "caller", "name", "outcome", "ai_outcome", "stage", "assigned_to", "property", "area", "sqft", "timeline",
                "answered_in_s", "price_asked", "duration_s", "cost_inr", "gist"])
    for c in db.all_calls():
        lead = c.get("lead") or {}; ops = c.get("ops") or {}
        w.writerow([c["id"], c["started_at"], c["caller_label"], lead.get("name") or "", stats.eff_verdict(c), c["verdict"],
                    stats.stage_of(c) if stats.eff_verdict(c) == "qualified" else "", ops.get("assigned_to") or "", stats.prop_type(lead), stats.area_of(lead),
                    lead.get("sqft") or "", lead.get("timeline") or "", c["ring_to_answer_s"], int(bool((c.get("result") or {}).get("price_asked"))),
                    round(c["duration_s"] or 0), c["cost_inr"], c["gist"]])
    return out.getvalue().encode("utf-8")

def pdf_bytes():
    from reportlab.lib.pagesizes import A4
    from reportlab.lib import colors
    from reportlab.lib.styles import getSampleStyleSheet
    from reportlab.platypus import SimpleDocTemplate, Paragraph, Spacer, Table, TableStyle
    s = stats.compute(); st = getSampleStyleSheet()
    buf = io.BytesIO(); doc = SimpleDocTemplate(buf, pagesize=A4, leftMargin=36, rightMargin=36, topMargin=36, bottomMargin=36)
    inr = lambda x: "Rs " + format(round(x, 2), ",")
    lk = lambda x: f"Rs {x:,.1f} L"
    def tbl(rows, widths=None):
        t = Table(rows, colWidths=widths)
        t.setStyle(TableStyle([("FONTSIZE", (0, 0), (-1, -1), 9), ("GRID", (0, 0), (-1, -1), 0.25, colors.HexColor("#CCCCCC")),
                               ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#EEEAF8")), ("VALIGN", (0, 0), (-1, -1), "TOP")]))
        return t
    el = [Paragraph("Aangan Studio - Enquiry Desk report", st["Title"]), Paragraph(config.now_ist().strftime("Generated %d %B %Y, %H:%M"), st["Normal"]), Spacer(1, 10)]
    el += [Paragraph("Headline numbers", st["Heading2"]), tbl([
        ["Calls handled", s["calls"], "Qualified leads", s["qualified"]],
        ["Answered within 5 min", f"{s['within_5_min_pct']}%" if s["within_5_min_pct"] is not None else "-", "Handled by the AI", f"{s['containment_pct']}%" if s["containment_pct"] is not None else "-"],
        ["Median time to answer", f"{s['median_answer_s']} s" if s["median_answer_s"] is not None else "-", "After-hours calls", s["after_hours"]],
        ["Price questions deflected", f"{s['price_deflected']} of {s['price_asked']}", "Price figures spoken", s["price_leaks"]]], [150, 90, 150, 90]), Spacer(1, 10)]
    p = s["pipeline"]; f = s["forecast"]
    el += [Paragraph("Pipeline and return", st["Heading2"]), tbl([
        ["Pipeline value (qualified x average project)", lk(p["mid_lakh"])], ["Range", f"{lk(p['low_lakh'])} to {lk(p['high_lakh'])}"],
        ["Expected revenue at assumed close rate", lk(p["expected_lakh"])], ["Running cost", inr(s["cost_total_inr"])],
        ["Cost per qualified lead", inr(s["cost_per_qualified_inr"]) if s["cost_per_qualified_inr"] else "-"],
        ["Voice minutes / AI tokens", f"{s['voice_minutes']} / {s['tokens_in'] + s['tokens_out']:,}"], ["Front-desk hours saved", f"{s['time_saved_hours']} h"],
        ["Month-end projection (run-rate, not a model)", f"{f['projected_qualified_month']} leads, {lk(f['projected_revenue_lakh'])} expected"]], [260, 220]), Spacer(1, 6),
        Paragraph("Pipeline is potential value, not revenue. Close rate and project value are assumptions set on the dashboard.", st["Italic"]), Spacer(1, 10)]
    el += [Paragraph("Outcomes", st["Heading2"]), tbl([["Outcome", "Calls"]] + [[k.replace("_", " ").title(), v] for k, v in s["verdicts"].items()], [200, 80]), Spacer(1, 10)]
    el += [Paragraph("Where leads come from", st["Heading2"]), tbl([["Property type", "Leads"]] + [list(x) for x in s["types"]] , [200, 80]), Spacer(1, 6),
           tbl([["Area", "Leads"]] + [list(x) for x in s["areas"]], [200, 80])]
    doc.build(el)
    return buf.getvalue()
