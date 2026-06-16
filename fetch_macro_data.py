"""
fetch_macro_data.py
===================
Aggregates key Pakistan macroeconomic indicators from multiple sources and
saves a clean JSON snapshot that the AI agents can use as macro context.

Uses robust Gemini schema-enforced extraction with deterministic verification
and change-gated cache checks, falling back to regex when needed.

Sources:
  1. Yahoo REST        — PKR/USD, Brent crude, WTI crude
  2. Morning briefing  — KSE-100 level & changes, LIPI/FIPI flows, indices
  3. Economy .md files — SBP policy rate, CPI/inflation

Output files:
  market_data/macroeconomics/macro_data.json        — structured macro snapshot
  market_data/summary/morning_briefing_summary.json — latest briefing summary
  market_data/macroeconomics/macro_cache.json       — change-gating cache
"""

from __future__ import annotations

import argparse
import base64
import hashlib
import json
import logging
import os
import re
from datetime import datetime, date
from pathlib import Path
from typing import Any, Dict, Optional

from google.genai import types
from agents.base_agent import BaseAgent
from data.verify import verify_against_source

# Set up logging
logging.basicConfig(level=logging.INFO, format="[%(asctime)s] [%(levelname)s] %(message)s")
logger = logging.getLogger("fetch_macro_data")

BASE_DIR      = Path(__file__).parent
EXTRACTED_DIR = BASE_DIR / "market_data" / "briefings" / "extracted"
PDFS_DIR      = BASE_DIR / "market_data" / "briefings" / "pdfs"
MACRO_PATH    = BASE_DIR / "market_data" / "macroeconomics" / "macro_data.json"
BRIEFING_PATH = BASE_DIR / "market_data" / "summary" / "morning_briefing_summary.json"
CACHE_PATH    = BASE_DIR / "market_data" / "macroeconomics" / "macro_cache.json"
QUARANTINE_DIR = BASE_DIR / "market_data" / "quarantine"

# Import Gemini configurations from config
from config import GEMINI_MODEL, GEMINI_TEMPERATURE, GEMINI_MAX_OUTPUT_TOKENS

# ── Gemini Schemas ─────────────────────────────────────────────────────────

morning_briefing_schema = types.Schema(
    type=types.Type.OBJECT,
    description="Morning briefing macroeconomic indicators.",
    properties={
        "kse100_last": types.Schema(type=types.Type.NUMBER, description="Closing index value of KSE-100"),
        "kse100_chg_pct": types.Schema(type=types.Type.STRING, description="Daily change percentage with sign"),
        "kse100_fytd_pct": types.Schema(type=types.Type.STRING, description="FYTD change percentage with sign"),
        "kse100_cytd_pct": types.Schema(type=types.Type.STRING, description="CYTD change percentage with sign"),
        "lipi_fipi": types.Schema(
            type=types.Type.OBJECT,
            properties={
                cat: types.Schema(
                    type=types.Type.OBJECT,
                    properties={
                        "daily_usd_mn": types.Schema(type=types.Type.NUMBER),
                        "cytd_usd_mn": types.Schema(type=types.Type.NUMBER),
                    }
                ) for cat in ["Foreign", "Individuals", "Companies", "Banks/DFIs", "MF", "Broker", "Insurance"]
            }
        ),
        "fipi_by_sector_usd_mn": types.Schema(
            type=types.Type.OBJECT,
            properties={
                sec: types.Schema(type=types.Type.NUMBER)
                for sec in ["EP", "OMC", "Banks", "Tech", "Cement", "Fertilizer", "Autos", "Pharma"]
            }
        ),
        "top_headlines": types.Schema(
            type=types.Type.ARRAY,
            items=types.Schema(type=types.Type.STRING)
        ),
        "date": types.Schema(type=types.Type.STRING, description="Briefing date YYYY-MM-DD")
    },
    required=["kse100_last", "date"]
)

