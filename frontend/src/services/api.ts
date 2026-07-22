import { CompanyTarget, ActivityEntry } from "../types";

export const API_BASE_URL = process.env.NEXT_PUBLIC_API_URL || 'https://averroes-deal-backend-890361705054.europe-west1.run.app';

// Authenticated fetch: attaches the Google ID token. On a missing/expired
// session it redirects to sign-in cleanly and returns a never-resolving
// promise, so callers' catch blocks don't fire misleading error alerts.
function _sessionRedirect(): Promise<Response> {
  if (typeof window !== 'undefined') {
    localStorage.removeItem('averroes_id_token');
    sessionStorage.setItem('averroes_session_note', 'Your session expired — please sign in again.');
    window.location.reload();
  }
  return new Promise<Response>(() => {});  // never resolves; page is reloading
}

function _tokenValid(token: string | null): boolean {
  if (!token) return false;
  try {
    if (token.startsWith('avr.')) {
      // 12h session token: avr.<b64(email|exp)>.<sig>
      const b64 = token.slice(4).split('.')[0];
      const payload = atob(b64.replace(/-/g, '+').replace(/_/g, '/'));
      const exp = parseInt(payload.split('|').pop() || '0', 10) * 1000;
      return exp > Date.now() + 30_000;
    }
    const payload = JSON.parse(atob(token.split('.')[1]));
    return !payload.exp || payload.exp * 1000 > Date.now() + 30_000;
  } catch { return false; }
}

async function apiFetch(url: string, options: RequestInit = {}): Promise<Response> {
  const token = typeof window !== 'undefined' ? localStorage.getItem('averroes_id_token') : null;
  const headers: Record<string, string> = { ...(options.headers as Record<string, string> || {}) };

  // Pre-check: if auth is known to be active and the token is missing/expired,
  // go straight to sign-in without a doomed network call.
  if (typeof window !== 'undefined' && sessionStorage.getItem('averroes_auth_on') === '1' && !_tokenValid(token)) {
    return _sessionRedirect();
  }
  if (token) headers['Authorization'] = `Bearer ${token}`;

  const response = await fetch(url, { ...options, headers });
  if (response.status === 401) {
    return _sessionRedirect();
  }
  return response;
}

