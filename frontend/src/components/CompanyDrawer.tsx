'use client';

import { useState, useEffect } from 'react';
import { CompanyTarget, ActivityEntry } from '../types';
import { dealApi } from '../services/api';

interface CompanyDrawerProps {
  company: CompanyTarget | null;
  onClose: () => void;
  onStatusChange?: (companyName: string, newStatus: string) => void;
}

export default function CompanyDrawer({ company, onClose, onStatusChange }: CompanyDrawerProps) {
  const [activeTab, setActiveTab] = useState<'overview' | 'financials' | 'contacts' | 'activity'>('overview');
  const [activity, setActivity] = useState<ActivityEntry[]>([]);
  const [loadingActivity, setLoadingActivity] = useState(false);
  const [noteText, setNoteText] = useState('');
  const [savingNote, setSavingNote] = useState(false);

  useEffect(() => {
    if (company && activeTab === 'activity') {
      loadActivity();
    }
  }, [company, activeTab]);

  useEffect(() => {
    setActiveTab('overview');
    setActivity([]);
    setNoteText('');
  }, [company?.name]);

  async function loadActivity() {
    if (!company) return;
    setLoadingActivity(true);
    try {
      const result = await dealApi.getCompanyActivity(company.name);
      setActivity(result.activity);
    } catch (e) { console.error(e); }
    finally { setLoadingActivity(false); }
  }

  async function handleSaveNote() {
    if (!company || !noteText.trim()) return;
    setSavingNote(true);
    try {
      await dealApi.addCompanyNote(company.name, noteText.trim());
      setNoteText('');
      await loadActivity();
    } catch (e) { alert(`Failed to save note: ${e}`); }
    finally { setSavingNote(false); }
  }

  function stageColor(stage: string): string {
    const colors: Record<string, string> = {
      'Qualified': '#3b82f6', 'Contacted': '#8b5cf6', 'Meeting': '#f59e0b',
      'DD': '#ef4444', 'Offer': '#10b981', 'Won': '#059669', 'Lost': '#6b7280',
      'Engaged': '#8b5cf6', 'Under Review': '#d97706', 'Scraped': '#94a3b8',
      'Uploaded': '#3b82f6', 'Not a Fit': '#ef4444',
    };
    return colors[stage] || '#6b7280';
  }

  const formatCurrency = (val?: number) => val ? `£${val.toFixed(1)}M` : '—';
  const formatNum = (val?: number) => val ? val.toLocaleString() : '—';
  const formatDate = (d?: string) => d ? new Date(d).toLocaleDateString('en-GB', { day: 'numeric', month: 'short', year: 'numeric' }) : '—';
  const formatPct = (val?: number) => val ? `${val > 0 ? '+' : ''}${val.toFixed(1)}%` : '—';

  if (!company) return null;

  return (
    <>
      <div className="drawer-overlay" onClick={onClose} />
      <div className="drawer-panel">
        {/* Header */}
        <div className="drawer-header">
          <div className="drawer-header-top">
            <button className="drawer-close" onClick={onClose}>
              <svg width="20" height="20" viewBox="0 0 20 20" fill="none"><path d="M15 5L5 15M5 5l10 10" stroke="currentColor" strokeWidth="2" strokeLinecap="round"/></svg>
            </button>
          </div>
          <div className="drawer-company-header">
            <h2 className="drawer-company-name">{company.name}</h2>
            <span className="drawer-status-badge" style={{ background: stageColor(company.status) }}>
              {company.status}
            </span>
          </div>
          {company.website && (
            <a href={company.website} target="_blank" rel="noreferrer" className="drawer-website">
              {company.website.replace(/^https?:\/\/(www\.)?/, '').replace(/\/$/, '')}
              <svg width="12" height="12" viewBox="0 0 12 12" fill="none"><path d="M3.5 8.5l5-5M4 3.5h4.5V8" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round" strokeLinejoin="round"/></svg>
            </a>
          )}
          {company.description && (
            <p className="drawer-description">{company.description}</p>
          )}
        </div>

        {/* Tabs */}
        <div className="drawer-tabs">
          {(['overview', 'financials', 'contacts', 'activity'] as const).map(tab => (
            <button
              key={tab}
              className={`drawer-tab ${activeTab === tab ? 'active' : ''}`}
              onClick={() => setActiveTab(tab)}
            >
              {tab.charAt(0).toUpperCase() + tab.slice(1)}
            </button>
          ))}
        </div>

        {/* Tab Content */}
        <div className="drawer-body">
          {activeTab === 'overview' && (
            <div className="drawer-section">
              <div className="detail-grid">
                <DetailRow label="Sector" value={company.sector} />
                <DetailRow label="Region" value={company.region || company.hq_location || company.hq_country} />
                <DetailRow label="HQ City" value={company.hq_city} />
                <DetailRow label="Employees" value={formatNum(company.employees)} />
                <DetailRow label="Founded" value={company.year_founded?.toString()} />
                <DetailRow label="Age" value={company.year_founded ? `${new Date().getFullYear() - company.year_founded} years` : undefined} />
                <DetailRow label="Ownership" value={company.ownership} />
                <DetailRow label="Business Status" value={company.business_status} />
                <DetailRow label="Source" value={company.source} />
                <DetailRow label="Date Added" value={formatDate(company.ingested_at)} />
                <DetailRow label="Keywords" value={company.keywords} />
                <DetailRow label="Verticals" value={company.verticals} />
                <DetailRow label="Industry Group" value={company.industry_group} />
                <DetailRow label="Emerging Spaces" value={company.emerging_spaces} />
                <DetailRow label="Competitors" value={company.competitors} />
                <DetailRow label="Also Known As" value={company.also_known_as} />
                <DetailRow label="Legal Name" value={company.legal_name} />
              </div>
            </div>
          )}

          {activeTab === 'financials' && (
            <div className="drawer-section">
              <div className="detail-grid">
                <DetailRow label="Revenue" value={formatCurrency(company.revenue_m)} highlight />
                <DetailRow label="Net Income" value={formatCurrency(company.net_income_m)} />
                <DetailRow label="EBITDA (Est.)" value={formatCurrency(company.estimated_ebitda)} highlight />
                <DetailRow label="Revenue Growth" value={formatPct(company.revenue_growth_pct)} />
                <DetailRow label="Enterprise Value" value={formatCurrency(company.enterprise_value_m)} />
                <DetailRow label="Valuation (Est.)" value={formatCurrency(company.valuation_estimate_m)} highlight />
                <DetailRow label="Last Valuation" value={formatCurrency(company.last_valuation_m)} />
                <DetailRow label="Last Valuation Date" value={formatDate(company.last_valuation_date)} />
                <DetailRow label="Total Raised" value={formatCurrency(company.total_raised_m)} />
                <DetailRow label="Financing Status" value={company.financing_status} />
                <DetailRow label="Last Financing" value={formatCurrency(company.last_financing_size_m)} />
                <DetailRow label="Last Financing Date" value={formatDate(company.last_financing_date)} />
                <DetailRow label="Last Financing Type" value={company.last_financing_type} />
                <DetailRow label="First Financing Date" value={formatDate(company.first_financing_date)} />
                <DetailRow label="First Financing Size" value={formatCurrency(company.first_financing_size_m)} />
                <DetailRow label="Active Investors" value={company.active_investors} />
                <DetailRow label="# Active Investors" value={company.num_active_investors?.toString()} />
                <DetailRow label="Former Investors" value={company.former_investors} />
                <DetailRow label="Growth Rate (PB)" value={formatPct(company.pitchbook_growth_rate)} />
                <DetailRow label="Growth Percentile" value={company.growth_rate_percentile ? `${company.growth_rate_percentile}th` : undefined} />
                <DetailRow label="Web Visitors" value={formatNum(company.web_visitors)} />
                <DetailRow label="Total Patents" value={company.total_patents?.toString()} />
              </div>
            </div>
          )}

          {activeTab === 'contacts' && (
            <div className="drawer-section">
              <div className="contact-card">
                <div className="contact-avatar">
                  {company.contact_name ? company.contact_name.split(' ').map(n => n[0]).join('').slice(0, 2).toUpperCase() : '??'}
                </div>
                <div className="contact-info">
                  <h4 className="contact-name">{company.contact_name || 'No contact on file'}</h4>
                  {company.contact_title && <p className="contact-title">{company.contact_title}</p>}
                </div>
              </div>

              <div className="detail-grid" style={{ marginTop: '1.5rem' }}>
                <DetailRow label="Email" value={company.contact_email} isLink={company.contact_email ? `mailto:${company.contact_email}` : undefined} />
                <DetailRow label="Phone" value={company.contact_phone} />
                <DetailRow label="LinkedIn" value={company.linkedin_url ? 'View Profile' : undefined} isLink={company.linkedin_url} />
                <DetailRow label="HQ Email" value={company.hq_email} isLink={company.hq_email ? `mailto:${company.hq_email}` : undefined} />
                <DetailRow label="HQ Phone" value={company.hq_phone} />
              </div>
            </div>
          )}

          {activeTab === 'activity' && (
            <div className="drawer-section">
              {/* Add note inline */}
              <div className="inline-note-form">
                <textarea
                  className="inline-note-input"
                  placeholder="Add a note..."
                  value={noteText}
                  onChange={e => setNoteText(e.target.value)}
                  rows={2}
                />
                <button
                  className="inline-note-btn"
                  onClick={handleSaveNote}
                  disabled={savingNote || !noteText.trim()}
                >
                  {savingNote ? 'Saving...' : 'Add Note'}
                </button>
              </div>

              {/* Timeline */}
              {loadingActivity ? (
                <div className="loading-placeholder">Loading activity...</div>
              ) : activity.length === 0 ? (
                <p className="empty-activity">No activity recorded yet.</p>
              ) : (
                <div className="activity-timeline">
                  {activity.map(entry => (
                    <div key={entry.id} className="activity-item">
                      <div className="activity-dot" style={{
                        background: entry.action_type === 'status_change' ? stageColor(entry.new_status || '') :
                          entry.action_type === 'note' ? '#94a3b8' : '#f59e0b'
                      }} />
                      <div className="activity-content">
                        <div className="activity-header">
                          <span className="activity-type">
                            {entry.action_type === 'status_change' ? 'Stage Change' : entry.action_type === 'note' ? 'Note' : 'Outreach'}
                          </span>
                          <span className="activity-date">
                            {new Date(entry.created_at).toLocaleDateString('en-GB', { day: 'numeric', month: 'short', year: 'numeric', hour: '2-digit', minute: '2-digit' })}
                          </span>
                        </div>
                        {entry.action_type === 'status_change' && (
                          <div className="stage-change">
                            <span className="stage-chip" style={{ background: stageColor(entry.old_status || '') }}>{entry.old_status}</span>
                            <svg width="16" height="16" viewBox="0 0 16 16" fill="none"><path d="M3 8h10m-3-3l3 3-3 3" stroke="#94a3b8" strokeWidth="1.5" strokeLinecap="round" strokeLinejoin="round"/></svg>
                            <span className="stage-chip" style={{ background: stageColor(entry.new_status || '') }}>{entry.new_status}</span>
                          </div>
                        )}
                        {entry.note_text && entry.action_type === 'note' && (
                          <p className="activity-note-text">{entry.note_text}</p>
                        )}
                        <span className="activity-by">{entry.created_by}</span>
                      </div>
                    </div>
                  ))}
                </div>
              )}
            </div>
          )}
        </div>
      </div>

      <style jsx>{`
        .drawer-overlay {
          position: fixed;
          inset: 0;
          background: rgba(15, 23, 42, 0.3);
          z-index: 500;
          animation: fadeIn 0.2s ease;
        }

        .drawer-panel {
          position: fixed;
          top: 0;
          right: 0;
          width: 520px;
          max-width: 90vw;
          height: 100vh;
          background: #fff;
          z-index: 501;
          display: flex;
          flex-direction: column;
          box-shadow: -8px 0 30px rgba(0, 0, 0, 0.08);
          animation: slideIn 0.25s ease;
        }

        @keyframes fadeIn { from { opacity: 0; } to { opacity: 1; } }
        @keyframes slideIn { from { transform: translateX(100%); } to { transform: translateX(0); } }

        .drawer-header {
          padding: 1.5rem 2rem;
          border-bottom: 1px solid #e2e8f0;
        }

        .drawer-header-top {
          display: flex;
          justify-content: flex-end;
          margin-bottom: 0.75rem;
        }

        .drawer-close {
          background: none;
          border: none;
          color: #94a3b8;
          cursor: pointer;
          padding: 0.25rem;
          border-radius: 6px;
          display: flex;
          align-items: center;
          justify-content: center;
        }
        .drawer-close:hover { background: #f1f5f9; color: #0f172a; }

        .drawer-company-header {
          display: flex;
          align-items: center;
          gap: 0.75rem;
          margin-bottom: 0.5rem;
        }

        .drawer-company-name {
          font-size: 1.35rem;
          font-weight: 800;
          color: #0f172a;
          margin: 0;
          line-height: 1.2;
        }

        .drawer-status-badge {
          font-size: 0.65rem;
          font-weight: 800;
          text-transform: uppercase;
          padding: 0.2rem 0.6rem;
          border-radius: 4px;
          color: white;
          letter-spacing: 0.05em;
          flex-shrink: 0;
        }

        .drawer-website {
          display: inline-flex;
          align-items: center;
          gap: 0.3rem;
          font-size: 0.85rem;
          color: #2563eb;
          font-weight: 600;
          margin-bottom: 0.75rem;
        }
        .drawer-website:hover { text-decoration: underline; }

        .drawer-description {
          font-size: 0.88rem;
          color: #475569;
          line-height: 1.6;
          margin: 0;
          display: -webkit-box;
          -webkit-line-clamp: 4;
          -webkit-box-orient: vertical;
          overflow: hidden;
        }

        .drawer-tabs {
          display: flex;
          border-bottom: 1px solid #e2e8f0;
          padding: 0 2rem;
          gap: 0;
        }

        .drawer-tab {
          background: none;
          border: none;
          padding: 0.85rem 1rem;
          font-size: 0.82rem;
          font-weight: 600;
          color: #94a3b8;
          cursor: pointer;
          border-bottom: 2px solid transparent;
          transition: all 0.15s;
        }
        .drawer-tab:hover { color: #475569; }
        .drawer-tab.active {
          color: #2563eb;
          border-bottom-color: #2563eb;
        }

        .drawer-body {
          flex: 1;
          overflow-y: auto;
          padding: 1.5rem 2rem 2rem;
        }

        .detail-grid {
          display: flex;
          flex-direction: column;
          gap: 0;
        }

        /* Contact card */
        .contact-card {
          display: flex;
          align-items: center;
          gap: 1rem;
          padding: 1.25rem;
          background: #f8fafc;
          border-radius: 10px;
          border: 1px solid #e2e8f0;
        }

        .contact-avatar {
          width: 48px;
          height: 48px;
          background: #eff6ff;
          color: #2563eb;
          border-radius: 50%;
          display: flex;
          align-items: center;
          justify-content: center;
          font-weight: 800;
          font-size: 0.9rem;
          flex-shrink: 0;
        }

        .contact-name {
          font-size: 1rem;
          font-weight: 700;
          color: #0f172a;
          margin: 0 0 0.15rem;
        }

        .contact-title {
          font-size: 0.82rem;
          color: #64748b;
          margin: 0;
        }

        /* Inline note form */
        .inline-note-form {
          margin-bottom: 1.5rem;
          display: flex;
          flex-direction: column;
          gap: 0.5rem;
        }

        .inline-note-input {
          width: 100%;
          padding: 0.75rem;
          border: 1.5px solid #e2e8f0;
          border-radius: 8px;
          font-size: 0.88rem;
          font-family: inherit;
          resize: vertical;
          outline: none;
          background: #f8fafc;
          color: #0f172a;
          line-height: 1.5;
        }
        .inline-note-input:focus { border-color: #2563eb; background: #fff; }

        .inline-note-btn {
          align-self: flex-end;
          padding: 0.45rem 1rem;
          background: #2563eb;
          color: white;
          border: none;
          border-radius: 6px;
          font-size: 0.78rem;
          font-weight: 700;
          cursor: pointer;
        }
        .inline-note-btn:disabled { opacity: 0.4; cursor: not-allowed; }
        .inline-note-btn:hover:not(:disabled) { background: #1d4ed8; }

        /* Activity timeline */
        .loading-placeholder, .empty-activity {
          text-align: center;
          color: #94a3b8;
          padding: 2rem 0;
          font-size: 0.88rem;
        }

        .activity-timeline {
          display: flex;
          flex-direction: column;
        }

        .activity-item {
          display: flex;
          gap: 0.85rem;
          padding: 0.85rem 0;
          border-bottom: 1px solid #f1f5f9;
        }
        .activity-item:last-child { border-bottom: none; }

        .activity-dot {
          width: 8px;
          height: 8px;
          border-radius: 50%;
          margin-top: 6px;
          flex-shrink: 0;
        }

        .activity-content { flex: 1; }

        .activity-header {
          display: flex;
          justify-content: space-between;
          align-items: center;
          margin-bottom: 0.3rem;
        }

        .activity-type {
          font-size: 0.7rem;
          font-weight: 700;
          text-transform: uppercase;
          letter-spacing: 0.05em;
          color: #64748b;
        }

        .activity-date {
          font-size: 0.68rem;
          color: #94a3b8;
        }

        .stage-change {
          display: flex;
          align-items: center;
          gap: 0.4rem;
          margin: 0.25rem 0;
        }

        .stage-chip {
          font-size: 0.6rem;
          font-weight: 700;
          color: white;
          padding: 0.1rem 0.45rem;
          border-radius: 3px;
          text-transform: uppercase;
        }

        .activity-note-text {
          font-size: 0.85rem;
          color: #0f172a;
          line-height: 1.5;
          margin: 0.2rem 0;
        }

        .activity-by {
          font-size: 0.68rem;
          color: #94a3b8;
        }
      `}</style>
    </>
  );
}

