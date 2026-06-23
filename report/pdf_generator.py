"""
report/pdf_generator.py
Generates a comprehensive 5-page professional investment research report.
Uses ReportLab Platypus.
"""

from __future__ import annotations

import io
import re
from datetime import datetime
from typing import Any, Dict, List, Optional, Tuple

try:
    import pandas as pd
    _HAS_PD = True
except ImportError:
    _HAS_PD = False

from reportlab.lib import colors
from reportlab.lib.enums import TA_CENTER, TA_LEFT, TA_RIGHT
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle
from reportlab.lib.units import cm
from reportlab.platypus import (
    BaseDocTemplate, Frame, HRFlowable, PageBreak,
    PageTemplate, Paragraph, Spacer, Table, TableStyle,
)

# ── Colour palette ────────────────────────────────────────────────────────────
C_GREEN   = colors.HexColor("#10b981")
C_CYAN    = colors.HexColor("#06b6d4")
C_RED     = colors.HexColor("#f43f5e")
C_AMBER   = colors.HexColor("#f59e0b")
C_ORANGE  = colors.HexColor("#f97316")
C_GRAY    = colors.HexColor("#64748b")
C_LGRAY   = colors.HexColor("#f1f5f9")
C_DARK    = colors.HexColor("#0f172a")
C_SURFACE = colors.HexColor("#f8fafc")
C_BORDER  = colors.HexColor("#e2e8f0")
C_WHITE   = colors.white
C_ACCENT  = colors.HexColor("#1e40af")

W, H = A4  # 595 x 842 pts


# ── Text safety ───────────────────────────────────────────────────────────────

# Smart punctuation / symbols → ASCII or WinAnsi-safe equivalents so the PDF's
# Helvetica font renders them instead of dropping them to "?".
_UNICODE_MAP = {
    "—": "-", "–": "-", "‒": "-", "−": "-",   # dashes
    "‘": "'", "’": "'", "‚": "'", "‛": "'",   # single quotes
    "“": '"', "”": '"', "„": '"',                  # double quotes
    "…": "...", "•": "*", "·": "*", "⁃": "-",  # ellipsis/bullets
    "→": "->", "←": "<-", "↔": "<->",              # arrows
    " ": " ", "​": "", "﻿": "",                    # spaces/BOM
}


def _normalize_text(s: str) -> str:
    """Map smart punctuation to ASCII and drop characters outside Latin-1
    (e.g. emoji) so they vanish cleanly instead of becoming "?" glyphs."""
    for k, v in _UNICODE_MAP.items():
        if k in s:
            s = s.replace(k, v)
    return s.encode("latin-1", errors="ignore").decode("latin-1")


def _safe(text: Any, maxlen: int = 0) -> str:
    if text is None:
        return ""
    s = _normalize_text(str(text))
    s = s.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
    return s[:maxlen] if maxlen else s


# ── Number formatting ─────────────────────────────────────────────────────────

def _is_nan(v: Any) -> bool:
    if v is None:
        return True
    try:
        import math
        return math.isnan(float(v))
    except (TypeError, ValueError):
        return False


def _fmt_mn(val: Any) -> str:
    """Format a value already in PKR millions."""
    if val is None or _is_nan(val):
        return "-"
    try:
        v = float(val)
        if v == 0:
            return "-"
        if abs(v) >= 1_000:
            return f"{v:,.0f}"
        return f"{v:,.1f}"
    except (TypeError, ValueError):
        return "-"


def _pct(val: Any) -> str:
    if val is None or _is_nan(val):
        return "-"
    try:
        v = float(val)
        if abs(v) < 50:
            return f"{v * 100:.1f}%"
        return f"{v:.1f}%"
    except (TypeError, ValueError):
        return "-"


def _clean_date(raw: Any) -> str:
    """Sanitize an announcement date for display.

    Source dates arrive wrapped/trailing ("[April 28, ]", "[February 2]",
    "December 31, 20 -- PSX Portal"). The old blind ``[:10]`` slice mangled valid
    dates ("December 3"). Strip brackets and trailing source, keep the ISO date
    part when present, otherwise a sane prefix.
    """
    s = str(raw or "").strip()
    if not s:
        return ""
    s = re.sub(r"\s*--.*$", "", s)        # drop trailing " -- Source"
    s = s.strip("[]() \t").rstrip(", ")
    m = re.match(r"\d{4}-\d{2}-\d{2}", s)  # already ISO → keep date part
    if m:
        return m.group(0)
    return s[:20]


def _mktcap_str(v: Any) -> str:
    if not v or _is_nan(v):
        return "-"
    try:
        v = float(v)
        if v >= 1e12: return f"PKR {v / 1e12:.2f}T"
        if v >= 1e9:  return f"PKR {v / 1e9:.1f}B"
        if v >= 1e6:  return f"PKR {v / 1e6:.0f}M"
        return f"PKR {v:,.0f}"
    except (TypeError, ValueError):
        return "-"


# ── Colour helpers ────────────────────────────────────────────────────────────

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
    if "low"  in r: return C_GREEN
    if "high" in r: return C_RED
    if "med"  in r: return C_AMBER
    return C_GRAY


def _sent_color(s: str) -> colors.Color:
    s = s.lower()
    if "bull" in s: return C_GREEN
    if "bear" in s: return C_RED
    return C_AMBER


# ── Style factory ─────────────────────────────────────────────────────────────

