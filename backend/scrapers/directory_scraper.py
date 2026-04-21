import requests
from bs4 import BeautifulSoup
from typing import List, Dict
import logging
import time

logger = logging.getLogger(__name__)

class DirectoryScraper:
    """
    Scrapes B2B SaaS company directories for deal sourcing.
    Sources: TheSaaSDirectory.com
    """
    def __init__(self):
        self.sources = {
            "TheSaaSDirectory": {
                "base_url": "https://thesaasdirectory.com/listings/",
                "page_url": "https://thesaasdirectory.com/listings/page/{page}/?ls",
                "first_page": "https://thesaasdirectory.com/listings/?ls=",
            }
        }
        self.headers = {
            "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
        }

    def scrape_source(self, source_name: str, max_pages: int = 20) -> List[Dict]:
        """Scrapes a directory source. Default limit: 20 pages (~200 companies)."""
        if source_name not in self.sources:
            logger.error(f"Unknown directory source: {source_name}")
            return []
        
        if source_name == "TheSaaSDirectory":
            return self._scrape_saas_directory(max_pages)
        return []

    def _scrape_saas_directory(self, max_pages: int = 20) -> List[Dict]:
        """Scrapes TheSaaSDirectory.com listings with pagination."""
        logger.info(f"Scraping TheSaaSDirectory.com (up to {max_pages} pages)...")
        all_companies = []

        for page in range(1, max_pages + 1):
            url = self.sources["TheSaaSDirectory"]["first_page"] if page == 1 else self.sources["TheSaaSDirectory"]["page_url"].format(page=page)
            
            try:
                resp = requests.get(url, headers=self.headers, timeout=15)
                if resp.status_code != 200:
                    logger.warning(f"Page {page} returned {resp.status_code}, stopping.")
                    break
                
                soup = BeautifulSoup(resp.text, "html.parser")
                articles = soup.select("article.listing-item")
                
                if not articles:
                    logger.info(f"No more listings found on page {page}, stopping.")
                    break
                
                for article in articles:
                    company = self._parse_listing(article)
                    if company and company.get("name"):
                        all_companies.append(company)
                
                logger.info(f"Page {page}: found {len(articles)} listings (total: {len(all_companies)})")
                time.sleep(1)  # Be polite
                
            except Exception as e:
                logger.error(f"Error scraping page {page}: {e}")
                break

        logger.info(f"TheSaaSDirectory scrape complete: {len(all_companies)} companies found.")
        return all_companies

    def _parse_listing(self, article) -> Dict:
        """Parses a single article element from TheSaaSDirectory."""
        try:
            name = article.get("data-title", "").strip()
            address = article.get("data-address", "").strip()
            permalink = article.get("data-permalink", "")
            
            # Extract category tags
            categories = []
            cat_links = article.select('a[href*="/listings/category/"]')
            for link in cat_links:
                cat_text = link.get_text(strip=True)
                if cat_text:
                    categories.append(cat_text)
            
            # Map address to region
            region = self._map_region(address)
            
            # Build description from categories
            sector = categories[0] if categories else "SaaS"
            description = f"{', '.join(categories)}" if categories else ""
            if address:
                description = f"Location: {address}. {description}" if description else f"Location: {address}"
            
            return {
                "name": name,
                "website": permalink,  # Will be overwritten by SmartFill with actual company URL
                "sector": sector,
                "region": region,
                "description": description,
                "ownership": "",
                "growth_signals": False,
                "source": "TheSaaSDirectory"
            }
        except Exception as e:
            logger.warning(f"Failed to parse listing: {e}")
            return {}

    def _scrape_detail_page(self, url: str) -> Dict:
        """Optionally scrape individual company page for website URL and description."""
        try:
            resp = requests.get(url, headers=self.headers, timeout=10)
            if resp.status_code != 200:
                return {}
            soup = BeautifulSoup(resp.text, "html.parser")
            
            # Find "Visit Website" link
            website = ""
            for a in soup.find_all("a", href=True):
                if "visit website" in a.get_text(strip=True).lower():
                    website = a["href"]
                    break
            
            # Find email
            email = ""
            mailto = soup.select_one('a[href^="mailto:"]')
            if mailto:
                email = mailto["href"].replace("mailto:", "")
            
            return {"website": website, "contact_email": email}
        except Exception:
            return {}

    def _map_region(self, address: str) -> str:
        """Maps an address string to a region category."""
        addr_lower = address.lower()
        uk_terms = ["uk", "united kingdom", "london", "manchester", "birmingham", "edinburgh", "glasgow", "bristol", "leeds", "cambridge", "oxford", "england", "scotland", "wales"]
        ireland_terms = ["ireland", "dublin", "cork", "galway", "limerick"]
        europe_terms = ["germany", "france", "netherlands", "sweden", "denmark", "norway", "finland", "spain", "italy", "belgium", "austria", "switzerland", "portugal", "poland", "czech", "berlin", "paris", "amsterdam", "stockholm", "copenhagen", "munich", "vienna", "zurich"]
        
        for term in uk_terms:
            if term in addr_lower:
                return "UK"
        for term in ireland_terms:
            if term in addr_lower:
                return "Ireland"
        for term in europe_terms:
            if term in addr_lower:
                return "Europe"
        if "us" in addr_lower or "usa" in addr_lower or "united states" in addr_lower or any(state in addr_lower for state in ["california", "new york", "texas", "florida", "san francisco", "boston"]):
            return "North America"
        return address.split(",")[-1].strip() if address else "Unknown"

    def get_supported_sources(self) -> List[str]:
        return list(self.sources.keys())
