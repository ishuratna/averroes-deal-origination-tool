"""
Outreach Service
Generates personalised PE/growth capital outreach emails using Gemini AI,
and sends them via Gmail SMTP.
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
SENDER_EMAIL = os.getenv("OUTREACH_EMAIL", "iratna@averroescapital.com")
SENDER_NAME = os.getenv("OUTREACH_NAME", "Beatrice Carrara")
SMTP_PASSWORD = os.getenv("OUTREACH_SMTP_PASSWORD", "")  # Gmail App Password


def draft_outreach_email(company_data: Dict) -> Dict[str, str]:
    """
    Use Gemini + Google Search to draft a personalised outreach email.
    Strategy: use BQ data first, scrape website if description is sparse.
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

    # Determine if we have enough context or need website research
    has_good_context = bool(description and len(str(description)) > 50)
    research_instruction = ""
    if not has_good_context and website:
        research_instruction = f"""
        IMPORTANT: The company description is thin. Please use Google Search to research
        {name}'s website ({website}) and recent news to understand what they do,
        their products, and any recent milestones. Use this research to personalise the email.
        """

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

    prompt = f"""
    You are Beatrice Carrara, Partner at Averroes Capital — a growth capital / private equity
    investor focused on B2B SaaS and tech-enabled services in the UK & Europe.

    Write a SHORT, warm, personalised outreach email to {contact_name or 'the founder'} at {name}.

    {research_instruction}

    COMPANY INTELLIGENCE:
    {company_context}

    EMAIL GUIDELINES:
    - Tone: Friendly, peer-to-peer, NOT salesy or corporate. Like a fellow founder reaching out.
    - Length: 4-6 sentences max. Busy founders don't read long emails.
    - Opening: Reference something SPECIFIC about their company (product, growth, sector position).
      Do NOT open with "I hope this email finds you well" or "I came across your company".
    - Value prop: Position as a supportive growth partner, not an acquirer. Emphasise:
      * Operational support and growth capital
      * Respect for what they've built
      * No pressure, just exploring a conversation
    - CTA: Suggest a brief 15-minute call, keep it low-commitment.
    - Sign off as: Beatrice Carrara, Partner, Averroes Capital
    - Do NOT include email headers (To, From, Date) — just subject and body.

    Return ONLY valid JSON with exactly these keys:
    {{"subject": "email subject line", "body": "full email body text"}}

    The body should use \\n for line breaks between paragraphs.
    """

    try:
        from google import genai
        from google.genai.types import GenerateContentConfig, GoogleSearch, Tool

        client = genai.Client(api_key=api_key)

        # Use Google Search grounding when we need to research the company
        tools = [Tool(google_search=GoogleSearch())] if (not has_good_context or research_instruction) else []

        response = client.models.generate_content(
            model="gemini-2.5-flash",
            contents=prompt,
            config=GenerateContentConfig(tools=tools) if tools else None,
        )

        text = response.text.strip()
        # Strip markdown code fences if present
        if text.startswith("```"):
            text = text.split("\n", 1)[1].rsplit("```", 1)[0].strip()

        result = json.loads(text)
        return {
            "subject": result.get("subject", f"Quick intro — Averroes Capital x {name}"),
            "body": result.get("body", ""),
            "to": contact_email or "",
            "contact_name": contact_name or "",
            "company": name,
        }
    except Exception as e:
        logger.error(f"Outreach draft generation failed for {name}: {e}")
        return _fallback_template(company_data)


def send_email(to: str, subject: str, body: str) -> Dict[str, str]:
    """
    Send an email via Gmail SMTP using App Password.
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

        # Plain text version
        msg.attach(MIMEText(body, "plain"))

        # Simple HTML version (converts newlines to <br>)
        html_body = body.replace("\n", "<br>")
        html = f"""<html><body style="font-family: Arial, sans-serif; font-size: 14px; color: #333; line-height: 1.6;">{html_body}</body></html>"""
        msg.attach(MIMEText(html, "html"))

        with smtplib.SMTP_SSL("smtp.gmail.com", 465) as server:
            server.login(SENDER_EMAIL, SMTP_PASSWORD)
            server.sendmail(SENDER_EMAIL, to, msg.as_string())

        logger.info(f"Outreach email sent to {to} (subject: {subject})")
        return {"status": "sent", "to": to, "subject": subject}

    except smtplib.SMTPAuthenticationError:
        logger.error("Gmail SMTP auth failed — check App Password")
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

    body = (
        f"Hi {first_name},\n\n"
        f"I've been following {name}'s progress and I'm impressed by what you've built. "
        f"At Averroes Capital, we partner with founder-led B2B tech companies in the UK & Europe "
        f"to support their next phase of growth — whether that's scaling, expanding, or simply "
        f"having a like-minded investor in your corner.\n\n"
        f"Would you be open to a brief 15-minute chat? No pressure at all — happy to work "
        f"around your schedule.\n\n"
        f"Best,\nBeatrice Carrara\nPartner, Averroes Capital"
    )

    return {
        "subject": f"{first_name} — quick intro from Averroes Capital",
        "body": body,
        "to": contact_email or "",
        "contact_name": contact_name or "",
        "company": name,
    }