sbp_rate_schema = types.Schema(
    type=types.Type.OBJECT,
    properties={
        "policy_rate": types.Schema(type=types.Type.NUMBER),
        "bps_change": types.Schema(type=types.Type.NUMBER),
        "decision": types.Schema(type=types.Type.STRING),
        "decision_date": types.Schema(type=types.Type.STRING),
    },
    required=["policy_rate", "decision_date"]
)

cpi_schema = types.Schema(
    type=types.Type.OBJECT,
    properties={
        "cpi_yoy": types.Schema(type=types.Type.NUMBER),
        "report_title": types.Schema(type=types.Type.STRING),
        "report_date": types.Schema(type=types.Type.STRING),
    },
    required=["cpi_yoy", "report_date"]
)

# ── helpers ────────────────────────────────────────────────────────────────

def _float(text: str) -> Optional[float]:
    """Parse a float from a possibly messy string."""
    try:
        return float(re.sub(r"[,%\s]", "", text))
    except (ValueError, TypeError):
        return None


def _read(path: Path) -> str:
    try:
        return path.read_text(encoding="utf-8", errors="ignore")
    except Exception:
        return ""


def _save_json(path: Path, data: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2, ensure_ascii=False, default=str)
    logger.info(f"Saved: {path}")


def get_file_md5(path: Path) -> str:
    """Compute the MD5 hash of a file."""
    if not path.exists():
        return ""
    h = hashlib.md5()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(4096), b""):
            h.update(chunk)
    return h.hexdigest()


def get_pdf_path(md_path: Path) -> Optional[Path]:
    """Find the corresponding PDF file in briefings/pdfs/ if it exists."""
    pdf_path = PDFS_DIR / md_path.with_suffix(".pdf").name
    return pdf_path if pdf_path.exists() else None


def load_cache() -> dict:
    """Load the processing cache."""
    if CACHE_PATH.exists():
        try:
            with open(CACHE_PATH, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception:
            return {}
    return {}


def save_cache(cache: dict) -> None:
    """Save the processing cache."""
    _save_json(CACHE_PATH, cache)


# ── 1. Yahoo REST live prices ────────────────────────────────────────────────

def fetch_live_prices() -> dict:
    """Fetch PKR/USD, Brent, WTI from direct Yahoo Finance REST API."""
    import requests
    result: dict = {}
    SYMBOLS = {
        "pkr_per_usd":   "PKR=X",
        "brent_usd_bbl": "BZ=F",
        "wti_usd_bbl":   "CL=F",
    }
    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
    }
    for key, sym in SYMBOLS.items():
        try:
            url = f"https://query1.finance.yahoo.com/v8/finance/chart/{sym}"
            r = requests.get(url, headers=headers, timeout=8)
            if r.status_code == 200:
                res_data = r.json()
                price = res_data["chart"]["result"][0]["meta"]["regularMarketPrice"]
                if price:
                    result[key] = round(float(price), 2)
                    logger.info(f"Live market: {key} = {result[key]}")
        except Exception as e:
            logger.warning(f"{sym} fetch failed: {e}")
    return result


# ── 2. Gemini Extraction Engine ───────────────────────────────────────────

