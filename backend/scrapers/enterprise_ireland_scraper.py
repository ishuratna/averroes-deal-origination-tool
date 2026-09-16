"""
Enterprise Ireland client directory (directory.enterprise-ireland.com).

An Angular front end over a public JSON API (the "Proven" platform), no
session or key needed. 4,176 Irish companies at the time of writing, every
one an Enterprise Ireland client, so the population is exactly the Irish half
of our mandate: exporting, mostly founder-owned, mostly small. Probed 16 Sep
2026 (Ishu: "scrape all companies possible from here and add").

Two calls per company, both cheap:

    list      GET /api/v1/homepage/vendors/?limit=200&offset=N
              name, slug, summary, office_country, employee_min/max, founded,
              primary_service, services. 21 pages cover the whole directory.
    profile   GET /api/v1/vendors/profile/{slug}/
              website, linkedin, description (HTML), founded, employees.
    location  GET /api/v1/vendors/profile/{pk}/locations/  -> state/city

THE TIME BUDGET. Cloud Run cuts a request at 300 seconds and the profile pass
is ~8,000 HTTP calls, so one call cannot do everything. The list pass is fast
and always complete; the profile pass is TIME-BOXED and skips companies whose
website we already hold (the caller passes them in), so each run fills the
next few hundred and the weekly refresh finishes the job over a few weeks.
save_targets is merge-only, so re-running never overwrites anything.
"""
import html as _html
import logging
import re
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import Dict, Iterable, List, Optional

import requests

logger = logging.getLogger(__name__)

BASE = "https://directory.enterprise-ireland.com"
SOURCE_NAME = "Enterprise Ireland Directory"
_HEADERS = {"User-Agent": "Mozilla/5.0 (compatible; AverroesIntel/1.0; +https://averroescapital.com)",
            "Accept": "application/json"}
_PAGE = 200
_TIMEOUT = 15


def _strip_html(s: str) -> str:
    t = re.sub(r"<(script|style)[^>]*>.*?</\1>", " ", s or "", flags=re.I | re.S)
    t = re.sub(r"</(p|div|li|br|h\d)>", "\n", t, flags=re.I)
    t = re.sub(r"<[^>]+>", " ", t)
    t = _html.unescape(t)
    return re.sub(r"[ \t]+", " ", re.sub(r"\n\s*\n+", "\n", t)).strip()


def _get(url: str, session: Optional[requests.Session] = None) -> Optional[dict]:
    try:
        r = (session or requests).get(url, headers=_HEADERS, timeout=_TIMEOUT)
        if r.status_code != 200:
            return None
        return r.json()
    except Exception as e:
        logger.debug(f"[EI] {url}: {e}")
        return None


def list_vendors(max_pages: Optional[int] = None, session: Optional[requests.Session] = None) -> List[Dict]:
    """Every vendor summary, in directory order. ~21 pages, a few seconds."""
    out, offset, pages = [], 0, 0
    while True:
        data = _get(f"{BASE}/api/v1/homepage/vendors/?limit={_PAGE}&offset={offset}", session)
        results = (data or {}).get("results") or []
        if not results:
            break
        out.extend(results)
        pages += 1
        offset += _PAGE
        if (max_pages and pages >= max_pages) or offset >= int((data or {}).get("count") or 0):
            break
    return out


def fetch_profile(slug: str, pk: Optional[int] = None, session: Optional[requests.Session] = None) -> Dict:
    """website, linkedin, description and the main office for one vendor."""
    prof = _get(f"{BASE}/api/v1/vendors/profile/{slug}/", session) or {}
    out = {
        "website": (prof.get("website") or "").strip(),
        "linkedin_url": "",
        "description": _strip_html(prof.get("description") or ""),
        "year_founded": prof.get("founded"),
        "employee_min": prof.get("employee_min"), "employee_max": prof.get("employee_max"),
        "hq_city": "", "hq_location": "",
    }
    li = (prof.get("linkedin") or "").strip()
    if li:
        out["linkedin_url"] = li if li.startswith("http") else f"https://www.linkedin.com/{li.lstrip('/')}"
    vendor_pk = prof.get("pk") or pk
    if vendor_pk:
        locs = _get(f"{BASE}/api/v1/vendors/profile/{vendor_pk}/locations/", session) or []
        main = next((l for l in locs if l.get("is_main")), locs[0] if locs else None)
        loc = (main or {}).get("location") or {}
        out["hq_city"] = (loc.get("city") or loc.get("state") or "").strip()
        out["hq_location"] = ", ".join(p for p in (loc.get("address"), loc.get("country")) if p) or ""
    return out


