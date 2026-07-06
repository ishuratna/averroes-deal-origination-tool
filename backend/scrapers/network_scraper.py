"""
Network Scraper — founder-network / accelerator alumni sources.

Sources:
  1. "EF Alumni"  — Entrepreneur First public portfolio directory (joinef.com/portfolio).
     Server-rendered WordPress, paginated via ?pagenum=N. ~645 companies.
     Default: UK-relevant only (London location), since the fund thesis is UK/Ireland.
  2. "Tech Nation" — Future Fifty cohort announcement pages (technation.io).
     Each article lists companies as: <a href="website">Name</a> – description.

Architecture rule: RAW data only — no AI at ingest. SmartFill qualifies later.
"""
import re
import logging
from typing import List, Dict, Optional

import requests
from bs4 import BeautifulSoup

logger = logging.getLogger(__name__)

HEADERS = {"User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0 Safari/537.36"}

EF_PORTFOLIO_URL = "https://www.joinef.com/portfolio/"

# Tech Nation cohort/list article pages. Add new cohort URLs here each year.
TECHNATION_PAGES = [
    {
        "label": "Tech Nation Future Fifty 2026",
        "url": "https://technation.io/https-technation-io-future-fifty-2026-cohort-1/",
    },
    {
        "label": "Tech Nation Future Fifty 2025",
        "url": "https://technation.io/future-fifty-2025/",
    },
]


