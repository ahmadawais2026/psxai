"""
report/pdf_generator.py
Generates a professional PDF investment report from a PSX analysis dict.
Uses ReportLab (Platypus high-level API).
"""

from __future__ import annotations

import io
from datetime import datetime
from typing import Any, Dict, List, Optional

from reportlab.lib import colors
from reportlab.lib.enums import TA_CENTER, TA_LEFT
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
from reportlab.lib.units import cm
from reportlab.platypus import (
    BaseDocTemplate, Frame, HRFlowable, PageBreak, PageTemplate,
    Paragraph, Spacer, Table, TableStyle,
)


def _safe(text: Any) -> str:
    """Escape HTML special chars so ReportLab's XML parser doesn't crash."""
    if text is None:
        return ""
    s = str(text)
    s = s.replace("&", "&amp;")
    s = s.replace("<", "&lt;")
    s = s.replace(">", "&gt;")
    # Strip any non-Latin1 characters ReportLab can't render with Helvetica
    s = s.encode("latin-1", errors="replace").decode("latin-1")
    return s

# ── Colour palette (matches the web app) ────────────────────────────
C_GREEN   = colors.HexColor("#10b981")
C_CYAN    = colors.HexColor("#06b6d4")
C_RED     = colors.HexColor("#f43f5e")
C_AMBER   = colors.HexColor("#f59e0b")
C_ORANGE  = colors.HexColor("#f97316")
C_GRAY    = colors.HexColor("#64748b")
C_DARK    = colors.HexColor("#0f172a")
C_SURFACE = colors.HexColor("#f8fafc")
C_BORDER  = colors.HexColor("#e2e8f0")
C_WHITE   = colors.white

W, H = A4   # 595 × 842 pts


# ── Style factory ────────────────────────────────────────────────────

def _styles() -> Dict[str, ParagraphStyle]:
    s = {}
    s["cover_company"] = ParagraphStyle(
        "cover_company", fontSize=26, leading=32, fontName="Helvetica-Bold",
        textColor=C_DARK, spaceAfter=4,
    )
    s["cover_sub"] = ParagraphStyle(
        "cover_sub", fontSize=12, leading=16, fontName="Helvetica",
        textColor=C_GRAY, spaceAfter=6,
    )
    s["cover_date"] = ParagraphStyle(
        "cover_date", fontSize=9, leading=12, fontName="Helvetica",
        textColor=C_GRAY, spaceAfter=0,
    )
    s["rec_badge"] = ParagraphStyle(
        "rec_badge", fontSize=20, leading=26, fontName="Helvetica-Bold",
        textColor=C_WHITE, alignment=TA_CENTER,
    )
    s["section"] = ParagraphStyle(
        "section", fontSize=12, leading=16, fontName="Helvetica-Bold",
        textColor=C_DARK, spaceBefore=10, spaceAfter=4,
    )
    s["sub"] = ParagraphStyle(
        "sub", fontSize=9, leading=13, fontName="Helvetica-Bold",
        textColor=C_GRAY, spaceBefore=6, spaceAfter=2,
    )
    s["body"] = ParagraphStyle(
        "body", fontSize=9, leading=13, fontName="Helvetica",
        textColor=C_DARK, spaceAfter=3,
    )
    s["italic"] = ParagraphStyle(
        "italic", fontSize=9, leading=13, fontName="Helvetica-Oblique",
        textColor=C_GRAY, spaceAfter=3,
    )
    s["bullet"] = ParagraphStyle(
        "bullet", fontSize=9, leading=13, fontName="Helvetica",
        textColor=C_DARK, leftIndent=10, spaceAfter=2,
    )
    s["tc"] = ParagraphStyle(
        "tc", fontSize=8, leading=11, fontName="Helvetica",
        textColor=C_DARK, alignment=TA_CENTER,
    )
    s["disclaimer"] = ParagraphStyle(
        "disclaimer", fontSize=7, leading=10, fontName="Helvetica",
        textColor=C_GRAY,
    )
    return s


# ── Colour helpers ───────────────────────────────────────────────────

def _rec_color(rec: str) -> colors.Color:
    r = rec.upper()
    if "STRONG BUY"  in r: return colors.HexColor("#059669")
    if "BUY"         in r: return C_GREEN
    if "ACCUMULATE"  in r: return C_CYAN
    if "HOLD"        in r: return C_AMBER
    if "TRIM"        in r: return C_ORANGE
    if "STRONG SELL" in r: return colors.HexColor("#be123c")
    if "SELL"        in r: return C_RED
    return C_GRAY


