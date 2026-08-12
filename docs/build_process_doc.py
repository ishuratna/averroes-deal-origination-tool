#!/usr/bin/env python3
"""
Averroes Capital - Deal Pipeline Operating Process (v3, 4 pages)

Page 1: the conversation flow, Email 1 through the first call
Page 2: the weekly operating rhythm
Page 3: who does what, the standing rules, and the new Responded page
Page 4: settled decisions + questions still open

Stage names (Qualified / Engaged / Contacted / Meeting / DD / Offer) and the
fit-bucket keys are the REAL values in BigQuery and on the Pipeline board, so
the document and the screen never disagree.

Every box measures its own content before it is drawn, and check_fits() fails
the build if a page runs into its footer. Hardcoded heights silently clip text
when copy changes, which is exactly what went wrong on the first two builds.
"""
from reportlab.lib.pagesizes import A4
from reportlab.lib.utils import simpleSplit
from reportlab.pdfgen import canvas
from reportlab.lib.colors import HexColor

W, H = A4
M = 46.0
CW = W - 2 * M

INK    = HexColor("#16202B")
BODY   = HexColor("#33414F")
MUTED  = HexColor("#6B7885")
RULE   = HexColor("#D6DCE4")
PANEL  = HexColor("#F4F6F9")
WHITE  = HexColor("#FFFFFF")

NAVY   = HexColor("#1F3B5C")
TEAL   = HexColor("#0F5132")
TEALBG = HexColor("#E8F1EC")
AMBER  = HexColor("#8A4B0F")
AMBBG  = HexColor("#FBF1E6")
GREY   = HexColor("#5B6672")
GREYBG = HexColor("#F0F2F5")
FLAG   = HexColor("#8C1D2A")
PLUM   = HexColor("#4A2A6B")
PLUMBG = HexColor("#F1ECF7")

F, FB, FO, FC = "Helvetica", "Helvetica-Bold", "Helvetica-Oblique", "Courier"

c = canvas.Canvas("Averroes_Deal_Pipeline_Process.pdf", pagesize=A4)
c.setTitle("Averroes Capital - Deal Pipeline Operating Process")
c.setAuthor("Averroes Capital")

TOTAL_PAGES = 4
FLOOR = M + 4


# ── helpers ──────────────────────────────────────────────────────────────────

def lines(t, font, size, maxw):
    return simpleSplit(t, font, size, maxw)


def nlines(t, font, size, maxw):
    return len(lines(t, font, size, maxw))


def para_h(t, font, size, lead, maxw):
    """EXACTLY what para() consumes: 2*size + (n-1)*lead + 1.5."""
    return 2 * size + (nlines(t, font, size, maxw) - 1) * lead + 1.5


def para(x, y, w, t, font=F, size=8.6, lead=11.4, color=BODY):
    c.setFont(font, size)
    c.setFillColor(color)
    yy = y - size
    for ln in lines(t, font, size, w):
        c.drawString(x, yy, ln)
        yy -= lead
    return yy + lead - size - 1.5


def box(x, y, w, h, fill=None, stroke=RULE, lw=0.7, r=3.5):
    if fill is not None:
        c.setFillColor(fill)
    c.setStrokeColor(stroke)
    c.setLineWidth(lw)
    c.roundRect(x, y - h, w, h, r, stroke=1, fill=1 if fill is not None else 0)


def tag(x, y, label, fg=WHITE, bg=NAVY, size=6.3, padx=4.6, h=11.5):
    c.setFont(FB, size)
    tw = c.stringWidth(label, FB, size)
    c.setFillColor(bg)
    c.roundRect(x, y - h, tw + 2 * padx, h, 2.0, stroke=0, fill=1)
    c.setFillColor(fg)
    c.drawString(x + padx, y - h + 3.4, label)
    return tw + 2 * padx


def arrow_down(cx, y_from, y_to, color=NAVY):
    c.setStrokeColor(color)
    c.setFillColor(color)
    c.setLineWidth(1.1)
    c.line(cx, y_from, cx, y_to + 4.5)
    p = c.beginPath()
    p.moveTo(cx, y_to)
    p.lineTo(cx - 3.4, y_to + 5.2)
    p.lineTo(cx + 3.4, y_to + 5.2)
    p.close()
    c.drawPath(p, stroke=0, fill=1)


