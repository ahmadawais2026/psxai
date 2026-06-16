import re
from pathlib import Path
from typing import Any, Dict, List, Optional
import pdfplumber

MONTHS = [
    'january', 'february', 'march', 'april', 'may', 'june', 
    'july', 'august', 'september', 'october', 'november', 'december'
]

def normalize(s: str) -> str:
    """Lowercase string, strip thousands commas, collapse spaces."""
    s_str = str(s)
    # Strip thousands separators inside numbers: e.g. 100,000 -> 100000
    s_cleaned = re.sub(r"(\d),(?=\d)", r"\1", s_str)
    return " ".join(s_cleaned.lower().split())

def number_renderings(value: Any) -> List[str]:
    """Candidate string renderings for a numeric value to check verbatim presence."""
    try:
        n = float(value)
    except (ValueError, TypeError):
        return []
    out = set()
    s = str(n)
    out.add(s)
    if n.is_integer():
        out.add(str(int(n)))
    else:
        out.add(f"{n:.2f}")
        out.add(f"{n:.1f}")
        out.add(str(float(f"{n:.2f}")))
    return [r.lower() for r in out]

def date_renderings(value: Any) -> List[str]:
    """Candidate renderings for an ISO-ish date (YYYY-MM-DD)."""
    val_str = str(value)
    m = re.match(r"^(\d{4})-(\d{2})-(\d{2})", val_str)
    if not m:
        return [normalize(val_str)]
    y, mo, d = m.group(1), m.group(2), m.group(3)
    try:
        month_idx = int(mo) - 1
        month = MONTHS[month_idx] if 0 <= month_idx < 12 else "unknown"
    except (ValueError, IndexError):
        month = "unknown"
    
    day_n = str(int(d))
    
    def ordinal(n: int) -> str:
        s = ['th', 'st', 'nd', 'rd']
        v = n % 100
        if 11 <= v <= 13:
            return 'th'
        return s[v % 10] if v % 10 < len(s) else 'th'

    ord_suffix = ordinal(int(day_n))
    
    renderings = [
        f"{y}-{mo}-{d}",
        f"{month} {day_n}, {y}",
        f"{month} {d}, {y}",
        f"{day_n} {month} {y}",
        f"{day_n}{ord_suffix} {month} {y}",
        f"{month} {day_n} {y}",
        f"{d}-{mo}-{y}",
        f"{d}/{mo}/{y}",
    ]
    return [normalize(r) for r in renderings]

def field_present(value: Any, kind: str, normalized_source: str) -> bool:
    """Check if a field is present in the source."""
    if value is None or value == "":
        return True
    
    val_str = str(value).strip().lower()
    if val_str in ("not disclosed", "n/a", "tbd", "—", "-"):
        return True
        
    if kind == 'number':
        renderings = number_renderings(value)
        return any(r in normalized_source for r in renderings)
        
    if kind == 'date':
        renderings = date_renderings(value)
        return any(r in normalized_source for r in renderings)
        
    if kind == 'rating':
        core = normalize(re.sub(r"\(.*?\)", "", str(value)).strip())
        return len(core) > 0 and core in normalized_source
        
    # string check: token containment
    tokens = [t for t in normalize(str(value)).split() if len(t) > 3]
    if not tokens:
        return True
    hits = sum(1 for t in tokens if t in normalized_source)
    return (hits / len(tokens)) >= 0.6

def verify_against_source(
    collection: str, 
    extracted: Dict[str, Any], 
    pdf_path: Optional[Path] = None, 
    html_content: Optional[str] = None
) -> Dict[str, Any]:
    """
    Verify extracted data fields against source PDF or HTML.
    
    Returns a dict with verification results.
    """
    chunks = []
    sources = []
    
    if pdf_path and pdf_path.exists():
        try:
            with pdfplumber.open(pdf_path) as pdf:
                pdf_text = "\n".join([page.extract_text() or "" for page in pdf.pages])
                if pdf_text:
                    chunks.append(pdf_text)
                    sources.append(pdf_path.name)
        except Exception as e:
            print(f"[verify] PDF extraction failed for {pdf_path.name}: {e}")
            
    if html_content:
        # Strip HTML tags
        clean_html = re.sub(r"<[^>]+>", " ", str(html_content))
        chunks.append(clean_html)
        sources.append("html_content")
        
    source_text = "\n".join(chunks)
    normalized = normalize(source_text)
    
    if len(normalized) < 50:
        return {
            "checked": False,
            "passed": False,
            "failures": [{"field": "(source)", "value": None, "reason": "No source text available to verify against"}],
            "sources": sources
        }
        
    # Define critical fields to verify per category
    contracts = {
        "morning_briefing": [
            ("kse100_last", "number"),
            ("date", "date"),
        ],
        "sbp_rate": [
            ("policy_rate", "number"),
            ("decision_date", "date"),
        ],
        "cpi_inflation": [
            ("cpi_yoy", "number"),
        ]
    }
    
    spec = contracts.get(collection, [])
    failures = []
    
    for field, kind in spec:
        value = extracted.get(field)
        if field == "policy_rate" and value is None:
            continue
            
        if not field_present(value, kind, normalized):
            failures.append({
                "field": field,
                "value": value,
                "reason": f"Value '{value}' not found in source document ({kind} match)"
            })
            
    return {
        "checked": True,
        "passed": len(failures) == 0,
        "failures": failures,
        "sources": sources
    }
