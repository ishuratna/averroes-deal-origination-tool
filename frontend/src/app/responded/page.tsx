'use client';

// The Responded page: every company that has ever replied, grouped by what it
// needs NEXT rather than by stage. This is the working surface for Ishu's
// Tuesday/Thursday blocks, and it doubles as the Wednesday and Thursday agenda.
//
// Process reference: docs/Averroes_Deal_Pipeline_Process.pdf
//   Email 1 (Bea, sent by Ishu) -> reply -> Email 2 (the growth-story ask)
//   -> reply -> Ishu triages into Track A (Bea), Track B (associate) or kill.
//
// Nothing here owns state: every queue is DERIVED server-side from email_log
// plus the company's track/owner, so this page and the Pipeline board can never
// disagree. Triage and assignment write through the shared endpoints.

import { useEffect, useMemo, useState } from 'react';
import SideNav from '../../components/SideNav';
import CompanyProfile from '../../components/CompanyProfile';
import OwnerTag from '../../components/OwnerTag';
import ReplyRuleButton from '../../components/ReplyRuleButton';
import { dealApi } from '../../services/api';
import {
  RESPONDED_QUEUES, CALL_ASSOCIATES, DEAL_OWNERS,
  RespondedCompany, RespondedResponse, DealOwner, DealTrack,
  displayStatus,
} from '../../types';

const LIVE_QUEUES: string[] = RESPONDED_QUEUES.filter(q => q.key !== 'closed').map(q => q.key);

