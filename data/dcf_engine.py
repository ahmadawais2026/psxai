import numpy as np

class DCFEngine:
    """
    Robust 2-Stage Free Cash Flow to Equity (FCFE) Discounted Cash Flow Engine.
    Implements surgical guardrails to prevent mathematical explosions.
    """
    
    def __init__(self, risk_free_rate=0.15, market_risk_premium=0.08, terminal_growth_cap=0.045):
        self.risk_free_rate = risk_free_rate
        self.market_risk_premium = market_risk_premium
        self.terminal_growth_cap = terminal_growth_cap
        
    def calculate_cost_of_equity(self, levered_beta):
        """
        Calculate Cost of Equity using CAPM.
        Guardrail: Bound levered beta between 0.8 and 2.0 to prevent absurd discount rates.
        """
        bounded_beta = max(0.8, min(2.0, levered_beta))
        ke = self.risk_free_rate + (bounded_beta * self.market_risk_premium)
        return ke

    def project_cash_flows(self, base_fcf, growth_rate, years=5):
        """Project FCFs for the first stage."""
        cash_flows = []
        current_fcf = base_fcf
        for _ in range(years):
            current_fcf *= (1 + growth_rate)
            cash_flows.append(current_fcf)
        return cash_flows

    def calculate_terminal_value(self, final_year_fcf, cost_of_equity, terminal_growth):
        """
        Calculate Terminal Value using the Gordon Growth Model.
        Guardrail: WACC/Ke must be strictly greater than Terminal Growth Rate + 1%.
        """
        bounded_tg = min(terminal_growth, self.terminal_growth_cap)
        # Ensure Ke > g to avoid divide-by-zero or negative denominator
        effective_ke = max(cost_of_equity, bounded_tg + 0.01)
        
        terminal_value = (final_year_fcf * (1 + bounded_tg)) / (effective_ke - bounded_tg)
        return terminal_value

    def calculate_intrinsic_value(self, base_fcf, levered_beta, short_term_growth, terminal_growth, shares_outstanding, years=5):
        """
        Calculate the intrinsic value per share using 2-stage DCF.
        """
        if base_fcf <= 0:
            return None # Cannot run standard DCF on negative FCF
            
        ke = self.calculate_cost_of_equity(levered_beta)
        
        # Stage 1: Explicit projection
        projected_fcfs = self.project_cash_flows(base_fcf, short_term_growth, years)
        
        # Discount Stage 1
        pv_fcfs = sum([fcf / ((1 + ke) ** i) for i, fcf in enumerate(projected_fcfs, 1)])
        
        # Stage 2: Terminal Value
        tv = self.calculate_terminal_value(projected_fcfs[-1], ke, terminal_growth)
        pv_tv = tv / ((1 + ke) ** years)
        
        total_equity_value = pv_fcfs + pv_tv
        intrinsic_value_per_share = total_equity_value / shares_outstanding
        
        return intrinsic_value_per_share

    def generate_scenarios(
        self,
        base_fcf,
        levered_beta,
        shares_outstanding,
        historical_growth,
        current_price=None,
        book_value_per_share=None,
        high_52w=None,
    ):
        """
        Generates Base, Bull, and Bear scenarios.

        Optional sanity-check arguments (current_price, book_value_per_share, high_52w)
        are used to flag implausibly high DCF values without clamping them.
        When any scenario value exceeds max(3x price, 3x book, 1.5x 52-wk high),
        the scenario dict gains a 'sanity_flag': True key so downstream consumers
        can discard the DCF and fall back to relative valuation.
        """
        if base_fcf <= 0 or shares_outstanding <= 0:
            return {"error": "Negative FCF or invalid shares. Fallback to Relative Valuation required."}

        # Pre-compute sanity ceiling: the highest value we'd consider credible.
        # We take the maximum across all available reference points so that any
        # single generous anchor is enough to avoid a false flag.
        # Only apply the ceiling when at least one reference price is provided.
        _ref_ceilings = []
        if current_price and current_price > 0:
            _ref_ceilings.append(3.0 * current_price)
        if book_value_per_share and book_value_per_share > 0:
            _ref_ceilings.append(3.0 * book_value_per_share)
        if high_52w and high_52w > 0:
            _ref_ceilings.append(1.5 * high_52w)
        _sanity_ceiling = max(_ref_ceilings) if _ref_ceilings else None

        ke = self.calculate_cost_of_equity(levered_beta)

        scenarios = {
            "base": {
                "short_term_growth": historical_growth,
                "terminal_growth": min(0.04, self.terminal_growth_cap),
            },
            "bull": {
                "short_term_growth": historical_growth + 0.05, # +5% growth
                "terminal_growth": min(0.045, self.terminal_growth_cap),
            },
            "bear": {
                "short_term_growth": max(0.0, historical_growth - 0.05), # -5% growth
                "terminal_growth": 0.02, # 2% long term growth
            }
        }

        results = {}
        for scenario, assumptions in scenarios.items():
            val = self.calculate_intrinsic_value(
                base_fcf=base_fcf,
                levered_beta=levered_beta,
                short_term_growth=assumptions["short_term_growth"],
                terminal_growth=assumptions["terminal_growth"],
                shares_outstanding=shares_outstanding
            )
            scenario_result = {
                "value": round(val, 2) if val else None,
                "assumptions": assumptions,
                "cost_of_equity": round(ke, 4)
            }
            # Sanity check: flag values that exceed the credibility ceiling.
            # We flag rather than clamp so the analyst can still see the raw
            # model output while being explicitly warned it is non-credible.
            if val is not None and _sanity_ceiling is not None and val > _sanity_ceiling:
                scenario_result["sanity_flag"] = True
                scenario_result["sanity_ceiling"] = round(_sanity_ceiling, 2)
                scenario_result["sanity_note"] = (
                    f"DCF value {val:.2f} exceeds credibility ceiling "
                    f"{_sanity_ceiling:.2f} (3x price/book or 1.5x 52-wk high). "
                    "Treat as non-credible; use relative valuation instead."
                )
            results[scenario] = scenario_result

        # Build 3x3 Sensitivity Matrix around the Base Case WACC(Ke) and Terminal Growth
        base_tg = scenarios["base"]["terminal_growth"]
        sensitivity_matrix = self._build_sensitivity_matrix(
            base_fcf, historical_growth, shares_outstanding, ke, base_tg
        )

        results["sensitivity_matrix"] = sensitivity_matrix
        return results
        
    def _build_sensitivity_matrix(self, base_fcf, st_growth, shares, base_ke, base_tg):
        """
        Builds a 3x3 matrix varying Cost of Equity (+/- 1%) and Terminal Growth (+/- 0.5%)
        """
        ke_shifts = [-0.01, 0.0, 0.01]
        tg_shifts = [-0.005, 0.0, 0.005]
        
        matrix = []
        for ke_shift in ke_shifts:
            row = []
            for tg_shift in tg_shifts:
                test_ke = max(base_ke + ke_shift, 0.01)
                test_tg = min(base_tg + tg_shift, self.terminal_growth_cap)
                
                # Avoid divide by zero
                effective_ke = max(test_ke, test_tg + 0.01)
                
                # Manual calculation for specific Ke
                projected_fcfs = self.project_cash_flows(base_fcf, st_growth, 5)
                pv_fcfs = sum([fcf / ((1 + effective_ke) ** i) for i, fcf in enumerate(projected_fcfs, 1)])
                tv = (projected_fcfs[-1] * (1 + test_tg)) / (effective_ke - test_tg)
                pv_tv = tv / ((1 + effective_ke) ** 5)
                
                val = (pv_fcfs + pv_tv) / shares
                
                row.append({
                    "ke": round(effective_ke, 4),
                    "tg": round(test_tg, 4),
                    "value": round(val, 2)
                })
            matrix.append(row)
            
        return matrix
