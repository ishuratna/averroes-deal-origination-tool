"use client";

import { useEffect, useState } from "react";
import Link from "next/link";
import { Investor, INVESTOR_STAGES } from "../../types";
import { dealApi } from "../../services/api";
import InfoTip from "../../components/InfoTip";

const INVESTOR_DEFS: Record<string, string> = {
  name: "Investor / LP name. Mined from portfolio companies' cap tables (PitchBook), uploaded, or found via AI search.",
  fit: "LP Fit Score (0–100): average of 4 criteria — geography (UK/Europe/KSA), private-markets appetite, ticket size fit (£250K–5M), tech affinity. At least 3 of 4 must be evidenced via web search, otherwise unscored.",
  type: "Family Office, Fund of Funds, HNWI/UHNWI, VC, PE, Angel, Corporate or Sovereign — classified by InvestorFill from web evidence.",
  aum: "Assets under management (£M), where disclosed or estimated from public sources.",
  ticket: "Typical investment/commitment size range (£M). Target: £250K–£5M.",
  hq: "Headquarters location. Fit favours UK, Western Europe and Saudi Arabia/GCC.",
  portfolio: "Companies in OUR deal universe this investor has backed — warm-intro path and evidence of relevant appetite.",
  stage: "Relationship stage: Identified → Researched (after InvestorFill) → Contacted → Meeting → Committed / Passed.",
  actions: "InvestorFill researches this investor via AI + web search: classifies type, finds AUM/ticket/contacts, scores LP fit.",
};

const STAGE_COLORS: Record<string, string> = {
  Identified: '#64748b', Researched: '#2563eb', Contacted: '#8b5cf6',
  Meeting: '#f59e0b', Committed: '#16a34a', Passed: '#dc2626',
};

