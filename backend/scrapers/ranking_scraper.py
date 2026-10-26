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
            EY_EOY_LIST: "https://www.ey.com/en_uk/entrepreneur-of-the-year",
        }

    def scrape_ranking(self, list_name: str) -> List[Dict]:
        logger.info(f"Ingesting Ranking List: {list_name}")

        if list_name == "Startups 100 UK":
            return self._scrape_startups100()
        if list_name == EY_EOY_LIST:
            return self._scrape_ey_eoy()

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

    def _scrape_ey_eoy(self) -> List[Dict]:
        return scrape_ey_eoy()

    def get_supported_lists(self) -> List[str]:
        return list(self.sources.keys())

    # The weekly refresh (main.py) walks every scraper through
    # get_supported_sources()/scrape_source(). This class only ever had the
    # _lists/_ranking names, so the loop's catch-all logged "scraper
    # enumeration failed" and no ranking list was ever refreshed on a
    # schedule (found 26 Oct 2026, doctrine 4ah). Same methods, both names.
    def get_supported_sources(self) -> List[str]:
        return self.get_supported_lists()

    def scrape_source(self, list_name: str) -> List[Dict]:
        return self.scrape_ranking(list_name)


# ── EY Entrepreneur Of The Year UK (Ishu, 26 Oct 2026) ───────────────────────
# Three kinds of page, all server-rendered on ey.com (AEM), no JS needed:
#   1. The April press release "EY Entrepreneur Of The Year {year} UK regional
#      finalists": five accordion panels (London, Midlands, North, Scotland,
#      South), each a <table> of Nominees | Company Name, the company cell a
#      link to the company's own website, sometimes "<a>Legal Co</a> trading
#      as Brand". ~90 businesses a year; the richest list by far.
#   2. The "Previous UK finalists" page: the ten-or-so national finalists per
#      year, each as a portrait + <h4>Person</h4> + <p><a>Company</a></p>
#      (2025 layout) or <p>Person<br><a>Company</a></p> (2024 and earlier).
#   3. (Not read) the October "UK finalists" and November "winners" releases:
#      every name there is already on page 1 or 2.
# Nothing is inferred: name, website and the nominee come from the page; the
# year and region from the page it sat on. Sector, size and fit are left to
# the hard filters and SmartFill, as with every source (doctrine 4).

EY_EOY_LIST = "EY Entrepreneur Of The Year"
EY_EOY_FINALISTS_URL = "https://www.ey.com/en_uk/entrepreneur-of-the-year/uk-finalists"
# Both slugs EY has used for the regional release; a year is tried with each.
EY_EOY_REGIONAL_SLUGS = (
    "https://www.ey.com/en_uk/newsroom/{year}/04/ey-entrepreneur-of-the-year-{year}-regional-finalists",
    "https://www.ey.com/en_uk/newsroom/{year}/04/ey-entrepreneur-of-the-year-{year}-uk-regional-finalists",
)
# Links on an EY page that are never a finalist's website.
_EY_NOT_COMPANY = re.compile(
    r"(ey\.com|linkedin\.com|facebook\.com|youtube\.com|twitter\.com|x\.com|instagram\.com|"
    r"juliusbaer\.com|awardsplatform\.com|login\.ey\.com)", re.I)
_TRADING_AS = re.compile(r"\btrading\s+as\s+(.+?)\s*$", re.I)


def _clean(s: str) -> str:
    s = (s or "").replace("\xa0", " ")
    s = re.sub(r"\bRead (more|less)\b", " ", s)
    return re.sub(r"\s+", " ", s).strip(" |")


def _split_nominees(text: str) -> List[str]:
    parts = re.split(r"\s*(?:\||,|&|\band\b)\s*", _clean(text))
    return [p.strip() for p in parts if p.strip()]


def parse_ey_eoy_regional(html: str, year: int) -> List[Dict]:
    """Page kind 1. PURE: html in, rows out."""
    soup = BeautifulSoup(html, "html.parser")
    out: List[Dict] = []
    for table in soup.find_all("table"):
        # The region is the accordion header that owns this table: the
        # nearest preceding <h2> with any text (the table's own caption h2
        # is empty).
        region = ""
        for h in table.find_all_previous(["h2", "h3"]):
            t = _clean(h.get_text(" ", strip=True))
            if t:
                region = t
                break
        for tr in table.find_all("tr"):
            tds = tr.find_all("td")
            if len(tds) < 2:
                continue
            nominees = _clean(tds[0].get_text(" ", strip=True))
            cell = tds[1]
            links = [a for a in cell.find_all("a", href=True)
                     if a["href"].startswith("http") and not _EY_NOT_COMPANY.search(a["href"])]
            if not links:
                continue
            a = links[0]
            legal = _clean(a.get_text(" ", strip=True))
            cell_text = _clean(cell.get_text(" ", strip=True))
            m = _TRADING_AS.search(cell_text)
            brand = _clean(m.group(1)) if m else ""
            name = brand or legal
            if not name:
                continue
            desc = (f"EY Entrepreneur Of The Year {year} UK regional finalist ({region or 'UK'}). "
                    f"Nominee{'s' if len(_split_nominees(nominees)) > 1 else ''}: {nominees}.")
            if brand and legal and brand.lower() != legal.lower():
                desc += f" Legal entity: {legal} (trading as {brand})."
            out.append(_ey_row(name, a["href"], nominees, region, year, desc))
    return out


