#!/usr/bin/env python3
"""
The identity guard: the two-anchor rule and the zero-AI retro audit.

The failure this prevents: two companies share a name, the grounded search
finds the wrong one, and its founder/city/description overwrite the right
company's record. Verification is CODE, not the model's confidence.
"""
import os
import sys
import warnings

warnings.filterwarnings("ignore")
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
os.environ.setdefault("GCP_PROJECT_ID", "averroes-deal-origination")

from ai.identity_check import (  # noqa: E402
    audit_row, check_identity, registrable_domain, seed_anchors, surname,
)

fails = 0


def chk(label, got, want=True):
    global fails
    ok = got == want
    print(("PASS" if ok else "FAIL"), label, "" if ok else f"-> {got!r} (wanted {want!r})")
    if not ok:
        fails += 1


print("── The anchors themselves ──")
chk("domain from url", registrable_domain("https://www.plastometrex.com/about"), "plastometrex.com")
chk("uk two-part suffix kept", registrable_domain("http://shop.acme.co.uk"), "acme.co.uk")
chk("domain from email", registrable_domain("jim@sub.acme.co.uk"), "acme.co.uk")
chk("garbage is empty", registrable_domain("not a url"), "")
chk("surname from full name", surname("Dr James Dean-Smith"), "dean-smith")
chk("single word gives that word", surname("Cher"), "cher")

seed = seed_anchors({"website": "https://plastometrex.com", "hq_city": "Cambridge",
                     "contact_name": "James Dean", "year_founded": 2018,
                     "ch_company_number": "11361720"})
chk("seed anchors extracted", seed["domain"], "plastometrex.com")
chk("seed city normalised", seed_anchors({"hq_city": "Greater Manchester"})["hq_city"], "manchester")

print()
print("── The two-anchor rule ──")
ok_echo = {"website": "plastometrex.com", "hq_city": "Cambridge, UK",
           "founder_name": "James Dean", "year_founded": "2018", "company_number": "11361720"}
chk("full agreement -> confirmed", check_identity(seed, ok_echo)["verdict"], "confirmed")
chk("domain alone confirms (weight 2)",
    check_identity(seed, {"website": "https://www.plastometrex.com"})["verdict"], "confirmed")
chk("founder alone does NOT confirm (weight 1)",
    check_identity(seed, {"founder_name": "Jane Dean"})["verdict"], "unverified")
chk("founder + city together confirm",
    check_identity(seed, {"founder_name": "Sarah Dean", "hq_city": "cambridge"})["verdict"], "confirmed")
chk("year within 1 still matches",
    check_identity(seed, {"hq_city": "Cambridge", "year_founded": "2019"})["verdict"], "confirmed")
# The mixup case: same name, different company entirely.
wrong = {"website": "plastometrex.io", "hq_city": "Austin",
         "founder_name": "Bob Smith", "year_founded": "2011"}
chk("wrong company -> mismatch", check_identity(seed, wrong)["verdict"], "mismatch")
chk("mismatch note names the conflicts",
    "domain" in check_identity(seed, wrong)["note"] and "austin" in check_identity(seed, wrong)["note"].lower())
# One stale anchor must not block a strong match (seeds are sometimes wrong).
stale_city = {"website": "plastometrex.com", "founder_name": "James Dean", "hq_city": "London"}
got = check_identity(seed, stale_city)
chk("one stale anchor vs domain+founder -> still confirmed", got["verdict"], "confirmed")
chk("...but the stale anchor is named", "stale" in got["note"])
chk("empty echo -> unverified, never confirmed", check_identity(seed, {})["verdict"], "unverified")
chk("no seed anchors at all -> unverified",
    check_identity(seed_anchors({}), ok_echo)["verdict"], "unverified")
chk("conflict equal to weak match -> mismatch (fail closed)",
    check_identity(seed, {"hq_city": "Leeds", "year_founded": "2018"})["verdict"], "mismatch")

print()
print("── The zero-AI retro audit ──")
owners = {"plastometrex.com": "Plastometrex", "acme.co.uk": "Acme"}
clean = {"name": "Plastometrex", "website": "https://plastometrex.com",
         "contact_email": "james@plastometrex.com", "ch_official_name": "PLASTOMETREX LIMITED"}
chk("clean row is not a suspect", audit_row(clean, owners)["suspect"], False)
cross = dict(clean, contact_email="jane@acme.co.uk")
got = audit_row(cross, owners)
chk("email on ANOTHER company's domain is the smoking gun", got["suspect"], True)
chk("...and names the other company", "Acme" in got["signals"][0])
chk("unknown foreign domain still flags",
    audit_row(dict(clean, contact_email="j@somewhere-else.com"), owners)["suspect"], True)
chk("generic gmail never flags",
    audit_row(dict(clean, contact_email="founder@gmail.com"), owners)["suspect"], False)
chk("CH match sharing no core word flags",
    audit_row(dict(clean, ch_official_name="MERIDIAN CATERING LIMITED"), owners)["suspect"], True)
chk("CH suffix words alone never flag",
    audit_row(dict(clean, ch_official_name="Plastometrex Technologies Ltd"), owners)["suspect"], False)
chk("empty row never crashes", audit_row({}, {})["suspect"], False)
# v2 refinements, from reading the first live audit (406 flags, 28 Aug 2026):
chk("fabricated placeholder email gets its own signal",
    "FABRICATED" in audit_row(dict(clean, contact_email="a@example.com"), owners)["signals"][0])
chk("company.com placeholder also caught",
    "FABRICATED" in audit_row(dict(clean, contact_email="ceo@company.com"), owners)["signals"][0])
chk("shared service domains (wix, onmicrosoft) never flag",
    audit_row(dict(clean, contact_email="x@wixpress.com"), owners)["suspect"], False)
chk("squashed CH name kills punctuation false positives (VU.CITY / VUCITY)",
    audit_row({"name": "VU.CITY", "ch_official_name": "VUCITY LIMITED"}, {})["suspect"], False)
chk("SailTies vs SAIL TIES LTD is the same brand",
    audit_row({"name": "SailTies", "ch_official_name": "SAIL TIES LTD"}, {})["suspect"], False)
chk("genuinely alien CH name still flags",
    audit_row({"name": "Hortis", "ch_official_name": "MERIDIAN CATERING LIMITED"}, {})["suspect"], True)

print()
print(f"{fails} FAILURES" if fails else "ALL PASS")
sys.exit(1 if fails else 0)
