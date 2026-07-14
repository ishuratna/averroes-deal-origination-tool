"use client";

import { useEffect, useState, useRef } from "react";
import Link from "next/link";
import { Investor, INVESTOR_STAGES } from "../../types";
import { dealApi } from "../../services/api";
import InfoTip from "../../components/InfoTip";
import AuthGate from "../../components/AuthGate";

const INVESTOR_DEFS: Record<string, string> = {
  name: "Investor / LP name. Mined from portfolio companies' cap tables, uploaded from PitchBook LP exports, or found via AI search. Hover a name to see the description.",
  fit: "LP Fit Score (0–100): average of 4 criteria — geography (UK/Europe/KSA), private-markets appetite, ticket size fit (£250K–5M), tech affinity. At least 3 of 4 must be evidenced via web search, otherwise unscored.",
  type: "Family Office, Fund of Funds, HNWI/UHNWI, VC, PE, Angel, Corporate or Sovereign — from the PitchBook export or classified by InvestorFill.",
  aum: "Assets under management in $M (PitchBook exports in USD), or as found by InvestorFill.",
  ticket: "Preferred commitment size range ($M, from PitchBook). Target: roughly £250K–£5M equivalent.",
  hq: "Headquarters location. Fit favours UK, Western Europe and Saudi Arabia/GCC.",
  strategy: "PE-relevant fund strategy preferences from PitchBook (Buyout, Growth/Expansion, FoF, Co-Investment, Secondaries…). 'None relevant' = they state preferences, but not ours — a real negative.",
  geoPref: "Geographies in their stated investment mandate, condensed to our targets (UK/Ireland/Europe/Middle East). 'Global' = 100+ territory mandate.",
  firstTime: "Open to first-time funds (per PitchBook). Decisive when raising a first fund; blank = undisclosed.",
  commitments: "Track record: number of PE fund commitments · total commitments across all funds ($M). Proof they actually write cheques.",
  portfolio: "Companies in OUR deal universe this investor has backed — warm-intro path and evidence of relevant appetite.",
  stage: "Relationship stage: Identified → Researched (after InvestorFill) → Contacted → Meeting → Committed / Passed.",
  actions: "InvestorFill researches this investor via AI + web search: classifies type, finds AUM/ticket/contacts, scores LP fit.",
};

const STAGE_COLORS: Record<string, string> = {
  Identified: '#64748b', Researched: '#2563eb', Contacted: '#8b5cf6',
  Meeting: '#f59e0b', Committed: '#16a34a', Passed: '#dc2626',
};

export default function Investors() {
  return <AuthGate><InvestorsInner /></AuthGate>;
}

