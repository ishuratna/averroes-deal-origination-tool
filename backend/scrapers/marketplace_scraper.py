import logging
from typing import List, Dict

logger = logging.getLogger(__name__)


class MarketplaceScraper:
    """
    Marketplace deal sources: Acquire.com, Flippa, Microns, SideProjectors.

    HONESTY NOTE: These marketplaces require authenticated sessions and/or
    JavaScript rendering (Playwright) to scrape. Real integrations are not yet
    built, so scraping returns EMPTY rather than fabricated demo data.
    (Demo/simulated listings were removed 2026-07 — never fabricate deal data.)

    Integration options per source:
      - Acquire.com: requires a (free) buyer account + Playwright session.
      - Flippa: public API exists (api.flippa.com) but is rate-limited; JS site.
      - Microns: newsletter-driven; listings page is client-rendered.
      - SideProjectors: client-rendered search.
    """

    def __init__(self):
        self.sources = {
            "Acquire.com": "https://acquire.com/marketplaces/saas/",
            "Flippa": "https://flippa.com/search?filter[property_type]=saas",
            "Microns": "https://microns.io/browse-listings",
            "SideProjectors": "https://sideprojectors.com/project/search?type=saas",
        }

    def scrape_all(self) -> List[Dict]:
        all_listings = []
        for name in self.sources.keys():
            all_listings.extend(self.scrape_source(name))
        return all_listings

    def scrape_source(self, source_name: str) -> List[Dict]:
        if source_name not in self.sources:
            logger.error(f"Unknown marketplace source: {source_name}")
            return []
        logger.warning(
            f"[Marketplace] '{source_name}' requires authenticated/JS scraping — "
            f"real integration pending, returning no companies (no demo data)."
        )
        return []

    def get_supported_sources(self) -> List[str]:
        return list(self.sources.keys())
