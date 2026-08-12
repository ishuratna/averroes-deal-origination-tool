# Kickoff Brief: KSA Medical & Aesthetics Clinics Origination Tool

Give this note to your AI as the founding instruction. It reuses the exact architecture,
tech stack and hard-learned rules of the Averroes Deal Origination Tool, re-aimed at
finding, researching and reaching medical/aesthetics clinics in Saudi Arabia.

---

## 1. What to build (one paragraph)

A sourcing-and-outreach CRM for medical and aesthetics clinics in Saudi Arabia.
It discovers clinics from many sources (scrapers, pasted URLs, uploaded files),
stores every clinic once in a master database, enriches each clinic with AI that
answers a fixed set of questions we define (stored as structured fields, never prose),
finds and verifies contact details, drafts personalised outreach for review-before-send,
tracks replies through a pipeline (kanban), and reports honest funnel analytics.

## 2. Tech stack (same as the proven tool)

- Backend: Python FastAPI, single `main.py` hub + `services/` modules
- Frontend: Next.js/React, all styles in `globals.css` (styled-jsx is unreliable)
- Database: BigQuery, ONE dataset (e.g. `ksa_clinics`) — tables: `clinics`,
  `contacts_log`, `activity_log`, `email_log`, `analytics_ledger`, `qualification_config`
- Hosting: Cloud Run (region me-central2 Dammam if latency matters, else europe-west1),
  Cloud Build auto-deploy from GitHub, env vars ALWAYS `--update-env-vars`
- AI: Gemini with search grounding, behind a shared weighted DAILY BUDGET counter
- **Use a fresh GCP project.** Never share a project with another tool (hard lesson).

## 3. The entity: a clinic, not a company

Design the master table for clinics from day one. Suggested field families:

- Identity: name (Arabic + English), brand/chain name, website, Instagram handle,
  Google Maps place ID, city (Riyadh/Jeddah/Dammam/…), district, branches count
- Regulatory: MOH license number and status, commercial registration (CR) number
- Services: category (dermatology, plastic surgery, dental aesthetics, laser, slimming…),
  services list, doctors count, treatment rooms/chairs if discoverable
- Traction proxies: Google rating + review count, Instagram followers, booking
  platforms present, price signals — in KSA, social traction IS the revenue proxy
- Ownership: single-doctor owner vs group/chain vs hospital-affiliated; owner name
- Contact: owner/medical director name, mobile/WhatsApp, email, Instagram DM
- Pipeline: status, stage timestamps, outreach draft/sent, reply fields, fit score

## 4. Sources (build in this order, probe scrapability FIRST)

1. **Google Maps/Places** — the backbone. Category + city sweeps; dedupe by place ID.
2. **Wathq API (api.wathq.sa)** — Saudi commercial-registry data; this is your
   Companies House equivalent for ownership and CR verification. Verify access terms.
3. **MOH / health-licensing directories** — license validity; verify what is public.
4. **Booking/aggregator platforms** (e.g. Vezeeta and local equivalents — verify
   which cover KSA aesthetics) — services, prices, doctor rosters.
5. **Instagram/Snapchat/TikTok** — discovery + traction; KSA aesthetics lives here.
6. **Universal ingestion from day one**: a Source Agent (paste ANY URL → AI extracts
   clinics → preview → confirm) and Smart Upload (any CSV/XLSX/PDF → AI maps columns
   by meaning → preview → confirm). These made the original tool source-agnostic.

## 5. Enrichment = your questions as fields

Write the question set FIRST (like: chain or independent? which services? how many
doctors? licensed? who owns it? review sentiment? expansion signals?). Each question
becomes a typed column. The AI enrichment layer ("ClinicFill"):

- Runs cheap HARD FILTERS first (geo = KSA, category = medical/aesthetics, size
  threshold) — failures are gated out before any expensive grounded call
- Then ONE grounded research pass fills the answer fields, source-stated facts only
- All numbers computed in code; AI writes prose only. Estimates labelled as estimates,
  never mixed with verified data (separate columns)

## 6. Outreach & CRM

- Channels: email AND WhatsApp (in KSA, WhatsApp Business is often the real channel;
  email is secondary). Design outreach records channel-agnostic from day one.
- Bilingual drafts (Arabic + English), reviewed by a human before every send.
- First message: NO ask — introduce, show you know their clinic, open door to talk.
- Verified contacts only: guessed emails must pass a verifier (Hunter.io) before
  storage; phone numbers only from published sources.
- Pipeline stages: Identified → Qualified → Engaged (contacted) → Responded →
  Meeting → Due Diligence/Deal → Won/Lost. Reply classification into action buckets
  with suggested replies (review-and-send, never auto-send).

## 7. Non-negotiable engineering rules (copy verbatim into the new repo's CLAUDE.md)

1. BigQuery is the ONLY store of state; UI pages are views of the same tables.
   Never copy data between pages; localStorage is for view preferences only.
2. Same intent → same endpoint + shared component. Never fork logic per page.
3. Event truth: timestamps record when the EVENT happened, not when we processed it.
   Per-stage first-entry timestamps are stamped once, never overwritten.
4. NO AI calls at ingest. Enrichment is the only AI layer, behind a daily budget.
5. Merge never overwrite: new data fills gaps; longer descriptions win; NULL never
   wipes stored values; verified beats estimated.
6. Preview-before-ingest for ALL AI ingestion (Source Agent, Smart Upload).
7. Every long endpoint streams heartbeats (spaces every 10s, final line = JSON) —
   hostile networks kill silent connections. Frontend parses the last line.
8. Analytics on an immutable ledger keyed by EVIDENCE (message actually sent /
   reply actually received), so ever-counts survive deletion and stage relabelling.
9. Keep a permanent Internal Test clinic (source = 'Internal Test'): all sends
   force-route to your own inbox; auto-resets after each test cycle.
10. Verify before every push: `python3 -m compileall backend`, real `import main`
    + route asserts, `npx tsc --noEmit`. Deploy checks must cache-bust.
11. Never commit secrets. Env vars via `--update-env-vars` only. Rotate any key
    that ever touches a chat or a file.
12. Ask the human before assuming. Show drafts and mappings before deploying them.

## 8. Build order (each step shippable)

1. Master table + universe page + manual/file upload (no AI)
2. Google Maps sweep for 2–3 cities + dedupe
3. Hard filters + qualification config
4. ClinicFill enrichment (question set → fields) + fit score
5. Contact finder + verifier
6. Outreach drafts + review-and-send + pipeline kanban
7. Reply sync + action buckets + follow-up queues
8. Source Agent + Smart Upload (universal ingestion)
9. Analytics ledger + dashboard
10. Wathq/registry integration for ownership depth

---
*Derived from the Averroes Deal Origination Tool (July 2026). Same stack, same
doctrine, different hunting ground.*
