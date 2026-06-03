"""
portfolio/manager.py
═══════════════════════════════════════════════════════════════════════
SQLite-backed portfolio manager class for tracking user holdings and
generating position-aware advice context.
═══════════════════════════════════════════════════════════════════════
"""

from __future__ import annotations

import logging
import sqlite3
import os
from typing import Any, Dict, List, Optional
from config import PORTFOLIO_DB_PATH
from data.market_data import get_quote

logger = logging.getLogger(__name__)

_CREATE_TABLE_SQL = """
CREATE TABLE IF NOT EXISTS holdings (
    symbol   TEXT PRIMARY KEY,
    shares   REAL NOT NULL,
    avg_cost REAL NOT NULL
);
"""

class PortfolioManager:
    """Class to manage user portfolio positions stored in an SQLite database."""

    def __init__(self) -> None:
        self.db_path = PORTFOLIO_DB_PATH
        self._init_db()

    def _get_connection(self) -> sqlite3.Connection:
        """Create and return a database connection, ensuring directories exist."""
        db_dir = os.path.dirname(self.db_path)
        if db_dir and not os.path.exists(db_dir):
            os.makedirs(db_dir, exist_ok=True)
        
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        return conn

    def _init_db(self) -> None:
        """Initialize the portfolio database schema."""
        with self._get_connection() as conn:
            conn.execute(_CREATE_TABLE_SQL)
            conn.commit()

    def add_holding(self, symbol: str, shares: float, avg_cost: float) -> bool:
        """
        Add or update a holding position.
        
        Args:
            symbol: Stock symbol without .KA suffix (e.g. 'OGDC')
            shares: Number of shares owned
            avg_cost: Average acquisition price
        """
        symbol = symbol.strip().upper()
        if shares <= 0 or avg_cost <= 0:
            logger.error(f"Invalid shares ({shares}) or avg_cost ({avg_cost}) for {symbol}")
            return False
            
        try:
            with self._get_connection() as conn:
                conn.execute(
                    """
                    INSERT INTO holdings (symbol, shares, avg_cost)
                    VALUES (?, ?, ?)
                    ON CONFLICT(symbol) DO UPDATE SET
                        shares = excluded.shares,
                        avg_cost = excluded.avg_cost
                    """,
                    (symbol, shares, avg_cost)
                )
                conn.commit()
            logger.info(f"Updated holding: {symbol} - {shares} shares @ PKR {avg_cost}")
            return True
        except Exception as e:
            logger.error(f"Error adding holding for {symbol}: {e}")
            return False

    def remove_holding(self, symbol: str) -> bool:
        """
        Remove a holding position entirely.
        
        Args:
            symbol: Stock symbol without .KA suffix (e.g. 'OGDC')
        """
        symbol = symbol.strip().upper()
        try:
            with self._get_connection() as conn:
                cursor = conn.execute("DELETE FROM holdings WHERE symbol = ?", (symbol,))
                conn.commit()
                success = cursor.rowcount > 0
            if success:
                logger.info(f"Removed holding: {symbol}")
            else:
                logger.warning(f"Holding not found to remove: {symbol}")
            return success
        except Exception as e:
            logger.error(f"Error removing holding for {symbol}: {e}")
            return False

    def get_holdings(self) -> List[Dict[str, Any]]:
        """
        Get all holdings with current valuation and calculated P&L.
        
        Returns:
            List of dicts: {symbol, shares, avg_cost, current_price, current_value, cost_basis, pnl, pnl_pct}
        """
        holdings = []
        try:
            with self._get_connection() as conn:
                rows = conn.execute("SELECT symbol, shares, avg_cost FROM holdings").fetchall()
                
            for row in rows:
                symbol = row["symbol"]
                shares = row["shares"]
                avg_cost = row["avg_cost"]
                
                # Fetch current quote
                current_price = 0.0
                quote = get_quote(symbol)
                if quote and "price" in quote:
                    current_price = quote["price"]
                    
                cost_basis = shares * avg_cost
                current_value = shares * current_price
                pnl = current_value - cost_basis
                pnl_pct = (pnl / cost_basis * 100) if cost_basis > 0 else 0.0
                
                holdings.append({
                    "symbol": symbol,
                    "shares": shares,
                    "avg_cost": avg_cost,
                    "current_price": current_price,
                    "current_value": current_value,
                    "cost_basis": cost_basis,
                    "pnl": pnl,
                    "pnl_pct": pnl_pct
                })
                
        except Exception as e:
            logger.error(f"Error retrieving holdings: {e}")
            
        return holdings

    def get_portfolio_summary(self) -> Dict[str, Any]:
        """
        Calculate and return overall portfolio summary metrics.
        
        Returns:
            Dict: {total_value, total_cost, total_pnl, total_pnl_pct, holdings}
        """
        holdings = self.get_holdings()
        total_value = sum(h["current_value"] for h in holdings)
        total_cost = sum(h["cost_basis"] for h in holdings)
        total_pnl = total_value - total_cost
        total_pnl_pct = (total_pnl / total_cost * 100) if total_cost > 0 else 0.0
        
        # Calculate allocations
        for h in holdings:
            h["allocation_pct"] = (h["current_value"] / total_value * 100) if total_value > 0 else 0.0
            
        return {
            "total_value": total_value,
            "total_cost": total_cost,
            "total_pnl": total_pnl,
            "total_pnl_pct": total_pnl_pct,
            "holdings": holdings
        }

    def get_position_context(self, symbol: str) -> Dict[str, Any]:
        """
        Get context for a specific ticker to enable position-aware advice.
        
        Args:
            symbol: Stock symbol without .KA suffix
            
        Returns:
            Dict: {owns_stock, shares, avg_cost, current_value, portfolio_pct, is_concentrated}
        """
        symbol = symbol.strip().upper()
        summary = self.get_portfolio_summary()
        total_val = summary["total_value"]
        
        # Check if stock exists in portfolio
        target_holding = None
        for h in summary["holdings"]:
            if h["symbol"] == symbol:
                target_holding = h
                break
                
        if target_holding:
            portfolio_pct = target_holding["allocation_pct"]
            return {
                "owns_stock": True,
                "shares": target_holding["shares"],
                "avg_cost": target_holding["avg_cost"],
                "current_value": target_holding["current_value"],
                "portfolio_pct": portfolio_pct,
                "is_concentrated": portfolio_pct > 15.0
            }
        else:
            return {
                "owns_stock": False,
                "shares": 0.0,
                "avg_cost": 0.0,
                "current_value": 0.0,
                "portfolio_pct": 0.0,
                "is_concentrated": False
            }
