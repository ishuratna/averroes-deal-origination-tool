"""
Outreach Service
Generates personalised PE/growth capital outreach emails using Gemini AI.
Uses company data already saved in BQ (from SmartFill) - no extra Google Search calls.
"""
import os
import json
import smtplib
import logging
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
from typing import Dict, Optional
from datetime import datetime

logger = logging.getLogger(__name__)

# ── Sender config (env vars on Cloud Run) ──────────────────────────────────────
SENDER_EMAIL = os.getenv("OUTREACH_EMAIL", "beatrice@averroescapital.com")
SENDER_NAME = os.getenv("OUTREACH_NAME", "Beatrice Carrara")
SMTP_PASSWORD = os.getenv("OUTREACH_SMTP_PASSWORD", "")  # Gmail App Password


# Portfolio proof points - real Averroes investments, referenced in outreach.
# Keep factual and plain; the prompt forbids hype around them.
PORTFOLIO_PROOF = (
    "Averroes has backed companies including Glowday and Journey. "
    "Both have grown strongly since we invested, with our capital and "
    "hands-on operating support."
)


def find_news_hook(company_name: str, website: str = "") -> str:
    """
    One grounded Gemini search for a SPECIFIC, RECENT signal about the company
    (last ~60 days): product launch, award, senior hire, customer win, funding.
    Returns a short factual sentence or "" - never invents.
    Costs 1 grounded call; the caller enforces the daily budget.
    """
    api_key = os.getenv("GEMINI_API_KEY")
    if not api_key or not company_name:
        return ""
    try:
        from google import genai
        from google.genai.types import GenerateContentConfig, GoogleSearch, Tool

        client = genai.Client(api_key=api_key)
        prompt = f"""Search for recent news (last 60 days) about the company "{company_name}"{f' ({website})' if website else ''}.

Look for ONE specific, verifiable item a private equity partner could naturally
mention when writing to the founder: a product launch, award, notable customer,
senior hire, partnership, or funding announcement.

Return ONLY valid JSON: {{"found": true/false, "hook": "one plain factual sentence with the specific item and rough timing, or empty", "source": "publication/site name or empty"}}

Rules: only items you actually found via search about THIS company. If nothing
specific and recent, return found=false. Never guess."""
        response = client.models.generate_content(
            model="gemini-2.5-flash",
            contents=prompt,
            config=GenerateContentConfig(tools=[Tool(google_search=GoogleSearch())], temperature=0.2),
        )
        text = (response.text or "").strip()
        if text.startswith("```"):
            text = text.split("\n", 1)[1].rsplit("```", 1)[0].strip()
        start, end = text.find("{"), text.rfind("}")
        result = json.loads(text[start:end + 1]) if start != -1 else {}
        if result.get("found") and result.get("hook"):
            hook = result["hook"].strip()
            src = (result.get("source") or "").strip()
            logger.info(f"[Outreach] News hook for '{company_name}': {hook[:80]}")
            return f"{hook}" + (f" (via {src})" if src else "")
        return ""
    except Exception as e:
        logger.warning(f"[Outreach] News lookup failed for '{company_name}': {e}")
        return ""


def _greeting_for(company_data: Dict) -> tuple:
    """Who this email opens by name, given who the To: actually belongs to.

    The contact waterfall does not always land on the founder, so the greeting
    follows the recipient, not the record:
      colleague     greet the colleague by their OWN name (writing "Hi Sarah"
                    to Tom is the fastest way to get deleted)
      shared inbox  greet the FOUNDER by name (info@ is read by someone whose
                    job is to pass it on, and the email is for the founder)
      founder       greet the founder, as before
      nobody named  no name at all, "Hello,"
    Returns (first_name_or_blank, instruction_for_the_model).
    """
    contact_name = (company_data.get("contact_name") or "").strip()
    kind = (company_data.get("contact_email_kind") or "").strip()
    recipient = (company_data.get("contact_email_name") or "").strip()

    if kind == "colleague":
        first = recipient.split()[0] if recipient else ""
    else:
        first = contact_name.split()[0] if contact_name else ""

    if not first:
        return "", '"Hello," on its own line. No name is known, so do NOT invent one.'
    return first, f'"Hi {first}," on its own line.'


