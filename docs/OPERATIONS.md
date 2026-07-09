# Averroes Tool — Operations Reference
**Authentication, cost controls and runtime configuration. Updated 8 July 2026.**
All settings change via env vars — always `--update-env-vars`, NEVER `--set-env-vars` (it wipes everything else).

---

## 1. Authentication (Google Sign-In)

**What it is:** Every page requires Google Sign-In restricted to **@averroescapital.com** accounts. Every API request carries a Google-signed ID token; the backend verifies signature, expiry and domain on each call. The bare backend URL returns 401 to strangers. Sessions last ~1 hour, then one click re-authenticates.

**How it's wired:**
- Backend: `backend/auth.py` — FastAPI middleware, token verification via google-auth (no external service, £0)
- Frontend: `frontend/src/components/AuthGate.tsx` wraps all three pages; `apiFetch` in `services/api.ts` attaches the token to every call
- OAuth Client ID (public identifier, safe in code): `890361705054-c5glgcq0029d5o447t114kl19hmvc8lo.apps.googleusercontent.com`
  - Lives in Google Cloud Console → APIs & Services → Credentials (project averroes-deal-origination)
  - Authorized JavaScript origin: `https://averroes-deal-frontend-sxi7lpwcnq-ew.a.run.app`
  - Baked as default in `auth.py`; env var `GOOGLE_OAUTH_CLIENT_ID` overrides (for rotation)

**Exempt paths (by design):** `/` (Cloud Run health), `/auth/config` (frontend bootstrap), `/ch-pdf/*` (links open in new tabs; documents are public Companies House filings anyway), CORS preflights.

**Common operations:**

| Task | How |
|---|---|
| Admit an external email | `gcloud run services update averroes-deal-backend --region=europe-west1 --project=averroes-deal-origination --update-env-vars ALLOWED_EMAILS=adviser@example.com` (comma-separate multiple) |
| Change allowed domain | `--update-env-vars ALLOWED_DOMAIN=newdomain.com` |
| Disable auth entirely | `--update-env-vars GOOGLE_OAUTH_CLIENT_ID=off` (any non-matching value breaks verification; to truly disable, set it empty in auth.py and redeploy) |
| Rotate the client ID | Create new OAuth client in Console (add the frontend origin!) → set `GOOGLE_OAUTH_CLIENT_ID` env var |
| "Not authorised" for a valid user | Check the email's domain; personal Gmail accounts are rejected by design — add via ALLOWED_EMAILS |
| Everyone locked out | Frontend can't reach `/auth/config` or origin mismatch on the OAuth client — check the JS origin matches the frontend URL exactly |

**Known caveat:** if the Cloud Scheduler job for `/smartfill-refresh-due` is set up, it will be blocked by auth (it can't sign in). Options: exempt that path in `auth.py` EXEMPT_PATHS, or use a Cloud Scheduler OIDC service account token (cleaner). Not yet done — flag when setting up the scheduler.

---

## 2. Cost controls (AI spend)

**Principle: paid search-grounding is never allowed.** Google gives 1,500 free grounded Gemini prompts/day; a shared, weighted daily budget is enforced server-side before any AI run. Weights are worst-case: SmartFill = 3 grounded calls, SmartEnrich = 2, InvestorFill = 1. Ordinary token costs (incl. CH PDF parsing) are linear and trivial (~1p per SmartFill) and are not capped.

| Control | Default | Env var | Meaning |
|---|---|---|---|
| SmartFill/Enrich runs per day | 450 | `DAILY_SMARTFILL_CAP` | Run cap across SmartFill + SmartEnrich |
| Grounded-call budget per day | 1,400 | `DAILY_GROUNDING_BUDGET` | Shared across SmartFill/SmartEnrich/InvestorFill; 100-call buffer under the free 1,500 |

Both reset at midnight UTC (counted from the activity_log). Bulk runners stop gracefully at either limit ("preserved for tomorrow"). Eligibility modals show used/remaining before any run starts.

**Worst-case daily AI spend under these controls:** ~£4.50 in tokens, £0 in grounding.

**SmartFill vs SmartEnrich:** first run of a company = SmartFill (full pipeline, ~5 calls). The button then becomes SmartEnrich (green ↻): refreshes only what's missing or stale — enrichment only if contacts absent, CH registry intel always (free), PDFs re-parsed only if a newer filing exists, re-score only if unscored or new financials. Typically 0–2 calls.

**Bulk SmartFill eligibility:** never-SmartFilled companies only, passing all three hard filters on stored data (UK/IE + tech + under £50M where determinable), trimmed to today's remaining quota. Too Large-band companies skip the 3 web-search scoring metrics in bulk mode (score individually on demand).

---

## 3. Key env vars (backend service)

| Var | Default | Purpose |
|---|---|---|
| `GEMINI_API_KEY` | (set) | All AI calls |
| `COMPANIES_HOUSE_API_KEY` | (set) | CH search/profile/PSC/charges/filings — free |
| `GOOGLE_OAUTH_CLIENT_ID` | baked default | Auth on/off + which OAuth client |
| `ALLOWED_DOMAIN` | averroescapital.com | Sign-in domain allowlist |
| `ALLOWED_EMAILS` | (empty) | Extra individual emails |
| `DAILY_SMARTFILL_CAP` | 450 | Run cap |
| `DAILY_GROUNDING_BUDGET` | 1400 | Free-tier grounding guarantee |
| `OUTREACH_SMTP_PASSWORD` | **not set** | Gmail App Password — outreach sends fail until configured |
| `OUTREACH_EMAIL` / `OUTREACH_NAME` | beatrice@averroescapital.com / Beatrice Carrara | Outreach sender |

View current values:
`gcloud run services describe averroes-deal-backend --region=europe-west1 --project=averroes-deal-origination --format='value(spec.template.spec.containers[0].env)'`

---

## 4. Related documents
- `Averroes_Product_Flow_Map_v2.pdf` — complete business rules (both engines, 10 pages)
- `Averroes_Revenue_Engine.pdf` — one-page revenue estimation rules
- Original technical handover (v2.0, July 2026) — credentials, deployment, troubleshooting
