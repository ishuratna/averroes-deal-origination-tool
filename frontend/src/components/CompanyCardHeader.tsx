'use client';

// The top of a Pipeline card, as ONE component: name, sector · city, the
// band / fit / owner badges and the Revenue / EBITDA / Team row. Rendered by
// the kanban card and by the outreach modal (Ishu, 5 Nov 2026: "when we
// open that pop-up it should also show the company card ... exactly the
// same format ... so I know what kind of company I'm sending emails to").
// Same intent, same component, same CSS: the two can never drift
// (doctrine 2). The timing chips (days in stage, replied) are the card's
// own and are passed in as an optional slot, because the modal has no
// column clock to show.

import React from 'react';
import { CompanyTarget, getRevenueBand } from '../types';
import OwnerTag from './OwnerTag';

export default function CompanyCardHeader({
  company,
  onNameClick,
  badges,
  compact = false,
}: {
  company: CompanyTarget;
  onNameClick?: () => void;
  badges?: React.ReactNode;     // the card's timing chips (days, replied)
  compact?: boolean;            // inside the modal: a little more room
}) {
  const band = getRevenueBand(company);
  const revM = company.revenue_y1 ? company.revenue_y1 / 1e6 : company.revenue_m || null;
  const revEst = !revM && company.revenue_estimate_m ? company.revenue_estimate_m : null;
  const team = company.employees || company.employees_ch || null;
  const hasMetrics = !!(revM || revEst || company.estimated_ebitda || team);

  return (
    <div className={`cch ${compact ? 'cch-compact' : ''}`}>
      <div className="kc-header">
        {onNameClick ? (
          <button className="kc-name" onClick={onNameClick}>{company.name}</button>
        ) : (
          <span className="kc-name kc-name-static">{company.name}</span>
        )}
        {badges ? <div className="kc-header-badges">{badges}</div> : null}
      </div>
      <p className="kc-meta">
        {company.sector || 'Tech'}
        {company.hq_city ? ` · ${company.hq_city}` : company.region ? ` · ${company.region}` : ''}
      </p>

      {(band || company.averroes_fit_score != null || company.owner) && (
        <div className="kc-badge-row">
          {band && (
            <span className={`kc-band-badge band-${band.toLowerCase().replace(/\s+/g, '-')}`}>{band}</span>
          )}
          {company.averroes_fit_score != null && (
            <span className={`kc-fit-badge ${company.averroes_fit_score >= 0.7 ? 'high' : company.averroes_fit_score >= 0.4 ? 'mid' : 'low'}`}
              title="Averroes fit score">
              Fit {Math.round(company.averroes_fit_score * 100)}
            </span>
          )}
          {company.owner && <OwnerTag owner={company.owner} />}
        </div>
      )}

      {hasMetrics && (
        <div className="kc-metrics">
          {(revM || revEst) && (
            <span className="kc-metric">
              <span className="kc-metric-label">Revenue</span>
              <span className="kc-metric-value">{revM ? `£${revM.toFixed(1)}M` : `~£${revEst!.toFixed(1)}M`}{revEst ? <em className="kc-est"> est.</em> : null}</span>
            </span>
          )}
          {company.estimated_ebitda ? (
            <span className="kc-metric">
              <span className="kc-metric-label">EBITDA</span>
              <span className="kc-metric-value">&pound;{company.estimated_ebitda}M</span>
            </span>
          ) : null}
          {team ? (
            <span className="kc-metric">
              <span className="kc-metric-label">Team</span>
              <span className="kc-metric-value">{team}</span>
            </span>
          ) : null}
        </div>
      )}

      <style jsx>{`
        .kc-header { display: flex; justify-content: space-between; align-items: flex-start; gap: 0.4rem; }
        .kc-header-badges { display: flex; gap: 0.3rem; align-items: center; flex-shrink: 0; }
        .kc-name {
          font-size: 0.82rem; font-weight: 700; color: #0f172a; background: none; border: none;
          padding: 0; cursor: pointer; text-align: left; line-height: 1.3; letter-spacing: -0.01em;
        }
        .kc-name:hover { color: #2563eb; }
        .kc-name-static { cursor: default; }
        .kc-name-static:hover { color: #0f172a; }
        /* Sector and city: sentence case, small and grey. */
        .kc-meta { font-size: 0.62rem; color: #94a3b8; font-weight: 500; margin: 0.1rem 0 0; }
        .kc-badge-row { display: flex; align-items: center; gap: 0.35rem; flex-wrap: wrap; margin-top: 0.5rem; }
        .kc-fit-badge { font-size: 0.62rem; font-weight: 800; padding: 0.12rem 0.45rem; border-radius: 999px; color: white; letter-spacing: 0.02em; }
        .kc-fit-badge.high { background: #16a34a; }
        .kc-fit-badge.mid { background: #d97706; }
        .kc-fit-badge.low { background: #dc2626; }
        .kc-band-badge { font-size: 0.58rem; font-weight: 800; padding: 0.12rem 0.45rem; border-radius: 999px; text-transform: uppercase; letter-spacing: 0.04em; white-space: nowrap; }
        .kc-band-badge.band-target-band { background: #dcfce7; color: #166534; }
        .kc-band-badge.band-too-early { background: #fef3c7; color: #92400e; }
        .kc-band-badge.band-too-large { background: #fef2f2; color: #dc2626; }
        /* Labelled financial columns: Revenue / EBITDA / Team scan better
           labelled than as one prose line. */
        .kc-metrics { display: flex; gap: 0; margin-top: 0.5rem; padding: 0.4rem 0; border-top: 1px solid #f1f5f9; border-bottom: 1px solid #f1f5f9; }
        .kc-metric { display: flex; flex-direction: column; flex: 1; min-width: 0; padding: 0 0.55rem; border-left: 1px solid #f1f5f9; }
        .kc-metric:first-child { padding-left: 0; border-left: none; }
        .kc-metric-label { font-size: 0.56rem; color: #94a3b8; font-weight: 700; text-transform: uppercase; letter-spacing: 0.05em; margin-bottom: 0.1rem; }
        .kc-metric-value { font-size: 0.78rem; font-weight: 700; color: #0f172a; white-space: nowrap; }
        .kc-est { font-size: 0.6rem; color: #94a3b8; font-weight: 600; font-style: normal; }
        /* In the modal the same block sits in a quiet panel, a touch larger. */
        .cch-compact { background: #f8fafc; border: 1px solid #e2e8f0; border-radius: 8px; padding: 0.7rem 0.85rem; }
        .cch-compact .kc-name { font-size: 0.92rem; }
        .cch-compact .kc-meta { font-size: 0.7rem; }
        .cch-compact .kc-metric-value { font-size: 0.85rem; }
        .cch-compact .kc-metrics { border-bottom: none; padding-bottom: 0; }
      `}</style>
    </div>
  );
}
