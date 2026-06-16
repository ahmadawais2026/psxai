"""
Simplified Derived Cash Flow Calculator
=========================================
Uses only major Balance Sheet category TOTALS — not individual line items —
so the calculation is fully standardized across all 158 companies regardless
of how each company labels its individual rows.

Key fix: Pakistani companies report Income Statement figures CUMULATIVELY
(Q2 = 6-month YTD, Q3 = 9-month YTD, Q4 = 12-month YTD). The IS is
converted to per-quarter figures by detecting fiscal year resets before
being used in the derivation.

Method (Indirect, always-balancing):
  Operating CF  = PAT + Dep - Δ(Non-cash Current Assets) + Δ(Non-debt Current Liabilities)
  Investing CF  = -Δ(Total Non-Current Assets) - Dep - Δ(ST Investments)
  Financing CF  = Δ(Financial Debt) + Δ(Total NCL) + Δ(Paid-up Capital) - Dividends

  Non-cash CA   = Total CA  - Cash - ST Investments
  Non-debt CL   = Total CL  - ST Debt - Current portion LT Debt

  The actual cash movement (Cash Closing - Cash Opening) is taken from the
  Balance Sheet as GROUND TRUTH. OCF/ICF/FCF are estimates; a residual line
  "Other / Non-cash Adj" absorbs whatever cannot be attributed from BS totals
  alone (revaluation surplus, deferred tax through equity, FX translation,
  minority interest, goodwill/intangible movements, asset reclassifications).
  By construction:  OCF + ICF + FCF + Other == Net Change in Cash  (exactly).

  Quality = "Explained %" = share of gross cash flows cleanly attributed to
  real activities vs. the unexplained plug. HIGH ≥80%, MEDIUM ≥50%, LOW <50%.

Outputs:
  - "Simplified CF" sheet added to each company file (original CF sheet untouched)
  - company_data/cashflow_audit.xlsx — quality summary + low-confidence flags
"""

import os
import re
import numpy as np
import pandas as pd

COMPANY_DATA_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "company_data")
EXPLAINED_HIGH   = 80   # % of gross cash flow cleanly attributed → HIGH confidence
EXPLAINED_MED    = 50   # ≥ this → MEDIUM; below → LOW


# ─── Row finder (keyword substring matching & fuzzy fallback) ─────────────────

KEYWORDS = {
    # Major totals — present in every company
    "cash":       [("cash & bank balances",), ("cash and bank balances",),
                   ("cash and balances with treasury",), ("cash and balances",)],
    "st_inv":     [("short term investments",), ("short-term investments",)],
    "total_ca":   [("total current assets",), ("current assets",)],
    "total_nca":  [("total non-current assets",), ("non-current assets",),
                   ("non current assets",)],
    "total_cl":   [("total current liabilities",), ("current liabilities",)],
    "total_ncl":  [("total non-current liabilities",), ("non-current liabilities",),
                   ("non current liabilities",)],
    "total_eq":   [("total equity",)],
    "paid_up":    [("equity - paid-up capital",), ("equity - paid up capital",),
                   ("paid-up capital",), ("paid up capital",)],
    # Debt (to separate financial from operating in CL)
    "st_debt":    [("short-term debt",), ("short term debt",),
                   ("short term borrowings",), ("borrowings",)],
    "curr_ltd":   [("current portion of long-term debt",),
                   ("current maturity of long-term",),
                   ("current portion of long term",)],
    # Income statement
    "pat":        [("profit after tax",), ("net profit after tax",),
                   ("profit after taxation",)],
    "dep":        [("depreciation & amortisation",), ("depreciation and amortisation",),
                   ("depreciation & amortization",), ("depreciation",)],
    "dps":        [("dps",)],
}

NAME_STOPWORDS = {
    "limited", "ltd", "the", "company", "co", "pvt", "private", "corporation", 
    "corp", "inc", "and", "or", "of", "with", "to", "for", "at", "by", "in", 
    "on", "from", "a", "an", "as", "&"
}

def _get_tokens(s: str) -> set[str]:
    """Tokenize a string for similarity checking, ignoring common stopwords."""
    cleaned = re.sub(r"[^a-z0-9\s]", " ", str(s).lower())
    return {t for t in cleaned.split() if t and t not in NAME_STOPWORDS}

