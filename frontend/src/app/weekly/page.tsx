'use client';

// The Weekly Review: the Wednesday meeting pack as a LIVE page, laid out as
// three slides. Open it any time - it reads the current data through the same
// endpoints as the pages it summarises, so it can never disagree with them.
// "Print / save PDF" turns the three slides into a shareable deck (each slide
// is one printed page).

import { useEffect, useState } from 'react';
import SideNav from '../../components/SideNav';
import OwnerTag from '../../components/OwnerTag';
import { dealApi } from '../../services/api';
import { DealOwner } from '../../types';

interface WkCompany { name: string; owner: string; days_since_reply?: number | null }
interface WkFunnel { stage: string; cumulative: number; conversion_pct: number | null }
interface WkReview {
  generated_at: string;
  analytics: {
    universe_total?: number; funnel: WkFunnel[];
    emailed_ever?: number; replied_ever?: number; response_rate?: number | null;
    daily_emails: { day: string; sent: number; received: number }[];
    inconsistencies: Record<string, number>;
  };
  last7: { sent: number; genuine_replies: number };
  pipeline: Record<string, WkCompany[]>;
  open_calls: Record<string, number>;
  updates: { date: string; text: string }[];
}

// The Responded page's sections, in its order, in its words.
const PIPELINE_SECTIONS: { key: string; label: string; tone: string }[] = [
  { key: 'nurture',          label: 'Nurture (Ishu)',                     tone: 'plum' },
  { key: 'assignment_ready', label: 'Assignment ready',                   tone: 'plum' },
  { key: 'bea_review',       label: 'To discuss for Bea — Thursday',      tone: 'teal' },
  { key: 'assoc_review',     label: 'To discuss for calls — Wednesday',   tone: 'amber' },
  { key: 'assoc_pending',    label: 'Allocated — call pending',           tone: 'amber' },
  { key: 'bea_assigned',     label: 'With Bea',                           tone: 'teal' },
  { key: 'progressed',       label: 'Progressed past first meeting',      tone: 'teal' },
  { key: 'talk_later',       label: 'Talk later (parked)',                tone: 'grey' },
  { key: 'closed',           label: 'Not interested',                     tone: 'grey' },
];

