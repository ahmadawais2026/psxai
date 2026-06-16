"""
data/technical_indicators.py
═══════════════════════════════════════════════════════════════════════
Pure computational technical indicators for PSX stocks.

**Design principle (FinAgent paper):**  Keep all arithmetic and
indicator logic in deterministic code — never involve an LLM for
number crunching.  The LLM layer consumes the *results* of these
functions for narrative reasoning.

All indicators are computed using the ``ta`` (Technical Analysis)
library, which wraps well-tested Pandas / NumPy implementations of
standard market indicators.

Usage::

    from data.market_data import get_history
    from data.technical_indicators import compute_all_indicators, get_trend_summary

    df = get_history("OGDC")
    indicators = compute_all_indicators(df)
    summary    = get_trend_summary(indicators)
═══════════════════════════════════════════════════════════════════════
"""

from __future__ import annotations

import logging
from typing import Any, Dict, List, Optional, Tuple

import numpy as np
import pandas as pd
import ta

logger = logging.getLogger(__name__)


# ── Main Entry Point ─────────────────────────────────────────────────


def compute_all_indicators(df: pd.DataFrame) -> Dict[str, Any]:
    """
    Compute a comprehensive set of technical indicators on an OHLCV
    DataFrame.

    Args:
        df: pandas DataFrame with at least ``Close``, ``High``,
            ``Low``, ``Volume`` columns, indexed by datetime.
            Typically the output of :func:`data.market_data.get_history`.

    Returns:
        Dictionary keyed by indicator group::

            {
                "rsi": { "value": 55.3, "interpretation": "Neutral" },
                "macd": { "macd": ..., "signal": ..., "histogram": ..., "interpretation": "..." },
                "bollinger": { ... },
                "moving_averages": { ... },
                "volume": { ... },
                "atr": { ... },
                "price_info": { ... },
            }

        Returns dict with ``error`` key if the DataFrame is too small.
    """
    if df is None or df.empty or len(df) < 30:
        return {
            "error": "Insufficient data — need at least 30 bars for indicator computation.",
            "bars_available": 0 if df is None or df.empty else len(df),
        }

    try:
        result: Dict[str, Any] = {}

        close = df["Close"]
        high = df["High"]
        low = df["Low"]
        volume = df["Volume"]

        # ── Current price info ───────────────────────────────────────
        result["price_info"] = _compute_price_info(close, high, low)

        # ── RSI ──────────────────────────────────────────────────────
        result["rsi"] = _compute_rsi(close)

        # ── MACD ─────────────────────────────────────────────────────
        result["macd"] = _compute_macd(close)

        # ── Bollinger Bands ──────────────────────────────────────────
        result["bollinger"] = _compute_bollinger(close)

        # ── Moving Averages ──────────────────────────────────────────
        result["moving_averages"] = _compute_moving_averages(close)

        # ── Volume Analysis ──────────────────────────────────────────
        result["volume"] = _compute_volume(volume)

        # ── ATR ──────────────────────────────────────────────────────
        result["atr"] = _compute_atr(high, low, close)

        # ── SOTA Indicators for KSE-100 ─────────────────────────────
        result["kama"] = _compute_kama(close)
        result["cmf"] = _compute_cmf(high, low, close, volume)
        result["adx"] = _compute_adx(high, low, close)
        result["ichimoku"] = _compute_ichimoku(high, low)
        result["stochastic"] = _compute_stochastic(high, low, close)
        result["williams_r"] = _compute_williams_r(high, low, close)
        result["disparity_index"] = _compute_disparity_index(close)
        result["vwma"] = _compute_vwma(close, volume)

        return result

    except Exception as exc:
        logger.error("Error computing indicators: %s", exc, exc_info=True)
        return {"error": str(exc)}


# ── Individual Indicator Computations ────────────────────────────────


def _compute_price_info(
    close: pd.Series, high: pd.Series, low: pd.Series
) -> Dict[str, Any]:
    """Basic price context."""
    current = float(close.iloc[-1])
    prev = float(close.iloc[-2]) if len(close) > 1 else current
    return {
        "current_price": round(current, 2),
        "previous_close": round(prev, 2),
        "change": round(current - prev, 2),
        "change_percent": round(((current - prev) / prev) * 100, 2) if prev != 0 else 0,
        "period_high": round(float(high.max()), 2),
        "period_low": round(float(low.min()), 2),
    }


