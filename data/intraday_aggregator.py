from __future__ import annotations

import datetime
from typing import Any, Dict, List


def aggregate_ticks_to_hours(ticks: List[List[Any]]) -> List[Dict[str, Any]]:
    """
    Aggregate a list of raw intraday ticks into 1-hour OHLCV bars.
    Ticks format: [[timestamp_ms, price, volume], ...]
    Returns a list of dicts: [{"date": "YYYY-MM-DD HH:00:00", "open": ..., "high": ..., "low": ..., "close": ..., "volume": ..., "interval": "1h"}, ...]
    """
    if not ticks:
        return []

    # Sort ticks chronologically by timestamp
    sorted_ticks = sorted(ticks, key=lambda x: x[0])
    
    # Pakistan Standard Time (PKT) is UTC+5
    tz_pkt = datetime.timezone(datetime.timedelta(hours=5))
    
    # Group ticks by hour
    grouped: Dict[str, List[tuple[float, int]]] = {}
    for item in sorted_ticks:
        if len(item) < 2:
            continue
        ts_ms = item[0]
        price = float(item[1])
        volume = int(item[2]) if len(item) > 2 else 0
        
        # Convert to datetime in PKT (detect seconds vs milliseconds)
        ts_sec = ts_ms / 1000.0 if ts_ms > 1e11 else ts_ms
        dt = datetime.datetime.fromtimestamp(ts_sec, tz=datetime.timezone.utc).astimezone(tz_pkt)
        hour_str = dt.strftime("%Y-%m-%d %H:00:00")
        
        if hour_str not in grouped:
            grouped[hour_str] = []
        grouped[hour_str].append((price, volume))
        
    bars = []
    for hour_str, tick_data in sorted(grouped.items()):
        if not tick_data:
            continue
        prices = [x[0] for x in tick_data]
        volumes = [x[1] for x in tick_data]
        
        bars.append({
            "date": hour_str,
            "interval": "1h",
            "open": prices[0],
            "high": max(prices),
            "low": min(prices),
            "close": prices[-1],
            "volume": sum(volumes)
        })
        
    return bars


def merge_hourly_bars(existing_bars: List[Dict[str, Any]], new_bars: List[Dict[str, Any]], max_days: int = 7) -> List[Dict[str, Any]]:
    """
    Merge existing hourly bars with new ones, deduplicate by date, sort chronologically,
    and retain only the last max_days (default 7 days).
    """
    merged_dict = {}
    
    for bar in existing_bars:
        date_str = bar.get("date")
        if date_str:
            merged_dict[date_str] = bar
            
    for bar in new_bars:
        date_str = bar.get("date")
        if date_str:
            merged_dict[date_str] = bar
            
    # Sort chronologically
    sorted_dates = sorted(merged_dict.keys())
    
    if not sorted_dates:
        return []
        
    # Calculate cutoff time for 1-week retention
    cutoff_date = datetime.datetime.now() - datetime.timedelta(days=max_days)
    cutoff_str = cutoff_date.strftime("%Y-%m-%d %H:%M:%S")
    
    final_bars = []
    for d in sorted_dates:
        if d >= cutoff_str:
            final_bars.append(merged_dict[d])
            
    return final_bars