def _recipient_note(company_data: Dict) -> str:
    """One line telling the model who is on the other end of the To: field."""
    kind = (company_data.get("contact_email_kind") or "").strip()
    recipient = (company_data.get("contact_email_name") or "").strip()
    founder = (company_data.get("contact_name") or "").strip()
    if kind == "colleague":
        who = recipient or "a person who works there"
        return (f"{who}, who works at the company but is NOT the founder. "
                f"Write to them directly and warmly. Do not ask them to forward "
                f"anything and do not mention the founder by name.")
    if kind == "generic":
        return ("the company's shared enquiries inbox, so whoever reads it will "
                f"pass it on. Address it to {founder or 'the founder'}.")
    return f"{founder or 'the founder'} directly."


def draft_followup_email(company_data: Dict) -> Dict[str, str]:
    """The 14-day follow-up. A fixed template, zero AI calls.

    Wording approved by Ishu (18 Aug 2026). Deliberately NOT model-generated:
    the first email carries the personalisation, and a follow-up that suddenly
    knows new things about the company reads as automated. A short, identical,
    human nudge is the point.

    Replies in the SAME THREAD: the subject is "Re:" plus the original subject,
    so the first email sits directly underneath and the founder needs no
    context. Ends with "Best," and no name, because send_email appends the
    full signature beneath it, exactly like the first email.
    """
    first, _ = _greeting_for(company_data)
    greeting = f"Hi {first}," if first else "Hello,"
    company = company_data.get("name", "your company")

    original_subject = (company_data.get("outreach_draft_subject") or "").strip()
    if original_subject:
        subject = original_subject if original_subject.lower().startswith("re:") \
            else f"Re: {original_subject}"
    else:
        subject = f"Averroes Capital, {company}"

    body = (
        f"{greeting}\n\n"
        "I wanted to come back to my note from a couple of weeks ago. I know how "
        "busy things get, so no concern at all if it slipped past.\n\n"
        "In short, we are Averroes Capital, a growth equity investor in UK software "
        f"companies, and {company} stood out to us. I would love to hear how you "
        "think about the business and where it is heading.\n\n"
        "If now is not the right time, that is completely understood. I would be "
        "glad to stay in touch either way.\n\n"
        "Best,"
    )
    return {"to": company_data.get("contact_email", ""), "subject": subject,
            "body": body, "company": company_data.get("name", "")}