def _tokens_match(t1: str, t2: str) -> bool:
    """Check if two tokens are similar (exact, plural, or prefix match)."""
    if t1 == t2:
        return True
    # Plural check
    if t1 + 's' == t2 or t2 + 's' == t1:
        return True
    # Prefix check (length >= 4) to handle abbreviations
    if len(t1) >= 4 and len(t2) >= 4:
        if t1.startswith(t2) or t2.startswith(t1):
            return True
    return False

def _dice_coefficient(a: str, b: str) -> float:
    """Compute Dice similarity coefficient between two strings based on tokens with prefix/plural support."""
    ta = _get_tokens(a)
    tb = _get_tokens(b)
    if not ta or not tb:
        return 0.0
        
    common = 0
    tb_matched = set()
    for t1 in ta:
        for t2 in tb:
            if t2 not in tb_matched and _tokens_match(t1, t2):
                common += 1
                tb_matched.add(t2)
                break
                
    return (2.0 * common) / (len(ta) + len(tb))

def find_row(df, key):
    # First attempt: exact substring match (preserving original strict path)
    for group in KEYWORDS.get(key, []):
        for idx in df.index:
            if all(kw in str(idx).lower() for kw in group):
                return idx
                
    # Second attempt: fuzzy token-based matching if exact match fails
    best_idx = None
    best_score = 0.0
    threshold = 0.75  # Safe similarity threshold
    
    targets = [" ".join(group) for group in KEYWORDS.get(key, [])]
    
    for idx in df.index:
        idx_str = str(idx).lower()
        for target in targets:
            score = _dice_coefficient(idx_str, target)
            if score > best_score:
                best_score = score
                best_idx = idx
                
    if best_score >= threshold:
        print(f"  [fuzzy match] Resolved '{best_idx}' for key '{key}' (target: '{targets[0]}', score: {best_score:.2f})")
        return best_idx
        
    return None


def val(df, col, key):
    row = find_row(df, key)
    if row is None or col not in df.columns:
        return np.nan
    return pd.to_numeric(df.loc[row, col], errors="coerce")


def nanv(x):
    return 0.0 if np.isnan(x) else x


# ─── Cumulative → quarterly conversion ───────────────────────────────────────

def _parse_col_date(col):
    """Parse a column label like 'Dec-11' or 'Sep-25' to a sortable date."""
    try:
        return pd.to_datetime(str(col).strip(), format="%b-%y")
    except Exception:
        return None


def to_quarterly(inc):
    """
    Return a copy of the Income Statement with all rows converted from
    cumulative YTD to per-quarter figures.

    Pakistani companies report YTD totals in quarterly filings:
      Q1 = 3-month    → use as-is
      Q2 = 6-month    → subtract Q1
      Q3 = 9-month    → subtract Q2 cumulative
      Q4 = 12-month   → subtract Q3 cumulative

    Fiscal year reset detection: when a value drops by >40% from the
    prior quarter it is treated as Q1 of a new fiscal year (reset).
    Negative PAT or zero values are handled gracefully.
    """
    inc_q = inc.copy().astype(object)

    # Sort columns oldest → newest for the conversion, then restore original order
    cols = list(inc.columns)
    dated = [(c, _parse_col_date(c)) for c in cols]
    # Only sort if we can parse all dates; otherwise leave order as-is
    if all(d is not None for _, d in dated):
        cols_sorted = [c for c, _ in sorted(dated, key=lambda x: x[1])]
    else:
        cols_sorted = list(reversed(cols))   # assume newest-first, reverse to oldest-first

    for row_idx in inc_q.index:
        row_data = inc.loc[row_idx]
        # Duplicate index labels return a DataFrame — take the first row
        if isinstance(row_data, pd.DataFrame):
            row_data = row_data.iloc[0]
        raw = pd.to_numeric(row_data, errors="coerce")
        series = raw[cols_sorted]            # oldest → newest

        converted = series.copy().astype(float)
        for i in range(1, len(series)):
            curr_v = series.iloc[i]
            prev_v = series.iloc[i - 1]

            if pd.isna(curr_v) or pd.isna(prev_v):
                continue

            # Detect fiscal year reset:
            # Value drops >40% from prior (and prior was positive) → Q1 reset
            is_reset = (prev_v > 0 and curr_v < prev_v * 0.60) or \
                       (prev_v < 0 and curr_v > prev_v * 0.60)

            if not is_reset:
                converted.iloc[i] = curr_v - prev_v   # strip cumulative

        # Map back to original column order
        for col in cols:
            inc_q.loc[row_idx, col] = converted.get(col, raw[col])

    # Restore numeric dtype
    for col in inc_q.columns:
        inc_q[col] = pd.to_numeric(inc_q[col], errors="coerce")

    return inc_q