def extract_structured_data(
    agent: BaseAgent,
    prompt: str,
    schema: types.Schema,
    md_path: Path
) -> Optional[Dict[str, Any]]:
    """Query Gemini with schema enforcement and multimodal fallback."""
    pdf_path = get_pdf_path(md_path)
    contents = []
    
    if pdf_path:
        try:
            with open(pdf_path, "rb") as f:
                pdf_bytes = f.read()
            contents.append(
                types.Part.from_bytes(data=pdf_bytes, mime_type="application/pdf")
            )
            logger.info(f"Querying multimodal PDF: {pdf_path.name}")
        except Exception as e:
            logger.warning(f"Failed to read PDF bytes for {pdf_path.name}: {e}. Falling back to markdown text.")
            pdf_path = None

    if not pdf_path:
        text = md_path.read_text(encoding="utf-8", errors="ignore")
        contents.append(types.Part.from_text(text=text))
        logger.info(f"Querying extracted markdown text: {md_path.name}")

    contents.append(prompt)
    
    # Retry loop with exponential backoff
    import time
    for attempt in range(1, 4):
        try:
            response = agent.client.models.generate_content(
                model=GEMINI_MODEL,
                contents=contents,
                config=types.GenerateContentConfig(
                    system_instruction=agent.persona,
                    temperature=0.0,  # low temperature for accuracy
                    max_output_tokens=GEMINI_MAX_OUTPUT_TOKENS,
                    response_mime_type="application/json",
                    response_schema=schema
                )
            )
            if response.text:
                return json.loads(response.text.strip())
        except Exception as e:
            err_str = str(e)
            # Rate-limiting: wait longer
            if "429" in err_str or "quota" in err_str.lower():
                wait = 15 * attempt
                logger.warning(f"Rate limited. Waiting {wait}s (attempt {attempt}/3)...")
                time.sleep(wait)
            else:
                wait = 2 ** attempt
                logger.warning(f"Extraction failed: {err_str}. Retrying in {wait}s (attempt {attempt}/3)...")
                time.sleep(wait)
    return None


def process_verification(
    collection: str,
    md_path: Path,
    extracted_data: dict,
    extracted_verify: dict
) -> bool:
    """Run deterministic anti-hallucination checks against source text."""
    pdf_path = get_pdf_path(md_path)
    text = md_path.read_text(encoding="utf-8", errors="ignore")
    
    verification = verify_against_source(
        collection=collection,
        extracted=extracted_verify,
        pdf_path=pdf_path,
        html_content=text
    )
    
    if not verification["passed"]:
        logger.warning(f"[verify] QUARANTINED {collection} from {md_path.name} due to verification failures: {verification['failures']}")
        QUARANTINE_DIR.mkdir(parents=True, exist_ok=True)
        q_path = QUARANTINE_DIR / f"{collection}_{md_path.stem}_failed.json"
        q_data = {
            "source_file": md_path.name,
            "extracted": extracted_data,
            "verification": verification
        }
        with open(q_path, "w", encoding="utf-8") as f:
            json.dump(q_data, f, indent=2)
        return False
        
    logger.info(f"[verify] PASSED verification for {collection} from {md_path.name}")
    return True


# ── 3. Morning briefing parser ─────────────────────────────────────────────

def _latest_mb_file() -> Optional[Path]:
    """Return the most recent MB_DD-MM-YYYY.md file."""
    files = sorted(EXTRACTED_DIR.glob("MB_*.md"), reverse=True)
    return files[0] if files else None


def extract_morning_briefing_gemini(agent: BaseAgent, mb_path: Path) -> Optional[dict]:
    prompt = (
        "Extract Pakistan stock market morning briefing data. Ensure all figures match the document exactly.\n"
        "Required fields:\n"
        "- kse100_last: Last closing level of KSE-100 index (number)\n"
        "- kse100_chg_pct: Daily change percentage (string, e.g. '-0.4%')\n"
        "- kse100_fytd_pct: Financial Year to Date change percentage (string, e.g. '+33.0%')\n"
        "- kse100_cytd_pct: Calendar Year to Date change percentage (string, e.g. '-2.1%')\n"
        "- lipi_fipi: flows in USD millions per category (Foreign, Individuals, Companies, Banks/DFIs, MF, Broker, Insurance)\n"
        "- fipi_by_sector_usd_mn: sector flows in USD millions (EP, OMC, Banks, Tech, Cement, Fertilizer, Autos, Pharma)\n"
        "- top_headlines: Top news headlines / summaries mentioned (array of strings)\n"
        "- date: Date of briefing (YYYY-MM-DD)\n"
    )
    return extract_structured_data(agent, prompt, morning_briefing_schema, mb_path)


