export interface CompanyTarget {
  name: string;
  website: string;
  sector: string;
  source: string;
  description: string;
  region?: string;
  ownership?: string;
  estimated_ebitda?: number;
  match_score: number; // 0 to 1
  contact_name?: string;
  contact_email?: string;
  // Contact waterfall v4: who contact_email belongs to and how we got it.
  contact_email_kind?: 'founder' | 'colleague' | 'generic' | '';
  contact_email_name?: string;
  contact_email_source?: string;
  // The contact SmartFill first found, preserved (stamped once) when a
  // pre-send edit or a cross-domain reply adoption replaced it.
  original_contact_name?: string;
  original_contact_email?: string;
  // Why the company is parked (track kill/later): bucket + optional detail.
  park_reason?: string;
  park_reason_detail?: string;
  // Cached NEWS list (JSON of NewsItem[]), refreshed only by the button.
  news_items?: string;
  news_refreshed_at?: string;
  // Identity guard: was the researched company verifiably OURS?
  // 'confirmed' | 'unverified' | 'mismatch' | 'suspect' (retro audit).
  identity_status?: string;
  identity_note?: string;
  linkedin_url?: string;
  growth_signals?: boolean;
  status: 'Qualified' | 'Contacted' | 'Responded' | 'Meeting' | 'DD' | 'Offer' | 'Won' | 'Lost' | 'Under Review' | 'Not a Fit' | 'Scraped' | 'Uploaded';
  ingested_at?: string;
  // Expanded PitchBook fields
  contact_title?: string;
  contact_phone?: string;
  hq_email?: string;
  hq_phone?: string;
  hq_location?: string;
  hq_city?: string;
  hq_country?: string;
  employees?: number;
  year_founded?: number;
  keywords?: string;
  verticals?: string;
  industry_group?: string;
  industry_code?: string;
  emerging_spaces?: string;
  business_status?: string;
  financing_status?: string;
  total_raised_m?: number;
  revenue_m?: number;
  net_income_m?: number;
  enterprise_value_m?: number;
  revenue_growth_pct?: number;
  valuation_estimate_m?: number;
  last_valuation_m?: number;
  last_valuation_date?: string;
  active_investors?: string;
  num_active_investors?: number;
  former_investors?: string;
  last_financing_date?: string;
  last_financing_size_m?: number;
  last_financing_valuation_m?: number;
  last_financing_type?: string;
  first_financing_date?: string;
  first_financing_size_m?: number;
  pitchbook_growth_rate?: number;
  growth_rate_percentile?: number;
  web_visitors?: number;
  opportunity_score?: number;
  success_probability?: number;
  ma_probability?: number;
  predicted_exit_type?: string;
  total_patents?: number;
  competitors?: string;
  also_known_as?: string;
  legal_name?: string;
  registration_number?: string;
  financing_note?: string;
  size_bucket?: string;
  // Companies House financial data
  ch_company_number?: string;
  ch_official_name?: string;
  ch_status?: string;
  ch_incorporated_date?: string;
  ch_sic_codes?: string;
  revenue_y1?: number;
  revenue_y1_date?: string;
  revenue_y2?: number;
  revenue_y2_date?: string;
  revenue_y3?: number;
  revenue_y3_date?: string;
  gross_profit_y1?: number;
  gross_profit_y2?: number;
  profit_y1?: number;
  profit_y1_date?: string;
  profit_y2?: number;
  profit_y3?: number;
  total_assets_y1?: number;
  net_assets_y1?: number;
  cash_y1?: number;
  employees_ch?: number;
  filing_type?: string;
  ch_match_confidence?: string;
  ch_notes?: string;
  ch_pdf_path?: string;
  // Deal-team ownership + triage (docs/Averroes_Deal_Pipeline_Process.pdf).
  // One owner field, which changes hands from Ishu to the assigned associate.
  owner?: DealOwner | '';
  track?: DealTrack;
  triaged_at?: string;
  // Averroes fit scoring
  averroes_fit_score?: number;
  score_employee_growth?: number;
  score_revenue_growth?: number;
  score_revenue_size?: number;
  score_business_fit?: number;
  score_market_sentiment?: number;
  score_details?: string;
  revenue_band?: string;
  revenue_estimate_m?: number;
  revenue_source?: string;
  revenue_confidence?: string;
  // Companies House registry intelligence
  ch_psc_summary?: string;
  ch_ownership_verified?: string;
  ch_charges_count?: number;
  ch_charges_summary?: string;
  ch_last_share_allotment?: string;
  ch_accounts_next_due?: string;
  ch_accounts_overdue?: boolean;
  ch_insolvency_summary?: string;
  ch_last_resolution?: string;
  ch_accounts_regime?: string;
  ch_cap_table?: string;
  ch_cap_table_date?: string;
  ch_founder_pct?: number;
  ch_watched_at?: string;
  ch_history?: string;
  // Inven export fields
  revenue_cagr_3yr_pct?: number;
  employee_growth_1yr_pct?: number;
  employee_growth_3yr_pct?: number;
  ebitda_margin_pct?: number;
  directors?: string;
  company_linkedin?: string;
  last_smartfill_at?: string;
  unfit_reason?: string;
  outreach_draft_subject?: string;
  outreach_draft_body?: string;
  outreach_draft_to?: string;
  outreach_drafted_at?: string;
  outreach_sent_at?: string;
  last_reply_at?: string;
  reply_classification?: string;
  // Responded-stage action buckets (set by email sync intelligence)
  action_bucket?: string;
  action_rationale?: string;
  action_follow_up_date?: string;
  action_set_at?: string;
  action_reply_subject?: string;
  action_reply_body?: string;
  // IC memo one-pager (JSON string) for Responded-or-later companies
  ic_memo?: string;
  ic_memo_at?: string;
  // Raw investor/owner lists from the Inven export
  investors_raw?: string;
  current_owners?: string;
  // Smart Upload: unmapped source columns preserved as JSON
  extra_data?: string;
  // Stage timeline
  stage_entered_at?: string;
  qualified_at?: string;
  contacted_at?: string;
  meeting_at?: string;
  dd_at?: string;
  offer_at?: string;
  won_at?: string;
  lost_at?: string;
}

