'use client';

import { useEffect, useState } from "react";
import Link from 'next/link';
import { CompanyTarget } from "../../types";
import { dealApi } from "../../services/api";

export default function Universe() {
  const [universe, setUniverse] = useState<CompanyTarget[]>([]);
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    async function loadData() {
      try {
        const data = await dealApi.getUniverse();
        setUniverse(data);
      } catch (error) {
        console.error("Failed to load universe", error);
      } finally {
        setLoading(false);
      }
    }
    loadData();
  }, []);

  return (
    <main className="container">
      <header className="navbar">
        <div className="logo">AVERROES CAPITAL</div>
        <nav style={{ display: 'flex', gap: '1.5rem', alignItems: 'center' }}>
          <Link href="/" style={{ color: 'var(--white)', opacity: 0.7, textDecoration: 'none', fontWeight: 600 }}>Active Pipeline</Link>
          <Link href="/universe" style={{ color: 'var(--gold)', textDecoration: 'none', fontWeight: 600 }}>Master Universe</Link>
          <button className="button" onClick={() => window.location.reload()}>
            Refresh Sync ↻
          </button>
        </nav>
      </header>

      <section className="hero">
        <h1>Master Universe</h1>
        <p className="subtitle">All raw targets sourced across the entire market dataset.</p>
      </section>

      <section className="table-container glass">
        {loading ? (
          <div className="skeleton">Loading universe data...</div>
        ) : (
          <table className="universe-table">
            <thead>
              <tr>
                <th>Company</th>
                <th>Sector</th>
                <th>Region</th>
                <th>Ownership</th>
                <th>Match Score</th>
                <th>Source</th>
              </tr>
            </thead>
            <tbody>
              {[...universe].sort((a, b) => b.match_score - a.match_score).map((company, i) => (
                <tr key={i}>
                  <td style={{ fontWeight: 700, color: 'var(--white)' }}>
                    <a href={company.website} target="_blank" rel="noreferrer" style={{color: 'inherit', textDecoration: 'none'}}>
                      {company.name} ↗
                    </a>
                  </td>
                  <td>{company.sector}</td>
                  <td>{company.region || 'Unknown'}</td>
                  <td>{company.ownership || 'Unknown'}</td>
                  <td>
                    <span className="badge-growth" style={{
                      background: company.match_score >= 0.4 ? 'rgba(100, 255, 218, 0.1)' : 'rgba(255, 100, 100, 0.1)',
                      color: company.match_score >= 0.4 ? 'var(--green)' : '#ff6b6b'
                    }}>
                      {Math.round(company.match_score * 100)}%
                    </span>
                  </td>
                  <td>{company.source}</td>
                </tr>
              ))}
              {universe.length === 0 && (
                <tr>
                  <td colSpan={6} style={{ textAlign: 'center', padding: '2rem', fontStyle: 'italic', color: 'var(--slate)' }}>
                    No companies found in the Master Universe. Trigger a scraper to populate.
                  </td>
                </tr>
              )}
            </tbody>
          </table>
        )}
      </section>

      <style jsx>{`
        .hero {
          padding: 3rem 0 2rem;
          text-align: center;
        }
        
        .subtitle {
          color: var(--slate);
          font-size: 1.25rem;
          margin-bottom: 2rem;
        }

        .table-container {
          overflow-x: auto;
          border-radius: var(--radius-lg);
          padding: 1px;
        }

        .universe-table {
          width: 100%;
          border-collapse: collapse;
          text-align: left;
        }

        .universe-table th, .universe-table td {
          padding: 1.25rem 1.5rem;
          border-bottom: 1px solid rgba(255, 255, 255, 0.05);
        }

        .universe-table th {
          color: var(--gold);
          font-size: 0.85rem;
          text-transform: uppercase;
          letter-spacing: 0.1em;
          font-weight: 600;
          background: rgba(10, 25, 47, 0.8);
        }

        .universe-table tr:hover td {
          background: rgba(212, 175, 55, 0.05);
        }

        .universe-table td {
          color: var(--light-slate);
          font-size: 0.95rem;
        }

        .skeleton {
          padding: 4rem;
          text-align: center;
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