def _styles() -> Dict[str, ParagraphStyle]:
    st: Dict[str, ParagraphStyle] = {}

    def add(name: str, **kw):
        st[name] = ParagraphStyle(name, **kw)

    add("h_company",  fontSize=22, leading=28, fontName="Helvetica-Bold", textColor=C_DARK, spaceAfter=2)
    add("h_sub",      fontSize=11, leading=15, fontName="Helvetica",      textColor=C_GRAY, spaceAfter=4)
    add("h_date",     fontSize=8,  leading=11, fontName="Helvetica",      textColor=C_GRAY, spaceAfter=0)
    add("rec_badge",  fontSize=18, leading=24, fontName="Helvetica-Bold", textColor=C_WHITE, alignment=TA_CENTER)
    add("section",    fontSize=11, leading=15, fontName="Helvetica-Bold", textColor=C_DARK, spaceBefore=8, spaceAfter=3)
    add("sub",        fontSize=9,  leading=13, fontName="Helvetica-Bold", textColor=C_GRAY, spaceBefore=5, spaceAfter=2)
    add("body",       fontSize=9,  leading=13, fontName="Helvetica",      textColor=C_DARK, spaceAfter=3)
    add("body_sm",    fontSize=8,  leading=11, fontName="Helvetica",      textColor=C_DARK, spaceAfter=2)
    add("italic",     fontSize=9,  leading=13, fontName="Helvetica-Oblique", textColor=C_GRAY, spaceAfter=3)
    add("bullet",     fontSize=9,  leading=13, fontName="Helvetica",      textColor=C_DARK, leftIndent=8, spaceAfter=2)
    add("tc",         fontSize=8,  leading=11, fontName="Helvetica",      textColor=C_DARK, alignment=TA_CENTER)
    add("tcb",        fontSize=8,  leading=11, fontName="Helvetica-Bold", textColor=C_DARK, alignment=TA_CENTER)
    add("tr",         fontSize=8,  leading=11, fontName="Helvetica",      textColor=C_DARK, alignment=TA_RIGHT)
    add("disclaimer", fontSize=7,  leading=10, fontName="Helvetica",      textColor=C_GRAY)
    add("news_title", fontSize=8,  leading=11, fontName="Helvetica-Bold", textColor=C_DARK, spaceAfter=1)
    add("news_body",  fontSize=7.5, leading=10, fontName="Helvetica",     textColor=C_GRAY, spaceAfter=5)
    add("research",   fontSize=8,  leading=11, fontName="Helvetica-Oblique",
        textColor=colors.HexColor("#374151"), spaceAfter=6, leftIndent=6)
    return st


# ── Low-level helpers ─────────────────────────────────────────────────────────

def _hr(color: colors.Color = C_BORDER, thickness: float = 0.5) -> HRFlowable:
    return HRFlowable(width="100%", thickness=thickness, color=color, spaceAfter=5, spaceBefore=2)


def _kv_banner(headers: List[str], values: List[str],
               col_w: float, hdr_bg: colors.Color, val_bg: colors.Color = None) -> Table:
    vbg = val_bg or C_SURFACE
    hdr_p = [Paragraph(_safe(h), ParagraphStyle(
        "bh", fontSize=7.5, fontName="Helvetica-Bold",
        textColor=C_WHITE, alignment=TA_CENTER)) for h in headers]
    val_p = [Paragraph(_safe(v), ParagraphStyle(
        "bv", fontSize=8.5, fontName="Helvetica",
        textColor=C_DARK, alignment=TA_CENTER)) for v in values]
    t = Table([hdr_p, val_p], colWidths=[col_w] * len(headers))
    t.setStyle(TableStyle([
        ("BACKGROUND",    (0, 0), (-1, 0), hdr_bg),
        ("BACKGROUND",    (0, 1), (-1, 1), vbg),
        ("TOPPADDING",    (0, 0), (-1, -1), 6),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 6),
        ("GRID",          (0, 0), (-1, -1), 0.4, C_BORDER),
        ("VALIGN",        (0, 0), (-1, -1), "MIDDLE"),
    ]))
    return t


def _two_col_list(lh: str, li: List[str], rh: str, ri: List[str],
                  col_w: float, st: Dict) -> Table:
    rows = [[Paragraph(_safe(lh), st["sub"]), Paragraph(_safe(rh), st["sub"])]]
    for i in range(min(max(len(li), len(ri)), 8)):
        l_txt = f"+ {li[i]}" if i < len(li) else ""
        r_txt = f"- {ri[i]}" if i < len(ri) else ""
        rows.append([Paragraph(_safe(l_txt), st["body"]),
                     Paragraph(_safe(r_txt), st["body"])])
    t = Table(rows, colWidths=[col_w, col_w])
    t.setStyle(TableStyle([
        ("VALIGN",        (0, 0), (-1, -1), "TOP"),
        ("TOPPADDING",    (0, 0), (-1, -1), 2),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 2),
        ("LINEAFTER",     (0, 0), (0, -1),  0.4, C_BORDER),
        ("BACKGROUND",    (0, 0), (-1, 0),  C_LGRAY),
    ]))
    return t


def _page_deco(canvas, doc):
    canvas.saveState()
    canvas.setFillColor(C_DARK)
    canvas.rect(0, H - 26, W, 26, fill=1, stroke=0)
    canvas.setFillColor(C_GREEN)
    canvas.setFont("Helvetica-Bold", 9)
    canvas.drawString(56, H - 17, "PSX ADVISOR -- INVESTMENT RESEARCH")
    canvas.setFillColor(C_WHITE)
    canvas.setFont("Helvetica", 8)
    canvas.drawRightString(W - 56, H - 17, f"Page {doc.page}")
    canvas.setFillColor(C_GRAY)
    canvas.setFont("Helvetica-Oblique", 6)
    canvas.drawString(56, 16, "For educational purposes only. Not financial advice. PSX Advisor AI.")
    canvas.restoreState()


# ── Financial statement parsing ───────────────────────────────────────────────

_IS_KW = [
    "total revenue", "net revenue", "net sales", "turnover", "revenue",
    "gross profit",
    "operating profit", "profit from operations", "ebit",
    "ebitda",
    "profit after tax", "net profit", "profit for the period", "pat",
    "earnings per share", "eps - basic", "eps",
]

_BS_KW = [
    "total assets",
    # List "non-current assets" before "current assets": the matcher takes the
    # first substring hit, and "current assets" is a substring of "non-current
    # assets", so the broad term must claim its own row first.
    "non-current assets",
    "current assets",
    # Working-capital line items — previously omitted, which left ~half the
    # asset base unexplained (Total Assets didn't reconcile to the listed rows).
    "stock in trade", "inventory", "stores and spares",
    "trade debts", "trade receivable", "accounts receivable", "receivable",
    "short-term investment", "short term investment",
    "cash and bank", "cash & bank", "cash",
    "total equity", "total shareholders",
    "total liabilities",
    "non-current liabilities", "current liabilities",
    "total debt", "short-term borrowing", "long-term borrowing",
]

