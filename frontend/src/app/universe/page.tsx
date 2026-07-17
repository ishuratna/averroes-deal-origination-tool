'use client';

import { useEffect, useState, useMemo, useRef } from "react";
import Link from 'next/link';
import { CompanyTarget, getRevenueBand, displayStatus } from "../../types";
import { dealApi } from "../../services/api";
import CompanyProfile from "../../components/CompanyProfile";
import InfoTip, { DEFS } from "../../components/InfoTip";
import AuthGate from "../../components/AuthGate";
import OutreachModal from "../../components/OutreachModal";
import SyncEmailsButton from "../../components/SyncEmailsButton";
import { outreachButtonState } from "../../lib/outreach";

// ── Source definitions ──────────────────────────────────────────────────────

interface SourceDef {
  name: string;
  type: 'marketplace' | 'conference' | 'ranking' | 'directory' | 'network' | 'upload';
  label: string;
  description: string;
  icon: string;          // emoji
  canRefresh: boolean;
  refreshType?: 'marketplace' | 'conference' | 'ranking' | 'directory' | 'network';
}

const ALL_SOURCES: SourceDef[] = [
  // Marketplaces — real integrations pending (require auth/JS scraping); no demo data
  { name: 'Acquire.com', type: 'marketplace', label: 'Acquire.com', description: 'SaaS acquisition marketplace. Integration pending — requires authenticated scraping (Playwright).', icon: '🛒', canRefresh: false },
  { name: 'Flippa', type: 'marketplace', label: 'Flippa', description: 'Online business marketplace. Integration pending — JS-rendered site / rate-limited API.', icon: '🛒', canRefresh: false },
  { name: 'Microns', type: 'marketplace', label: 'Microns', description: 'Micro-SaaS marketplace. Integration pending — client-rendered listings.', icon: '🛒', canRefresh: false },
  { name: 'SideProjectors', type: 'marketplace', label: 'SideProjectors', description: 'Side-project marketplace. Integration pending — client-rendered search.', icon: '🛒', canRefresh: false },
  // Conferences
  { name: 'SaaStock Europe', type: 'conference', label: 'SaaStock Europe', description: 'Sponsors + founder speakers from the official machine-readable archives, editions 2022–2025 (Dublin).', icon: '🎤', canRefresh: true, refreshType: 'conference' },
  { name: 'London Tech Week', type: 'conference', label: 'London Tech Week 2026', description: 'Full 2026 exhibitor list (~250 companies) + speaker companies, scraped from the official site.', icon: '🎤', canRefresh: true, refreshType: 'conference' },
  { name: 'SaaSiest', type: 'conference', label: 'SaaSiest', description: 'Nordic/European B2B SaaS conference. Generic partner-page scrape — may return few results.', icon: '🎤', canRefresh: true, refreshType: 'conference' },
  // Rankings
  { name: 'FT 1000', type: 'ranking', label: 'FT 1000', description: 'FT ranking of Europe\'s fastest-growing companies. Not scrapeable — paywalled interactive table.', icon: '📊', canRefresh: false },
  { name: 'Startups 100 UK', type: 'ranking', label: 'Startups 100 UK', description: 'The UK\'s top 100 new businesses, scraped live from startups.co.uk (latest year).', icon: '📊', canRefresh: true, refreshType: 'ranking' },
  { name: 'Deloitte Fast 50 UK', type: 'ranking', label: 'Deloitte Fast 50 UK', description: 'UK\'s 50 fastest-growing tech companies. Not scrapeable — JS-rendered page.', icon: '📊', canRefresh: false },
  // Directories
  { name: 'TheSaaSDirectory', type: 'directory', label: 'TheSaaSDirectory', description: 'Curated directory of SaaS products, scraped page-by-page.', icon: '📁', canRefresh: true, refreshType: 'directory' },
  // Founder networks / alumni
  { name: 'EF Alumni', type: 'network', label: 'EF Alumni', description: 'Entrepreneur First portfolio directory — London B2B companies, 2014+ vintages. Founder-led secondaries angle.', icon: '🎓', canRefresh: true, refreshType: 'network' },
  { name: 'Tech Nation', type: 'network', label: 'Tech Nation Future Fifty', description: 'Future Fifty cohort lists (2025, 2026) — UK scaleups at £5M+ revenue or 50% YoY growth.', icon: '🇬🇧', canRefresh: true, refreshType: 'network' },
];

// ── Saved view type ─────────────────────────────────────────────────────────

interface SavedView {
  id: string;
  name: string;
  filters: { vertical: string; region: string; status: string; searchQuery: string; };
}

// ── Source stats type ───────────────────────────────────────────────────────

interface SourceStats {
  companyCount: number;
  qualifiedCount: number;
  lastIngested: string | null;
  firstIngested: string | null;
  topSectors: string[];
  topRegions: string[];
}

export default function Universe() {
  return <AuthGate><UniverseInner /></AuthGate>;
}

