# Aangan Enquiry Desk (phone channel)

AI phone agent for Aangan Studio. Scope: incoming phone calls only.

## Run
1. `cp .env.example .env` and set `GEMINI_API_KEY` (already stored if you pasted it).
2. `./run.sh` then open http://localhost:8000 (dashboard) and http://localhost:8000/simulator (call it).
3. Optional: `.venv/bin/python replay.py` runs the 20 real September phone transcripts through the pipeline and scores the verdicts.
   `MOCK_LLM=1` runs everything without a key (canned replies, for plumbing tests only).

## What is real now
- Agent prompt built from knowledge/services.md, qualified.md and the prohibitions in pricing.md (the price table is never given to the model).
- Code guard (app/guard.py) blocks any price figure before it is spoken. The approved deflection line replaces it.
- Post-call: 5-criteria verdict, gist, handoff note (Gemini), then HubSpot, designer email, client calendar invite (.ics).
- Without HubSpot or SMTP credentials, every output is written to outbox/ and shown in the UI. Add HUBSPOT_TOKEN / SMTP_* to .env to go live.
- Dashboard reads the SQLite call log: volume, answer speed, outcomes, hours, cost to run, Gemini brief.

## Not done yet
- Real phone number: /api/twilio/voice and /api/twilio/turn are an untested stub. Vaani key is stored in .env but not wired (need its API docs).
- Calendar: Cal.com (event type 'Aangan design consultation', 60 min). Free slots come from Cal.com and the booking is made there, so Cal.com emails the client the invite. Falls back to a mock calendar plus .ics email if Cal.com fails. Tested live.
- HubSpot and SMTP code paths are written but untested against live accounts.
- designers.json holds placeholders. Slots come from a mock calendar (app/slots.py); swap for Google Calendar free/busy.
- Costs are estimates from the rates in .env, not vendor bills.

## Decisions worth knowing
- The agent never quotes pricing. Nikhil's brief asks for per-sq-ft quotes, but his pricing.md forbids any number, so the rule wins.
- services.md caps commercial at ~3,000 sq ft with no minimum. The 500 sq ft minimum appears only in transcript T18 (front desk). Ask Nikhil.
