'use client';

// Analytics — retention-proof funnel stats.
// "Ever" counts come from the backend's immutable analytics_ledger (facts
// survive deletion/re-statusing); "current" counts are live targets rows.
// Trend series comes from daily snapshots written by the watch job.

import React, { useCallback, useEffect, useState } from 'react';
import Link from 'next/link';
import { dealApi } from '../../services/api';
import AuthGate from '../../components/AuthGate';

interface FunnelRow { stage: string; ever: number; current: number }
interface WeeklyRow { week: string; sent: number; received: number }
interface Snapshot {
  date: string;
  stored_ever?: number; stored_current?: number;
  emailed_ever?: number; replied_ever?: number;
  response_rate?: number | null;
  funnel?: FunnelRow[];
}
interface Analytics {
  stored_ever: number; stored_current: number;
  funnel: FunnelRow[];
  not_a_fit_ever: number; not_a_fit_current: number;
  emailed_ever: number; replied_ever: number;
  response_rate: number | null;
  weekly_emails: WeeklyRow[];
  snapshots: Snapshot[];
}

const STAGE_LABELS: Record<string, string> = { Contacted: 'Responded' };
const label = (s: string) => STAGE_LABELS[s] || s;

export default function Analytics() {
  return <AuthGate><AnalyticsInner /></AuthGate>;
}

