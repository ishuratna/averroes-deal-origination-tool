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
  `entity_type='company'`, and `classification NOT IN NON_REPLY_CLASSES`
  (`out_of_office`, `bounce`). Every caller uses that fragment — the pipeline, the
  Responded page, the follow-up queue AND the analytics ledger. Never inline a
  second copy of the predicate: the analytics layer held its own version without
  the filter, and that inflated the headline response rate with autoresponders.
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
     THE SUPERSEDE RULE (Cezanne HR, 28 Aug 2026): a bounce invalidates only
     the send it bounced against. If a NEWER outbound send exists
     (`latest_send_times`), the bounce is stale history and must not pull the
     company back - the newest-INBOUND guard cannot see this, because a
     re-send is outbound and the old bounce stays the newest inbound forever.
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
- ONE FOLLOW-UP ONLY (Ishu, 24 Sep 2026: "I do not want that I follow up
  on the follow up"). The Follow up button shows while exactly ONE email has
  ever gone to the company; at two or more it reads "Followed up", the
  template is never offered again (the click is a blank compose in the same
  thread), the card never goes red for it, and the `waiting_on_them`
  reminder skips it (`status = 'Contacted' AND sent_count >= 2`). Responded
  and later are untouched: a reply in a live conversation is not a follow-up.
- THE COUNT COMES FROM email_log, NOT THE ROW. `outreach_sent_at` is stamped
  only by the tool's send path; Ishu had been following up from his inbox,
  and the card kept offering the follow-up again because the row could not
  see those sends. The sync files every outbound message from Gmail's All
  Mail (the tool's own sends included), so `bq_handler._sent_agg_sql()`
  derives `sent_count` and `last_sent_at` per company and every pipeline,
  slim-universe and full-profile row carries them; `hasFollowedUp` /
  `lastSentAt` / `owesReply` in `lib/outreach.ts` read them first and fall
  back to the timestamp gap only when a row arrives without them. Never
  store the count: it is a fact about email_log (doctrine 1).

## 2c. THE INVESTOR LOOP (LPs mirror founders; one machinery, two tables)

- Stages (`investor_handler.INVESTOR_STAGES`): Identified → Researched
  (InvestorFill done) → Contacted (we emailed) → Responded (they GENUINELY
  replied) → Meeting → Committed; Passed and Talk Later are PARKED and require
  a `PARK_REASONS` bucket (the same list companies use). Meeting/Committed and
  the parked stages are never changed automatically.
- `investors` carries the SAME outreach column names as `targets`
  (`outreach_draft_*`, `outreach_drafted_at`, `outreach_sent_at`,
  `contacted_at`, `last_reply_at`, `stage_entered_at`, `park_reason*`) so the
  shared frontend logic (`lib/outreach.ts`, `OutreachModal` with
  `entity="investor"`, `InvestorStageControl`) applies unchanged. Never write
  an investor-specific copy of the button state or the modal.
- ONE writer for investor status: `investor_handler.update_status` (stamps
  `stage_entered_at`, first-entry columns, park reason, and the notes audit
  trail). Sends go through `record_send` (forward-only: Identified/Researched →
  Contacted, later stages untouched). Drafts are persisted once; the fallback
  template is never persisted.
- SENDER PROFILES (`outreach_service.sender_profile`): founder = Bea's outreach
  mailbox; investor = a SEPARATE mailbox from `INVESTOR_OUTREACH_EMAIL` /
  `INVESTOR_OUTREACH_NAME` / `INVESTOR_SMTP_PASSWORD` (+ `INVESTOR_SIGNATURE_*`),
  with its own signature. Until those are set (TBU #166) the investor profile
  FALLS BACK to the founder mailbox VISIBLY (`fallback=True`, every From line
  says "founder mailbox; investor mailbox not configured yet") - per Ishu,
  8 Sep 2026, so the loop can be exercised now. `sync_mailbox` reads every
  configured mailbox once (deduped by address); direction is detected per
  mailbox.
- TWO INVESTOR DESKS, ROUTED BY REGION (Ishu, 14 Sep 2026, "rule number
  one"): Ellie runs the Middle East pipeline from `INVESTOR_*`; Bea runs UK,
  Europe and everyone else. Her desk IS the founder outreach mailbox BY
  DESIGN (Ishu, same day: "Bea's secondary email is the same I use for
  companies, so use the same"), so `sender_profile("investor_intl")` returns
  the founder mailbox with `fallback=False`; `INVESTOR_INTL_*` exists only to
  split it later without a code change. Bea's signature is used whenever the
  message leaves Bea's address, whichever desk asked.
  `outreach_service.investor_sender_kind(investor)` is the ONE decision
  (region rollup == Middle East, or a GCC tag -> "investor"; everything else,
  INCLUDING unknown, -> "investor_intl"): unknown goes to Bea because a wrong
  guess there costs a forwarded email while the other way invites a Zurich
  office to Riyadh. The draft, follow-up, compose, the send path (which
  derives the desk from the ROW, never from the caller), the sync mailbox
  list and the bounce pass all go through it. `bounced_address(exclude=...)`
  takes every one of our addresses, because a bounce report quotes the
  sender and the sender must never be read as the dead address.
- THE INVESTOR LOOP HAS THE WHOLE LIFECYCLE (14 Sep 2026), mirrored from 2b:
  OOO stamps `ooo_until/ooo_note` on `investors` via `stamp_ooo` (same
  columns, same clearing of reply state), `_apply_ooo` handles both entity
  types and pulls a wrongly-Responded investor back to Contacted (Researched
  if never sent); the follow-up SQL defers on `ooo_until` for both tables.
  `_investor_delivery_pass` applies BOUNCE and NEVER SENT
  (`investor_handler.unverified_sends`, twin of the company query, same
  window and grace guards) and is included in the dry-run preview. The reply
  rule runs on the investors table through `_genuine_reply_sql('investor')`
  (the one predicate, parameterised, never copied) and the same PURE
  `classify_reply_stage`; every demotion is ASKED because investor moves are
  not attributed. `ReplyRuleButton entity="investor"` on the Investor
  Pipeline. `retire_to_universe` is the investor "remove": back to Researched
  or Identified, send stamps reset, notes and email log kept.
- The Internal Test INVESTOR (`INVESTOR_TEST_NAME`, source = 'Internal Test',
  created/reset via POST /admin/investors/test-seed) has its recipient forced
  to `INVESTOR_TEST_RECIPIENT` (Ishu) on draft, follow-up, compose and send,
  keyed off `source` exactly like the test company.
- The reply rule is the same one: a received `email_log` row for the investor
  whose classification is not in `NON_REPLY_CLASSES` moves Contacted →
  Responded (in the sync and its self-heal pass); autoresponders and bounces
  never do. Follow-ups use the SAME `/followups` endpoint with
  `entity=investor` and the same 14 / 7 day thresholds; never a second SQL.
- THE GATE (`ai/investor_gate.py`, PURE, zero AI, zero network) decides whether
  an investor enters the pipeline AT ALL, and runs BEFORE any grounded call,
  exactly as SmartFill's hard filters do for companies (doctrine 4). TWO
  filters (Ishu, 11 Sep 2026, revised the same day):
    1. REACH. Based in the UK/IE, Europe or the GCC, OR a stated mandate
       covering the UK, Ireland or Europe. EITHER ROUTE QUALIFIES. I first
       excluded `geo_preferences`, calling it a bug that a Singapore office
       with a European mandate passed; Ishu overruled that and is right, since
       someone already writing cheques into UK companies is warmer than a
       neighbour, and reach is solvable by a call while mandate is not. A GULF
       mandate alone does NOT qualify: we raise there, we do not invest there.
    2. SIZE. `AUM_CEILING_USD_M = 1000` (Ishu's choice of the tighter option):
       an investor puts 1 to 5 per cent of assets into one private position and
       ignores anything under about half a per cent, so at USD 1bn our GBP 10M
       is roughly 1 per cent and material, at USD 5bn it is 0.2 per cent and
       beneath notice. `AUM_FLOOR_USD_M = 10`, deliberately low: a UHNWI with
       USD 10M can comfortably write GBP 200K and is exactly our audience.
       A STATED ticket range beats any assets proxy, because it is their number.
  TYPE IS NOT A FILTER. It was, for about an hour. Ishu removed it: "lets not
  completely eliminate institutions, lets only put Size as the criteria." Size
  already excludes every institution worth avoiding, by arithmetic rather than
  keyword, and the type filter also threw out the small pension that CAN write
  GBP 2M. `looks_institutional` survives as INFORMATION for the card and for
  `lp_priority`'s ranking: a preference, not a wall.
  UNKNOWN IS NOT A FAILURE. A missing country, AUM or ticket PASSES, flagged,
  and goes through to research to find that very information (Ishu: "unknown
  will go through smartfill by itself"). So the gate runs TWICE: once on the
  stored row (skipped when too bare to judge, always skipped for the Internal
  Test investor), and again on the researched facts, which is usually the first
  time we know the country. A post-research refusal PARKS the investor but
  keeps every field we paid for. The reported reason is SIZE FIRST, because
  that is the criterion Ishu kept.
  `GET /investors/gate-audit` (+ `/admin/` alias) previews the whole universe,
  DEFAULTS TO A DRY RUN, and reports qualifying rows split by which email they
  need; `apply=1` parks the refused through `park_bulk`.
- BULK WRITES ARE ONE STATEMENT, NOT A LOOP. Applying the gate to 1,292 rows
  via `update_status` meant three sequential BigQuery queries per row (read,
  update, note), about an hour, and Cloud Run cuts a request at ten minutes:
  Ishu watched curl die at 10:00 with a partial apply (11 Sep 2026).
  `park_bulk` is the BULK TWIN of `update_status`: same columns, same
  `stage_entered_at` reset, same audit line appended to `notes`, computed in
  SQL from the row's OLD status (BigQuery evaluates SET against pre-update
  values) via `UPDATE ... FROM UNNEST(@names) WITH OFFSET`. Protected stages
  are excluded INSIDE the WHERE so a re-run is safe. The two must never drift;
  `tests_investor_gate.py` checks they write the same fields. Any future bulk
  stage change follows this shape.
- RESEARCH TRIAGE (`ai/investor_triage.py`, PURE, zero AI) orders the queue
  that bulk InvestorFill walks. The gate audit (11 Sep 2026) showed 7,413
  investors with NO location: bare names mined from cap tables, correctly let
  through as "unknown". Researching them in arbitrary order would spend a month
  of grounding budget confirming that most are venture funds. THE NAME ALREADY
  SAYS A GREAT DEAL, so `name_shape` reads it, in an order where every step was
  learnt from a misfire:
    fund first        "Family Ventures LLP" is a fund, not a family
    legal form next   "Vodafone Group Plc" is a plc, not a group
    sector/public     "Kuwait Investment Authority" is an authority, not "invest"
    wanted words      family, holding, office, trust; Arabic particles as WHOLE
                      WORDS (`\b(al|bin|bint)\b`), because " al " inside
                      "capital " put Balderton Capital and Legal & General at
                      the front as Arabic family names
    person shape      titled (Sheikh, Sir, Dr) is strong; two plain words is
                      only "not obviously a firm" (Hambro Perks, Local Globe,
                      Praxis Rock all pass and are all firms) and earns a bump,
                      not a promotion
  Warmth adds: `source_companies` (a mined name backing ONE of our companies
  reaches `research_first`, which is most of them), `network_tags`, a contact
  on file. A WARM FUND stays `research_last`: the name already answered.
  `research_last` (fund- and corporate-shaped) is HELD BACK from the queue
  unless `include_funds=1`, and COUNTED so nobody thinks rows vanished.
  IT IS A QUEUE ORDER, NEVER A REFUSAL: the module touches no status, and a
  test asserts it never can. Only the gate parks.
  `/investorfill/eligible` now applies, in order: skip parked, the PitchBook
  negatives, the GATE on stored facts (refusable now = never worth a call),
  then triage, and returns `queue_head` with the reasoning for the first 25.
  `?region=gcc` (the "Gulf only" toggle INSIDE the Bulk InvestorFill modal,
  default on) restricts the queue to Gulf-based (gate `email_strategy ==
  "gcc"`) or GCC-tagged investors: GCC FIRST, UK/EU later (Ishu, 11 Sep 2026),
  because the Gulf email is the only one written. It used to be a red GCC chip
  on the filter bar; Ishu had it removed the same day, and rightly: it scoped a
  RUN, not the table, and sat among filters that scope the table. A control
  lives where its effect is.
- CONTACTABLE, NOT TIER, IS THE FILTER (Ishu, 11 Sep 2026: "just use
  contactable vs not as a filter and fit score remains as such"). The tier
  folded two questions into one letter (how good a fit, and can we write to
  them), and a filter on it answered neither cleanly. `contactableBucket()` in
  `types/index.ts` mirrors `_readiness` in `lp_priority.py` exactly (an email
  with an @; a name alone is not contactable). The score and the chip stay;
  the table still sorts by score.
- CITY FILTER (Ishu, 11 Sep 2026) on both investor pages, options formatted
  "City, Country" by `cityLabel()` in `types/index.ts`, the same helper the
  Location column uses, so the filter and the column can never disagree.
  Only investors with a city ON RECORD appear in the list (no city, no
  option), and the list is narrowed to the selected Regions so it stays
  readable. Region is where they roughly are; City is where to have the
  coffee.
- FILTER ROLLUPS are computed ONCE, server-side, from the gate's geography
  sets and served on every `/investors` row as `region_bucket` (Middle East |
  UK & Ireland | Europe | Global | Unknown) and `mandate_buckets` (any of UK,
  Ireland, Europe, Middle East, Other, from `geo_preferences` ONLY). The
  Universe and Pipeline pages read those fields; neither holds its own
  `regionOf` on raw `hq_country` any more, which is why the UAE was not
  rolling up into Middle East (Ishu, 11 Sep 2026). "Unknown" stays apart from
  "Global": 7,565 rows have no location, and folding them into Global would
  make Global look like a finding. `REGION_BUCKETS` / `MANDATE_BUCKETS` fix
  the display order in both `investor_gate.py` and `types/index.ts`.
- PLACE NAMES MATCH AS WHOLE WORDS, via `_mentions()` in `investor_gate.py`,
  used by the gate, the filter rollups and `lp_priority`. Substring
  containment put Romania in the Middle East ("r-oman-ia") and Ukraine in the
  UK ("uk-raine") in all three (11 Sep 2026). Never test a place set with
  `any(g in blob ...)` again.
- WHICH EMAIL depends on HOW they qualified, and only ONE is written.
  `email_strategy` is `gcc` (the v3 copy: "based in London and Riyadh", "a
  coffee in London or Riyadh"), `uk_eu` (TBU #174) or `mandate_only`
  (TBU #175). `lp_recipient_warning` returns the mismatch FIRST, before any
  gatekeeper warning, because inviting a Zurich family office for coffee in
  Riyadh is a worse mistake than writing to an assistant.
- NEVER INFER AN EMAIL ADDRESS (Ishu, 11 Sep 2026: "we will either find them or
  keep them out"). `_found_email_only` enforces it IN CODE and not only in the
  prompt, because an instruction is a request and this is a rule: anything the
  research marked other than `found`, anything with no confidence stated, and
  any general enquiries inbox is DROPPED, keeping the person's name and title,
  which are most of the value. A guessed address bounces, and a bounce burns
  the one approach we get with that investor.
- THE CHEQUE BAND is GBP 200K to 10M (Ishu, confirmed 11 Sep 2026), defined
  ONCE as `TICKET_MIN_USD_M` / `TICKET_MAX_USD_M` in `ai/investor_gate.py` and
  imported by `lp_priority`. The geography sets live there too, for the same
  reason: when the filter and the ranking disagreed about the band, we would
  have emailed the wrong people a correct number.
- INVESTORFILL (`ai/investor_fill.py`) exists ONLY to find the facts the gate
  and `lp_priority` need, plus the decision maker. It scores nothing that
  matters: the four 0-1 scores feed the older informational `lp_fit_score`.
  Three defects, all found by reading the module against its CONSUMERS rather
  than on its own (11 Sep 2026), and none of which threw an error:
    * UNITS. It asked for GBP millions while the gate compares USD thresholds.
      A GBP 900M office read as 900 against a 1000 ceiling and PASSED (USD
      1.17bn, should fail); a stated GBP 250K ticket read as 0.25 against a
      0.26 floor and was REFUSED. Every money field is now USD, the JSON keys
      SAY so (`aum_usd_m`, `ticket_min_usd_m`), the prompt carries worked
      conversions, and `aum_converted_from` records what was converted so a
      wrong conversion is auditable. A unit mismatch does not fail loudly, it
      quietly qualifies the wrong people.
    * MISSING FIELDS. `lp_priority` puts its HEAVIEST weight (0.30, co-invest
      appetite) on `strategy_preferences` / `other_preferences` /
      `policy_description`, and the research returned NONE of them, so every
      researched investor scored the 0.3 default on the dimension that decides
      the tier. Also absent: `num_pe_commitments`, `num_vc_commitments`,
      `last_commitment_date` (recency). All returned now.
    * `geo_preferences`, WITHOUT WHICH THE MANDATE ROUTE IS DEAD. The gate
      qualifies an investor whose mandate covers the UK/IE or Europe wherever
      they sit, but research never returned the field, so only a PitchBook row
      that happened to carry it could ever qualify that way.
  `tests_investor_fill.py` derives the required set FROM `lp_priority`'s source
  and fails if the research stops returning any of it, so this cannot silently
  regress when the ranking changes.
  IDENTITY (doctrine 4a, previously missing on the investor side): the research
  echoes `name_as_found` and `_identity_ok` requires ONE shared distinctive
  word, generic furniture ("Capital", "Family Office", "Partners") excluded. A
  mismatch returns an error and writes NOTHING. Deliberately lighter than the
  company guard because investor names are short and formulaic.
  WRITES ARE FILL-ONLY for the PitchBook text fields: an AI paraphrase must
  never replace the investor's own published wording.
- WHO TO WRITE TO is structural, not a guess (`TARGET_LADDERS`, `target_brief`).
  A single family office is decided by its CIO or head of investments, a multi
  family office by its head of private markets, a wealthy individual by
  themselves or by whoever runs their private office, a syndicate by its lead.
  ORDER MATTERS in `target_kind`: "Angel Syndicate" contains "angel" but is led
  by someone deciding for a group, so syndicate is tested before individual.
  The ladder is passed INTO `investor_fill` rather than left to the model, and
  the prompt forbids returning an assistant, an analyst or a general inbox when
  a decision maker can be found.
- PRIORITY (`ai/lp_priority.py`, pure, zero AI) is the ONE ranking of
  investors for the raise: deal-by-deal co-investment at GBP 200K-10M per LP
  (Ishu, 9 Sep 2026, band confirmed 11 Sep). Weighted fit (co-invest appetite
  0.25, ticket 0.20, SIZE 0.15, home geography UK/IE + GCC 0.15, software
  affinity 0.10, recency 0.075, readiness 0.075) plus a capped warm-path boost
  from `network_tags` and portfolio overlap; tier A needs a contactable
  principal. Stored as `priority_score/tier/details`, recomputed by
  `investor_handler.write_priorities` after InvestorFill, uploads and tag
  edits (never edited by hand). `lp_fit_score` is the older fund-raise fit
  and stays informational. Warm-path sources: PitchBook GCC export and network
  lists uploaded WITH a tag; public registers (DIFC/ADGM/CMA) are bot-protected
  and off limits (TBU #168).
- SIZE IS TWO FACTS, AND THE RANKING MUST OBEY THE FILTER (Ishu, 11 Sep 2026:
  "the scoring is not following the filters", Mubadala at 98 "does not make
  sense"). How it got there: GCC geography 1.0, a fresh PitchBook date, a named
  contact and a GCC tag, while a USD 250bn book entered the score only as a
  soft 0.4 FALLBACK used when no ticket was stated. A weight can never hold a
  sovereign down against four perfect dimensions, so three rules now apply:
    1. THE TICKET is the primary size signal, scored by the SHARE OF THEIR
       RANGE inside ours (floor 0.4 for any overlap). "5M to 500M" overlapped
       our band and scored 1.0; it is 0.41 now, because the top of a range is
       where the attention is.
    2. HOW BIG THEY ARE is its own dimension (`_size_score`, via the gate's
       `size_of`), never a fallback, and SMALLER IS BETTER: "if the cheque
       range is the same between two investors, the smaller investor is more
       interesting." 1.0 at USD 50M, 0.6 at the USD 1bn ceiling, 0 at 50bn.
    3. THE HARD LAYER: `check_size` from the gate, the one definition of "too
       big", caps the score at `SIZE_FAIL_CAP = 25` and forces tier C. A row
       the gate could not park (protected stage, missing apply, stale score)
       still cannot reach the top of the list. `details.size_gate` records
       `capped_from` so the card shows what the weights alone would have said.
  The gate changed with it: a stated ticket WAIVES THE FLOOR (a USD 5M angel
  writing 250K cheques is real) but NEVER THE CEILING. Earlier doctrine said "a
  stated ticket beats any assets proxy"; that let a sovereign through on a
  published 5M minimum, and Ishu's instruction is that it never will be
  interested whatever it publishes. Changing weights requires recomputing every
  stored score (`POST /admin/investors/recompute-priority`); scores are
  derived, never edited. `GET /admin/investors/gate-audit?name=` diagnoses one
  row: stored fields, gate verdict and a fresh score side by side.
- LP email STRUCTURE v3 (Ishu, 11 Sep 2026, replacing v2) lives ONLY in
  `draft_lp_outreach_email`. v2 was a good letter and the wrong instrument: it
  explained the firm in full before anyone had agreed to talk. THE EMAIL IS NOT
  THE PITCH. Its only job is to open a door that a coffee or a call then walks
  through. Six rules, all enforced by `tests_investor_loop.py`:
    1. VERY SHORT and personal, around 90 words. A test fails it over 120.
    2. `AVERROES_LP_WHY_NOW` is the reason for writing and the part every
       earlier version lacked: exits lined up for next year, more UK companies
       being acquired, the Gulf investor group widening. News earns a reply;
       "we are expanding our investor base" is a fact about us and earns none.
    3. NAME AND TITLE COME FROM THE MAILBOX (`_lp_role_line` reads
       `sender_profile`), never a constant, so the email can never claim a
       sender it is not. Bea/Partner today, Ellie/Director of Investor
       Relations once TBU #166 is configured.
    4. Addressed to a DECISION MAKER. `lp_recipient_warning` returns a warning
       (never a block, a gatekeeper is sometimes the only way in) for a
       gatekeeper title, an info@ style inbox, or no contact at all.
    5. NO corporate profile, no attachment, and NO OFFER TO SEND ONE. Fuller
       information follows a conversation. This is why v2's "I can send a short
       note on Averroes first" was removed, and a test forbids its return.
    6. The ask is a coffee in London or Riyadh, or a named fifteen minutes.
  ONE AI-written "why them" sentence may sit after paragraph 1, and is DROPPED
  ENTIRELY when nothing specific is known: v2 fell back to "Given your activity
  in private markets", which is the filler these rules exist to delete.
  The 14-day follow-up is two sentences, repeats the same ask and offers
  nothing new. Zero em or en dashes. Copy changes happen in the
  `AVERROES_LP_*` constants and nowhere else.
- NOT YET IN THE TOOL, and the biggest gap in this loop: rule 6 of Ishu's
  outreach doctrine is that email opens the door and a HUMAN TOUCHPOINT (call,
  referral, meeting) walks through it. Nothing prompts or records that
  touchpoint; a reply merely moves the investor to Responded.
- The email states NO cheque figure, deliberately: it is a door opener, and a
  number invites a decision before a conversation.

## 2d. THE RESPONDED FLOW (one flow, two calls; the letters keep their values)

- Ishu (Nurture) -> associates, THURSDAY call -> partners, MONDAY call (Ishu,
  22 Sep 2026: "there's only two calls ... no high fit low fit bifurcation
  now ... it should pass from me to associates to partners"). Every company
  walks the same two steps; nothing forks on fit or size.
- `_responded_group` in `main.py` is the ONE derivation of which list a
  company sits in (nurture / assoc_review / assoc_pending / partner_review /
  partner_assigned / progressed / talk_later / closed); the page, its header
  stats and the weekly review all read it. `tests_responded_groups.py` pins
  every branch.
- STORED TRACK VALUES DID NOT CHANGE. `B` = with the associates, `A` = with
  the partners; v3 read them as "good fit, still small" and "high fit, right
  size". Renaming the letters to match the new words would have meant a
  value migration while live code writes the old ones, which is exactly the
  failure in 2a. Only the labels moved. `assignment_ready_at` is a retired
  v3 staging stamp: still on the row, read by nothing.
- A PASS CLEARS THE OWNER. The Thursday and Monday calls decide who takes a
  company; a stale owner left on the row must never let it skip the
  discussion and land as already taken. Owner is set only by the call's
  decision (an associate from the Thursday list, Bea from the Monday list).
- Talk later wakes back into Nurture (not into a routing list): what a
  company needs after six months asleep is Ishu's fresh read.

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

## 3a. ENRICHMENT NEVER UNDOES WORK (the stage guard)

- `qualify_company_with_gemini` reads the RECORD. It cannot know an email was
  sent, a reply came back, or a meeting happened, so for a company we are
  mid-conversation with it correctly answers "Qualified". The SmartFill write
  took that as an instruction: `status = @status`, unconditional.
- That cost a live conversation. FoundIt! (10 Sep 2026) sat at Responded. A
  SmartFill re-run whose ONLY purpose was to attach the CH number we had just
  found reset it to Qualified and reset `stage_entered_at`. It logged nothing,
  so the demotion was invisible to reconciliation and to the activity log, and
  the company simply disappeared from the Responded queue.
- `bq_handler.WORK_DONE_STAGES` (Contacted, Responded, Meeting, DD, Offer, Won,
  Lost) are stages that record work ACTUALLY DONE. Enrichment writes everything
  else it learns and leaves `status`, `stage_entered_at` and `unfit_reason`
  alone on those rows: `status = CASE WHEN status IN UNNEST(@protected) THEN
  status ELSE @status END`. Only a PERSON moves a company out of them.
- `Qualified` and `Not a Fit` are deliberately NOT protected: nothing has
  happened yet in the first, and re-judging the second is the whole point of
  re-running enrichment on a rejected company.
- The guard lives at the single UPDATE, not at the call sites: manual SmartFill,
  bulk, nightly auto and `/smartfill/run-by-number` all land on it. Adding a
  new caller must not require remembering this rule.
- And per 2a, that write now logs `status_change` when the stage really moves.
  It never did, which is exactly why the loss left no trace.
- `tests_smartfill_stage_guard.py` enforces all of the above against the real
  handler source.

## 4aa. The officer gate (a person we know, found on the register)

- A one-word company name is UNMATCHABLE by string similarity: "foundit" sits
  inside FOUNDIT PROPERTY, FOUNDIT! GROUP, FOUND IT LONDON and hundreds more,
  so `_name_gate` correctly returns `core-ambiguous` for every candidate and
  `extract_ch_financials` refuses them all. That refusal is right and it also
  loses real matches: FoundIt! IS 09690801, and the proof is that Warren Cowan,
  the contact on our row, is an active director there. Its accounts sat unread.
- `_officer_verify` therefore runs as a FOURTH disambiguator in
  `_pick_best_match`, but ONLY when the top pick is below the financials bar or
  a different company is within 8 points of it. Never otherwise: a confident
  name match must cost zero extra register calls (enforced by a test).
- It NEVER rescues a candidate that failed `_name_gate`. Officer agreement does
  not marry two unrelated names; it breaks the tie between candidates already
  in contention. Name AND person, never person alone.
- The SURNAME carries the identity, not the forename. Surname + a matching
  forename or shared initial = `full` -> gate becomes `officer-verified`
  (admitted alongside exact/exact-core/contains, confidence
  `verified-officer`). Surname alone = +20 tie-break and NO promotion, because
  a common surname on a same-named company is a coincidence we cannot rule out.
  A shared forename alone is worth nothing.
- Absence is not evidence: a candidate nobody matches keeps exactly the score
  and gate level it had. This gate can only ADD matches.
- Names come from `person_names_for_match`, researched contact FIRST and
  `contact_name` LAST — the send path can overwrite `contact_name` with
  whoever replied, who may never have been near the register. A wrong name
  matches nothing, so a bad guess costs one free API call, never a wrong
  company. Single-token and bracketed (test row) names are refused outright.
- Officers and PSC names arrive in DIFFERENT formats ("COWAN, Warren James"
  vs "Mr Warren James Cowan"). `_person_tokens` normalises both or the
  comparison is meaningless. Check directors first, then the PSC register: a
  founder off the board often still holds significant control.

## 4ab. Reading accounts: how deep, and trusting a tag too far

- DEPTH (Ishu, 10 Sep 2026: latest + four prior). N accounts filings yield N+1
  distinct years, because each filing carries its own year AND the prior year's
  comparatives and consecutive filings overlap by one. `ACCOUNTS_FILINGS_PARSED
  = 5` therefore gives six years; `ACCOUNTS_FILINGS_FETCHED = 8` leaves slack
  for a changed year end or a skipped filing; `YEARS_KEPT = 8` in `ch_history`.
  `revenue_y1..y3` remain a legacy projection of the newest three.
- The old cap of three was set when every filing cost a Gemini call. It does
  not any more, so `PDF_FALLBACK_MAX_FILINGS = 1`: only the LATEST filing may
  fall back to the AI PDF read. An older filing with no iXBRL is SKIPPED, and
  the PDF is downloaded only for the AI fallback or to store filing 1 in GCS.
  Deepening history must never quietly multiply AI spend.
- `scale` IS NOT UNIVERSALLY SAFE. It exists so money can be reported in
  thousands or millions. FOUNDIT! GROUP (09690801, FY2025) tags
  `AverageNumberEmployeesDuringPeriod` as `unitRef="Pure" decimals="2"
  scale="-2"` around a printed `10`: the filing software mirrored `decimals`
  onto a Pure-unit fact, where it means nothing. We computed 0.1 and `int()`
  took it to ZERO, so a company with GBP 9.5M revenue showed no staff and its
  employee-growth score collapsed.
- `_headcount()` is the rule: if scaling turns a whole number of people into a
  fraction below one, the SCALE is the error, not the number, so use the figure
  as printed. Narrow on purpose - a positive scale is honoured (2,000 staff is
  real), a genuine tagged 0 survives (dormant holding companies exist), and a
  missing fact stays None rather than becoming 0. Money is untouched.
- The general lesson: a machine-readable tag is only as good as the filer.
  Sanity-check any derived figure against what a human reading the document
  would see. `tests_ixbrl_headcount.py` pins this against the real filing.

## 4ae. Fiscal years: one convention, one window, and a figure with no year is not placed

- THE CONVENTION (`services/fiscal_year.py`, PURE, and its mirror in
  `types/index.ts`): a fiscal year is named after the CALENDAR YEAR THE
  PERIOD ENDS IN. A year to 31 Mar 2026 is FY26; to 31 Dec 2025 is FY25.
  It is the only rule under which a Gain row saying "FY2025" and a Companies
  House period ending 2025-03-31 land in the same column (Ishu, 17 Sep 2026:
  "correctly match it with the financial year with the Companies House
  accounts"). Labels are `fy_label` / `fyLabel`, never a second format.
- THE WINDOW is five years ending in the CURRENT calendar year (FY22..FY26 in
  2026), served by `GET /company/{name}/financials` as `window` with each
  year's period end and `running`/`closed` status. The card DRAWS it; it
  does not decide it. The last column is nearly always empty, ON PURPOSE:
  Ishu wants the running year visible as a placeholder so a table ending a
  year early is never mistaken for one that is current. Later years are
  appended only when the store holds figures for them (a founder's budget).
- PLACING AN IMPORT LABEL (`place_label`): an ISO date is itself; a label
  with a year and no day ("FY2025 (Gain, reported)", "FY2024") is placed at
  the company's OWN year end when Companies House has told us what it is
  (`year_end_of`: the most common month/day across filed periods, NEVER from
  an import label), else 31 December, and the cell's evidence records the
  assumption. A label with NO year ("latest (Inven)") is NOT placed. A
  guessed year is a wrong year with a confident face; the figure stays on
  the record, the endpoint lists it under `unplaced`, and the table says so.
- ONE CELL PER (YEAR, METRIC) ON THE CARD (`pickCell`): actual before budget
  before forecast, Companies House before a document before an import,
  latest period end last. The store keeps every cell; the view chooses, and
  the rest is on hover. This is why a Gain "FY2025" placed on a 31 March
  year end beside the filing is harmless: the filing shows.
- THE STORE HOLDS THE WHOLE HISTORY. `cells_from_history` writes every
  `ch_history` period (up to six years, doctrine 4ab); `cells_from_columns`
  alone lost FY22 for a company whose filings went back that far, because the
  y-columns are a three-slot projection. `cells_from_record` = both. The
  read path (`ensure_financials_seeded`) tops the store up FILL-ONLY on every
  read: a (period, metric) already held is never touched, a figure on the
  record with no cell is added, no write is issued when nothing is missing.
- THE EBITDA COLUMN IS PLACED FOR GAIN ONLY. `estimated_ebitda` is a
  misnamed grab-bag: Gain writes reported EBITDA into it, Inven "latest"
  EBITDA with no year, and the old Excel upload a REVENUE estimate. Only the
  Gain row states the year it reports, so only a "(Gain" label places it,
  beside its revenue. Anything else would put a revenue estimate in an
  EBITDA row of a Companies House year.
- The chart and the grid read the SAME columns from the SAME store
  (`finColumns`). The legacy `revenue_y1..y3` fallback in the chart is gone;
  those columns are a projection of the store and cannot disagree with it.

## 4af. The register says more than we were reading (the Mark to Market audit)

- Ishu compared Arcus Global on Mark to Market with our card (25 Sep 2026).
  Nearly everything they show is DERIVED from Companies House filings we
  already download; the gap was computation and presentation, not access.
  Three things were built from it, all traceable to a filing, none a vendor
  number: the FUNDING LADDER, the DEBT LINE, and the WIDER ACCOUNTS READ.
- THE FUNDING LADDER (`services/funding_ladder.py`, `ch_funding_rounds`).
  An SH01 states shares allotted, nominal value and amount paid per share,
  then the total shares in issue after. So raised = shares x paid, price =
  raised / shares, post-money = total after x price, pre = post - raised.
  READ FROM THE FILING TEXT (pymupdf), ZERO AI; a scanned form falls back to
  one ungrounded Gemini read, bounded (`MAX_AI_FALLBACKS_PER_RUN = 3`) and
  logged as kind `sh01`. A NOMINAL ISSUE (paid <= nominal: options, bonus
  shares, founder subscription) is listed, marked, and excluded from raised
  and from any valuation. The ledger is incremental (`filings_seen`); each
  filing is read once, ever, and each round carries its own `reading` so
  the whole ladder is rebuilt from the filings' figures, never from a stored
  derivation. `column_fills` writes `total_raised_m`, `last_financing_*`,
  `last_financing_valuation_m`, `last_valuation_date` FILL-ONLY: a Gain,
  PitchBook or document figure is never replaced by a derivation.
  `tests_funding_ladder.py` checks the derivation against Mark to Market's
  independent reading of Arcus Round 6 (GBP 3.23 a share, ~GBP 7.27m post).
  THE PARITY CHECK (same day, against the live row) found three faults,
  none of which threw: (1) THE E-FILED LAYOUT. SH01(ef) prints "Number
  allotted" for every class IN ISSUE in the statement of capital too, so
  section 3 is cut at "Statement of Capital" or 2.3m shares read as a new
  allotment. Labels and values sit on separate lines; all whitespace is
  folded before matching. (2) A DUPLICATE FILING. The May 2019 return was
  accepted on 29 May and again on 5 Jun; two readings, one allotment,
  total raised doubled. `_fold_duplicates` keeps one per (date, shares,
  price) and notes the twin; both ids stay in `filings_seen`. (3) SMALL
  ISSUES. 9,000 shares at GBP 1 and 333,083 at GBP 0.10 are option
  exercises priced above nominal; the first had set
  `last_financing_valuation_m = 2.25`. Under `SMALL_ISSUE_GBP` (50k) or
  under `OPTION_PRICE_RATIO` (a quarter) of the last round's price is a
  "small issue": listed, never a round, never a valuation. A statement
  total below the shares allotted derives no valuation either.
  THE LADDER MAY CORRECT ITS OWN FILL AND NOBODY ELSE'S. `ledger["fills"]`
  records what `column_fills` wrote; `_ladder_write` in main.py is the one
  place a ladder result becomes SET clauses (SmartFill, SmartEnrich,
  `POST /admin/funding-ladder/{name}`), and a column still holding exactly
  our earlier derivation counts as empty. `LEDGER_VERSION` bumps trigger a
  free rebuild from the stored readings on the next run. A v1 ledger with
  no `fills` is credited with whatever v1's rules would have written.
  THE PDF FALLBACK READS THE WIDER SET TOO (`CH_HISTORY_VERSION = 3`).
  Arcus's FY25 accounts have no iXBRL rendition, so the wider fields were
  empty for exactly the year that mattered; the Gemini prompt now asks for
  the same keys and derives EBITDA the same way (`ebitda_basis` says
  "read from the filed PDF"). Re-parsing a v2 row costs one AI call only
  when the latest filing is a scan, and only when SmartEnrich runs on it.
  Diag `?step=ixbrl` walks past scanned filings to the newest tagged one.
- THE DEBT LINE (`get_charges_detail`, `ch_charges`): the charges register
  in full (lender, created, status, satisfied). The card names the lenders
  NOW and flags a REFINANCING, a charge satisfied within 30 days of a new
  one appearing (Arcus: SaaS Capital out, Gilion in, August 2026). Net debt
  is borrowings minus cash from the accounts, derived in the view.
- THE WIDER READ (`ixbrl_accounts._CONCEPTS`): operating profit (EBIT),
  depreciation, amortisation, staff costs, director remuneration,
  borrowings, trade debtors and creditors. EBITDA is DERIVED (EBIT + D + A)
  only when operating profit is tagged, and `ebitda_basis` says so. Two
  rules learnt writing it: (1) DIMENSIONAL CONTEXTS. Director pay is tagged
  per director AND in total; "first fact wins" took a slice. A fact whose
  context carries an explicitMember/typedMember is a slice and only ever a
  fallback; a total for the same period replaces it. (2) A tagged ZERO is
  kept for borrowings (debt-free is a fact) and dropped elsewhere (a nil is
  "unknown"): `_HISTORY_KEEP_ZERO` in doc_smartfill. The keys travel via
  `HISTORY_EXTRA_KEYS` into `ch_history` (marker `"v": 2`; SmartEnrich
  re-parses a v1 history once, free) and via `cells_from_history` into the
  year store, where they are ordinary metrics (`ebit`, `staff_costs`,
  `director_pay`, `borrowings`, `trade_debtors`, `trade_creditors`).
  Margins, net debt and debtor/creditor days are DERIVED ROWS in `FinGrid`,
  computed in the view from two stored figures and never stored (doctrine 1).
- NOT built, on purpose: web-traffic estimates and composite growth scores
  (third-party estimates, not filings; our own growth signals already cover
  headcount and revenue).

## 4ad. Sources with a JSON API: list fast, profile within a time box

- Enterprise Ireland's directory (`scrapers/enterprise_ireland_scraper.py`,
  16 Sep 2026) is an Angular shell over a public API; the HTML has nothing
  in it, and WebFetch sees only a GTM iframe. Find the API in the page's
  `performance` resource entries, not by guessing paths: the detail call was
  `/api/v1/vendors/profile/{slug}/` after a dozen guesses returned 404.
- ~4,200 companies at two or three calls each is ~8,000 HTTP requests, and
  Cloud Run cuts a request at 300 seconds. So the LIST pass (21 pages) is
  always complete and saved, and the PROFILE pass (website, LinkedIn,
  description, city) is TIME-BOXED, chunked so the deadline is checked before
  each batch is submitted, and skips names we already hold WITH a website
  (`DirectoryScraper.skip_names_provider`, set by main.py from BigQuery).
  Each run fills the next few hundred; `save_targets` is merge-only, so
  re-running is free of side effects. The Friday refresh reaches it through
  `DirectoryScraper.scrape_source("EnterpriseIreland")` with a 60s budget and
  finishes the job over a few weeks. `POST /admin/ingest/enterprise-ireland`
  (token) is the terminal loop; the Sources panel card is the same door.
- The whole directory goes in, all sectors. Fit is decided afterwards by the
  hard filters and SmartFill, as with every source (doctrine 4).

## 4ac. An address must be a mailbox, not a template

- The crawler stored xyz@example.com as a company's contact (Ishu, 16 Sep
  2026). Cause: `_extract_emails` ran a regex over RAW HTML, and a contact
  form's `placeholder=` attribute matched. Nothing then asked whether the
  address was real, and a template address is a guaranteed bounce.
- WHERE WE LOOK (`_visible_text`, `_harvest_ld_json_emails`): mailto: hrefs,
  the page's visible text, and schema.org JSON-LD `email`. Form fields
  (`<input>`, `<textarea>`, `<select>`, `<option>`, `<button>`, `<label>`) are
  removed as whole tags so their placeholder/value attributes go with them;
  scripts, styles, comments and every other tag's attributes are dropped.
  Obfuscated "name [at] domain [dot] com" is decoded: that is a deliberate
  publication aimed at exactly this crawler.
- WHAT WE ACCEPT (`is_placeholder_email`, PURE) refuses ONLY THE CERTAIN.
  Ishu, 16 Sep 2026, after reading the audit: "loosen the rule max, I'd
  rather have false positives than false negatives." So: RFC 2606 reserved
  domains (example.*, .test, .invalid, .localhost), template domains
  (yourdomain.com, company.com, email.com ...), infrastructure domains that
  appear in page source (sentry.io, wixpress.com, godaddy.com ...), 32-hex
  tracking hashes, file suffixes, noreply@, and a SHORT list of unambiguous
  template locals (firstname.lastname, your.name, john.doe, joe.bloggs, xyz,
  abc, placeholder). NOT refused: you@, name@, user@, test@, mail@, me@,
  initials (ab@, az@, caz@), single or repeated letters (h@, aaa@), big-tech
  corporate domains (amyhood@microsoft.com is a person). The first audit
  nearly cleared mail@shawmeters.com, me@kirstys.co.uk, h@theoriginalh.com
  and four investors' initials: a doubtful address is KEPT and the bounce
  pass judges it; a real one refused here is gone for good.
- OBFUSCATION IS PUBLICATION. `decode_obfuscated` undoes HTML entities
  (&#64;, &#x40;, double-encoded) and percent escapes (%40) before the regex
  runs; six stored contacts were real addresses wearing that disguise
  (hello@skratchtech.com, sales@compio.co.uk ...). `normalise_email` is the
  audit's judge: decodes to a real address -> REPAIR; decodes to nothing
  (`\`, `,`, `#`, a bare word, a URL) -> CLEAR.
- THE GUARD RUNS ON EVERY RUNG. The AI search reads the same site and can
  echo the same template; Hunter can index one. `resolve_contact_email`
  drops a placeholder `ai_email` before the waterfall starts,
  `find_email_by_name` results are checked, `choose_best_email` checks both
  sides. `tests_contact_placeholder.py` pins the exact HTML that caused the
  bug.
- RETRO: `POST /admin/contacts/placeholder-audit` (dry run by default,
  companies and investors) lists stored values with an `action` of repair
  or clear; `dry_run=0` applies both with a note on the card, never touching
  a row already emailed at that address (the bounce pass owns that case).

## 4a. Identity guard (same-named companies must never mix)

- Every grounded enrichment call receives the row's SEED ANCHORS as identity
  constraints in the prompt AND must echo back the identity of the company it
  actually researched. `ai/identity_check.py` then verifies IN CODE:
  TWO-ANCHOR RULE — domain and CH number score 2 (near-unique), founder
  surname / city / founding year score 1 (year ±1); confirmed needs ≥2 points
  of agreement outweighing conflict; conflicts ≥ agreement = mismatch;
  not enough overlap = unverified (write allowed, flagged, never trusted).
- On MISMATCH the guard strips every researched field BEFORE persistence -
  the wrong company's details are refused, not corrected. Verdict + note go
  to identity_status/identity_note and the Activity Log; the profile shows
  the badge. All four enrichment call sites use `_identity_guard` (main.py);
  never add a grounded enrichment path without it.
- Seeds are constraints, not suggestions - but seeds can be stale, so one
  conflicting anchor never blocks a match that two others confirm (the note
  names the stale anchor).
- Retro: GET /admin/identity-audit is ZERO AI - it finds the contradictions a
  past mixup leaves (contact email on ANOTHER universe company's domain, CH
  match sharing no core word with the name). Only listed suspects are worth a
  guarded SmartFill re-run.

## 5. Internal Test row (source = 'Internal Test')

- All test-company exceptions key off `source = 'Internal Test'`, nowhere else:
  recipient forced to admin@averroescapital.com (draft + send), contact pinned,
  removal/lost auto-resets to a fresh Qualified state, send/advance guards bypassed.

## 6. Outreach content

- ZERO em dashes anywhere in `outreach_service.py` (instruction text included —
  the model mimics instruction style). Founder email structure v10 (NO ask at
  all in the first email: no call, no meeting, no request for details or
  documents; just "would love to learn more" plus an open collaboration
  invitation. Details/overview requests happen only after the founder shows
  interest, likely under NDA) lives in
  `draft_outreach_email`; change structure only there.
- NEVER CLAIM A HISTORY WE DO NOT HAVE (Ishu, 21 Sep 2026). Paragraph 4
  opened "We have been following {name} for some time"; it now opens "We
  came across {name} and were impressed by ..." in the prompt, its worked
  example and the fallback template alike. We found the company through a
  directory, a filing or an upload, and a founder can tell when a claim of
  weeks of attention is a template. The prompt forbids following, watching
  and tracking outright, so a rephrase cannot bring it back. The specific
  detail is MANDATORY whenever the record holds one ("impressed by what you
  are building" only on an empty record), and the conviction sentence has a
  fixed shape: "We believe X is a big pain point for Y and the opportunity in
  solving it is huge."
- A WORDING CHANGE REACHES STORED DRAFTS BY REDRAFTING, NOT BY WAITING.
  Drafts are persisted on the row (`outreach_draft_body`) so Review & Send
  works without a call, which means old copy survives a deploy. `POST
  /admin/outreach/redraft-stale` (token, dry run by default, `limit` per
  call, 240s time box) regenerates every UNSENT draft carrying a
  `STALE_DRAFT_PHRASES` string, one ungrounded call each, stored news hook
  only (zero grounding spend), persisted exactly as the Draft button does,
  with an activity note. Sent emails are history and are never touched. The
  older pattern (`_clear_v7_drafts`, a boot-time wipe) is for a STRUCTURE
  change where a click-per-company is acceptable; a wording change should
  not cost the team a click each.

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

## 6b. Analytics: a derived cache must be rebuildable

- `analytics_ledger` is a CACHE of derived conclusions. The primary sources are
  `targets`, `activity_log` and `email_log`. Append-only is right for an archive
  of primary data (`archive_service`, which never deletes); it is WRONG for
  derived conclusions, because a wrong conclusion becomes permanent.
- That happened. The ledger ingested the company's CURRENT STATUS as a fact, so
  while the stage rename wrongly held 18 companies in Responded it banked
  "ever reached Responded" for each, and fixing the live rows could not undo it.
  A snapshot of a mutable field is not evidence that an event occurred.
- Stage facts therefore come only from per-stage timestamp stamps and logged
  `status_change` rows. Current status is used ONLY for stages with no stamp
  column (`Not a Fit`, `Under Review`), which are not funnel stages.
- `ledger_rebuild()` recomputes facts for companies still in `targets` and
  PRESERVES facts for companies that have since gone, which is the whole reason
  the ledger exists. Defaults to a dry run and reports a per-event delta.
- The `replied` event uses `NON_REPLY_CLASSES`, like everything else. It did not,
  so autoresponders and bounces counted as replies and the headline response rate
  was inflated. Analytics must never hold its own definition of a reply.
- The inconsistency counters are an ALARM, not a footnote: with the reply rule
  running, `contacted_without_email` and `responded_without_reply` must both be
  zero. `reply_exempt` companies are excluded server-side so a deliberate human
  decision cannot make the alarm permanently non-zero and therefore ignorable.
  `replied_never_emailed` is not a fault; it is an inbound-first thread.

## 6c. Weekly Review (Ishu-private, NOT in the tool)

- The Wednesday meeting pack is Ishu's PRIVATE prep: it must never appear in
  the team-facing UI (a /weekly page was built and reversed on his request,
  26 Aug 2026). It lives as a local file, docs/Weekly_Review.html, which
  fetches GET /weekly-review live — token-gated + exempt (a local file has no
  browser session), token entered once and kept in browser localStorage,
  NEVER embedded in the file (it is in the git repo).
- GET /weekly-review composes analytics (refresh_and_stats), last-7-day counts
  (built from NON_REPLY_CLASSES — the one reply definition), the Responded
  page's own endpoint for the pipeline lists, and tool_updates.py. Never
  re-derive any of those numbers separately for this view.
- `backend/tool_updates.py` is the curated plain-English changelog. EVERY
  session that ships a user-visible change appends one dated line there
  (the deployed container has no git history, and commit messages are for
  engineers). Newest first.

## 6d. Cold start: nothing blocking at import, nothing unbounded in the client

- Cloud Run scales to zero. Whatever runs at IMPORT time is paid for by the
  first request after a deploy or an idle spell, by the real person waiting.
- Loading the qualification criteria was a blocking BigQuery query at import.
  A cold start therefore ran to tens of seconds and the app sat on a blank
  "Loading..." (Ishu, 10 Sep 2026). It now loads on a daemon thread; anything
  that depends on it calls `_ensure_criteria()`, which waits on an Event that
  is set even when the load FAILED (defaults are a legitimate outcome, and a
  caller must never block forever on a load that already gave up). Never put a
  new blocking network call at module scope.
- `AuthGate` had a `catch` but NO timeout, and a hung request is not a failed
  one: nothing ever rejected, so the gate never left "loading" and the user got
  a blank screen with nothing to click. Any fetch the UI blocks on needs an
  AbortController deadline, a bounded retry, and a message that says what is
  happening. Falling through to "open" on failure is right: an unreachable
  backend must not lock the UI, because each call reports its own error anyway.

## 6f. One company means one row: never scan the universe to find a name

- Six request handlers (SmartFill, SmartEnrich, first draft, follow-up
  draft, compose, SEND) found their company with `for c in
  bq_handler.get_universe(): if c["name"] == name`. That is `SELECT *` over
  the whole table, every heavy blob included, into Python dicts, inside a
  512Mi container, to read one row. It held while the universe was 13k rows;
  at 17k (Enterprise Ireland added 4,176) a click on Follow up for Giftcloud
  came back "Failed to fetch" (24 Sep 2026): the container was killed and
  Cloud Run's 503 carries no CORS headers, so the browser reports a network
  error and the true cause is invisible. All six now call
  `bq_handler.get_company_full(name)`. A new handler that needs one company
  uses that; `get_universe()` is for passes that genuinely need every row
  (the sync, the audits, eligibility), and each of those is a memory risk to
  keep in mind as the universe grows.
- The follow-up is PREFILLED on the row. `draft_followup_email` is pure
  string formatting, so `/pipeline` attaches `followup_draft` to every
  Contacted row still owed its one follow-up and `OutreachModal` opens it
  with no request (Ishu: "it should already be loaded, it's super generic
  stuff, no AI"). The endpoint stays as the fallback for rows without it.
  The template itself still lives in ONE place, the backend; the frontend
  never carries a copy of the wording.

## 6e. Every page is gated, and a 401 reloads at most once

- `AuthGate` is applied PER PAGE (`<AuthGate><XInner /></AuthGate>` in each
  `page.tsx`), not in the layout. The Responded page never had it, and
  nothing noticed for a month because a live session hides the gap. On 24
  Sep 2026 Ishu's 12h token lapsed, he clicked Responded, and the page
  loaded, got 401, `apiFetch` cleared the token and reloaded so the gate
  could show sign-in, found no gate, and reloaded again for ever ("it
  continuously reloads and the screen jitters"). A new page MUST wrap its
  content in `AuthGate`; check with `grep -L AuthGate src/app/**/page.tsx`.
- `_sessionRedirect` now reloads at most ONCE per lapse
  (`averroes_401_reloaded` in sessionStorage, cleared by the next successful
  call): a second 401 in a row sends the browser to `/`, which is gated,
  instead of reloading the same page. A missing gate is then a wrong
  landing page, not a frozen browser.

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
- EVERY COMMAND BLOCK FOR ISHU STARTS WITH `avr` (16 Sep 2026). He runs two
  projects in two terminals, and a terminal restart flips gcloud back to the
  other account; three separate errors that day ("does not have permission",
  "not a git repository", "no matches found") were the environment, not the
  task. THE DIRECTORY DECIDES THE ACCOUNT: a `chpwd` hook in his ~/.zshrc
  sets `CLOUDSDK_CONFIG` to `~/.gcloud/dealsmart` under ~/Projects/dealsmart
  and `~/.gcloud/averroes` everywhere else, each dir signed in once to its
  own account. `avr` is a zsh function that cds to the repo (firing the
  hook) and exports `$T` (ops token) and `$B` (backend URL). Never give a
  command that assumes the folder, the account or the variables; never use
  a bare glob that may not match (zsh aborts the whole line), use
  `find ... -delete`.
- The `~/Projects` mount lets the sandbox create files but not delete them,
  so every commit from the sandbox leaves `.git/HEAD.lock` and `tmp_obj_*`
  behind, and the NEXT commit fails until Ishu clears them. The cleanup is
  part of every push block: `rm -f .git/HEAD.lock .git/index.lock; find
  .git/objects -name 'tmp_obj_*' -delete`.
