'use client';

import { useEffect, useState } from "react";
import Link from 'next/link';
import { CompanyTarget } from "../types";
import { dealApi } from "../services/api";

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
  const vcSignals = ['vc-backed', 'pe-backed', 'venture capital', 'series a', 'series b', 'series c', 'series d', 'institutional'];
  const bootstrapSignals = ['bootstrapped', 'founder-led', 'family-owned', 'management-owned', 'self-funded'];
  const angelSignals = ['angel', 'seed', 'angel-backed', 'pre-seed'];
  if (vcSignals.some(k => text.includes(k))) return 'vc';
  if (bootstrapSignals.some(k => text.includes(k))) return 'bootstrapped';
  if (angelSignals.some(k => text.includes(k))) return 'angel';
  return 'unknown';
}

function growthCategory(c: CompanyTarget): 'fast' | 'steady' | 'unknown' {
  const growthRate = c.revenue_growth_pct || c.pitchbook_growth_rate || 0;
  const hasSignals = c.growth_signals;
  const oppScore = c.opportunity_score || 0;
  if (growthRate > 30 || (hasSignals && growthRate > 15) || oppScore > 70) return 'fast';
  if (growthRate > 0 || hasSignals || oppScore > 30) return 'steady';
  return 'unknown';
}

// ── Component ───────────────────────────────────────────────────────────────

