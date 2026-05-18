'use client';

import { useEffect, useState } from "react";
import Link from 'next/link';
import { CompanyTarget, ActivityEntry, DEAL_STAGES } from "../types";
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

// ── Stage helpers ────────────────────────────────────────────────────────────

const PIPELINE_STAGES = ['Qualified', 'Contacted', 'Meeting', 'DD', 'Offer'] as const;
const STAGE_LABELS: Record<string, string> = {
  'Qualified': 'Qualified',
  'Contacted': 'Contacted',
  'Meeting': 'Meeting',
  'DD': 'Due Diligence',
  'Offer': 'Offer',
  'Won': 'Won',
  'Lost': 'Lost',
  'Engaged': 'Engaged',
};

function getNextStage(current: string): string | null {
  const idx = PIPELINE_STAGES.indexOf(current as any);
  if (idx === -1 || idx >= PIPELINE_STAGES.length - 1) return null;
  return PIPELINE_STAGES[idx + 1];
}

function stageColor(stage: string): string {
  const colors: Record<string, string> = {
    'Qualified': '#3b82f6',
    'Contacted': '#8b5cf6',
    'Meeting': '#f59e0b',
    'DD': '#ef4444',
    'Offer': '#10b981',
    'Won': '#059669',
    'Lost': '#6b7280',
    'Engaged': '#8b5cf6',
  };
  return colors[stage] || '#6b7280';
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
  const [filterStage, setFilterStage] = useState<'all' | string>('all');

  // Deal lifecycle states
  const [updatingStatus, setUpdatingStatus] = useState<string | null>(null);
  const [noteModal, setNoteModal] = useState<{ company: string } | null>(null);
  const [noteText, setNoteText] = useState("");
  const [savingNote, setSavingNote] = useState(false);
  const [activityModal, setActivityModal] = useState<{ company: string; activity: ActivityEntry[] } | null>(null);
  const [loadingActivity, setLoadingActivity] = useState(false);

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

  // ── Deal lifecycle handlers ──────────────────────────────────────────────

  const handleAdvanceStage = async (companyName: string, newStatus: string) => {
    setUpdatingStatus(companyName);
    try {
      await dealApi.updateCompanyStatus(companyName, newStatus);
      await loadData();
    } catch (error) {
      alert(`Failed to update status: ${error}`);
    } finally {
      setUpdatingStatus(null);
    }
  };

  const handleMarkWon = async (companyName: string) => {
    if (!confirm(`Mark ${companyName} as Won?`)) return;
    await handleAdvanceStage(companyName, 'Won');
  };

  const handleMarkLost = async (companyName: string) => {
    if (!confirm(`Mark ${companyName} as Lost?`)) return;
    await handleAdvanceStage(companyName, 'Lost');
  };

  const handleSaveNote = async () => {
    if (!noteModal || !noteText.trim()) return;
    setSavingNote(true);
    try {
      await dealApi.addCompanyNote(noteModal.company, noteText.trim());
      setNoteModal(null);
      setNoteText("");
    } catch (error) {
      alert(`Failed to save note: ${error}`);
    } finally {
      setSavingNote(false);
    }
  };

  const handleShowActivity = async (companyName: string) => {
    setLoadingActivity(true);
    try {
      const result = await dealApi.getCompanyActivity(companyName);
      setActivityModal({ company: companyName, activity: result.activity });
    } catch (error) {
      alert(`Failed to load activity: ${error}`);
    } finally {
      setLoadingActivity(false);
    }
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
    if (filterOwnership !== 'all') {
      matchesOwnership = ownershipCategory(c) === filterOwnership;
    }

    let matchesGrowth = true;
    if (filterGrowth !== 'all') {
      const level = growthCategory(c);
      if (filterGrowth === 'fast') matchesGrowth = level === 'fast';
      else if (filterGrowth === 'steady') matchesGrowth = level === 'fast' || level === 'steady';
    }

    let matchesStage = true;
    if (filterStage !== 'all') {
      matchesStage = c.status === filterStage;
    }

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
            <p className="subtitle">Track deals from qualification through to close.</p>
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

        {/* Stage funnel stats */}
        <section className="stage-funnel">
          {PIPELINE_STAGES.map((stage, i) => (
            <button
              key={stage}
              className={`stage-pill ${filterStage === stage ? 'active' : ''}`}
              style={{ '--stage-color': stageColor(stage) } as React.CSSProperties}
              onClick={() => setFilterStage(filterStage === stage ? 'all' : stage)}
            >
              <span className="stage-count">{stageCounts[stage] || 0}</span>
              <span className="stage-name">{STAGE_LABELS[stage]}</span>
              {i < PIPELINE_STAGES.length - 1 && <span className="stage-arrow">&rarr;</span>}
            </button>
          ))}
        </section>

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
            <span className="label">Active Pipeline</span>
            <div className="value-wrap">
              <span className="value">{stats.qualified}</span>
              <span className="unit">In pipeline</span>
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
            <button className="clear-filters-btn" onClick={() => { setFilterSaaS('all'); setFilterOwnership('all'); setFilterGrowth('all'); setFilterStage('all'); }}>
              Clear all filters
            </button>
          )}
        </div>

        {/* Pipeline Cards */}
        <section className="pipeline-section animate-in" style={{ animationDelay: '0.5s' }}>
          <div className="section-header">
            <h3>Pipeline ({filteredPipeline.length})</h3>
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
                const nextStage = getNextStage(company.status);
                const isUpdating = updatingStatus === company.name;

                return (
                  <div key={i} className="card glass deal-card">
                    {/* Stage badge */}
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
                      </div>
                      <p className="founder-name" style={{ opacity: sanitizeContact(company.contact_name) ? 1 : 0.4 }}>
                        {sanitizeContact(company.contact_name) || 'Contact Hidden'}
                      </p>
                      <p className="founder-email" style={{ opacity: sanitizeContact(company.contact_email) ? 1 : 0.4 }}>
                        {sanitizeContact(company.contact_email) || 'Sync required for email'}
                      </p>
                    </div>

                    {/* Deal lifecycle actions */}
                    <div className="lifecycle-actions">
                      {nextStage && (
                        <button
                          className="advance-btn"
                          onClick={() => handleAdvanceStage(company.name, nextStage)}
                          disabled={isUpdating}
                          style={{ '--btn-color': stageColor(nextStage) } as React.CSSProperties}
                        >
                          {isUpdating ? 'Updating...' : `Advance to ${STAGE_LABELS[nextStage]}`} &rarr;
                        </button>
                      )}
                      {company.status === 'Offer' && (
                        <button className="won-btn" onClick={() => handleMarkWon(company.name)} disabled={isUpdating}>
                          Mark Won
                        </button>
                      )}
                      <button className="lost-btn" onClick={() => handleMarkLost(company.name)} disabled={isUpdating}>
                        Lost
                      </button>
                    </div>

                    <div className="card-footer">
                      <div className="footer-left">
                        <button className="note-btn" onClick={() => setNoteModal({ company: company.name })}>
                          + Note
                        </button>
                        <button className="activity-btn" onClick={() => handleShowActivity(company.name)}>
                          Activity
                        </button>
                      </div>
                      <a href={company.website} target="_blank" rel="noreferrer" className="view-link">Visit &nearr;</a>
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

      {/* Note Modal */}
      {noteModal && (
        <div className="modal-overlay" onClick={() => { setNoteModal(null); setNoteText(""); }}>
          <div className="modal-content note-modal" onClick={e => e.stopPropagation()}>
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

      {/* Activity Timeline Modal */}
      {activityModal && (
        <div className="modal-overlay" onClick={() => setActivityModal(null)}>
          <div className="modal-content activity-modal" onClick={e => e.stopPropagation()}>
            <div className="modal-header">
              <h3>Activity — {activityModal.company}</h3>
              <button className="modal-close" onClick={() => setActivityModal(null)}>&times;</button>
            </div>
            <div className="modal-body">
              {activityModal.activity.length === 0 ? (
                <p className="no-activity">No activity recorded yet.</p>
              ) : (
                <div className="timeline">
                  {activityModal.activity.map((entry) => (
                    <div key={entry.id} className="timeline-entry">
                      <div className="timeline-dot" style={{
                        background: entry.action_type === 'status_change' ? stageColor(entry.new_status || '') : entry.action_type === 'note' ? '#6b7280' : '#f59e0b'
                      }} />
                      <div className="timeline-content">
                        <div className="timeline-header-row">
                          <span className="timeline-type">
                            {entry.action_type === 'status_change' ? 'Stage Change' : entry.action_type === 'note' ? 'Note' : 'Outreach'}
                          </span>
                          <span className="timeline-date">
                            {new Date(entry.created_at).toLocaleDateString('en-GB', { day: 'numeric', month: 'short', year: 'numeric', hour: '2-digit', minute: '2-digit' })}
                          </span>
                        </div>
                        {entry.action_type === 'status_change' && (
                          <p className="timeline-detail">
                            <span className="stage-tag" style={{ background: stageColor(entry.old_status || '') }}>{entry.old_status}</span>
                            &nbsp;&rarr;&nbsp;
                            <span className="stage-tag" style={{ background: stageColor(entry.new_status || '') }}>{entry.new_status}</span>
                          </p>
                        )}
                        {entry.note_text && entry.action_type === 'note' && (
                          <p className="timeline-note">{entry.note_text}</p>
                        )}
                        <span className="timeline-by">{entry.created_by}</span>
                      </div>
                    </div>
                  ))}
                </div>
              )}
            </div>
            <div className="modal-footer">
              <button className="modal-save" onClick={() => setActivityModal(null)}>Close</button>
            </div>
          </div>
        </div>
      )}

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
        .requalify-btn { border-color: var(--primary-blue); color: var(--primary-blue); font-weight: 700; }
        .requalify-btn:hover:not(:disabled) { background: var(--primary-blue); color: var(--white); }
        .sidebar-footer { padding: 1.5rem; border-top: 1px solid var(--border-glass); }
        .user-profile { display: flex; align-items: center; gap: 0.75rem; }
        .avatar { width: 40px; height: 40px; background: var(--primary-blue-light); color: var(--primary-blue); border-radius: 50%; display: flex; align-items: center; justify-content: center; font-weight: 900; }
        .user-info { display: flex; flex-direction: column; }
        .user-name { font-size: 0.9rem; font-weight: 700; color: var(--text-primary); }
        .user-role { font-size: 0.7rem; color: var(--text-dim); }

        .main-content {
          margin-left: 280px;
          flex: 1;
          padding: 3rem;
          width: calc(100% - 280px);
        }

        .page-header { display: flex; justify-content: space-between; align-items: flex-end; margin-bottom: 2rem; }
        .subtitle { color: var(--text-secondary); font-size: 1.1rem; margin-top: 0.5rem; }
        .search-box { display: flex; align-items: center; padding: 1rem 1.5rem; gap: 1rem; min-width: 400px; background: var(--white); border: 1.5px solid var(--border-light); border-radius: var(--radius-md); }
        .search-box input { background: transparent; border: none; color: var(--text-primary); width: 100%; outline: none; font-size: 1rem; }

        /* Stage funnel */
        .stage-funnel {
          display: flex;
          gap: 0.5rem;
          margin-bottom: 2rem;
          align-items: center;
          flex-wrap: wrap;
        }

        .stage-pill {
          display: flex;
          align-items: center;
          gap: 0.5rem;
          padding: 0.6rem 1.2rem;
          border: 1.5px solid var(--border-light);
          border-radius: 24px;
          background: var(--white);
          cursor: pointer;
          transition: all 0.2s;
          font-size: 0.85rem;
        }

        .stage-pill:hover {
          border-color: var(--stage-color);
          box-shadow: 0 0 0 1px var(--stage-color);
        }

        .stage-pill.active {
          background: var(--stage-color);
          border-color: var(--stage-color);
          color: white;
        }

        .stage-pill.active .stage-count,
        .stage-pill.active .stage-name { color: white; }

        .stage-count {
          font-weight: 800;
          font-size: 1.1rem;
          color: var(--text-primary);
        }

        .stage-name {
          font-weight: 600;
          color: var(--text-secondary);
        }

        .stage-arrow {
          color: var(--text-dim);
          font-size: 1rem;
          margin-left: 0.25rem;
        }

        .stage-pill.active .stage-arrow { color: rgba(255,255,255,0.6); }

        /* Stats */
        .dashboard-grid { display: grid; grid-template-columns: repeat(3, 1fr); gap: 2rem; margin-bottom: 2rem; }
        .stat-card { display: flex; flex-direction: column; gap: 0.5rem; padding: 1.5rem; background: var(--white); border: 1px solid var(--border-light); border-radius: var(--radius-md); }
        .stat-card .label { font-size: 0.75rem; text-transform: uppercase; letter-spacing: 0.1em; color: var(--text-secondary); }
        .value-wrap { display: flex; align-items: baseline; gap: 0.5rem; }
        .value-wrap .value { font-size: 2.5rem; font-weight: 800; color: var(--text-primary); }
        .value-wrap .unit { font-size: 0.8rem; color: var(--text-dim); }

        /* Filter Bar */
        .filter-bar { display: flex; gap: 2rem; align-items: flex-end; padding: 1.5rem 2rem; background: var(--white); border: 1px solid var(--border-light); border-radius: var(--radius-md); margin-bottom: 2rem; flex-wrap: wrap; }
        .filter-group { display: flex; flex-direction: column; gap: 0.5rem; }
        .filter-label { font-size: 0.7rem; text-transform: uppercase; letter-spacing: 0.1em; color: var(--text-dim); font-weight: 700; }
        .filter-options { display: flex; gap: 0.35rem; }
        .filter-btn { padding: 0.4rem 0.85rem; border: 1px solid var(--border-light); border-radius: 20px; background: transparent; color: var(--text-secondary); font-size: 0.78rem; font-weight: 600; cursor: pointer; transition: all 0.15s; white-space: nowrap; }
        .filter-btn:hover { border-color: var(--primary-blue); color: var(--primary-blue); }
        .filter-btn.active { background: var(--primary-blue); color: var(--white); border-color: var(--primary-blue); }
        .clear-filters-btn { background: none; border: none; color: var(--text-dim); font-size: 0.78rem; cursor: pointer; text-decoration: underline; padding: 0.4rem 0; margin-left: auto; }
        .clear-filters-btn:hover { color: var(--text-primary); }

        /* Cards */
        .section-header { display: flex; justify-content: space-between; align-items: center; margin-bottom: 2rem; }
        .cards-grid { display: grid; grid-template-columns: repeat(auto-fill, minmax(360px, 1fr)); gap: 2rem; }
        .deal-card { display: flex; flex-direction: column; background: var(--white); padding: 2rem; border-radius: var(--radius-md); border: 1px solid var(--border-light); box-shadow: var(--shadow-sm); }
        .deal-card:hover { border-color: var(--primary-blue); box-shadow: var(--shadow-md); }

        .card-top { display: flex; justify-content: space-between; align-items: flex-start; margin-bottom: 1rem; gap: 0.5rem; flex-wrap: wrap; }

        .stage-badge {
          font-size: 0.7rem;
          font-weight: 800;
          text-transform: uppercase;
          padding: 0.25rem 0.75rem;
          border-radius: 4px;
          color: white;
          letter-spacing: 0.05em;
          flex-shrink: 0;
        }

        .tag-row { display: flex; gap: 0.35rem; flex-wrap: wrap; }
        .fit-tag { font-size: 0.6rem; font-weight: 800; text-transform: uppercase; padding: 0.15rem 0.5rem; border-radius: 3px; letter-spacing: 0.03em; }
        .tag-high { background: #dcfce7; color: #166534; }
        .tag-medium { background: #fef9c3; color: #854d0e; }
        .tag-low { background: #f3f4f6; color: #6b7280; }
        .tag-own-bootstrapped { background: #dbeafe; color: #1e40af; }
        .tag-own-angel { background: #ede9fe; color: #5b21b6; }
        .tag-own-vc { background: #fef3c7; color: #92400e; }
        .tag-own-unknown { background: #f3f4f6; color: #6b7280; }
        .tag-grow-fast { background: #d1fae5; color: #065f46; }
        .tag-grow-steady { background: #e0f2fe; color: #0c4a6e; }
        .source-label { font-size: 0.65rem; color: var(--text-dim); text-transform: uppercase; letter-spacing: 0.05em; white-space: nowrap; flex-shrink: 0; }

        h4 { font-size: 1.4rem; margin-bottom: 0.5rem; letter-spacing: -0.01em; }
        .sector { color: var(--primary-blue); font-size: 0.85rem; font-weight: 700; margin-bottom: 1rem; text-transform: uppercase; }
        .description { color: var(--text-secondary); font-size: 0.95rem; margin-bottom: 1.5rem; line-height: 1.5; display: -webkit-box; -webkit-line-clamp: 3; -webkit-box-orient: vertical; overflow: hidden; }

        .meta-info { display: grid; grid-template-columns: 1fr 1fr; gap: 1rem; margin-bottom: 1.5rem; padding-bottom: 1.5rem; border-bottom: 1px solid var(--border-glass); }
        .info-item { display: flex; flex-direction: column; gap: 0.25rem; }
        .info-item .label { font-size: 0.65rem; color: var(--text-dim); text-transform: uppercase; }
        .info-item .value { font-size: 0.9rem; color: var(--text-primary); font-weight: 600; }

        .founder-highlight { padding: 1.25rem; margin-bottom: 1.5rem; border-radius: var(--radius-md); }
        .founder-header { display: flex; justify-content: space-between; align-items: center; margin-bottom: 0.75rem; }
        .founder-label { font-size: 0.7rem; text-transform: uppercase; letter-spacing: 0.05em; color: var(--gold); font-weight: 800; }
        .founder-name { font-weight: 700; color: var(--white); margin-bottom: 0.25rem; }
        .founder-email { font-size: 0.8rem; color: var(--text-secondary); }

        /* Lifecycle actions */
        .lifecycle-actions {
          display: flex;
          gap: 0.5rem;
          margin-bottom: 1rem;
          flex-wrap: wrap;
        }

        .advance-btn {
          flex: 1;
          padding: 0.6rem 1rem;
          background: var(--btn-color, var(--primary-blue));
          color: white;
          border: none;
          border-radius: 6px;
          font-size: 0.78rem;
          font-weight: 700;
          cursor: pointer;
          transition: all 0.2s;
        }
        .advance-btn:hover:not(:disabled) { opacity: 0.85; transform: translateY(-1px); }
        .advance-btn:disabled { opacity: 0.5; cursor: wait; }

        .won-btn {
          padding: 0.6rem 1rem;
          background: #059669;
          color: white;
          border: none;
          border-radius: 6px;
          font-size: 0.78rem;
          font-weight: 700;
          cursor: pointer;
        }
        .won-btn:hover:not(:disabled) { background: #047857; }

        .lost-btn {
          padding: 0.6rem 0.75rem;
          background: transparent;
          color: var(--text-dim);
          border: 1px solid var(--border-light);
          border-radius: 6px;
          font-size: 0.75rem;
          font-weight: 600;
          cursor: pointer;
        }
        .lost-btn:hover:not(:disabled) { border-color: #ef4444; color: #ef4444; }

        .card-footer {
          display: flex;
          justify-content: space-between;
          align-items: center;
          margin-top: auto;
          padding-top: 0.75rem;
          border-top: 1px solid var(--border-glass);
        }

        .footer-left { display: flex; gap: 0.5rem; }

        .note-btn, .activity-btn {
          background: transparent;
          border: 1px solid var(--border-light);
          color: var(--text-secondary);
          padding: 0.35rem 0.75rem;
          border-radius: 4px;
          font-size: 0.72rem;
          font-weight: 600;
          cursor: pointer;
          transition: all 0.15s;
        }
        .note-btn:hover { border-color: var(--primary-blue); color: var(--primary-blue); }
        .activity-btn:hover { border-color: var(--gold); color: var(--gold); }
        .view-link { font-size: 0.85rem; font-weight: 700; }

        .empty-state { grid-column: 1 / -1; padding: 5rem; text-align: center; color: var(--text-secondary); }
        .skeleton-card { height: 400px; }

        /* Modals */
        .modal-overlay {
          position: fixed;
          top: 0;
          left: 0;
          right: 0;
          bottom: 0;
          background: rgba(0, 0, 0, 0.5);
          display: flex;
          align-items: center;
          justify-content: center;
          z-index: 1000;
        }

        .modal-content {
          background: var(--white);
          border-radius: var(--radius-md);
          width: 90%;
          max-width: 560px;
          max-height: 85vh;
          display: flex;
          flex-direction: column;
          box-shadow: var(--shadow-lg, 0 10px 40px rgba(0,0,0,0.15));
        }

        .modal-header {
          display: flex;
          justify-content: space-between;
          align-items: center;
          padding: 1.5rem 2rem;
          border-bottom: 1px solid var(--border-light);
        }

        .modal-header h3 { font-size: 1.1rem; margin: 0; }

        .modal-close {
          background: none;
          border: none;
          font-size: 1.5rem;
          color: var(--text-dim);
          cursor: pointer;
          padding: 0 0.25rem;
        }

        .modal-body {
          padding: 2rem;
          overflow-y: auto;
          flex: 1;
        }

        .modal-footer {
          display: flex;
          justify-content: flex-end;
          gap: 0.75rem;
          padding: 1.25rem 2rem;
          border-top: 1px solid var(--border-light);
        }

        .modal-cancel {
          padding: 0.5rem 1.25rem;
          background: transparent;
          border: 1px solid var(--border-light);
          border-radius: 6px;
          color: var(--text-secondary);
          font-weight: 600;
          cursor: pointer;
        }

        .modal-save {
          padding: 0.5rem 1.25rem;
          background: var(--primary-blue);
          border: none;
          border-radius: 6px;
          color: white;
          font-weight: 700;
          cursor: pointer;
        }
        .modal-save:disabled { opacity: 0.5; cursor: not-allowed; }

        .note-textarea {
          width: 100%;
          padding: 1rem;
          border: 1.5px solid var(--border-light);
          border-radius: 8px;
          font-size: 0.95rem;
          font-family: inherit;
          resize: vertical;
          outline: none;
          background: var(--bg-secondary);
          color: var(--text-primary);
        }
        .note-textarea:focus { border-color: var(--primary-blue); }

        /* Activity timeline */
        .no-activity { color: var(--text-dim); text-align: center; padding: 2rem 0; }

        .timeline {
          display: flex;
          flex-direction: column;
          gap: 0;
        }

        .timeline-entry {
          display: flex;
          gap: 1rem;
          padding: 1rem 0;
          border-bottom: 1px solid var(--border-glass);
        }
        .timeline-entry:last-child { border-bottom: none; }

        .timeline-dot {
          width: 10px;
          height: 10px;
          border-radius: 50%;
          margin-top: 5px;
          flex-shrink: 0;
        }

        .timeline-content { flex: 1; }

        .timeline-header-row {
          display: flex;
          justify-content: space-between;
          align-items: center;
          margin-bottom: 0.35rem;
        }

        .timeline-type {
          font-size: 0.75rem;
          font-weight: 700;
          text-transform: uppercase;
          letter-spacing: 0.05em;
          color: var(--text-secondary);
        }

        .timeline-date {
          font-size: 0.7rem;
          color: var(--text-dim);
        }

        .timeline-detail { margin: 0.25rem 0; }

        .stage-tag {
          display: inline-block;
          font-size: 0.65rem;
          font-weight: 700;
          color: white;
          padding: 0.1rem 0.5rem;
          border-radius: 3px;
          text-transform: uppercase;
        }

        .timeline-note {
          font-size: 0.9rem;
          color: var(--text-primary);
          line-height: 1.5;
          margin: 0.25rem 0;
        }

        .timeline-by {
          font-size: 0.7rem;
          color: var(--text-dim);
        }

        @media (max-width: 1024px) {
          .sidebar { width: 80px; }
          .sidebar .logo span, .sidebar .group-label, .sidebar .nav-item, .sidebar .agent-btn, .sidebar .user-info { display: none !important; }
          .sidebar .logo { text-align: center; padding: 2rem 0; font-size: 1rem; }
          .main-content { margin-left: 80px; width: calc(100% - 80px); }
        }

        @media (max-width: 768px) {
          .page-header { flex-direction: column; align-items: flex-start; gap: 1.5rem; }
          .search-box { width: 100%; min-width: 0; }
          .filter-bar { flex-direction: column; }
          .stage-funnel { flex-direction: column; }
        }
      `}</style>
    </div>
  );
}
