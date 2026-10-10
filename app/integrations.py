"""HubSpot, email and calendar invite. Each runs live when credentials exist, otherwise writes to outbox/."""
import json, re, smtplib, uuid
from email.message import EmailMessage
from datetime import datetime
import httpx
from app import config
from app.ics import build_invite
from app import links

def _outbox(kind, name, content):
    try:
        d = config.OUTBOX / kind; d.mkdir(parents=True, exist_ok=True)
        p = d / name; p.write_text(content if isinstance(content, str) else json.dumps(content, indent=2, ensure_ascii=False))
        return str(p.relative_to(config.ROOT))
    except OSError:
        return "stored in the call record (read-only filesystem)"

# ---------------- HubSpot ----------------
HS = "https://api.hubapi.com"

def _first_stage(c):
    """Use the first stage of the default deal pipeline, whatever this HubSpot account calls it."""
    r = c.get(f"{HS}/crm/v3/pipelines/deals"); r.raise_for_status()
    p = next((x for x in r.json()["results"] if x["id"] == "default"), r.json()["results"][0])
    st = sorted(p["stages"], key=lambda x: x.get("displayOrder", 0))[0]
    return p["id"], st["id"]

def _note_html(text):
    """Plain note text to the HTML HubSpot stores: escaped, line breaks kept, and the dashboard link made clickable."""
    import html as _h
    body = _h.escape(text).replace("\n", "<br>")
    return re.sub(r"(https://[^\s<]+)", r'<a href="\1">\1</a>', body)

def transcript_note(turns, limit=60000):
    """The call transcript as plain lines with timestamps, trimmed to fit HubSpot's note size limit."""
    lines = []
    for t in turns or []:
        sec = t.get("t")
        stamp = f"[{int(sec) // 60}:{int(sec) % 60:02d}] " if isinstance(sec, (int, float)) else ""
        lines.append(f"{stamp}{'AGENT' if t.get('who') == 'agent' else 'CALLER'}: {t.get('text', '')}")
    out = "\n".join(lines)
    return out if len(out) <= limit else out[:limit].rsplit("\n", 1)[0] + "\n[transcript trimmed; full text on the dashboard]"

def _real_phone(p):
    """Web-test calls carry labels like 'web-user'; only a real number goes to HubSpot."""
    p = str(p or "")
    return p if sum(ch.isdigit() for ch in p) >= 7 else ""