function InvestorsInner() {
  const [investors, setInvestors] = useState<Investor[]>([]);
  const [loading, setLoading] = useState(true);
  const [searchQuery, setSearchQuery] = useState("");
  const [stageFilter, setStageFilter] = useState<string>("All");
  const [typeFilter, setTypeFilter] = useState<string>("All");
  const [mining, setMining] = useState(false);
  const [filling, setFilling] = useState<string | null>(null);
  const [fillResult, setFillResult] = useState<any | null>(null);
  const [updatingStatus, setUpdatingStatus] = useState<string | null>(null);
  const [showSources, setShowSources] = useState(false);
  const [uploading, setUploading] = useState(false);

  // Bulk InvestorFill
  const [bulkEligibility, setBulkEligibility] = useState<any | null>(null);
  const [bulkLoading, setBulkLoading] = useState(false);
  const [bulkRunning, setBulkRunning] = useState(false);
  const [bulkProgress, setBulkProgress] = useState<{ done: number; total: number; current: string; ok: number; failed: number } | null>(null);
  const bulkCancelRef = useRef(false);

  // LP Outreach
  const [outreachDraft, setOutreachDraft] = useState<any | null>(null);
  const [outreachLoading, setOutreachLoading] = useState<string | null>(null);
  const [outreachSending, setOutreachSending] = useState(false);

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

  const [scraping, setScraping] = useState<string | null>(null);
  const handleScrape = async (sourceName: string) => {
    setScraping(sourceName);
    try {
      const res = await dealApi.scrapeInvestors(sourceName);
      alert(res.message || `Found ${res.found} investors.`);
      await loadData();
    } catch (e: any) { alert(`Scrape failed: ${e.message}`); }
    finally { setScraping(null); }
  };

  const handleUpload = async (file: File) => {
    setUploading(true);
    try {
      const res = await dealApi.uploadInvestorFile(file);
      alert(res.message || `Parsed ${res.parsed} investors (${res.inserted_new} new).`);
      await loadData();
    } catch (e: any) { alert(`Upload failed: ${e.message}`); }
    finally { setUploading(false); }
  };

  // ── Bulk InvestorFill ──
  const openBulkFill = async () => {
    setBulkLoading(true);
    try {
      const data = await dealApi.getInvestorFillEligible();
      setBulkEligibility(data);
    } catch (e) { alert('Failed to load eligibility — is the backend deployed?'); }
    finally { setBulkLoading(false); }
  };

  const runBulkFill = async () => {
    if (!bulkEligibility?.eligible_names?.length) return;
    const names: string[] = bulkEligibility.eligible_names;
    bulkCancelRef.current = false;
    setBulkRunning(true);
    let ok = 0, failed = 0;
    for (let i = 0; i < names.length; i++) {
      if (bulkCancelRef.current) break;
      setBulkProgress({ done: i, total: names.length, current: names[i], ok, failed });
      try { await dealApi.investorFill(names[i]); ok++; }
      catch (e: any) {
        if ((e?.message || '').includes('budget') || (e?.message || '').includes('limit')) {
          alert(`Daily free-tier budget reached after ${ok} investors — the rest are preserved for tomorrow.`);
          break;
        }
        failed++; console.error(`Bulk InvestorFill failed for ${names[i]}`, e);
      }
      await new Promise(r => setTimeout(r, 1500));
    }
    setBulkProgress({ done: ok + failed, total: names.length, current: '', ok, failed });
    setBulkRunning(false);
    await loadData();
  };

  const closeBulkModal = () => {
    if (bulkRunning) {
      if (!confirm('A bulk run is in progress. Cancel it?')) return;
      bulkCancelRef.current = true;
    }
    setBulkEligibility(null);
    setBulkProgress(null);
  };

  // ── LP Outreach ──
  const openOutreach = async (inv: Investor) => {
    setOutreachLoading(inv.name);
    try {
      const draft = await dealApi.draftInvestorOutreach(inv.name);
      setOutreachDraft({ ...draft, investor: inv.name });
    } catch (e: any) { alert(`Draft failed: ${e.message}`); }
    finally { setOutreachLoading(null); }
  };

  const sendOutreach = async () => {
    if (!outreachDraft?.to) { alert('No recipient email — run InvestorFill to find contacts first.'); return; }
    setOutreachSending(true);
    try {
      await dealApi.sendInvestorOutreach(outreachDraft.to, outreachDraft.subject, outreachDraft.body, outreachDraft.investor);
      alert('Sent. Stage moved to Contacted.');
      setOutreachDraft(null);
      await loadData();
    } catch (e: any) { alert(`Send failed: ${e.message}`); }
    finally { setOutreachSending(false); }
  };

  // ── CSV export of the current filtered view ──
  const exportCsv = () => {
    const cols = ['name', 'investor_type', 'lp_fit_score', 'aum_m', 'net_assets_m', 'ticket_min_m', 'ticket_max_m', 'hq_city', 'hq_country', 'strategy_preferences', 'geo_preferences', 'open_to_first_time', 'num_pe_commitments', 'total_commitments_m', 'contact_name', 'contact_title', 'contact_email', 'contact_phone', 'psc_summary', 'officers_summary', 'registration_number', 'source', 'source_companies', 'status'];
    const esc = (v: any) => {
      const s = v == null ? '' : String(v);
      return /[",\n]/.test(s) ? `"${s.replace(/"/g, '""')}"` : s;
    };
    const lines = [cols.join(','), ...filtered.map(inv => cols.map(c => esc((inv as any)[c])).join(','))];
    const blob = new Blob([lines.join('\n')], { type: 'text/csv;charset=utf-8' });
    const url = URL.createObjectURL(blob);
    const a = document.createElement('a');
    a.href = url;
    a.download = `averroes_lp_shortlist_${new Date().toISOString().slice(0, 10)}.csv`;
    a.click();
    URL.revokeObjectURL(url);
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
    const lo = i.ticket_min_m != null ? `$${i.ticket_min_m.toFixed(1)}M` : '?';
    const hi = i.ticket_max_m != null ? `$${i.ticket_max_m.toFixed(1)}M` : '?';
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
            <Link href="/" className="nav-item">
              <svg width="16" height="16" viewBox="0 0 16 16" fill="none"><path d="M2 2h3v12H2zM6.5 2h3v8h-3zM11 2h3v10h-3z" fill="currentColor" opacity="0.7"/></svg>
              Deal Pipeline
            </Link>
            <Link href="/universe" className="nav-item">
              <svg width="16" height="16" viewBox="0 0 16 16" fill="none"><circle cx="8" cy="8" r="6" stroke="currentColor" strokeWidth="1.5" fill="none"/><path d="M2 8h12M8 2c-2 2-2 10 0 12M8 2c2 2 2 10 0 12" stroke="currentColor" strokeWidth="1" fill="none"/></svg>
              Master Universe
            </Link>
            <Link href="/investors" className="nav-item active">
              <svg width="16" height="16" viewBox="0 0 16 16" fill="none"><circle cx="8" cy="5" r="3" stroke="currentColor" strokeWidth="1.5" fill="none"/><path d="M2 14c0-3 2.7-5 6-5s6 2 6 5" stroke="currentColor" strokeWidth="1.5" fill="none"/></svg>
              Investors (LPs)
            </Link>
            <Link href="/chat" className="nav-item">
              <svg width="16" height="16" viewBox="0 0 16 16" fill="none"><path d="M2 3.5C2 2.7 2.7 2 3.5 2h9c.8 0 1.5.7 1.5 1.5v6c0 .8-.7 1.5-1.5 1.5H8l-3.5 3v-3h-1C2.7 11 2 10.3 2 9.5v-6z" stroke="currentColor" strokeWidth="1.5" fill="none"/></svg>
              Intelligence Chat
            </Link>
          </div>
        </nav>
      </aside>

      <main className="main-content">
        <header className="page-header">
          <div>
            <h1>Investor Universe</h1>
            <p className="subtitle">Potential LPs — family offices, funds of funds, HNWIs/UHNWIs — to invest through Averroes</p>
          </div>
          <div className="header-actions">
            <button className="sources-btn" onClick={() => setShowSources(true)}>
              Sources
              <span className="sources-badge">{Array.from(new Set(investors.map(i => i.source).filter(Boolean))).length}</span>
            </button>
            <button className="export-btn" onClick={exportCsv} disabled={filtered.length === 0}>
              ⬇ Export ({filtered.length})
            </button>
            <button className="bulkfill-btn" onClick={openBulkFill} disabled={bulkLoading || bulkRunning}>
              {bulkLoading ? 'Checking…' : bulkRunning ? 'Running…' : '⚡ Bulk InvestorFill'}
            </button>
          </div>
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
                  <th><InfoTip label="PE Strategy" tip={INVESTOR_DEFS.strategy} /></th>
                  <th><InfoTip label="Geo Mandate" tip={INVESTOR_DEFS.geoPref} /></th>
                  <th><InfoTip label="1st-Time" tip={INVESTOR_DEFS.firstTime} /></th>
                  <th><InfoTip label="Commitments" tip={INVESTOR_DEFS.commitments} /></th>
                  <th>Contact</th>
                  <th>Email</th>
                  <th><InfoTip label="Portfolio Overlap" tip={INVESTOR_DEFS.portfolio} /></th>
                  <th>Source</th>
                  <th><InfoTip label="Added" tip="When this investor was FIRST added to the database. Preserved across re-uploads, merges and enrichment. Table is sorted by this, oldest first." /></th>
                  <th><InfoTip label="Stage" tip={INVESTOR_DEFS.stage} /></th>
                  <th><InfoTip label="Actions" tip={INVESTOR_DEFS.actions} /></th>
                </tr>
              </thead>
              <tbody>
                {loading ? (
                  <tr><td colSpan={17} className="empty-row">Loading…</td></tr>
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
                      <td className="num-cell">{inv.aum_m ? `$${inv.aum_m >= 1000 ? (inv.aum_m / 1000).toFixed(1) + 'B' : inv.aum_m.toFixed(0) + 'M'}` : '—'}</td>
                      <td className="num-cell">{fmtTicket(inv)}</td>
                      <td>{[inv.hq_city, inv.hq_country].filter(Boolean).join(', ') || inv.region || '—'}</td>
                      <td className="strat-cell" title={inv.strategy_preferences || ''}>
                        {inv.strategy_preferences
                          ? <span className={inv.strategy_preferences === 'None relevant' ? 'strat-none' : 'strat-ok'}>{inv.strategy_preferences}</span>
                          : '—'}
                      </td>
                      <td className="geo-cell" title={inv.geo_preferences || ''}>{inv.geo_preferences || '—'}</td>
                      <td>
                        {inv.open_to_first_time === 'Yes' ? <span className="ft-yes">Yes</span>
                          : inv.open_to_first_time === 'No' ? <span className="ft-no">No</span> : '—'}
                      </td>
                      <td className="num-cell">
                        {inv.num_pe_commitments != null || inv.total_commitments_m != null
                          ? <>{inv.num_pe_commitments != null ? `${inv.num_pe_commitments} PE` : '—'}{inv.total_commitments_m != null ? ` · $${inv.total_commitments_m.toFixed(0)}M` : ''}</>
                          : '—'}
                      </td>
                      <td title={inv.contact_title || ''}>{inv.contact_name || '—'}</td>
                      <td className="email-cell">{inv.contact_email ? <a href={`mailto:${inv.contact_email}`}>{inv.contact_email}</a> : '—'}</td>
                      <td className="portfolio-cell" title={inv.source_companies || ''}>{inv.source_companies || '—'}</td>
                      <td className="source-cell">{inv.source || '—'}</td>
                      <td className="num-cell">{inv.ingested_at ? new Date(inv.ingested_at).toLocaleDateString('en-GB') : '—'}</td>
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
                        <div className="action-btns">
                          <button className="fill-btn" disabled={filling === inv.name} onClick={() => handleFill(inv.name)}>
                            {filling === inv.name ? '…' : 'InvestorFill'}
                          </button>
                          <button className="outreach-btn" disabled={outreachLoading === inv.name} onClick={() => openOutreach(inv)}>
                            {outreachLoading === inv.name ? '…' : 'Outreach'}
                          </button>
                        </div>
                      </td>
                    </tr>
                  ))
                ) : (
                  <tr><td colSpan={17} className="empty-row">
                    No investors yet. Click &quot;Mine from High-Fit Companies&quot; to extract investors from your qualified deal universe.
                  </td></tr>
                )}
              </tbody>
            </table>
          </div>
        </section>
      </main>

      {/* ── Sources Overlay (same template as Master Universe) ── */}
      {showSources && (() => {
        const bySource = (label: string) => investors.filter(i => (i.source || '').toLowerCase().includes(label.toLowerCase()));
        const lastIngested = (list: Investor[]) => {
          const dates = list.map(i => i.ingested_at).filter(Boolean).sort();
          return dates.length ? new Date(dates[dates.length - 1]!).toLocaleDateString() : null;
        };
        const mined = bySource('Mined');
        const uploaded = bySource('PitchBook');
        return (
          <div className="sources-overlay" onClick={() => setShowSources(false)}>
            <div className="sources-panel" onClick={e => e.stopPropagation()}>
              <div className="sources-header">
                <div>
                  <h2>Investor Sources</h2>
                  <p className="sources-subtitle">{investors.length} investors ingested across {[mined, uploaded].filter(l => l.length > 0).length} active sources</p>
                </div>
                <button className="sources-close" onClick={() => setShowSources(false)}>&times;</button>
              </div>

              <h3 className="source-type-label">Portfolio Intelligence</h3>
              <div className="source-cards-grid">
                <div className="source-card">
                  <div className="source-card-head">
                    <span className="source-icon">⛏</span>
                    <div>
                      <span className="source-name">Mine from High-Fit Companies</span>
                      <p className="source-desc">Extracts investors from the cap tables (PitchBook active/former investors) of companies scoring 40+ or Qualified in your deal universe. Zero AI cost; portfolio overlap is your warm-intro path.</p>
                    </div>
                  </div>
                  <div className="source-stats">
                    <span><b>{mined.length}</b> investors</span>
                    {lastIngested(mined) && <span>Last mined: {lastIngested(mined)}</span>}
                  </div>
                  <button className="source-refresh" onClick={handleMine} disabled={mining}>
                    {mining ? 'Mining…' : 'Refresh ↻'}
                  </button>
                </div>
              </div>

              <h3 className="source-type-label">Databases</h3>
              <div className="source-cards-grid">
                <div className="source-card">
                  <div className="source-card-head">
                    <span className="source-icon">📄</span>
                    <div>
                      <span className="source-name">PitchBook LP Export</span>
                      <p className="source-desc">Upload a PitchBook Limited Partners export (Excel/CSV). Recommended filters: Family Office + Fund of Funds · HQ UK/Europe/KSA/UAE · Preferred type Buyout/PE Growth · commitment overlapping £250K–5M · industry Software/IT.</p>
                    </div>
                  </div>
                  <div className="source-stats">
                    <span><b>{uploaded.length}</b> investors</span>
                    {lastIngested(uploaded) && <span>Last upload: {lastIngested(uploaded)}</span>}
                  </div>
                  <label className={`source-upload ${uploading ? 'busy' : ''}`}>
                    {uploading ? 'Uploading…' : '+ Upload LP Export'}
                    <input type="file" accept=".xlsx,.xls,.csv" style={{ display: 'none' }} disabled={uploading}
                      onChange={e => { const f = e.target.files?.[0]; if (f) handleUpload(f); e.target.value = ''; }} />
                  </label>
                </div>
              </div>

              <h3 className="source-type-label">Web Scrapers</h3>
              <div className="source-cards-grid">
                {(() => {
                  const praxis = bySource('Praxis Rock');
                  const chreg = bySource('Companies House');
                  return (
                    <>
                      <div className="source-card">
                        <div className="source-card-head">
                          <span className="source-icon">🏛</span>
                          <div>
                            <span className="source-name">Praxis Rock Directories</span>
                            <p className="source-desc">Public family-office &amp; SWF directories (praxisrock.com): London (146 firms), largest global, multi-family offices, sovereign wealth funds. Name, type, description, website per firm.</p>
                          </div>
                        </div>
                        <div className="source-stats">
                          <span><b>{praxis.length}</b> investors</span>
                          {lastIngested(praxis) && <span>Last scraped: {lastIngested(praxis)}</span>}
                        </div>
                        <button className="source-refresh" onClick={() => handleScrape('Praxis Rock Directories')} disabled={scraping !== null}>
                          {scraping === 'Praxis Rock Directories' ? 'Scraping…' : 'Scrape ↻'}
                        </button>
                      </div>
                      <div className="source-card">
                        <div className="source-card-head">
                          <span className="source-icon">🇬🇧</span>
                          <div>
                            <span className="source-name">Companies House Registry</span>
                            <p className="source-desc">Official UK register: 6 investor name patterns (family office, family investments, private investment office…) plus SIC-code search (64303 venture/development capital, 66300 fund management) — catches family offices whose names don&apos;t say what they are. Free API, registration numbers included.</p>
                          </div>
                        </div>
                        <div className="source-stats">
                          <span><b>{chreg.length}</b> investors</span>
                          {lastIngested(chreg) && <span>Last scraped: {lastIngested(chreg)}</span>}
                        </div>
                        <button className="source-refresh" onClick={() => handleScrape('Companies House Registry')} disabled={scraping !== null}>
                          {scraping === 'Companies House Registry' ? 'Scraping…' : 'Scrape ↻'}
                        </button>
                      </div>
                    </>
                  );
                })()}
              </div>

              <h3 className="source-type-label">Coming Next</h3>
              <div className="source-cards-grid">
                <div className="source-card pending">
                  <div className="source-card-head">
                    <span className="source-icon">🔎</span>
                    <div>
                      <span className="source-name">AI Web Search</span>
                      <p className="source-desc">Gemini + Search segment sweeps — e.g. &quot;UK single-family offices backing lower-mid-market PE&quot;, &quot;GCC family offices with UK tech exposure&quot;. Not yet built.</p>
                    </div>
                  </div>
                </div>
              </div>
            </div>
          </div>
        );
      })()}

      {/* ── Bulk InvestorFill modal ── */}
      {bulkEligibility && (
        <div className="modal-overlay" onClick={closeBulkModal}>
          <div className="fill-modal" onClick={e => e.stopPropagation()}>
            <div className="fill-modal-header">
              <h3>Bulk InvestorFill</h3>
              <button className="modal-close" onClick={closeBulkModal}>&times;</button>
            </div>

            {!bulkRunning && !bulkProgress && (
              <>
                <div className="fill-scores" style={{ marginTop: '0.6rem' }}>
                  <div className="fill-score-row"><span>Total investors</span><b>{bulkEligibility.total_investors}</b></div>
                  <div className="fill-score-row"><span>Excluded — mandate outside UK/EU/ME</span><b>−{bulkEligibility.excluded_outside_mandate}</b></div>
                  <div className="fill-score-row"><span>Excluded — no relevant PE strategy</span><b>−{bulkEligibility.excluded_no_relevant_strategy}</b></div>
                  <div className="fill-score-row"><span>Skipped — already researched</span><b>−{bulkEligibility.skipped_already_researched}</b></div>
                  <div className="fill-score-row composite"><span>Eligible for InvestorFill</span><b>{bulkEligibility.eligible_count}</b></div>
                </div>
                <p className="fill-desc" style={{ marginTop: '0.7rem' }}>
                  1 AI call per investor → ~{bulkEligibility.estimate.total_gemini_calls} calls, token cost ≈ ${bulkEligibility.estimate.token_cost_usd_typical}. {bulkEligibility.estimate.grounding_note}
                </p>
                <div style={{ display: 'flex', gap: '0.6rem', justifyContent: 'flex-end' }}>
                  <button className="modal-ok" style={{ width: 'auto', background: '#fff', color: '#64748b', border: '1px solid #e2e8f0' }} onClick={closeBulkModal}>Cancel</button>
                  <button className="modal-ok" style={{ width: 'auto', background: '#16a34a' }} onClick={runBulkFill} disabled={bulkEligibility.eligible_count === 0}>
                    Start — {bulkEligibility.eligible_count} investors
                  </button>
                </div>
              </>
            )}

            {(bulkRunning || bulkProgress) && bulkProgress && (
              <div style={{ marginTop: '0.8rem' }}>
                <div className="bulk-bar-track"><div className="bulk-bar-fill" style={{ width: `${bulkProgress.total ? Math.round((bulkProgress.done / bulkProgress.total) * 100) : 0}%` }} /></div>
                <p className="fill-desc">
                  {bulkRunning
                    ? <>Researching <b>{bulkProgress.current}</b> ({bulkProgress.done + 1}/{bulkProgress.total}) · {bulkProgress.ok} done · {bulkProgress.failed} failed</>
                    : <>Finished: {bulkProgress.ok} succeeded · {bulkProgress.failed} failed of {bulkProgress.total}</>}
                </p>
                {bulkRunning
                  ? <button className="modal-ok" style={{ background: '#fff', color: '#64748b', border: '1px solid #e2e8f0' }} onClick={() => { bulkCancelRef.current = true; }}>Stop after current</button>
                  : <button className="modal-ok" onClick={closeBulkModal}>Close</button>}
              </div>
            )}
          </div>
        </div>
      )}

      {/* ── LP Outreach modal ── */}
      {outreachDraft && (
        <div className="modal-overlay" onClick={() => setOutreachDraft(null)}>
          <div className="fill-modal" style={{ width: 560 }} onClick={e => e.stopPropagation()}>
            <div className="fill-modal-header">
              <h3>LP Outreach — {outreachDraft.investor}</h3>
              <button className="modal-close" onClick={() => setOutreachDraft(null)}>&times;</button>
            </div>
            <label className="or-label">To</label>
            <input className="or-input" value={outreachDraft.to || ''} placeholder="No email on file — run InvestorFill first"
              onChange={e => setOutreachDraft({ ...outreachDraft, to: e.target.value })} />
            <label className="or-label">Subject</label>
            <input className="or-input" value={outreachDraft.subject || ''}
              onChange={e => setOutreachDraft({ ...outreachDraft, subject: e.target.value })} />
            <label className="or-label">Body</label>
            <textarea className="or-textarea" rows={11} value={outreachDraft.body || ''}
              onChange={e => setOutreachDraft({ ...outreachDraft, body: e.target.value })} />
            <div style={{ display: 'flex', gap: '0.6rem', justifyContent: 'flex-end', marginTop: '0.8rem' }}>
              <button className="modal-ok" style={{ width: 'auto', background: '#fff', color: '#64748b', border: '1px solid #e2e8f0' }} onClick={() => setOutreachDraft(null)}>Cancel</button>
              <button className="modal-ok" style={{ width: 'auto' }} onClick={sendOutreach} disabled={outreachSending || !outreachDraft.to}>
                {outreachSending ? 'Sending…' : 'Send & mark Contacted'}
              </button>
            </div>
          </div>
        </div>
      )}

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
        .sidebar-nav :global(.nav-item) { display: flex; align-items: center; gap: 0.6rem; padding: 0.6rem 0.75rem; border-radius: 8px; color: #475569; font-size: 0.85rem; font-weight: 600; text-decoration: none; }
        .sidebar-nav :global(.nav-item:hover) { background: #f1f5f9; }
        .sidebar-nav :global(.nav-item.active) { color: #2563eb; background: #eff6ff; }

        .main-content { flex: 1; margin-left: 260px; padding: 1.75rem 2rem; max-width: calc(100vw - 260px); min-width: 0; }
        .page-header { display: flex; justify-content: space-between; align-items: flex-start; margin-bottom: 1.25rem; }
        .page-header h1 { font-size: 1.4rem; color: #0f172a; }
        .subtitle { font-size: 0.8rem; color: #64748b; margin-top: 0.2rem; }
        .header-actions { display: flex; gap: 0.6rem; }
        .sources-btn { background: #fff; border: 1px solid #e2e8f0; color: #334155; border-radius: 8px; padding: 0.55rem 1rem; font-size: 0.82rem; font-weight: 700; cursor: pointer; display: flex; align-items: center; gap: 0.4rem; box-shadow: 0 1px 2px rgba(15, 23, 42, 0.04); transition: all 0.15s; }
        .sources-btn:hover { border-color: #2563eb; color: #2563eb; box-shadow: 0 2px 6px rgba(37, 99, 235, 0.12); }
        .sources-badge { background: #2563eb; color: #fff; border-radius: 10px; font-size: 0.65rem; padding: 0.05rem 0.45rem; font-weight: 800; }

        /* Sources overlay — same template as Master Universe */
        .sources-overlay { position: fixed; inset: 0; background: rgba(2,6,23,0.5); display: flex; justify-content: flex-end; z-index: 900; }
        .sources-panel { background: #f8fafc; width: 560px; max-width: 94vw; height: 100vh; overflow-y: auto; padding: 1.5rem; box-shadow: -12px 0 40px rgba(2,6,23,0.25); }
        .sources-header { display: flex; justify-content: space-between; align-items: flex-start; margin-bottom: 1.25rem; }
        .sources-header h2 { font-size: 1.15rem; color: #0f172a; }
        .sources-subtitle { font-size: 0.75rem; color: #64748b; margin-top: 0.2rem; }
        .sources-close { background: none; border: none; font-size: 1.6rem; color: #94a3b8; cursor: pointer; line-height: 1; }
        .source-type-label { font-size: 0.65rem; text-transform: uppercase; letter-spacing: 0.12em; color: #94a3b8; font-weight: 800; margin: 1.1rem 0 0.5rem 0; }
        .source-cards-grid { display: flex; flex-direction: column; gap: 0.6rem; }
        .source-card { background: #fff; border: 1px solid #e2e8f0; border-radius: 10px; padding: 0.9rem 1rem; }
        .source-card.pending { opacity: 0.65; }
        .source-card-head { display: flex; gap: 0.7rem; align-items: flex-start; }
        .source-icon { font-size: 1.2rem; }
        .source-name { font-weight: 800; font-size: 0.85rem; color: #0f172a; }
        .source-desc { font-size: 0.72rem; color: #64748b; margin-top: 0.2rem; line-height: 1.45; }
        .source-stats { display: flex; gap: 1rem; font-size: 0.72rem; color: #475569; margin: 0.6rem 0; }
        .source-refresh { background: #fff; border: 1px solid #2563eb; color: #2563eb; border-radius: 7px; padding: 0.4rem 0.9rem; font-size: 0.75rem; font-weight: 700; cursor: pointer; }
        .source-refresh:hover:not(:disabled) { background: #2563eb; color: #fff; }
        .source-refresh:disabled { opacity: 0.6; cursor: wait; }
        .source-upload { display: inline-block; background: #16a34a; color: #fff; border-radius: 7px; padding: 0.45rem 0.9rem; font-size: 0.75rem; font-weight: 700; cursor: pointer; }
        .source-upload:hover { background: #15803d; }
        .source-upload.busy { opacity: 0.6; cursor: wait; }

        .stats-row { display: flex; gap: 0.75rem; margin-bottom: 1.25rem; }
        .stat-card { background: #fff; border: 1px solid #e2e8f0; border-radius: 10px; padding: 0.8rem 1.2rem; display: flex; flex-direction: column; min-width: 110px; }
        .stat-value { font-size: 1.3rem; font-weight: 800; color: #0f172a; }
        .stat-label { font-size: 0.65rem; text-transform: uppercase; letter-spacing: 0.08em; color: #94a3b8; font-weight: 700; }

        .filter-row { display: flex; gap: 0.6rem; margin-bottom: 1rem; }
        .search-input { flex: 1; max-width: 340px; padding: 0.55rem 0.8rem; border: 1px solid #e2e8f0; border-radius: 8px; font-size: 0.8rem; }
        .filter-select { padding: 0.55rem 0.8rem; border: 1px solid #e2e8f0; border-radius: 8px; font-size: 0.8rem; background: #fff; color: #475569; }

        .table-section { background: #fff; border: 1px solid #e2e8f0; border-radius: 12px; overflow: hidden; }
        .table-scroll { overflow: auto; max-height: calc(100vh - 215px); }
        .inv-table td { white-space: nowrap; }
        .inv-table { width: 100%; border-collapse: separate; border-spacing: 0; font-size: 0.78rem; }
        .inv-table th {
          position: sticky; top: 0; z-index: 5;
          background: #f8fafc; text-align: left; padding: 0.65rem 0.8rem; font-size: 0.65rem;
          text-transform: uppercase; letter-spacing: 0.09em; color: #64748b; font-weight: 800;
          border-bottom: 1px solid #e2e8f0; white-space: nowrap; box-shadow: 0 1px 0 #e2e8f0;
        }
        .inv-table td { padding: 0.65rem 0.8rem; border-bottom: 1px solid #f1f5f9; color: #334155; vertical-align: middle; transition: background 0.1s; }
        .inv-table tbody tr:hover td { background: #f8fafc; }
        .inv-table tbody tr:last-child td { border-bottom: none; }
        .name-cell { font-weight: 700; color: #0f172a; white-space: nowrap; }
        .num-cell { white-space: nowrap; }
        .email-cell a { color: #2563eb; text-decoration: none; }
        .portfolio-cell { max-width: 200px; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
        .source-cell { color: #94a3b8; white-space: nowrap; }
        .empty-row { text-align: center; padding: 3rem !important; color: #94a3b8; }

        .fit-badge { font-weight: 800; padding: 0.15rem 0.5rem; border-radius: 999px; color: #fff; font-size: 0.72rem; }
        .fit-badge.high { background: #16a34a; }
        .fit-badge.mid { background: #d97706; }
        .fit-badge.low { background: #dc2626; }
        .type-badge { background: #eff6ff; color: #2563eb; font-weight: 700; font-size: 0.65rem; padding: 0.2rem 0.5rem; border-radius: 4px; text-transform: uppercase; letter-spacing: 0.04em; white-space: nowrap; }
        .strat-cell, .geo-cell { max-width: 170px; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
        .strat-ok { color: #166534; font-weight: 600; }
        .strat-none { color: #dc2626; font-weight: 600; }
        .ft-yes { background: #dcfce7; color: #166534; font-weight: 800; font-size: 0.65rem; padding: 0.15rem 0.5rem; border-radius: 4px; }
        .ft-no { background: #f1f5f9; color: #94a3b8; font-weight: 700; font-size: 0.65rem; padding: 0.15rem 0.5rem; border-radius: 4px; }
        .action-btns { display: flex; gap: 0.35rem; }
        .outreach-btn { background: #fff; border: 1px solid #2563eb; color: #2563eb; border-radius: 6px; padding: 0.35rem 0.6rem; font-size: 0.72rem; font-weight: 700; cursor: pointer; white-space: nowrap; }
        .outreach-btn:hover:not(:disabled) { background: #eff6ff; }
        .export-btn { background: #fff; border: 1px solid #e2e8f0; color: #334155; border-radius: 8px; padding: 0.55rem 1rem; font-size: 0.82rem; font-weight: 700; cursor: pointer; box-shadow: 0 1px 2px rgba(15, 23, 42, 0.04); transition: all 0.15s; }
        .export-btn:hover:not(:disabled) { border-color: #2563eb; color: #2563eb; box-shadow: 0 2px 6px rgba(37, 99, 235, 0.12); }
        .export-btn:hover:not(:disabled) { border-color: #16a34a; color: #16a34a; }
        .export-btn:disabled { opacity: 0.5; cursor: not-allowed; }
        .bulkfill-btn { background: #2563eb; color: #fff; border: none; border-radius: 8px; padding: 0.6rem 1rem; font-size: 0.8rem; font-weight: 700; cursor: pointer; }
        .bulkfill-btn:hover:not(:disabled) { background: #1d4ed8; }
        .bulkfill-btn:disabled { opacity: 0.6; cursor: wait; }
        .bulk-bar-track { height: 10px; background: #f1f5f9; border-radius: 6px; overflow: hidden; margin-bottom: 0.6rem; }
        .bulk-bar-fill { height: 100%; background: #16a34a; transition: width 0.4s ease; }
        .or-label { display: block; font-size: 0.65rem; text-transform: uppercase; letter-spacing: 0.08em; color: #94a3b8; font-weight: 700; margin: 0.6rem 0 0.2rem 0; }
        .or-input { width: 100%; padding: 0.5rem 0.7rem; border: 1px solid #e2e8f0; border-radius: 7px; font-size: 0.8rem; }
        .or-textarea { width: 100%; padding: 0.6rem 0.7rem; border: 1px solid #e2e8f0; border-radius: 7px; font-size: 0.8rem; line-height: 1.5; resize: vertical; font-family: inherit; }
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
