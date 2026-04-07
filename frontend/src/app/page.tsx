import Image from "next/image";
import styles from "./page.module.css";

export default function Home() {
  return (
    <main className="container">
      <header className="navbar">
        <div className="logo">AVERROES CAPITAL</div>
        <nav>
          <button className="button">
            New Source +
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
        <div className="pipeline-column">
          <h3>Qualified Targets</h3>
          <div className="card glass">
            <div className="card-header">
              <span className="badge-growth">92% Match</span>
              <h4>SaaS Synergy Corp</h4>
            </div>
            <p>B2B Infrastructure | $5M-$10M EBITDA</p>
            <div className="metrics">
              <div className="metric"><span>Rule of 40</span> <strong>45%</strong></div>
              <div className="metric"><span>Growth</span> <strong>28% YoY</strong></div>
            </div>
          </div>
          
          <div className="card glass">
            <div className="card-header">
              <span className="badge-growth">88% Match</span>
              <h4>Nexus Flow Ltd</h4>
            </div>
            <p>FinTech Enabler | $2M-$4M EBITDA</p>
            <div className="metrics">
              <div className="metric"><span>Rule of 40</span> <strong>38%</strong></div>
              <div className="metric"><span>Growth</span> <strong>35% YoY</strong></div>
            </div>
          </div>
        </div>

        <div className="pipeline-column">
          <h3>Under Review</h3>
          <div className="card glass empty">
            <p>Drop companies here to start AI enrichment.</p>
          </div>
        </div>
        
        <div className="pipeline-column">
          <h3>Engaged</h3>
          <div className="card glass">
            <div className="card-header">
              <span className="badge-growth" style={{background: 'rgba(212, 175, 55, 0.1)', color: 'var(--gold)'}}>Verified</span>
              <h4>Alpha Logic</h4>
            </div>
            <p>Service-Led Tech | $12M EBITDA</p>
            <p className="contact-info">CEO: Sarah Jenkins | 077 1234 5678</p>
          </div>
        </div>
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
          margin-bottom: 1rem;
        }

        .metrics {
          display: flex;
          gap: 1rem;
          border-top: 1px solid rgba(255, 255, 255, 0.05);
          padding-top: 1rem;
        }

        .metric {
          font-size: 0.75rem;
          color: var(--light-slate);
          display: flex;
          flex-direction: column;
        }

        .metric strong {
          color: var(--white);
          font-size: 1rem;
        }

        .empty {
          border: 2px dashed rgba(255, 255, 255, 0.05);
          text-align: center;
          padding: 4rem 2rem;
          color: var(--slate);
        }

        .contact-info {
          font-style: italic;
          border-top: 1px solid rgba(255, 255, 255, 0.05);
          margin-top: 0.5rem;
        }
      `}</style>
    </main>
  );
}
