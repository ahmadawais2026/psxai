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
import logging
import traceback
from datetime import datetime

# ── Logging ───────────────────────────────────────────────────
# Configure root logging at INFO so the full analysis pipeline
# (orchestrator/agents emit logger.info) is visible in Cloud Run /
# Cloud Logging. Without this the root logger defaults to WARNING and
# all pipeline progress is silently dropped in production. Override
# with the LOG_LEVEL env var (e.g. DEBUG). force=True so it wins over
# any handler gunicorn/Functions installed first; stdout so Cloud Run
# ingests it.
logging.basicConfig(
    level=os.getenv("LOG_LEVEL", "INFO").upper(),
    format="[%(asctime)s] [%(levelname)s] %(name)s: %(message)s",
    stream=sys.stdout,
    force=True,
)
for _noisy in ("urllib3", "google.api_core", "google.auth", "werkzeug"):
    logging.getLogger(_noisy).setLevel(logging.WARNING)

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

from config import FLASK_HOST, FLASK_PORT, FLASK_DEBUG, GEMINI_API_KEY, USE_VERTEX

# ── Flask App Setup ───────────────────────────────────────────
app = Flask(__name__, static_url_path="", static_folder="static")
CORS(app)


from flask.json.provider import DefaultJSONProvider
import math

class SafeJSONProvider(DefaultJSONProvider):
    def dumps(self, obj, **kwargs):
        def sanitize(o):
            if isinstance(o, dict):
                return {k: sanitize(v) for k, v in o.items()}
            elif isinstance(o, list):
                return [sanitize(x) for x in o]
            elif isinstance(o, tuple):
                return tuple(sanitize(x) for x in o)
            tname = type(o).__name__
            if isinstance(o, float) or 'float' in tname or tname == 'floating':
                try:
                    fval = float(o)
                    if math.isnan(fval) or math.isinf(fval):
                        return None
                    return fval
                except (ValueError, TypeError):
                    pass
            return o
        return super().dumps(sanitize(obj), **kwargs)

app.json = SafeJSONProvider(app)


@app.after_request
def set_streaming_headers(response):
    """
    Disable GFE/Nginx proxy buffering on all responses.
    X-Accel-Buffering: no ensures Server-Sent Event chunks reach the client
    immediately rather than being held in the Google Front End buffer.
    Critical for the 2-4 minute multi-agent streaming pipeline.
    """
    response.headers["X-Accel-Buffering"] = "no"
    return response



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
        # model_name=None → agents self-route per role (config.ROLE_TIER). The UI
        # no longer sends a model; an explicit value is only used for internal A/B.
        model_name = (data.get("model") or "").strip() or None

        if not symbol:
            return jsonify({"error": "Symbol is required"}), 400

        # Routing is first-party Gemini via Vertex AI (credit-covered on the trial).
        if not USE_VERTEX and not GEMINI_API_KEY:
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
                    user_context = pm.get_portfolio_overlap_context(uid, symbol)
            except Exception:
                user_context = None  # Proceed without portfolio context

        # Run the full orchestrator pipeline
        orchestrator = _get_orchestrator()
        report = orchestrator.analyze(symbol, user_context=user_context, model_name=model_name)

        if report is None:
            return jsonify({"error": f"Could not analyze '{symbol}'. Ticker may not exist on PSX."}), 404

        return jsonify(report)

    except Exception as e:
        traceback.print_exc()
        return jsonify({
            "error": f"Analysis failed: {str(e)}",
            "details": "The AI analysis pipeline encountered an error. This may be due to rate limiting or API issues. Please try again in a moment."
        }), 500


@app.route("/api/analyze/stream", methods=["POST"])
def analyze_stock_stream():
    """Stream analysis events for real-time frontend updates using SSE."""
    try:
        data = request.json or {}
        symbol = data.get("symbol", "").strip().upper()
        if not symbol:
            return jsonify({"error": "Ticker symbol is required"}), 400

        # model_name=None → agents self-route per role (see /api/analyze).
        model_name = (data.get("model") or "").strip() or None

        # Routing is first-party Gemini via Vertex AI (credit-covered on the trial).
        if not USE_VERTEX and not GEMINI_API_KEY:
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
                    user_context = pm.get_portfolio_overlap_context(uid, symbol)
            except Exception:
                user_context = None

        orchestrator = _get_orchestrator()

        def generate():
            # The debate runs as a single multi-minute LangGraph node that emits no
            # events, so the SSE stream would sit idle long enough for Cloud Run /
            # the browser to drop the connection ("network error") even though the
            # pipeline completes server-side (~4 min, HTTP 200). Run the pipeline in
            # a worker thread and emit a heartbeat comment whenever no real event has
            # arrived for a few seconds, so bytes keep flowing until completion.
            import queue as _queue
            import threading as _threading

            q = _queue.Queue()

            def _produce():
                try:
                    for event in orchestrator.analyze_stream(symbol, user_context=user_context, model_name=model_name):
                        q.put(("event", event))
                except Exception as exc:
                    traceback.print_exc()
                    q.put(("error", str(exc)))
                finally:
                    q.put(("done", None))

            _threading.Thread(target=_produce, daemon=True).start()

            while True:
                try:
                    kind, payload = q.get(timeout=10)
                except _queue.Empty:
                    # SSE comment line: ignored by the client's `data:` parser but
                    # keeps the connection alive during the silent debate phase.
                    yield ": keepalive\n\n"
                    continue
                if kind == "done":
                    break
                if kind == "error":
                    yield f"data: {json.dumps({'event': 'error', 'message': payload})}\n\n"
                    break
                yield f"data: {json.dumps(payload)}\n\n"

        from flask import Response, stream_with_context
        response = Response(stream_with_context(generate()), mimetype="text/event-stream")
        response.headers["Cache-Control"] = "no-cache"
        response.headers["Connection"] = "keep-alive"
        response.headers["X-Accel-Buffering"] = "no"
        return response

    except Exception as e:
        traceback.print_exc()
        return jsonify({"error": f"Stream setup failed: {str(e)}"}), 500



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


