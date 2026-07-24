"""
IC memo (v2, classic 10-section PE flow) -> A4 PDF via reportlab.
Pure rendering: no data decisions here, everything comes from the memo JSON.
"""
from io import BytesIO

NAVY = "#0f172a"
SLATE = "#475569"
MUTED = "#94a3b8"
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
                                    TableStyle, HRFlowable)

    h = memo.get("header", {})
    n = memo.get("narrative", {})
    buf = BytesIO()
    doc = SimpleDocTemplate(buf, pagesize=A4, leftMargin=15 * mm, rightMargin=15 * mm,
                            topMargin=12 * mm, bottomMargin=11 * mm,
                            title=f"IC Memo - {h.get('name', '')}")

    def st(name, size, color=NAVY, bold=False, leading=None, space_after=2):
        return ParagraphStyle(name, fontName="Helvetica-Bold" if bold else "Helvetica",
                              fontSize=size, textColor=colors.HexColor(color),
                              leading=leading or (size + 2.6), spaceAfter=space_after)

    title_s = st("t", 15, bold=True)
    meta_s = st("m", 7.5, SLATE)
    sec_s = st("s", 9, BLUE, bold=True, space_after=2)
    body_s = st("b", 8.4, NAVY, leading=11.5)
    small_s = st("sm", 7.6, SLATE, leading=10)
    note_s = st("n", 6.8, MUTED, leading=8.5)

    story = []
    seq = [0]

    def section(label, flowables):
        seq[0] += 1
        story.append(Paragraph(f"{seq[0]}. {label}".upper(), sec_s))
        story.extend(flowables if isinstance(flowables, list) else [flowables])
        story.append(Spacer(1, 5))

    # Header
    meta_bits = [x for x in [h.get("sector"), h.get("hq"),
                             f"Founded {h.get('founded')}" if h.get("founded") else "",
                             h.get("website"), f"CH #{h.get('ch_number')}" if h.get("ch_number") else "",
                             f"Stage: {h.get('stage')}", h.get("band")] if x]
    story.append(Paragraph(f"{h.get('name', '')} &nbsp;<font size=8 color='{SLATE}'>Investment Memo · "
                           f"{str(memo.get('generated_at', ''))[:10]} · Averroes Capital · pre-diligence</font>", title_s))
    story.append(Paragraph(" · ".join(meta_bits), meta_s))
    story.append(HRFlowable(width="100%", thickness=1, color=colors.HexColor(NAVY), spaceAfter=6))

    # 1. Executive summary
    ex = memo.get("executive_summary", {})
    facts = ex.get("facts", {})
    fact_rows = []
    for label, key in [("Company", "company"), ("Sector", "sector"),
                       ("Indicative EV", "indicative_ev"), ("Equity investment", "equity_investment"),
                       ("Ownership targeted", "ownership_targeted"), ("Fit score", "fit_score"),
                       ("Recommendation", "recommendation")]:
        v = facts.get(key)
        if v not in (None, ""):
            if key == "fit_score":
                v = f"{v}/100"
            color = GREEN if key == "recommendation" else NAVY
            fact_rows.append([Paragraph(f"<b>{label}</b>", small_s),
                              Paragraph(f"<font color='{color}'>{v}</font>", small_s)])
    ex_flow = []
    if fact_rows:
        t = Table(fact_rows, colWidths=[34 * mm, 146 * mm])
        t.setStyle(TableStyle([("VALIGN", (0, 0), (-1, -1), "TOP"),
                               ("ROWBACKGROUNDS", (0, 0), (-1, -1), [colors.HexColor(LIGHT), colors.white]),
                               ("TOPPADDING", (0, 0), (-1, -1), 1.6), ("BOTTOMPADDING", (0, 0), (-1, -1), 1.6),
                               ("LEFTPADDING", (0, 0), (-1, -1), 4)]))
        ex_flow.append(t)
    if ex.get("summary"):
        ex_flow.append(Spacer(1, 3))
        ex_flow.append(Paragraph(ex["summary"], body_s))
    section("Executive Summary", ex_flow or [Paragraph("Not yet known", small_s)])

    # 2. Investment thesis
    thesis = memo.get("investment_thesis") or []
    if thesis:
        section("Investment Thesis", [Paragraph(f"• {b}", body_s) for b in thesis[:6]])

    # 3. Company overview
    ov = memo.get("company_overview") or {}
    ov_rows = [[Paragraph(f"<b>{lbl}</b>", small_s), Paragraph(str(ov.get(k) or "Not yet known"), small_s)]
               for lbl, k in [("History", "history"), ("Products", "products"), ("Geography", "geography"),
                              ("Customers", "customers"), ("Revenue mix", "revenue_mix"), ("Team", "team")]
               if ov.get(k)]
    ct = memo.get("cap_table") or {}
    holders = ct.get("holders") or []
    if holders:
        hh = ", ".join(f"{x.get('name', '?')} {x.get('pct', '')}%" for x in holders[:4] if isinstance(x, dict))
        ov_rows.append([Paragraph("<b>Ownership</b>", small_s),
                        Paragraph(f"{hh} (CS01{', ' + str(ct.get('as_of'))[:10] if ct.get('as_of') else ''})", small_s)])
    if ov_rows:
        t = Table(ov_rows, colWidths=[28 * mm, 152 * mm])
        t.setStyle(TableStyle([("VALIGN", (0, 0), (-1, -1), "TOP"),
                               ("TOPPADDING", (0, 0), (-1, -1), 1.4), ("BOTTOMPADDING", (0, 0), (-1, -1), 1.4),
                               ("LINEBELOW", (0, 0), (-1, -2), 0.4, colors.HexColor(LIGHT))]))
        section("Company Overview", t)

    # 4. Market
    mk = memo.get("market") or {}
    mk_rows = [[Paragraph(f"<b>{lbl}</b>", small_s), Paragraph(str(mk.get(k) or "Not yet known"), small_s)]
               for lbl, k in [("Size", "size"), ("Growth", "growth"), ("Competitors", "competitors"),
                              ("Demand drivers", "demand_drivers"), ("Regulation", "regulation")]
               if mk.get(k)]
    if mk_rows:
        t = Table(mk_rows, colWidths=[28 * mm, 152 * mm])
        t.setStyle(TableStyle([("VALIGN", (0, 0), (-1, -1), "TOP"),
                               ("TOPPADDING", (0, 0), (-1, -1), 1.4), ("BOTTOMPADDING", (0, 0), (-1, -1), 1.4),
                               ("LINEBELOW", (0, 0), (-1, -2), 0.4, colors.HexColor(LIGHT))]))
        section("Market (sourced via live search)", t)

    # 5. Financials
    fin_rows = [[Paragraph("<b>Metric</b>", small_s), Paragraph("<b>Value</b>", small_s), Paragraph("<b>Source</b>", small_s)]]
    for r in (memo.get("financials") or [])[:10]:
        fin_rows.append([Paragraph(r.get("label", ""), small_s), Paragraph(str(r.get("value", "")), small_s),
                         Paragraph(r.get("source", ""), small_s)])
    fin_flow = []
    if len(fin_rows) > 1:
        t = Table(fin_rows, colWidths=[70 * mm, 55 * mm, 55 * mm])
        t.setStyle(TableStyle([("VALIGN", (0, 0), (-1, -1), "TOP"),
                               ("LINEBELOW", (0, 0), (-1, 0), 0.5, colors.HexColor(SLATE)),
                               ("TOPPADDING", (0, 0), (-1, -1), 1.4), ("BOTTOMPADDING", (0, 0), (-1, -1), 1.4)]))
        fin_flow.append(t)
    sc = memo.get("scorecard") or {}
    if sc.get("fit") is not None:
        subs = " · ".join(f"{s['label']} {s['value']}" for s in sc.get("subscores", []) if s.get("value") is not None)
        fin_flow.append(Paragraph(f"<b>Fit score {sc['fit']}/100</b> ({subs})", small_s))
    if fin_flow:
        section("Financials (source-tagged)", fin_flow)

    # 6. Diligence status
    dd = memo.get("diligence") or {}
    dd_flow = [Paragraph(f"<font color='{GREEN}'>✓</font> {v}", small_s) for v in (dd.get("verified") or [])[:6]]
    oq = dd.get("open_questions") or {}
    for ws, label in [("commercial", "Commercial"), ("financial", "Financial"),
                      ("legal", "Legal"), ("technology", "Technology")]:
        qs = oq.get(ws) or []
        if qs:
            dd_flow.append(Paragraph(f"<b>{label} (to diligence):</b> " + " ".join(f"• {q}" for q in qs[:3]), small_s))
    if dd_flow:
        section("Diligence Status (pre-DD: verified vs open)", dd_flow)

    # 7. Value creation
    vc = memo.get("value_creation") or []
    if vc:
        section("Value Creation Plan (hypotheses)", [Paragraph(f"• {b}", body_s) for b in vc[:6]])

    # 8. Risks
    risks = memo.get("risks") or []
    flags = memo.get("registry_flags") or []
    risk_rows = [[Paragraph("<b>Risk</b>", small_s), Paragraph("<b>Mitigation</b>", small_s)]]
    for f in flags[:2]:
        risk_rows.append([Paragraph(f, small_s), Paragraph("Verify in DD; registry-flagged", small_s)])
    for r in risks[:5]:
        if isinstance(r, dict):
            risk_rows.append([Paragraph(r.get("risk", ""), small_s), Paragraph(r.get("mitigation", ""), small_s)])
    if len(risk_rows) > 1:
        t = Table(risk_rows, colWidths=[90 * mm, 90 * mm])
        t.setStyle(TableStyle([("VALIGN", (0, 0), (-1, -1), "TOP"),
                               ("LINEBELOW", (0, 0), (-1, 0), 0.5, colors.HexColor(SLATE)),
                               ("ROWBACKGROUNDS", (0, 1), (-1, -1), [colors.white, colors.HexColor(LIGHT)]),
                               ("TOPPADDING", (0, 0), (-1, -1), 1.6), ("BOTTOMPADDING", (0, 0), (-1, -1), 1.6)]))
        section("Risks & Mitigations", t)

    # 9. Returns
    rt = memo.get("returns") or {}
    if rt.get("available"):
        r_rows = [[Paragraph("<b>Scenario</b>", small_s), Paragraph("<b>Rev growth</b>", small_s),
                   Paragraph("<b>Exit multiple</b>", small_s), Paragraph("<b>MOIC</b>", small_s),
                   Paragraph("<b>IRR</b>", small_s)]]
        for s in rt.get("scenarios", []):
            r_rows.append([Paragraph(s["scenario"], small_s), Paragraph(f"{s['revenue_growth_pct']}%/yr", small_s),
                           Paragraph(f"{s['exit_multiple']}x rev", small_s),
                           Paragraph(f"<b>{s['moic']}x</b>", small_s), Paragraph(f"<b>{s['irr_pct']}%</b>", small_s)])
        t = Table(r_rows, colWidths=[34 * mm, 36 * mm, 38 * mm, 36 * mm, 36 * mm])
        t.setStyle(TableStyle([("LINEBELOW", (0, 0), (-1, 0), 0.5, colors.HexColor(SLATE)),
                               ("TOPPADDING", (0, 0), (-1, -1), 1.6), ("BOTTOMPADDING", (0, 0), (-1, -1), 1.6)]))
        section(f"Illustrative Returns (entry {rt.get('entry_multiple')}x = £{rt.get('entry_ev_m')}M EV, {rt.get('hold_years')}yr, unlevered)",
                [t, Paragraph(rt.get("note", ""), note_s)])
    else:
        section("Illustrative Returns", Paragraph(rt.get("note", "Not computable."), small_s))

    # 10. Recommendation
    rec = memo.get("recommendation") or ""
    if rec:
        story.append(HRFlowable(width="100%", thickness=0.75, color=colors.HexColor(SLATE), spaceAfter=3))
        section("Recommendation", Paragraph(f"<b>{rec}</b>", body_s))

    doc.build(story)
    return buf.getvalue()
