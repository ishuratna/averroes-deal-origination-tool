"""
Investor (LP) web scrapers.

Sources:
  1. "Praxis Rock Directories" — public family-office / SWF directory pages
     (praxisrock.com). Server-rendered cards: name, type, description, website.
  2. "Companies House Registry" — official UK register search for entities
     with 'family office' in their name. Free API (existing CH key), returns
     registration numbers for later verification. Active companies only.

RAW extraction only — no AI. InvestorFill researches each investor on demand.
"""
import os
import re
import logging
from typing import List, Dict

import requests
from bs4 import BeautifulSoup

logger = logging.getLogger(__name__)

HEADERS = {"User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0 Safari/537.36"}

PRAXIS_PAGES = [
    {"url": "https://praxisrock.com/resources/investors/family-offices-london", "region": "UK", "country": "United Kingdom"},
    {"url": "https://praxisrock.com/resources/investors/largest-family-offices", "region": "Global", "country": ""},
    {"url": "https://praxisrock.com/resources/investors/multi-family-offices", "region": "Global", "country": ""},
    {"url": "https://praxisrock.com/resources/investors/sovereign-wealth-funds", "region": "Global", "country": ""},
    {"url": "https://praxisrock.com/resources/investors/family-offices-dubai", "region": "KSA/GCC", "country": "United Arab Emirates"},
]

CH_API_BASE = "https://api.company-information.service.gov.uk"


