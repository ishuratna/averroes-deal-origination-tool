#!/usr/bin/env python3
"""
Enterprise Ireland directory scraper: shapes captured live on 16 Sep 2026
from /api/v1/homepage/vendors/ and /api/v1/vendors/profile/{slug}/, so the
mapping is tested without the network.
"""
import os
import sys
import warnings
from unittest import mock

warnings.filterwarnings("ignore")
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
os.environ.setdefault("GCP_PROJECT_ID", "averroes-deal-origination")

from scrapers import enterprise_ireland_scraper as ei  # noqa: E402
from scrapers.directory_scraper import DirectoryScraper  # noqa: E402

fails = 0


def chk(label, got, want=True):
    global fails
    ok = got == want
    print(("PASS" if ok else "FAIL"), label, "" if ok else f"-> {got!r} (wanted {want!r})")
    if not ok:
        fails += 1


LIST_ITEM = {"pk": 4072, "slug": "binarii-labs-2", "name": "Binarii Labs",
             "summary": "Zero-trust data security for unstructured enterprise data.",
             "office_country": "Ireland", "employee_min": 10, "employee_max": 14, "founded": 2021,
             "primary_service": {"name": "Cybersecurity", "pk": 1, "slug": "cybersecurity"},
             "services": [{"name": "Cybersecurity"}, {"name": "Financial Services"}], "contact_persons": [{}]}
PROFILE = {"pk": 4072, "slug": "binarii-labs-2", "name": "Binarii Labs", "founded": 2021,
           "description": "<p>Binarii Labs is an Irish cybersecurity company.</p><p>Our patented platform encrypts &amp; fragments data.</p>",
           "website": "http://binariilabs.com", "linkedin": "company/binariilabs", "employee_min": 10, "employee_max": 14}
LOCATIONS = [{"pk": 1470, "vendor": 4072, "is_main": True,
              "location": {"address": "Nova UCD, Dublin", "city": "", "country": "Ireland", "state": "Dublin"}}]

print("── List row alone (no profile yet) ──")
r = ei.to_target(LIST_ITEM)
chk("name", r["name"], "Binarii Labs")
chk("source stamped", r["source"], "Enterprise Ireland Directory")
chk("sector from primary service", r["sector"], "Cybersecurity")
chk("verticals from services", r["verticals"], "Cybersecurity, Financial Services")
chk("region and country Ireland", (r["region"], r["hq_country"]), ("Ireland", "Ireland"))
chk("employees is the midpoint of the band", r["employees"], 12)
chk("founded", r["year_founded"], 2021)
chk("website empty until the profile pass", r["website"], "")
chk("description falls back to the summary", r["description"].startswith("Zero-trust"))
chk("no service tags at all -> sector left EMPTY for SmartFill, never a filler label",
    ei.to_target({"name": "Bare Ltd", "slug": "bare", "pk": 1})["sector"], "")

print()
print("── With the profile ──")


def fake_get(url, session=None):
    if "/vendors/profile/binarii-labs-2/" in url:
        return PROFILE
    if "/locations/" in url:
        return LOCATIONS
    return None


with mock.patch.object(ei, "_get", side_effect=fake_get):
    prof = ei.fetch_profile("binarii-labs-2", 4072)
r = ei.to_target(LIST_ITEM, prof)
chk("website from the profile", r["website"], "http://binariilabs.com")
chk("LinkedIn path becomes a URL", r["linkedin_url"], "https://www.linkedin.com/company/binariilabs")
chk("HTML description stripped and entities decoded", "encrypts & fragments" in r["description"] and "<p>" not in r["description"])
chk("summary kept in front of the long description", r["description"].startswith("Zero-trust"))
chk("city from the main location's state when city is blank", r["hq_city"], "Dublin")
chk("address kept", r["hq_location"], "Nova UCD, Dublin, Ireland")
chk("resume keys removed only at save time", "_slug" in r)

print()
print("── Time box and skip list ──")
vendors = [dict(LIST_ITEM, slug=f"v{i}", name=f"Vendor {i}", pk=i) for i in range(40)]
calls = {"n": 0}


def fake_profile(slug, pk=None, session=None):
    calls["n"] += 1
    return {"website": f"https://{slug}.ie", "linkedin_url": "", "description": "", "year_founded": None,
            "employee_min": None, "employee_max": None, "hq_city": "", "hq_location": ""}


with mock.patch.object(ei, "list_vendors", return_value=vendors), \
     mock.patch.object(ei, "fetch_profile", side_effect=fake_profile):
    res = ei.scrape(time_budget_s=20, skip_names=["Vendor 0", "Vendor 1"])
chk("every vendor listed regardless of the profile pass", res["listed"], 40)
chk("skipped names are not profiled", calls["n"], 38)
chk("profiled count reported", res["profiled"], 38)
chk("nothing pending", res["profile_pending"], 0)
chk("resume keys stripped from the saved rows", all("_slug" not in c for c in res["companies"]))
chk("skipped rows still present with no website", next(c for c in res["companies"] if c["name"] == "Vendor 0")["website"], "")

calls["n"] = 0
with mock.patch.object(ei, "list_vendors", return_value=vendors), \
     mock.patch.object(ei, "fetch_profile", side_effect=fake_profile), \
     mock.patch.object(ei.time, "time", side_effect=[0] + [10 ** 9] * 60):
    res = ei.scrape(time_budget_s=20)
chk("an exhausted budget stops the profile pass before the first batch", calls["n"], 0)
chk("...and reports everything as pending", res["profile_pending"], 40)

print()
print("── An empty or partial list is a FAILURE, never a finished directory ──")
with mock.patch.object(ei, "_get", return_value=None):
    try:
        ei.list_vendors(); chk("a page that fails twice raises ListError", False)
    except ei.ListError:
        chk("a page that fails twice raises ListError", True)
pages = iter([{"count": 4, "results": [dict(LIST_ITEM, slug="a")]}, {"count": 4, "results": []}])
with mock.patch.object(ei, "_get", side_effect=lambda *a, **k: next(pages)):
    try:
        ei.list_vendors(); chk("fewer vendors than the directory's count raises ListError", False)
    except ei.ListError:
        chk("fewer vendors than the directory's count raises ListError", True)
chk("page size is small enough for the server to answer in time", ei._PAGE <= 50)
chk("list timeout is generous", ei._LIST_TIMEOUT >= 60)

print()
print("── Registered as a directory source ──")
ds = DirectoryScraper()
chk("EnterpriseIreland is a supported source", "EnterpriseIreland" in ds.get_supported_sources())
chk("TheSaaSDirectory untouched", "TheSaaSDirectory" in ds.get_supported_sources())

print()
print(f"{fails} FAILURES" if fails else "ALL PASS")
sys.exit(1 if fails else 0)