def draft_outreach_email(company_data: Dict, news_hook: str = "") -> Dict[str, str]:
    """
    Use Gemini to draft a personalised outreach email from stored BQ data,
    plus an optional recent-news hook (found separately - see find_news_hook).
    Returns: {"subject": "...", "body": "...", "to": "..."}
    """
    api_key = os.getenv("GEMINI_API_KEY")
    if not api_key:
        return _fallback_template(company_data)

    name = company_data.get("name", "")
    contact_name = company_data.get("contact_name", "")
    contact_email = company_data.get("contact_email", "")
    description = company_data.get("description", "")
    sector = company_data.get("sector", "")
    region = company_data.get("region", "")
    website = company_data.get("website", "")
    ownership = company_data.get("ownership", "")
    employees = company_data.get("employees", "")
    revenue_m = company_data.get("revenue_m", "")
    year_founded = company_data.get("year_founded", "")
    keywords = company_data.get("keywords", "")
    financing_status = company_data.get("financing_status", "")

    # Build context block from BQ data
    context_parts = []
    if description:
        context_parts.append(f"Description: {description}")
    if sector:
        context_parts.append(f"Sector: {sector}")
    if region:
        context_parts.append(f"Region: {region}")
    if ownership:
        context_parts.append(f"Ownership: {ownership}")
    if employees:
        context_parts.append(f"Employees: {employees}")
    if revenue_m:
        context_parts.append(f"Revenue: £{revenue_m}M")
    if year_founded:
        context_parts.append(f"Founded: {year_founded}")
    if keywords:
        context_parts.append(f"Keywords: {keywords}")
    if financing_status:
        context_parts.append(f"Financing: {financing_status}")
    if website:
        context_parts.append(f"Website: {website}")

    company_context = "\n".join(context_parts) if context_parts else f"Company: {name}"

    first_name, greeting_instruction = _greeting_for(company_data)

    # Data richness decides the mode: with substance we personalise;
    # with a thin record we write a shorter, plainer note and fake nothing.
    rich_signals = sum(bool(x) for x in [description, sector, keywords, year_founded, employees, financing_status])
    thin_data = rich_signals < 2

    length_rule = (
        "90-115 words. You know little about this company, so keep the follow + "
        "conviction paragraph (structure item 4) to one honest sentence and lean "
        "on who Averroes is."
        if thin_data else
        "120-160 words. Spend the extra length on the follow + conviction "
        "paragraph (structure item 4): specificity there is what separates this "
        "email from every other investor's."
    )

    prompt = f"""
    You are Beatrice Carrara, Partner at Averroes Capital, a London-based growth equity
    investor focused on founder-led technology businesses. Averroes typically gets involved
    where a company has a proven product and is looking at the next stage of growth.

    Write an outreach email to {contact_name or 'the founder'} at {name}. You are writing as
    yourself, an experienced investor a founder would want to hear from, not as a marketer.

    WHO IS RECEIVING IT: {_recipient_note(company_data)}
    Whoever opens it, the email itself never changes: same structure, same
    substance, no ask. Only the greeting follows the recipient.

    COMPANY NAME: the record name above may be a legal or registry name. Everywhere the
    company appears in the email, including the subject line, use the natural name a person
    would say out loud. Strip legal suffixes and registry noise (Ltd, Limited, PLC, LLP,
    Inc, Corp, Co., GmbH, and similar) and fix shouty registry casing: "FIDO TECH LTD"
    becomes "Fido Tech". Keep genuine brand styling when it is clearly the brand (e.g.
    "iPlato" stays "iPlato"). Use your judgement; never write the legal suffix in the email.

    WHAT WE KNOW ABOUT THE COMPANY (do not use anything beyond this):
    {company_context}

    {f'RECENT SIGNAL (verified, you may use it in structure item 4): {news_hook}' if news_hook else 'RECENT SIGNAL: none found. Do NOT invent one; ground item 4 in the company data instead.'}

    EMAIL STRUCTURE (follow this exact order and paragraphing; vary the
    wording naturally where noted, never the order):

    1. GREETING, use exactly this: {greeting_instruction}
    2. PLEASANTRY, own line: "Hope you are having a great day." (light
       variations fine: "Hope your week is going well.")
    3. WHO I AM, own paragraph, comes BEFORE anything about their company:
       "I am a Partner at Averroes Capital, a London-based growth equity
       investor focused on founder-led technology businesses. We typically
       get involved where a company has a proven product and is looking at
       the next stage of growth."
    4. FOLLOW + CONVICTION, own paragraph, STRICTLY 2 sentences, both written
       from Averroes' point of view as an investor watching the space, never
       as someone explaining the company to its own founder.
       Sentence one, the observer stance: "We have been following {name} for
       some time and really like <the specific thing that caught our eye:
       their approach, a product choice, how they serve a market>." One
       concrete detail from the data, framed as what we like, not as a
       description of their business.
       Sentence two, our conviction: "We believe <the problem> is a real pain
       point for <who suffers it> and the potential in solving it is huge."
       State it as our belief (we believe / we think), tied to why an
       investor cares: big market, real pain, underserved.
       Example of the move: "We have been following BookingX for some time
       and really like the way you give hotels direct-channel pricing tools.
       We believe hotels losing direct bookings to OTAs is a real pain point
       and the potential in fixing it is huge."
       Ground everything in the data or the recent signal. If the data is
       thin, keep this to one honest sentence.
    5. PORTFOLIO PROOF, own paragraph: "At Averroes, we have backed similar
       companies such as Journey and Glowday, and helped them scale with
       hands-on operational support alongside capital." (Use only these
       facts. NEVER invent fund sizes, AUM figures, or any numbers.)
    6. HUMILITY + OPEN DOOR, own paragraph. There is NO ask in this email
       beyond an open invitation to a dialogue. Nothing about calls,
       meetings, documents, numbers or details: "I appreciate this may not
       be a priority right now, but we would love to start a dialogue, hear
       more about your ambitions for the future, and get to introduce you to
       Averroes." (Vary the wording lightly and naturally; the substance
       never changes: start a dialogue, their ambitions, introduce Averroes.
       No timing pressure anywhere, so a founder never has to say no just
       because of timing.)
    7. CLOSING LINE, own line: "Look forward to hearing from you."
    8. SIGN-OFF: end the body with exactly "Best," on its own line and NOTHING
       after it. Do not write a name. The full signature (Maria Beatrice
       Carrara, Partner, phone, email) is appended automatically on send.

    LENGTH & OPENING: {length_rule}

    HOW A REAL PE PARTNER WRITES (follow all of these):
    - Plain English. Write like you talk. Short, common words: help, build, grow, run, talk.
      If a sentence needs reading twice, rewrite it.
    - Plain, confident, understated. Short sentences. One idea per sentence.
    - Tone: warm, low-pressure, genuinely curious. You are opening a relationship,
      not making an offer. No urgency tricks anywhere.
    - State a reason for writing that is true: their profile fits what we invest in.
    - Sign off exactly as "Best," alone, per structure item 9. Never add a name.

    HARD RULES (the email fails review if it breaks any of these):
    1. NEVER invent facts, numbers, achievements or "news" about the company. If the data
       doesn't say it, the email doesn't say it.
    2. NEVER quote their financial figures back at them (revenue, headcount, funding).
       citing a founder's own numbers in a cold email reads as surveillance, not diligence.
       Use the data only to inform what you choose to say.
    3. Banned phrases and patterns: "I hope this email finds you well",
       "I couldn't help but notice", "I was impressed by", "exciting journey", "resonated",
       "cutting-edge", "revolutionary", "game-changing", "reach out" (as a noun or verb),
       "touch base", "synergies". No exclamation marks. No lists of three adjectives.
    3a. NO em dashes or hyphens used as pauses, anywhere. Not in the subject, not in the
       body. Use a comma, a full stop, or start a new sentence instead.
    3b. No business jargon. Banned words: "leverage", "utilise"/"utilize", "ecosystem",
       "value creation", "deploy capital", "proprietary", "best-in-class", "world-class",
       "streamline", "scalable", "robust", "holistic", "strategic fit", "unlock".
       Say the plain version: "use" not "leverage", "grow" not "scale up the business".
    4. No flattery that isn't earned by a specific data point. Respect reads better than praise.
    5. Subject line: specific and quiet, like a person wrote it (e.g. "Averroes Capital, {name}"
       or a plain reference to their space). Never clickbait, never "Quick question".
    6. Do NOT include email headers (To/From/Date), and never mention databases, research
       tools, or how you found them.
    7. NEVER mention a meeting, call, or calendar in any form in this first email.
       No "20-minute call", no "set up a call", no "next couple of weeks". Also NEVER
       ask for company details, financials, metrics, decks or documents. Requests for
       an overview come later, only after the founder shows interest (they will likely
       want an NDA first). The ONLY invitation in this email is the open door in
       structure item 6.

    VARIETY: Do not follow a template. Vary the opening line, sentence rhythm and structure
    from other emails you might write. Two founders comparing notes should not see the same
    skeleton. The compliment-positioning-CTA formula is a template; avoid it.

    Return ONLY valid JSON with exactly these keys:
    {{"subject": "email subject line", "body": "full email body text"}}

    The body should use \\n for line breaks between paragraphs.
    """

    try:
        from google import genai
        from google.genai.types import GenerateContentConfig

        client = genai.Client(api_key=api_key)

        # No Google Search - just Gemini with the data we already have.
        # Higher temperature for structural variety between drafts.
        # Two attempts: transient errors (rate limits, empty responses) were
        # silently producing fallback-template drafts that read short and vague.
        import time as _time
        last_err = None
        for attempt in range(2):
            try:
                response = client.models.generate_content(
                    model="gemini-2.5-flash",
                    contents=prompt,
                    config=GenerateContentConfig(temperature=1.0),
                )
                text = (response.text or "").strip()
                if not text:
                    raise ValueError("empty response")
                if text.startswith("```"):
                    text = text.split("\n", 1)[1].rsplit("```", 1)[0].strip()
                result = json.loads(text)
                if not result.get("body"):
                    raise ValueError("no body in response")
                return {
                    "subject": result.get("subject", f"Averroes Capital, {name}"),
                    "body": result.get("body", ""),
                    "to": contact_email or "",
                    "contact_name": contact_name or "",
                    "company": name,
                }
            except Exception as e:
                last_err = e
                logger.warning(f"Outreach draft attempt {attempt + 1} failed for {name}: {e}")
                _time.sleep(2)
        logger.error(f"Outreach draft generation failed for {name} after retries: {last_err}")
        return _fallback_template(company_data)
    except Exception as e:
        logger.error(f"Outreach draft generation failed for {name}: {e}")
        return _fallback_template(company_data)


