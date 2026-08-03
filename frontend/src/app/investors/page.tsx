"use client";

import { useEffect, useState, useRef } from "react";
import Link from "next/link";
import { Investor, INVESTOR_STAGES } from "../../types";
import { dealApi } from "../../services/api";
import InfoTip from "../../components/InfoTip";
import AuthGate from "../../components/AuthGate";
import SideNav from '../../components/SideNav';

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
  commitments: "Track record: total fund commitments (count · $M, all asset classes, PitchBook USD). Hover for active commitments, average ticket, VC breakdown and secondaries activity.",
  peCommitments: "PE-specific track record: commitments to PE funds (count · $M). The strongest single proof of appetite for our asset class.",
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
  const [regionFilter, setRegionFilter] = useState<string>("All");
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

  const [miningAll, setMiningAll] = useState(false);
  // ── AI Source Agent (LP flavour) ──
  const [srcUrl, setSrcUrl] = useState('');
  const [srcBusy, setSrcBusy] = useState(false);
  const [srcError, setSrcError] = useState('');
  const [srcPreview, setSrcPreview] = useState<any>(null);
  const [srcExcluded, setSrcExcluded] = useState<Set<number>>(new Set());
  const [aiSources, setAiSources] = useState<any[]>([]);
  const loadAiSources = () => dealApi.listSources('investors').then(r => setAiSources(r.sources || [])).catch(() => {});
  useEffect(() => { loadAiSources(); }, []);
  const analyzeSource = async () => {
    setSrcBusy(true); setSrcError(''); setSrcPreview(null); setSrcExcluded(new Set());
    try {
      const r = await dealApi.sourcePreview(srcUrl.trim(), 'investors');
      setSrcPreview(r);
      if (!r.companies?.length) setSrcError('No investors found on that page — is it an investor list?');
    } catch (e: any) { setSrcError(e?.message || 'Analysis failed'); }
    finally { setSrcBusy(false); }
  };
  const confirmSource = async () => {
    if (!srcPreview) return;
    setSrcBusy(true);
    try {
      const selected = srcPreview.companies.filter((_: any, i: number) => !srcExcluded.has(i));
      const r = await dealApi.sourceConfirm(srcPreview.url, srcPreview.title, selected, 'investors');
      alert(`Added ${r.added} new investors from "${r.label}" (${r.found} reviewed). The source auto-refreshes every Friday.`);
      setSrcPreview(null); setSrcUrl('');
      await loadData(); loadAiSources();
    } catch (e: any) { alert(e?.message || 'Ingest failed'); }
    finally { setSrcBusy(false); }
  };

  // ── Smart Upload (AI, LP flavour) ──
  const [suBusy, setSuBusy] = useState(false);
  const [suError, setSuError] = useState('');
  const [suPreview, setSuPreview] = useState<any>(null);
  const [suFilename, setSuFilename] = useState('');
  const analyzeFile = async (f: globalThis.File) => {
    setSuBusy(true); setSuError(''); setSuPreview(null); setSuFilename(f.name);
    try {
      const r = await dealApi.smartUploadPreview(f, 'investors');
      setSuPreview(r);
      if (!r.total) setSuError((r.warnings || []).join('; ') || 'No investors found in that file.');
    } catch (e: any) { setSuError(e?.message || 'Analysis failed'); }
    finally { setSuBusy(false); }
  };
  const confirmSmartUpload = async () => {
    if (!suPreview?.companies?.length) return;
    setSuBusy(true);
    try {
      const label = suPreview.dataset_guess || suFilename.replace(/\.[a-z]+$/i, '');
      const r = await dealApi.smartUploadConfirm(label, suPreview.companies, 'investors');
      alert(r.message || `Ingested ${r.found} investors, ${r.added} new.`);
      setSuPreview(null); setSuFilename('');
      await loadData();
    } catch (e: any) { alert(e?.message || 'Ingest failed'); }
    finally { setSuBusy(false); }
  };

  const [connFor, setConnFor] = useState<string | null>(null);
  const [connData, setConnData] = useState<any>(null);
  const openConnections = async (name: string) => {
    setConnFor(name);
    setConnData(null);
    try { setConnData(await dealApi.getInvestorConnections(name)); }
    catch { setConnData({ companies: [], co_investors: [] }); }
  };
  const handleMineAll = async () => {
    setMiningAll(true);
    try {
      const res = await dealApi.mineAllInvestors();
      alert(`Mined ${res.companies_scanned} companies: ${res.new_investors} new investors, `
        + `${res.overlaps_merged} overlaps merged, ${res.links_saved} connections mapped.`);
      await loadData();
    } catch (e: any) { alert(e?.message || "Mining failed — check backend logs."); }
    finally { setMiningAll(false); }
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
    const cols = ['name', 'investor_type', 'lp_fit_score', 'aum_m', 'net_assets_m', 'ticket_min_m', 'ticket_max_m', 'hq_city', 'hq_country', 'strategy_preferences', 'geo_preferences', 'open_to_first_time', 'num_commitments', 'total_commitments_m', 'num_active_commitments', 'total_active_commitments_m', 'num_pe_commitments', 'total_pe_commitments_m', 'num_vc_commitments', 'total_vc_commitments_m', 'sold_secondaries', 'bought_secondaries', 'contact_name', 'contact_title', 'contact_email', 'contact_phone', 'psc_summary', 'officers_summary', 'registration_number', 'source', 'source_companies', 'status'];
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

  const regionOf = (i: Investor) => i.global_region || i.hq_country || i.region || '';
  const regions = Array.from(new Set(investors.map(regionOf).filter(Boolean))).sort();

  const filtered = investors.filter(i => {
    const q = searchQuery.toLowerCase();
    const matchesSearch = i.name.toLowerCase().includes(q) || (i.description || '').toLowerCase().includes(q) || (i.source_companies || '').toLowerCase().includes(q);
    const matchesStage = stageFilter === "All" || i.status === stageFilter;
    const matchesType = typeFilter === "All" || i.investor_type === typeFilter;
    const matchesRegion = regionFilter === "All" || regionOf(i) === regionFilter;
    return matchesSearch && matchesStage && matchesType && matchesRegion;
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

  // $M formatter: $850M / $2.3B (PitchBook figures are USD millions)
  const fmtM = (v: number) => v >= 1000 ? `$${(v / 1000).toFixed(1)}B` : `$${v.toFixed(0)}M`;

  // Hover detail for the Commitments cell — everything we know, nothing invented
  const commitTip = (i: Investor) => {
    const lines: string[] = [];
    if (i.num_active_commitments != null) lines.push(`Active: ${i.num_active_commitments}${i.total_active_commitments_m != null ? ` · ${fmtM(i.total_active_commitments_m)}` : ''}`);
    else if (i.total_active_commitments_m != null) lines.push(`Active: ${fmtM(i.total_active_commitments_m)}`);
    if (i.num_commitments && i.total_commitments_m != null) lines.push(`Avg ticket: ${fmtM(i.total_commitments_m / i.num_commitments)}`);
    if (i.num_vc_commitments != null || i.total_vc_commitments_m != null) lines.push(`VC funds: ${i.num_vc_commitments ?? '?'}${i.total_vc_commitments_m != null ? ` · ${fmtM(i.total_vc_commitments_m)}` : ''}`);
    if (i.sold_secondaries || i.bought_secondaries) lines.push(`Secondaries — sold: ${i.sold_secondaries || '?'}, bought: ${i.bought_secondaries || '?'}`);
    return lines.join('\n');
  };

  return (
    <div className="layout-wrapper">
      {/* Sidebar */}
      <SideNav active="investor-universe" />

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
          <select value={regionFilter} onChange={e => setRegionFilter(e.target.value)} className="filter-select">
            <option value="All">All regions</option>
            {regions.map(r => <option key={r} value={r}>{r}</option>)}
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
                  <th><InfoTip label="PE" tip={INVESTOR_DEFS.peCommitments} /></th>
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
                  <tr><td colSpan={18} className="empty-row">Loading…</td></tr>
                ) : filtered.length > 0 ? (
                  filtered.map((inv, idx) => (
                    <tr key={idx}>
                      <td className="name-cell" title={inv.description || ''}>
                        <button className="inv-name-btn" onClick={() => openConnections(inv.name)}
                          title="Show portfolio connections">{inv.name}</button>
                      </td>
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
                      <td className="num-cell" title={commitTip(inv)}>
                        {inv.num_commitments != null || inv.total_commitments_m != null
                          ? <>{inv.num_commitments ?? '—'}{inv.total_commitments_m != null ? ` · ${fmtM(inv.total_commitments_m)}` : ''}</>
                          : '—'}
                      </td>
                      <td className="num-cell">
                        {inv.num_pe_commitments != null || inv.total_pe_commitments_m != null
                          ? <>{inv.num_pe_commitments ?? '—'}{inv.total_pe_commitments_m != null ? ` · ${fmtM(inv.total_pe_commitments_m)}` : ''}</>
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
                  <tr><td colSpan={18} className="empty-row">
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

              <h3 className="source-type-label">Smart Upload (AI)</h3>
              <div className="ai-source-box" style={{ marginBottom: '1rem' }}>
                <div className="ai-source-input-row">
                  <label className="ai-source-btn" style={{ cursor: suBusy ? 'wait' : 'pointer' }}>
                    {suBusy ? 'Analyzing…' : 'Choose file (CSV / Excel / PDF)'}
                    <input type="file" accept=".csv,.tsv,.xlsx,.xls,.pdf" style={{ display: 'none' }} disabled={suBusy}
                      onChange={e => { const f = e.target.files?.[0]; if (f) analyzeFile(f); e.target.value = ''; }} />
                  </label>
                  <span className="ai-source-desc" style={{ alignSelf: 'center' }}>
                    Any investor dataset — the AI maps its columns to the LP schema. PitchBook LP exports keep their dedicated parser via the normal upload.
                  </span>
                </div>
                {suError && <p className="ai-source-error">{suError}</p>}
                {suPreview && suPreview.total > 0 && (
                  <div className="ai-source-preview">
                    <p className="ai-source-summary">
                      <b>{suPreview.dataset_guess || suFilename}</b> — {suPreview.total} investors parsed
                      {suPreview.kind === 'pdf' && suPreview.shape ? ` (PDF, ${suPreview.shape})` : ''}
                      {(suPreview.warnings || []).length > 0 && <span style={{ color: '#b45309' }}> · {suPreview.warnings.join('; ')}</span>}
                    </p>
                    {(suPreview.mapping || []).length > 0 && (
                      <div className="ai-source-table" style={{ maxHeight: 150, marginBottom: '0.5rem' }}>
                        {suPreview.mapping.map((m: any, i: number) => (
                          <div className="ai-source-row" key={i} style={{ cursor: 'default' }}>
                            <b>{m.source}</b>
                            <span className="ai-source-desc">→ {m.target}{m.transform && m.transform !== 'none' ? ` (${m.transform.replace(/_/g, ' ')})` : ''}{m.note ? ` · ${m.note}` : ''}</span>
                          </div>
                        ))}
                      </div>
                    )}
                    <div className="ai-source-table" style={{ maxHeight: 180 }}>
                      {(suPreview.sample || []).map((c: any, i: number) => (
                        <div className="ai-source-row" key={i} style={{ cursor: 'default' }}>
                          <b>{c.name}</b>
                          {c.investor_type && <span className="type-badge">{c.investor_type}</span>}
                          {c.aum_m != null && <span className="ai-source-desc">AUM {c.aum_m}M</span>}
                          {c.hq_country && <span className="ai-source-desc">{c.hq_country}</span>}
                          {c.contact_name && <span className="ai-source-desc">{c.contact_name}</span>}
                        </div>
                      ))}
                    </div>
                    <button className="ai-source-btn confirm" disabled={suBusy} onClick={confirmSmartUpload}>
                      {suBusy ? 'Ingesting…' : `Ingest ${suPreview.total} investors (dedup-safe)`}
                    </button>
                  </div>
                )}
              </div>

              <h3 className="source-type-label">Add Source (AI Agent)</h3>
              <div className="ai-source-box" style={{ marginBottom: '1rem' }}>
                <div className="ai-source-input-row">
                  <input className="ai-source-input" placeholder="Paste any URL with an investor list — LP directories, family office lists, association members…"
                    value={srcUrl} onChange={e => setSrcUrl(e.target.value)}
                    onKeyDown={e => { if (e.key === 'Enter' && srcUrl.trim() && !srcBusy) analyzeSource(); }} />
                  <button className="ai-source-btn" disabled={!srcUrl.trim() || srcBusy} onClick={analyzeSource}>
                    {srcBusy ? 'Reading page…' : 'Analyze'}
                  </button>
                </div>
                {srcError && <p className="ai-source-error">{srcError}</p>}
                {srcPreview && (
                  <div className="ai-source-preview">
                    <p className="ai-source-summary">
                      <b>{srcPreview.title}</b> — found {srcPreview.companies.length} investors
                      ({srcPreview.pages_scanned} page{srcPreview.pages_scanned > 1 ? 's' : ''} read)
                      {(srcPreview.warnings || []).length > 0 && <span style={{ color: '#b45309' }}> · {srcPreview.warnings.join('; ')}</span>}
                    </p>
                    {srcPreview.companies.length > 0 && (
                      <>
                        <div className="ai-source-table">
                          {srcPreview.companies.map((c: any, i: number) => (
                            <label className="ai-source-row" key={i}>
                              <input type="checkbox" checked={!srcExcluded.has(i)}
                                onChange={() => setSrcExcluded(prev => { const n = new Set(prev); if (n.has(i)) { n.delete(i); } else { n.add(i); } return n; })} />
                              <b>{c.name}</b>
                              {c.investor_type && c.investor_type !== 'Unknown' && <span className="type-badge">{c.investor_type}</span>}
                              {c.hq_location && <span className="ai-source-desc">{c.hq_location}</span>}
                              {c.description && <span className="ai-source-desc" title={c.description}>{c.description.slice(0, 70)}</span>}
                            </label>
                          ))}
                        </div>
                        <button className="ai-source-btn confirm" disabled={srcBusy} onClick={confirmSource}>
                          {srcBusy ? 'Adding…' : `Add ${srcPreview.companies.length - srcExcluded.size} investors as "${srcPreview.title}"`}
                        </button>
                      </>
                    )}
                  </div>
                )}
                {aiSources.length > 0 && (
                  <div className="ai-source-saved">
                    <p className="ai-source-saved-title">Saved LP sources (auto-refresh every Friday)</p>
                    {aiSources.map((s: any, i: number) => (
                      <div className="ai-source-saved-row" key={i}>
                        <b>{s.label}</b>
                        <span className="ai-source-desc">{s.url.replace(/^https?:\/\/(www\.)?/, '').slice(0, 50)}</span>
                        <span className="ai-source-desc">{s.last_refreshed_at ? `last: ${new Date(s.last_refreshed_at).toLocaleDateString('en-GB')} · ${s.last_count} found` : 'never refreshed'}</span>
                        <button className="ai-source-btn small" disabled={srcBusy}
                          onClick={async () => { setSrcBusy(true); try { const r = await dealApi.sourceRefresh(s.url); alert(`${s.label}: ${r.found} found, ${r.added} new.`); await loadData(); loadAiSources(); } catch (e: any) { alert(e?.message || 'Refresh failed'); } finally { setSrcBusy(false); } }}>
                          Refresh ↻
                        </button>
                      </div>
                    ))}
                  </div>
                )}
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

                <div className="source-card">
                  <div className="source-card-head">
                    <span className="source-icon">🕸</span>
                    <div>
                      <span className="source-name">Mine All Sources + Connections</span>
                      <p className="source-desc">Sweeps every Qualified+ company: CH cap tables (with % stakes), PitchBook lists and Inven investor/owner columns. Every investor is typed (Fund / Angel / Agency / Bank / Corporate…), deduped, and each investor↔company relationship is stored in the connection layer for interconnection queries. Refreshes itself daily.</p>
                    </div>
                  </div>
                  <div className="source-stats">
                    <span><b>{bySource('mining v2').length}</b> investors</span>
                    {lastIngested(bySource('mining v2')) && <span>Last mined: {lastIngested(bySource('mining v2'))}</span>}
                  </div>
                  <button className="source-refresh" onClick={handleMineAll} disabled={miningAll}>
                    {miningAll ? 'Mining…' : 'Run now ↻'}
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

      {/* ── Investor connections overlay ── */}
      {connFor && (
        <div className="modal-overlay" onClick={() => setConnFor(null)}>
          <div className="fill-modal" style={{ width: 560 }} onClick={e => e.stopPropagation()}>
            <div className="fill-modal-header">
              <h3>{connFor} — connections</h3>
              <button className="modal-close" onClick={() => setConnFor(null)}>&times;</button>
            </div>
            {!connData ? (
              <p className="fill-desc">Loading connections…</p>
            ) : (
              <>
                <p className="fill-type">Portfolio companies in our universe ({connData.companies?.length || 0})</p>
                {(connData.companies || []).length === 0 && (
                  <p className="fill-desc">No connections mapped yet — the miner runs daily over Qualified+ companies (or press &quot;Mine All Sources&quot; in Sources).</p>
                )}
                <div className="fill-scores" style={{ maxHeight: 180, overflowY: 'auto' }}>
                  {(connData.companies || []).map((c: any, i: number) => (
                    <div className="fill-score-row" key={i}>
                      <span><b style={{ color: '#0f172a' }}>{c.company_name}</b></span>
                      <span>{c.pct != null ? `${c.pct}% · ` : ''}{String(c.link_type || '').replace(/_/g, ' ')}</span>
                    </div>
                  ))}
                </div>
                {(connData.co_investors || []).length > 0 && (
                  <>
                    <p className="fill-type" style={{ marginTop: '0.6rem' }}>Co-investors (shared portfolio companies)</p>
                    <div className="fill-scores" style={{ maxHeight: 150, overflowY: 'auto' }}>
                      {(connData.co_investors || []).slice(0, 20).map((c: any, i: number) => (
                        <div className="fill-score-row" key={i}>
                          <span><b style={{ color: '#0f172a' }}>{c.investor_name}</b> <span style={{ color: '#94a3b8' }}>{c.investor_type}</span></span>
                          <span>via {c.shared_company}</span>
                        </div>
                      ))}
                    </div>
                  </>
                )}
                <button className="modal-ok" style={{ marginTop: '0.9rem' }} onClick={() => setConnFor(null)}>Close</button>
              </>
            )}
          </div>
        </div>
      )}
    </div>
  );
}