def parse_ey_eoy_finalists(html: str) -> List[Dict]:
    """Page kind 2. PURE. The year comes from the nearest preceding heading
    that carries one ('2025 UK finalists')."""
    soup = BeautifulSoup(html, "html.parser")
    out: List[Dict] = []
    for a in soup.find_all("a", href=True):
        href = a["href"]
        if not href.startswith("http") or _EY_NOT_COMPANY.search(href):
            continue
        company = _clean(a.get_text(" ", strip=True))
        if not company or len(company) > 60:
            continue
        # Year: nearest preceding heading with a four-digit year.
        year = 0
        for h in a.find_all_previous(["h1", "h2", "h3"]):
            m = re.search(r"\b(20\d\d)\b", h.get_text(" ", strip=True))
            if m:
                year = int(m.group(1))
                break
        if not year:
            continue  # a link outside any finalists section (nav, footer)
        # Person: the <h4> just before (2025 layout), else the paragraph's own
        # text with the company stripped (2024 layout).
        person = ""
        h4 = a.find_previous("h4")
        p = a.find_parent("p")
        p_text = _clean(p.get_text(" ", strip=True)) if p else ""
        own = _clean(p_text.replace(company, "")) if p_text else ""
        if own:
            person = own
        elif h4 is not None:
            person = _clean(h4.get_text(" ", strip=True))
        region = ""
        for h in a.find_all_previous(["h2", "h3"]):
            t = _clean(h.get_text(" ", strip=True))
            if t and not re.search(r"\b20\d\d\b", t):
                region = t
                break
            if re.search(r"\b20\d\d\b", t):
                break
        desc = f"EY Entrepreneur Of The Year {year} UK national finalist{f' ({region})' if region else ''}. Nominee: {person}." if person \
            else f"EY Entrepreneur Of The Year {year} UK national finalist."
        out.append(_ey_row(company, href, person, region, year, desc))
    return out


def _ey_row(name: str, website: str, nominees: str, region: str, year: int, desc: str,
            country: str = "UK") -> Dict:
    first = _split_nominees(nominees)[0] if nominees else ""
    return {
        "name": name,
        "website": website.strip(),
        "sector": "",
        "region": "Ireland" if country == "Ireland" else "UK",
        "hq_country": "Ireland" if country == "Ireland" else "United Kingdom",
        "hq_location": region or "",
        "contact_name": first,
        "contact_title": "Founder / CEO (EY EOY nominee)" if first else "",
        "description": desc,
        "source": f"{EY_EOY_LIST} {country} {year}",
        "status": "Scraped",
    }


# ── Ireland (Ishu, 26 Oct 2026: "UK and Ireland, that's it") ────────────────
# EY Ireland's April release names its 24 finalists inside a Flourish
# embed (flo.uri.sh/visualisation/<id>/embed), three columns (Emerging,
# Established, International), each cell "Person(s) of Company". The embed
# page carries the data inline as `_Flourish_data = {...}`; the EY page is
# otherwise empty at "The finalists are:". No website on the page: SmartFill
# finds it. "International" is a category (Irish companies trading abroad),
# not a location: every finalist is on the island of Ireland.
EY_EOY_IE_SLUGS = (
    "https://www.ey.com/en_ie/newsroom/{year}/04/ey-entrepreneur-of-the-year-ireland-{year}-finalists-announced",
    "https://www.ey.com/en_ie/newsroom/{year}/04/ey-entrepreneur-of-the-year-{year}-finalists-revealed",
    "https://www.ey.com/en_ie/newsroom/{year}/04/ey-entrepreneur-of-the-year-{year}-finalists-announced",
)
_FLOURISH_SRC = re.compile(r"https://flo\.uri\.sh/visualisation/\d+/embed[^\"' ]*")
_FLOURISH_DATA = re.compile(r"_Flourish_data\s*=\s*(\{.*?\})\s*[,;]\s*(?:_Flourish|var|let|const|window|</script>)", re.S)
_PERSON_OF = re.compile(r"^(?P<people>.+?)\s+of\s+(?P<company>.+?)$", re.S)


_FLOURISH_DATA_SRC = re.compile(r'class="flourish-embed[^"]*"\s+data-src="visualisation/(\d+)[^"]*"')


