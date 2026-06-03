"""
data/psx_tickers.py
═══════════════════════════════════════════════════════════════════════
Curated database of KSE-100 constituent companies listed on the
Pakistan Stock Exchange (PSX).

Each entry maps a local ticker symbol (e.g. 'OGDC') to its full
company name and GICS-style sector classification.  Yahoo Finance
uses the '.KA' suffix for Karachi-listed equities, so callers should
append config.PSX_SUFFIX when constructing yfinance ticker strings.

Usage:
    from data.psx_tickers import search_tickers, get_all_tickers, get_sectors

    results = search_tickers("bank")      # partial name / ticker match
    all_tickers = get_all_tickers()        # full list
    sectors = get_sectors()                # unique sector names
═══════════════════════════════════════════════════════════════════════
"""

from __future__ import annotations

from typing import Dict, List, Optional

# ── KSE-100 Ticker Database ─────────────────────────────────────────
# Each entry:  symbol → { name, sector }
# Symbols are *without* the .KA suffix (added at runtime by market_data).

PSX_TICKERS: Dict[str, Dict[str, str]] = {
    # ── Oil & Gas Exploration / Marketing ────────────────────────────
    "OGDC":    {"name": "Oil & Gas Development Company",      "sector": "Oil & Gas"},
    "PPL":     {"name": "Pakistan Petroleum Limited",         "sector": "Oil & Gas"},
    "PSO":     {"name": "Pakistan State Oil",                 "sector": "Oil & Gas"},
    "SNGP":    {"name": "Sui Northern Gas Pipelines",         "sector": "Oil & Gas"},
    "SSGC":    {"name": "Sui Southern Gas Company",           "sector": "Oil & Gas"},
    "MARI":    {"name": "Mari Petroleum Company",             "sector": "Oil & Gas"},
    "POL":     {"name": "Pakistan Oilfields Limited",         "sector": "Oil & Gas"},
    "APL":     {"name": "Attock Petroleum Limited",           "sector": "Oil & Gas"},
    "SHEL":    {"name": "Shell Pakistan Limited",             "sector": "Oil & Gas"},
    "ATRL":    {"name": "Attock Refinery Limited",            "sector": "Oil & Gas"},
    "CNERGY":  {"name": "Cnergyico PK Limited",               "sector": "Oil & Gas"},

    # ── Banking ──────────────────────────────────────────────────────
    "HBL":     {"name": "Habib Bank Limited",                 "sector": "Banking"},
    "UBL":     {"name": "United Bank Limited",                "sector": "Banking"},
    "MCB":     {"name": "MCB Bank Limited",                   "sector": "Banking"},
    "NBP":     {"name": "National Bank of Pakistan",          "sector": "Banking"},
    "ABL":     {"name": "Allied Bank Limited",                "sector": "Banking"},
    "BAFL":    {"name": "Bank Alfalah Limited",               "sector": "Banking"},
    "MEBL":    {"name": "Meezan Bank Limited",                "sector": "Banking"},
    "BAHL":    {"name": "Bank Al Habib Limited",              "sector": "Banking"},
    "AKBL":    {"name": "Askari Bank Limited",                "sector": "Banking"},
    "JSBL":    {"name": "JS Bank Limited",                    "sector": "Banking"},

    # ── Cement ───────────────────────────────────────────────────────
    "LUCK":    {"name": "Lucky Cement Limited",               "sector": "Cement"},
    "DGKC":    {"name": "D.G. Khan Cement Company",           "sector": "Cement"},
    "MLCF":    {"name": "Maple Leaf Cement Factory",          "sector": "Cement"},
    "FCCL":    {"name": "Fauji Cement Company Limited",       "sector": "Cement"},
    "KOHC":    {"name": "Kohat Cement Company",               "sector": "Cement"},
    "PIOC":    {"name": "Pioneer Cement Limited",             "sector": "Cement"},
    "CHCC":    {"name": "Cherat Cement Company",              "sector": "Cement"},
    "BWCL":    {"name": "Bestway Cement Limited",             "sector": "Cement"},

    # ── Fertilizer ───────────────────────────────────────────────────
    "ENGRO":   {"name": "Engro Corporation Limited",          "sector": "Fertilizer"},
    "EFERT":   {"name": "Engro Fertilizers Limited",          "sector": "Fertilizer"},
    "FFC":     {"name": "Fauji Fertilizer Company",           "sector": "Fertilizer"},
    "FATIMA":  {"name": "Fatima Fertilizer Company",          "sector": "Fertilizer"},
    "FFBL":    {"name": "Fauji Fertilizer Bin Qasim",         "sector": "Fertilizer"},

    # ── Power / Energy ───────────────────────────────────────────────
    "HUBC":    {"name": "Hub Power Company",                  "sector": "Power"},
    "KEL":     {"name": "K-Electric Limited",                 "sector": "Power"},
    "LPL":     {"name": "Lalpir Power Limited",               "sector": "Power"},
    "KAPCO":   {"name": "Kot Addu Power Company",             "sector": "Power"},

    # ── Technology ───────────────────────────────────────────────────
    "TRG":     {"name": "TRG Pakistan Limited",               "sector": "Technology"},
    "SYS":     {"name": "Systems Limited",                    "sector": "Technology"},
    "AVN":     {"name": "AVN Technologies (Avanceon)",        "sector": "Technology"},
    "NETSOL":  {"name": "Netsol Technologies Limited",        "sector": "Technology"},

    # ── Textile ──────────────────────────────────────────────────────
    "NML":     {"name": "Nishat Mills Limited",               "sector": "Textile"},
    "NCL":     {"name": "Nishat Chunian Limited",             "sector": "Textile"},
    "ILP":     {"name": "Interloop Limited",                  "sector": "Textile"},
    "GATM":    {"name": "Gul Ahmed Textile Mills Limited",    "sector": "Textile"},
    "GATI":    {"name": "Gatron (Industries) Limited",        "sector": "Textile"},

    # ── Pharma ───────────────────────────────────────────────────────
    "GLAXO":   {"name": "GlaxoSmithKline Pakistan",           "sector": "Pharma"},
    "SEARL":   {"name": "The Searle Company Limited",         "sector": "Pharma"},
    "AGP":     {"name": "AGP Limited",                        "sector": "Pharma"},
    "ABOT":    {"name": "Abbott Laboratories Pakistan",       "sector": "Pharma"},

    # ── Food & Personal Care ─────────────────────────────────────────
    "NESTLE":  {"name": "Nestle Pakistan Limited",            "sector": "Food"},
    "UNITY":   {"name": "Unity Foods Limited",                "sector": "Food"},
    "FFL":     {"name": "Friesland Campina Engro Foods",      "sector": "Food"},
    "COLG":    {"name": "Colgate Palmolive Pakistan",         "sector": "Food"},

    # ── Automobile ───────────────────────────────────────────────────
    "INDU":    {"name": "Indus Motor Company",                "sector": "Automobile"},
    "HCAR":    {"name": "Honda Atlas Cars Pakistan",          "sector": "Automobile"},
    "MTL":     {"name": "Millat Tractors Limited",            "sector": "Automobile"},

    # ── Steel / Engineering ──────────────────────────────────────────
    "MUGHAL":  {"name": "Mughal Iron & Steel Industries",     "sector": "Steel"},
    "ISL":     {"name": "International Steels Limited",       "sector": "Steel"},
    "ASTL":    {"name": "Amreli Steels Limited",              "sector": "Steel"},

    # ── Chemical ─────────────────────────────────────────────────────
    "LCI":     {"name": "Lucky Core Industries Limited",      "sector": "Chemical"},
    "LOTCHEM": {"name": "Lotte Chemical Pakistan",            "sector": "Chemical"},
    "GGL":     {"name": "Ghani Global Holdings Limited",      "sector": "Chemical"},

    # ── Insurance ────────────────────────────────────────────────────
    "AICL":    {"name": "Adamjee Insurance Company",          "sector": "Insurance"},
    "JLICL":   {"name": "Jubilee Life Insurance",             "sector": "Insurance"},

    # ── Paper & Packaging ────────────────────────────────────────────
    "PKGS":    {"name": "Packages Limited",                   "sector": "Paper & Packaging"},
    "PAEL":    {"name": "Pak Elektron Limited",               "sector": "Paper & Packaging"},

    # ── Real Estate / Investment ─────────────────────────────────────
    "GADT":    {"name": "Gadoon Textile Mills",               "sector": "Real Estate"},

    # ── Miscellaneous / Conglomerate ─────────────────────────────────
    "PAKT":    {"name": "Pakistan Tobacco Company",           "sector": "Tobacco"},
    "DAWH":    {"name": "Dawood Hercules Corporation",        "sector": "Conglomerate"},
    "EPCL":    {"name": "Engro Polymer & Chemicals",          "sector": "Chemical"},
}