export default function Home() {
  const [pipeline, setPipeline] = useState<CompanyTarget[]>([]);
  const [searchQuery, setSearchQuery] = useState("");
  const [loading, setLoading] = useState(true);
  const [ingesting, setIngesting] = useState<string | null>(null);
  const [requalifying, setRequalifying] = useState(false);
  const [stats, setStats] = useState({ total: 0, qualified: 0 });

  // Filter states
  const [filterSaaS, setFilterSaaS] = useState<'all' | 'high' | 'medium'>('all');
  const [filterOwnership, setFilterOwnership] = useState<'all' | 'bootstrapped' | 'angel' | 'vc'>('all');
  const [filterGrowth, setFilterGrowth] = useState<'all' | 'fast' | 'steady'>('all');

  useEffect(() => {
    loadData();
  }, []);

  async function loadData() {
    setLoading(true);
    try {
      const data = await dealApi.getPipeline();
      setPipeline(data);

      const uni = await dealApi.getUniverse();

      setStats({
        total: uni.length,
        qualified: data.length,
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

  const handleRequalifyAll = async () => {
    if (!confirm("This will re-evaluate ALL companies against the hard filters (UK/Ireland + Tech). Continue?")) return;
    setRequalifying(true);
    try {
      const response = await fetch(`${process.env.NEXT_PUBLIC_API_URL || 'https://averroes-deal-backend-890361705054.europe-west1.run.app'}/requalify-all`, { method: 'POST' });
      const result = await response.json();
      alert(`Re-qualification complete:\n${result.qualified} Qualified\n${result.rejected} Not a Fit\n${result.errors || 0} Errors`);
      await loadData();
    } catch (error) {
      alert("Re-qualification failed. Check console.");
      console.error(error);
    } finally {
      setRequalifying(false);
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

  // Apply filters
  const filteredPipeline = pipeline.filter(c => {
    const q = searchQuery.toLowerCase();
    const matchesSearch = c.name.toLowerCase().includes(q) ||
      (c.sector && c.sector.toLowerCase().includes(q)) ||
      (c.description && c.description.toLowerCase().includes(q));

    // B2B SaaS filter
    let matchesSaaS = true;
    if (filterSaaS !== 'all') {
      const level = isSaaSOrB2B(c);
      if (filterSaaS === 'high') matchesSaaS = level === 'high';
      else if (filterSaaS === 'medium') matchesSaaS = level === 'high' || level === 'medium';
    }

    // Ownership filter
    let matchesOwnership = true;
    if (filterOwnership !== 'all') {
      matchesOwnership = ownershipCategory(c) === filterOwnership;
    }

    // Growth filter
    let matchesGrowth = true;
    if (filterGrowth !== 'all') {
      const level = growthCategory(c);
      if (filterGrowth === 'fast') matchesGrowth = level === 'fast';
      else if (filterGrowth === 'steady') matchesGrowth = level === 'fast' || level === 'steady';
    }

    return matchesSearch && matchesSaaS && matchesOwnership && matchesGrowth;
  });

  // Count active filters
  const activeFilterCount = [filterSaaS !== 'all', filterOwnership !== 'all', filterGrowth !== 'all'].filter(Boolean).length;

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
              className={`agent-btn ${ingesting === 'SaaStock Europe' ? 'loading' : ''}`}
              onClick={() => handleIngest('conference', 'SaaStock Europe')}
              disabled={!!ingesting}
            >
              Scrape SaaStock {ingesting === 'SaaStock Europe' && '...'}
            </button>
          </div>

          <div className="nav-group">
            <span className="group-label">Actions</span>
            <button
              className={`agent-btn requalify-btn ${requalifying ? 'loading' : ''}`}
              onClick={handleRequalifyAll}
              disabled={requalifying}
            >
              {requalifying ? 'Re-qualifying...' : 'Re-qualify All'}
            </button>
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
            <h1>Deal Pipeline</h1>
            <p className="subtitle">Qualified UK/Ireland tech companies. Use filters to narrow your focus.</p>
          </div>
          <div className="header-right">
            <div className="glass search-box">
              <input
                type="text"
                placeholder="Search by company, sector, or keyword..."
                value={searchQuery}
                onChange={(e) => setSearchQuery(e.target.value)}
              />
              <span className="search-icon">&#128269;</span>
            </div>
          </div>
        </header>

        {/* Stats */}
        <section className="dashboard-grid">
          <div className="stat-card glass animate-in" style={{ animationDelay: '0.1s' }}>
            <span className="label">Master Universe</span>
            <div className="value-wrap">
              <span className="value">{stats.total}</span>
              <span className="unit">Total</span>
            </div>
          </div>
          <div className="stat-card glass animate-in" style={{ animationDelay: '0.2s' }}>
            <span className="label">Qualified (UK/IE + Tech)</span>
            <div className="value-wrap">
              <span className="value">{stats.qualified}</span>
              <span className="unit">Pipeline</span>
            </div>
          </div>
          <div className="stat-card glass animate-in" style={{ animationDelay: '0.3s' }}>
            <span className="label">Showing</span>
            <div className="value-wrap">
              <span className="value">{filteredPipeline.length}</span>
              <span className="unit">{activeFilterCount > 0 ? `${activeFilterCount} filter${activeFilterCount > 1 ? 's' : ''} active` : 'No filters'}</span>
            </div>
          </div>
        </section>

        {/* Filter Controls */}
        <div className="filter-bar animate-in" style={{ animationDelay: '0.4s' }}>
          <div className="filter-group">
            <label className="filter-label">B2B SaaS Fit</label>
            <div className="filter-options">
              <button className={`filter-btn ${filterSaaS === 'all' ? 'active' : ''}`} onClick={() => setFilterSaaS('all')}>All</button>
              <button className={`filter-btn ${filterSaaS === 'high' ? 'active' : ''}`} onClick={() => setFilterSaaS('high')}>B2B SaaS</button>
              <button className={`filter-btn ${filterSaaS === 'medium' ? 'active' : ''}`} onClick={() => setFilterSaaS('medium')}>B2B + Tech</button>
            </div>
          </div>

          <div className="filter-group">
            <label className="filter-label">Ownership</label>
            <div className="filter-options">
              <button className={`filter-btn ${filterOwnership === 'all' ? 'active' : ''}`} onClick={() => setFilterOwnership('all')}>All</button>
              <button className={`filter-btn ${filterOwnership === 'bootstrapped' ? 'active' : ''}`} onClick={() => setFilterOwnership('bootstrapped')}>Bootstrapped</button>
              <button className={`filter-btn ${filterOwnership === 'angel' ? 'active' : ''}`} onClick={() => setFilterOwnership('angel')}>Angel</button>
              <button className={`filter-btn ${filterOwnership === 'vc' ? 'active' : ''}`} onClick={() => setFilterOwnership('vc')}>VC-backed</button>
            </div>
          </div>

          <div className="filter-group">
            <label className="filter-label">Growth</label>
            <div className="filter-options">
              <button className={`filter-btn ${filterGrowth === 'all' ? 'active' : ''}`} onClick={() => setFilterGrowth('all')}>All</button>
              <button className={`filter-btn ${filterGrowth === 'fast' ? 'active' : ''}`} onClick={() => setFilterGrowth('fast')}>Fast Growth</button>
              <button className={`filter-btn ${filterGrowth === 'steady' ? 'active' : ''}`} onClick={() => setFilterGrowth('steady')}>Growing</button>
            </div>
          </div>

          {activeFilterCount > 0 && (
            <button className="clear-filters-btn" onClick={() => { setFilterSaaS('all'); setFilterOwnership('all'); setFilterGrowth('all'); }}>
              Clear all filters
            </button>
          )}
        </div>

        {/* Pipeline Cards */}
        <section className="pipeline-section animate-in" style={{ animationDelay: '0.5s' }}>
          <div className="section-header">
            <h3>Qualified Companies ({filteredPipeline.length})</h3>
            <button className="button-tiny" onClick={loadData}>Refresh Data &#8635;</button>
          </div>

          <div className="cards-grid">
            {loading ? (
              [1, 2, 3, 4].map(i => <div key={i} className="card glass skeleton-card"></div>)
            ) : filteredPipeline.length > 0 ? (
              filteredPipeline.map((company, i) => {
                const saasLevel = isSaaSOrB2B(company);
                const ownLevel = ownershipCategory(company);
                const growLevel = growthCategory(company);
                return (
                  <div key={i} className="card glass deal-card">
                    <div className="card-top">
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

                    <h4>{company.name}</h4>
                    <p className="sector">{company.sector}</p>
                    <p className="description">{company.description}</p>

                    <div className="meta-info">
                      <div className="info-item">
                        <span className="label">Region</span>
                        <span className="value">{company.region || company.hq_country || 'UK/Ireland'}</span>
                      </div>
                      <div className="info-item">
                        <span className="label">Structure</span>
                        <span className="value">{company.ownership || '—'}</span>
                      </div>
                      {company.employees ? (
                        <div className="info-item">
                          <span className="label">Employees</span>
                          <span className="value">{company.employees}</span>
                        </div>
                      ) : null}
                      {company.revenue_m ? (
                        <div className="info-item">
                          <span className="label">Revenue</span>
                          <span className="value">&pound;{company.revenue_m}M</span>
                        </div>
                      ) : null}
                    </div>

                    <div className="founder-highlight glass">
                      <div className="founder-header">
                        <span className="founder-label">Leadership</span>
                        <button className="enrich-btn" onClick={() => handleEnrich(company.name)}>Enrich Meta &rarr;</button>
                      </div>
                      <p className="founder-name" style={{ opacity: sanitizeContact(company.contact_name) ? 1 : 0.4 }}>
                        {sanitizeContact(company.contact_name) || 'Contact Hidden'}
                      </p>
                      <p className="founder-email" style={{ opacity: sanitizeContact(company.contact_email) ? 1 : 0.4 }}>
                        {sanitizeContact(company.contact_email) || 'Sync required for email'}
                      </p>
                    </div>

                    <div className="card-footer">
                      <a href={company.website} target="_blank" rel="noreferrer" className="view-link">Visit Website &#8599;</a>
                      <button className="button-action" onClick={() => handleDeepDive(company.name)}>Analyze Deep-Dive</button>
                    </div>
                  </div>
                );
              })
            ) : (
              <div className="empty-state glass">
                <p>{activeFilterCount > 0 ? 'No companies match your current filters. Try broadening them.' : 'No qualified companies yet. Run a sourcing agent and SmartFill from the Master Universe.'}</p>
              </div>
            )}
          </div>
        </section>
      </main>

      <style jsx>{`
        .layout-wrapper {
          display: flex;
          min-height: 100vh;
          background: var(--bg-secondary);
        }

        .sidebar {
          width: 280px;
          background: var(--white);
          border-right: 1px solid var(--border-light);
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
          color: var(--text-primary);
        }

        .logo span {
          color: var(--primary-blue);
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
          color: var(--primary-blue);
          background: var(--primary-blue-light);
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

        .requalify-btn {
          border-color: var(--primary-blue);
          color: var(--primary-blue);
          font-weight: 700;
        }

        .requalify-btn:hover:not(:disabled) {
          background: var(--primary-blue);
          color: var(--white);
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
          background: var(--primary-blue-light);
          color: var(--primary-blue);
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
          color: var(--text-primary);
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
           .filter-bar {
             flex-direction: column;
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
          background: var(--white);
          border: 1.5px solid var(--border-light);
          border-radius: var(--radius-md);
        }

        .search-box input {
          background: transparent;
          border: none;
          color: var(--text-primary);
          width: 100%;
          outline: none;
          font-size: 1rem;
        }

        /* Stats */
        .dashboard-grid {
          display: grid;
          grid-template-columns: repeat(3, 1fr);
          gap: 2rem;
          margin-bottom: 2rem;
        }

        .stat-card {
          display: flex;
          flex-direction: column;
          gap: 0.5rem;
          padding: 1.5rem;
          background: var(--white);
          border: 1px solid var(--border-light);
          border-radius: var(--radius-md);
        }

        .stat-card .label {
          font-size: 0.75rem;
          text-transform: uppercase;
          letter-spacing: 0.1em;
          color: var(--text-secondary);
        }

        .value-wrap {
          display: flex;
          align-items: baseline;
          gap: 0.5rem;
        }

        .value-wrap .value {
          font-size: 2.5rem;
          font-weight: 800;
          color: var(--text-primary);
        }

        .value-wrap .unit {
          font-size: 0.8rem;
          color: var(--text-dim);
        }

        /* Filter Bar */
        .filter-bar {
          display: flex;
          gap: 2rem;
          align-items: flex-end;
          padding: 1.5rem 2rem;
          background: var(--white);
          border: 1px solid var(--border-light);
          border-radius: var(--radius-md);
          margin-bottom: 2rem;
          flex-wrap: wrap;
        }

        .filter-group {
          display: flex;
          flex-direction: column;
          gap: 0.5rem;
        }

        .filter-label {
          font-size: 0.7rem;
          text-transform: uppercase;
          letter-spacing: 0.1em;
          color: var(--text-dim);
          font-weight: 700;
        }

        .filter-options {
          display: flex;
          gap: 0.35rem;
        }

        .filter-btn {
          padding: 0.4rem 0.85rem;
          border: 1px solid var(--border-light);
          border-radius: 20px;
          background: transparent;
          color: var(--text-secondary);
          font-size: 0.78rem;
          font-weight: 600;
          cursor: pointer;
          transition: all 0.15s;
          white-space: nowrap;
        }

        .filter-btn:hover {
          border-color: var(--primary-blue);
          color: var(--primary-blue);
        }

        .filter-btn.active {
          background: var(--primary-blue);
          color: var(--white);
          border-color: var(--primary-blue);
        }

        .clear-filters-btn {
          background: none;
          border: none;
          color: var(--text-dim);
          font-size: 0.78rem;
          cursor: pointer;
          text-decoration: underline;
          padding: 0.4rem 0;
          margin-left: auto;
        }

        .clear-filters-btn:hover {
          color: var(--text-primary);
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
          background: var(--white);
          padding: 2rem;
          border-radius: var(--radius-md);
          border: 1px solid var(--border-light);
          box-shadow: var(--shadow-sm);
        }

        .deal-card:hover {
          border-color: var(--primary-blue);
          box-shadow: var(--shadow-md);
        }

        .card-top {
          display: flex;
          justify-content: space-between;
          align-items: flex-start;
          margin-bottom: 1.5rem;
          gap: 0.5rem;
        }

        .tag-row {
          display: flex;
          gap: 0.35rem;
          flex-wrap: wrap;
        }

        .fit-tag {
          font-size: 0.65rem;
          font-weight: 800;
          text-transform: uppercase;
          padding: 0.2rem 0.6rem;
          border-radius: 4px;
          letter-spacing: 0.03em;
        }

        .tag-high {
          background: #dcfce7;
          color: #166534;
        }
        .tag-medium {
          background: #fef9c3;
          color: #854d0e;
        }
        .tag-low {
          background: #f3f4f6;
          color: #6b7280;
        }

        .tag-own-bootstrapped {
          background: #dbeafe;
          color: #1e40af;
        }
        .tag-own-angel {
          background: #ede9fe;
          color: #5b21b6;
        }
        .tag-own-vc {
          background: #fef3c7;
          color: #92400e;
        }
        .tag-own-unknown {
          background: #f3f4f6;
          color: #6b7280;
        }

        .tag-grow-fast {
          background: #d1fae5;
          color: #065f46;
        }
        .tag-grow-steady {
          background: #e0f2fe;
          color: #0c4a6e;
        }

        .source-label {
          font-size: 0.65rem;
          color: var(--text-dim);
          text-transform: uppercase;
          letter-spacing: 0.05em;
          white-space: nowrap;
          flex-shrink: 0;
        }

        h4 {
          font-size: 1.4rem;
          margin-bottom: 0.5rem;
          letter-spacing: -0.01em;
        }

        .sector {
          color: var(--primary-blue);
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
          background: var(--primary-blue-light);
          color: var(--primary-blue);
          border: 1.5px solid transparent;
          padding: 0.525rem 1.25rem;
          border-radius: 6px;
          font-size: 0.75rem;
          font-weight: 700;
          cursor: pointer;
          transition: all 0.2s;
        }

        .button-action:hover {
          background: var(--primary-blue);
          color: var(--white);
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
