export interface CompanyTarget {
  name: string;
  website: string;
  sector: string;
  source: string;
  description: string;
  estimated_ebitda?: number;
  match_score: number; // 0 to 1
  contact_name?: string;
  contact_email?: string;
  status: 'Qualified' | 'Under Review' | 'Engaged' | 'Not a Fit';
}

export interface PipelineMetrics {
  totalTargets: number;
  avgMatchScore: number;
  totalEbitdaValue: number;
}
