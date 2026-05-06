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
  const [smartFillResult, setSmartFillResult] = useState<any | null>(null);
  const [outreachTarget, setOutreachTarget] = useState<any | null>(null);
  const [outreachDraft, setOutreachDraft] = useState<{to: string; subject: string; body: string; company: string} | null>(null);
  const [outreachLoading, setOutreachLoading] = useState(false);
  const [outreachSent, setOutreachSent] = useState(false);
  
  const [filters, setFilters] = useState({
    vertical: "All",
    region: "All",
    status: "All",
  });

  const verticals = ["All", "SaaS", "FinTech", "HealthTech", "AI", "Cybersecurity", "E-commerce", "Industrial", "Logistics", "Professional Services"];
  const regions = ["All", "UK", "Ireland", "UK/Ireland", "Europe", "North America"];
  const statuses = ["All", "Qualified", "Under Review", "Uploaded", "In Pipeline", "Not a Fit"];

  useEffect(() => { loadData(); }, []);

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
    if (!dateStr) return '\u2014';
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
    } finally {
      setOutreachLoading(false);
    }
  };

  const handleSendOutreach = () => {
    if (!outreachDraft) return;
    // Open Gmail compose with pre-filled fields
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

  return (
    <div className="layout-wrapper">
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
                  <span className={`result-value ${smartFillResult.is_uk_ireland ? 'found' : 'not-found'}`}>
                    {smartFillResult.is_uk_ireland ? 'Yes' : 'No'}
                  </span>
                </div>
                <div className="result-row">
                  <span className="result-label">Tech Company</span>
                  <span className={`result-value ${smartFillResult.is_tech ? 'found' : 'not-found'}`}>
                    {smartFillResult.is_tech ? 'Yes' : 'No'}
                  </span>
                </div>
                {smartFillResult.reason && (
                  <div className="result-row">
                    <span className="result-label">Reason</span>
                    <span className="result-value" style={{fontSize: '0.8rem', whiteSpace: 'normal'}}>{smartFillResult.reason}</span>
                  </div>
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
                  <span className={`result-value ${smartFillResult.contact_name ? 'found' : 'not-found'}`}>
                    {smartFillResult.contact_name || 'Not Found'}
                  </span>
                </div>
                <div className="result-row">
                  <span className="result-label">Contact Email</span>
                  <span className={`result-value ${smartFillResult.contact_email ? 'found' : 'not-found'}`}>
                    {smartFillResult.contact_email || 'Not Found'}
                  </span>
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
                  <button className="outreach-send-btn" onClick={handleSendOutreach} disabled={!outreachDraft.to}>
                    Open in Gmail
                  </button>
                </>
              ) : null}
            </div>
          </div>
        </div>
      )}

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
            <button className={`agent-btn ${ingesting === 'SaaStock Europe' ? 'loading' : ''}`} onClick={() => handleIngest('conference', 'SaaStock Europe')} disabled={!!ingesting}>
              Scrape SaaStock {ingesting === 'SaaStock Europe' && '...'}
            </button>
          </div>
          <div className="nav-group">
            <span className="group-label">Directories</span>
            <button className={`agent-btn ${ingesting === 'TheSaaSDirectory' ? 'loading' : ''}`} onClick={() => handleDirectoryScrape('TheSaaSDirectory')} disabled={!!ingesting}>
              Scrape SaaS Directory {ingesting === 'TheSaaSDirectory' && '...'}
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
                        alert(`Upload Failed: ${err.message || "Unknown error"}\n\nPlease check that the backend is running.`);
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
              <span className="user-role">Associate</span>
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
          <div className="filter-actions">
            <button className="button-reset" onClick={() => setFilters({vertical: "All", region: "All", status: "All"})}>Reset Filters</button>
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
                  <th>Employees</th>
                  <th>Founded</th>
                  <th>Raised</th>
                  <th>Valuation</th>
                  <th>Status</th>
                  <th>Leadership</th>
                  <th>Email</th>
                  <th>LinkedIn</th>
                  <th>Source</th>
                  <th>Date</th>
                  <th>Actions</th>
                </tr>
              </thead>
              <tbody>
                {loading ? (
                  Array.from({ length: 8 }).map((_, i) => (
                    <tr key={i} className="skeleton-row"><td colSpan={14}><div className="skeleton-line"></div></td></tr>
                  ))
                ) : filteredUniverse.length > 0 ? (
                  filteredUniverse.map((company, i) => (
                    <tr key={i}>
                      <td className="company-cell">
                        <div className="name-wrap">
                          <span className="name">{company.name}</span>
                          {company.website && (<a href={company.website} target="_blank" rel="noreferrer" className="site-icon">&#8599;</a>)}
                        </div>
                      </td>
                      <td className="sector-cell">{company.sector || 'TBD'}</td>
                      <td>{company.region || 'UK/Europe'}</td>
                      <td className="num-cell">{company.employees ? company.employees.toLocaleString() : '\u2014'}</td>
                      <td className="num-cell">{company.year_founded || '\u2014'}</td>
                      <td className="num-cell">{company.total_raised_m ? `\u00A3${company.total_raised_m.toFixed(1)}M` : '\u2014'}</td>
                      <td className="num-cell">{company.valuation_estimate_m ? `\u00A3${company.valuation_estimate_m.toFixed(1)}M` : '\u2014'}</td>
                      <td><span className={`status-badge ${company.status?.toLowerCase().replace(' ', '-')}`}>{company.status}</span></td>
                      <td>{company.contact_name || '\u2014'}</td>
                      <td className="email-cell">{company.contact_email ? (<a href="#" className="email-link" onClick={(e) => { e.preventDefault(); openOutreach(company); }}>{company.contact_email}</a>) : '\u2014'}</td>
                      <td>{company.linkedin_url ? (<a href={company.linkedin_url} target="_blank" rel="noreferrer" className="linkedin-link">View</a>) : '\u2014'}</td>
                      <td className="source-cell">{company.source}</td>
                      <td className="date-cell">{formatDate(company.ingested_at)}</td>
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
                          <button className="outreach-btn" onClick={() => openOutreach(company)}>
                            Outreach
                          </button>
                        </div>
                      </td>
                    </tr>
                  ))
                ) : (
                  <tr><td colSpan={14} className="empty-row">No targets match your search. Try running a sourcing agent.</td></tr>
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
        .sidebar-nav { flex: 1; padding: 0 1.5rem; overflow-y: auto; }
        .nav-group { margin-bottom: 2rem; display: flex; flex-direction: column; gap: 0.5rem; }
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
        @media (max-width: 1024px) { .sidebar { width: 80px; } .sidebar .logo span, .sidebar .group-label, .sidebar .nav-item, .sidebar .agent-btn, .sidebar .user-info { display: none; } .sidebar .logo { text-align: center; padding: 2rem 0; font-size: 1rem; } .sidebar-nav { padding: 0 0.5rem; } .main-content { margin-left: 80px; max-width: calc(100vw - 80px); } }
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
        .num-cell { font-size: 0.85rem; font-variant-numeric: tabular-nums; text-align: right; }
        @media (max-width: 1600px) { .crm-table th:nth-child(6), .crm-table td:nth-child(6), .crm-table th:nth-child(7), .crm-table td:nth-child(7) { display: none; } }
        @media (max-width: 1400px) { .crm-table th:nth-child(12), .crm-table td:nth-child(12), .crm-table th:nth-child(14), .crm-table td:nth-child(14) { display: none; } }
        @media (max-width: 1100px) { .crm-table th:nth-child(4), .crm-table td:nth-child(4), .crm-table th:nth-child(5), .crm-table td:nth-child(5), .crm-table th:nth-child(13), .crm-table td:nth-child(13) { display: none; } }
        @media (max-width: 768px) { .crm-table th:nth-child(10), .crm-table td:nth-child(10), .crm-table th:nth-child(11), .crm-table td:nth-child(11) { display: none; } .section-header { flex-direction: column; align-items: flex-start; gap: 1.5rem; } }
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
        .email-cell { font-size: 0.8rem; }
        .email-link { color: var(--primary-blue); text-decoration: none; }
        .email-link:hover { text-decoration: underline; }
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
        .modal-overlay { position: fixed; top: 0; left: 0; right: 0; bottom: 0; background: rgba(0,0,0,0.5); display: flex; align-items: center; justify-content: center; z-index: 1000; backdrop-filter: blur(4px); }
        .modal-content { background: white; border-radius: 12px; width: 520px; max-width: 90vw; box-shadow: 0 20px 60px rgba(0,0,0,0.3); overflow: hidden; }
        .modal-header { display: flex; justify-content: space-between; align-items: center; padding: 1.5rem 2rem; border-bottom: 1px solid var(--border-light); }
        .modal-header h3 { font-size: 1.1rem; font-weight: 800; color: var(--text-primary); margin: 0; }
        .modal-close { background: none; border: none; font-size: 1.5rem; color: var(--text-dim); cursor: pointer; padding: 0; line-height: 1; }
        .modal-close:hover { color: var(--text-primary); }
        .modal-body { padding: 2rem; }
        .result-company-name { font-size: 1.3rem; font-weight: 900; color: var(--text-primary); margin-bottom: 1.5rem; padding-bottom: 1rem; border-bottom: 2px solid var(--primary-blue); }
        .result-grid { display: flex; flex-direction: column; gap: 0.75rem; }
        .result-row { display: flex; justify-content: space-between; align-items: center; padding: 0.75rem 1rem; border-radius: 8px; background: var(--bg-secondary); }
        .result-label { font-size: 0.8rem; font-weight: 700; color: var(--text-dim); text-transform: uppercase; letter-spacing: 0.05em; }
        .result-value { font-size: 0.9rem; font-weight: 700; text-align: right; max-width: 60%; overflow: hidden; text-overflow: ellipsis; }
        .result-value.found { color: var(--green, #16a34a); }
        .result-value.partial { color: var(--gold, #d97706); }
        .result-value.low { color: #FF4D4D; }
        .result-value.not-found { color: #FF4D4D; font-style: italic; }
        .result-value a { color: #0A66C2; text-decoration: underline; word-break: break-all; }
        .result-description { padding: 1rem; border-radius: 8px; background: var(--bg-secondary); margin-top: 0.5rem; }
        .result-description .result-label { display: block; margin-bottom: 0.5rem; font-size: 0.8rem; font-weight: 700; color: var(--text-dim); text-transform: uppercase; letter-spacing: 0.05em; }
        .description-text { font-size: 0.88rem; color: var(--text-primary); line-height: 1.65; margin: 0; white-space: pre-wrap; }
        .modal-footer { padding: 1rem 2rem 1.5rem; display: flex; justify-content: flex-end; }
        .modal-ok-btn { background: var(--primary-blue, #2563EB); color: white; border: none; padding: 0.6rem 2rem; border-radius: 6px; font-weight: 700; font-size: 0.9rem; cursor: pointer; }
        .modal-ok-btn:hover { opacity: 0.9; }
        .action-btns { display: flex; gap: 0.4rem; }
        .outreach-btn { background: transparent; border: 1px solid var(--gold); color: var(--gold); padding: 0.35rem 0.75rem; border-radius: 4px; font-size: 0.7rem; font-weight: 700; cursor: pointer; transition: all 0.2s; white-space: nowrap; }
        .outreach-btn:hover { background: var(--gold); color: var(--navy-dark); }
        .outreach-modal { width: 640px; }
        .outreach-loading { text-align: center; padding: 3rem 2rem; }
        .outreach-loading p { color: var(--text-secondary); margin-top: 1rem; font-size: 1rem; }
        .outreach-loading .loading-sub { font-size: 0.85rem; color: var(--text-dim); margin-top: 0.25rem; }
        .spinner { width: 40px; height: 40px; border: 3px solid var(--border-light); border-top-color: var(--primary-blue); border-radius: 50%; animation: spin 0.8s linear infinite; margin: 0 auto; }
        @keyframes spin { to { transform: rotate(360deg); } }
        .outreach-sent { text-align: center; padding: 2rem; }
        .sent-icon { font-size: 3rem; color: var(--green, #16a34a); margin-bottom: 1rem; }
        .outreach-sent h4 { font-size: 1.3rem; color: var(--text-primary); margin-bottom: 0.5rem; }
        .outreach-sent .sent-sub { font-size: 0.85rem; color: var(--text-dim); margin-top: 0.5rem; }
        .outreach-form { display: flex; flex-direction: column; gap: 1rem; }
        .form-row { display: flex; flex-direction: column; gap: 0.3rem; }
        .form-row label { font-size: 0.7rem; text-transform: uppercase; letter-spacing: 0.1em; color: var(--text-dim); font-weight: 700; }
        .form-row input { padding: 0.6rem 0.75rem; border: 1px solid var(--border-light); border-radius: var(--radius-sm); font-size: 0.9rem; color: var(--text-primary); background: var(--bg-secondary); }
        .form-row textarea { padding: 0.75rem; border: 1px solid var(--border-light); border-radius: var(--radius-sm); font-size: 0.9rem; color: var(--text-primary); background: var(--bg-secondary); resize: vertical; line-height: 1.6; font-family: inherit; }
        .form-row input:focus, .form-row textarea:focus { outline: none; border-color: var(--primary-blue); }
        .from-row { flex-direction: row; }
        .from-label { font-size: 0.8rem; color: var(--text-dim); font-style: italic; }
        .outreach-cancel-btn { background: transparent; border: 1px solid var(--border-light); color: var(--text-dim); padding: 0.6rem 1.5rem; border-radius: 6px; font-weight: 700; font-size: 0.85rem; cursor: pointer; }
        .outreach-cancel-btn:hover { border-color: var(--text-secondary); color: var(--text-secondary); }
        .outreach-copy-btn { background: transparent; border: 1px solid var(--primary-blue); color: var(--primary-blue); padding: 0.6rem 1.5rem; border-radius: 6px; font-weight: 700; font-size: 0.85rem; cursor: pointer; }
        .outreach-copy-btn:hover { background: var(--primary-blue-light); }
        .outreach-send-btn { background: var(--gold); color: var(--navy-dark, #1a1a2e); border: none; padding: 0.6rem 2rem; border-radius: 6px; font-weight: 800; font-size: 0.9rem; cursor: pointer; letter-spacing: 0.03em; }
        .outreach-send-btn:hover:not(:disabled) { opacity: 0.9; }
        .outreach-send-btn:disabled { opacity: 0.5; cursor: wait; }
        .modal-footer { display: flex; justify-content: flex-end; gap: 0.75rem; }
      `}</style>
    </div>
  );
}