export interface ActivityEntry {
  id: string;
  company_name: string;
  action_type: 'status_change' | 'note' | 'outreach_sent';
  old_status?: string;
  new_status?: string;
  note_text?: string;
  created_by: string;
  created_at: string;
}

export interface PipelineMetrics {
  totalTargets: number;
  avgMatchScore: number;
  totalEbitdaValue: number;
}

// Deal stages in pipeline order
// 'Contacted' = we emailed them. 'Responded' = they replied. There is no
// 'Engaged' anywhere any more.
export const DEAL_STAGES = ['Qualified', 'Contacted', 'Responded', 'Meeting', 'DD', 'Offer', 'Won', 'Lost'] as const;
export type DealStage = typeof DEAL_STAGES[number];

// ── Deal-team ownership + triage ────────────────────────────────────────────
// Mirrors bq_handler.OWNERS / TRACKS. Process: docs/Averroes_Deal_Pipeline_Process.pdf
export const DEAL_OWNERS = ['Bea', 'Ishu', 'Issam', 'Marianna'] as const;
export type DealOwner = typeof DEAL_OWNERS[number];

// Only Issam and Marianna take Track B founder calls; the Wednesday allocation
// balances between them. Bea takes Track A calls; Ishu takes none.
export const CALL_ASSOCIATES = ['Issam', 'Marianna'] as const;

// Stored values, never renamed (see CLAUDE.md 2a on value renames). The UI
// shows: A = "Pass to Bea", B = "Pass to Issam/Marianna",
// kill = "Not interested", later = "Talk later".
export type DealTrack = 'A' | 'B' | 'kill' | 'later' | '';

export const OWNER_ROLES: Record<DealOwner, string> = {
  Bea: 'Partner — takes Track A calls',
  Ishu: 'Operator — triages, writes as Bea, takes no calls',
  Issam: 'Associate — Track B calls',
  Marianna: 'Associate — Track B calls',
};

