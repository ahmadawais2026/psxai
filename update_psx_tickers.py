"""
update_psx_tickers.py
═══════════════════════════════════════════════════════════════════════
Fetches the full PSX company list from AskAnalyst API and regenerates
data/psx_tickers.py with all listed companies (500+) instead of the
hardcoded ~60 KSE-100 entries.

Usage:
    python update_psx_tickers.py
"""

import os
import sys
import requests

OUTPUT_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "data", "psx_tickers.py")
API_URL = "https://api.askanalyst.com.pk/api/companylistwithids"

# Map AskAnalyst sector names (ALL CAPS) → normalized sector names
# used throughout the codebase and in SECTOR_INTELLIGENCE config
SECTOR_MAP = {
    "PHARMACEUTICALS":                          "Pharma",
    "COMMERCIAL BANKS":                         "Banking",
    "ISLAMIC BANKS":                            "Banking",
    "INVESTMENT BANKS / INVESTMENT COS. / SECURITIES COS.": "Banking",
    "INV. BANKS / INV. COS. / SECURITIES COS.":            "Banking",
    "INV. BANKS":                                           "Banking",
    "LEASING COMPANIES":                                    "Banking",
    "PAPER, BOARD & PACKAGING":                            "Paper & Packaging",
    "OIL & GAS EXPLORATION COMPANIES":         "Oil & Gas",
    "OIL & GAS MARKETING COMPANIES":           "Oil & Gas",
    "REFINERY":                                 "Oil & Gas",
    "CEMENT":                                   "Cement",
    "FERTILIZER":                               "Fertilizer",
    "POWER GENERATION & DISTRIBUTION":         "Power",
    "TEXTILE COMPOSITE":                        "Textile",
    "TEXTILE SPINNING":                         "Textile",
    "TEXTILE WEAVING":                          "Textile",
    "WOOLLEN":                                  "Textile",
    "SYNTHETIC & RAYON":                        "Textile",
    "JUTE":                                     "Textile",
    "TECHNOLOGY & COMMUNICATION":              "Technology",
    "AUTOMOBILE ASSEMBLER":                     "Automobile",
    "AUTOMOBILE PARTS & ACCESSORIES":          "Automobile",
    "STEEL & ALLIED":                           "Steel",
    "ENGINEERING":                              "Steel",
    "CHEMICAL":                                 "Chemical",
    "INSURANCE":                                "Insurance",
    "FOOD & PERSONAL CARE PRODUCTS":           "Food",
    "SUGAR & ALLIED INDUSTRIES":               "Food",
    "TOBACCO":                                  "Tobacco",
    "PAPER & BOARD":                            "Paper & Packaging",
    "PACKAGING":                                "Paper & Packaging",
    "GLASS & CERAMICS":                         "Chemical",
    "LEATHER & TANNERIES":                      "Chemical",
    "VANASPATI & ALLIED INDUSTRIES":           "Food",
    "REAL ESTATE INVESTMENT TRUST":            "Real Estate",
    "REAL ESTATE INVESTMENT TRUST (REIT)":    "Real Estate",
    "MODARABAS":                                "Banking",
    "MUTUAL FUNDS":                             "Banking",
    "CLOSE - END MUTUAL FUND":                 "Banking",
    "TRANSPORT":                                "Transport",
    "CABLE & ELECTRICAL GOODS":               "Technology",
    "MISCELLANEOUS":                            "Miscellaneous",
}


def normalize_sector(raw: str) -> str:
    """Map AskAnalyst sector string to normalized sector name."""
    return SECTOR_MAP.get(raw.strip().upper(), raw.title())


def fetch_companies() -> list:
    print(f"[+] Fetching company list from AskAnalyst...")
    try:
        r = requests.get(API_URL, timeout=30)
        r.raise_for_status()
        data = r.json()
        print(f"[+] Received {len(data)} companies")
        return data
    except Exception as e:
        print(f"[-] Failed to fetch: {e}")
        sys.exit(1)


