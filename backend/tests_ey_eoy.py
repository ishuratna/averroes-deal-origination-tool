"""EY Entrepreneur Of The Year UK source (26 Oct 2026). Fixtures are cut from
the live pages (ey.com, AEM): the Midlands accordion of the 2026 regional
release, and the two layouts of the national finalists page.
Run: python3 tests_ey_eoy.py"""
from scrapers.ranking_scraper import (parse_ey_eoy_regional, parse_ey_eoy_finalists,
                                      RankingListScraper, EY_EOY_LIST)

REGIONAL = """
<h2 class="up-accordion__header"><button><span class="up-accordion__header-button-title">Midlands</span></button></h2>
<table class="up-table-container__table">
  <caption><h2></h2></caption>
  <thead><tr><th><div><p>Nominees</p></div></th><th><div><p>Company Name</p></div></th></tr></thead>
  <tbody>
    <tr><td><div><p>Baoli Zhao</p></div></td>
        <td><div><p><a href="https://www.allsee-tech.com/" target="_blank">Allsee Technologies</a></p></div></td></tr>
    <tr><td><div><p>Ben Harwood</p></div></td>
        <td><div><p><a href="https://www.naismiths.com" target="_blank">Cornerstone Group Holdings</a>&nbsp;trading as Naismiths</p></div></td></tr>
    <tr><td><div><p>Jos van der Steen | Peter Cliff</p></div></td>
        <td><div><p><a href="https://conductr.com/">CLIFFSTEEN</a> trading as CONDUCTR</p></div></td></tr>
    <tr><td><div><p>Dean Willshee</p></div></td>
        <td><div><p><a href="https://www.willshees.co.uk/">Willshee's Waste &amp; Recycling</a></p></div></td></tr>
  </tbody>
</table>
<p>The programme Headline Sponsor is <a href="https://www.juliusbaer.com/uk/en/">Julius Baer International</a>.</p>
"""

FINALISTS = """
<nav><a href="https://www.ey.com/en_uk/insights">Insights</a></nav>
<h1>2025 UK finalists</h1>
<h4>Mia Drennan</h4>
<p><a href="https://glas.agency">GLAS</a></p>
<ul><li><a href="http://LinkedIn.com/in/miadrennan">LinkedIn</a></li></ul>
<h4>James O'Loan &amp; Ricardo Laverty</h4>
<p><a href="https://www.chemist-4-u.com/">Chemist 4 U</a></p>
<h3>2024 UK finalists</h3>
<h2>London &amp; South East</h2>
<p>Dr Ben Maruthappu<br><a href="https://ceracare.co.uk/">Cera Care</a></p>
<h2>North</h2>
<p>Jenna Meek<br><a href="https://refybeauty.com/">REFY</a></p>
<footer><a href="https://www.facebook.com/pages/Ernst-Young/195665063800329">Facebook</a></footer>
"""


def test_regional_table():
    rows = parse_ey_eoy_regional(REGIONAL, 2026)
    names = [r["name"] for r in rows]
    assert names == ["Allsee Technologies", "Naismiths", "CONDUCTR", "Willshee's Waste & Recycling"], names
    by = {r["name"]: r for r in rows}
    assert by["Allsee Technologies"]["website"] == "https://www.allsee-tech.com/"
    assert by["Allsee Technologies"]["contact_name"] == "Baoli Zhao"
    assert by["Allsee Technologies"]["hq_location"] == "Midlands"
    assert by["Allsee Technologies"]["source"] == "EY Entrepreneur Of The Year UK 2026"
    assert "regional finalist (Midlands)" in by["Allsee Technologies"]["description"]
    # trading-as: the brand is the name, the legal entity is kept in the text
    assert by["Naismiths"]["website"] == "https://www.naismiths.com"
    assert "Legal entity: Cornerstone Group Holdings (trading as Naismiths)" in by["Naismiths"]["description"]
    # two nominees: first is the contact, both are in the description
    assert by["CONDUCTR"]["contact_name"] == "Jos van der Steen"
    assert "Nominees: Jos van der Steen | Peter Cliff" in by["CONDUCTR"]["description"]
    # the sponsor link outside the table never becomes a company
    assert "Julius Baer International" not in names
    assert all(r["hq_country"] == "United Kingdom" and r["status"] == "Scraped" for r in rows)


