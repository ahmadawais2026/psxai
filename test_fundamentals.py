"""Unit tests for the fundamentals computation pipeline in data/market_data.py.

Covers two defects fixed after the SSGC fact-check:

  1. Debt-to-Equity must use INTEREST-BEARING DEBT (borrowings) only — never
     total liabilities (which for a circular-debt utility are dominated by
     gas-supplier payables and inflate D/E to absurd multiples). When borrowings
     can't be isolated, D/E is N/A.

  2. Trailing P/E / EPS / ROE must be on a FULL-YEAR basis (stitched TTM or the
     latest annual year) — never price ÷ a single positive interim quarter, which
     can present a loss-making stock as positively valued. Non-positive full-year
     EPS → P/E = N/A.

Runs standalone (`python test_fundamentals.py`) and is pytest-collectable.
Uses an in-memory fake Firestore so no network or credentials are needed.
"""

import sys

import data.market_data as md
import config


# ── Fake Firestore (in-memory) ───────────────────────────────────────────

class _FakeDoc:
    def __init__(self, data):
        self._data = data
        self.exists = data is not None

    def get(self):
        return self

    def to_dict(self):
        return self._data


class _FakeFinancialsCol:
    def __init__(self, annual, quarter):
        self._map = {"annual": _FakeDoc(annual), "quarter": _FakeDoc(quarter)}

    def document(self, name):
        return self._map[name]


class _FakeCompanyRef:
    def __init__(self, annual, quarter):
        self._col = _FakeFinancialsCol(annual, quarter)

    def collection(self, name):  # "financials"
        return self._col


class _FakeCompaniesCol:
    def __init__(self, annual, quarter):
        self._ref = _FakeCompanyRef(annual, quarter)

    def document(self, name):  # local symbol
        return self._ref


class FakeDB:
    """Minimal stand-in for the Firestore client used by get_fundamentals."""

    def __init__(self, annual, quarter):
        self._companies = _FakeCompaniesCol(annual, quarter)

    def collection(self, name):  # "companies"
        return self._companies


# ── Test harness ───────────────────────────────────────────────────────────

class _Patches:
    """Patch the module seams get_fundamentals reaches out through, so the
    Firestore branch runs against in-memory data with a fixed price and no
    network calls. Restores everything on exit."""

    def __init__(self, annual, quarter, price):
        self._annual = annual
        self._quarter = quarter
        self._price = price
        self._saved = {}

    def __enter__(self):
        self._saved["firebase_db"] = getattr(config, "firebase_db", None)
        self._saved["get_cached"] = md.get_cached
        self._saved["set_cached"] = md.set_cached
        self._saved["get_quote"] = md.get_quote
        self._saved["_get_askanalyst_id"] = md._get_askanalyst_id

        config.firebase_db = FakeDB(self._annual, self._quarter)
        md.get_cached = lambda *a, **k: None       # force recompute
        md.set_cached = lambda *a, **k: None        # no-op
        md.get_quote = lambda symbol: {"price": self._price}
        md._get_askanalyst_id = lambda symbol: None  # skip live shares fetch
        return self

    def __exit__(self, *exc):
        config.firebase_db = self._saved["firebase_db"]
        md.get_cached = self._saved["get_cached"]
        md.set_cached = self._saved["set_cached"]
        md.get_quote = self._saved["get_quote"]
        md._get_askanalyst_id = self._saved["_get_askanalyst_id"]
        return False


def _is_rows(*triples):
    """Build income-statement rows: (metric, col, value)."""
    return [{"Metric": m, col: v} for (m, col, v) in triples]


def _bs_rows(col, items):
    """Build balance-sheet rows from {metric: value} under one period column."""
    return [{"Metric": m, col: v} for m, v in items.items()]


def _approx(a, b, tol=1e-6):
    return a is not None and b is not None and abs(float(a) - float(b)) <= tol