def generate_psx_tickers_file(companies: list) -> str:
    """Generate the full content of data/psx_tickers.py."""

    # Build the ticker dict, grouped by sector
    by_sector: dict = {}
    skipped = 0
    for c in companies:
        symbol = (c.get("symbol") or "").strip().upper()
        name = (c.get("name") or c.get("label") or "").strip()
        raw_sector = (c.get("sector") or "").strip()
        askanalyst_id = c.get("id")

        if not symbol or not name:
            skipped += 1
            continue

        sector = normalize_sector(raw_sector)
        by_sector.setdefault(sector, []).append((symbol, name, askanalyst_id))

    # Sort sectors and symbols within each sector
    sorted_sectors = sorted(by_sector.keys())

    lines = []
    lines.append('"""')
    lines.append('data/psx_tickers.py')
    lines.append('═' * 70)
    lines.append('Complete PSX company database — auto-generated from AskAnalyst API.')
    lines.append('DO NOT edit manually. Regenerate with: python update_psx_tickers.py')
    lines.append('')
    lines.append(f'Total companies: {sum(len(v) for v in by_sector.values())}')
    lines.append(f'Total sectors:   {len(sorted_sectors)}')
    lines.append('═' * 70)
    lines.append('"""')
    lines.append('')
    lines.append('from __future__ import annotations')
    lines.append('from typing import Dict, List, Optional')
    lines.append('')
    lines.append('')
    lines.append('PSX_TICKERS: Dict[str, Dict] = {')

    for sector in sorted_sectors:
        entries = sorted(by_sector[sector], key=lambda x: x[0])
        lines.append(f'    # {"─" * 4} {sector} {"─" * max(1, 60 - len(sector))}')
        for symbol, name, askanalyst_id in entries:
            # Escape any quotes in the name
            safe_name = name.replace('"', '\\"')
            lines.append(
                f'    "{symbol}": {{"name": "{safe_name}", "sector": "{sector}", "askanalyst_id": {askanalyst_id}}},'
            )
        lines.append('')

    lines.append('}')
    lines.append('')
    lines.append('')
    lines.append('# ── Public API ──────────────────────────────────────────────────────')
    lines.append('')
    lines.append('')
    lines.append('def get_all_tickers() -> List[Dict]:')
    lines.append('    return [')
    lines.append('        {"symbol": sym, "name": info["name"], "sector": info["sector"], "askanalyst_id": info.get("askanalyst_id")}')
    lines.append('        for sym, info in PSX_TICKERS.items()')
    lines.append('    ]')
    lines.append('')
    lines.append('')
    lines.append('def get_ticker_info(symbol: str) -> Optional[Dict]:')
    lines.append('    return PSX_TICKERS.get(symbol.strip().upper())')
    lines.append('')
    lines.append('')
    lines.append('def search_tickers(query: str, limit: int = 15) -> List[Dict]:')
    lines.append('    q = query.strip().lower()')
    lines.append('    results = []')
    lines.append('    for sym, info in PSX_TICKERS.items():')
    lines.append('        if q in sym.lower() or q in info["name"].lower() or q in info["sector"].lower():')
    lines.append('            results.append({"symbol": sym, "name": info["name"], "sector": info["sector"]})')
    lines.append('    return results[:limit]')
    lines.append('')
    lines.append('')
    lines.append('def get_sectors() -> List[str]:')
    lines.append('    return sorted(set(info["sector"] for info in PSX_TICKERS.values()))')
    lines.append('')
    lines.append('')
    lines.append('def get_tickers_by_sector(sector: str) -> List[Dict]:')
    lines.append('    return [')
    lines.append('        {"symbol": sym, "name": info["name"], "sector": info["sector"]}')
    lines.append('        for sym, info in PSX_TICKERS.items()')
    lines.append('        if info["sector"].lower() == sector.lower()')
    lines.append('    ]')

    return "\n".join(lines) + "\n"


def main():
    companies = fetch_companies()

    # Show sector breakdown
    sectors: dict = {}
    for c in companies:
        raw = (c.get("sector") or "").strip()
        norm = normalize_sector(raw)
        sectors.setdefault(norm, 0)
        sectors[norm] += 1

    print(f"\n[+] Sector breakdown ({len(sectors)} sectors):")
    for s, count in sorted(sectors.items()):
        print(f"    {s:<35} {count} companies")

    content = generate_psx_tickers_file(companies)

    with open(OUTPUT_FILE, "w", encoding="utf-8") as f:
        f.write(content)

    print(f"\n[OK] Written to {OUTPUT_FILE}")
    print(f"     {sum(sectors.values())} companies across {len(sectors)} sectors")


if __name__ == "__main__":
    main()