function AnalyticsInner() {
  const [data, setData] = useState<Analytics | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState('');

  const load = useCallback(async (refresh: boolean) => {
    setLoading(true);
    setError('');
    try {
      setData(await dealApi.getAnalytics(refresh));
    } catch {
      setError('Failed to load analytics. Try refresh.');
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => { load(false); }, [load]);

  const maxFunnel = Math.max(1, ...(data?.funnel || []).map(f => Math.max(f.ever, f.current)));
  const rate = data?.response_rate != null ? `${(data.response_rate * 100).toFixed(1)}%` : '—';

  return (
    <div className="layout-wrapper">
      <aside className="sidebar">
        <div className="logo-section">
          <div className="logo">AVERROES<span>INTEL</span></div>
        </div>
        <nav className="sidebar-nav">
          <div className="nav-group">
            <span className="group-label">Intelligence</span>
            <Link href="/" className="nav-item">
              <svg width="16" height="16" viewBox="0 0 16 16" fill="none"><path d="M2 3h5v5H2V3zm7 0h5v5H9V3zM2 10h5v4H2v-4zm7 0h5v4H9v-4z" fill="currentColor" opacity="0.7"/></svg>
              Deal Pipeline
            </Link>
            <Link href="/universe" className="nav-item">
              <svg width="16" height="16" viewBox="0 0 16 16" fill="none"><circle cx="8" cy="8" r="6" stroke="currentColor" strokeWidth="1.5" fill="none"/><path d="M2 8h12M8 2c-2 2-2 10 0 12M8 2c2 2 2 10 0 12" stroke="currentColor" strokeWidth="1" fill="none"/></svg>
              Master Universe
            </Link>
            <Link href="/investors" className="nav-item">
              <svg width="16" height="16" viewBox="0 0 16 16" fill="none"><circle cx="8" cy="5" r="3" stroke="currentColor" strokeWidth="1.5" fill="none"/><path d="M2 14c0-3 2.7-5 6-5s6 2 6 5" stroke="currentColor" strokeWidth="1.5" fill="none"/></svg>
              Investors (LPs)
            </Link>
            <Link href="/chat" className="nav-item">
              <svg width="16" height="16" viewBox="0 0 16 16" fill="none"><path d="M2 3.5C2 2.7 2.7 2 3.5 2h9c.8 0 1.5.7 1.5 1.5v6c0 .8-.7 1.5-1.5 1.5H8l-3.5 3v-3h-1C2.7 11 2 10.3 2 9.5v-6z" stroke="currentColor" strokeWidth="1.5" fill="none"/></svg>
              Intelligence Chat
            </Link>
            <Link href="/analytics" className="nav-item active">
              <svg width="16" height="16" viewBox="0 0 16 16" fill="none"><path d="M2 13.5h12M4 11V7m4 4V4m4 7V6" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round"/></svg>
              Analytics
            </Link>
          </div>
        </nav>
        <div className="sidebar-footer">
          <div className="user-profile">
            <div className="avatar">IR</div>
            <div className="user-info">
              <span className="user-name">Ishu Ratna</span>
              <span className="user-role">Associate</span>
            </div>
            <button className="sign-out-btn" title="Sign out" onClick={() => {
              localStorage.removeItem('averroes_id_token');
              sessionStorage.removeItem('averroes_auth_on');
              window.location.reload();
            }}>Sign out</button>
          </div>
        </div>
      </aside>

      <main className="main-content">
        <div className="an-header">
          <div>
            <h1 className="an-title">Analytics</h1>
            <p className="an-sub">
              Ever counts are recorded in an immutable ledger, so they survive deletions
              and re-uploads. Current counts are live database rows.
            </p>
          </div>
          <button className="an-refresh" disabled={loading} onClick={() => load(true)}>
            {loading ? 'Loading…' : 'Refresh'}
          </button>
        </div>

        {error && <div className="an-error">{error}</div>}

        {data && (
          <>
            {/* KPI cards */}
            <section className="an-kpis">
              <div className="an-kpi">
                <span className="an-kpi-label">Companies stored</span>
                <span className="an-kpi-value">{data.stored_ever.toLocaleString()}</span>
                <span className="an-kpi-foot">{data.stored_current.toLocaleString()} currently in database</span>
              </div>
              <div className="an-kpi">
                <span className="an-kpi-label">Companies emailed</span>
                <span className="an-kpi-value">{data.emailed_ever.toLocaleString()}</span>
                <span className="an-kpi-foot">first outreach ever sent</span>
              </div>
              <div className="an-kpi">
                <span className="an-kpi-label">Companies replied</span>
                <span className="an-kpi-value">{data.replied_ever.toLocaleString()}</span>
                <span className="an-kpi-foot">at least one reply received</span>
              </div>
              <div className="an-kpi an-kpi-accent">
                <span className="an-kpi-label">Response rate</span>
                <span className="an-kpi-value">{rate}</span>
                <span className="an-kpi-foot">replied ever / emailed ever</span>
              </div>
            </section>

            {/* Funnel: ever vs current */}
            <section className="an-card">
              <h2 className="an-card-title">Funnel, ever vs current</h2>
              <div className="an-funnel">
                {data.funnel.map(f => (
                  <div className="an-funnel-row" key={f.stage}>
                    <span className="an-stage">{label(f.stage)}</span>
                    <div className="an-bars">
                      <div className="an-bar-line">
                        <div className="an-bar an-bar-ever" style={{ width: `${(f.ever / maxFunnel) * 100}%` }} />
                        <span className="an-bar-num">{f.ever.toLocaleString()}</span>
                      </div>
                      <div className="an-bar-line">
                        <div className="an-bar an-bar-current" style={{ width: `${(f.current / maxFunnel) * 100}%` }} />
                        <span className="an-bar-num">{f.current.toLocaleString()}</span>
                      </div>
                    </div>
                  </div>
                ))}
              </div>
              <div className="an-legend">
                <span><i className="an-dot an-dot-ever" /> ever reached stage</span>
                <span><i className="an-dot an-dot-current" /> currently in stage</span>
                <span className="an-legend-note">
                  Engaged ever = companies we ever emailed · Responded ever = actual inbound replies · Not a Fit: {data.not_a_fit_ever.toLocaleString()} ever · {data.not_a_fit_current.toLocaleString()} current
                </span>
              </div>
            </section>

            <div className="an-two-col">
              {/* Trend from daily snapshots */}
              <section className="an-card">
                <h2 className="an-card-title">Trend, daily snapshots</h2>
                {data.snapshots.length >= 2 ? (
                  <TrendChart snapshots={data.snapshots} />
                ) : (
                  <p className="an-empty">
                    Trend charts appear once a few daily snapshots accumulate
                    (first one was written today; the watch job adds one per day).
                  </p>
                )}
              </section>

              {/* Weekly email volume */}
              <section className="an-card">
                <h2 className="an-card-title">Email volume, last 12 weeks</h2>
                {data.weekly_emails.length > 0 ? (
                  <WeeklyBars rows={data.weekly_emails} />
                ) : (
                  <p className="an-empty">No logged emails in the last 12 weeks.</p>
                )}
              </section>
            </div>
          </>
        )}
        {!data && loading && <div className="an-empty">Loading analytics…</div>}
      </main>
    </div>
  );
}

// ── Hand-rolled SVG line chart (no chart deps) ───────────────────────────────
function TrendChart({ snapshots }: { snapshots: Snapshot[] }) {
  const W = 520, H = 220, P = 34;
  const series: { key: keyof Snapshot; label: string; color: string }[] = [
    { key: 'stored_ever', label: 'Stored ever', color: '#2563eb' },
    { key: 'emailed_ever', label: 'Emailed ever', color: '#b45309' },
    { key: 'replied_ever', label: 'Replied ever', color: '#16a34a' },
  ];
  const vals = snapshots.flatMap(s => series.map(x => Number(s[x.key] ?? 0)));
  const maxV = Math.max(1, ...vals);
  const x = (i: number) => P + (i / Math.max(1, snapshots.length - 1)) * (W - 2 * P);
  const y = (v: number) => H - P - (v / maxV) * (H - 2 * P);
  const path = (key: keyof Snapshot) =>
    snapshots.map((s, i) => `${i === 0 ? 'M' : 'L'}${x(i).toFixed(1)},${y(Number(s[key] ?? 0)).toFixed(1)}`).join(' ');
  return (
    <div>
      <svg viewBox={`0 0 ${W} ${H}`} className="an-svg" role="img" aria-label="Funnel trend over time">
        {[0, 0.5, 1].map(t => (
          <g key={t}>
            <line x1={P} x2={W - P} y1={y(maxV * t)} y2={y(maxV * t)} stroke="#e2e8f0" strokeWidth="1" />
            <text x={P - 6} y={y(maxV * t) + 4} textAnchor="end" fontSize="10" fill="#64748b">{Math.round(maxV * t)}</text>
          </g>
        ))}
        {series.map(s => <path key={s.key as string} d={path(s.key)} fill="none" stroke={s.color} strokeWidth="2" />)}
        <text x={P} y={H - 8} fontSize="10" fill="#64748b">{snapshots[0].date}</text>
        <text x={W - P} y={H - 8} fontSize="10" fill="#64748b" textAnchor="end">{snapshots[snapshots.length - 1].date}</text>
      </svg>
      <div className="an-legend">
        {series.map(s => <span key={s.key as string}><i className="an-dot" style={{ background: s.color }} /> {s.label}</span>)}
      </div>
    </div>
  );
}

function WeeklyBars({ rows }: { rows: WeeklyRow[] }) {
  const maxV = Math.max(1, ...rows.map(r => Math.max(r.sent, r.received)));
  return (
    <div>
      <div className="an-weeks">
        {rows.map(r => (
          <div className="an-week" key={r.week} title={`Week of ${r.week}: ${r.sent} sent, ${r.received} replies`}>
            <div className="an-week-bars">
              <div className="an-wbar an-wbar-sent" style={{ height: `${(r.sent / maxV) * 100}%` }} />
              <div className="an-wbar an-wbar-recv" style={{ height: `${(r.received / maxV) * 100}%` }} />
            </div>
            <span className="an-week-label">{r.week.slice(5)}</span>
          </div>
        ))}
      </div>
      <div className="an-legend">
        <span><i className="an-dot an-dot-sent" /> sent</span>
        <span><i className="an-dot an-dot-recv" /> replies</span>
      </div>
    </div>
  );
}
