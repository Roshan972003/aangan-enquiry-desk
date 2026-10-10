# Vaani agent setup

1. Agent name: Aangan Studio, Front Desk. Paste vaani/persona.md into the Persona prompt.
2. First message: "Good morning, Aangan Studio. This call may be recorded. How can I help you?" (use time-of-day variables if Vaani has them).
3. Custom tools (HTTP, POST, JSON). Base URL: https://aangan-enquiry-desk.vercel.app
   - check_availability: POST /api/vaani/tools/availability  (no inputs)
   - book_appointment:   POST /api/vaani/tools/book  inputs: name, email, start, notes
   - Header on both: X-Webhook-Secret: <VAANI_WEBHOOK_SECRET from .env, and set the same in Vercel>
   Turn OFF the built-in tools you are not using: send_whatsapp_message, cancel_appointment, reschedule_appointment, google_sheets_sync.
4. Post-call webhook: POST https://aangan-enquiry-desk.vercel.app/api/vaani/webhook with the same header. It should send the transcript.
5. Test with Start Web (RTC) Call, then check the dashboard for the call.
