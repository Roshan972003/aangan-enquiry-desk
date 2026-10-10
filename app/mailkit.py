"""Designer and client emails: branded HTML with a plain-text alternative. Facts come from the call record; only the client's warm opening is written by Gemini."""
from html import escape as h
from app import config, links, gemini, guard

BRAND, INK, MUTED, SOFT = "#4F46E5", "#111827", "#6B7280", "#F5F3FF"

def _shell(preheader, inner):
    return (f'<div style="background:#F3F4F6;padding:24px 12px;font-family:-apple-system,Segoe UI,Helvetica,Arial,sans-serif">'
            f'<span style="display:none;opacity:0;height:0;overflow:hidden">{h(preheader)}</span>'
            f'<table role="presentation" width="100%" cellpadding="0" cellspacing="0" style="max-width:600px;margin:0 auto;background:#ffffff;border-radius:14px;overflow:hidden">'
            f'<tr><td style="background:{BRAND};padding:18px 26px;color:#fff;font-size:18px;font-weight:700;letter-spacing:.3px">Aangan Studio<span style="font-weight:400;opacity:.85;font-size:13px"> &nbsp;|&nbsp; Interior design, Pune</span></td></tr>'
            f'<tr><td style="padding:26px;color:{INK};font-size:15px;line-height:1.6">{inner}</td></tr>'
            f'<tr><td style="padding:16px 26px;background:#F9FAFB;color:{MUTED};font-size:12px">Aangan Studio, Pune. You are receiving this because of your enquiry call.</td></tr></table></div>')

def _p(t, style=""): return f'<p style="margin:0 0 14px;{style}">{h(t)}</p>'
def _btn(label, url, primary=True):
    bg, fg, bd = (BRAND, "#fff", BRAND) if primary else ("#fff", BRAND, BRAND)
    return f'<a href="{h(url)}" style="display:inline-block;padding:11px 20px;margin:0 8px 8px 0;border-radius:9px;background:{bg};color:{fg};border:1px solid {bd};text-decoration:none;font-weight:600;font-size:14px">{h(label)}</a>'
def _rows(pairs):
    return ('<table role="presentation" width="100%" cellpadding="0" cellspacing="0" style="margin:0 0 16px;border:1px solid #E5E7EB;border-radius:10px">' +
            "".join(f'<tr><td style="padding:9px 14px;width:34%;color:{MUTED};font-size:13px;border-bottom:1px solid #F3F4F6">{h(k)}</td><td style="padding:9px 14px;font-size:14px;border-bottom:1px solid #F3F4F6">{h(str(v))}</td></tr>' for k, v in pairs if v) + '</table>')
def _h3(t): return f'<h3 style="margin:20px 0 8px;font-size:15px;color:{BRAND}">{h(t)}</h3>'

# ---------------- client ----------------
def _warm_parts(lead, slot, result):
    first = (lead.get("name") or "").split(" ")[0] or "there"
    proj = ", ".join(x for x in (lead.get("scope"), (f"{int(lead['sqft'])} sq ft" if lead.get("sqft") else None), lead.get("location")) if x)
    fallback = {"opening": f"Thank you so much for calling Aangan Studio, {first}. It was a real pleasure to hear about your plans" + (f" for {proj}" if proj else "") + ", and we would love to be the team that brings them to life.",
                "closing": "We are genuinely excited about the possibility of working with you. Our designers pour themselves into every home, and we cannot wait to hear more about how you imagine yours."}
    if config.MOCK_LLM or not config.GEMINI_API_KEY:
        return fallback
    try:
        out, _ = gemini.generate(
            "You write two short paragraphs for a warm, genuine thank-you email from Aangan Studio, a Pune interior design studio, to someone who just phoned in an enquiry. "
            "Tone: friendly, enthusiastic, personal and professional, like a studio that truly wants to work with them. Use the caller's first name once. "
            "Paragraph 'opening' (2 sentences): thank them and mention one or two specific things they told us about their project, showing we listened. "
            "Paragraph 'closing' (2 sentences): say we are excited to work with them and look forward to meeting. "
            "Never mention any price, cost, budget, discount or figure. Do not mention dates or times. Plain text, no emojis, no markdown.",
            [{"role": "user", "text": f"First name: {first}\nProject: {proj}\nTimeline: {lead.get('timeline')}\nCall summary: {result.get('gist') if result else ''}"}],
            {"type": "OBJECT", "properties": {"opening": {"type": "STRING"}, "closing": {"type": "STRING"}}, "required": ["opening", "closing"]}, 0.6)
        if isinstance(out, dict) and out.get("opening") and out.get("closing") and not guard.violates(out["opening"] + " " + out["closing"], ""):
            return {"opening": out["opening"].strip(), "closing": out["closing"].strip()}
    except Exception:
        pass
    return fallback