def _compute_rsi(close: pd.Series, window: int = 14) -> Dict[str, Any]:
    """RSI (14-period) with overbought/oversold interpretation."""
    rsi_series = ta.momentum.RSIIndicator(close=close, window=window).rsi()
    rsi_val = float(rsi_series.iloc[-1]) if not rsi_series.empty and pd.notna(rsi_series.iloc[-1]) else None

    if rsi_val is None:
        return {"value": None, "interpretation": "Insufficient data"}

    rsi_val = round(rsi_val, 2)

    if rsi_val >= 70:
        interp = "Overbought — stock may be overvalued; potential pullback"
    elif rsi_val >= 60:
        interp = "Mildly overbought — bullish momentum but watch for reversal"
    elif rsi_val <= 30:
        interp = "Oversold — stock may be undervalued; potential bounce"
    elif rsi_val <= 40:
        interp = "Mildly oversold — bearish pressure but watch for reversal"
    else:
        interp = "Neutral — no strong momentum signal"

    return {"value": rsi_val, "interpretation": interp}


def _compute_macd(close: pd.Series) -> Dict[str, Any]:
    """MACD (12, 26, 9) with signal-line crossover interpretation."""
    macd_ind = ta.trend.MACD(close=close, window_slow=26, window_fast=12, window_sign=9)

    macd_val = _latest(macd_ind.macd())
    signal_val = _latest(macd_ind.macd_signal())
    hist_val = _latest(macd_ind.macd_diff())

    if macd_val is None or signal_val is None:
        return {
            "macd": None,
            "signal": None,
            "histogram": None,
            "interpretation": "Insufficient data",
        }

    # Crossover detection (compare last 2 bars)
    macd_series = macd_ind.macd()
    signal_series = macd_ind.macd_signal()
    interp = _macd_crossover_interpretation(macd_series, signal_series, macd_val, signal_val, hist_val)

    return {
        "macd": round(macd_val, 4),
        "signal": round(signal_val, 4),
        "histogram": round(hist_val, 4) if hist_val is not None else None,
        "interpretation": interp,
    }


def _macd_crossover_interpretation(
    macd_s: pd.Series,
    signal_s: pd.Series,
    macd_val: float,
    signal_val: float,
    hist_val: Optional[float],
) -> str:
    """Determine MACD crossover state from the last 2 bars."""
    if len(macd_s) < 2 or len(signal_s) < 2:
        return "Insufficient history for crossover detection"

    prev_macd = _safe_float(macd_s.iloc[-2])
    prev_signal = _safe_float(signal_s.iloc[-2])

    if prev_macd is not None and prev_signal is not None:
        # Bullish crossover: MACD crosses above signal
        if prev_macd <= prev_signal and macd_val > signal_val:
            return "Bullish crossover — MACD just crossed above signal line (buy signal)"
        # Bearish crossover: MACD crosses below signal
        if prev_macd >= prev_signal and macd_val < signal_val:
            return "Bearish crossover — MACD just crossed below signal line (sell signal)"

    if macd_val > signal_val:
        if macd_val > 0:
            return "Bullish — MACD above signal in positive territory"
        return "Improving — MACD above signal but still negative"
    else:
        if macd_val < 0:
            return "Bearish — MACD below signal in negative territory"
        return "Weakening — MACD below signal but still positive"


def _compute_bollinger(close: pd.Series, window: int = 20, std: int = 2) -> Dict[str, Any]:
    """Bollinger Bands (20, 2) with position interpretation."""
    bb = ta.volatility.BollingerBands(close=close, window=window, window_dev=std)

    upper = _latest(bb.bollinger_hband())
    middle = _latest(bb.bollinger_mavg())
    lower = _latest(bb.bollinger_lband())
    width = _latest(bb.bollinger_wband())
    pband = _latest(bb.bollinger_pband())  # %B indicator

    current = float(close.iloc[-1])

    if upper is None or lower is None:
        return {
            "upper": None, "middle": None, "lower": None,
            "width": None, "percent_b": None,
            "interpretation": "Insufficient data",
        }

    # Position interpretation
    if current > upper:
        interp = "Price ABOVE upper band — potentially overbought; may revert to mean"
    elif current < lower:
        interp = "Price BELOW lower band — potentially oversold; may bounce"
    elif current > middle:
        interp = "Price above middle band — in upper half of Bollinger range (mild bullish)"
    else:
        interp = "Price below middle band — in lower half of Bollinger range (mild bearish)"

    # Squeeze detection
    if width is not None and width < 0.05:
        interp += " | SQUEEZE detected — low volatility, potential breakout ahead"

    return {
        "upper": round(upper, 2),
        "middle": round(middle, 2),
        "lower": round(lower, 2),
        "width": round(width, 4) if width else None,
        "percent_b": round(pband, 4) if pband is not None else None,
        "current_price": round(current, 2),
        "interpretation": interp,
    }


