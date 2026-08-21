'use client';

// Analytics — retention-proof funnel stats.
// "Ever" counts come from the backend's immutable analytics_ledger (facts
// survive deletion/re-statusing); "current" counts are live targets rows.
// Activity series (daily emails, SmartFill vs Qualified, universe growth) are
// recomputed from primary sources on every load; the funnel is CUMULATIVE from
// the immutable ledger.

import React, { useCallback, useEffect, useState } from 'react';
import Link from 'next/link';
import { dealApi } from '../../services/api';
import AuthGate from '../../components/AuthGate';
import SideNav from '../../components/SideNav';

interface FunnelRow { stage: string; cumulative: number; ever: number; current: number; conversion_pct: number | null }
interface DailyEmailRow { day: string; sent: number; received: number }
interface EnrichmentRow { day: string; smartfills: number; qualified: number }
interface UniverseRow { month: string; added: number; cumulative: number }
interface Analytics {
  stored_ever: number; stored_current: number;
  funnel: FunnelRow[];
  not_a_fit_ever: number; not_a_fit_current: number;
  emailed_ever: number; replied_ever: number;
  response_rate: number | null;
  daily_emails: DailyEmailRow[];
  daily_enrichment: EnrichmentRow[];
  universe_growth: UniverseRow[];
  inconsistencies?: {
    contacted_without_email?: number;
    responded_without_reply?: number;
    replied_never_emailed?: number;
  };
}

// Stage names read the same everywhere. This page used to relabel Contacted as
// "Responded" — a leftover from the old naming, where the stored value
// 'Contacted' meant "they replied". After the rename that translation INVERTED
// the chart: companies we had emailed with no reply were shown under Responded.
// Never translate a stage name for display. Contacted = we emailed them,
// Responded = they replied, on every page.
const label = (s: string) => s;

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

  const rate = data?.response_rate != null ? `${(data.response_rate * 100).toFixed(1)}%` : '—';
  // The chain runs Qualified -> Won: the funnel's decision path. Lost is not a
  // conversion step (it is an exit that can happen anywhere), so it reads as a
  // footnote rather than a link in the chain.
  const chain = (data?.funnel || []).filter(f => f.stage !== 'Lost');
  const lost = (data?.funnel || []).find(f => f.stage === 'Lost');

  return (
    <div className="layout-wrapper">
      <SideNav active="analytics" />

      <main className="main-content">
        <div className="an-header">
          <div>
            <h1 className="an-title">Analytics</h1>
            <p className="an-sub">
              Cumulative counts: how many companies have ever reached each step.
              Recorded in an immutable ledger, so they survive deletions and re-uploads.
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

            {/* CUMULATIVE FUNNEL — numbers, not bars, on purpose: the universe
                (12k+) dwarfs everything downstream, so proportional bars turn
                every later stage into an invisible sliver. Equal blocks with
                the conversion % between them read the story instead. Starts at
                Qualified; the universe is the caption above. */}
            <section className="an-card">
              <h2 className="an-card-title">Cumulative funnel</h2>
              <p className="an-chain-caption">
                From a universe of <b>{data.stored_ever.toLocaleString()}</b> companies,
                every step a company has ever reached:
              </p>
              <div className="an-chain">
                {chain.map((f, i) => (
                  <div className="an-chain-step" key={f.stage}>
                    {i > 0 && (
                      <div className="an-chain-link">
                        <span className="an-chain-pct">
                          {f.conversion_pct != null ? `${f.conversion_pct}%` : '—'}
                        </span>
                        <span className="an-chain-arrow">→</span>
                      </div>
                    )}
                    <div className={`an-chain-block ${i === 0 ? 'first' : ''}`}>
                      <span className="an-chain-n">{f.cumulative.toLocaleString()}</span>
                      <span className="an-chain-stage">{label(f.stage)}</span>
                    </div>
                  </div>
                ))}
              </div>
              <div className="an-legend">
                <span className="an-legend-note">
                  Percentages are conversion from the previous step. Contacted counts
                  companies we emailed; Responded counts genuine replies (autoresponders
                  and bounces never count). Not a fit: {data.not_a_fit_ever.toLocaleString()}
                  {lost ? <> · Lost: {(lost.cumulative || 0).toLocaleString()}</> : null}
                </span>
              </div>
              {(() => {
                // DATA-INTEGRITY ALARM, not a footnote.
                //
                // The reply rule keeps status in step with the email log, so both
                // of the first two counters MUST be zero. A non-zero value means
                // the rule has not run since something changed, or something is
                // writing status outside it. Companies deliberately kept in
                // Responded by hand are excluded server-side, so they cannot make
                // this fire and dull the signal.
                //
                // replied_never_emailed is NOT a fault: an inbound-first thread is
                // a founder reaching us first, which is why it reads separately.
                const inc = data.inconsistencies || {};
                const faults: string[] = [];
                if (inc.contacted_without_email) faults.push(`${inc.contacted_without_email} sitting in Contacted with no outbound email on record`);
                if (inc.responded_without_reply) faults.push(`${inc.responded_without_reply} sitting in Responded with no genuine reply on record`);
                const inbound = inc.replied_never_emailed || 0;
                return (
                  <>
                    {faults.length > 0 ? (
                      <div className="an-warn">
                        <b>Stages disagree with the email evidence.</b> {faults.join(' · ')}.
                        These should always be zero. Run <b>Check stages</b> on the Pipeline
                        to reconcile them.
                      </div>
                    ) : (
                      <div className="an-ok">
                        Every stage agrees with the email evidence. Contacted means we
                        emailed them; Responded means they genuinely replied.
                      </div>
                    )}
                    {inbound > 0 && (
                      <div className="an-note">
                        {inbound} replied without us emailing first — inbound-first threads,
                        not a fault.
                      </div>
                    )}
                  </>
                );
              })()}
            </section>

            <div className="an-two-col">
              {/* Emails per day, working week */}
              <section className="an-card">
                <h2 className="an-card-title">Emails, last 7 days</h2>
                {data.daily_emails.length > 0 ? (
                  <DailyEmailBars rows={data.daily_emails} />
                ) : (
                  <p className="an-empty">No logged emails in the last 7 days.</p>
                )}
              </section>

              {/* SmartFill vs newly Qualified — qualified drawn INSIDE the
                  smartfill column, so "how much enrichment became pipeline"
                  is literal, not inferred. */}
              <section className="an-card">
                <h2 className="an-card-title">SmartFill vs Qualified, last 7 days</h2>
                {data.daily_enrichment.length > 0 ? (
                  <EnrichmentBars rows={data.daily_enrichment} />
                ) : (
                  <p className="an-empty">No SmartFill runs in the last 7 days.</p>
                )}
              </section>
            </div>

            {/* Universe growth: the asset being built, month by month. Uploads
                appear as visible jumps, which is the honest shape of it. */}
            <section className="an-card">
              <h2 className="an-card-title">Universe growth, cumulative by month</h2>
              {data.universe_growth.length >= 2 ? (
                <UniverseLine rows={data.universe_growth} />
              ) : (
                <p className="an-empty">Growth appears once the universe spans more than one month.</p>
              )}
            </section>
          </>
        )}
        {!data && loading && <div className="an-empty">Loading analytics…</div>}
      </main>
    </div>
  );
}