_CF_KW = [
    "net cash from operating", "cash from operating", "operating activities",
    "net cash from investing", "investing activities", "capital expenditure",
    "net cash from financing", "financing activities",
    "net change in cash", "cash at end",
]


def _find_metric_col(row: dict) -> str:
    for key in list(row.keys())[:4]:
        val = row[key]
        if isinstance(val, str) and len(val.strip()) > 1:
            return key
    return list(row.keys())[0] if row else ""


def _sort_period_keys(keys: List[str]) -> List[str]:
    from datetime import datetime as _dt
    fmts = ["%b-%y", "%b-%Y", "%Y-%m-%d", "%Y"]

    def _try(k):
        for fmt in fmts:
            try:
                return _dt.strptime(str(k).strip(), fmt)
            except ValueError:
                pass
        return None

    dated   = [(k, _try(k)) for k in keys]
    sorted_ = sorted([(k, d) for k, d in dated if d], key=lambda x: x[1])
    undated = [k for k, d in dated if d is None]
    return [k for k, _ in sorted_] + undated


def _parse_fs_rows(rows: list, keywords: List[str],
                   n_periods: int = 7) -> Tuple[List[str], List[List]]:
    if not rows or not isinstance(rows, list):
        return [], []

    metric_col = _find_metric_col(rows[0])
    all_keys   = list(rows[0].keys())

    skip = {metric_col, "Unit", "symbol", "period", "last_updated",
            "deleted_at", "updated_at", "id", "old_id", "created_at"}
    period_keys = [k for k in all_keys if k not in skip]
    period_keys = _sort_period_keys(period_keys)
    period_keys = period_keys[-n_periods:]

    if not period_keys:
        return [], []

    header = ["Metric (PKR mn)"] + [str(k) for k in period_keys]

    found: List[List] = []
    matched: set = set()
    for kw in keywords:
        kw_l = kw.lower()
        for row in rows:
            metric = str(row.get(metric_col, "")).strip()
            if not metric:
                continue
            metric_l = metric.lower()
            if kw_l in metric_l and metric_l not in matched:
                vals = [_fmt_mn(row.get(pk)) for pk in period_keys]
                found.append([metric[:40]] + vals)
                matched.add(metric_l)
                break

    return header, found


def _fin_table(header: List[str], data_rows: List[List], col_w: float) -> Optional[Table]:
    if not data_rows:
        return None

    n_cols  = len(header)
    label_w = col_w * 0.36
    val_w   = (col_w - label_w) / max(1, n_cols - 1)
    widths  = [label_w] + [val_w] * (n_cols - 1)

    def _hdr_p(txt):
        return Paragraph(_safe(txt), ParagraphStyle(
            "fh", fontSize=7, fontName="Helvetica-Bold",
            textColor=C_WHITE, alignment=TA_CENTER))

    def _lbl_p(txt):
        return Paragraph(_safe(txt), ParagraphStyle(
            "fl", fontSize=7.5, fontName="Helvetica-Bold", textColor=C_DARK))

    def _val_p(txt):
        return Paragraph(_safe(str(txt)), ParagraphStyle(
            "fv", fontSize=7.5, fontName="Helvetica",
            textColor=C_DARK, alignment=TA_RIGHT))

    table_data = [[_hdr_p(h) for h in header]]
    for row in data_rows:
        table_data.append([_lbl_p(row[0])] + [_val_p(v) for v in row[1:]])

    ts = [
        ("BACKGROUND",    (0, 0), (-1, 0), C_DARK),
        ("TOPPADDING",    (0, 0), (-1, -1), 4),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 4),
        ("LEFTPADDING",   (0, 0), (-1, -1), 4),
        ("RIGHTPADDING",  (0, 0), (-1, -1), 4),
        ("GRID",          (0, 0), (-1, -1), 0.3, C_BORDER),
        ("VALIGN",        (0, 0), (-1, -1), "MIDDLE"),
    ]
    for i in range(1, len(table_data)):
        bg = C_WHITE if i % 2 == 1 else C_SURFACE
        ts.append(("BACKGROUND", (0, i), (-1, i), bg))

    t = Table(table_data, colWidths=widths)
    t.setStyle(TableStyle(ts))
    return t


def _ratios_table(ratios: List[Tuple[str, str]], col_w: float) -> Optional[Table]:
    if not ratios:
        return None

    mid   = (len(ratios) + 1) // 2
    left  = ratios[:mid]
    right = ratios[mid:]

    rows = []
    for i in range(mid):
        lk, lv = left[i]  if i < len(left)  else ("", "")
        rk, rv = right[i] if i < len(right) else ("", "")
        rows.append([
            Paragraph(_safe(lk), ParagraphStyle("rk",  fontSize=8, fontName="Helvetica-Bold", textColor=C_GRAY)),
            Paragraph(_safe(lv), ParagraphStyle("rv",  fontSize=8, fontName="Helvetica", textColor=C_DARK, alignment=TA_RIGHT)),
            Paragraph(_safe(rk), ParagraphStyle("rk2", fontSize=8, fontName="Helvetica-Bold", textColor=C_GRAY)),
            Paragraph(_safe(rv), ParagraphStyle("rv2", fontSize=8, fontName="Helvetica", textColor=C_DARK, alignment=TA_RIGHT)),
        ])

    half = col_w / 2
    t = Table(rows, colWidths=[half * 0.55, half * 0.45, half * 0.55, half * 0.45])
    ts = [
        ("TOPPADDING",    (0, 0), (-1, -1), 4),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 4),
        ("LEFTPADDING",   (0, 0), (-1, -1), 5),
        ("RIGHTPADDING",  (0, 0), (-1, -1), 5),
        ("GRID",          (0, 0), (-1, -1), 0.3, C_BORDER),
        ("VALIGN",        (0, 0), (-1, -1), "MIDDLE"),
        ("LINEAFTER",     (1, 0), (1, -1),  0.8, C_BORDER),
    ]
    for i in range(len(rows)):
        bg = C_WHITE if i % 2 == 0 else C_SURFACE
        ts.append(("BACKGROUND", (0, i), (-1, i), bg))
    t.setStyle(TableStyle(ts))
    return t


