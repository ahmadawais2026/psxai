# PSX Investment Advisor — AI-Powered Intelligence

A premium, multi-agent financial advisory platform for the **Pakistan Stock Exchange (PSX)**. The system models a professional wealth management committee, orchestrating specialized AI analyst agents that analyze technicals, fundamentals, sentiment, and risk, conduct a dialectical debate, and formulate a position-aware final recommendation dossier.

---

## 🏛️ Architecture Overview

The system implements key architectural patterns from state-of-the-art agentic finance research:

1. **Arithmetic Separation (FinAgent)**: All indicator calculations (RSI, MACD, Volatility, Max Drawdown, Beta) are performed deterministically in Python (using `ta`, `pandas`, and `numpy`). The AI agents consume the *results* of these calculations to perform strategic reasoning, eliminating arithmetic hallucinations.
2. **Disagree-or-Commit Deliberation (FinCom)**: A structured two-round debate protocol between a Bull Researcher and Bear Researcher to challenge assumptions and prevent sycophancy (conformity bias).
3. **Position-Aware Sizing (FinPos)**: The Portfolio Manager agent dynamically adjusts its advice based on the user's current holdings and concentration (flagging overconcentration >15%).
4. **Layered Memory (FinMem)**: SQLite-backed TTL caching (market quotes: 5min, news: 1hr, fundamentals: 24hr) to respect rate limits and reduce latency.

---

## 🚀 Getting Started

### 1. Prerequisites
- Python 3.8+
- A Google Gemini API Key (get a free key at [Google AI Studio](https://aistudio.google.com/))

### 2. Installation
Clone the repository and install the dependencies:
```bash
pip install -r requirements.txt
```

### 3. Environment Setup
Copy the template `.env.example` to `.env` and fill in your Gemini API key:
```ini
GEMINI_API_KEY=your_actual_gemini_api_key_here
FLASK_DEBUG=true
```

### 4. Running the Application
Start the Flask development server:
```bash
python app.py
```
Then open your browser and navigate to `http://localhost:5000`.

---

## 📊 Curated Ticker Universe
The advisor is pre-configured with a curated database of **~70 major KSE-100 companies** spanning all key sectors of the Pakistan Stock Exchange:
- **Oil & Gas**: OGDC, PPL, PSO, MARI, POL, ATRL, SNGP
- **Banking**: HBL, UBL, MCB, NBP, MEBL, BAFL, BAHL, ABL
- **Cement**: LUCK, DGKC, MLCF, FCCL, KOHC, PIOC, CHCC
- **Fertilizer**: ENGRO, EFERT, FFC, FATIMA, FFBL
- **Power**: HUBC, KEL, KAPCO
- **Technology**: SYS, TRG, AVN, NTS
- **Textile, Pharma, Food, Automobile, Steel, Chemical, Insurance, Packaging**

---

## 🛠️ Multi-Agent Pool Roles

- **Technical Analyst**: Reads pre-computed indicators (RSI, MACD, Bollinger Bands, Crossovers) and identifies key support/resistance zones, stop-losses, and entry signals.
- **Fundamentals Analyst**: Evaluates balance sheets, margins, valuation multiples (P/E, P/B, ROE), and industry competitive advantages (moats).
- **Sentiment Analyst**: Classifies recent stock news headlines, calculates sentiment scores (-100 to +100), and captures market narratives.
- **Risk Analyst**: Computes annualized volatility, maximum drawdowns, and beta coefficients against KSE-100 index (`^KSE`). Suggests max exposure limits.
- **Bull vs Bear Committee**: Debates the stock. The Bull Researcher builds the strongest case for the stock; the Bear Researcher acts as the contrarian pointing out red flags.
- **Portfolio Manager**: The executive decision maker. Synthesizes all analyst reports and debate traces to render a final position-aware verdict (`STRONG BUY` to `STRONG SELL`) with realistic price target ranges.

---

## ⚠️ Disclaimer
*This software is created for educational and research purposes only. It is **not financial advice**. The application does not connect to brokerage protocols or execute live transactions. All trading carries risk. Always consult a licensed fiduciary advisor before investing.*
