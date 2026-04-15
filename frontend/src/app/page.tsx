'use client';

import { useEffect, useState } from "react";
import Link from 'next/link';
import { CompanyTarget } from "../types";
import { dealApi } from "../services/api";

export default function Home() {
  const [pipeline, setPipeline] = useState<CompanyTarget[]>([]);
  const [searchQuery, setSearchQuery] = useState("");
  const [loading, setLoading] = useState(true);
  const [ingesting, setIngesting] = useState<string | null>(null);
  const [stats, setStats] = useState({ total: 0, qualified: 0, avgMatch: 0 });

  useEffect(() => {
    loadData();
  }, []);

  async function loadData() {
    setLoading(true);
    try {
      const data = await dealApi.getPipeline();
      setPipeline(data);
      
      const uni = await dealApi.getUniverse();
      const qualified = data.filter(c => c.status === 'Qualified' || c.status === 'Under Review').length;
      const avgMatch = data.length > 0 ? data.reduce((acc, curr) => acc + curr.match_score, 0) / data.length : 0;
      
      setStats({
        total: uni.length,
        qualified: qualified,
        avgMatch: Math.round(avgMatch * 100)
      });
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
    } catch (error) {
      alert(`Ingestion failed for ${name}`);
    } finally {
      setIngesting(null);
    }
  };

  const handleEnrich = async (name: string) => {
    try {
      await dealApi.enrichCompany(name);
      await loadData();
    } catch (error) {
      console.error("Enrichment failed", error);
    }
  };

  const handleDeepDive = async (name: string) => {
    try {
      const results = await dealApi.analyzeCompany(name);
      alert(`Deep Dive Complete for ${name}:\n\nMarket Sentiment: ${results.granular_findings.market_sentiment}\nEdge: ${results.granular_findings.competitive_edge}`);
      await loadData();
    } catch (error) {
      console.error("Deep dive failed", error);
    }
  };

  const sanitizeContact = (val: string | undefined | null) => {
    if (!val) return "";
    const placeholders = ['System Override Required', 'Data Missing', 'Pending Activation', 'Unknown Founder', 'research@averroescapital.com'];
    return placeholders.includes(val) ? "" : val;
  };

  const filteredPipeline = pipeline.filter(c => {
    const q = searchQuery.toLowerCase();
    return (
      c.name.toLowerCase().includes(q) ||
      (c.sector && c.sector.toLowerCase().includes(q)) ||
      (c.description && c.description.toLowerCase().includes(q))
    );
  }).sort((a, b) => b.match_score - a.match_score);

  return (
    <div className="layout-wrapper">
      {/* Sidebar for Agents */}
      <aside className="sidebar">
        <div className="logo-section">
          <div className="logo">AVERROES<span>INTEL</span></div>
        </div>
        
        <nav className="sidebar-nav">
          <div className="nav-group">
            <span className="group-label">Intelligence</span>
            <Link href="/" className="nav-item active">Deal Pipeline</Link>
            <Link href="/universe" className="nav-item">Master Universe</Link>
          </div>

          <div className="nav-group">
            <span className="group-label">Sourcing Agents</span>
            <button 
              className={`agent-btn ${ingesting === 'Acquire.com' ? 'loading' : ''}`}
              onClick={() => handleIngest('marketplace', 'Acquire.com')}
              disabled={!!ingesting}
            >
              Monitor Acquire.com {ingesting === 'Acquire.com' && '...'}
            </button>
            <button 
              className={`agent-btn ${ingesting === 'Flippa' ? 'loading' : ''}`}
              onClick={() => handleIngest('marketplace', 'Flippa')}
              disabled={!!ingesting}
            >
              Monitor Flippa {ingesting === 'Flippa' && '...'}
            </button>
            <button 
              className={`agent-btn ${ingesting === 'FT 1000' ? 'loading' : ''}`}
              onClick={() => handleIngest('ranking', 'FT 1000')}
              disabled={!!ingesting}
            >
              Scan FT 1000 {ingesting === 'FT 1000' && '...'}
            </button>
            <button 
              className={`agent-btn ${ingesting === 'Web Summit' ? 'loading' : ''}`}
              onClick={() => handleIngest('conference', 'Web Summit')}
              disabled={!!ingesting}
            >
              Scrape Web Summit {ingesting === 'Web Summit' && '...'}
            </button>
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
            <h1>Active Deal Pipeline</h1>
            <p className="subtitle">High-conviction targets matching Averroes philosophy.</p>
          </div>
          <div className="header-right">
            <div className="glass search-box">
              <input 
                type="text" 
                placeholder="Filter by company, sector, or thesis..." 
                value={searchQuery}
                onChange={(e) => setSearchQuery(e.target.value)}
              />
              <span className="search-icon">🔍</span>
            </div>
          </div>
        </header>

        {/* Stats Row */}
        <section className="stats-row">
          <div className="stat-card glass">
            <span className="stat-label">Total Universe</span>
            <span className="stat-value">{stats.total}</span>
            <span className="stat-trend">+12% this week</span>
          </div>
          <div className="stat-card glass">
            <span className="stat-label">Qualified Targets</span>
            <span className="stat-value">{stats.qualified}</span>
            <span className="stat-trend positive">High Intent</span>
          </div>
          <div className="stat-card glass">
            <span className="stat-label">Avg Match Score</span>
            <span className="stat-value">{stats.avgMatch}%</span>
            <div className="stat-progress">
              <div className="progress-bar" style={{ width: `${stats.avgMatch}%` }}></div>
            </div>
          </div>
        </section>

        {/* Pipeline Grid */}
        <section className="pipeline-section">
          <div className="section-header">
            <h3>Top Recommendations</h3>
            <button className="button-tiny" onClick={loadData}>Refresh Data ↻</button>
          </div>
          
          <div className="cards-grid">
            {loading ? (
              [1, 2, 3, 4].map(i => <div key={i} className="card glass skeleton-card"></div>)
            ) : filteredPipeline.length > 0 ? (
              filteredPipeline.map((company, i) => (
                <div key={i} className="card glass deal-card">
                  <div className="card-top">
                    <span className="match-tag" style={{
                      color: company.match_score >= 0.8 ? 'var(--green)' : 'var(--gold)',
                      borderColor: company.match_score >= 0.8 ? 'var(--green-glow)' : 'var(--gold-muted)'
                    }}>
                      {Math.round(company.match_score * 100)}% Match
                    </span>
                    <span className="source-label">{company.source}</span>
                  </div>
                  
                  <h4>{company.name}</h4>
                  <p className="sector">{company.sector}</p>
                  <p className="description">{company.description}</p>
                  
                  <div className="meta-info">
                    <div className="info-item">
                      <span className="label">Region</span>
                      <span className="value">{company.region || 'UK/Europe'}</span>
                    </div>
                    <div className="info-item">
                      <span className="label">Structure</span>
                      <span className="value">{company.ownership || 'Founder-led'}</span>
                    </div>
                  </div>

                  <div className="founder-highlight glass">
                    <div className="founder-header">
                      <span className="founder-label">Leadership</span>
                      <button className="enrich-btn" onClick={() => handleEnrich(company.name)}>Enrich Meta →</button>
                    </div>
                    <p className="founder-name" style={{ opacity: sanitizeContact(company.contact_name) ? 1 : 0.4 }}>
                      {sanitizeContact(company.contact_name) || 'Contact Hidden'}
                    </p>
                    <p className="founder-email" style={{ opacity: sanitizeContact(company.contact_email) ? 1 : 0.4 }}>
                      {sanitizeContact(company.contact_email) || 'Sync required for email'}
                    </p>
                  </div>

                  <div className="card-footer">
                    <a href={company.website} target="_blank" rel="noreferrer" className="view-link">Visit Website ↗</a>
                    <button className="button-action" onClick={() => handleDeepDive(company.name)}>Analyze Deep-Dive</button>
                  </div>
                </div>
              ))
            ) : (
              <div className="empty-state glass">
                <p>No matches found. Try running a sourcing agent from the sidebar.</p>
              </div>
            )}
          </div>
        </section>
      </main>

      <style jsx>{`
        .layout-wrapper {
          display: flex;
          min-height: 100vh;
        }

        /* Sidebar Decor */
        .sidebar {
          width: 280px;
          background: var(--navy-dark);
          border-right: 1px solid var(--border-glass);
          display: flex;
          flex-direction: column;
          position: fixed;
          height: 100vh;
          z-index: 100;
        }

        .logo-section {
          padding: 3rem 2rem;
        }

        .logo {
          font-size: 1.5rem;
          font-weight: 900;
          letter-spacing: 0.1em;
          color: var(--white);
        }

        .logo span {
          color: var(--gold);
        }

        .sidebar-nav {
          flex: 1;
          padding: 0 1.5rem;
        }

        .nav-group {
          margin-bottom: 2.5rem;
          display: flex;
          flex-direction: column;
          gap: 0.5rem;
        }

        .group-label {
          font-size: 0.7rem;
          text-transform: uppercase;
          letter-spacing: 0.15em;
          color: var(--text-dim);
          padding-left: 0.5rem;
          margin-bottom: 0.5rem;
        }

        .nav-item {
          padding: 0.75rem 1rem;
          color: var(--text-secondary);
          border-radius: var(--radius-sm);
          font-weight: 600;
          font-size: 0.9rem;
          transition: all 0.2s;
        }

        .nav-item:hover, .nav-item.active {
          color: var(--white);
          background: var(--bg-tertiary);
          box-shadow: var(--shadow-sm);
        }

        .agent-btn {
          background: transparent;
          border: 1px solid var(--border-glass);
          color: var(--text-secondary);
          padding: 0.75rem 1rem;
          border-radius: var(--radius-sm);
          text-align: left;
          font-size: 0.85rem;
          cursor: pointer;
          transition: all 0.2s;
        }

        .agent-btn:hover:not(:disabled) {
          border-color: var(--gold);
          color: var(--gold);
        }

        .agent-btn.loading {
          opacity: 0.6;
          cursor: wait;
          border-color: var(--gold);
        }

        .sidebar-footer {
          padding: 1.5rem;
          border-top: 1px solid var(--border-glass);
        }

        .user-profile {
          display: flex;
          align-items: center;
          gap: 0.75rem;
        }

        .avatar {
          width: 40px;
          height: 40px;
          background: var(--gold);
          color: var(--navy-dark);
          border-radius: 50%;
          display: flex;
          align-items: center;
          justify-content: center;
          font-weight: 900;
        }

        .user-info {
          display: flex;
          flex-direction: column;
        }

        .user-name {
          font-size: 0.9rem;
          font-weight: 700;
          color: var(--white);
        }

        .user-role {
          font-size: 0.7rem;
          color: var(--text-dim);
        }

        /* Main Content */
        .main-content {
          margin-left: 280px;
          flex: 1;
          padding: 3rem;
          width: calc(100% - 280px);
          transition: all 0.3s cubic-bezier(0.4, 0, 0.2, 1);
        }

        @media (max-width: 1280px) {
          .main-content {
            padding: 2rem;
          }
        }

        @media (max-width: 1024px) {
          .sidebar {
            width: 80px;
          }
          .sidebar .logo span, 
          .sidebar .group-label,
          .sidebar .nav-item,
          .sidebar .agent-btn,
          .sidebar .user-info {
            display: none !important;
          }
          .sidebar .logo {
            text-align: center;
            padding: 2rem 0;
            font-size: 1rem;
          }
          .main-content {
            margin-left: 80px;
            width: calc(100% - 80px);
          }
        }

        @media (max-width: 768px) {
           .page-header {
             flex-direction: column;
             align-items: flex-start;
             gap: 1.5rem;
           }
           .search-box {
             width: 100%;
             min-width: 0;
           }
        }

        .page-header {
          display: flex;
          justify-content: space-between;
          align-items: flex-end;
          margin-bottom: 3rem;
        }

        .subtitle {
          color: var(--text-secondary);
          font-size: 1.1rem;
          margin-top: 0.5rem;
        }

        .search-box {
          display: flex;
          align-items: center;
          padding: 1rem 1.5rem;
          gap: 1rem;
          min-width: 400px;
        }

        .search-box input {
          background: transparent;
          border: none;
          color: var(--white);
          width: 100%;
          outline: none;
          font-size: 1rem;
        }

        /* Stats Row */
        .stats-row {
          display: grid;
          grid-template-columns: repeat(3, 1fr);
          gap: 2rem;
          margin-bottom: 4rem;
        }

        @media (max-width: 1100px) {
          .stats-row {
            grid-template-columns: 1fr;
            gap: 1.5rem;
          }
        }

        .stat-card {
          display: flex;
          flex-direction: column;
          gap: 0.5rem;
        }

        .stat-label {
          font-size: 0.8rem;
          text-transform: uppercase;
          letter-spacing: 0.1em;
          color: var(--text-secondary);
        }

        .stat-value {
          font-size: 2.5rem;
          font-weight: 800;
          color: var(--white);
        }

        .stat-trend {
          font-size: 0.75rem;
          color: var(--text-dim);
        }

        .stat-trend.positive {
          color: var(--green);
        }

        .stat-progress {
          height: 4px;
          background: var(--bg-tertiary);
          border-radius: 2px;
          margin-top: 1rem;
          overflow: hidden;
        }

        .progress-bar {
          height: 100%;
          background: var(--gold);
          box-shadow: 0 0 10px var(--gold-glow);
        }

        /* Cards Grid */
        .section-header {
          display: flex;
          justify-content: space-between;
          align-items: center;
          margin-bottom: 2rem;
        }

        .cards-grid {
          display: grid;
          grid-template-columns: repeat(auto-fill, minmax(360px, 1fr));
          gap: 2rem;
        }

        .deal-card {
          display: flex;
          flex-direction: column;
          border-top: 2px solid var(--border-glass);
        }

        .deal-card:hover {
          border-top-color: var(--gold);
        }

        .card-top {
          display: flex;
          justify-content: space-between;
          align-items: center;
          margin-bottom: 1.5rem;
        }

        .match-tag {
          font-size: 0.75rem;
          font-weight: 900;
          text-transform: uppercase;
          border: 1px solid;
          padding: 0.25rem 0.75rem;
          border-radius: 4px;
        }

        .source-label {
          font-size: 0.7rem;
          color: var(--text-dim);
          text-transform: uppercase;
          letter-spacing: 0.05em;
        }

        h4 {
          font-size: 1.4rem;
          margin-bottom: 0.5rem;
          letter-spacing: -0.01em;
        }

        .sector {
          color: var(--gold);
          font-size: 0.85rem;
          font-weight: 700;
          margin-bottom: 1rem;
          text-transform: uppercase;
        }

        .description {
          color: var(--text-secondary);
          font-size: 0.95rem;
          margin-bottom: 1.5rem;
          line-height: 1.5;
        }

        .meta-info {
          display: grid;
          grid-template-columns: 1fr 1fr;
          gap: 1rem;
          margin-bottom: 1.5rem;
          padding-bottom: 1.5rem;
          border-bottom: 1px solid var(--border-glass);
        }

        .info-item {
          display: flex;
          flex-direction: column;
          gap: 0.25rem;
        }

        .info-item .label {
          font-size: 0.65rem;
          color: var(--text-dim);
          text-transform: uppercase;
        }

        .info-item .value {
          font-size: 0.9rem;
          color: var(--text-primary);
          font-weight: 600;
        }

        .founder-highlight {
          padding: 1.25rem;
          margin-bottom: 1.5rem;
          border-radius: var(--radius-md);
        }

        .founder-header {
          display: flex;
          justify-content: space-between;
          align-items: center;
          margin-bottom: 0.75rem;
        }

        .founder-label {
          font-size: 0.7rem;
          text-transform: uppercase;
          letter-spacing: 0.05em;
          color: var(--gold);
          font-weight: 800;
        }

        .enrich-btn {
          background: transparent;
          border: none;
          color: var(--text-secondary);
          font-size: 0.7rem;
          cursor: pointer;
          font-weight: 700;
          text-decoration: underline;
        }

        .enrich-btn:hover {
          color: var(--white);
        }

        .founder-name {
          font-weight: 700;
          color: var(--white);
          margin-bottom: 0.25rem;
        }

        .founder-email {
          font-size: 0.8rem;
          color: var(--text-secondary);
        }

        .card-footer {
          display: flex;
          justify-content: space-between;
          align-items: center;
          margin-top: auto;
        }

        .view-link {
          font-size: 0.85rem;
          font-weight: 700;
        }

        .button-action {
          background: var(--bg-tertiary);
          color: var(--white);
          border: 1px solid var(--border-glass);
          padding: 0.5rem 1rem;
          border-radius: 4px;
          font-size: 0.75rem;
          font-weight: 700;
          cursor: pointer;
          transition: all 0.2s;
        }

        .button-action:hover {
          background: var(--gold);
          color: var(--navy-dark);
        }

        .empty-state {
          grid-column: 1 / -1;
          padding: 5rem;
          text-align: center;
          color: var(--text-secondary);
        }

        .skeleton-card {
          height: 400px;
        }
      `}</style>
    </div>
  );
}
