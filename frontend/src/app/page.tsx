'use client';

import { useEffect, useState, useRef } from "react";
import Link from 'next/link';
import { CompanyTarget, ActivityEntry, DEAL_STAGES, getRevenueBand } from "../types";
import { dealApi } from "../services/api";
import CompanyDrawer from "../components/CompanyDrawer";

// ── Filter helpers ──────────────────────────────────────────────────────────

function isSaaSOrB2B(c: CompanyTarget): 'high' | 'medium' | 'low' {
  const text = `${c.sector || ''} ${c.description || ''} ${c.keywords || ''} ${c.verticals || ''}`.toLowerCase();
  const isB2B = ['b2b', 'enterprise', 'business', 'corporate', 'professional services', 'industrial', 'logistics'].some(k => text.includes(k));
  const isSaaS = ['saas', 'software-as-a-service', 'platform-as-a-service'].some(k => text.includes(k));
  const isSoftware = ['software', 'platform', 'cloud'].some(k => text.includes(k));
  if (isB2B && (isSaaS || isSoftware)) return 'high';
  if (isB2B || isSaaS || isSoftware) return 'medium';
  return 'low';
}

function ownershipCategory(c: CompanyTarget): 'bootstrapped' | 'angel' | 'vc' | 'unknown' {
  const text = `${c.ownership || ''} ${c.financing_status || ''} ${c.active_investors || ''}`.toLowerCase();
  if (['vc-backed', 'pe-backed', 'venture capital', 'series a', 'series b', 'series c', 'series d', 'institutional'].some(k => text.includes(k))) return 'vc';
  if (['bootstrapped', 'founder-led', 'family-owned', 'management-owned', 'self-funded'].some(k => text.includes(k))) return 'bootstrapped';
  if (['angel', 'seed', 'angel-backed', 'pre-seed'].some(k => text.includes(k))) return 'angel';
  return 'unknown';
}

function growthCategory(c: CompanyTarget): 'fast' | 'steady' | 'unknown' {
  const growthRate = c.revenue_growth_pct || c.pitchbook_growth_rate || 0;
  const oppScore = c.opportunity_score || 0;
  if (growthRate > 30 || (c.growth_signals && growthRate > 15) || oppScore > 70) return 'fast';
  if (growthRate > 0 || c.growth_signals || oppScore > 30) return 'steady';
  return 'unknown';
}

// ── Stage helpers ────────────────────────────────────────────────────────────

const PIPELINE_STAGES = ['Qualified', 'Contacted', 'Meeting', 'DD', 'Offer'] as const;
const STAGE_LABELS: Record<string, string> = {
  'Qualified': 'Qualified', 'Contacted': 'Contacted', 'Meeting': 'Meeting',
  'DD': 'Due Diligence', 'Offer': 'Offer', 'Won': 'Won', 'Lost': 'Lost', 'Engaged': 'Engaged',
};

function getNextStage(current: string): string | null {
  const idx = PIPELINE_STAGES.indexOf(current as any);
  if (idx === -1 || idx >= PIPELINE_STAGES.length - 1) return null;
  return PIPELINE_STAGES[idx + 1];
}

function stageColor(stage: string): string {
  const colors: Record<string, string> = {
    'Qualified': '#3b82f6', 'Contacted': '#8b5cf6', 'Meeting': '#f59e0b',
    'DD': '#ef4444', 'Offer': '#10b981', 'Won': '#059669', 'Lost': '#6b7280', 'Engaged': '#8b5cf6',
  };
  return colors[stage] || '#6b7280';
}

function stageBg(stage: string): string {
  const bgs: Record<string, string> = {
    'Qualified': '#eff6ff', 'Contacted': '#f5f3ff', 'Meeting': '#fffbeb',
    'DD': '#fef2f2', 'Offer': '#ecfdf5', 'Won': '#ecfdf5', 'Lost': '#f9fafb', 'Engaged': '#f5f3ff',
  };
  return bgs[stage] || '#f9fafb';
}

// ── Saved view type ────────────────────────────────────────────────────────

interface SavedView {
  id: string;
  name: string;
  filters: {
    filterSaaS: 'all' | 'high' | 'medium';
    filterOwnership: 'all' | 'bootstrapped' | 'angel' | 'vc';
    filterGrowth: 'all' | 'fast' | 'steady';
    filterStage: 'all' | string;
    searchQuery: string;
  };
}

// ── Component ───────────────────────────────────────────────────────────────

