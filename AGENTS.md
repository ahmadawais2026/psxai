# AGENTS.md — Operating Guide for Coding Agents

This file orients any AI coding agent (Claude Code, Codex, Gemini CLI, Copilot, etc.)
working on the **PSX Investment Advisor** repo. It captures conventions, commands, and
gotchas that are *not* obvious from the code. For product/architecture detail, read
[`README.md`](README.md) — this file does not duplicate it.

> This is a personal research/educational project (PSX = Pakistan Stock Exchange).
> It is **not financial advice** and is not for public distribution. See the SECP
> disclaimer in the README before changing any user-facing output.

---

## What this project is (30-second version)

A multi-agent financial advisory pipeline for the Pakistan Stock Exchange. Specialist
LLM agents (Technical, Fundamentals, Sentiment, Risk) run in parallel, then a Bull/Bear
debate + synthesis stage feeds a Portfolio Manager that issues a position-aware dossier.
Orchestrated in `agents/orchestrator.py`; served by a Flask app (`app.py`), deployable to
Firebase Cloud Functions via `main.py`.

- **Models**: served through Google Vertex AI (project `aiforpsx`) via Application
  Default Credentials — there are **no per-provider model API keys at runtime**.
- **Data store**: Firestore (company financials + price history) with a SQLite TTL cache
  layer (`data/cache.py`).

---

## Commands

```bash
pip install -r requirements.txt          # install deps
gcloud auth application-default login    # one-time auth for Vertex AI + Firestore
python app.py                            # run locally → http://localhost:5000
firebase deploy                          # deploy hosting + functions

# Data ingestion / maintenance
python backfill_firestore.py --dry-run --tickers ABOT OGDC   # validate without writing
python backfill_firestore.py --all --skip-existing --workers 2 --sleep 5.0
python refresh_market_data.py            # refresh quotes/news + archive hourly bars
```

There is no formal test runner configured; ad-hoc test scripts live at the repo root
(`test_*.py`) and in `scratch/` (gitignored).

---

## Conventions — follow these

1. **API-first for structured data.** Financial statements, OHLCV prices, and ratios come
   from the **AskAnalyst / PSX DPS JSON APIs** — never HTML-scrape structured numbers.
   **Firecrawl** is used *only* for unstructured prose (news article full-text, research
   PDFs) where no clean API exists.
2. **Arithmetic separation.** Numeric indicators (RSI, MACD, Bollinger, drawdown, beta)
   and the DCF are computed in Python (`data/technical_indicators.py`, `data/dcf_engine.py`).
   LLM agents *interpret* pre-computed numbers; they must never do the math themselves.
3. **DCF sources FCFE.** The 2-stage model discounts Free Cash Flow to Equity. FCFE and
   share count are both in **PKR millions** — keep units consistent when touching it.
4. **Rate limits.** AskAnalyst allows ~60 req/min. Ingestion scripts hold a global ~1.0s
   pacing lock; preserve it when adding new fetch paths.
5. **Match the surrounding style.** Vanilla CSS/JS frontend (no framework) in `static/`;
   plain Python modules with no heavy abstractions. Don't introduce new frameworks without
   a reason.

---

## Gotchas — things that have bitten us

- **Firestore project is pinned to `aiforpsx`** in `config.py` (`FIREBASE_PROJECT_ID`).
  `gcloud`'s default project often differs; without the pin the SDK targets the wrong
  project and Firestore returns "database does not exist." Don't remove the override.
- **Validate frontend JS before committing.** A past outage traced to escaped backticks
  (a syntax error) in `static/js/app.js` silently broke the whole UI. Run
  `node --check static/js/app.js` after editing it.
- **Secrets never get committed.** `.env`, Firebase service-account keys (`*-adminsdk-*.json`),
  and large data folders are gitignored — keep it that way. See `.gitignore`.

## Do NOT commit

`.env` · service-account JSON keys · `scratch/` · `__pycache__/` · the SQLite DBs
(`data/*.db`) · large `market_data/` / `company_data/` / `market_intelligence/` folders.
When in doubt, check `.gitignore` before staging.