def parse_morning_briefing_regex(mb: Path) -> dict:
    """Original regex fallback morning briefing parser."""
    text = _read(mb)
    result: dict = {"source_file": mb.name}

    m = re.search(r"KSE-100\s+([\d,]+)\s+([+-][\d.]+%)\s+([+-][\d.]+%)\s+([+-][\d.]+%)", text)
    if m:
        result["kse100_last"]      = _float(m.group(1))
        result["kse100_chg_pct"]   = m.group(2)
        result["kse100_fytd_pct"]  = m.group(3)
        result["kse100_cytd_pct"]  = m.group(4)

    lipi_fipi: dict = {}
    for cat in ("Foreign", "Individuals", "Companies", "Banks/DFIs", "MF", "Broker", "Insurance"):
        pattern = rf"{cat}\s+([+-]?[\d.]+)\s+([+-]?[\d.]+)"
        m = re.search(pattern, text)
        if m:
            lipi_fipi[cat] = {"daily_usd_mn": _float(m.group(1)), "cytd_usd_mn": _float(m.group(2))}
    if lipi_fipi:
        result["lipi_fipi"] = lipi_fipi

    sectors_fipi: dict = {}
    for sec in ("EP", "OMC", "Banks", "Tech", "Cement", "Fertilizer", "Autos", "Pharma"):
        m = re.search(rf"\b{sec}\s+([+-]?[\d.]+)\b", text)
        if m:
            sectors_fipi[sec] = _float(m.group(1))
    if sectors_fipi:
        result["fipi_by_sector_usd_mn"] = sectors_fipi

    headlines = re.findall(r"([A-Z][^:\n]{20,120}):\s*\n", text)
    result["top_headlines"] = headlines[:6]

    m = re.search(r"(\d{1,2}\s+\w+\s+\d{4})\s*\n\s*Morning Briefing", text)
    result["date"] = m.group(1) if m else mb.stem.replace("MB_", "")

    return result


def parse_morning_briefing(agent: BaseAgent, cache: dict) -> dict:
    mb = _latest_mb_file()
    if not mb:
        logger.warning("no morning briefing .md found")
        return {}

    file_md5 = get_file_md5(mb)
    cached_entry = cache.get("morning_briefing", {})
    if cached_entry.get("file_name") == mb.name and cached_entry.get("md5") == file_md5:
        logger.info(f"Morning briefing '{mb.name}' is cached.")
        return cached_entry.get("data")

    logger.info(f"Extracting morning briefing from {mb.name}...")
    extracted = extract_morning_briefing_gemini(agent, mb)
    if extracted:
        extracted_verify = {
            "kse100_last": extracted.get("kse100_last"),
            "date": extracted.get("date")
        }
        if process_verification("morning_briefing", mb, extracted, extracted_verify):
            extracted["source_file"] = mb.name
            cache["morning_briefing"] = {
                "file_name": mb.name,
                "md5": file_md5,
                "data": extracted
            }
            save_cache(cache)
            return extracted

    logger.warning("Gemini extraction or verification failed. Falling back to regex morning briefing parser...")
    fallback = parse_morning_briefing_regex(mb)
    return fallback


# ── 3. Economy reports parser ──────────────────────────────────────────────

def _md_date(path: Path) -> date:
    """Extract date from markdown frontmatter **Date:** line."""
    m = re.search(r"\*\*Date:\*\*\s*([\d]{4}-[\d]{2}-[\d]{2})", _read(path))
    if m:
        try:
            return datetime.strptime(m.group(1), "%Y-%m-%d").date()
        except ValueError:
            pass
    return date.min


