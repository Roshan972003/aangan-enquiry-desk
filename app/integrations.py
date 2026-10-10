"""HubSpot, email and calendar invite. Each runs live when credentials exist, otherwise writes to outbox/."""
import json, smtplib, uuid
from email.message import EmailMessage
from datetime import datetime
import httpx
from app import config
from app.ics import build_invite

def _outbox(kind, name, content):
    try:
        d = config.OUTBOX / kind; d.mkdir(parents=True, exist_ok=True)
        p = d / name; p.write_text(content if isinstance(content, str) else json.dumps(content, indent=2, ensure_ascii=False))
        return str(p.relative_to(config.ROOT))
    except OSError:
        return "stored in the call record (read-only filesystem)"

# ---------------- HubSpot ----------------
HS = "https://api.hubapi.com"

def hubspot_sync(call_id, lead, result, call_meta, live=True):
    props = {"firstname": (lead.get("name") or "Unknown caller"), "phone": call_meta.get("phone") or "",
             "email": lead.get("email") or "", "city": "Pune"}
    props = {k: v for k, v in props.items() if v}
    note = (f"Aangan phone enquiry {call_id}\nVerdict: {result['verdict']}\nGist: {result['gist']}\n"
            f"Scope: {lead.get('scope')} | Area: {lead.get('location')} | Size: {lead.get('sqft')} sq ft | Timeline: {lead.get('timeline')}\n"
            f"Decision maker: {lead.get('decision_maker')} | Referral: {lead.get('referral')}\n"
            f"Uncertainties: {', '.join(result.get('uncertainties') or []) or 'none'}")
    payload = {"contact": props, "deal": {"dealname": f"{lead.get('name') or 'Caller'} - {lead.get('location') or 'Pune'} - {lead.get('scope') or 'enquiry'}",
                                          "pipeline": "default", "dealstage": "appointmentscheduled" if call_meta.get("slot") else "qualifiedtobuy"},
               "note": note, "meeting_slot": call_meta.get("slot")}
    if not (live and config.HUBSPOT_TOKEN):
        return {"mode": "outbox", "status": "saved locally, HubSpot token not set", "file": _outbox("hubspot", f"{call_id}.json", payload)}
    h = {"Authorization": f"Bearer {config.HUBSPOT_TOKEN}", "Content-Type": "application/json"}
    try:
        with httpx.Client(timeout=20, headers=h) as c:
            contact_id = None
            if props.get("phone"):
                s = c.post(f"{HS}/crm/v3/objects/contacts/search", json={"filterGroups": [{"filters": [{"propertyName": "phone", "operator": "EQ", "value": props["phone"]}]}]})
                s.raise_for_status()
                if s.json().get("results"):
                    contact_id = s.json()["results"][0]["id"]
                    c.patch(f"{HS}/crm/v3/objects/contacts/{contact_id}", json={"properties": props}).raise_for_status()
            if not contact_id:
                r = c.post(f"{HS}/crm/v3/objects/contacts", json={"properties": props}); r.raise_for_status(); contact_id = r.json()["id"]
            deal = c.post(f"{HS}/crm/v3/objects/deals", json={"properties": payload["deal"], "associations": [{"to": {"id": contact_id}, "types": [{"associationCategory": "HUBSPOT_DEFINED", "associationTypeId": 3}]}]})
            deal.raise_for_status()
            n = c.post(f"{HS}/crm/v3/objects/notes", json={"properties": {"hs_note_body": note.replace("\n", "<br>"), "hs_timestamp": datetime.utcnow().isoformat() + "Z"},
                       "associations": [{"to": {"id": contact_id}, "types": [{"associationCategory": "HUBSPOT_DEFINED", "associationTypeId": 202}]}]})
            n.raise_for_status()
        return {"mode": "live", "status": "synced", "contact_id": contact_id, "deal_id": deal.json()["id"]}
    except httpx.HTTPError as e:
        return {"mode": "error", "status": f"HubSpot failed: {e}", "file": _outbox("hubspot", f"{call_id}.json", payload)}

# ---------------- Email + invite ----------------
def _send(msg: EmailMessage, kind, call_id, live=True):
    fname = f"{call_id}-{kind}.eml"
    if not (live and config.SMTP_HOST):
        return {"mode": "outbox", "status": "saved locally, SMTP not set", "file": _outbox("email", fname, msg.as_string())}
    try:
        with smtplib.SMTP(config.SMTP_HOST, config.SMTP_PORT, timeout=20) as s:
            s.starttls(); 
            if config.SMTP_USER: s.login(config.SMTP_USER, config.SMTP_PASS)
            s.send_message(msg)
        return {"mode": "live", "status": "sent"}
    except Exception as e:
        return {"mode": "error", "status": f"SMTP failed: {e}", "file": _outbox("email", fname, msg.as_string())}

def designer_email(call_id, lead, result, slot, transcript_text, live=True):
    d = slot["designer"] if slot else "Designer pool"
    to = slot["designer_email"] if slot else config.STUDIO_EMAIL
    subj = f"Qualified: {lead.get('scope') or 'enquiry'} in {lead.get('location') or 'Pune'}, {int(lead['sqft']) if lead.get('sqft') else '?'} sq ft"
    body = (f"GIST\n{result['gist']}\n\n"
            f"WHO: {lead.get('name')} ({lead.get('decision_maker') or 'decision-maker not confirmed'})\n"
            f"WANTS: {lead.get('scope')}\nWHERE: {lead.get('location')}   SIZE: {lead.get('sqft')} sq ft\nTIMELINE: {lead.get('timeline')}\n"
            f"BUDGET: {lead.get('budget_volunteered') or 'not mentioned'}   REFERRAL: {lead.get('referral') or '-'}\n"
            f"PRICE QUESTION: {'asked, deflected to consultation' if result.get('price_asked') else 'not asked'}\n"
            f"BOOKED: {slot['label'] if slot else 'not yet, call the client'}\n\n"
            f"DON'T RE-ASK: {', '.join(result.get('dont_reask') or [])}\n"
            f"UNCERTAIN: {', '.join(result.get('uncertainties') or []) or 'nothing'}\n"
            f"START WITH: {result.get('first_question_for_designer')}\n\nCALL ID: {call_id}\n\n--- TRANSCRIPT ---\n{transcript_text}")
    m = EmailMessage(); m["From"] = config.STUDIO_EMAIL; m["To"] = to; m["Cc"] = config.STUDIO_HEAD_EMAIL; m["Subject"] = subj; m.set_content(body)
    r = _send(m, "designer", call_id, live); r.update({"to": to, "subject": subj, "body": body}); return r

def client_invite(call_id, lead, slot, live=True):
    if not (lead.get("email") and slot):
        return {"mode": "skipped", "status": "no client email or no slot, nothing to invite"}
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