def _risk_color(risk: str) -> colors.Color:
    r = risk.lower()
    if r == "low":       return C_GREEN
    if r == "medium":    return C_AMBER
    if r == "high":      return C_ORANGE
    if r == "very_high": return C_RED
    return C_GRAY


def _sent_color(s: str) -> colors.Color:
    s = s.lower()
    if s == "bullish": return C_GREEN
    if s == "bearish": return C_RED
    return C_GRAY


# ── Low-level helpers ────────────────────────────────────────────────

def _hr(color: colors.Color = C_BORDER, thickness: float = 0.5) -> HRFlowable:
    return HRFlowable(width="100%", thickness=thickness, color=color,
                      spaceAfter=6, spaceBefore=2)


def _header_table(header_row: List[str], data_row: List[str],
                  col_width: float, header_bg: colors.Color) -> Table:
    """One-row header + one-row data summary table."""
    t = Table([header_row, data_row], colWidths=[col_width] * len(header_row))
    t.setStyle(TableStyle([
        ("BACKGROUND",    (0, 0), (-1, 0), header_bg),
        ("TEXTCOLOR",     (0, 0), (-1, 0), C_WHITE),
        ("FONTNAME",      (0, 0), (-1, 0), "Helvetica-Bold"),
        ("FONTNAME",      (0, 1), (-1, 1), "Helvetica"),
        ("FONTSIZE",      (0, 0), (-1, -1), 8),
        ("ALIGN",         (0, 0), (-1, -1), "CENTER"),
        ("VALIGN",        (0, 0), (-1, -1), "MIDDLE"),
        ("TOPPADDING",    (0, 0), (-1, -1), 6),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 6),
        ("GRID",          (0, 0), (-1, -1), 0.4, C_BORDER),
        ("BACKGROUND",    (0, 1), (-1, 1), C_SURFACE),
    ]))
    return t


def _two_col_list(left_heading: str, left_items: List[str],
                  right_heading: str, right_items: List[str],
                  col_width: float, st: Dict) -> Table:
    """Side-by-side bullet list table."""
    rows = [[Paragraph(_safe(left_heading), st["sub"]), Paragraph(_safe(right_heading), st["sub"])]]
    n = max(len(left_items), len(right_items))
    for i in range(min(n, 6)):
        l = f"+ {left_items[i]}"  if i < len(left_items)  else ""
        r = f"- {right_items[i]}" if i < len(right_items) else ""
        rows.append([Paragraph(_safe(l), st["body"]), Paragraph(_safe(r), st["body"])])
    t = Table(rows, colWidths=[col_width, col_width])
    t.setStyle(TableStyle([
        ("VALIGN",        (0, 0), (-1, -1), "TOP"),
        ("TOPPADDING",    (0, 0), (-1, -1), 3),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 2),
        ("LINEAFTER",     (0, 0), (0, -1), 0.4, C_BORDER),
    ]))
    return t


# ── Page decorator ───────────────────────────────────────────────────

def _page_deco(canvas, doc):
    canvas.saveState()
    # Top bar
    canvas.setFillColor(C_DARK)
    canvas.rect(0, H - 28, W, 28, fill=1, stroke=0)
    canvas.setFillColor(C_GREEN)
    canvas.setFont("Helvetica-Bold", 10)
    canvas.drawString(56, H - 18, "PSX ADVISOR")
    canvas.setFillColor(C_WHITE)
    canvas.setFont("Helvetica", 8)
    canvas.drawRightString(W - 56, H - 18, f"Page {doc.page}")
    # Bottom line
    canvas.setFillColor(C_GRAY)
    canvas.setFont("Helvetica-Oblique", 6)
    canvas.drawString(56, 18, "For educational purposes only · Not financial advice · PSX Advisor AI")
    canvas.restoreState()


# ── Main entry point ─────────────────────────────────────────────────