function DetailRow({ label, value, highlight, isLink }: { label: string; value?: string | null; highlight?: boolean; isLink?: string }) {
  const displayValue = value && value !== '—' ? value : '—';
  const isEmpty = !value || value === '—';

  return (
    <div className="detail-row">
      <span className="detail-label">{label}</span>
      {isLink && !isEmpty ? (
        <a href={isLink} target="_blank" rel="noreferrer" className="detail-value detail-link">
          {displayValue}
        </a>
      ) : (
        <span className={`detail-value ${isEmpty ? 'empty' : ''} ${highlight && !isEmpty ? 'highlight' : ''}`}>
          {displayValue}
        </span>
      )}

      <style jsx>{`
        .detail-row {
          display: flex;
          justify-content: space-between;
          align-items: center;
          padding: 0.6rem 0;
          border-bottom: 1px solid #f1f5f9;
        }
        .detail-row:last-child { border-bottom: none; }

        .detail-label {
          font-size: 0.78rem;
          font-weight: 600;
          color: #64748b;
          flex-shrink: 0;
        }

        .detail-value {
          font-size: 0.85rem;
          font-weight: 600;
          color: #0f172a;
          text-align: right;
          max-width: 55%;
          overflow: hidden;
          text-overflow: ellipsis;
          white-space: nowrap;
        }

        .detail-value.empty {
          color: #cbd5e1;
          font-weight: 400;
        }

        .detail-value.highlight {
          color: #2563eb;
          font-weight: 700;
        }

        .detail-link {
          color: #2563eb;
          text-decoration: none;
        }
        .detail-link:hover { text-decoration: underline; }
      `}</style>
    </div>
  );
}