def _compute_moving_averages(close: pd.Series) -> Dict[str, Any]:
    """SMA-20/50/200, EMA-12/26 with golden/death cross detection."""
    n = len(close)
    current = float(close.iloc[-1])

    sma20 = _latest(ta.trend.SMAIndicator(close=close, window=20).sma_indicator()) if n >= 20 else None
    sma50 = _latest(ta.trend.SMAIndicator(close=close, window=50).sma_indicator()) if n >= 50 else None
    sma200 = _latest(ta.trend.SMAIndicator(close=close, window=200).sma_indicator()) if n >= 200 else None
    ema12 = _latest(ta.trend.EMAIndicator(close=close, window=12).ema_indicator()) if n >= 12 else None
    ema26 = _latest(ta.trend.EMAIndicator(close=close, window=26).ema_indicator()) if n >= 26 else None

    # ── Cross detection ──────────────────────────────────────────
    cross_signals: List[str] = []

    if sma50 is not None and sma200 is not None and n >= 200:
        sma50_series = ta.trend.SMAIndicator(close=close, window=50).sma_indicator()
        sma200_series = ta.trend.SMAIndicator(close=close, window=200).sma_indicator()

        if len(sma50_series.dropna()) >= 2 and len(sma200_series.dropna()) >= 2:
            prev_50 = _safe_float(sma50_series.dropna().iloc[-2])
            prev_200 = _safe_float(sma200_series.dropna().iloc[-2])

            if prev_50 is not None and prev_200 is not None:
                if prev_50 <= prev_200 and sma50 > sma200:
                    cross_signals.append("GOLDEN CROSS — SMA-50 crossed above SMA-200 (strong bullish)")
                elif prev_50 >= prev_200 and sma50 < sma200:
                    cross_signals.append("DEATH CROSS — SMA-50 crossed below SMA-200 (strong bearish)")

    # ── Trend interpretation ─────────────────────────────────────
    trend_layers: List[str] = []
    if sma20 is not None:
        trend_layers.append("above SMA-20" if current > sma20 else "below SMA-20")
    if sma50 is not None:
        trend_layers.append("above SMA-50" if current > sma50 else "below SMA-50")
    if sma200 is not None:
        trend_layers.append("above SMA-200" if current > sma200 else "below SMA-200")

    return {
        "sma_20":  round(sma20, 2) if sma20 else None,
        "sma_50":  round(sma50, 2) if sma50 else None,
        "sma_200": round(sma200, 2) if sma200 else None,
        "ema_12":  round(ema12, 2) if ema12 else None,
        "ema_26":  round(ema26, 2) if ema26 else None,
        "current_price": round(current, 2),
        "price_vs_ma": trend_layers,
        "cross_signals": cross_signals,
    }


def _compute_volume(volume: pd.Series) -> Dict[str, Any]:
    """Volume analysis: average, current-to-average ratio."""
    if volume.empty:
        return {"avg_volume": None, "current_volume": None, "ratio": None, "interpretation": "No volume data"}

    avg_20 = float(volume.tail(20).mean())
    current_vol = float(volume.iloc[-1])
    ratio = round(current_vol / avg_20, 2) if avg_20 > 0 else 0

    if ratio >= 2.0:
        interp = "Very HIGH volume — significant activity; confirms trend move"
    elif ratio >= 1.5:
        interp = "Above-average volume — increased interest"
    elif ratio >= 0.7:
        interp = "Normal volume — typical trading activity"
    else:
        interp = "LOW volume — weak conviction; trend may lack follow-through"

    return {
        "avg_volume_20d": int(avg_20),
        "current_volume": int(current_vol),
        "volume_ratio": ratio,
        "interpretation": interp,
    }


