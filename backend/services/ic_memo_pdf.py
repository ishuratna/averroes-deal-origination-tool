"""
IC memo -> one-page A4 PDF (reportlab). Pure rendering: no data decisions
here, everything comes from the stored memo JSON.
"""
from io import BytesIO

NAVY = "#0f172a"
SLATE = "#475569"
LIGHT = "#f1f5f9"
BLUE = "#1d4ed8"
GREEN = "#15803d"
RED = "#b91c1c"
AMBER = "#b45309"


def render_ic_memo_pdf(memo: dict) -> bytes:
    from reportlab.lib.pagesizes import A4
    from reportlab.lib.units import mm
    from reportlab.lib import colors
    from reportlab.lib.styles import ParagraphStyle
    from reportlab.platypus import (SimpleDocTemplate, Paragraph, Spacer, Table,
                                    TableStyle, HRFlowable, KeepTogether)

    h = memo.get("header", {})
    n = memo.get("narrative", {})
    buf = BytesIO()
    doc = SimpleDocTemplate(buf, pagesize=A4, leftMargin=14 * mm, rightMargin=14 * mm,
                            topMargin=12 * mm, bottomMargin=10 * mm,
                            title=f"IC Memo - {h.get('name', '')}")

    def st(name, size, color=NAVY, bold=False, leading=None, space_after=2):
        return ParagraphStyle(name, fontName="Helvetica-Bold" if bold else "Helvetica",
                              fontSize=size, textColor=colors.HexColor(color),
                              leading=leading or (size + 2.5), spaceAfter=space_after)

    title_s = st("t", 15, bold=True)
    meta_s = st("m", 7.5, SLATE)
    sec_s = st("s", 8.5, BLUE, bold=True, space_after=1.5)
    body_s = st("b", 8.2, NAVY, leading=11)
    small_s = st("sm", 7.4, SLATE, leading=9.5)

    story = []
    meta_bits = [x for x in [h.get("sector"), h.get("hq"),
                             f"Founded {h.get('founded')}" if h.get("founded") else "",
                             h.get("website"),
                             f"CH #{h.get('ch_number')}" if h.get("ch_number") else "",
                             f"Stage: {h.get('stage')}", h.get("band")] if x]
    story.append(Paragraph(f"{h.get('name', '')} &nbsp;<font size=8 color='{SLATE}'>Investment Committee Memo · "
                           f"{str(memo.get('generated_at', ''))[:10]} · Prepared by Averroes origination</font>", title_s))
    story.append(Paragraph(" · ".join(meta_bits), meta_s))
    story.append(HRFlowable(width="100%", thickness=1, color=colors.HexColor(NAVY), spaceAfter=5))

    def section(label, flowables):
        story.append(Paragraph(label.upper(), sec_s))
        story.extend(flowables if isinstance(flowables, list) else [flowables])
        story.append(Spacer(1, 4))

    if n.get("opportunity"):
        section("The Opportunity", Paragraph(n["opportunity"], body_s))

    # Mandate fit table
    fit_rows = []
    for item in (n.get("mandate_fit") or [])[:3]:
        v = str(item.get("verdict", "?")).upper()
        color = GREEN if v == "PASS" else (RED if v == "FAIL" else AMBER)
        fit_rows.append([Paragraph(f"<b>{item.get('check', '')}</b>", small_s),
                         Paragraph(f"<font color='{color}'><b>{v}</b></font>", small_s),
                         Paragraph(item.get("evidence", ""), small_s)])
    if fit_rows:
        t = Table(fit_rows, colWidths=[52 * mm, 16 * mm, 112 * mm])
        t.setStyle(TableStyle([("VALIGN", (0, 0), (-1, -1), "TOP"),
                               ("ROWBACKGROUNDS", (0, 0), (-1, -1), [colors.HexColor(LIGHT), colors.white]),
                               ("TOPPADDING", (0, 0), (-1, -1), 2), ("BOTTOMPADDING", (0, 0), (-1, -1), 2),
                               ("LEFTPADDING", (0, 0), (-1, -1), 4)]))
        section("Mandate Fit", t)

    # Two columns: financials | deal math + scorecard
    fin_rows = [[Paragraph("<b>Metric</b>", small_s), Paragraph("<b>Value</b>", small_s), Paragraph("<b>Source</b>", small_s)]]
    for r in (memo.get("financials") or [])[:9]:
        fin_rows.append([Paragraph(r.get("label", ""), small_s), Paragraph(str(r.get("value", "")), small_s),
                         Paragraph(r.get("source", ""), small_s)])
    fin_t = Table(fin_rows, colWidths=[38 * mm, 26 * mm, 22 * mm])
    fin_t.setStyle(TableStyle([("VALIGN", (0, 0), (-1, -1), "TOP"),
                               ("LINEBELOW", (0, 0), (-1, 0), 0.5, colors.HexColor(SLATE)),
                               ("TOPPADDING", (0, 0), (-1, -1), 1.5), ("BOTTOMPADDING", (0, 0), (-1, -1), 1.5)]))

    right_bits = []
    dm = memo.get("deal_math") or {}
    if dm.get("available"):
        est = " (estimated revenue)" if dm.get("estimated") else ""
        right_bits.append(Paragraph(f"<b>Implied valuation:</b> £{dm.get('val_low_m')}M-£{dm.get('val_high_m')}M "
                                    f"at 4-6x £{dm.get('revenue_m')}M revenue{est}", small_s))
        if dm.get("stake_note"):
            right_bits.append(Paragraph(dm["stake_note"], small_s))
    else:
        right_bits.append(Paragraph(dm.get("note", "Valuation not computable."), small_s))
    if n.get("deal_hypothesis"):
        right_bits.append(Paragraph(n["deal_hypothesis"], small_s))
    sc = memo.get("scorecard") or {}
    if sc.get("fit") is not None:
        subs = " · ".join(f"{s['label']} {s['value']}" for s in sc.get("subscores", []) if s.get("value") is not None)
        right_bits.append(Paragraph(f"<b>Fit score {sc['fit']}/100</b> ({subs})", small_s))
    right_cell = right_bits or [Paragraph("", small_s)]

    two_col = Table([[fin_t, right_cell]], colWidths=[88 * mm, 92 * mm])
    two_col.setStyle(TableStyle([("VALIGN", (0, 0), (-1, -1), "TOP"), ("LEFTPADDING", (1, 0), (1, 0), 8)]))
    section("Financial Snapshot  |  Deal Hypothesis", two_col)

    # Ownership
    ct = memo.get("cap_table") or {}
    own_bits = []
    if ct.get("founder_pct") is not None:
        own_bits.append(f"Founder holding ~{ct['founder_pct']}%" + (f" (CS01, {ct['as_of'][:10]})" if ct.get("as_of") else ""))
    holders = ct.get("holders") or []
    if holders:
        hh = ", ".join(f"{x.get('name', '?')} {x.get('pct', x.get('percent', ''))}%" for x in holders[:5] if isinstance(x, dict))
        if hh:
            own_bits.append(f"Cap table: {hh}")
    if ct.get("psc"):
        own_bits.append(f"PSC: {ct['psc'][:220]}")
    if ct.get("ownership_verified"):
        own_bits.append(str(ct["ownership_verified"])[:200])
    if own_bits:
        section("Ownership & Cap Table", [Paragraph(x, small_s) for x in own_bits])

    if n.get("engagement_status"):
        section("Engagement", Paragraph(n["engagement_status"], body_s))
    if n.get("market_context"):
        section("Market Context (sourced)", Paragraph(n["market_context"], small_s))

    risks = list(memo.get("registry_flags") or []) + list(n.get("risks") or [])
    # de-dupe while keeping order
    seen, uniq = set(), []
    for r in risks:
        if r and r not in seen:
            seen.add(r)
            uniq.append(r)
    if uniq:
        section("Risks & Red Flags", [Paragraph(f"• {r}", small_s) for r in uniq[:6]])
    if n.get("open_questions"):
        section("Open Questions for First Meeting", [Paragraph(f"• {q}", small_s) for q in n["open_questions"][:4]])
    if n.get("recommendation"):
        story.append(HRFlowable(width="100%", thickness=0.75, color=colors.HexColor(SLATE), spaceAfter=3))
        story.append(Paragraph(f"<b>RECOMMENDATION:</b> {n['recommendation']}", body_s))

    doc.build(story)
    return buf.getvalue()
