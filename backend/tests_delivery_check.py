#!/usr/bin/env python3
"""
Tests for services/delivery_check.py — did the outreach actually land?

The stakes are asymmetric, and the tests are weighted accordingly:

  a MISSED bounce  -> we keep a dead address and chase a mailbox nobody reads
  a FALSE bounce   -> a live prospect is thrown back to Qualified and a working
                      founder address is wiped off the record

So the negatives here matter more than the positives. The single most dangerous
confusion is out-of-office versus bounce: both are automated, both arrive
seconds after we write, both are ABOUT our message. One means the address is
good and the human is away; the other means the address is dead.
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from services.delivery_check import (  # noqa: E402
    bounced_address, classify_delivery, is_bounce,
)

OURS = "beatrice@averroescapital.com"
fails = 0


def chk(label, got, want):
    global fails
    ok = got == want
    print(("PASS" if ok else "FAIL"), label, "->", got, "" if ok else f"(wanted {want})")
    if not ok:
        fails += 1


print("── Real bounces must be caught ──")
chk("Gmail address-not-found",
    is_bounce("Address not found",
              "Your message wasn't delivered to jane@acme.co.uk because the address "
              "couldn't be found, or is unable to receive mail.",
              from_addr="mailer-daemon@googlemail.com"), True)
chk("Postfix undelivered mail",
    is_bounce("Undelivered Mail Returned to Sender",
              "<jane@acme.co.uk>: host mx.acme.co.uk said: 550 5.1.1 User unknown",
              from_addr="MAILER-DAEMON@mail.acme.co.uk"), True)
chk("Exchange NDR with a DSN report",
    is_bounce("Undeliverable: Introduction",
              "Your message to jane@acme.co.uk couldn't be delivered. 550 5.4.1 "
              "Recipient address rejected: Access denied.",
              headers="Content-Type: multipart/report; report-type=delivery-status"), True)
chk("mailbox full",
    is_bounce("Delivery Status Notification (Failure)",
              "552 5.2.2 The email account that you tried to reach is over quota. "
              "Mailbox is full.",
              from_addr="mailer-daemon@googlemail.com"), True)
chk("domain does not resolve",
    is_bounce("Mail delivery failed: returning message to sender",
              "Domain not found. 550 5.1.2 We weren't able to find the recipient domain.",
              from_addr="Mail Delivery Subsystem <mailer-daemon@googlemail.com>"), True)
chk("gateway rejected as spam",
    is_bounce("Message blocked",
              "Our system has detected that this message was blocked it as spam. "
              "554 5.7.1 Message rejected.",
              from_addr="mailer-daemon@googlemail.com"), True)

print()
print("── An out-of-office is NOT a bounce (the dangerous confusion) ──")
chk("plain autoresponder",
    is_bounce("Automatic reply: Introduction",
              "I am out of the office until 15 September with limited access to email."), False)
# THE TRAP. Autoresponders routinely use delivery language: "your message could
# not be dealt with", "I am unable to respond". Without the out-of-office check
# running FIRST, phrase matching alone would bin a live prospect.
chk("autoresponder using delivery language",
    is_bounce("Out of Office",
              "I am on annual leave. Your message could not be delivered to me and "
              "will not be read until I return."), False)
chk("autoresponder with an RFC 3834 header",
    is_bounce("Abwesenheitsnotiz", "Ich bin im Urlaub.",
              headers="Auto-Submitted: auto-replied"), False)
chk("parental leave with a redirect",
    is_bounce("Automatic reply", "On parental leave. Please contact ops@acme.co.uk, "
                                 "as this mailbox does not exist for me right now."), False)

print()
print("── Genuine founder replies must never be touched ──")
chk("interested reply",
    is_bounce("Re: Introduction", "Thanks for reaching out, happy to chat next week."), False)
chk("a decline",
    is_bounce("Re: Introduction", "We're not looking to raise or sell right now."), False)
# Founders talk about their own business in words that overlap bounce prose.
chk("reply discussing a product that failed",
    is_bounce("Re: Introduction",
              "Our first product could not be delivered on time and the launch failed, "
              "but revenue has since tripled."), False)
chk("reply mentioning a full inbox",
    is_bounce("Re: Introduction", "Sorry for the delay, my mailbox is full of these."), False)
chk("a referral",
    is_bounce("Re: Introduction", "I'm not the right person, please contact our CFO."), False)

print()
print("── Temporary failures must NOT pull a company back ──")
# 4.x.x means try again later. The mail may still arrive, so demoting the company
# and wiping the address would be wrong.
chk("greylisted, 4.2.1",
    is_bounce("Delivery incomplete",
              "There was a temporary problem delivering your message. 4.2.1 Please try again.",
              from_addr="mailer-daemon@googlemail.com"), False)
chk("delayed, not failed",
    is_bounce("Delivery Status Notification (Delay)",
              "This is an automatically generated Delivery Status Notification. "
              "THIS IS A WARNING MESSAGE ONLY. 4.4.7 Message delayed.",
              from_addr="mailer-daemon@googlemail.com"), False)
# But a report carrying BOTH must be treated as permanent: the 5.x.x is the
# final verdict on that recipient.
chk("a delay warning that turned permanent",
    is_bounce("Undelivered Mail Returned to Sender",
              "4.4.7 delayed earlier; final status 5.1.1 user unknown",
              from_addr="mailer-daemon@googlemail.com"), True)

print()
print("── A daemon address alone is not a verdict ──")
# mailer-daemon also sends notices that have nothing to do with a failed send.
chk("quota warning about OUR mailbox",
    is_bounce("Your mailbox is almost full",
              "You are using 95% of your storage.",
              from_addr="mailer-daemon@googlemail.com"), False)

print()
print("── Which address died ──")
chk("reads Final-Recipient first",
    bounced_address(snippet="Delivery failed for everyone.",
                    headers="Final-Recipient: rfc822; jane@acme.co.uk",
                    exclude=OURS), "jane@acme.co.uk")
chk("X-Failed-Recipients",
    bounced_address(snippet="", headers="X-Failed-Recipients: bob@acme.co.uk",
                    exclude=OURS), "bob@acme.co.uk")
chk("falls back to the body",
    bounced_address(snippet="Your message to jane@acme.co.uk was not delivered.",
                    exclude=OURS), "jane@acme.co.uk")
# THE CATASTROPHIC CASE: a bounce quotes our own sending address. Clearing that
# would break sending for every company at once.
chk("never returns our own address",
    bounced_address(snippet=f"From: {OURS}\nTo: jane@acme.co.uk\nFailed.",
                    exclude=OURS), "jane@acme.co.uk")
chk("never returns the daemon's own address",
    bounced_address(snippet="mailer-daemon@googlemail.com could not deliver to jane@acme.co.uk",
                    exclude=OURS), "jane@acme.co.uk")
chk("no address to be found -> None, never a guess",
    bounced_address(snippet="Delivery to the following recipient failed permanently.",
                    exclude=OURS), None)
chk("angle brackets and a trailing colon are stripped",
    bounced_address(snippet="<jane@acme.co.uk>: host said 550", exclude=OURS),
    "jane@acme.co.uk")

print()
print("── classify_delivery end to end ──")
d = classify_delivery("Address not found",
                      "Your message wasn't delivered to jane@acme.co.uk because the "
                      "address couldn't be found.",
                      from_addr="mailer-daemon@googlemail.com", our_address=OURS)
chk("flags the bounce", d["is_bounce"], True)
chk("names the dead address", d["address"], "jane@acme.co.uk")
chk("records a reason", bool(d["reason"]), True)

ok = classify_delivery("Re: Introduction", "Happy to talk next week.", our_address=OURS)
chk("a real reply is clean", ok["is_bounce"], False)
chk("...and names no address", ok["address"], None)
chk("...and claims no reason", ok["reason"], "")

print()
print(f"{fails} FAILURES" if fails else "ALL PASS")
sys.exit(1 if fails else 0)