def client_email(lead, slot, result=None):
    """Returns (subject, text, html)."""
    first = (lead.get("name") or "").split(" ")[0] or "there"
    w = _warm_parts(lead, slot, result)
    when = slot["label"] if slot else None
    designer = slot.get("designer") if slot else None
    subject = f"Looking forward to working with you, {first}" + (f" | Your consultation: {when}" if when else "")
    expect = ["Your designer will listen to your ideas and look at the space with you.",
              "Together you will talk through the scope, the look and feel, and how we would approach it.",
              "Pricing depends on the site, materials and scope, so your designer will walk you through it in detail.",
              "It helps to bring floor plans, photos of the space and any inspiration you love."]
    text = "\n".join([f"Hello {first},", "", w["opening"], ""] +
        ([f"YOUR FREE DESIGN CONSULTATION", f"  When: {when}", f"  With: {designer or 'one of our designers'}", "  Where: your site or a video call; your designer will confirm", "  A calendar invite has been sent separately (please check spam if you cannot see it).", ""] if when
         else ["One of our designers will call you shortly to agree a time for your free design consultation.", ""]) +
        ["WHAT TO EXPECT"] + ["  - " + x for x in expect] + ["", w["closing"], "", "Need a different time? Just reply to this email and we will sort it out.", "", "Warm regards,", "The Aangan Studio team", "Pune"])
    card = (_rows([("When", when), ("With", designer or "One of our designers"), ("Where", "Your site or a video call; your designer will confirm"), ("Calendar", "Invite sent separately (check spam if needed)")])
            if when else _p("One of our designers will call you shortly to agree a time for your free design consultation."))
    inner = (_p(f"Hello {first},") + _p(w["opening"]) + (_h3("Your free design consultation") if when else "") + card + _h3("What to expect") +
             '<ul style="margin:0 0 16px;padding-left:20px">' + "".join(f"<li style='margin-bottom:6px'>{h(x)}</li>" for x in expect) + "</ul>" +
             _p(w["closing"]) + _p("Need a different time? Just reply to this email and we will sort it out.") +
             _p("Warm regards,<br>The Aangan Studio team", "").replace("Warm regards,&lt;br&gt;The Aangan Studio team", "Warm regards,<br><b>The Aangan Studio team</b>"))
    return subject, text, _shell(f"We loved hearing about your project, {first}", inner)

# ---------------- designer ----------------
CRIT_LABEL = {"real_project": "Real project", "service_area": "In service area", "timeline": "Timeline", "budget": "Budget", "decision_maker": "Decision-maker"}