# ─── Per-quarter derivation ────────────────────────────────────────────────────

def derive_quarter(bs, inc, curr, prev):
    # ── Balance sheet totals ──────────────────────────────────────────
    cash_c    = val(bs, curr, "cash");       cash_p    = val(bs, prev, "cash")
    st_inv_c  = val(bs, curr, "st_inv");     st_inv_p  = val(bs, prev, "st_inv")
    total_ca_c= val(bs, curr, "total_ca");   total_ca_p= val(bs, prev, "total_ca")
    total_nca_c=val(bs, curr, "total_nca");  total_nca_p=val(bs, prev, "total_nca")
    total_cl_c= val(bs, curr, "total_cl");   total_cl_p= val(bs, prev, "total_cl")
    total_ncl_c=val(bs, curr, "total_ncl");  total_ncl_p=val(bs, prev, "total_ncl")
    st_debt_c = val(bs, curr, "st_debt");    st_debt_p = val(bs, prev, "st_debt")
    curr_ltd_c= val(bs, curr, "curr_ltd");   curr_ltd_p= val(bs, prev, "curr_ltd")
    paid_up_c = val(bs, curr, "paid_up")

    # ── Income statement ──────────────────────────────────────────────
    pat  = val(inc, curr, "pat") if curr in inc.columns else np.nan
    dep  = val(inc, curr, "dep") if curr in inc.columns else np.nan
    dps  = val(inc, curr, "dps") if curr in inc.columns else np.nan

    # Dividends estimate: DPS × shares outstanding (face value PKR 10)
    shares_mn = paid_up_c / 10 if not np.isnan(paid_up_c) else np.nan
    dividends = -(dps * shares_mn) if not (np.isnan(dps) or np.isnan(shares_mn) or dps == 0) else 0.0

    # ── Non-cash current assets (working capital assets) ─────────────
    #   = Total CA - Cash - ST Investments
    wca_c = nanv(total_ca_c) - nanv(cash_c) - nanv(st_inv_c)
    wca_p = nanv(total_ca_p) - nanv(cash_p) - nanv(st_inv_p)
    d_wca = wca_c - wca_p        # increase = cash used = negative for OCF

    # ── Non-debt current liabilities (operating CL) ───────────────────
    #   = Total CL - ST Debt - Current portion LT Debt
    op_cl_c = nanv(total_cl_c) - nanv(st_debt_c) - nanv(curr_ltd_c)
    op_cl_p = nanv(total_cl_p) - nanv(st_debt_p) - nanv(curr_ltd_p)
    d_op_cl = op_cl_c - op_cl_p  # increase = cash received = positive for OCF

    # ── Operating CF ──────────────────────────────────────────────────
    operating_cf = nanv(pat) + nanv(dep) - d_wca + d_op_cl

    # ── Investing CF ──────────────────────────────────────────────────
    # NCA decreases by dep each period; gross change = ΔNCA + dep = new capex (approx)
    # Negative because capex is cash outflow
    d_nca = (nanv(total_nca_c) - nanv(total_nca_p))
    investing_cf = -(d_nca + nanv(dep)) - (nanv(st_inv_c) - nanv(st_inv_p))

    # ── Financing CF ──────────────────────────────────────────────────
    # Financial debt in current liabilities
    fin_cl_c = nanv(st_debt_c) + nanv(curr_ltd_c)
    fin_cl_p = nanv(st_debt_p) + nanv(curr_ltd_p)
    d_fin_cl  = fin_cl_c - fin_cl_p

    d_ncl     = nanv(total_ncl_c) - nanv(total_ncl_p)
    d_paid_up = nanv(paid_up_c) - nanv(val(bs, prev, "paid_up"))

    financing_cf = d_fin_cl + d_ncl + d_paid_up + nanv(dividends)

    # ── Reconciliation (always balances by construction) ─────────────
    # The actual cash movement from the Balance Sheet is GROUND TRUTH.
    # OCF/ICF/FCF are estimates; a residual line absorbs everything that
    # cannot be cleanly attributed from BS totals alone — revaluation
    # surpluses, deferred tax through equity, FX translation, minority
    # interest, intangible/goodwill movements, asset reclassifications.
    net_actual = (cash_c - cash_p) if not (np.isnan(cash_c) or np.isnan(cash_p)) else np.nan

    est_total = operating_cf + investing_cf + financing_cf
    # Residual plug so OCF + ICF + FCF + Other == actual cash change EXACTLY
    other_adj = (net_actual - est_total) if not np.isnan(net_actual) else np.nan

    # Quality = share of gross cash flows that is cleanly attributed to
    # operating/investing/financing activities (vs. the unexplained plug).
    gross = abs(operating_cf) + abs(investing_cf) + abs(financing_cf) + abs(nanv(other_adj))
    explained_pct = (1 - abs(nanv(other_adj)) / gross) * 100 if gross > 0 else np.nan

    quality = ("HIGH"    if not np.isnan(explained_pct) and explained_pct >= EXPLAINED_HIGH else
               "MEDIUM"  if not np.isnan(explained_pct) and explained_pct >= EXPLAINED_MED  else
               "LOW"     if not np.isnan(explained_pct)                                     else
               "NO DATA")

    return {
        # ── Balance sheet snapshot ────────────────────────
        "Cash & Equivalents":           cash_c,
        "ST Investments":               st_inv_c,
        "Total Current Assets":         total_ca_c,
        "Non-cash Current Assets":      wca_c,
        "Total Non-Current Assets":     total_nca_c,
        "Total Current Liabilities":    total_cl_c,
        "Operating Current Liab":       op_cl_c,
        "Financial Debt (Current)":     fin_cl_c,
        "Total Non-Current Liab":       total_ncl_c,
        "Total Equity":                 val(bs, curr, "total_eq"),
        # ── Period changes ────────────────────────────────
        "Δ Non-cash CA (WC assets)":    d_wca,
        "Δ Operating CL (WC liab)":     d_op_cl,
        "Δ Non-Current Assets":         d_nca,
        "Δ Financial Debt (Current)":   d_fin_cl,
        "Δ Non-Current Liab":           d_ncl,
        "Δ Paid-up Capital":            d_paid_up,
        # ── IS inputs ─────────────────────────────────────
        "PAT (quarterly)":              pat,
        "Depreciation (quarterly)":     dep,
        "Dividends Paid (est)":         dividends,
        # ── Cash flow statement (balances by construction) ─
        "Operating CF (est)":           operating_cf,
        "Investing CF (est)":           investing_cf,
        "Financing CF (est)":           financing_cf,
        "Other / Non-cash Adj":         other_adj,
        "Net Change in Cash":           net_actual,
        # ── Verification ──────────────────────────────────
        "Cash Opening":                 cash_p,
        "Cash Closing":                 cash_c,
        "Explained %":                  explained_pct,
        "Quality":                      quality,
    }


