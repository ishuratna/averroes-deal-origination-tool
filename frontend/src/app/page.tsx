'use client';

import { useEffect, useState } from "react";
import Link from 'next/link';
import { CompanyTarget } from "../types";
import { dealApi } from "../services/api";

export default function Home() {
  const [pipeline, setPipeline] = useState<CompanyTarget[]>([]);
  const [searchQuery, setSearchQuery] = useState("");
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    async function loadData() {
      try {
        const data = await dealApi.getPipeline();
        setPipeline(data);
      } catch (error) {
        console.error("Failed to load pipeline", error);
      } finally {
        setLoading(false);
      }
    }
    loadData();
  }, []);


  const renderTop10Pipeline = () => {
    // Filter by search query
    const filteredPipeline = pipeline.filter(c => {
      const q = searchQuery.toLowerCase();
      return (
        c.name.toLowerCase().includes(q) ||
        (c.sector && c.sector.toLowerCase().includes(q)) ||
        (c.description && c.description.toLowerCase().includes(q))
      );
    });

    // Sort by match_score descending, take top 10
    const top10 = [...filteredPipeline]
      .sort((a, b) => b.match_score - a.match_score)
      .slice(0, 10);

    return (
      <div className="pipeline-column-full">
        <div className="column-header">
           <h3>Current Pipeline (Top 10)</h3>
           <span className="count-badge">{top10.length}</span>
        </div>
        {top10.length === 0 && !loading && (
          <div className="card glass empty">
            <p>No companies currently in pipeline. Trigger ingestion to find targets.</p>
          </div>
        )}
        
        <div className="cards-grid">
          {top10.map((company, i) => (
            <div key={i} className="card glass">
              <div className="card-header">
                <span className="badge-growth" style={{
                  background: company.match_score >= 0.8 ? 'rgba(100, 255, 218, 0.1)' : 'rgba(212, 175, 55, 0.1)',
                  color: company.match_score >= 0.8 ? 'var(--green)' : 'var(--gold)'
                }}>
                  {Math.round(company.match_score * 100)}% Match
                </span>
                <div className="source-tag">{company.source}</div>
              </div>
              
              <h4>{company.name}</h4>
              <p className="sector-tag">{company.sector}</p>
              <p className="description">{company.description}</p>
              
              <div className="founder-box">
                <p className="founder-label">Founder / CEO</p>
                <p className="founder-name" style={{ opacity: company.contact_name ? 1 : 0.5 }}>
                  {company.contact_name || 'N/A'}
                </p>
                {company.linkedin_url && (
                  <a href={company.linkedin_url} target="_blank" rel="noreferrer" className="founder-linkedin">
                    View LinkedIn ↗
                  </a>
                )}
                <p className="founder-email" style={{ opacity: company.contact_email ? 1 : 0.5 }}>
                  {company.contact_email || 'N/A'}
                </p>
              </div>

              <div className="card-footer">
                {company.website ? (
                   <a href={company.website} target="_blank" rel="noreferrer" className="link-website">
                    View Site ↗
                  </a>
                ) : (
                  <span className="link-website disabled">No Website</span>
                )}
              </div>
            </div>
          ))}
        </div>
        {loading && <div className="skeleton">Loading pipeline...</div>}
      </div>
    );
  };

  return (
    <main className="container">
      <header className="navbar">
        <div className="logo">AVERROES CAPITAL</div>
        <nav style={{ display: 'flex', gap: '1.5rem', alignItems: 'center' }}>
          <Link href="/" style={{ color: 'var(--gold)', textDecoration: 'none', fontWeight: 600 }}>Active Pipeline</Link>
          <Link href="/universe" style={{ color: 'var(--navy)', opacity: 0.7, textDecoration: 'none', fontWeight: 600 }}>Master Universe</Link>
          <button className="button" onClick={() => window.location.reload()}>
            Refresh Sync ↻
          </button>
        </nav>
      </header>

      <section className="hero">
        <h1>Deal Origination Tool</h1>
        <p className="subtitle">AI-Powered intelligence for high-conviction targets.</p>
        
        <div className="glass search-bar">
          <input 
            type="text" 
            placeholder="Search company, sector, or investment thesis..." 
            className="input-main"
            value={searchQuery}
            onChange={(e) => setSearchQuery(e.target.value)}
          />
          <button className="button-search">Analyze Target</button>
        </div>
      </section>

      <section className="pipeline-grid" style={{ gridTemplateColumns: '1fr' }}>
        {renderTop10Pipeline()}
      </section>

      <style jsx>{`
        .hero {
          padding: 4rem 0;
          text-align: center;
        }
        
        .subtitle {
          color: var(--text-secondary);
          font-size: 1.25rem;
          margin-bottom: 2rem;
        }

        .search-bar {
          display: flex;
          gap: 1rem;
          max-width: 800px;
          margin: 0 auto;
          margin-top: 2rem;
        }

        .input-main {
          flex: 1;
          background: var(--bg-secondary);
          border: 1px solid var(--light-gray);
          border-radius: var(--radius-sm);
          padding: 1rem;
          color: var(--text-primary);
          font-size: 1rem;
          box-shadow: inset 0 2px 4px rgba(0,0,0,0.02);
        }

        .input-main:focus {
          outline: none;
          border-color: var(--gold);
          background: var(--white);
        }

        .button-search {
          background: var(--navy);
          color: var(--white);
          border: none;
          padding: 1rem 2rem;
          border-radius: var(--radius-sm);
          font-weight: 700;
          cursor: pointer;
          transition: background 0.2s;
        }

        .button-search:hover {
          background: var(--gold);
        }

        .pipeline-grid {
          display: grid;
          grid-template-columns: 1fr;
          gap: 2rem;
          margin-top: 3rem;
          padding-bottom: 5rem;
        }

        .cards-grid {
          display: grid;
          grid-template-columns: repeat(auto-fill, minmax(320px, 1fr));
          gap: 1.5rem;
        }

        .pipeline-column-full {
          width: 100%;
        }

        .column-header {
          display: flex;
          justify-content: space-between;
          align-items: center;
          border-bottom: 2px solid var(--light-gray);
          padding-bottom: 1rem;
          margin-bottom: 1.5rem;
        }

        .column-header h3 {
          font-size: 0.9rem;
          text-transform: uppercase;
          letter-spacing: 0.15em;
          color: var(--text-secondary);
          margin: 0;
        }

        .count-badge {
          background: var(--bg-secondary);
          color: var(--gold);
          font-size: 0.75rem;
          font-weight: 700;
          padding: 0.2rem 0.6rem;
          border-radius: 10px;
          border: 1px solid var(--light-gray);
        }

        .card h4 {
          color: var(--text-primary);
          font-size: 1.15rem;
        }

        .source-tag {
          font-size: 0.65rem;
          text-transform: uppercase;
          letter-spacing: 0.05em;
          color: var(--text-secondary);
        }

        .sector-tag {
          font-size: 0.8rem !important;
          color: var(--gold) !important;
          font-weight: 600;
          margin-bottom: 0.75rem !important;
        }

        .description {
          color: var(--text-secondary);
          font-size: 0.9rem;
        }

        .founder-box {
          background: var(--bg-secondary);
          border-left: 3px solid var(--gold);
          padding: 0.75rem;
          margin: 1rem 0;
          border-radius: 0 4px 4px 0;
        }

        .founder-label {
          font-size: 0.65rem !important;
          text-transform: uppercase;
          color: var(--text-secondary) !important;
          margin-bottom: 0.25rem !important;
          letter-spacing: 0.05em;
        }

        .founder-name {
          font-size: 0.9rem !important;
          color: var(--text-primary) !important;
          font-weight: 600;
          margin-bottom: 0.1rem !important;
        }

        .founder-email {
          font-size: 0.75rem !important;
          color: var(--gold) !important;
          margin: 0 !important;
        }

        .founder-linkedin {
          display: block;
          font-size: 0.7rem;
          color: var(--navy);
          font-weight: 700;
          text-decoration: underline;
          margin-bottom: 0.25rem;
          transition: color 0.2s;
        }

        .founder-linkedin:hover {
          color: var(--gold);
        }

        .card-footer {
          display: flex;
          justify-content: space-between;
          align-items: center;
          margin-top: 1rem;
          padding-top: 1rem;
          border-top: 1px solid var(--light-gray);
        }

        .link-website {
          font-size: 0.85rem;
          font-weight: 600;
          color: var(--navy);
        }

        .button-tiny {
          background: transparent;
          border: 1px solid var(--navy);
          color: var(--navy);
          font-size: 0.7rem;
          padding: 0.3rem 0.6rem;
          border-radius: 4px;
          cursor: pointer;
          font-weight: 600;
          transition: all 0.2s ease;
        }

        .button-tiny:hover {
          background: var(--navy);
          color: var(--white);
        }

        .empty {
          border: 2px dashed var(--light-gray);
          background: var(--bg-secondary);
          text-align: center;
          padding: 4rem 2rem;
          color: var(--text-secondary);
        }

        .skeleton {
          padding: 2rem;
          color: var(--text-secondary);
          font-style: italic;
          opacity: 0.6;
          animation: pulse 1.5s infinite;
        }

        @keyframes pulse {
          0% { opacity: 0.4; }
          50% { opacity: 0.8; }
          100% { opacity: 0.4; }
        }
      `}</style>
    </main>
  );
}
