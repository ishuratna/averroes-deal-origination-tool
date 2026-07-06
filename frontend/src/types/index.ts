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

// Revenue band: Averroes sweet spot £2.5–10M = "Target Band".
// Uses the stored band (computed by SmartFill, incl. AI-estimated revenue);
// falls back to deriving from raw revenue data for rows not yet re-SmartFilled.
export function getRevenueBand(company: { revenue_band?: string; revenue_y1?: number; revenue_m?: number }): string | null {
  if (company.revenue_band) return company.revenue_band;
  let revM: number | null = null;
  if (company.revenue_y1 != null && company.revenue_y1 > 0) revM = company.revenue_y1 / 1e6;
  else if (company.revenue_m != null && company.revenue_m > 0) revM = company.revenue_m;
  if (revM == null) return null;
  if (revM < 2.5) return 'Too Early';
  if (revM <= 10) return 'Target Band';
  return 'Too Large';
}
