/**
 * PSX Advisor — Frontend Application Logic
 * ═══════════════════════════════════════════════════════════════════════
 * Handles:
 * 1. State management (current ticker, recent searches, portfolio list)
 * 2. API queries for search, quote, analysis, and portfolio database
 * 3. Dynamic UI rendering of agent reports, debate committee, and verdict
 * 4. Animated confidence gauges, progress trackers, and numeric transitions
 * 5. Complete modal controls and form validations for portfolio management
 * ═══════════════════════════════════════════════════════════════════
 */

document.addEventListener('DOMContentLoaded', () => {
    // ── STATE ────────────────────────────────────────────────────────
    let currentTicker = '';
    let recentSearches = JSON.parse(localStorage.getItem('psx_recent_searches') || '[]');
    let portfolio = [];

    // ── DOM ELEMENTS ─────────────────────────────────────────────────
    // Header & Navigation
    const btnPortfolioToggle = document.getElementById('btn-portfolio-toggle');
    const btnPortfolioClose = document.getElementById('btn-portfolio-close');
    const portfolioModal = document.getElementById('portfolio-modal');
    const modalBackdrop = document.getElementById('modal-backdrop');

    // Search Section
    const searchInput = document.getElementById('search-input');
    const btnSearch = document.getElementById('btn-search');
    const autocompleteDropdown = document.getElementById('autocomplete-dropdown');
    const recentSearchesContainer = document.getElementById('recent-searches');
    const recentList = document.getElementById('recent-list');

    // Main Analysis Views
    const analysisSection = document.getElementById('analysis-section');
    const quoteCard = document.getElementById('quote-card');
    const agentStatusBar = document.getElementById('agent-status-bar');
    const agentsGrid = document.getElementById('agents-grid');
    const skeletonGrid = document.getElementById('skeleton-grid');
    const debateSection = document.getElementById('debate-section');
    const verdictSection = document.getElementById('verdict-section');
    const errorDisplay = document.getElementById('error-display');
    const btnRetry = document.getElementById('btn-retry');

    // Agent Steps
    const stepTechnical = document.getElementById('step-technical');
    const stepFundamental = document.getElementById('step-fundamental');
    const stepSentiment = document.getElementById('step-sentiment');
    const stepRisk = document.getElementById('step-risk');
    const stepDebate = document.getElementById('step-debate');
    const stepRecommendation = document.getElementById('step-recommendation');

    // Agent Cards
    const badgeTechnicalTrend = document.getElementById('badge-technical-trend');
    const contentTechnical = document.getElementById('content-technical');
    const badgeFundamentalVerdict = document.getElementById('badge-fundamental-verdict');
    const contentFundamental = document.getElementById('content-fundamental');
    const badgeSentimentScore = document.getElementById('badge-sentiment-score');
    const contentSentiment = document.getElementById('content-sentiment');
    const badgeRiskLevel = document.getElementById('badge-risk-level');
    const contentRisk = document.getElementById('content-risk');

    // Debate Side-by-side
    const debateBullContent = document.getElementById('debate-bull-content');
    const debateBearContent = document.getElementById('debate-bear-content');

    // Verdict Elements
    const verdictBadge = document.getElementById('verdict-badge');
    const confidenceCircle = document.getElementById('confidence-circle');
    const confidenceValue = document.getElementById('confidence-value');
    const priceTargetLow = document.getElementById('price-target-low');
    const priceTargetHigh = document.getElementById('price-target-high');
    const catalystsList = document.getElementById('catalysts-list');
    const risksList = document.getElementById('risks-list');
    const verdictPositionAdvice = document.getElementById('verdict-position-advice');
    const positionAdviceText = document.getElementById('position-advice-text');
    const verdictReasoning = document.getElementById('verdict-reasoning');
    const reasoningText = document.getElementById('reasoning-text');

    // Portfolio panel form & list
    const addHoldingForm = document.getElementById('add-holding-form');
    const holdingSymbol = document.getElementById('holding-symbol');
    const holdingShares = document.getElementById('holding-shares');
    const holdingCost = document.getElementById('holding-cost');
    const portfolioSummary = document.getElementById('portfolio-summary');
    const portfolioTotalValue = document.getElementById('portfolio-total-value');
    const portfolioTotalPnl = document.getElementById('portfolio-total-pnl');
    const portfolioTotalPnlPct = document.getElementById('portfolio-total-pnl-pct');
    const holdingsTable = document.getElementById('holdings-table');
    const holdingsTbody = document.getElementById('holdings-tbody');
    const portfolioEmpty = document.getElementById('portfolio-empty');

    // ── INITIALIZATION ───────────────────────────────────────────────
    initApp();

    function initApp() {
        renderRecentSearches();
        fetchPortfolio();
        setupEventListeners();
    }

    // ── EVENT LISTENERS ──────────────────────────────────────────────
    function setupEventListeners() {
        // Portfolio panel visibility
        btnPortfolioToggle.addEventListener('click', openPortfolio);
        btnPortfolioClose.addEventListener('click', closePortfolio);
        modalBackdrop.addEventListener('click', closePortfolio);

        // Search trigger
        btnSearch.addEventListener('click', () => triggerAnalysis(searchInput.value));
        searchInput.addEventListener('keydown', (e) => {
            if (e.key === 'Enter') {
                triggerAnalysis(searchInput.value);
            }
        });

        // Search autocomplete debouncing
        let debounceTimer;
        searchInput.addEventListener('input', () => {
            clearTimeout(debounceTimer);
            const query = searchInput.value.trim();
            if (query.length > 0) {
                debounceTimer = setTimeout(() => handleAutocomplete(query), 200);
            } else {
                hideAutocomplete();
            }
        });

        // Click outside search hides autocomplete
        document.addEventListener('click', (e) => {
            if (!searchInput.contains(e.target) && !autocompleteDropdown.contains(e.target)) {
                hideAutocomplete();
            }
        });

        // Retry button
        btnRetry.addEventListener('click', () => triggerAnalysis(currentTicker));

        // Form submission for holdings
        addHoldingForm.addEventListener('submit', (e) => {
            e.preventDefault();
            handleAddHolding();
        });
    }

    // ── PORTFOLIO FUNCTIONS ──────────────────────────────────────────
    function openPortfolio() {
        portfolioModal.removeAttribute('hidden');
        document.body.style.overflow = 'hidden'; // Lock body scroll
        fetchPortfolio();
    }

    function closePortfolio() {
        portfolioModal.setAttribute('hidden', '');
        document.body.style.overflow = ''; // Unlock scroll
    }

    async function fetchPortfolio() {
        try {
            const res = await fetch('/api/portfolio');
            const data = await res.json();
            if (res.ok) {
                portfolio = data.holdings || [];
                renderPortfolio(data.summary);
            }
        } catch (err) {
            console.error('Failed to fetch portfolio:', err);
        }
    }

    function renderPortfolio(summary) {
        if (!portfolio || portfolio.length === 0) {
            portfolioEmpty.removeAttribute('hidden');
            holdingsTable.setAttribute('hidden', '');
            portfolioSummary.setAttribute('hidden', '');
            return;
        }

        portfolioEmpty.setAttribute('hidden', '');
        holdingsTable.removeAttribute('hidden');
        portfolioSummary.removeAttribute('hidden');

        // Render Summary Cards
        portfolioTotalValue.textContent = formatCurrency(summary.total_value);
        portfolioTotalPnl.textContent = formatCurrency(summary.total_pnl);
        portfolioTotalPnl.className = 'summary-value data-font ' + getPriceColorClass(summary.total_pnl);
        
        portfolioTotalPnlPct.textContent = formatPercent(summary.total_pnl_pct);
        portfolioTotalPnlPct.className = 'summary-value data-font ' + getPriceColorClass(summary.total_pnl);

        // Render Holdings Table
        holdingsTbody.innerHTML = '';
        portfolio.forEach(h => {
            const tr = document.createElement('tr');
            tr.innerHTML = `
                <td class="holding-sym">${h.symbol}</td>
                <td class="text-right data-font">${formatNumberRaw(h.shares)}</td>
                <td class="text-right data-font">${formatCurrency(h.avg_cost, false)}</td>
                <td class="text-right data-font">${formatCurrency(h.current_price, false)}</td>
                <td class="text-right data-font ${getPriceColorClass(h.pnl)}">${formatPercent(h.pnl_pct)}</td>
                <td class="text-center">
                    <button class="btn-delete btn-icon" data-symbol="${h.symbol}" aria-label="Remove ${h.symbol}">
                        <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><polyline points="3 6 5 6 21 6"/><path d="M19 6v14a2 2 0 0 1-2 2H7a2 2 0 0 1-2-2V6m3 0V4a2 2 0 0 1 2-2h4a2 2 0 0 1 2 2v2"/><line x1="10" y1="11" x2="10" y2="17"/><line x1="14" y1="11" x2="14" y2="17"/></svg>
                    </button>
                </td>
            `;

            // Delete position handler
            tr.querySelector('.btn-delete').addEventListener('click', async (e) => {
                const sym = e.currentTarget.getAttribute('data-symbol');
                if (confirm(`Are you sure you want to remove ${sym} from your portfolio?`)) {
                    await removeHolding(sym);
                }
            });

            holdingsTbody.appendChild(tr);
        });
    }

    async function handleAddHolding() {
        const symbol = holdingSymbol.value.trim().toUpperCase();
        const shares = parseFloat(holdingShares.value);
        const cost = parseFloat(holdingCost.value);

        if (!symbol || isNaN(shares) || isNaN(cost)) return;

        try {
            const res = await fetch('/api/portfolio/add', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ symbol, shares, avg_cost: cost })
            });

            if (res.ok) {
                // Clear Form
                holdingSymbol.value = '';
                holdingShares.value = '';
                holdingCost.value = '';
                
                await fetchPortfolio();
                // If the currently analyzed stock matches what we just added, re-run analysis to update position context!
                if (currentTicker === symbol) {
                    triggerAnalysis(symbol);
                }
            } else {
                const err = await res.json();
                alert(err.error || 'Failed to add holding');
            }
        } catch (e) {
            console.error('Error adding holding:', e);
        }
    }

    async function removeHolding(symbol) {
        try {
            const res = await fetch(`/api/portfolio/${symbol}`, {
                method: 'DELETE'
            });

            if (res.ok) {
                await fetchPortfolio();
                if (currentTicker === symbol) {
                    triggerAnalysis(symbol);
                }
            }
        } catch (e) {
            console.error('Error removing holding:', e);
        }
    }

    // ── SEARCH AUTOCOMPLETE ──────────────────────────────────────────
    async function handleAutocomplete(query) {
        try {
            const res = await fetch(`/api/search?q=${encodeURIComponent(query)}`);
            const data = await res.json();
            
            const results = (res.ok && data.results) ? data.results : [];
            renderAutocomplete(results, query);
        } catch (e) {
            console.error('Autocomplete fetch failed:', e);
            renderAutocomplete([], query);
        }
    }

    function renderAutocomplete(results, query) {
        autocompleteDropdown.innerHTML = '';
        
        // Render matching results
        results.forEach(item => {
            const div = document.createElement('div');
            div.className = 'autocomplete-item';
            div.role = 'option';
            div.innerHTML = `
                <span class="ac-symbol">${item.symbol}</span>
                <div class="ac-meta">
                    <span class="ac-name">${item.name}</span>
                    <span class="ac-sector">${item.sector}</span>
                </div>
            `;
            div.addEventListener('click', () => {
                searchInput.value = item.symbol;
                hideAutocomplete();
                triggerAnalysis(item.symbol);
            });
            autocompleteDropdown.appendChild(div);
        });

        // Add custom ticker option
        const cleanedQuery = query.trim().toUpperCase();
        if (cleanedQuery.length > 0) {
            // Check if exact match is already in results to avoid duplication
            const hasExactMatch = results.some(item => item.symbol.toUpperCase() === cleanedQuery);
            if (!hasExactMatch) {
                const customDiv = document.createElement('div');
                customDiv.className = 'autocomplete-item custom-ticker-item';
                customDiv.role = 'option';
                customDiv.innerHTML = `
                    <span class="ac-symbol">${cleanedQuery}</span>
                    <div class="ac-meta">
                        <span class="ac-name">Analyze "${cleanedQuery}" as custom PSX ticker...</span>
                        <span class="ac-sector">External Query</span>
                    </div>
                `;
                customDiv.addEventListener('click', () => {
                    searchInput.value = cleanedQuery;
                    hideAutocomplete();
                    triggerAnalysis(cleanedQuery);
                });
                autocompleteDropdown.appendChild(customDiv);
            }
        }

        if (autocompleteDropdown.children.length > 0) {
            autocompleteDropdown.removeAttribute('hidden');
            searchInput.setAttribute('aria-expanded', 'true');
        } else {
            hideAutocomplete();
        }
    }

    function hideAutocomplete() {
        autocompleteDropdown.setAttribute('hidden', '');
        searchInput.setAttribute('aria-expanded', 'false');
    }

    // ── RECENT SEARCHES ──────────────────────────────────────────────
    function addToRecentSearches(symbol) {
        symbol = symbol.trim().toUpperCase();
        recentSearches = recentSearches.filter(s => s !== symbol);
        recentSearches.unshift(symbol);
        recentSearches = recentSearches.slice(0, 5); // Max 5 items
        localStorage.setItem('psx_recent_searches', JSON.stringify(recentSearches));
        renderRecentSearches();
    }

    function renderRecentSearches() {
        if (recentSearches.length === 0) {
            recentSearchesContainer.setAttribute('hidden', '');
            return;
        }

        recentSearchesContainer.removeAttribute('hidden');
        recentList.innerHTML = '';
        recentSearches.forEach(sym => {
            const button = document.createElement('button');
            button.className = 'recent-item btn btn-ghost btn-sm';
            button.textContent = sym;
            button.addEventListener('click', () => {
                searchInput.value = sym;
                triggerAnalysis(sym);
            });
            recentList.appendChild(button);
        });
    }

    // ── RUN ANALYSIS PIPELINE ────────────────────────────────────────
    async function triggerAnalysis(symbol) {
        symbol = symbol.trim().toUpperCase();
        if (!symbol) return;

        currentTicker = symbol;
        hideAutocomplete();
        addToRecentSearches(symbol);

        // Prep UI for loading
        showLoadingState();

        try {
            // Set first step (technical) to working state
            setStepStatus(stepTechnical, 'working');

            // Simulate progress step updates
            const stepUpdates = [
                { step: stepTechnical, delay: 2000, next: stepFundamental },
                { step: stepFundamental, delay: 4000, next: stepSentiment },
                { step: stepSentiment, delay: 6000, next: stepRisk },
                { step: stepRisk, delay: 8000, next: stepDebate },
                { step: stepDebate, delay: 10000, next: stepRecommendation }
            ];

            stepUpdates.forEach(u => {
                setTimeout(() => {
                    if (isCurrentlyLoading()) {
                        setStepStatus(u.step, 'done');
                        setStepStatus(u.next, 'working');
                    }
                }, u.delay);
            });

            // Send actual POST to backend analyzer
            const res = await fetch('/api/analyze', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({
                    symbol: symbol,
                    include_portfolio: true
                })
            });

            const report = await res.json();

            if (res.ok) {
                // Complete all steps
                [stepTechnical, stepFundamental, stepSentiment, stepRisk, stepDebate, stepRecommendation].forEach(el => {
                    setStepStatus(el, 'done');
                });

                // Display reports
                renderAnalysisReport(report);
            } else {
                throw new Error(report.error || 'Server error during analysis');
            }

        } catch (err) {
            console.error('Analysis failed:', err);
            showErrorState(err.message || 'An unexpected error occurred. Rate limits may have been hit.');
        }
    }

    function isCurrentlyLoading() {
        return !skeletonGrid.hasAttribute('hidden');
    }

    function setStepStatus(stepElement, status) {
        if (!stepElement) return;
        // status can be: pending, working, done
        stepElement.setAttribute('data-status', status);
    }

    function showLoadingState() {
        analysisSection.removeAttribute('hidden');
        quoteCard.setAttribute('hidden', '');
        agentStatusBar.removeAttribute('hidden');
        skeletonGrid.removeAttribute('hidden');
        agentsGrid.setAttribute('hidden', '');
        debateSection.setAttribute('hidden', '');
        verdictSection.setAttribute('hidden', '');
        errorDisplay.setAttribute('hidden', '');

        // Reset step statuses
        [stepTechnical, stepFundamental, stepSentiment, stepRisk, stepDebate, stepRecommendation].forEach(el => {
            setStepStatus(el, 'pending');
        });
    }

    function showErrorState(message) {
        agentStatusBar.setAttribute('hidden', '');
        skeletonGrid.setAttribute('hidden', '');
        errorDisplay.removeAttribute('hidden');
        document.getElementById('error-message').textContent = message;
    }

    // ── RENDER ANALYSIS RESULTS ──────────────────────────────────────
    function renderAnalysisReport(report) {
        // Toggle view flags
        agentStatusBar.setAttribute('hidden', '');
        skeletonGrid.setAttribute('hidden', '');
        quoteCard.removeAttribute('hidden');
        agentsGrid.removeAttribute('hidden');
        debateSection.removeAttribute('hidden');
        verdictSection.removeAttribute('hidden');

        // 1. Render Quote
        renderQuote(report.quote, report.company_name, report.sector);

        // 2. Render Technical Analyst Card
        renderTechnicalCard(report.technical_report);

        // 3. Render Fundamental Analyst Card
        renderFundamentalCard(report.fundamental_report);

        // 4. Render Sentiment Analyst Card
        renderSentimentCard(report.sentiment_report);

        // 5. Render Risk Analyst Card
        renderRiskCard(report.risk_report);

        // 6. Render Bull vs Bear Debate
        renderDebate(report.debate);

        // 7. Render Final Recommendation Verdict
        renderVerdict(report.recommendation);
    }

    // ── VIEW RENDERING HELPERS ───────────────────────────────────────
    
    function renderQuote(quote, name, sector) {
        const pnlClass = getPriceColorClass(quote.change);
        const icon = quote.change >= 0 
            ? `<svg class="quote-trend-icon quote-trend-icon--up" width="24" height="24" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5"><polyline points="23 6 13.5 15.5 8.5 10.5 1 18"/><polyline points="17 6 23 6 23 12"/></svg>`
            : `<svg class="quote-trend-icon quote-trend-icon--down" width="24" height="24" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5"><polyline points="23 18 13.5 8.5 8.5 13.5 1 6"/><polyline points="17 18 23 18 23 12"/></svg>`;

        quoteCard.innerHTML = `
            <div class="quote-header">
                <div>
                    <div class="quote-title-row">
                        <h2 class="quote-symbol data-font">${quote.symbol}</h2>
                        ${icon}
                    </div>
                    <p class="quote-company">${name}</p>
                    <span class="badge quote-sector">${sector}</span>
                </div>
                <div class="quote-price-block">
                    <div class="quote-price data-font">PKR ${formatNumberWithCommas(quote.price)}</div>
                    <div class="quote-change data-font ${pnlClass}">
                        ${formatPercent(quote.change_pct)} (${quote.change >= 0 ? '+' : ''}${quote.change.toFixed(2)})
                    </div>
                </div>
            </div>
            <div class="quote-grid">
                <div class="quote-metric">
                    <span class="quote-metric-label">Volume</span>
                    <span class="quote-metric-value data-font">${formatCompactNumber(quote.volume)}</span>
                </div>
                <div class="quote-metric">
                    <span class="quote-metric-label">Market Cap</span>
                    <span class="quote-metric-value data-font">${formatCompactNumber(quote.market_cap)}</span>
                </div>
                <div class="quote-metric">
                    <span class="quote-metric-label">High / Low (Day)</span>
                    <span class="quote-metric-value data-font">${formatNumberRaw(quote.day_high)} / ${formatNumberRaw(quote.day_low)}</span>
                </div>
                <div class="quote-metric">
                    <span class="quote-metric-label">Open</span>
                    <span class="quote-metric-value data-font">${formatNumberRaw(quote.open)}</span>
                </div>
            </div>
        `;
    }

    function renderTechnicalCard(report) {
        if (report.error) {
            badgeTechnicalTrend.textContent = 'ERROR';
            badgeTechnicalTrend.className = 'badge badge--bearish';
            contentTechnical.innerHTML = `<p class="text-error">${report.summary}</p>`;
            return;
        }

        // Set Trend Badge
        const trend = report.trend.toUpperCase();
        badgeTechnicalTrend.textContent = trend;
        badgeTechnicalTrend.className = `badge badge--${getTrendClass(trend)}`;

        // List key indicator readings
        let signalsHtml = '<ul class="indicator-list">';
        if (report.signals && report.signals.length > 0) {
            report.signals.forEach(sig => {
                signalsHtml += `
                    <li class="indicator-item">
                        <div class="indicator-info">
                            <span class="indicator-name">${sig.indicator}</span>
                            <span class="indicator-reading data-font">${sig.reading}</span>
                        </div>
                        <span class="indicator-interpret">${sig.interpretation}</span>
                    </li>
                `;
            });
        }
        signalsHtml += '</ul>';

        // Add Support/Resistance info
        let levelsHtml = '';
        if (report.key_levels) {
            const supports = report.key_levels.support || [];
            const resistances = report.key_levels.resistance || [];
            levelsHtml = `
                <div class="key-levels-grid">
                    <div class="key-level-col">
                        <span class="key-level-title">Support</span>
                        <div class="key-level-values data-font">
                            ${supports.map(s => `<span>${formatNumberRaw(s)}</span>`).join('') || '—'}
                        </div>
                    </div>
                    <div class="key-level-col">
                        <span class="key-level-title">Resistance</span>
                        <div class="key-level-values data-font">
                            ${resistances.map(r => `<span>${formatNumberRaw(r)}</span>`).join('') || '—'}
                        </div>
                    </div>
                </div>
            `;
        }

        contentTechnical.innerHTML = `
            <p class="agent-summary-text">${report.summary}</p>
            ${levelsHtml}
            <h4 class="indicator-section-title">Technical Oscillators &amp; Averages</h4>
            ${signalsHtml}
            <div class="confidence-footer">
                <span class="confidence-label">Analyst Confidence:</span>
                <span class="confidence-score data-font">${report.confidence}/10</span>
            </div>
        `;
    }

    function renderFundamentalCard(report) {
        if (report.error) {
            badgeFundamentalVerdict.textContent = 'ERROR';
            badgeFundamentalVerdict.className = 'badge badge--bearish';
            contentFundamental.innerHTML = `<p class="text-error">${report.summary}</p>`;
            return;
        }

        // Set Verdict Badge
        const verdict = report.valuation_verdict.replace('_', ' ').toUpperCase();
        badgeFundamentalVerdict.textContent = verdict;
        badgeFundamentalVerdict.className = `badge badge--${getValuationClass(report.valuation_verdict)}`;

        // List strengths / concerns
        let detailsHtml = '<div class="fund-details-grid">';
        
        if (report.strengths && report.strengths.length > 0) {
            detailsHtml += `
                <div class="fund-col">
                    <span class="fund-col-title fund-col-title--strength">Strengths</span>
                    <ul class="bullet-list">
                        ${report.strengths.map(s => `<li>${s}</li>`).join('')}
                    </ul>
                </div>
            `;
        }
        
        if (report.concerns && report.concerns.length > 0) {
            detailsHtml += `
                <div class="fund-col">
                    <span class="fund-col-title fund-col-title--concern">Risks &amp; Concerns</span>
                    <ul class="bullet-list">
                        ${report.concerns.map(c => `<li>${c}</li>`).join('')}
                    </ul>
                </div>
            `;
        }
        detailsHtml += '</div>';

        // Valuation Range
        let rangeHtml = '';
        if (report.fair_value_range) {
            rangeHtml = `
                <div class="valuation-range-box">
                    <span class="range-title">Estimated Fair Value Range</span>
                    <span class="range-value data-font">PKR ${formatNumberRaw(report.fair_value_range.low)} - PKR ${formatNumberRaw(report.fair_value_range.high)}</span>
                </div>
            `;
        }

        contentFundamental.innerHTML = `
            <p class="agent-summary-text">${report.summary}</p>
            ${rangeHtml}
            ${detailsHtml}
            <div class="confidence-footer">
                <span class="confidence-label">Analyst Confidence:</span>
                <span class="confidence-score data-font">${report.confidence}/10</span>
            </div>
        `;
    }

    function renderSentimentCard(report) {
        const sentiment = report.overall_sentiment.toUpperCase();
        badgeSentimentScore.textContent = `${sentiment} (${report.sentiment_score > 0 ? '+' : ''}${report.sentiment_score})`;
        badgeSentimentScore.className = `badge badge--${getSentimentClass(report.overall_sentiment)}`;

        let narrativesHtml = '';
        if (report.key_narratives && report.key_narratives.length > 0) {
            narrativesHtml += `
                <h4 class="indicator-section-title">Key Media Narratives</h4>
                <ul class="bullet-list">
                    ${report.key_narratives.map(n => `<li>${n}</li>`).join('')}
                </ul>
            `;
        }

        let catalystsHtml = '<div class="fund-details-grid">';
        if (report.catalysts_positive && report.catalysts_positive.length > 0) {
            catalystsHtml += `
                <div class="fund-col">
                    <span class="fund-col-title fund-col-title--strength">Positive Anchors</span>
                    <ul class="bullet-list">
                        ${report.catalysts_positive.map(c => `<li>${c}</li>`).join('')}
                    </ul>
                </div>
            `;
        }
        if (report.catalysts_negative && report.catalysts_negative.length > 0) {
            catalystsHtml += `
                <div class="fund-col">
                    <span class="fund-col-title fund-col-title--concern">Negative Anchors</span>
                    <ul class="bullet-list">
                        ${report.catalysts_negative.map(c => `<li>${c}</li>`).join('')}
                    </ul>
                </div>
            `;
        }
        catalystsHtml += '</div>';

        contentSentiment.innerHTML = `
            <p class="agent-summary-text">${report.summary}</p>
            ${narrativesHtml}
            ${catalystsHtml}
            <div class="confidence-footer">
                <span class="confidence-label">Analyst Confidence:</span>
                <span class="confidence-score data-font">${report.confidence}/10</span>
            </div>
        `;
    }

    function renderRiskCard(report) {
        if (report.error) {
            badgeRiskLevel.textContent = 'ERROR';
            badgeRiskLevel.className = 'badge badge--bearish';
            contentRisk.innerHTML = `<p class="text-error">${report.summary}</p>`;
            return;
        }

        const risk = report.risk_level.toUpperCase();
        badgeRiskLevel.textContent = `RISK: ${risk}`;
        badgeRiskLevel.className = `badge badge--${getRiskClass(report.risk_level)}`;

        // List specific factors
        let factorsHtml = '<ul class="indicator-list">';
        if (report.risk_factors && report.risk_factors.length > 0) {
            report.risk_factors.forEach(rf => {
                factorsHtml += `
                    <li class="indicator-item">
                        <div class="indicator-info">
                            <span class="indicator-name">${rf.factor}</span>
                            <span class="badge badge--${getSeverityClass(rf.severity)}">${rf.severity.toUpperCase()}</span>
                        </div>
                        <span class="indicator-interpret">${rf.detail}</span>
                    </li>
                `;
            });
        }
        factorsHtml += '</ul>';

        // Sizing guidelines
        const maxPos = report.max_position_pct ? `${report.max_position_pct}%` : 'N/A';
        const stopLoss = report.stop_loss_pct ? `${report.stop_loss_pct}%` : 'N/A';

        contentRisk.innerHTML = `
            <p class="agent-summary-text">${report.summary}</p>
            <div class="risk-limits-row">
                <div class="risk-limit-box">
                    <span class="limit-label">Max Position Size</span>
                    <span class="limit-value data-font">${maxPos}</span>
                </div>
                <div class="risk-limit-box">
                    <span class="limit-label">Recommended Stop-Loss</span>
                    <span class="limit-value data-font">${stopLoss}</span>
                </div>
            </div>
            <h4 class="indicator-section-title">Risk Exposure Matrix</h4>
            ${factorsHtml}
            <div class="confidence-footer">
                <span class="confidence-label">Analyst Confidence:</span>
                <span class="confidence-score data-font">${report.confidence}/10</span>
            </div>
        `;
    }

    function renderDebate(debate) {
        // Bull Thesis & Arguments
        let bullHtml = `<p class="debate-thesis"><strong>Thesis:</strong> ${debate.bull_thesis}</p>`;
        if (debate.bull_arguments && debate.bull_arguments.length > 0) {
            bullHtml += '<ul class="debate-points">';
            debate.bull_arguments.forEach(arg => {
                bullHtml += `
                    <li>
                        <span class="debate-point-title">${arg.point}</span>
                        <p class="debate-point-desc">${arg.evidence}</p>
                    </li>
                `;
            });
            bullHtml += '</ul>';
        }

        // Bear Thesis & Arguments
        let bearHtml = `<p class="debate-thesis"><strong>Thesis:</strong> ${debate.bear_thesis}</p>`;
        if (debate.bear_arguments && debate.bear_arguments.length > 0) {
            bearHtml += '<ul class="debate-points">';
            debate.bear_arguments.forEach(arg => {
                bearHtml += `
                    <li>
                        <span class="debate-point-title">${arg.point}</span>
                        <p class="debate-point-desc">${arg.evidence}</p>
                    </li>
                `;
            });
            bearHtml += '</ul>';
        }

        debateBullContent.innerHTML = bullHtml;
        debateBearContent.innerHTML = bearHtml;
    }

    function renderVerdict(rec) {
        // 1. Recommendation Badge
        const recommendation = rec.recommendation.toUpperCase();
        verdictBadge.textContent = recommendation;
        verdictBadge.className = `verdict-badge verdict-badge--${getVerdictClass(recommendation)}`;

        // Set card glowing boundary styling
        const verdictCard = document.getElementById('verdict-card');
        verdictCard.className = `verdict-card glass-card verdict-card--glow-${getVerdictClass(recommendation)} animate-in`;

        // 2. Circular Confidence meter
        // Perimeter = 2 * PI * r = 2 * 3.14159 * 42 = 263.89
        const perimeter = 263.89;
        const confidence = rec.confidence || 0;
        const offset = perimeter - (confidence / 10) * perimeter;
        
        confidenceCircle.style.strokeDasharray = perimeter;
        confidenceCircle.style.strokeDashoffset = offset;
        confidenceValue.textContent = `${confidence}/10`;

        // 3. Price Target Display
        priceTargetLow.textContent = formatCurrency(rec.price_target_low);
        priceTargetHigh.textContent = formatCurrency(rec.price_target_high);

        // 4. Catalysts and Risks Bullet list
        catalystsList.innerHTML = (rec.catalysts || []).map(c => `<li>${c}</li>`).join('') || '<li>None identified</li>';
        risksList.innerHTML = (rec.risks || []).map(r => `<li>${r}</li>`).join('') || '<li>None identified</li>';

        // 5. Position Specific Advice
        if (rec.position_advice) {
            verdictPositionAdvice.removeAttribute('hidden');
            positionAdviceText.textContent = rec.position_advice;
        } else {
            verdictPositionAdvice.setAttribute('hidden', '');
        }

        // 6. Summary / Executive Reasoning
        if (rec.summary) {
            verdictReasoning.removeAttribute('hidden');
            reasoningText.textContent = rec.summary;
        } else {
            verdictReasoning.setAttribute('hidden', '');
        }
    }

    // ── UTILITIES & HELPERS ──────────────────────────────────────────
    function formatCurrency(val, includeCurrency = true) {
        if (val === undefined || val === null || isNaN(val)) return '—';
        const formatted = Number(val).toLocaleString('en-PK', {
            minimumFractionDigits: 2,
            maximumFractionDigits: 2
        });
        return includeCurrency ? `PKR ${formatted}` : formatted;
    }

    function formatNumberRaw(val) {
        if (val === undefined || val === null || isNaN(val)) return '—';
        return Number(val).toLocaleString('en-US', {
            minimumFractionDigits: 2,
            maximumFractionDigits: 2
        });
    }

    function formatNumberWithCommas(val) {
        if (val === undefined || val === null || isNaN(val)) return '—';
        return Number(val).toLocaleString('en-US', {
            maximumFractionDigits: 2
        });
    }

    function formatNumberRawInt(val) {
        if (val === undefined || val === null || isNaN(val)) return '—';
        return Number(val).toLocaleString('en-US', {
            maximumFractionDigits: 0
        });
    }

    function formatNumberRaw(val) {
        if (val === undefined || val === null || isNaN(val)) return '—';
        return Number(val).toFixed(2);
    }

    function formatNumberRaw(val) {
        if (val === undefined || val === null || isNaN(val)) return '—';
        return Number(val).toLocaleString('en-US', {
            minimumFractionDigits: 2,
            maximumFractionDigits: 2
        });
    }

    function formatPercent(val) {
        if (val === undefined || val === null || isNaN(val)) return '0.00%';
        const sign = val >= 0 ? '+' : '';
        return `${sign}${val.toFixed(2)}%`;
    }

    function formatCompactNumber(val) {
        if (val === undefined || val === null || isNaN(val)) return '—';
        const num = Number(val);
        if (num >= 1.0e12) return (num / 1.0e12).toFixed(2) + 'T';
        if (num >= 1.0e9) return (num / 1.0e9).toFixed(2) + 'B';
        if (num >= 1.0e6) return (num / 1.0e6).toFixed(2) + 'M';
        if (num >= 1.0e3) return (num / 1.0e3).toFixed(2) + 'K';
        return num.toString();
    }

    function getPriceColorClass(changeVal) {
        if (changeVal > 0) return 'text-success';
        if (changeVal < 0) return 'text-danger';
        return 'text-neutral';
    }

    // Color mapper helpers for badges
    function getTrendClass(trend) {
        trend = trend.toLowerCase();
        if (trend === 'bullish') return 'bullish';
        if (trend === 'bearish') return 'bearish';
        return 'neutral';
    }

    function getValuationClass(verdict) {
        verdict = verdict.toLowerCase();
        if (verdict === 'undervalued') return 'bullish';
        if (verdict === 'overvalued') return 'bearish';
        return 'neutral';
    }

    function getSentimentClass(sent) {
        sent = sent.toLowerCase();
        if (sent === 'bullish') return 'bullish';
        if (sent === 'bearish') return 'bearish';
        return 'neutral';
    }

    function getRiskClass(risk) {
        risk = risk.toLowerCase();
        if (risk === 'low') return 'bullish';
        if (risk === 'medium') return 'neutral';
        return 'bearish';
    }

    function getSeverityClass(sev) {
        sev = sev.toLowerCase();
        if (sev === 'low') return 'bullish';
        if (sev === 'medium') return 'neutral';
        return 'bearish';
    }

    function getVerdictClass(rec) {
        rec = rec.toUpperCase();
        if (rec === 'STRONG BUY') return 'strong-buy';
        if (rec === 'BUY') return 'buy';
        if (rec === 'ACCUMULATE') return 'accumulate';
        if (rec === 'HOLD') return 'hold';
        if (rec === 'TRIM') return 'trim';
        if (rec === 'SELL') return 'sell';
        if (rec === 'STRONG SELL') return 'strong-sell';
        return 'neutral';
    }
});
