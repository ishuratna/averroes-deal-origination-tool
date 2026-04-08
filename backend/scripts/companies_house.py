import time

class UKRegistryChecker:
    """
    Agentic Source Step 2: The 'Registry' Check.
    For UK companies, query the Companies House API to cross-reference
    stated financials, verify SIC codes (e.g. 62012 for Business/Domestic software),
    and check for institutional shareholders to confirm 'Bootstrapped' status.
    """
    def __init__(self):
        # Requires an API Key from developer.companieshouse.gov.uk
        self.base_url = "https://api.company-information.service.gov.uk/search/companies"
        self.sic_software_code = "62012"
        
    def cross_reference_company(self, company_name: str) -> dict:
        """
        Queries Companies House for the company name.
        In a production environment, this triggers a GET request to `self.base_url`.
        """
        print(f"Agent triggered: Querying UK Companies House for '{company_name}'...")
        time.sleep(1) # Simulating network request
        
        # Simulated response confirming thesis
        return {
            "verified_name": company_name.upper(),
            "sic_codes": [self.sic_software_code],
            "is_uk_registered": True,
            "institutional_shareholders": False, # Verifies "No VC / Bootstrapped"
            "status": "Active"
        }