# ── Scenarios ────────────────────────────────────────────────────────────────

def test_pe_uses_annual_not_single_quarter():
    """Single positive interim quarter + positive full-year annual ⇒ P/E is
    computed off the ANNUAL EPS, not the (larger-multiple) quarter EPS."""
    annual = {
        "income_statement": _is_rows(
            ("Total Revenue", "2025", 1000.0),
            ("Profit after Tax", "2025", 500.0),
            ("EPS - Basic", "2025", 5.0),     # full-year EPS
        ),
    }
    quarter = {
        "income_statement": _is_rows(
            ("Total Revenue", "Dec-25", 300.0),
            ("Profit after Tax", "Dec-25", 100.0),
            ("EPS - Basic", "Dec-25", 1.0),   # single good quarter
        ),
        "balance_sheet": _bs_rows("Dec-25", {
            "Total Assets": 1000.0,
            "Total Liabilities": 800.0,
        }),
    }
    with _Patches(annual, quarter, price=50.0):
        f = md.get_fundamentals("TEST")

    # P/E must be 50 / 5.0 = 10.0 (annual basis), NOT 50 / 1.0 = 50.0 (quarter).
    assert _approx(f["pe_ratio"], 10.0), f"pe_ratio={f['pe_ratio']} (expected 10.0 off annual EPS)"
    assert _approx(f["eps"], 5.0), f"eps={f['eps']} (expected full-year 5.0)"
    assert f["eps_period"] == "2025", f"eps_period={f['eps_period']} (expected '2025')"
    print("  PASS: P/E computed off full-year annual EPS, not the single quarter")


def test_pe_none_for_negative_annual_eps():
    """The SSGC case: one positive interim quarter but the company is loss-making
    over the full year ⇒ P/E must be N/A (not a positive multiple)."""
    annual = {
        "income_statement": _is_rows(
            ("Total Revenue", "2025", 1000.0),
            ("Profit after Tax", "2025", -300.0),
            ("EPS - Basic", "2025", -3.0),    # loss over the year
        ),
    }
    quarter = {
        "income_statement": _is_rows(
            ("Profit after Tax", "Dec-25", 100.0),
            ("EPS - Basic", "Dec-25", 1.0),   # one good quarter
        ),
        "balance_sheet": _bs_rows("Dec-25", {
            "Total Assets": 1000.0,
            "Total Liabilities": 800.0,
        }),
    }
    with _Patches(annual, quarter, price=50.0):
        f = md.get_fundamentals("TEST")

    assert f["pe_ratio"] is None, f"pe_ratio={f['pe_ratio']} (expected None for negative annual EPS)"
    assert _approx(f["eps"], -3.0), f"eps={f['eps']} (expected -3.0)"
    # ROE off full-year loss must be negative (honest), not the positive quarter.
    assert f["roe"] is not None and f["roe"] < 0, f"roe={f['roe']} (expected negative)"
    print("  PASS: P/E is N/A and ROE negative for a full-year loss-making stock")


def test_de_uses_interest_bearing_debt():
    """D/E uses borrowings summed across line items, NOT total liabilities."""
    annual = {
        "income_statement": _is_rows(
            ("Profit after Tax", "2025", 200.0),
            ("EPS - Basic", "2025", 2.0),
        ),
    }
    quarter = {
        "income_statement": _is_rows(
            ("EPS - Basic", "Dec-25", 0.5),
        ),
        "balance_sheet": _bs_rows("Dec-25", {
            "Total Assets": 1000.0,
            "Total Liabilities": 800.0,                       # mostly payables
            "Long term financing": 100.0,                     # borrowing
            "Lease liabilities against right-of-use assets": 40.0,  # borrowing
            "Short term borrowings": 30.0,                    # borrowing
            "Trade and other payables": 600.0,                # NOT debt
        }),
    }
    with _Patches(annual, quarter, price=20.0):
        f = md.get_fundamentals("TEST")

    # equity = 1000 - 800 = 200; total_debt = 100 + 40 + 30 = 170; D/E = 0.85.
    assert _approx(f["debt_to_equity"], 0.85), \
        f"debt_to_equity={f['debt_to_equity']} (expected 0.85 from borrowings)"
    # Guard against the old defect: must NOT equal total_liabilities/equity = 4.0.
    assert not _approx(f["debt_to_equity"], 4.0), "D/E regressed to total_liabilities/equity"
    print("  PASS: D/E summed from interest-bearing borrowings (0.85x), not 4.0x liabilities")