def to_target(v: Dict, profile: Optional[Dict] = None) -> Dict:
    """One directory vendor -> one Master Universe row (save_targets shape)."""
    primary = (v.get("primary_service") or {}).get("name") or ""
    services = [s.get("name") for s in (v.get("services") or []) if s.get("name")]
    emp_min, emp_max = v.get("employee_min"), v.get("employee_max")
    if profile:
        emp_min, emp_max = profile.get("employee_min") or emp_min, profile.get("employee_max") or emp_max
    employees = None
    if emp_min is not None and emp_max is not None:
        employees = int(round((float(emp_min) + float(emp_max)) / 2))
    elif emp_max is not None:
        employees = int(emp_max)
    summary = (v.get("summary") or "").strip()
    description = (profile or {}).get("description") or summary
    if summary and description and summary not in description:
        description = f"{summary}\n{description}"
    country = (v.get("office_country") or "Ireland").strip()
    row = {
        "name": (v.get("name") or "").strip(),
        "website": (profile or {}).get("website") or "",
        "linkedin_url": (profile or {}).get("linkedin_url") or "",
        # The directory's primary service is the sector. When it has none, the
        # sector stays EMPTY for SmartFill to classify: a filler label
        # ("Irish exporter", briefly, 16 Sep 2026) is not a sector and only
        # pollutes the filter.
        "sector": primary or (services[0] if services else ""),
        "verticals": ", ".join(services[:8]),
        "region": "Ireland" if country.lower() == "ireland" else country,
        "hq_country": country,
        "hq_city": (profile or {}).get("hq_city") or "",
        "hq_location": (profile or {}).get("hq_location") or "",
        "description": description,
        "employees": employees,
        "year_founded": (profile or {}).get("year_founded") or v.get("founded"),
        "ownership": "",
        "growth_signals": False,
        "source": SOURCE_NAME,
        # Kept for the caller's resume logic, stripped before save.
        "_slug": v.get("slug"), "_pk": v.get("pk"),
    }
    return row


def scrape(max_pages: Optional[int] = None, time_budget_s: int = 200,
           skip_names: Optional[Iterable[str]] = None, workers: int = 8) -> Dict:
    """Full list pass, then a TIME-BOXED profile pass.

    skip_names: companies we already hold WITH a website; their profile is not
    fetched again. Returns {"companies": [...], "listed", "profiled",
    "profile_pending", "seconds"}.
    """
    t0 = time.time()
    skip = {(n or "").strip().lower() for n in (skip_names or [])}
    with requests.Session() as s:
        vendors = list_vendors(max_pages, s)
    by_slug = {v["slug"]: v for v in vendors if v.get("name") and v.get("slug")}
    rows = {slug: to_target(v) for slug, v in by_slug.items()}
    todo = [v for v in by_slug.values() if (v.get("name") or "").strip().lower() not in skip]
    profiled = 0
    deadline = t0 + max(20, time_budget_s)
    chunk = workers * 2
    # Chunked so the deadline is checked BEFORE each batch is submitted: a
    # Cloud Run request dies at 300s, and work submitted past the budget is
    # work thrown away.
    with ThreadPoolExecutor(max_workers=workers) as pool:
        for i in range(0, len(todo), chunk):
            if time.time() > deadline:
                break
            batch = todo[i:i + chunk]
            futs = {pool.submit(fetch_profile, v["slug"], v.get("pk")): v for v in batch}
            for f in as_completed(futs):
                v = futs[f]
                try:
                    rows[v["slug"]] = to_target(v, f.result())
                    profiled += 1
                except Exception as e:
                    logger.debug(f"[EI] profile failed for {v.get('slug')}: {e}")
    companies = []
    for r in rows.values():
        r.pop("_slug", None), r.pop("_pk", None)
        companies.append(r)
    pending = max(0, len(todo) - profiled)
    logger.info(f"[EI] listed {len(companies)}, profiled {profiled}, pending {pending}, {time.time() - t0:.0f}s")
    return {"companies": companies, "listed": len(companies), "profiled": profiled,
            "profile_pending": pending, "seconds": round(time.time() - t0, 1)}
