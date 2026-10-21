"""Call notes: the PURE matcher (ai/call_match.py) and the shape of the sync.
Run: python3 tests_call_match.py
"""
import inspect

from ai.call_match import CompanyRef, Meeting, match_meeting, domain_of, core_name

CYJAX = CompanyRef("Cyjax", website="https://www.cyjax.com", contact_email="ian@cyjax.com", status="Responded")
BOUNCE = CompanyRef("Bounce", website="https://bounce.io", status="Responded")
ARCUS = CompanyRef("Arcus Global Ltd", website="arcusglobal.com", status="Meeting")
PLAST = CompanyRef("Plastometrex", website="plastometrex.com", status="Responded")
BOOK = [CYJAX, BOUNCE, ARCUS, PLAST]


def strong(ms):
    return [(m.company, m.confidence) for m in ms if m.confidence in ("domain", "title", "body")]


def test_domain_helpers():
    assert domain_of("https://www.Foo.co.uk/x") == "foo.co.uk"
    assert domain_of("a@Foo.co.uk") == "foo.co.uk"
    assert core_name("Arcus Global Ltd.") == "arcus global"
    assert core_name("Cyjax") == "cyjax"


def test_adviser_on_the_call_does_not_match_the_adviser():
    m = Meeting("1", title="Averroes Capital >< Cavendish (Cyjax)",
                participant_emails=["iratna@averroescapital.com", "tstoten@cavendish.com"],
                body="Tom walked us through the Cyjax process.")
    assert strong(match_meeting(m, BOOK)) == [("Cyjax", "title")]


def test_participant_domain_beats_everything():
    m = Meeting("2", title="Intro call", participant_emails=["ceo@cyjax.com", "iratna@averroescapital.com"], body="")
    assert strong(match_meeting(m, BOOK)) == [("Cyjax", "domain")]


def test_project_x_title_is_read_from_the_body():
    # Ishu, 15 Oct 2026: companies are often anonymised as "Project X".
    m = Meeting("3", title="Project Falcon - team call",
                participant_emails=["iratna@averroescapital.com", "izeraidi@averroescapital.com"],
                body="## Summary\nPlastometrex is raising; founders want to keep control. Plastometrex revenue GBP 2.1m.")
    assert strong(match_meeting(m, BOOK)) == [("Plastometrex", "body")]


def test_generic_one_word_name_needs_more_than_one_mention():
    m = Meeting("4", title="Project Glow team call", participant_emails=[],
                body="Lots of bounce in the numbers this quarter; discussed pricing.")
    ms = match_meeting(m, BOOK)
    assert strong(ms) == []
    assert [(x.company, x.confidence) for x in ms] == [("Bounce", "weak")]
    # even as a proper noun twice, a dictionary-word name stays weak: the
    # sync lists it for a person and never writes it (21 Oct 2026, "Send").
    m2 = Meeting("5", title="Project Glow team call", participant_emails=[],
                 body="We met Bounce on Monday, and Bounce want a term sheet by Friday.")
    assert strong(match_meeting(m2, BOOK)) == []
    assert [(x.company, x.confidence) for x in match_meeting(m2, BOOK)] == [("Bounce", "weak")]
    # title or attendee domain is how such a company gets matched
    m3 = Meeting("5b", title="Bounce <> Averroes", participant_emails=[], body="")
    assert strong(match_meeting(m3, BOOK)) == [("Bounce", "title")]


def test_no_substring_matches():
    # "arcus" inside "Marcus" must not match Arcus Global.
    m = Meeting("6", title="Catch up with Marcus", participant_emails=[], body="Marcus is well. arcusglobal was not mentioned.")
    assert strong(match_meeting(m, BOOK)) == []


def test_public_mail_domain_is_not_a_company():
    gm = CompanyRef("Gmail Shop", website="", contact_email="shop@gmail.com", status="Responded")
    m = Meeting("7", title="x", participant_emails=["someone@gmail.com"], body="")
    assert strong(match_meeting(m, [gm])) == []


def test_sync_rules_in_source():
    from services import call_notes_sync as s
    src = inspect.getsource(s)
    assert s.PROMOTE_FROM == "Responded" and s.PROMOTE_TO == "Meeting"
    assert "update_company_status" in src, "promotion must go through the one status writer"
    assert "weak" not in s.STRONG, "a weak match is report-only"
    assert "event_time=" in src, "the activity row carries the MEETING time (doctrine 3)"
    assert "dry_run: bool = True" in src, "dry run by default"
    # Only Responded and later are ever candidates, computed in SQL once.
    from storage import bq_handler as b
    bsrc = inspect.getsource(b.BigQueryHandler.call_note_candidates)
    assert "'Responded', 'Meeting', 'DD', 'Offer', 'Won', 'Lost'" in bsrc
    assert "Internal Test" in bsrc




def test_dictionary_word_names_never_match_on_body():
    # 21 Oct 2026: "Send" matched eleven internal meetings on "send the deck".
    send = CompanyRef("Send", website="send.co", status="Responded")
    sess = CompanyRef("Sessions", website="sessions.io", status="Responded")
    m = Meeting("8", title="Weekly Cashflow update", participant_emails=[],
                body="Send the deck to Bea. We should send the model too. Sessions with the team were long.")
    assert strong(match_meeting(m, [send, sess])) == []
    # named mid-sentence twice as a proper noun: a real reference, but still
    # only weak for a common word; a distinctive one-word name passes.
    zut = CompanyRef("Zutec", website="zutec.com", status="Responded")
    m2 = Meeting("9", title="Project Glow", participant_emails=[],
                 body="Zutec update: they want a term sheet.")
    assert strong(match_meeting(m2, [zut])) == [("Zutec", "body")]
    m3 = Meeting("10", title="Project Glow", participant_emails=[],
                 body="We met Send on Monday, and Send want a term sheet.")
    assert strong(match_meeting(m3, [send])) == []


def test_isp_mailbox_is_not_a_company_domain():
    rg = CompanyRef("ReadyGo (Diagnostic Equipment)", contact_email="bob@btinternet.com", status="Lost")
    m = Meeting("11", title="Glacier Finance Weekly Catch up", participant_emails=["bob@btinternet.com"], body="")
    assert strong(match_meeting(m, [rg])) == []


def test_parked_companies_are_never_promoted():
    from services import call_notes_sync as s
    src = inspect.getsource(s)
    assert 'in ("kill", "later")' in src and "m.company not in parked" in src


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