export default function RespondedPage() {
  const [data, setData] = useState<RespondedResponse | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState('');
  const [busy, setBusy] = useState<string>('');          // company currently being written
  const [showClosed, setShowClosed] = useState(false);
  const [search, setSearch] = useState('');
  // Index into the flat visible list, so the profile's prev/next arrows walk
  // the same order the page shows.
  const [profileIdx, setProfileIdx] = useState<number | null>(null);

  const load = async () => {
    setLoading(true);
    setError('');
    try {
      setData(await dealApi.getResponded());
    } catch (e) {
      setError(e instanceof Error ? e.message : 'Failed to load the Responded queue');
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => { load(); }, []);

  const companies = data?.companies || [];

  const filtered = useMemo(() => {
    const q = search.trim().toLowerCase();
    if (!q) return companies;
    return companies.filter(c =>
      (c.name || '').toLowerCase().includes(q) ||
      (c.sector || '').toLowerCase().includes(q) ||
      (c.contact_name || '').toLowerCase().includes(q));
  }, [companies, search]);

  const byQueue = useMemo(() => {
    const m: Record<string, RespondedCompany[]> = {};
    for (const c of filtered) (m[c.queue] ||= []).push(c);
    return m;
  }, [filtered]);

  // Whoever has fewer live founder conversations takes the next Track B call.
  // Counted, not remembered — that is the whole point of showing it here.
  const nextUp = useMemo(() => {
    const oc = data?.open_calls || {};
    const [a, b] = CALL_ASSOCIATES;
    if ((oc[a] ?? 0) === (oc[b] ?? 0)) return null;   // genuinely tied: your call
    return (oc[a] ?? 0) < (oc[b] ?? 0) ? a : b;
  }, [data]);

  const act = async (fn: () => Promise<unknown>, name: string) => {
    setBusy(name);
    try {
      await fn();
      await load();
    } catch (e) {
      alert(e instanceof Error ? e.message : 'Action failed');
    } finally {
      setBusy('');
    }
  };

  const triage = (c: RespondedCompany, track: DealTrack) => {
    // Track A goes to Bea; Track B is deliberately left unassigned so the
    // Wednesday session allocates it. A kill clears the owner entirely.
    const owner: DealOwner | '' = track === 'A' ? 'Bea' : '';
    act(() => dealApi.triageCompany(c.name, track, owner), c.name);
  };

  const assign = (c: RespondedCompany, owner: DealOwner | '') =>
    act(() => dealApi.setCompanyOwner(c.name, owner), c.name);

  const visibleQueues = RESPONDED_QUEUES.filter(q =>
    showClosed ? true : q.key !== 'closed');

  // Flat list in the exact order rendered below, so the profile drawer's
  // prev/next moves through what the user is actually looking at.
  const flatOrder = useMemo(
    () => visibleQueues.flatMap(q => byQueue[q.key] || []),
    [visibleQueues, byQueue],
  );
  const openProfile = (c: RespondedCompany) => {
    const i = flatOrder.findIndex(x => x.name === c.name);
    setProfileIdx(i >= 0 ? i : null);
  };

  const liveTotal = companies.filter(c => LIVE_QUEUES.includes(c.queue)).length;
  const needsAction = (byQueue['needs_email_2']?.length || 0) + (byQueue['needs_triage']?.length || 0);

  return (
    <div className="app-shell">
      <SideNav active="responded" />
      <main className="main-content">
        <header className="page-header">
          <div>
            <h1>Responded</h1>
            <p className="page-sub">
              Every company that has ever replied, grouped by what it needs next.
              Emails go out from Bea&apos;s mailbox; Ishu triages each Email 2 reply.
            </p>
          </div>
          <div className="rsp-actions">
            {/* Same component as the Pipeline header: one implementation of the
                reply rule, so this page's count and the board's Responded
                column can never drift apart. */}
            <ReplyRuleButton onDone={load} />
            <button className="rsp-btn" onClick={load} disabled={loading}>
              {loading ? 'Loading…' : 'Reload'}
            </button>
          </div>
        </header>

        {error && <div className="rsp-empty" style={{ borderColor: '#f0cdd5', color: '#8c1d2a' }}>{error}</div>}

        {!error && (
          <>
            <div className="rsp-summary">
              <div className="rsp-stat act">
                <div className="rsp-stat-n">{needsAction}</div>
                <div className="rsp-stat-l">Waiting on Ishu</div>
              </div>
              <div className="rsp-stat">
                <div className="rsp-stat-n">{byQueue['track_a_awaiting_thursday']?.length || 0}</div>
                <div className="rsp-stat-l">For Thursday</div>
              </div>
              <div className="rsp-stat">
                <div className="rsp-stat-n">{byQueue['track_b_awaiting_wednesday']?.length || 0}</div>
                <div className="rsp-stat-l">For Wednesday</div>
              </div>
              <div className="rsp-stat">
                <div className="rsp-stat-n">{liveTotal}</div>
                <div className="rsp-stat-l">Live conversations</div>
              </div>
            </div>

            <div className="rsp-load-panel">
              <div className="rsp-load-title">Open founder conversations</div>
              <div className="rsp-load-row">
                {DEAL_OWNERS.map(o => (
                  <span className="rsp-load-item" key={o}>
                    <OwnerTag owner={o} />
                    <span className="rsp-load-count">{data?.open_calls?.[o] ?? 0}</span>
                    {nextUp === o && <span className="rsp-load-next">next up</span>}
                  </span>
                ))}
              </div>
              <div className="rsp-load-hint">
                {nextUp
                  ? `${nextUp} has fewer live conversations, so the next Track B call goes to them.`
                  : 'Issam and Marianna are level, so the next Track B call is your call on Wednesday.'}
              </div>
            </div>

            <div className="rsp-filters">
              <input
                className="rsp-search"
                placeholder="Search company, sector or contact…"
                value={search}
                onChange={e => setSearch(e.target.value)}
              />
              <button
                className={`rsp-chip ${showClosed ? 'on' : ''}`}
                onClick={() => setShowClosed(v => !v)}
              >
                {showClosed ? 'Hiding nothing' : 'Show closed'}
              </button>
            </div>

            {loading && !data && <div className="rsp-empty">Loading the queue…</div>}

            {visibleQueues.map(q => {
              const rows = byQueue[q.key] || [];
              if (!rows.length) return null;
              const isTriage = q.key === 'needs_triage';
              const isAllocate = q.key === 'track_b_awaiting_wednesday';
              return (
                <section className={`rsp-group ${q.tone}`} key={q.key}>
                  <div className="rsp-group-head">
                    <span className="rsp-group-title">{q.label}</span>
                    <span className="rsp-group-n">{rows.length}</span>
                    <span className="rsp-group-hint">{q.hint}</span>
                  </div>
                  <table className="rsp-table">
                    <thead>
                      <tr>
                        <th style={{ width: '26%' }}>Company</th>
                        <th style={{ width: '10%' }}>Stage</th>
                        <th style={{ width: '10%' }}>Fit</th>
                        <th style={{ width: '13%' }}>Revenue band</th>
                        <th style={{ width: '11%' }}>Last reply</th>
                        <th style={{ width: '12%' }}>Owner</th>
                        <th>Action</th>
                      </tr>
                    </thead>
                    <tbody>
                      {rows.map(c => (
                        <tr key={c.name}>
                          <td>
                            <button className="rsp-name" onClick={() => openProfile(c)}>{c.name}</button>
                            <div className="rsp-sub">{c.sector || '—'}</div>
                          </td>
                          <td>{displayStatus(c.status)}</td>
                          <td>{c.averroes_fit_score != null ? Number(c.averroes_fit_score).toFixed(1) : '—'}</td>
                          <td>{c.revenue_band || '—'}</td>
                          <td className={(c.days_since_reply ?? 0) >= 14 ? 'rsp-stale' : ''}>
                            {c.days_since_reply != null ? `${c.days_since_reply}d ago` : '—'}
                          </td>
                          <td><OwnerTag owner={c.owner} /></td>
                          <td>
                            <div className="rsp-actions">
                              {isTriage && (
                                <>
                                  <button className="rsp-btn a" disabled={busy === c.name}
                                          onClick={() => triage(c, 'A')} title="High fit — to Bea via the Thursday session">
                                    Track A
                                  </button>
                                  <button className="rsp-btn b" disabled={busy === c.name}
                                          onClick={() => triage(c, 'B')} title="Low or moderate fit, or too early — associate call">
                                    Track B
                                  </button>
                                  <button className="rsp-btn kill" disabled={busy === c.name}
                                          onClick={() => triage(c, 'kill')} title="Close out with a decline from Bea's mailbox">
                                    Kill
                                  </button>
                                </>
                              )}
                              {isAllocate && (
                                <select
                                  className="rsp-assign"
                                  value={c.owner || ''}
                                  disabled={busy === c.name}
                                  onChange={e => assign(c, e.target.value as DealOwner | '')}
                                >
                                  <option value="">Assign to…</option>
                                  {CALL_ASSOCIATES.map(a => (
                                    <option key={a} value={a}>
                                      {a}{nextUp === a ? ' (next up)' : ''}
                                    </option>
                                  ))}
                                </select>
                              )}
                              {!isTriage && !isAllocate && (
                                <select
                                  className="rsp-assign"
                                  value={c.owner || ''}
                                  disabled={busy === c.name}
                                  onChange={e => assign(c, e.target.value as DealOwner | '')}
                                >
                                  <option value="">Unassigned</option>
                                  {DEAL_OWNERS.map(o => <option key={o} value={o}>{o}</option>)}
                                </select>
                              )}
                            </div>
                          </td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                </section>
              );
            })}

            {!loading && !filtered.length && (
              <div className="rsp-empty">
                {search ? 'No replied-to company matches that search.' : 'No company has replied yet.'}
              </div>
            )}
          </>
        )}
      </main>

      {profileIdx != null && flatOrder[profileIdx] && (
        <CompanyProfile
          companies={flatOrder}
          index={profileIdx}
          onClose={() => setProfileIdx(null)}
          onNavigate={setProfileIdx}
          onChanged={load}
        />
      )}
    </div>
  );
}
