"""
Responded-stage action buckets.

When a founder replies, one ungrounded Gemini call reads the reply AGAINST
our own record of the company (revenue band v3, fit score, financials, the
mandate) and files it into an action bucket: a recommendation, never an
automatic stage move. For buckets that warrant a response, the same call
drafts the suggested reply (review-and-send only, never auto-sent).

Cost: one flash call per new reply, zero grounding weight, no budget impact.

Style rule (inherited from outreach doctrine): no em dashes anywhere in this
file, including instruction text, because the model mimics instruction style.
"""
import os
import json
import logging
from datetime import date
from typing import List, Optional

logger = logging.getLogger(__name__)

# Bucket keys are the stored values; labels live in the frontend too
# (frontend/src/types/index.ts ACTION_BUCKETS). Keep the two in sync.
BUCKETS = {
    "right_fit_call": "Right fit - set up call",
    "right_fit_answer": "Right fit - answer & advance",
    "right_fit_early": "Right fit - too early, nurture",
    "right_fit_structure": "Right fit - structure mismatch, discuss",
    "right_fit_large": "Right fit - too large, stay close",
    "not_now_timing": "Not now - timing",
    "not_fit_no_respond": "Not the right fit - do not respond",
    "declined_close": "Declined - close politely",
    "redirect_referral": "Redirect - follow the referral",
    "needs_human": "Needs human read",
}

# Buckets where a drafted response makes sense (review-and-send)
_REPLY_BUCKETS = {
    "right_fit_call", "right_fit_answer", "right_fit_early",
    "right_fit_structure", "right_fit_large", "not_now_timing",
    "declined_close", "redirect_referral",
}

_MANDATE = (
    "Averroes Capital invests 15 to 40 million pounds of equity per deal in UK and "
    "Ireland B2B software companies, taking majority or significant minority (25 percent "
    "or more) stakes. Investable revenue envelope: 2.5 to 40 million pounds, core sweet "
    "spot 8 to 20 million. Below 2.5m is too early; above 40m is too large. Pure secondary "
    "purchases or very small minority stakes with no path to 25 percent do not fit the mandate."
)


def _fmt_money(v) -> str:
    try:
        v = float(v)
    except (TypeError, ValueError):
        return ""
    return f"{v / 1e6:.1f}m GBP" if v > 100000 else f"{v:.1f}m GBP"


def _company_snapshot(company: dict) -> str:
    """Compact factual snapshot of OUR data for the model. No fabrication:
    only fields that actually exist are included."""
    lines = [f"Company: {company.get('name')}"]
    for label, key in [("Sector", "sector"), ("Status", "status"),
                       ("Revenue band (v3)", "revenue_band"),
                       ("Ownership", "ownership"), ("Unfit reason", "unfit_reason")]:
        if company.get(key):
            lines.append(f"{label}: {company[key]}")
    if company.get("averroes_fit_score") is not None:
        lines.append(f"Averroes fit score: {round(float(company['averroes_fit_score']) * 100)}/100")
    rev = company.get("revenue_y1")
    if rev:
        lines.append(f"Latest filed/known revenue: {_fmt_money(rev)}")
    elif company.get("revenue_estimate_m"):
        lines.append(f"Estimated revenue: {company['revenue_estimate_m']}m GBP "
                     f"({company.get('revenue_confidence') or 'unverified'})")
    if company.get("employees") or company.get("employees_ch"):
        lines.append(f"Employees: {company.get('employees_ch') or company.get('employees')}")
    if company.get("revenue_cagr_3yr_pct") is not None:
        lines.append(f"Revenue CAGR 3yr: {company['revenue_cagr_3yr_pct']}%")
    if company.get("ch_founder_pct"):
        lines.append(f"Founder holding (Companies House cap table): {company['ch_founder_pct']}%")
    desc = (company.get("description") or "").strip()
    if desc:
        lines.append(f"What they do: {desc[:400]}")
    return "\n".join(lines)


