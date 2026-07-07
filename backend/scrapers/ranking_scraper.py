import re
import requests
from bs4 import BeautifulSoup
from typing import List, Dict
import logging

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

HEADERS = {"User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0 Safari/537.36"}

# Startups 100 list years to try, newest first
STARTUPS100_YEARS = [2026, 2025]


class RankingListScraper:
    """
    High-growth ranking lists.

    Real scraper: Startups 100 UK (startups.co.uk — server-rendered, public).
    NOT scrapeable (return empty, no demo data — removed 2026-07):
      - FT 1000: paywalled interactive table on ft.com.
      - Deloitte Fast 50 UK: JavaScript-rendered page, no static list.
    """

    def __init__(self):
        self.sources = {
            "FT 1000": "https://www.ft.com/ft1000",
            "Startups 100 UK": "https://startups.co.uk/startups-100/",
            "Deloitte Fast 50 UK": "https://www2.deloitte.com/uk/en/pages/technology-fast-50/articles/technology-fast-50-winners.html",
        }

    def scrape_ranking(self, list_name: str) -> List[Dict]:
        logger.info(f"Ingesting Ranking List: {list_name}")

        if list_name == "Startups 100 UK":
            return self._scrape_startups100()

        if list_name in self.sources:
            logger.warning(
                f"[Ranking] '{list_name}' is not scrapeable "
                f"({'paywalled' if 'FT' in list_name else 'JS-rendered'}) — "
                f"returning no companies (no demo data)."
            )
            return []

        logger.error(f"Unknown ranking list: {list_name}")
        return []

    def _scrape_startups100(self) -> List[Dict]:
        """
        Scrape the Startups 100 UK list. Entries appear as links to
        /startups-100/{year}/{slug}/ with heading text like "12. CompanyName".
        Tries the newest year's full-list page first.
        """
        companies: Dict[str, Dict] = {}

        for year in STARTUPS100_YEARS:
            urls = [
                f"https://startups.co.uk/startups-100/{year}/main-page/",
                f"https://startups.co.uk/startups-100/{year}/",
            ]
            for url in urls:
                try:
                    resp = requests.get(url, headers=HEADERS, timeout=20)
                    if resp.status_code != 200:
                        continue
                except Exception as e:
                    logger.warning(f"[Startups100] Fetch failed for {url}: {e}")
                    continue

                soup = BeautifulSoup(resp.text, "html.parser")
                new_count = 0
                for a in soup.find_all("a", href=True):
                    href = a["href"]
                    if f"/startups-100/{year}/" not in href:
                        continue
                    text = a.get_text(" ", strip=True)
                    # Match "12. CompanyName" (rank-prefixed entries only)
                    m = re.match(r"^(\d{1,3})\.\s+(.{2,60})$", text)
                    if not m:
                        continue
                    rank, name = int(m.group(1)), m.group(2).strip()
                    key = name.lower()
                    if key in companies:
                        continue
                    companies[key] = {
                        "name": name,
                        "website": "",
                        "sector": "Technology",
                        "region": "UK",
                        "description": f"Ranked #{rank} in the Startups 100 {year} (startups.co.uk annual ranking of the UK's most innovative new businesses).",
                        "source": f"Startups 100 UK {year}",
                        "status": "Scraped",
                    }
                    new_count += 1

                logger.info(f"[Startups100] {url}: +{new_count} (total {len(companies)})")

            if companies:
                break  # got a year's list — don't mix years

        if not companies:
            logger.warning("[Startups100] No companies parsed — page structure may have changed.")
        return list(companies.values())

    def get_supported_lists(self) -> List[str]:
        return list(self.sources.keys())
