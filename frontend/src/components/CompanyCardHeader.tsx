'use client';

// The top of a Pipeline card, as ONE component: name, sector · city, the
// band / fit / owner badges and the Revenue / EBITDA / Team row. Rendered by
// the kanban card and by the outreach modal (Ishu, 5 Nov 2026: "when we
// open that pop-up it should also show the company card ... exactly the
// same format ... so I know what kind of company I'm sending emails to").
// Same intent, same component, same CSS: the two can never drift
// (doctrine 2). Its CSS lives in globals.css under "CompanyCardHeader":
// the first cut carried it as component-scoped styled-jsx and production
// rendered the block bare (6 Nov 2026), so the rules are global on purpose.
// The timing chips (days in stage, replied) are the card's
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

    </div>
  );
}