// Responded page v3 (agreed with Ishu, 21 Aug 2026): three OWNED SECTIONS,
// each a stage of the funnel with a named person responsible, plus the parked
// lists. Backend counterpart: main.py _responded_group() — the queue keys here
// mirror its return values exactly, so the page renders whatever the one
// derivation says and can never disagree with the header stats.
// PLAIN ENGLISH ON PURPOSE: every list says what a company is WAITING FOR, in
// words a first-time reader understands. Internal vocabulary (Track A/B, kill)
// stays in the stored values; it does not appear on screen.
// WHY a company was parked. Mirrors bq_handler.PARK_REASONS exactly - the
// backend validates against its list, so the two must never drift. The
// description is the picker's hover text explaining when to use each bucket.
export const PARK_REASONS: { bucket: string; description: string }[] = [
  { bucket: 'Too early',           description: 'Revenue or maturity below our range; worth revisiting as they grow.' },
  { bucket: 'Fundraising instead', description: 'Raising equity rather than considering a sale.' },
  { bucket: 'Bad timing',          description: 'Founder is open, but now is the wrong moment (personal or company timing).' },
  { bucket: 'In another process',  description: 'Already engaged with another buyer or adviser.' },
  { bucket: 'Revisit next year',   description: 'Agreed to reconnect in 6-12 months.' },
  { bucket: 'Not selling',         description: 'Founder explicitly has no intent to sell.' },
  { bucket: 'Too small',           description: 'Below the mandate’s revenue range on closer look.' },
  { bucket: 'Too large',           description: 'Above the range, or the cheque would be too big for us.' },
  { bucket: 'Sector mismatch',     description: 'Outside the UK/Ireland B2B software focus on closer look.' },
  { bucket: 'Weak financials',     description: 'Declining revenue, losses, or poor quality of earnings.' },
  { bucket: 'Valuation gap',       description: 'Expectations far above what we would pay.' },
  { bucket: 'Unresponsive',        description: 'Showed interest then went quiet despite follow-ups.' },
  { bucket: 'Founder concerns',    description: 'Credibility, behaviour, or key-person doubts.' },
  { bucket: 'Chose another buyer', description: 'Sold, or exclusive with someone else.' },
  { bucket: 'Other',               description: 'Anything else - explain in the detail box.' },
];

// One clickable item in the profile's NEWS section.
export interface NewsItem {
  title: string;
  source: string;
  date: string;   // YYYY-MM or YYYY-MM-DD, may be ''
  url: string;
}

// Sections hold LANES. A lane is one route drawn top-to-bottom, exactly like
// a branch in the decision tree; a section with two lanes renders them side
// by side, because that is the picture Ishu approved: Assignment ready splits
// into TWO KINDS of call, the Bea route and the associate route, and stacking
// them made the split read as one queue.
export interface RespondedList { key: string; label: string; hint: string; }
export interface RespondedLane { key: string; title: string; tone: string; lists: RespondedList[]; }
export interface RespondedSection {
  key: string; title: string; owner: string; tone: string; blurb: string;
  lanes: RespondedLane[];
}

export const RESPONDED_SECTIONS: RespondedSection[] = [
  {
    key: 's1', title: 'Nurture', owner: 'Ishu', tone: 'plum',
    blurb: 'Ishu runs every conversation until it is mature, then clicks it forward.',
    lanes: [
      {
        key: 'main', title: '', tone: 'plum', lists: [
          { key: 'nurture',          label: 'Nurture',          hint: 'Live email conversations. Keep them warm; the reminders below chase anything quiet for 7 days.' },
          { key: 'assignment_ready', label: 'Assignment ready', hint: 'Mature conversations you marked ready. Route each to Bea or to an associate call.' },
        ],
      },
    ],
  },
  {
    key: 's2', title: 'Associates weekly list', owner: 'Wed · Thu sessions', tone: 'amber',
    blurb: 'Assignment ready splits into two kinds of call, each with its own weekly session:',
    lanes: [
      {
        key: 'bea', title: 'Bea route — Thursday', tone: 'teal', lists: [
          { key: 'bea_review', label: 'To discuss for Bea', hint: 'High-fit candidates. The Thursday session confirms each to Bea or bounces it back.' },
        ],
      },
      {
        key: 'assoc', title: 'Associate route — Wednesday', tone: 'amber', lists: [
          { key: 'assoc_review',  label: 'To discuss for calls',     hint: 'Waiting for Wednesday to decide which associate takes the call.' },
          { key: 'assoc_pending', label: 'Allocated — call pending', hint: 'An associate owns it. After the call they move it to Meeting on the Pipeline themselves.' },
        ],
      },
    ],
  },
  {
    key: 's3', title: 'Qualified leads', owner: 'Bea', tone: 'teal',
    blurb: 'Confirmed to Bea at the Thursday session. Hers until a meeting happens.',
    lanes: [
      {
        key: 'main', title: '', tone: 'teal', lists: [
          { key: 'bea_assigned', label: 'With Bea', hint: 'Bea takes these conversations forward. A booked meeting moves them off this page.' },
        ],
      },
    ],
  },
];