def hubspot_sync(call_id, lead, result, call_meta, live=True):
    """Contact for every caller; a deal only for qualified leads so the pipeline stays clean."""
    full = (lead.get("name") or "").strip()
    first, _, last = full.partition(" ")
    verdict = result["verdict"]
    props = {"firstname": first or "Unknown caller", "lastname": last.strip(), "phone": _real_phone(call_meta.get("phone")),
             "email": lead.get("email") or "", "city": "Pune",
             "lifecyclestage": "salesqualifiedlead" if verdict == "qualified" else "lead",
             "hs_lead_status": {"qualified": "OPEN_DEAL", "declined": "UNQUALIFIED", "not_now": "ATTEMPTED_TO_CONTACT"}.get(verdict, "NEW")}
    props = {k: v for k, v in props.items() if v}
    sqft = int(lead["sqft"]) if lead.get("sqft") else None
    note = (f"Aangan phone enquiry {call_id}\nVerdict: {verdict}\nGist: {result['gist']}\n"
            f"Scope: {lead.get('scope')} | Area: {lead.get('location')} | Size: {sqft or '?'} sq ft | Timeline: {lead.get('timeline')}\n"
            f"Decision maker: {lead.get('decision_maker')} | Referral: {lead.get('referral')}\n"
            f"Assigned designer: {call_meta.get('designer') or 'not assigned yet'}\n"
            f"Consultation: {call_meta.get('slot') or 'not booked'}\n"
            f"Uncertainties: {', '.join(result.get('uncertainties') or []) or 'none'}\n"
            f"Dashboard (listen to the recording): {links.url(call_id, 'listen')}")
    if call_meta.get("transcript"):
        note += "\n\nFULL TRANSCRIPT\n" + transcript_note(call_meta["transcript"])
    st = config.DEFAULT_SETTINGS
    amount = round((st["project_low_lakh"] + st["project_high_lakh"]) / 2 * 100000) if verdict == "qualified" else None
    deal = {"dealname": f"{full or 'Caller'} - {lead.get('location') or 'Pune'} - {lead.get('scope') or 'enquiry'}"[:120],
            "description": f"Designer: {call_meta.get('designer') or 'not assigned yet'}\n{result['gist']}\nInternal project estimate only. No price was quoted to the client.",
            "pipeline": "default", "dealstage": "appointmentscheduled" if call_meta.get("slot") else "qualifiedtobuy"}
    if amount: deal["amount"] = str(amount)
    payload = {"contact": props, "deal": deal if verdict == "qualified" else None, "note": note, "meeting_slot": call_meta.get("slot")}
    if not (live and config.LIVE_INTEGRATIONS and config.HUBSPOT_TOKEN):
        return {"mode": "outbox", "status": "saved locally, HubSpot not live here", "file": _outbox("hubspot", f"{call_id}.json", payload)}
    h = {"Authorization": f"Bearer {config.HUBSPOT_TOKEN}", "Content-Type": "application/json"}
    def _find(c, prop, val):
        s = c.post(f"{HS}/crm/v3/objects/contacts/search", json={"filterGroups": [{"filters": [{"propertyName": prop, "operator": "EQ", "value": val}]}]})
        s.raise_for_status()
        r = s.json().get("results")
        return r[0]["id"] if r else None
    try:
        with httpx.Client(timeout=20, headers=h) as c:
            contact_id = None
            for prop in ("phone", "email"):
                if props.get(prop) and not contact_id:
                    contact_id = _find(c, prop, props[prop])
            if contact_id:
                c.patch(f"{HS}/crm/v3/objects/contacts/{contact_id}", json={"properties": props}).raise_for_status()
            else:
                r = c.post(f"{HS}/crm/v3/objects/contacts", json={"properties": props}); r.raise_for_status(); contact_id = r.json()["id"]
            deal_id = None
            if verdict == "qualified":
                pipe, stage = _first_stage(c)
                deal.update({"pipeline": pipe, "dealstage": stage})
                d = c.post(f"{HS}/crm/v3/objects/deals", json={"properties": deal, "associations": [{"to": {"id": contact_id}, "types": [{"associationCategory": "HUBSPOT_DEFINED", "associationTypeId": 3}]}]})
                d.raise_for_status(); deal_id = d.json()["id"]
            n = c.post(f"{HS}/crm/v3/objects/notes", json={"properties": {"hs_note_body": _note_html(note), "hs_timestamp": datetime.utcnow().isoformat() + "Z"},
                       "associations": [{"to": {"id": contact_id}, "types": [{"associationCategory": "HUBSPOT_DEFINED", "associationTypeId": 202}]}]})
            n.raise_for_status()
        return {"mode": "live", "status": "synced" if deal_id else "contact and note synced (no deal: not qualified)", "contact_id": contact_id, "deal_id": deal_id}
    except httpx.HTTPError as e:
        return {"mode": "error", "status": f"HubSpot failed: {e}", "file": _outbox("hubspot", f"{call_id}.json", payload)}

