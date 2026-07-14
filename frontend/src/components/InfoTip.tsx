"use client";
import React, { useState } from "react";

/**
 * Hover tooltip for column headers / headings.
 * Usage: <InfoTip label="Status" tip={DEFS.status} />
 *
 * Implementation note: uses INLINE styles + React hover state (not CSS classes)
 * so the popup is hidden in the very first server-rendered paint — styled-jsx
 * class-based hiding caused a flash of raw definition text on initial load.
 */
export default function InfoTip({ label, tip }: { label: React.ReactNode; tip?: string }) {
  const [open, setOpen] = useState(false);
  if (!tip) return <>{label}</>;

  return (
    <span
      style={{ position: "relative", display: "inline-block" }}
      onMouseEnter={() => setOpen(true)}
      onMouseLeave={() => setOpen(false)}
    >
      <span style={{ borderBottom: "1px dotted currentColor", cursor: "help" }}>{label}</span>
      {open && (
        <span
          style={{
            position: "absolute",
            top: "calc(100% + 7px)",
            left: 0,
            background: "#0f172a",
            color: "#f1f5f9",
            fontSize: "0.7rem",
            fontWeight: 500,
            lineHeight: 1.45,
            textTransform: "none",
            letterSpacing: "normal",
            whiteSpace: "normal",
            padding: "0.5rem 0.65rem",
            borderRadius: "6px",
            width: "max-content",
            maxWidth: "250px",
            boxShadow: "0 8px 20px rgba(2, 6, 23, 0.35)",
            zIndex: 500,
            pointerEvents: "none",
            textAlign: "left",
          }}
        >
          {tip}
        </span>
      )}
    </span>
  );
}

/**
 * Definitions for every metric / column / heading across the two pages.
 * Single source of truth — keep in sync with the business rules in the backend.
 */
export const DEFS: Record<string, string> = {
  // ── Universe table columns ──
  company: "Company name as ingested from the source (PitchBook upload, scraper or manual). Click to open the full company card.",
  fit: "Averroes Fit Score (0–100): average of 5 metrics — revenue growth, revenue size, employee growth, business model fit, market sentiment. At least 4 of 5 must be assessable, otherwise unscored. Green ≥ 70, amber 40–69, red < 40.",
  website: "Company website, from source data or found by AI enrichment.",
  sector: "Industry classification from source data or inferred by AI. Feeds the technology/SaaS qualification filter.",
  region: "Headquarters location. Drives the geography filter — companies outside UK/Ireland are marked Not a Fit.",
  employees: "Headcount from source data. Also used as a revenue proxy (~£100K revenue per employee) when no revenue is filed.",
  founded: "Year the company was founded.",
  age: "Years since founding.",
  raised: "Total funding raised to date (PitchBook).",
  valuation: "Estimated valuation (PitchBook). Also a revenue proxy (valuation ÷ 6). Thesis target range: £15–50M.",
  revenueFY: "Latest financial-year revenue. Filed Companies House accounts first, then PitchBook. '~ (est.)' means estimated from proxies (employees, assets, EBITDA, valuation, funding) — hover the value for detail.",
  revenuePrevFY: "Prior financial-year revenue from Companies House filings. Compared against the current FY to compute the revenue-growth score.",
  band: "Deal band by revenue (actual or estimated): Too Early < £2.5M · Target Band £2.5–40M (core sweet spot £8–20M) · Too Large > £40M. Calibrated to £15–40M equity cheques at 25–100% stakes. Companies over £40M revenue fail the size filter.",
  ebitda: "Estimated EBITDA from source data. The investment thesis targets £1–10M EBITDA.",
  profit: "Latest filed profit from Companies House accounts (or net income from PitchBook).",
  assets: "Total assets from the latest Companies House filing. Used as a revenue proxy (× 2.5) when turnover is not disclosed.",
  size: "Size bucket from revenue or AI estimate: Micro < £5M, Small £5–15M, Mid £15–50M all qualify; Large > £50M is rejected by the size filter.",
  status: "Deal lifecycle stage: Scraped/Uploaded (raw, not yet assessed) → Qualified or Not a Fit (after SmartFill) → Contacted → Meeting → Due Diligence → Offer → Won/Lost. 'Engaged' is set automatically when an outreach email is sent.",
  leadership: "Founder/CEO name discovered by AI enrichment (live web search).",
  email: "Contact email found by enrichment. Click to draft an AI outreach email.",
  linkedin: "Company or founder LinkedIn profile.",
  source: "Where this company was ingested from (file upload, marketplace, conference, ranking or directory). Raw ingest — no AI is applied until SmartFill.",
  dateAdded: "When the company was first added to the universe.",
  description: "Company summary from source data or AI enrichment.",
  actions: "SmartFill runs the full AI pipeline for this company: qualification (3 hard filters) → founder enrichment → Companies House financials → fit scoring. Outreach drafts a personalised email from the stored data.",

  // ── Fit Score metrics (company drawer) ──
  metricEmployeeGrowth: "YoY headcount trend from live web search — LinkedIn, job postings, hiring/layoff news. 0–20 shrinking · 40–60 stable · 60–80 healthy growth (10–30% YoY) · 80–100 rapid expansion.",
  metricRevenueGrowth: "YoY revenue change from filed Companies House accounts (latest vs prior year). Negative growth scores 0–20; 10–25% scores 50–75; 50%+ scores 90–100.",
  metricRevenueSize: "Fit to the £5–40M investable envelope; the £8–20M core sweet spot scores 100. Revenue from filed accounts, or estimated from proxies (employees, assets, EBITDA, valuation, funding) when not disclosed.",
  metricBusinessFit: "Alignment with the Averroes thesis, assessed via web search: B2B? SaaS/recurring revenue? Tech at the core? Pure B2B SaaS scores 80–100; B2C or non-tech scores under 20.",
  metricMarketSentiment: "Brand and market signals from web search — press coverage, awards, reviews, thought leadership. Strong brand scores 80–100; negative coverage under 20.",

  // ── Pipeline stages ──
  stageQualified: "Passed all 3 hard filters: UK/Ireland geography, technology/SaaS industry, and revenue under £50M. Awaiting first contact.",
  stageContacted: "Outreach has been made to the founder or company.",
  stageMeeting: "An intro call or meeting is scheduled or has taken place.",
  stageDD: "Due diligence — reviewing financials, contracts, tech and team in depth.",
  stageOffer: "A term sheet or offer has been put forward.",
  stageEngaged: "An outreach email was sent from the platform (set automatically on send).",
  stageWon: "Deal closed successfully.",
  stageLost: "Deal did not proceed — passed, lost or went cold.",

  // ── Pipeline filters ──
  filterSaaS: "Filter by AI-assessed business model: pure B2B SaaS (strongest thesis fit) vs broader B2B tech.",
  filterOwnership: "Filter by ownership structure. Founder-led and bootstrapped companies are preferred under the Averroes thesis.",
  filterGrowth: "Filter by growth signals detected in the data — hiring momentum, revenue trend and funding activity.",
};

/** Definitions keyed by raw stage name, for Kanban column headers. */
export const STAGE_DEFS: Record<string, string> = {
  Qualified: DEFS.stageQualified,
  Contacted: DEFS.stageContacted,
  Meeting: DEFS.stageMeeting,
  DD: DEFS.stageDD,
  Offer: DEFS.stageOffer,
  Engaged: DEFS.stageEngaged,
  Won: DEFS.stageWon,
  Lost: DEFS.stageLost,
};
