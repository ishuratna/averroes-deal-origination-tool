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

    first_name = contact_name.split()[0] if contact_name and contact_name.strip() else "there"

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

    WHAT WE KNOW ABOUT THE COMPANY (do not use anything beyond this):
    {company_context}

    {f'RECENT SIGNAL (verified, you may use it in structure item 4): {news_hook}' if news_hook else 'RECENT SIGNAL: none found. Do NOT invent one; ground item 4 in the company data instead.'}

    EMAIL STRUCTURE (follow this exact order and paragraphing; vary the
    wording naturally where noted, never the order):

    1. GREETING: "Hi {first_name}," (if no name is known, use "Hello,").
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
    6. HUMILITY + CURIOSITY, own paragraph: "I appreciate this may not be a
       priority right now, but I would love to learn more about what you are
       building at {name} and where you see the opportunity going."
    7. CTA, own paragraph: "If this sounds interesting, would you be open to
       a quick 20-minute call in the next couple of weeks?"
    8. CLOSING LINE, own line: "Look forward to hearing back from you."
    9. SIGN-OFF: end the body with exactly "Best," on its own line and NOTHING
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
        response = client.models.generate_content(
            model="gemini-2.5-flash",
            contents=prompt,
            config=GenerateContentConfig(temperature=1.0),
        )

        text = response.text
        if not text:
            logger.warning(f"Gemini returned empty response for outreach to {name}")
            return _fallback_template(company_data)

        text = text.strip()
        # Strip markdown code fences if present
        if text.startswith("```"):
            text = text.split("\n", 1)[1].rsplit("```", 1)[0].strip()

        result = json.loads(text)
        return {
            "subject": result.get("subject", f"Averroes Capital, {name}"),
            "body": result.get("body", ""),
            "to": contact_email or "",
            "contact_name": contact_name or "",
            "company": name,
        }
    except Exception as e:
        logger.error(f"Outreach draft generation failed for {name}: {e}")
        return _fallback_template(company_data)


def draft_lp_outreach_email(investor: Dict) -> Dict[str, str]:
    """
    Draft a personalised LP introduction email using stored investor data
    (PitchBook fields + InvestorFill research). No Google Search - saves credits.
    """
    api_key = os.getenv("GEMINI_API_KEY")

    name = investor.get("name", "")
    contact_name = investor.get("contact_name", "")
    contact_email = investor.get("contact_email", "")

    context_parts = []
    for label, key in [
        ("Type", "investor_type"), ("Description", "description"),
        ("HQ", "hq_country"), ("AUM ($M)", "aum_m"),
        ("PE strategy preferences", "strategy_preferences"),
        ("Geographic mandate", "geo_preferences"),
        ("Open to first-time funds", "open_to_first_time"),
        ("PE fund commitments", "num_pe_commitments"),
        ("Portfolio overlap with our pipeline", "source_companies"),
        ("Contact title", "contact_title"),
    ]:
        val = investor.get(key)
        if val not in (None, ""):
            context_parts.append(f"{label}: {val}")
    lp_context = "\n".join(context_parts) if context_parts else f"Investor: {name}"

    fallback = {
        "subject": f"Introduction from Averroes Capital",
        "body": (f"Dear {contact_name.split()[0] if contact_name.strip() else 'colleague'},\n\n"
                 f"I lead investor relations at Averroes Capital, a UK private equity firm focused on founder-led "
                 f"B2B software companies with £2.5-10M revenue, a segment we believe is underserved.\n\n"
                 f"Given {name}'s activity in private markets, I thought a brief introduction could be mutually interesting. "
                 f"Would you be open to a short call in the coming weeks?\n\n"
                 f"Best regards,\nBeatrice Carrara\nPartner, Averroes Capital"),
        "to": contact_email or "",
        "contact_name": contact_name or "",
        "investor": name,
    }
    if not api_key:
        return fallback

    prompt = f"""
    You are Beatrice Carrara, Partner at Averroes Capital, a UK lower-mid-market private
    equity firm investing in founder-led B2B SaaS and software companies (£2.5-10M revenue).

    Write a SHORT, professional LP introduction email to {contact_name or 'the principal'}
    at {name}, a potential LIMITED PARTNER (investor in our fund / co-investor in deals).

    INVESTOR INTELLIGENCE (from our database, use to personalise):
    {lp_context}

    EMAIL GUIDELINES:
    - This is INVESTOR RELATIONS, not deal sourcing: we are inviting them to hear about
      our strategy, not pitching a specific transaction.
    - Reference something SPECIFIC about them: their strategy preferences (e.g. buyout/growth),
      geographic mandate, or portfolio overlap with our pipeline if present.
    - Position Averroes: disciplined UK lower-mid-market software specialist; proprietary
      AI-driven origination covering the whole UK/Ireland universe; founder-friendly.
    - Length: 5-7 sentences. Senior investors skim.
    - No hyperbole, no "exciting opportunity" language. Understated and credible.
    - CTA: offer a short introductory call or to share our strategy note.
    - Sign off: Beatrice Carrara, Partner, Averroes Capital
    - No email headers. Just subject and body.

    Return ONLY valid JSON: {{"subject": "...", "body": "..."}} with \\n for line breaks.
    """

    try:
        from google import genai

        client = genai.Client(api_key=api_key)
        response = client.models.generate_content(model="gemini-2.5-flash", contents=prompt)
        text = (response.text or "").strip()
        if not text:
            return fallback
        if text.startswith("```"):
            text = text.split("\n", 1)[1].rsplit("```", 1)[0].strip()
        result = json.loads(text)
        return {
            "subject": result.get("subject", fallback["subject"]),
            "body": result.get("body", fallback["body"]),
            "to": contact_email or "",
            "contact_name": contact_name or "",
            "investor": name,
        }
    except Exception as e:
        logger.error(f"LP outreach draft failed for {name}: {e}")
        return fallback