export default function Investors() {
  const [investors, setInvestors] = useState<Investor[]>([]);
  const [loading, setLoading] = useState(true);
  const [searchQuery, setSearchQuery] = useState("");
  const [stageFilter, setStageFilter] = useState<string>("All");
  const [typeFilter, setTypeFilter] = useState<string>("All");
  const [mining, setMining] = useState(false);
  const [filling, setFilling] = useState<string | null>(null);
  const [fillResult, setFillResult] = useState<any | null>(null);
  const [updatingStatus, setUpdatingStatus] = useState<string | null>(null);

  useEffect(() => { loadData(); }, []);

  async function loadData() {
    setLoading(true);
    try {
      const data = await dealApi.getInvestors();
      setInvestors(data);
    } catch (e) { console.error("Failed to load investors", e); }
    finally { setLoading(false); }
  }

  const handleMine = async () => {
    setMining(true);
    try {
      const res = await dealApi.mineInvestors();
      alert(res.message || `Mined ${res.found} investors (${res.inserted_new} new).`);
      await loadData();
    } catch (e) { alert("Mining failed — check backend logs."); }
    finally { setMining(false); }
  };

  const handleFill = async (name: string) => {
    setFilling(name);
    try {
      const res = await dealApi.investorFill(name);
      setFillResult(res);
      await loadData();
    } catch (e: any) { alert(`InvestorFill failed: ${e.message}`); }
    finally { setFilling(null); }
  };

  const handleStatusChange = async (name: string, status: string) => {
    setUpdatingStatus(name);
    try {
      await dealApi.updateInvestorStatus(name, status);
      setInvestors(prev => prev.map(i => i.name === name ? { ...i, status } : i));
    } catch (e) { alert("Status update failed."); }
    finally { setUpdatingStatus(null); }
  };

  const types = Array.from(new Set(investors.map(i => i.investor_type).filter(Boolean))) as string[];

  const filtered = investors.filter(i => {
    const q = searchQuery.toLowerCase();
    const matchesSearch = i.name.toLowerCase().includes(q) || (i.description || '').toLowerCase().includes(q) || (i.source_companies || '').toLowerCase().includes(q);
    const matchesStage = stageFilter === "All" || i.status === stageFilter;
    const matchesType = typeFilter === "All" || i.investor_type === typeFilter;
    return matchesSearch && matchesStage && matchesType;
  });

  const stats = {
    total: investors.length,
    researched: investors.filter(i => i.lp_fit_score != null).length,
    highFit: investors.filter(i => (i.lp_fit_score ?? 0) >= 0.7).length,
    inDialogue: investors.filter(i => ['Contacted', 'Meeting'].includes(i.status || '')).length,
    committed: investors.filter(i => i.status === 'Committed').length,
  };

  const fmtTicket = (i: Investor) => {
    if (i.ticket_min_m == null && i.ticket_max_m == null) return '—';
    const lo = i.ticket_min_m != null ? `£${i.ticket_min_m}M` : '?';
    const hi = i.ticket_max_m != null ? `£${i.ticket_max_m}M` : '?';
    return `${lo}–${hi}`;
  };

  return (
    <div className="layout-wrapper">
      {/* Sidebar */}
      <aside className="sidebar">
        <div className="logo-section"><div className="logo">AVERROES<span>INTEL</span></div></div>
        <nav className="sidebar-nav">
          <div className="nav-group">
            <span className="group-label">Intelligence</span>
            <Link href="/" className="nav-item">Deal Pipeline</Link>
            <Link href="/universe" className="nav-item">Master Universe</Link>
            <Link href="/investors" className="nav-item active">Investors (LPs)</Link>
          </div>
        </nav>
      </aside>

      <main className="main-content">
        <header className="page-header">
          <div>
            <h1>Investor Universe</h1>
            <p className="subtitle">Potential LPs — family offices, funds of funds, HNWIs/UHNWIs — to invest through Averroes</p>
          </div>
          <button className="mine-btn" onClick={handleMine} disabled={mining}>
            {mining ? 'Mining…' : '⛏ Mine from High-Fit Companies'}
          </button>
        </header>

        {/* Stats */}
        <section className="stats-row">
          <div className="stat-card"><span className="stat-value">{stats.total}</span><span className="stat-label">Investors</span></div>
          <div className="stat-card"><span className="stat-value">{stats.researched}</span><span className="stat-label">Researched</span></div>
          <div className="stat-card"><span className="stat-value">{stats.highFit}</span><span className="stat-label">High Fit (70+)</span></div>
          <div className="stat-card"><span className="stat-value">{stats.inDialogue}</span><span className="stat-label">In Dialogue</span></div>
          <div className="stat-card"><span className="stat-value">{stats.committed}</span><span className="stat-label">Committed</span></div>
        </section>

        {/* Filters */}
        <section className="filter-row">
          <input className="search-input" placeholder="Search investors, portfolio companies…" value={searchQuery} onChange={e => setSearchQuery(e.target.value)} />
          <select value={stageFilter} onChange={e => setStageFilter(e.target.value)} className="filter-select">
            <option value="All">All stages</option>
            {INVESTOR_STAGES.map(s => <option key={s} value={s}>{s}</option>)}
          </select>
          <select value={typeFilter} onChange={e => setTypeFilter(e.target.value)} className="filter-select">
            <option value="All">All types</option>
            {types.map(t => <option key={t} value={t}>{t}</option>)}
          </select>
        </section>

        {/* Table */}
        <section className="table-section">
          <div className="table-scroll">
            <table className="inv-table">
              <thead>
                <tr>
                  <th><InfoTip label="Investor" tip={INVESTOR_DEFS.name} /></th>
                  <th><InfoTip label="Fit" tip={INVESTOR_DEFS.fit} /></th>
                  <th><InfoTip label="Type" tip={INVESTOR_DEFS.type} /></th>
                  <th><InfoTip label="AUM" tip={INVESTOR_DEFS.aum} /></th>
                  <th><InfoTip label="Ticket" tip={INVESTOR_DEFS.ticket} /></th>
                  <th><InfoTip label="HQ" tip={INVESTOR_DEFS.hq} /></th>
                  <th>Contact</th>
                  <th>Email</th>
                  <th><InfoTip label="Portfolio Overlap" tip={INVESTOR_DEFS.portfolio} /></th>
                  <th>Source</th>
                  <th><InfoTip label="Stage" tip={INVESTOR_DEFS.stage} /></th>
                  <th><InfoTip label="Actions" tip={INVESTOR_DEFS.actions} /></th>
                </tr>
              </thead>
              <tbody>
                {loading ? (
                  <tr><td colSpan={12} className="empty-row">Loading…</td></tr>
                ) : filtered.length > 0 ? (
                  filtered.map((inv, idx) => (
                    <tr key={idx}>
                      <td className="name-cell" title={inv.description || ''}>{inv.name}</td>
                      <td>
                        {inv.lp_fit_score != null ? (
                          <span className={`fit-badge ${inv.lp_fit_score >= 0.7 ? 'high' : inv.lp_fit_score >= 0.4 ? 'mid' : 'low'}`}>
                            {Math.round(inv.lp_fit_score * 100)}
                          </span>
                        ) : '—'}
                      </td>
                      <td>{inv.investor_type && inv.investor_type !== 'Unknown' ? <span className="type-badge">{inv.investor_type}</span> : '—'}</td>
                      <td className="num-cell">{inv.aum_m ? `£${inv.aum_m >= 1000 ? (inv.aum_m / 1000).toFixed(1) + 'B' : inv.aum_m.toFixed(0) + 'M'}` : '—'}</td>
                      <td className="num-cell">{fmtTicket(inv)}</td>
                      <td>{[inv.hq_city, inv.hq_country].filter(Boolean).join(', ') || inv.region || '—'}</td>
                      <td>{inv.contact_name || '—'}</td>
                      <td className="email-cell">{inv.contact_email ? <a href={`mailto:${inv.contact_email}`}>{inv.contact_email}</a> : '—'}</td>
                      <td className="portfolio-cell" title={inv.source_companies || ''}>{inv.source_companies || '—'}</td>
                      <td className="source-cell">{inv.source || '—'}</td>
                      <td>
                        <select
                          className="stage-select"
                          style={{ color: STAGE_COLORS[inv.status || 'Identified'] }}
                          value={inv.status || 'Identified'}
                          disabled={updatingStatus === inv.name}
                          onChange={e => handleStatusChange(inv.name, e.target.value)}
                        >
                          {INVESTOR_STAGES.map(s => <option key={s} value={s}>{s}</option>)}
                        </select>
                      </td>
                      <td>
                        <button className="fill-btn" disabled={filling === inv.name} onClick={() => handleFill(inv.name)}>
                          {filling === inv.name ? '…' : 'InvestorFill'}
                        </button>
                      </td>
                    </tr>
                  ))
                ) : (
                  <tr><td colSpan={12} className="empty-row">
                    No investors yet. Click &quot;Mine from High-Fit Companies&quot; to extract investors from your qualified deal universe.
                  </td></tr>
                )}
              </tbody>
            </table>
          </div>
        </section>
      </main>

      {/* InvestorFill result modal */}
      {fillResult && (
        <div className="modal-overlay" onClick={() => setFillResult(null)}>
          <div className="fill-modal" onClick={e => e.stopPropagation()}>
            <div className="fill-modal-header">
              <h3>{fillResult.investor}</h3>
              <button className="modal-close" onClick={() => setFillResult(null)}>&times;</button>
            </div>
            <p className="fill-type">{fillResult.investor_type} {fillResult.hq_city ? `· ${fillResult.hq_city}, ${fillResult.hq_country}` : ''}</p>
            {fillResult.description && <p className="fill-desc">{fillResult.description}</p>}
            <div className="fill-scores">
              {[
                ['Geography', fillResult.score_geography],
                ['PE Appetite', fillResult.score_pe_appetite],
                ['Ticket Fit', fillResult.score_ticket_fit],
                ['Tech Affinity', fillResult.score_tech_affinity],
              ].map(([label, score]) => (
                <div key={label as string} className="fill-score-row">
                  <span>{label}</span>
                  <b>{score != null ? Math.round((score as number) * 100) : 'n/a'}</b>
                </div>
              ))}
              <div className="fill-score-row composite">
                <span>LP Fit Score</span>
                <b>{fillResult.lp_fit_score != null ? Math.round(fillResult.lp_fit_score * 100) : `insufficient evidence (${fillResult.criteria_assessed}/4)`}</b>
              </div>
            </div>
            <button className="modal-ok" onClick={() => setFillResult(null)}>OK</button>
          </div>
        </div>
      )}

      <style jsx>{`
        .layout-wrapper { display: flex; min-height: 100vh; background: #f8fafc; }
        .sidebar { width: 260px; background: #fff; border-right: 1px solid #e2e8f0; position: fixed; height: 100vh; z-index: 100; }
        .logo-section { padding: 1.5rem 1.25rem; border-bottom: 1px solid #e2e8f0; }
        .logo { font-weight: 800; font-size: 1rem; letter-spacing: 0.05em; color: #0f172a; }
        .logo span { color: #2563eb; }
        .sidebar-nav { padding: 1.25rem 0.75rem; }
        .nav-group { display: flex; flex-direction: column; gap: 0.25rem; }
        .group-label { font-size: 0.65rem; text-transform: uppercase; letter-spacing: 0.15em; color: #94a3b8; padding-left: 0.75rem; margin-bottom: 0.5rem; font-weight: 700; }
        .sidebar-nav :global(.nav-item) { padding: 0.6rem 0.75rem; border-radius: 8px; color: #475569; font-size: 0.85rem; font-weight: 600; text-decoration: none; }
        .sidebar-nav :global(.nav-item:hover) { background: #f1f5f9; }
        .sidebar-nav :global(.nav-item.active) { color: #2563eb; background: #eff6ff; }

        .main-content { flex: 1; margin-left: 260px; padding: 1.75rem 2rem; }
        .page-header { display: flex; justify-content: space-between; align-items: flex-start; margin-bottom: 1.25rem; }
        .page-header h1 { font-size: 1.4rem; color: #0f172a; }
        .subtitle { font-size: 0.8rem; color: #64748b; margin-top: 0.2rem; }
        .mine-btn { background: #0f172a; color: #fff; border: none; border-radius: 8px; padding: 0.6rem 1.1rem; font-size: 0.8rem; font-weight: 700; cursor: pointer; }
        .mine-btn:hover:not(:disabled) { background: #1e293b; }
        .mine-btn:disabled { opacity: 0.6; cursor: wait; }

        .stats-row { display: flex; gap: 0.75rem; margin-bottom: 1.25rem; }
        .stat-card { background: #fff; border: 1px solid #e2e8f0; border-radius: 10px; padding: 0.8rem 1.2rem; display: flex; flex-direction: column; min-width: 110px; }
        .stat-value { font-size: 1.3rem; font-weight: 800; color: #0f172a; }
        .stat-label { font-size: 0.65rem; text-transform: uppercase; letter-spacing: 0.08em; color: #94a3b8; font-weight: 700; }

        .filter-row { display: flex; gap: 0.6rem; margin-bottom: 1rem; }
        .search-input { flex: 1; max-width: 340px; padding: 0.55rem 0.8rem; border: 1px solid #e2e8f0; border-radius: 8px; font-size: 0.8rem; }
        .filter-select { padding: 0.55rem 0.8rem; border: 1px solid #e2e8f0; border-radius: 8px; font-size: 0.8rem; background: #fff; color: #475569; }

        .table-section { background: #fff; border: 1px solid #e2e8f0; border-radius: 12px; overflow: hidden; }
        .table-scroll { overflow-x: auto; }
        .inv-table { width: 100%; border-collapse: collapse; font-size: 0.78rem; }
        .inv-table th { background: #f8fafc; text-align: left; padding: 0.6rem 0.8rem; font-size: 0.65rem; text-transform: uppercase; letter-spacing: 0.08em; color: #94a3b8; font-weight: 700; border-bottom: 1px solid #e2e8f0; white-space: nowrap; }
        .inv-table td { padding: 0.6rem 0.8rem; border-bottom: 1px solid #f1f5f9; color: #334155; vertical-align: middle; }
        .name-cell { font-weight: 700; color: #0f172a; white-space: nowrap; }
        .num-cell { white-space: nowrap; }
        .email-cell a { color: #2563eb; text-decoration: none; }
        .portfolio-cell { max-width: 200px; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
        .source-cell { color: #94a3b8; white-space: nowrap; }
        .empty-row { text-align: center; padding: 3rem !important; color: #94a3b8; }

        .fit-badge { font-weight: 800; padding: 0.15rem 0.5rem; border-radius: 4px; color: #fff; font-size: 0.72rem; }
        .fit-badge.high { background: #16a34a; }
        .fit-badge.mid { background: #d97706; }
        .fit-badge.low { background: #dc2626; }
        .type-badge { background: #eff6ff; color: #2563eb; font-weight: 700; font-size: 0.65rem; padding: 0.2rem 0.5rem; border-radius: 4px; text-transform: uppercase; letter-spacing: 0.04em; white-space: nowrap; }
        .stage-select { border: 1px solid #e2e8f0; border-radius: 6px; padding: 0.3rem 0.4rem; font-size: 0.72rem; font-weight: 700; background: #fff; cursor: pointer; }
        .fill-btn { background: #2563eb; color: #fff; border: none; border-radius: 6px; padding: 0.35rem 0.7rem; font-size: 0.72rem; font-weight: 700; cursor: pointer; white-space: nowrap; }
        .fill-btn:hover:not(:disabled) { background: #1d4ed8; }
        .fill-btn:disabled { opacity: 0.6; cursor: wait; }

        .modal-overlay { position: fixed; inset: 0; background: rgba(2,6,23,0.5); display: flex; align-items: center; justify-content: center; z-index: 1000; }
        .fill-modal { background: #fff; border-radius: 12px; width: 420px; max-width: 92vw; padding: 1.25rem 1.5rem; box-shadow: 0 20px 50px rgba(2,6,23,0.35); }
        .fill-modal-header { display: flex; justify-content: space-between; align-items: center; }
        .fill-modal-header h3 { font-size: 1.05rem; color: #0f172a; }
        .modal-close { background: none; border: none; font-size: 1.4rem; color: #94a3b8; cursor: pointer; }
        .fill-type { font-size: 0.78rem; color: #2563eb; font-weight: 700; margin: 0.2rem 0 0.5rem 0; }
        .fill-desc { font-size: 0.78rem; color: #475569; margin-bottom: 0.75rem; }
        .fill-scores { border: 1px solid #e2e8f0; border-radius: 8px; overflow: hidden; margin-bottom: 1rem; }
        .fill-score-row { display: flex; justify-content: space-between; padding: 0.5rem 0.8rem; font-size: 0.78rem; border-bottom: 1px solid #f1f5f9; color: #475569; }
        .fill-score-row.composite { background: #f0fdf4; color: #166534; font-weight: 700; border-bottom: none; }
        .modal-ok { width: 100%; background: #0f172a; color: #fff; border: none; border-radius: 8px; padding: 0.55rem; font-weight: 700; cursor: pointer; }
      `}</style>
    </div>
  );
}
