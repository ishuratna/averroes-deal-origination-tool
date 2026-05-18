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