# ── Email signature (appended automatically at send time) ────────────────────
SIG_NAME = os.getenv("SIGNATURE_NAME", "Maria Beatrice Carrara")
SIG_TITLE = os.getenv("SIGNATURE_TITLE", "Partner")
SIG_PHONE = os.getenv("SIGNATURE_PHONE", "+44 7384 357070")
SIG_EMAIL = os.getenv("SIGNATURE_EMAIL", "beatrice@averroescapital.com")
SIG_LOGO_URL = os.getenv("SIGNATURE_LOGO_URL", "")  # hosted Averroes logo, optional

# Body ends with "Best,"; the signature follows directly beneath it
SIGNATURE_TEXT = f"\n{SIG_NAME}\n{SIG_TITLE}\n{SIG_PHONE} | {SIG_EMAIL}"

SIGNATURE_HTML = f"""
<br>
{f'<img src="{SIG_LOGO_URL}" alt="Averroes Capital" style="height:44px; margin:6px 0;"><br>' if SIG_LOGO_URL else ''}
<span style="color:#6b7280;"><b style="color:#374151;">{SIG_NAME}</b><br>
{SIG_TITLE}<br>
{SIG_PHONE} | <a href="mailto:{SIG_EMAIL}" style="color:#2563eb;">{SIG_EMAIL}</a></span>
"""


def send_email(to: str, subject: str, body: str) -> Dict[str, str]:
    """
    Send an email via Gmail SMTP using App Password. Beatrice's signature
    (name, title, phone, email, logo if configured) is appended automatically.
    Returns {"status": "sent"} or {"status": "error", "detail": "..."}.
    """
    if not SMTP_PASSWORD:
        return {"status": "error", "detail": "OUTREACH_SMTP_PASSWORD not configured. Set it as a Cloud Run env var."}

    if not to:
        return {"status": "error", "detail": "No recipient email address provided."}

    try:
        msg = MIMEMultipart("alternative")
        msg["From"] = f"{SENDER_NAME} <{SENDER_EMAIL}>"
        msg["To"] = to
        msg["Subject"] = subject

        # Plain text version + signature
        msg.attach(MIMEText(body + SIGNATURE_TEXT, "plain"))

        # HTML version (newlines to <br>) + styled signature
        html_body = body.replace("\n", "<br>")
        html = f"""<html><body style="font-family: Arial, sans-serif; font-size: 14px; color: #333; line-height: 1.6;">{html_body}{SIGNATURE_HTML}</body></html>"""
        msg.attach(MIMEText(html, "html"))

        with smtplib.SMTP_SSL("smtp.gmail.com", 465) as server:
            server.login(SENDER_EMAIL, SMTP_PASSWORD)
            server.sendmail(SENDER_EMAIL, to, msg.as_string())

        logger.info(f"Outreach email sent to {to} (subject: {subject})")
        return {"status": "sent", "to": to, "subject": subject}

    except smtplib.SMTPAuthenticationError:
        logger.error("Gmail SMTP auth failed - check App Password")
        return {"status": "error", "detail": "Gmail authentication failed. Check OUTREACH_SMTP_PASSWORD."}
    except Exception as e:
        logger.error(f"Email send failed: {e}")
        return {"status": "error", "detail": str(e)}


def _fallback_template(company_data: Dict) -> Dict[str, str]:
    """Basic template when Gemini is unavailable."""
    name = company_data.get("name", "your company")
    contact_name = company_data.get("contact_name", "")
    contact_email = company_data.get("contact_email", "")
    first_name = contact_name.split()[0] if contact_name else "Hi"

    greeting = f"Hi {first_name}," if contact_name else "Hello,"
    body = (
        f"{greeting}\n\n"
        f"Hope you are having a great day.\n\n"
        f"I am a Partner at Averroes Capital, a London-based growth equity investor "
        f"focused on founder-led technology businesses. We typically get involved where "
        f"a company has a proven product and is looking at the next stage of growth.\n\n"
        f"We have been following {name} for some time and like what you are building.\n\n"
        f"At Averroes, we have backed similar companies such as Journey and Glowday, "
        f"and helped them scale with hands-on operational support alongside capital.\n\n"
        f"I appreciate this may not be a priority right now, but I would love to learn "
        f"more about what you are building at {name} and where you see the opportunity going.\n\n"
        f"If this sounds interesting, would you be open to a quick 20-minute call in the "
        f"next couple of weeks?\n\n"
        f"Look forward to hearing back from you.\n\n"
        f"Best,"
    )

    return {
        "subject": f"Averroes Capital, introduction",
        "body": body,
        "to": contact_email or "",
        "contact_name": contact_name or "",
        "company": name,
    }