def extract_sbp_rate_gemini(agent: BaseAgent, md_path: Path) -> Optional[dict]:
    prompt = (
        "Extract the State Bank of Pakistan (SBP) monetary policy rate decision from this report.\n"
        "Required fields:\n"
        "- policy_rate: SBP interest policy rate percentage (number, e.g. 11.5)\n"
        "- bps_change: The rate change in basis points (number, e.g. 100 for hike of 100bps, -50 for cut, 0 for status quo/unchanged)\n"
        "- decision: raised/cut/unchanged (string)\n"
        "- decision_date: YYYY-MM-DD date of the policy announcement\n"
    )
    extracted = extract_structured_data(agent, prompt, sbp_rate_schema, md_path)
    if extracted:
        # Convert to expected format
        rate = extracted.get("policy_rate")
        bps = extracted.get("bps_change", 0)
        decision_type = extracted.get("decision", "unchanged")
        
        # Structure matching original pipeline expectation
        bps_val = int(bps) if bps is not None else 0
        if bps_val != 0:
            decision = f"{decision_type} {bps_val:+d}bps"
        else:
            decision = decision_type
            
        return {
            "rate_pct": rate,
            "decision": decision,
            "decision_date": extracted.get("decision_date"),
            "bps_change": bps_val
        }
    return None


def parse_sbp_rate_regex() -> dict:
    """Original regex SBP rate parser fallback."""
    result: dict = {}
    candidates = sorted(
        [f for f in EXTRACTED_DIR.glob("*.md") if "sbp" in f.name.lower() or "mpc" in f.name.lower()],
        key=_md_date,
        reverse=True,
    )

    last_known_rate: Optional[float] = None
    total_bps_change: int = 0
    last_decision_date: str = ""
    last_decision_type: str = "unchanged"

    for md in sorted(candidates, key=_md_date):
        full_text = _read(md)
        d         = _md_date(md)
        head = full_text[:1000]

        m = re.search(r"policy\s+rate\s+by\s+(\d+)\s*bps\s+to\s+([\d.]+)%", head, re.I)
        if m:
            last_known_rate   = float(m.group(2))
            total_bps_change  = 0
            last_decision_date = str(d)
            last_decision_type = f"raised +{m.group(1)}bps"
            continue

        m = re.search(r"(?:increases?|cuts?|reduced?|raised?)\s+(?:the\s+)?policy\s+rate\s+by\s+(\d+)\s*bps", head, re.I)
        if m:
            bps = int(m.group(1))
            direction = +1 if re.search(r"\b(increas|rais)", m.group(0), re.I) else -1
            total_bps_change += direction * bps
            last_decision_date = str(d)
            last_decision_type = f"{'raised' if direction > 0 else 'cut'} {direction*bps:+d}bps"
            continue

        m = re.search(r"(?:status\s+quo|maintains?\s+.*policy\s+rate)\s+at\s+([\d.]+)%", head, re.I)
        if m:
            last_known_rate   = float(m.group(1))
            total_bps_change  = 0
            last_decision_date = str(d)
            last_decision_type = "unchanged"
            continue

    if last_known_rate is not None:
        final_rate = last_known_rate + total_bps_change / 100
        result = {
            "rate_pct": round(final_rate, 2),
            "decision": last_decision_type,
            "decision_date": last_decision_date,
        }
    elif total_bps_change != 0:
        result = {
            "rate_pct": None,
            "decision": f"net {total_bps_change:+d}bps from unknown base",
            "decision_date": last_decision_date,
        }
    return result