# ── Public API ───────────────────────────────────────────────────────


def get_all_tickers() -> List[Dict[str, str]]:
    """
    Return every KSE-100 entry as a flat list of dicts.

    Returns:
        List of dicts each containing 'symbol', 'name', and 'sector'.

    Example::

        [
            {"symbol": "OGDC", "name": "Oil & Gas Development Company", "sector": "Oil & Gas"},
            ...
        ]
    """
    return [
        {"symbol": sym, "name": info["name"], "sector": info["sector"]}
        for sym, info in PSX_TICKERS.items()
    ]


def get_ticker_info(symbol: str) -> Optional[Dict[str, str]]:
    """
    Look up a single ticker by its exact PSX symbol (case-insensitive).

    Args:
        symbol: Ticker symbol without .KA suffix (e.g. ``'OGDC'``).

    Returns:
        Dict with 'symbol', 'name', 'sector' or ``None`` if not found.
    """
    key = symbol.upper().replace(".KA", "")
    info = PSX_TICKERS.get(key)
    if info is None:
        return None
    return {"symbol": key, "name": info["name"], "sector": info["sector"]}


def search_tickers(query: str, limit: int = 10) -> List[Dict[str, str]]:
    """
    Fuzzy-search PSX tickers by partial symbol *or* company name.

    The search is case-insensitive and matches anywhere inside the
    symbol or company name string.  Results are sorted so that
    symbol-prefix matches come first, then name matches.

    Args:
        query: Partial string to search for (e.g. ``'bank'``, ``'OG'``).
        limit: Maximum number of results to return (default 10).

    Returns:
        Sorted list of matching dicts, each with 'symbol', 'name', 'sector'.

    Example::

        >>> search_tickers("bank")
        [
            {"symbol": "HBL",  "name": "Habib Bank Limited",   "sector": "Banking"},
            {"symbol": "NBP",  "name": "National Bank of Pakistan", "sector": "Banking"},
            ...
        ]
    """
    q = query.upper().strip().replace(".KA", "")
    if not q:
        return []

    symbol_prefix: List[Dict[str, str]] = []
    symbol_contains: List[Dict[str, str]] = []
    name_matches: List[Dict[str, str]] = []

    for sym, info in PSX_TICKERS.items():
        entry = {"symbol": sym, "name": info["name"], "sector": info["sector"]}
        sym_upper = sym.upper()
        name_upper = info["name"].upper()

        if sym_upper.startswith(q):
            symbol_prefix.append(entry)
        elif q in sym_upper:
            symbol_contains.append(entry)
        elif q in name_upper:
            name_matches.append(entry)

    # Merge with priority: exact-prefix > symbol-contains > name-contains
    combined = symbol_prefix + symbol_contains + name_matches
    return combined[:limit]


def get_sectors() -> List[str]:
    """
    Return a sorted list of unique sector names present in the database.

    Returns:
        List of sector name strings.
    """
    return sorted({info["sector"] for info in PSX_TICKERS.values()})


def get_tickers_by_sector(sector: str) -> List[Dict[str, str]]:
    """
    Filter tickers by sector (case-insensitive).

    Args:
        sector: Sector name to filter by (e.g. ``'Banking'``).

    Returns:
        List of matching dicts with 'symbol', 'name', 'sector'.
    """
    s = sector.strip().lower()
    return [
        {"symbol": sym, "name": info["name"], "sector": info["sector"]}
        for sym, info in PSX_TICKERS.items()
        if info["sector"].lower() == s
    ]
