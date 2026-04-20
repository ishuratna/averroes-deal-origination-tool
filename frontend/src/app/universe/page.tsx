'use client';

import { useEffect, useState } from "react";
import Link from 'next/link';
import { CompanyTarget } from "../../types";
import { dealApi } from "../../services/api";

export default function Universe() {
  const [universe, setUniverse] = useState<CompanyTarget[]>([]);
  const [loading, setLoading] = useState(true);
  const [searchQuery, setSearchQuery] = useState("");
  const [ingesting, setIngesting] = useState<string | null>(null);
  const [smartFilling, setSmartFilling] = useState<string | null>(null);
  
  const [filters, setFilters] = useState({
    vertical: "All",
    region: "All",
    status: "All",
    minScore: 0
  });

  const verticals = ["All", "SaaS", "FinTech", "HealthTech", "AI", "Cybersecurity", "E-commerce", "Industrial", "Logistics", "Professional Services"];
  const regions = ["All", "UK", "Ireland", "UK/Ireland", "Europe", "North America"];
  const statuses = ["All", "Qualified", "Under Review", "Uploaded", "In Pipeline", "Not a Fit"];

  useEffect(() => {
    loadData();
  }, []);

  async function loadData() {
    setLoading(true);
    try {
      const data = await dealApi.getUniverse();
      setUniverse(data);
    } catch (error) {
      console.error("Failed to load universe", error);
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
    } catch (error) {
      alert(`Ingestion failed for ${name}`);
    } finally {
      setIngesting(null);
    }
  };

  const filteredUniverse = universe.filter(c => {
    const q = searchQuery.toLowerCase();
    const matchesSearch = c.name.toLowerCase().includes(q) ||
      (c.sector && c.sector.toLowerCase().includes(q)) ||
      (c.description && c.description.toLowerCase().includes(q));
    const matchesVertical = filters.vertical === "All" || (c.sector && c.sector.toLowerCase().includes(filters.vertical.toLowerCase()));
    const matchesRegion = filters.region === "All" || (c.region && c.region.toLowerCase().includes(filters.region.toLowerCase()));
    const matchesUKIE = filters.region === "UK/Ireland" && 
      (c.region?.toLowerCase().includes("uk") || c.region?.toLowerCase().includes("ireland") || c.region?.toLowerCase().includes("united kingdom"));
    const matchesStatus = filters.status === "All" || c.status === filters.status;
    const matchesScore = (c.match_score * 100) >= filters.minScore;
    return matchesSearch && (matchesRegion || matchesUKIE) && matchesVertical && matchesStatus && matchesScore;
  });

  const formatDate = (dateStr?: string) => {
    if (!dateStr) return '—';
    return new Date(dateStr).toLocaleDateString('en-GB', { day: '2-digit', month: 'short', year: 'numeric' });
  };

  return (
    <div className="layout-wrapper">
      <aside className="sidebar">
        <div className="logo-section">
          <div className="logo">AVERROES<span>INTEL</span></div>
        </div>
        
        <nav className="sidebar-nav">
          <div className="nav-group">
            <span className="group-label">Intelligence</span>
            <Link href="/" className="nav-item">Deal Pipeline</Link>
            <Link href="/universe" className="nav-item active">Master Universe</Link>
          </div>

          <div className="nav-group">
            <span className="group-label">Sourcing Agents</span>
            <button className={`agent-btn ${ingesting === 'Acquire.com' ? 'loading' : ''}`} onClick={() => handleIngest('marketplace', 'Acquire.com')} disabled={!!ingesting}>
              Monitor Acquire.com {ingesting === 'Acquire.com' && '...'}
            </button>
            <button className={`agent-btn ${ingesting === 'Flippa' ? 'loading' : ''}`} onClick={() => handleIngest('marketplace', 'Flippa')} disabled={!!ingesting}>
              Monitor Flippa {ingesting === 'Flippa' && '...'}
            </button>
            <button className={`agent-btn ${ingesting === 'FT 1000' ? 'loading' : ''}`} onClick={() => handleIngest('ranking', 'FT 1000')} disabled={!!ingesting}>
              Scan FT 1000 {ingesting === 'FT 1000' && '...'}
            </button>
            <button className={`agent-btn ${ingesting === 'Web Summit' ? 'loading' : ''}`} onClick={() => handleIngest('conference', 'Web Summit')} disabled={!!ingesting}>
              Scrape Web Summit {ingesting === 'Web Summit' && '...'}
            </button>
          </div>

          <div className="nav-group border-top">
             <span className="group-label">Proprietary Data</span>
             <label className={`agent-btn upload-btn ${ingesting === 'Upload' ? 'loading' : ''}`}>
                {ingesting === 'Upload' ? 'Uploading...' : 'Upload Target List'}
                <input
                  type="file"
                  accept=".xlsx,.xls,.csv"
                  style={{ display: 'none' }}
                  onChange={async (e) => {
                    const file = e.target.files?.[0];
                    if (file) {
                      setIngesting('Upload');
                      try {
                        const res = await dealApi.uploadFile(file);
                        alert(res.message || "Upload complete!");
                        await loadData();
                      } catch (err: any) {
                        alert(`Upload Failed: ${err.message || "Unknown error"}\n\nPlease check that the backend is running and dependencies (openpyxl) are loaded.`);
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
            <h1>Master Data Universe</h1>
            <p className="subtitle">Consolidated view of all market targets and historical scrapes.</p>
          </div>
          <div className="header-right">
            <div className="glass search-box">
              <input type="text" placeholder="Search universe..." value={searchQuery} onChange={(e) => setSearchQuery(e.target.value)} />
              <span className="search-icon">&#128269;</span>
            </div>
          </div>
        </header>

        <section className="filter-layer glass">
          <div className="filter-group">
            <label>Vertical</label>
            <select value={filters.vertical} onChange={(e) => setFilters({...filters, vertical: e.target.value})}>
              {verticals.map(v => <option key={v} value={v}>{v}</option>)}
            </select>
          </div>
          <div className="filter-group">
            <label>Geography</label>
            <select value={filters.region} onChange={(e) => setFilters({...filters, region: e.target.value})}>
              {regions.map(r => <option key={r} value={r}>{r}</option>)}
            </select>
          </div>
          <div className="filter-group">
            <label>Status</label>
            <select value={filters.status} onChange={(e) => setFilters({...filters, status: e.target.value})}>
              {statuses.map(s => <option key={s} value={s}>{s}</option>)}
            </select>
          </div>
          <div className="filter-group">
            <label>Min Score ({filters.minScore}%)</label>
            <input type="range" min="0" max="100" step="5" value={filters.minScore} onChange={(e) => setFilters({...filters, minScore: parseInt(e.target.value)})} className="score-slider" />
          </div>
          <div className="filter-actions">
            <button className="button-reset" onClick={() => setFilters({vertical: "All", region: "All", status: "All", minScore: 0})}>Reset Filters</button>
          </div>
        </section>

        <section className="table-section glass">
          <div className="section-header">
            <div className="header-meta">
              <h3>{filteredUniverse.length} Targets Found</h3>
              <p className="description">Filtered from the Master Intelligence Universe.</p>
            </div>
            <button className="button-tiny" onClick={loadData}>Force Sync &#8635;</button>
          </div>

          <div className="table-scroll-container">
            <table className="crm-table">
              <thead>
                <tr>
                  <th>Company</th>
                  <th>Sector</th>
                  <th>Region</th>
                  <th>Status</th>
                  <th>Score</th>
                  <th>Leadership</th>
                  <th>LinkedIn</th>
                  <th>Source</th>
                  <th>Date</th>
                  <th>Actions</th>
                </tr>
              </thead>
              <tbody>
                {loading ? (
                  Array.from({ length: 8 }).map((_, i) => (
                    <tr key={i} className="skeleton-row">
                      <td colSpan={10}><div className="skeleton-line"></div></td>
                    </tr>
                  ))
                ) : filteredUniverse.length > 0 ? (
                  filteredUniverse.map((company, i) => (
                    <tr key={i}>
                      <td className="company-cell">
                        <div className="name-wrap">
                          <span className="name">{company.name}</span>
                          {company.website && (
                             <a href={company.website} target="_blank" rel="noreferrer" className="site-icon">&#8599;</a>
                          )}
                        </div>
                      </td>
                      <td className="sector-cell">{company.sector || 'TBD'}</td>
                      <td>{company.region || 'UK/Europe'}</td>
                      <td>
                        <span className={`status-badge ${company.status?.toLowerCase().replace(' ', '-')}`}>
                          {company.status}
                        </span>
                      </td>
                      <td>
                         <span className="score-val" style={{ color: company.match_score >= 0.7 ? 'var(--green)' : 'var(--gold)' }}>
                            {Math.round(company.match_score * 100)}%
                         </span>
                      </td>
                      <td>{company.contact_name || '\u2014'}</td>
                      <td>
                        {company.linkedin_url ? (
                          <a href={company.linkedin_url} target="_blank" rel="noreferrer" className="linkedin-link">View</a>
                        ) : '\u2014'}
                      </td>
                      <td className="source-cell">{company.source}</td>
                      <td className="date-cell">{formatDate(company.ingested_at)}</td>
                      <td>
                        <button
                          className={`smartfill-btn ${smartFilling === company.name ? 'filling' : ''}`}
                          disabled={smartFilling === company.name}
                          onClick={async () => {
                            setSmartFilling(company.name);
                            try {
                              await dealApi.smartFill(company.name);
                              await loadData();
                            } catch (err: any) {
                              alert(`SmartFill failed: ${err.message}`);
                            } finally {
                              setSmartFilling(null);
                            }
                          }}
                        >
                          {smartFilling === company.name ? '...' : 'SmartFill'}
                        </button>
                      </td>
                    </tr>
                  ))
                ) : (
                  <tr>
                    <td colSpan={10} className="empty-row">
                      No targets match your search. Try running a sourcing agent.
                    </td>
                  </tr>
                )}
              </tbody>
            </table>
          </div>
        </section>
      </main>

      <style jsx>{`
        .layout-wrapper { display: flex; min-height: 100vh; background: var(--bg-secondary); }
        .sidebar { width: 280px; background: var(--white); border-right: 1px solid var(--border-light); display: flex; flex-direction: column; position: fixed; height: 100vh; z-index: 100; }
        .logo-section { padding: 3rem 2rem; }
        .logo { font-size: 1.5rem; font-weight: 900; letter-spacing: 0.1em; color: var(--text-primary); }
        .logo span { color: var(--primary-blue); }
        .sidebar-nav { flex: 1; padding: 0 1.5rem; }
        .nav-group { margin-bottom: 2.5rem; display: flex; flex-direction: column; gap: 0.5rem; }
        .group-label { font-size: 0.7rem; text-transform: uppercase; letter-spacing: 0.15em; color: var(--text-dim); padding-left: 0.5rem; margin-bottom: 0.5rem; }
        .nav-item { padding: 0.75rem 1rem; color: var(--text-secondary); border-radius: var(--radius-sm); font-weight: 600; font-size: 0.9rem; transition: all 0.2s; }
        .nav-item:hover, .nav-item.active { color: var(--primary-blue); background: var(--primary-blue-light); }
        .agent-btn { background: transparent; border: 1px solid var(--border-glass); color: var(--text-secondary); padding: 0.75rem 1rem; border-radius: var(--radius-sm); text-align: left; font-size: 0.85rem; cursor: pointer; transition: all 0.2s; }
        .agent-btn:hover:not(:disabled) { border-color: var(--gold); color: var(--gold); }
        .agent-btn.loading { opacity: 0.6; cursor: wait; border-color: var(--gold); }
        .sidebar-footer { padding: 1.5rem; border-top: 1px solid var(--border-glass); }
        .user-profile { display: flex; align-items: center; gap: 0.75rem; }
        .avatar { width: 40px; height: 40px; background: var(--primary-blue-light); color: var(--primary-blue); border-radius: 50%; display: flex; align-items: center; justify-content: center; font-weight: 900; }
        .user-info { display: flex; flex-direction: column; }
        .user-name { font-size: 0.9rem; font-weight: 700; color: var(--text-primary); }
        .user-role { font-size: 0.7rem; color: var(--text-dim); }
        .main-content { margin-left: 280px; flex: 1; padding: 3rem; max-width: calc(100vw - 280px); transition: all 0.3s ease; }
        @media (max-width: 1280px) { .main-content { padding: 2rem; } }
        @media (max-width: 1024px) {
          .sidebar { width: 80px; }
          .sidebar .logo span, .sidebar .group-label, .sidebar .nav-item, .sidebar .agent-btn, .sidebar .user-info { display: none; }
          .sidebar .logo { text-align: center; padding: 2rem 0; font-size: 1rem; }
          .sidebar-nav { padding: 0 0.5rem; }
          .main-content { margin-left: 80px; max-width: calc(100vw - 80px); }
        }
        .page-header { display: flex; justify-content: space-between; align-items: flex-end; margin-bottom: 3rem; flex-wrap: wrap; gap: 2rem; }
        .header-left { flex: 1; min-width: 300px; }
        .subtitle { color: var(--text-secondary); font-size: 1.1rem; margin-top: 0.5rem; }
        .search-box { display: flex; align-items: center; padding: 1rem 1.5rem; gap: 1rem; min-width: 300px; width: 100%; max-width: 400px; background: var(--white); border: 1.5px solid var(--border-light); border-radius: var(--radius-md); }
        .search-box input { background: transparent; border: none; color: var(--text-primary); width: 100%; outline: none; font-size: 1rem; }
        .filter-layer { display: flex; gap: 1.5rem; align-items: flex-end; padding: 1.5rem 2rem; margin-bottom: 2rem; background: var(--white); border-radius: var(--radius-md); border: 1px solid var(--border-light); flex-wrap: wrap; }
        .filter-group { display: flex; flex-direction: column; gap: 0.4rem; }
        .filter-group label { font-size: 0.7rem; text-transform: uppercase; letter-spacing: 0.1em; color: var(--text-dim); font-weight: 700; }
        .filter-group select { padding: 0.5rem 0.75rem; border: 1px solid var(--border-light); border-radius: var(--radius-sm); font-size: 0.85rem; color: var(--text-primary); background: var(--white); }
        .score-slider { width: 120px; }
        .button-reset { background: transparent; border: 1px solid var(--border-light); color: var(--text-dim); padding: 0.5rem 1rem; border-radius: var(--radius-sm); font-size: 0.75rem; cursor: pointer; }
        .button-reset:hover { border-color: var(--gold); color: var(--gold); }
        .table-section { padding: 2rem; background: var(--white); border-radius: var(--radius-md); border: 1px solid var(--border-light); box-shadow: var(--shadow-sm); }
        .section-header { display: flex; justify-content: space-between; align-items: center; margin-bottom: 2rem; padding-bottom: 1.5rem; border-bottom: 1px solid var(--border-glass); }
        h3 { font-size: 1.25rem; margin-bottom: 0.25rem; }
        .header-meta .description { font-size: 0.85rem; color: var(--text-secondary); }
        .table-scroll-container { overflow-x: auto; }
        .crm-table { width: 100%; border-collapse: collapse; text-align: left; }
        .crm-table th { background: var(--bg-tertiary); color: var(--text-dim); font-size: 0.7rem; text-transform: uppercase; letter-spacing: 0.1em; font-weight: 700; padding: 1rem 1.5rem; border-bottom: 2px solid var(--border-light); }
        .crm-table td { padding: 1.25rem 1.5rem; border-bottom: 1px solid var(--border-glass); font-size: 0.9rem; color: var(--text-secondary); white-space: nowrap; }
        @media (max-width: 1400px) { .crm-table th:nth-child(7), .crm-table td:nth-child(7), .crm-table th:nth-child(9), .crm-table td:nth-child(9) { display: none; } }
        @media (max-width: 1100px) { .crm-table th:nth-child(8), .crm-table td:nth-child(8), .crm-table th:nth-child(3), .crm-table td:nth-child(3) { display: none; } }
        @media (max-width: 768px) { .crm-table th:nth-child(6), .crm-table td:nth-child(6) { display: none; } .section-header { flex-direction: column; align-items: flex-start; gap: 1.5rem; } }
        .table-scroll-container::-webkit-scrollbar { height: 8px; }
        .table-scroll-container::-webkit-scrollbar-track { background: rgba(255, 255, 255, 0.05); }
        .table-scroll-container::-webkit-scrollbar-thumb { background: var(--border-glass); border-radius: 4px; }
        .table-scroll-container::-webkit-scrollbar-thumb:hover { background: var(--gold); }
        .crm-table tr:hover td { background: var(--primary-blue-light); }
        .company-cell .name-wrap { display: flex; align-items: center; gap: 0.5rem; }
        .company-cell .name { color: var(--text-primary); font-weight: 700; }
        .site-icon { font-size: 0.75rem; color: var(--primary-blue); opacity: 0.6; }
        .site-icon:hover { opacity: 1; }
        .sector-cell { font-weight: 600; color: var(--text-primary); }
        .status-badge { font-size: 0.65rem; font-weight: 800; padding: 0.3rem 0.6rem; border-radius: 4px; text-transform: uppercase; letter-spacing: 0.05em; }
        .status-badge.qualified { background: var(--green-glow); color: var(--green); }
        .status-badge.under-review { background: var(--gold-muted); color: var(--gold); }
        .status-badge.uploaded { background: var(--primary-blue-light); color: var(--primary-blue); }
        .status-badge.scraped { background: var(--bg-tertiary); color: var(--text-dim); }
        .status-badge.not-a-fit { background: rgba(255, 77, 77, 0.1); color: #FF4D4D; }
        .score-val { font-weight: 800; }
        .linkedin-link { color: #0A66C2; font-weight: 600; text-decoration: underline; }
        .source-cell { font-size: 0.8rem; font-style: italic; }
        .date-cell { font-size: 0.8rem; white-space: nowrap; }
        .empty-row { text-align: center; padding: 4rem !important; font-style: italic; color: var(--text-dim); }
        .button-tiny { background: transparent; border: 1px solid var(--gold); color: var(--gold); padding: 0.5rem 1rem; border-radius: 4px; font-size: 0.75rem; font-weight: 700; cursor: pointer; }
        .button-tiny:hover { background: var(--gold); color: var(--navy-dark); }
        .smartfill-btn { background: transparent; border: 1px solid var(--primary-blue); color: var(--primary-blue); padding: 0.35rem 0.75rem; border-radius: 4px; font-size: 0.7rem; font-weight: 700; cursor: pointer; transition: all 0.2s; white-space: nowrap; }
        .smartfill-btn:hover:not(:disabled) { background: var(--primary-blue); color: white; }
        .smartfill-btn.filling { opacity: 0.5; cursor: wait; border-color: var(--gold); color: var(--gold); }
        .skeleton-line { height: 12px; background: var(--bg-tertiary); width: 100%; border-radius: 2px; animation: loading-shimmer 1.5s infinite; }
      `}</style>
    </div>
  );
}
