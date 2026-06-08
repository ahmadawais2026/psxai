"""
PSX Investment Advisor — Flask Application
==========================================
AI-powered investment advisory for Pakistan Stock Exchange (PSX/KSE-100).
Uses a multi-agent architecture with specialized analyst agents powered by
Google Gemini 3.1 Flash Lite.

DISCLAIMER: This is an educational tool only. NOT financial advice.
"""

import os
import sys
import json
import traceback
from datetime import datetime

from flask import Flask, request, jsonify, send_from_directory, send_file
from flask_cors import CORS
import io

# ── Ensure project root is on path ────────────────────────────
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import firebase_admin
from firebase_admin import auth

# Clean up FIREBASE_CONFIG env var on Windows if it has bad escaping
firebase_config = os.environ.get("FIREBASE_CONFIG")
if firebase_config:
    cleaned = firebase_config.replace('\\"', '"')
    if cleaned.startswith('"') and cleaned.endswith('"') and cleaned.count('{') > 0:
        cleaned = cleaned[1:-1]
    os.environ["FIREBASE_CONFIG"] = cleaned

# Initialize firebase admin SDK
if not firebase_admin._apps:
    firebase_admin.initialize_app()

from config import FLASK_HOST, FLASK_PORT, FLASK_DEBUG, GEMINI_API_KEY

# ── Flask App Setup ───────────────────────────────────────────
app = Flask(__name__, static_url_path="", static_folder="static")
CORS(app)


def _get_authenticated_uid(req):
    """Verify Authorization Bearer token from header and return user uid."""
    auth_header = req.headers.get("Authorization")
    if not auth_header or not auth_header.startswith("Bearer "):
        return None
    token = auth_header.split("Bearer ")[1]
    try:
        decoded_token = auth.verify_id_token(token)
        return decoded_token["uid"]
    except Exception:
        return None


# ── Lazy imports (allows app to start even if deps fail) ──────
def _get_orchestrator():
    """Lazy-load the orchestrator to avoid import errors on startup."""
    from agents.orchestrator import Orchestrator
    if not hasattr(_get_orchestrator, "_instance"):
        _get_orchestrator._instance = Orchestrator()
    return _get_orchestrator._instance


def _get_portfolio_manager():
    """Lazy-load the portfolio manager."""
    from portfolio.manager import PortfolioManager
    if not hasattr(_get_portfolio_manager, "_instance"):
        _get_portfolio_manager._instance = PortfolioManager()
    return _get_portfolio_manager._instance


# ══════════════════════════════════════════════════════════════
#  STATIC FILE ROUTES
# ══════════════════════════════════════════════════════════════

@app.route("/")
def serve_index():
    """Serve the main SPA."""
    return send_from_directory(app.static_folder, "index.html")


# ══════════════════════════════════════════════════════════════
#  HEALTH & STATUS
# ══════════════════════════════════════════════════════════════

@app.route("/api/health")
def health_check():
    """Health check endpoint for deployment monitoring."""
    return jsonify({
        "status": "healthy",
        "timestamp": datetime.utcnow().isoformat(),
        "gemini_configured": bool(GEMINI_API_KEY),
        "version": "1.0.0"
    })


# ══════════════════════════════════════════════════════════════
#  TICKER SEARCH
# ══════════════════════════════════════════════════════════════

@app.route("/api/search")
def search_tickers():
    """
    Search PSX tickers by symbol or company name.
    Query params: q (search query)
    """
    query = request.args.get("q", "").strip()
    if len(query) < 1:
        return jsonify({"results": []})

    from data.psx_tickers import search_tickers
    results = search_tickers(query)
    return jsonify({"results": results[:15]})  # Limit to 15 suggestions


# ══════════════════════════════════════════════════════════════
#  QUICK QUOTE
# ══════════════════════════════════════════════════════════════

@app.route("/api/quote/<symbol>")
def get_quote(symbol: str):
    """
    Get current quote for a PSX stock.
    Returns price, change, volume, market cap.
    """
    try:
        from data.market_data import get_quote
        quote = get_quote(symbol.upper())
        if quote is None:
            return jsonify({"error": f"Ticker '{symbol}' not found on PSX"}), 404
        return jsonify(quote)
    except Exception as e:
        return jsonify({"error": str(e)}), 500


# ══════════════════════════════════════════════════════════════
#  FULL ANALYSIS (Main endpoint)
# ══════════════════════════════════════════════════════════════

@app.route("/api/analyze", methods=["POST"])
def analyze_stock():
    """
    Run the full multi-agent analysis pipeline on a PSX stock.

    Request body:
    {
        "symbol": "OGDC",
        "include_portfolio": true  // optional, includes user's position context
    }

    Returns the complete advisory report from all agents.
    """
    try:
        data = request.get_json(force=True)
        symbol = data.get("symbol", "").strip().upper()

        if not symbol:
            return jsonify({"error": "Symbol is required"}), 400

        # Check if Gemini is configured
        if not GEMINI_API_KEY:
            return jsonify({
                "error": "Gemini API key not configured. Please add GEMINI_API_KEY to your .env file."
            }), 503

        # Get portfolio context if requested
        user_context = None
        if data.get("include_portfolio", False):
            try:
                uid = _get_authenticated_uid(request)
                if uid:
                    pm = _get_portfolio_manager()
                    user_context = pm.get_position_context(uid, symbol)
            except Exception:
                user_context = None  # Proceed without portfolio context

        # Run the full orchestrator pipeline
        orchestrator = _get_orchestrator()
        report = orchestrator.analyze(symbol, user_context=user_context)

        if report is None:
            return jsonify({"error": f"Could not analyze '{symbol}'. Ticker may not exist on PSX."}), 404

        return jsonify(report)

    except Exception as e:
        traceback.print_exc()
        return jsonify({
            "error": f"Analysis failed: {str(e)}",
            "details": "The AI analysis pipeline encountered an error. This may be due to rate limiting or API issues. Please try again in a moment."
        }), 500


