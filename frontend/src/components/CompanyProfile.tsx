'use client';

// Inven-style full-screen company profile. Replaces CompanyDrawer.
// Self-contained: fetches its own activity + email thread, embeds the shared
// OutreachModal, and supports prev/next browsing through the caller's list
// (← → keys) exactly like Inven's "1 / 500" flow.
// Styling: ALL classes live in globals.css (cp-*) — deliberately no styled-jsx.

import React, { useEffect, useMemo, useState } from 'react';
import { CompanyTarget, ActivityEntry, EmailDoc, NewsItem, displayStatus, getRevenueBand, actionBucketInfo } from '../types';
import { dealApi } from '../services/api';
import OutreachModal from './OutreachModal';
import { outreachButtonState } from '../lib/outreach';
import OwnerTag from './OwnerTag';

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
  Qualified: 'Contacted', Contacted: 'Responded', Responded: 'Meeting', Meeting: 'DD', DD: 'Offer',
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
    Qualified: '#3b82f6', Contacted: '#8b5cf6', Responded: '#7c3aed', Meeting: '#f59e0b', DD: '#ef4444',
    Offer: '#10b981', Won: '#059669', Lost: '#6b7280',
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
  const baseCompany = companies[index];
  // The universe list is SLIM (heavy fields like cap tables, filing history,
  // IC memos are excluded server-side so 13k rows stay lightweight). The
  // profile fetches the FULL record on open; until it arrives the slim row
  // renders, then depth fills in.
  const [fullCompany, setFullCompany] = useState<typeof baseCompany | null>(null);
  const company = (fullCompany && fullCompany.name === baseCompany?.name) ? fullCompany : baseCompany;
  const [tab, setTab] = useState<typeof TABS[number]>(
    (TABS as readonly string[]).includes(initialTab || '') ? (initialTab as typeof TABS[number]) : 'Summary');
  const [activity, setActivity] = useState<ActivityEntry[]>([]);
  const [emailDocs, setEmailDocs] = useState<EmailDoc[]>([]);
  const [docUploading, setDocUploading] = useState(false);
  // Which fit-score dimension is expanded to show its stored evidence.
  const [scoreDim, setScoreDim] = useState<string | null>(null);
  const [newsBusy, setNewsBusy] = useState(false);
  const [newsItems, setNewsItems] = useState<NewsItem[]>([]);
  const [emails, setEmails] = useState<any[]>([]);
  const [connections, setConnections] = useState<any>({ investors: [], siblings: [] });
  const [noteText, setNoteText] = useState('');
  const [busy, setBusy] = useState('');
  const [outreachOpen, setOutreachOpen] = useState(false);

  useEffect(() => {
    if (!baseCompany) return;
    setActivity([]); setEmails([]); setEmailDocs([]); setConnections({ investors: [], siblings: [] });
    setFullCompany(null);
    dealApi.getCompanyFull(baseCompany.name).then(r => {
      if (r && r.name) {
        setFullCompany({ ...baseCompany, ...r });
        try { setNewsItems(r.news_items ? JSON.parse(r.news_items) : []); } catch { setNewsItems([]); }
      }
    }).catch(() => {});
    setScoreDim(null);
    dealApi.getCompanyActivity(baseCompany.name).then(r => setActivity(r.activity || [])).catch(() => {});
    dealApi.getCompanyEmails(baseCompany.name).then(r => setEmails(r.emails || [])).catch(() => {});
    dealApi.getEmailDocs(baseCompany.name).then(r => setEmailDocs(r.documents || [])).catch(() => {});
    dealApi.getCompanyConnections(baseCompany.name).then(r => setConnections(r || { investors: [], siblings: [] })).catch(() => {});
  }, [baseCompany?.name]);

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
  const inPipeline = ['Qualified', 'Contacted', 'Responded', 'Meeting', 'DD', 'Offer'].includes(company.status);
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
            {/* Who is managing this company. Same shared tag as the Universe
                table, the Pipeline cards and the Responded page. */}
            <OwnerTag owner={company.owner} size="md" />
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
                      <div key={i}>
                        {/* Click any dimension for the WHY: exactly what the
                            scorer stored - inputs, rule, source. No AI, no
                            recomputation, just the evidence. */}
                        <div className="cp-kv" style={{ cursor: det ? 'pointer' : 'default' }}
                             title={det ? 'Click for the full scoring evidence' : 'No stored detail for this dimension'}
                             onClick={() => det && setScoreDim(scoreDim === label ? null : label)}>
                          <span className="k">{label} {det && <span style={{ color: '#94a3b8', fontSize: '0.7rem' }}>{scoreDim === label ? '▾' : '▸'}</span>}</span>
                          <span className="v" style={{ color: s >= 0.7 ? '#16a34a' : s >= 0.4 ? '#d97706' : '#dc2626' }}>{Math.round(s * 100)}</span>
                        </div>
                        {scoreDim === label && det && (
                          <div className="cp-score-why">
                            {det.explanation && <p>{det.explanation}</p>}
                            {Object.entries(det)
                              .filter(([k, v]) => k !== 'explanation' && v !== null && v !== '' && typeof v !== 'object')
                              .map(([k, v]) => (
                                <div className="cp-kv" key={k} style={{ padding: '0.15rem 0' }}>
                                  <span className="k" style={{ textTransform: 'capitalize' }}>{k.replace(/_/g, ' ')}</span>
                                  <span className="v" style={{ fontWeight: 600 }}>{String(v)}</span>
                                </div>
                              ))}
                          </div>
                        )}
                      </div>
                    ))}
                  </div>
                </>
              )}

              {/* NEWS: the top coverage found by one grounded search, cached
                  on the record. Refresh only by the button - browsing costs
                  nothing. Items open in a new tab. */}
              <div className="cp-section-title" style={{ display: 'flex', alignItems: 'baseline', gap: '0.6rem' }}>
                News
                <button className="cp-chip-btn" disabled={newsBusy}
                        title="One AI web search (~1p); results are saved until you refresh again."
                        onClick={async () => {
                          setNewsBusy(true);
                          try {
                            const r = await dealApi.refreshNews(baseCompany.name);
                            setNewsItems(r.items || []);
                            if (!r.items?.length) alert(r.message || 'Nothing solid found.');
                          } catch (e: any) { alert(e?.message || 'News refresh failed'); }
                          finally { setNewsBusy(false); }
                        }}>
                  {newsBusy ? 'Searching…' : '↻ Refresh news'}
                </button>
                {company.news_refreshed_at && (
                  <span style={{ fontSize: '0.68rem', color: '#94a3b8' }}>
                    last refreshed {fmtDate(company.news_refreshed_at)}
                  </span>
                )}
              </div>
              {newsItems.length > 0 && (
                <div className="cp-card">
                  {newsItems.map((n, i) => (
                    <div className="cp-doc-row" key={i}>
                      <a className="cp-doc-name" href={n.url} target="_blank" rel="noreferrer"
                         style={{ textDecoration: 'none' }}>
                        {n.title} ↗
                      </a>
                      <span className="cp-doc-meta">{[n.source, n.date].filter(Boolean).join(' · ')}</span>
                    </div>
                  ))}
                </div>
              )}

              {/* Email documents: everything this company has ever attached to
                  an email, filed automatically by the sync. Opens through the
                  authenticated fetch - founders' own files never get a bare
                  public link. */}
              <div className="cp-section-title" style={{ display: 'flex', alignItems: 'baseline', gap: '0.6rem' }}>
                Email documents
                {/* Manual route into the same pipeline: a founder shares a
                    deck behind a Drive/Dropbox link the sync cannot fetch;
                    download it and upload it here. Filed, AI-read and
                    field-updated exactly like an email attachment. */}
                <label className="cp-chip-btn" style={{ cursor: docUploading ? 'default' : 'pointer', fontWeight: 700 }}>
                  {docUploading ? 'Uploading…' : '⬆ Upload document'}
                  <input type="file" style={{ display: 'none' }} disabled={docUploading}
                    onChange={async e => {
                      const f = e.target.files?.[0];
                      e.target.value = '';
                      if (!f) return;
                      setDocUploading(true);
                      try {
                        const r = await dealApi.uploadEmailDoc(baseCompany.name, f);
                        if (r.status === 'Skipped') alert(r.message);
                        const docs = await dealApi.getEmailDocs(baseCompany.name);
                        setEmailDocs(docs.documents || []);
                      } catch (err: any) { alert(err?.message || 'Upload failed'); }
                      finally { setDocUploading(false); }
                    }} />
                </label>
              </div>
              {emailDocs.length > 0 && (
                <>
                  <div className="cp-card">
                    {emailDocs.map(d => (
                      <div className="cp-doc-row" key={d.gcs_path}>
                        <button className="cp-doc-name" title={`From ${d.sender_email} — "${d.email_subject}"`}
                          onClick={() => dealApi.openEmailDoc(d.gcs_path).catch(e => alert(e.message))}>
                          {d.filename}
                        </button>
                        <span className="cp-doc-meta">
                          {fmtDate(d.received_at)} · {(d.size_bytes / 1024) < 1024
                            ? `${Math.max(1, Math.round(d.size_bytes / 1024))}KB`
                            : `${(d.size_bytes / 1048576).toFixed(1)}MB`}
                          {d.ai_updates ? ' · updated fields' : ''}
                        </span>
                        {d.ai_summary && <p className="cp-doc-summary">{d.ai_summary}</p>}
                      </div>
                    ))}
                  </div>
                </>
              )}

              <div className="cp-section-title">Activity Log</div>
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

              {(() => {
                // Reported investors from source data (Inven / PitchBook) — the
                // unverified layer that sits beside the CS01 cap table and feeds
                // the LP miner. Shown as-is, provenance labelled.
                const groups: Array<[string, string | undefined]> = [
                  ['Investors (Inven)', company.investors_raw],
                  ['Current owners (Inven)', company.current_owners],
                  ['Active investors (PitchBook)', company.active_investors],
                  ['Former investors (PitchBook)', company.former_investors],
                ];
                const present = groups.filter(([, v]) => (v || '').trim());
                if (!present.length) return null;
                return (
                  <>
                    <div className="cp-section-title">Reported investors (source data)</div>
                    <div className="cp-card">
                      {present.map(([label, v], gi) => (
                        <div key={gi} style={{ marginBottom: gi < present.length - 1 ? '0.55rem' : 0 }}>
                          <p style={{ fontSize: '0.66rem', fontWeight: 800, color: '#94a3b8', textTransform: 'uppercase', letterSpacing: '0.04em', margin: '0 0 0.25rem' }}>{label}</p>
                          <div className="cp-people-grid">
                            {Array.from(new Set((v || '').split(/[;,]/).map(s => s.trim()).filter(s => s.length > 2))).slice(0, 12).map((n, i) => (
                              <span className="cp-person-chip" key={i}>{n}</span>
                            ))}
                          </div>
                        </div>
                      ))}
                      <p className="cp-memo-p" style={{ color: '#94a3b8', fontSize: '0.72rem', marginTop: '0.5rem' }}>
                        As reported by source data — verified stakes come from the CS01 cap table above. The LP miner extracts these into the Investors database with connection mapping.
                      </p>
                    </div>
                  </>
                );
              })()}

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
                  <div className="cp-section-title">
                    Cap table (CS01{cap.base_date ? `, ${cap.base_date}` : cap.date ? `, ${cap.date}` : ''})
                  </div>
                  <div className="cp-card">
                    {(cap.rolled_forward || cap.psc_check) && (
                      <div style={{ marginBottom: '0.5rem' }}>
                        {cap.rolled_forward && (
                          <span className="cp-feed-tag cp-tag-outreach" title={`Base CS01 ${cap.base_date} rolled forward with SH01 allotment(s): ${(cap.sh01_applied || []).join(', ')}. Percentages recomputed on the enlarged share count — estimated.`}>
                            estimated · rolled forward to {cap.date}
                          </span>
                        )}
                        {cap.psc_check && cap.psc_check.startsWith('VERIFY') && (
                          <span className="cp-feed-tag" style={{ background: '#fee2e2', color: '#b91c1c', marginLeft: '0.4rem' }} title={cap.psc_check}>
                            ⚠ PSC mismatch — verify
                          </span>
                        )}
                        {cap.psc_check && !cap.psc_check.startsWith('VERIFY') && cap.psc_check.startsWith('consistent') && (
                          <span className="cp-feed-tag cp-tag-reply" style={{ marginLeft: cap.rolled_forward ? '0.4rem' : 0 }} title="Computed founder % agrees with the independent PSC register band">
                            ✓ PSC consistent
                          </span>
                        )}
                      </div>
                    )}
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

                  {(cap.share_classes || []).length > 0 && (
                    <>
                      <div className="cp-section-title">Share classes &amp; rights</div>
                      <div className="cp-card">
                        {(cap.share_classes || []).map((sc: any, i: number) => (
                          <div key={i} style={{ marginBottom: i < cap.share_classes.length - 1 ? '0.6rem' : 0, paddingBottom: i < cap.share_classes.length - 1 ? '0.6rem' : 0, borderBottom: i < cap.share_classes.length - 1 ? '1px solid #f1f5f9' : 'none' }}>
                            <p style={{ fontSize: '0.82rem', fontWeight: 800, color: '#0f172a', margin: 0 }}>
                              {sc.class}
                              {sc.total_shares != null && <span style={{ color: '#94a3b8', fontWeight: 600 }}> · {Number(sc.total_shares).toLocaleString()} shares</span>}
                              {sc.nominal_value && <span style={{ color: '#94a3b8', fontWeight: 600 }}> · {sc.nominal_value}</span>}
                            </p>
                            {sc.voting && <p className="cp-memo-p" style={{ margin: '0.15rem 0 0' }}><b style={{ color: '#64748b' }}>Voting:</b> {sc.voting}</p>}
                            {sc.dividend && <p className="cp-memo-p" style={{ margin: '0.15rem 0 0' }}><b style={{ color: '#64748b' }}>Dividend:</b> {sc.dividend}</p>}
                            {sc.capital && <p className="cp-memo-p" style={{ margin: '0.15rem 0 0' }}><b style={{ color: '#64748b' }}>Capital:</b> {sc.capital}</p>}
                          </div>
                        ))}
                        <p style={{ fontSize: '0.7rem', color: '#94a3b8', fontStyle: 'italic', margin: '0.5rem 0 0' }}>Prescribed particulars as filed in the statement of capital — economic and voting rights per class.</p>
                      </div>
                    </>
                  )}
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
                  {/* Contact waterfall v4: WHO this address belongs to, and how
                      we got it. Visible before anyone hits send, because the
                      To: is not always the founder. */}
                  {company.contact_email && (
                    <div className="cp-recipient">
                      <span className={`cp-recip-chip ${company.contact_email_kind || ''}`}>
                        {company.contact_email_kind === 'colleague'
                          ? `Colleague${company.contact_email_name ? `: ${company.contact_email_name}` : ''}, not the founder`
                          : company.contact_email_kind === 'generic'
                            ? 'Company enquiries inbox'
                            : company.contact_email_kind === 'founder'
                              ? 'Founder / CEO'
                              : 'Recipient not classified'}
                      </span>
                      {company.contact_email_source && (
                        <span className="cp-recip-prov" title={company.contact_email_source}>
                          {company.contact_email_source}
                        </span>
                      )}
                    </div>
                  )}
                  {/* The contact SmartFill first found, preserved when a
                      pre-send edit or a cross-domain reply replaced it.
                      Stamped once server-side, so however many times the POC
                      changes hands, the first person is never lost. */}
                  {(company.original_contact_name || company.original_contact_email) && (
                    <div style={{ fontSize: '0.74rem', color: '#94a3b8', marginTop: 6 }}
                         title="The contact originally found by SmartFill, before it was replaced by an edit you made at send time or by a reply from a different address.">
                      Originally: <b style={{ color: '#64748b' }}>{company.original_contact_name || '—'}</b>
                      {company.original_contact_email && (
                        <> · <a href={`mailto:${company.original_contact_email}`}
                                style={{ color: '#64748b', fontWeight: 600 }}>
                          {company.original_contact_email}
                        </a></>
                      )}
                    </div>
                  )}
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
                  {/* Our own stored PDF goes through the authenticated fetch (see
                      dealApi.openChFilingPdf); the Companies House register is
                      public, so that stays an ordinary link. */}
                  {company.ch_pdf_path ? (
                    <button className="cp-chip-btn"
                      onClick={() => dealApi.openChFilingPdf(company.name)
                        .catch(e => alert(e.message))}>
                      View filed accounts PDF
                    </button>
                  ) : (
                    <a className="cp-chip-btn" style={{ textDecoration: 'none', display: 'inline-block' }}
                      href={`https://find-and-update.company-information.service.gov.uk/company/${encodeURIComponent(company.ch_company_number)}/filing-history`}
                      target="_blank" rel="noreferrer">
                      View filings on Companies House
                    </a>
                  )}
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

                {!memo && <p className="cp-empty">No memo yet — Generate IC Memo builds the 10-section memo from the verified record (one grounded search for the market section).</p>}

                {memo && memo.v !== 2 && (
                  <p className="cp-empty" style={{ color: '#b45309' }}>This memo uses the old format — press Regenerate for the 10-section IC structure.</p>
                )}

                {memo && memo.v === 2 && (() => {
                  const ex = memo.executive_summary || {}; const facts = ex.facts || {};
                  const ov = memo.company_overview || {}; const mk = memo.market || {};
                  const dd = memo.diligence || {}; const oq = dd.open_questions || {};
                  const rt = memo.returns || {};
                  const kvs = (obj: any, pairs: Array<[string, string]>) => pairs
                    .filter(([, k]) => obj[k])
                    .map(([lbl, k], i) => <div className="cp-kv" key={i}><span className="k">{lbl}</span><span className="v" style={{ textAlign: 'left', fontWeight: 500 }}>{obj[k]}</span></div>);
                  return (
                    <>
                      <div className="cp-section-title">1 · Executive Summary</div>
                      <div className="cp-card">
                        {kvs(facts, [['Company', 'company'], ['Sector', 'sector'], ['Indicative EV', 'indicative_ev'], ['Equity investment', 'equity_investment'], ['Ownership targeted', 'ownership_targeted']])}
                        {facts.fit_score != null && <div className="cp-kv"><span className="k">Fit score</span><span className="v">{facts.fit_score}/100</span></div>}
                        {facts.recommendation && <div className="cp-kv"><span className="k">Recommendation</span><span className="v" style={{ color: '#15803d' }}>{facts.recommendation}</span></div>}
                        {ex.summary && <p className="cp-memo-p" style={{ marginTop: '0.5rem' }}>{ex.summary}</p>}
                      </div>

                      {(memo.investment_thesis || []).length > 0 && (<><div className="cp-section-title">2 · Investment Thesis</div>
                        <div className="cp-card">{memo.investment_thesis.map((b: string, i: number) => <p className="cp-memo-p" key={i}>• {b}</p>)}</div></>)}

                      {Object.keys(ov).length > 0 && (<><div className="cp-section-title">3 · Company Overview</div>
                        <div className="cp-card">{kvs(ov, [['History', 'history'], ['Products', 'products'], ['Geography', 'geography'], ['Customers', 'customers'], ['Revenue mix', 'revenue_mix'], ['Team', 'team']])}</div></>)}

                      {Object.keys(mk).length > 0 && (<><div className="cp-section-title">4 · Market (sourced)</div>
                        <div className="cp-card">{kvs(mk, [['Size', 'size'], ['Growth', 'growth'], ['Competitors', 'competitors'], ['Demand drivers', 'demand_drivers'], ['Regulation', 'regulation']])}</div></>)}

                      {(memo.financials || []).length > 0 && (<><div className="cp-section-title">5 · Financials (source-tagged)</div>
                        <div className="cp-card">
                          <table className="cp-table"><tbody>
                            {memo.financials.map((r: any, i: number) => (
                              <tr key={i}><td>{r.label}</td><td>{r.value}</td><td style={{ color: '#94a3b8', fontSize: '0.72rem' }}>{r.source}</td></tr>
                            ))}
                          </tbody></table>
                          {sc.fit != null && <p className="cp-memo-p" style={{ color: '#475569', marginTop: '0.4rem' }}><b>Fit {sc.fit}/100</b> · {(sc.subscores || []).filter((s: any) => s.value != null).map((s: any) => `${s.label} ${s.value}`).join(' · ')}</p>}
                        </div></>)}

                      {((dd.verified || []).length > 0 || Object.keys(oq).length > 0) && (<><div className="cp-section-title">6 · Diligence Status (pre-DD)</div>
                        <div className="cp-card">
                          {(dd.verified || []).map((v: string, i: number) => <p className="cp-memo-p" key={i}><span style={{ color: '#15803d' }}>✓</span> {v}</p>)}
                          {(['commercial', 'financial', 'legal', 'technology'] as const).map(ws => (oq[ws] || []).length > 0 && (
                            <p className="cp-memo-p" key={ws}><b style={{ color: '#64748b', textTransform: 'capitalize' }}>{ws}:</b> {(oq[ws] || []).join(' · ')}</p>
                          ))}
                        </div></>)}

                      {(memo.value_creation || []).length > 0 && (<><div className="cp-section-title">7 · Value Creation Plan (hypotheses)</div>
                        <div className="cp-card">{memo.value_creation.map((b: string, i: number) => <p className="cp-memo-p" key={i}>• {b}</p>)}</div></>)}

                      {((memo.risks || []).length > 0 || (memo.registry_flags || []).length > 0) && (<><div className="cp-section-title">8 · Risks &amp; Mitigations</div>
                        <div className="cp-card">
                          <table className="cp-table"><thead><tr><th style={{ textAlign: 'left' }}>Risk</th><th style={{ textAlign: 'left' }}>Mitigation</th></tr></thead><tbody>
                            {(memo.registry_flags || []).slice(0, 2).map((f: string, i: number) => (
                              <tr key={`f${i}`}><td style={{ textAlign: 'left' }}>{f}</td><td style={{ textAlign: 'left', color: '#64748b' }}>Verify in DD; registry-flagged</td></tr>
                            ))}
                            {(memo.risks || []).map((r: any, i: number) => (
                              <tr key={i}><td style={{ textAlign: 'left' }}>{r.risk}</td><td style={{ textAlign: 'left', color: '#64748b' }}>{r.mitigation}</td></tr>
                            ))}
                          </tbody></table>
                        </div></>)}

                      <div className="cp-section-title">9 · Illustrative Returns</div>
                      <div className="cp-card">
                        {rt.available ? (
                          <>
                            <table className="cp-table"><thead><tr><th style={{ textAlign: 'left' }}>Scenario</th><th>Growth</th><th>Exit</th><th>MOIC</th><th>IRR</th></tr></thead><tbody>
                              {(rt.scenarios || []).map((s: any, i: number) => (
                                <tr key={i}><td style={{ textAlign: 'left' }}>{s.scenario}</td><td>{s.revenue_growth_pct}%/yr</td><td>{s.exit_multiple}x</td><td><b>{s.moic}x</b></td><td><b>{s.irr_pct}%</b></td></tr>
                              ))}
                            </tbody></table>
                            <p className="cp-memo-p" style={{ color: '#94a3b8', fontSize: '0.7rem', marginTop: '0.4rem' }}>{rt.note}</p>
                          </>
                        ) : <p className="cp-memo-p">{rt.note || 'Not computable without a revenue figure.'}</p>}
                      </div>

                      {memo.recommendation && (<><div className="cp-section-title">10 · Recommendation</div>
                        <div className="cp-card"><p className="cp-memo-p"><b>{memo.recommendation}</b></p></div></>)}
                    </>
                  );
                })()}
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
