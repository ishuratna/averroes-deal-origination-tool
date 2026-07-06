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

  // Formatting helpers
  const fmtGBP = (val?: number | null) => (val != null && val !== 0) ? `£${(val / 1e6).toFixed(2)}M` : null;
  const fmtM = (val?: number | null) => (val != null && val !== 0) ? `£${val.toFixed(1)}M` : null;
  const fmtNum = (val?: number | null) => (val != null && val !== 0) ? val.toLocaleString() : null;
  const fmtDate = (d?: string | null) => d ? new Date(d).toLocaleDateString('en-GB', { day: 'numeric', month: 'short', year: 'numeric' }) : null;
  const fmtPct = (val?: number | null) => (val != null && val !== 0) ? `${val > 0 ? '+' : ''}${val.toFixed(1)}%` : null;

  // Check if a section has any data
  const hasChData = !!(company?.ch_company_number);
  const hasChRevenue = company?.revenue_y1 != null || company?.revenue_y2 != null || company?.revenue_y3 != null;
  const hasChProfit = company?.profit_y1 != null || company?.profit_y2 != null || company?.profit_y3 != null;
  const hasChBalance = company?.total_assets_y1 != null || company?.net_assets_y1 != null || company?.cash_y1 != null;
  const hasFunding = !!(company?.total_raised_m || company?.last_financing_size_m || company?.financing_status || company?.active_investors);

  // Parse score details JSON
  const scoreDetails = (() => {
    if (!company?.score_details) return null;
    try { return JSON.parse(company.score_details); } catch { return null; }
  })();

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

          {/* ═══ OVERVIEW TAB ═══ */}
          {activeTab === 'overview' && (
            <div className="drawer-section">
              {/* Quick Stats Row */}
              <div className="stats-row">
                <div className="stat-card">
                  <span className="stat-label">Fit Score</span>
                  <span className={`stat-value ${company.averroes_fit_score != null ? (company.averroes_fit_score >= 0.7 ? 'score-high' : company.averroes_fit_score >= 0.4 ? 'score-mid' : 'score-low') : ''}`}>
                    {company.averroes_fit_score != null ? `${(company.averroes_fit_score * 100).toFixed(0)}` : '—'}
                  </span>
                </div>
                <div className="stat-card">
                  <span className="stat-label">Size</span>
                  <span className="stat-value">{company.size_bucket || '—'}</span>
                </div>
                <div className="stat-card">
                  <span className="stat-label">Employees</span>
                  <span className="stat-value">{fmtNum(company.employees_ch || company.employees) || '—'}</span>
                </div>
                <div className="stat-card">
                  <span className="stat-label">Founded</span>
                  <span className="stat-value">{company.year_founded || '—'}</span>
                </div>
              </div>

              {/* Averroes Fit Score Breakdown */}
              {company.averroes_fit_score != null && (
                <>
                  <SectionHeading title="Averroes Fit Score" />
                  <div className="score-breakdown">
                    <ScoreBar label="Employee Growth" score={company.score_employee_growth} details={scoreDetails?.employee_growth} />
                    <ScoreBar label="Revenue Growth" score={company.score_revenue_growth} details={scoreDetails?.revenue_growth} />
                    <ScoreBar label="Revenue Size" score={company.score_revenue_size} details={scoreDetails?.revenue_size} />
                    <ScoreBar label="Business Model Fit" score={company.score_business_fit} details={scoreDetails?.business_fit} />
                    <ScoreBar label="Market Sentiment" score={company.score_market_sentiment} details={scoreDetails?.market_sentiment} />
                  </div>
                </>
              )}

              {/* Company Info */}
              <SectionHeading title="Company Info" />
              <div className="detail-grid">
                <DetailRow label="Sector" value={company.sector} />
                <DetailRow label="Region" value={company.region || company.hq_location || company.hq_country} />
                {company.hq_city && <DetailRow label="HQ City" value={company.hq_city} />}
                {company.ownership && <DetailRow label="Ownership" value={company.ownership} />}
                {company.business_status && <DetailRow label="Business Status" value={company.business_status} />}
                {company.legal_name && <DetailRow label="Legal Name" value={company.legal_name} />}
                {company.also_known_as && <DetailRow label="Also Known As" value={company.also_known_as} />}
              </div>

              {/* Companies House Registration */}
              {hasChData && (
                <>
                  <SectionHeading title="Companies House" badge={company.ch_match_confidence} />
                  <div className="detail-grid">
                    <DetailRow label="Official Name" value={company.ch_official_name} />
                    <DetailRow label="Company #" value={company.ch_company_number} />
                    <DetailRow label="Status" value={company.ch_status} />
                    {company.ch_incorporated_date && <DetailRow label="Incorporated" value={company.ch_incorporated_date} />}
                    {company.ch_sic_codes && <DetailRow label="SIC Codes" value={company.ch_sic_codes} />}
                    {company.filing_type && <DetailRow label="Filing Type" value={company.filing_type} />}
                    {company.ch_pdf_path && (
                      <div className="detail-row">
                        <span className="detail-label">Filed Accounts</span>
                        <a
                          href={`${process.env.NEXT_PUBLIC_API_URL || 'https://averroes-deal-backend-890361705054.europe-west1.run.app'}/ch-pdf/${encodeURIComponent(company.name)}`}
                          target="_blank"
                          rel="noopener noreferrer"
                          className="ch-pdf-link"
                        >
                          View CH Filing PDF
                        </a>
                      </div>
                    )}
                  </div>
                </>
              )}

              {/* Classification */}
              {(company.keywords || company.verticals || company.industry_group || company.emerging_spaces) && (
                <>
                  <SectionHeading title="Classification" />
                  <div className="tags-section">
                    {company.industry_group && <div className="tag-row"><span className="tag-label">Industry</span><span className="tag-value">{company.industry_group}</span></div>}
                    {company.keywords && (
                      <div className="tag-row">
                        <span className="tag-label">Keywords</span>
                        <div className="tag-chips">
                          {company.keywords.split(',').slice(0, 6).map((k, i) => (
                            <span key={i} className="tag-chip">{k.trim()}</span>
                          ))}
                        </div>
                      </div>
                    )}
                    {company.verticals && (
                      <div className="tag-row">
                        <span className="tag-label">Verticals</span>
                        <div className="tag-chips">
                          {company.verticals.split(',').slice(0, 4).map((v, i) => (
                            <span key={i} className="tag-chip">{v.trim()}</span>
                          ))}
                        </div>
                      </div>
                    )}
                    {company.emerging_spaces && (
                      <div className="tag-row">
                        <span className="tag-label">Emerging</span>
                        <div className="tag-chips">
                          {company.emerging_spaces.split(',').slice(0, 4).map((e, i) => (
                            <span key={i} className="tag-chip accent">{e.trim()}</span>
                          ))}
                        </div>
                      </div>
                    )}
                  </div>
                </>
              )}

              {/* Competitors */}
              {company.competitors && (
                <>
                  <SectionHeading title="Competitors" />
                  <div className="tag-chips" style={{ padding: '0' }}>
                    {company.competitors.split(',').slice(0, 6).map((c, i) => (
                      <span key={i} className="tag-chip">{c.trim()}</span>
                    ))}
                  </div>
                </>
              )}

              {/* Source & Meta */}
              <SectionHeading title="Source" />
              <div className="detail-grid">
                <DetailRow label="Source" value={company.source} />
                <DetailRow label="Date Added" value={fmtDate(company.ingested_at) || undefined} />
              </div>
            </div>
          )}

          {/* ═══ FINANCIALS TAB ═══ */}
          {activeTab === 'financials' && (
            <div className="drawer-section">

              {/* Key Metrics Summary Cards */}
              <div className="fin-cards">
                <div className="fin-card">
                  <span className="fin-card-label">Revenue</span>
                  <span className="fin-card-value primary">
                    {fmtGBP(company.revenue_y1) || fmtM(company.revenue_m) || '—'}
                  </span>
                  {company.revenue_y1_date && <span className="fin-card-sub">{company.revenue_y1_date}</span>}
                </div>
                <div className="fin-card">
                  <span className="fin-card-label">Profit</span>
                  <span className={`fin-card-value ${company.profit_y1 != null && company.profit_y1 < 0 ? 'negative' : 'primary'}`}>
                    {fmtGBP(company.profit_y1) || fmtM(company.net_income_m) || '—'}
                  </span>
                  {company.profit_y1_date && <span className="fin-card-sub">{company.profit_y1_date}</span>}
                </div>
                <div className="fin-card">
                  <span className="fin-card-label">Total Assets</span>
                  <span className="fin-card-value">{fmtGBP(company.total_assets_y1) || '—'}</span>
                </div>
                <div className="fin-card">
                  <span className="fin-card-label">Net Assets</span>
                  <span className={`fin-card-value ${company.net_assets_y1 != null && company.net_assets_y1 < 0 ? 'negative' : ''}`}>
                    {fmtGBP(company.net_assets_y1) || '—'}
                  </span>
                </div>
              </div>

              {/* Companies House Filing Data */}
              {hasChData && (
                <>
                  <SectionHeading title="Companies House Filings" badge={company.ch_match_confidence} />
                  <div className="detail-grid compact">
                    <DetailRow label="Official Name" value={company.ch_official_name} />
                    <DetailRow label="Company #" value={company.ch_company_number} />
                    <DetailRow label="Filing Type" value={company.filing_type} />
                    {company.employees_ch && <DetailRow label="Employees (CH)" value={fmtNum(company.employees_ch) || undefined} />}
                  </div>

                  {/* Revenue 3-Year Trend */}
                  {hasChRevenue && (
                    <>
                      <h4 className="ch-sub-heading">Revenue / Turnover</h4>
                      <div className="fin-trend">
                        {[
                          { date: company.revenue_y3_date, val: company.revenue_y3 },
                          { date: company.revenue_y2_date, val: company.revenue_y2 },
                          { date: company.revenue_y1_date, val: company.revenue_y1 },
                        ].filter(r => r.val != null).map((r, i, arr) => (
                          <div key={i} className="fin-trend-item">
                            <span className="fin-trend-date">{r.date || `Year ${i + 1}`}</span>
                            <div className="fin-trend-bar-wrap">
                              <div
                                className="fin-trend-bar"
                                style={{
                                  width: `${Math.min(100, Math.max(8, (Math.abs(r.val!) / Math.max(...arr.map(x => Math.abs(x.val!)))) * 100))}%`,
                                  background: i === arr.length - 1 ? '#2563eb' : '#cbd5e1',
                                }}
                              />
                            </div>
                            <span className={`fin-trend-val ${i === arr.length - 1 ? 'latest' : ''}`}>
                              {fmtGBP(r.val)}
                            </span>
                          </div>
                        ))}
                      </div>
                    </>
                  )}

                  {/* Profit 3-Year Trend */}
                  {hasChProfit && (
                    <>
                      <h4 className="ch-sub-heading">Profit Before Tax</h4>
                      <div className="fin-trend">
                        {[
                          { date: company.revenue_y3_date, val: company.profit_y3 },
                          { date: company.revenue_y2_date, val: company.profit_y2 },
                          { date: company.profit_y1_date || company.revenue_y1_date, val: company.profit_y1 },
                        ].filter(r => r.val != null).map((r, i, arr) => (
                          <div key={i} className="fin-trend-item">
                            <span className="fin-trend-date">{r.date || `Year ${i + 1}`}</span>
                            <div className="fin-trend-bar-wrap">
                              <div
                                className="fin-trend-bar"
                                style={{
                                  width: `${Math.min(100, Math.max(8, (Math.abs(r.val!) / Math.max(...arr.map(x => Math.abs(x.val!)), 1)) * 100))}%`,
                                  background: r.val! < 0 ? '#ef4444' : (i === arr.length - 1 ? '#10b981' : '#a7f3d0'),
                                }}
                              />
                            </div>
                            <span className={`fin-trend-val ${r.val! < 0 ? 'negative' : ''} ${i === arr.length - 1 ? 'latest' : ''}`}>
                              {fmtGBP(r.val)}
                            </span>
                          </div>
                        ))}
                      </div>
                    </>
                  )}

                  {/* Balance Sheet */}
                  {hasChBalance && (
                    <>
                      <h4 className="ch-sub-heading">Balance Sheet (Latest)</h4>
                      <div className="detail-grid compact">
                        <DetailRow label="Total Assets" value={fmtGBP(company.total_assets_y1) || undefined} highlight />
                        <DetailRow label="Net Assets" value={fmtGBP(company.net_assets_y1) || undefined} />
                        <DetailRow label="Cash" value={fmtGBP(company.cash_y1) || undefined} />
                      </div>
                    </>
                  )}

                  {company.ch_notes && (
                    <p className="ch-filing-note">{company.ch_notes}</p>
                  )}
                </>
              )}

              {/* Valuation & Enterprise Value */}
              {(company.estimated_ebitda || company.enterprise_value_m || company.valuation_estimate_m || company.last_valuation_m || company.revenue_growth_pct) && (
                <>
                  <SectionHeading title="Valuation & Performance" />
                  <div className="detail-grid compact">
                    {company.estimated_ebitda != null && <DetailRow label="EBITDA (Est.)" value={fmtM(company.estimated_ebitda) || undefined} highlight />}
                    {company.enterprise_value_m != null && <DetailRow label="Enterprise Value" value={fmtM(company.enterprise_value_m) || undefined} highlight />}
                    {company.valuation_estimate_m != null && <DetailRow label="Valuation (Est.)" value={fmtM(company.valuation_estimate_m) || undefined} />}
                    {company.last_valuation_m != null && <DetailRow label="Last Valuation" value={fmtM(company.last_valuation_m) || undefined} />}
                    {company.last_valuation_date && <DetailRow label="Valuation Date" value={fmtDate(company.last_valuation_date) || undefined} />}
                    {company.revenue_m != null && <DetailRow label="Revenue (PB)" value={fmtM(company.revenue_m) || undefined} />}
                    {company.net_income_m != null && <DetailRow label="Net Income" value={fmtM(company.net_income_m) || undefined} />}
                    {company.revenue_growth_pct != null && <DetailRow label="Revenue Growth" value={fmtPct(company.revenue_growth_pct) || undefined} />}
                  </div>
                </>
              )}

              {/* Funding History */}
              {hasFunding && (
                <>
                  <SectionHeading title="Funding History" />
                  <div className="detail-grid compact">
                    {company.total_raised_m != null && <DetailRow label="Total Raised" value={fmtM(company.total_raised_m) || undefined} highlight />}
                    {company.financing_status && <DetailRow label="Financing Status" value={company.financing_status} />}
                    {company.last_financing_size_m != null && <DetailRow label="Last Round" value={fmtM(company.last_financing_size_m) || undefined} />}
                    {company.last_financing_type && <DetailRow label="Round Type" value={company.last_financing_type} />}
                    {company.last_financing_date && <DetailRow label="Round Date" value={fmtDate(company.last_financing_date) || undefined} />}
                    {company.last_financing_valuation_m != null && <DetailRow label="Round Valuation" value={fmtM(company.last_financing_valuation_m) || undefined} />}
                    {company.first_financing_date && <DetailRow label="First Financing" value={fmtDate(company.first_financing_date) || undefined} />}
                    {company.first_financing_size_m != null && <DetailRow label="First Round Size" value={fmtM(company.first_financing_size_m) || undefined} />}
                    {company.active_investors && <DetailRow label="Active Investors" value={company.active_investors} />}
                    {company.num_active_investors != null && <DetailRow label="# Investors" value={company.num_active_investors.toString()} />}
                    {company.former_investors && <DetailRow label="Former Investors" value={company.former_investors} />}
                  </div>
                </>
              )}

              {/* Growth & Signals */}
              {(company.pitchbook_growth_rate || company.growth_rate_percentile || company.web_visitors || company.total_patents || company.opportunity_score || company.success_probability || company.ma_probability) && (
                <>
                  <SectionHeading title="Growth & Signals" />
                  <div className="detail-grid compact">
                    {company.pitchbook_growth_rate != null && <DetailRow label="Growth Rate" value={fmtPct(company.pitchbook_growth_rate) || undefined} />}
                    {company.growth_rate_percentile != null && <DetailRow label="Growth Percentile" value={`${company.growth_rate_percentile}th`} />}
                    {company.web_visitors != null && <DetailRow label="Web Visitors" value={fmtNum(company.web_visitors) || undefined} />}
                    {company.total_patents != null && <DetailRow label="Patents" value={company.total_patents.toString()} />}
                    {company.opportunity_score != null && <DetailRow label="Opportunity Score" value={company.opportunity_score.toString()} />}
                    {company.success_probability != null && <DetailRow label="Success Prob." value={`${company.success_probability}%`} />}
                    {company.ma_probability != null && <DetailRow label="M&A Prob." value={`${company.ma_probability}%`} />}
                    {company.predicted_exit_type && <DetailRow label="Predicted Exit" value={company.predicted_exit_type} />}
                  </div>
                </>
              )}

              {/* Empty state */}
              {!hasChData && !company.revenue_m && !company.estimated_ebitda && !hasFunding && (
                <div className="empty-financials">
                  <p>No financial data available yet.</p>
                  <p className="empty-sub">Run SmartFill to extract financials from Companies House.</p>
                </div>
              )}
            </div>
          )}

          {/* ═══ CONTACTS TAB ═══ */}
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

          {/* ═══ ACTIVITY TAB ═══ */}
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
          width: 560px;
          max-width: 92vw;
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
          padding: 1.25rem 1.75rem;
          border-bottom: 1px solid #e2e8f0;
        }

        .drawer-header-top {
          display: flex;
          justify-content: flex-end;
          margin-bottom: 0.5rem;
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
          margin-bottom: 0.4rem;
        }

        .drawer-company-name {
          font-size: 1.3rem;
          font-weight: 800;
          color: #0f172a;
          margin: 0;
          line-height: 1.2;
        }

        .drawer-status-badge {
          font-size: 0.62rem;
          font-weight: 800;
          text-transform: uppercase;
          padding: 0.2rem 0.55rem;
          border-radius: 4px;
          color: white;
          letter-spacing: 0.05em;
          flex-shrink: 0;
        }

        .drawer-website {
          display: inline-flex;
          align-items: center;
          gap: 0.3rem;
          font-size: 0.82rem;
          color: #2563eb;
          font-weight: 600;
          margin-bottom: 0.6rem;
        }
        .drawer-website:hover { text-decoration: underline; }

        .drawer-description {
          font-size: 0.84rem;
          color: #475569;
          line-height: 1.6;
          margin: 0;
          display: -webkit-box;
          -webkit-line-clamp: 3;
          -webkit-box-orient: vertical;
          overflow: hidden;
        }

        .drawer-tabs {
          display: flex;
          border-bottom: 1px solid #e2e8f0;
          padding: 0 1.75rem;
          gap: 0;
        }

        .drawer-tab {
          background: none;
          border: none;
          padding: 0.75rem 0.9rem;
          font-size: 0.78rem;
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
          padding: 1.25rem 1.75rem 2rem;
        }

        /* ── Quick Stats Row ── */
        .stats-row {
          display: grid;
          grid-template-columns: repeat(4, 1fr);
          gap: 0.6rem;
          margin-bottom: 1.25rem;
        }

        .stat-card {
          background: #f8fafc;
          border: 1px solid #e2e8f0;
          border-radius: 8px;
          padding: 0.65rem 0.5rem;
          text-align: center;
          display: flex;
          flex-direction: column;
          gap: 0.2rem;
        }

        .stat-label {
          font-size: 0.62rem;
          font-weight: 700;
          text-transform: uppercase;
          letter-spacing: 0.05em;
          color: #94a3b8;
        }

        .stat-value {
          font-size: 0.88rem;
          font-weight: 800;
          color: #0f172a;
        }

        .stat-value.score-high { color: #16a34a; }
        .stat-value.score-mid { color: #d97706; }
        .stat-value.score-low { color: #dc2626; }

        /* ── Score Breakdown ── */
        .score-breakdown {
          display: flex;
          flex-direction: column;
          gap: 0.6rem;
        }

        /* ── Detail Grid ── */
        .detail-grid {
          display: flex;
          flex-direction: column;
          gap: 0;
        }

        .detail-grid.compact {
          margin-bottom: 0.25rem;
        }

        /* ── Financial Cards ── */
        .fin-cards {
          display: grid;
          grid-template-columns: repeat(2, 1fr);
          gap: 0.6rem;
          margin-bottom: 1.25rem;
        }

        .fin-card {
          background: #f8fafc;
          border: 1px solid #e2e8f0;
          border-radius: 10px;
          padding: 0.85rem;
          display: flex;
          flex-direction: column;
          gap: 0.15rem;
        }

        .fin-card-label {
          font-size: 0.65rem;
          font-weight: 700;
          text-transform: uppercase;
          letter-spacing: 0.05em;
          color: #94a3b8;
        }

        .fin-card-value {
          font-size: 1.1rem;
          font-weight: 800;
          color: #0f172a;
        }

        .fin-card-value.primary { color: #2563eb; }
        .fin-card-value.negative { color: #ef4444; }

        .fin-card-sub {
          font-size: 0.68rem;
          color: #94a3b8;
        }

        /* ── Revenue/Profit Trend Bars ── */
        .fin-trend {
          display: flex;
          flex-direction: column;
          gap: 0.5rem;
          margin-bottom: 0.5rem;
        }

        .fin-trend-item {
          display: grid;
          grid-template-columns: 80px 1fr auto;
          align-items: center;
          gap: 0.6rem;
        }

        .fin-trend-date {
          font-size: 0.72rem;
          font-weight: 600;
          color: #64748b;
          text-align: right;
        }

        .fin-trend-bar-wrap {
          height: 18px;
          background: #f1f5f9;
          border-radius: 4px;
          overflow: hidden;
        }

        .fin-trend-bar {
          height: 100%;
          border-radius: 4px;
          transition: width 0.3s ease;
          min-width: 4px;
        }

        .fin-trend-val {
          font-size: 0.78rem;
          font-weight: 700;
          color: #475569;
          min-width: 65px;
          text-align: right;
        }

        .fin-trend-val.latest { color: #0f172a; font-weight: 800; }
        .fin-trend-val.negative { color: #ef4444; }

        /* ── Section Headings ── */
        .ch-sub-heading {
          font-size: 0.7rem;
          font-weight: 700;
          text-transform: uppercase;
          letter-spacing: 0.06em;
          color: #475569;
          margin: 1rem 0 0.4rem;
        }

        .ch-filing-note {
          font-size: 0.78rem;
          color: #64748b;
          background: #fffbeb;
          padding: 0.6rem 0.8rem;
          border-radius: 6px;
          border: 1px solid #fde68a;
          margin: 0.75rem 0 0;
          line-height: 1.5;
        }

        /* ── Tags ── */
        .tags-section {
          display: flex;
          flex-direction: column;
          gap: 0.65rem;
        }

        .tag-row {
          display: flex;
          align-items: flex-start;
          gap: 0.6rem;
        }

        .tag-label {
          font-size: 0.72rem;
          font-weight: 600;
          color: #94a3b8;
          min-width: 65px;
          padding-top: 0.15rem;
          flex-shrink: 0;
        }

        .tag-chips {
          display: flex;
          flex-wrap: wrap;
          gap: 0.3rem;
        }

        .tag-chip {
          font-size: 0.68rem;
          font-weight: 600;
          padding: 0.18rem 0.5rem;
          background: #f1f5f9;
          color: #475569;
          border-radius: 4px;
          border: 1px solid #e2e8f0;
        }

        .tag-chip.accent {
          background: #eff6ff;
          color: #2563eb;
          border-color: #bfdbfe;
        }

        /* ── Empty states ── */
        .empty-financials {
          text-align: center;
          padding: 3rem 1rem;
        }

        .empty-financials p {
          color: #64748b;
          font-size: 0.9rem;
          margin: 0;
        }

        .empty-financials .empty-sub {
          color: #94a3b8;
          font-size: 0.8rem;
          margin-top: 0.35rem;
        }

        /* ── Contact card ── */
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

        /* ── Inline note form ── */
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
          font-size: 0.85rem;
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

        /* ── Activity timeline ── */
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


/* ── Section Heading Component ── */
function SectionHeading({ title, badge }: { title: string; badge?: string | null }) {
  const badgeColor = badge === 'high' ? '#dcfce7' : badge === 'medium' ? '#fef9c3' : badge === 'low' ? '#fee2e2' : '';
  const badgeText = badge === 'high' ? '#166534' : badge === 'medium' ? '#854d0e' : badge === 'low' ? '#991b1b' : '';

  return (
    <div className="section-heading">
      <h3 className="section-title">{title}</h3>
      {badge && (
        <span className="section-badge" style={{ background: badgeColor, color: badgeText }}>
          {badge}
        </span>
      )}
      <style jsx>{`
        .section-heading {
          display: flex;
          align-items: center;
          gap: 0.5rem;
          margin: 1.25rem 0 0.5rem;
          padding-bottom: 0.4rem;
          border-bottom: 1px solid #e2e8f0;
        }
        .section-heading:first-child { margin-top: 0; }

        .section-title {
          font-size: 0.72rem;
          font-weight: 800;
          text-transform: uppercase;
          letter-spacing: 0.06em;
          color: #0f172a;
          margin: 0;
        }

        .section-badge {
          font-size: 0.6rem;
          font-weight: 700;
          padding: 0.12rem 0.4rem;
          border-radius: 3px;
          text-transform: capitalize;
        }
      `}</style>
    </div>
  );
}


/* ── Detail Row Component ── */
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
          padding: 0.5rem 0;
          border-bottom: 1px solid #f8fafc;
        }
        .detail-row:last-child { border-bottom: none; }

        .detail-label {
          font-size: 0.76rem;
          font-weight: 600;
          color: #64748b;
          flex-shrink: 0;
        }

        .detail-value {
          font-size: 0.82rem;
          font-weight: 600;
          color: #0f172a;
          text-align: right;
          max-width: 58%;
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

        .ch-pdf-link {
          font-size: 0.8rem;
          font-weight: 600;
          color: #2563eb;
          text-decoration: none;
          display: inline-flex;
          align-items: center;
          gap: 0.3rem;
          padding: 0.25rem 0.6rem;
          border-radius: 6px;
          background: #eff6ff;
          transition: all 0.15s;
        }
        .ch-pdf-link:hover {
          background: #dbeafe;
          text-decoration: none;
        }
      `}</style>
    </div>
  );
}


/* ── Score Bar Component ── */
function ScoreBar({ label, score, details }: { label: string; score?: number | null; details?: { value?: string; explanation?: string } }) {
  if (score == null) return null;

  const pct = Math.round(score * 100);
  const color = pct >= 70 ? '#16a34a' : pct >= 40 ? '#d97706' : '#dc2626';
  const bgColor = pct >= 70 ? '#dcfce7' : pct >= 40 ? '#fef9c3' : '#fee2e2';

  return (
    <div className="score-bar-item">
      <div className="score-bar-header">
        <span className="score-bar-label">{label}</span>
        <span className="score-bar-pct" style={{ color }}>{pct}</span>
      </div>
      <div className="score-bar-track">
        <div className="score-bar-fill" style={{ width: `${pct}%`, background: color }} />
      </div>
      {details?.explanation && (
        <p className="score-bar-detail">{details.explanation}</p>
      )}

      <style jsx>{`
        .score-bar-item {
          padding: 0.45rem 0;
        }

        .score-bar-header {
          display: flex;
          justify-content: space-between;
          align-items: center;
          margin-bottom: 0.25rem;
        }

        .score-bar-label {
          font-size: 0.74rem;
          font-weight: 600;
          color: #475569;
        }

        .score-bar-pct {
          font-size: 0.78rem;
          font-weight: 800;
        }

        .score-bar-track {
          height: 6px;
          background: #f1f5f9;
          border-radius: 3px;
          overflow: hidden;
        }

        .score-bar-fill {
          height: 100%;
          border-radius: 3px;
          transition: width 0.4s ease;
        }

        .score-bar-detail {
          font-size: 0.7rem;
          color: #94a3b8;
          margin: 0.2rem 0 0;
          line-height: 1.4;
        }
      `}</style>
    </div>
  );
}