def parse_sbp_rate(agent: BaseAgent, cache: dict) -> dict:
    candidates = sorted(
        [f for f in EXTRACTED_DIR.glob("*.md") if "sbp" in f.name.lower() or "mpc" in f.name.lower()],
        key=_md_date,
        reverse=True,
    )
    if not candidates:
        logger.warning("No SBP reports found.")
        return {}

    latest_sbp = candidates[0]
    file_md5 = get_file_md5(latest_sbp)
    cached_entry = cache.get("sbp_rate", {})
    if cached_entry.get("file_name") == latest_sbp.name and cached_entry.get("md5") == file_md5:
        logger.info(f"SBP rate report '{latest_sbp.name}' is cached.")
        return cached_entry.get("data")

    logger.info(f"Extracting SBP rate from {latest_sbp.name}...")
    extracted = extract_sbp_rate_gemini(agent, latest_sbp)
    if extracted:
        extracted_verify = {
            "policy_rate": extracted.get("rate_pct"),
            "decision_date": extracted.get("decision_date")
        }
        if process_verification("sbp_rate", latest_sbp, extracted, extracted_verify):
            cache["sbp_rate"] = {
                "file_name": latest_sbp.name,
                "md5": file_md5,
                "data": extracted
            }
            save_cache(cache)
            return extracted

    logger.warning("Gemini SBP extraction or verification failed. Falling back to SBP regex parser...")
    fallback = parse_sbp_rate_regex()
    return fallback


def extract_cpi_gemini(agent: BaseAgent, md_path: Path) -> Optional[dict]:
    prompt = (
        "Extract the CPI YoY inflation rate percentage from this report.\n"
        "Required fields:\n"
        "- cpi_yoy: YoY CPI inflation percentage (number, e.g. 11.9)\n"
        "- report_title: Subject or title of the report\n"
        "- report_date: Report release date in YYYY-MM-DD format\n"
    )
    extracted = extract_structured_data(agent, prompt, cpi_schema, md_path)
    if extracted:
        return {
            "cpi_yoy_pct": extracted.get("cpi_yoy"),
            "report_title": extracted.get("report_title"),
            "report_date": extracted.get("report_date"),
            "source": md_path.name
        }
    return None


def parse_cpi_regex() -> dict:
    """Original regex CPI inflation parser fallback."""
    result: dict = {}
    inflation_mds = sorted(
        [f for f in EXTRACTED_DIR.glob("*.md") if "inflation" in f.name.lower()],
        key=_md_date,
        reverse=True,
    )

    for md in inflation_mds:
        text  = _read(md)
        d     = _md_date(md)
        title_m = re.search(r"#\s+(.+)", text)
        title   = title_m.group(1).strip() if title_m else md.stem

        m = re.search(r"(?:increase|rise|stand)\s+(?:to|by|at)?\s*([\d.]+)%\s*YoY", text, re.I)
        if not m:
            m = re.search(r"([\d.]+)%\s*YoY", text, re.I)

        if m:
            result = {
                "cpi_yoy_pct": float(m.group(1)),
                "report_title": title,
                "report_date": str(d),
                "source": md.name,
            }
            break
    return result


def parse_cpi(agent: BaseAgent, cache: dict) -> dict:
    inflation_mds = sorted(
        [f for f in EXTRACTED_DIR.glob("*.md") if "inflation" in f.name.lower()],
        key=_md_date,
        reverse=True,
    )
    if not inflation_mds:
        logger.warning("No CPI reports found.")
        return {}

    latest_cpi = inflation_mds[0]
    file_md5 = get_file_md5(latest_cpi)
    cached_entry = cache.get("cpi_inflation", {})
    if cached_entry.get("file_name") == latest_cpi.name and cached_entry.get("md5") == file_md5:
        logger.info(f"CPI report '{latest_cpi.name}' is cached.")
        return cached_entry.get("data")

    logger.info(f"Extracting CPI inflation from {latest_cpi.name}...")
    extracted = extract_cpi_gemini(agent, latest_cpi)
    if extracted:
        extracted_verify = {
            "cpi_yoy": extracted.get("cpi_yoy_pct")
        }
        if process_verification("cpi_inflation", latest_cpi, extracted, extracted_verify):
            cache["cpi_inflation"] = {
                "file_name": latest_cpi.name,
                "md5": file_md5,
                "data": extracted
            }
            save_cache(cache)
            return extracted

    logger.warning("Gemini CPI extraction or verification failed. Falling back to CPI regex parser...")
    fallback = parse_cpi_regex()
    return fallback