# ── Investor (LP) outreach: structure v2 ─────────────────────────────────────
# Per Ishu (9 Sep 2026): an INVITATION to talk, human, no placeholders.
#   1. "Hi {first},"
#   2. Who writes and who we are: investor relations at Averroes Capital (or a
#      Partner when Bea's mailbox sends); a London-based technology investor
#      taking growth equity or significant and majority positions in software
#      and tech companies, primarily UK and Ireland.
#   3. WHY THEM: one specific, personal line from what we hold (co-investment
#      appetite, tech exposure, a company we both know). Never generic praise.
#   4. How we invest: deal by deal alongside a pool of investors, many of whom
#      have backed us across more than one round, now widening that circle;
#      collaborative, alongside management teams; Glowday and Journey have
#      delivered strong returns for the investors who came in with us.
#   5. The invitation: share our philosophy, no expectation beyond a
#      conversation; a short call in the coming weeks, or a short note first.
#   6. "Best," and the signature (added on send).
# ZERO em dashes or en dashes anywhere. Structure changes happen HERE only.
AVERROES_LP_POSITIONING = (
    "We are a technology investor based in London, taking growth equity or significant and "
    "majority positions in software and tech companies, primarily in the UK and Ireland.")
AVERROES_LP_MODEL = (
    "We invest deal by deal alongside a pool of investors, many of whom have backed us across more "
    "than one round, and we are now widening that circle. Our approach is collaborative: we work "
    "alongside the management teams we back rather than around them, and the companies we have "
    "invested in, Glowday and Journey among them, have delivered strong returns for the investors "
    "who came in with us.")
