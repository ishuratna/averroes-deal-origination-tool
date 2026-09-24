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
  // DERIVED server-side from email_log on every pipeline/universe/profile
  // row: how many emails WE have sent this company (tool or inbox alike,
  // the sync files both) and when the last one went. outreach_sent_at only
  // knows about sends from the tool, so a follow-up typed in the inbox is
  // invisible to it; these two are what the Follow up button and the card
  // clock read (Ishu, 24 Sep 2026: follow up only once).
  sent_count?: number;
  last_sent_at?: string;
  // The 14-day follow-up, pre-built server-side on Contacted pipeline rows
  // still owed their one follow-up, so the modal opens with no request.
  followup_draft?: { to: string; subject: string; body: string };
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

// Issam and Marianna are the associates (the Thursday call); Bea is the
// partner (the Monday call); Ishu nurtures and takes no calls.
export const CALL_ASSOCIATES = ['Issam', 'Marianna'] as const;
export const CALL_PARTNERS = ['Bea'] as const;

// Stored values, never renamed (see CLAUDE.md 2a on value renames). Since
// 22 Sep 2026 the UI reads them as: B = "with the associates" (Thursday),
// A = "with the partners" (Monday), kill = "Not interested", later = "Talk
// later". The old fit/size meaning of A and B is gone.
export type DealTrack = 'A' | 'B' | 'kill' | 'later' | '';

export const OWNER_ROLES: Record<DealOwner, string> = {
  Bea: 'Partner — Monday call',
  Ishu: 'Operator — nurtures, writes as Bea, takes no calls',
  Issam: 'Associate — Thursday call',
  Marianna: 'Associate — Thursday call',
};

// Responded page v4 (Ishu, 22 Sep 2026): ONE flow, two calls, no fork.
//   Ishu (Nurture) -> associates, Thursday call -> partners, Monday call
// Three OWNED SECTIONS, each a step with a named person responsible, plus the
// parked lists. Backend counterpart: main.py _responded_group() — the queue
// keys here mirror its return values exactly, so the page renders whatever
// the one derivation says and can never disagree with the header stats.
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

// Sections hold LANES. Since v4 every section is a single lane (the v3
// fit/size split into two side-by-side routes is gone), but the shape is kept
// so a future branch can be drawn without rewriting the page.
export interface RespondedList { key: string; label: string; hint: string; }
export interface RespondedLane { key: string; title: string; tone: string; lists: RespondedList[]; }
export interface RespondedSection {
  key: string; title: string; owner: string; tone: string; blurb: string;
  lanes: RespondedLane[];
}

export const RESPONDED_SECTIONS: RespondedSection[] = [
  {
    key: 's1', title: 'Nurture', owner: 'Ishu', tone: 'plum',
    blurb: 'Ishu runs every conversation until it is ready, then passes it to the associates.',
    lanes: [
      {
        key: 'main', title: '', tone: 'plum', lists: [
          { key: 'nurture', label: 'Nurture', hint: 'Live email conversations. Keep them warm; the reminders chase anything quiet for 7 days. When one is ready, pass it to the associates for Thursday.' },
        ],
      },
    ],
  },
  {
    key: 's2', title: 'Associates', owner: 'Thursday call · Issam & Marianna', tone: 'amber',
    blurb: 'Everything Ishu passes on lands here. The Thursday call decides: an associate takes the relationship, or it goes up to the partners.',
    lanes: [
      {
        key: 'main', title: '', tone: 'amber', lists: [
          { key: 'assoc_review',  label: 'Thursday list',        hint: 'Passed by Ishu, not yet discussed. On Thursday: assign to Issam or Marianna, or pass straight to the partners.' },
          { key: 'assoc_pending', label: 'With Issam / Marianna', hint: 'An associate owns the relationship. When it is ready, they pass it to the partners for Monday; a booked meeting moves it to the Pipeline.' },
        ],
      },
    ],
  },
  {
    key: 's3', title: 'Partners', owner: 'Monday call · Bea', tone: 'teal',
    blurb: 'Passed up by the associates. The Monday call confirms who takes it forward.',
    lanes: [
      {
        key: 'main', title: '', tone: 'teal', lists: [
          { key: 'partner_review',   label: 'Monday list', hint: 'Passed by the associates, not yet discussed. On Monday: confirm to Bea, or send it back to the associates.' },
          { key: 'partner_assigned', label: 'With Bea',    hint: 'Bea takes these conversations forward. A booked meeting moves them off this page.' },
        ],
      },
    ],
  },
];