# ══════════════════════════════════════════════════════════════
#  PORTFOLIO MANAGEMENT
# ══════════════════════════════════════════════════════════════

@app.route("/api/portfolio", methods=["GET"])
def get_portfolio():
    """Get all user holdings with current values."""
    try:
        uid = _get_authenticated_uid(request)
        if not uid:
            return jsonify({"error": "Unauthorized. Please log in first."}), 401
            
        pm = _get_portfolio_manager()
        holdings = pm.get_holdings(uid)
        summary = pm.get_portfolio_summary(uid)
        return jsonify({
            "holdings": holdings,
            "summary": summary
        })
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@app.route("/api/portfolio/add", methods=["POST"])
def add_holding():
    """
    Add or update a portfolio holding.
    Request body: {"symbol": "OGDC", "shares": 100, "avg_cost": 95.50}
    """
    try:
        uid = _get_authenticated_uid(request)
        if not uid:
            return jsonify({"error": "Unauthorized. Please log in first."}), 401

        data = request.get_json(force=True)
        symbol = data.get("symbol", "").strip().upper()
        shares = float(data.get("shares", 0))
        avg_cost = float(data.get("avg_cost", 0))

        if not symbol or shares <= 0 or avg_cost <= 0:
            return jsonify({"error": "Valid symbol, shares (>0), and avg_cost (>0) are required"}), 400

        pm = _get_portfolio_manager()
        pm.add_holding(uid, symbol, shares, avg_cost)
        return jsonify({"success": True, "message": f"Added {shares} shares of {symbol} at PKR {avg_cost:.2f}"})
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@app.route("/api/portfolio/<symbol>", methods=["DELETE"])
def remove_holding(symbol: str):
    """Remove a holding from the portfolio."""
    try:
        uid = _get_authenticated_uid(request)
        if not uid:
            return jsonify({"error": "Unauthorized. Please log in first."}), 401

        pm = _get_portfolio_manager()
        success = pm.remove_holding(uid, symbol.upper())
        if not success:
            return jsonify({"error": f"Holding {symbol.upper()} not found or could not be removed."}), 400
        return jsonify({"success": True, "message": f"Removed {symbol.upper()} from portfolio"})
    except Exception as e:
        return jsonify({"error": str(e)}), 500


# ══════════════════════════════════════════════════════════════
#  ERROR HANDLERS
# ══════════════════════════════════════════════════════════════

@app.route("/api/report/generate", methods=["POST"])
def generate_report():
    """
    Generate a PDF investment report from a full analysis payload.

    Request body: the complete JSON object returned by /api/analyze.
    Returns: PDF file download (application/pdf).
    """
    try:
        data = request.get_json(force=True)
        if not data or not data.get("symbol"):
            return jsonify({"error": "Report data with symbol is required"}), 400

        from report.pdf_generator import generate_pdf
        pdf_bytes = generate_pdf(data)

        symbol   = data.get("symbol", "report").upper()
        filename = f"PSX_Analysis_{symbol}_{datetime.utcnow().strftime('%Y%m%d')}.pdf"

        return send_file(
            io.BytesIO(pdf_bytes),
            mimetype="application/pdf",
            as_attachment=True,
            download_name=filename,
        )

    except Exception as e:
        traceback.print_exc()
        return jsonify({"error": f"PDF generation failed: {str(e)}"}), 500


@app.errorhandler(404)
def not_found(e):
    """Return JSON for API 404s, index.html for page 404s."""
    if request.path.startswith("/api/"):
        return jsonify({"error": "Endpoint not found"}), 404
    return send_from_directory(app.static_folder, "index.html")


@app.errorhandler(500)
def internal_error(e):
    return jsonify({"error": "Internal server error"}), 500


# ══════════════════════════════════════════════════════════════
#  MAIN ENTRY POINT
# ══════════════════════════════════════════════════════════════

if __name__ == "__main__":
    print("=" * 60)
    print("  PSX Investment Advisor")
    print("  AI-Powered Intelligence for Pakistan Stock Exchange")
    print("=" * 60)

    if not GEMINI_API_KEY:
        print("\n  [!] WARNING: GEMINI_API_KEY not set!")
        print("  Copy .env.example to .env and add your key.")
        print("  Get a free key at: https://aistudio.google.com/\n")
    else:
        print(f"\n  [OK] Gemini API configured")

    print(f"  Starting server at http://localhost:{FLASK_PORT}")
    print(f"  Market: Pakistan Stock Exchange (PSX/KSE-100)")
    print(f"  [!] For educational purposes only — NOT financial advice")
    print("=" * 60 + "\n")

    app.run(host=FLASK_HOST, port=FLASK_PORT, debug=FLASK_DEBUG)