# ---------------- Email + invite ----------------
def _resend(msg: EmailMessage):
    """Send through the Resend HTTP API. Without a verified domain Resend only delivers to the account owner's own address."""
    import base64
    body = msg.get_body(preferencelist=("plain",))
    to = [a.strip() for a in str(msg["To"]).split(",") if a.strip()]
    cc = [a.strip() for a in str(msg["Cc"]).split(",") if a.strip()] if msg["Cc"] else []
    text, subject = (body.get_content() if body else ""), str(msg["Subject"])
    if "resend.dev" in config.RESEND_FROM:
        # No verified domain yet: Resend only delivers to the account owner, so send a test copy there and say who it was meant for.
        owner = config.STUDIO_HEAD_EMAIL
        if [a for a in to + cc if a.lower() != owner.lower()]:
            text = f"[TEST COPY. Resend has no verified domain yet, so this went to you instead of: To {', '.join(to)}" + (f"; Cc {', '.join(cc)}" if cc else "") + "]\n\n" + text
            subject = "[Test copy] " + subject
        to, cc = [owner], []
    payload = {"from": config.RESEND_FROM, "to": to, "subject": subject, "text": text}
    hb = msg.get_body(preferencelist=("html",))
    if hb:
        html = hb.get_content()
        if text.startswith("[TEST COPY"):
            banner = text.split("\n\n", 1)[0]
            html = f'<div style="background:#FEF3C7;color:#92400E;padding:10px 14px;font:13px sans-serif">{banner.replace("<", "&lt;")}</div>' + html
        payload["html"] = html
    if cc: payload["cc"] = cc
    atts = [{"filename": p.get_filename() or "attachment", "content": base64.b64encode(p.get_payload(decode=True)).decode()} for p in msg.iter_attachments()]
    if atts: payload["attachments"] = atts
    r = httpx.post("https://api.resend.com/emails", json=payload, headers={"Authorization": f"Bearer {config.RESEND_API_KEY}"}, timeout=20)
    if r.status_code >= 300:
        raise RuntimeError(f"Resend {r.status_code}: {r.text[:200]}")
    return r.json().get("id")

def _send(msg: EmailMessage, kind, call_id, live=True):
    fname = f"{call_id}-{kind}.eml"
    if not (live and config.LIVE_INTEGRATIONS and config.EMAIL_LIVE):
        return {"mode": "outbox", "status": "saved locally, email not live here", "file": _outbox("email", fname, msg.as_string())}
    try:
        if config.RESEND_API_KEY:
            rid = _resend(msg)
            return {"mode": "live", "status": "sent via Resend", "id": rid}
        with smtplib.SMTP(config.SMTP_HOST, config.SMTP_PORT, timeout=20) as s:
            s.starttls()
            if config.SMTP_USER: s.login(config.SMTP_USER, config.SMTP_PASS)
            s.send_message(msg)
        return {"mode": "live", "status": "sent"}
    except Exception as e:
        return {"mode": "error", "status": f"Email failed: {e}", "file": _outbox("email", fname, msg.as_string())}

def designer_email(call_id, lead, result, slot, transcript_text, live=True, phone=None):
    from app import mailkit
    to = slot["designer_email"] if slot else config.STUDIO_EMAIL
    if str(to).endswith("@example.com"):   # placeholder designer address: send to the studio head until real ones are set in designers.json
        to = config.STUDIO_HEAD_EMAIL
    subj, body, html = mailkit.designer_email(call_id, lead, result, slot, transcript_text, phone)
    m = EmailMessage(); m["From"] = config.STUDIO_EMAIL; m["To"] = to; m["Cc"] = config.STUDIO_HEAD_EMAIL; m["Subject"] = subj
    m.set_content(body); m.add_alternative(html, subtype="html")
    r = _send(m, "designer", call_id, live); r.update({"to": to, "subject": subj, "body": body}); return r

def cal_book(call_id, lead, slot, result):
    """Book on Cal.com. Cal.com emails the client a confirmation with the calendar invite."""
    from datetime import timezone
    start = datetime.fromisoformat(slot["start"]).astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.000Z")
    body = {"start": start, "eventTypeId": int(config.env("CAL_EVENT_TYPE_ID")),
            "attendee": {"name": lead.get("name") or "Aangan enquiry", "email": lead["email"], "timeZone": "Asia/Kolkata", "language": "en"},
            "metadata": {"aangan_call_id": call_id},
            "bookingFieldsResponses": {"notes": f"{result.get('gist','')}\nPricing is confirmed by your designer at the consultation."}}
    r = httpx.post("https://api.cal.com/v2/bookings", json=body, timeout=30,
                   headers={"Authorization": f"Bearer {config.env('CAL_API_KEY')}", "cal-api-version": "2024-08-13", "Content-Type": "application/json"})
    r.raise_for_status()
    d = r.json()["data"]
    return {"mode": "live", "status": "booked on Cal.com, invite emailed by Cal.com", "booking_uid": d.get("uid"), "to": lead["email"],
            "subject": "Aangan design consultation, " + slot["label"], "body": f"Booking {d.get('uid')} confirmed for {slot['label']}. Cal.com sent the client a confirmation with the calendar invite."}