def page_header(kicker, title, sub=None):
    y = H - M
    c.setFont(FB, 7.0)
    c.setFillColor(MUTED)
    c.drawString(M, y - 7, kicker.upper())
    y -= 20
    c.setFont(FB, 16.0)
    c.setFillColor(INK)
    c.drawString(M, y - 15, title)
    y -= 21
    if sub:
        y = para(M, y - 2, CW * 0.86, sub, F, 8.8, 11.6, MUTED) - 2
    c.setStrokeColor(RULE)
    c.setLineWidth(0.9)
    c.line(M, y - 6, W - M, y - 6)
    return y - 19


def check_fits(page_no, y):
    if y < FLOOR:
        raise SystemExit(f"LAYOUT OVERFLOW page {page_no}: ends y={y:.1f}, floor {FLOOR:.1f} "
                         f"(over by {FLOOR - y:.1f}pt)")
    print(f"  page {page_no}: ends y={y:.1f}, {y - FLOOR:.1f}pt clear")


def footer(page_no, note=""):
    c.setStrokeColor(RULE)
    c.setLineWidth(0.6)
    c.line(M, M - 10, W - M, M - 10)
    c.setFont(F, 6.9)
    c.setFillColor(MUTED)
    c.drawString(M, M - 21, "Averroes Capital - Deal Pipeline Operating Process  |  v3")
    if note:
        c.drawCentredString(W / 2.0, M - 21, note)
    c.drawRightString(W - M, M - 21, f"Page {page_no} of {TOTAL_PAGES}")


def section(y, label, color=INK, size=8.6):
    c.setFont(FB, size)
    c.setFillColor(color)
    c.drawString(M, y - 9, label)
    return y - 21


def table(y, cols, heads, rows, size=7.8, lead=10.0):
    """rows: list of (cell_texts..., accent_colour). Returns bottom y."""
    c.setFillColor(HexColor("#EDF1F6"))
    c.rect(M, y - 15, CW, 15, stroke=0, fill=1)
    xx = M
    c.setFont(FB, 7.2)
    c.setFillColor(NAVY)
    for hd, frac in zip(heads, cols):
        c.drawString(xx + 6, y - 10.5, hd.upper())
        xx += CW * frac
    y -= 15
    for row in rows:
        cells, col = list(row[:-1]), row[-1]
        rh = 4 + max(para_h(t, F, size, lead, CW * fr - 12) for t, fr in zip(cells, cols)) + 5
        c.setStrokeColor(RULE)
        c.setLineWidth(0.6)
        c.rect(M, y - rh, CW, rh, stroke=1, fill=0)
        c.setFillColor(col)
        c.rect(M, y - rh, 2.6, rh, stroke=0, fill=1)
        xx = M
        for i, (t, fr) in enumerate(zip(cells, cols)):
            para(xx + 6, y - 4, CW * fr - 12, t,
                 FB if i == 0 else F, size, lead, INK if i == 0 else BODY)
            xx += CW * fr
        y -= rh
    return y


STEP_X = 34.0
STEP_W = CW - STEP_X - 13


def step_card(y, num, title, pills, body, accent=NAVY, fill=WHITE, stroke=RULE, lw=0.7):
    h = 28 + (12.5 if pills else 0) + para_h(body, F, 8.4, 11.0, STEP_W) + 6
    box(M, y, CW, h, fill=fill, stroke=stroke, lw=lw)
    c.setFillColor(accent)
    c.roundRect(M, y - h, 3.2, h, 1.6, stroke=0, fill=1)
    c.circle(M + 19, y - 16, 8.2, stroke=0, fill=1)
    c.setFont(FB, 8.2)
    c.setFillColor(WHITE)
    c.drawCentredString(M + 19, y - 18.8, str(num))
    c.setFont(FB, 9.5)
    c.setFillColor(INK)
    c.drawString(M + STEP_X, y - 19.2, title)
    yy = y - 28
    if pills:
        xx = M + STEP_X
        for label, bg in pills:
            xx += tag(xx, yy + 1.5, label, WHITE, bg) + 4
        yy -= 12.5
    para(M + STEP_X, yy, STEP_W, body, F, 8.4, 11.0, BODY)
    return y - h


