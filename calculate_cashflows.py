"""
Simplified Derived Cash Flow Calculator
=========================================
Uses only major Balance Sheet category TOTALS — not individual line items —
so the calculation is fully standardized across all 158 companies regardless
of how each company labels its individual rows.

Method (Indirect):
  Operating CF  = PAT + Dep - Δ(Non-cash Current Assets) + Δ(Non-debt Current Liabilities)
  Investing CF  = -Δ(Total Non-Current Assets) - Dep - Δ(ST Investments)
  Financing CF  = Δ(Financial Debt) + Δ(Total NCL - Op NCL) + Δ(Paid-up Capital) - Dividends

  Non-cash CA   = Total CA  - Cash - ST Investments
  Non-debt CL   = Total CL  - ST Debt - Current portion LT Debt
  Financial Debt = ST Debt + Current portion LT Debt

  Verification: Net Derived should ≈ Cash Closing - Cash Opening (from BS)

Outputs:
  - "Simplified CF" sheet added to each company file (original CF sheet untouched)
  - company_data/cashflow_audit.xlsx — reconciliation summary
"""

import os
import numpy as np
import pandas as pd

COMPANY_DATA_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "company_data")
VARIANCE_OK      = 20
VARIANCE_WARN    = 50


# ─── Row finder (keyword substring matching) ──────────────────────────────────

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


def find_row(df, key):
    for group in KEYWORDS.get(key, []):
        for idx in df.index:
            if all(kw in str(idx).lower() for kw in group):
                return idx
    return None


def val(df, col, key):
    row = find_row(df, key)
    if row is None or col not in df.columns:
        return np.nan
    return pd.to_numeric(df.loc[row, col], errors="coerce")


def nanv(x):
    return 0.0 if np.isnan(x) else x


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

    # ── Verification ─────────────────────────────────────────────────
    net_derived = operating_cf + investing_cf + financing_cf
    net_actual  = (cash_c - cash_p) if not (np.isnan(cash_c) or np.isnan(cash_p)) else np.nan

    variance     = (net_derived - net_actual) if not np.isnan(net_actual) else np.nan
    variance_pct = (abs(variance / net_actual) * 100
                    if not (np.isnan(variance) or np.isnan(net_actual) or net_actual == 0)
                    else np.nan)

    match = ("OK"      if not np.isnan(variance_pct) and variance_pct < VARIANCE_OK  else
             "WARN"    if not np.isnan(variance_pct) and variance_pct < VARIANCE_WARN else
             "FAIL"    if not np.isnan(variance_pct)                                  else
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
        "PAT (from IS)":                pat,
        "Depreciation (from IS)":       dep,
        "Dividends Paid (est)":         dividends,
        # ── Derived cash flows ────────────────────────────
        "Operating CF":                 operating_cf,
        "Investing CF":                 investing_cf,
        "Financing CF":                 financing_cf,
        "Net Change (Derived)":         net_derived,
        # ── Verification ──────────────────────────────────
        "Cash Opening":                 cash_p,
        "Cash Closing":                 cash_c,
        "Net Change (Actual BS)":       net_actual,
        "Variance (PKR mn)":            variance,
        "Variance %":                   variance_pct,
        "Match":                        match,
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
        ok_count   = (df_cf["Match"] == "OK").sum()
        warn_count = (df_cf["Match"] == "WARN").sum()
        print(f"  [OK] {ticker:<10} {len(rows)} quarters  "
              f"OK:{ok_count}  WARN:{warn_count}  "
              f"FAIL:{(df_cf['Match']=='FAIL').sum()}")
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
                "PAT":                r["PAT (from IS)"],
                "Depreciation":       r["Depreciation (from IS)"],
                "Operating CF":       r["Operating CF"],
                "Investing CF":       r["Investing CF"],
                "Financing CF":       r["Financing CF"],
                "Net Change Derived": r["Net Change (Derived)"],
                "Net Change Actual":  r["Net Change (Actual BS)"],
                "Cash Closing":       r["Cash Closing"],
                "Variance (PKR mn)":  r["Variance (PKR mn)"],
                "Variance %":         r["Variance %"],
                "Match":              r["Match"],
            })

    # ── Save audit workbook ───────────────────────────────────────────
    if audit_rows:
        df_a = pd.DataFrame(audit_rows)
        path = os.path.join(COMPANY_DATA_DIR, "cashflow_audit.xlsx")

        with pd.ExcelWriter(path, engine="openpyxl") as writer:
            df_a.to_excel(writer, sheet_name="All Results", index=False)

            summary = df_a.groupby("Match").size().reset_index(name="Count")
            summary["% of Total"] = (summary["Count"] / len(df_a) * 100).round(1)
            summary.to_excel(writer, sheet_name="Summary", index=False)

            df_a[df_a["Match"] == "FAIL"].to_excel(
                writer, sheet_name="Failures", index=False)
            df_a[df_a["Match"] == "WARN"].to_excel(
                writer, sheet_name="Warnings", index=False)

        print(f"\n[OK] Audit -> {path}")

        total = len(df_a)
        mc    = df_a["Match"].value_counts()
        print(f"\n{'='*60}")
        print(f"  RECONCILIATION SUMMARY  ({total} quarter-company checks)")
        print(f"{'='*60}")
        for s in ["OK", "WARN", "FAIL", "NO DATA"]:
            n   = mc.get(s, 0)
            pct = n / total * 100 if total else 0
            bar = "█" * int(pct / 2)
            print(f"  {s:<8} {n:>4}  ({pct:5.1f}%)  {bar}")

    if errors:
        print(f"\n  Skipped: {[e['Ticker'] for e in errors]}")

    print("\n" + "=" * 60)
    print("  Done. Original 'Cash Flow' sheets untouched.")
    print("=" * 60)


if __name__ == "__main__":
    main()