AVERROES_LP_INVITATION = (
    "I would enjoy sharing our philosophy and how we work with our investors, with no expectation "
    "beyond a conversation. Would you be open to a short call in the coming weeks? If it is easier, "
    "I can send a short note on Averroes first.")


def _lp_role_line(prof: Dict) -> str:
    """Who is writing, kept true to the mailbox that sends: Ishu's investor
    relations line when the investor mailbox is configured, a Partner line
    while Bea's mailbox is the fallback."""
    if prof.get("fallback") or (prof.get("sig_title") or "").lower().startswith("partner"):
        return "I am a Partner at Averroes Capital."
    return "I look after investor relations at Averroes Capital."


def draft_lp_outreach_email(investor: Dict) -> Dict[str, str]:
    """Draft the LP invitation (structure v2). The only AI-written part is the
    personal WHY THEM line; the positioning, model and invitation are fixed
    house copy. No Google Search. Falls back to a fully fixed template."""
    api_key = os.getenv("GEMINI_API_KEY")
    prof = sender_profile("investor")
    role_line = _lp_role_line(prof)

    name = investor.get("name", "")
    contact_name = investor.get("contact_name", "")
    contact_email = investor.get("contact_email", "")
    first = contact_name.split()[0] if contact_name.strip() else ""
    greeting = f"Hi {first}," if first else "Hello,"

    context_parts = []
    for label, key in [
        ("Type", "investor_type"), ("Description", "description"),
        ("HQ", "hq_country"), ("AUM (USD m)", "aum_m"),
        ("PE strategy preferences", "strategy_preferences"),
        ("Geographic mandate", "geo_preferences"),
        ("Open to first-time funds", "open_to_first_time"),
        ("PE fund commitments", "num_pe_commitments"),
        ("Companies in our universe they have backed", "source_companies"),
        ("Contact title", "contact_title"),
        ("Policy", "policy_description"),
        ("Network tags", "network_tags"),
    ]:
        val = investor.get(key)
        if val not in (None, ""):
            context_parts.append(f"{label}: {val}")
    lp_context = "\n".join(context_parts) if context_parts else f"Investor: {name}"

    subject = f"Averroes Capital, {name}"

    def _assemble(why_them: str) -> str:
        parts = [greeting, "", f"{role_line} {AVERROES_LP_POSITIONING}", ""]
        if why_them:
            parts += [why_them, ""]
        parts += [AVERROES_LP_MODEL, "", AVERROES_LP_INVITATION, "", "Best,"]
        return "\n".join(parts)

    generic_why = (f"Given {name}'s activity in private markets, I thought an introduction might be of interest."
                   if name else "")
    fallback = {"subject": subject, "body": _assemble(generic_why), "to": contact_email or "",
                "contact_name": contact_name or "", "investor": name,
                "from": sender_label("investor"), "is_fallback": True}
    if not api_key:
        return fallback

    prompt = f"""You write ONE sentence, at most two, for an email from Averroes Capital (a London-based
technology investor, growth equity and majority positions in UK and Irish software companies,
investing deal by deal with a pool of co-investors) to {contact_name or 'the principal'} at {name},
a potential co-investor.

Write the WHY THEM sentence only: something specific and true from the intelligence below that
explains why we are writing to them in particular. Prefer, in this order: a company we both know
(they have backed a company in our universe); a stated co-investment or direct investing appetite;
technology or growth exposure; their geographic mandate. It must read as one person writing to
another, warm and plain, not flattery and not a sales line.

INTELLIGENCE:
{lp_context}

RULES: British spelling. No em dashes or en dashes, use commas or full stops. Do not mention
Averroes, returns, or a call (the rest of the email does that). Do not invent facts: if the
intelligence gives you nothing specific, return an empty string.

Return ONLY valid JSON: {{"why_them": "..."}}"""

    try:
        from google import genai

        client = genai.Client(api_key=api_key)
        response = client.models.generate_content(model="gemini-2.5-flash", contents=prompt)
        text = (response.text or "").strip()
        if text.startswith("```"):
            text = text.split("\n", 1)[1].rsplit("```", 1)[0].strip()
        why = (json.loads(text).get("why_them") or "").strip() if text else ""
        why = why.replace("—", ",").replace("–", ",")
        if len(why) > 400:
            why = why[:400].rsplit(".", 1)[0] + "."
        return {"subject": subject, "body": _assemble(why or generic_why), "to": contact_email or "",
                "contact_name": contact_name or "", "investor": name,
                "from": sender_label("investor")}
    except Exception as e:
        logger.warning(f"LP draft failed for {name}: {e}")
        return fallback