def test_finalists_page_both_layouts():
    rows = parse_ey_eoy_finalists(FINALISTS)
    by = {r["name"]: r for r in rows}
    assert set(by) == {"GLAS", "Chemist 4 U", "Cera Care", "REFY"}, set(by)
    assert by["GLAS"]["contact_name"] == "Mia Drennan" and by["GLAS"]["source"].endswith("2025")
    assert by["Chemist 4 U"]["contact_name"] == "James O'Loan"
    assert by["Cera Care"]["contact_name"] == "Dr Ben Maruthappu" and by["Cera Care"]["source"].endswith("2024")
    assert by["Cera Care"]["hq_location"] == "London & South East"
    assert by["REFY"]["hq_location"] == "North"
    # nav/footer/LinkedIn links never become companies
    assert "Insights" not in by and "LinkedIn" not in by and "Facebook" not in by


def test_registered_and_reachable_by_the_weekly_refresh():
    r = RankingListScraper()
    assert EY_EOY_LIST in r.get_supported_lists()
    # the weekly refresh walks get_supported_sources()/scrape_source()
    assert r.get_supported_sources() == r.get_supported_lists()
    assert callable(getattr(r, "scrape_source", None))


FLOURISH = """<script>
            var _Flourish_data_column_names = {"rows":{"columns":["Emerging","Established","International"]}},
                _Flourish_data_metadata = {"rows":{"columns":[{"type":"string"},{"type":"string"},{"type":"string"}]}},
                _Flourish_data_timestamps = {"rows":{"last_updated":new Date(1776173411291)}},
                _Flourish_data = {"rows":[{"columns":["Aoife Matthews, Jennifer O'Connell & Louise O'Riordan of SISTERLY","Tommy Kearns of Xtremepush","Brian Moloney of StormHarvester"]},{"columns":["Bobby Healy of Manna Air Delivery","John Lunn of Lunn's","Ronan & Conor Burke of Inscribe"]}]},
                _Flourish_visualisation_id = 28338005;
</script>"""

EY_IE_PAGE = '<div class="upHtmlApp"><iframe src="https://flo.uri.sh/visualisation/28338005/embed?auto=1"></iframe></div>'


def test_ireland_flourish_embed():
    from scrapers.ranking_scraper import parse_ey_eoy_ireland, flourish_embed_url
    assert flourish_embed_url(EY_IE_PAGE) == "https://flo.uri.sh/visualisation/28338005/embed?auto=1"
    # the raw (pre-JS) page: Flourish's div, no iframe yet
    raw = ('<div id="htmlApp-5b95a59d4e5d" class="up-html-app"><script>window.Flourish = {};</script>'
           '<div class="flourish-embed flourish-cards" data-src="visualisation/28338005?790558">'
           '<script src="https://public.flourish.studio/resources/embed.js"></script></div></div>')
    assert flourish_embed_url(raw) == "https://flo.uri.sh/visualisation/28338005/embed?auto=1"
    rows = parse_ey_eoy_ireland(FLOURISH, 2026)
    by = {r["name"]: r for r in rows}
    assert set(by) == {"SISTERLY", "Xtremepush", "StormHarvester", "Manna Air Delivery", "Lunn's", "Inscribe"}, set(by)
    assert by["SISTERLY"]["contact_name"] == "Aoife Matthews"
    assert "Nominees: Aoife Matthews, Jennifer O'Connell & Louise O'Riordan" in by["SISTERLY"]["description"]
    assert "(Emerging category)" in by["SISTERLY"]["description"]
    assert "(Established category)" in by["Xtremepush"]["description"]
    assert "(International category)" in by["Inscribe"]["description"]
    assert by["Inscribe"]["contact_name"] == "Ronan"  # first of "Ronan & Conor Burke"; SmartFill completes it
    assert by["Lunn's"]["hq_country"] == "Ireland" and by["Lunn's"]["region"] == "Ireland"
    assert by["Lunn's"]["hq_location"] == ""  # a category is not a place
    assert by["Lunn's"]["source"] == "EY Entrepreneur Of The Year Ireland 2026"
    assert by["Lunn's"]["website"] == ""


if __name__ == "__main__":
    import sys
    fails = 0
    for name, fn in list(globals().items()):
        if name.startswith("test_") and callable(fn):
            try:
                fn(); print(f"ok   {name}")
            except Exception as e:
                fails += 1; print(f"FAIL {name}: {e!r}")
    sys.exit(1 if fails else 0)


