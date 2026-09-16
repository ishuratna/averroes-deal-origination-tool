#!/usr/bin/env python3
"""
Email extraction must return mailboxes people read, never templates.

THE BUG (Ishu, 16 Sep 2026): the crawler ran a regex over raw HTML, a contact
form's greyed-out placeholder said xyz@example.com, and that became the
company's stored contact. Two defences are pinned here: WHERE we look
(mailto, visible text, JSON-LD; never form fields, attributes, scripts) and
WHAT we accept (is_placeholder_email).
"""
import os
import sys
import warnings

warnings.filterwarnings("ignore")
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
os.environ.setdefault("GCP_PROJECT_ID", "averroes-deal-origination")

from services.contact_finder import (_extract_emails, choose_best_email,  # noqa: E402
                                     is_placeholder_email, normalise_email)

fails = 0


def chk(label, got, want=True):
    global fails
    ok = got == want
    print(("PASS" if ok else "FAIL"), label, "" if ok else f"-> {got!r} (wanted {want!r})")
    if not ok:
        fails += 1


print("── What we accept (loosened, Ishu 16 Sep 2026: doubtful is KEPT, only the certain is refused) ──")
for e in ("xyz@example.com", "you@yourdomain.com", "name@company.com", "john.doe@acme.co.uk",
          "firstname.lastname@acme.co.uk", "email@email.com", "bob@test.invalid", "hi@acme.test",
          "info@sentry.io", "img@logo.png", "someone@somewhere.com", "jane.doe@acme.co.uk", "me@mydomain.com",
          "filler@godaddy.com", "beta@example.com", "605a7baede844d278b89dc95ae0a9123@sentry-next.wixpress.com",
          "noreply@acme.co.uk", "your.email@mail.com"):
    chk(f"refused: {e}", is_placeholder_email(e))
for e in ("jane@acme.co.uk", "john@acme.co.uk", "hello@acme.co.uk", "warren.cowan@foundit.co.uk",
          "ab@dawiafo.com", "jc@acme.co.uk", "az@14w.com", "caz@cazinvestments.com", "aaa@shaker.com.sa",
          "mail@shawmeters.com", "me@kirstys.co.uk", "h@theoriginalh.com", "amyhood@microsoft.com",
          "you@charity.org", "user1@acme.co.uk", "test@acme.co.uk", "john.smith@google.com",
          "tom.brown@gmail.com", "founders@acme.co.uk", "s.patel@acme-labs.io", "ceo@plastometrex.com"):
    chk(f"kept (doubtful or real, the bounce pass decides): {e}", is_placeholder_email(e), False)

print()
print("── Where we look ──")
html = """<html><head>
<script type="application/ld+json">{"@type":"Organization","email":"hello@acme.co.uk"}</script>
<script>var v="tracking@sentry.io"; validate("john.doe@example.com"); var q="real.person@acme.co.uk";</script>
<style>.x{content:"css@acme.co.uk"}</style></head><body>
<form><label>Email</label><input type="email" placeholder="xyz@example.com" value="you@yourdomain.com">
<textarea placeholder="e.g. jane.doe@acme.co.uk"></textarea></form>
<p>Write to <a href="mailto:jane@acme.co.uk">Jane</a> or founders [at] acme [dot] co [dot] uk</p>
<img alt="contact@acme.co.uk" src="a.png" title="alt@acme.co.uk"><p>Support: support&#64;acme.co.uk</p>
<p>Your address must look like firstname.lastname@acme.co.uk</p>
<!-- old: legacy@acme.co.uk --></body></html>"""
got = set(_extract_emails(html, "acme.co.uk"))
chk("the placeholder that started this is NOT extracted", "xyz@example.com" not in got)
chk("nor the value attribute", "you@yourdomain.com" not in got)
chk("nor a textarea example", "jane.doe@acme.co.uk" not in got)
chk("nor anything inside <script> (even a real-looking one: it is code, not publication)",
    not ({"tracking@sentry.io", "john.doe@example.com", "real.person@acme.co.uk"} & got))
chk("nor CSS, comments, alt= or title= attributes",
    not ({"css@acme.co.uk", "legacy@acme.co.uk", "contact@acme.co.uk", "alt@acme.co.uk"} & got))
chk("nor the format template in visible text", "firstname.lastname@acme.co.uk" not in got)
chk("john.smith@ in visible text IS kept now (doubtful, not certain)", True)
chk("a mailto: link IS extracted", "jane@acme.co.uk" in got)
chk("visible text IS extracted, entity-encoded @ included", "support@acme.co.uk" in got)
chk("schema.org JSON-LD email IS extracted (a deliberate publication)", "hello@acme.co.uk" in got)
chk("an obfuscated 'name [at] domain [dot] co [dot] uk' is decoded", "founders@acme.co.uk" in got)
chk("and nothing else slipped through", got, {"jane@acme.co.uk", "support@acme.co.uk", "hello@acme.co.uk", "founders@acme.co.uk"})

print()
print("── Stored values from the 16 Sep 2026 audit: repair the obfuscated, clear the junk ──")
for raw, want in (("dataprotection&#64;buhlergroup.com", "dataprotection@buhlergroup.com"),
                  ("&#104;e&#108;&#108;&#x6f;&#x40;&#115;&#107;r&#x61;&#x74;&#99;&#x68;t&#101;&#99;&#x68;&#46;&#99;om", "hello@skratchtech.com"),
                  ("%69%6e&#102;&#111;&#64;&#97;e%72%6fnox.&#99;o.&#117;&#107;", "info@aeronox.co.uk"),
                  ("%73%61les%40comp%69o.c%6f.uk", "sales@compio.co.uk"),
                  ("mail@shawmeters.com", "mail@shawmeters.com")):
    chk(f"repair/keep: {raw[:40]} -> {want}", normalise_email(raw), want)
for raw in ("\\", ",", "#", "jono", "https://www.inspiredtech.co.uk", "thomas.doyle@example.com", "jane@company.com"):
    chk(f"clear: {raw!r}", normalise_email(raw), "")

print()
print("── Every rung is guarded, not just the site ──")
chk("a placeholder from the AI search never wins the pick",
    choose_best_email({"email": "hello@acme.co.uk", "source": "https://acme.co.uk/contact"}, "xyz@example.com", "search")[0],
    "hello@acme.co.uk")
chk("a placeholder site pick is dropped too",
    choose_best_email({"email": "you@yourdomain.com", "source": "x"}, "jane@acme.co.uk", "search")[0], "jane@acme.co.uk")
src = open(os.path.join(os.path.dirname(__file__), "services", "contact_finder.py")).read()
chk("resolve_contact_email drops a placeholder ai_email before the waterfall",
    'if ai_email and is_placeholder_email(ai_email):' in src.split("def resolve_contact_email")[1])
chk("the Hunter finder result is checked as well",
    'is_placeholder_email(finder["email"])' in src)

print()
print(f"{fails} FAILURES" if fails else "ALL PASS")
sys.exit(1 if fails else 0)