class NetworkScraper:
    """Scrapes founder-network / accelerator alumni directories. Raw save only."""

    SOURCES = ["EF Alumni", "Tech Nation"]

    def get_supported_sources(self) -> List[str]:
        return self.SOURCES

    def scrape_source(self, source_name: str, **kwargs) -> List[Dict]:
        if source_name == "EF Alumni":
            return self.scrape_ef_alumni(**kwargs)
        if source_name == "Tech Nation":
            return self.scrape_technation()
        logger.error(f"Unknown network source: {source_name}")
        return []

    # ── EF Alumni ─────────────────────────────────────────────────────────────

    def scrape_ef_alumni(self, uk_only: bool = True, max_pages: int = 40) -> List[Dict]:
        """
        Paginate the EF portfolio directory and parse company cards.
        Cards contain: name (heading), location + industry tag links,
        description, founder links (LinkedIn), founded year.
        """
        companies: List[Dict] = []
        seen = set()

        for page in range(1, max_pages + 1):
            url = EF_PORTFOLIO_URL if page == 1 else f"{EF_PORTFOLIO_URL}?pagenum={page}"
            try:
                resp = requests.get(url, headers=HEADERS, timeout=20)
                resp.raise_for_status()
            except Exception as e:
                logger.warning(f"[EF] Fetch failed for page {page}: {e}")
                break

            page_companies = self._parse_ef_page(resp.text)
            new = [c for c in page_companies if c["name"].lower() not in seen]
            if not new:
                logger.info(f"[EF] No new companies on page {page} — stopping.")
                break
            for c in new:
                seen.add(c["name"].lower())
                companies.append(c)
            logger.info(f"[EF] Page {page}: {len(new)} companies (total {len(companies)})")

        if uk_only:
            before = len(companies)
            companies = [c for c in companies if "london" in (c.get("region") or "").lower()]
            logger.info(f"[EF] UK filter: {before} → {len(companies)} (London only)")

        return companies

    def _parse_ef_page(self, html: str) -> List[Dict]:
        """Parse one EF portfolio page. Anchors on /industry/ + /location/ tag links
        to find company card containers, so it survives class-name changes."""
        soup = BeautifulSoup(html, "html.parser")
        results: List[Dict] = []
        seen_containers = set()

        for loc_link in soup.select('a[href*="/location/"]'):
            # Climb to a container that also holds a heading (the company card)
            container = loc_link
            heading = None
            for _ in range(6):
                container = container.parent
                if container is None:
                    break
                heading = container.find(["h2", "h3", "h4"])
                if heading is not None and heading.get_text(strip=True):
                    break
            if container is None or heading is None:
                continue
            cid = id(container)
            if cid in seen_containers:
                continue
            seen_containers.add(cid)

            name = heading.get_text(strip=True)
            if not name or len(name) > 80:
                continue

            location = ", ".join(
                a.get_text(strip=True) for a in container.select('a[href*="/location/"]')
            )
            industries = [a.get_text(strip=True) for a in container.select('a[href*="/industry/"]')]

            # Description: first paragraph that isn't a founder-role line
            description = ""
            for p in container.find_all("p"):
                text = p.get_text(strip=True)
                if text and len(text) > 15 and "linkedin" not in text.lower():
                    description = text
                    break

            # Founded year
            founded = None
            m = re.search(r"Founded\D{0,20}(20\d{2}|19\d{2})", container.get_text(" ", strip=True))
            if m:
                founded = int(m.group(1))

            # First founder LinkedIn (usually the CEO — listed first)
            linkedin = ""
            contact_name = ""
            li = container.select_one('a[href*="linkedin.com"]')
            if li:
                linkedin = li.get("href", "")
                contact_name = li.get_text(strip=True)

            region = f"{location}, UK" if location.strip().lower() == "london" else location

            results.append({
                "name": name,
                "website": "",  # EF cards don't expose company websites — SmartFill finds them
                "sector": industries[0] if industries else "Technology",
                "keywords": ", ".join(industries),
                "description": description or f"Entrepreneur First portfolio company ({location}).",
                "region": region,
                "year_founded": founded,
                "contact_name": contact_name,
                "linkedin_url": linkedin,
                "source": "EF Alumni",
                "status": "Scraped",
            })

        return results

    # ── Tech Nation ───────────────────────────────────────────────────────────

    def scrape_technation(self) -> List[Dict]:
        """
        Parse Tech Nation cohort articles. Company entries follow the pattern:
        <a href="https://company.com">Name</a> – one-line description.
        """
        companies: List[Dict] = []
        seen = set()

        for page in TECHNATION_PAGES:
            try:
                resp = requests.get(page["url"], headers=HEADERS, timeout=20)
                resp.raise_for_status()
            except Exception as e:
                logger.warning(f"[TechNation] Fetch failed for {page['label']}: {e}")
                continue

            found = self._parse_technation_article(resp.text, page["label"])
            new = [c for c in found if c["name"].lower() not in seen]
            for c in new:
                seen.add(c["name"].lower())
                companies.append(c)
            logger.info(f"[TechNation] {page['label']}: {len(new)} companies")

        return companies

    def _parse_technation_article(self, html: str, label: str) -> List[Dict]:
        soup = BeautifulSoup(html, "html.parser")
        results: List[Dict] = []

        skip_domains = (
            "technation.io", "facebook.com", "twitter.com", "linkedin.com",
            "instagram.com", "youtube.com", "gov.uk", "hsbc", "mailto:",
            "google.com", "founders-forum", "ff.co",
        )

        for a in soup.find_all("a", href=True):
            href = a["href"].strip()
            name = a.get_text(strip=True)
            if not href.startswith("http") or not name or len(name) > 60:
                continue
            if any(d in href.lower() for d in skip_domains):
                continue

            # The description follows the link in the same block: "Name – description."
            parent_text = a.parent.get_text(" ", strip=True) if a.parent else ""
            description = ""
            if parent_text.startswith(name):
                tail = parent_text[len(name):].strip()
                tail = tail.lstrip("–—-— ").strip()
                # Only accept if it reads like a description (not nav/footer text)
                if 15 <= len(tail) <= 300:
                    description = tail
            if not description:
                continue  # anchors without the "– description" pattern are nav links

            # Clean tracking params off the website URL
            website = href.split("?")[0].rstrip("/")

            results.append({
                "name": name,
                "website": website,
                "sector": "Technology",
                "description": description,
                "region": "UK",
                "source": label,
                "status": "Scraped",
            })

        return results


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    s = NetworkScraper()
    tn = s.scrape_technation()
    print(f"Tech Nation: {len(tn)}")
    for c in tn[:5]:
        print(" ", c["name"], "|", c["website"], "|", c["description"][:60])
    ef = s.scrape_ef_alumni(max_pages=3)
    print(f"EF (3 pages, London): {len(ef)}")
    for c in ef[:5]:
        print(" ", c["name"], "|", c["sector"], "|", c.get("year_founded"))