def outcome_col(x0, w, y, h, label, sub, col, bgc, bullets):
    box(x0, y, w, h, fill=bgc, stroke=col, lw=1.0)
    c.setFillColor(col)
    c.roundRect(x0, y - h, w, 3.0, 1.5, stroke=0, fill=1)
    tag(x0 + 10, y - 7, label, WHITE, col, 6.6)
    yy = para(x0 + 10, y - 22, w - 20, sub, FB, 8.6, 10.6, INK) - 4
    for b in bullets:
        c.setFillColor(col)
        c.circle(x0 + 13, yy - 4.0, 1.6, stroke=0, fill=1)
        yy = para(x0 + 19, yy + 0.5, w - 30, b, F, 7.9, 10.0, BODY) - 3.6
    return yy


def outcome_h(w, sub, bullets):
    return 22 + para_h(sub, FB, 8.6, 10.6, w - 20) + 4 + \
        sum(para_h(b, F, 7.9, 10.0, w - 30) + 3.6 for b in bullets) + 6


# ══════════════════════════════════════════════════════════════════════════════
# PAGE 1 - The conversation flow
# ══════════════════════════════════════════════════════════════════════════════
y = page_header(
    "Averroes Capital  |  Origination",
    "How a company travels from first contact to a call",
    "The first email asks for nothing, and every ask after it is earned by the reply before it. No call is "
    "booked until we have seen real numbers.",
)

# Who the founder corresponds with, and who operates the mailbox
PERSONA = ("Emails 1, 2 and 3 are sent from Bea's mailbox. Ishu drafts them, sends them and manages all replies. "
           "The founder corresponds with Bea throughout.")
pw = CW - 108
sh = 12 + para_h(PERSONA, F, 8.3, 10.8, pw) + 8
box(M, y, CW, sh, fill=PLUMBG, stroke=PLUM, lw=1.0)
c.setFillColor(PLUM)
c.rect(M, y - sh, 2.6, sh, stroke=0, fill=1)
c.setFont(FB, 7.2)
c.setFillColor(PLUM)
c.drawString(M + 12, y - 19, "SENDER")
para(M + 96, y - 8, pw, PERSONA, F, 8.3, 10.8, BODY)
y -= sh + 11

GAP = 12

y = step_card(y, 1, "Email 1  -  Introduction, no ask",
              [("AUTO-DRAFTED", NAVY), ("FROM BEA", PLUM), ("QUALIFIED -> ENGAGED", GREY)],
              "Why this company, who we are, an open invitation. No call, no meeting, no document request. "
              "Ishu sends these in his Tuesday and Thursday blocks.")
arrow_down(M + 19, y - 2, y - GAP + 4)
y -= GAP + 2

y = step_card(y, 2, "The founder replies",
              [("AUTOMATED", NAVY), ("ENGAGED -> CONTACTED", GREY)],
              "Logged against the company, classified into a fit bucket, and surfaced on the Responded page.")
arrow_down(M + 19, y - 2, y - GAP + 4)
y -= GAP + 2

y = step_card(y, 3, "Email 2  -  Ask for the growth story",
              [("MANUAL TODAY", FLAG), ("FROM BEA", PLUM), ("NOT HELD FOR A MEETING", GREY)],
              "Business model, revenue for the last two to three years, headcount, the few metrics they run on. "
              "Still no call. Ishu sends it in his next Tuesday or Thursday block, never waiting for a meeting.")
arrow_down(M + 19, y - 2, y - GAP + 4)
y -= GAP + 2

y = step_card(y, 4, "Ishu triages the Email 2 reply",
              [("ISHU'S JUDGEMENT", PLUM), ("SCORE IS REFERENCE ONLY", GREY)],
              "Ishu decides the path by reading the reply. The fit score, revenue band and filed accounts inform "
              "that call but do not make it. Three outcomes:",
              accent=PLUM, fill=HexColor("#F7F4FB"), stroke=PLUM, lw=1.0)