// Parked lists render after the sections, always visible (never behind a
// toggle): live sections + these + progressed = the Pipeline's
// Responded-and-beyond count, so the reconciliation is a glance, not faith.
export const RESPONDED_PARKED = [
  { key: 'talk_later', label: 'Talk later',     hint: 'Warm but not now. No reminders; each wakes back into Nurture 6 months after you parked it.' },
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
  // v3 staging stamp, retired in v4 (22 Sep 2026); still on the row, ignored.
  assignment_ready_at?: string;
  // Derived server-side alongside the queue, so the rules live once:
  resurfaced?: boolean;      // a Talk-later that just woke up after 6 months
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
  // Document SmartFill: values that DISAGREE with the record wait for a
  // decision. JSON array of DocReviewItem while open; after the review it
  // holds {accepted, declined} and pending_resolved_at is set.
  pending_updates: string;
  pending_resolved_at?: string | null;
  read_error?: string;   // why the AI read failed, '' when it succeeded
}

// One figure in company_financials: (period_end, metric, segment) -> value.
export interface FinCell {
  period_end: string;     // YYYY-MM-DD
  metric: string;         // revenue | arr | gross_profit | gross_margin_pct | ebitda | ... | employees | customers
  segment: string;        // '' for the whole company; a product/segment name for a revenue split
  value: number;
  unit: 'GBP' | 'pct' | 'count' | string;
  basis: 'actual' | 'budget' | 'forecast' | string;
  source: string;
  evidence: string;
  recorded_at?: string;
}

export const FIN_METRIC_LABELS: Record<string, string> = {
  revenue: 'Revenue', arr: 'ARR', gross_profit: 'Gross profit', gross_margin_pct: 'Gross margin',
  ebitda: 'EBITDA', ebitda_margin_pct: 'EBITDA margin', profit_before_tax: 'Profit before tax',
  net_income: 'Net income', cash: 'Cash', net_assets: 'Net assets', total_assets: 'Total assets',
  employees: 'Employees', customers: 'Customers',
};
export const FIN_METRIC_ORDER = Object.keys(FIN_METRIC_LABELS);

// ── Fiscal years (mirror of backend/services/fiscal_year.py) ─────────────────
// ONE convention: a fiscal year is named after the calendar year the period
// ENDS in. A year to 31 Mar 2026 is FY26; to 31 Dec 2025 is FY25. The window
// (FY22..FY26 in 2026) is served by GET /company/{name}/financials so the card
// never decides the years itself; these helpers only read and label.
export function fiscalYearOf(periodEnd: string | null | undefined): number | null {
  const m = /^(\d{4})-\d{2}/.exec((periodEnd || '').trim());
  return m ? Number(m[1]) : null;
}
export function fyLabel(fy: number | null | undefined): string {
  return fy ? `FY${String(fy % 100).padStart(2, '0')}` : '';
}
// "to 31 Mar 26" for a column sub-header.
export function periodEndShort(periodEnd: string | null | undefined): string {
  const m = /^(\d{4})-(\d{2})-(\d{2})/.exec((periodEnd || '').trim());
  if (!m) return '';
  const months = ['Jan', 'Feb', 'Mar', 'Apr', 'May', 'Jun', 'Jul', 'Aug', 'Sep', 'Oct', 'Nov', 'Dec'];
  return `to ${Number(m[3])} ${months[Number(m[2]) - 1]} ${m[1].slice(2)}`;
}

// "31 Mar" from the backend's "MM-DD" year end.
export function yearEndShort(mmdd: string | null | undefined): string {
  const m = /^(\d{2})-(\d{2})$/.exec((mmdd || '').trim());
  if (!m) return '';
  const months = ['Jan', 'Feb', 'Mar', 'Apr', 'May', 'Jun', 'Jul', 'Aug', 'Sep', 'Oct', 'Nov', 'Dec'];
  return `${Number(m[2])} ${months[Number(m[1]) - 1]}`;
}

// One column of the five-year window, as the backend defines it.
export interface FinYear {
  fy: number;
  period_end: string;            // the day this company's FY closes (year end from Companies House, else 31 Dec)
  status: 'running' | 'closed' | string;
}
export interface FinUnplaced { metric: string; value: number; label: string; reason: string }
export interface FinancialsResponse {
  cells: FinCell[];
  window: FinYear[];
  year_end: string | null;       // "MM-DD" when Companies House has told us, else null
  unplaced: FinUnplaced[];
}
export const EMPTY_FINANCIALS: FinancialsResponse = { cells: [], window: [], year_end: null, unplaced: [] };