def draft_lp_followup_email(investor: Dict) -> Dict[str, str]:
    """The 14-day LP follow-up: fixed template, same thread (Re: subject),
    zero AI. One nudge, human, no new ask."""
    contact_name = investor.get("contact_name", "")
    first = contact_name.split()[0] if contact_name.strip() else ""
    subj = investor.get("outreach_draft_subject") or f"Averroes Capital, {investor.get('name', '')}"
    body = (f"{'Hi ' + first + ',' if first else 'Hello,'}\n\n"
            f"Following up on my note below in case it got buried. We are having a small number of "
            f"conversations with investors about how Averroes works alongside its co-investors in UK "
            f"and Irish software, and I would still value a short conversation if the timing suits.\n\n"
            f"If it is easier, I am happy to send a short note on Averroes first.\n\n"
            f"Best,")
    return {"to": investor.get("outreach_draft_to") or investor.get("contact_email") or "",
            "subject": subj if subj.lower().startswith("re:") else f"Re: {subj}",
            "body": body, "investor": investor.get("name", ""), "from": sender_label("investor")}


# ── Email signature (appended automatically at send time) ────────────────────
# Matches Bea's real signature (per Ishu, 7 Sep 2026): the Averroes logo,
# name in bold, title, contact line, then the regulatory disclaimer in small
# bold grey. The logo is EMBEDDED (CID attachment, multipart/related) so it
# renders in every client without a hosted URL.
#
# The email shown is the OUTREACH mailbox, not Bea's personal bcarrara@
# address: replies must land where the sync reads them, and a founder who
# copies the signature address into a new email would otherwise vanish from
# the pipeline.
SIG_NAME = os.getenv("SIGNATURE_NAME", "Maria Beatrice Carrara")
SIG_TITLE = os.getenv("SIGNATURE_TITLE", "Partner")
SIG_PHONE = os.getenv("SIGNATURE_PHONE", "")   # blank = no phone line (Ishu's copy omits it)
SIG_EMAIL = os.getenv("SIGNATURE_EMAIL", "beatrice@averroescapital.com")
SIG_LOGO_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "assets", "averroes_logo.png")
SIG_LOGO_CID = "averroes-logo"

SIG_DISCLAIMER = (
    "Averroes Capital Limited is an Appointed Representative of Capricorn Fund Managers Limited, "
    "which is authorised and regulated by the Financial Conduct Authority. Averroes Capital Limited "
    "provides investment management services to Averroes Fund under a secondment arrangement with "
    "Capricorn Fund Managers Limited, the AIFM of record. Averroes Capital Limited is incorporated in "
    "England and the registered office is at 77 Charlotte Street, London, W1T 4PW. The investment "
    "products and services of Averroes Capital Limited are only available to professional clients and "
    "eligible counterparties. They are not available to retail clients. This email does not constitute "
    "an offer to buy or sell shares in any of the products offered by Averroes Capital Limited. This "
    "email contains confidential information and is intended only for the individual or entity named. "
    "If you are not the named addressee you should not disseminate, distribute or copy this email. "
    "Please notify the sender immediately by email if you have received this email by mistake and "
    "delete this email from your system."
)


