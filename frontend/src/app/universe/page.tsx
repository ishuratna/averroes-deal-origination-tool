'use client';

import { useEffect, useState } from "react";
import Link from 'next/link';
import { CompanyTarget } from "../../types";
import { dealApi } from "../../services/api";
import CompanyDrawer from "../../components/CompanyDrawer";

// ── Saved view type ─────────────────────────────────────────────────────────

interface SavedView {
  id: string;
  name: string;
  filters: { vertical: string; region: string; status: string; searchQuery: string; };
}

export default function Universe() {
  const [universe, setUniverse] = useState<CompanyTarget[]>([]);
  const [loading, setLoading] = useState(true);
  const [searchQuery, setSearchQuery] = useState("");
  const [ingesting, setIngesting] = useState<string | null>(null);
  const [smartFilling, setSmartFilling] = useState<string | null>(null);
  const [smartFillResult, setSmartFillResult] = useState<any | null>(null);
  const [outreachTarget, setOutreachTarget] = useState<any | null>(null);
  const [outreachDraft, setOutreachDraft] = useState<{to: string; subject: string; body: string; company: string} | null>(null);
  const [outreachLoading, setOutreachLoading] = useState(false);
  const [outreachSent, setOutreachSent] = useState(false);

  // Drawer
  const [drawerCompany, setDrawerCompany] = useState<CompanyTarget | null>(null);

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

  const handleDirectoryScrape = async (sourceName: string) => {
    setIngesting(sourceName);
    try {
      const res = await dealApi.ingestDirectory(sourceName);
      alert(`Found ${res.count} companies from ${sourceName}. ${res.total_in_universe || ''} total in universe.`);
      await loadData();
    } catch (error) { alert(`Scraping failed for ${sourceName}`); }
    finally { setIngesting(null); }
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

  const formatDate = (dateStr?: string) => {
    if (!dateStr) return '—';
    return new Date(dateStr).toLocaleDateString('en-GB', { day: '2-digit', month: 'short', year: 'numeric' });
  };

  const openOutreach = async (company: any) => {
    setOutreachTarget(company);
    setOutreachDraft(null);
    setOutreachSent(false);
    setOutreachLoading(true);
    try {
      const draft = await dealApi.draftOutreach(company.name);
      setOutreachDraft({
        to: draft.to || company.contact_email || '',
        subject: draft.subject || '',
        body: draft.body || '',
        company: company.name,
      });
    } catch (err: any) {
      alert(`Failed to generate draft: ${err.message}`);
      setOutreachTarget(null);
    } finally { setOutreachLoading(false); }
  };

  const handleSendOutreach = () => {
    if (!outreachDraft) return;
    const gmailUrl = `https://mail.google.com/mail/?view=cm&fs=1&to=${encodeURIComponent(outreachDraft.to)}&su=${encodeURIComponent(outreachDraft.subject)}&body=${encodeURIComponent(outreachDraft.body)}`;
    window.open(gmailUrl, '_blank');
    setOutreachSent(true);
  };

  const handleCopyDraft = () => {
    if (!outreachDraft) return;
    const text = `To: ${outreachDraft.to}\nSubject: ${outreachDraft.subject}\n\n${outreachDraft.body}`;
    navigator.clipboard.writeText(text);
    alert('Draft copied to clipboard!');
  };

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
              </div>
            </div>
            <div className="modal-footer">
              <button className="modal-ok-btn" onClick={() => setSmartFillResult(null)}>OK</button>
            </div>
          </div>
        </div>
      )}

      {/* Outreach Modal */}
      {outreachTarget && (
        <div className="modal-overlay" onClick={() => { setOutreachTarget(null); setOutreachDraft(null); }}>
          <div className="modal-content outreach-modal" onClick={(e) => e.stopPropagation()}>
            <div className="modal-header">
              <h3>Outreach — {outreachTarget.name}</h3>
              <button className="modal-close" onClick={() => { setOutreachTarget(null); setOutreachDraft(null); }}>&times;</button>
            </div>
            <div className="modal-body">
              {outreachLoading ? (
                <div className="outreach-loading">
                  <div className="spinner"></div>
                  <p>Drafting personalised email with AI...</p>
                  <p className="loading-sub">Researching {outreachTarget.name} to craft the perfect intro</p>
                </div>
              ) : outreachSent ? (
                <div className="outreach-sent">
                  <div className="sent-icon">&#10003;</div>
                  <h4>Draft Opened in Gmail</h4>
                  <p>Your outreach to <strong>{outreachDraft?.to}</strong> is ready in Gmail.</p>
                  <p className="sent-sub">Review and hit Send in the Gmail compose window.</p>
                </div>
              ) : outreachDraft ? (
                <div className="outreach-form">
                  <div className="form-row">
                    <label>To</label>
                    <input type="email" value={outreachDraft.to} onChange={(e) => setOutreachDraft({...outreachDraft, to: e.target.value})} />
                  </div>
                  <div className="form-row">
                    <label>Subject</label>
                    <input type="text" value={outreachDraft.subject} onChange={(e) => setOutreachDraft({...outreachDraft, subject: e.target.value})} />
                  </div>
                  <div className="form-row">
                    <label>Body</label>
                    <textarea rows={12} value={outreachDraft.body} onChange={(e) => setOutreachDraft({...outreachDraft, body: e.target.value})} />
                  </div>
                  <div className="form-row from-row">
                    <span className="from-label">From: Beatrice Carrara &lt;iratna@averroescapital.com&gt;</span>
                  </div>
                </div>
              ) : null}
            </div>
            <div className="modal-footer">
              {outreachSent ? (
                <button className="modal-ok-btn" onClick={() => { setOutreachTarget(null); setOutreachDraft(null); }}>Done</button>
              ) : outreachDraft && !outreachLoading ? (
                <>
                  <button className="outreach-cancel-btn" onClick={() => { setOutreachTarget(null); setOutreachDraft(null); }}>Cancel</button>
                  <button className="outreach-copy-btn" onClick={handleCopyDraft}>Copy Draft</button>
                  <button className="outreach-send-btn" onClick={handleSendOutreach} disabled={!outreachDraft.to}>Open in Gmail</button>
                </>
              ) : null}
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
          </div>
          <div className="nav-group">
            <span className="group-label">Sourcing</span>
            {[
              { label: 'Acquire.com', type: 'marketplace' as const },
              { label: 'Flippa', type: 'marketplace' as const },
              { label: 'FT 1000', type: 'ranking' as const },
              { label: 'SaaStock Europe', type: 'conference' as const },
            ].map(src => (
              <button key={src.label} className={`agent-btn ${ingesting === src.label ? 'loading' : ''}`}
                onClick={() => handleIngest(src.type, src.label)} disabled={!!ingesting}>
                {src.label} {ingesting === src.label && '...'}
              </button>
            ))}
          </div>
          <div className="nav-group">
            <span className="group-label">Directories</span>
            <button className={`agent-btn ${ingesting === 'TheSaaSDirectory' ? 'loading' : ''}`}
              onClick={() => handleDirectoryScrape('TheSaaSDirectory')} disabled={!!ingesting}>
              SaaS Directory {ingesting === 'TheSaaSDirectory' && '...'}
            </button>
          </div>
          <div className="nav-group border-top">
            <span className="group-label">Proprietary Data</span>
            <label className={`agent-btn upload-btn ${ingesting === 'Upload' ? 'loading' : ''}`}>
              {ingesting === 'Upload' ? 'Uploading...' : 'Upload Target List'}
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
        </nav>
        <div className="sidebar-footer">
          <div className="user-profile">
            <div className="avatar">IR</div>
            <div className="user-info">
              <span className="user-name">Ishu Ratna</span>
              <span className="user-role">Managing Partner</span>
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
                  <th>Company</th>
                  <th>Website</th>
                  <th>Sector</th>
                  <th>Region</th>
                  <th>Employees</th>
                  <th>Founded</th>
                  <th>Age</th>
                  <th>Raised</th>
                  <th>Valuation</th>
                  <th>Status</th>
                  <th>Leadership</th>
                  <th>Email</th>
                  <th>LinkedIn</th>
                  <th>Source</th>
                  <th>Date Added</th>
                  <th>Description</th>
                  <th>Actions</th>
                </tr>
              </thead>
              <tbody>
                {loading ? (
                  Array.from({ length: 8 }).map((_, i) => (
                    <tr key={i} className="skeleton-row"><td colSpan={17}><div className="skeleton-line"></div></td></tr>
                  ))
                ) : filteredUniverse.length > 0 ? (
                  filteredUniverse.map((company, i) => (
                    <tr key={i}>
                      <td className="company-cell">
                        <button className="company-name-btn" onClick={() => setDrawerCompany(company)}>{company.name}</button>
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
                      <td><span className={`status-badge ${company.status?.toLowerCase().replace(/\s+/g, '-')}`}>{company.status}</span></td>
                      <td>{company.contact_name || '—'}</td>
                      <td className="email-cell">{company.contact_email ? (<a href="#" className="email-link" onClick={(e) => { e.preventDefault(); openOutreach(company); }}>{company.contact_email}</a>) : '—'}</td>
                      <td>{company.linkedin_url ? (<a href={company.linkedin_url} target="_blank" rel="noreferrer" className="linkedin-link">View</a>) : '—'}</td>
                      <td className="source-cell">{company.source}</td>
                      <td className="date-cell">{formatDate(company.ingested_at)}</td>
                      <td>
                        {company.description ? (
                          <button className="desc-btn" onClick={() => setDrawerCompany(company)}>View</button>
                        ) : '—'}
                      </td>
                      <td>
                        <div className="action-btns">
                          <button className={`smartfill-btn ${smartFilling === company.name ? 'filling' : ''}`} disabled={smartFilling === company.name}
                            onClick={async () => {
                              setSmartFilling(company.name);
                              try { const res = await dealApi.smartFill(company.name); setSmartFillResult(res); await loadData(); }
                              catch (err: any) { alert(`SmartFill failed: ${err.message}`); }
                              finally { setSmartFilling(null); }
                            }}>
                            {smartFilling === company.name ? '...' : 'SmartFill'}
                          </button>
                          <button className="outreach-btn" onClick={() => openOutreach(company)}>Outreach</button>
                        </div>
                      </td>
                    </tr>
                  ))
                ) : (
                  <tr><td colSpan={17} className="empty-row">No targets match your search.</td></tr>
                )}
              </tbody>
            </table>
          </div>
        </section>
      </main>

      {/* Company Drawer */}
      <CompanyDrawer company={drawerCompany} onClose={() => setDrawerCompany(null)} />

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
          display: flex; align-items: center; gap: 0.6rem;
          padding: 0.65rem 0.75rem; color: #64748b; border-radius: 8px;
          font-weight: 600; font-size: 0.88rem; transition: all 0.15s;
        }
        .nav-item:hover { color: #2563eb; background: #eff6ff; }
        .nav-item.active { color: #2563eb; background: #eff6ff; }
        .agent-btn {
          background: transparent; border: 1px solid #e2e8f0; color: #64748b;
          padding: 0.55rem 0.75rem; border-radius: 8px; text-align: left;
          font-size: 0.8rem; font-weight: 600; cursor: pointer; transition: all 0.15s;
        }
        .agent-btn:hover:not(:disabled) { border-color: #2563eb; color: #2563eb; }
        .agent-btn.loading { opacity: 0.5; cursor: wait; }
        .sidebar-footer { padding: 1.25rem; border-top: 1px solid #e2e8f0; }
        .user-profile { display: flex; align-items: center; gap: 0.65rem; }
        .avatar { width: 36px; height: 36px; background: #eff6ff; color: #2563eb; border-radius: 50%; display: flex; align-items: center; justify-content: center; font-weight: 800; font-size: 0.8rem; }
        .user-info { display: flex; flex-direction: column; }
        .user-name { font-size: 0.85rem; font-weight: 700; color: #0f172a; }
        .user-role { font-size: 0.68rem; color: #94a3b8; }
        .border-top { border-top: 1px solid #e2e8f0; margin-top: 0.75rem; padding-top: 0.75rem; }

        .upload-btn {
          background: #f8fafc !important;
          color: #2563eb !important;
          border: 1px dashed #2563eb !important;
          justify-content: center !important;
          cursor: pointer;
          display: flex !important;
          align-items: center;
          gap: 0.5rem;
          font-size: 0.78rem !important;
          font-weight: 700 !important;
          padding: 0.65rem !important;
          border-radius: 8px !important;
          transition: all 0.15s ease !important;
        }
        .upload-btn:hover { background: #2563eb !important; color: #fff !important; border-style: solid !important; }

        /* ── Main ───────────────────────────────────────────────── */
        .main-content { margin-left: 260px; flex: 1; padding: 2rem 2.5rem; max-width: calc(100vw - 260px); }

        .page-header { display: flex; justify-content: space-between; align-items: flex-start; margin-bottom: 1.5rem; flex-wrap: wrap; gap: 1rem; }
        h1 { font-size: 1.75rem; font-weight: 800; color: #0f172a; margin-bottom: 0.25rem; letter-spacing: -0.02em; }
        .subtitle { color: #94a3b8; font-size: 0.88rem; font-weight: 500; margin: 0; }
        .search-box {
          display: flex; align-items: center; padding: 0.6rem 1rem; gap: 0.6rem;
          background: #fff; border: 1.5px solid #e2e8f0; border-radius: 8px; width: 280px;
        }
        .search-box input { background: transparent; border: none; color: #0f172a; width: 100%; outline: none; font-size: 0.88rem; }

        /* ── Filter Bar ─────────────────────────────────────────── */
        .filter-bar {
          background: #fff;
          border: 1px solid #e2e8f0;
          border-radius: 10px;
          padding: 1.25rem 1.5rem;
          margin-bottom: 1.5rem;
        }

        .filter-row {
          display: flex; gap: 1.5rem; align-items: flex-end; flex-wrap: wrap;
        }

        .filter-group { display: flex; flex-direction: column; gap: 0.3rem; }
        .filter-group label { font-size: 0.65rem; text-transform: uppercase; letter-spacing: 0.1em; color: #94a3b8; font-weight: 700; }
        .filter-group select {
          padding: 0.45rem 0.75rem; border: 1px solid #e2e8f0; border-radius: 6px;
          font-size: 0.82rem; color: #0f172a; background: #f8fafc; min-width: 140px; cursor: pointer;
        }

        .filter-actions { display: flex; gap: 0.5rem; align-items: center; margin-left: auto; }

        .save-view-btn {
          background: none; border: 1px solid #2563eb; color: #2563eb;
          padding: 0.35rem 0.85rem; border-radius: 6px; font-size: 0.72rem; font-weight: 700; cursor: pointer;
        }
        .save-view-btn:hover { background: #eff6ff; }

        .reset-btn {
          background: none; border: none; color: #94a3b8;
          font-size: 0.72rem; cursor: pointer; text-decoration: underline;
        }
        .reset-btn:hover { color: #64748b; }

        .views-row {
          border-top: 1px solid #f1f5f9;
          padding-top: 0.75rem;
          margin-top: 0.75rem;
        }
        .saved-views { display: flex; gap: 0.35rem; flex-wrap: wrap; }
        .view-chip {
          display: flex; align-items: center; border: 1px solid #e2e8f0;
          border-radius: 6px; overflow: hidden; transition: all 0.15s;
        }
        .view-chip.active { border-color: #2563eb; background: #eff6ff; }
        .view-chip-btn { background: none; border: none; padding: 0.25rem 0.6rem; font-size: 0.72rem; font-weight: 600; color: #64748b; cursor: pointer; }
        .view-chip.active .view-chip-btn { color: #2563eb; }
        .view-chip-delete { background: none; border: none; border-left: 1px solid #e2e8f0; padding: 0.25rem 0.4rem; font-size: 0.8rem; color: #cbd5e1; cursor: pointer; }
        .view-chip-delete:hover { color: #ef4444; }

        .save-view-form {
          display: flex; gap: 0.5rem; align-items: center;
          margin-top: 0.75rem; padding-top: 0.75rem; border-top: 1px solid #f1f5f9;
        }
        .save-view-input { flex: 1; padding: 0.4rem 0.75rem; border: 1.5px solid #e2e8f0; border-radius: 6px; font-size: 0.82rem; outline: none; }
        .save-view-input:focus { border-color: #2563eb; }
        .save-view-confirm { padding: 0.4rem 1rem; background: #2563eb; color: #fff; border: none; border-radius: 6px; font-size: 0.78rem; font-weight: 700; cursor: pointer; }
        .save-view-confirm:disabled { opacity: 0.4; }
        .save-view-cancel { padding: 0.4rem 0.75rem; background: none; border: 1px solid #e2e8f0; border-radius: 6px; font-size: 0.78rem; color: #64748b; cursor: pointer; }

        /* ── Table ──────────────────────────────────────────────── */
        .table-section {
          background: #fff;
          border: 1px solid #e2e8f0;
          border-radius: 10px;
          padding: 1.5rem;
        }

        .section-header {
          display: flex; justify-content: space-between; align-items: center;
          margin-bottom: 1.25rem; padding-bottom: 1rem; border-bottom: 1px solid #f1f5f9;
        }
        .section-header h3 { font-size: 1.1rem; color: #0f172a; margin: 0; }

        .refresh-btn {
          background: none; border: 1px solid #e2e8f0; color: #64748b;
          padding: 0.4rem 0.85rem; border-radius: 6px; font-size: 0.78rem; font-weight: 600; cursor: pointer;
        }
        .refresh-btn:hover { border-color: #2563eb; color: #2563eb; }

        .table-scroll-container { overflow-x: auto; }
        .table-scroll-container::-webkit-scrollbar { height: 6px; }
        .table-scroll-container::-webkit-scrollbar-track { background: #f8fafc; }
        .table-scroll-container::-webkit-scrollbar-thumb { background: #e2e8f0; border-radius: 3px; }
        .table-scroll-container::-webkit-scrollbar-thumb:hover { background: #cbd5e1; }

        .crm-table { width: 100%; border-collapse: collapse; text-align: left; }
        .crm-table th {
          background: #f8fafc; color: #94a3b8; font-size: 0.68rem; text-transform: uppercase;
          letter-spacing: 0.08em; font-weight: 700; padding: 0.75rem 1rem;
          border-bottom: 1px solid #e2e8f0; white-space: nowrap;
        }
        .crm-table td {
          padding: 0.85rem 1rem; border-bottom: 1px solid #f1f5f9;
          font-size: 0.85rem; color: #475569; white-space: nowrap;
        }
        .crm-table tr:hover td { background: #f8fafc; }

        .company-cell { }
        .company-name-btn {
          background: none; border: none; padding: 0;
          font-size: 0.88rem; font-weight: 700; color: #0f172a; cursor: pointer; text-align: left;
        }
        .company-name-btn:hover { color: #2563eb; }

        .website-cell { max-width: 160px; overflow: hidden; text-overflow: ellipsis; }
        .website-link { color: #2563eb; font-size: 0.78rem; font-weight: 600; text-decoration: none; }
        .website-link:hover { text-decoration: underline; }

        .sector-cell { font-weight: 600; color: #0f172a; }
        .num-cell { font-size: 0.82rem; font-variant-numeric: tabular-nums; text-align: right; }

        .status-badge {
          font-size: 0.62rem; font-weight: 800; padding: 0.25rem 0.5rem;
          border-radius: 4px; text-transform: uppercase; letter-spacing: 0.05em;
        }
        .status-badge.qualified { background: #dcfce7; color: #166534; }
        .status-badge.under-review { background: #fef3c7; color: #92400e; }
        .status-badge.uploaded { background: #eff6ff; color: #2563eb; }
        .status-badge.scraped { background: #f1f5f9; color: #94a3b8; }
        .status-badge.not-a-fit { background: #fef2f2; color: #dc2626; }

        .email-cell { font-size: 0.78rem; }
        .email-link { color: #2563eb; text-decoration: none; }
        .email-link:hover { text-decoration: underline; }
        .linkedin-link { color: #0A66C2; font-weight: 600; text-decoration: underline; }
        .source-cell { font-size: 0.78rem; color: #94a3b8; }
        .date-cell { font-size: 0.78rem; }
        .empty-row { text-align: center; padding: 3rem !important; color: #94a3b8; }

        .desc-btn {
          background: transparent; border: 1px solid #2563eb; color: #2563eb;
          padding: 0.25rem 0.6rem; border-radius: 4px; font-size: 0.68rem; font-weight: 700; cursor: pointer;
        }
        .desc-btn:hover { background: #2563eb; color: white; }

        .action-btns { display: flex; gap: 0.35rem; }
        .smartfill-btn {
          background: transparent; border: 1px solid #2563eb; color: #2563eb;
          padding: 0.3rem 0.65rem; border-radius: 4px; font-size: 0.68rem; font-weight: 700; cursor: pointer;
        }
        .smartfill-btn:hover:not(:disabled) { background: #2563eb; color: white; }
        .smartfill-btn.filling { opacity: 0.4; cursor: wait; }

        .outreach-btn {
          background: transparent; border: 1px solid #d97706; color: #d97706;
          padding: 0.3rem 0.65rem; border-radius: 4px; font-size: 0.68rem; font-weight: 700; cursor: pointer;
        }
        .outreach-btn:hover { background: #d97706; color: white; }

        .skeleton-row td { padding: 0.75rem 1rem; }
        .skeleton-line { height: 10px; background: #e2e8f0; width: 100%; border-radius: 2px; animation: pulse 1.5s infinite; }
        @keyframes pulse { 0%, 100% { opacity: 1; } 50% { opacity: 0.5; } }

        /* ── Modals ────────────────────────────────────────────── */
        .modal-overlay {
          position: fixed; inset: 0; background: rgba(15, 23, 42, 0.3);
          display: flex; align-items: center; justify-content: center; z-index: 1000;
        }
        .modal-content {
          background: #fff; border-radius: 12px; width: 520px; max-width: 90vw;
          max-height: 85vh; display: flex; flex-direction: column;
          box-shadow: 0 20px 60px rgba(0,0,0,0.12); overflow: hidden;
        }
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

        /* Outreach modal specifics */
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
          .sidebar .agent-btn, .sidebar .user-info, .sidebar .upload-btn { display: none !important; }
          .sidebar .logo { text-align: center; padding: 1.5rem 0; font-size: 0.9rem; }
          .main-content { margin-left: 72px; max-width: calc(100vw - 72px); }
        }
        @media (max-width: 768px) {
          .page-header { flex-direction: column; gap: 1rem; }
          .search-box { width: 100%; }
          .filter-row { flex-direction: column; }
        }
      `}</style>
    </div>
  );
}
