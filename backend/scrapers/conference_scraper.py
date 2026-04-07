import requests
from bs4 import BeautifulSoup
from typing import List, Dict
import logging

# Configure logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

class ConferenceScraper:
    """
    Base Scraper for Conference Exhibitor/Sponsor Lists.
    """
    def __init__(self):
        self.targets = [
            {
                "name": "SaaStock Europe",
                "url": "https://www.saastock.com/events/saastock-europe/",
                "selector": ".sponsor-logo, .startup-logo" # Generalized selectors to start
            },
            {
                "name": "London Tech Week",
                "url": "https://londontechweek.com/partners",
                "selector": "a.partner-logo"
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
        
        try:
            headers = {'User-Agent': 'Mozilla/5.0'}
            response = requests.get(target['url'], headers=headers, timeout=10)
            response.raise_for_status()
            
            soup = BeautifulSoup(response.text, 'html.parser')
            companies = []
            
            # This is a generic logic that will need refinement per conference
            # Often lists are inside specific containers.
            elements = soup.select(target['selector'])
            
            for el in elements:
                company_name = el.get('alt') or el.get('title') or el.text.strip()
                link = el.get('href') or (el.parent.get('href') if el.parent.name == 'a' else None)
                
                if company_name:
                    companies.append({
                        "name": company_name,
                        "url": link,
                        "source": conf_name,
                        "status": "Scraped"
                    })
            
            return companies
            
        except Exception as e:
            logger.error(f"Failed to scrape {conf_name}: {str(e)}")
            return []

    def get_all_targets(self) -> List[str]:
        return [t["name"] for t in self.targets]

if __name__ == "__main__":
    scraper = ConferenceScraper()
    # Simple test for one
    results = scraper.scrape_conference("SaaSiest")
    print(f"Scraped {len(results)} companies from SaaSiest.")
    for r in results[:5]:
        print(r)