def test_de_none_when_no_borrowings():
    """No borrowing line items ⇒ D/E is N/A, never total_liabilities/equity."""
    annual = {
        "income_statement": _is_rows(
            ("Profit after Tax", "2025", 200.0),
            ("EPS - Basic", "2025", 2.0),
        ),
    }
    quarter = {
        "income_statement": _is_rows(
            ("EPS - Basic", "Dec-25", 0.5),
        ),
        "balance_sheet": _bs_rows("Dec-25", {
            "Total Assets": 1000.0,
            "Total Liabilities": 800.0,
            "Trade and other payables": 800.0,  # only payables, no borrowings
        }),
    }
    with _Patches(annual, quarter, price=20.0):
        f = md.get_fundamentals("TEST")

    assert f["debt_to_equity"] is None, \
        f"debt_to_equity={f['debt_to_equity']} (expected None when no borrowings)"
    print("  PASS: D/E is N/A when interest-bearing debt can't be isolated")


def test_compute_ttm_highlights_surfaces_annual_basis():
    """_compute_ttm_highlights surfaces full-year figures and a non-TTM flag when
    only a single interim quarter is available (no prior-year YTD to stitch)."""
    annual = {
        "income_statement": _is_rows(
            ("Profit after Tax", "2025", -300.0),
            ("EPS - Basic", "2025", -3.0),
        ),
    }
    quarter = {
        "income_statement": _is_rows(
            ("EPS - Basic", "Dec-25", 1.0),
        ),
        "balance_sheet": _bs_rows("Dec-25", {
            "Total Assets": 1000.0,
            "Total Liabilities": 800.0,
            "Long term financing": 170.0,
        }),
    }
    h = md._compute_ttm_highlights(annual, quarter, "TEST")

    assert h["period_label"] == "Dec-25", f"period_label={h['period_label']}"
    assert h["earnings_is_ttm"] is False, "earnings_is_ttm should be False on single-interim fallback"
    assert _approx(h["annual_eps"], -3.0), f"annual_eps={h['annual_eps']}"
    assert h["annual_period_label"] == "2025", f"annual_period_label={h['annual_period_label']}"
    assert _approx(h["total_debt"], 170.0), f"total_debt={h['total_debt']}"
    print("  PASS: highlights surface annual EPS/period, total_debt, and earnings_is_ttm=False")


# ── Runner ───────────────────────────────────────────────────────────────────

def main():
    print("==================================================")
    print(" TESTING FUNDAMENTALS PIPELINE (P/E, ROE, D/E)    ")
    print("==================================================")

    tests = [
        test_pe_uses_annual_not_single_quarter,
        test_pe_none_for_negative_annual_eps,
        test_de_uses_interest_bearing_debt,
        test_de_none_when_no_borrowings,
        test_compute_ttm_highlights_surfaces_annual_basis,
    ]

    failures = 0
    for t in tests:
        print(f"\n- {t.__name__}")
        try:
            t()
        except AssertionError as e:
            failures += 1
            print(f"  FAIL: {e}")
        except Exception as e:
            failures += 1
            print(f"  ERROR: {type(e).__name__}: {e}")

    print("\n==================================================")
    if failures:
        print(f" {failures} test(s) FAILED")
    else:
        print(f" All {len(tests)} tests PASSED")
    print("==================================================")
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
