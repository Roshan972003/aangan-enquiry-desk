from datetime import datetime, timedelta, timezone
IST = timezone(timedelta(hours=5, minutes=30))

def _utc(dt):
    return dt.astimezone(timezone.utc).strftime("%Y%m%dT%H%M%SZ")

def _esc(s):
    return s.replace("\\", "\\\\").replace(";", "\;").replace(",", "\\,").replace("\n", "\\n")

def build_invite(uid, start_iso, minutes, summary, description, location, organizer, attendees):
    start = datetime.fromisoformat(start_iso)
    if start.tzinfo is None:
        start = start.replace(tzinfo=IST)
    end = start + timedelta(minutes=minutes)
    lines = ["BEGIN:VCALENDAR", "VERSION:2.0", "PRODID:-//Aangan Studio//Enquiry Desk//EN", "METHOD:REQUEST", "BEGIN:VEVENT",
             f"UID:{uid}@aangan.studio", f"DTSTAMP:{_utc(datetime.now(timezone.utc))}", f"DTSTART:{_utc(start)}", f"DTEND:{_utc(end)}",
             f"SUMMARY:{_esc(summary)}", f"DESCRIPTION:{_esc(description)}", f"LOCATION:{_esc(location)}",
             f"ORGANIZER;CN=Aangan Studio:mailto:{organizer}"]
    for name, mail in attendees:
        lines.append(f"ATTENDEE;CN={_esc(name)};ROLE=REQ-PARTICIPANT;RSVP=TRUE:mailto:{mail}")
    lines += ["BEGIN:VALARM", "TRIGGER:-P1D", "ACTION:DISPLAY", "DESCRIPTION:Consultation tomorrow", "END:VALARM",
              "BEGIN:VALARM", "TRIGGER:-PT1H", "ACTION:DISPLAY", "DESCRIPTION:Consultation in 1 hour", "END:VALARM",
              "END:VEVENT", "END:VCALENDAR"]
    return "\r\n".join(lines) + "\r\n"
