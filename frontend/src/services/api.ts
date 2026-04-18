import { CompanyTarget } from "../types";

const API_BASE_URL = process.env.NEXT_PUBLIC_API_URL || 'http://localhost:8000';

export const dealApi = {
  /**
   * Fetch the active sourcing pipeline (Top matches)
   */
  async getPipeline(): Promise<CompanyTarget[]> {
    try {
      const response = await fetch(`${API_BASE_URL}/pipeline`);
      if (!response.ok) throw new Error('Failed to fetch pipeline');
      return await response.json();
    } catch (error) {
      console.error('Deal API Error:', error);
      return [];
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
   * Trigger Marketplace Ingestion
   */
  async ingestMarketplace(name?: string): Promise<any> {
    const url = name ? `${API_BASE_URL}/ingest/marketplace?marketplace_name=${encodeURIComponent(name)}` : `${API_BASE_URL}/ingest/marketplace`;
    const response = await fetch(url, { method: 'POST' });
    if (!response.ok) throw new Error('Marketplace ingestion failed');
    return await response.json();
  },

  /**
   * Trigger Conference Ingestion
   */
  async ingestConference(name: string): Promise<any> {
    const response = await fetch(`${API_BASE_URL}/ingest/conference?conference_name=${encodeURIComponent(name)}`, {
      method: 'POST'
    });
    if (!response.ok) throw new Error('Conference ingestion failed');
    return await response.json();
  },

  /**
   * Trigger Ranking List Ingestion
   */
  async ingestRanking(name: string): Promise<any> {
    const response = await fetch(`${API_BASE_URL}/ingest/ranking?list_name=${encodeURIComponent(name)}`, {
      method: 'POST'
    });
    if (!response.ok) throw new Error('Ranking ingestion failed');
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
  },

  /**
   * Trigger AI Deep-Dive Analysis
   */
  async analyzeCompany(companyName: string): Promise<any> {
    const response = await fetch(`${API_BASE_URL}/analyze/${encodeURIComponent(companyName)}`, {
      method: 'POST'
    });
    if (!response.ok) throw new Error('Deep-dive analysis failed');
    return await response.json();
  },

  /**
   * Upload and process a custom Excel/CSV file
   */
  async uploadFile(file: File): Promise<any> {
    const formData = new FormData();
    formData.append('file', file);

    const response = await fetch(`${API_BASE_URL}/ingest/upload`, {
      method: 'POST',
      body: formData,
    });
    
    let data;
    try {
      data = await response.json();
    } catch (e) {
      throw new Error(`Server returned invalid response: ${response.statusText}`);
    }

    if (!response.ok) {
      throw new Error(data.detail || data.message || 'File upload failed');
    }
    return data;
  },

  /**
   * Bulk enrich all missing contacts in the universe
   */
  async enrichUniverse(): Promise<any> {
    const response = await fetch(`${API_BASE_URL}/ingest/enrich-universe`, {
      method: 'POST'
    });
    if (!response.ok) throw new Error('Bulk enrichment failed');
    return await response.json();
  }
};