def flourish_embed_url(page_html: str) -> str:
    """The raw page carries <div class="flourish-embed" data-src="visualisation/<id>?..">;
    the iframe URL only exists after Flourish's embed.js runs (found 26 Oct
    2026: the live run read the UK pages and zero Irish rows). Either form."""
    m = _FLOURISH_SRC.search(page_html or "")
    if m:
        return m.group(0)
    m = _FLOURISH_DATA_SRC.search(page_html or "")
    if m:
        return f"https://flo.uri.sh/visualisation/{m.group(1)}/embed?auto=1"
    return ""


def parse_ey_eoy_ireland(embed_html: str, year: int) -> List[Dict]:
    """PURE: the Flourish embed page in, finalist rows out."""
    import json as _json
    m = _FLOURISH_DATA.search(embed_html or "")
    if not m:
        return []
    try:
        data = _json.loads(m.group(1))
    except Exception:
        return []
    cols = []
    cm = re.search(r"_Flourish_data_column_names\s*=\s*(\{.*?\})\s*[,;]", embed_html, re.S)
    if cm:
        try:
            rows = _json.loads(cm.group(1)).get("rows")
            # Flourish writes {"rows": {"columns": [...]}} for a single header
            # row and {"rows": [{"columns": [...]}, ...]} for several.
            rows = [rows] if isinstance(rows, dict) else (rows or [])
            cols = [c for row in rows for c in row.get("columns", [])]
        except Exception:
            cols = []
    out: List[Dict] = []
    for row in data.get("rows", []):
        for i, cell in enumerate(row.get("columns", [])):
            cell = _clean(str(cell))
            pm = _PERSON_OF.match(cell)
            if not pm:
                continue
            people, company = _clean(pm.group("people")), _clean(pm.group("company"))
            if not company:
                continue
            category = cols[i] if i < len(cols) else ""
            desc = (f"EY Entrepreneur Of The Year {year} Ireland finalist"
                    f"{f' ({category} category)' if category else ''}, island of Ireland. Nominee"
                    f"{'s' if len(_split_nominees(people)) > 1 else ''}: {people}.")
            # The category (Emerging/Established/International) is a programme
            # label, not a place: it stays in the description only.
            out.append(_ey_row(company, "", people, "", year, desc, country="Ireland"))
    return out


def scrape_ey_eoy_ireland(years: List[int]) -> List[Dict]:
    out: List[Dict] = []
    for year in years:
        for slug in EY_EOY_IE_SLUGS:
            url = slug.format(year=year)
            try:
                resp = requests.get(url, headers=HEADERS, timeout=15)
            except Exception as e:
                logger.warning(f"[EY EOY IE] {url}: {e}")
                continue
            if resp.status_code != 200:
                continue
            embed = flourish_embed_url(resp.text)
            if not embed:
                logger.warning(f"[EY EOY IE] {url}: no Flourish embed found")
                break
            try:
                er = requests.get(embed, headers=HEADERS, timeout=15)
                rows = parse_ey_eoy_ireland(er.text, year) if er.status_code == 200 else []
            except Exception as e:
                logger.warning(f"[EY EOY IE] {embed}: {e}")
                rows = []
            logger.info(f"[EY EOY IE] {year}: {len(rows)} finalists")
            out.extend(rows)
            break
    return out


def scrape_ey_eoy(years: List[int] = None) -> List[Dict]:
    """Every regional release we can reach (this year and the four before, both
    slugs) plus the national finalists page. Deduped by name, newest year
    first so a company that appears twice keeps its latest citation."""
    from datetime import date
    this_year = date.today().year
    years = years or list(range(this_year + 1, this_year - 4, -1))
    seen: Dict[str, Dict] = {}

    def _add(rows: List[Dict]):
        for r in rows:
            key = re.sub(r"[^a-z0-9]", "", r["name"].lower())
            if key and key not in seen:
                seen[key] = r

    for year in years:
        for slug in EY_EOY_REGIONAL_SLUGS:
            url = slug.format(year=year)
            try:
                resp = requests.get(url, headers=HEADERS, timeout=15)
            except Exception as e:
                logger.warning(f"[EY EOY] {url}: {e}")
                continue
            if resp.status_code != 200 or "<table" not in resp.text:
                continue
            rows = parse_ey_eoy_regional(resp.text, year)
            logger.info(f"[EY EOY] {year} regional finalists: {len(rows)} companies")
            _add(rows)
            break
    try:
        resp = requests.get(EY_EOY_FINALISTS_URL, headers=HEADERS, timeout=15)
        if resp.status_code == 200:
            rows = parse_ey_eoy_finalists(resp.text)
            logger.info(f"[EY EOY] national finalists page: {len(rows)} companies")
            _add(rows)
    except Exception as e:
        logger.warning(f"[EY EOY] finalists page: {e}")
    _add(scrape_ey_eoy_ireland(years))
    if not seen:
        logger.warning("[EY EOY] No companies parsed; the page layout may have changed.")
    return list(seen.values())
