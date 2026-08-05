"""
Companies House — SIC code registry search (deal-sourcing side).

Pulls active UK companies straight from the official register, filtered by
SIC code (the industry classification CH files every company under). Mirrors
scrapers/investor_scraper.py's CH SIC search, which already does this for LP
sourcing — same API, same "raw extraction only, no AI" doctrine, applied to
the `targets` table instead of `investors`.

WHY THIS EXISTS: SIC codes are the one dimension the CH register lets you
filter on for free, at scale, with zero AI cost. It is a BLUNT instrument —
CH files a company under a SIC code regardless of revenue, headcount, or
whether it is even trading — so this is intentionally a wide net. Every row
lands as an ordinary Scraped record; the existing hard filters and SmartFill
decide fit exactly as they do for every other source. NO scoring happens here.

KNOWN CH LIMIT (verified against the CH developer forum, 2026-08-05): the
advanced-search endpoint 500s once start_index + size passes roughly 10,000,
regardless of how many companies actually match. For a handful of these SIC
codes (IT consultancy, "other professional/scientific/technical n.e.c.") the
true active-company count is very likely above that ceiling, so a pull for
those codes will NOT be exhaustive — this is a Companies House limitation,
not a choice made here. `hit_ceiling` on the returned summary says so per
code, honestly, rather than silently returning a partial set that looks complete.

TWO WAYS THIS RUNS, on purpose:
  * Friday auto-refresh (main.py's weekly-refresh loop, inside /ch-watch/run)
    calls scrape_source() -> scrape_weekly_batch(): a BOUNDED slice of
    _WEEKLY_BATCH_SIZE codes, chosen deterministically by ISO week number, so
    it never risks blowing that endpoint's own timeout budget. Cycles through
    all 16 codes over several weeks.
  * The manual "Refresh" button (POST /ingest/ch-sic, streamed/heartbeated)
    calls scrape_all_sic_codes_detailed(): every code, right now, no batching
    — safe because that endpoint holds the connection open with heartbeats
    instead of racing a shared timeout.
"""
import os
import math
import logging
from datetime import datetime, timezone
from typing import Dict, List, Tuple

import requests

logger = logging.getLogger(__name__)

CH_API_BASE = "https://api.company-information.service.gov.uk"

# Labels verified against Companies House's own SIC 2007 reference
# (resources.companieshouse.gov.uk/sic/) — not guessed.
SIC_CODES: List[Tuple[str, str]] = [
    ("58210", "Publishing of computer games"),
    ("58290", "Other software publishing"),
    ("62011", "Ready-made interactive leisure and entertainment software development"),
    ("62012", "Business and domestic software development"),
    ("62020", "Information technology consultancy activities"),
    ("62030", "Computer facilities management activities"),
    ("62090", "Other information technology and computer service activities"),
    ("63110", "Data processing, hosting and related activities"),
    ("63120", "Web portals"),
    ("63990", "Other information service activities n.e.c."),
    ("70229", "Management consultancy activities other than financial management"),
    ("73110", "Advertising agencies"),
    ("73200", "Market research and public opinion polling"),
    ("74100", "Specialised design activities"),
    ("74909", "Other professional, scientific and technical activities n.e.c."),
    ("82990", "Other business support service activities n.e.c."),
]

_PAGE_SIZE = 5000          # CH's documented maximum for `size`
_CEILING = 10000           # CH 500s past this regardless of `size`/`start_index`

# Weekly auto-refresh runs a BOUNDED slice, not all 16 codes — it rides inside
# /ch-watch/run, which already budgets itself against Cloud Run's request
# timeout ("~80 companies ≈ 1-2 min worst case" per that endpoint's own
# comment). 4 codes x up to 2 pages each = at most 8 sequential CH calls,
# roughly 8-25s added — safe alongside that existing budget. A full pull
# (all 16 codes, no ceiling on time since it's held open some other way) is
# still available on demand via the streamed /ingest/ch-sic Refresh button.
_WEEKLY_BATCH_SIZE = 4


def _ch_auth():
    key = os.getenv("COMPANIES_HOUSE_API_KEY", "")
    return (key, "")


def _pull_one_sic(sic: str, label: str) -> Tuple[List[Dict], bool]:
    """One SIC code, paged to CH's own ceiling. Returns (rows, hit_ceiling)."""
    key = _ch_auth()[0]
    if not key:
        return [], False

    rows: List[Dict] = []
    start = 0
    while start < _CEILING:
        try:
            resp = requests.get(
                f"{CH_API_BASE}/advanced-search/companies",
                params={"sic_codes": sic, "company_status": "active",
                        "size": _PAGE_SIZE, "start_index": start},
                auth=(key, ""), timeout=30,
            )
            resp.raise_for_status()
            items = resp.json().get("items", [])
        except Exception as e:
            logger.warning(f"[CH SIC] {sic} failed at start_index={start}: {e}")
            break
        if not items:
            break

        for item in items:
            title = (item.get("company_name") or item.get("title") or "").strip()
            number = (item.get("company_number") or "").strip()
            if not title or not number:
                continue
            addr = item.get("registered_office_address", {}) or item.get("address", {}) or {}
            rows.append({
                "name": title.title(),
                "sector": label,
                "region": "UK",
                "hq_city": addr.get("locality") or "",
                "registration_number": number,
                "description": (
                    f"Active UK company registered under SIC {sic} ({label}) per "
                    f"Companies House, inc. {item.get('date_of_creation', 'n/a')}."
                ),
                "source": "Companies House SIC Search",
                "status": "Scraped",
                "match_score": 0.0,
            })

        if len(items) < _PAGE_SIZE:
            break
        start += _PAGE_SIZE

    hit_ceiling = start >= _CEILING and len(rows) > 0
    return rows, hit_ceiling