# ─── Per-company processor ────────────────────────────────────────────────────

def process_company(ticker, filepath):
    xl     = pd.ExcelFile(filepath)
    sheets = xl.sheet_names

    bs_sheet = next((s for s in sheets if "balance" in s.lower()), None)
    is_sheet = next((s for s in sheets if "income"  in s.lower()), None)

    if not bs_sheet or not is_sheet:
        return ticker, None, f"Missing sheet — found: {sheets}"

    bs  = pd.read_excel(filepath, sheet_name=bs_sheet, index_col=0)
    inc = pd.read_excel(filepath, sheet_name=is_sheet, index_col=0)

    for df in (bs, inc):
        df.drop(columns=[c for c in df.columns if str(c).strip().lower() == "unit"],
                inplace=True, errors="ignore")
        df.index = df.index.astype(str).str.strip().str.lower()

    # Convert cumulative YTD Income Statement to per-quarter figures
    inc = to_quarterly(inc)

    bs_cols = list(bs.columns)
    if len(bs_cols) < 2:
        return ticker, None, "Fewer than 2 quarters"

    rows = []
    for i in range(len(bs_cols) - 1):
        row = derive_quarter(bs, inc, bs_cols[i], bs_cols[i + 1])
        row["Quarter"] = str(bs_cols[i])
        rows.append(row)

    df_cf = pd.DataFrame(rows).set_index("Quarter")

    try:
        with pd.ExcelWriter(filepath, engine="openpyxl", mode="a",
                            if_sheet_exists="replace") as writer:
            # Transpose so quarters are columns, metrics are rows
            df_cf.T.to_excel(writer, sheet_name="Simplified CF")
        hi  = (df_cf["Quality"] == "HIGH").sum()
        med = (df_cf["Quality"] == "MEDIUM").sum()
        low = (df_cf["Quality"] == "LOW").sum()
        print(f"  [OK] {ticker:<10} {len(rows)} quarters  "
              f"HIGH:{hi}  MED:{med}  LOW:{low}  (always balances)")
    except Exception as e:
        print(f"  [-] {ticker} — could not write: {e}")

    return ticker, df_cf, None


