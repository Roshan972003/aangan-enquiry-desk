# Aangan Enquiry Desk: AI phone-enquiry system for Aangan Studio, Pune

MESA Case 03. An AI agent answers every incoming enquiry call within seconds, day or night, qualifies the caller against Nikhil's rubric, never quotes a price, hands qualified leads to a designer with full context, books a free consultation, syncs to HubSpot and shows the studio an executive dashboard.

- **Live dashboard:** https://aangan-enquiry-desk.vercel.app
- **Code:** https://github.com/Roshan972003/aangan-enquiry-desk
- **Scope:** incoming phone calls only (as in the brief).

## How a call flows

```
Caller -> Vaani voice agent (answers in seconds, 24x7)
       -> webhook to this app (FastAPI on Vercel)
       -> Gemini judges the call: 5-criteria verdict, gist, mood, key moments
       -> Neon Postgres stores the call, transcript, audit trail
       -> qualified: designer chosen by workload and area
            -> designer email (gist, answers, "don't re-ask" list, one-click Accept / Listen)
            -> Cal.com books the nearest free slot, client gets the calendar invite
       -> every caller: HubSpot contact + note; qualified: HubSpot deal
       -> dashboard updates (KPIs, kanban, escalations, forecast)
```

## Requirements from the case and where they live

| Requirement | Implementation |
|---|---|
| Answer within 5 minutes, day and night | Vaani agent picks up in seconds; dashboard shows median/slowest answer, % under 5 min, after-hours calls |
| Qualify per Nikhil's rubric | `knowledge/qualified.md` + `knowledge/services.md` drive the prompt; Gemini returns a structured 5-criteria verdict (`app/prompts.py`) |
| Never quote prices | The price table is never given to the model; `app/guard.py` blocks any price figure before it is spoken; the deflection line steers to the free consultation; dashboard counts deflections |
| Handoff to designers with context | `app/integrations.py designer_email`: gist, who/what/where/timeline, price question status, "don't re-ask" list, transcript, signed Accept and Listen links |
| Book a consultation + invite to client | Cal.com v2 (`app/slots.py`): booking made post-call from the caller's preferred time; Cal.com emails the invite. `.ics` fallback |
| HubSpot sync | Contact for every caller, deal only for qualified leads (amount is an internal estimate), call note with dashboard link |
| Executive dashboard | `app/static/dashboard.html`: AI digest, 4 KPI cards, kanban (drag or arrows), escalation feed, pricing-deflection tracker, response speed, designer workload, mood heatmap, off-hours revenue ticker, forecast, win chance, first-touch timers, NL search, call drawer with transcript, waveform markers and audit log, CSV/PDF export, system health |
| Running cost and ROI | Per-call cost (Gemini tokens, telephony, speech) and hours/money saved on the dashboard; rates and project-value assumptions are editable settings |

## Accuracy check

`replay.py` runs the 20 real September 2026 call transcripts (`data/enquiries_sep2026.txt`) through the same pipeline. Result against my reading of the rubric: **20 of 20 verdicts match** (judgement calls are listed in `replay.py`).

## Stack

FastAPI (Python), static HTML/JS dashboard, Gemini (`gemini-3.5-flash`), Vaani (voice), Neon Postgres (SQLite locally), Cal.com, HubSpot, Vercel (+ weekly cron), GitHub. Emails via Resend.

## Run locally

```bash
cp .env.example .env     # add GEMINI_API_KEY; LIVE_INTEGRATIONS=0 keeps HubSpot/email/Cal.com off
./run.sh                 # dashboard http://localhost:8000, call simulator /simulator
.venv/bin/python replay.py
```
`MOCK_LLM=1` runs the whole pipeline without any API key (canned replies, for plumbing tests).

## Honest status and decisions

- **Not yet run end to end by a real phone call.** The Vaani agent, webhook, post-call booking and HubSpot sync were each verified (Vaani connectivity test, real Gemini + Cal.com + HubSpot runs). A live voice call and an inbound phone number still need to be set up in Vaani.
- **Email runs through Resend** (`RESEND_API_KEY`, Gmail SMTP still supported). Delivery verified. Without a verified sending domain Resend only delivers to the account owner's address, so all designer and escalation emails currently go to `STUDIO_HEAD_EMAIL` (the owner). To email real designers and clients, verify a domain at resend.com/domains and set `RESEND_FROM`.
- **Designers are placeholders** in `designers.json` (name, email, optional `areas`, `commercial`). Placeholder addresses fall back to the studio head.
- **Pricing rule wins over the brief.** The brief mentions per-sq-ft quotes, but `pricing.md` forbids any number, so the agent never quotes one.
- **Open question for Nikhil:** `services.md` caps commercial work near 3,000 sq ft with no minimum; a 500 sq ft minimum appears only in transcript T18.
- **Mood is read from the transcript text, not vocal tone.** Win chance is a transparent rules-based estimate, not a trained model. Cost figures are estimates from configured rates, not vendor bills.
- `LIVE_INTEGRATIONS=0` locally is a safety switch so tests never touch the real HubSpot, Cal.com or inbox.
