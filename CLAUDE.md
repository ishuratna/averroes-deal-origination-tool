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
  - Check stages → `ReplyRuleButton` component, Pipeline + Responded headers.
  - Stage changes → PUT `/company/{name}/status` (never raw SQL from a page).
- Never fork logic per page. If styling must differ, share the logic and vary
  only the CSS.

## 2a. THE REPLY RULE (stage definitions are fixed)

- `Qualified` = promoted from the Master Universe. No outreach sent yet.
- `Contacted` = we emailed them, no genuine reply has come back yet.
- `Responded` = we emailed them AND they genuinely replied.
- An out-of-office autoresponder is NOT a reply. It leaves (or returns) the
  company in Contacted and only defers the follow-up reminder.
- "Genuinely replied" has exactly ONE definition, `_genuine_reply_sql()` in
  `bq_handler.py`: an `email_log` row with `direction='received'`,
  `entity_type='company'`, `classification != 'out_of_office'`. Every caller
  uses that fragment. Never inline a second copy of the predicate.
- The decision is `classify_reply_stage()` — a module-level PURE function, so it
  is testable without BigQuery. `reconcile_reply_stages()` is its only caller.
- The Pipeline's Responded column and the Responded page MUST select on the same
  condition (status). This was violated once: the board counted
  `status='Responded'` while `get_responded()` counted any inbound message in
  `email_log` including autoresponders, so the two could never reconcile and the
  page showed companies the board did not. If the counts can differ, it is a bug.
- Stages past Responded (Meeting / DD / Offer / Won / Lost) carry real work and
  are NEVER changed automatically.
- Three outcomes, not two. A wrong row is demoted automatically only when
  email-sync made the move (the machine correcting itself). When a PERSON moved
  it, or nothing records how it got there, it is returned as
  `needs_confirmation` and the UI asks. "Keep it" stamps `reply_exempt_at` /
  `reply_exempt_by` and the rule skips that company permanently.
- NEVER gate a correction on the presence of an `activity_log` row. That mistake
  cost real data integrity: `reconcile_unreplied_contacted()` INNER JOINed the
  activity log and demoted only rows moved by `email-sync`. Of 21 wrongly
  Responded companies, 20 had no activity row at all (the raw-SQL stage-rename
  migration logged nothing), so the join silently dropped exactly the rows that
  needed fixing and every preview came back empty while the board stayed wrong.
  The activity log records what happened, not what is true now.
- Any migration that writes `status` with raw SQL MUST also write an
  `activity_log` row, or it creates rows no reconciliation can reason about.

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
  the model mimics instruction style). Founder email structure v9 (NO ask at
  all in the first email: no call, no meeting, no request for details or
  documents; just "would love to learn more" plus an open collaboration
  invitation. Details/overview requests happen only after the founder shows
  interest, likely under NDA) lives in
  `draft_outreach_email`; change structure only there.

## 7. Verification before push (hard-learned)

- `python3 -m compileall backend` (lazy imports hide f-string syntax errors),
  real `import main` + route asserts, `npx tsc --noEmit`.
- Deploy checks against the live service must cache-bust (`?v=N`) — the fetch
  layer caches responses.
- Cloud Run env vars: ALWAYS `--update-env-vars`, NEVER `--set-env-vars`.

## 8. Git remotes + credentials (hard-learned)

- `origin` has ONE fetch url and TWO push urls: `averroescapital/...` (canonical)
  and `ishuratna/...` (mirror). One `git push` goes to both.
- NEVER embed a token in a remote url. It lands in `.git/config` in plaintext,
  GitHub's secret scanning revokes it, and every push then dies with
  "Invalid username or token". Credentials live in the macOS keychain
  (`git config --global credential.helper osxkeychain`).
- `git remote set-url` only changes the FETCH url. When pushurls exist they
  override it silently, so a "fixed" url can still push to the old target. Debug
  with `git remote -v` and read which line says `(push)`; fix with
  `git config --unset-all remote.origin.pushurl` then re-add.
- The PAT must have access to BOTH repos. A fine-grained token scoped to the org
  only will push to `averroescapital` and fail on the mirror.
- Pushes now come from Ishu's terminal, not the sandbox: the keychain is not
  reachable from the Linux sandbox, so Claude commits and Ishu pushes.