function UniverseInner() {
  const [universe, setUniverse] = useState<CompanyTarget[]>([]);
  const [loading, setLoading] = useState(true);
  const [searchQuery, setSearchQuery] = useState("");
  const [ingesting, setIngesting] = useState<string | null>(null);
  const [smartFilling, setSmartFilling] = useState<string | null>(null);
  const [smartFillResult, setSmartFillResult] = useState<any | null>(null);
  const [outreachTarget, setOutreachTarget] = useState<any | null>(null);

  // Drawer
  const [profileIdx, setProfileIdx] = useState<number | null>(null);

  // Sources overlay
  const [showSources, setShowSources] = useState(false);
  const [expandedSource, setExpandedSource] = useState<string | null>(null);


  // Bulk SmartFill
  const [bulkEligibility, setBulkEligibility] = useState<any | null>(null);
  const [bulkLoadingEligibility, setBulkLoadingEligibility] = useState(false);
  const [bulkRunning, setBulkRunning] = useState(false);
  const [bulkProgress, setBulkProgress] = useState<{ done: number; total: number; current: string; ok: number; failed: number } | null>(null);
  const bulkCancelRef = useRef(false);

  // Filters
  const [filters, setFilters] = useState({ vertical: "All", region: "All", status: "All" });
  const verticals = ["All", "SaaS", "FinTech", "HealthTech", "AI", "Cybersecurity", "E-commerce", "Industrial", "Logistics", "Professional Services"];
  const regions = ["All", "UK", "Ireland", "UK/Ireland", "Europe", "North America"];
  const statuses = ["All", "Qualified", "Under Review", "Uploaded", "In Pipeline", "Not a Fit"];

  // Saved views
  const [savedViews, setSavedViews] = useState<SavedView[]>([]);
  const [showSaveView, setShowSaveView] = useState(false);
  const [newViewName, setNewViewName] = useState('');
  const [activeViewId, setActiveViewId] = useState<string | null>(null);

  useEffect(() => {
    loadData();
    try {
      const stored = localStorage.getItem('averroes_universe_views');
      if (stored) setSavedViews(JSON.parse(stored));
    } catch (e) {}
  }, []);

  async function loadData() {
    setLoading(true);
    try { const data = await dealApi.getUniverse(); setUniverse(data); }
    catch (error) { console.error("Failed to load universe", error); }
    finally { setLoading(false); }
  }

  // ── Manual qualification override ─────────────────────────────────────────

  const [qualifyingName, setQualifyingName] = useState<string | null>(null);

  async function qualifyAnyway(company: CompanyTarget) {
    if (qualifyingName) return;
    const score = company.averroes_fit_score != null ? Math.round(company.averroes_fit_score * 100) : null;
    const scoreLine = score != null
      ? `Fit score: ${score}${score < 70 ? ' (below the qualification bar)' : ''}.`
      : 'This company has not been scored yet.';
    if (!window.confirm(`${scoreLine}\n\nQualify "${company.name}" anyway? It will move into the pipeline as Qualified.`)) return;
    setQualifyingName(company.name);
    try {
      await dealApi.updateCompanyStatus(company.name, 'Qualified', 'Ishu Ratna (manual override)');
      await loadData();
    } catch (e: any) {
      alert(`Failed to qualify: ${e.message}`);
    } finally {
      setQualifyingName(null);
    }
  }

  // ── Compute source stats from universe data ──────────────────────────────

  const sourceStats = useMemo(() => {
    const stats: Record<string, SourceStats> = {};

    // Initialize known sources
    ALL_SOURCES.forEach(s => {
      stats[s.name] = { companyCount: 0, qualifiedCount: 0, lastIngested: null, firstIngested: null, topSectors: [], topRegions: [] };
    });

    // Find upload sources dynamically
    const uploadSourceNames = new Set<string>();
    universe.forEach(c => {
      if (c.source?.startsWith('Upload:')) uploadSourceNames.add(c.source);
    });

    uploadSourceNames.forEach(name => {
      stats[name] = { companyCount: 0, qualifiedCount: 0, lastIngested: null, firstIngested: null, topSectors: [], topRegions: [] };
    });

    // Tally
    const sectorCounts: Record<string, Record<string, number>> = {};
    const regionCounts: Record<string, Record<string, number>> = {};

    universe.forEach(c => {
      const src = c.source;
      if (!src || !stats[src]) return;
      const s = stats[src];
      s.companyCount++;
      if (c.status === 'Qualified') s.qualifiedCount++;
      if (c.ingested_at) {
        if (!s.lastIngested || c.ingested_at > s.lastIngested) s.lastIngested = c.ingested_at;
        if (!s.firstIngested || c.ingested_at < s.firstIngested) s.firstIngested = c.ingested_at;
      }
      // Sector counts
      if (c.sector) {
        if (!sectorCounts[src]) sectorCounts[src] = {};
        sectorCounts[src][c.sector] = (sectorCounts[src][c.sector] || 0) + 1;
      }
      if (c.region) {
        if (!regionCounts[src]) regionCounts[src] = {};
        regionCounts[src][c.region] = (regionCounts[src][c.region] || 0) + 1;
      }
    });

    // Top sectors/regions
    Object.entries(sectorCounts).forEach(([src, counts]) => {
      stats[src].topSectors = Object.entries(counts).sort((a, b) => b[1] - a[1]).slice(0, 3).map(([k]) => k);
    });
    Object.entries(regionCounts).forEach(([src, counts]) => {
      stats[src].topRegions = Object.entries(counts).sort((a, b) => b[1] - a[1]).slice(0, 3).map(([k]) => k);
    });

    return { stats, uploadSources: Array.from(uploadSourceNames) };
  }, [universe]);

  // ── Handlers ─────────────────────────────────────────────────────────────

  const handleIngest = async (type: 'marketplace' | 'conference' | 'ranking' | 'network', name: string) => {
    setIngesting(name);
    try {
      if (type === 'marketplace') await dealApi.ingestMarketplace(name);
      else if (type === 'conference') await dealApi.ingestConference(name);
      else if (type === 'ranking') await dealApi.ingestRanking(name);
      else if (type === 'network') {
        const res = await dealApi.ingestNetwork(name);
        alert(`Found ${res.count} companies from ${name}.`);
      }
      await loadData();
    } catch (error) { alert(`Ingestion failed for ${name}`); }
    finally { setIngesting(null); }
  };

  const handleDirectoryScrape = async (sourceName: string) => {
    setIngesting(sourceName);
    try {
      const res = await dealApi.ingestDirectory(sourceName);
      alert(`Found ${res.count} companies from ${sourceName}. ${res.total_in_universe || ''} total in universe.`);
      await loadData();
    } catch (error) { alert(`Scraping failed for ${sourceName}`); }
    finally { setIngesting(null); }
  };

  // ── Bulk SmartFill ────────────────────────────────────────────────────────

  const openBulkSmartFill = async () => {
    setBulkLoadingEligibility(true);
    try {
      const data = await dealApi.getSmartFillEligible();
      setBulkEligibility(data);
    } catch (e) {
      alert('Failed to load eligibility — is the backend deployed?');
    } finally {
      setBulkLoadingEligibility(false);
    }
  };

  const runBulkSmartFill = async () => {
    if (!bulkEligibility?.eligible_names?.length) return;
    const names: string[] = bulkEligibility.eligible_names;
    bulkCancelRef.current = false;
    setBulkRunning(true);
    let ok = 0, failed = 0;
    for (let i = 0; i < names.length; i++) {
      if (bulkCancelRef.current) break;
      setBulkProgress({ done: i, total: names.length, current: names[i], ok, failed });
      try {
        await dealApi.smartFill(names[i], true);  // bulk mode: Too Large skips web-search scoring
        ok++;
      } catch (e: any) {
        if ((e?.message || '').includes('Daily SmartFill limit')) {
          alert(`Daily cap reached after ${ok} companies — the rest are preserved for tomorrow's run.`);
          break;
        }
        failed++;
        console.error(`Bulk SmartFill failed for ${names[i]}`, e);
      }
      // Rate limiting between companies (each SmartFill already takes 20-60s server-side)
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

  const handleRefreshSource = async (source: SourceDef) => {
    if (source.refreshType === 'directory') {
      await handleDirectoryScrape(source.name);
    } else if (source.refreshType) {
      await handleIngest(source.refreshType, source.name);
    }
  };

  const filteredUniverse = universe.filter(c => {
    const q = searchQuery.toLowerCase();
    const matchesSearch = c.name.toLowerCase().includes(q) || (c.sector && c.sector.toLowerCase().includes(q)) || (c.description && c.description.toLowerCase().includes(q));
    const matchesVertical = filters.vertical === "All" || (c.sector && c.sector.toLowerCase().includes(filters.vertical.toLowerCase()));
    const matchesRegion = filters.region === "All" || (c.region && c.region.toLowerCase().includes(filters.region.toLowerCase()));
    const matchesUKIE = filters.region === "UK/Ireland" && (c.region?.toLowerCase().includes("uk") || c.region?.toLowerCase().includes("ireland") || c.region?.toLowerCase().includes("united kingdom"));
    const matchesStatus = filters.status === "All" || c.status === filters.status;
    return matchesSearch && (matchesRegion || matchesUKIE) && matchesVertical && matchesStatus;
  });

  const formatDate = (dateStr?: string | null) => {
    if (!dateStr) return '—';
    return new Date(dateStr).toLocaleDateString('en-GB', { day: '2-digit', month: 'short', year: 'numeric' });
  };

  const formatDateTime = (dateStr?: string | null) => {
    if (!dateStr) return 'Never';
    return new Date(dateStr).toLocaleDateString('en-GB', { day: 'numeric', month: 'short', year: 'numeric', hour: '2-digit', minute: '2-digit' });
  };

  const openOutreach = (company: any) => setOutreachTarget(company);

  // Saved views
  const activeFilterCount = [filters.vertical !== 'All', filters.region !== 'All', filters.status !== 'All', searchQuery !== ''].filter(Boolean).length;

  const handleSaveView = () => {
    if (!newViewName.trim()) return;
    const view: SavedView = {
      id: Date.now().toString(),
      name: newViewName.trim(),
      filters: { ...filters, searchQuery },
    };
    const updated = [...savedViews, view];
    setSavedViews(updated);
    localStorage.setItem('averroes_universe_views', JSON.stringify(updated));
    setNewViewName('');
    setShowSaveView(false);
    setActiveViewId(view.id);
  };

  const handleLoadView = (view: SavedView) => {
    setFilters({ vertical: view.filters.vertical, region: view.filters.region, status: view.filters.status });
    setSearchQuery(view.filters.searchQuery);
    setActiveViewId(view.id);
  };

  const handleDeleteView = (id: string) => {
    const updated = savedViews.filter(v => v.id !== id);
    setSavedViews(updated);
    localStorage.setItem('averroes_universe_views', JSON.stringify(updated));
    if (activeViewId === id) setActiveViewId(null);
  };

  // Source type badge colors
  const typeColor = (type: string) => {
    const colors: Record<string, { bg: string; fg: string }> = {
      marketplace: { bg: '#fef3c7', fg: '#92400e' },
      conference: { bg: '#ede9fe', fg: '#5b21b6' },
      ranking: { bg: '#dbeafe', fg: '#1e40af' },
      directory: { bg: '#d1fae5', fg: '#065f46' },
      upload: { bg: '#f1f5f9', fg: '#475569' },
    };
    return colors[type] || colors.upload;
  };

  // Total source-level counts
  const totalSourceCompanies = Object.values(sourceStats.stats).reduce((sum, s) => sum + s.companyCount, 0);
  const activeSources = ALL_SOURCES.filter(s => (sourceStats.stats[s.name]?.companyCount || 0) > 0).length + sourceStats.uploadSources.length;

  return (
    <div className="layout-wrapper">
      {/* SmartFill Result Modal */}
      {smartFillResult && (
        <div className="modal-overlay" onClick={() => setSmartFillResult(null)}>
          <div className="modal-content" onClick={(e) => e.stopPropagation()}>
            <div className="modal-header">
              <h3>SmartFill Results</h3>
              <button className="modal-close" onClick={() => setSmartFillResult(null)}>&times;</button>
            </div>
            <div className="modal-body">
              <div className="result-company-name">{smartFillResult.company}</div>
              <div className="result-grid">
                <div className="result-row">
                  <span className="result-label">Status</span>
                  <span className={`result-value ${smartFillResult.new_status === 'Qualified' ? 'found' : 'low'}`}>{smartFillResult.new_status}</span>
                </div>
                <div className="result-row">
                  <span className="result-label">UK/Ireland</span>
                  <span className={`result-value ${smartFillResult.is_uk_ireland ? 'found' : 'not-found'}`}>{smartFillResult.is_uk_ireland ? 'Yes' : 'No'}</span>
                </div>
                <div className="result-row">
                  <span className="result-label">Tech Company</span>
                  <span className={`result-value ${smartFillResult.is_tech ? 'found' : 'not-found'}`}>{smartFillResult.is_tech ? 'Yes' : 'No'}</span>
                </div>
                {smartFillResult.size_bucket && (
                  <div className="result-row">
                    <span className="result-label">Company Size</span>
                    <span className={`result-value ${smartFillResult.size_qualified !== false ? 'found' : 'not-found'}`}>
                      {smartFillResult.size_bucket}{smartFillResult.size_confidence ? ` (${smartFillResult.size_confidence} confidence)` : ''}
                    </span>
                  </div>
                )}
                {smartFillResult.size_reason && (
                  <div className="result-row">
                    <span className="result-label">Size Basis</span>
                    <span className="result-value" style={{fontSize: '0.8rem', whiteSpace: 'normal'}}>{smartFillResult.size_reason}</span>
                  </div>
                )}
                {/* Companies House Financials */}
                {smartFillResult.ch_company_number && (
                  <>
                    <div className="result-ch-header">
                      <span className="result-ch-badge">Companies House</span>
                      {smartFillResult.ch_match_confidence && (
                        <span className={`result-ch-conf result-ch-conf-${smartFillResult.ch_match_confidence}`}>{smartFillResult.ch_match_confidence} match</span>
                      )}
                    </div>
                    <div className="result-row">
                      <span className="result-label">Official Name</span>
                      <span className="result-value found">{smartFillResult.ch_official_name}</span>
                    </div>
                    <div className="result-row">
                      <span className="result-label">Company #</span>
                      <span className="result-value found">{smartFillResult.ch_company_number}</span>
                    </div>
                    <div className="result-row">
                      <span className="result-label">Filing Type</span>
                      <span className="result-value">{smartFillResult.filing_type || '—'}</span>
                    </div>
                    {smartFillResult.revenue_y1 != null && (
                      <div className="result-row">
                        <span className="result-label">Revenue (Latest)</span>
                        <span className="result-value found">£{(smartFillResult.revenue_y1 / 1e6).toFixed(2)}M {smartFillResult.revenue_y1_date ? `(${smartFillResult.revenue_y1_date})` : ''}</span>
                      </div>
                    )}
                    {smartFillResult.profit_y1 != null && (
                      <div className="result-row">
                        <span className="result-label">Profit (Latest)</span>
                        <span className="result-value found">£{(smartFillResult.profit_y1 / 1e6).toFixed(2)}M</span>
                      </div>
                    )}
                    {smartFillResult.total_assets_y1 != null && (
                      <div className="result-row">
                        <span className="result-label">Total Assets</span>
                        <span className="result-value found">£{(smartFillResult.total_assets_y1 / 1e6).toFixed(2)}M</span>
                      </div>
                    )}
                    {smartFillResult.ch_notes && (
                      <div className="result-row">
                        <span className="result-label">CH Notes</span>
                        <span className="result-value" style={{fontSize: '0.8rem', whiteSpace: 'normal'}}>{smartFillResult.ch_notes}</span>
                      </div>
                    )}
                  </>
                )}
                {smartFillResult.reason && (
                  <div className="result-row"><span className="result-label">Reason</span><span className="result-value" style={{fontSize: '0.8rem', whiteSpace: 'normal'}}>{smartFillResult.reason}</span></div>
                )}
                {smartFillResult.description && (
                  <div className="result-description">
                    <span className="result-label">Company Summary</span>
                    <p className="description-text">{smartFillResult.description}</p>
                  </div>
                )}
                <div className="result-row">
                  <span className="result-label">Website</span>
                  <span className={`result-value ${smartFillResult.website ? 'found' : 'not-found'}`}>
                    {smartFillResult.website ? (<a href={smartFillResult.website} target="_blank" rel="noreferrer">{smartFillResult.website}</a>) : 'Not Found'}
                  </span>
                </div>
                <div className="result-row">
                  <span className="result-label">Founder / CEO</span>
                  <span className={`result-value ${smartFillResult.contact_name ? 'found' : 'not-found'}`}>{smartFillResult.contact_name || 'Not Found'}</span>
                </div>
                <div className="result-row">
                  <span className="result-label">Contact Email</span>
                  <span className={`result-value ${smartFillResult.contact_email ? 'found' : 'not-found'}`}>{smartFillResult.contact_email || 'Not Found'}</span>
                </div>
                <div className="result-row">
                  <span className="result-label">LinkedIn</span>
                  <span className={`result-value ${smartFillResult.linkedin_url ? 'found' : 'not-found'}`}>
                    {smartFillResult.linkedin_url ? (<a href={smartFillResult.linkedin_url} target="_blank" rel="noreferrer">{smartFillResult.linkedin_url}</a>) : 'Not Found'}
                  </span>
                </div>
                {/* Averroes Fit Score */}
                {smartFillResult.averroes_fit_score != null && (
                  <>
                    <div className="result-ch-header">
                      <span className="result-ch-badge" style={{ background: smartFillResult.averroes_fit_score >= 0.7 ? '#16a34a' : smartFillResult.averroes_fit_score >= 0.4 ? '#d97706' : '#dc2626' }}>
                        Fit Score: {Math.round(smartFillResult.averroes_fit_score * 100)}
                      </span>
                      <span style={{ fontSize: '0.7rem', color: '#64748b' }}>{smartFillResult.metrics_available}/5 metrics</span>
                    </div>
                    {smartFillResult.score_employee_growth != null && (
                      <div className="result-row">
                        <span className="result-label">Employee Growth</span>
                        <span className="result-value found">{Math.round(smartFillResult.score_employee_growth * 100)}/100</span>
                      </div>
                    )}
                    {smartFillResult.score_revenue_growth != null && (
                      <div className="result-row">
                        <span className="result-label">Revenue Growth</span>
                        <span className="result-value found">{Math.round(smartFillResult.score_revenue_growth * 100)}/100</span>
                      </div>
                    )}
                    {smartFillResult.score_revenue_size != null && (
                      <div className="result-row">
                        <span className="result-label">Revenue Size</span>
                        <span className="result-value found">{Math.round(smartFillResult.score_revenue_size * 100)}/100</span>
                      </div>
                    )}
                    {smartFillResult.score_business_fit != null && (
                      <div className="result-row">
                        <span className="result-label">Business Fit</span>
                        <span className="result-value found">{Math.round(smartFillResult.score_business_fit * 100)}/100</span>
                      </div>
                    )}
                    {smartFillResult.score_market_sentiment != null && (
                      <div className="result-row">
                        <span className="result-label">Market Sentiment</span>
                        <span className="result-value found">{Math.round(smartFillResult.score_market_sentiment * 100)}/100</span>
                      </div>
                    )}
                  </>
                )}
              </div>
            </div>
            <div className="modal-footer">
              <button className="modal-ok-btn" onClick={() => setSmartFillResult(null)}>OK</button>
            </div>
          </div>
        </div>
      )}

      {/* Outreach Modal (shared component — same one used on the Pipeline) */}
      <OutreachModal company={outreachTarget} onClose={() => setOutreachTarget(null)} onSent={loadData} />

      {/* ── Bulk SmartFill Modal ────────────────────────────────────── */}
      {bulkEligibility && (
        <div className="modal-overlay" onClick={closeBulkModal}>
          <div className="bulk-modal" onClick={e => e.stopPropagation()}>
            <div className="bulk-modal-header">
              <h3>Bulk SmartFill</h3>
              <button className="modal-close" onClick={closeBulkModal}>&times;</button>
            </div>

            {!bulkRunning && !bulkProgress && (
              <>
                <div className="bulk-funnel">
                  <div className="bulk-funnel-row"><span>Total universe</span><b>{bulkEligibility.total_universe}</b></div>
                  <div className="bulk-funnel-row excluded"><span>Skipped — already SmartFilled</span><b>−{bulkEligibility.skipped_already_smartfilled}</b></div>
                  <div className="bulk-funnel-row excluded"><span>Excluded — not UK/Ireland</span><b>−{bulkEligibility.excluded_non_uk_ie}</b></div>
                  <div className="bulk-funnel-row excluded"><span>Excluded — not tech/software</span><b>−{bulkEligibility.excluded_non_tech}</b></div>
                  <div className="bulk-funnel-row excluded"><span>Excluded — over £50M (size filter)</span><b>−{bulkEligibility.excluded_too_large}</b></div>
                  <div className="bulk-funnel-row eligible"><span>Eligible (new + passes all 3 filters)</span><b>{bulkEligibility.eligible_count}</b></div>
                  <div className="bulk-funnel-row" style={{ background: '#eff6ff', color: '#1d4ed8', fontWeight: 700 }}>
                    <span>Batch: {bulkEligibility.batch_limit || 100}/run · daily cap: {bulkEligibility.daily_cap} · used today: {bulkEligibility.used_today}</span>
                    <b>runs now: {bulkEligibility.runnable_now}</b>
                  </div>
                </div>

                <div className="bulk-estimate">
                  <h4>Gemini credit estimate</h4>
                  <p>{bulkEligibility.estimate.gemini_calls_per_company.min}–{bulkEligibility.estimate.gemini_calls_per_company.max} API calls per company (typically {bulkEligibility.estimate.gemini_calls_per_company.typical}) → <b>~{bulkEligibility.estimate.total_gemini_calls.typical} total calls</b> ({bulkEligibility.estimate.total_gemini_calls.min}–{bulkEligibility.estimate.total_gemini_calls.max} range).</p>
                  <p>Token cost ≈ <b>${bulkEligibility.estimate.token_cost_usd_typical}</b> · Grounded search calls: ~{bulkEligibility.estimate.total_grounded_calls_typical}.</p>
                  <p className="bulk-note">{bulkEligibility.estimate.grounding_note}</p>
                  <p className="bulk-note">Filters use keyword matching on stored data (no AI). Companies with missing region/sector data are excluded — enrich key targets individually if needed.</p>
                </div>

                <div className="bulk-actions">
                  <button className="bulk-cancel" onClick={closeBulkModal}>Cancel</button>
                  <button className="bulk-start" onClick={runBulkSmartFill} disabled={bulkEligibility.runnable_now === 0}>
                    Start — {bulkEligibility.runnable_now} companies
                  </button>
                </div>
              </>
            )}

            {(bulkRunning || bulkProgress) && (
              <div className="bulk-run">
                {bulkProgress && (
                  <>
                    <div className="bulk-bar-track">
                      <div className="bulk-bar-fill" style={{ width: `${bulkProgress.total ? Math.round((bulkProgress.done / bulkProgress.total) * 100) : 0}%` }} />
                    </div>
                    <p className="bulk-run-status">
                      {bulkRunning
                        ? <>Processing <b>{bulkProgress.current}</b> ({bulkProgress.done + 1}/{bulkProgress.total}) · {bulkProgress.ok} done · {bulkProgress.failed} failed</>
                        : <>Finished: {bulkProgress.ok} succeeded · {bulkProgress.failed} failed of {bulkProgress.total}</>}
                    </p>
                  </>
                )}
                {bulkRunning ? (
                  <button className="bulk-cancel" onClick={() => { bulkCancelRef.current = true; }}>Stop after current</button>
                ) : (
                  <button className="bulk-start" onClick={closeBulkModal}>Close</button>
                )}
              </div>
            )}
          </div>
        </div>
      )}

      {/* ── Sources Overlay ─────────────────────────────────────────── */}
      {showSources && (
        <div className="sources-overlay">
          <div className="sources-panel">
            <div className="sources-header">
              <div>
                <h2 className="sources-title">Data Sources</h2>
                <p className="sources-subtitle">{activeSources} active sources &middot; {totalSourceCompanies} companies ingested</p>
              </div>
              <button className="sources-close" onClick={() => { setShowSources(false); setExpandedSource(null); }}>
                <svg width="24" height="24" viewBox="0 0 24 24" fill="none"><path d="M18 6L6 18M6 6l12 12" stroke="currentColor" strokeWidth="2" strokeLinecap="round"/></svg>
              </button>
            </div>

            {/* Source type sections */}
            {(['marketplace', 'conference', 'ranking', 'directory', 'network'] as const).map(type => {
              const sources = ALL_SOURCES.filter(s => s.type === type);
              const typeLabels: Record<string, string> = {
                marketplace: 'Marketplaces',
                conference: 'Conferences & Events',
                ranking: 'Rankings & Lists',
                directory: 'Directories',
                network: 'Founder Networks & Alumni',
              };
              return (
                <div key={type} className="source-type-section">
                  <h3 className="source-type-label">{typeLabels[type]}</h3>
                  <div className="source-cards-grid">
                    {sources.map(source => {
                      const stats = sourceStats.stats[source.name];
                      const isExpanded = expandedSource === source.name;
                      const isRefreshing = ingesting === source.name;
                      return (
                        <div key={source.name} className={`source-card ${isExpanded ? 'expanded' : ''}`}>
                          <button className="source-card-header" onClick={() => setExpandedSource(isExpanded ? null : source.name)}>
                            <div className="source-card-left">
                              <span className="source-icon">{source.icon}</span>
                              <div>
                                <span className="source-name">{source.label}</span>
                                <span className="source-type-badge" style={{ background: typeColor(type).bg, color: typeColor(type).fg }}>{type}</span>
                              </div>
                            </div>
                            <div className="source-card-right">
                              <span className="source-count">{stats?.companyCount || 0}</span>
                              <svg className={`chevron ${isExpanded ? 'open' : ''}`} width="16" height="16" viewBox="0 0 16 16" fill="none">
                                <path d="M4 6l4 4 4-4" stroke="#94a3b8" strokeWidth="1.5" strokeLinecap="round" strokeLinejoin="round"/>
                              </svg>
                            </div>
                          </button>

                          {isExpanded && (
                            <div className="source-expanded">
                              <p className="source-desc">{source.description}</p>
                              <div className="source-stats-grid">
                                <div className="source-stat">
                                  <span className="source-stat-label">Total Companies</span>
                                  <span className="source-stat-value">{stats?.companyCount || 0}</span>
                                </div>
                                <div className="source-stat">
                                  <span className="source-stat-label">Qualified</span>
                                  <span className="source-stat-value source-stat-qualified">{stats?.qualifiedCount || 0}</span>
                                </div>
                                <div className="source-stat">
                                  <span className="source-stat-label">Last Scraped</span>
                                  <span className="source-stat-value">{formatDateTime(stats?.lastIngested)}</span>
                                </div>
                                <div className="source-stat">
                                  <span className="source-stat-label">First Scraped</span>
                                  <span className="source-stat-value">{formatDateTime(stats?.firstIngested)}</span>
                                </div>
                                {stats?.topSectors && stats.topSectors.length > 0 && (
                                  <div className="source-stat full-width">
                                    <span className="source-stat-label">Top Sectors</span>
                                    <span className="source-stat-value">{stats.topSectors.join(', ')}</span>
                                  </div>
                                )}
                                {stats?.topRegions && stats.topRegions.length > 0 && (
                                  <div className="source-stat full-width">
                                    <span className="source-stat-label">Top Regions</span>
                                    <span className="source-stat-value">{stats.topRegions.join(', ')}</span>
                                  </div>
                                )}
                              </div>
                              {source.canRefresh && (
                                <button
                                  className={`source-refresh-btn ${isRefreshing ? 'refreshing' : ''}`}
                                  onClick={() => handleRefreshSource(source)}
                                  disabled={isRefreshing || !!ingesting}
                                >
                                  {isRefreshing ? 'Scraping...' : 'Refresh Source'}
                                </button>
                              )}
                            </div>
                          )}
                        </div>
                      );
                    })}
                  </div>
                </div>
              );
            })}

            {/* Upload sources */}
            {sourceStats.uploadSources.length > 0 && (
              <div className="source-type-section">
                <h3 className="source-type-label">Uploaded Files</h3>
                <div className="source-cards-grid">
                  {sourceStats.uploadSources.map(name => {
                    const stats = sourceStats.stats[name];
                    const isExpanded = expandedSource === name;
                    const displayName = name.replace('Upload: ', '');
                    return (
                      <div key={name} className={`source-card ${isExpanded ? 'expanded' : ''}`}>
                        <button className="source-card-header" onClick={() => setExpandedSource(isExpanded ? null : name)}>
                          <div className="source-card-left">
                            <span className="source-icon">📄</span>
                            <div>
                              <span className="source-name">{displayName}</span>
                              <span className="source-type-badge" style={{ background: typeColor('upload').bg, color: typeColor('upload').fg }}>upload</span>
                            </div>
                          </div>
                          <div className="source-card-right">
                            <span className="source-count">{stats?.companyCount || 0}</span>
                            <svg className={`chevron ${isExpanded ? 'open' : ''}`} width="16" height="16" viewBox="0 0 16 16" fill="none">
                              <path d="M4 6l4 4 4-4" stroke="#94a3b8" strokeWidth="1.5" strokeLinecap="round" strokeLinejoin="round"/>
                            </svg>
                          </div>
                        </button>

                        {isExpanded && (
                          <div className="source-expanded">
                            <div className="source-stats-grid">
                              <div className="source-stat">
                                <span className="source-stat-label">Total Companies</span>
                                <span className="source-stat-value">{stats?.companyCount || 0}</span>
                              </div>
                              <div className="source-stat">
                                <span className="source-stat-label">Qualified</span>
                                <span className="source-stat-value source-stat-qualified">{stats?.qualifiedCount || 0}</span>
                              </div>
                              <div className="source-stat">
                                <span className="source-stat-label">Uploaded On</span>
                                <span className="source-stat-value">{formatDateTime(stats?.lastIngested)}</span>
                              </div>
                              {stats?.topSectors && stats.topSectors.length > 0 && (
                                <div className="source-stat full-width">
                                  <span className="source-stat-label">Top Sectors</span>
                                  <span className="source-stat-value">{stats.topSectors.join(', ')}</span>
                                </div>
                              )}
                            </div>
                          </div>
                        )}
                      </div>
                    );
                  })}
                </div>
              </div>
            )}

            {/* Upload new file */}
            <div className="source-upload-section">
              <label className={`source-upload-btn ${ingesting === 'Upload' ? 'uploading' : ''}`}>
                {ingesting === 'Upload' ? 'Uploading...' : '+ Upload New Target List'}
                <input type="file" accept=".xlsx,.xls,.csv" style={{ display: 'none' }}
                  onChange={async (e) => {
                    const file = e.target.files?.[0];
                    if (file) {
                      setIngesting('Upload');
                      try {
                        const res = await dealApi.uploadFile(file);
                        alert(res.message || "Upload complete!");
                        await loadData();
                      } catch (err: any) {
                        alert(`Upload Failed: ${err.message || "Unknown error"}`);
                      } finally {
                        setIngesting(null);
                        if (e.target) e.target.value = '';
                      }
                    }
                  }}
                />
              </label>
            </div>
          </div>
        </div>
      )}

      {/* Sidebar */}
      <aside className="sidebar">
        <div className="logo-section">
          <div className="logo">AVERROES<span>INTEL</span></div>
        </div>
        <nav className="sidebar-nav">
          <div className="nav-group">
            <span className="group-label">Intelligence</span>
            <Link href="/" className="nav-item">
              <svg width="16" height="16" viewBox="0 0 16 16" fill="none"><path d="M2 2h3v12H2zM6.5 2h3v8h-3zM11 2h3v10h-3z" fill="currentColor" opacity="0.7"/></svg>
              Deal Pipeline
            </Link>
            <Link href="/universe" className="nav-item active">
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
          </div>
        </nav>
        <div className="sidebar-footer">
          <div className="user-profile">
            <div className="avatar">IR</div>
            <div className="user-info">
              <span className="user-name">Ishu Ratna</span>
              <span className="user-role">Associate</span>
            </div>
          </div>
        </div>
      </aside>

      <main className="main-content">
        <header className="page-header">
          <div className="header-left">
            <h1>Master Universe</h1>
            <p className="subtitle">{filteredUniverse.length} targets from {universe.length} total</p>
          </div>
          <div className="header-right">
            <button className="sources-btn" onClick={() => setShowSources(true)}>
              <svg width="16" height="16" viewBox="0 0 16 16" fill="none"><path d="M2 3h12M2 7h8M2 11h10" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round"/></svg>
              Sources
              <span className="sources-badge">{activeSources}</span>
            </button>
            <button className="bulk-smartfill-btn" onClick={openBulkSmartFill} disabled={bulkLoadingEligibility || bulkRunning}>
              {bulkLoadingEligibility ? 'Checking...' : bulkRunning ? 'Running...' : '⚡ Bulk SmartFill'}
            </button>
            <SyncEmailsButton onSynced={loadData} />
            <div className="search-box">
              <svg width="16" height="16" viewBox="0 0 16 16" fill="none"><circle cx="7" cy="7" r="4.5" stroke="#94a3b8" strokeWidth="1.5"/><path d="M10.5 10.5L14 14" stroke="#94a3b8" strokeWidth="1.5" strokeLinecap="round"/></svg>
              <input type="text" placeholder="Search universe..." value={searchQuery} onChange={(e) => { setSearchQuery(e.target.value); setActiveViewId(null); }} />
            </div>
          </div>
        </header>

        {/* Filter bar with saved views */}
        <section className="filter-bar">
          <div className="filter-row">
            <div className="filter-group">
              <label>Vertical</label>
              <select value={filters.vertical} onChange={(e) => { setFilters({...filters, vertical: e.target.value}); setActiveViewId(null); }}>
                {verticals.map(v => <option key={v} value={v}>{v}</option>)}
              </select>
            </div>
            <div className="filter-group">
              <label>Geography</label>
              <select value={filters.region} onChange={(e) => { setFilters({...filters, region: e.target.value}); setActiveViewId(null); }}>
                {regions.map(r => <option key={r} value={r}>{r}</option>)}
              </select>
            </div>
            <div className="filter-group">
              <label>Status</label>
              <select value={filters.status} onChange={(e) => { setFilters({...filters, status: e.target.value}); setActiveViewId(null); }}>
                {statuses.map(s => <option key={s} value={s}>{s}</option>)}
              </select>
            </div>
            <div className="filter-actions">
              {activeFilterCount > 0 && (
                <>
                  <button className="save-view-btn" onClick={() => setShowSaveView(!showSaveView)}>Save View</button>
                  <button className="reset-btn" onClick={() => { setFilters({vertical: "All", region: "All", status: "All"}); setSearchQuery(''); setActiveViewId(null); }}>Reset</button>
                </>
              )}
            </div>
          </div>
          {savedViews.length > 0 && (
            <div className="views-row">
              <div className="saved-views">
                {savedViews.map(view => (
                  <div key={view.id} className={`view-chip ${activeViewId === view.id ? 'active' : ''}`}>
                    <button className="view-chip-btn" onClick={() => handleLoadView(view)}>{view.name}</button>
                    <button className="view-chip-delete" onClick={() => handleDeleteView(view.id)}>&times;</button>
                  </div>
                ))}
              </div>
            </div>
          )}
          {showSaveView && (
            <div className="save-view-form">
              <input type="text" className="save-view-input" placeholder="View name (e.g. UK SaaS Qualified)"
                value={newViewName} onChange={e => setNewViewName(e.target.value)} onKeyDown={e => e.key === 'Enter' && handleSaveView()} autoFocus />
              <button className="save-view-confirm" onClick={handleSaveView} disabled={!newViewName.trim()}>Save</button>
              <button className="save-view-cancel" onClick={() => { setShowSaveView(false); setNewViewName(''); }}>Cancel</button>
            </div>
          )}
        </section>

        {/* Table */}
        <section className="table-section">
          <div className="section-header">
            <h3>{filteredUniverse.length} Targets</h3>
            <button className="refresh-btn" onClick={loadData}>Sync &nbsp;&#8635;</button>
          </div>
          <div className="table-scroll-container">
            <table className="crm-table">
              <thead>
                <tr>
                  <th><InfoTip label="Company" tip={DEFS.company} /></th>
                  <th><InfoTip label="Fit" tip={DEFS.fit} /></th>
                  <th><InfoTip label="Website" tip={DEFS.website} /></th>
                  <th><InfoTip label="Sector" tip={DEFS.sector} /></th>
                  <th><InfoTip label="Region" tip={DEFS.region} /></th>
                  <th><InfoTip label="Employees" tip={DEFS.employees} /></th>
                  <th><InfoTip label="Founded" tip={DEFS.founded} /></th>
                  <th><InfoTip label="Age" tip={DEFS.age} /></th>
                  <th><InfoTip label="Raised" tip={DEFS.raised} /></th>
                  <th><InfoTip label="Valuation" tip={DEFS.valuation} /></th>
                  <th><InfoTip label="Revenue (FY)" tip={DEFS.revenueFY} /></th>
                  <th><InfoTip label="Revenue (Prev FY)" tip={DEFS.revenuePrevFY} /></th>
                  <th><InfoTip label="Revenue Band" tip={DEFS.band} /></th>
                  <th><InfoTip label="EBITDA" tip={DEFS.ebitda} /></th>
                  <th><InfoTip label="Profit" tip={DEFS.profit} /></th>
                  <th><InfoTip label="Assets" tip={DEFS.assets} /></th>
                  <th><InfoTip label="Status" tip={DEFS.status} /></th>
                  <th><InfoTip label="Leadership" tip={DEFS.leadership} /></th>
                  <th><InfoTip label="Email" tip={DEFS.email} /></th>
                  <th><InfoTip label="LinkedIn" tip={DEFS.linkedin} /></th>
                  <th><InfoTip label="Source" tip={DEFS.source} /></th>
                  <th><InfoTip label="Date Added" tip={DEFS.dateAdded} /></th>
                  <th><InfoTip label="Description" tip={DEFS.description} /></th>
                  <th><InfoTip label="Actions" tip={DEFS.actions} /></th>
                </tr>
              </thead>
              <tbody>
                {loading ? (
                  Array.from({ length: 8 }).map((_, i) => (
                    <tr key={i} className="skeleton-row"><td colSpan={24}><div className="skeleton-line"></div></td></tr>
                  ))
                ) : filteredUniverse.length > 0 ? (
                  filteredUniverse.map((company, i) => (
                    <tr key={i} className={company.source === 'Internal Test' ? 'test-row' : ''}>
                      <td className="company-cell">
                        <button className="company-name-btn" onClick={() => setProfileIdx(i)}>{company.name}</button>
                      </td>
                      <td className="score-cell">
                        {company.averroes_fit_score != null ? (
                          <span className={`fit-score-badge ${company.averroes_fit_score >= 0.7 ? 'high' : company.averroes_fit_score >= 0.4 ? 'mid' : 'low'}`}>
                            {Math.round(company.averroes_fit_score * 100)}
                          </span>
                        ) : '—'}
                      </td>
                      <td className="website-cell">
                        {company.website ? (
                          <a href={company.website} target="_blank" rel="noreferrer" className="website-link">{company.website.replace(/^https?:\/\/(www\.)?/, '').replace(/\/$/, '')}</a>
                        ) : '—'}
                      </td>
                      <td className="sector-cell">{company.sector || 'TBD'}</td>
                      <td>{company.region || 'UK/Europe'}</td>
                      <td className="num-cell">{company.employees ? company.employees.toLocaleString() : '—'}</td>
                      <td className="num-cell">{company.year_founded || '—'}</td>
                      <td className="num-cell">{company.year_founded ? `${new Date().getFullYear() - company.year_founded}y` : '—'}</td>
                      <td className="num-cell">{company.total_raised_m ? `£${company.total_raised_m.toFixed(1)}M` : '—'}</td>
                      <td className="num-cell">{company.valuation_estimate_m ? `£${company.valuation_estimate_m.toFixed(1)}M` : '—'}</td>
                      <td className="num-cell">
                        {company.revenue_y1 ? (
                          <span title={company.revenue_y1_date ? `Companies House filing, FY ending ${company.revenue_y1_date}` : 'Companies House filing'}>
                            £{(company.revenue_y1 / 1e6).toFixed(1)}M
                          </span>
                        ) : company.revenue_m ? (
                          <span title="PitchBook">£{company.revenue_m.toFixed(1)}M</span>
                        ) : company.revenue_estimate_m ? (
                          <span className="rev-estimate" title={`Estimated: ${company.revenue_source || 'proxy-based'} (${company.revenue_confidence || 'low'} confidence)`}>
                            ~£{company.revenue_estimate_m.toFixed(1)}M <span className="est-tag">(est.)</span>
                          </span>
                        ) : '—'}
                      </td>
                      <td className="num-cell">
                        {company.revenue_y2 ? (
                          <span title={company.revenue_y2_date ? `Companies House filing, FY ending ${company.revenue_y2_date}` : 'Companies House filing'}>
                            £{(company.revenue_y2 / 1e6).toFixed(1)}M
                          </span>
                        ) : '—'}
                      </td>
                      <td>{(() => { const band = getRevenueBand(company); return band ? <span className={`band-badge band-${band.toLowerCase().replace(/\s+/g, '-')}`}>{band}</span> : '—'; })()}</td>
                      <td className="num-cell">{company.estimated_ebitda ? `£${company.estimated_ebitda.toFixed(1)}M` : '—'}</td>
                      <td className="num-cell">{company.profit_y1 != null ? `£${(company.profit_y1 / 1e6).toFixed(1)}M` : company.net_income_m ? `£${company.net_income_m.toFixed(1)}M` : '—'}</td>
                      <td className="num-cell">{company.total_assets_y1 ? `£${(company.total_assets_y1 / 1e6).toFixed(1)}M` : '—'}</td>
                      <td>
                        {['Not a Fit', 'Scraped', 'Uploaded', 'Under Review'].includes(company.status) ? (
                          <span
                            className={`status-badge clickable ${company.status?.toLowerCase().replace(/\s+/g, '-')}`}
                            title={`${company.status === 'Not a Fit' && company.unfit_reason ? company.unfit_reason + '\n\n' : ''}Click to Qualify anyway (manual override)`}
                            onClick={() => qualifyAnyway(company)}
                          >
                            {qualifyingName === company.name ? 'Qualifying…' : displayStatus(company.status)}
                          </span>
                        ) : (
                          <span className={`status-badge ${company.status?.toLowerCase().replace(/\s+/g, '-')}`}>{displayStatus(company.status)}</span>
                        )}
                      </td>
                      <td>{company.contact_name || '—'}</td>
                      <td className="email-cell">{company.contact_email ? (<a href="#" className="email-link" onClick={(e) => { e.preventDefault(); openOutreach(company); }}>{company.contact_email}</a>) : '—'}</td>
                      <td>{company.linkedin_url ? (<a href={company.linkedin_url} target="_blank" rel="noreferrer" className="linkedin-link">View</a>) : '—'}</td>
                      <td className="source-cell">{company.source}</td>
                      <td className="date-cell">{formatDate(company.ingested_at)}</td>
                      <td>
                        {company.description ? (
                          <button className="desc-btn" onClick={() => setProfileIdx(i)}>View</button>
                        ) : '—'}
                      </td>
                      <td>
                        <div className="action-btns">
                          <button
                            className={`smartfill-btn ${smartFilling === company.name ? 'filling' : ''} ${company.last_smartfill_at ? 'enrich' : ''}`}
                            disabled={smartFilling === company.name}
                            title={company.last_smartfill_at
                              ? `Last SmartFilled: ${new Date(company.last_smartfill_at).toLocaleString('en-GB')}. SmartEnrich refreshes only what's missing or stale (0–2 AI calls).`
                              : 'Never SmartFilled — runs the full AI pipeline (~5 calls)'}
                            onClick={async () => {
                              setSmartFilling(company.name);
                              try {
                                if (company.last_smartfill_at) {
                                  const res = await dealApi.smartEnrich(company.name);
                                  alert(`SmartEnrich: ${(res.actions || []).join(' · ')}`);
                                } else {
                                  const res = await dealApi.smartFill(company.name);
                                  setSmartFillResult(res);
                                }
                                await loadData();
                              } catch (err: any) { alert(`${company.last_smartfill_at ? 'SmartEnrich' : 'SmartFill'} failed: ${err.message}`); }
                              finally { setSmartFilling(null); }
                            }}>
                            {smartFilling === company.name ? '...' : company.last_smartfill_at ? 'SmartEnrich ↻' : 'SmartFill'}
                          </button>
                          {(() => { const ob = outreachButtonState(company); return (
                            <button className={`outreach-btn ${ob.cls}`} title={ob.title}
                              onClick={() => openOutreach(company)}>
                              {ob.label}
                            </button>
                          ); })()}
                        </div>
                      </td>
                    </tr>
                  ))
                ) : (
                  <tr><td colSpan={24} className="empty-row">No targets match your search.</td></tr>
                )}
              </tbody>
            </table>
          </div>
        </section>
      </main>

      {/* Company Drawer */}
      {profileIdx != null && filteredUniverse[profileIdx] && (
        <CompanyProfile
          companies={filteredUniverse}
          index={profileIdx}
          onClose={() => setProfileIdx(null)}
          onNavigate={setProfileIdx}
          onChanged={loadData}
        />
      )}

      <style jsx>{`
        /* ── Layout ─────────────────────────────────────────────── */
        .layout-wrapper { display: flex; min-height: 100vh; background: #f8fafc; }

        /* ── Sidebar ────────────────────────────────────────────── */
        .sidebar {
          width: 260px; background: #fff; border-right: 1px solid #e2e8f0;
          display: flex; flex-direction: column; position: fixed; height: 100vh; z-index: 100;
        }
        .logo-section { padding: 2rem 1.75rem 1.5rem; }
        .logo { font-size: 1.25rem; font-weight: 900; letter-spacing: 0.08em; color: #0f172a; }
        .logo span { color: #2563eb; }
        .sidebar-nav { flex: 1; padding: 0 1.25rem; overflow-y: auto; }
        .nav-group { margin-bottom: 2rem; display: flex; flex-direction: column; gap: 0.25rem; }
        .group-label { font-size: 0.65rem; text-transform: uppercase; letter-spacing: 0.15em; color: #94a3b8; padding-left: 0.75rem; margin-bottom: 0.5rem; font-weight: 700; }
        .nav-item {
          display: flex; align-items: center; gap: 0.6rem;
          padding: 0.65rem 0.75rem; color: #64748b; border-radius: 8px;
          font-weight: 600; font-size: 0.88rem; transition: all 0.15s;
        }
        .nav-item:hover { color: #2563eb; background: #eff6ff; }
        .nav-item.active { color: #2563eb; background: #eff6ff; }
        .sidebar-footer { padding: 1.25rem; border-top: 1px solid #e2e8f0; }
        .user-profile { display: flex; align-items: center; gap: 0.65rem; }
        .avatar { width: 36px; height: 36px; background: #eff6ff; color: #2563eb; border-radius: 50%; display: flex; align-items: center; justify-content: center; font-weight: 800; font-size: 0.8rem; }
        .user-info { display: flex; flex-direction: column; }
        .user-name { font-size: 0.85rem; font-weight: 700; color: #0f172a; }
        .user-role { font-size: 0.68rem; color: #94a3b8; }

        /* ── Main ───────────────────────────────────────────────── */
        .main-content { margin-left: 260px; flex: 1; padding: 2rem 2.5rem; max-width: calc(100vw - 260px); }

        .page-header { display: flex; justify-content: space-between; align-items: flex-start; margin-bottom: 1.5rem; flex-wrap: wrap; gap: 1rem; }
        h1 { font-size: 1.75rem; font-weight: 800; color: #0f172a; margin-bottom: 0.25rem; letter-spacing: -0.02em; }
        .subtitle { color: #94a3b8; font-size: 0.88rem; font-weight: 500; margin: 0; }
        .header-right { display: flex; align-items: center; gap: 0.75rem; }

        /* Sources button */
        .sources-btn {
          display: flex; align-items: center; gap: 0.5rem;
          padding: 0.55rem 1rem; background: #fff; border: 1px solid #e2e8f0;
          border-radius: 8px; font-size: 0.82rem; font-weight: 700; color: #334155;
          cursor: pointer; transition: all 0.15s;
          box-shadow: 0 1px 2px rgba(15, 23, 42, 0.04);
        }
        .sources-btn:hover { border-color: #2563eb; color: #2563eb; box-shadow: 0 2px 6px rgba(37, 99, 235, 0.12); }
        .sources-badge {
          background: #2563eb; color: #fff; font-size: 0.65rem; font-weight: 800;
          padding: 0.1rem 0.45rem; border-radius: 10px; min-width: 20px; text-align: center;
        }

        .search-box {
          display: flex; align-items: center; padding: 0.6rem 1rem; gap: 0.6rem;
          background: #fff; border: 1.5px solid #e2e8f0; border-radius: 8px; width: 280px;
        }
        .search-box input { background: transparent; border: none; color: #0f172a; width: 100%; outline: none; font-size: 0.88rem; }

        /* ── Sources Overlay ─────────────────────────────────────── */
        .sources-overlay {
          position: fixed; inset: 0; z-index: 200;
          background: rgba(15, 23, 42, 0.4);
          display: flex; justify-content: center; align-items: flex-start;
          padding: 3rem;
          overflow-y: auto;
          animation: fadeIn 0.15s ease;
        }
        @keyframes fadeIn { from { opacity: 0; } to { opacity: 1; } }

        .sources-panel {
          background: #fff;
          border-radius: 16px;
          width: 100%;
          max-width: 900px;
          padding: 2rem 2.5rem 2.5rem;
          box-shadow: 0 25px 60px rgba(0,0,0,0.12);
          animation: slideUp 0.2s ease;
        }
        @keyframes slideUp { from { opacity: 0; transform: translateY(20px); } to { opacity: 1; transform: translateY(0); } }

        .sources-header {
          display: flex; justify-content: space-between; align-items: flex-start;
          margin-bottom: 2rem; padding-bottom: 1.25rem; border-bottom: 1px solid #e2e8f0;
        }
        .sources-title { font-size: 1.5rem; font-weight: 800; color: #0f172a; margin: 0 0 0.25rem; }
        .sources-subtitle { font-size: 0.85rem; color: #94a3b8; margin: 0; }
        .sources-close {
          background: none; border: none; color: #94a3b8; cursor: pointer; padding: 0.25rem;
          border-radius: 6px; display: flex; align-items: center;
        }
        .sources-close:hover { background: #f1f5f9; color: #0f172a; }

        .source-type-section { margin-bottom: 1.75rem; }
        .source-type-label {
          font-size: 0.72rem; text-transform: uppercase; letter-spacing: 0.12em;
          color: #94a3b8; font-weight: 700; margin: 0 0 0.65rem;
        }

        .source-cards-grid { display: flex; flex-direction: column; gap: 0.5rem; }

        .source-card {
          border: 1px solid #e2e8f0; border-radius: 10px;
          overflow: hidden; transition: all 0.15s;
        }
        .source-card:hover { border-color: #cbd5e1; }
        .source-card.expanded { border-color: #2563eb; box-shadow: 0 2px 12px rgba(37,99,235,0.08); }

        .source-card-header {
          display: flex; justify-content: space-between; align-items: center;
          padding: 0.85rem 1.25rem; background: none; border: none;
          width: 100%; cursor: pointer; text-align: left;
        }
        .source-card-header:hover { background: #f8fafc; }

        .source-card-left { display: flex; align-items: center; gap: 0.75rem; }
        .source-icon { font-size: 1.3rem; }
        .source-name { font-size: 0.92rem; font-weight: 700; color: #0f172a; display: block; }
        .source-type-badge {
          font-size: 0.58rem; font-weight: 700; text-transform: uppercase; letter-spacing: 0.05em;
          padding: 0.1rem 0.4rem; border-radius: 3px; margin-top: 0.15rem; display: inline-block;
        }

        .source-card-right { display: flex; align-items: center; gap: 0.75rem; }
        .source-count { font-size: 1.15rem; font-weight: 800; color: #0f172a; }
        .chevron { transition: transform 0.2s; }
        .chevron.open { transform: rotate(180deg); }

        .source-expanded {
          padding: 0 1.25rem 1.25rem;
          border-top: 1px solid #f1f5f9;
          animation: expandIn 0.15s ease;
        }
        @keyframes expandIn { from { opacity: 0; } to { opacity: 1; } }

        .source-desc { font-size: 0.85rem; color: #64748b; line-height: 1.5; margin: 1rem 0; }

        .source-stats-grid {
          display: grid; grid-template-columns: 1fr 1fr; gap: 0.65rem;
          margin-bottom: 1rem;
        }

        .source-stat {
          padding: 0.65rem 0.85rem; background: #f8fafc; border-radius: 8px;
          display: flex; flex-direction: column; gap: 0.2rem;
        }
        .source-stat.full-width { grid-column: 1 / -1; }
        .source-stat-label { font-size: 0.65rem; text-transform: uppercase; letter-spacing: 0.08em; color: #94a3b8; font-weight: 700; }
        .source-stat-value { font-size: 0.92rem; font-weight: 700; color: #0f172a; }
        .source-stat-qualified { color: #059669; }

        .source-refresh-btn {
          padding: 0.5rem 1.25rem; background: #2563eb; color: #fff;
          border: none; border-radius: 8px; font-size: 0.82rem; font-weight: 700;
          cursor: pointer; transition: all 0.15s; width: 100%;
        }
        .source-refresh-btn:hover:not(:disabled) { background: #1d4ed8; }
        .source-refresh-btn:disabled { opacity: 0.5; cursor: not-allowed; }
        .source-refresh-btn.refreshing { background: #64748b; }

        .source-upload-section { padding-top: 0.75rem; border-top: 1px solid #e2e8f0; margin-top: 0.5rem; }
        .source-upload-btn {
          display: flex; align-items: center; justify-content: center;
          padding: 0.85rem; border: 1.5px dashed #2563eb; border-radius: 10px;
          color: #2563eb; font-size: 0.88rem; font-weight: 700;
          cursor: pointer; transition: all 0.15s; width: 100%; background: #f8fafc;
        }
        .source-upload-btn:hover { background: #2563eb; color: #fff; border-style: solid; }
        .source-upload-btn.uploading { opacity: 0.5; cursor: wait; }

        /* ── Filters Overlay ────────────────────────────────────── */
        .filters-panel { max-width: 960px; }
        .criteria-loading { text-align: center; padding: 3rem; color: #64748b; }
        .criteria-loading .spinner { margin: 0 auto 1rem; }
        .criteria-content { display: flex; flex-direction: column; gap: 1.75rem; }
        .criteria-cards { display: flex; flex-direction: column; gap: 0.85rem; }

        .criteria-card {
          display: flex; gap: 1rem; padding: 1.25rem; background: #f8fafc;
          border: 1px solid #e2e8f0; border-radius: 10px;
        }
        .criteria-card-icon { font-size: 1.5rem; flex-shrink: 0; margin-top: 0.1rem; }
        .criteria-card-body { flex: 1; min-width: 0; }
        .criteria-card-title { font-size: 1rem; font-weight: 800; color: #0f172a; margin: 0 0 0.3rem; }
        .criteria-card-desc { font-size: 0.82rem; color: #64748b; margin: 0 0 0.65rem; line-height: 1.5; }
        .criteria-tags { display: flex; flex-wrap: wrap; gap: 0.3rem; }
        .criteria-tag {
          font-size: 0.68rem; font-weight: 600; padding: 0.2rem 0.55rem;
          border-radius: 4px; white-space: nowrap;
        }
        .criteria-tag.geo { background: #dbeafe; color: #1e40af; }
        .criteria-tag.geo.code { background: #e0e7ff; color: #3730a3; font-family: monospace; }
        .criteria-tag.tech { background: #dcfce7; color: #166534; }
        .criteria-tag.more { background: #f1f5f9; color: #64748b; font-style: italic; }
        .criteria-tag.size-micro { background: #f0fdf4; color: #166534; }
        .criteria-tag.size-small { background: #dcfce7; color: #166534; }
        .criteria-tag.size-mid { background: #fef3c7; color: #92400e; }
        .criteria-tag.size-large { background: #fef2f2; color: #dc2626; text-decoration: line-through; }

        .criteria-meta {
          font-size: 0.82rem; color: #0f172a; padding: 0.5rem 0.85rem;
          background: #f8fafc; border-radius: 6px;
        }
        .criteria-meta-label { font-weight: 700; color: #64748b; font-size: 0.72rem; text-transform: uppercase; letter-spacing: 0.05em; margin-right: 0.5rem; }
        .criteria-meta.muted { color: #94a3b8; font-size: 0.75rem; }

        /* Chat section */
        .criteria-chat-section {
          border-top: 1px solid #e2e8f0; padding-top: 1.5rem;
        }
        .criteria-chat-title { font-size: 1rem; font-weight: 800; color: #0f172a; margin: 0 0 0.25rem; }
        .criteria-chat-desc { font-size: 0.82rem; color: #94a3b8; margin: 0 0 1rem; }

        .criteria-chat-messages {
          display: flex; flex-direction: column; gap: 0.75rem;
          max-height: 340px; overflow-y: auto; margin-bottom: 0.85rem;
          padding-right: 0.5rem;
        }

        .chat-msg { display: flex; }
        .chat-msg.user { justify-content: flex-end; }
        .chat-msg.ai { justify-content: flex-start; }
        .chat-msg-bubble {
          max-width: 80%; padding: 0.75rem 1rem; border-radius: 12px;
          font-size: 0.85rem; line-height: 1.55;
        }
        .chat-msg.user .chat-msg-bubble { background: #2563eb; color: #fff; border-bottom-right-radius: 4px; }
        .chat-msg.ai .chat-msg-bubble { background: #f1f5f9; color: #0f172a; border-bottom-left-radius: 4px; }
        .chat-msg-bubble p { margin: 0; }

        .chat-preview {
          margin-top: 0.75rem; padding-top: 0.65rem; border-top: 1px solid #e2e8f0;
          display: flex; flex-direction: column; gap: 0.35rem;
        }
        .preview-row { display: flex; justify-content: space-between; align-items: center; }
        .preview-label { font-size: 0.75rem; color: #64748b; font-weight: 600; }
        .preview-value { font-size: 0.85rem; font-weight: 700; color: #0f172a; }
        .preview-value.green { color: #059669; }
        .preview-value.red { color: #dc2626; }
        .preview-samples { margin-top: 0.25rem; }
        .preview-names { font-size: 0.78rem; color: #475569; }

        .chat-thinking {
          display: flex; align-items: center; gap: 0.6rem;
          font-size: 0.82rem; color: #94a3b8;
        }
        .dot-pulse {
          width: 6px; height: 6px; background: #94a3b8; border-radius: 50%;
          animation: dotPulse 1s infinite;
        }
        @keyframes dotPulse { 0%, 100% { opacity: 0.3; } 50% { opacity: 1; } }

        .criteria-action-bar {
          display: flex; justify-content: space-between; align-items: center;
          padding: 0.75rem 1rem; background: #eff6ff; border: 1.5px solid #2563eb;
          border-radius: 10px; margin-bottom: 0.75rem;
        }
        .criteria-action-bar.applying { justify-content: center; gap: 0.75rem; border-color: #94a3b8; background: #f8fafc; color: #64748b; font-size: 0.85rem; }
        .action-bar-text { font-size: 0.85rem; font-weight: 700; color: #1e40af; }
        .action-bar-btns { display: flex; gap: 0.5rem; }
        .discard-btn {
          padding: 0.4rem 1rem; background: #fff; border: 1px solid #e2e8f0;
          border-radius: 6px; font-size: 0.78rem; font-weight: 700; color: #64748b; cursor: pointer;
        }
        .discard-btn:hover { border-color: #dc2626; color: #dc2626; }
        .apply-btn {
          padding: 0.4rem 1.25rem; background: #2563eb; color: #fff;
          border: none; border-radius: 6px; font-size: 0.78rem; font-weight: 800; cursor: pointer;
        }
        .apply-btn:hover { background: #1d4ed8; }
        .spinner.small { width: 18px; height: 18px; border-width: 2px; }

        .criteria-chat-input {
          display: flex; gap: 0.5rem; align-items: center;
        }
        .criteria-chat-input input {
          flex: 1; padding: 0.65rem 1rem; border: 1.5px solid #e2e8f0;
          border-radius: 10px; font-size: 0.88rem; color: #0f172a; background: #fff; outline: none;
        }
        .criteria-chat-input input:focus { border-color: #2563eb; }
        .criteria-chat-input input:disabled { opacity: 0.5; }
        .chat-send-btn {
          padding: 0.6rem; background: #2563eb; color: #fff; border: none;
          border-radius: 8px; cursor: pointer; display: flex; align-items: center; justify-content: center;
        }
        .chat-send-btn:hover:not(:disabled) { background: #1d4ed8; }
        .chat-send-btn:disabled { opacity: 0.3; cursor: not-allowed; }

        /* ── Filter Bar ─────────────────────────────────────────── */
        .filter-bar {
          background: #fff; border: 1px solid #e2e8f0; border-radius: 10px;
          padding: 1.25rem 1.5rem; margin-bottom: 1.5rem;
        }
        .filter-row { display: flex; gap: 1.5rem; align-items: flex-end; flex-wrap: wrap; }
        .filter-group { display: flex; flex-direction: column; gap: 0.3rem; }
        .filter-group label { font-size: 0.65rem; text-transform: uppercase; letter-spacing: 0.1em; color: #94a3b8; font-weight: 700; }
        .filter-group select {
          padding: 0.45rem 0.75rem; border: 1px solid #e2e8f0; border-radius: 6px;
          font-size: 0.82rem; color: #0f172a; background: #f8fafc; min-width: 140px; cursor: pointer;
        }
        .filter-actions { display: flex; gap: 0.5rem; align-items: center; margin-left: auto; }
        .save-view-btn { background: none; border: 1px solid #2563eb; color: #2563eb; padding: 0.35rem 0.85rem; border-radius: 6px; font-size: 0.72rem; font-weight: 700; cursor: pointer; }
        .save-view-btn:hover { background: #eff6ff; }
        .reset-btn { background: none; border: none; color: #94a3b8; font-size: 0.72rem; cursor: pointer; text-decoration: underline; }
        .reset-btn:hover { color: #64748b; }
        .views-row { border-top: 1px solid #f1f5f9; padding-top: 0.75rem; margin-top: 0.75rem; }
        .saved-views { display: flex; gap: 0.35rem; flex-wrap: wrap; }
        .view-chip { display: flex; align-items: center; border: 1px solid #e2e8f0; border-radius: 6px; overflow: hidden; transition: all 0.15s; }
        .view-chip.active { border-color: #2563eb; background: #eff6ff; }
        .view-chip-btn { background: none; border: none; padding: 0.25rem 0.6rem; font-size: 0.72rem; font-weight: 600; color: #64748b; cursor: pointer; }
        .view-chip.active .view-chip-btn { color: #2563eb; }
        .view-chip-delete { background: none; border: none; border-left: 1px solid #e2e8f0; padding: 0.25rem 0.4rem; font-size: 0.8rem; color: #cbd5e1; cursor: pointer; }
        .view-chip-delete:hover { color: #ef4444; }
        .save-view-form { display: flex; gap: 0.5rem; align-items: center; margin-top: 0.75rem; padding-top: 0.75rem; border-top: 1px solid #f1f5f9; }
        .save-view-input { flex: 1; padding: 0.4rem 0.75rem; border: 1.5px solid #e2e8f0; border-radius: 6px; font-size: 0.82rem; outline: none; }
        .save-view-input:focus { border-color: #2563eb; }
        .save-view-confirm { padding: 0.4rem 1rem; background: #2563eb; color: #fff; border: none; border-radius: 6px; font-size: 0.78rem; font-weight: 700; cursor: pointer; }
        .save-view-confirm:disabled { opacity: 0.4; }
        .save-view-cancel { padding: 0.4rem 0.75rem; background: none; border: 1px solid #e2e8f0; border-radius: 6px; font-size: 0.78rem; color: #64748b; cursor: pointer; }

        /* ── Table ──────────────────────────────────────────────── */
        .table-section { background: #fff; border: 1px solid #e2e8f0; border-radius: 10px; padding: 1.5rem; }
        .section-header { display: flex; justify-content: space-between; align-items: center; margin-bottom: 1.25rem; padding-bottom: 1rem; border-bottom: 1px solid #f1f5f9; }
        .section-header h3 { font-size: 1.1rem; color: #0f172a; margin: 0; }
        .refresh-btn { background: none; border: 1px solid #e2e8f0; color: #64748b; padding: 0.4rem 0.85rem; border-radius: 6px; font-size: 0.78rem; font-weight: 600; cursor: pointer; }
        .refresh-btn:hover { border-color: #2563eb; color: #2563eb; }
        .table-scroll-container { overflow: auto; max-height: calc(100vh - 215px); border-radius: 0 0 12px 12px; }
        .crm-table { width: 100%; border-collapse: separate; border-spacing: 0; text-align: left; }
        .crm-table th {
          position: sticky; top: 0; z-index: 5;
          background: #f8fafc; color: #64748b; font-size: 0.66rem; text-transform: uppercase;
          letter-spacing: 0.09em; font-weight: 800; padding: 0.7rem 1rem;
          border-bottom: 1px solid #e2e8f0; white-space: nowrap;
          box-shadow: 0 1px 0 #e2e8f0;
        }
        .crm-table td { padding: 0.8rem 1rem; border-bottom: 1px solid #f1f5f9; font-size: 0.85rem; color: #475569; white-space: nowrap; transition: background 0.1s; }
        .crm-table tbody tr:hover td { background: #f8fafc; }
        .crm-table tbody tr:last-child td { border-bottom: none; }
        .company-name-btn { background: none; border: none; padding: 0; font-size: 0.88rem; font-weight: 700; color: #0f172a; cursor: pointer; text-align: left; }
        .company-name-btn:hover { color: #2563eb; }
        .score-cell { text-align: center; }
        .fit-score-badge { font-size: 0.72rem; font-weight: 800; padding: 0.15rem 0.5rem; border-radius: 999px; color: white; }
        .fit-score-badge.high { background: #16a34a; }
        .fit-score-badge.mid { background: #d97706; }
        .fit-score-badge.low { background: #dc2626; }
        .website-cell { max-width: 160px; overflow: hidden; text-overflow: ellipsis; }
        .website-link { color: #2563eb; font-size: 0.78rem; font-weight: 600; text-decoration: none; }
        .website-link:hover { text-decoration: underline; }
        .sector-cell { font-weight: 600; color: #0f172a; }
        .num-cell { font-size: 0.82rem; font-variant-numeric: tabular-nums; text-align: right; }
        .status-badge { font-size: 0.62rem; font-weight: 800; padding: 0.25rem 0.55rem; border-radius: 999px; text-transform: uppercase; letter-spacing: 0.05em; }
        .status-badge.clickable { cursor: pointer; border: 1px dashed rgba(0,0,0,0.15); transition: filter 0.15s, box-shadow 0.15s; }
        .status-badge.clickable:hover { filter: brightness(0.94); box-shadow: 0 1px 4px rgba(0,0,0,0.15); }
        .status-badge.qualified { background: #dcfce7; color: #166534; }
        .status-badge.under-review { background: #fef3c7; color: #92400e; }
        .status-badge.uploaded { background: #eff6ff; color: #2563eb; }
        .status-badge.scraped { background: #f1f5f9; color: #94a3b8; }
        .status-badge.not-a-fit { background: #fef2f2; color: #dc2626; }
        .band-badge { font-size: 0.62rem; font-weight: 800; padding: 0.25rem 0.5rem; border-radius: 4px; text-transform: uppercase; letter-spacing: 0.05em; white-space: nowrap; }
        .band-badge.band-target-band { background: #dcfce7; color: #166534; }
        .band-badge.band-too-early { background: #fef3c7; color: #92400e; }
        .band-badge.band-too-large { background: #fef2f2; color: #dc2626; }
        .crm-table tr.test-row td { background: #fef3c7 !important; }
        .crm-table tr.test-row .company-name-btn::after { content: ' 🧪'; }
        .rev-estimate { color: #64748b; font-style: italic; cursor: help; }
        .est-tag { font-size: 0.62rem; color: #94a3b8; font-style: normal; }

        /* ── Bulk SmartFill ── */
        .bulk-smartfill-btn {
          background: #0f172a; color: #fff; border: none; border-radius: 8px;
          padding: 0.55rem 1rem; font-size: 0.8rem; font-weight: 700; cursor: pointer;
        }
        .bulk-smartfill-btn:hover:not(:disabled) { background: #1e293b; }
        .bulk-smartfill-btn:disabled { opacity: 0.6; cursor: wait; }
        .bulk-modal {
          background: #fff; border-radius: 12px; width: 480px; max-width: 92vw;
          padding: 1.25rem 1.5rem; box-shadow: 0 20px 50px rgba(2,6,23,0.35);
        }
        .bulk-modal-header { display: flex; justify-content: space-between; align-items: center; margin-bottom: 0.75rem; }
        .bulk-modal-header h3 { font-size: 1.05rem; color: #0f172a; }
        .bulk-funnel { border: 1px solid #e2e8f0; border-radius: 8px; overflow: hidden; margin-bottom: 0.9rem; }
        .bulk-funnel-row { display: flex; justify-content: space-between; padding: 0.5rem 0.8rem; font-size: 0.8rem; border-bottom: 1px solid #f1f5f9; color: #334155; }
        .bulk-funnel-row.excluded { color: #94a3b8; }
        .bulk-funnel-row.eligible { background: #f0fdf4; color: #166534; font-weight: 700; border-bottom: none; }
        .bulk-estimate h4 { font-size: 0.8rem; color: #0f172a; margin-bottom: 0.3rem; }
        .bulk-estimate p { font-size: 0.76rem; color: #475569; margin-bottom: 0.35rem; line-height: 1.45; }
        .bulk-estimate .bulk-note { font-size: 0.7rem; color: #94a3b8; }
        .bulk-actions { display: flex; justify-content: flex-end; gap: 0.6rem; margin-top: 1rem; }
        .bulk-cancel { background: #fff; border: 1px solid #e2e8f0; color: #64748b; border-radius: 8px; padding: 0.5rem 1rem; font-size: 0.8rem; font-weight: 600; cursor: pointer; }
        .bulk-start { background: #16a34a; border: none; color: #fff; border-radius: 8px; padding: 0.5rem 1rem; font-size: 0.8rem; font-weight: 700; cursor: pointer; }
        .bulk-start:disabled { opacity: 0.5; cursor: not-allowed; }
        .bulk-run { padding: 0.5rem 0 0.25rem 0; }
        .bulk-bar-track { height: 10px; background: #f1f5f9; border-radius: 6px; overflow: hidden; margin-bottom: 0.6rem; }
        .bulk-bar-fill { height: 100%; background: #16a34a; transition: width 0.4s ease; }
        .bulk-run-status { font-size: 0.78rem; color: #475569; margin-bottom: 0.8rem; }
        .email-cell { font-size: 0.78rem; }
        .email-link { color: #2563eb; text-decoration: none; }
        .email-link:hover { text-decoration: underline; }
        .linkedin-link { color: #0A66C2; font-weight: 600; text-decoration: underline; }
        .source-cell { font-size: 0.78rem; color: #94a3b8; }
        .date-cell { font-size: 0.78rem; }
        .empty-row { text-align: center; padding: 3rem !important; color: #94a3b8; }
        .desc-btn { background: transparent; border: 1px solid #2563eb; color: #2563eb; padding: 0.25rem 0.6rem; border-radius: 4px; font-size: 0.68rem; font-weight: 700; cursor: pointer; }
        .desc-btn:hover { background: #2563eb; color: white; }
        .action-btns { display: flex; gap: 0.35rem; }
        .smartfill-btn { background: transparent; border: 1px solid #2563eb; color: #2563eb; padding: 0.3rem 0.65rem; border-radius: 4px; font-size: 0.68rem; font-weight: 700; cursor: pointer; }
        .smartfill-btn.enrich { border-color: #16a34a; color: #16a34a; }
        .smartfill-btn.enrich:hover:not(:disabled) { background: #f0fdf4; }
        .outreach-btn.drafted { border-color: #8b5cf6; color: #8b5cf6; }
        .outreach-btn.drafted:hover { background: #f5f3ff; }
        .outreach-btn.sent { border-color: #16a34a; color: #16a34a; background: #f0fdf4; }
        .outreach-btn.sent:hover { background: #dcfce7; }
        .smartfill-btn:hover:not(:disabled) { background: #2563eb; color: white; }
        .smartfill-btn.filling { opacity: 0.4; cursor: wait; }
        .outreach-btn { background: transparent; border: 1px solid #d97706; color: #d97706; padding: 0.3rem 0.65rem; border-radius: 4px; font-size: 0.68rem; font-weight: 700; cursor: pointer; }
        .outreach-btn:hover { background: #d97706; color: white; }
        .skeleton-row td { padding: 0.75rem 1rem; }
        .skeleton-line { height: 10px; background: #e2e8f0; width: 100%; border-radius: 2px; animation: pulse 1.5s infinite; }
        @keyframes pulse { 0%, 100% { opacity: 1; } 50% { opacity: 0.5; } }

        /* ── Modals ────────────────────────────────────────────── */
        .modal-overlay { position: fixed; inset: 0; background: rgba(15, 23, 42, 0.3); display: flex; align-items: center; justify-content: center; z-index: 1000; }
        .modal-content { background: #fff; border-radius: 12px; width: 520px; max-width: 90vw; max-height: 85vh; display: flex; flex-direction: column; box-shadow: 0 20px 60px rgba(0,0,0,0.12); overflow: hidden; }
        .outreach-modal { width: 640px; }
        .modal-header { display: flex; justify-content: space-between; align-items: center; padding: 1.25rem 1.5rem; border-bottom: 1px solid #e2e8f0; }
        .modal-header h3 { font-size: 1rem; font-weight: 800; color: #0f172a; margin: 0; }
        .modal-close { background: none; border: none; font-size: 1.4rem; color: #94a3b8; cursor: pointer; }
        .modal-body { padding: 1.5rem; overflow-y: auto; flex: 1; }
        .modal-footer { padding: 1rem 1.5rem; display: flex; justify-content: flex-end; gap: 0.5rem; border-top: 1px solid #f1f5f9; }
        .modal-ok-btn { background: #2563eb; color: white; border: none; padding: 0.5rem 1.5rem; border-radius: 6px; font-weight: 700; font-size: 0.85rem; cursor: pointer; }
        .modal-ok-btn:hover { opacity: 0.9; }
        .result-company-name { font-size: 1.2rem; font-weight: 800; color: #0f172a; margin-bottom: 1.25rem; padding-bottom: 0.75rem; border-bottom: 2px solid #2563eb; }
        .result-grid { display: flex; flex-direction: column; gap: 0.5rem; }
        .result-row { display: flex; justify-content: space-between; align-items: center; padding: 0.6rem 0.85rem; border-radius: 6px; background: #f8fafc; }
        .result-label { font-size: 0.75rem; font-weight: 700; color: #94a3b8; text-transform: uppercase; letter-spacing: 0.05em; }
        .result-value { font-size: 0.85rem; font-weight: 700; text-align: right; max-width: 60%; overflow: hidden; text-overflow: ellipsis; }
        .result-value.found { color: #16a34a; }
        .result-value.low { color: #dc2626; }
        .result-value.not-found { color: #dc2626; font-style: italic; }
        .result-value a { color: #0A66C2; text-decoration: underline; word-break: break-all; }
        .result-description { padding: 0.85rem; border-radius: 6px; background: #f8fafc; margin-top: 0.25rem; }
        .result-description .result-label { display: block; margin-bottom: 0.4rem; }
        .description-text { font-size: 0.85rem; color: #0f172a; line-height: 1.65; margin: 0; white-space: pre-wrap; }
        .result-ch-header { display: flex; align-items: center; gap: 0.5rem; margin: 0.75rem 0 0.25rem; padding: 0.4rem 0.85rem; }
        .result-ch-badge { font-size: 0.65rem; font-weight: 800; text-transform: uppercase; letter-spacing: 0.05em; padding: 0.2rem 0.55rem; background: #0f172a; color: #fff; border-radius: 4px; }
        .result-ch-conf { font-size: 0.65rem; font-weight: 700; padding: 0.15rem 0.45rem; border-radius: 4px; text-transform: capitalize; }
        .result-ch-conf-high { background: #dcfce7; color: #166534; }
        .result-ch-conf-medium { background: #fef9c3; color: #854d0e; }
        .result-ch-conf-low { background: #fee2e2; color: #991b1b; }
        .outreach-loading { text-align: center; padding: 2.5rem 1.5rem; }
        .outreach-loading p { color: #64748b; margin-top: 0.75rem; font-size: 0.95rem; }
        .outreach-loading .loading-sub { font-size: 0.82rem; color: #94a3b8; margin-top: 0.15rem; }
        .spinner { width: 36px; height: 36px; border: 3px solid #e2e8f0; border-top-color: #2563eb; border-radius: 50%; animation: spin 0.8s linear infinite; margin: 0 auto; }
        @keyframes spin { to { transform: rotate(360deg); } }
        .outreach-sent { text-align: center; padding: 1.5rem; }
        .sent-icon { font-size: 2.5rem; color: #16a34a; margin-bottom: 0.75rem; }
        .outreach-sent h4 { font-size: 1.15rem; color: #0f172a; margin-bottom: 0.4rem; }
        .outreach-sent .sent-sub { font-size: 0.82rem; color: #94a3b8; margin-top: 0.4rem; }
        .outreach-form { display: flex; flex-direction: column; gap: 0.85rem; }
        .form-row { display: flex; flex-direction: column; gap: 0.25rem; }
        .form-row label { font-size: 0.68rem; text-transform: uppercase; letter-spacing: 0.1em; color: #94a3b8; font-weight: 700; }
        .form-row input { padding: 0.5rem 0.65rem; border: 1px solid #e2e8f0; border-radius: 6px; font-size: 0.88rem; color: #0f172a; background: #f8fafc; }
        .form-row textarea { padding: 0.65rem; border: 1px solid #e2e8f0; border-radius: 6px; font-size: 0.88rem; color: #0f172a; background: #f8fafc; resize: vertical; line-height: 1.6; font-family: inherit; }
        .form-row input:focus, .form-row textarea:focus { outline: none; border-color: #2563eb; background: #fff; }
        .from-row { flex-direction: row; }
        .from-label { font-size: 0.78rem; color: #94a3b8; font-style: italic; }
        .outreach-cancel-btn { background: transparent; border: 1px solid #e2e8f0; color: #64748b; padding: 0.5rem 1.25rem; border-radius: 6px; font-weight: 700; font-size: 0.82rem; cursor: pointer; }
        .outreach-copy-btn { background: transparent; border: 1px solid #2563eb; color: #2563eb; padding: 0.5rem 1.25rem; border-radius: 6px; font-weight: 700; font-size: 0.82rem; cursor: pointer; }
        .outreach-copy-btn:hover { background: #eff6ff; }
        .outreach-send-btn { background: #d97706; color: white; border: none; padding: 0.5rem 1.5rem; border-radius: 6px; font-weight: 800; font-size: 0.85rem; cursor: pointer; }
        .outreach-send-btn:hover:not(:disabled) { opacity: 0.9; }
        .outreach-send-btn:disabled { opacity: 0.4; }

        /* ── Responsive ────────────────────────────────────────── */
        @media (max-width: 1600px) { .crm-table th:nth-child(6), .crm-table td:nth-child(6), .crm-table th:nth-child(7), .crm-table td:nth-child(7) { display: none; } }
        @media (max-width: 1400px) { .crm-table th:nth-child(12), .crm-table td:nth-child(12), .crm-table th:nth-child(14), .crm-table td:nth-child(14) { display: none; } }
        @media (max-width: 1100px) { .crm-table th:nth-child(4), .crm-table td:nth-child(4), .crm-table th:nth-child(5), .crm-table td:nth-child(5), .crm-table th:nth-child(13), .crm-table td:nth-child(13) { display: none; } }
        @media (max-width: 1024px) {
          .sidebar { width: 72px; }
          .sidebar .logo span, .sidebar .group-label, .sidebar .nav-item span:not(svg),
          .sidebar .user-info { display: none !important; }
          .sidebar .logo { text-align: center; padding: 1.5rem 0; font-size: 0.9rem; }
          .main-content { margin-left: 72px; max-width: calc(100vw - 72px); }
        }
        @media (max-width: 768px) {
          .page-header { flex-direction: column; gap: 1rem; }
          .header-right { flex-direction: column; width: 100%; }
          .search-box { width: 100%; }
          .filter-row { flex-direction: column; }
          .sources-overlay { padding: 1rem; }
        }
      `}</style>
    </div>
  );
}