# ─── Main ─────────────────────────────────────────────────────────────────────

def main():
    print("=" * 60)
    print("  Simplified Derived Cash Flow  (Major Categories Only)")
    print("=" * 60)

    companies = sorted([
        d for d in os.listdir(COMPANY_DATA_DIR)
        if os.path.isdir(os.path.join(COMPANY_DATA_DIR, d))
    ])

    print(f"\n[*] Processing {len(companies)} companies...\n")

    audit_rows, errors = [], []

    for ticker in companies:
        filepath = os.path.join(COMPANY_DATA_DIR, ticker,
                                f"{ticker}_quarter_financials.xlsx")
        if not os.path.exists(filepath):
            continue

        ticker_out, df_cf, err = process_company(ticker, filepath)

        if err:
            print(f"  [-] {ticker:<10} {err}")
            errors.append({"Ticker": ticker, "Error": err})
            continue

        for quarter in list(df_cf.index)[:4]:
            r = df_cf.loc[quarter]
            audit_rows.append({
                "Ticker":             ticker,
                "Quarter":            quarter,
                "PAT (qtr)":          r["PAT (quarterly)"],
                "Depreciation":       r["Depreciation (quarterly)"],
                "Operating CF":       r["Operating CF (est)"],
                "Investing CF":       r["Investing CF (est)"],
                "Financing CF":       r["Financing CF (est)"],
                "Other/Non-cash Adj": r["Other / Non-cash Adj"],
                "Net Change in Cash": r["Net Change in Cash"],
                "Cash Closing":       r["Cash Closing"],
                "Explained %":        r["Explained %"],
                "Quality":            r["Quality"],
            })

    # ── Save audit workbook ───────────────────────────────────────────
    if audit_rows:
        df_a = pd.DataFrame(audit_rows)
        path = os.path.join(COMPANY_DATA_DIR, "cashflow_audit.xlsx")

        with pd.ExcelWriter(path, engine="openpyxl") as writer:
            df_a.to_excel(writer, sheet_name="All Results", index=False)

            summary = df_a.groupby("Quality").size().reset_index(name="Count")
            summary["% of Total"] = (summary["Count"] / len(df_a) * 100).round(1)
            summary.to_excel(writer, sheet_name="Summary", index=False)

            df_a[df_a["Quality"] == "LOW"].to_excel(
                writer, sheet_name="Low Confidence", index=False)

        print(f"\n[OK] Audit -> {path}")

        total = len(df_a)
        mc    = df_a["Quality"].value_counts()
        avg_expl = df_a["Explained %"].mean()
        print(f"\n{'='*60}")
        print(f"  CASH FLOW QUALITY  ({total} quarter-company checks)")
        print(f"  (every statement balances; quality = % cleanly attributed)")
        print(f"{'='*60}")
        for s in ["HIGH", "MEDIUM", "LOW", "NO DATA"]:
            n   = mc.get(s, 0)
            pct = n / total * 100 if total else 0
            bar = "#" * int(pct / 2)
            print(f"  {s:<8} {n:>4}  ({pct:5.1f}%)  {bar}")
        print(f"\n  Average explained: {avg_expl:.1f}%")

    if errors:
        print(f"\n  Skipped: {[e['Ticker'] for e in errors]}")

    print("\n" + "=" * 60)
    print("  Done. Original 'Cash Flow' sheets untouched.")
    print("=" * 60)


if __name__ == "__main__":
    main()
