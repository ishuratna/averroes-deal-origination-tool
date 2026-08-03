'use client';

// Investor Pipeline — kanban mirroring the Deals pipeline. The Investors
// Master Universe holds everything (Identified stage, like Uploaded/Scraped
// on the deal side); this board tracks investors we are actively working:
// Researched → Contacted → Meeting → Committed / Passed.
// Stage moves use the SAME endpoint as the master table (doctrine).

import React, { useCallback, useEffect, useMemo, useState } from 'react';
import { dealApi } from '../../../services/api';
import AuthGate from '../../../components/AuthGate';
import SideNav from '../../../components/SideNav';

interface Investor {
  name: string; investor_type?: string; status?: string;
  lp_fit_score?: number; ticket_min_m?: number; ticket_max_m?: number;
  hq_city?: string; hq_country?: string; global_region?: string; region?: string;
  contact_name?: string; contact_email?: string; aum_m?: number;
  source_companies?: string;
}

const BOARD_STAGES = ['Researched', 'Contacted', 'Meeting', 'Committed', 'Passed'] as const;
const ALL_STAGES = ['Identified', ...BOARD_STAGES];
const STAGE_COLORS: Record<string, string> = {
  Researched: '#2563eb', Contacted: '#8b5cf6', Meeting: '#f59e0b',
  Committed: '#16a34a', Passed: '#dc2626',
};

const regionOf = (i: Investor) => i.global_region || i.hq_country || i.region || '';

export default function InvestorPipeline() {
  return <AuthGate><InvestorPipelineInner /></AuthGate>;
}

function InvestorPipelineInner() {
  const [investors, setInvestors] = useState<Investor[]>([]);
  const [loading, setLoading] = useState(true);
  const [search, setSearch] = useState('');
  const [regionFilter, setRegionFilter] = useState('All');
  const [moving, setMoving] = useState('');

  const load = useCallback(async () => {
    setLoading(true);
    try { setInvestors(await dealApi.getInvestors()); } catch { /* auth gate handles */ }
    finally { setLoading(false); }
  }, []);
  useEffect(() => { load(); }, [load]);

  const regions = useMemo(() => {
    const set = new Set<string>();
    investors.forEach(i => { const r = regionOf(i); if (r) set.add(r); });
    return ['All', ...Array.from(set).sort()];
  }, [investors]);

  const visible = investors.filter(i => {
    const q = search.toLowerCase();
    const matchesSearch = !q || i.name.toLowerCase().includes(q) || (i.investor_type || '').toLowerCase().includes(q);
    const matchesRegion = regionFilter === 'All' || regionOf(i) === regionFilter;
    return matchesSearch && matchesRegion;
  });

  const move = async (name: string, status: string) => {
    setMoving(name);
    try {
      await dealApi.updateInvestorStatus(name, status);
      setInvestors(prev => prev.map(i => i.name === name ? { ...i, status } : i));
    } catch { /* keep as-is on failure */ }
    finally { setMoving(''); }
  };

  const identifiedCount = visible.filter(i => (i.status || 'Identified') === 'Identified').length;

  return (
    <div className="layout-wrapper">
      <SideNav active="investor-pipeline" />
      <main className="main-content" style={{ marginLeft: 260, flex: 1, padding: '1.75rem 2rem', maxWidth: 'calc(100vw - 260px)', minWidth: 0 }}>
        <div className="an-header">
          <div>
            <h1 className="an-title">Investor Pipeline</h1>
            <p className="an-sub">
              Investors we are actively working, from first research to commitment.
              {` ${identifiedCount.toLocaleString()} more sit at Identified in the Master Universe — run InvestorFill there to promote them.`}
            </p>
          </div>
        </div>

        <div className="ikb-toolbar">
          <input className="ikb-search" placeholder="Search investors..." value={search} onChange={e => setSearch(e.target.value)} />
          <select className="ikb-select" value={regionFilter} onChange={e => setRegionFilter(e.target.value)}>
            {regions.map(r => <option key={r} value={r}>{r === 'All' ? 'All regions' : r}</option>)}
          </select>
        </div>

        {loading ? <p className="an-empty">Loading investors…</p> : (
          <div className="ikb-board">
            {BOARD_STAGES.map(stage => {
              const cards = visible.filter(i => i.status === stage)
                .sort((a, b) => (b.lp_fit_score ?? -1) - (a.lp_fit_score ?? -1));
              return (
                <div className="ikb-col" key={stage}>
                  <div className="ikb-col-head">
                    <span className="ikb-col-title" style={{ color: STAGE_COLORS[stage] }}>{stage}</span>
                    <span className="ikb-col-count">{cards.length}</span>
                  </div>
                  <div className="ikb-cards">
                    {cards.length === 0 && <span className="ikb-empty">No investors here yet.</span>}
                    {cards.map(inv => (
                      <div className="ikb-card" key={inv.name}>
                        <button className="ikb-name" title={inv.source_companies ? `Portfolio overlap: ${inv.source_companies}` : ''}>{inv.name}</button>
                        <div className="ikb-meta">
                          {inv.investor_type && inv.investor_type !== 'Unknown' && <span className="ikb-chip">{inv.investor_type}</span>}
                          {inv.lp_fit_score != null && (
                            <span className={`ikb-chip ${inv.lp_fit_score >= 0.7 ? 'fit-high' : inv.lp_fit_score >= 0.4 ? 'fit-mid' : ''}`}>
                              Fit {Math.round(inv.lp_fit_score * 100)}
                            </span>
                          )}
                          {regionOf(inv) && <span className="ikb-chip">{regionOf(inv)}</span>}
                        </div>
                        {(inv.ticket_min_m != null || inv.ticket_max_m != null) && (
                          <div className="ikb-row">Ticket: ${inv.ticket_min_m?.toFixed(1) ?? '?'}M–${inv.ticket_max_m?.toFixed(1) ?? '?'}M</div>
                        )}
                        {inv.contact_name && <div className="ikb-row">Contact: {inv.contact_name}{inv.contact_email ? ' ✉' : ''}</div>}
                        <select className="ikb-move" value={stage} disabled={moving === inv.name}
                          onChange={e => move(inv.name, e.target.value)}>
                          {ALL_STAGES.map(s => <option key={s} value={s}>{s === stage ? `Stage: ${s}` : `Move to ${s}`}</option>)}
                        </select>
                      </div>
                    ))}
                  </div>
                </div>
              );
            })}
          </div>
        )}
      </main>
    </div>
  );
}