// Parked lists render after the sections, always visible (never behind a
// toggle): live sections + these + progressed = the Pipeline's
// Responded-and-beyond count, so the reconciliation is a glance, not faith.
export const RESPONDED_PARKED = [
  { key: 'talk_later', label: 'Talk later',     hint: 'Warm but not now. No reminders; each wakes into Assignment ready 6 months after you parked it.' },
  { key: 'closed',     label: 'Not interested', hint: 'Closed out by us. Still counted in the Pipeline’s Responded column, because they did reply.' },
] as const;

export interface RespondedCompany extends CompanyTarget {
  queue: string;
  sent_count?: number;
  recv_count?: number;
  last_direction?: string;
  last_msg_at?: string;
  days_since_reply?: number;
  // Set when the user answered "keep it in Responded" to the reply-rule prompt:
  // a genuine reply exists that the mailbox has no record of.
  reply_exempt_at?: string;
  reply_exempt_by?: string;
  // v3: Ishu's "Ready to assign" click (Nurture -> Assignment ready).
  assignment_ready_at?: string;
  // Derived server-side alongside the queue, so the rules live once:
  resurfaced?: boolean;      // a Talk-later that just woke up after 6 months
  probably_ready?: boolean;  // still in Nurture but they answered email 2 — likely mature
}

// ── THE REPLY RULE ──────────────────────────────────────────────────────────
//
//   Qualified = promoted from the Master Universe, no outreach sent yet
//   Contacted = we emailed them, no genuine reply has come back yet
//   Responded = we emailed them AND they genuinely replied
//
// An out-of-office autoresponder is not a reply, so it returns the company to
// Contacted and only defers the follow-up reminder.
//
// The Pipeline's Responded column and the Responded page render the SAME set,
// selected on status, so the two counts always agree. Keeping status honest is
// this rule's job.
export const STAGE_MEANINGS: Record<string, string> = {
  Qualified: 'Qualified from the Master Universe. No outreach sent yet.',
  Contacted: 'We emailed them. No genuine reply yet — an out-of-office does not count.',
  Responded: 'We emailed them and they genuinely replied.',
};

// A file a founder attached to an email, filed automatically by the sync.
export interface EmailDoc {
  filename: string;
  gcs_path: string;
  content_type: string;
  size_bytes: number;
  email_subject: string;
  sender_email: string;
  received_at: string;
  ai_summary: string;
  ai_updates: string;   // JSON of the field changes the document caused, '' if none
}

export interface ReplyRuleMove {
  name: string;
  from: string;
  to: string;
  moved_by?: string;
  reason?: string;
  last_reply_at?: string;
}

export interface ReplyRuleResult {
  status: string;
  dry_run: boolean;
  counts: { promote: number; demote: number; needs_confirmation: number };
  promote: ReplyRuleMove[];
  demote: ReplyRuleMove[];
  // No reply on record, but a person put them in Responded. Never moved without
  // an explicit answer, because they may know the founder rang instead.
  needs_confirmation: ReplyRuleMove[];
  message?: string;
}

export interface RespondedResponse {
  total: number;
  counts: Record<string, number>;
  open_calls: Record<string, number>;
  owners: string[];
  companies: RespondedCompany[];
}

// Stored statuses now read the same on screen as they do in BigQuery, so there
// is nothing left to translate. Kept as a function because it is called in a
// lot of places and a stage label may need special-casing again one day.
export function displayStatus(status?: string): string {
  return status || '';
}