def client_message(call_id, lead, slot, result=None):
    """The studio's own warm confirmation email to the client (separate from the Cal.com calendar invite). Returns (EmailMessage, body)."""
    from app import mailkit
    subj, body, html = mailkit.client_email(lead, slot, result)
    m = EmailMessage(); m["From"] = config.STUDIO_EMAIL; m["To"] = lead.get("email") or config.STUDIO_HEAD_EMAIL; m["Subject"] = subj
    m.set_content(body); m.add_alternative(html, subtype="html")
    return m, body

def send_client_email(call_id, lead, slot, live=True, result=None):
    m, body = client_message(call_id, lead, slot, result)
    if not lead.get("email"):
        return {"mode": "draft", "status": "draft only: the caller's email was not captured, so the designer will collect it on the call-back", "subject": m["Subject"], "to": "(email not captured)", "body": body}
    r = _send(m, "client-email", call_id, live); r.update({"to": lead["email"], "subject": m["Subject"], "body": body}); return r

def client_invite(call_id, lead, slot, live=True, result=None):
    if not (lead.get("email") and slot):
        d = send_client_email(call_id, lead, slot, False, result)
        return {"mode": "skipped", "status": "no client email or no slot, so no calendar invite was sent. Draft message below.", "subject": d["subject"], "to": d["to"], "body": d["body"]}
    if live and config.LIVE_INTEGRATIONS and slot.get("source") == "cal" and config.env("CAL_API_KEY"):
        try:
            res = cal_book(call_id, lead, slot, result or {})
            mail = send_client_email(call_id, lead, slot, live, result)
            res.update({"to": lead["email"], "subject": mail["subject"], "body": mail["body"], "status": f"{res.get('status', '')}. Studio confirmation email: {mail['status']}"})
            return res
        except Exception as e:
            err = str(e).replace(config.env("CAL_API_KEY"), "***")
            fallback = _client_invite_ics(call_id, lead, slot, live)
            fallback["status"] = f"Cal.com booking failed ({err[:120]}); sent .ics email instead. " + fallback.get("status", "")
            return fallback
    return _client_invite_ics(call_id, lead, slot, live)

def _client_invite_ics(call_id, lead, slot, live=True):
    ics = build_invite(call_id, slot["start"], 60, "Aangan Studio: design consultation",
                       f"Free design consultation with {slot['designer']}. Reply to reschedule.", "Your site or video call, to be confirmed by your designer",
                       config.STUDIO_EMAIL, [(lead.get("name") or "Client", lead["email"]), (slot["designer"], slot["designer_email"])])
    m = EmailMessage(); m["From"] = config.STUDIO_EMAIL; m["To"] = lead["email"]; m["Cc"] = slot["designer_email"]
    m["Subject"] = f"Your Aangan Studio consultation, {slot['label']}"
    body = (f"Hello {lead.get('name') or ''},\n\nThank you for calling Aangan Studio. Your free design consultation is booked for {slot['label']} "
            f"with {slot['designer']}. The calendar invite is attached, and it will add itself to your calendar.\n\nPricing is confirmed by your designer at the consultation.\n\nAangan Studio")
    m.set_content(body)
    m.add_attachment(ics.encode(), maintype="text", subtype="calendar", filename="invite.ics", params={"method": "REQUEST"})
    r = _send(m, "client-invite", call_id, live); r.update({"to": lead["email"], "subject": m["Subject"], "body": body, "ics": ics}); return r