export default function Home() {
  const [pipeline, setPipeline] = useState<CompanyTarget[]>([]);
  const [searchQuery, setSearchQuery] = useState("");
  const [loading, setLoading] = useState(true);
  const [ingesting, setIngesting] = useState<string | null>(null);
  const [stats, setStats] = useState({ total: 0, qualified: 0 });

  // View toggle
  const [viewMode, setViewMode] = useState<'list' | 'kanban'>('kanban');

  // Filter states
  const [filterSaaS, setFilterSaaS] = useState<'all' | 'high' | 'medium'>('all');
  const [filterOwnership, setFilterOwnership] = useState<'all' | 'bootstrapped' | 'angel' | 'vc'>('all');
  const [filterGrowth, setFilterGrowth] = useState<'all' | 'fast' | 'steady'>('all');
  const [filterStage, setFilterStage] = useState<'all' | string>('all');

  // Deal lifecycle states
  const [updatingStatus, setUpdatingStatus] = useState<string | null>(null);
  const [noteModal, setNoteModal] = useState<{ company: string } | null>(null);
  const [noteText, setNoteText] = useState("");
  const [savingNote, setSavingNote] = useState(false);

  // Company drawer
  const [drawerCompany, setDrawerCompany] = useState<CompanyTarget | null>(null);

  // Saved views
  const [savedViews, setSavedViews] = useState<SavedView[]>([]);
  const [showSaveView, setShowSaveView] = useState(false);
  const [newViewName, setNewViewName] = useState('');
  const [activeViewId, setActiveViewId] = useState<string | null>(null);

  // Kanban drag state
  const [dragItem, setDragItem] = useState<string | null>(null);
  const [dragOverStage, setDragOverStage] = useState<string | null>(null);

  useEffect(() => {
    loadData();
    // Load saved views from localStorage
    try {
      const stored = localStorage.getItem('averroes_pipeline_views');
      if (stored) setSavedViews(JSON.parse(stored));
    } catch (e) {}
  }, []);

  async function loadData() {
    setLoading(true);
    try {
      const data = await dealApi.getPipeline();
      // Sort by Averroes Fit Score descending (scored companies first, then by score)
      data.sort((a, b) => (b.averroes_fit_score ?? -1) - (a.averroes_fit_score ?? -1));
      setPipeline(data);
      const uni = await dealApi.getUniverse();
      setStats({ total: uni.length, qualified: data.length });
    } catch (error) {
      console.error("Failed to load pipeline", error);
    } finally {
      setLoading(false);
    }
  }

  const handleIngest = async (type: 'marketplace' | 'conference' | 'ranking', name: string) => {
    setIngesting(name);
    try {
      if (type === 'marketplace') await dealApi.ingestMarketplace(name);
      else if (type === 'conference') await dealApi.ingestConference(name);
      else if (type === 'ranking') await dealApi.ingestRanking(name);
      await loadData();
    } catch (error) { alert(`Ingestion failed for ${name}`); }
    finally { setIngesting(null); }
  };

  // ── Deal lifecycle handlers ──────────────────────────────────────────────

  const handleAdvanceStage = async (companyName: string, newStatus: string) => {
    setUpdatingStatus(companyName);
    try {
      await dealApi.updateCompanyStatus(companyName, newStatus);
      await loadData();
    } catch (error) { alert(`Failed to update status: ${error}`); }
    finally { setUpdatingStatus(null); }
  };

  const handleMarkWon = async (companyName: string) => {
    if (!confirm(`Mark ${companyName} as Won?`)) return;
    await handleAdvanceStage(companyName, 'Won');
  };

  const handleMarkLost = async (companyName: string) => {
    if (!confirm(`Mark ${companyName} as Lost?`)) return;
    await handleAdvanceStage(companyName, 'Lost');
  };

  const handleRemoveFromPipeline = async (companyName: string) => {
    if (!confirm(`Remove ${companyName} from pipeline? It will be set to "Not a Fit" with score 0 in the Master Universe.`)) return;
    setUpdatingStatus(companyName);
    try {
      await dealApi.removeFromPipeline(companyName);
      await loadData();
    } catch (err: any) {
      alert(`Failed to remove: ${err.message}`);
    } finally {
      setUpdatingStatus(null);
    }
  };

  const handleSaveNote = async () => {
    if (!noteModal || !noteText.trim()) return;
    setSavingNote(true);
    try {
      await dealApi.addCompanyNote(noteModal.company, noteText.trim());
      setNoteModal(null);
      setNoteText("");
    } catch (error) { alert(`Failed to save note: ${error}`); }
    finally { setSavingNote(false); }
  };

  // ── Saved views ──────────────────────────────────────────────────────────

  const handleSaveView = () => {
    if (!newViewName.trim()) return;
    const view: SavedView = {
      id: Date.now().toString(),
      name: newViewName.trim(),
      filters: { filterSaaS, filterOwnership, filterGrowth, filterStage, searchQuery },
    };
    const updated = [...savedViews, view];
    setSavedViews(updated);
    localStorage.setItem('averroes_pipeline_views', JSON.stringify(updated));
    setNewViewName('');
    setShowSaveView(false);
    setActiveViewId(view.id);
  };

  const handleLoadView = (view: SavedView) => {
    setFilterSaaS(view.filters.filterSaaS);
    setFilterOwnership(view.filters.filterOwnership);
    setFilterGrowth(view.filters.filterGrowth);
    setFilterStage(view.filters.filterStage);
    setSearchQuery(view.filters.searchQuery);
    setActiveViewId(view.id);
  };

  const handleDeleteView = (id: string) => {
    const updated = savedViews.filter(v => v.id !== id);
    setSavedViews(updated);
    localStorage.setItem('averroes_pipeline_views', JSON.stringify(updated));
    if (activeViewId === id) setActiveViewId(null);
  };

  // ── Kanban drag handlers ─────────────────────────────────────────────────

  const handleDragStart = (companyName: string) => {
    setDragItem(companyName);
  };

  const handleDragOver = (e: React.DragEvent, stage: string) => {
    e.preventDefault();
    setDragOverStage(stage);
  };

  const handleDrop = async (e: React.DragEvent, newStage: string) => {
    e.preventDefault();
    setDragOverStage(null);
    if (!dragItem) return;
    const company = pipeline.find(c => c.name === dragItem);
    if (company && company.status !== newStage) {
      await handleAdvanceStage(dragItem, newStage);
    }
    setDragItem(null);
  };

  const handleDragEnd = () => {
    setDragItem(null);
    setDragOverStage(null);
  };

  const sanitizeContact = (val: string | undefined | null) => {
    if (!val) return "";
    const placeholders = ['System Override Required', 'Data Missing', 'Pending Activation', 'Unknown Founder', 'research@averroescapital.com'];
    return placeholders.includes(val) ? "" : val;
  };

  // Stage counts
  const stageCounts = PIPELINE_STAGES.reduce((acc, stage) => {
    acc[stage] = pipeline.filter(c => c.status === stage).length;
    return acc;
  }, {} as Record<string, number>);

  // Apply filters
  const filteredPipeline = pipeline.filter(c => {
    const q = searchQuery.toLowerCase();
    const matchesSearch = c.name.toLowerCase().includes(q) ||
      (c.sector && c.sector.toLowerCase().includes(q)) ||
      (c.description && c.description.toLowerCase().includes(q));
    let matchesSaaS = true;
    if (filterSaaS !== 'all') {
      const level = isSaaSOrB2B(c);
      if (filterSaaS === 'high') matchesSaaS = level === 'high';
      else if (filterSaaS === 'medium') matchesSaaS = level === 'high' || level === 'medium';
    }
    let matchesOwnership = true;
    if (filterOwnership !== 'all') matchesOwnership = ownershipCategory(c) === filterOwnership;
    let matchesGrowth = true;
    if (filterGrowth !== 'all') {
      const level = growthCategory(c);
      if (filterGrowth === 'fast') matchesGrowth = level === 'fast';
      else if (filterGrowth === 'steady') matchesGrowth = level === 'fast' || level === 'steady';
    }
    let matchesStage = true;
    if (filterStage !== 'all') matchesStage = c.status === filterStage;
    return matchesSearch && matchesSaaS && matchesOwnership && matchesGrowth && matchesStage;
  });

  const activeFilterCount = [filterSaaS !== 'all', filterOwnership !== 'all', filterGrowth !== 'all', filterStage !== 'all'].filter(Boolean).length;

  return (
    <div className="layout-wrapper">
      {/* Sidebar */}
      <aside className="sidebar">
        <div className="logo-section">
          <div className="logo">AVERROES<span>INTEL</span></div>
        </div>
        <nav className="sidebar-nav">
          <div className="nav-group">
            <span className="group-label">Intelligence</span>
            <Link href="/" className="nav-item active">
              <svg width="16" height="16" viewBox="0 0 16 16" fill="none"><path d="M2 3h5v5H2V3zm7 0h5v5H9V3zM2 10h5v4H2v-4zm7 0h5v4H9v-4z" fill="currentColor" opacity="0.7"/></svg>
              Deal Pipeline
            </Link>
            <Link href="/universe" className="nav-item">
              <svg width="16" height="16" viewBox="0 0 16 16" fill="none"><circle cx="8" cy="8" r="6" stroke="currentColor" strokeWidth="1.5" fill="none"/><path d="M2 8h12M8 2c-2 2-2 10 0 12M8 2c2 2 2 10 0 12" stroke="currentColor" strokeWidth="1" fill="none"/></svg>
              Master Universe
            </Link>
          </div>

          <div className="nav-group">
            <span className="group-label">Sourcing</span>
            {[
              { label: 'Acquire.com', type: 'marketplace' as const },
              { label: 'Flippa', type: 'marketplace' as const },
              { label: 'FT 1000', type: 'ranking' as const },
              { label: 'SaaStock Europe', type: 'conference' as const },
            ].map(src => (
              <button
                key={src.label}
                className={`agent-btn ${ingesting === src.label ? 'loading' : ''}`}
                onClick={() => handleIngest(src.type, src.label)}
                disabled={!!ingesting}
              >
                {src.label} {ingesting === src.label && '...'}
              </button>
            ))}
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
        {/* Page Header */}
        <header className="page-header">
          <div className="header-left">
            <h1>Deal Pipeline</h1>
            <p className="subtitle">{filteredPipeline.length} active deals across {Object.values(stageCounts).filter(v => v > 0).length} stages</p>
          </div>
          <div className="header-right">
            <div className="view-toggle">
              <button className={`toggle-btn ${viewMode === 'kanban' ? 'active' : ''}`} onClick={() => setViewMode('kanban')}>
                <svg width="16" height="16" viewBox="0 0 16 16" fill="none"><path d="M2 2h3v12H2zM6.5 2h3v8h-3zM11 2h3v10h-3z" fill="currentColor" opacity={viewMode === 'kanban' ? 1 : 0.4}/></svg>
                Board
              </button>
              <button className={`toggle-btn ${viewMode === 'list' ? 'active' : ''}`} onClick={() => setViewMode('list')}>
                <svg width="16" height="16" viewBox="0 0 16 16" fill="none"><path d="M2 3h12M2 6.5h12M2 10h12M2 13.5h12" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round" opacity={viewMode === 'list' ? 1 : 0.4}/></svg>
                List
              </button>
            </div>
            <div className="search-box">
              <svg width="16" height="16" viewBox="0 0 16 16" fill="none"><circle cx="7" cy="7" r="4.5" stroke="#94a3b8" strokeWidth="1.5"/><path d="M10.5 10.5L14 14" stroke="#94a3b8" strokeWidth="1.5" strokeLinecap="round"/></svg>
              <input
                type="text"
                placeholder="Search deals..."
                value={searchQuery}
                onChange={(e) => { setSearchQuery(e.target.value); setActiveViewId(null); }}
              />
            </div>
          </div>
        </header>

        {/* Stage funnel */}
        <section className="stage-funnel">
          {PIPELINE_STAGES.map((stage, i) => (
            <button
              key={stage}
              className={`stage-pill ${filterStage === stage ? 'active' : ''}`}
              style={{ '--stage-color': stageColor(stage) } as React.CSSProperties}
              onClick={() => { setFilterStage(filterStage === stage ? 'all' : stage); setActiveViewId(null); }}
            >
              <span className="stage-count">{stageCounts[stage] || 0}</span>
              <span className="stage-name">{STAGE_LABELS[stage]}</span>
              {i < PIPELINE_STAGES.length - 1 && <span className="stage-arrow">&rarr;</span>}
            </button>
          ))}
        </section>

        {/* Stats Row */}
        <section className="stats-row">
          <div className="stat-chip">
            <span className="stat-label">Universe</span>
            <span className="stat-value">{stats.total}</span>
          </div>
          <div className="stat-chip">
            <span className="stat-label">Pipeline</span>
            <span className="stat-value">{stats.qualified}</span>
          </div>
          <div className="stat-chip">
            <span className="stat-label">Showing</span>
            <span className="stat-value">{filteredPipeline.length}</span>
          </div>
          {activeFilterCount > 0 && (
            <div className="stat-chip filter-count">
              <span className="stat-label">Filters</span>
              <span className="stat-value">{activeFilterCount}</span>
            </div>
          )}
        </section>

        {/* Filter + Saved Views Bar */}
        <div className="filter-bar">
          <div className="filter-row">
            <div className="filter-group">
              <label className="filter-label">B2B SaaS Fit</label>
              <div className="filter-options">
                {[{ v: 'all', l: 'All' }, { v: 'high', l: 'B2B SaaS' }, { v: 'medium', l: 'B2B + Tech' }].map(o => (
                  <button key={o.v} className={`filter-btn ${filterSaaS === o.v ? 'active' : ''}`} onClick={() => { setFilterSaaS(o.v as any); setActiveViewId(null); }}>{o.l}</button>
                ))}
              </div>
            </div>
            <div className="filter-group">
              <label className="filter-label">Ownership</label>
              <div className="filter-options">
                {[{ v: 'all', l: 'All' }, { v: 'bootstrapped', l: 'Bootstrapped' }, { v: 'angel', l: 'Angel' }, { v: 'vc', l: 'VC-backed' }].map(o => (
                  <button key={o.v} className={`filter-btn ${filterOwnership === o.v ? 'active' : ''}`} onClick={() => { setFilterOwnership(o.v as any); setActiveViewId(null); }}>{o.l}</button>
                ))}
              </div>
            </div>
            <div className="filter-group">
              <label className="filter-label">Growth</label>
              <div className="filter-options">
                {[{ v: 'all', l: 'All' }, { v: 'fast', l: 'Fast Growth' }, { v: 'steady', l: 'Growing' }].map(o => (
                  <button key={o.v} className={`filter-btn ${filterGrowth === o.v ? 'active' : ''}`} onClick={() => { setFilterGrowth(o.v as any); setActiveViewId(null); }}>{o.l}</button>
                ))}
              </div>
            </div>
          </div>
          <div className="views-row">
            <div className="saved-views">
              {savedViews.map(view => (
                <div key={view.id} className={`view-chip ${activeViewId === view.id ? 'active' : ''}`}>
                  <button className="view-chip-btn" onClick={() => handleLoadView(view)}>{view.name}</button>
                  <button className="view-chip-delete" onClick={() => handleDeleteView(view.id)}>&times;</button>
                </div>
              ))}
            </div>
            <div className="view-actions">
              {activeFilterCount > 0 && (
                <>
                  <button className="save-view-btn" onClick={() => setShowSaveView(!showSaveView)}>
                    Save View
                  </button>
                  <button className="clear-btn" onClick={() => { setFilterSaaS('all'); setFilterOwnership('all'); setFilterGrowth('all'); setFilterStage('all'); setSearchQuery(''); setActiveViewId(null); }}>
                    Clear
                  </button>
                </>
              )}
            </div>
          </div>
          {showSaveView && (
            <div className="save-view-form">
              <input
                type="text"
                className="save-view-input"
                placeholder="View name (e.g. UK SaaS Bootstrapped)"
                value={newViewName}
                onChange={e => setNewViewName(e.target.value)}
                onKeyDown={e => e.key === 'Enter' && handleSaveView()}
                autoFocus
              />
              <button className="save-view-confirm" onClick={handleSaveView} disabled={!newViewName.trim()}>Save</button>
              <button className="save-view-cancel" onClick={() => { setShowSaveView(false); setNewViewName(''); }}>Cancel</button>
            </div>
          )}
        </div>

        {/* Main Content: Kanban or List */}
        {viewMode === 'kanban' ? (
          <section className="kanban-board">
            {PIPELINE_STAGES.map(stage => {
              const stageDeals = filteredPipeline.filter(c => c.status === stage);
              return (
                <div
                  key={stage}
                  className={`kanban-column ${dragOverStage === stage ? 'drag-over' : ''}`}
                  onDragOver={(e) => handleDragOver(e, stage)}
                  onDragLeave={() => setDragOverStage(null)}
                  onDrop={(e) => handleDrop(e, stage)}
                >
                  <div className="kanban-column-header" style={{ borderTopColor: stageColor(stage) }}>
                    <span className="kanban-column-title">{STAGE_LABELS[stage]}</span>
                    <span className="kanban-column-count">{stageDeals.length}</span>
                  </div>
                  <div className="kanban-cards">
                    {loading ? (
                      [1, 2].map(i => <div key={i} className="kanban-card skeleton-kanban" />)
                    ) : stageDeals.length > 0 ? (
                      stageDeals.map(company => {
                        const nextStage = getNextStage(company.status);
                        const isUpdating = updatingStatus === company.name;
                        const daysInStage = company.ingested_at
                          ? Math.floor((Date.now() - new Date(company.ingested_at).getTime()) / (1000 * 60 * 60 * 24))
                          : null;

                        return (
                          <div
                            key={company.name}
                            className={`kanban-card ${dragItem === company.name ? 'dragging' : ''}`}
                            draggable
                            onDragStart={() => handleDragStart(company.name)}
                            onDragEnd={handleDragEnd}
                          >
                            <div className="kc-header">
                              <button className="kc-name" onClick={() => setDrawerCompany(company)}>
                                {company.name}
                              </button>
                              {daysInStage !== null && (
                                <span className="kc-days">{daysInStage}d</span>
                              )}
                            </div>
                            <div className="kc-sector-row">
                              <p className="kc-sector">{company.sector || 'Tech'}</p>
                              {getRevenueBand(company) && (
                                <span className={`kc-band-badge band-${getRevenueBand(company)!.toLowerCase().replace(/\s+/g, '-')}`}>
                                  {getRevenueBand(company)}
                                </span>
                              )}
                              {company.averroes_fit_score != null && (
                                <span className={`kc-fit-badge ${company.averroes_fit_score >= 0.7 ? 'high' : company.averroes_fit_score >= 0.4 ? 'mid' : 'low'}`}>
                                  {Math.round(company.averroes_fit_score * 100)}
                                </span>
                              )}
                            </div>

                            <div className="kc-metrics">
                              {company.estimated_ebitda ? (
                                <span className="kc-metric">
                                  <span className="kc-metric-label">EBITDA</span>
                                  <span className="kc-metric-value">&pound;{company.estimated_ebitda}M</span>
                                </span>
                              ) : company.revenue_m ? (
                                <span className="kc-metric">
                                  <span className="kc-metric-label">Revenue</span>
                                  <span className="kc-metric-value">&pound;{company.revenue_m}M</span>
                                </span>
                              ) : company.revenue_estimate_m ? (
                                <span className="kc-metric">
                                  <span className="kc-metric-label">Revenue</span>
                                  <span className="kc-metric-value">~&pound;{company.revenue_estimate_m.toFixed(1)}M (est.)</span>
                                </span>
                              ) : null}
                              {company.employees ? (
                                <span className="kc-metric">
                                  <span className="kc-metric-label">Team</span>
                                  <span className="kc-metric-value">{company.employees}</span>
                                </span>
                              ) : null}
                            </div>

                            {sanitizeContact(company.contact_name) && (
                              <p className="kc-contact">{sanitizeContact(company.contact_name)}</p>
                            )}

                            <div className="kc-actions">
                              {nextStage && (
                                <button
                                  className="kc-advance"
                                  onClick={() => handleAdvanceStage(company.name, nextStage)}
                                  disabled={isUpdating}
                                  style={{ color: stageColor(nextStage) }}
                                >
                                  {isUpdating ? '...' : `${STAGE_LABELS[nextStage]}`} &rarr;
                                </button>
                              )}
                              <button className="kc-note" onClick={() => setNoteModal({ company: company.name })}>
                                + Note
                              </button>
                              <button className="kc-lost" onClick={() => handleMarkLost(company.name)} disabled={isUpdating}>
                                &times;
                              </button>
                              <button className="kc-remove" onClick={() => handleRemoveFromPipeline(company.name)} disabled={isUpdating} title="Remove from pipeline">
                                &#128465;
                              </button>
                            </div>
                          </div>
                        );
                      })
                    ) : (
                      <div className="kanban-empty">No deals</div>
                    )}
                  </div>
                </div>
              );
            })}
          </section>
        ) : (
          /* List View */
          <section className="list-section">
            <div className="section-header">
              <h3>Pipeline ({filteredPipeline.length})</h3>
              <button className="refresh-btn" onClick={loadData}>Refresh &nbsp;&#8635;</button>
            </div>

            <div className="cards-grid">
              {loading ? (
                [1, 2, 3, 4].map(i => <div key={i} className="card skeleton-card" />)
              ) : filteredPipeline.length > 0 ? (
                filteredPipeline.map((company, i) => {
                  const saasLevel = isSaaSOrB2B(company);
                  const ownLevel = ownershipCategory(company);
                  const growLevel = growthCategory(company);
                  const nextStage = getNextStage(company.status);
                  const isUpdating = updatingStatus === company.name;

                  return (
                    <div key={i} className="deal-card">
                      <div className="card-top">
                        <div className="stage-badge" style={{ background: stageColor(company.status) }}>
                          {STAGE_LABELS[company.status] || company.status}
                        </div>
                        <div className="tag-row">
                          <span className={`fit-tag tag-${saasLevel}`}>
                            {saasLevel === 'high' ? 'B2B SaaS' : saasLevel === 'medium' ? 'B2B Tech' : 'Tech'}
                          </span>
                          <span className={`fit-tag tag-own-${ownLevel}`}>
                            {ownLevel === 'bootstrapped' ? 'Bootstrapped' : ownLevel === 'angel' ? 'Angel' : ownLevel === 'vc' ? 'VC-backed' : '—'}
                          </span>
                          {growLevel !== 'unknown' && (
                            <span className={`fit-tag tag-grow-${growLevel}`}>
                              {growLevel === 'fast' ? 'Fast Growth' : 'Growing'}
                            </span>
                          )}
                        </div>
                        <span className="source-label">{company.source}</span>
                      </div>

                      <h4>
                        <button className="card-name-btn" onClick={() => setDrawerCompany(company)}>
                          {company.name}
                        </button>
                      </h4>
                      <p className="sector">{company.sector}</p>
                      <p className="description">{company.description}</p>

                      <div className="meta-info">
                        <div className="info-item"><span className="label">Region</span><span className="value">{company.region || company.hq_country || 'UK/Ireland'}</span></div>
                        <div className="info-item"><span className="label">Structure</span><span className="value">{company.ownership || '—'}</span></div>
                        {company.employees && <div className="info-item"><span className="label">Employees</span><span className="value">{company.employees}</span></div>}
                        {company.revenue_m && <div className="info-item"><span className="label">Revenue</span><span className="value">&pound;{company.revenue_m}M</span></div>}
                      </div>

                      <div className="founder-highlight">
                        <div className="founder-header"><span className="founder-label">Leadership</span></div>
                        <p className="founder-name" style={{ opacity: sanitizeContact(company.contact_name) ? 1 : 0.4 }}>
                          {sanitizeContact(company.contact_name) || 'Contact Hidden'}
                        </p>
                        <p className="founder-email" style={{ opacity: sanitizeContact(company.contact_email) ? 1 : 0.4 }}>
                          {sanitizeContact(company.contact_email) || 'Sync required for email'}
                        </p>
                      </div>

                      <div className="lifecycle-actions">
                        {nextStage && (
                          <button className="advance-btn" onClick={() => handleAdvanceStage(company.name, nextStage)} disabled={isUpdating}
                            style={{ '--btn-color': stageColor(nextStage) } as React.CSSProperties}
                          >
                            {isUpdating ? 'Updating...' : `Advance to ${STAGE_LABELS[nextStage]}`} &rarr;
                          </button>
                        )}
                        {company.status === 'Offer' && (
                          <button className="won-btn" onClick={() => handleMarkWon(company.name)} disabled={isUpdating}>Mark Won</button>
                        )}
                        <button className="lost-btn" onClick={() => handleMarkLost(company.name)} disabled={isUpdating}>Lost</button>
                        <button className="remove-btn" onClick={() => handleRemoveFromPipeline(company.name)} disabled={isUpdating}>Remove</button>
                      </div>

                      <div className="card-footer">
                        <div className="footer-left">
                          <button className="note-btn" onClick={() => setNoteModal({ company: company.name })}>+ Note</button>
                          <button className="activity-btn" onClick={() => setDrawerCompany(company)}>Details</button>
                        </div>
                        <a href={company.website} target="_blank" rel="noreferrer" className="view-link">Visit &nearr;</a>
                      </div>
                    </div>
                  );
                })
              ) : (
                <div className="empty-state">
                  <p>{activeFilterCount > 0 ? 'No companies match your current filters.' : 'No qualified companies yet. Run a sourcing agent and SmartFill from the Master Universe.'}</p>
                </div>
              )}
            </div>
          </section>
        )}
      </main>

      {/* Company Drawer */}
      <CompanyDrawer
        company={drawerCompany}
        onClose={() => setDrawerCompany(null)}
        onStatusChange={async (name, status) => {
          await handleAdvanceStage(name, status);
        }}
      />

      {/* Note Modal */}
      {noteModal && (
        <div className="modal-overlay" onClick={() => { setNoteModal(null); setNoteText(""); }}>
          <div className="modal-content" onClick={e => e.stopPropagation()}>
            <div className="modal-header">
              <h3>Add Note — {noteModal.company}</h3>
              <button className="modal-close" onClick={() => { setNoteModal(null); setNoteText(""); }}>&times;</button>
            </div>
            <div className="modal-body">
              <textarea
                className="note-textarea"
                placeholder="Meeting notes, call summary, key observations..."
                value={noteText}
                onChange={e => setNoteText(e.target.value)}
                rows={5}
                autoFocus
              />
            </div>
            <div className="modal-footer">
              <button className="modal-cancel" onClick={() => { setNoteModal(null); setNoteText(""); }}>Cancel</button>
              <button className="modal-save" onClick={handleSaveNote} disabled={savingNote || !noteText.trim()}>
                {savingNote ? 'Saving...' : 'Save Note'}
              </button>
            </div>
          </div>
        </div>
      )}

      <style jsx>{`
        /* ── Layout ─────────────────────────────────────────────── */
        .layout-wrapper { display: flex; min-height: 100vh; background: #f8fafc; }

        /* ── Sidebar ────────────────────────────────────────────── */
        .sidebar {
          width: 260px;
          background: #fff;
          border-right: 1px solid #e2e8f0;
          display: flex;
          flex-direction: column;
          position: fixed;
          height: 100vh;
          z-index: 100;
        }

        .logo-section { padding: 2rem 1.75rem 1.5rem; }
        .logo { font-size: 1.25rem; font-weight: 900; letter-spacing: 0.08em; color: #0f172a; }
        .logo span { color: #2563eb; }

        .sidebar-nav { flex: 1; padding: 0 1.25rem; overflow-y: auto; }
        .nav-group { margin-bottom: 2rem; display: flex; flex-direction: column; gap: 0.25rem; }
        .group-label { font-size: 0.65rem; text-transform: uppercase; letter-spacing: 0.15em; color: #94a3b8; padding-left: 0.75rem; margin-bottom: 0.5rem; font-weight: 700; }

        .nav-item {
          display: flex;
          align-items: center;
          gap: 0.6rem;
          padding: 0.65rem 0.75rem;
          color: #64748b;
          border-radius: 8px;
          font-weight: 600;
          font-size: 0.88rem;
          transition: all 0.15s;
        }
        .nav-item:hover { color: #2563eb; background: #eff6ff; }
        .nav-item.active { color: #2563eb; background: #eff6ff; }

        .agent-btn {
          background: transparent;
          border: 1px solid #e2e8f0;
          color: #64748b;
          padding: 0.55rem 0.75rem;
          border-radius: 8px;
          text-align: left;
          font-size: 0.8rem;
          font-weight: 600;
          cursor: pointer;
          transition: all 0.15s;
        }
        .agent-btn:hover:not(:disabled) { border-color: #2563eb; color: #2563eb; }
        .agent-btn.loading { opacity: 0.5; cursor: wait; }

        .sidebar-footer { padding: 1.25rem; border-top: 1px solid #e2e8f0; }
        .user-profile { display: flex; align-items: center; gap: 0.65rem; }
        .avatar { width: 36px; height: 36px; background: #eff6ff; color: #2563eb; border-radius: 50%; display: flex; align-items: center; justify-content: center; font-weight: 800; font-size: 0.8rem; }
        .user-info { display: flex; flex-direction: column; }
        .user-name { font-size: 0.85rem; font-weight: 700; color: #0f172a; }
        .user-role { font-size: 0.68rem; color: #94a3b8; }

        /* ── Main ───────────────────────────────────────────────── */
        .main-content {
          margin-left: 260px;
          flex: 1;
          padding: 2rem 2.5rem;
          width: calc(100% - 260px);
        }

        .page-header {
          display: flex;
          justify-content: space-between;
          align-items: flex-start;
          margin-bottom: 1.75rem;
        }

        h1 { font-size: 1.75rem; font-weight: 800; color: #0f172a; margin-bottom: 0.25rem; letter-spacing: -0.02em; }
        .subtitle { color: #94a3b8; font-size: 0.88rem; font-weight: 500; margin: 0; }

        .header-right { display: flex; align-items: center; gap: 1rem; }

        .view-toggle {
          display: flex;
          background: #f1f5f9;
          border-radius: 8px;
          padding: 0.2rem;
          gap: 0.15rem;
        }

        .toggle-btn {
          display: flex;
          align-items: center;
          gap: 0.4rem;
          padding: 0.4rem 0.75rem;
          background: transparent;
          border: none;
          border-radius: 6px;
          font-size: 0.78rem;
          font-weight: 600;
          color: #94a3b8;
          cursor: pointer;
          transition: all 0.15s;
        }
        .toggle-btn.active { background: #fff; color: #0f172a; box-shadow: 0 1px 3px rgba(0,0,0,0.08); }

        .search-box {
          display: flex;
          align-items: center;
          padding: 0.6rem 1rem;
          gap: 0.6rem;
          background: #fff;
          border: 1.5px solid #e2e8f0;
          border-radius: 8px;
          width: 280px;
        }
        .search-box input { background: transparent; border: none; color: #0f172a; width: 100%; outline: none; font-size: 0.88rem; }

        /* ── Stage funnel ────────────────────────────────────────── */
        .stage-funnel {
          display: flex;
          gap: 0.4rem;
          margin-bottom: 1.25rem;
          flex-wrap: wrap;
        }

        .stage-pill {
          display: flex;
          align-items: center;
          gap: 0.4rem;
          padding: 0.45rem 1rem;
          border: 1.5px solid #e2e8f0;
          border-radius: 20px;
          background: #fff;
          cursor: pointer;
          transition: all 0.15s;
          font-size: 0.82rem;
        }
        .stage-pill:hover { border-color: var(--stage-color); }
        .stage-pill.active { background: var(--stage-color); border-color: var(--stage-color); color: white; }
        .stage-pill.active .stage-count, .stage-pill.active .stage-name { color: white; }
        .stage-count { font-weight: 800; font-size: 1rem; color: #0f172a; }
        .stage-name { font-weight: 600; color: #64748b; }
        .stage-arrow { color: #cbd5e1; font-size: 0.85rem; margin-left: 0.15rem; }
        .stage-pill.active .stage-arrow { color: rgba(255,255,255,0.5); }

        /* ── Stats Row ─────────────────────────────────────────── */
        .stats-row {
          display: flex;
          gap: 0.75rem;
          margin-bottom: 1.25rem;
        }

        .stat-chip {
          display: flex;
          align-items: center;
          gap: 0.5rem;
          padding: 0.5rem 1rem;
          background: #fff;
          border: 1px solid #e2e8f0;
          border-radius: 8px;
        }
        .stat-chip.filter-count { border-color: #2563eb; background: #eff6ff; }
        .stat-label { font-size: 0.7rem; color: #94a3b8; font-weight: 600; text-transform: uppercase; letter-spacing: 0.05em; }
        .stat-value { font-size: 1.1rem; font-weight: 800; color: #0f172a; }
        .filter-count .stat-value { color: #2563eb; }

        /* ── Filter Bar ────────────────────────────────────────── */
        .filter-bar {
          background: #fff;
          border: 1px solid #e2e8f0;
          border-radius: 10px;
          padding: 1.25rem 1.5rem;
          margin-bottom: 1.5rem;
        }

        .filter-row {
          display: flex;
          gap: 2rem;
          flex-wrap: wrap;
          margin-bottom: 0.75rem;
        }

        .filter-group { display: flex; flex-direction: column; gap: 0.35rem; }
        .filter-label { font-size: 0.65rem; text-transform: uppercase; letter-spacing: 0.1em; color: #94a3b8; font-weight: 700; }
        .filter-options { display: flex; gap: 0.25rem; }
        .filter-btn {
          padding: 0.3rem 0.7rem;
          border: 1px solid #e2e8f0;
          border-radius: 6px;
          background: transparent;
          color: #64748b;
          font-size: 0.75rem;
          font-weight: 600;
          cursor: pointer;
          transition: all 0.15s;
          white-space: nowrap;
        }
        .filter-btn:hover { border-color: #2563eb; color: #2563eb; }
        .filter-btn.active { background: #2563eb; color: #fff; border-color: #2563eb; }

        .views-row {
          display: flex;
          justify-content: space-between;
          align-items: center;
          border-top: 1px solid #f1f5f9;
          padding-top: 0.75rem;
        }

        .saved-views { display: flex; gap: 0.35rem; flex-wrap: wrap; }

        .view-chip {
          display: flex;
          align-items: center;
          gap: 0;
          border: 1px solid #e2e8f0;
          border-radius: 6px;
          overflow: hidden;
          transition: all 0.15s;
        }
        .view-chip.active { border-color: #2563eb; background: #eff6ff; }

        .view-chip-btn {
          background: none;
          border: none;
          padding: 0.25rem 0.6rem;
          font-size: 0.72rem;
          font-weight: 600;
          color: #64748b;
          cursor: pointer;
        }
        .view-chip.active .view-chip-btn { color: #2563eb; }
        .view-chip-btn:hover { color: #2563eb; }

        .view-chip-delete {
          background: none;
          border: none;
          border-left: 1px solid #e2e8f0;
          padding: 0.25rem 0.4rem;
          font-size: 0.8rem;
          color: #cbd5e1;
          cursor: pointer;
        }
        .view-chip-delete:hover { color: #ef4444; }

        .view-actions { display: flex; gap: 0.5rem; align-items: center; }

        .save-view-btn {
          background: none;
          border: 1px solid #2563eb;
          color: #2563eb;
          padding: 0.25rem 0.75rem;
          border-radius: 6px;
          font-size: 0.72rem;
          font-weight: 700;
          cursor: pointer;
        }
        .save-view-btn:hover { background: #eff6ff; }

        .clear-btn {
          background: none;
          border: none;
          color: #94a3b8;
          font-size: 0.72rem;
          cursor: pointer;
          text-decoration: underline;
        }
        .clear-btn:hover { color: #64748b; }

        .save-view-form {
          display: flex;
          gap: 0.5rem;
          align-items: center;
          margin-top: 0.75rem;
          padding-top: 0.75rem;
          border-top: 1px solid #f1f5f9;
        }

        .save-view-input {
          flex: 1;
          padding: 0.4rem 0.75rem;
          border: 1.5px solid #e2e8f0;
          border-radius: 6px;
          font-size: 0.82rem;
          outline: none;
        }
        .save-view-input:focus { border-color: #2563eb; }

        .save-view-confirm {
          padding: 0.4rem 1rem;
          background: #2563eb;
          color: #fff;
          border: none;
          border-radius: 6px;
          font-size: 0.78rem;
          font-weight: 700;
          cursor: pointer;
        }
        .save-view-confirm:disabled { opacity: 0.4; }

        .save-view-cancel {
          padding: 0.4rem 0.75rem;
          background: none;
          border: 1px solid #e2e8f0;
          border-radius: 6px;
          font-size: 0.78rem;
          color: #64748b;
          cursor: pointer;
        }

        /* ── Kanban Board ──────────────────────────────────────── */
        .kanban-board {
          display: grid;
          grid-template-columns: repeat(${PIPELINE_STAGES.length}, 1fr);
          gap: 1rem;
          min-height: 60vh;
        }

        .kanban-column {
          background: #f1f5f9;
          border-radius: 10px;
          display: flex;
          flex-direction: column;
          min-height: 400px;
          transition: all 0.15s;
        }
        .kanban-column.drag-over { background: #e0f2fe; outline: 2px dashed #3b82f6; }

        .kanban-column-header {
          padding: 0.85rem 1rem;
          border-top: 3px solid;
          border-radius: 10px 10px 0 0;
          display: flex;
          justify-content: space-between;
          align-items: center;
        }

        .kanban-column-title { font-size: 0.78rem; font-weight: 700; color: #0f172a; text-transform: uppercase; letter-spacing: 0.05em; }
        .kanban-column-count { font-size: 0.72rem; font-weight: 700; color: #94a3b8; background: #fff; padding: 0.15rem 0.5rem; border-radius: 10px; }

        .kanban-cards {
          flex: 1;
          padding: 0.5rem;
          display: flex;
          flex-direction: column;
          gap: 0.5rem;
          overflow-y: auto;
        }

        .kanban-card {
          background: #fff;
          border: 1px solid #e2e8f0;
          border-radius: 8px;
          padding: 0.85rem;
          cursor: grab;
          transition: all 0.15s;
          user-select: none;
        }
        .kanban-card:hover { box-shadow: 0 2px 8px rgba(0,0,0,0.06); border-color: #cbd5e1; }
        .kanban-card.dragging { opacity: 0.5; transform: rotate(2deg); }

        .skeleton-kanban { height: 100px; background: #e2e8f0; animation: pulse 1.5s infinite; }
        @keyframes pulse { 0%, 100% { opacity: 1; } 50% { opacity: 0.5; } }

        .kc-header { display: flex; justify-content: space-between; align-items: flex-start; margin-bottom: 0.25rem; }
        .kc-name {
          font-size: 0.88rem;
          font-weight: 700;
          color: #0f172a;
          background: none;
          border: none;
          padding: 0;
          cursor: pointer;
          text-align: left;
          line-height: 1.2;
        }
        .kc-name:hover { color: #2563eb; }
        .kc-days { font-size: 0.65rem; color: #94a3b8; font-weight: 600; background: #f8fafc; padding: 0.1rem 0.4rem; border-radius: 4px; flex-shrink: 0; }

        .kc-sector-row { display: flex; align-items: center; justify-content: space-between; margin-bottom: 0.5rem; }
        .kc-sector { font-size: 0.72rem; color: #64748b; font-weight: 600; margin: 0; text-transform: uppercase; }
        .kc-fit-badge { font-size: 0.65rem; font-weight: 800; padding: 0.1rem 0.4rem; border-radius: 4px; color: white; }
        .kc-fit-badge.high { background: #16a34a; }
        .kc-fit-badge.mid { background: #d97706; }
        .kc-fit-badge.low { background: #dc2626; }
        .kc-band-badge { font-size: 0.58rem; font-weight: 800; padding: 0.1rem 0.4rem; border-radius: 4px; text-transform: uppercase; letter-spacing: 0.04em; white-space: nowrap; }
        .kc-band-badge.band-target-band { background: #dcfce7; color: #166534; }
        .kc-band-badge.band-too-early { background: #fef3c7; color: #92400e; }
        .kc-band-badge.band-too-large { background: #fef2f2; color: #dc2626; }

        .kc-metrics { display: flex; gap: 0.75rem; margin-bottom: 0.5rem; }
        .kc-metric { display: flex; flex-direction: column; }
        .kc-metric-label { font-size: 0.6rem; color: #94a3b8; font-weight: 600; text-transform: uppercase; }
        .kc-metric-value { font-size: 0.82rem; font-weight: 700; color: #0f172a; }

        .kc-contact { font-size: 0.72rem; color: #64748b; margin: 0 0 0.5rem; }

        .kc-actions { display: flex; gap: 0.35rem; align-items: center; margin-top: 0.25rem; }
        .kc-advance {
          flex: 1;
          background: none;
          border: none;
          font-size: 0.72rem;
          font-weight: 700;
          cursor: pointer;
          text-align: left;
          padding: 0.2rem 0;
        }
        .kc-advance:hover { text-decoration: underline; }
        .kc-advance:disabled { opacity: 0.4; cursor: wait; }

        .kc-note {
          background: none;
          border: 1px solid #e2e8f0;
          color: #94a3b8;
          padding: 0.15rem 0.4rem;
          border-radius: 4px;
          font-size: 0.65rem;
          font-weight: 600;
          cursor: pointer;
        }
        .kc-note:hover { border-color: #2563eb; color: #2563eb; }

        .kc-lost {
          background: none;
          border: 1px solid #e2e8f0;
          color: #cbd5e1;
          padding: 0.15rem 0.35rem;
          border-radius: 4px;
          font-size: 0.75rem;
          cursor: pointer;
          line-height: 1;
        }
        .kc-lost:hover { border-color: #ef4444; color: #ef4444; }
        .kc-remove {
          background: none; border: 1px solid #e2e8f0; color: #cbd5e1;
          border-radius: 4px; padding: 0.2rem 0.35rem; font-size: 0.7rem; cursor: pointer;
        }
        .kc-remove:hover { border-color: #dc2626; color: #dc2626; background: #fef2f2; }

        .kanban-empty {
          text-align: center;
          padding: 2rem 0.5rem;
          color: #cbd5e1;
          font-size: 0.78rem;
        }

        /* ── List View ─────────────────────────────────────────── */
        .list-section { }
        .section-header { display: flex; justify-content: space-between; align-items: center; margin-bottom: 1.25rem; }
        .section-header h3 { font-size: 1.1rem; color: #0f172a; margin: 0; }

        .refresh-btn {
          background: none;
          border: 1px solid #e2e8f0;
          color: #64748b;
          padding: 0.4rem 0.85rem;
          border-radius: 6px;
          font-size: 0.78rem;
          font-weight: 600;
          cursor: pointer;
        }
        .refresh-btn:hover { border-color: #2563eb; color: #2563eb; }

        .cards-grid { display: grid; grid-template-columns: repeat(auto-fill, minmax(340px, 1fr)); gap: 1.25rem; }

        .deal-card {
          display: flex;
          flex-direction: column;
          background: #fff;
          padding: 1.5rem;
          border-radius: 10px;
          border: 1px solid #e2e8f0;
          transition: all 0.15s;
        }
        .deal-card:hover { border-color: #cbd5e1; box-shadow: 0 4px 12px rgba(0,0,0,0.04); }

        .card-top { display: flex; justify-content: space-between; align-items: flex-start; margin-bottom: 0.75rem; gap: 0.5rem; flex-wrap: wrap; }

        .stage-badge {
          font-size: 0.65rem; font-weight: 800; text-transform: uppercase;
          padding: 0.2rem 0.6rem; border-radius: 4px; color: white; letter-spacing: 0.05em; flex-shrink: 0;
        }

        .tag-row { display: flex; gap: 0.3rem; flex-wrap: wrap; }
        .fit-tag { font-size: 0.58rem; font-weight: 800; text-transform: uppercase; padding: 0.12rem 0.4rem; border-radius: 3px; letter-spacing: 0.03em; }
        .tag-high { background: #dcfce7; color: #166534; }
        .tag-medium { background: #fef9c3; color: #854d0e; }
        .tag-low { background: #f3f4f6; color: #6b7280; }
        .tag-own-bootstrapped { background: #dbeafe; color: #1e40af; }
        .tag-own-angel { background: #ede9fe; color: #5b21b6; }
        .tag-own-vc { background: #fef3c7; color: #92400e; }
        .tag-own-unknown { background: #f3f4f6; color: #6b7280; }
        .tag-grow-fast { background: #d1fae5; color: #065f46; }
        .tag-grow-steady { background: #e0f2fe; color: #0c4a6e; }
        .source-label { font-size: 0.62rem; color: #94a3b8; text-transform: uppercase; letter-spacing: 0.05em; }

        h4 { font-size: 1.2rem; margin-bottom: 0.35rem; }
        .card-name-btn {
          background: none;
          border: none;
          padding: 0;
          font: inherit;
          color: #0f172a;
          cursor: pointer;
          text-align: left;
        }
        .card-name-btn:hover { color: #2563eb; }

        .sector { color: #2563eb; font-size: 0.78rem; font-weight: 700; margin-bottom: 0.75rem; text-transform: uppercase; }
        .description { color: #64748b; font-size: 0.88rem; margin-bottom: 1.25rem; line-height: 1.5; display: -webkit-box; -webkit-line-clamp: 3; -webkit-box-orient: vertical; overflow: hidden; }

        .meta-info { display: grid; grid-template-columns: 1fr 1fr; gap: 0.75rem; margin-bottom: 1.25rem; padding-bottom: 1.25rem; border-bottom: 1px solid #f1f5f9; }
        .info-item { display: flex; flex-direction: column; gap: 0.15rem; }
        .info-item .label { font-size: 0.62rem; color: #94a3b8; text-transform: uppercase; }
        .info-item .value { font-size: 0.85rem; color: #0f172a; font-weight: 600; }

        .founder-highlight { padding: 1rem; margin-bottom: 1.25rem; background: #f8fafc; border-radius: 8px; border: 1px solid #f1f5f9; }
        .founder-header { margin-bottom: 0.5rem; }
        .founder-label { font-size: 0.65rem; text-transform: uppercase; letter-spacing: 0.05em; color: #d97706; font-weight: 800; }
        .founder-name { font-weight: 700; color: #0f172a; margin-bottom: 0.15rem; font-size: 0.9rem; }
        .founder-email { font-size: 0.78rem; color: #64748b; }

        .lifecycle-actions { display: flex; gap: 0.4rem; margin-bottom: 0.75rem; flex-wrap: wrap; }

        .advance-btn {
          flex: 1;
          padding: 0.5rem 0.85rem;
          background: var(--btn-color, #2563eb);
          color: white;
          border: none;
          border-radius: 6px;
          font-size: 0.75rem;
          font-weight: 700;
          cursor: pointer;
          transition: all 0.15s;
        }
        .advance-btn:hover:not(:disabled) { opacity: 0.85; }
        .advance-btn:disabled { opacity: 0.5; cursor: wait; }

        .won-btn { padding: 0.5rem 0.85rem; background: #059669; color: white; border: none; border-radius: 6px; font-size: 0.75rem; font-weight: 700; cursor: pointer; }
        .won-btn:hover:not(:disabled) { background: #047857; }

        .lost-btn {
          padding: 0.5rem 0.65rem;
          background: transparent;
          color: #94a3b8;
          border: 1px solid #e2e8f0;
          border-radius: 6px;
          font-size: 0.72rem;
          font-weight: 600;
          cursor: pointer;
        }
        .lost-btn:hover:not(:disabled) { border-color: #ef4444; color: #ef4444; }

        .remove-btn {
          padding: 0.5rem 0.65rem;
          background: transparent;
          border: 1px solid #e2e8f0;
          border-radius: 6px;
          font-size: 0.72rem;
          font-weight: 700;
          color: #94a3b8;
          cursor: pointer;
        }
        .remove-btn:hover:not(:disabled) { border-color: #dc2626; color: #dc2626; background: #fef2f2; }

        .card-footer {
          display: flex;
          justify-content: space-between;
          align-items: center;
          margin-top: auto;
          padding-top: 0.65rem;
          border-top: 1px solid #f1f5f9;
        }

        .footer-left { display: flex; gap: 0.4rem; }
        .note-btn, .activity-btn {
          background: transparent;
          border: 1px solid #e2e8f0;
          color: #64748b;
          padding: 0.3rem 0.65rem;
          border-radius: 4px;
          font-size: 0.7rem;
          font-weight: 600;
          cursor: pointer;
        }
        .note-btn:hover { border-color: #2563eb; color: #2563eb; }
        .activity-btn:hover { border-color: #d97706; color: #d97706; }
        .view-link { font-size: 0.8rem; font-weight: 700; color: #2563eb; }

        .empty-state { grid-column: 1 / -1; padding: 4rem; text-align: center; color: #94a3b8; background: #fff; border-radius: 10px; border: 1px solid #e2e8f0; }
        .skeleton-card { height: 350px; background: #f1f5f9; border-radius: 10px; animation: pulse 1.5s infinite; }

        /* ── Modals ────────────────────────────────────────────── */
        .modal-overlay {
          position: fixed; inset: 0;
          background: rgba(15, 23, 42, 0.3);
          display: flex; align-items: center; justify-content: center;
          z-index: 1000;
        }

        .modal-content {
          background: #fff;
          border-radius: 12px;
          width: 90%; max-width: 520px;
          max-height: 85vh;
          display: flex; flex-direction: column;
          box-shadow: 0 20px 60px rgba(0,0,0,0.12);
        }

        .modal-header { display: flex; justify-content: space-between; align-items: center; padding: 1.25rem 1.5rem; border-bottom: 1px solid #e2e8f0; }
        .modal-header h3 { font-size: 1rem; margin: 0; color: #0f172a; }
        .modal-close { background: none; border: none; font-size: 1.4rem; color: #94a3b8; cursor: pointer; }
        .modal-body { padding: 1.5rem; flex: 1; overflow-y: auto; }
        .modal-footer { display: flex; justify-content: flex-end; gap: 0.5rem; padding: 1rem 1.5rem; border-top: 1px solid #f1f5f9; }

        .modal-cancel { padding: 0.4rem 1rem; background: transparent; border: 1px solid #e2e8f0; border-radius: 6px; color: #64748b; font-weight: 600; cursor: pointer; font-size: 0.82rem; }
        .modal-save { padding: 0.4rem 1rem; background: #2563eb; border: none; border-radius: 6px; color: white; font-weight: 700; cursor: pointer; font-size: 0.82rem; }
        .modal-save:disabled { opacity: 0.4; cursor: not-allowed; }

        .note-textarea {
          width: 100%; padding: 0.85rem;
          border: 1.5px solid #e2e8f0; border-radius: 8px;
          font-size: 0.9rem; font-family: inherit;
          resize: vertical; outline: none; background: #f8fafc; color: #0f172a;
        }
        .note-textarea:focus { border-color: #2563eb; background: #fff; }

        /* ── Responsive ────────────────────────────────────────── */
        @media (max-width: 1024px) {
          .sidebar { width: 72px; }
          .sidebar .logo span, .sidebar .group-label, .sidebar .nav-item span:not(svg),
          .sidebar .agent-btn, .sidebar .user-info { display: none !important; }
          .sidebar .logo { text-align: center; padding: 1.5rem 0; font-size: 0.9rem; }
          .main-content { margin-left: 72px; width: calc(100% - 72px); }
          .kanban-board { grid-template-columns: repeat(3, 1fr); }
        }

        @media (max-width: 768px) {
          .page-header { flex-direction: column; gap: 1rem; }
          .header-right { flex-direction: column; width: 100%; }
          .search-box { width: 100%; }
          .kanban-board { grid-template-columns: 1fr; }
        }
      `}</style>
    </div>
  );
}
