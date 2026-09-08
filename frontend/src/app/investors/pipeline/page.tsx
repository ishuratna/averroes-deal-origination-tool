'use client';

// Investor Pipeline: the kanban of THE INVESTOR LOOP, mirroring the Deals
// pipeline (per Ishu, 8 Sep 2026). The Investor Universe holds everything;
// this board tracks investors we are actively working:
//   Researched (InvestorFill done) → Contacted (we emailed) → Responded (they
//   genuinely replied) → Meeting → Committed, with Passed / Talk Later parked.
// Same rules as founders, same components: the outreach button state and the
// modal come from lib/outreach.ts + OutreachModal (entity 'investor'), the
// stage control asks for a park reason, the follow-up queue is the same
// /followups endpoint (entity=investor) with the same 14 / 7 day thresholds,
// and Sync Emails is the same button. Nothing here is a second copy of logic.

import React, { useCallback, useEffect, useMemo, useState } from 'react';
import { dealApi } from '../../../services/api';
import { Investor } from '../../../types';
import AuthGate from '../../../components/AuthGate';
import SideNav from '../../../components/SideNav';
import MultiSelect from '../../../components/MultiSelect';
import OutreachModal from '../../../components/OutreachModal';
import SyncEmailsButton from '../../../components/SyncEmailsButton';
import InvestorStageControl, { INVESTOR_STAGE_COLORS } from '../../../components/InvestorStageControl';
import InvestorProfile from '../../../components/InvestorProfile';
import { PriorityChip, TagChips } from '../../../components/InvestorPriority';
import { PRIORITY_TIERS, isGcc } from '../../../types';
import { outreachButtonState, owesReply } from '../../../lib/outreach';

const BOARD_STAGES = ['Researched', 'Contacted', 'Responded', 'Meeting', 'Committed'] as const;
const PARKED_STAGES = ['Talk Later', 'Passed'] as const;
const STALE_DAYS = 14;

const regionOf = (i: Investor) => i.global_region || i.hq_country || i.region || '';
const daysSince = (ts?: string) => ts ? Math.floor((Date.now() - new Date(ts).getTime()) / 86_400_000) : null;

export default function InvestorPipeline() {
  return <AuthGate><InvestorPipelineInner /></AuthGate>;
}