export const dealApi = {
  async getPipeline(): Promise<CompanyTarget[]> {
    try {
      const response = await apiFetch(`${API_BASE_URL}/pipeline`);
      if (!response.ok) throw new Error('Failed to fetch pipeline');
      return await response.json();
    } catch (error) {
      console.error('Deal API Error:', error);
      return [];
    }
  },

  async getUniverse(): Promise<CompanyTarget[]> {
    try {
      const response = await apiFetch(`${API_BASE_URL}/universe`);
      if (!response.ok) throw new Error('Failed to fetch universe');
      return await response.json();
    } catch (error) {
      console.error('Universe API Error:', error);
      return [];
    }
  },

  async ingestMarketplace(name?: string): Promise<any> {
    const url = name ? `${API_BASE_URL}/ingest/marketplace?marketplace_name=${encodeURIComponent(name)}` : `${API_BASE_URL}/ingest/marketplace`;
    const response = await apiFetch(url, { method: 'POST' });
    if (!response.ok) throw new Error('Marketplace ingestion failed');
    return await response.json();
  },

  async ingestConference(name: string): Promise<any> {
    const response = await apiFetch(`${API_BASE_URL}/ingest/conference?conference_name=${encodeURIComponent(name)}`, { method: 'POST' });
    if (!response.ok) throw new Error('Conference ingestion failed');
    return await response.json();
  },

  async ingestRanking(name: string): Promise<any> {
    const response = await apiFetch(`${API_BASE_URL}/ingest/ranking?list_name=${encodeURIComponent(name)}`, { method: 'POST' });
    if (!response.ok) throw new Error('Ranking ingestion failed');
    return await response.json();
  },

  async ingestNetwork(sourceName: string): Promise<any> {
    const response = await apiFetch(`${API_BASE_URL}/ingest/network?source_name=${encodeURIComponent(sourceName)}`, { method: 'POST' });
    if (!response.ok) throw new Error('Network ingestion failed');
    return await response.json();
  },

  async ingestDirectory(sourceName: string, maxPages: number = 20): Promise<any> {
    const response = await apiFetch(`${API_BASE_URL}/ingest/directory?source_name=${encodeURIComponent(sourceName)}&max_pages=${maxPages}`, { method: 'POST' });
    if (!response.ok) throw new Error('Directory ingestion failed');
    return await response.json();
  },

  async enrichCompany(companyName: string): Promise<CompanyTarget> {
    const response = await apiFetch(`${API_BASE_URL}/enrich/${encodeURIComponent(companyName)}`, { method: 'POST' });
    if (!response.ok) throw new Error('Enrichment failed');
    return await response.json();
  },

  async uploadFile(file: File): Promise<any> {
    const formData = new FormData();
    formData.append('file', file);
    const response = await apiFetch(`${API_BASE_URL}/ingest/upload`, { method: 'POST', body: formData });
    let data;
    try { data = await response.json(); } catch (e) { throw new Error(`Server returned invalid response: ${response.statusText}`); }
    if (!response.ok) { throw new Error(data.detail || data.message || 'File upload failed'); }
    return data;
  },

  // ── Investor (LP) database ──
  async getInvestors(): Promise<any[]> {
    const response = await apiFetch(`${API_BASE_URL}/investors`);
    if (!response.ok) throw new Error('Failed to load investors');
    return await response.json();
  },

  async mineInvestors(minFit: number = 0.4): Promise<any> {
    const response = await apiFetch(`${API_BASE_URL}/investors/mine?min_fit=${minFit}`, { method: 'POST' });
    if (!response.ok) throw new Error('Investor mining failed');
    return await response.json();
  },

  async investorFill(name: string): Promise<any> {
    const response = await apiFetch(`${API_BASE_URL}/investorfill/${encodeURIComponent(name)}`, { method: 'POST' });
    if (!response.ok) {
      const err = await response.json().catch(() => ({}));
      throw new Error(err.detail || 'InvestorFill failed');
    }
    return await response.json();
  },

  async getInvestorFillEligible(): Promise<any> {
    const response = await apiFetch(`${API_BASE_URL}/investorfill/eligible`);
    if (!response.ok) throw new Error('Failed to load InvestorFill eligibility');
    return await response.json();
  },

  async draftInvestorOutreach(name: string): Promise<any> {
    const response = await apiFetch(`${API_BASE_URL}/investors/outreach/draft/${encodeURIComponent(name)}`, { method: 'POST' });
    if (!response.ok) throw new Error('LP outreach draft failed');
    return await response.json();
  },

  async sendInvestorOutreach(to: string, subject: string, body: string, investorName?: string): Promise<any> {
    const response = await apiFetch(`${API_BASE_URL}/investors/outreach/send`, {
      method: 'POST', headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ to, subject, body, investor_name: investorName }),
    });
    if (!response.ok) {
      const err = await response.json().catch(() => ({}));
      throw new Error(err.detail || 'LP outreach send failed');
    }
    return await response.json();
  },

  async scrapeInvestors(sourceName: string): Promise<any> {
    const response = await apiFetch(`${API_BASE_URL}/investors/scrape?source_name=${encodeURIComponent(sourceName)}`, { method: 'POST' });
    if (!response.ok) {
      const err = await response.json().catch(() => ({}));
      throw new Error(err.detail || 'Investor scrape failed');
    }
    return await response.json();
  },

  async uploadInvestorFile(file: File): Promise<any> {
    const formData = new FormData();
    formData.append('file', file);
    const response = await apiFetch(`${API_BASE_URL}/investors/upload`, { method: 'POST', body: formData });
    if (!response.ok) {
      const err = await response.json().catch(() => ({}));
      throw new Error(err.detail || 'Investor upload failed');
    }
    return await response.json();
  },

  async updateInvestorStatus(name: string, status: string): Promise<any> {
    const response = await apiFetch(`${API_BASE_URL}/investors/${encodeURIComponent(name)}/status`, {
      method: 'PUT', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ status }),
    });
    if (!response.ok) throw new Error('Investor status update failed');
    return await response.json();
  },

  async addInvestorNote(name: string, note: string): Promise<any> {
    const response = await apiFetch(`${API_BASE_URL}/investors/${encodeURIComponent(name)}/notes`, {
      method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ note }),
    });
    if (!response.ok) throw new Error('Investor note failed');
    return await response.json();
  },

  async smartEnrich(companyName: string): Promise<any> {
    const response = await apiFetch(`${API_BASE_URL}/smartenrich/${encodeURIComponent(companyName)}`, { method: 'POST' });
    let data;
    try { data = await response.json(); } catch (e) { throw new Error(`SmartEnrich failed: ${response.statusText}`); }
    if (!response.ok) { throw new Error(data.detail || 'SmartEnrich failed'); }
    return data;
  },

  async syncEmails(days: number = 30): Promise<any> {
    const response = await apiFetch(`${API_BASE_URL}/email/sync?days=${days}`, { method: 'POST' });
    let data;
    try { data = await response.json(); } catch (e) { throw new Error(`Email sync failed: ${response.statusText}`); }
    if (!response.ok) { throw new Error(data.detail || 'Email sync failed'); }
    return data;
  },

  async getSmartFillEligible(): Promise<any> {
    const response = await apiFetch(`${API_BASE_URL}/smartfill/eligible`);
    if (!response.ok) throw new Error('Failed to load SmartFill eligibility');
    return await response.json();
  },

  async generateIcMemo(companyName: string): Promise<any> {
    const response = await apiFetch(`${API_BASE_URL}/company/${encodeURIComponent(companyName)}/ic-memo`, { method: 'POST' });
    let data;
    try { data = await response.json(); } catch (e) { throw new Error(`IC memo failed: ${response.statusText}`); }
    if (!response.ok) { throw new Error(data.detail || 'IC memo generation failed'); }
    return data;
  },

  async downloadIcMemoPdf(companyName: string): Promise<void> {
    const response = await apiFetch(`${API_BASE_URL}/company/${encodeURIComponent(companyName)}/ic-memo.pdf`);
    if (!response.ok) {
      let detail = 'PDF download failed';
      try { detail = (await response.json()).detail || detail; } catch {}
      throw new Error(detail);
    }
    const blob = await response.blob();
    const url = URL.createObjectURL(blob);
    const a = document.createElement('a');
    a.href = url;
    a.download = `IC_Memo_${companyName.replace(/[^A-Za-z0-9_-]+/g, '_')}.pdf`;
    document.body.appendChild(a);
    a.click();
    a.remove();
    URL.revokeObjectURL(url);
  },

  async smartFillBatch(names: string[]): Promise<any> {
    // The batch endpoint STREAMS heartbeat spaces while it works (idle
    // connections get killed on some networks) and ends with one JSON line.
    const response = await apiFetch(`${API_BASE_URL}/smartfill/batch`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ names }),
    });
    const text = await response.text();
    if (!response.ok) {
      try { throw new Error(JSON.parse(text).detail || 'Batch failed'); }
      catch (e: any) { throw new Error(e?.message || `Batch failed: ${response.statusText}`); }
    }
    const lines = text.trim().split('\n');
    try { return JSON.parse(lines[lines.length - 1]); }
    catch { throw new Error('Batch response unreadable — the run may still have completed server-side'); }
  },

  async smartFill(companyName: string, bulk: boolean = false): Promise<any> {
    const response = await apiFetch(`${API_BASE_URL}/smartfill/${encodeURIComponent(companyName)}${bulk ? '?bulk=true' : ''}`, { method: 'POST' });
    let data;
    try { data = await response.json(); } catch (e) { throw new Error(`SmartFill failed: ${response.statusText}`); }
    if (!response.ok) { throw new Error(data.detail || 'SmartFill failed'); }
    return data;
  },

  async draftOutreach(companyName: string): Promise<any> {
    const response = await apiFetch(`${API_BASE_URL}/outreach/draft/${encodeURIComponent(companyName)}`, { method: 'POST' });
    let data;
    try { data = await response.json(); } catch (e) { throw new Error(`Draft failed: ${response.statusText}`); }
    if (!response.ok) { throw new Error(data.detail || 'Draft generation failed'); }
    return data;
  },

  async sendOutreach(to: string, subject: string, body: string, companyName?: string): Promise<any> {
    const response = await apiFetch(`${API_BASE_URL}/outreach/send`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ to, subject, body, company_name: companyName }),
    });
    let data;
    try { data = await response.json(); } catch (e) { throw new Error(`Send failed: ${response.statusText}`); }
    if (!response.ok) { throw new Error(data.detail || 'Email send failed'); }
    return data;
  },

  // ── Deal Lifecycle ──────────────────────────────────────────────────────────

  async updateCompanyStatus(companyName: string, status: string, createdBy?: string): Promise<any> {
    const response = await apiFetch(`${API_BASE_URL}/company/${encodeURIComponent(companyName)}/status`, {
      method: 'PUT',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ status, created_by: createdBy || 'Ishu Ratna' }),
    });
    let data;
    try { data = await response.json(); } catch (e) { throw new Error(`Status update failed: ${response.statusText}`); }
    if (!response.ok) { throw new Error(data.detail || 'Status update failed'); }
    return data;
  },

  async addCompanyNote(companyName: string, note: string, createdBy?: string): Promise<any> {
    const response = await apiFetch(`${API_BASE_URL}/company/${encodeURIComponent(companyName)}/notes`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ note, created_by: createdBy || 'Ishu Ratna' }),
    });
    let data;
    try { data = await response.json(); } catch (e) { throw new Error(`Note save failed: ${response.statusText}`); }
    if (!response.ok) { throw new Error(data.detail || 'Note save failed'); }
    return data;
  },

  async getCompanyActivity(companyName: string, limit: number = 50): Promise<{ company: string; activity: ActivityEntry[]; count: number }> {
    const response = await apiFetch(`${API_BASE_URL}/company/${encodeURIComponent(companyName)}/activity?limit=${limit}`);
    if (!response.ok) throw new Error('Failed to fetch activity');
    return await response.json();
  },

  async removeFromPipeline(companyName: string, createdBy?: string): Promise<any> {
    const response = await apiFetch(`${API_BASE_URL}/company/${encodeURIComponent(companyName)}/remove`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ created_by: createdBy || 'Ishu Ratna' }),
    });
    let data;
    try { data = await response.json(); } catch (e) { throw new Error(`Remove failed: ${response.statusText}`); }
    if (!response.ok) { throw new Error(data.detail || 'Remove failed'); }
    return data;
  },

  // ── Qualification Criteria ──────────────────────────────────────────────────

  async getCriteria(): Promise<any> {
    const response = await apiFetch(`${API_BASE_URL}/criteria`);
    if (!response.ok) throw new Error('Failed to fetch criteria');
    return await response.json();
  },

  async chatCriteria(message: string): Promise<any> {
    const response = await apiFetch(`${API_BASE_URL}/criteria/chat`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ message }),
    });
    let data;
    try { data = await response.json(); } catch (e) { throw new Error(`Criteria chat failed: ${response.statusText}`); }
    if (!response.ok) { throw new Error(data.detail || 'Criteria chat failed'); }
    return data;
  },

  async getCompanyEmails(companyName: string): Promise<{ emails: any[] }> {
    try {
      const response = await apiFetch(`${API_BASE_URL}/company/${encodeURIComponent(companyName)}/emails`);
      if (!response.ok) return { emails: [] };
      return await response.json();
    } catch { return { emails: [] }; }
  },

  // ── Deal Intelligence Chat ──────────────────────────────────────────────
  async chat(message: string, history: Array<{ role: string; content: string }>, webSearch?: boolean): Promise<{ reply: string; needs_web_search: boolean; matched: string[] }> {
    const response = await apiFetch(`${API_BASE_URL}/chat`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ message, history, web_search: !!webSearch }),
    });
    let data;
    try { data = await response.json(); } catch (e) { throw new Error(`Chat failed: ${response.statusText}`); }
    if (!response.ok) { throw new Error(data.detail || 'Chat failed'); }
    return data;
  },

  async applyCriteria(criteria: any, updatedBy?: string, requalify?: boolean): Promise<any> {
    const response = await apiFetch(`${API_BASE_URL}/criteria/apply`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ criteria, updated_by: updatedBy || 'Ishu Ratna', requalify: requalify !== false }),
    });
    let data;
    try { data = await response.json(); } catch (e) { throw new Error(`Apply criteria failed: ${response.statusText}`); }
    if (!response.ok) { throw new Error(data.detail || 'Apply criteria failed'); }
    return data;
  },
};
