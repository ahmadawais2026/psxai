import os
from dotenv import load_dotenv

load_dotenv()

# ── Gemini Configuration ──────────────────────────────────────
GEMINI_API_KEY = os.getenv("GEMINI_API_KEY", "")
GEMINI_MODEL = os.getenv("GEMINI_MODEL", "gemini-3.1-pro")
GEMINI_TEMPERATURE = 0.5          # Balanced: detailed, flowing analysis while staying factual
GEMINI_MAX_OUTPUT_TOKENS = 100000  # Generous ceiling so deep reasoning + granular output is never truncated

# ── Vertex AI Configuration ───────────────────────────────────
USE_VERTEX = os.getenv("USE_VERTEX", "true").lower() == "true"
VERTEX_PROJECT = os.getenv("VERTEX_PROJECT", "project-744d0520-c16e-4aa5-b3e")
VERTEX_LOCATION = os.getenv("VERTEX_LOCATION", "global")  # Gemini 3.x models are served on the `global` endpoint only (404 in us-central1)

def map_model_name(model_name: str) -> str:
    """Map the UI's model selection to the exact Vertex AI model ID.

    The 3.x Gemini models are now live in Vertex Model Garden, so selections
    pass through unchanged. This function previously rewrote 3.x → 2.5 as a
    shim from when 3.x wasn't available on Vertex; that downgrade silently
    served the old models on every request and has been removed.

    If Vertex rejects an ID (404 / NOT_FOUND), confirm the exact string in
    Model Garden → the model page → "View Code" and add an alias below.
    """
    if not model_name:
        return "gemini-3.5-flash"
    # Aliases for any UI label whose Vertex model ID differs go here. The Pro
    # model ships under a `-preview` suffix in Model Garden; without this alias
    # every reasoning-tier call (and the news entity-resolution filter) 404s.
    aliases = {
        "gemini-3.1-pro": "gemini-3.1-pro-preview",
    }
    return aliases.get(model_name.lower(), model_name)

# ── DeepSeek Configuration ────────────────────────────────────
# DeepSeek is a Vertex AI partner Model-as-a-Service. On the Google Cloud $300
# free-trial credit it is NOT payable (the credit excludes partner MaaS models),
# so it is intentionally left out of all routing below. The key is read from env
# only — no hardcoded secret — for a future paid-account re-enable.
DEEPSEEK_API_KEY = os.getenv("DEEPSEEK_API_KEY", "")


# ── Per-Role Model Routing ────────────────────────────────────
# Bump whenever the routing maps below change, so cached analyses re-run.
# NOTE: also bump this when the learned-context injection (Phase 2 flywheel)
# goes live, so a stale cached dossier isn't served as if it had incorporated
# the track-record feedback.
# r2: Portfolio Manager prompt now carries the {calibration_context} track-record
#     block (learning flywheel) — bumped so pre-learning cached dossiers re-run.
ROUTING_VERSION = "r2"

# ── Learning Loop / Recommendation Ledger ─────────────────────
# Durable, append-only store of every recommendation (NOT the TTL'd "cache"
# collection) plus the realized outcomes the scorer backfills. This is the hub
# the self-improvement flywheel and the forecast track-record read from.
RECOMMENDATIONS_COLLECTION = "recommendations"
LESSONS_COLLECTION = "lessons"

# Forward-return horizons the outcome scorer evaluates each call against,
# expressed in calendar days (mapped to the nearest available trading bar).
OUTCOME_HORIZONS = {"1w": 7, "1m": 30, "3m": 90}

# KSE-100 benchmark symbol used for excess-return (alpha) scoring.
BENCHMARK_SYMBOL = "KSE100"

# Tier → concrete first-party Gemini model (served via Vertex AI, credit-covered).
# Only first-party Gemini is used: the free-trial credit covers Vertex AI Gemini
# but NOT partner MaaS models (DeepSeek/Claude/Llama/Mistral) nor the AI Studio
# Gemini API. Keep USE_VERTEX=true so calls bill against the credit.
MODEL_TIERS = {
    "reasoning": "gemini-3.1-pro",
    "fast": "gemini-3.5-flash",
}

# Per-tier generation config. Bound to the tier and held fixed across the whole
# fallback chain (so a reasoning agent that falls back to flash keeps its budget).
GEN_CONFIG_BY_TIER = {
    "reasoning": {"temperature": 0.5, "max_output_tokens": 32000, "thinking_budget": -1},
    "fast": {"temperature": 0.25, "max_output_tokens": 6000, "thinking_budget": 0},
}

