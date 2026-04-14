import { CompanyTarget } from "../types";

const API_BASE_URL = process.env.NEXT_PUBLIC_API_URL || 'http://localhost:8000';

export const dealApi = {
  /**
   * Fetch the active sourcing pipeline
   */
  async getPipeline(): Promise<CompanyTarget[]> {
    try {
      const response = await fetch(`${API_BASE_URL}/pipeline`);
      if (!response.ok) throw new Error('Failed to fetch pipeline');
      return await response.json();
    } catch (error) {
      console.error('Deal API Error:', error);
      // Return mocked data if backend is not alive yet
      return [
        {
          name: "SaaS Synergy Corp",
          website: "https://synergy.io",
          sector: "B2B Infrastructure",
          source: "Historical Database",
          estimated_ebitda: 7.2,
          description: "Infrastructure for hybrid work environments.",
          match_score: 0.92,
          status: 'Qualified'
        },
        {
          name: "Nexus Flow Ltd",
          website: "https://nexus.flow",
          sector: "FinTech Enabler",
          source: "Manual Research",
          estimated_ebitda: 3.5,
          description: "Payment gateway orchestration for Mid-Market.",
          match_score: 0.88,
          status: 'Under Review'
        }
      ];
    }
  },

  /**
   * Fetch the Master Universe (all scraped targets)
   */
  async getUniverse(): Promise<CompanyTarget[]> {
    try {
      const response = await fetch(`${API_BASE_URL}/universe`);
      if (!response.ok) throw new Error('Failed to fetch universe');
      return await response.json();
    } catch (error) {
      console.error('Universe API Error:', error);
      return [];
    }
  },

  /**
   * Run AI Analysis on a new URL
   */
  async analyzeTarget(url: string): Promise<CompanyTarget> {
    const response = await fetch(`${API_BASE_URL}/analyze-target`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ url })
    });
    if (!response.ok) throw new Error('Analysis failed');
    return await response.json();
  },

  /**
   * Manually trigger deep-dive enrichment for a founder
   */
  async enrichCompany(companyName: string): Promise<CompanyTarget> {
    const response = await fetch(`${API_BASE_URL}/enrich/${encodeURIComponent(companyName)}`, {
      method: 'POST'
    });
    if (!response.ok) throw new Error('Enrichment failed');
    return await response.json();
  }
};