def generate_pdf(report: Dict[str, Any]) -> bytes:
    """
    Build and return a PDF investment report from *report* dict.
    The dict is the full JSON returned by /api/analyze.
    """
    buf = io.BytesIO()
    st = _styles()

    doc = BaseDocTemplate(
        buf, pagesize=A4,
        leftMargin=2*cm, rightMargin=2*cm,
        topMargin=2.2*cm, bottomMargin=1.8*cm,
        title=f"PSX Analysis — {report.get('symbol', '')}",
        author="PSX Advisor AI",
    )
    frame = Frame(doc.leftMargin, doc.bottomMargin, doc.width, doc.height, id="main")
    doc.addPageTemplates([PageTemplate(id="main", frames=[frame], onPage=_page_deco)])

    story: list = []
    rec    = report.get("recommendation", {})
    tech   = report.get("technical_report", {})
    fund   = report.get("fundamental_report", {})
    sent   = report.get("sentiment_report", {})
    risk   = report.get("risk_report", {})
    debate = report.get("debate", {})
    sym    = report.get("symbol", "")
    name   = report.get("company_name", sym)
    sector = report.get("sector", "")
    ts     = report.get("timestamp", datetime.now().strftime("%Y-%m-%d %H:%M"))
    dw     = doc.width

    # ═══════════════════════════ COVER ══════════════════════════════
    story.append(Spacer(1, 0.6*cm))
    story.append(Paragraph(_safe(name), st["cover_company"]))
    story.append(Paragraph(_safe(f"{sym}  ·  {sector}"), st["cover_sub"]))
    story.append(Paragraph(_safe(f"Report Generated: {ts}"), st["cover_date"]))
    story.append(Spacer(1, 0.5*cm))

    rec_text = rec.get("recommendation", "N/A").upper()
    rec_col  = _rec_color(rec_text)
    rec_tbl = Table([[Paragraph(rec_text, st["rec_badge"])]], colWidths=[dw])
    rec_tbl.setStyle(TableStyle([
        ("BACKGROUND",    (0, 0), (-1, -1), rec_col),
        ("TOPPADDING",    (0, 0), (-1, -1), 10),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 10),
        ("ROUNDEDCORNERS", [6]),
    ]))
    story.append(rec_tbl)
    story.append(Spacer(1, 0.35*cm))

    # Price-target summary row
    current   = rec.get("current_price") or report.get("quote", {}).get("price", 0) or 0
    pt_low    = rec.get("price_target_low", 0) or 0
    pt_high   = rec.get("price_target_high", 0) or 0
    upside    = rec.get("upside_pct", 0) or 0
    horizon   = rec.get("time_horizon", "—").replace("_", " ").title()
    conf      = rec.get("confidence", 0)

    story.append(_header_table(
        ["Current Price", "Price Target", "Upside Potential", "Time Horizon", "Confidence"],
        [
            f"PKR {current:,.2f}" if current else "—",
            f"PKR {pt_low:,.0f}–{pt_high:,.0f}" if (pt_low and pt_high) else "—",
            f"{upside:+.1f}%" if upside else "—",
            horizon,
            f"{conf}/10",
        ],
        col_width=dw / 5,
        header_bg=C_DARK,
    ))
    story.append(Spacer(1, 0.3*cm))

    # Executive summary
    if rec.get("summary"):
        story.append(Paragraph("EXECUTIVE SUMMARY", st["section"]))
        story.append(_hr())
        story.append(Paragraph(_safe(rec["summary"]), st["body"]))
    if rec.get("position_advice"):
        story.append(Paragraph(_safe(f"Position Advice: {rec['position_advice']}"), st["italic"]))

    # Catalysts / Risks
    cats = rec.get("catalysts", []) or []
    rsks = rec.get("risks", []) or []
    if cats or rsks:
        story.append(Spacer(1, 0.2*cm))
        story.append(_two_col_list("CATALYSTS", cats, "KEY RISKS", rsks, dw / 2, st))

    story.append(PageBreak())

    # ═══════════════════════ TECHNICAL ══════════════════════════════
    story.append(Paragraph("TECHNICAL ANALYSIS", st["section"]))
    story.append(_hr(C_CYAN))

    trend  = tech.get("trend", "unknown").upper()
    tstr   = tech.get("trend_strength", "").replace("_", " ").title()
    entry  = tech.get("entry_zone", {})
    sl     = tech.get("stop_loss", 0)
    tc     = tech.get("confidence", 0)

    story.append(_header_table(
        ["Trend", "Strength", "Entry Zone", "Stop Loss", "Confidence"],
        [
            trend, tstr,
            f"PKR {entry.get('low', 0):,.2f}–{entry.get('high', 0):,.2f}" if entry else "—",
            f"PKR {sl:,.2f}" if sl else "—",
            f"{tc}/10",
        ],
        col_width=dw / 5, header_bg=C_CYAN,
    ))
    story.append(Spacer(1, 0.2*cm))

    if tech.get("summary"):
        story.append(Paragraph(_safe(tech["summary"]), st["body"]))

    # Key levels
    levels = tech.get("key_levels", {}) or {}
    sup    = [x for x in (levels.get("support", []) or []) if x]
    res    = [x for x in (levels.get("resistance", []) or []) if x]
    if sup or res:
        story.append(Paragraph("Key Price Levels", st["sub"]))
        lv = Table(
            [["Support Levels", "Resistance Levels"],
             [" | ".join([f"{x:,.2f}" for x in sup[:5]]) or "—",
              " | ".join([f"{x:,.2f}" for x in res[:5]]) or "—"]],
            colWidths=[dw / 2, dw / 2],
        )
        lv.setStyle(TableStyle([
            ("BACKGROUND", (0, 0), (-1, 0), C_DARK),
            ("TEXTCOLOR",  (0, 0), (-1, 0), C_WHITE),
            ("FONTNAME",   (0, 0), (-1, 0), "Helvetica-Bold"),
            ("FONTSIZE",   (0, 0), (-1, -1), 9),
            ("ALIGN",      (0, 0), (-1, -1), "CENTER"),
            ("TOPPADDING", (0, 0), (-1, -1), 6),
            ("BOTTOMPADDING", (0, 0), (-1, -1), 6),
            ("GRID",       (0, 0), (-1, -1), 0.4, C_BORDER),
            ("BACKGROUND", (0, 1), (-1, 1), C_SURFACE),
        ]))
        story.append(lv)

    # Signals table
    sigs = tech.get("signals", []) or []
    if sigs:
        story.append(Paragraph("Technical Signals", st["sub"]))
        rows = [["Indicator", "Reading", "Interpretation"]]
        for s in sigs[:8]:
            rows.append([_safe(s.get("indicator","")), _safe(s.get("reading","")),
                         Paragraph(_safe(s.get("interpretation","")), st["body"])])
        st_tbl = Table(rows, colWidths=[dw*0.20, dw*0.18, dw*0.62])
        st_tbl.setStyle(TableStyle([
            ("BACKGROUND",    (0, 0), (-1, 0), C_DARK),
            ("TEXTCOLOR",     (0, 0), (-1, 0), C_WHITE),
            ("FONTNAME",      (0, 0), (-1, 0), "Helvetica-Bold"),
            ("FONTSIZE",      (0, 0), (-1, -1), 8),
            ("ALIGN",         (0, 0), (1, -1), "CENTER"),
            ("VALIGN",        (0, 0), (-1, -1), "TOP"),
            ("TOPPADDING",    (0, 0), (-1, -1), 4),
            ("BOTTOMPADDING", (0, 0), (-1, -1), 4),
            ("ROWBACKGROUNDS",(0, 1), (-1, -1), [C_WHITE, C_SURFACE]),
            ("GRID",          (0, 0), (-1, -1), 0.4, C_BORDER),
        ]))
        story.append(st_tbl)

    story.append(Spacer(1, 0.4*cm))

    # ═════════════════════ FUNDAMENTAL ══════════════════════════════
    story.append(Paragraph("FUNDAMENTAL ANALYSIS", st["section"]))
    story.append(_hr(C_GREEN))

    verdict = fund.get("valuation_verdict", "—").replace("_", " ").upper()
    health  = fund.get("financial_health", "—").replace("_", " ").upper()
    growth  = fund.get("growth_outlook", "—").replace("_", " ").upper()
    moat    = fund.get("moat", "—").upper()
    fc      = fund.get("confidence", 0)

    story.append(_header_table(
        ["Valuation", "Financial Health", "Growth Outlook", "Moat", "Confidence"],
        [verdict, health, growth, moat, f"{fc}/10"],
        col_width=dw / 5, header_bg=C_GREEN,
    ))
    story.append(Spacer(1, 0.2*cm))

    if fund.get("summary"):
        story.append(Paragraph(_safe(fund["summary"]), st["body"]))

    fv = fund.get("fair_value_range") or {}
    if fv.get("low") or fv.get("high"):
        story.append(Paragraph(
            _safe(f"Estimated Fair Value: PKR {fv.get('low', 0):,.0f} - PKR {fv.get('high', 0):,.0f}"),
            st["italic"],
        ))

    strengths = fund.get("strengths", []) or []
    concerns  = fund.get("concerns", [])  or []
    if strengths or concerns:
        story.append(_two_col_list("STRENGTHS", strengths, "CONCERNS", concerns, dw / 2, st))

    story.append(PageBreak())

    # ══════════════════════ SENTIMENT ═══════════════════════════════
    story.append(Paragraph("SENTIMENT ANALYSIS", st["section"]))
    story.append(_hr(_sent_color(sent.get("overall_sentiment", "neutral"))))

    over    = sent.get("overall_sentiment", "—").upper()
    score   = sent.get("sentiment_score", 0)
    vol     = sent.get("news_volume", "—").upper()
    sc      = sent.get("confidence", 0)

    story.append(_header_table(
        ["Overall Sentiment", "Score", "News Volume", "Confidence"],
        [over, str(score), vol, f"{sc}/10"],
        col_width=dw / 4, header_bg=C_DARK,
    ))
    story.append(Spacer(1, 0.2*cm))

    if sent.get("summary"):
        story.append(Paragraph(_safe(sent["summary"]), st["body"]))

    narratives = sent.get("key_narratives", []) or []
    if narratives:
        story.append(Paragraph("Key Sentiment Drivers", st["sub"]))
        for n in narratives[:6]:
            story.append(Paragraph(_safe(f"* {n}"), st["bullet"]))

    pos_cats = sent.get("catalysts_positive", []) or []
    neg_cats = sent.get("catalysts_negative", []) or []
    if pos_cats or neg_cats:
        story.append(_two_col_list("POSITIVE CATALYSTS", pos_cats, "NEGATIVE CATALYSTS", neg_cats, dw / 2, st))

    # News articles
    articles = sent.get("articles", []) or []
    if articles:
        story.append(Paragraph("Recent News & Announcements", st["sub"]))
        rows = [["Date", "Headline", "Source"]]
        for art in articles[:10]:
            title  = art.get("title") or art.get("Title") or art.get("headline") or "-"
            date   = (art.get("published") or art.get("date") or art.get("Date") or "-")[:10]
            source = art.get("source") or art.get("Source") or art.get("publisher") or "-"
            rows.append([Paragraph(_safe(date), st["tc"]),
                         Paragraph(_safe(str(title)[:120]), st["body"]),
                         Paragraph(_safe(str(source)[:30]), st["tc"])])
        news_tbl = Table(rows, colWidths=[dw*0.12, dw*0.70, dw*0.18])
        news_tbl.setStyle(TableStyle([
            ("BACKGROUND",    (0, 0), (-1, 0), C_DARK),
            ("TEXTCOLOR",     (0, 0), (-1, 0), C_WHITE),
            ("FONTNAME",      (0, 0), (-1, 0), "Helvetica-Bold"),
            ("FONTSIZE",      (0, 0), (-1, -1), 8),
            ("VALIGN",        (0, 0), (-1, -1), "TOP"),
            ("TOPPADDING",    (0, 0), (-1, -1), 4),
            ("BOTTOMPADDING", (0, 0), (-1, -1), 4),
            ("ROWBACKGROUNDS",(0, 1), (-1, -1), [C_WHITE, C_SURFACE]),
            ("GRID",          (0, 0), (-1, -1), 0.4, C_BORDER),
        ]))
        story.append(news_tbl)

    story.append(Spacer(1, 0.4*cm))

    # ═══════════════════════ RISK ════════════════════════════════════
    story.append(Paragraph("RISK ASSESSMENT", st["section"]))
    story.append(_hr(_risk_color(risk.get("risk_level", "medium"))))

    rlevel  = risk.get("risk_level", "—").replace("_", " ").upper()
    rscore  = risk.get("risk_score", 0)
    maxpos  = risk.get("max_position_pct", 0) or 0
    stoploss= risk.get("stop_loss_pct", 0) or 0
    rconf   = risk.get("confidence", 0)

    story.append(_header_table(
        ["Risk Level", "Risk Score", "Max Position", "Stop Loss", "Confidence"],
        [rlevel, f"{rscore}/10",
         f"{maxpos}%" if maxpos else "—",
         f"{stoploss}%" if stoploss else "—",
         f"{rconf}/10"],
        col_width=dw / 5, header_bg=C_DARK,
    ))
    story.append(Spacer(1, 0.2*cm))

    if risk.get("summary"):
        story.append(Paragraph(_safe(risk["summary"]), st["body"]))

    rfs = risk.get("risk_factors", []) or []
    if rfs:
        story.append(Paragraph("Identified Risk Factors", st["sub"]))
        rf_rows = [["Factor", "Severity", "Detail"]]
        for rf in rfs[:8]:
            rf_rows.append([
                Paragraph(_safe(rf.get("factor", "")), st["body"]),
                Paragraph(_safe(rf.get("severity", "").upper()), st["tc"]),
                Paragraph(_safe(rf.get("detail", "")), st["body"]),
            ])
        rf_tbl = Table(rf_rows, colWidths=[dw*0.25, dw*0.12, dw*0.63])
        rf_tbl.setStyle(TableStyle([
            ("BACKGROUND",    (0, 0), (-1, 0), C_DARK),
            ("TEXTCOLOR",     (0, 0), (-1, 0), C_WHITE),
            ("FONTNAME",      (0, 0), (-1, 0), "Helvetica-Bold"),
            ("FONTSIZE",      (0, 0), (-1, -1), 8),
            ("VALIGN",        (0, 0), (-1, -1), "TOP"),
            ("TOPPADDING",    (0, 0), (-1, -1), 4),
            ("BOTTOMPADDING", (0, 0), (-1, -1), 4),
            ("ROWBACKGROUNDS",(0, 1), (-1, -1), [C_WHITE, C_SURFACE]),
            ("GRID",          (0, 0), (-1, -1), 0.4, C_BORDER),
        ]))
        story.append(rf_tbl)

    story.append(PageBreak())

    # ═══════════════════════ DEBATE ══════════════════════════════════
    story.append(Paragraph("INVESTMENT DEBATE: BULL vs BEAR", st["section"]))
    story.append(_hr())

    bull_thesis = debate.get("bull_thesis", "")
    bear_thesis = debate.get("bear_thesis", "")
    bull_args   = debate.get("bull_arguments", []) or []
    bear_args   = debate.get("bear_arguments", []) or []

    if bull_thesis or bear_thesis:
        th_tbl = Table(
            [[Paragraph(_safe(f'BULL: "{bull_thesis}"'), st["italic"]),
              Paragraph(_safe(f'BEAR: "{bear_thesis}"'), st["italic"])]],
            colWidths=[dw / 2, dw / 2],
        )
        th_tbl.setStyle(TableStyle([
            ("BACKGROUND", (0, 0), (0, 0), colors.HexColor("#f0fdf4")),
            ("BACKGROUND", (1, 0), (1, 0), colors.HexColor("#fff1f2")),
            ("VALIGN",     (0, 0), (-1, -1), "TOP"),
            ("TOPPADDING", (0, 0), (-1, -1), 8),
            ("BOTTOMPADDING", (0, 0), (-1, -1), 8),
            ("LEFTPADDING",   (0, 0), (-1, -1), 8),
            ("LINEAFTER",  (0, 0), (0, -1), 0.8, C_BORDER),
            ("BOX",        (0, 0), (-1, -1), 0.4, C_BORDER),
        ]))
        story.append(th_tbl)
        story.append(Spacer(1, 0.2*cm))

    if bull_args or bear_args:
        rows = [[Paragraph("BULL CASE", st["sub"]), Paragraph("BEAR CASE", st["sub"])]]
        for i in range(min(max(len(bull_args), len(bear_args)), 6)):
            bl = ""
            br = ""
            if i < len(bull_args):
                a = bull_args[i]
                bl = f"+ {a.get('point','')}\n{a.get('evidence','')}"
            if i < len(bear_args):
                a = bear_args[i]
                br = f"- {a.get('point','')}\n{a.get('evidence','')}"
            rows.append([Paragraph(_safe(bl), st["body"]), Paragraph(_safe(br), st["body"])])
        db_tbl = Table(rows, colWidths=[dw / 2, dw / 2])
        db_tbl.setStyle(TableStyle([
            ("VALIGN",        (0, 0), (-1, -1), "TOP"),
            ("TOPPADDING",    (0, 0), (-1, -1), 4),
            ("BOTTOMPADDING", (0, 0), (-1, -1), 4),
            ("LINEAFTER",     (0, 0), (0, -1), 0.4, C_BORDER),
        ]))
        story.append(db_tbl)

    story.append(Spacer(1, 0.5*cm))

    # ═══════════════════════ DISCLAIMER ══════════════════════════════
    story.append(_hr())
    story.append(Paragraph("DISCLAIMER", st["sub"]))
    disclaimer = report.get("disclaimer", (
        "This analysis is generated by AI for educational purposes only. "
        "It is NOT financial advice. Always consult a qualified financial "
        "advisor before making investment decisions."
    ))
    story.append(Paragraph(_safe(disclaimer), st["disclaimer"]))

    doc.build(story)
    return buf.getvalue()