def _inflation_trend(cpi_files: list[Path]) -> str:
    """Determine trend from last 3 CPI readings using regex."""
    values: list[float] = []
    for md in sorted(cpi_files, key=_md_date)[-3:]:
        text = _read(md)
        m = re.search(r"([\d.]+)%\s*YoY", text, re.I)
        if m:
            values.append(float(m.group(1)))
    if len(values) < 2:
        return "unknown"
    if values[-1] > values[-2]:
        return "rising"
    if values[-1] < values[-2]:
        return "falling"
    return "stable"


# ── main ───────────────────────────────────────────────────────────────────

def main(no_live: bool = False) -> None:
    now = datetime.utcnow().isoformat()
    today = str(date.today())

    logger.info("== Macro Data Fetcher (Robust Gemini Mode) ==")
    logger.info(f"As of: {today}")

    macro: dict[str, Any] = {"as_of": today, "last_updated": now}

    # Initialize BaseAgent
    agent = BaseAgent(
        name="MacroExtractor",
        persona="You are a precise Pakistani financial data extractor. Output ONLY valid JSON matching the requested schema."
    )

    # Load processing cache
    cache = load_cache()

    # 1. Live prices
    if not no_live:
        logger.info("[1] Fetching live prices (Yahoo REST)...")
        macro.update(fetch_live_prices())
    else:
        logger.info("[1] Skipping live prices (--no-live)")

    # 2. Morning briefing
    logger.info("[2] Parsing morning briefing...")
    briefing = parse_morning_briefing(agent, cache)
    if briefing:
        for key in ("kse100_last", "kse100_chg_pct", "kse100_fytd_pct", "kse100_cytd_pct"):
            if key in briefing:
                macro[key] = briefing[key]
        macro["lipi_fipi"]              = briefing.get("lipi_fipi", {})
        macro["fipi_by_sector_usd_mn"]  = briefing.get("fipi_by_sector_usd_mn", {})
        macro["briefing_date"]          = briefing.get("date", "")
        _save_json(BRIEFING_PATH, briefing)

    # 3. SBP rate
    logger.info("[3] Parsing SBP policy rate...")
    sbp = parse_sbp_rate(agent, cache)
    if sbp:
        macro["sbp_policy_rate_pct"]  = sbp.get("rate_pct")
        macro["sbp_decision"]         = sbp.get("decision")
        macro["sbp_decision_date"]    = sbp.get("decision_date")

    # 4. CPI
    logger.info("[4] Parsing CPI / inflation...")
    cpi = parse_cpi(agent, cache)
    if cpi:
        macro["cpi_yoy_pct"]          = cpi.get("cpi_yoy_pct")
        macro["cpi_report_date"]      = cpi.get("report_date")

    # Inflation trend
    inflation_files = [f for f in EXTRACTED_DIR.glob("*.md") if "inflation" in f.name.lower()]
    macro["inflation_trend"] = _inflation_trend(inflation_files)

    # Derived: real interest rate
    if macro.get("sbp_policy_rate_pct") is not None and macro.get("cpi_yoy_pct") is not None:
        macro["real_interest_rate_pct"] = round(
            macro["sbp_policy_rate_pct"] - macro["cpi_yoy_pct"], 2
        )
        logger.info(f"Derived: Real interest rate = {macro['real_interest_rate_pct']}%")

    # Save
    logger.info("[5] Saving final macro data...")
    _save_json(MACRO_PATH, macro)

    logger.info("== Summary ==")
    for k, v in macro.items():
        if k not in ("lipi_fipi", "fipi_by_sector_usd_mn"):
            logger.info(f"  {k:<30} {v}")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Fetch Pakistan macro data snapshot")
    parser.add_argument("--no-live", action="store_true", help="Skip live prices")
    args = parser.parse_args()
    main(no_live=args.no_live)