def designer_email(call_id, lead, result, slot, transcript_text, phone=None):
    """Returns (subject, text, html)."""
    d = slot["designer"] if slot else None
    first_d = (d or "team").split(" ")[0]
    phone = phone if sum(ch.isdigit() for ch in str(phone or "")) >= 7 else None
    size = f"{int(lead['sqft'])} sq ft" if lead.get("sqft") else "size unknown"
    when = slot["label"] if slot else None
    subject = f"New qualified lead: {lead.get('name') or 'Caller'}, {lead.get('scope') or 'enquiry'}, {lead.get('location') or 'Pune'} ({size})" + (f" | Consultation {when}" if when else " | Needs a call-back")
    accept, listen = links.url(call_id, "accept"), links.url(call_id, "listen")
    crit = [(CRIT_LABEL.get(c["id"], c["id"]), {"pass": "Yes", "fail": "No", "unclear": "Unclear"}.get(c["status"], c["status"]), c.get("evidence") or "") for c in (result.get("criteria") or [])]
    facts = [("Client", lead.get("name")), ("Phone", phone), ("Email", lead.get("email") or "Not captured: collect it on your first call"), ("Project", lead.get("scope")),
             ("Property", lead.get("property_type")), ("Area", lead.get("location")), ("Size", size if lead.get("sqft") else None), ("Timeline", lead.get("timeline")),
             ("Decision-maker", lead.get("decision_maker")), ("Budget said on call", lead.get("budget_volunteered") or "Not mentioned"), ("Referral", lead.get("referral")),
             ("Consultation", when or "Not booked yet. Please call the client to fix a time.")]
    priced = "Yes. They asked about price; we gave no figure and steered them to your consultation, so expect the question." if result.get("price_asked") else "No."
    mood = f"{result.get('mood') or 'calm'} ({result.get('sentiment') or 'neutral'})"
    reask, unc = ", ".join(result.get("dont_reask") or []) or "nothing", "; ".join(result.get("uncertainties") or []) or "nothing"
    start = result.get("first_question_for_designer") or ""
    intro = f"A new qualified lead has just come in from the AI phone desk" + (f" and is booked in with you, {d}." if d else ".") + " Everything we learned is below so you do not need to re-ask."
    text = "\n".join([f"Hi {first_d},", "", intro, "", f"ACCEPT THIS LEAD: {accept}", f"LISTEN TO THE CALL / OPEN IN DASHBOARD: {listen}", "", "THE CALL IN BRIEF", result.get("gist", ""), "",
        "AT A GLANCE"] + [f"  {k}: {v}" for k, v in facts if v] + ["", "WHY THEY QUALIFIED"] + [f"  {a}: {b}. {c}" for a, b, c in crit] +
        ["", f"Asked about price: {priced}", f"Caller mood: {mood}", "", f"Do not re-ask: {reask}", f"Still unclear: {unc}", f"Good first question: {start}", "", f"Call ID: {call_id}", "", "FULL TRANSCRIPT", transcript_text])
    cr = ('<table role="presentation" width="100%" cellpadding="0" cellspacing="0" style="margin:0 0 16px;border:1px solid #E5E7EB;border-radius:10px">' +
          "".join(f'<tr><td style="padding:8px 14px;width:30%;font-size:13px;color:{MUTED};border-bottom:1px solid #F3F4F6">{h(a)}</td><td style="padding:8px 8px;width:14%;font-size:13px;font-weight:600;color:{"#059669" if b=="Yes" else "#B45309" if b=="Unclear" else "#DC2626"};border-bottom:1px solid #F3F4F6">{h(b)}</td><td style="padding:8px 14px;font-size:13px;border-bottom:1px solid #F3F4F6">{h(c)}</td></tr>' for a, b, c in crit) + "</table>")
    inner = (_p(f"Hi {first_d},") + _p(intro) + f'<div style="margin:6px 0 10px">{_btn("Accept this lead", accept)}{_btn("Listen to the call", listen, False)}</div>' +
             _h3("The call in brief") + f'<div style="background:{SOFT};border-radius:10px;padding:12px 14px;margin:0 0 6px">{h(result.get("gist", ""))}</div>' +
             _h3("At a glance") + _rows(facts) + _h3("Why they qualified") + cr +
             _h3("Before you call") + _rows([("Asked about price", priced), ("Caller mood", mood), ("Do not re-ask", reask), ("Still unclear", unc), ("Good first question", start)]) +
             f'<p style="color:{MUTED};font-size:12px;margin:18px 0 6px">Call ID {h(call_id)}</p>' +
             f'<div style="font-size:11.5px;color:{MUTED};white-space:pre-wrap;border-top:1px solid #E5E7EB;padding-top:10px"><b>Full transcript</b>\n{h(transcript_text)}</div>')
    return subject, text, _shell(f"New qualified lead: {lead.get('name') or 'Caller'}", inner)