y -= 7

# Fork into three outcomes
gap3 = 10
w3 = (CW - 2 * gap3) / 3.0
xs = [M, M + w3 + gap3, M + 2 * (w3 + gap3)]
c.setStrokeColor(PLUM)
c.setLineWidth(1.1)
for xc in [x + w3 / 2.0 for x in xs]:
    c.line(xc, y + 7, xc, y - 2)
c.line(xs[0] + w3 / 2.0, y + 7, xs[2] + w3 / 2.0, y + 7)
y -= 6

OUTCOMES = [
    ("TRACK A", "High fit, in the target band", TEAL, TEALBG, [
        "Reviewed at the fortnightly Thursday session.",
        "Top ones prepared for Bea, with a handover brief from Ishu.",
        "Agreed on the Friday partners' call.",
        "The call is with Bea.",
    ]),
    ("TRACK B", "Low or moderate fit, or too early", AMBER, AMBBG, [
        "Reviewed at the Wednesday associates call.",
        "Email 3 from Bea's mailbox introduces the associate.",
        "Email 4 from that associate's own mailbox.",
        "The call is with Issam or Marianna.",
    ]),
    ("KILL", "Not a fit, or too early to pursue", GREY, GREYBG, [
        "Ishu can close a company on his own, no meeting needed.",
        "Wednesday can also kill on review.",
        "Decline from Bea's mailbox: final if not a fit, warmer if only too early.",
        "Moves to Lost, and stops there.",
    ]),
]
h3 = max(outcome_h(w3, s, b) for _, s, _, _, b in
         [(a, s, cl, bg, b) for a, s, cl, bg, b in OUTCOMES])
for (label, sub, col, bgc, bullets), x0 in zip(OUTCOMES, xs):
    outcome_col(x0, w3, y, h3, label, sub, col, bgc, bullets)
y -= h3 + 10

CLOSE = ("After a call the company moves to Meeting and on through DD and Offer as normal. There is no fast path: "
         "a high-fit reply landing the day after a Thursday session waits for the next one.")
y = para(M, y, CW, CLOSE, FO, 7.9, 10.2, MUTED)

check_fits(1, y)
footer(1, "Flow")
c.showPage()


# ══════════════════════════════════════════════════════════════════════════════
# PAGE 2 - Weekly rhythm
# ══════════════════════════════════════════════════════════════════════════════
y = page_header(
    "Averroes Capital  |  Origination",
    "The weekly rhythm that keeps the pipeline moving",
    "Everything is anchored to the partners' deal origination call every second Friday. Ishu's own blocks keep the "
    "funnel moving continuously; the two associate meetings exist so nothing reaches Bea unprepared.",
)

y = section(y, "The two-week cycle")

LEG = {
    1: (PLUM, PLUMBG, "Ishu block"),
    2: (AMBER, AMBBG, "Associates"),
    3: (NAVY, HexColor("#E9EEF4"), "Shortlist"),
    4: (TEAL, TEALBG, "Partners"),
}
cycle = [
    ("WEEK 1", [("Mon", 0), ("Tue", 1), ("Wed", 2), ("Thu", 1), ("Fri", 0)]),
    ("WEEK 2", [("Mon", 0), ("Tue", 1), ("Wed", 2), ("Thu", 13), ("Fri", 4)]),
]
cell_w = (CW - 58 - 4 * 6) / 5.0
cell_h = 31
for wk_label, days in cycle:
    c.setFont(FB, 7.2)
    c.setFillColor(MUTED)
    c.drawString(M, y - cell_h / 2.0 - 2, wk_label)
    xx = M + 58
    for dname, kind in days:
        if kind == 13:      # Thursday week 2 does double duty
            box(xx, y, cell_w, cell_h, fill=PLUMBG, stroke=NAVY, lw=1.0)
            c.setFont(FB, 7.4)
            c.setFillColor(NAVY)
            c.drawCentredString(xx + cell_w / 2.0, y - 12, dname)
            c.setFont(FB, 6.3)
            c.setFillColor(PLUM)
            c.drawCentredString(xx + cell_w / 2.0, y - 21, "Ishu block  +")
            c.setFillColor(NAVY)
            c.drawCentredString(xx + cell_w / 2.0, y - 29, "Shortlist")
        elif kind:
            col, bgc, lbl = LEG[kind]
            box(xx, y, cell_w, cell_h, fill=bgc, stroke=col, lw=1.0)
            c.setFont(FB, 7.4)
            c.setFillColor(col)
            c.drawCentredString(xx + cell_w / 2.0, y - 13, dname)
            c.setFont(FB, 6.5)
            c.drawCentredString(xx + cell_w / 2.0, y - 24, lbl)
        else:
            box(xx, y, cell_w, cell_h, fill=HexColor("#FAFBFC"), stroke=RULE, lw=0.6)
            c.setFont(F, 7.4)
            c.setFillColor(HexColor("#A8B2BD"))
            c.drawCentredString(xx + cell_w / 2.0, y - 13, dname)
        xx += cell_w + 6
    y -= cell_h + 7