@app.route("/api/feedback", methods=["POST"])
def submit_feedback():
    """
    Record user feedback against a stored recommendation (learning flywheel).
    Request body: {"recommendation_id": "...", "rating": "agree|disagree",
                   "action": "bought|sold|held|none", "note": "..."}
    """
    try:
        uid = _get_authenticated_uid(request)  # may be None for anonymous use
        data = request.get_json(force=True) or {}
        rec_id = (data.get("recommendation_id") or "").strip()
        if not rec_id:
            return jsonify({"error": "recommendation_id is required"}), 400

        rating = data.get("rating")
        if rating not in (None, "agree", "disagree"):
            return jsonify({"error": "rating must be 'agree' or 'disagree'"}), 400

        feedback = {
            "uid": uid,
            "rating": rating,
            "action": data.get("action"),
            "note": (data.get("note") or "")[:1000],
            "symbol": (data.get("symbol") or "").strip().upper() or None,
        }

        from learning.ledger import attach_feedback
        if attach_feedback(rec_id, feedback):
            return jsonify({"success": True})
        return jsonify({"error": "Could not record feedback (unknown id or store unavailable)."}), 400
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

        symbol = data.get("symbol", "").upper()
        sector = data.get("sector", "")

        # Fetch rich financial data from Firestore/AskAnalyst and attach for PDF rendering
        raw_data: dict = {}
        try:
            from data.market_data import get_financial_statements, get_fundamentals
            from data.local_data import (
                get_market_context, get_local_company_news,
                get_research_reports, format_market_context_text,
            )

            try:
                raw_data["financial_statements"] = get_financial_statements(symbol)
            except Exception as _e:
                app.logger.warning("financial_statements fetch failed: %s", _e)
                raw_data["financial_statements"] = {}

            try:
                raw_data["fundamentals"] = get_fundamentals(symbol)
            except Exception as _e:
                app.logger.warning("fundamentals fetch failed: %s", _e)
                raw_data["fundamentals"] = {}

            try:
                mctx = get_market_context(sector=sector)
                raw_data["market_context_text"] = format_market_context_text(mctx)
            except Exception as _e:
                app.logger.warning("market_context fetch failed: %s", _e)
                raw_data["market_context_text"] = ""

            try:
                raw_data["company_news"] = get_local_company_news(symbol)[:20]
            except Exception as _e:
                app.logger.warning("company_news fetch failed: %s", _e)
                raw_data["company_news"] = []

            try:
                raw_data["research_excerpts"] = [
                    r[:3000] for r in get_research_reports(symbol, sector=sector, max_reports=4)
                ]
            except Exception as _e:
                app.logger.warning("research_reports fetch failed: %s", _e)
                raw_data["research_excerpts"] = []

        except Exception as _e:
            app.logger.warning("raw_data enrichment block failed: %s", _e)

        data["raw_data"] = raw_data

        from report.pdf_generator import generate_pdf
        pdf_bytes = generate_pdf(data)

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


@app.route("/api/dcf", methods=["POST"])
def calculate_dynamic_dcf():
    """
    On-the-fly Exposable Assumptions DCF API.
    Payload: { "fcf": float, "beta": float, "shares": float, "growth": float, "terminal_growth": float, "wacc_override": float }
    """
    data = request.json
    try:
        fcf = float(data.get("fcf", 0))
        beta = float(data.get("beta", 1.0))
        shares = float(data.get("shares", 0))
        growth = float(data.get("growth", 0.08))
        
        if fcf <= 0 or shares <= 0:
            return jsonify({"error": "Invalid FCF or Shares. DCF requires positive cashflow."}), 400
            
        from data.dcf_engine import DCFEngine
        engine = DCFEngine()
        
        if "wacc_override" in data or "terminal_growth" in data:
            # Custom singular calculation
            tg = float(data.get("terminal_growth", 0.04))
            val = engine.calculate_intrinsic_value(
                base_fcf=fcf,
                levered_beta=beta,
                short_term_growth=growth,
                terminal_growth=tg,
                shares_outstanding=shares
            )
            return jsonify({"intrinsic_value": round(val, 2) if val else None})
        else:
            # Full scenarios return
            results = engine.generate_scenarios(fcf, beta, shares, growth)
            return jsonify(results)
    except Exception as e:
        return jsonify({"error": str(e)}), 400


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
