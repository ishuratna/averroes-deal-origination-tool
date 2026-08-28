'use client';

// The Responded page, v3 (agreed with Ishu, 21 Aug 2026): a funnel of three
// OWNED SECTIONS — Nurture (Ishu) -> Associates weekly list -> Qualified leads
// (Bea) — plus the parked lists. Each section is a collapsed card that opens
// on click, so a first-time reader sees the SHAPE of the process before any
// detail. Companies move forward by explicit human clicks:
//
//   Ishu nurtures  ->  "Ready to assign"  ->  routed to Bea's Thursday list
//   or the associates' Wednesday list  ->  confirmed  ->  a call books a
//   Meeting, and the company leaves this page for the Pipeline.
//
// Nothing here owns state: every list is DERIVED server-side in ONE function
// (_responded_group) from track/owner/status, so this page, its header stats
// and the Pipeline board can never disagree. All moves write through the
// shared endpoints.

import { useEffect, useMemo, useState } from 'react';
import SideNav from '../../components/SideNav';
import CompanyProfile from '../../components/CompanyProfile';
import OwnerTag from '../../components/OwnerTag';
import ReplyRuleButton from '../../components/ReplyRuleButton';
import { dealApi } from '../../services/api';
import {
  RESPONDED_SECTIONS, RESPONDED_PARKED, CALL_ASSOCIATES, PARK_REASONS,
  RespondedCompany, RespondedResponse, DealOwner, DealTrack,
  displayStatus,
} from '../../types';

// The keys, flat and in render order, so the profile drawer's prev/next walks
// exactly what the page shows.
const SECTION_KEYS = RESPONDED_SECTIONS.flatMap(s => s.lanes.flatMap(ln => ln.lists.map(l => l.key)));
const PARKED_KEYS = RESPONDED_PARKED.map(p => p.key);
const RENDER_ORDER: string[] = [...SECTION_KEYS, ...PARKED_KEYS, 'progressed'];

