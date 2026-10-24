"""Doctrine 2g: what the email chain teaches (codenames, advisers, invites).
Run: python3 tests_thread_signals.py"""
import inspect
from datetime import datetime

from services.thread_signals import (codenames_in, link_by_alias, merge_aliases, parse_aliases,
                                     is_adviser_address, invite_from_subject, invite_from_ics)
from ai.call_match import CompanyRef, Meeting, match_meeting


def test_codenames():
    assert codenames_in("Project Rise - Teaser Live.pdf") == ["Project Rise"]
    assert codenames_in("Re: Project Rise | NDA") == ["Project Rise"]
    assert codenames_in("Project update for the team; project plan attached") == []
    assert codenames_in("Project Glow team call and Project Falcon") == ["Project Glow", "Project Falcon"]


def test_adviser_address():
    assert is_adviser_address("chris@barnsgatesolutions.com", {"placecube.com"})
    assert not is_adviser_address("jason.fahy@placecube.com", {"placecube.com"})
    assert not is_adviser_address("bcarrara@averroescapital.com", {"placecube.com"})
    assert not is_adviser_address("founder@gmail.com", {"placecube.com"})
    # the company on a sister domain, or a same-named stranger, is never an adviser
    assert not is_adviser_address("andrew.thatcher@flagstoneim.com", {"flagstone.com"}, "Flagstone International")
    assert not is_adviser_address("kieran@realtafusion.com", {"realtarisk.com"}, "Realta")
    assert is_adviser_address("chris@barnsgatesolutions.com", {"placecube.com"}, "Placecube")


def test_merge_aliases():
    a, changed = merge_aliases(None, ["Project Rise"], ["chris@barnsgatesolutions.com"])
    assert changed and a["codenames"] == ["Project Rise"] and a["adviser_domains"] == ["barnsgatesolutions.com"]
    a2, changed2 = merge_aliases(a, ["project rise"], ["CHRIS@barnsgatesolutions.com"])
    assert not changed2, "case-insensitive union"
    assert parse_aliases("not json")["codenames"] == []


def test_fourth_rung_new_chain_by_adviser():
    cn = {"project rise": "Placecube"}
    adv = {"chris@barnsgatesolutions.com": ["Placecube"], "busy@bigbank.com": ["Placecube", "Zutec"]}
    # the adviser starts a different chain: codename in the subject links it
    assert link_by_alias("Project Rise: next steps", "", "someone@else.com", cn, adv) == ("Placecube", "codename")
    # adviser names the company in the body
    assert link_by_alias("Catch up", "Placecube would like a call next week", "chris@barnsgatesolutions.com", cn, adv) == ("Placecube", "adviser")
    # adviser for one company, names nothing: linked but flagged
    assert link_by_alias("Quick one", "Are you free Thursday?", "chris@barnsgatesolutions.com", cn, adv) == ("Placecube", "adviser-unconfirmed")
    # adviser on several deals, names nothing: never on the address alone
    assert link_by_alias("Quick one", "Are you free Thursday?", "busy@bigbank.com", cn, adv) == (None, "")
    assert link_by_alias("Quick one", "Zutec term sheet", "busy@bigbank.com", cn, adv) == ("Zutec", "adviser")


def test_invites():
    g = invite_from_subject("Invitation: Placecube <> Averroes @ Tue 28 Oct 2026 3pm - 3:30pm (BST) (bcarrara@averroescapital.com)")
    assert g and g["status"] == "booked" and g["start"] == datetime(2026, 10, 28, 15, 0, tzinfo=g["start"].tzinfo)
    assert g["title"].startswith("Placecube <> Averroes")
    c = invite_from_subject("Canceled event: Placecube <> Averroes @ Tue 28 Oct 2026 3pm")
    assert c and c["status"] == "cancelled"
    assert invite_from_subject("Re: Averroes Capital, Placecube") is None
    ics = "BEGIN:VCALENDAR\nMETHOD:REQUEST\nBEGIN:VEVENT\nDTSTART;TZID=Europe/London:20261028T150000\nSUMMARY:Project Rise intro\nEND:VEVENT\nEND:VCALENDAR"
    i = invite_from_ics(ics)
    assert i["status"] == "booked" and i["start"].day == 28 and i["title"] == "Project Rise intro"


def test_granola_uses_codename_and_adviser():
    pc = CompanyRef("Placecube", website="placecube.com", status="Responded",
                    codenames=["Project Rise"], adviser_domains=["barnsgatesolutions.com"])
    # the adviser's call, titled by the codename: a meeting WITH the deal side
    m = Meeting("1", title="Project Rise - intro call", participant_emails=["chris@barnsgatesolutions.com"], body="")
    assert [(x.company, x.confidence) for x in match_meeting(m, [pc])] == [("Placecube", "title")]
    # adviser present, codename only in the notes: promotion-grade
    m2 = Meeting("2", title="Catch up", participant_emails=["chris@barnsgatesolutions.com"], body="Project Rise: they want a call.")
    assert [(x.company, x.confidence) for x in match_meeting(m2, [pc])] == [("Placecube", "domain")]
    # adviser present, deal not named: weak, a person decides
    m3 = Meeting("3", title="Catch up", participant_emails=["chris@barnsgatesolutions.com"], body="general chat")
    assert [(x.company, x.confidence) for x in match_meeting(m3, [pc])] == [("Placecube", "weak")]


def test_unconfirmed_link_is_never_a_reply():
    from storage import bq_handler as b
    assert "unconfirmed_link" in b.BigQueryHandler.NON_REPLY_CLASSES
    import main
    src = inspect.getsource(main._sync_emails_impl)
    assert 'matched_by") == "adviser-unconfirmed"' in src and '"unconfirmed_link"' in src


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
