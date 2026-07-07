import re
import requests
from bs4 import BeautifulSoup
from typing import List, Dict
import logging

# Configure logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# SaaStock publishes machine-readable llms.txt indexes per edition —
# sponsors + featured speakers with companies. Far more reliable than HTML scraping.
SAASTOCK_EDITIONS = [
    "https://saastock.com/europe/2025/llms.txt",
    "https://saastock.com/europe/2024/llms.txt",
    "https://saastock.com/europe/2023/llms.txt",
    "https://saastock.com/europe/2022/llms.txt",
]

# Generic legal suffixes / non-company entries to skip in sponsor lists
_SPONSOR_SKIP = {"failte ireland", "enterprise ireland", "dublin city council"}


class ConferenceScraper:
    """
    Base Scraper for Conference Exhibitor/Sponsor Lists.
    """
    def __init__(self):
        self.targets = [
            {
                "name": "SaaStock Europe",
                "url": "https://saastock.com/europe/",
                "selector": None  # handled by _scrape_saastock_llms (llms.txt archive)
            },
            {
                "name": "London Tech Week",
                "url": "https://londontechweek.com/exhibitors",
                "selector": None  # handled by _scrape_ltw (exhibitors + speakers, paginated)
            },
            {
                "name": "SaaSiest",
                "url": "https://saasiest.se/partners/",
                "selector": "img.partner-logo"
            }
        ]

    def scrape_conference(self, conf_name: str) -> List[Dict]:
        """
        Scrapes a specific conference by name from the target list.
        """
        target = next((t for t in self.targets if t["name"] == conf_name), None)
        if not target:
            logger.error(f"Conference {conf_name} not found in targets.")
            return []

        logger.info(f"Scraping {conf_name} from {target['url']}...")

        # SaaStock: use the official llms.txt archive (multi-edition, machine-readable)
        if "SaaStock" in conf_name:
            companies = self._scrape_saastock_llms()
            if companies:
                return companies
            logger.warning("SaaStock llms.txt scrape returned nothing — falling through.")

        # London Tech Week: paginated exhibitor list + speaker list (server-rendered)
        if "London Tech Week" in conf_name:
            companies = self._scrape_ltw()
            if companies:
                return companies
            logger.warning("LTW scrape returned nothing — falling through.")

        try:
            headers = {'User-Agent': 'Mozilla/5.0'}
            response = requests.get(target['url'], headers=headers, timeout=5)
            response.raise_for_status()
            
            soup = BeautifulSoup(response.text, 'html.parser')
            companies = []
            
            elements = soup.select(target['selector'])
            for el in elements:
                company_name = el.get('alt') or el.get('title') or el.text.strip()
                if company_name:
                    companies.append({
                        "name": company_name,
                        "website": el.get('href') or "",
                        "sector": "B2B SaaS",
                        "description": f"Sourced from {conf_name} exhibitor list.",
                        "source": conf_name,
                        "status": "Scraped"
                    })
            
            if not companies:
                logger.warning(f"No companies scraped from {conf_name} — returning empty (no demo fallback).")

            # Standardize for the main API
            for c in companies:
                c["source"] = conf_name
                if "website" not in c: c["website"] = ""
                if "sector" not in c: c["sector"] = "Technology"
                if "description" not in c: c["description"] = f"Sourced from {conf_name}"
                
            return companies
            
        except Exception as e:
            logger.error(f"Failed to scrape {conf_name}: {str(e)}")
            return []

    def _scrape_ltw(self, max_exhibitor_pages: int = 15, max_speaker_pages: int = 12) -> List[Dict]:
        """
        London Tech Week 2026 (londontechweek.com, ASP.events platform — server-rendered).
        Scrapes:
          1. /exhibitors?page=N  — ~250 exhibitors with name + stand + description
          2. /speaker-list?page=N — speakers as "Name / Role, Company"
        """
        headers = {'User-Agent': 'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 Chrome/126.0 Safari/537.36'}
        companies: Dict[str, Dict] = {}

        # ── 1. Exhibitors (paginated) ──
        for page in range(1, max_exhibitor_pages + 1):
            url = f"https://londontechweek.com/exhibitors?page={page}"
            try:
                resp = requests.get(url, headers=headers, timeout=20)
                resp.raise_for_status()
            except Exception as e:
                logger.warning(f"[LTW] Exhibitor page {page} fetch failed: {e}")
                break

            soup = BeautifulSoup(resp.text, 'html.parser')
            new_count = 0
            # Exhibitor cards: heading links referencing 'exhibitor-list/'
            for a in soup.find_all("a", href=True):
                if "exhibitor-list/" not in a["href"]:
                    continue
                name = a.get_text(strip=True)
                if not name or len(name) > 70:
                    continue
                key = name.lower()
                if key in companies:
                    continue

                # Description: nearest following paragraph text in the card
                description = ""
                container = a.parent
                for _ in range(4):
                    if container is None:
                        break
                    p = container.find("p")
                    if p and len(p.get_text(strip=True)) > 20:
                        description = p.get_text(strip=True)
                        break
                    container = container.parent
                if not description:
                    # fall back to text after the heading
                    txt = a.find_parent().get_text(" ", strip=True) if a.find_parent() else ""
                    description = txt[:300]

                companies[key] = {
                    "name": name,
                    "website": "",
                    "sector": "Technology",
                    "description": (description[:400] + "…") if len(description) > 400 else description or "Exhibitor at London Tech Week 2026.",
                    "source": "London Tech Week 2026",
                    "status": "Scraped",
                }
                new_count += 1

            logger.info(f"[LTW] Exhibitor page {page}: +{new_count} (total {len(companies)})")
            if new_count == 0:
                break

        # ── 2. Speakers (paginated) — "Role, Company" under each name ──
        for page in range(1, max_speaker_pages + 1):
            url = f"https://londontechweek.com/speaker-list?page={page}"
            try:
                resp = requests.get(url, headers=headers, timeout=20)
                resp.raise_for_status()
            except Exception as e:
                logger.warning(f"[LTW] Speaker page {page} fetch failed: {e}")
                break

            soup = BeautifulSoup(resp.text, 'html.parser')
            new_count = 0
            for a in soup.find_all("a", href=True):
                if "/speakers/" not in a["href"]:
                    continue
                person = a.get_text(strip=True)
                if not person or len(person) > 60:
                    continue
                # Role/company line follows the heading in the card
                block = a.find_parent(["li", "div", "article"])
                if not block:
                    continue
                block_text = block.get_text("\n", strip=True)
                role_company = ""
                for line in block_text.split("\n"):
                    line = line.strip()
                    if line and line != person and ", " in line and len(line) < 120:
                        role_company = line
                        break
                if not role_company:
                    continue
                role, _, comp = role_company.rpartition(", ")
                comp = comp.strip()
                if not comp or len(comp) > 60 or comp.lower() in companies:
                    # still capture founder contact on an existing record
                    if comp and comp.lower() in companies and any(k in role.lower() for k in ["founder", "ceo"]) and not companies[comp.lower()].get("contact_name"):
                        companies[comp.lower()]["contact_name"] = person
                    continue

                companies[comp.lower()] = {
                    "name": comp,
                    "website": "",
                    "sector": "Technology",
                    "description": f"{person} ({role}) spoke at London Tech Week 2026.",
                    "contact_name": person if any(k in role.lower() for k in ["founder", "ceo"]) else "",
                    "source": "London Tech Week 2026",
                    "status": "Scraped",
                }
                new_count += 1

            logger.info(f"[LTW] Speaker page {page}: +{new_count} (total {len(companies)})")
            if new_count == 0:
                break

        return list(companies.values())

    def _scrape_saastock_llms(self) -> List[Dict]:
        """
        Parse SaaStock's per-edition llms.txt files (official machine-readable indexes).
        Extracts sponsor companies and featured speakers' companies across editions.
        SaaStock Europe 2024 alone lists 392 sponsors — the richest conference source.
        """
        companies: Dict[str, Dict] = {}

        for url in SAASTOCK_EDITIONS:
            edition = "SaaStock Europe " + (re.search(r"/(20\d{2})/", url).group(1) if re.search(r"/(20\d{2})/", url) else "")
            try:
                resp = requests.get(url, headers={'User-Agent': 'Mozilla/5.0'}, timeout=20)
                resp.raise_for_status()
            except Exception as e:
                logger.warning(f"[SaaStock] Fetch failed for {url}: {e}")
                continue

            text = resp.text
            found = 0

            # ── Sponsors section: "- Company Name" bullets under "## Sponsors" ──
            sponsors_match = re.search(r"## Sponsors\n(.*?)(?:\n## |\Z)", text, re.DOTALL)
            if sponsors_match:
                for line in sponsors_match.group(1).splitlines():
                    line = line.strip()
                    if not line.startswith("- "):
                        continue
                    name = line[2:].strip()
                    # Trim legal suffixes for cleaner CH matching later
                    name = re.sub(r"\s+(Ltd|LTD|Limited|LLC|Inc\.?|N\.V\.?|Pte|Pty|SL|UAB|GmbH|B\.V\.?)\.?$", "", name).strip()
                    if not name or len(name) > 60 or name.lower() in _SPONSOR_SKIP:
                        continue
                    key = name.lower()
                    if key not in companies:
                        companies[key] = {
                            "name": name,
                            "website": "",
                            "sector": "B2B SaaS",
                            "description": f"Sponsor/partner at {edition}.",
                            "source": edition,
                            "status": "Scraped",
                        }
                        found += 1

            # ── Featured speakers: "- Name, Role at Company — "talk"" bullets ──
            speakers_match = re.search(r"## Featured speakers\n(.*?)(?:\n## |\Z)", text, re.DOTALL)
            if speakers_match:
                for line in speakers_match.group(1).splitlines():
                    line = line.strip()
                    if not line.startswith("- "):
                        continue
                    m = re.match(r"- (.+?), (.+?) at (.+?) —", line)
                    if not m:
                        continue
                    person, role, comp = m.group(1).strip(), m.group(2).strip(), m.group(3).strip()
                    if not comp or len(comp) > 60:
                        continue
                    key = comp.lower()
                    if key not in companies:
                        companies[key] = {
                            "name": comp,
                            "website": "",
                            "sector": "B2B SaaS",
                            "description": f"{person} ({role}) spoke at {edition}.",
                            "contact_name": person if any(k in role.lower() for k in ["founder", "ceo"]) else "",
                            "source": edition,
                            "status": "Scraped",
                        }
                        found += 1
                    elif any(k in role.lower() for k in ["founder", "ceo"]) and not companies[key].get("contact_name"):
                        companies[key]["contact_name"] = person

            logger.info(f"[SaaStock] {edition}: +{found} new companies (running total {len(companies)})")

        return list(companies.values())

    def get_all_targets(self) -> List[str]:
        return [t["name"] for t in self.targets]

if __name__ == "__main__":
    scraper = ConferenceScraper()
    # Simple test for one
    results = scraper.scrape_conference("SaaSiest")
    print(f"Scraped {len(results)} companies from SaaSiest.")
    for r in results[:5]:
        print(r)
