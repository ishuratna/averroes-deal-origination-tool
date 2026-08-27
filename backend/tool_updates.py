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
