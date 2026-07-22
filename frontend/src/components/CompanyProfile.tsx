'use client';

// Inven-style full-screen company profile. Replaces CompanyDrawer.
// Self-contained: fetches its own activity + email thread, embeds the shared
// OutreachModal, and supports prev/next browsing through the caller's list
// (← → keys) exactly like Inven's "1 / 500" flow.
// Styling: ALL classes live in globals.css (cp-*) — deliberately no styled-jsx.

import React, { useEffect, useMemo, useState } from 'react';
import { CompanyTarget, ActivityEntry, displayStatus, getRevenueBand, actionBucketInfo } from '../types';
import { dealApi } from '../services/api';
import OutreachModal from './OutreachModal';
import { outreachButtonState } from '../lib/outreach';

interface Props {
  companies: CompanyTarget[];
  index: number;
  onClose: () => void;
  onNavigate: (index: number) => void;
  onChanged: () => void | Promise<void>;
  initialTab?: string;
}

const TABS = ['Summary', 'Financials', 'Ownership', 'People', 'Companies House', 'Outreach', 'IC Memo'] as const;
const NEXT_STAGE: Record<string, string> = {
  Qualified: 'Contacted', Engaged: 'Contacted', Contacted: 'Meeting', Meeting: 'DD', DD: 'Offer',
};

const fmtRaw = (v?: number | null) => (v != null && v !== 0) ? `£${(v / 1e6).toFixed(1)}M` : null;
const fmtM = (v?: number | null) => (v != null && v !== 0) ? `£${v.toFixed(1)}M` : null;
const fmtPct = (v?: number | null) => (v != null) ? `${v > 0 ? '+' : ''}${v.toFixed(1)}%` : null;
const fmtDate = (d?: string | null) => d ? new Date(d).toLocaleDateString('en-GB', { day: 'numeric', month: 'short', year: 'numeric' }) : '';

function feedTag(e: ActivityEntry): { label: string; cls: string } {
  const t = (e.note_text || '').toLowerCase();
  if (e.action_type === 'status_change') return { label: 'Stage', cls: 'cp-tag-stage' };
  if (e.action_type === 'outreach_sent' || t.startsWith('outreach')) return { label: 'Outreach', cls: 'cp-tag-outreach' };
  if (t.includes('reply')) return { label: 'Reply', cls: 'cp-tag-reply' };
  if (t.startsWith('ch watch') || t.includes('smartenrich') || t.includes('smartfill')) return { label: 'Intel', cls: 'cp-tag-filing' };
  if (e.created_by === 'system' || e.created_by === 'band-migration') return { label: 'System', cls: 'cp-tag-system' };
  return { label: 'Note', cls: 'cp-tag-note' };
}

function parseDirectors(s?: string): string[] {
  if (!s) return [];
  return s.split(/\),\s*/).map(d => (d.includes('(') && !d.endsWith(')')) ? d + ')' : d)
    .map(d => d.trim()).filter(Boolean).slice(0, 24);
}

function statusColor(stage: string): string {
  const c: Record<string, string> = {
    Qualified: '#3b82f6', Contacted: '#8b5cf6', Meeting: '#f59e0b', DD: '#ef4444',
    Offer: '#10b981', Won: '#059669', Lost: '#6b7280', Engaged: '#8b5cf6',
    'Under Review': '#d97706', Scraped: '#94a3b8', Uploaded: '#3b82f6', 'Not a Fit': '#ef4444',
  };
  return c[stage] || '#6b7280';
}

// ── Multi-year history helpers ──────────────────────────────────────────────
export function chHistory(company: CompanyTarget): Array<any> {
  try {
    const h = company.ch_history ? JSON.parse(company.ch_history) : null;
    if (h?.years?.length) return [...h.years].sort((a, b) => (a.period_end || '').localeCompare(b.period_end || ''));
  } catch { /* fall through */ }
  return [];
}

