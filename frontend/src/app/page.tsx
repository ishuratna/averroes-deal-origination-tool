'use client';

import { useEffect, useState } from "react";
import { CompanyTarget } from "../types";
import { dealApi } from "../services/api";

export default function Home() {
  const [pipeline, setPipeline] = useState<CompanyTarget[]>([]);
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

  const renderColumn = (status: string, title: string) => {
    const filtered = pipeline.filter(c => c.status === status);
    return (
      <div className="pipeline-column">
        <h3>{title} ({filtered.length})</h3>
        {filtered.length === 0 && !loading && (
          <div className="card glass empty">
            <p>No companies currently in {title.toLowerCase()}.</p>
          </div>
        )}
        {filtered.map((company, i) => (
          <div key={i} className="card glass">
            <div className="card-header">
              <span className="badge-growth" style={{
                background: company.match_score > 0.9 ? 'rgba(100, 255, 218, 0.1)' : 'rgba(212, 175, 55, 0.1)',
                color: company.match_score > 0.9 ? 'var(--green)' : 'var(--gold)'
              }}>
                {Math.round(company.match_score * 100)}% Match
              </span>
              <h4>{company.name}</h4>
            </div>
            <p>{company.sector} | {company.source}</p>
            <p className="description">{company.description}</p>
            <div className="metrics">
              <a href={company.website} target="_blank" rel="noreferrer" className="link-website">
                Visit Website ↗
              </a>
            </div>
          </div>
        ))}
        {loading && <div className="skeleton">Loading targets...</div>}
      </div>
    );
  };

  return (
    <main className="container">
      <header className="navbar">
        <div className="logo">AVERROES CAPITAL</div>
        <nav>
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
          />
          <button className="button-search">Analyze Target</button>
        </div>
      </section>

      <section className="pipeline-grid">
        {renderColumn('Qualified', 'Sourced Targets')}
        {renderColumn('Under Review', 'AI Enrichment')}
        {renderColumn('Engaged', 'Verified Pipeline')}
      </section>

      <style jsx>{`
        .hero {
          padding: 4rem 0;
          text-align: center;
        }
        
        .subtitle {
          color: var(--slate);
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
          background: rgba(255, 255, 255, 0.05);
          border: 1px solid rgba(255, 255, 255, 0.1);
          border-radius: var(--radius-sm);
          padding: 1rem;
          color: var(--white);
          font-size: 1rem;
        }

        .input-main:focus {
          outline: none;
          border-color: var(--gold);
        }

        .button-search {
          background: var(--gold);
          color: var(--navy);
          border: none;
          padding: 1rem 2rem;
          border-radius: var(--radius-sm);
          font-weight: 700;
          cursor: pointer;
        }

        .pipeline-grid {
          display: grid;
          grid-template-columns: repeat(3, 1fr);
          gap: 2rem;
          margin-top: 3rem;
          padding-bottom: 5rem;
        }

        .pipeline-column h3 {
          font-size: 1rem;
          text-transform: uppercase;
          letter-spacing: 0.1em;
          color: var(--slate);
          border-bottom: 1px solid var(--navy-lighter);
          padding-bottom: 1rem;
          margin-bottom: 1.5rem;
        }

        .card {
          margin-bottom: 1.5rem;
          transition: transform var(--transition-fast);
        }

        .card:hover {
          transform: translateY(-5px);
          border-color: var(--gold);
        }

        .card-header {
          display: flex;
          flex-direction: row-reverse;
          justify-content: space-between;
          align-items: center;
          margin-bottom: 0.5rem;
        }

        .card h4 {
          margin: 0;
          font-size: 1.1rem;
        }

        .card p {
          font-size: 0.9rem;
          color: var(--slate);
          margin-bottom: 0.5rem;
        }

        .description {
          font-size: 0.85rem !important;
          color: var(--light-slate) !important;
          font-style: italic;
          display: -webkit-box;
          -webkit-line-clamp: 2;
          -webkit-box-orient: vertical;
          overflow: hidden;
        }

        .metrics {
          display: flex;
          gap: 1rem;
          border-top: 1px solid rgba(255, 255, 255, 0.05);
          padding-top: 1rem;
          margin-top: 1rem;
        }

        .link-website {
          font-size: 0.75rem;
          color: var(--gold);
          font-weight: 600;
        }

        .empty {
          border: 2px dashed rgba(255, 255, 255, 0.05);
          text-align: center;
          padding: 4rem 2rem;
          color: var(--slate);
        }

        .skeleton {
          padding: 2rem;
          color: var(--slate);
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
