#!/usr/bin/env python3
"""
Tests for contact adoption: the send is the truth about the contact.

Ishu verifies the recipient address and the greeting before every send. When he
corrects either, that correction must flow back into the stored contact - both
live (the send path) and retroactively (/admin/contacts/sync-from-sends). Both
paths share the two pure functions tested here.

The asymmetry that shapes every rule: a MISSED update costs nothing (the stored
contact just stays as the waterfall guessed), but a WRONG update corrupts a
verified contact. So every doubtful case must return "no change".
"""
import os
import sys
import warnings

warnings.filterwarnings("ignore")
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
os.environ.setdefault("GCP_PROJECT_ID", "averroes-deal-origination")

from main import contact_adoption, greeting_name  # noqa: E402

fails = 0


def chk(label, got, want):
    global fails
    ok = got == want
    print(("PASS" if ok else "FAIL"), label, "->", got, "" if ok else f"(wanted {want})")
    if not ok:
        fails += 1


print("── Reading the greeting ──")
chk("Hi James,", greeting_name("Hi James,\n\nI came across Acme..."), "James")
chk("Hello Sarah", greeting_name("Hello Sarah\n\nbody"), "Sarah")
chk("Dear Dr-less full name", greeting_name("Dear Jane Smith,\n\nbody"), "Jane Smith")
chk("Hey with exclamation", greeting_name("Hey Tom!\nbody"), "Tom")
chk("Good morning Priya", greeting_name("Good morning Priya,\nbody"), "Priya")
chk("leading blank lines are fine", greeting_name("\n\nHi James,\nbody"), "James")
chk("apostrophe surname", greeting_name("Hi Aoife O'Brien,\nbody"), "Aoife O'Brien")

print()
print("── What is NOT a person ──")
chk("Hi there is nobody", greeting_name("Hi there,\n\nbody"), "")
chk("Hi team is nobody", greeting_name("Hi team,\nbody"), "")
chk("Hello everyone is nobody", greeting_name("Hello everyone,\nbody"), "")
chk("Dear Sir is nobody", greeting_name("Dear Sir,\nbody"), "")
chk("no greeting at all", greeting_name("Quick one - congrats on the raise.\nbody"), "")
chk("empty body", greeting_name(""), "")
# The greeting must be the FIRST line. A 'Hi X' quoted deeper in the body (for
# example inside a forwarded thread) must never be read as the recipient.
chk("greeting buried mid-body is ignored",
    greeting_name("Sharing the note below.\n\nHi James,\nold thread"), "")
# Lowercase after the greeting word is not a confident name.
chk("lowercase word is not a name", greeting_name("hi again,\nbody"), "")

print()
print("── Adopting the email ──")
chk("a changed address is adopted",
    contact_adoption("old@acme.co.uk", "James Smith", "new@acme.co.uk", "")["email"],
    "new@acme.co.uk")
chk("same address, no change",
    contact_adoption("a@acme.co.uk", "James", "a@acme.co.uk", "")["email"], None)
chk("case and whitespace do not fake a change",
    contact_adoption("A@Acme.co.uk", "James", "  a@acme.co.uk ", "")["email"], None)
chk("no stored address at all is adopted",
    contact_adoption("", "James", "ceo@acme.co.uk", "")["email"], "ceo@acme.co.uk")
chk("a known-bounced address is never re-adopted",
    contact_adoption("good@acme.co.uk", "James", "dead@acme.co.uk", "",
                     bounced_email="dead@acme.co.uk")["email"], None)
chk("the test row never adopts anything",
    contact_adoption("real@acme.co.uk", "James", "admin@averroescapital.com", "Bob",
                     is_test=True), {"email": None, "name": None})

print()
print("── Adopting the name ──")
chk("a different person replaces the name",
    contact_adoption("a@x.com", "James Smith", "a@x.com", "Sarah")["name"], "Sarah")
# THE RULE THAT PROTECTS SURNAMES: the greeting only carries a first name, so a
# matching first name is the SAME person and the stored full name must survive.
chk("same first name keeps the stored full name",
    contact_adoption("a@x.com", "James Smith", "a@x.com", "James")["name"], None)
chk("first-name match is case-insensitive",
    contact_adoption("a@x.com", "james smith", "a@x.com", "James")["name"], None)
chk("no greeting, no name change",
    contact_adoption("a@x.com", "James Smith", "a@x.com", "")["name"], None)
chk("a name where none was stored is adopted",
    contact_adoption("a@x.com", "", "a@x.com", "Sarah")["name"], "Sarah")
chk("full name in the greeting is kept whole",
    contact_adoption("a@x.com", "James Smith", "a@x.com", "Jane Doe")["name"], "Jane Doe")

print()
print("── Both together: the real correction scenario ──")
# Ishu retargets the email from a generic inbox to the founder and fixes the
# greeting in the same edit. Both must be adopted in one pass.
got = contact_adoption("hello@acme.co.uk", "", "james@acme.co.uk", "James")
chk("email adopted", got["email"], "james@acme.co.uk")
chk("name adopted", got["name"], "James")

print()
print(f"{fails} FAILURES" if fails else "ALL PASS")
sys.exit(1 if fails else 0)