def _compute_atr(
    high: pd.Series, low: pd.Series, close: pd.Series, window: int = 14
) -> Dict[str, Any]:
    """ATR (14-period) for volatility measurement."""
    atr_series = ta.volatility.AverageTrueRange(
        high=high, low=low, close=close, window=window
    ).average_true_range()

    atr_val = _latest(atr_series)
    current = float(close.iloc[-1])

    if atr_val is None or current == 0:
        return {"value": None, "percent": None, "interpretation": "Insufficient data"}

    atr_pct = round((atr_val / current) * 100, 2)

    if atr_pct >= 4:
        interp = "HIGH volatility — wide price swings; larger stop-losses needed"
    elif atr_pct >= 2:
        interp = "Moderate volatility — normal trading range"
    else:
        interp = "LOW volatility — tight price action; potential for breakout"

    return {
        "value": round(atr_val, 2),
        "percent_of_price": atr_pct,
        "interpretation": interp,
    }


# ── Support & Resistance ────────────────────────────────────────────


def detect_support_resistance(
    df: pd.DataFrame, lookback: int = 60, tolerance_pct: float = 1.0
) -> Dict[str, Any]:
    """
    Detect key support and resistance levels from price history.

    Uses a simple swing-high / swing-low approach with clustering to
    merge nearby levels.

    Args:
        df:            OHLCV DataFrame.
        lookback:      Number of recent bars to analyse.
        tolerance_pct: Percentage within which two levels are merged.

    Returns:
        Dict with ``support_levels``, ``resistance_levels``,
        ``current_price``, and ``nearest_support`` / ``nearest_resistance``.
    """
    if df is None or df.empty or len(df) < 10:
        return {"error": "Insufficient data for support/resistance detection"}

    subset = df.tail(min(lookback, len(df)))
    close = subset["Close"]
    high = subset["High"]
    low = subset["Low"]
    current = float(close.iloc[-1])

    # ── Find swing highs (resistance) and swing lows (support) ───
    swing_window = 5
    resistance_raw: List[float] = []
    support_raw: List[float] = []

    highs = high.values
    lows = low.values

    for i in range(swing_window, len(highs) - swing_window):
        # Swing high: higher than neighbours on both sides
        if highs[i] == max(highs[i - swing_window: i + swing_window + 1]):
            resistance_raw.append(float(highs[i]))
        # Swing low: lower than neighbours on both sides
        if lows[i] == min(lows[i - swing_window: i + swing_window + 1]):
            support_raw.append(float(lows[i]))

    # ── Cluster nearby levels ────────────────────────────────────
    support_levels = _cluster_levels(support_raw, tolerance_pct)
    resistance_levels = _cluster_levels(resistance_raw, tolerance_pct)

    # Filter: supports below current, resistances above current
    supports = sorted([s for s in support_levels if s < current], reverse=True)
    resistances = sorted([r for r in resistance_levels if r > current])

    return {
        "current_price": round(current, 2),
        "support_levels": [round(s, 2) for s in supports[:5]],
        "resistance_levels": [round(r, 2) for r in resistances[:5]],
        "nearest_support": round(supports[0], 2) if supports else None,
        "nearest_resistance": round(resistances[0], 2) if resistances else None,
    }


def _cluster_levels(levels: List[float], tolerance_pct: float) -> List[float]:
    """Merge nearby price levels within tolerance_pct of each other."""
    if not levels:
        return []

    levels_sorted = sorted(levels)
    clusters: List[List[float]] = [[levels_sorted[0]]]

    for lvl in levels_sorted[1:]:
        cluster_avg = np.mean(clusters[-1])
        if abs(lvl - cluster_avg) / cluster_avg * 100 <= tolerance_pct:
            clusters[-1].append(lvl)
        else:
            clusters.append([lvl])

    # Use median of each cluster as the representative level
    return [float(np.median(c)) for c in clusters]


# ── Trend Summary ────────────────────────────────────────────────────


