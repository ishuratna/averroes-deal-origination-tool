import { CompanyTarget } from "../types";

const API_BASE_URL = process.env.NEXT_PUBLIC_API_URL || 'https://averroes-deal-backend-890361705054.europe-west1.run.app';

export const dealApi = {
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

  async ingestMarketplace(name?: string): Promise<any> {
    const url = name ? `${API_BASE_URL}/ingest/marketplace?marketplace_name=${encodeURIComponent(name)}` : `${API_BASE_URL}/ingest/marketplace`;
    const response = await fetch(url, { method: 'POST' });
    if (!response.ok) throw new Error('Marketplace ingestion failed');
    return await response.json();
  },

  async ingestConference(name: string): Promise<any> {
    const response = await fetch(`${API_BASE_URL}/ingest/conference?conference_name=${encodeURIComponent(name)}`, { method: 'POST' });
    if (!response.ok) throw new Error('Conference ingestion failed');
    return await response.json();
  },

  async ingestRanking(name: string): Promise<any> {
    const response = await fetch(`${API_BASE_URL}/ingest/ranking?list_name=${encodeURIComponent(name)}`, { method: 'POST' });
    if (!response.ok) throw new Error('Ranking ingestion failed');
    return await response.json();
  },

  async ingestDirectory(sourceName: string, maxPages: number = 20): Promise<any> {
    const response = await fetch(`${API_BASE_URL}/ingest/directory?source_name=${encodeURIComponent(sourceName)}&max_pages=${maxPages}`, { method: 'POST' });
    if (!response.ok) throw new Error('Directory ingestion failed');
    return await response.json();
  },

  async enrichCompany(companyName: string): Promise<CompanyTarget> {
    const response = await fetch(`${API_BASE_URL}/enrich/${encodeURIComponent(companyName)}`, { method: 'POST' });
    if (!response.ok) throw new Error('Enrichment failed');
    return await response.json();
  },

  async analyzeCompany(companyName: string): Promise<any> {
    const response = await fetch(`${API_BASE_URL}/analyze/${encodeURIComponent(companyName)}`, { method: 'POST' });
    if (!response.ok) throw new Error('Deep-dive analysis failed');
    return await response.json();
  },

  async uploadFile(file: File): Promise<any> {
    const formData = new FormData();
    formData.append('file', file);
    const response = await fetch(`${API_BASE_URL}/ingest/upload`, { method: 'POST', body: formData });
    let data;
    try { data = await response.json(); } catch (e) { throw new Error(`Server returned invalid response: ${response.statusText}`); }
    if (!response.ok) { throw new Error(data.detail || data.message || 'File upload failed'); }
    return data;
  },

  async smartFill(companyName: string): Promise<any> {
    const response = await fetch(`${API_BASE_URL}/smartfill/${encodeURIComponent(companyName)}`, { method: 'POST' });
    let data;
    try { data = await response.json(); } catch (e) { throw new Error(`SmartFill failed: ${response.statusText}`); }
    if (!response.ok) { throw new Error(data.detail || 'SmartFill failed'); }
    return data;
  }
};