# ── Main entry point ──────────────────────────────────────────────────────────

def generate_pdf(report: Dict[str, Any]) -> bytes:
    buf = io.BytesIO()
    st  = _styles()

    doc = BaseDocTemplate(
        buf, pagesize=A4,
        leftMargin=1.8 * cm, rightMargin=1.8 * cm,
        topMargin=2.0 * cm, bottomMargin=1.6 * cm,
        title=f"PSX Analysis -- {report.get('symbol', '')}",
        author="PSX Advisor AI",
    )
    frame = Frame(doc.leftMargin, doc.bottomMargin, doc.width, doc.height, id="main")
    doc.addPageTemplates([PageTemplate(id="main", frames=[frame], onPage=_page_deco)])

    story: List = []

    # ── Unpack fields ─────────────────────────────────────────────────────────
    rec    = report.get("recommendation",    {}) or {}
    tech   = report.get("technical_report",  {}) or {}
    fund   = report.get("fundamental_report",{}) or {}
    sent   = report.get("sentiment_report",  {}) or {}
    risk   = report.get("risk_report",       {}) or {}
    debate = report.get("debate",            {}) or {}
    quote  = report.get("quote",             {}) or {}
    raw    = report.get("raw_data",          {}) or {}
    fs     = raw.get("financial_statements", {}) or {}
    funda  = raw.get("fundamentals",         {}) or {}
    mkt_ctx   = raw.get("market_context_text",  "") or ""
    excerpts  = raw.get("research_excerpts",    []) or []
    news: List = raw.get("company_news", []) or sent.get("articles", []) or []

    sym    = report.get("symbol", "")
    name   = report.get("company_name", sym)
    sector = report.get("sector", "")
    ts_    = report.get("timestamp", datetime.now().strftime("%Y-%m-%d %H:%M"))
    dw     = doc.width

    # ══════════════════════════════════════════════════════════════════════════
    # PAGE 1 — COVER + EXECUTIVE SUMMARY
    # ══════════════════════════════════════════════════════════════════════════
    story.append(Spacer(1, 0.4 * cm))
    story.append(Paragraph(_safe(name), st["h_company"]))
    story.append(Paragraph(_safe(f"{sym}  |  {sector}"), st["h_sub"]))
    story.append(Paragraph(_safe(f"AI Investment Research Report  |  {ts_}"), st["h_date"]))
    story.append(Spacer(1, 0.4 * cm))

    rec_text = (rec.get("recommendation") or "N/A").upper()
    rec_col  = _rec_color(rec_text)
    rec_tbl  = Table([[Paragraph(rec_text, st["rec_badge"])]], colWidths=[dw])
    rec_tbl.setStyle(TableStyle([
        ("BACKGROUND",    (0, 0), (-1, -1), rec_col),
        ("TOPPADDING",    (0, 0), (-1, -1), 8),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 8),
    ]))
    story.append(rec_tbl)
    story.append(Spacer(1, 0.3 * cm))

    current = rec.get("current_price") or quote.get("price") or 0
    pt_low  = rec.get("price_target_low")  or 0
    pt_high = rec.get("price_target_high") or 0
    upside  = rec.get("upside_pct")
    # Fallback: derive upside from the target midpoint vs current price when the
    # recommendation didn't quantify it, so the field isn't left blank while the
    # prose quotes a downside figure.
    if not upside and current and (pt_low or pt_high):
        try:
            target_mid = ((float(pt_low) + float(pt_high)) / 2.0
                          if (pt_low and pt_high) else float(pt_high or pt_low))
            upside = (target_mid - float(current)) / float(current) * 100.0
        except (TypeError, ValueError, ZeroDivisionError):
            upside = 0
    upside = upside or 0
    horizon = (rec.get("time_horizon") or "-").replace("_", " ").title()
    conf    = rec.get("confidence") or 0

    story.append(_kv_banner(
        ["Current Price", "Target Range", "Upside Potential", "Time Horizon", "Conviction"],
        [
            f"PKR {float(current):,.2f}" if current else "-",
            f"PKR {float(pt_low):,.0f} - {float(pt_high):,.0f}" if (pt_low and pt_high) else "-",
            f"{float(upside):+.1f}%" if upside else "-",
            horizon,
            f"{conf}/10",
        ],
        col_w=dw / 5, hdr_bg=C_DARK,
    ))
    story.append(Spacer(1, 0.2 * cm))

    pe    = quote.get("pe_ratio")   or funda.get("pe_ratio")
    pb    = funda.get("pb_ratio")
    beta  = quote.get("beta")       or funda.get("beta")
    roe   = funda.get("roe")
    mkcap = quote.get("market_cap") or funda.get("market_cap")

    story.append(_kv_banner(
        ["Market Cap", "P/E Ratio", "P/B Ratio", "Beta", "ROE"],
        [
            _mktcap_str(mkcap),
            f"{float(pe):.1f}x"  if pe   else "-",
            f"{float(pb):.2f}x"  if pb   else "-",
            f"{float(beta):.2f}" if beta  else "-",
            _pct(roe)            if roe   else "-",
        ],
        col_w=dw / 5, hdr_bg=C_ACCENT,
    ))
    story.append(Spacer(1, 0.3 * cm))

    story.append(Paragraph("EXECUTIVE SUMMARY", st["section"]))
    story.append(_hr(C_DARK, 1.0))
    if rec.get("summary"):
        story.append(Paragraph(_safe(rec["summary"]), st["body"]))
        story.append(Spacer(1, 0.15 * cm))
    if rec.get("position_advice"):
        story.append(Paragraph(_safe(f"Position Advice: {rec['position_advice']}"), st["italic"]))

    cats = rec.get("catalysts", []) or []
    rsks = rec.get("risks",     []) or []
    if cats or rsks:
        story.append(Spacer(1, 0.2 * cm))
        story.append(_two_col_list("INVESTMENT CATALYSTS", cats, "KEY RISKS", rsks, dw / 2, st))

    story.append(PageBreak())

    # ══════════════════════════════════════════════════════════════════════════
    # PAGE 2 — FINANCIAL ANALYSIS
    # ══════════════════════════════════════════════════════════════════════════
    story.append(Paragraph("FINANCIAL ANALYSIS", st["section"]))
    story.append(_hr(C_GREEN, 1.0))

    is_rows = fs.get("income_statement") or fs.get("income statement") or []
    hdr, data = _parse_fs_rows(is_rows, _IS_KW, n_periods=7)
    if data:
        story.append(Paragraph("Income Statement (PKR mn)", st["sub"]))
        t = _fin_table(hdr, data, dw)
        if t:
            story.append(t)
        story.append(Spacer(1, 0.3 * cm))
    else:
        story.append(Paragraph("Income Statement", st["sub"]))
        story.append(Paragraph(
            "(Financial statement tables are populated from Firestore when this report is generated "
            "on the live server. See the AI-derived fundamental assessment below.)",
            st["italic"],
        ))
        story.append(Spacer(1, 0.15 * cm))

    bs_rows = fs.get("balance_sheet") or fs.get("balance sheet") or []
    hdr, data = _parse_fs_rows(bs_rows, _BS_KW, n_periods=7)
    if data:
        story.append(Paragraph("Balance Sheet (PKR mn)", st["sub"]))
        t = _fin_table(hdr, data, dw)
        if t:
            story.append(t)
        story.append(Spacer(1, 0.3 * cm))

    cf_rows = fs.get("cash_flow") or fs.get("cash flow") or []
    if not cf_rows:
        for key in list(fs.keys()):
            if "cash" in key.lower() or "simplif" in key.lower():
                cf_rows = fs[key]
                break
    hdr, data = _parse_fs_rows(cf_rows, _CF_KW, n_periods=7)
    if data:
        story.append(Paragraph("Cash Flow Statement (PKR mn)", st["sub"]))
        t = _fin_table(hdr, data, dw)
        if t:
            story.append(t)
        story.append(Spacer(1, 0.3 * cm))

    # Valuation ratios grid
    def _rx(v: Any, fmt: str = "x") -> str:
        if v is None or _is_nan(v):
            return "-"
        try:
            fv = float(v)
            if fmt == "x":   return f"{fv:.2f}x"
            if fmt == "pct": return _pct(v)
            return f"{fv:,.2f}"
        except (TypeError, ValueError):
            return "-"

    eps   = funda.get("eps")
    div_y = funda.get("dividend_yield")

    ratios: List[Tuple[str, str]] = [
        ("P/E Ratio",        f"{float(pe):.1f}x" if pe   else "-"),
        ("P/B Ratio",        f"{float(pb):.2f}x" if pb   else "-"),
        ("EV/EBITDA",        _rx(funda.get("ev_to_ebitda"))),
        ("EPS (TTM)",        f"PKR {float(eps):.2f}" if eps else "-"),
        ("ROE",              _pct(roe) if roe else "-"),
        ("ROA",              _pct(funda.get("roa")) if funda.get("roa") else "-"),
        ("Profit Margin",    _pct(funda.get("profit_margin"))),
        ("Operating Margin", _pct(funda.get("operating_margin"))),
        ("Dividend Yield",   _pct(div_y) if div_y else "-"),
        ("Revenue Growth",   _pct(funda.get("revenue_growth"))),
        ("Earnings Growth",  _pct(funda.get("earnings_growth"))),
        ("Debt/Equity",      f"{float(funda.get('debt_to_equity', 0)):.2f}x" if funda.get("debt_to_equity") else "-"),
        ("Current Ratio",    f"{float(funda.get('current_ratio', 0)):.2f}x"  if funda.get("current_ratio")  else "-"),
        ("Beta",             f"{float(beta):.2f}" if beta else "-"),
        ("Free Cash Flow",   _mktcap_str(funda.get("free_cash_flow"))),
        ("EBITDA",           _mktcap_str(funda.get("ebitda"))),
    ]
    ratios_clean = [(k, v) for k, v in ratios if v != "-"]
    if ratios_clean:
        story.append(Paragraph("Valuation Ratios &amp; Financial Metrics", st["sub"]))
        rt = _ratios_table(ratios_clean, dw)
        if rt:
            story.append(rt)
        story.append(Spacer(1, 0.3 * cm))

    # Fundamental analyst deep-dive
    story.append(Paragraph("Fundamental Analysis -- Detailed Assessment", st["sub"]))

    verdict = (fund.get("valuation_verdict") or "-").replace("_", " ").upper()
    health  = (fund.get("financial_health")  or "-").replace("_", " ").upper()
    growth  = (fund.get("growth_outlook")    or "-").replace("_", " ").upper()
    moat    = (fund.get("moat")              or "-").upper()
    fc      = fund.get("confidence") or 0

    story.append(_kv_banner(
        ["Valuation", "Financial Health", "Growth Outlook", "Moat", "Confidence"],
        [verdict, health, growth, moat, f"{fc}/10"],
        col_w=dw / 5, hdr_bg=C_GREEN,
    ))
    story.append(Spacer(1, 0.2 * cm))

    if fund.get("summary"):
        story.append(Paragraph(_safe(fund["summary"]), st["body"]))

    # Forward thesis (new field from updated prompt)
    if fund.get("forward_thesis"):
        story.append(Paragraph("Forward Outlook", st["sub"]))
        story.append(Paragraph(_safe(fund["forward_thesis"]), st["body"]))

    # Key observed trends
    key_trends = fund.get("key_trends") or []
    if key_trends:
        story.append(Paragraph("Key Financial Trends", st["sub"]))
        for trend in key_trends[:6]:
            story.append(Paragraph(_safe(f"* {trend}"), st["bullet"]))

    fvr = fund.get("fair_value_range") or {}
    if fvr.get("low") or fvr.get("high"):
        story.append(Paragraph(
            _safe(f"Estimated Fair Value Range: PKR {fvr.get('low', 0):,.0f} -- PKR {fvr.get('high', 0):,.0f}"),
            st["italic"],
        ))

    strengths = fund.get("strengths", []) or []
    concerns  = fund.get("concerns",  []) or []
    if strengths or concerns:
        story.append(Spacer(1, 0.15 * cm))
        story.append(_two_col_list("FUNDAMENTAL STRENGTHS", strengths, "KEY CONCERNS", concerns, dw / 2, st))

    story.append(PageBreak())

    # ══════════════════════════════════════════════════════════════════════════
    # PAGE 3 — TECHNICAL ANALYSIS + MARKET CONTEXT + RESEARCH
    # ══════════════════════════════════════════════════════════════════════════
    story.append(Paragraph("TECHNICAL ANALYSIS", st["section"]))
    story.append(_hr(C_CYAN, 1.0))

    trend = (tech.get("trend") or "unknown").upper()
    tstr  = (tech.get("trend_strength") or "").replace("_", " ").title()
    entry = tech.get("entry_zone") or {}
    sl_   = tech.get("stop_loss") or 0
    tc    = tech.get("confidence") or 0

    story.append(_kv_banner(
        ["Trend Direction", "Trend Strength", "Entry Zone (PKR)", "Stop Loss (PKR)", "Confidence"],
        [
            trend, tstr,
            f"{float(entry.get('low', 0)):,.2f} - {float(entry.get('high', 0)):,.2f}" if entry else "-",
            f"{float(sl_):,.2f}" if sl_ else "-",
            f"{tc}/10",
        ],
        col_w=dw / 5, hdr_bg=C_CYAN,
    ))
    story.append(Spacer(1, 0.2 * cm))

    if tech.get("summary"):
        story.append(Paragraph(_safe(tech["summary"]), st["body"]))

    levels = tech.get("key_levels") or {}
    sup = [x for x in (levels.get("support",    []) or []) if x]
    res = [x for x in (levels.get("resistance", []) or []) if x]
    if sup or res:
        story.append(Paragraph("Key Price Levels", st["sub"]))
        lv = Table(
            [["Support Levels (PKR)", "Resistance Levels (PKR)"],
             [" | ".join(f"{float(x):,.2f}" for x in sup[:6]) or "-",
              " | ".join(f"{float(x):,.2f}" for x in res[:6]) or "-"]],
            colWidths=[dw / 2, dw / 2],
        )
        lv.setStyle(TableStyle([
            ("BACKGROUND",    (0, 0), (-1, 0), C_DARK),
            ("TEXTCOLOR",     (0, 0), (-1, 0), C_WHITE),
            ("FONTNAME",      (0, 0), (-1, 0), "Helvetica-Bold"),
            ("FONTNAME",      (0, 1), (-1, 1), "Helvetica"),
            ("FONTSIZE",      (0, 0), (-1, -1), 8),
            ("ALIGN",         (0, 0), (-1, -1), "CENTER"),
            ("TOPPADDING",    (0, 0), (-1, -1), 5),
            ("BOTTOMPADDING", (0, 0), (-1, -1), 5),
            ("GRID",          (0, 0), (-1, -1), 0.4, C_BORDER),
            ("BACKGROUND",    (0, 1), (-1, 1), C_SURFACE),
        ]))
        story.append(lv)
        story.append(Spacer(1, 0.2 * cm))

    sigs = tech.get("signals") or []
    if sigs:
        story.append(Paragraph("Technical Indicators", st["sub"]))
        rows = [[Paragraph("Indicator", st["tcb"]),
                 Paragraph("Reading",   st["tcb"]),
                 Paragraph("Interpretation", st["tcb"])]]
        for s in sigs[:12]:
            rows.append([
                Paragraph(_safe(s.get("indicator", "")), st["body_sm"]),
                Paragraph(_safe(s.get("reading",    "")), st["body_sm"]),
                Paragraph(_safe(s.get("interpretation", "")), st["body_sm"]),
            ])
        st_tbl = Table(rows, colWidths=[dw * 0.18, dw * 0.15, dw * 0.67])
        st_tbl.setStyle(TableStyle([
            ("BACKGROUND",    (0, 0), (-1, 0), C_DARK),
            ("TEXTCOLOR",     (0, 0), (-1, 0), C_WHITE),
            ("FONTNAME",      (0, 0), (-1, 0), "Helvetica-Bold"),
            ("FONTSIZE",      (0, 0), (-1, -1), 8),
            ("VALIGN",        (0, 0), (-1, -1), "TOP"),
            ("TOPPADDING",    (0, 0), (-1, -1), 4),
            ("BOTTOMPADDING", (0, 0), (-1, -1), 4),
            ("GRID",          (0, 0), (-1, -1), 0.4, C_BORDER),
        ]))
        for i in range(1, len(rows)):
            bg = C_WHITE if i % 2 == 1 else C_SURFACE
            st_tbl.setStyle(TableStyle([("BACKGROUND", (0, i), (-1, i), bg)]))
        story.append(st_tbl)

    mkt_lines = [l.strip() for l in mkt_ctx.split("\n") if l.strip()] if mkt_ctx else []
    if mkt_lines:
        story.append(Spacer(1, 0.4 * cm))
        story.append(Paragraph("MARKET &amp; SECTOR CONTEXT", st["section"]))
        story.append(_hr(C_AMBER, 1.0))
        for line in mkt_lines[:60]:
            if line.startswith("--") or line.startswith("=="):
                story.append(Paragraph(_safe(line.strip("-= ")), st["sub"]))
            else:
                story.append(Paragraph(_safe(line), st["body_sm"]))

    if excerpts:
        story.append(Spacer(1, 0.3 * cm))
        story.append(Paragraph("BROKER RESEARCH HIGHLIGHTS", st["section"]))
        story.append(_hr(C_ACCENT, 1.0))
        for i, excerpt in enumerate(excerpts[:3]):
            lines = excerpt.split("\n", 2)
            title_line = lines[0].strip("=# \t") if lines else f"Report {i+1}"
            body_text  = (lines[2].strip() if len(lines) > 2
                          else (lines[1].strip() if len(lines) > 1 else excerpt))
            story.append(Paragraph(_safe(f"[{i+1}] {title_line[:90]}"), st["sub"]))
            story.append(Paragraph(_safe(body_text[:1400]), st["research"]))
            if i < len(excerpts) - 1:
                story.append(Spacer(1, 0.1 * cm))

    story.append(PageBreak())

    # ══════════════════════════════════════════════════════════════════════════
    # PAGE 4 — SENTIMENT + NEWS + RISK
    # ══════════════════════════════════════════════════════════════════════════
    story.append(Paragraph("SENTIMENT ANALYSIS", st["section"]))
    sent_col = _sent_color(sent.get("overall_sentiment") or "neutral")
    story.append(_hr(sent_col, 1.0))

    over  = (sent.get("overall_sentiment") or "-").upper()
    score = sent.get("sentiment_score") or 0
    vol2  = (sent.get("news_volume") or "-").upper()
    sc    = sent.get("confidence") or 0

    story.append(_kv_banner(
        ["Overall Sentiment", "Sentiment Score", "News Volume", "Confidence"],
        [over, str(score), vol2, f"{sc}/10"],
        col_w=dw / 4, hdr_bg=C_DARK,
    ))
    story.append(Spacer(1, 0.2 * cm))

    if sent.get("summary"):
        story.append(Paragraph(_safe(sent["summary"]), st["body"]))

    narratives = sent.get("key_narratives") or []
    if narratives:
        story.append(Paragraph("Key Sentiment Drivers", st["sub"]))
        for n in narratives[:8]:
            story.append(Paragraph(_safe(f"* {n}"), st["bullet"]))

    pos_cats = sent.get("catalysts_positive") or []
    neg_cats = sent.get("catalysts_negative") or []
    if pos_cats or neg_cats:
        story.append(Spacer(1, 0.1 * cm))
        story.append(_two_col_list("POSITIVE CATALYSTS", pos_cats, "NEGATIVE CATALYSTS", neg_cats, dw / 2, st))

    story.append(Spacer(1, 0.3 * cm))
    story.append(Paragraph("Company News &amp; Announcements", st["sub"]))
    if news:
        for art in news[:10]:
            title_  = art.get("title")       or art.get("Title")    or art.get("headline") or "-"
            date_   = _clean_date(art.get("date") or art.get("Date") or art.get("published"))
            source  = art.get("source")      or art.get("Source")   or art.get("publisher") or ""
            body_   = art.get("content") or art.get("description") or art.get("body") or art.get("snippet") or ""

            hdr_txt = f"[{date_}]  {str(title_)[:100]}"
            if source:
                hdr_txt += f"  --  {str(source)[:40]}"
            story.append(Paragraph(_safe(hdr_txt), st["news_title"]))
            if body_ and len(str(body_).strip()) > 20:
                story.append(Paragraph(_safe(str(body_)[:700]), st["news_body"]))
    else:
        story.append(Paragraph(
            "No recent news articles were retrieved for this ticker. "
            "This is common for less actively covered stocks or when the news feed is offline.",
            st["italic"],
        ))

    story.append(Spacer(1, 0.4 * cm))
    story.append(Paragraph("RISK ASSESSMENT", st["section"]))
    risk_col = _risk_color(risk.get("risk_level") or "medium")
    story.append(_hr(risk_col, 1.0))

    rlevel   = (risk.get("risk_level")      or "-").replace("_", " ").upper()
    rscore   = risk.get("risk_score")       or 0
    maxpos   = risk.get("max_position_pct") or 0
    stoploss = risk.get("stop_loss_pct")    or 0
    rconf    = risk.get("confidence")       or 0

    story.append(_kv_banner(
        ["Risk Level", "Risk Score", "Max Position", "Stop Loss %", "Confidence"],
        [rlevel, f"{rscore}/10",
         f"{maxpos}%" if maxpos else "-",
         f"{stoploss}%" if stoploss else "-",
         f"{rconf}/10"],
        col_w=dw / 5, hdr_bg=C_DARK,
    ))
    story.append(Spacer(1, 0.2 * cm))

    if risk.get("summary"):
        story.append(Paragraph(_safe(risk["summary"]), st["body"]))

    rfs = risk.get("risk_factors") or []
    if rfs:
        story.append(Paragraph("Identified Risk Factors", st["sub"]))
        rf_rows = [[Paragraph("Risk Factor", st["tcb"]),
                    Paragraph("Severity",    st["tcb"]),
                    Paragraph("Detail",      st["tcb"])]]
        for rf in rfs[:8]:
            rf_rows.append([
                Paragraph(_safe(rf.get("factor", "")), st["body_sm"]),
                Paragraph(_safe((rf.get("severity") or "").upper()), st["tc"]),
                Paragraph(_safe(rf.get("detail", "")), st["body_sm"]),
            ])
        rf_tbl = Table(rf_rows, colWidths=[dw * 0.22, dw * 0.10, dw * 0.68])
        rf_tbl.setStyle(TableStyle([
            ("BACKGROUND",    (0, 0), (-1, 0), C_DARK),
            ("TEXTCOLOR",     (0, 0), (-1, 0), C_WHITE),
            ("FONTNAME",      (0, 0), (-1, 0), "Helvetica-Bold"),
            ("FONTSIZE",      (0, 0), (-1, -1), 8),
            ("VALIGN",        (0, 0), (-1, -1), "TOP"),
            ("TOPPADDING",    (0, 0), (-1, -1), 4),
            ("BOTTOMPADDING", (0, 0), (-1, -1), 4),
            ("GRID",          (0, 0), (-1, -1), 0.4, C_BORDER),
        ]))
        for i in range(1, len(rf_rows)):
            bg = C_WHITE if i % 2 == 1 else C_SURFACE
            rf_tbl.setStyle(TableStyle([("BACKGROUND", (0, i), (-1, i), bg)]))
        story.append(rf_tbl)

    story.append(PageBreak())

    # ══════════════════════════════════════════════════════════════════════════
    # PAGE 5 — BULL vs BEAR + FINAL VERDICT
    # ══════════════════════════════════════════════════════════════════════════
    story.append(Paragraph("INVESTMENT DEBATE: BULL vs BEAR", st["section"]))
    story.append(_hr(C_BORDER, 1.0))

    bull_thesis = debate.get("bull_thesis") or ""
    bear_thesis = debate.get("bear_thesis") or ""
    bull_args   = debate.get("bull_arguments") or []
    bear_args   = debate.get("bear_arguments") or []

    if bull_thesis or bear_thesis:
        th_tbl = Table(
            [[Paragraph(_safe(f'BULL CASE: "{bull_thesis}"'), st["italic"]),
              Paragraph(_safe(f'BEAR CASE: "{bear_thesis}"'), st["italic"])]],
            colWidths=[dw / 2, dw / 2],
        )
        th_tbl.setStyle(TableStyle([
            ("BACKGROUND",    (0, 0), (0, 0), colors.HexColor("#f0fdf4")),
            ("BACKGROUND",    (1, 0), (1, 0), colors.HexColor("#fff1f2")),
            ("VALIGN",        (0, 0), (-1, -1), "TOP"),
            ("TOPPADDING",    (0, 0), (-1, -1), 8),
            ("BOTTOMPADDING", (0, 0), (-1, -1), 8),
            ("LEFTPADDING",   (0, 0), (-1, -1), 8),
            ("LINEAFTER",     (0, 0), (0, -1),  0.8, C_BORDER),
            ("BOX",           (0, 0), (-1, -1), 0.4, C_BORDER),
        ]))
        story.append(th_tbl)
        story.append(Spacer(1, 0.25 * cm))

    if bull_args or bear_args:
        rows = [[Paragraph("BULL ARGUMENTS", st["sub"]),
                 Paragraph("BEAR ARGUMENTS", st["sub"])]]
        for i in range(min(max(len(bull_args), len(bear_args)), 7)):
            bl = br = ""
            if i < len(bull_args):
                a  = bull_args[i]
                pt = _safe(a.get("point",    ""))
                ev = _safe(a.get("evidence", ""))
                bl = f"<b>{pt}</b><br/>{ev}" if ev else pt
            if i < len(bear_args):
                a  = bear_args[i]
                pt = _safe(a.get("point",    ""))
                ev = _safe(a.get("evidence", ""))
                br = f"<b>{pt}</b><br/>{ev}" if ev else pt
            rows.append([Paragraph(bl, st["body"]), Paragraph(br, st["body"])])

        db_tbl = Table(rows, colWidths=[dw / 2, dw / 2])
        db_tbl.setStyle(TableStyle([
            ("BACKGROUND",    (0, 0), (-1, 0), C_LGRAY),
            ("VALIGN",        (0, 0), (-1, -1), "TOP"),
            ("TOPPADDING",    (0, 0), (-1, -1), 5),
            ("BOTTOMPADDING", (0, 0), (-1, -1), 5),
            ("LEFTPADDING",   (0, 0), (-1, -1), 5),
            ("LINEAFTER",     (0, 0), (0, -1),  0.4, C_BORDER),
            ("GRID",          (0, 0), (-1, -1), 0.3, C_BORDER),
        ]))
        for i in range(1, len(rows)):
            bg = C_WHITE if i % 2 == 1 else C_SURFACE
            db_tbl.setStyle(TableStyle([("BACKGROUND", (0, i), (-1, i), bg)]))
        story.append(db_tbl)

    agreements    = debate.get("agreements",    []) or []
    disagreements = debate.get("disagreements", []) or []
    if agreements or disagreements:
        story.append(Spacer(1, 0.3 * cm))
        story.append(_two_col_list(
            "ANALYST AGREEMENTS", agreements,
            "KEY DISAGREEMENTS",  disagreements,
            dw / 2, st,
        ))

    # Final verdict summary
    story.append(Spacer(1, 0.5 * cm))
    story.append(Paragraph("FINAL VERDICT", st["section"]))
    story.append(_hr(rec_col, 1.5))

    summary_items = [
        ("Recommendation",   rec_text),
        ("Time Horizon",     horizon),
        ("Conviction",       f"{conf}/10"),
        ("Risk Level",       rlevel),
        ("Valuation",        verdict),
        ("Price Target",     f"PKR {float(pt_low):,.0f} - {float(pt_high):,.0f}" if (pt_low and pt_high) else "-"),
        ("Upside Potential", f"{float(upside):+.1f}%" if upside else "-"),
        ("Max Position",     f"{maxpos}%" if maxpos else "-"),
    ]
    mid2 = (len(summary_items) + 1) // 2
    sl_rows = []
    for i in range(mid2):
        lk, lv = summary_items[i]           if i      < len(summary_items) else ("", "")
        rk, rv = summary_items[i + mid2]    if i+mid2 < len(summary_items) else ("", "")
        sl_rows.append([
            Paragraph(_safe(lk), st["sub"]),
            Paragraph(_safe(lv), st["body"]),
            Paragraph(_safe(rk), st["sub"]),
            Paragraph(_safe(rv), st["body"]),
        ])
    half = dw / 2
    sl_tbl = Table(sl_rows, colWidths=[half*0.40, half*0.60, half*0.40, half*0.60])
    sl_tbl.setStyle(TableStyle([
        ("VALIGN",        (0, 0), (-1, -1), "MIDDLE"),
        ("TOPPADDING",    (0, 0), (-1, -1), 5),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 5),
        ("LEFTPADDING",   (0, 0), (-1, -1), 6),
        ("GRID",          (0, 0), (-1, -1), 0.3, C_BORDER),
        ("LINEAFTER",     (1, 0), (1, -1),  0.8, C_BORDER),
    ]))
    for i in range(len(sl_rows)):
        bg = C_WHITE if i % 2 == 0 else C_SURFACE
        sl_tbl.setStyle(TableStyle([("BACKGROUND", (0, i), (-1, i), bg)]))
    story.append(sl_tbl)

    # Disclaimer
    story.append(Spacer(1, 0.6 * cm))
    story.append(_hr())
    story.append(Paragraph("DISCLAIMER", st["sub"]))
    disc = report.get("disclaimer") or (
        "This analysis is generated by an AI system for educational and informational purposes only. "
        "It does NOT constitute financial advice, investment advice, or a recommendation to buy or sell "
        "any security. Always consult a qualified SECP-registered financial advisor before making "
        "investment decisions. Past performance is not indicative of future results. "
        "AI-generated analysis may contain errors or omissions."
    )
    story.append(Paragraph(_safe(disc), st["disclaimer"]))

    doc.build(story)
    return buf.getvalue()