export default function RespondedPage() {
  const [data, setData] = useState<RespondedResponse | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState('');
  const [busy, setBusy] = useState<string>('');          // company currently being written
  const [search, setSearch] = useState('');
  // Which cards are open. Everything starts COLLAPSED on purpose: the page
  // reads as a three-step funnel first, and detail only appears on click.
  const [open, setOpen] = useState<Record<string, boolean>>({});
  const [profileIdx, setProfileIdx] = useState<number | null>(null);
  // The park picker: which company + track is being parked, and the reason.
  const [park, setPark] = useState<{ name: string; track: 'later' | 'kill' } | null>(null);
  const [parkBucket, setParkBucket] = useState('');
  const [parkDetail, setParkDetail] = useState('');

  const load = async () => {
    setLoading(true);
    setError('');
    try {
      setData(await dealApi.getResponded());
    } catch (e) {
      setError(e instanceof Error ? e.message : 'Failed to load the Responded page');
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

  // Searching auto-opens every card with a hit, otherwise a match could sit
  // invisible behind a collapsed header.
  useEffect(() => {
    if (!search.trim()) return;
    const opened: Record<string, boolean> = {};
    for (const s of RESPONDED_SECTIONS) {
      if (s.lanes.some(ln => ln.lists.some(l => (byQueue[l.key] || []).length))) opened[s.key] = true;
    }
    for (const p of RESPONDED_PARKED) {
      if ((byQueue[p.key] || []).length) opened[p.key] = true;
    }
    setOpen(o => ({ ...o, ...opened }));
  }, [search, byQueue]);

  // Whoever has fewer live founder conversations takes the next associate
  // call. Counted, not remembered — that is the whole point of showing it.
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

  // Routing decisions. Track A no longer auto-assigns Bea: the Thursday
  // session CONFIRMS a candidate (two-step), so routing CLEARS the owner —
  // a stale owner left on the row must never let a company skip the weekly
  // discussion and land as already confirmed. 'later' keeps its owner as
  // history; the wake-up path clears it before any re-route.
  const route = (c: RespondedCompany, track: DealTrack) =>
    act(() => dealApi.triageCompany(c.name, track, track === 'later' ? undefined : ''), c.name);

  // NO PARK WITHOUT A REASON (per Ishu, 27 Aug 2026): Talk later and Not
  // interested open a picker - a required bucket plus optional detail - and
  // the backend rejects a park that arrives without one.
  const confirmPark = () => {
    if (!park || !parkBucket) return;
    const p = park;
    setPark(null);
    act(() => dealApi.triageCompany(p.name, p.track,
      p.track === 'later' ? undefined : '', parkBucket, parkDetail.trim()), p.name);
  };

  const ready = (c: RespondedCompany, on: boolean) =>
    act(() => dealApi.setAssignmentReady(c.name, on), c.name);

  // A woken Talk-later still carries track='later'; sending it back to Nurture
  // must clear BOTH the track and any stale ready-stamp.
  const backToNurture = (c: RespondedCompany) =>
    act(async () => {
      if (c.track === 'later') await dealApi.triageCompany(c.name, '', '');
      await dealApi.setAssignmentReady(c.name, false);
    }, c.name);

  // Waking a parked company by hand goes where the automatic wake-up goes:
  // Assignment ready, because what it needs next is a routing decision.
  const wakeNow = (c: RespondedCompany) =>
    act(async () => {
      await dealApi.triageCompany(c.name, '', '');
      await dealApi.setAssignmentReady(c.name, true);
    }, c.name);

  const assign = (c: RespondedCompany, owner: DealOwner | '') =>
    act(() => dealApi.setCompanyOwner(c.name, owner), c.name);

  const flatOrder = useMemo(
    () => RENDER_ORDER.flatMap(k => byQueue[k] || []),
    [byQueue],
  );
  const openProfile = (c: RespondedCompany) => {
    const i = flatOrder.findIndex(x => x.name === c.name);
    setProfileIdx(i >= 0 ? i : null);
  };

  const n = (k: string) => byQueue[k]?.length || 0;

  // Header stats, all derived from the SAME queues the lists render.
  const waitingOnYou = (byQueue['nurture'] || []).concat(byQueue['assignment_ready'] || [])
    .filter(c => c.last_direction === 'received').length;
  const liveTotal = companies.filter(c => !['closed', 'talk_later'].includes(c.queue)).length;
  const goingToBea = n('bea_review') + n('bea_assigned');
  const withAssociates = n('assoc_review') + n('assoc_pending');

  const toggle = (key: string) => setOpen(o => ({ ...o, [key]: !o[key] }));

  // The two exits every live list offers: a conversation can pause or end at
  // any point, not only at the first decision.
  const startPark = (c: RespondedCompany, track: 'later' | 'kill') => {
    setParkBucket(''); setParkDetail(''); setPark({ name: c.name, track });
  };

  const exits = (c: RespondedCompany) => (
    <>
      <button className="rsp-btn later" disabled={busy === c.name}
              onClick={() => startPark(c, 'later')}
              title="Warm but not now. Asks why, then parks it below; wakes into Assignment ready in 6 months.">
        Talk later
      </button>
      <button className="rsp-btn kill" disabled={busy === c.name}
              onClick={() => startPark(c, 'kill')}
              title="Close it out. Asks why, then moves to Not interested below; reversible.">
        Not interested
      </button>
    </>
  );

  const actionsFor = (listKey: string, c: RespondedCompany) => {
    switch (listKey) {
      case 'nurture':
        return (
          <>
            <button className="rsp-btn a" disabled={busy === c.name}
                    onClick={() => ready(c, true)}
                    title="The conversation is mature. Moves it to Assignment ready for routing.">
              Ready to assign
            </button>
            {exits(c)}
          </>
        );
      case 'assignment_ready':
        return (
          <>
            <button className="rsp-btn a" disabled={busy === c.name}
                    onClick={() => route(c, 'A')}
                    title="High fit — candidate for Bea, discussed at the Thursday session before it is confirmed.">
              Bea candidate
            </button>
            <button className="rsp-btn b" disabled={busy === c.name}
                    onClick={() => route(c, 'B')}
                    title="An associate takes the call — which one is decided on Wednesday.">
              Associate call
            </button>
            <button className="rsp-btn" disabled={busy === c.name}
                    onClick={() => backToNurture(c)}
                    title="Not mature after all. Returns it to Nurture.">
              Back to nurture
            </button>
            {exits(c)}
          </>
        );
      case 'bea_review':
        return (
          <>
            <button className="rsp-btn a" disabled={busy === c.name}
                    onClick={() => assign(c, 'Bea')}
                    title="The Thursday session agreed: Bea takes it. Moves to Qualified leads.">
              Confirm to Bea
            </button>
            <button className="rsp-btn" disabled={busy === c.name}
                    onClick={() => route(c, '')}
                    title="The session passed on it. Returns to Ishu's Assignment ready list.">
              Back to Ishu
            </button>
            {exits(c)}
          </>
        );
      case 'assoc_review':
        return (
          <>
            <select
              className="rsp-assign"
              value={c.owner || ''}
              disabled={busy === c.name}
              onChange={e => assign(c, e.target.value as DealOwner | '')}
            >
              <option value="">Assign to…</option>
              {CALL_ASSOCIATES.map(a => (
                <option key={a} value={a}>{a}{nextUp === a ? ' (next up)' : ''}</option>
              ))}
            </select>
            <button className="rsp-btn" disabled={busy === c.name}
                    onClick={() => route(c, '')}
                    title="Not one for the associates. Returns to Ishu's Assignment ready list.">
              Back to Ishu
            </button>
            {exits(c)}
          </>
        );
      case 'assoc_pending':
        return (
          <>
            <select
              className="rsp-assign"
              value={c.owner || ''}
              disabled={busy === c.name}
              onChange={e => assign(c, e.target.value as DealOwner | '')}
              title="Reassign, or clear to put it back on the Wednesday list"
            >
              <option value="">Back to Wednesday list</option>
              {CALL_ASSOCIATES.map(a => (
                <option key={a} value={a}>{a}{nextUp === a ? ' (next up)' : ''}</option>
              ))}
            </select>
            {exits(c)}
          </>
        );
      case 'bea_assigned':
        return (
          <>
            <button className="rsp-btn" disabled={busy === c.name}
                    onClick={() => assign(c, '')}
                    title="Back to the Thursday discussion list.">
              Back to discussion
            </button>
            {exits(c)}
          </>
        );
      case 'talk_later':
        return (
          <button className="rsp-btn" disabled={busy === c.name}
                  onClick={() => wakeNow(c)}
                  title="Wake it up now instead of waiting 6 months. Goes to Assignment ready.">
            Wake up now
          </button>
        );
      case 'closed':
        return (
          <button className="rsp-btn" disabled={busy === c.name}
                  onClick={() => route(c, '')}
                  title="Bring it back into the live sections.">
            Restore
          </button>
        );
      default:
        return null;
    }
  };

  // compact = the half-width lane tables in Section 2: Stage and Revenue band
  // give way so the two routes fit side by side (both are one click away in
  // the profile).
  const renderTable = (listKey: string, rows: RespondedCompany[], parked = false, compact = false) => (
    <table className="rsp-table">
      <thead>
        <tr>
          <th style={{ width: compact ? '32%' : '26%' }}>Company</th>
          {!compact && <th style={{ width: '9%' }}>Stage</th>}
          <th style={{ width: compact ? '10%' : '8%' }}>Fit</th>
          {!compact && <th style={{ width: '13%' }}>Revenue band</th>}
          <th style={{ width: compact ? '14%' : '11%' }}>Last reply</th>
          <th style={{ width: compact ? '13%' : '10%' }}>Owner</th>
          <th>Action</th>
        </tr>
      </thead>
      <tbody>
        {rows.map(c => (
          <tr key={c.name}>
            <td>
              <button className="rsp-name" onClick={() => openProfile(c)}>{c.name}</button>
              {/* Hints, never actions: "probably ready" flags a likely-mature
                  conversation (they answered the second email) but only Ishu's
                  click moves it; "back from Talk later" explains why a company
                  reappeared after 6 months asleep. */}
              {listKey === 'nurture' && c.probably_ready && (
                <span className="rsp3-chip ready" title="They answered your second email — likely mature. Your click still decides.">
                  probably ready
                </span>
              )}
              {listKey === 'assignment_ready' && c.resurfaced && (
                <span className="rsp3-chip woke" title="Parked 6 months ago; its Talk-later timer just expired.">
                  back from Talk later
                </span>
              )}
              <div className="rsp-sub">{c.sector || '—'}</div>
              {/* WHY it was parked: the bucket on the row, the detail on
                  hover. Filled by whoever parked it; cleared on unpark. */}
              {parked && c.park_reason && (
                <div className="rsp3-reason"
                     title={c.park_reason_detail || 'No further detail was added.'}>
                  Reason: <b>{c.park_reason}</b>
                </div>
              )}
            </td>
            {!compact && <td>{displayStatus(c.status)}</td>}
            <td>{c.averroes_fit_score != null ? Number(c.averroes_fit_score).toFixed(1) : '—'}</td>
            {!compact && <td>{c.revenue_band || '—'}</td>}
            {/* Red at 7 days, matching the agreed Responded reminder (their
                message last + 7 days). Parked rows never redden. */}
            <td className={!parked && (c.days_since_reply ?? 0) >= 7 ? 'rsp-stale' : ''}>
              {c.days_since_reply != null ? `${c.days_since_reply}d ago` : '—'}
            </td>
            <td><OwnerTag owner={c.owner} /></td>
            <td><div className="rsp-actions">{actionsFor(listKey, c)}</div></td>
          </tr>
        ))}
      </tbody>
    </table>
  );

  return (
    <div className="app-shell">
      <SideNav active="responded" />
      <main className="main-content">
        <header className="page-header">
          <div>
            <h1>Responded</h1>
            <p className="page-sub">
              Everyone who replied, as a three-step funnel: Ishu nurtures, the
              weekly sessions route, Bea and the associates take the calls.
              Click a section to open its lists.
            </p>
          </div>
          <div className="rsp-actions">
            {/* Same component as the Pipeline header: one implementation of the
                reply rule, so this page's total and the board's Responded
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
              <div className="rsp-stat">
                <div className="rsp-stat-n">{companies.length}</div>
                <div className="rsp-stat-l">Total responded</div>
              </div>
              <div className="rsp-stat">
                <div className="rsp-stat-n">{liveTotal}</div>
                <div className="rsp-stat-l">Live conversations</div>
              </div>
              <div className="rsp-stat">
                <div className="rsp-stat-n">{goingToBea}</div>
                <div className="rsp-stat-l">Going to Bea</div>
              </div>
              <div className="rsp-stat">
                <div className="rsp-stat-n">{withAssociates}</div>
                <div className="rsp-stat-l">With Issam/Marianna</div>
              </div>
              <div className="rsp-stat act">
                <div className="rsp-stat-n">{waitingOnYou}</div>
                <div className="rsp-stat-l">Waiting on you</div>
              </div>
            </div>
            {/* Second line: the reconciliation footnote. Live + parked +
                progressed = the Pipeline's Responded-and-beyond count. */}
            <div className="rsp3-subline">
              Talk later: <b>{n('talk_later')}</b> · Not interested: <b>{n('closed')}</b>
              {n('progressed') > 0 && (
                <> · <b>{n('progressed')}</b> moved past a first meeting — managed on the Pipeline</>
              )}
            </div>

            <div className="rsp-filters">
              <input
                className="rsp-search"
                placeholder="Search company, sector or contact…"
                value={search}
                onChange={e => setSearch(e.target.value)}
              />
            </div>

            {loading && !data && <div className="rsp-empty">Loading the funnel…</div>}

            {RESPONDED_SECTIONS.map((s, si) => {
              const twoLanes = s.lanes.length > 1;
              const isOpen = !!open[s.key];
              return (
                <div key={s.key}>
                  <section className={`rsp3-section ${s.tone} ${isOpen ? 'open' : ''}`}>
                    <button className="rsp3-head" onClick={() => toggle(s.key)}
                            aria-expanded={isOpen}>
                      <span className="rsp3-step">{si + 1}</span>
                      <span className="rsp3-title">{s.title}</span>
                      <span className="rsp3-owner">{s.owner}</span>
                      <span className="rsp3-counts">
                        {s.lanes.flatMap(ln => ln.lists).map(l => (
                          <span className="rsp3-count" key={l.key}>
                            {l.label.split(' — ')[0]}: <b>{n(l.key)}</b>
                          </span>
                        ))}
                      </span>
                      <span className="rsp3-chev" aria-hidden>▾</span>
                    </button>
                    <div className="rsp3-body">
                      <div className="rsp3-body-inner">
                        <p className="rsp3-blurb">{s.blurb}</p>
                        {/* Two lanes render side by side, exactly like the two
                            branches in the approved decision tree: the Bea
                            route and the associate route are DIFFERENT KINDS
                            of call, and stacking them read as one queue. */}
                        <div className={twoLanes ? 'rsp3-lanes' : undefined}>
                          {s.lanes.map(ln => (
                            <div className={twoLanes ? `rsp3-lane ${ln.tone}` : undefined} key={ln.key}>
                              {twoLanes && ln.title && (
                                <div className={`rsp3-lane-head ${ln.tone}`}>{ln.title}</div>
                              )}
                              {ln.lists.map((l, li) => {
                                const rows = byQueue[l.key] || [];
                                return (
                                  <div key={l.key}>
                                    {/* The flow inside a lane, drawn: To
                                        discuss ↓ Allocated. */}
                                    {li > 0 && <div className="rsp3-arrow lane" aria-hidden>↓</div>}
                                    <div className="rsp3-list">
                                      <div className="rsp-group-head">
                                        <span className="rsp-group-title">{l.label}</span>
                                        <span className="rsp-group-n">{rows.length}</span>
                                        <span className="rsp-group-hint">{l.hint}</span>
                                      </div>
                                      {rows.length
                                        ? renderTable(l.key, rows, false, twoLanes)
                                        : <div className="rsp-group-empty">Nothing here right now.</div>}
                                    </div>
                                  </div>
                                );
                              })}
                            </div>
                          ))}
                        </div>
                      </div>
                    </div>
                  </section>
                  {/* The arrow between steps: the funnel drawn on the page. */}
                  {si < RESPONDED_SECTIONS.length - 1 && (
                    <div className="rsp3-arrow" aria-hidden>↓</div>
                  )}
                </div>
              );
            })}

            {RESPONDED_PARKED.map(p => {
              const rows = byQueue[p.key] || [];
              const isOpen = !!open[p.key];
              return (
                <section className={`rsp3-section grey parked ${isOpen ? 'open' : ''}`} key={p.key}>
                  <button className="rsp3-head" onClick={() => toggle(p.key)}
                          aria-expanded={isOpen}>
                    <span className="rsp3-title">{p.label}</span>
                    <span className="rsp3-counts"><span className="rsp3-count"><b>{rows.length}</b></span></span>
                    <span className="rsp3-chev" aria-hidden>▾</span>
                  </button>
                  <div className="rsp3-body">
                    <div className="rsp3-body-inner">
                      <p className="rsp3-blurb">{p.hint}</p>
                      {rows.length
                        ? renderTable(p.key, rows, true)
                        : <div className="rsp-group-empty">Nothing parked here.</div>}
                    </div>
                  </div>
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

      {/* The park picker: bucket required, detail optional. */}
      {park && (
        <div className="rsp3-park-overlay" onClick={() => setPark(null)}>
          <div className="rsp3-park-modal" onClick={e => e.stopPropagation()}>
            <h3>
              {park.track === 'later' ? 'Talk later' : 'Not interested'} — {park.name}
            </h3>
            <p className="rsp3-park-sub">
              Why? Pick a reason (shown on the parked list; hover reveals your detail).
            </p>
            <div className="rsp3-park-buckets">
              {PARK_REASONS.map(r => (
                <button key={r.bucket}
                        className={`rsp3-park-bucket ${parkBucket === r.bucket ? 'sel' : ''}`}
                        title={r.description}
                        onClick={() => setParkBucket(r.bucket)}>
                  {r.bucket}
                </button>
              ))}
            </div>
            <textarea
              className="rsp3-park-detail"
              placeholder="Optional detail — e.g. 'Founder mid-Series A, said to call back after close'…"
              value={parkDetail}
              onChange={e => setParkDetail(e.target.value)}
              rows={2}
            />
            <div className="rsp3-park-actions">
              <button className="rsp-btn" onClick={() => setPark(null)}>Cancel</button>
              <button className={`rsp-btn ${park.track === 'later' ? 'later' : 'kill'}`}
                      disabled={!parkBucket}
                      title={parkBucket ? '' : 'Pick a reason bucket first'}
                      onClick={confirmPark}>
                {park.track === 'later' ? 'Park — talk later' : 'Close out — not interested'}
              </button>
            </div>
          </div>
        </div>
      )}

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