def get_trend_summary(indicators: Dict[str, Any]) -> str:
    """
    Generate a concise text summary of the overall technical trend.

    This produces a *deterministic* narrative (no LLM) that the agent
    layer can feed into prompts.

    Args:
        indicators: Output of :func:`compute_all_indicators`.

    Returns:
        Multi-line string summarising the trend.
    """
    if "error" in indicators:
        return f"Unable to compute trend summary: {indicators['error']}"

    lines: List[str] = []

    # ── Price ────────────────────────────────────────────────────
    pi = indicators.get("price_info", {})
    change_pct = pi.get("change_percent", 0)
    lines.append(
        f"Price: {pi.get('current_price', 'N/A')} PKR "
        f"({'+' if change_pct >= 0 else ''}{change_pct}% from previous close)"
    )

    # ── RSI ──────────────────────────────────────────────────────
    rsi = indicators.get("rsi", {})
    if rsi.get("value") is not None:
        lines.append(f"RSI(14): {rsi['value']} — {rsi['interpretation']}")

    # ── MACD ─────────────────────────────────────────────────────
    macd = indicators.get("macd", {})
    if macd.get("macd") is not None:
        lines.append(f"MACD: {macd['macd']} | Signal: {macd['signal']} — {macd['interpretation']}")

    # ── Bollinger ────────────────────────────────────────────────
    bb = indicators.get("bollinger", {})
    if bb.get("interpretation"):
        lines.append(f"Bollinger Bands: {bb['interpretation']}")

    # ── Moving Averages ──────────────────────────────────────────
    ma = indicators.get("moving_averages", {})
    if ma.get("price_vs_ma"):
        lines.append(f"Moving Averages: Price is {', '.join(ma['price_vs_ma'])}")
    if ma.get("cross_signals"):
        for sig in ma["cross_signals"]:
            lines.append(f"⚠ {sig}")

    # ── Volume ───────────────────────────────────────────────────
    vol = indicators.get("volume", {})
    if vol.get("volume_ratio") is not None:
        lines.append(
            f"Volume: {vol['current_volume']:,} "
            f"({vol['volume_ratio']}x avg) — {vol['interpretation']}"
        )

    # ── ATR ──────────────────────────────────────────────────────
    atr = indicators.get("atr", {})
    if atr.get("value") is not None:
        lines.append(
            f"ATR(14): {atr['value']} ({atr['percent_of_price']}% of price) — {atr['interpretation']}"
        )

    # ── Overall Bias ─────────────────────────────────────────────
    bias = _compute_overall_bias(indicators)
    lines.append(f"\nOverall Technical Bias: {bias}")

    return "\n".join(lines)


def _compute_overall_bias(indicators: Dict[str, Any]) -> str:
    """Score indicators to produce Bullish / Bearish / Neutral."""
    score = 0  # positive = bullish, negative = bearish

    rsi_val = indicators.get("rsi", {}).get("value")
    if rsi_val is not None:
        if rsi_val > 60:
            score += 1
        elif rsi_val < 40:
            score -= 1

    macd_interp = indicators.get("macd", {}).get("interpretation", "")
    if "Bullish" in macd_interp or "Improving" in macd_interp:
        score += 1
    elif "Bearish" in macd_interp or "Weakening" in macd_interp:
        score -= 1

    ma_layers = indicators.get("moving_averages", {}).get("price_vs_ma", [])
    for layer in ma_layers:
        if "above" in layer:
            score += 1
        elif "below" in layer:
            score -= 1

    cross_signals = indicators.get("moving_averages", {}).get("cross_signals", [])
    for sig in cross_signals:
        if "GOLDEN" in sig:
            score += 2
        elif "DEATH" in sig:
            score -= 2

    if score >= 3:
        return "STRONG BULLISH 🟢"
    elif score >= 1:
        return "MILD BULLISH 🟢"
    elif score <= -3:
        return "STRONG BEARISH 🔴"
    elif score <= -1:
        return "MILD BEARISH 🔴"
    else:
        return "NEUTRAL ⚪"


# ── Utility ──────────────────────────────────────────────────────────


def _latest(series: pd.Series) -> Optional[float]:
    """Safely extract the last non-NaN value of a Series."""
    if series is None or series.empty:
        return None
    last = series.iloc[-1]
    return round(float(last), 6) if pd.notna(last) else None