# Which tier each agent role runs on. "risk" is the tune candidate: context-heavy
# but not deep-reasoning — kept on reasoning for safety, can move to fast later.
ROLE_TIER = {
    "fundamentals": "reasoning",
    "risk": "reasoning",
    "bull": "reasoning",
    "bear": "reasoning",
    "portfolio_manager": "reasoning",
    "technical": "fast",
    "sentiment": "fast",
    "debate_synthesizer": "fast",
}

# Ordered model fallback per role — Gemini-only (DeepSeek excluded by trial terms).
# Primary = the role's tier model; fallback = the sibling Gemini tier. Fallback
# fires only on transport/empty/exhaustion failure, never on malformed JSON.
FALLBACK_BY_ROLE = {
    role: [MODEL_TIERS[tier]] + [MODEL_TIERS[t] for t in MODEL_TIERS if t != tier]
    for role, tier in ROLE_TIER.items()
}


# ── Firebase Configuration ────────────────────────────────────
import firebase_admin
from firebase_admin import credentials, firestore

# Clean up FIREBASE_CONFIG env var on Windows if it has bad escaping
firebase_config = os.environ.get("FIREBASE_CONFIG")
if firebase_config:
    cleaned = firebase_config.replace('\\"', '"')
    if cleaned.startswith('"') and cleaned.endswith('"') and cleaned.count('{') > 0:
        cleaned = cleaned[1:-1]
    os.environ["FIREBASE_CONFIG"] = cleaned

# Pin the Firebase/Firestore project explicitly. Without this, the Admin SDK
# falls back to ADC's default project — which locally may be an unrelated
# gcloud project (e.g. a personal default) that has no Firestore database.
# Matches the "default" project in .firebaserc; override via env if needed.
FIREBASE_PROJECT_ID = os.getenv("FIREBASE_PROJECT_ID", "aiforpsx")

# Initialize Firebase App
firebase_db = None
try:
    if not firebase_admin._apps:
        cred_path = os.getenv("GOOGLE_APPLICATION_CREDENTIALS")
        app_options = {"projectId": FIREBASE_PROJECT_ID}
        if cred_path and os.path.exists(cred_path):
            cred = credentials.Certificate(cred_path)
            firebase_admin.initialize_app(cred, app_options)
        else:
            firebase_admin.initialize_app(options=app_options)
    firebase_db = firestore.client()
except Exception as e:
    print(f"[-] Failed to initialize Firebase Admin SDK: {e}")

# ── Flask Configuration ───────────────────────────────────────
FLASK_HOST = "0.0.0.0"
FLASK_PORT = int(os.getenv("PORT", 5000))
FLASK_DEBUG = os.getenv("FLASK_DEBUG", "false").lower() == "true"

# ── Cache TTLs (seconds) ─────────────────────────────────────
CACHE_TTL_QUOTE = 10              # 10 seconds (near real-time)
CACHE_TTL_HISTORY = 60 * 15       # 15 minutes
CACHE_TTL_FUNDAMENTALS = 60 * 60 * 24  # 24 hours
CACHE_TTL_NEWS = 60 * 60          # 1 hour

# Bump this whenever the SHAPE or COMPUTATION of cached fundamentals/financials
# changes, so a deploy invalidates stale entries instead of serving 24h-old data
# computed by the previous code (e.g. the ROE/D&E ×100 fix and the TTM rewrite).
FUNDAMENTALS_CACHE_VERSION = "v2"

# ── PSX Configuration ────────────────────────────────────────
PSX_SUFFIX = ".KA"                # Yahoo Finance suffix for Karachi Stock Exchange
DEFAULT_CURRENCY = "PKR"
PSX_PORTAL_BASE = "https://dps.psx.com.pk"
CACHE_TTL_MARKET_WATCH = 15       # 15 seconds Cache TTL for Market Watch HTML page


# ── Analysis Configuration ────────────────────────────────────
HISTORY_PERIOD_DAILY = "1y"       # 1 year of daily data for technical analysis
HISTORY_PERIOD_HOURLY = "1mo"     # 1 month of hourly data for short-term
DEBATE_ROUNDS = 2                 # Bull vs Bear debate rounds

# ── Database ──────────────────────────────────────────────────
DB_PATH = os.path.join(os.path.dirname(__file__), "data", "advisor_cache.db")
PORTFOLIO_DB_PATH = os.path.join(os.path.dirname(__file__), "data", "portfolio.db")
