'use client';

// Quick Tools -> Company Deep Research.
// Type a company name (or paste text about it), or drop a document. The
// backend identifies the company, seeds one ordinary universe row, then runs
// the SAME SmartFill workflow as the Universe buttons — so the result is a
// normal company record, shown in the SAME company card, and saved.

import React, { useRef, useState } from 'react';
import { dealApi } from '../../services/api';
import { CompanyTarget } from '../../types';
import AuthGate from '../../components/AuthGate';
import SideNav from '../../components/SideNav';
import CompanyProfile from '../../components/CompanyProfile';

export default function QuickTools() {
  return <AuthGate><QuickToolsInner /></AuthGate>;
}

function QuickToolsInner() {
  const [query, setQuery] = useState('');
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState('');
  const [result, setResult] = useState<any | null>(null);
  const [showCard, setShowCard] = useState(false);
  const fileRef = useRef<HTMLInputElement>(null);

  const run = async (mode: 'text' | 'file') => {
    setBusy(true); setError(''); setResult(null); setShowCard(false);
    try {
      let r;
      if (mode === 'file') {
        const f = fileRef.current?.files?.[0];
        if (!f) { setError('Choose a document first.'); setBusy(false); return; }
        r = await dealApi.quickResearchDocument(f);
      } else {
        if (!query.trim()) { setError('Type a company name, or paste text about it.'); setBusy(false); return; }
        r = await dealApi.quickResearch(query.trim());
      }
      setResult(r);
      setShowCard(true);
      if (fileRef.current) fileRef.current.value = '';
    } catch (e: any) {
      setError(e?.message || 'Research failed');
    } finally { setBusy(false); }
  };

  const co: CompanyTarget | null = result?.company?.name ? (result.company as CompanyTarget) : null;
  const ident = result?.identification;

  return (
    <div className="layout-wrapper">
      <SideNav active="quick-tools" />
      <main className="main-content" style={{ marginLeft: 260, flex: 1, padding: '1.75rem 2rem', maxWidth: 'calc(100vw - 260px)', minWidth: 0 }}>
        <div className="an-header">
          <div>
            <h1 className="an-title">Company Deep Research</h1>
            <p className="an-sub">
              Give a company name, paste text about it, or drop a document. The tool identifies
              the company, then runs the standard SmartFill workflow: Companies House registry,
              filed financials, ownership and cap table, contacts, fit score. The result is saved
              as a normal universe record you can open anywhere in the tool.
            </p>
          </div>
        </div>

        <section className="an-card">
          <h2 className="an-card-title">1 · Company name or pasted text</h2>
          <div className="qt-row">
            <textarea className="qt-input" rows={2} value={query} disabled={busy}
              placeholder="e.g. Summize  —  or paste a paragraph / teaser text about the company"
              onChange={e => setQuery(e.target.value)}
              onKeyDown={e => { if (e.key === 'Enter' && !e.shiftKey) { e.preventDefault(); run('text'); } }} />
            <button className="an-refresh" disabled={busy} onClick={() => run('text')}>
              {busy ? 'Researching…' : 'Run deep research'}
            </button>
          </div>

          <h2 className="an-card-title" style={{ marginTop: '1.2rem' }}>2 · Or upload a document</h2>
          <div className="qt-row">
            <input ref={fileRef} type="file" accept=".pdf,.docx,.txt,.md,.csv,.xlsx,.xls" className="dq-file" disabled={busy} />
            <button className="an-refresh" disabled={busy} onClick={() => run('file')}>
              {busy ? 'Researching…' : 'Identify & research'}
            </button>
          </div>

          {busy && (
            <p className="qt-note">
              Working… identification, then the full SmartFill pass (registry, filings, cap table,
              scoring). This usually takes 1–3 minutes; the connection is kept alive with heartbeats.
            </p>
          )}
          {error && <div className="an-error">{error}</div>}
        </section>

        {result && (
          <section className="an-card">
            <h2 className="an-card-title">Result</h2>
            {ident && (
              <div className="qt-ident">
                <span><b>Identified:</b> {ident.name || '—'}</span>
                {ident.confidence && <span className="ikb-chip">confidence {ident.confidence}</span>}
                {result.seeded ? <span className="ikb-chip">new record created</span>
                               : <span className="ikb-chip">existing record updated</span>}
                {ident.notes && <span className="qt-muted">{ident.notes}</span>}
              </div>
            )}
            {result.smartfill_error && (
              <div className="an-warn">SmartFill reported: {result.smartfill_error}</div>
            )}
            {co ? (
              <>
                <div className="qt-summary">
                  <div className="an-kpi">
                    <span className="an-kpi-label">Fit score</span>
                    <span className="an-kpi-value">{co.averroes_fit_score != null ? Math.round(co.averroes_fit_score * 100) : '—'}</span>
                    <span className="an-kpi-foot">{co.revenue_band || 'band not set'}</span>
                  </div>
                  <div className="an-kpi">
                    <span className="an-kpi-label">Revenue (filed FY1)</span>
                    <span className="an-kpi-value">{co.revenue_y1 ? `£${(co.revenue_y1 / 1e6).toFixed(2)}M` : (co.revenue_estimate_m ? `£${co.revenue_estimate_m}M*` : '—')}</span>
                    <span className="an-kpi-foot">{co.revenue_y1 ? (co.revenue_y1_date || 'filed') : (co.revenue_estimate_m ? '*estimate' : 'not disclosed')}</span>
                  </div>
                  <div className="an-kpi">
                    <span className="an-kpi-label">Companies House</span>
                    <span className="an-kpi-value" style={{ fontSize: '1rem' }}>{co.ch_company_number || co.registration_number || '—'}</span>
                    <span className="an-kpi-foot">{co.ch_status || co.ch_official_name || ''}</span>
                  </div>
                  <div className="an-kpi">
                    <span className="an-kpi-label">Founder %</span>
                    <span className="an-kpi-value">{co.ch_founder_pct != null ? `${co.ch_founder_pct}%` : '—'}</span>
                    <span className="an-kpi-foot">{co.ch_ownership_verified || 'ownership not verified'}</span>
                  </div>
                </div>
                <button className="an-refresh" onClick={() => setShowCard(true)}>Open full company card</button>
                <span className="qt-muted" style={{ marginLeft: '0.7rem' }}>
                  Saved as “{co.name}” (source: {co.source}) — also visible in Master Universe.
                </span>
              </>
            ) : (
              <p className="an-empty">No company record returned.</p>
            )}
          </section>
        )}

        {showCard && co && (
          <CompanyProfile
            companies={[co]}
            index={0}
            onClose={() => setShowCard(false)}
            onNavigate={() => {}}
            onChanged={() => {}}
          />
        )}
      </main>
    </div>
  );
}