function InvestorPipelineInner() {
  const [investors, setInvestors] = useState<Investor[]>([]);
  const [loading, setLoading] = useState(true);
  const [search, setSearch] = useState('');
  const [regionFilter, setRegionFilter] = useState<string[]>([]);   // EMPTY = all
  const [outreachFor, setOutreachFor] = useState<Investor | null>(null);
  const [followups, setFollowups] = useState<any[]>([]);
  const [showFollowups, setShowFollowups] = useState(false);
  const [showParked, setShowParked] = useState(false);
  const [profileName, setProfileName] = useState<string | null>(null);
  const profileInv = profileName ? investors.find(x => x.name === profileName) || null : null;
  const [tierFilter, setTierFilter] = useState<string[]>([]);
  const [gccOnly, setGccOnly] = useState(false);

  const load = useCallback(async () => {
    setLoading(true);
    try {
      setInvestors(await dealApi.getInvestors());
      dealApi.getFollowups(14, 7, 'investor').then(r => setFollowups(r.followups || [])).catch(() => {});
    } catch { /* auth gate handles */ }
    finally { setLoading(false); }
  }, []);
  useEffect(() => { load(); }, [load]);

  const regions = useMemo(() => {
    const set = new Set<string>();
    investors.forEach(i => { const r = regionOf(i); if (r) set.add(r); });
    return Array.from(set).sort();
  }, [investors]);

  const visible = investors.filter(i => {
    const q = search.toLowerCase();
    const matchesSearch = !q || i.name.toLowerCase().includes(q) || (i.investor_type || '').toLowerCase().includes(q)
      || (i.contact_name || '').toLowerCase().includes(q);
    const matchesRegion = regionFilter.length === 0 || regionFilter.includes(regionOf(i));
    const matchesTier = tierFilter.length === 0 || tierFilter.includes(i.priority_tier || '');
    const matchesGcc = !gccOnly || isGcc(i);
    return matchesSearch && matchesRegion && matchesTier && matchesGcc;
  });

  const identifiedCount = visible.filter(i => (i.status || 'Identified') === 'Identified').length;
  const parked = visible.filter(i => (PARKED_STAGES as readonly string[]).includes(i.status || ''));
  const oweCount = followups.filter(f => f.type === 'we_owe_reply').length;

  const card = (inv: Investor) => {
    const ob = outreachButtonState(inv);
    const owes = inv.status === 'Responded' && owesReply(inv);
    const waitingDays = inv.status === 'Contacted' ? daysSince(inv.outreach_sent_at) : null;
    const stale = waitingDays != null && waitingDays >= STALE_DAYS;
    return (
      <div className={`ikb-card${owes ? ' owes' : ''}${stale ? ' stale' : ''}`} key={inv.name}>
        <button className="ikb-name" title={inv.source_companies ? `Portfolio overlap: ${inv.source_companies}` : 'Open the investor card'}
                onClick={() => setProfileName(inv.name)}>{inv.name}</button>
        <div className="ikb-meta">
          <PriorityChip inv={inv} compact />
          <TagChips inv={inv} />
          {inv.investor_type && inv.investor_type !== 'Unknown' && <span className="ikb-chip">{inv.investor_type}</span>}
          {inv.lp_fit_score != null && (
            <span className={`ikb-chip ${inv.lp_fit_score >= 0.7 ? 'fit-high' : inv.lp_fit_score >= 0.4 ? 'fit-mid' : ''}`}>
              Fit {Math.round(inv.lp_fit_score * 100)}
            </span>
          )}
          {regionOf(inv) && <span className="ikb-chip">{regionOf(inv)}</span>}
          {owes && <span className="ikb-chip owe" title="They wrote last; we have not answered">Ball with us</span>}
          {stale && <span className="ikb-chip stale" title={`Our last email ${waitingDays} days ago, no reply`}>{waitingDays}d silent</span>}
        </div>
        {(inv.ticket_min_m != null || inv.ticket_max_m != null) && (
          <div className="ikb-row">Ticket: ${inv.ticket_min_m?.toFixed(1) ?? '?'}M–${inv.ticket_max_m?.toFixed(1) ?? '?'}M</div>
        )}
        {inv.contact_name && <div className="ikb-row">Contact: {inv.contact_name}{inv.contact_email ? ' ✉' : ''}</div>}
        {inv.last_reply_at && <div className="ikb-row">Last reply: {new Date(inv.last_reply_at).toLocaleDateString('en-GB')}{inv.reply_classification ? ` · ${inv.reply_classification}` : ''}</div>}
        {inv.park_reason && <div className="ikb-row park" title={inv.park_reason_detail || ''}>Reason: {inv.park_reason}</div>}
        <div className="ikb-actions">
          <button className={`ikb-outreach ${ob.cls}`} title={ob.title} onClick={() => setOutreachFor(inv)}>{ob.label}</button>
          <InvestorStageControl name={inv.name} status={inv.status} className="ikb-move" onChanged={load} />
        </div>
      </div>
    );
  };

  return (
    <div className="layout-wrapper">
      <SideNav active="investor-pipeline" />
      <main className="main-content" style={{ marginLeft: 260, flex: 1, padding: '1.75rem 2rem', maxWidth: 'calc(100vw - 260px)', minWidth: 0 }}>
        <div className="an-header">
          <div>
            <h1 className="an-title">Investor Pipeline</h1>
            <p className="an-sub">
              Investors we are actively working, from first research to commitment.
              {` ${identifiedCount.toLocaleString()} more sit at Identified in the Investor Universe; run InvestorFill there to promote them.`}
            </p>
          </div>
          <div className="ikb-header-actions">
            {followups.length > 0 && (
              <button className={`followup-btn ${showFollowups ? 'open' : ''} ${oweCount ? 'owe' : ''}`}
                title={`${oweCount} awaiting OUR reply · ${followups.length - oweCount} waiting on them 14+ days`}
                onClick={() => setShowFollowups(v => !v)}>
                ⏰ Follow up <span className="followup-count">{followups.length}</span>
              </button>
            )}
            <SyncEmailsButton onSynced={load} />
          </div>
        </div>

        <div className="ikb-toolbar">
          <input className="ikb-search" placeholder="Search investors, contacts..." value={search} onChange={e => setSearch(e.target.value)} />
          <MultiSelect label="All regions" options={regions} selected={regionFilter} onChange={setRegionFilter} />
          <MultiSelect label="All tiers" options={PRIORITY_TIERS} selected={tierFilter} onChange={setTierFilter} />
          <button className={`ikb-parked-toggle ${gccOnly ? 'on' : ''}`} title="KSA + GCC network only" onClick={() => setGccOnly(v => !v)}>GCC</button>
          <button className="ikb-parked-toggle" onClick={() => setShowParked(v => !v)}>
            {showParked ? 'Hide' : 'Show'} parked ({parked.length})
          </button>
        </div>

        {showFollowups && followups.length > 0 && (
          <section className="followup-panel">
            <div className="followup-head">
              <h3>⏰ Follow-up queue — {followups.length} investors</h3>
              <button className="followup-close" onClick={() => setShowFollowups(false)}>&times;</button>
            </div>
            {(['we_owe_reply', 'waiting_on_them'] as const).map(group => {
              const items = followups.filter(f => f.type === group);
              if (!items.length) return null;
              return (
                <div key={group}>
                  <p className={`followup-group ${group === 'we_owe_reply' ? 'owe' : ''}`}>
                    {group === 'we_owe_reply'
                      ? `🔴 You owe them a reply (${items.length}) — their email is waiting on us`
                      : `🟠 Waiting on them (${items.length}) — our last email unanswered 14+ days`}
                  </p>
                  {items.map((f, i) => (
                    <div className="followup-row" key={i}>
                      <div className="followup-main">
                        <button className="followup-name" onClick={() => {
                          const inv = investors.find(x => x.name === f.name);
                          if (inv) { setShowFollowups(false); setOutreachFor(inv); }
                        }}>{f.name}</button>
                        <span className={`followup-days ${(group === 'we_owe_reply' && f.days_waiting >= 7) || f.days_waiting >= 28 ? 'severe' : ''}`}>
                          {f.days_waiting}d {group === 'we_owe_reply' ? 'unanswered' : 'silent'}
                        </span>
                        <span className="followup-meta">{f.status}{f.contact_name ? ` · ${f.contact_name}` : ''}{f.counterparty_email ? ` · ${f.counterparty_email}` : ''}</span>
                      </div>
                      <p className="followup-email" title={f.snippet || ''}>
                        <b>{f.subject || '(no subject)'}</b> · {group === 'we_owe_reply' ? 'received' : 'sent'} {f.last_email_at ? new Date(f.last_email_at).toLocaleDateString('en-GB') : ''}
                        {f.snippet ? ` — ${String(f.snippet).slice(0, 140)}…` : ''}
                      </p>
                    </div>
                  ))}
                </div>
              );
            })}
          </section>
        )}

        {loading ? <p className="an-empty">Loading investors…</p> : (
          <div className="ikb-board">
            {BOARD_STAGES.map(stage => {
              const cards = visible.filter(i => i.status === stage)
                .sort((a, b) => {
                  // Researched = the outreach queue: highest co-investment priority first
                  if (stage === 'Researched') return (b.priority_score ?? -1) - (a.priority_score ?? -1) || (b.lp_fit_score ?? -1) - (a.lp_fit_score ?? -1);
                  // Responded: the ones awaiting our answer first
                  if (stage === 'Responded') {
                    const oa = owesReply(a) ? 0 : 1, ob = owesReply(b) ? 0 : 1;
                    if (oa !== ob) return oa - ob;
                  }
                  return new Date(b.stage_entered_at || 0).getTime() - new Date(a.stage_entered_at || 0).getTime();
                });
              return (
                <div className="ikb-col" key={stage}>
                  <div className="ikb-col-head">
                    <span className="ikb-col-title" style={{ color: INVESTOR_STAGE_COLORS[stage] }}>{stage}</span>
                    <span className="ikb-col-count">{cards.length}</span>
                  </div>
                  <div className="ikb-cards">
                    {cards.length === 0 && <span className="ikb-empty">No investors here yet.</span>}
                    {cards.map(card)}
                  </div>
                </div>
              );
            })}
            {showParked && PARKED_STAGES.map(stage => {
              const cards = parked.filter(i => i.status === stage);
              return (
                <div className="ikb-col parked" key={stage}>
                  <div className="ikb-col-head">
                    <span className="ikb-col-title" style={{ color: INVESTOR_STAGE_COLORS[stage] }}>{stage}</span>
                    <span className="ikb-col-count">{cards.length}</span>
                  </div>
                  <div className="ikb-cards">
                    {cards.length === 0 && <span className="ikb-empty">Nothing parked.</span>}
                    {cards.map(card)}
                  </div>
                </div>
              );
            })}
          </div>
        )}

        {outreachFor && (
          <OutreachModal entity="investor" company={outreachFor} onClose={() => setOutreachFor(null)} onSent={load} />
        )}
        {profileInv && (
          <InvestorProfile investor={profileInv} onClose={() => setProfileName(null)} onChanged={load} />
        )}
      </main>
    </div>
  );
}
