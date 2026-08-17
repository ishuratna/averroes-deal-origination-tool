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
- Any code path that writes `status` MUST also write a `status_change`
  `activity_log` row — migrations AND the send path. Without it the row is
  invisible to reconciliation.

### Renaming a stored VALUE: gate the migration on evidence, never on the old value

This cost 18 wrong rows and is the subtlest failure in this codebase so far.

`/admin/stage-rename` mapped old `Contacted` → `Responded`, because under the old
scheme `Contacted` meant "they replied". Correct in principle. But the SEND PATH
had already been updated to write `Contacted` with its NEW meaning ("we emailed
them"), so every company emailed between the code change and the data migration
held a `Contacted` row that meant the new thing. The migration read the value,
could not know which meaning was intended, and promoted 18 freshly-emailed
companies to Responded. Every one had `we_sent = 1, all_inbound = 0`: not even an
autoresponder.

The rule that follows: when a migration renames a stored value whose meaning has
changed, the WHERE clause must test the underlying evidence, not just the old
value — here, `old status = 'Contacted' AND a genuine reply exists`. Rows failing
that test keep the new meaning. A value rename is only safe when no live code path
can already be writing the new meaning, and during a deploy there is always a
window where one can.

Corollary: such a migration must be re-runnable and must log `status_change`, so a
mistake is both visible and correctable. This one logged nothing, which is why
`reconcile_unreplied_contacted()` could not see the damage.

## 2b. THE OUTREACH LIFECYCLE (the whole loop, in order)

1. Outreach sent → `Qualified` → `Contacted`. Stamps `outreach_sent_at`,
   `contacted_at`, and BOTH activity rows (`outreach_sent` note +
   `status_change`).
2. Delivery is then VERIFIED, because SMTP success is not receipt
   (`_verify_delivery` in `main.py`, inside the email sync so it costs no extra
   IMAP call). Two independent failures, one consequence — back to `Qualified`:
   - BOUNCE: a mailer-daemon report came back (`services/delivery_check.py`).
     The address is dead, so `contact_email` is cleared and preserved in
     `bounced_email` and the contact waterfall finds a new one.
   - NEVER SENT: no `direction='sent'` row exists in `email_log` for the
     company. The sync reads Gmail's All Mail (which includes Sent), so absence
     means nothing was filed and nobody received it.
   Both guarded by `window_days` (only judge sends inside the scanned period —
   otherwise a shallow sync demotes the whole back catalogue) and `grace_hours`
   (Gmail files to Sent with a lag).
3. `NON_REPLY_CLASSES = ("out_of_office", "bounce")`. Neither may EVER count as
   a reply. A bounce counting as one was a real bug: it is inbound and not an
   autoresponder, so a mailer-daemon message promoted companies to Responded.
   The delivery check therefore runs BEFORE the reply rule in the sync, so
   bounces are already classified when the rule reads the log.
4. Genuine reply → `Responded`. Out-of-office → stays `Contacted`.

### Reminder thresholds (confirmed with Ishu, 14 Aug 2026)

- `Contacted`, waiting on them: **14 days** since our last email.
  OOO override: `length = days(our send date → their stated return date)`;
  if `length > 14` remind on `return date + 1`, else 14 days from our send.
  No date stated → 14 days. Floor is always 14; a past return date never
  shortens it. Implemented once in `ooo_detect.followup_due_date()` and
  cross-checked against the SQL by `tests_followup_ooo_sql.py`.
- `Responded`, ball with us: **7 days** since their last genuine message.
  ONE condition covers both halves of the rule — "we never wrote since they
  replied" and "they sent the last email and we have not answered" — because a
  company that replied and has heard nothing since necessarily has their message
  as the last one. Do not add a second rule for it.
- Parked companies (`not_fit_no_respond`, `declined_close`) never nag:
  intentional silence is not an oversight.

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

## 6a. Auth: EXEMPT_PATHS and the token check are a matched pair

- `auth.py` `EXEMPT_PATHS` / `EXEMPT_PREFIXES` skip Google sign-in, because
  Cloud Scheduler and a terminal cannot hold a browser session. The guard then
  lives INSIDE the handler: `_require_token(request)` in `main.py`.
- Exempt without a token check = OPEN TO THE INTERNET. This happened:
  `/delivery/verify` was exempted while its handler checked nothing, leaving an
  endpoint that rewrites company stages callable by anyone.
- Token check without exemption = unreachable from a terminal, and the failure
  ("Sign in required") is indistinguishable from a genuine auth error.
- `tests_auth_exempt.py` reads the live route table and the real handler source
  and enforces BOTH directions. Public-by-design paths are an explicit,
  documented allowlist in that test.
- A missing route also returns "Sign in required", because the middleware runs
  before routing. When a fresh endpoint says that, check the deploy first.
- Never exempt a path just because the browser cannot send a header on it. That
  is why `/ch-pdf/` was public and leaking which companies are in the pipeline;
  the fix was `dealApi.openChFilingPdf` fetching it with auth and opening a blob,
  not an exemption.
- A UI action that ops also needs runs as TWO routes on ONE handler: the plain
  path (session) and an `/admin/...` alias (token). Never a second copy of the
  logic.

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