// ── Hand-rolled charts (no chart deps) ───────────────────────────────────────

const DAY_LABEL = (d: string) =>
  new Date(d + 'T00:00:00').toLocaleDateString('en-GB', { weekday: 'short' });

function DailyEmailBars({ rows }: { rows: DailyEmailRow[] }) {
  const maxV = Math.max(1, ...rows.map(r => Math.max(r.sent, r.received)));
  return (
    <div>
      <div className="an-weeks">
        {rows.map(r => (
          <div className="an-week" key={r.day} title={`${r.day}: ${r.sent} sent, ${r.received} replies`}>
            <div className="an-week-bars">
              <div className="an-wbar an-wbar-sent" style={{ height: `${(r.sent / maxV) * 100}%` }} />
              <div className="an-wbar an-wbar-recv" style={{ height: `${(r.received / maxV) * 100}%` }} />
            </div>
            <span className="an-week-label">{DAY_LABEL(r.day)}</span>
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

function EnrichmentBars({ rows }: { rows: EnrichmentRow[] }) {
  // The qualified column sits INSIDE the smartfill column (same x, in front),
  // so the subset relationship is drawn, not implied by a legend.
  const maxV = Math.max(1, ...rows.map(r => Math.max(r.smartfills, r.qualified)));
  return (
    <div>
      <div className="an-weeks">
        {rows.map(r => (
          <div className="an-week" key={r.day} title={`${r.day}: ${r.smartfills} SmartFills, ${r.qualified} newly qualified`}>
            <div className="an-week-bars an-nest">
              <div className="an-wbar an-wbar-fill" style={{ height: `${(r.smartfills / maxV) * 100}%` }} />
              <div className="an-wbar an-wbar-qual" style={{ height: `${(r.qualified / maxV) * 100}%` }} />
            </div>
            <span className="an-week-label">{DAY_LABEL(r.day)}</span>
          </div>
        ))}
      </div>
      <div className="an-legend">
        <span><i className="an-dot an-dot-fill" /> SmartFill runs</span>
        <span><i className="an-dot an-dot-qual" /> became Qualified</span>
      </div>
    </div>
  );
}

function UniverseLine({ rows }: { rows: UniverseRow[] }) {
  const W = 1040, H = 220, P = 42;
  const maxV = Math.max(1, ...rows.map(r => r.cumulative));
  const x = (i: number) => P + (i / Math.max(1, rows.length - 1)) * (W - 2 * P);
  const y = (v: number) => H - P - (v / maxV) * (H - 2 * P);
  const path = rows.map((r, i) => `${i === 0 ? 'M' : 'L'}${x(i).toFixed(1)},${y(r.cumulative).toFixed(1)}`).join(' ');
  const area = `${path} L${x(rows.length - 1).toFixed(1)},${y(0)} L${x(0).toFixed(1)},${y(0)} Z`;
  return (
    <div>
      <svg viewBox={`0 0 ${W} ${H}`} className="an-svg" role="img" aria-label="Universe growth over time">
        {[0, 0.5, 1].map(t => (
          <g key={t}>
            <line x1={P} x2={W - P} y1={y(maxV * t)} y2={y(maxV * t)} stroke="#e2e8f0" strokeWidth="1" />
            <text x={P - 6} y={y(maxV * t) + 4} textAnchor="end" fontSize="10" fill="#64748b">{Math.round(maxV * t).toLocaleString()}</text>
          </g>
        ))}
        <path d={area} fill="#eff6ff" />
        <path d={path} fill="none" stroke="#2563eb" strokeWidth="2" />
        {rows.map((r, i) => (
          <circle key={r.month} cx={x(i)} cy={y(r.cumulative)} r="3" fill="#2563eb">
            <title>{`${r.month}: ${r.cumulative.toLocaleString()} total (+${r.added.toLocaleString()})`}</title>
          </circle>
        ))}
        {rows.map((r, i) => (rows.length <= 14 || i % 2 === 0) && (
          <text key={`l${r.month}`} x={x(i)} y={H - 10} fontSize="10" fill="#64748b" textAnchor="middle">{r.month}</text>
        ))}
      </svg>
    </div>
  );
}