def bucket_reply(company: dict, reply_subject: str, reply_text: str,
                 thread: Optional[List[dict]] = None) -> dict:
    """
    Classify a founder reply into an action bucket using our company record
    plus the email content. Returns {} on failure (caller leaves state as is).

    Success shape:
      {"bucket", "label", "rationale", "follow_up_date" ("" if none),
       "reply_subject", "reply_body" ("" when no response is warranted)}
    """
    api_key = os.getenv("GEMINI_API_KEY")
    if not api_key or not (reply_text or "").strip():
        return {}

    thread_block = ""
    if thread:
        rows = []
        for m in thread[-6:]:
            rows.append(f"[{m.get('direction')}] {str(m.get('sent_at') or '')[:10]} "
                        f"{m.get('subject') or ''}: {(m.get('snippet') or '')[:300]}")
        thread_block = "Recent thread (oldest first):\n" + "\n".join(rows) + "\n\n"

    bucket_defs = """
- "right_fit_call": company fits the mandate (revenue band is Target Band or clearly investable, thesis fit) AND the founder is open, positive, or asking for a call or meeting. Action: propose times.
- "right_fit_answer": company fits, but the founder asked questions first (ticket size, fund, model, who we are) before committing. Action: answer, then push for the call.
- "right_fit_early": thesis fit but the company is below the revenue envelope (band Too Early, or the founder revealed revenue below 2.5m GBP). Action: warm reply, keep the door open, set a revisit date.
- "right_fit_structure": fit and size work, but the founder signalled deal terms outside the mandate: pure secondary, very small minority, no path to 25 percent, or similar structure constraints. Action: judgement call, explore whether a workable structure exists.
- "right_fit_large": thesis fit but above the envelope (band Too Large, or the founder revealed revenue or valuation above what a 15 to 40m cheque can buy 25 percent of). Action: polite reply, stay close, no active pursuit.
- "not_now_timing": fit is fine but the founder said the timing is wrong: revisit in N months, mid fundraise, after an event. Action: courteous reply plus a stored follow up date.
- "not_fit_no_respond": the reply confirms a thesis mismatch (wrong sector, geography, business model) or our record already marks it unfit and nothing in the reply changes that. Action: no response.
- "declined_close": the founder clearly declined regardless of fit: not interested, not for sale, asked to stop contacting. Action: one graceful close out, or silence if they asked for silence.
- "redirect_referral": the founder points to someone else: a CFO, advisor, chairman, or a different entity. Action: thank them and contact the referred person.
- "needs_human": auto reply or out of office, ambiguous, conflicting signals, or the email cannot be read confidently. Action: human review. For out of office, use the stated return date as follow_up_date if given.
"""

    prompt = f"""You are the deal intelligence layer of a private equity origination tool. An outreach email was sent to a founder and they replied. Decide the ACTION BUCKET by combining two things: our own data about the company, and what the founder actually said. Where the founder reveals numbers (revenue, valuation, stake) that contradict our record, the founder's numbers win.

THE MANDATE:
{_MANDATE}

OUR RECORD OF THE COMPANY:
{_company_snapshot(company)}

{thread_block}THE FOUNDER'S REPLY:
Subject: {reply_subject}
{(reply_text or '')[:2500]}

BUCKETS (choose exactly one key):
{bucket_defs}

Also decide:
1. rationale: ONE plain sentence explaining the choice, citing the decisive fact (e.g. "Founder asks for a call and revenue of 12m sits in the core sweet spot"). Never invent numbers that appear in neither our record nor the reply.
2. follow_up_date: ISO date (YYYY-MM-DD) ONLY if the reply or bucket implies a concrete revisit moment (not_now_timing, right_fit_early, out of office return). Otherwise empty string. Today is {date.today().isoformat()}.
3. If the bucket warrants a response ({', '.join(sorted(_REPLY_BUCKETS))}), draft the suggested reply email:
   - From Beatrice at Averroes Capital, warm, concise, no more than 120 words.
   - Directly answer what the founder said or asked. Do not repeat the original pitch.
   - right_fit_call: propose a 20 minute call and offer two windows next week (keep them generic, e.g. "Tuesday or Thursday afternoon").
   - right_fit_answer: answer their questions factually from the mandate above, then suggest the call.
   - right_fit_early or not_now_timing: thank them, agree to reconnect, name the timeframe.
   - right_fit_structure or right_fit_large: acknowledge the constraint honestly and leave the door open.
   - declined_close: two sentences, gracious, no persuasion.
   - redirect_referral: thank them and ask for an introduction to the person they named.
   - Sign off with "Best," on its own line followed by "Beatrice". NEVER use an em dash anywhere.
   For buckets with no response (not_fit_no_respond, needs_human), reply_subject and reply_body must be empty strings.

Return ONLY valid JSON:
{{"bucket": "...", "rationale": "...", "follow_up_date": "", "reply_subject": "", "reply_body": ""}}"""

    try:
        from google import genai
        from google.genai.types import GenerateContentConfig

        client = genai.Client(api_key=api_key)
        response = client.models.generate_content(
            model="gemini-2.5-flash", contents=prompt,
            config=GenerateContentConfig(temperature=0.2, response_mime_type="application/json"),
        )
        text = (response.text or "").strip()
        if text.startswith("```"):
            text = text.split("\n", 1)[1].rsplit("```", 1)[0].strip()
        start, end = text.find("{"), text.rfind("}")
        result = json.loads(text[start:end + 1])
        bucket = result.get("bucket", "")
        if bucket not in BUCKETS:
            logger.warning(f"[ReplyIntel] Unknown bucket '{bucket}' returned; discarding")
            return {}
        if bucket not in _REPLY_BUCKETS:
            result["reply_subject"], result["reply_body"] = "", ""
        # Containment: never let an em dash through in the drafted reply
        for k in ("reply_subject", "reply_body", "rationale"):
            result[k] = (result.get(k) or "").replace("\u2014", "-").replace("\u2013", "-")
        return {
            "bucket": bucket,
            "label": BUCKETS[bucket],
            "rationale": result.get("rationale", ""),
            "follow_up_date": (result.get("follow_up_date") or "")[:10],
            "reply_subject": result.get("reply_subject", ""),
            "reply_body": result.get("reply_body", ""),
        }
    except Exception as e:
        logger.warning(f"[ReplyIntel] Bucketing failed for {company.get('name')}: {e}")
        return {}