def build_signature(name: str, title: str, email: str, phone: str = "") -> Dict[str, str]:
    """The signature block for one sender: plain text and HTML (logo via CID,
    contact line, regulatory disclaimer). Body ends with "Best,"; the
    signature follows directly beneath it."""
    contact_text = f"{phone} | {email}" if phone else email
    contact_html = (
        (f'<a href="tel:{phone.replace(" ", "")}" style="color:#1a56db; text-decoration:underline;">{phone}</a>'
         f'<span style="color:#9ca3af;"> | </span>' if phone else "")
        + f'<a href="mailto:{email}" style="color:#1a56db; text-decoration:underline;">{email}</a>'
    )
    text = f"\n{name}\n{title}\n{contact_text}\n\n{SIG_DISCLAIMER}"
    html = f"""
<br>
<img src="cid:{SIG_LOGO_CID}" alt="Averroes Capital" width="150" style="display:block; width:150px; height:auto; margin:10px 0 14px;">
<div style="font-family: Arial, Helvetica, sans-serif; font-size:14px; line-height:1.5; color:#6b7280;">
  <b style="color:#4b5563;">{name}</b><br>
  {title}<br>
  {contact_html}
</div>
<p style="font-family: Arial, Helvetica, sans-serif; font-size:11px; line-height:1.7; color:#9ca3af; font-weight:bold; margin:18px 0 0; max-width:760px;">
  {SIG_DISCLAIMER}
</p>
"""
    return {"text": text, "html": html}


_founder_sig = build_signature(SIG_NAME, SIG_TITLE, SIG_EMAIL, SIG_PHONE)
SIGNATURE_TEXT = _founder_sig["text"]
SIGNATURE_HTML = _founder_sig["html"]


# ── Sender profiles ──────────────────────────────────────────────────────────
# Founder outreach goes from Bea's outreach mailbox. Investor (LP) outreach
# goes from a SEPARATE mailbox (per Ishu, 8 Sep 2026), configured on Cloud Run
# with INVESTOR_OUTREACH_EMAIL / INVESTOR_OUTREACH_NAME / INVESTOR_SMTP_PASSWORD
# (+ optional INVESTOR_SIGNATURE_NAME / _TITLE / _PHONE). Each profile has its
# own signature and its own mailbox for the reply sync to read.
# UNTIL the investor mailbox is configured (TBU, per Ishu 8 Sep 2026) the
# investor profile FALLS BACK to the founder mailbox, VISIBLY: the profile
# carries fallback=True and every draft/modal From line says so. Once
# INVESTOR_OUTREACH_EMAIL + INVESTOR_SMTP_PASSWORD are set the fallback
# disappears without a code change.
def sender_profile(kind: str = "founder") -> Dict[str, str]:
    founder = {
        "kind": "founder", "email": SENDER_EMAIL, "name": SENDER_NAME, "password": SMTP_PASSWORD,
        "sig_name": SIG_NAME, "sig_title": SIG_TITLE, "sig_phone": SIG_PHONE,
        "configured": bool(SENDER_EMAIL and SMTP_PASSWORD), "fallback": False,
    }
    if kind != "investor":
        return founder
    email = os.getenv("INVESTOR_OUTREACH_EMAIL", "")
    name = os.getenv("INVESTOR_OUTREACH_NAME", "")
    pw = os.getenv("INVESTOR_SMTP_PASSWORD", "")
    if email and pw:
        return {
            "kind": "investor", "email": email, "name": name, "password": pw,
            "sig_name": os.getenv("INVESTOR_SIGNATURE_NAME", name),
            "sig_title": os.getenv("INVESTOR_SIGNATURE_TITLE", ""),
            "sig_phone": os.getenv("INVESTOR_SIGNATURE_PHONE", ""),
            "configured": True, "fallback": False,
        }
    return {**founder, "kind": "investor", "fallback": True}


def sender_label(kind: str = "founder") -> str:
    """'Name <address>' for the UI; says so when the investor side is borrowing
    the founder mailbox, or when nothing is configured at all."""
    p = sender_profile(kind)
    if not p["configured"]:
        return "not configured (OUTREACH_EMAIL / OUTREACH_SMTP_PASSWORD)"
    label = f"{p['name'] or p['email']} <{p['email']}>"
    if p.get("fallback"):
        label += " (founder mailbox; investor mailbox not configured yet)"
    return label


def _logo_part():
    """The embedded logo as a related MIME part, or None if the asset is absent."""
    try:
        from email.mime.image import MIMEImage
        with open(SIG_LOGO_PATH, "rb") as f:
            img = MIMEImage(f.read(), _subtype="png")
        img.add_header("Content-ID", f"<{SIG_LOGO_CID}>")
        img.add_header("Content-Disposition", "inline", filename="averroes_logo.png")
        return img
    except Exception as e:
        logger.warning(f"Signature logo unavailable ({e}); sending without it.")
        return None


