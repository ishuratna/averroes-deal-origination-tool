"""
Tool updates, curated for the weekly review page.

One plain-English line per user-visible change, newest first. This list is
MAINTAINED BY HAND whenever a change ships (the deployed container has no git
history to read, and commit messages are written for engineers anyway).
Doctrine: every session that changes behaviour Ishu's team can see appends an
entry here, dated with the commit date.
"""

# (date YYYY-MM-DD, one line a non-engineer understands)
TOOL_UPDATES = [
    ("2026-09-08", "Investor card: clicking an investor's name (Universe or Pipeline) opens one card with their profile, portfolio connections, the full email thread, the activity trail (stage moves, sends, replies, InvestorFill, your notes) and the Outreach and stage controls. Bounced LP emails now pull the investor back to Researched and retire the dead address, exactly as for founders. The Wednesday review gained an Investor loop section (stage counts, last-7-day sends and replies, who needs attention)."),
    ("2026-09-08", "Investor outreach loop: the Investor Universe and Investor Pipeline now work like the founder loop. Stages are Identified → Researched (InvestorFill) → Contacted (we emailed) → Responded (they genuinely replied) → Meeting → Committed, with Passed and Talk Later parked behind a reason. The Outreach button drafts a tailored LP introduction once (saved, then Review & Send), follows up in the same thread after 14 days, and turns into Reply once they answer; sends go from a dedicated investor mailbox with its own signature (configured on Cloud Run) and never from Bea's founder mailbox. Sync Emails reads that mailbox too, moves Contacted → Responded on a real reply (autoresponders and bounces never count), and the board shows the follow-up queue (14 days waiting on them / 7 days we owe a reply), 'ball with us' and silent-days signals."),
    ("2026-09-08", "Financials by year: every figure we hold for a company now lives in one year-by-year store - revenue, ARR, gross profit and margin, EBITDA and margin, PBT, net income, cash, net assets, total assets, headcount, customers, plus a revenue split by product/segment - for as many years as the sources give, with the source and evidence behind each number on hover. Companies House filings and uploaded documents both feed it (documents read revenue-by-year charts too); the Financials tab shows the full grid, budget/forecast years greyed."),
    ("2026-09-07", "Document SmartFill: a deck, accounts or data-room file (PDF, PowerPoint, Excel or Word) uploaded on a company card - or attached to the company's email - is read in full. Anything the record lacks (financials by year, headcount, funding, investors, founders, description...) is filled at once; anything that disagrees with a stored value is shown side by side with its evidence and you tick what to replace. The fit score is recomputed after every change. Uploads up to 100MB now work (large files go straight to storage; the old button failed with 'Failed to fetch' on anything over 32MB)."),
    ("2026-09-07", "Outreach emails now carry Bea's full signature: the Averroes Capital logo, name and title, the outreach email link, and the regulatory disclaimer - matching her real signature."),
    ("2026-08-28", "Companies House accounts are now read from the machine-tagged iXBRL filing instead of an AI reading the PDF: every figure (revenue, cash, employees, net assets) is exact, auditable and free. The AI read remains only for old paper-scan filings - this removes the single largest AI cost inside SmartFill."),
    ("2026-08-28", "Delivery check fix: when an email bounced but a newer email was later sent (usually to a corrected address), the old bounce no longer drags the company back to Qualified - the newest send takes precedence and is judged on its own."),
    ("2026-08-28", "Responded page made first-timer friendly: the weekly list now has two clearly named tables - 'High Fit, Right Size companies' (inside Averroes' investment range, for Bea) and 'Good Fit, Small Companies' (right fit, not yet the size - kept warm by the associates). The 'probably ready' hint was removed, and companies parked before reasons existed show an 'add reason' button that never resets the Talk-later clock."),
    ("2026-08-27", "Identity guard: AI research is now anchored to the company we actually mean (website, city, founder, founding year, CH number). If the research finds a different same-named company, its details are refused instead of written, and the profile shows how identity was verified. A free audit can flag past mixups for re-research."),
    ("2026-08-27", "Talk later and Not interested now carry a REASON: parking a company asks for a 2-3 word bucket (15 to pick from) plus optional detail; the parked lists show 'Reason: ...' with the detail on hover, and unparking clears it."),
    ("2026-08-27", "New NEWS section on the company profile: a Refresh button runs one AI web search and saves the top clickable articles (title, source, date). Cached until refreshed, so browsing costs nothing."),
    ("2026-08-27", "Fit score dimensions are now clickable: each one opens the stored evidence - the inputs, the rule applied and the data source - instead of just a hover sentence."),
    ("2026-08-27", "Email documents now also captures decks shared as LINKS: direct PDF links in a founder's reply are downloaded (behind a strict safety check) and filed like attachments, and an Upload document button on the company profile handles Drive/Dropbox links you download yourself. Both run the same AI-read pipeline."),
    ("2026-08-27", "IC Memo rebuilt: one click on any Responded-or-later card now builds the full 4-slide screening deck in the house CIM format (summary, overview with criteria scorecard and financials, rationale and risks, diligence questions) and downloads it as PowerPoint. Company numbers come only from the record; market context is AI-researched and tagged."),
    ("2026-08-21", "Responded page rebuilt as a three-step funnel: Nurture (Ishu) → the weekly lists → Qualified leads (Bea), with collapsible sections and the Wednesday/Thursday routes drawn side by side."),
    ("2026-08-21", "When a send-time edit or a founder's reply changes the contact, the person SmartFill originally found is now preserved on the profile (\"Originally: ...\")."),
    ("2026-08-21", "Email documents: any file a founder attaches to an email is filed to the company profile automatically; AI reads decks/accounts and updates revenue, headcount and description with the evidence logged."),
    ("2026-08-21", "Company Deep Research always returns full findings now; the fit verdict is applied after the research instead of blocking it."),
    ("2026-08-21", "Analytics page rebuilt: cumulative funnel with conversion percentages, daily email and SmartFill charts, and autoresponders no longer count as replies in the response rate."),
    ("2026-08-21", "Nightly auto-SmartFill fixed and tuned: 60 companies enriched per night, best prospects first (Gain, then Inven), about £4/day."),
    ("2026-08-18", "Follow-ups now thread as real replies in the founder's inbox instead of arriving as separate emails."),
    ("2026-08-18", "Pipeline cards unified to one template with labelled financials; a 'Followed up' state resets each card's clock and stale outline."),
    ("2026-08-18", "The address and greeting actually sent become the stored contact (applied live and retroactively); replies from a different domain are adopted only after a third-party check."),
    ("2026-08-18", "Fit score v4: employee growth read from Companies House filings, declining revenue scores zero, and new revenue-size bands centred on the mandate."),
    ("2026-08-18", "Morning email sync runs by itself at 6 AM London."),
    ("2026-08-17", "Stage integrity enforced end to end: one definition of a genuine reply everywhere, delivery verification (bounces and silently-failed sends pull companies back), and two auth gaps closed."),
]


def updates_since(days: int = 8, minimum: int = 3):
    """Entries from the last `days` days; if the week was quiet, the latest
    `minimum` entries anyway so the meeting slide is never blank."""
    from datetime import date, timedelta
    cutoff = (date.today() - timedelta(days=days)).isoformat()
    recent = [{"date": d, "text": t} for d, t in TOOL_UPDATES if d >= cutoff]
    if len(recent) < minimum:
        recent = [{"date": d, "text": t} for d, t in TOOL_UPDATES[:max(minimum, len(recent))]]
    return recent