export interface DocReviewItem {
  key: string;        // column, or 'financials' for the year table
  label: string;
  kind: 'fill' | 'conflict';
  old: string;        // current value, display form ('(empty)' when blank)
  new: string;        // the document's value, display form
  evidence: string;
}

export function parsePendingReview(doc: EmailDoc): DocReviewItem[] {
  if (!doc.pending_updates || doc.pending_resolved_at) return [];
  try {
    const v = JSON.parse(doc.pending_updates);
    return Array.isArray(v) ? v : [];
  } catch { return []; }
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
  // The outreach loop (same names as CompanyTarget so lib/outreach.ts applies)
  outreach_draft_subject?: string;
  outreach_draft_body?: string;
  outreach_draft_to?: string;
  outreach_drafted_at?: string;
  outreach_sent_at?: string;
  contacted_at?: string;
  responded_at?: string;
  stage_entered_at?: string;
  last_reply_at?: string;
  reply_classification?: string;
  park_reason?: string;
  park_reason_detail?: string;
  bounced_email?: string;
  // Co-investment priority (ai/lp_priority.py): the one ranking for the raise
  network_tags?: string;      // 'GCC, Bea'
  priority_score?: number;    // 0-100
  priority_tier?: string;     // A | B | C | Parked
  priority_details?: string;  // JSON breakdown
  // PitchBook LP export fields (USD figures)
  pb_id?: string;
  aka?: string;
  contact_title?: string;
  contact_phone?: string;
  hq_email?: string;
  global_region?: string;
  // Server-side rollups from the gate's geography sets (one definition):
  // where they ARE, and where they INVEST. See ai/investor_gate.py.
  region_bucket?: 'Middle East' | 'UK & Ireland' | 'Europe' | 'Global' | 'Unknown';
  mandate_buckets?: string[];
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

// Mirrors storage/investor_handler.py INVESTOR_STAGES: the investor loop follows the founder loop.
export const INVESTOR_STAGES = ['Identified', 'Researched', 'Contacted', 'Responded', 'Meeting', 'Committed', 'Passed', 'Talk Later'];
export const INVESTOR_PARKED = ['Passed', 'Talk Later'];

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

export const PRIORITY_TIERS = ['A', 'B', 'C', 'Parked'];

/** The Contactable filter (Ishu, 11 Sep 2026: "just use contactable vs not").
 *  SAME definition as `_readiness` in backend ai/lp_priority.py: a contact
 *  email with an @ in it. A name alone is not contactable. */
export const CONTACTABLE_OPTIONS = ['Contactable', 'No email yet'];
export function contactableBucket(i: { contact_email?: string | null }): string {
  return (i.contact_email || '').includes('@') ? 'Contactable' : 'No email yet';
}
export const NETWORK_TAG_SUGGESTIONS = ['GCC', 'Bea', 'Partner', 'Co-investor', 'Network', 'Warm'];
export function parseTags(s?: string): string[] {
  return (s || '').split(/[,;|]/).map(t => t.trim()).filter(Boolean);
}
// "GCC" on the investor pages means the KSA/GCC base: home geography in the
// six GCC states OR an explicit GCC network tag. The 3 Aug 2026 PitchBook
// export already holds ~870 such rows, so geography must count, not only tags.
const GCC_COUNTRIES = ['saudi arabia', 'united arab emirates', 'uae', 'qatar', 'kuwait', 'bahrain', 'oman'];
// Fixed display order for the region and mandate filters. Matches
// REGION_BUCKETS / MANDATE_BUCKETS in ai/investor_gate.py.
export const REGION_BUCKETS = ['Middle East', 'UK & Ireland', 'Europe', 'Global', 'Unknown'] as const;
export const MANDATE_BUCKETS = ['UK', 'Ireland', 'Europe', 'Middle East', 'Other'] as const;

/** "City, Country" for the City filter and the Location column: one format
 *  everywhere. Empty when no city is on record, so the filter lists only
 *  investors whose city we actually know. */
export function cityLabel(i: { hq_city?: string | null; hq_country?: string | null }): string {
  const city = (i.hq_city || '').trim();
  if (!city) return '';
  const country = (i.hq_country || '').trim();
  return country ? `${city}, ${country}` : city;
}

export function isGcc(i: { hq_country?: string; region?: string; global_region?: string; network_tags?: string }): boolean {
  const geo = `${i.hq_country || ''} ${i.region || ''}`.toLowerCase();
  if (GCC_COUNTRIES.some(c => geo.includes(c))) return true;
  return parseTags(i.network_tags).some(t => t.toLowerCase() === 'gcc');
}