// ── Responded-stage action buckets ──────────────────────────────────────────
// Keys mirror backend/services/reply_intel.py BUCKETS. tone drives chip colour;
// priority drives kanban ordering inside the Responded column (act-now first).
export const ACTION_BUCKETS: Record<string, { label: string; tone: 'act' | 'respond' | 'hold' | 'stop' | 'review'; priority: number }> = {
  right_fit_call:      { label: 'Right fit — set up call',            tone: 'act',     priority: 0 },
  right_fit_answer:    { label: 'Right fit — answer & advance',       tone: 'act',     priority: 1 },
  redirect_referral:   { label: 'Redirect — follow the referral',     tone: 'respond', priority: 2 },
  right_fit_structure: { label: 'Right fit — structure mismatch',     tone: 'respond', priority: 3 },
  right_fit_early:     { label: 'Right fit — too early, nurture',     tone: 'hold',    priority: 4 },
  not_now_timing:      { label: 'Not now — timing',                   tone: 'hold',    priority: 5 },
  right_fit_large:     { label: 'Right fit — too large, stay close',  tone: 'hold',    priority: 6 },
  needs_human:         { label: 'Needs human read',                   tone: 'review',  priority: 7 },
  declined_close:      { label: 'Declined — close politely',          tone: 'stop',    priority: 8 },
  not_fit_no_respond:  { label: 'Not the right fit — do not respond', tone: 'stop',    priority: 9 },
};

export function actionBucketInfo(key?: string) {
  return key ? ACTION_BUCKETS[key] ?? null : null;
}

// ── Investor (LP) database ──────────────────────────────────────────────────

export interface Investor {
  investor_id?: string;
  name: string;
  investor_type?: string;
  aum_m?: number;
  ticket_min_m?: number;
  ticket_max_m?: number;
  region?: string;
  hq_city?: string;
  hq_country?: string;
  website?: string;
  description?: string;
  contact_name?: string;
  contact_email?: string;
  linkedin_url?: string;
  source?: string;
  source_companies?: string;
  status?: string;
  lp_fit_score?: number;
  score_geography?: number;
  score_pe_appetite?: number;
  score_ticket_fit?: number;
  score_tech_affinity?: number;
  fit_details?: string;
  notes?: string;
  // PitchBook LP export fields (USD figures)
  pb_id?: string;
  aka?: string;
  contact_title?: string;
  contact_phone?: string;
  hq_email?: string;
  global_region?: string;
  year_founded?: number;
  strategy_preferences?: string;
  geo_preferences?: string;
  open_to_first_time?: string;
  num_commitments?: number;
  num_active_commitments?: number;
  num_pe_commitments?: number;
  total_commitments_m?: number;
  // Commitments breakdown v2 ($M USD as reported by PitchBook)
  total_active_commitments_m?: number;
  total_pe_commitments_m?: number;
  num_vc_commitments?: number;
  total_vc_commitments_m?: number;
  sold_secondaries?: string;
  bought_secondaries?: string;
  policy_description?: string;
  extra_data?: string;
  other_preferences?: string;
  registration_number?: string;
  pb_last_updated?: string;
  psc_summary?: string;
  officers_summary?: string;
  net_assets_m?: number;
  ingested_at?: string;
  updated_at?: string;
}

export const INVESTOR_STAGES = ['Identified', 'Researched', 'Contacted', 'Meeting', 'Committed', 'Passed'] as const;

// Revenue band v3 — calibrated to the mandate: £15–40M equity cheques for
// majority or significant minority (25%+) stakes → investable revenue
// envelope £5–40M at 4–6x EV/revenue (core sweet spot £8–20M).
// Uses the stored band (computed by SmartFill, incl. AI-estimated revenue);
// falls back to deriving from raw revenue data for rows not yet re-SmartFilled.
export function getRevenueBand(company: { revenue_band?: string; revenue_y1?: number; revenue_m?: number; revenue_estimate_m?: number }): string | null {
  if (company.revenue_band) return company.revenue_band;
  let revM: number | null = null;
  if (company.revenue_y1 != null && company.revenue_y1 > 0) revM = company.revenue_y1 / 1e6;
  else if (company.revenue_m != null && company.revenue_m > 0) revM = company.revenue_m;
  else if (company.revenue_estimate_m != null && company.revenue_estimate_m > 0) revM = company.revenue_estimate_m;
  if (revM == null) return null;
  if (revM < 2.5) return 'Too Early';
  if (revM <= 40) return 'Target Band';
  return 'Too Large';
}
