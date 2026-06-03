import os
from dotenv import load_dotenv

load_dotenv()

# ── Gemini Configuration ──────────────────────────────────────
GEMINI_API_KEY = os.getenv("GEMINI_API_KEY", "")
GEMINI_MODEL = "gemini-3.1-flash-lite"
GEMINI_TEMPERATURE = 0.3          # Low temperature for analytical consistency
GEMINI_MAX_OUTPUT_TOKENS = 4096

# ── Flask Configuration ───────────────────────────────────────
FLASK_HOST = "0.0.0.0"
FLASK_PORT = int(os.getenv("PORT", 5000))
FLASK_DEBUG = os.getenv("FLASK_DEBUG", "false").lower() == "true"

# ── Cache TTLs (seconds) ─────────────────────────────────────
CACHE_TTL_QUOTE = 10              # 10 seconds (near real-time)
CACHE_TTL_HISTORY = 60 * 15       # 15 minutes
CACHE_TTL_FUNDAMENTALS = 60 * 60 * 24  # 24 hours
CACHE_TTL_NEWS = 60 * 60          # 1 hour

# ── PSX Configuration ────────────────────────────────────────
PSX_SUFFIX = ".KA"                # Yahoo Finance suffix for Karachi Stock Exchange
DEFAULT_CURRENCY = "PKR"

# ── Analysis Configuration ────────────────────────────────────
HISTORY_PERIOD_DAILY = "1y"       # 1 year of daily data for technical analysis
HISTORY_PERIOD_HOURLY = "1mo"     # 1 month of hourly data for short-term
DEBATE_ROUNDS = 2                 # Bull vs Bear debate rounds

# ── Database ──────────────────────────────────────────────────
DB_PATH = os.path.join(os.path.dirname(__file__), "data", "advisor_cache.db")
PORTFOLIO_DB_PATH = os.path.join(os.path.dirname(__file__), "data", "portfolio.db")