def _safe_float(val: Any) -> Optional[float]:
    """Convert a value to float, returning None on failure."""
    if val is None or (isinstance(val, float) and np.isnan(val)):
        return None
    try:
        return float(val)
    except (ValueError, TypeError):
        return None


# ── SOTA Indicator Helpers ───────────────────────────────────────────

def _compute_kama(close: pd.Series, window: int = 10) -> Dict[str, Any]:
    """Compute Kaufman's Adaptive Moving Average (KAMA)."""
    try:
        kama_ind = ta.momentum.KAMAIndicator(close=close, window=window)
        kama_val = _latest(kama_ind.kama())
        # Calculate Efficiency Ratio (ER)
        # ER = Change / Volatility
        change = (close - close.shift(window)).abs()
        volatility = close.diff().abs().rolling(window=window).sum()
        er = change / volatility
        er_val = _latest(er)
        
        interpretation = "Chop/Low Liquidity" if er_val is not None and er_val < 0.3 else "Trending/High Conviction"
        return {
            "value": round(kama_val, 2) if kama_val is not None else None,
            "efficiency_ratio": round(er_val, 3) if er_val is not None else None,
            "interpretation": interpretation
        }
    except Exception as e:
        return {"error": str(e)}


def _compute_cmf(high: pd.Series, low: pd.Series, close: pd.Series, volume: pd.Series, window: int = 21) -> Dict[str, Any]:
    """Compute Chaikin Money Flow (CMF)."""
    try:
        cmf_ind = ta.volume.ChaikinMoneyFlowIndicator(high=high, low=low, close=close, volume=volume, window=window)
        cmf_val = _latest(cmf_ind.chaikin_money_flow())
        if cmf_val is None:
            return {"value": None, "interpretation": "Insufficient data"}
            
        if cmf_val > 0.05:
            interp = "Accumulation — institutional buying pressure"
        elif cmf_val < -0.05:
            interp = "Distribution — institutional selling pressure"
        else:
            interp = "Neutral — balanced capital flows"
        return {
            "value": round(cmf_val, 4),
            "interpretation": interp
        }
    except Exception as e:
        return {"error": str(e)}


def _compute_adx(high: pd.Series, low: pd.Series, close: pd.Series, window: int = 14) -> Dict[str, Any]:
    """Compute Average Directional Index (ADX) to determine trend strength."""
    try:
        adx_ind = ta.trend.ADXIndicator(high=high, low=low, close=close, window=window)
        adx_val = _latest(adx_ind.adx())
        plus_di = _latest(adx_ind.adx_pos())
        minus_di = _latest(adx_ind.adx_neg())
        
        if adx_val is None:
            return {"value": None, "interpretation": "Insufficient data"}
            
        if adx_val >= 25:
            interp = "Strong trend"
        elif adx_val <= 20:
            interp = "Chop/Range-bound market"
        else:
            interp = "Moderate trend strength"
            
        bias = "Bullish bias (+DI > -DI)" if plus_di is not None and minus_di is not None and plus_di > minus_di else "Bearish bias (-DI > +DI)"
        
        return {
            "value": round(adx_val, 2),
            "plus_di": round(plus_di, 2) if plus_di is not None else None,
            "minus_di": round(minus_di, 2) if minus_di is not None else None,
            "interpretation": f"{interp} ({bias})"
        }
    except Exception as e:
        return {"error": str(e)}


def _compute_ichimoku(high: pd.Series, low: pd.Series, n1: int = 9, n2: int = 26, n3: int = 52) -> Dict[str, Any]:
    """Compute Ichimoku Kinko Hyo components."""
    try:
        ichimoku = ta.trend.IchimokuIndicator(high=high, low=low, window1=n1, window2=n2, window3=n3)
        tenkan = _latest(ichimoku.ichimoku_conversion_line())
        kijun = _latest(ichimoku.ichimoku_base_line())
        span_a = _latest(ichimoku.ichimoku_a())
        span_b = _latest(ichimoku.ichimoku_b())
        
        if tenkan is None or kijun is None or span_a is None or span_b is None:
            return {"interpretation": "Insufficient data"}
            
        thickness = round(abs(span_a - span_b), 2)
        cloud_bias = "Bullish Cloud (Span A > Span B)" if span_a > span_b else "Bearish Cloud (Span B > Span A)"
        
        return {
            "tenkan_sen": round(tenkan, 2),
            "kijun_sen": round(kijun, 2),
            "senkou_span_a": round(span_a, 2),
            "senkou_span_b": round(span_b, 2),
            "cloud_thickness": thickness,
            "interpretation": f"{cloud_bias} | Thickness: {thickness}"
        }
    except Exception as e:
        return {"error": str(e)}


