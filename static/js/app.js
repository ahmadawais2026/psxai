/**
 * PSX Advisor — Frontend Application Logic
 * ═══════════════════════════════════════════════════════════════════════
 * Handles:
 * 1. Theme management (Light/Dark toggling and persistence)
 * 2. Firebase Initialization (Auto-configuration from Hosting backend)
 * 3. Firebase Authentication Compat (Login, Signup, Google OAuth, Sign-out)
 * 4. State management (current ticker, recent searches, user portfolio)
 * 5. Portfolio Dashboard UI (canvas allocation donut, collapsible forms, table sorting)
 * 6. Flask API endpoints queries with JWT Authorization Bearer headers
 * 7. Dynamic UI rendering of agent reports, debate committee, and verdict
 * ═══════════════════════════════════════════════════════════════════
 */

document.addEventListener('DOMContentLoaded', async () => {
    // ── API BASE URL ─────────────────────────────────────────────────
    const API_BASE = window.location.hostname === 'localhost' || window.location.hostname === '127.0.0.1'
        ? ''
        : 'https://api-gu36tcyboa-uc.a.run.app';

    // ── STATE ────────────────────────────────────────────────────────
    let currentTicker = '';
    let recentSearches = JSON.parse(localStorage.getItem('psx_recent_searches') || '[]');
    let portfolio = [];
    let currentTheme = 'dark';
    let currentReport = null;   // stores the full analysis report for PDF download

    // ── DOM ELEMENTS ─────────────────────────────────────────────────
    // Theme Toggle
    const btnThemeToggle = document.getElementById('btn-theme-toggle');

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

    // Report Download
    const reportDownloadBar = document.getElementById('report-download-bar');
    const btnDownloadReport  = document.getElementById('btn-download-report');
    const btnDownloadText    = document.getElementById('btn-download-report-text');

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

    // Portfolio Dashboard UI
    const portfolioTotalValue = document.getElementById('portfolio-total-value');
    const portfolioTotalCost = document.getElementById('portfolio-total-cost');
    const portfolioTotalPnl = document.getElementById('portfolio-total-pnl');
    const portfolioTotalPnlPct = document.getElementById('portfolio-total-pnl-pct');
    const portfolioTotalCount = document.getElementById('portfolio-total-count');
    
    // Allocation Visualization
    const portfolioVisualsContainer = document.getElementById('portfolio-visuals-container');
    const allocationCanvas = document.getElementById('allocation-canvas');
    const allocationLegend = document.getElementById('allocation-legend');

    // Collapsible Form
    const btnToggleAddForm = document.getElementById('btn-toggle-add-form');
    const collapsibleFormBody = document.getElementById('collapsible-form-body');

    // Add Position form & inputs
    const addHoldingForm = document.getElementById('add-holding-form');
    const holdingSymbol = document.getElementById('holding-symbol');
    const holdingShares = document.getElementById('holding-shares');
    const holdingCost = document.getElementById('holding-cost');
    
    // Holdings list UI elements
    const portfolioEmpty = document.getElementById('portfolio-empty');
    const holdingsListWrapper = document.getElementById('holdings-list-wrapper');
    const holdingsSortSelect = document.getElementById('holdings-sort');

    // Auth Modal Elements
    const authModal = document.getElementById('auth-modal');
    const authBackdrop = document.getElementById('auth-backdrop');
    const btnAuthClose = document.getElementById('btn-auth-close');
    const tabLogin = document.getElementById('tab-login');
    const tabSignup = document.getElementById('tab-signup');
    const authForm = document.getElementById('auth-form');
    const authEmail = document.getElementById('auth-email');
    const authPassword = document.getElementById('auth-password');
    const groupConfirmPassword = document.getElementById('group-confirm-password');
    const authConfirmPassword = document.getElementById('auth-confirm-password');
    const authError = document.getElementById('auth-error');
    const btnAuthSubmit = document.getElementById('btn-auth-submit');
    const btnGoogleAuth = document.getElementById('btn-google-auth');
    const btnAuthSignout = document.getElementById('btn-auth-signout');

    // Auth Form State
    let authMode = 'login'; // 'login' or 'signup'

    // Preset Colors for Donut Allocation Slices
    const ALLOCATION_COLORS = [
        '#10b981', // Emerald
        '#06b6d4', // Cyan
        '#8b5cf6', // Violet
        '#f43f5e', // Rose
        '#f59e0b', // Amber
        '#3b82f6', // Blue
        '#ec4899', // Pink
        '#14b8a6', // Teal
        '#6366f1', // Indigo
        '#a855f7'  // Purple
    ];

    // ── INITIALIZATION ───────────────────────────────────────────────
    await initApp();

    async function initApp() {
        initTheme();
        await initFirebase();
        setupEventListeners();
        renderRecentSearches();
    }

    // ── THEME FUNCTIONS ──────────────────────────────────────────────
    function initTheme() {
        const storedTheme = localStorage.getItem('psx_theme');
        if (storedTheme) {
            currentTheme = storedTheme;
        } else {
            const systemPrefersDark = window.matchMedia('(prefers-color-scheme: dark)').matches;
            currentTheme = systemPrefersDark ? 'dark' : 'light';
        }
        document.documentElement.setAttribute('data-theme', currentTheme);
    }

    function toggleTheme() {
        currentTheme = currentTheme === 'dark' ? 'light' : 'dark';
        document.documentElement.setAttribute('data-theme', currentTheme);
        localStorage.setItem('psx_theme', currentTheme);
    }

    // ── FIREBASE INITIALIZATION ──────────────────────────────────────
    async function initFirebase() {
        try {
            const response = await fetch('/__/firebase/init.json');
            if (response.ok) {
                const config = await response.json();
                firebase.initializeApp(config);
                console.log('Firebase Compat initialized successfully from /__/firebase/init.json');
            } else {
                throw new Error('Host config not found');
            }
        } catch (e) {
            console.warn('Could not load auto-config, falling back to local emulator default config:', e);
            // Fallback for local emulator environment or standalone testing
            firebase.initializeApp({
                apiKey: "mock-api-key",
                authDomain: "stocks-psx.firebaseapp.com",
                projectId: "stocks-psx",
                storageBucket: "stocks-psx.appspot.com",
                messagingSenderId: "12345678",
                appId: "1:12345678:web:12345678"
            });
        }

        // Initialize Firestore client settings
        const db = firebase.firestore();
        // Point to emulator if hosted locally with emulator configurations
        if (window.location.hostname === 'localhost' || window.location.hostname === '127.0.0.1') {
            try {
                // If emulator is active on these standard ports, connect
                db.useEmulator('localhost', 8080);
                firebase.auth().useEmulator('http://localhost:9099/');
                console.log('Connecting client to local Firebase Emulators (Auth on 9099, Firestore on 8080)');
            } catch (emuErr) {
                // Ignore if already initialized
            }
        }

        // Listen for authentication changes
        firebase.auth().onAuthStateChanged(async (user) => {
            if (user) {
                console.log('User signed in:', user.email);
                btnAuthSignout.style.display = 'block';
                btnPortfolioToggle.innerHTML = `
                    <svg width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M21 12V7H5a2 2 0 0 1 0-4h14v4"/><path d="M3 5v14a2 2 0 0 0 2 2h16v-5"/><path d="M18 12a2 2 0 0 0 0 4h4v-4Z"/></svg>
                    <span>Dashboard</span>
                `;
                closeAuthModal();
                await fetchPortfolio();
            } else {
                console.log('User signed out');
                btnAuthSignout.style.display = 'none';
                btnPortfolioToggle.innerHTML = `
                    <svg width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M21 12V7H5a2 2 0 0 1 0-4h14v4"/><path d="M3 5v14a2 2 0 0 0 2 2h16v-5"/><path d="M18 12a2 2 0 0 0 0 4h4v-4Z"/></svg>
                    <span>Portfolio</span>
                `;
                portfolio = [];
                renderPortfolioEmpty();
            }
        });
    }

    // ── EVENT LISTENERS ──────────────────────────────────────────────
    function setupEventListeners() {
        // Theme toggler
        btnThemeToggle.addEventListener('click', toggleTheme);

        // Portfolio panel visibility
        btnPortfolioToggle.addEventListener('click', () => {
            const user = firebase.auth().currentUser;
            if (user) {
                openPortfolio();
            } else {
                openAuthModal();
            }
        });
        btnPortfolioClose.addEventListener('click', closePortfolio);
        modalBackdrop.addEventListener('click', closePortfolio);

        // Auth Modal controls
        btnAuthClose.addEventListener('click', closeAuthModal);
        authBackdrop.addEventListener('click', closeAuthModal);
        tabLogin.addEventListener('click', () => switchAuthMode('login'));
        tabSignup.addEventListener('click', () => switchAuthMode('signup'));
        authForm.addEventListener('submit', handleAuthSubmit);
        btnGoogleAuth.addEventListener('click', handleGoogleAuth);
        btnAuthSignout.addEventListener('click', handleSignout);

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

        // Collapsible Add Holding Form
        btnToggleAddForm.addEventListener('click', toggleAddHoldingForm);

        // Form submission for holdings
        addHoldingForm.addEventListener('submit', (e) => {
            e.preventDefault();
            handleAddHolding();
        });

        // Holdings Table Sorting
        holdingsSortSelect.addEventListener('change', () => {
            sortAndRenderHoldings();
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

    function toggleAddHoldingForm() {
        const isCollapsed = btnToggleAddForm.classList.contains('collapsed');
        if (isCollapsed) {
            btnToggleAddForm.classList.remove('collapsed');
            btnToggleAddForm.setAttribute('aria-expanded', 'true');
            collapsibleFormBody.removeAttribute('hidden');
        } else {
            btnToggleAddForm.classList.add('collapsed');
            btnToggleAddForm.setAttribute('aria-expanded', 'false');
            collapsibleFormBody.setAttribute('hidden', '');
        }
    }

    async function fetchPortfolio() {
        const user = firebase.auth().currentUser;
        if (!user) return;

        try {
            const token = await user.getIdToken();
            const res = await fetch(`${API_BASE}/api/portfolio`, {
                headers: {
                    'Authorization': `Bearer ${token}`
                }
            });
            const data = await res.json();
            if (res.ok) {
                portfolio = data.holdings || [];
                renderPortfolio(data.summary);
            } else {
                console.error('Portfolio response error:', data.error);
            }
        } catch (err) {
            console.error('Failed to fetch portfolio:', err);
        }
    }

    function renderPortfolioEmpty() {
        portfolioTotalValue.textContent = 'PKR 0.00';
        portfolioTotalCost.textContent = 'PKR 0.00';
        portfolioTotalPnl.textContent = 'PKR 0.00';
        portfolioTotalPnlPct.textContent = '0.00%';
        portfolioTotalPnlPct.className = 'stat-badge badge badge--neutral';
        portfolioTotalCount.textContent = '0 Tickers';
        
        portfolioEmpty.removeAttribute('hidden');
        holdingsListWrapper.setAttribute('hidden', '');
        portfolioVisualsContainer.setAttribute('hidden', '');
    }

    function renderPortfolio(summary) {
        if (!portfolio || portfolio.length === 0) {
            renderPortfolioEmpty();
            return;
        }

        portfolioEmpty.setAttribute('hidden', '');
        holdingsListWrapper.removeAttribute('hidden');

        // Update Stat Cards
        portfolioTotalValue.textContent = formatCurrency(summary.total_value);
        portfolioTotalCost.textContent = formatCurrency(summary.total_cost);
        
        const pnlColorClass = getPriceColorClass(summary.total_pnl);
        portfolioTotalPnl.textContent = formatCurrency(summary.total_pnl);
        portfolioTotalPnl.className = 'stat-value data-font ' + pnlColorClass;

        portfolioTotalPnlPct.textContent = formatPercent(summary.total_pnl_pct);
        portfolioTotalPnlPct.className = `stat-badge badge badge--${getTrendClass(summary.total_pnl >= 0 ? 'bullish' : 'bearish')}`;

        portfolioTotalCount.textContent = `${portfolio.length} Ticker${portfolio.length > 1 ? 's' : ''}`;

        // Draw Donut Chart Slices and render Legend
        renderAllocationChart();

        // Render sorted Holdings Table
        sortAndRenderHoldings();
    }

    function renderAllocationChart() {
        if (!portfolio || portfolio.length === 0) {
            portfolioVisualsContainer.setAttribute('hidden', '');
            return;
        }
        portfolioVisualsContainer.removeAttribute('hidden');

        // Map values with color assignments
        const chartData = portfolio.map((h, i) => ({
            label: h.symbol,
            value: h.current_value,
            color: ALLOCATION_COLORS[i % ALLOCATION_COLORS.length]
        }));

        // Draw donut on canvas
        drawDonutChart(allocationCanvas, chartData);

        // Build legend with percentages
        const totalValue = chartData.reduce((sum, d) => sum + d.value, 0);
        allocationLegend.innerHTML = '';
        chartData.forEach(item => {
            const pct = totalValue > 0 ? (item.value / totalValue * 100) : 0;
            const legendRow = document.createElement('div');
            legendRow.className = 'legend-item';
            legendRow.innerHTML = `
                <div class="legend-key">
                    <span class="legend-color" style="background-color: ${item.color};"></span>
                    <span class="legend-sym">${item.label}</span>
                </div>
                <span class="legend-val data-font">${pct.toFixed(1)}%</span>
            `;
            allocationLegend.appendChild(legendRow);
        });
    }

    function drawDonutChart(canvas, data) {
        const ctx = canvas.getContext('2d');
        // Handle high DPI retina display clarity
        const dpr = window.devicePixelRatio || 1;
        const rect = canvas.getBoundingClientRect();
        canvas.width = rect.width * dpr;
        canvas.height = rect.height * dpr;
        ctx.scale(dpr, dpr);

        ctx.clearRect(0, 0, rect.width, rect.height);
        
        const total = data.reduce((sum, item) => sum + item.value, 0);
        if (total === 0) return;
        
        const centerX = rect.width / 2;
        const centerY = rect.height / 2;
        const radius = Math.min(centerX, centerY) - 6;
        const innerRadius = radius * 0.65;
        
        let startAngle = -Math.PI / 2; // Start drawing from 12 o'clock top
        
        data.forEach(item => {
            const sliceAngle = (item.value / total) * 2 * Math.PI;
            if (sliceAngle <= 0) return;
            
            // Draw outer arc, then inner arc counter-clockwise to form ring slice
            ctx.beginPath();
            ctx.arc(centerX, centerY, radius, startAngle, startAngle + sliceAngle);
            ctx.arc(centerX, centerY, innerRadius, startAngle + sliceAngle, startAngle, true);
            ctx.closePath();
            
            ctx.fillStyle = item.color;
            ctx.fill();
            
            startAngle += sliceAngle;
        });
    }

    function sortAndRenderHoldings() {
        const sortBy = holdingsSortSelect.value;
        
        // Sort portfolio array copy
        const sorted = [...portfolio].sort((a, b) => {
            if (sortBy === 'value') {
                return b.current_value - a.current_value;
            } else if (sortBy === 'pnl') {
                return b.pnl - a.pnl;
            } else if (sortBy === 'shares') {
                return b.shares - a.shares;
            } else if (sortBy === 'symbol') {
                return a.symbol.localeCompare(b.symbol);
            }
            return 0;
        });

        // Generate table HTML contents
        holdingsListWrapper.innerHTML = `
            <table class="holdings-table">
                <thead>
                    <tr>
                        <th>Symbol</th>
                        <th class="text-right">Shares</th>
                        <th class="text-right">Avg Cost</th>
                        <th class="text-right">Price</th>
                        <th class="text-right">Returns</th>
                        <th class="text-center">Action</th>
                    </tr>
                </thead>
                <tbody id="holdings-tbody">
                </tbody>
            </table>
        `;

        const holdingsTbody = document.getElementById('holdings-tbody');

        sorted.forEach(h => {
            const tr = document.createElement('tr');
            tr.className = 'holding-row';
            tr.innerHTML = `
                <td class="holding-sym">${h.symbol}</td>
                <td class="text-right data-font">${formatNumberRawInt(h.shares)}</td>
                <td class="text-right data-font">${formatCurrency(h.avg_cost, false)}</td>
                <td class="text-right data-font">${formatCurrency(h.current_price, false)}</td>
                <td class="text-right data-font ${getPriceColorClass(h.pnl)}">
                    ${formatPercent(h.pnl_pct)}
                </td>
                <td class="text-center">
                    <button class="btn-delete" data-symbol="${h.symbol}" title="Delete ${h.symbol} position">
                        <svg width="15" height="15" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5" stroke-linecap="round" stroke-linejoin="round"><polyline points="3 6 5 6 21 6"/><path d="M19 6v14a2 2 0 0 1-2 2H7a2 2 0 0 1-2-2V6m3 0V4a2 2 0 0 1 2-2h4a2 2 0 0 1 2 2v2"/><line x1="10" y1="11" x2="10" y2="17"/><line x1="14" y1="11" x2="14" y2="17"/></svg>
                    </button>
                </td>
            `;

            // Delete action handler
            tr.querySelector('.btn-delete').addEventListener('click', async (e) => {
                const sym = e.currentTarget.getAttribute('data-symbol');
                if (confirm(`Remove all positions of ${sym} from your portfolio?`)) {
                    await removeHolding(sym);
                }
            });

            // Allow clicking symbol row cell to load analysis directly
            tr.querySelector('.holding-sym').addEventListener('click', () => {
                closePortfolio();
                searchInput.value = h.symbol;
                triggerAnalysis(h.symbol);
            });

            holdingsTbody.appendChild(tr);
        });
    }

    async function handleAddHolding() {
        const user = firebase.auth().currentUser;
        if (!user) return;

        const symbol = holdingSymbol.value.trim().toUpperCase();
        const shares = parseFloat(holdingShares.value);
        const cost = parseFloat(holdingCost.value);

        if (!symbol || isNaN(shares) || isNaN(cost) || shares <= 0 || cost <= 0) return;

        try {
            const token = await user.getIdToken();
            const res = await fetch(`${API_BASE}/api/portfolio/add`, {
                method: 'POST',
                headers: { 
                    'Content-Type': 'application/json',
                    'Authorization': `Bearer ${token}`
                },
                body: JSON.stringify({ symbol, shares, avg_cost: cost })
            });

            if (res.ok) {
                // Reset Fields
                holdingSymbol.value = '';
                holdingShares.value = '';
                holdingCost.value = '';
                
                // Hide collapsible
                toggleAddHoldingForm();
                
                await fetchPortfolio();

                // If currently viewing details for the added ticker, trigger re-analysis to overlay user holdings insights
                if (currentTicker === symbol) {
                    triggerAnalysis(symbol);
                }
            } else {
                const err = await res.json();
                alert(err.error || 'Failed to add position');
            }
        } catch (e) {
            console.error('Error adding holding:', e);
        }
    }

    async function removeHolding(symbol) {
        const user = firebase.auth().currentUser;
        if (!user) return;

        try {
            const token = await user.getIdToken();
            const res = await fetch(`${API_BASE}/api/portfolio/${symbol}`, {
                method: 'DELETE',
                headers: {
                    'Authorization': `Bearer ${token}`
                }
            });

            if (res.ok) {
                await fetchPortfolio();
                
                // Reload current analyzer if looking at deleted ticker to clear ownership context advice
                if (currentTicker === symbol) {
                    triggerAnalysis(symbol);
                }
            } else {
                const err = await res.json();
                console.error('Failed to remove position:', err.error);
            }
        } catch (e) {
            console.error('Error removing holding:', e);
        }
    }

    // ── AUTHENTICATION FUNCTIONS ─────────────────────────────────────
    function openAuthModal() {
        authModal.removeAttribute('hidden');
        document.body.style.overflow = 'hidden';
        switchAuthMode('login');
    }

    function closeAuthModal() {
        authModal.setAttribute('hidden', '');
        document.body.style.overflow = '';
        clearAuthInputs();
    }

    function switchAuthMode(mode) {
        authMode = mode;
        clearAuthError();

        if (mode === 'login') {
            tabLogin.classList.add('active');
            tabLogin.setAttribute('aria-selected', 'true');
            tabSignup.classList.remove('active');
            tabSignup.setAttribute('aria-selected', 'false');
            groupConfirmPassword.style.display = 'none';
            authConfirmPassword.removeAttribute('required');
            btnAuthSubmit.querySelector('.auth-submit-text').textContent = 'Sign In';
        } else {
            tabSignup.classList.add('active');
            tabSignup.setAttribute('aria-selected', 'true');
            tabLogin.classList.remove('active');
            tabLogin.setAttribute('aria-selected', 'false');
            groupConfirmPassword.style.display = 'block';
            authConfirmPassword.setAttribute('required', '');
            btnAuthSubmit.querySelector('.auth-submit-text').textContent = 'Register';
        }
    }

    function clearAuthInputs() {
        authEmail.value = '';
        authPassword.value = '';
        authConfirmPassword.value = '';
        clearAuthError();
    }

    function setAuthLoading(isLoading) {
        const submitText = btnAuthSubmit.querySelector('.auth-submit-text');
        const spinner = btnAuthSubmit.querySelector('.auth-spinner');

        if (isLoading) {
            btnAuthSubmit.setAttribute('disabled', '');
            submitText.setAttribute('hidden', '');
            spinner.removeAttribute('hidden');
        } else {
            btnAuthSubmit.removeAttribute('disabled');
            submitText.removeAttribute('hidden');
            spinner.setAttribute('hidden', '');
        }
    }

    function showAuthError(message) {
        authError.textContent = message;
        authError.removeAttribute('hidden');
    }

    function clearAuthError() {
        authError.textContent = '';
        authError.setAttribute('hidden', '');
    }

    async function handleAuthSubmit(e) {
        e.preventDefault();
        clearAuthError();

        const email = authEmail.value.trim();
        const password = authPassword.value;

        if (!email || !password) return;

        setAuthLoading(true);

        try {
            if (authMode === 'login') {
                await firebase.auth().signInWithEmailAndPassword(email, password);
            } else {
                const confirmPassword = authConfirmPassword.value;
                if (password !== confirmPassword) {
                    throw new Error("Passwords do not match");
                }
                await firebase.auth().createUserWithEmailAndPassword(email, password);
            }
        } catch (error) {
            console.error('Authentication error:', error);
            let userMsg = error.message;
            if (error.code === 'auth/invalid-email') {
                userMsg = 'Invalid email address format.';
            } else if (error.code === 'auth/user-not-found' || error.code === 'auth/wrong-password') {
                userMsg = 'Incorrect email or password.';
            } else if (error.code === 'auth/email-already-in-use') {
                userMsg = 'This email is already registered.';
            } else if (error.code === 'auth/weak-password') {
                userMsg = 'Password must be at least 6 characters.';
            }
            showAuthError(userMsg);
        } finally {
            setAuthLoading(false);
        }
    }

    async function handleGoogleAuth() {
        clearAuthError();
        const provider = new firebase.auth.GoogleAuthProvider();
        try {
            await firebase.auth().signInWithPopup(provider);
        } catch (error) {
            console.error('Google Auth failure:', error);
            showAuthError(error.message || 'Google sign-in was cancelled or encountered an error.');
        }
    }

    async function handleSignout() {
        try {
            await firebase.auth().signOut();
            closePortfolio();
        } catch (e) {
            console.error('Sign out error:', e);
        }
    }

    // ── SEARCH AUTOCOMPLETE ──────────────────────────────────────────
    async function handleAutocomplete(query) {
        try {
            const res = await fetch(`${API_BASE}/api/search?q=${encodeURIComponent(query)}`);
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
                <span class="autocomplete-ticker">${item.symbol}</span>
                <span class="autocomplete-name">${item.name}</span>
                <span class="autocomplete-sector">${item.sector}</span>
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
                    <span class="autocomplete-ticker">${cleanedQuery}</span>
                    <span class="autocomplete-name">Analyze "${cleanedQuery}" as custom PSX ticker...</span>
                    <span class="autocomplete-sector">External Query</span>
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
            button.className = 'recent-chip';
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

            // Build headers with Authorization Bearer if user is logged in
            const headers = { 'Content-Type': 'application/json' };
            const user = firebase.auth().currentUser;
            if (user) {
                try {
                    const token = await user.getIdToken();
                    headers['Authorization'] = `Bearer ${token}`;
                } catch (tokenErr) {
                    console.error('Failed to get Auth token:', tokenErr);
                }
            }

            // Send actual POST to backend analyzer
            const res = await fetch(`${API_BASE}/api/analyze`, {
                method: 'POST',
                headers: headers,
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
        if (reportDownloadBar) reportDownloadBar.setAttribute('hidden', '');
        currentReport = null;

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
    // ── PDF REPORT DOWNLOAD ──────────────────────────────────────────
    if (btnDownloadReport) {
        btnDownloadReport.addEventListener('click', async () => {
            if (!currentReport) return;
            btnDownloadReport.disabled = true;
            btnDownloadText.textContent = 'Generating PDF...';
            try {
                const headers = { 'Content-Type': 'application/json' };
                const user = firebase.auth().currentUser;
                if (user) {
                    try {
                        const token = await user.getIdToken();
                        headers['Authorization'] = `Bearer ${token}`;
                    } catch (_) {}
                }

                const resp = await fetch(`${API_BASE}/api/report/generate`, {
                    method: 'POST',
                    headers,
                    body: JSON.stringify(currentReport),
                });

                if (!resp.ok) {
                    const err = await resp.json().catch(() => ({ error: 'Unknown error' }));
                    throw new Error(err.error || `HTTP ${resp.status}`);
                }

                const blob = await resp.blob();
                const url  = URL.createObjectURL(blob);
                const a    = document.createElement('a');
                a.href     = url;
                a.download = `PSX_Analysis_${currentReport.symbol}_${new Date().toISOString().slice(0,10)}.pdf`;
                document.body.appendChild(a);
                a.click();
                document.body.removeChild(a);
                URL.revokeObjectURL(url);
                btnDownloadText.textContent = 'Download PDF Report';
            } catch (e) {
                console.error('PDF download failed:', e);
                btnDownloadText.textContent = 'Download Failed — Retry';
            } finally {
                btnDownloadReport.disabled = false;
            }
        });
    }

    function renderAnalysisReport(report) {
        // Store for PDF download
        currentReport = report;

        // Toggle view flags
        agentStatusBar.setAttribute('hidden', '');
        skeletonGrid.setAttribute('hidden', '');
        quoteCard.removeAttribute('hidden');
        agentsGrid.removeAttribute('hidden');
        debateSection.removeAttribute('hidden');
        verdictSection.removeAttribute('hidden');
        if (reportDownloadBar) reportDownloadBar.removeAttribute('hidden');

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
                    <div style="display: flex; align-items: center; gap: 10px;">
                        <h2 class="quote-symbol data-font">${quote.symbol}</h2>
                        ${icon}
                    </div>
                    <p class="quote-name">${name}</p>
                    <span class="badge badge--info" style="margin-top: 4px;">${sector}</span>
                </div>
                <div class="quote-price-group">
                    <div class="quote-price data-font">PKR ${formatNumberWithCommas(quote.price)}</div>
                    <div class="quote-change data-font ${pnlClass}">
                        ${formatPercent(quote.change_pct)} (${quote.change >= 0 ? '+' : ''}${quote.change.toFixed(2)})
                    </div>
                </div>
            </div>
            <div class="quote-stats">
                <div class="quote-stat">
                    <span class="quote-stat-label">Volume</span>
                    <span class="quote-stat-value data-font">${formatCompactNumber(quote.volume)}</span>
                </div>
                <div class="quote-stat">
                    <span class="quote-stat-label">Market Cap</span>
                    <span class="quote-stat-value data-font">${formatCompactNumber(quote.market_cap)}</span>
                </div>
                <div class="quote-stat">
                    <span class="quote-stat-label">High / Low (Day)</span>
                    <span class="quote-stat-value data-font">${formatNumberRaw(quote.day_high)} / ${formatNumberRaw(quote.day_low)}</span>
                </div>
                <div class="quote-stat">
                    <span class="quote-stat-label">Open</span>
                    <span class="quote-stat-value data-font">${formatNumberRaw(quote.open)}</span>
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

        const trend = report.trend.toUpperCase();
        badgeTechnicalTrend.textContent = trend;
        badgeTechnicalTrend.className = `badge badge--${getTrendClass(trend)}`;

        let signalsHtml = '<ul style="list-style: none; display: flex; flex-direction: column; gap: 8px;">';
        if (report.signals && report.signals.length > 0) {
            report.signals.forEach(sig => {
                signalsHtml += `
                    <li class="card-data-row">
                        <span class="card-data-label">${sig.indicator}: <strong>${sig.reading}</strong></span>
                        <span class="card-data-value text-muted" style="font-size: 0.8rem;">${sig.interpretation}</span>
                    </li>
                `;
            });
        }
        signalsHtml += '</ul>';

        let levelsHtml = '';
        if (report.key_levels) {
            const supports = report.key_levels.support || [];
            const resistances = report.key_levels.resistance || [];
            levelsHtml = `
                <div style="display: grid; grid-template-columns: 1fr 1fr; gap: 16px; margin: 12px 0;">
                    <div style="padding: 10px; background: var(--bg-surface); border-radius: var(--radius-badge); text-align: center;">
                        <div class="card-data-label" style="font-size: 0.72rem; text-transform: uppercase;">Supports</div>
                        <div class="data-font" style="font-weight: 700; margin-top: 4px; font-size: 0.9rem;">
                            ${supports.map(s => s.toFixed(2)).join(' | ') || '—'}
                        </div>
                    </div>
                    <div style="padding: 10px; background: var(--bg-surface); border-radius: var(--radius-badge); text-align: center;">
                        <div class="card-data-label" style="font-size: 0.72rem; text-transform: uppercase;">Resistances</div>
                        <div class="data-font" style="font-weight: 700; margin-top: 4px; font-size: 0.9rem;">
                            ${resistances.map(r => r.toFixed(2)).join(' | ') || '—'}
                        </div>
                    </div>
                </div>
            `;
        }

        contentTechnical.innerHTML = `
            <p class="card-narrative">${report.summary}</p>
            ${levelsHtml}
            <div style="margin-top: 14px;">
                <span class="card-data-label" style="font-weight: 700; display: block; margin-bottom: 6px;">Technical Oscillators</span>
                ${signalsHtml}
            </div>
            <div style="margin-top: 14px; display: flex; justify-content: space-between; align-items: center; border-top: 1px solid var(--border-subtle); padding-top: 10px;">
                <span class="card-data-label">Confidence:</span>
                <span class="card-data-value data-font" style="color: var(--accent-start); font-weight: 700;">${report.confidence}/10</span>
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

        const verdict = report.valuation_verdict.replace('_', ' ').toUpperCase();
        badgeFundamentalVerdict.textContent = verdict;
        badgeFundamentalVerdict.className = `badge badge--${getValuationClass(report.valuation_verdict)}`;

        let detailsHtml = '<div style="display: grid; grid-template-columns: 1fr; gap: 12px; margin-top: 12px;">';
        
        if (report.strengths && report.strengths.length > 0) {
            detailsHtml += `
                <div>
                    <span class="card-data-label text-bullish" style="font-weight: 700;">Key Strengths</span>
                    <ul style="padding-left: 16px; margin-top: 4px; font-size: 0.8rem; color: var(--text-secondary);">
                        ${report.strengths.map(s => `<li style="margin-bottom: 3px;">${s}</li>`).join('')}
                    </ul>
                </div>
            `;
        }
        
        if (report.concerns && report.concerns.length > 0) {
            detailsHtml += `
                <div>
                    <span class="card-data-label text-bearish" style="font-weight: 700;">Risks &amp; Weaknesses</span>
                    <ul style="padding-left: 16px; margin-top: 4px; font-size: 0.8rem; color: var(--text-secondary);">
                        ${report.concerns.map(c => `<li style="margin-bottom: 3px;">${c}</li>`).join('')}
                    </ul>
                </div>
            `;
        }
        detailsHtml += '</div>';

        let rangeHtml = '';
        if (report.fair_value_range) {
            rangeHtml = `
                <div style="padding: 12px; background: rgba(16, 185, 129, 0.05); border: 1px solid rgba(16, 185, 129, 0.15); border-radius: var(--radius-badge); text-align: center; margin-top: 12px;">
                    <span class="card-data-label" style="font-size: 0.72rem; text-transform: uppercase;">Estimated Fair Value Range</span>
                    <div class="data-font" style="font-size: 1.1rem; font-weight: 700; color: var(--accent-start); margin-top: 4px;">
                        PKR ${formatNumberRaw(report.fair_value_range.low)} - PKR ${formatNumberRaw(report.fair_value_range.high)}
                    </div>
                </div>
            `;
        }

        contentFundamental.innerHTML = `
            <p class="card-narrative">${report.summary}</p>
            ${rangeHtml}
            ${detailsHtml}
            <div style="margin-top: 14px; display: flex; justify-content: space-between; align-items: center; border-top: 1px solid var(--border-subtle); padding-top: 10px;">
                <span class="card-data-label">Confidence:</span>
                <span class="card-data-value data-font" style="color: var(--accent-start); font-weight: 700;">${report.confidence}/10</span>
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
                <div style="margin-top: 12px;">
                    <span class="card-data-label" style="font-weight: 700;">Key Sentiment Drivers</span>
                    <ul style="padding-left: 16px; margin-top: 4px; font-size: 0.8rem; color: var(--text-secondary);">
                        ${report.key_narratives.map(n => `<li style="margin-bottom: 3px;">${n}</li>`).join('')}
                    </ul>
                </div>
            `;
        }

        let sentimentBarHtml = '';
        if (report.sentiment_score !== undefined) {
            // Score from -10 to +10, map to 0 to 100%
            const pct = ((report.sentiment_score + 10) / 20) * 100;
            sentimentBarHtml = `
                <div class="sentiment-gauge">
                    <div class="sentiment-gauge-marker" style="left: ${pct}%;"></div>
                </div>
                <div class="sentiment-gauge-labels">
                    <span>Fear (-10)</span>
                    <span>Neutral</span>
                    <span>Greed (+10)</span>
                </div>
            `;
        }

        contentSentiment.innerHTML = `
            <p class="card-narrative">${report.summary}</p>
            ${sentimentBarHtml}
            ${narrativesHtml}
            <div style="margin-top: 14px; display: flex; justify-content: space-between; align-items: center; border-top: 1px solid var(--border-subtle); padding-top: 10px;">
                <span class="card-data-label">Confidence:</span>
                <span class="card-data-value data-font" style="color: var(--accent-start); font-weight: 700;">${report.confidence}/10</span>
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

        let factorsHtml = '<ul style="list-style: none; display: flex; flex-direction: column; gap: 8px; margin-top: 12px;">';
        if (report.risk_factors && report.risk_factors.length > 0) {
            report.risk_factors.forEach(rf => {
                factorsHtml += `
                    <li class="card-data-row">
                        <span class="card-data-label">${rf.factor}</span>
                        <span class="badge badge--${getSeverityClass(rf.severity)}" style="font-size: 0.65rem; padding: 2px 6px;">${rf.severity.toUpperCase()}</span>
                    </li>
                    <p style="font-size: 0.78rem; color: var(--text-secondary); margin-left: 0px; margin-bottom: 6px; line-height: 1.4;">${rf.detail}</p>
                `;
            });
        }
        factorsHtml += '</ul>';

        const maxPos = report.max_position_pct ? `${report.max_position_pct}%` : 'N/A';
        const stopLoss = report.stop_loss_pct ? `${report.stop_loss_pct}%` : 'N/A';

        let sizingHtml = `
            <div style="display: grid; grid-template-columns: 1fr 1fr; gap: 16px; margin: 12px 0;">
                <div style="padding: 10px; background: var(--bg-surface); border-radius: var(--radius-badge); text-align: center;">
                    <div class="card-data-label" style="font-size: 0.72rem; text-transform: uppercase;">Max Position</div>
                    <div class="data-font" style="font-weight: 700; margin-top: 4px; font-size: 1.1rem; color: var(--accent-start);">${maxPos}</div>
                </div>
                <div style="padding: 10px; background: var(--bg-surface); border-radius: var(--radius-badge); text-align: center;">
                    <div class="card-data-label" style="font-size: 0.72rem; text-transform: uppercase;">Stop Loss</div>
                    <div class="data-font" style="font-weight: 700; margin-top: 4px; font-size: 1.1rem; color: var(--bearish);">${stopLoss}</div>
                </div>
            </div>
        `;

        contentRisk.innerHTML = `
            <p class="card-narrative">${report.summary}</p>
            ${sizingHtml}
            <div>
                <span class="card-data-label" style="font-weight: 700; display: block; margin-bottom: 4px;">Identified Risk Elements</span>
                ${factorsHtml}
            </div>
            <div style="margin-top: 14px; display: flex; justify-content: space-between; align-items: center; border-top: 1px solid var(--border-subtle); padding-top: 10px;">
                <span class="card-data-label">Confidence:</span>
                <span class="card-data-value data-font" style="color: var(--accent-start); font-weight: 700;">${report.confidence}/10</span>
            </div>
        `;
    }

    function renderDebate(debate) {
        let bullHtml = `<p class="card-narrative" style="margin-bottom: 12px; background: rgba(16, 185, 129, 0.05); border-left: 3px solid var(--bullish); font-style: italic;">"${debate.bull_thesis}"</p>`;
        if (debate.bull_arguments && debate.bull_arguments.length > 0) {
            debate.bull_arguments.forEach(arg => {
                bullHtml += `
                    <div style="margin-bottom: 10px;">
                        <span class="card-data-label text-bullish" style="font-weight: 700; font-size: 0.82rem;">▲ ${arg.point}</span>
                        <p style="font-size: 0.8rem; color: var(--text-secondary); margin-top: 2px; line-height: 1.4;">${arg.evidence}</p>
                    </div>
                `;
            });
        }

        let bearHtml = `<p class="card-narrative" style="margin-bottom: 12px; background: rgba(244, 63, 94, 0.05); border-left: 3px solid var(--bearish); font-style: italic;">"${debate.bear_thesis}"</p>`;
        if (debate.bear_arguments && debate.bear_arguments.length > 0) {
            debate.bear_arguments.forEach(arg => {
                bearHtml += `
                    <div style="margin-bottom: 10px;">
                        <span class="card-data-label text-bearish" style="font-weight: 700; font-size: 0.82rem;">▼ ${arg.point}</span>
                        <p style="font-size: 0.8rem; color: var(--text-secondary); margin-top: 2px; line-height: 1.4;">${arg.evidence}</p>
                    </div>
                `;
            });
        }

        debateBullContent.innerHTML = bullHtml;
        debateBearContent.innerHTML = bearHtml;
    }

    function renderVerdict(rec) {
        const recommendation = rec.recommendation.toUpperCase();
        verdictBadge.textContent = recommendation;
        verdictBadge.className = `verdict-badge verdict-badge--${getVerdictClass(recommendation)}`;

        const verdictCard = document.getElementById('verdict-card');
        verdictCard.className = `verdict-card glass-card verdict-card--glow-${getVerdictClass(recommendation)} animate-in`;

        // Update Confidence Meter
        const perimeter = 263.89; // 2 * PI * 42
        const confidence = rec.confidence || 0;
        const offset = perimeter - (confidence / 10) * perimeter;
        
        confidenceCircle.style.strokeDasharray = perimeter;
        confidenceCircle.style.strokeDashoffset = offset;
        confidenceValue.textContent = `${confidence}/10`;

        // Price Target Display
        priceTargetLow.textContent = formatCurrency(rec.price_target_low);
        priceTargetHigh.textContent = formatCurrency(rec.price_target_high);

        // Catalysts and Risks Bullet lists
        catalystsList.innerHTML = (rec.catalysts || []).map(c => `<li>${c}</li>`).join('') || '<li>None identified</li>';
        risksList.innerHTML = (rec.risks || []).map(r => `<li>${r}</li>`).join('') || '<li>None identified</li>';

        // Position Specific Advice Context
        if (rec.position_advice) {
            verdictPositionAdvice.removeAttribute('hidden');
            positionAdviceText.textContent = rec.position_advice;
        } else {
            verdictPositionAdvice.setAttribute('hidden', '');
        }

        // Summary Reasoning Text
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
        if (changeVal > 0) return 'text-bullish';
        if (changeVal < 0) return 'text-bearish';
        return 'text-neutral';
    }

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