export default function WeeklyPage() {
  const [data, setData] = useState<WkReview | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState('');

  const load = async () => {
    setLoading(true);
    setError('');
    try {
      setData(await dealApi.getWeeklyReview());
    } catch (e) {
      setError(e instanceof Error ? e.message : 'Failed to load the weekly review');
    } finally {
      setLoading(false);
    }
  };
  useEffect(() => { load(); }, []);

  const a = data?.analytics;
  const alarm = a ? (a.inconsistencies?.contacted_without_email || 0)
                  + (a.inconsistencies?.responded_without_reply || 0) : 0;
  const maxDay = Math.max(1, ...(a?.daily_emails || []).map(d => Math.max(d.sent, d.received)));

  const today = new Date().toLocaleDateString('en-GB', { day: 'numeric', month: 'long', year: 'numeric' });

  return (
    <div className="app-shell">
      <SideNav active="weekly" />
      <main className="main-content wk-print-root">
        <header className="page-header wk-no-print">
          <div>
            <h1>Weekly Review</h1>
            <p className="page-sub">
              The Wednesday meeting pack, live: this week&apos;s numbers, the pipeline
              by section, and what changed in the tool. Always current — same data
              as the Analytics and Responded pages.
            </p>
          </div>
          <div className="rsp-actions">
            <button className="rsp-btn" onClick={() => window.print()} disabled={!data}
                    title="Each slide prints as one page — save as PDF to share the deck.">
              🖨 Print / save PDF
            </button>
            <button className="rsp-btn" onClick={load} disabled={loading}>
              {loading ? 'Loading…' : 'Reload'}
            </button>
          </div>
        </header>

        {error && <div className="rsp-empty" style={{ borderColor: '#f0cdd5', color: '#8c1d2a' }}>{error}</div>}
        {loading && !data && <div className="rsp-empty">Pulling this week&apos;s numbers…</div>}

        {data && a && (
          <>
            {/* ── Slide 1: Analytics ─────────────────────────────────────── */}
            <section className="wk-slide">
              <div className="wk-slide-head">
                <span className="wk-slide-n">1</span>
                <h2>Outreach &amp; response — cumulative</h2>
                <span className="wk-date">{today}</span>
              </div>

              <div className="rsp-summary">
                <div className="rsp-stat"><div className="rsp-stat-n">{a.universe_total ?? '—'}</div><div className="rsp-stat-l">Companies in universe</div></div>
                <div className="rsp-stat"><div className="rsp-stat-n">{a.emailed_ever ?? '—'}</div><div className="rsp-stat-l">Companies emailed</div></div>
                <div className="rsp-stat"><div className="rsp-stat-n">{a.replied_ever ?? '—'}</div><div className="rsp-stat-l">Companies replied</div></div>
                <div className="rsp-stat act"><div className="rsp-stat-n">{a.response_rate != null ? `${(a.response_rate * 100).toFixed(1)}%` : '—'}</div><div className="rsp-stat-l">Response rate</div></div>
              </div>

              <div className="wk-funnel">
                {a.funnel.map((f, i) => (
                  <span key={f.stage} className="wk-funnel-step">
                    {i > 0 && (
                      <span className="wk-funnel-arrow">
                        →{f.conversion_pct != null && <em>{f.conversion_pct}%</em>}
                      </span>
                    )}
                    <span className="wk-funnel-box">
                      <b>{f.cumulative}</b> {f.stage}
                    </span>
                  </span>
                ))}
              </div>
              <p className="wk-note">Cumulative: how many companies have ever reached each stage, with conversion from the previous one.</p>

              <div className="wk-slide-head" style={{ marginTop: '1.1rem' }}>
                <h2 style={{ fontSize: '0.95rem' }}>Last 7 days</h2>
              </div>
              <div className="rsp-summary">
                <div className="rsp-stat"><div className="rsp-stat-n">{data.last7.sent}</div><div className="rsp-stat-l">Outreach emails sent</div></div>
                <div className="rsp-stat"><div className="rsp-stat-n">{data.last7.genuine_replies}</div><div className="rsp-stat-l">Genuine replies received</div></div>
                <div className="rsp-stat">
                  <div className="rsp-stat-n" style={{ color: alarm ? '#8c1d2a' : '#0f5132' }}>{alarm ? alarm : '✓'}</div>
                  <div className="rsp-stat-l">{alarm ? 'Data inconsistencies' : 'Data integrity clean'}</div>
                </div>
              </div>
              <div className="wk-days">
                {a.daily_emails.map(d => (
                  <div className="wk-day" key={d.day} title={`${d.day}: ${d.sent} sent, ${d.received} received`}>
                    <div className="wk-day-bars">
                      <div className="wk-bar sent" style={{ height: `${(d.sent / maxDay) * 46 + 2}px` }} />
                      <div className="wk-bar recv" style={{ height: `${(d.received / maxDay) * 46 + 2}px` }} />
                    </div>
                    <div className="wk-day-l">{d.day.slice(5)}</div>
                  </div>
                ))}
                <div className="wk-legend">
                  <span><i className="wk-bar sent" /> sent</span>
                  <span><i className="wk-bar recv" /> received</span>
                </div>
              </div>
            </section>

            {/* ── Slide 2: Pipeline development ──────────────────────────── */}
            <section className="wk-slide">
              <div className="wk-slide-head">
                <span className="wk-slide-n">2</span>
                <h2>Pipeline development — everyone who replied, by section</h2>
                <span className="wk-date">{today}</span>
              </div>
              {PIPELINE_SECTIONS.map(s => {
                const rows = data.pipeline[s.key] || [];
                if (!rows.length) return null;
                return (
                  <div className="wk-pipe-row" key={s.key}>
                    <div className={`wk-pipe-label ${s.tone}`}>{s.label} <b>{rows.length}</b></div>
                    <div className="wk-pipe-chips">
                      {rows.map(c => (
                        <span className="wk-chip" key={c.name}>
                          {c.name}
                          {c.owner && <OwnerTag owner={c.owner as DealOwner} />}
                          {c.days_since_reply != null && <em>{c.days_since_reply}d</em>}
                        </span>
                      ))}
                    </div>
                  </div>
                );
              })}
              <p className="wk-note">
                Same sections and rules as the Responded page. Open founder conversations:{' '}
                {Object.entries(data.open_calls).map(([o, n]) => `${o} ${n}`).join(' · ')}.
              </p>
            </section>

            {/* ── Slide 3: Tool updates ──────────────────────────────────── */}
            <section className="wk-slide">
              <div className="wk-slide-head">
                <span className="wk-slide-n">3</span>
                <h2>Tool updates this week</h2>
                <span className="wk-date">{today}</span>
              </div>
              <ul className="wk-updates">
                {data.updates.map((u, i) => (
                  <li key={i}>
                    <span className="wk-upd-date">{u.date.slice(5)}</span>
                    {u.text}
                  </li>
                ))}
              </ul>
            </section>
          </>
        )}
      </main>
    </div>
  );
}