y -= 4
y = para(M, y, CW,
         "Ishu's blocks run independently. In week 2 the shortlist session shares that Thursday, but neither waits "
         "on the other.", FO, 7.8, 10.0, MUTED) - 12

y = section(y, "What each commitment owns")

y = table(
    y,
    [0.165, 0.135, 0.135, 0.295, 0.27],
    ["Commitment", "When", "Who", "What happens", "Output"],
    [
        ("Ishu's outreach block", "Every Tuesday and Thursday", "Ishu, alone",
         "Send fresh Email 1s. Send Email 2 to everyone who replied to Email 1. Work through Email 2 replies, "
         "and close out the clear no's with a polite decline.",
         "The Responded page is cleared, and the Wednesday and Thursday agendas are set.", PLUM),
        ("Associates call", "Every Wednesday", "Issam, Marianna, Ishu",
         "Review every Email 2 reply from a low or moderate fit company, or one that is simply too early. Decide "
         "kill or loop in, and allocate the call to whoever has fewer live founder conversations.",
         "Every Track B company is either closed or assigned to a named associate.", AMBER),
        ("Shortlist session", "Every second Thursday, 1 hour, the day before the partners' call",
         "Issam, Marianna, Ishu",
         "Review every Email 2 reply from a high fit, in-band company. Choose the strongest and prepare them, "
         "including Ishu's handover brief so Bea knows what she has already said.",
         "A ranked shortlist with briefs attached, ready for Friday.", NAVY),
        ("Partners' deal origination call", "Every second Friday", "Partners and associates",
         "Review the Thursday shortlist and agree which companies Bea takes a call with.",
         "A go or no-go per company, and Bea's calls booked.", TEAL),
    ],
)
y -= 18

y = section(y, "Standing rules")
RULES = [
    ("Alternation is by load, not by turn",
     "The next call goes to whoever has fewer live founder conversations. This self-corrects after a holiday."),
    ("Ishu's judgement sets the track",
     "Fit score, revenue band and filed accounts are reference. The decision is Ishu's, on what the founder wrote."),
    ("Email 2 is never held for a meeting",
     "It goes out in Ishu's next block. Only Email 2 replies are reviewed on Wednesday and Thursday."),
    ("Nobody is left without an answer",
     "A killed company hears back from Bea's mailbox: a final close if not a fit, a warmer one if only too early."),
    ("No fast path to Bea",
     "A high-fit reply waits for the next fortnightly Thursday. Worst case is two weeks, accepted deliberately."),
]
rw = CW - 26
for t, d in RULES:
    bh = 13 + para_h(d, F, 8.0, 10.2, rw - 14) + 5
    c.setFillColor(NAVY)
    c.circle(M + 5, y - 6, 2.0, stroke=0, fill=1)
    c.setFont(FB, 8.2)
    c.setFillColor(INK)
    c.drawString(M + 14, y - 8.5, t)
    para(M + 14, y - 13, rw - 14, d, F, 8.0, 10.2, BODY)
    y -= bh + 2