def _compute_stochastic(high: pd.Series, low: pd.Series, close: pd.Series, window: int = 14, smooth_window: int = 3) -> Dict[str, Any]:
    """Compute Stochastic Oscillator %K and %D."""
    try:
        stoch = ta.momentum.StochasticOscillator(high=high, low=low, close=close, window=window, smooth_window=smooth_window)
        k_val = _latest(stoch.stoch())
        d_val = _latest(stoch.stoch_signal())
        
        if k_val is None or d_val is None:
            return {"k": None, "d": None, "interpretation": "Insufficient data"}
            
        if k_val >= 80:
            interp = "Overbought — watching for bearish crossover/exhaustion"
        elif k_val <= 20:
            interp = "Oversold — watching for bullish crossover/reversal"
        else:
            interp = "Neutral momentum"
            
        return {
            "k": round(k_val, 2),
            "d": round(d_val, 2),
            "interpretation": interp
        }
    except Exception as e:
        return {"error": str(e)}


def _compute_williams_r(high: pd.Series, low: pd.Series, close: pd.Series, lookback: int = 14) -> Dict[str, Any]:
    """Compute Williams %R inverted momentum oscillator."""
    try:
        williams = ta.momentum.WilliamsRIndicator(high=high, low=low, close=close, lbp=lookback)
        w_val = _latest(williams.williams_r())
        
        if w_val is None:
            return {"value": None, "interpretation": "Insufficient data"}
            
        if w_val >= -20:
            interp = "Extreme Overbought — distribution phase"
        elif w_val <= -80:
            interp = "Extreme Oversold — potential capitulation/bottom"
        else:
            interp = "Neutral momentum"
            
        return {
            "value": round(w_val, 2),
            "interpretation": interp
        }
    except Exception as e:
        return {"error": str(e)}


def _compute_disparity_index(close: pd.Series) -> Dict[str, Any]:
    """Compute 5-period and 14-period Disparity Index."""
    try:
        ma_5 = close.rolling(window=5).mean()
        di_5 = ((close - ma_5) / ma_5) * 100
        di_5_val = _latest(di_5)
        
        ma_14 = close.rolling(window=14).mean()
        di_14 = ((close - ma_14) / ma_14) * 100
        di_14_val = _latest(di_14)
        
        if di_5_val is None or di_14_val is None:
            return {"di_5": None, "di_14": None, "interpretation": "Insufficient data"}
            
        if di_5_val >= 5.0 or di_5_val <= -5.0:
            interp = f"DI_5 is overextended ({di_5_val:+.2f}%). Imminent mean-reversion risk."
        else:
            interp = f"DI_5 is neutral ({di_5_val:+.2f}%). Stable momentum."
            
        return {
            "di_5": round(di_5_val, 2),
            "di_14": round(di_14_val, 2),
            "interpretation": interp
        }
    except Exception as e:
        return {"error": str(e)}


def _compute_vwma(close: pd.Series, volume: pd.Series, window: int = 20) -> Dict[str, Any]:
    """Compute Volume Weighted Moving Average (VWMA)."""
    try:
        pv = close * volume
        sum_pv = pv.rolling(window=window).sum()
        sum_vol = volume.rolling(window=window).sum()
        vwma = sum_pv / sum_vol
        vwma_val = _latest(vwma)
        current_close = float(close.iloc[-1])
        
        if vwma_val is None:
            return {"value": None, "interpretation": "Insufficient data"}
            
        bias = "Above VWMA (Bullish accumulation)" if current_close > vwma_val else "Below VWMA (Bearish distribution)"
        return {
            "value": round(vwma_val, 2),
            "interpretation": f"Price is {bias} relative to PKR {vwma_val:.2f}"
        }
    except Exception as e:
        return {"error": str(e)}