class CHSicScraper:
    """Same get_supported_sources()/scrape_source() interface as every other
    scraper in scrapers/ — lets this slot straight into main.py's weekly
    refresh loop and the /ingest/* endpoint pattern with no special-casing."""

    SOURCES = ["Companies House SIC Search"]

    def get_supported_sources(self) -> List[str]:
        return self.SOURCES

    def scrape_source(self, source_name: str, **kwargs) -> List[Dict]:
        """Called by main.py's generic weekly-refresh loop (the same interface
        every sibling scraper exposes) — deliberately runs the BOUNDED weekly
        batch, not the full 16-code pull. See _WEEKLY_BATCH_SIZE for why: the
        automated path shares a timeout budget with other daily jobs that a
        full pull would risk blowing. On-demand full pulls go through
        scrape_all_sic_codes_detailed() via the separately streamed
        /ingest/ch-sic endpoint, which has no such constraint."""
        if source_name != "Companies House SIC Search":
            logger.error(f"Unknown CH SIC scraper source: {source_name}")
            return []
        return self.scrape_weekly_batch()

    def scrape_weekly_batch(self, batch_size: int = _WEEKLY_BATCH_SIZE) -> List[Dict]:
        """This week's slice of SIC codes, chosen deterministically by ISO
        week number — no cursor/state to persist or lose. Cycles through all
        16 codes over ceil(16/batch_size) weeks, then repeats. A failed or
        skipped week just tries the next slice next Friday; ingestion is
        merge-safe, so re-covering a code later never duplicates anything.
        """
        if not os.getenv("COMPANIES_HOUSE_API_KEY", ""):
            logger.error("[CH SIC] COMPANIES_HOUSE_API_KEY not configured")
            return []

        num_batches = math.ceil(len(SIC_CODES) / batch_size)
        week_num = datetime.now(timezone.utc).isocalendar()[1]
        batch_idx = week_num % num_batches
        this_week = SIC_CODES[batch_idx * batch_size: batch_idx * batch_size + batch_size]

        by_name: Dict[str, Dict] = {}
        capped: List[str] = []
        for sic, label in this_week:
            rows, hit_ceiling = _pull_one_sic(sic, label)
            for r in rows:
                by_name.setdefault(r["name"].lower(), r)
            if hit_ceiling:
                capped.append(sic)

        logger.info(f"[CH SIC] weekly batch {batch_idx + 1}/{num_batches} "
                   f"({', '.join(f'{s}' for s, _ in this_week)}): {len(by_name)} companies"
                   + (f" — ceiling hit for {', '.join(capped)}" if capped else ""))
        return list(by_name.values())

    def scrape_all_sic_codes(self) -> List[Dict]:
        """Plain List[Dict] — the full pull across all 16 codes, used by the
        on-demand /ingest/ch-sic endpoint. Use scrape_all_sic_codes_detailed()
        when the caller can surface which codes hit CH's paging ceiling."""
        rows, _ = self.scrape_all_sic_codes_detailed()
        return rows

    def scrape_all_sic_codes_detailed(self) -> Tuple[List[Dict], List[str]]:
        """Same pull, plus which SIC codes were NOT exhaustive (hit CH's own
        ~10,000-result paging ceiling) — the dedicated ingest endpoint surfaces
        this to the user instead of silently returning a partial set."""
        if not os.getenv("COMPANIES_HOUSE_API_KEY", ""):
            logger.error("[CH SIC] COMPANIES_HOUSE_API_KEY not configured")
            return [], []

        by_name: Dict[str, Dict] = {}
        capped_codes: List[str] = []
        for sic, label in SIC_CODES:
            rows, hit_ceiling = _pull_one_sic(sic, label)
            new = 0
            for r in rows:
                key = r["name"].lower()
                if key not in by_name:
                    by_name[key] = r
                    new += 1
            if hit_ceiling:
                capped_codes.append(f"{sic} ({label})")
            logger.info(f"[CH SIC] {sic} '{label}': +{new} new (pool now {len(by_name)})"
                       + (" — HIT CH'S PAGING CEILING, not exhaustive" if hit_ceiling else ""))

        result = list(by_name.values())
        if capped_codes:
            logger.warning(f"[CH SIC] Not exhaustive for: {', '.join(capped_codes)} "
                          f"— Companies House does not allow paging past ~{_CEILING} results per code.")
        return result, capped_codes