check_fits(2, y)
footer(2, "Rhythm")
c.showPage()


# ══════════════════════════════════════════════════════════════════════════════
# PAGE 3 - Roles, standing rules, and the Responded page
# ══════════════════════════════════════════════════════════════════════════════
y = page_header(
    "Averroes Capital  |  Origination",
    "Who does what, and the rules that do not move",
    "Four people, four jobs. The rules below are settled: they are the ones that stop the process drifting when "
    "a week gets busy.",
)

y = section(y, "Roles")
y = table(
    y,
    [0.18, 0.30, 0.52],
    ["Person", "Role", "What they do and do not do"],
    [
        ("Bea", "Partner. Named sender of Emails 1 to 3.",
         "Her mailbox sends Emails 1, 2 and 3, though Ishu operates it. She takes every Track A call, briefed "
         "beforehand by Ishu. She does not attend the Wednesday or Thursday sessions.", PLUM),
        ("Ishu", "Operates the mailbox and triages.",
         "Drafts and sends Emails 1 to 3 from Bea's mailbox. Triages every Email 2 reply into Track A, Track B or "
         "kill. Writes the handover brief for each Track A company. Takes no founder calls.", NAVY),
        ("Issam", "Associate. Track B founder calls.",
         "Takes Track B calls, alternating with Marianna. Email 4 comes from his own mailbox, and he owns the "
         "relationship from that point.", AMBER),
        ("Marianna", "Associate. Track B founder calls.",
         "Same as Issam. Allocation between the two is decided each Wednesday by whoever has fewer live founder "
         "conversations open.", AMBER),
    ],
)
y -= 16

# Responded page spec
y = section(y, "The new Responded page", FLAG)
SPEC_INTRO = ("A dedicated page for every company that has ever replied, filterable so the live queue is visible "
              "without losing the history. It doubles as the Wednesday and Thursday agenda.")
groups = [
    ("Needs Email 2", "Replied to Email 1. Ishu clears these in his Tuesday or Thursday block."),
    ("Needs triage", "Email 2 reply received. Ishu picks Track A, Track B or kill."),
    ("Track A, awaiting Thursday", "Triaged high fit. Sitting for the next fortnightly shortlist session."),
    ("Track B, awaiting Wednesday", "Triaged low or moderate fit, or too early. Sitting for the weekly associates call."),
    ("Assigned, call pending", "An associate owns it and Email 4 has gone out."),
    ("Closed", "Killed or declined, with the reason kept."),
]
extras = [
    "An assign control on every row: Bea, Ishu, Issam or Marianna. One owner field, and it changes hands on loop-in.",
    "An owner tag on the company card, the Universe table and the Pipeline board, so who is managing a company is "
    "visible everywhere, not just here.",
    "A live count of open founder conversations per associate, so the Wednesday allocation is a fact rather than a memory.",
    "Two decline drafts: a final close for a genuine no, a warmer one for a company that is only too early.",
]
sw = CW - 30
gw_k, gw_v = 132.0, CW - 30 - 132.0 - 8
bh = 18 + para_h(SPEC_INTRO, F, 8.1, 10.4, sw) + 7 \
     + sum(max(para_h(k, FB, 7.8, 10.0, gw_k), para_h(v, F, 7.8, 10.0, gw_v)) + 2.5 for k, v in groups) + 7 \
     + sum(para_h(e, F, 7.9, 10.0, sw - 12) + 3.5 for e in extras) + 6
box(M, y, CW, bh, fill=PANEL, stroke=FLAG, lw=0.9)
c.setFillColor(FLAG)
c.rect(M, y - bh, 2.6, bh, stroke=0, fill=1)
c.setFont(FB, 8.6)
c.setFillColor(FLAG)
c.drawString(M + 12, y - 13, "To build")
yy = para(M + 62, y - 5, sw - 50, SPEC_INTRO, F, 8.1, 10.4, BODY) - 7
for k, v in groups:
    rh = max(para_h(k, FB, 7.8, 10.0, gw_k), para_h(v, F, 7.8, 10.0, gw_v)) + 2.5
    para(M + 16, yy, gw_k, k, FB, 7.8, 10.0, INK)
    para(M + 16 + gw_k + 8, yy, gw_v, v, F, 7.8, 10.0, BODY)
    yy -= rh