class InvestorScraper:
    SOURCES = ["Praxis Rock Directories", "Companies House Registry"]

    def get_supported_sources(self) -> List[str]:
        return self.SOURCES

    def scrape_source(self, source_name: str) -> List[Dict]:
        if source_name == "Praxis Rock Directories":
            return self.scrape_praxis()
        if source_name == "Companies House Registry":
            return self.scrape_ch_registry()
        logger.error(f"Unknown investor scraper source: {source_name}")
        return []

    # ── Praxis Rock ───────────────────────────────────────────────────────────

    def scrape_praxis(self) -> List[Dict]:
        investors: Dict[str, Dict] = {}
        for page in PRAXIS_PAGES:
            try:
                resp = requests.get(page["url"], headers=HEADERS, timeout=25)
                if resp.status_code != 200:
                    logger.warning(f"[Praxis] {page['url']} returned {resp.status_code} — skipping")
                    continue
            except Exception as e:
                logger.warning(f"[Praxis] Fetch failed for {page['url']}: {e}")
                continue

            found = self._parse_praxis_page(resp.text, page)
            new = 0
            for inv in found:
                key = inv["name"].lower()
                if key not in investors:
                    investors[key] = inv
                    new += 1
            logger.info(f"[Praxis] {page['url']}: +{new} (total {len(investors)})")

        return list(investors.values())

    def _parse_praxis_page(self, html: str, page: Dict) -> List[Dict]:
        """
        Cards: an h3 with the firm name; card container includes a type line
        ('Single-Family Office' / 'Multi-Family Office' / 'Sovereign Wealth Fund'),
        a description paragraph and an external 'Visit' link.
        Structure-based parsing (no class names) to survive redesigns.
        """
        soup = BeautifulSoup(html, "html.parser")
        results: List[Dict] = []

        for h in soup.find_all(["h3", "h2"]):
            name = h.get_text(strip=True)
            if not name or len(name) > 70 or name.lower() in ("focus", "sectors", "aum", "invests via"):
                continue

            # Find the card container: nearest ancestor that includes an external link or a type label
            container = h
            for _ in range(4):
                if container.parent is None:
                    break
                container = container.parent
                text = container.get_text(" ", strip=True)
                if "Family Office" in text or "Sovereign Wealth" in text or container.find("a", href=re.compile(r"^https?://")):
                    break
            # A real firm card contains exactly ONE heading — if the container
            # holds several, we climbed into a page-level wrapper. Skip.
            if len(container.find_all(["h2", "h3"])) != 1:
                continue

            text = container.get_text(" ", strip=True)

            # Type
            if "Multi-Family Office" in text:
                inv_type = "Family Office"
            elif "Single-Family Office" in text or "Family Office" in text:
                inv_type = "Family Office"
            elif "Sovereign Wealth" in text:
                inv_type = "Sovereign/Institutional"
            elif "Pension" in text or "Endowment" in text or "Foundation" in text:
                inv_type = "Sovereign/Institutional"
            else:
                inv_type = "Unknown"

            # Website: external link that isn't praxisrock/social
            website = ""
            for a in container.find_all("a", href=True):
                href = a["href"]
                if href.startswith("http") and not any(d in href for d in ["praxisrock.com", "linkedin.com", "twitter.com", "x.com"]):
                    website = href.split("?")[0].rstrip("/")
                    break

            # Description: longest paragraph in the card
            description = ""
            for p in container.find_all("p"):
                t = p.get_text(strip=True)
                if len(t) > len(description):
                    description = t
            if len(description) < 30:
                continue  # not a firm card (nav/heading artifact)

            results.append({
                "name": name,
                "investor_type": inv_type,
                "region": page["region"],
                "hq_country": page["country"],
                "website": website,
                "description": description[:600],
                "source": "Praxis Rock Directory",
                "status": "Identified",
            })

        return results

    # ── Companies House registry search ───────────────────────────────────────

    # Name patterns that indicate an investor entity, mapped to our types.
    # Single-family offices are often named after the family, so name search
    # alone misses them; the SIC search below catches unnamed ones.
    CH_NAME_QUERIES = [
        ("family office", "Family Office"),
        ("family investments", "Family Office"),
        ("family capital", "Family Office"),
        ("private investment office", "Family Office"),
        ("investment office", "Family Office"),
        ("family holdings", "Family Office"),
    ]

    # SIC codes for investor entities (catches family offices with bland names):
    #   64303 — venture and development capital companies (highest signal)
    #   66300 — fund management activities
    CH_SIC_QUERIES = [
        ("64303", "PE", "venture and development capital company", 400),
        ("66300", "Fund of Funds", "fund management activities", 300),
    ]

    def scrape_ch_registry(self, max_per_query: int = 300) -> List[Dict]:
        """
        Search the official UK register for investor entities two ways:
          1. Name patterns ('family office', 'family investments', ...)
          2. SIC codes via the advanced-search API (finds family offices and
             investment vehicles whose names don't say what they are)
        Active companies only. Uses the existing COMPANIES_HOUSE_API_KEY.
        """
        key = os.getenv("COMPANIES_HOUSE_API_KEY", "")
        if not key:
            logger.error("[CH Registry] COMPANIES_HOUSE_API_KEY not configured")
            return []

        investors: Dict[str, Dict] = {}

        # ── 1. Name-pattern searches ──
        for query, inv_type in self.CH_NAME_QUERIES:
            start = 0
            while start < max_per_query:
                try:
                    resp = requests.get(
                        f"{CH_API_BASE}/search/companies",
                        params={"q": query, "items_per_page": 100, "start_index": start},
                        auth=(key, ""),
                        timeout=20,
                    )
                    resp.raise_for_status()
                    items = resp.json().get("items", [])
                except Exception as e:
                    logger.warning(f"[CH Registry] Search '{query}' failed at {start}: {e}")
                    break
                if not items:
                    break

                for item in items:
                    title = (item.get("title") or "").strip()
                    status = (item.get("company_status") or "").lower()
                    if not title or status != "active":
                        continue
                    # Require the phrase in the actual name (search matches loosely)
                    if query not in title.lower():
                        continue
                    key_name = title.lower()
                    if key_name in investors:
                        continue
                    addr = item.get("address", {}) or {}
                    investors[key_name] = {
                        "name": title.title(),
                        "investor_type": inv_type,
                        "region": "UK",
                        "hq_city": addr.get("locality") or "",
                        "hq_country": "United Kingdom",
                        "registration_number": item.get("company_number") or "",
                        "description": f"Active UK-registered entity with '{query}' in its name (Companies House, inc. {item.get('date_of_creation', 'n/a')}).",
                        "source": "Companies House Registry",
                        "status": "Identified",
                    }

                if len(items) < 100:
                    break
                start += 100
            logger.info(f"[CH Registry] name query '{query}': total pool now {len(investors)}")

        # ── 2. SIC-code advanced search (unnamed investment vehicles) ──
        for sic, inv_type, sic_label, cap in self.CH_SIC_QUERIES:
            start = 0
            found = 0
            while found < cap:
                try:
                    resp = requests.get(
                        f"{CH_API_BASE}/advanced-search/companies",
                        params={"sic_codes": sic, "company_status": "active",
                                "size": 100, "start_index": start},
                        auth=(key, ""),
                        timeout=20,
                    )
                    resp.raise_for_status()
                    items = resp.json().get("items", [])
                except Exception as e:
                    logger.warning(f"[CH Registry] SIC {sic} search failed at {start}: {e}")
                    break
                if not items:
                    break

                for item in items:
                    title = (item.get("company_name") or item.get("title") or "").strip()
                    if not title:
                        continue
                    key_name = title.lower()
                    if key_name in investors:
                        continue
                    addr = item.get("registered_office_address", {}) or item.get("address", {}) or {}
                    investors[key_name] = {
                        "name": title.title(),
                        "investor_type": inv_type,
                        "region": "UK",
                        "hq_city": addr.get("locality") or "",
                        "hq_country": "United Kingdom",
                        "registration_number": item.get("company_number") or "",
                        "description": f"Active UK company filed under SIC {sic} ({sic_label}) per Companies House, inc. {item.get('date_of_creation', 'n/a')}.",
                        "source": "Companies House Registry",
                        "status": "Identified",
                    }
                    found += 1
                    if found >= cap:
                        break

                if len(items) < 100:
                    break
                start += 100
            logger.info(f"[CH Registry] SIC {sic}: +{found} (total pool {len(investors)})")

        return list(investors.values())
