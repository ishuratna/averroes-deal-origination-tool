import { CompanyTarget, ActivityEntry, DealOwner, DealTrack, EmailDoc, RespondedResponse, ReplyRuleResult } from "../types";

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

  // ── Responded page: triage queues + ownership ──────────────────────────
  // Process reference: docs/Averroes_Deal_Pipeline_Process.pdf
  async getResponded(): Promise<RespondedResponse> {
    const response = await apiFetch(`${API_BASE_URL}/responded`);
    if (!response.ok) throw new Error('Failed to load the Responded queue');
    return await response.json();
  },

  // Open a stored Companies House filing PDF.
  //
  // Fetched through the authenticated layer and opened as a blob, NOT linked
  // directly. A plain <a href> cannot send our auth header, which is why
  // /ch-pdf/ used to be exempt from sign-in — leaving an endpoint that revealed
  // which companies are in the pipeline to anyone who guessed a name. Fetching it
  // here keeps the endpoint behind sign-in and still opens in a new tab.
  async openChFilingPdf(name: string): Promise<void> {
    const response = await apiFetch(`${API_BASE_URL}/ch-pdf/${encodeURIComponent(name)}`);
    if (!response.ok) {
      const detail = await response.json().catch(() => null);
      throw new Error(detail?.detail || 'Could not load the filing PDF');
    }
    const url = URL.createObjectURL(await response.blob());
    window.open(url, '_blank', 'noopener,noreferrer');
    // Give the new tab time to load before releasing the object URL; revoking it
    // immediately leaves a blank tab.
    setTimeout(() => URL.revokeObjectURL(url), 60_000);
  },

  // The 14-day follow-up: the approved fixed template, threaded under the
  // original outreach subject. Zero AI, so opening it costs nothing.
  async getFollowupDraft(name: string): Promise<{ to: string; subject: string; body: string }> {
    const response = await apiFetch(`${API_BASE_URL}/outreach/followup-draft/${encodeURIComponent(name)}`);
    if (!response.ok) {
      const detail = await response.json().catch(() => null);
      throw new Error(detail?.detail || 'Failed to load the follow-up template');
    }
    return await response.json();
  },

  // Compose (Responded and beyond): recipient and subject come from the
  // CONVERSATION — the address their last genuine reply came from, threaded
  // under its subject. Body stays blank; only Ishu knows what to say next.
  async getComposeDraft(name: string): Promise<{ to: string; subject: string; body: string }> {
    const response = await apiFetch(`${API_BASE_URL}/outreach/compose-draft/${encodeURIComponent(name)}`);
    if (!response.ok) {
      const detail = await response.json().catch(() => null);
      throw new Error(detail?.detail || 'Failed to load the compose draft');
    }
    return await response.json();
  },

  // ── Email documents: files founders attached to their emails ────────────
  async getEmailDocs(name: string): Promise<{ documents: EmailDoc[] }> {
    const response = await apiFetch(`${API_BASE_URL}/company/${encodeURIComponent(name)}/email-docs`);
    if (!response.ok) return { documents: [] };
    return await response.json();
  },

  // Authenticated blob open, same pattern as CH filing PDFs: these are
  // founders' own files, so the endpoint stays behind sign-in and a plain
  // link can never serve them.
  async openEmailDoc(gcsPath: string): Promise<void> {
    const response = await apiFetch(`${API_BASE_URL}/email-doc/download?path=${encodeURIComponent(gcsPath)}`);
    if (!response.ok) {
      const detail = await response.json().catch(() => null);
      throw new Error(detail?.detail || 'Could not load the document');
    }
    const url = URL.createObjectURL(await response.blob());
    window.open(url, '_blank', 'noopener,noreferrer');
    setTimeout(() => URL.revokeObjectURL(url), 60_000);
  },

  // ── The reply rule ─────────────────────────────────────────────────────
  // Qualified = not emailed. Contacted = emailed, no genuine reply yet.
  // Responded = emailed and they replied. An out-of-office is not a reply.
  //
  // Defaults to a preview so a caller can show the user what would change
  // before anything moves. `confirm` carries the names the user has agreed to.
  async reconcileReplyRule(apply = false, confirm: string[] = []): Promise<ReplyRuleResult> {
    const qs = new URLSearchParams({ dry_run: apply ? '0' : '1' });
    if (confirm.length) qs.set('confirm', confirm.join(','));
    const response = await apiFetch(`${API_BASE_URL}/reply-rule/reconcile?${qs}`, { method: 'POST' });
    if (!response.ok) {
      const detail = await response.json().catch(() => null);
      throw new Error(detail?.detail || 'Failed to reconcile the reply rule');
    }
    return await response.json();
  },

  // "Keep it in Responded" — the user knows a reply exists that the mailbox
  // does not. Pins the company so the rule never asks about it again.
  async setReplyExempt(name: string, on = true): Promise<any> {
    const response = await apiFetch(
      `${API_BASE_URL}/company/${encodeURIComponent(name)}/reply-exempt?on=${on ? 1 : 0}`,
      { method: 'PUT' });
    if (!response.ok) {
      const detail = await response.json().catch(() => null);
      throw new Error(detail?.detail || 'Failed to record the confirmation');
    }
    return await response.json();
  },

  // Ishu's click that moves a conversation between Nurture and Assignment
  // ready. A human decision by design — the "probably ready" hint suggests,
  // never acts.
  async setAssignmentReady(name: string, on = true): Promise<any> {
    const response = await apiFetch(
      `${API_BASE_URL}/company/${encodeURIComponent(name)}/assignment-ready?on=${on ? 1 : 0}`,
      { method: 'PUT' });
    if (!response.ok) {
      const detail = await response.json().catch(() => null);
      throw new Error(detail?.detail || 'Failed to move the company');
    }
    return await response.json();
  },

  async triageCompany(name: string, track: DealTrack, owner?: DealOwner | ''): Promise<any> {
    const body: Record<string, unknown> = { track };
    if (owner !== undefined) body.owner = owner;
    const response = await apiFetch(`${API_BASE_URL}/company/${encodeURIComponent(name)}/triage`, {
      method: 'PUT',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(body),
    });
    if (!response.ok) {
      const detail = await response.json().catch(() => null);
      throw new Error(detail?.detail || 'Failed to record the triage decision');
    }
    return await response.json();
  },

  async setCompanyOwner(name: string, owner: DealOwner | ''): Promise<any> {
    const response = await apiFetch(`${API_BASE_URL}/company/${encodeURIComponent(name)}/owner`, {
      method: 'PUT',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ owner }),
    });
    if (!response.ok) {
      const detail = await response.json().catch(() => null);
      throw new Error(detail?.detail || 'Failed to assign the owner');
    }
    return await response.json();
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

  // Companies House SIC-code registry search — streamed like /sources/refresh
  // (16 SIC codes each paged to CH's own ceiling takes longer than the other
  // single-page scrapers, so this holds the connection open with heartbeats).
  async ingestChSic(): Promise<any> {
    const response = await apiFetch(`${API_BASE_URL}/ingest/ch-sic`, { method: 'POST' });
    const text = await response.text();
    if (!response.ok) { try { throw new Error(JSON.parse(text).detail); } catch (e: any) { throw new Error(e?.message || 'Companies House SIC search failed'); } }
    const lines = text.trim().split('\n');
    const last = (lines[lines.length - 1] || '').trim();
    if (!last || !last.startsWith('{')) {
      throw new Error('The server was still working when the connection closed (request timeout). The work may have completed anyway — reload to check, or run it again.');
    }
    const data = JSON.parse(last);
    if (data.status === 'Error') throw new Error(data.message || 'Companies House SIC search failed');
    return data;
  },

  async enrichCompany(companyName: string): Promise<CompanyTarget> {
    const response = await apiFetch(`${API_BASE_URL}/enrich/${encodeURIComponent(companyName)}`, { method: 'POST' });
    if (!response.ok) throw new Error('Enrichment failed');
    return await response.json();
  },

  async uploadFile(file: File): Promise<any> {
    // Streamed endpoint: heartbeat spaces while parsing/saving big files,
    // final line = JSON result (survives silent-connection-killing networks).
    const formData = new FormData();
    formData.append('file', file);
    const response = await apiFetch(`${API_BASE_URL}/ingest/upload`, { method: 'POST', body: formData });
    const text = await response.text();
    if (!response.ok) {
      try { throw new Error(JSON.parse(text).detail); }
      catch (e: any) { throw new Error(e?.message || 'File upload failed'); }
    }
    const lines = text.trim().split('\n');
    const last = (lines[lines.length - 1] || '').trim();
    if (!last || !last.startsWith('{')) {
      throw new Error('The server was still working when the connection closed (request timeout). The work may have completed anyway — reload to check, or run it again.');
    }
    const data = JSON.parse(last);
    if (data.status === 'Error') throw new Error(data.detail || 'File upload failed');
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

  async smartUploadPreview(file: globalThis.File, kind: string = 'companies'): Promise<any> {
    const form = new FormData();
    form.append('file', file);
    const response = await apiFetch(`${API_BASE_URL}/upload/smart/preview?kind=${kind}`, { method: 'POST', body: form });
    const text = await response.text();
    if (!response.ok) { try { throw new Error(JSON.parse(text).detail); } catch (e: any) { throw new Error(e?.message || 'Analysis failed'); } }
    const lines = text.trim().split('\n');
    const last = (lines[lines.length - 1] || '').trim();
    if (!last || !last.startsWith('{')) {
      throw new Error('The server was still working when the connection closed (request timeout). The work may have completed anyway — reload to check, or run it again.');
    }
    const data = JSON.parse(last);
    if (data.status === 'Error') throw new Error(data.detail || 'Analysis failed');
    return data;
  },

  async smartUploadConfirm(label: string, companies: any[], kind: string = 'companies'): Promise<any> {
    const response = await apiFetch(`${API_BASE_URL}/upload/smart/confirm`, {
      method: 'POST', headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ label, companies, kind }),
    });
    const data = await response.json();
    if (!response.ok) throw new Error(data.detail || 'Ingest failed');
    return data;
  },

  async sourcePreview(url: string, kind: string = 'companies'): Promise<any> {
    // Heartbeat-streamed: spaces while working, final line = JSON
    const response = await apiFetch(`${API_BASE_URL}/sources/preview`, {
      method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ url, kind }),
    });
    const text = await response.text();
    if (!response.ok) { try { throw new Error(JSON.parse(text).detail); } catch (e: any) { throw new Error(e?.message || 'Preview failed'); } }
    const lines = text.trim().split('\n');
    const last = (lines[lines.length - 1] || '').trim();
    if (!last || !last.startsWith('{')) {
      throw new Error('The server was still working when the connection closed (request timeout). The work may have completed anyway — reload to check, or run it again.');
    }
    const data = JSON.parse(last);
    if (data.status === 'Error') throw new Error(data.detail || 'Preview failed');
    return data;
  },

  async sourceConfirm(url: string, label: string, companies: any[], kind: string = 'companies'): Promise<any> {
    const response = await apiFetch(`${API_BASE_URL}/sources/confirm`, {
      method: 'POST', headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ url, label, companies, kind }),
    });
    const data = await response.json();
    if (!response.ok) throw new Error(data.detail || 'Ingest failed');
    return data;
  },

  async listSources(kind: string = ''): Promise<any> {
    const response = await apiFetch(`${API_BASE_URL}/sources/list${kind ? `?kind=${kind}` : ''}`);
    if (!response.ok) return { sources: [] };
    return await response.json();
  },

  async sourceRefresh(url: string): Promise<any> {
    const response = await apiFetch(`${API_BASE_URL}/sources/refresh`, {
      method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ url }),
    });
    const text = await response.text();
    if (!response.ok) { try { throw new Error(JSON.parse(text).detail); } catch (e: any) { throw new Error(e?.message || 'Refresh failed'); } }
    const lines = text.trim().split('\n');
    const last = (lines[lines.length - 1] || '').trim();
    if (!last || !last.startsWith('{')) {
      throw new Error('The server was still working when the connection closed (request timeout). The work may have completed anyway — reload to check, or run it again.');
    }
    const data = JSON.parse(last);
    if (data.status === 'Error') throw new Error(data.detail || 'Refresh failed');
    return data;
  },

  // The agreed reminder thresholds. days = they have not answered our email
  // (Contacted, 14 days, overridden by a stated out-of-office return date).
  // replyDays = they wrote and we have not answered (Responded, 7 days).
  async getFollowups(days: number = 14, replyDays: number = 7): Promise<any> {
    const response = await apiFetch(`${API_BASE_URL}/followups?days=${days}&reply_days=${replyDays}`);
    if (!response.ok) return { count: 0, followups: [] };
    return await response.json();
  },

  // ── Quick Tools: Company Deep Research ──
  // TWO steps on purpose: identify+seed is seconds, SmartFill is minutes.
  // One combined request exceeded the Cloud Run timeout and the stream got
  // cut mid-flight. Step 2 reuses the same smartFillBatch the Universe uses.
  async quickResearchIdentify(query: string): Promise<any> {
    const response = await apiFetch(`${API_BASE_URL}/quick-research/identify`, {
      method: 'POST', headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ query }),
    });
    const data = await response.json().catch(() => ({}));
    if (!response.ok) throw new Error(data.detail || 'Identification failed');
    if (data.status === 'Error') throw new Error(data.detail || 'Identification failed');
    return data;
  },

  async quickResearchIdentifyDocument(file: globalThis.File): Promise<any> {
    const form = new FormData();
    form.append('file', file);
    const response = await apiFetch(`${API_BASE_URL}/quick-research/document`, { method: 'POST', body: form });
    const data = await response.json().catch(() => ({}));
    if (!response.ok) throw new Error(data.detail || 'Identification failed');
    if (data.status === 'Error') throw new Error(data.detail || 'Identification failed');
    return data;
  },

  async hideCompanies(names: string[]): Promise<any> {
    // SOFT delete: drops them from the Master Universe view; rows stay in BigQuery.
    const response = await apiFetch(`${API_BASE_URL}/companies/hide`, {
      method: 'POST', headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ names, created_by: 'Ishu Ratna' }),
    });
    if (!response.ok) throw new Error('Remove failed');
    return await response.json();
  },

  async unhideCompanies(names: string[]): Promise<any> {
    const response = await apiFetch(`${API_BASE_URL}/companies/unhide`, {
      method: 'POST', headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ names, created_by: 'Ishu Ratna' }),
    });
    if (!response.ok) throw new Error('Restore failed');
    return await response.json();
  },

  async getHiddenCompanies(): Promise<any> {
    const response = await apiFetch(`${API_BASE_URL}/companies/hidden`);
    if (!response.ok) return { count: 0, companies: [] };
    return await response.json();
  },

  async getCompanyFull(name: string): Promise<any> {
    // Full record for one company (the universe list is slim at 13k rows;
    // profiles fetch their depth on open).
    const response = await apiFetch(`${API_BASE_URL}/company/${encodeURIComponent(name)}/full`);
    if (!response.ok) throw new Error('Failed to load company record');
    return await response.json();
  },

  async getAnalytics(refresh: boolean = false): Promise<any> {
    const response = await apiFetch(`${API_BASE_URL}/analytics${refresh ? '?refresh=1' : ''}`);
    if (!response.ok) throw new Error('Failed to load analytics');
    return await response.json();
  },

  async mineAllInvestors(): Promise<any> {
    // Streams heartbeat spaces while mining; the final line is the JSON summary.
    const response = await apiFetch(`${API_BASE_URL}/investors/mine-all`, { method: 'POST' });
    const text = await response.text();
    if (!response.ok) {
      try { throw new Error(JSON.parse(text).detail || 'Investor mining failed'); }
      catch (e: any) { throw new Error(e?.message || `Mining failed: ${response.statusText}`); }
    }
    const lines = text.trim().split('\n');
    let data;
    try { data = JSON.parse(lines[lines.length - 1]); } catch { throw new Error('Mining response unreadable — it may still have completed server-side'); }
    if (data.status === 'Error') throw new Error(data.detail || 'Investor mining failed');
    return data;
  },

  async getCompanyConnections(companyName: string): Promise<any> {
    const response = await apiFetch(`${API_BASE_URL}/connections/company/${encodeURIComponent(companyName)}`);
    if (!response.ok) return { investors: [], siblings: [] };
    return await response.json();
  },

  async getInvestorConnections(investorName: string): Promise<any> {
    const response = await apiFetch(`${API_BASE_URL}/connections/investor/${encodeURIComponent(investorName)}`);
    if (!response.ok) return { companies: [], co_investors: [] };
    return await response.json();
  },

  async generateIcMemo(companyName: string): Promise<any> {
    const response = await apiFetch(`${API_BASE_URL}/company/${encodeURIComponent(companyName)}/ic-memo`, { method: 'POST' });
    let data;
    try { data = await response.json(); } catch (e) { throw new Error(`IC memo failed: ${response.statusText}`); }
    if (!response.ok) { throw new Error(data.detail || 'IC memo generation failed'); }
    return data;
  },

  // The 4-slide IC screening deck (Blink CIM format). Generated fresh on each
  // click - company facts from the record, market context AI-researched and
  // tagged - and downloaded as a .pptx. Takes ~30-60s (one grounded AI call).
  async downloadIcMemoDeck(companyName: string): Promise<void> {
    const response = await apiFetch(`${API_BASE_URL}/company/${encodeURIComponent(companyName)}/ic-memo-deck`, { method: 'POST' });
    if (!response.ok) {
      let detail = 'IC deck generation failed';
      try { detail = (await response.json()).detail || detail; } catch {}
      throw new Error(detail);
    }
    const blob = await response.blob();
    const url = URL.createObjectURL(blob);
    const a = document.createElement('a');
    a.href = url;
    a.download = `IC_Deck_${companyName.replace(/[^A-Za-z0-9_-]+/g, '_')}.pptx`;
    document.body.appendChild(a);
    a.click();
    a.remove();
    URL.revokeObjectURL(url);
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
