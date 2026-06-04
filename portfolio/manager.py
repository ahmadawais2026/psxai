"""
portfolio/manager.py
═══════════════════════════════════════════════════════════════════════
Firebase Firestore-backed portfolio manager class for tracking user holdings
and generating position-aware advice context.
═══════════════════════════════════════════════════════════════════════
"""

from __future__ import annotations

import logging
from typing import Any, Dict, List, Optional
from config import firebase_db
from data.market_data import get_quote

logger = logging.getLogger(__name__)


class PortfolioManager:
    """Class to manage user portfolio positions stored in Firebase Firestore."""

    def __init__(self) -> None:
        self.db = firebase_db
        if self.db is None:
            logger.error("Firestore database client is not initialized.")

    def add_holding(self, symbol: str, shares: float, avg_cost: float) -> bool:
        """
        Add or update a holding position in Firestore.
        
        Args:
            symbol: Stock symbol without .KA suffix (e.g. 'OGDC')
            shares: Number of shares owned
            avg_cost: Average acquisition price
        """
        symbol = symbol.strip().upper()
        if shares <= 0 or avg_cost <= 0:
            logger.error(f"Invalid shares ({shares}) or avg_cost ({avg_cost}) for {symbol}")
            return False
            
        if not self.db:
            logger.error("No database connection available.")
            return False

        try:
            doc_ref = self.db.collection("holdings").document(symbol)
            doc_ref.set({
                "symbol": symbol,
                "shares": shares,
                "avg_cost": avg_cost
            })
            logger.info(f"Updated Firestore holding: {symbol} - {shares} shares @ PKR {avg_cost}")
            return True
        except Exception as e:
            logger.error(f"Error adding holding for {symbol} to Firestore: {e}")
            return False

    def remove_holding(self, symbol: str) -> bool:
        """
        Remove a holding position entirely from Firestore.
        
        Args:
            symbol: Stock symbol without .KA suffix (e.g. 'OGDC')
        """
        symbol = symbol.strip().upper()
        if not self.db:
            logger.error("No database connection available.")
            return False

        try:
            doc_ref = self.db.collection("holdings").document(symbol)
            doc = doc_ref.get()
            if doc.exists:
                doc_ref.delete()
                logger.info(f"Removed Firestore holding: {symbol}")
                return True
            else:
                logger.warning(f"Holding not found to remove from Firestore: {symbol}")
                return False
        except Exception as e:
            logger.error(f"Error removing holding for {symbol} from Firestore: {e}")
            return False

    def get_holdings(self) -> List[Dict[str, Any]]:
        """
        Get all holdings from Firestore with current valuation and calculated P&L.
        
        Returns:
            List of dicts: {symbol, shares, avg_cost, current_price, current_value, cost_basis, pnl, pnl_pct}
        """
        holdings = []
        if not self.db:
            logger.error("No database connection available.")
            return holdings

        try:
            docs = self.db.collection("holdings").stream()
            for doc in docs:
                data = doc.to_dict()
                symbol = data.get("symbol", doc.id).upper()
                shares = float(data.get("shares", 0.0))
                avg_cost = float(data.get("avg_cost", 0.0))
                
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
            logger.error(f"Error retrieving holdings from Firestore: {e}")
            
        return holdings

    def get_portfolio_summary(self) -> Dict[str, Any]:
        """
        Calculate and return overall portfolio summary metrics from Firestore holdings.
        
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