// ── Revenue + EBITDA grouped bar chart (pure SVG) ───────────────────────────
function FinChart({ company }: { company: CompanyTarget }) {
  const years = useMemo(() => {
    // Prefer the full CH history (up to 6 periods); fall back to y1-y3 columns
    const hist = chHistory(company).filter(y => y.revenue != null);
    if (hist.length >= 2) {
      return hist.map((y, i) => ({
        label: (y.period_end || '').slice(0, 10),
        rev: y.revenue as number,
        ebitda: (i === hist.length - 1 && company.estimated_ebitda) ? company.estimated_ebitda * 1e6 : null,
      }));
    }
    const ys: Array<{ label: string; rev: number | null; ebitda: number | null }> = [];
    if (company.revenue_y3) ys.push({ label: (company.revenue_y3_date || 'Y-2').slice(0, 10), rev: company.revenue_y3, ebitda: null });
    if (company.revenue_y2) ys.push({ label: (company.revenue_y2_date || 'Y-1').slice(0, 10), rev: company.revenue_y2, ebitda: null });
    if (company.revenue_y1 || company.revenue_m) {
      ys.push({
        label: (company.revenue_y1_date || 'Latest').slice(0, 10),
        rev: company.revenue_y1 || (company.revenue_m ? company.revenue_m * 1e6 : null),
        ebitda: company.estimated_ebitda ? company.estimated_ebitda * 1e6 : null,
      });
    }
    return ys;
  }, [company]);

  if (!years.length) return <p className="cp-empty">No revenue history held for this company yet.</p>;
  const maxAbs = Math.max(...years.flatMap(y => [Math.abs(y.rev || 0), Math.abs(y.ebitda || 0)]), 1);
  const W = 640, H = 220, padL = 8, padB = 24, zero = (H - padB) * 0.82;
  const groupW = (W - padL * 2) / years.length;
  const scale = (v: number) => (v / maxAbs) * (zero - 12);

  return (
    <div>
      <div className="cp-legend"><span className="lg-rev">Revenue</span><span className="lg-ebitda">EBITDA</span></div>
      <svg viewBox={`0 0 ${W} ${H}`} style={{ width: '100%', height: 'auto' }}>
        <line x1={padL} y1={zero} x2={W - padL} y2={zero} stroke="#e2e8f0" strokeWidth="1" />
        {years.map((y, i) => {
          const cx = padL + groupW * i + groupW / 2;
          const bw = Math.min(34, groupW / 4);
          const bars = [];
          if (y.rev != null) {
            const h = Math.abs(scale(y.rev));
            bars.push(<rect key="r" x={cx - bw - 3} y={y.rev >= 0 ? zero - h : zero} width={bw} height={Math.max(h, 2)} rx="3" fill="#1e40af" />);
          }
          if (y.ebitda != null) {
            const h = Math.abs(scale(y.ebitda));
            bars.push(<rect key="e" x={cx + 3} y={y.ebitda >= 0 ? zero - h : zero} width={bw} height={Math.max(h, 2)} rx="3" fill="#60a5fa" />);
          }
          return (
            <g key={i}>
              {bars}
              {y.rev != null && (
                <text x={cx - 3 - bw / 2} y={(y.rev >= 0 ? zero - Math.abs(scale(y.rev)) - 5 : zero + Math.abs(scale(y.rev)) + 12)}
                  textAnchor="middle" fontSize="10" fontWeight="700" fill="#334155">
                  {(y.rev / 1e6).toFixed(1)}M
                </text>
              )}
              <text x={cx} y={H - 6} textAnchor="middle" fontSize="10.5" fill="#94a3b8" fontWeight="600">{y.label}</text>
            </g>
          );
        })}
      </svg>
    </div>
  );
}

// ── Employee development bar chart (CH filings, per year) ───────────────────
function EmpChart({ company }: { company: CompanyTarget }) {
  const years = chHistory(company).filter(y => y.employees != null);
  if (years.length < 2) return null;
  const max = Math.max(...years.map(y => y.employees), 1);
  return (
    <>
      <div className="cp-section-title">Headcount development (Companies House filings)</div>
      <div className="cp-card">
        <svg viewBox={`0 0 640 150`} style={{ width: '100%', height: 'auto' }}>
          {years.map((y, i) => {
            const gw = 624 / years.length;
            const cx = 8 + gw * i + gw / 2;
            const h = (y.employees / max) * 100;
            return (
              <g key={i}>
                <rect x={cx - 16} y={118 - h} width={32} height={Math.max(h, 2)} rx="3" fill="#7c3aed" />
                <text x={cx} y={110 - h} textAnchor="middle" fontSize="10" fontWeight="700" fill="#334155">{y.employees}</text>
                <text x={cx} y={140} textAnchor="middle" fontSize="10" fill="#94a3b8" fontWeight="600">{(y.period_end || '').slice(0, 7)}</text>
              </g>
            );
          })}
        </svg>
      </div>
    </>
  );
}

// ── Multi-year P&L / balance sheet table from the CH history ────────────────
function HistoryTable({ company }: { company: CompanyTarget }) {
  const years = chHistory(company);
  if (years.length < 2) return null;
  const cols = years.slice(-4); // up to 4 most recent, oldest → newest
  const rows: Array<{ label: string; key: string; margin?: boolean }> = [
    { label: 'Revenue', key: 'revenue' },
    { label: 'Gross profit', key: 'gross_profit' },
    { label: 'Profit before tax', key: 'profit' },
    { label: 'Total assets', key: 'total_assets' },
    { label: 'Net assets', key: 'net_assets' },
    { label: 'Cash', key: 'cash' },
    { label: 'Employees', key: 'employees' },
  ];
  const fmt = (k: string, v: any) => {
    if (v == null) return '—';
    if (k === 'employees') return Number(v).toLocaleString();
    return `£${(v / 1e6).toFixed(1)}M`;
  };
  const present = rows.filter(r => cols.some(c => c[r.key] != null));
  if (!present.length) return null;
  return (
    <>
      <div className="cp-section-title">Multi-year financials (filed accounts)</div>
      <div className="cp-card">
        <table className="cp-table">
          <thead><tr><th></th>{cols.map((c, i) => <th key={i}>{(c.period_end || '').slice(0, 10)}</th>)}</tr></thead>
          <tbody>
            {present.map(r => (
              <React.Fragment key={r.key}>
                <tr>
                  <td>{r.label}</td>
                  {cols.map((c, i) => <td key={i} style={r.key === 'profit' && c[r.key] < 0 ? { color: '#dc2626' } : undefined}>{fmt(r.key, c[r.key])}</td>)}
                </tr>
                {r.key === 'gross_profit' && cols.some(c => c.gross_profit != null && c.revenue) && (
                  <tr className="cp-margin-row">
                    <td>Gross margin</td>
                    {cols.map((c, i) => <td key={i}>{(c.gross_profit != null && c.revenue) ? `${((c.gross_profit / c.revenue) * 100).toFixed(1)}%` : '—'}</td>)}
                  </tr>
                )}
              </React.Fragment>
            ))}
          </tbody>
        </table>
      </div>
    </>
  );
}