def escalation_email(call_id, lead, result, live=True):
    m = EmailMessage(); m["From"] = config.STUDIO_EMAIL; m["To"] = config.STUDIO_HEAD_EMAIL
    subj = f"ESCALATION, call back within 15 min: {lead.get('name') or 'caller'}"; m["Subject"] = subj
    body = f"{result['gist']}\n\nCall ID: {call_id}\nThe caller asked for a senior person."; m.set_content(body)
    r = _send(m, "escalation", call_id, live); r.update({"to": config.STUDIO_HEAD_EMAIL, "subject": subj, "body": body}); return r

def decline_email(call_id, lead, live=True):
    if not lead.get("email"):
        return {"mode": "skipped", "status": "no client email"}
    m = EmailMessage(); m["From"] = config.STUDIO_EMAIL; m["To"] = lead["email"]; m["Subject"] = "Thank you for calling Aangan Studio"
    body = "Thank you for calling. This may not be the right fit for us right now, but please reach out if your scope or timeline changes.\n\nAangan Studio"
    m.set_content(body); r = _send(m, "decline", call_id, live); r.update({"to": lead["email"], "subject": m["Subject"], "body": body}); return r


def hubspot_assign_note(call, designer, by="dashboard"):
    """Keep HubSpot in step when a lead is (re)assigned: a note on the deal and contact, and the designer named on the deal. Best effort."""
    hs = (call.get("integrations") or {}).get("hubspot") or {}
    if not (config.HUBSPOT_TOKEN and config.LIVE_INTEGRATIONS and hs.get("contact_id")):
        return "no HubSpot record"
    h = {"Authorization": f"Bearer {config.HUBSPOT_TOKEN}", "Content-Type": "application/json"}
    try:
        with httpx.Client(timeout=20, headers=h) as c:
            assoc = [{"to": {"id": hs["contact_id"]}, "types": [{"associationCategory": "HUBSPOT_DEFINED", "associationTypeId": 202}]}]
            if hs.get("deal_id"):
                assoc.append({"to": {"id": hs["deal_id"]}, "types": [{"associationCategory": "HUBSPOT_DEFINED", "associationTypeId": 214}]})
                d = c.get(f"{HS}/crm/v3/objects/deals/{hs['deal_id']}", params={"properties": "description"}).json().get("properties", {})
                body = re.sub(r"^Designer: .*\n?", "", d.get("description") or "")
                c.patch(f"{HS}/crm/v3/objects/deals/{hs['deal_id']}", json={"properties": {"description": f"Designer: {designer}\n{body}"}})
            c.post(f"{HS}/crm/v3/objects/notes", json={"properties": {"hs_note_body": f"Lead assigned to {designer} ({by}).", "hs_timestamp": datetime.utcnow().isoformat() + "Z"}, "associations": assoc}).raise_for_status()
        return "HubSpot updated"
    except Exception as e:
        return "HubSpot not updated: " + str(e)[:80]

def hubspot_move_stage(deal_id, stage_key):
    """Keep the HubSpot deal stage in step with the dashboard board. Best effort."""
    if not (deal_id and config.HUBSPOT_TOKEN and config.LIVE_INTEGRATIONS):
        return "no HubSpot deal"
    want = {"qualified": "Initial Consultation", "consultation_booked": "Initial Consultation", "site_visit": "Initial Consultation", "closed_won": "Closed Won"}[stage_key]
    h = {"Authorization": f"Bearer {config.HUBSPOT_TOKEN}", "Content-Type": "application/json"}
    try:
        with httpx.Client(timeout=20, headers=h) as c:
            r = c.get(f"{HS}/crm/v3/pipelines/deals"); r.raise_for_status()
            p = next((x for x in r.json()["results"] if x["id"] == "default"), r.json()["results"][0])
            st = next((x for x in p["stages"] if x["label"].lower() == want.lower()), None)
            if not st:
                return "stage not found in HubSpot"
            c.patch(f"{HS}/crm/v3/objects/deals/{deal_id}", json={"properties": {"dealstage": st["id"]}}).raise_for_status()
        return "HubSpot deal moved to " + want
    except httpx.HTTPError as e:
        return "HubSpot not updated: " + str(e)[:80]