def send_email(to: str, subject: str, body: str,
               in_reply_to: str = "", references: str = "",
               sender: str = "founder") -> Dict[str, str]:
    """
    Send an email via Gmail SMTP using App Password. Beatrice's signature
    (name, title, phone, email, logo if configured) is appended automatically.
    Returns {"status": "sent"} or {"status": "error", "detail": "..."}.

    THREADING IS HEADERS, NOT SUBJECTS. A "Re:" subject alone does not put a
    message in the same conversation: Gmail threads on the In-Reply-To and
    References headers carrying the previous message's Message-ID. Without
    them a follow-up lands as a separate email and the founder loses the
    context of the first one, which is the whole point of following up.
    Callers pass the ids from email_log; blank means a fresh conversation.
    """
    prof = sender_profile(sender)
    if not prof["configured"]:
        return {"status": "error", "detail": f"Sender mailbox for {sender} outreach is {sender_label(sender)}. Set it as Cloud Run env vars."}

    if not to:
        return {"status": "error", "detail": "No recipient email address provided."}

    sig = _founder_sig if prof["kind"] == "founder" or prof.get("fallback") else build_signature(
        prof["sig_name"] or prof["name"], prof["sig_title"], prof["email"], prof["sig_phone"])
    try:
        # multipart/related wraps the alternative (text + html) AND the inline
        # logo, so the <img src="cid:..."> in the html resolves to the
        # attached bytes in every client. Plain-text readers still get the
        # full signature text including the disclaimer.
        msg = MIMEMultipart("related")
        msg["From"] = f"{prof['name'] or prof['email']} <{prof['email']}>"
        msg["To"] = to
        msg["Subject"] = subject
        if in_reply_to:
            msg["In-Reply-To"] = in_reply_to
        if references:
            msg["References"] = references

        alt = MIMEMultipart("alternative")
        alt.attach(MIMEText(body + sig["text"], "plain"))
        html_body = body.replace("\n", "<br>")
        html = f"""<html><body style="font-family: Arial, sans-serif; font-size: 14px; color: #333; line-height: 1.6;">{html_body}{sig["html"]}</body></html>"""
        alt.attach(MIMEText(html, "html"))
        msg.attach(alt)

        logo = _logo_part()
        if logo is not None:
            msg.attach(logo)

        with smtplib.SMTP_SSL("smtp.gmail.com", 465) as server:
            server.login(prof["email"], prof["password"])
            server.sendmail(prof["email"], to, msg.as_string())

        logger.info(f"Outreach email sent to {to} (subject: {subject})")
        return {"status": "sent", "to": to, "subject": subject}

    except smtplib.SMTPAuthenticationError:
        logger.error("Gmail SMTP auth failed - check App Password")
        return {"status": "error", "detail": f"Gmail authentication failed for {prof['email']}. Check the app password env var."}
    except Exception as e:
        logger.error(f"Email send failed: {e}")
        return {"status": "error", "detail": str(e)}


def _fallback_template(company_data: Dict) -> Dict[str, str]:
    """Basic template when Gemini is unavailable."""
    name = company_data.get("name", "your company")
    contact_name = company_data.get("contact_name", "")
    contact_email = company_data.get("contact_email", "")
    first_name, _ = _greeting_for(company_data)
    greeting = f"Hi {first_name}," if first_name else "Hello,"
    body = (
        f"{greeting}\n\n"
        f"Hope you are having a great day.\n\n"
        f"I am a Partner at Averroes Capital, a London-based growth equity investor "
        f"focused on founder-led technology businesses. We typically get involved where "
        f"a company has a proven product and is looking at the next stage of growth.\n\n"
        f"We have been following {name} for some time and like what you are building.\n\n"
        f"At Averroes, we have backed similar companies such as Journey and Glowday, "
        f"and helped them scale with hands-on operational support alongside capital.\n\n"
        f"I appreciate this may not be a priority right now, but we would love to start "
        f"a dialogue, hear more about your ambitions for the future, and get to "
        f"introduce you to Averroes.\n\n"
        f"Look forward to hearing from you.\n\n"
        f"Best,"
    )

    return {
        "subject": f"Averroes Capital, introduction",
        "body": body,
        "to": contact_email or "",
        "contact_name": contact_name or "",
        "company": name,
        # Marks this as the emergency template: callers must NOT persist it as
        # a saved draft; the user should get a real generation on next click.
        "is_fallback": True,
    }