export default function CompanyProfile({ companies, index, onClose, onNavigate, onChanged, initialTab }: Props) {
  const company = companies[index];
  const [tab, setTab] = useState<typeof TABS[number]>(
    (TABS as readonly string[]).includes(initialTab || '') ? (initialTab as typeof TABS[number]) : 'Summary');
  const [activity, setActivity] = useState<ActivityEntry[]>([]);
  const [emails, setEmails] = useState<any[]>([]);
  const [connections, setConnections] = useState<any>({ investors: [], siblings: [] });
  const [noteText, setNoteText] = useState('');
  const [busy, setBusy] = useState('');
  const [outreachOpen, setOutreachOpen] = useState(false);

  useEffect(() => {
    if (!company) return;
    setActivity([]); setEmails([]); setConnections({ investors: [], siblings: [] });
    dealApi.getCompanyActivity(company.name).then(r => setActivity(r.activity || [])).catch(() => {});
    dealApi.getCompanyEmails(company.name).then(r => setEmails(r.emails || [])).catch(() => {});
    dealApi.getCompanyConnections(company.name).then(r => setConnections(r || { investors: [], siblings: [] })).catch(() => {});
  }, [company?.name]);

  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      const tag = (e.target as HTMLElement)?.tagName;
      if (tag === 'INPUT' || tag === 'TEXTAREA' || outreachOpen) return;
      if (e.key === 'Escape') onClose();
      if (e.key === 'ArrowLeft' && index > 0) onNavigate(index - 1);
      if (e.key === 'ArrowRight' && index < companies.length - 1) onNavigate(index + 1);
    };
    window.addEventListener('keydown', onKey);
    return () => window.removeEventListener('keydown', onKey);
  }, [index, companies.length, onClose, onNavigate, outreachOpen]);

  if (!company) return null;
  const ob = outreachButtonState(company);
  const inPipeline = ['Qualified', 'Engaged', 'Contacted', 'Meeting', 'DD', 'Offer'].includes(company.status);
  const nextStage = NEXT_STAGE[company.status];
  const band = getRevenueBand(company);
  const revLatest = company.revenue_y1 ? company.revenue_y1 / 1e6 : company.revenue_m || company.revenue_estimate_m || null;
  const empGrowth = company.employee_growth_1yr_pct ?? company.employee_growth_3yr_pct;
  const grossMargin = (company.gross_profit_y1 && company.revenue_y1) ? (company.gross_profit_y1 / company.revenue_y1) * 100 : null;
  const cap = useMemo(() => { try { return company.ch_cap_table ? JSON.parse(company.ch_cap_table) : null; } catch { return null; } }, [company.ch_cap_table]);
  const directors = parseDirectors(company.directors);
  const scoreDetails = useMemo(() => { try { return company.score_details ? JSON.parse(company.score_details) : {}; } catch { return {}; } }, [company.score_details]);

  const act = async (label: string, fn: () => Promise<any>) => {
    setBusy(label);
    try { await fn(); await onChanged(); } catch (e: any) { alert(`${label} failed: ${e.message}`); }
    finally { setBusy(''); }
  };

  const saveNote = async () => {
    if (!noteText.trim()) return;
    await act('Note', () => dealApi.addCompanyNote(company.name, noteText.trim()));
    setNoteText('');
    dealApi.getCompanyActivity(company.name).then(r => setActivity(r.activity || [])).catch(() => {});
  };

  return (
    <div className="cp-overlay" onClick={onClose}>
      <div className="cp-shell" onClick={e => e.stopPropagation()}>
        {/* Top action bar */}
        <div className="cp-topbar">
          <div className="cp-actions">
            <button className="cp-chip-btn" disabled={!!busy}
              onClick={() => act('SmartFill', () => company.last_smartfill_at ? dealApi.smartEnrich(company.name) : dealApi.smartFill(company.name))}>
              {busy === 'SmartFill' ? 'Working…' : company.last_smartfill_at ? 'SmartEnrich ↻' : 'SmartFill'}
            </button>
            <button className="cp-chip-btn primary" onClick={() => setOutreachOpen(true)} title={ob.title}>{ob.label}</button>
            {!inPipeline && company.status !== 'Won' && (
              <button className="cp-chip-btn" disabled={!!busy}
                onClick={() => act('Qualify', () => dealApi.updateCompanyStatus(company.name, 'Qualified', 'Ishu Ratna (manual override)'))}>
                Qualify anyway
              </button>
            )}
            {inPipeline && nextStage && (
              <button className="cp-chip-btn" disabled={!!busy}
                onClick={() => act('Advance', () => dealApi.updateCompanyStatus(company.name, nextStage))}>
                {displayStatus(nextStage)} →
              </button>
            )}
            {inPipeline && (
              <button className="cp-chip-btn danger" disabled={!!busy}
                onClick={() => { if (confirm(`Remove ${company.name} from the pipeline?`)) act('Remove', () => dealApi.removeFromPipeline(company.name)); }}>
                Not a Fit
              </button>
            )}
          </div>
          <div className="cp-nav-pos">
            <span>{index + 1} / {companies.length}</span>
            <button className="cp-nav-btn" disabled={index === 0} onClick={() => onNavigate(index - 1)} title="Previous (←)">←</button>
            <button className="cp-nav-btn" disabled={index === companies.length - 1} onClick={() => onNavigate(index + 1)} title="Next (→)">→</button>
            <button className="cp-close" onClick={onClose} title="Close (Esc)">×</button>
          </div>
        </div>

        {/* Header */}
        <div className="cp-header">
          <div className="cp-title-block">
            <h2 className="cp-name">{company.name}</h2>
            <div className="cp-subline">
              {company.website && <a href={company.website} target="_blank" rel="noreferrer">{company.website.replace(/^https?:\/\/(www\.)?/, '').replace(/\/$/, '')}</a>}
              <span>{[company.hq_city, company.region].filter(Boolean).join(', ')}</span>
              {company.sector && <span>· {company.sector}</span>}
            </div>
          </div>
          <div className="cp-pills">
            <span className="cp-pill" style={{ background: statusColor(company.status) }}
              title={company.status === 'Not a Fit' && company.unfit_reason ? company.unfit_reason : undefined}>
              {displayStatus(company.status)}
            </span>
            {band && <span className="cp-pill outline">{band}</span>}
            {company.averroes_fit_score != null && (
              <span className="cp-pill" style={{ background: company.averroes_fit_score >= 0.7 ? '#16a34a' : company.averroes_fit_score >= 0.4 ? '#d97706' : '#dc2626' }}>
                Fit {Math.round(company.averroes_fit_score * 100)}
              </span>
            )}
            {company.ch_accounts_overdue && <span className="cp-pill red">Accounts overdue</span>}
            {company.ch_insolvency_summary && <span className="cp-pill red" title={company.ch_insolvency_summary}>Distress</span>}
          </div>
        </div>

        {/* Tabs */}
        <div className="cp-tabs">
          {TABS.map(t => (
            <button key={t} className={`cp-tab ${tab === t ? 'active' : ''}`} onClick={() => setTab(t)}>{t}</button>
          ))}
        </div>

        <div className="cp-body">
          {tab === 'Summary' && (
            <>
              <div className="cp-stats">
                <div className="cp-stat"><span className="cp-stat-label">Employees</span>
                  <span className="cp-stat-value">{(company.employees || company.employees_ch)?.toLocaleString() || '—'}</span>
                  {empGrowth != null && <span className={`cp-stat-sub ${empGrowth >= 0 ? 'cp-up' : 'cp-down'}`}>{empGrowth >= 0 ? '▲' : '▼'} {Math.abs(empGrowth).toFixed(1)}%</span>}
                </div>
                <div className="cp-stat"><span className="cp-stat-label">Ownership</span><span className="cp-stat-value" style={{ fontSize: '0.85rem' }}>{company.ownership || company.ch_ownership_verified || '—'}</span></div>
                <div className="cp-stat"><span className="cp-stat-label">Founded</span><span className="cp-stat-value">{company.year_founded || '—'}</span></div>
                <div className="cp-stat"><span className="cp-stat-label">Revenue</span>
                  <span className="cp-stat-value">{revLatest ? `£${revLatest.toFixed(1)}M` : '—'}</span>
                  {company.revenue_cagr_3yr_pct != null && <span className={`cp-stat-sub ${company.revenue_cagr_3yr_pct >= 0 ? 'cp-up' : 'cp-down'}`}>{fmtPct(company.revenue_cagr_3yr_pct)} 3yr</span>}
                </div>
                <div className="cp-stat"><span className="cp-stat-label">EBITDA Margin</span><span className="cp-stat-value">{company.ebitda_margin_pct != null ? `${company.ebitda_margin_pct.toFixed(1)}%` : '—'}</span></div>
                <div className="cp-stat"><span className="cp-stat-label">Fit Score</span>
                  <span className="cp-stat-value" style={{ color: company.averroes_fit_score == null ? undefined : company.averroes_fit_score >= 0.7 ? '#16a34a' : company.averroes_fit_score >= 0.4 ? '#d97706' : '#dc2626' }}>
                    {company.averroes_fit_score != null ? Math.round(company.averroes_fit_score * 100) : '—'}
                  </span>
                </div>
              </div>

              {company.description && (
                <div className="cp-card cp-desc">
                  {company.description.split(/\n+/).filter(Boolean).map((p, i) => <p key={i}>{p}</p>)}
                </div>
              )}

              {company.averroes_fit_score != null && (
                <>
                  <div className="cp-section-title">Fit score breakdown</div>
                  <div className="cp-card">
                    {[['Employee Growth', company.score_employee_growth, scoreDetails?.employee_growth],
                      ['Revenue Growth', company.score_revenue_growth, scoreDetails?.revenue_growth],
                      ['Revenue Size', company.score_revenue_size, scoreDetails?.revenue_size],
                      ['Business Model Fit', company.score_business_fit, scoreDetails?.business_fit],
                      ['Market Sentiment', company.score_market_sentiment, scoreDetails?.market_sentiment],
                    ].filter(([, s]) => s != null).map(([label, s, det]: any, i) => (
                      <div key={i} className="cp-kv" title={det?.explanation || ''}>
                        <span className="k">{label}</span>
                        <span className="v" style={{ color: s >= 0.7 ? '#16a34a' : s >= 0.4 ? '#d97706' : '#dc2626' }}>{Math.round(s * 100)}</span>
                      </div>
                    ))}
                  </div>
                </>
              )}

              <div className="cp-section-title">News &amp; activity</div>
              <div className="cp-card">
                <div style={{ display: 'flex', gap: '0.5rem', marginBottom: '0.6rem' }}>
                  <input value={noteText} onChange={e => setNoteText(e.target.value)}
                    onKeyDown={e => { if (e.key === 'Enter') saveNote(); }}
                    placeholder="Add a note…"
                    style={{ flex: 1, padding: '0.5rem 0.75rem', border: '1px solid #e2e8f0', borderRadius: 8, fontSize: '0.82rem', background: '#f8fafc' }} />
                  <button className="cp-chip-btn" onClick={saveNote} disabled={!noteText.trim() || !!busy}>Add</button>
                </div>
                {activity.length === 0 && <p className="cp-empty">No activity recorded yet.</p>}
                {activity.map(e => {
                  const tag = feedTag(e);
                  return (
                    <div className="cp-feed-item" key={e.id}>
                      <div className="cp-feed-main">
                        <p className="cp-feed-text">
                          {e.action_type === 'status_change'
                            ? `${displayStatus(e.old_status || '')} → ${displayStatus(e.new_status || '')}`
                            : (e.note_text || e.action_type)}
                        </p>
                        <div className="cp-feed-meta">{fmtDate(e.created_at)} · {e.created_by}</div>
                      </div>
                      <span className={`cp-feed-tag ${tag.cls}`}>{tag.label}</span>
                    </div>
                  );
                })}
              </div>
            </>
          )}

          {tab === 'Financials' && (
            <>
              <div className="cp-stats">
                <div className="cp-stat"><span className="cp-stat-label">Revenue {company.revenue_y1_date ? `(${company.revenue_y1_date})` : ''}</span><span className="cp-stat-value">{fmtRaw(company.revenue_y1) || fmtM(company.revenue_m) || '—'}</span></div>
                <div className="cp-stat"><span className="cp-stat-label">Gross profit</span><span className="cp-stat-value">{fmtRaw(company.gross_profit_y1) || '—'}</span></div>
                <div className="cp-stat"><span className="cp-stat-label">EBITDA</span><span className="cp-stat-value">{fmtM(company.estimated_ebitda) || '—'}</span></div>
                <div className="cp-stat"><span className="cp-stat-label">Net income</span><span className="cp-stat-value">{fmtM(company.net_income_m) || fmtRaw(company.profit_y1) || '—'}</span></div>
                <div className="cp-stat"><span className="cp-stat-label">Total assets</span><span className="cp-stat-value">{fmtRaw(company.total_assets_y1) || '—'}</span></div>
              </div>

              <div className="cp-section-title">Revenue &amp; EBITDA development</div>
              <div className="cp-card"><FinChart company={company} /></div>

              <HistoryTable company={company} />
              <EmpChart company={company} />

              <div className="cp-two-col">
                <div>
                  <div className="cp-section-title">Profitability</div>
                  <div className="cp-card">
                    {grossMargin != null && <div className="cp-kv"><span className="k">Gross margin</span><span className="v">{grossMargin.toFixed(1)}%</span></div>}
                    {company.ebitda_margin_pct != null && <div className="cp-kv"><span className="k">EBITDA margin</span><span className="v">{company.ebitda_margin_pct.toFixed(1)}%</span></div>}
                    {company.profit_y1 != null && <div className="cp-kv"><span className="k">Profit before tax</span><span className="v" style={{ color: company.profit_y1 < 0 ? '#dc2626' : undefined }}>{fmtRaw(company.profit_y1)}</span></div>}
                    {company.revenue_cagr_3yr_pct != null && <div className="cp-kv"><span className="k">Revenue 3yr CAGR</span><span className="v">{fmtPct(company.revenue_cagr_3yr_pct)}</span></div>}
                    {company.cash_y1 != null && company.cash_y1 !== 0 && <div className="cp-kv"><span className="k">Cash</span><span className="v">{fmtRaw(company.cash_y1)}</span></div>}
                    {company.net_assets_y1 != null && company.net_assets_y1 !== 0 && <div className="cp-kv"><span className="k">Net assets</span><span className="v">{fmtRaw(company.net_assets_y1)}</span></div>}
                  </div>
                </div>
                <div>
                  <div className="cp-section-title">Valuation</div>
                  <div className="cp-card">
                    {company.valuation_estimate_m != null && company.valuation_estimate_m !== 0 && <div className="cp-kv"><span className="k">Valuation (est.)</span><span className="v">{fmtM(company.valuation_estimate_m)}</span></div>}
                    {company.enterprise_value_m != null && company.enterprise_value_m !== 0 && <div className="cp-kv"><span className="k">Enterprise value</span><span className="v">{fmtM(company.enterprise_value_m)}</span></div>}
                    {company.total_raised_m != null && company.total_raised_m !== 0 && <div className="cp-kv"><span className="k">Total raised</span><span className="v">{fmtM(company.total_raised_m)}</span></div>}
                    {revLatest != null && <div className="cp-kv"><span className="k">Revenue band</span><span className="v">{band || '—'}</span></div>}
                    {company.revenue_estimate_m != null && <div className="cp-kv"><span className="k">Estimate basis</span><span className="v">{company.revenue_source || 'proxies'} ({company.revenue_confidence || 'low'})</span></div>}
                  </div>
                </div>
              </div>
            </>
          )}

          {tab === 'Ownership' && (
            <>
              <div className="cp-stats">
                <div className="cp-stat"><span className="cp-stat-label">Ownership</span><span className="cp-stat-value" style={{ fontSize: '0.85rem' }}>{company.ch_ownership_verified || company.ownership || '—'}</span></div>
                <div className="cp-stat"><span className="cp-stat-label">Founder holding</span><span className="cp-stat-value">{company.ch_founder_pct != null ? `~${company.ch_founder_pct}%` : '—'}</span></div>
                <div className="cp-stat"><span className="cp-stat-label">Total raised</span><span className="cp-stat-value">{fmtM(company.total_raised_m) || '—'}</span></div>
              </div>

              {(connections.investors?.length > 0 || connections.siblings?.length > 0) && (
                <>
                  <div className="cp-section-title">Investor connections</div>
                  <div className="cp-card">
                    {(connections.investors || []).map((inv: any, i: number) => (
                      <div className="cp-kv" key={i}>
                        <span className="k">{inv.investor_name}
                          <span className="cp-feed-tag cp-tag-note" style={{ marginLeft: '0.4rem' }}>{inv.investor_type}</span>
                        </span>
                        <span className="v">{inv.pct != null ? `${inv.pct}% · ` : ''}{String(inv.link_type || '').replace(/_/g, ' ')}</span>
                      </div>
                    ))}
                    {(() => {
                      const sib = new Map<string, string[]>();
                      (connections.siblings || []).forEach((s: any) => {
                        const arr = sib.get(s.company_name) || [];
                        if (!arr.includes(s.via)) arr.push(s.via);
                        sib.set(s.company_name, arr);
                      });
                      if (!sib.size) return null;
                      return (
                        <div style={{ marginTop: '0.7rem', borderTop: '1px solid #e2e8f0', paddingTop: '0.6rem' }}>
                          <p style={{ fontSize: '0.72rem', fontWeight: 800, color: '#64748b', textTransform: 'uppercase', letterSpacing: '0.04em', margin: '0 0 0.35rem' }}>Connected companies (shared investors)</p>
                          {Array.from(sib.entries()).slice(0, 8).map(([cn, vias], i) => (
                            <p className="cp-memo-p" key={i}><b>{cn}</b> <span style={{ color: '#94a3b8' }}>via {vias.slice(0, 3).join(', ')}</span></p>
                          ))}
                        </div>
                      );
                    })()}
                  </div>
                </>
              )}

              {cap?.shareholders?.length ? (
                <>
                  <div className="cp-section-title">Cap table (CS01, {cap.date || company.ch_cap_table_date})</div>
                  <div className="cp-card">
                    <table className="cp-table">
                      <thead><tr><th>Shareholder</th><th>Shares</th><th>Class</th><th>%</th></tr></thead>
                      <tbody>
                        {cap.shareholders.map((h: any, i: number) => (
                          <tr key={i}>
                            <td>{h.name}</td>
                            <td>{h.shares != null ? Number(h.shares).toLocaleString() : '—'}</td>
                            <td style={{ color: '#94a3b8', fontSize: '0.72rem' }}>{h.share_class || ''}</td>
                            <td style={{ fontWeight: 800 }}>{h.pct != null ? `${h.pct}%` : '—'}</td>
                          </tr>
                        ))}
                      </tbody>
                    </table>
                    {cap.notes && <p style={{ fontSize: '0.7rem', color: '#94a3b8', fontStyle: 'italic', margin: '0.5rem 0 0' }}>{cap.notes}</p>}
                  </div>
                </>
              ) : <p className="cp-empty">No cap table extracted yet — run SmartEnrich to parse the latest CS01.</p>}

              <div className="cp-section-title">Investors &amp; funding</div>
              <div className="cp-card">
                {company.active_investors && <div className="cp-kv"><span className="k">Investors</span><span className="v">{company.active_investors}</span></div>}
                {company.last_financing_date && <div className="cp-kv"><span className="k">Last round</span><span className="v">{[fmtM(company.last_financing_size_m), company.last_financing_type, fmtDate(company.last_financing_date)].filter(Boolean).join(' · ')}</span></div>}
                {company.ch_last_share_allotment && <div className="cp-kv"><span className="k">Last share allotment</span><span className="v">{company.ch_last_share_allotment}</span></div>}
                {company.ch_charges_summary && <div className="cp-kv"><span className="k">Secured debt</span><span className="v">{company.ch_charges_summary}</span></div>}
                {!company.active_investors && !company.last_financing_date && !company.ch_last_share_allotment && !company.ch_charges_summary && <p className="cp-empty">No funding or debt intelligence held.</p>}
              </div>
            </>
          )}

          {tab === 'People' && (
            <>
              <div className="cp-section-title">Primary contact</div>
              <div className="cp-card cp-contact-card">
                <div className="cp-avatar-lg">{(company.contact_name || '?').split(/\s+/).map(w => w[0]).slice(0, 2).join('').toUpperCase()}</div>
                <div style={{ minWidth: 0 }}>
                  <div style={{ fontWeight: 800, color: '#0f172a', fontSize: '0.95rem' }}>{company.contact_name || 'No contact on file'}</div>
                  {company.contact_title && <div style={{ fontSize: '0.78rem', color: '#64748b' }}>{company.contact_title}</div>}
                  <div style={{ fontSize: '0.8rem', marginTop: 2 }}>
                    {company.contact_email ? <a href={`mailto:${company.contact_email}`} style={{ color: '#2563eb', fontWeight: 600 }}>{company.contact_email}</a>
                      : <span style={{ color: '#94a3b8' }}>No verified email — see activity trail for the waterfall verdict</span>}
                    {company.linkedin_url && <a href={company.linkedin_url} target="_blank" rel="noreferrer" style={{ marginLeft: 12, color: '#2563eb', fontWeight: 600 }}>LinkedIn ↗</a>}
                  </div>
                </div>
              </div>

              {directors.length > 0 && (
                <>
                  <div className="cp-section-title">Registered directors ({directors.length})</div>
                  <div className="cp-card"><div className="cp-people-grid">
                    {directors.map((d, i) => <span key={i} className="cp-person-chip">{d}</span>)}
                  </div></div>
                </>
              )}
              {company.company_linkedin && (
                <div className="cp-card"><div className="cp-kv"><span className="k">Company LinkedIn</span>
                  <a className="v" href={company.company_linkedin} target="_blank" rel="noreferrer" style={{ color: '#2563eb' }}>View page ↗</a></div></div>
              )}
            </>
          )}

          {tab === 'Companies House' && (
            company.ch_company_number ? (
              <>
                <div className="cp-two-col">
                  <div className="cp-card">
                    <div className="cp-kv"><span className="k">Official name</span><span className="v">{company.ch_official_name}</span></div>
                    <div className="cp-kv"><span className="k">Company #</span><span className="v">{company.ch_company_number}</span></div>
                    <div className="cp-kv"><span className="k">Status</span><span className="v">{company.ch_status}</span></div>
                    {company.ch_incorporated_date && <div className="cp-kv"><span className="k">Incorporated</span><span className="v">{company.ch_incorporated_date}</span></div>}
                    {company.ch_sic_codes && <div className="cp-kv"><span className="k">SIC</span><span className="v">{company.ch_sic_codes}</span></div>}
                    {company.ch_match_confidence && <div className="cp-kv"><span className="k">Match confidence</span><span className="v">{company.ch_match_confidence}</span></div>}
                  </div>
                  <div className="cp-card">
                    {company.ch_accounts_regime && <div className="cp-kv"><span className="k">Accounts regime</span><span className="v">{company.ch_accounts_regime}</span></div>}
                    {company.ch_last_resolution && <div className="cp-kv"><span className="k">Last resolution</span><span className="v">{company.ch_last_resolution}</span></div>}
                    {company.ch_accounts_next_due && <div className="cp-kv"><span className="k">Accounts next due</span><span className="v">{company.ch_accounts_next_due}</span></div>}
                    {company.ch_accounts_overdue && <div className="cp-kv"><span className="k">Accounts</span><span className="v red">OVERDUE</span></div>}
                    {company.ch_insolvency_summary && <div className="cp-kv"><span className="k">Distress</span><span className="v red">{company.ch_insolvency_summary}</span></div>}
                    {company.ch_psc_summary && <div className="cp-kv"><span className="k">Controllers (PSC)</span><span className="v">{company.ch_psc_summary}</span></div>}
                  </div>
                </div>
                <div className="cp-card">
                  <a className="cp-chip-btn" style={{ textDecoration: 'none', display: 'inline-block' }}
                    href={company.ch_pdf_path
                      ? `${process.env.NEXT_PUBLIC_API_URL || 'https://averroes-deal-backend-890361705054.europe-west1.run.app'}/ch-pdf/${encodeURIComponent(company.name)}`
                      : `https://find-and-update.company-information.service.gov.uk/company/${encodeURIComponent(company.ch_company_number)}/filing-history`}
                    target="_blank" rel="noreferrer">
                    {company.ch_pdf_path ? 'View filed accounts PDF' : 'View filings on Companies House'}
                  </a>
                </div>
              </>
            ) : <p className="cp-empty">Not matched to a Companies House record yet — run SmartFill.</p>
          )}

          {tab === 'Outreach' && (
            <>
              {(() => {
                const b = actionBucketInfo(company.action_bucket);
                if (!b) return null;
                return (
                  <>
                    <div className="cp-section-title">Action bucket</div>
                    <div className="cp-card">
                      <div className={`kc-bucket bucket-${b.tone}`} style={{ display: 'inline-block' }}>{b.label}</div>
                      {company.action_rationale && (
                        <p style={{ fontSize: '0.82rem', color: '#334155', lineHeight: 1.6, margin: '0.55rem 0 0' }}>{company.action_rationale}</p>
                      )}
                      {company.action_follow_up_date && (
                        <div className="cp-kv" style={{ marginTop: '0.45rem' }}><span className="k">Follow up</span><span className="v">{company.action_follow_up_date}</span></div>
                      )}
                      {company.action_set_at && (
                        <div className="cp-kv"><span className="k">Assessed</span><span className="v">{fmtDate(company.action_set_at)}</span></div>
                      )}
                      {company.action_reply_body && (
                        <div style={{ marginTop: '0.7rem', borderTop: '1px solid #e2e8f0', paddingTop: '0.7rem' }}>
                          <div className="cp-kv"><span className="k">Suggested reply</span><span className="v">{company.action_reply_subject || '(no subject)'}</span></div>
                          <p style={{ fontSize: '0.82rem', color: '#334155', lineHeight: 1.65, whiteSpace: 'pre-wrap', margin: '0.5rem 0 0.6rem' }}>{company.action_reply_body}</p>
                          <button className="cp-chip-btn" onClick={() => {
                            navigator.clipboard?.writeText(company.action_reply_body || '');
                          }} title="Copy the suggested reply, then paste it into the Gmail thread so the response stays threaded">
                            Copy reply text
                          </button>
                        </div>
                      )}
                    </div>
                  </>
                );
              })()}

              {company.outreach_draft_body ? (
                <>
                  <div className="cp-section-title">Current draft {company.outreach_sent_at ? '(sent)' : '(unsent)'}</div>
                  <div className="cp-card">
                    <div className="cp-kv"><span className="k">To</span><span className="v">{company.outreach_draft_to || '—'}</span></div>
                    <div className="cp-kv"><span className="k">Subject</span><span className="v">{company.outreach_draft_subject}</span></div>
                    <p style={{ fontSize: '0.82rem', color: '#334155', lineHeight: 1.65, whiteSpace: 'pre-wrap', margin: '0.6rem 0 0' }}>{company.outreach_draft_body}</p>
                  </div>
                </>
              ) : <p className="cp-empty">No draft yet — press the outreach button above to generate one.</p>}

              <div className="cp-section-title">Email thread ({emails.length})</div>
              <div className="cp-card">
                {emails.length === 0 && <p className="cp-empty">No logged emails with this company yet.</p>}
                {emails.map((m, i) => (
                  <div className="cp-email-item" key={i}>
                    <div className="cp-email-head">
                      <span className={`cp-email-dir ${m.direction}`}>{m.direction}</span>
                      <span className="cp-email-subj">{m.subject}</span>
                      {m.classification && <span className="cp-feed-tag cp-tag-reply">{m.classification}</span>}
                      <span className="cp-email-date">{fmtDate(m.sent_at)}</span>
                    </div>
                    {m.snippet && <p className="cp-email-snip">{String(m.snippet).slice(0, 260)}</p>}
                  </div>
                ))}
              </div>
            </>
          )}

          {tab === 'IC Memo' && (() => {
            let memo: any = null;
            try { memo = company.ic_memo ? JSON.parse(company.ic_memo) : null; } catch { memo = null; }
            const n = memo?.narrative || {};
            const dm = memo?.deal_math || {};
            const sc = memo?.scorecard || {};
            const risks: string[] = [...(memo?.registry_flags || []), ...(n.risks || [])];
            return (
              <>
                <div className="cp-card" style={{ display: 'flex', gap: '0.6rem', alignItems: 'center', flexWrap: 'wrap' }}>
                  <button className="cp-chip-btn primary" disabled={busy === 'icmemo'} onClick={async () => {
                    setBusy('icmemo');
                    try { await dealApi.generateIcMemo(company.name); await onChanged(); }
                    catch (e: any) { alert(e?.message || 'IC memo generation failed'); }
                    finally { setBusy(''); }
                  }}>
                    {busy === 'icmemo' ? 'Generating…' : memo ? 'Regenerate memo' : 'Generate IC Memo'}
                  </button>
                  {memo && (
                    <button className="cp-chip-btn" onClick={() => dealApi.downloadIcMemoPdf(company.name).catch(e => alert(e.message))}>
                      Download PDF
                    </button>
                  )}
                  {memo && <span style={{ fontSize: '0.72rem', color: '#94a3b8' }}>Generated {fmtDate(memo.generated_at)} · numbers from verified record, sources labelled</span>}
                </div>

                {!memo && <p className="cp-empty">No memo yet — Generate IC Memo builds a one-pager from the verified record (one grounded search for market context).</p>}

                {memo && (
                  <>
                    {n.opportunity && (<><div className="cp-section-title">The Opportunity</div>
                      <div className="cp-card"><p className="cp-memo-p">{n.opportunity}</p></div></>)}

                    {(n.mandate_fit || []).length > 0 && (<><div className="cp-section-title">Mandate Fit</div>
                      <div className="cp-card">
                        {(n.mandate_fit || []).map((f: any, i: number) => (
                          <div className="cp-kv" key={i}>
                            <span className="k">{f.check}</span>
                            <span className="v"><b style={{ color: f.verdict === 'PASS' ? '#15803d' : f.verdict === 'FAIL' ? '#b91c1c' : '#b45309' }}>{f.verdict}</b> — {f.evidence}</span>
                          </div>
                        ))}
                      </div></>)}

                    {(memo.financials || []).length > 0 && (<><div className="cp-section-title">Financial Snapshot</div>
                      <div className="cp-card">
                        <table className="cp-table"><tbody>
                          {(memo.financials || []).map((r: any, i: number) => (
                            <tr key={i}><td>{r.label}</td><td>{r.value}</td><td style={{ color: '#94a3b8', fontSize: '0.72rem' }}>{r.source}</td></tr>
                          ))}
                        </tbody></table>
                      </div></>)}

                    <div className="cp-section-title">Deal Hypothesis</div>
                    <div className="cp-card">
                      {dm.available
                        ? <p className="cp-memo-p"><b>Implied valuation £{dm.val_low_m}M–£{dm.val_high_m}M</b> at 4–6x £{dm.revenue_m}M revenue{dm.estimated ? ' (estimated)' : ''}. {dm.stake_note}</p>
                        : <p className="cp-memo-p">{dm.note || 'Valuation not computable.'}</p>}
                      {n.deal_hypothesis && <p className="cp-memo-p">{n.deal_hypothesis}</p>}
                      {sc.fit != null && <p className="cp-memo-p" style={{ color: '#475569' }}><b>Fit {sc.fit}/100</b> · {(sc.subscores || []).filter((s: any) => s.value != null).map((s: any) => `${s.label} ${s.value}`).join(' · ')}</p>}
                    </div>

                    {n.engagement_status && (<><div className="cp-section-title">Engagement</div>
                      <div className="cp-card"><p className="cp-memo-p">{n.engagement_status}</p></div></>)}

                    {n.market_context && (<><div className="cp-section-title">Market Context (sourced)</div>
                      <div className="cp-card"><p className="cp-memo-p">{n.market_context}</p></div></>)}

                    {risks.length > 0 && (<><div className="cp-section-title">Risks &amp; Red Flags</div>
                      <div className="cp-card">{risks.slice(0, 6).map((r, i) => <p className="cp-memo-p" key={i}>• {r}</p>)}</div></>)}

                    {(n.open_questions || []).length > 0 && (<><div className="cp-section-title">Open Questions for First Meeting</div>
                      <div className="cp-card">{(n.open_questions || []).map((q: string, i: number) => <p className="cp-memo-p" key={i}>• {q}</p>)}</div></>)}

                    {n.recommendation && (<><div className="cp-section-title">Recommendation</div>
                      <div className="cp-card"><p className="cp-memo-p"><b>{n.recommendation}</b></p></div></>)}
                  </>
                )}
              </>
            );
          })()}
        </div>
      </div>

      {outreachOpen && (
        <div onClick={e => e.stopPropagation()}>
          <OutreachModal company={company} onClose={() => setOutreachOpen(false)} onSent={onChanged} />
        </div>
      )}
    </div>
  );
}
