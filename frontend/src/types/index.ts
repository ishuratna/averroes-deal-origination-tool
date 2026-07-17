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
  linkedin_url?: string;
  growth_signals?: boolean;
  status: 'Qualified' | 'Contacted' | 'Meeting' | 'DD' | 'Offer' | 'Won' | 'Lost' | 'Under Review' | 'Engaged' | 'Not a Fit' | 'Scraped' | 'Uploaded';
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
export const DEAL_STAGES = ['Qualified', 'Contacted', 'Meeting', 'DD', 'Offer', 'Won', 'Lost'] as const;
export type DealStage = typeof DEAL_STAGES[number];

// Display labels for stored statuses. 'Contacted' is stored in the DB but
// shown as "Responded" (a reply exists). Never rename the stored value.
export function displayStatus(status?: string): string {
  if (!status) return '';
  return status === 'Contacted' ? 'Responded' : status;
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
