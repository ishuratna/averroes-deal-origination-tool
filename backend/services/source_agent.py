"""
Source Agent: paste any URL, get a company list — no per-source code.

Architecture note: this is deliberately NOT a code-generating agent. One
universal pipeline handles every source: fetch the page (Playwright fallback
for JS-rendered sites), let ONE ungrounded AI call read the cleaned content
and extract the companies it can actually see (never invented), follow a
detected next-page link (bounded), and hand the list back for preview.
Re-reading the live page every refresh means nothing rots when a site's
HTML changes. No em dashes in this file.
"""
import os
import re
import json
import logging
from typing import Dict, List, Optional, Tuple
from urllib.parse import urljoin, urlparse

import requests
from bs4 import BeautifulSoup

logger = logging.getLogger(__name__)

_UA = ("Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
       "(KHTML, like Gecko) Chrome/126.0 Safari/537.36")


def fetch_page(url: str) -> Tuple[str, str]:
    """Page -> readable text with links preserved as [text](href).
    Returns (text, error). Playwright fallback for JS-shell pages."""
    html = ""
    try:
        resp = requests.get(url, headers={"User-Agent": _UA}, timeout=20)
        resp.raise_for_status()
        html = resp.text
    except Exception as e:
        return "", f"Fetch failed: {e}"

    text = _clean(html, url)
    if len(text) < 400:  # JS shell — try a rendered fetch
        try:
            from playwright.sync_api import sync_playwright
            with sync_playwright() as p:
                browser = p.chromium.launch(args=["--no-sandbox"])
                page = browser.new_page(user_agent=_UA)
                page.goto(url, timeout=25000, wait_until="networkidle")
                html = page.content()
                browser.close()
            text = _clean(html, url)
        except Exception as e:
            logger.info(f"[SourceAgent] Playwright fallback unavailable/failed for {url}: {e}")
    if not text:
        return "", "Page fetched but no readable content found"
    return text, ""


def _clean(html: str, base_url: str) -> str:
    soup = BeautifulSoup(html, "html.parser")
    for tag in soup(["script", "style", "noscript", "svg", "iframe"]):
        tag.decompose()
    # Preserve anchors as [text](absolute-href) so the model can attach websites
    for a in soup.find_all("a", href=True):
        label = a.get_text(" ", strip=True)
        href = urljoin(base_url, a["href"])
        if label and not href.startswith(("javascript:", "mailto:")):
            a.replace_with(f"[{label}]({href})")
    text = soup.get_text("\n", strip=True)
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text[:60000]


def ai_extract(page_text: str, url: str) -> Dict:
    """One ungrounded call: read the page, return the companies it lists."""
    api_key = os.getenv("GEMINI_API_KEY", "")
    if not api_key:
        return {"error": "GEMINI_API_KEY not configured"}
    prompt = f"""You are reading a web page fetched from {url}. Decide whether it presents a list
of COMPANIES (portfolio pages, award lists, accelerator cohorts, directories, league tables,
member lists and similar all count). Extract every company you can actually see.

STRICT RULES:
- Only companies whose names appear in the content below. NEVER invent, complete
  from memory, or add companies you believe should be on this list.
- website: only if a link for that company appears in the content (links look like
  [text](href)). Company's OWN site preferred; otherwise leave "".
- description: only from text on the page about that company, max 2 sentences, else "".
- Ignore navigation, sponsors, the site's own brand, people, and non-company entries.
- next_page_url: an absolute URL ONLY if the content clearly shows a next-page /
  page-2 style link for THIS list; else "".
- source_title: a short human name for this source, e.g. "Balderton Portfolio".

PAGE CONTENT:
{page_text}

Return ONLY valid JSON:
{{"is_company_list": true/false,
 "source_title": "...",
 "companies": [{{"name": "...", "website": "", "description": ""}}],
 "next_page_url": ""}}"""
    try:
        from google import genai
        from google.genai.types import GenerateContentConfig
        client = genai.Client(api_key=api_key)
        resp = client.models.generate_content(
            model="gemini-2.5-flash", contents=prompt,
            config=GenerateContentConfig(temperature=0.1, response_mime_type="application/json"))
        text = (resp.text or "").strip()
        if text.startswith("```"):
            text = text.split("\n", 1)[1].rsplit("```", 1)[0].strip()
        data = json.loads(text[text.find("{"):text.rfind("}") + 1])
        clean = []
        for c in (data.get("companies") or []):
            if isinstance(c, dict) and (c.get("name") or "").strip():
                clean.append({"name": c["name"].strip()[:120],
                              "website": (c.get("website") or "").strip()[:300],
                              "description": (c.get("description") or "").strip()[:500]})
        return {"is_company_list": bool(data.get("is_company_list")),
                "source_title": (data.get("source_title") or "").strip()[:80],
                "companies": clean,
                "next_page_url": (data.get("next_page_url") or "").strip()}
    except Exception as e:
        logger.warning(f"[SourceAgent] extraction failed for {url}: {e}")
        return {"error": f"AI extraction failed: {e}"}


def extract_source(url: str, max_pages: int = 4) -> Dict:
    """Full extraction: fetch -> AI read -> follow pagination (bounded)."""
    url = url.strip()
    if not urlparse(url).scheme:
        url = "https://" + url
    seen_urls, seen_names = set(), set()
    companies, warnings = [], []
    title = ""
    current, pages = url, 0

    while current and pages < max_pages and current not in seen_urls:
        seen_urls.add(current)
        pages += 1
        text, err = fetch_page(current)
        if err:
            warnings.append(f"page {pages}: {err}")
            break
        result = ai_extract(text, current)
        if result.get("error"):
            warnings.append(f"page {pages}: {result['error']}")
            break
        if pages == 1 and not result.get("is_company_list") and not result.get("companies"):
            return {"url": url, "title": result.get("source_title") or urlparse(url).netloc,
                    "companies": [], "pages_scanned": pages,
                    "warnings": ["The AI could not find a company list on this page."]}
        title = title or result.get("source_title") or urlparse(url).netloc
        for c in result.get("companies", []):
            key = re.sub(r"[^a-z0-9]", "", c["name"].lower())
            if key and key not in seen_names:
                seen_names.add(key)
                companies.append(c)
        nxt = result.get("next_page_url") or ""
        current = nxt if nxt.startswith("http") else ""

    return {"url": url, "title": title or urlparse(url).netloc,
            "companies": companies, "pages_scanned": pages, "warnings": warnings}