yy -= 5
for e in extras:
    c.setFillColor(FLAG)
    c.circle(M + 18, yy - 3.6, 1.6, stroke=0, fill=1)
    yy = para(M + 24, yy + 0.5, sw - 12, e, F, 7.9, 10.0, BODY) - 3.5

check_fits(3, y - bh)
footer(3, "Roles and rules")
c.showPage()


# ══════════════════════════════════════════════════════════════════════════════
# PAGE 4 - Assumptions + open questions
# ══════════════════════════════════════════════════════════════════════════════
y = page_header(
    "Averroes Capital  |  Origination",
    "Settled, and what is still open",
    "The decisions below are agreed and the tool work on page 3 can proceed on them. The remaining questions do "
    "not block the build, but they will decide how the process feels in practice.",
)

y = section(y, "Settled", TEAL)
SETTLED = [
    ("Ishu can kill a company on his own",
     "No meeting needed. Wednesday can also kill on review, but Ishu is not waiting for it."),
    ("Ishu's Tuesday and Thursday blocks are independent",
     "The fortnightly shortlist session happens to share a Thursday, but neither waits on the other."),
    ("Email 3 from Bea, Email 4 from the associate",
     "Bea's mailbox introduces Issam or Marianna, then that associate writes from their own address."),
    ("Bea does not attend Wednesday or Thursday",
     "Her involvement starts at the Friday partners' call and the Track A call itself."),
    ("One owner field, and it changes hands",
     "The tag moves from Ishu to the associate on loop-in. Open-call counts are still derived from it per person."),
    ("Two kinds of decline",
     "A final close for a genuine no; a warmer one for a company that is only too early. Both from Bea's mailbox."),
]
sw4 = CW - 30
for t, d in SETTLED:
    bh = 12.5 + para_h(d, F, 7.9, 10.2, sw4 - 14) + 4
    c.setFillColor(TEAL)
    c.circle(M + 5, y - 6, 2.0, stroke=0, fill=1)
    c.setFont(FB, 8.2)
    c.setFillColor(INK)
    c.drawString(M + 14, y - 8.5, t)
    para(M + 14, y - 12.5, sw4 - 14, d, F, 7.9, 10.2, BODY)
    y -= bh + 2
y -= 14

y = section(y, "Still open")
KEYW = 122.0
QX = M + 15 + KEYW + 9
QW = W - M - QX
QUESTIONS = [
    ("After the associate call",
     "A Track B call has happened. Does the company come back to a Wednesday review, go into nurture, or move "
     "straight to Meeting on the board?"),
    ("Who can reassign",
     "Can any of the three change an owner, or only Ishu? Defaulting to anyone, with the change recorded."),
    ("Chasing after Email 2",
     "If the numbers never arrive, how many follow-ups, spaced how far apart, before the company is closed?"),
    ("Both associates at capacity",
     "A Track B company is ready to allocate but Issam and Marianna are both full. Does it wait, or does Ishu "
     "take the call after all?"),
    ("Too early, re-contact window",
     "Is there a defined period before we go back to a company that was too early, six months say, or is it "
     "judged case by case?"),
    ("Bea being caught out",
     "If a founder references something 'Bea' wrote, she needs to recognise it. Is the handover brief enough, or "
     "should she also read the thread before every Track A call?"),
]
for k, q in QUESTIONS:
    rh = max(para_h(k, FB, 8.1, 10.4, KEYW), para_h(q, F, 8.1, 10.4, QW)) + 4.0
    c.setFillColor(NAVY)
    c.circle(M + 5, y - 6, 2.0, stroke=0, fill=1)
    para(M + 15, y, KEYW, k, FB, 8.1, 10.4, INK)
    para(QX, y, QW, q, F, 8.1, 10.4, BODY)
    y -= rh

check_fits(4, y)
footer(4, "Decisions")
c.save()
print("OK Averroes_Deal_Pipeline_Process.pdf")
