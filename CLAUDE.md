# Averroes Deal Origination Tool — Engineering Doctrine

Read this before building anything. These rules are binding for all future work.

## 1. Single source of truth (non-negotiable)

- BigQuery is the ONLY store of state: `averroes_deal_flow.targets` (companies),
  `investors`, `activity_log`, `email_log`, `qualification_config`.
- UI pages are VIEWS of those tables, never owners of state. The Universe table
  and the Pipeline board render the same `targets` rows filtered by status —
  nothing is ever copied between pages. If a feature needs data on two pages,
  it reads the same column via the same endpoint.
- Never introduce a second copy of a fact (no per-page caches persisted, no
  denormalised duplicates, no localStorage as a data store — localStorage is
  for view preferences only).
- Description rule: enrichment only replaces `description` if the new text is
  LONGER than the stored one ("longer wins"). Never overwrite good data with
  thinner data anywhere.

## 2. Same intent → same logic (shared components/endpoints)

- If a button with the same intended outcome appears in more than one place,
  it MUST call the same backend endpoint and share frontend logic:
  - Outreach draft/review/send → `OutreachModal` component
    (`frontend/src/components/OutreachModal.tsx`) + `outreachButtonState()`
    (`frontend/src/lib/outreach.ts`) on both Universe and Pipeline.
  - Sync Emails → `SyncEmailsButton` component, both headers.
  - Stage changes → PUT `/company/{name}/status` (never raw SQL from a page).
- Never fork logic per page. If styling must differ, share the logic and vary
  only the CSS.

## 3. Event truth

- Timestamps record when the EVENT happened, not when we processed it
  (e.g. email reply notes use the message's Date header, not sync time).
- Stage timestamps: `stage_entered_at` resets per move; per-stage first-entry
  columns (`qualified_at`, `contacted_at`, ...) are stamped once, never
  overwritten.

## 4. Cost guards (AI spend)

- NO AI calls at ingest. SmartFill/InvestorFill are the only AI layers.
- SmartFill runs the 3 hard filters FIRST (ungrounded); failures are gated to
  Not a Fit (+ `unfit_reason`) and never reach grounded enrichment/CH/scoring.
- All grounded calls go through the shared weighted daily budget
  (`_enforce_grounding_budget`) and are logged via `log_smartfill(kind)`.
  Never add a grounded Gemini call outside this accounting.

## 5. Internal Test row (source = 'Internal Test')

- All test-company exceptions key off `source = 'Internal Test'`, nowhere else:
  recipient forced to admin@averroescapital.com (draft + send), contact pinned,
  removal/lost auto-resets to a fresh Qualified state, send/advance guards bypassed.

## 6. Outreach content

- ZERO em dashes anywhere in `outreach_service.py` (instruction text included —
  the model mimics instruction style). Founder email structure v7 lives in
  `draft_outreach_email`; change structure only there.

## 7. Verification before push (hard-learned)

- `python3 -m compileall backend` (lazy imports hide f-string syntax errors),
  real `import main` + route asserts, `npx tsc --noEmit`.
- Deploy checks against the live service must cache-bust (`?v=N`) — the fetch
  layer caches responses.
- Cloud Run env vars: ALWAYS `--update-env-vars`, NEVER `--set-env-vars`.
