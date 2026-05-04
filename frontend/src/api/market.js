import service from './index'

// OHLCV overlaid with simulation sentiment (requires simulationId)
export const getMarketOHLCV = (simulationId, { ticker, startDate, endDate, interval = '1d' }) => {
  return service.get(`/api/market/${simulationId}/ohlcv`, {
    params: { ticker, start_date: startDate, end_date: endDate, interval }
  })
}

// Standalone OHLCV — no simulation needed
export const getOHLCV = ({ ticker, startDate, endDate, interval = '1d' }) => {
  return service.get('/api/market/ohlcv', {
    params: { ticker, start_date: startDate, end_date: endDate, interval }
  })
}

// News + Reddit + FOMO + stats for a ticker
export const getSignals = (ticker) => {
  return service.get(`/api/market/signals/${ticker}`)
}

// All world indices
export const getWorldIndices = () => {
  return service.get('/api/market/world-indices')
}

// Full asset universe metadata (fast, no prices)
export const getUniverse = (assetClass = '', country = '') => {
  return service.get('/api/market/universe', {
    params: { asset_class: assetClass, country }
  })
}

// Smart scanner — scores all assets, returns ranked BUY/SELL signals
export const scanUniverse = (market = 'india', limit = 50) => {
  return service.get('/api/market/scan', { params: { market, limit } })
}

// AI mini-prediction for a ticker (calls Claude)
export const getAiPredict = (ticker) => {
  return service.get(`/api/market/ai-predict/${ticker}`)
}

// Launch a quick OASIS simulation from live signals (no doc upload needed)
export const launchQuickSim = (ticker, maxRounds = 6) => {
  return service.post(`/api/market/quick-sim/${ticker}`, { max_rounds: maxRounds })
}

// Poll quick simulation for new agent actions
export const getQuickSimStatus = (simId, sinceRound = 0) => {
  return service.get(`/api/market/quick-sim-status/${simId}`, { params: { since_round: sinceRound } })
}

// Technical indicators (RSI, MACD, BB, EMA)
export const getIndicators = (ticker, interval = '1d', days = 365) => {
  return service.get(`/api/trade/indicators/${ticker}`, {
    params: { interval, days }
  })
}

// Backtest FOMO strategy on historical data
export const backtest = (ticker, days = 365, cash = 10000) => {
  return service.get(`/api/trade/backtest/${ticker}`, {
    params: { days, cash }
  })
}

// Rank multiple tickers by composite signal score
export const rankTickers = (tickers) => {
  return service.post('/api/trade/rank', { tickers })
}

// Moneycontrol RSS news (Indian market news — latest, markets, stocks, mutual-funds, economy)
export const getMcNews = (feed = 'latest', limit = 20) => {
  return service.get('/api/market/mc-news', { params: { feed, limit } })
}

// Fundamental data for a ticker (screener.in for Indian, yfinance for others)
export const getFundamentals = (ticker) => {
  return service.get(`/api/market/fundamentals/${ticker}`)
}

// Entity knowledge graph for a ticker (nodes + links for D3)
export const getEntityGraph = (ticker) => {
  return service.get(`/api/market/graph/${ticker}`)
}

// Multi-agent investment analysis (5 expert agents + CIO synthesis)
export const getInvestAnalysis = (ticker) => {
  return service.get(`/api/market/invest-analysis/${ticker}`)
}

// Streaming version — returns EventSource, emits agent/debate/done events in real-time
export const createInvestAnalysisStream = (ticker) => {
  const base = import.meta.env.VITE_API_BASE_URL || 'http://localhost:5001'
  return new EventSource(`${base}/api/market/invest-analysis-stream/${ticker}`)
}

// Entry price, stop loss, target levels
export const getTradeLevels = (ticker) => {
  return service.get(`/api/trade/levels/${ticker}`)
}

// Paper wallet state
export const getWallet = () => service.get('/api/trade/wallet')

// AI auto-trade decision + execution against paper wallet
export const aiTrade = (ticker, capitalPct = 0.1) => {
  return service.post('/api/trade/wallet/ai-trade', { ticker, capital_pct: capitalPct })
}

// Reset paper wallet
export const resetWallet = (cash = 10000) => {
  return service.post('/api/trade/wallet/reset', { cash })
}

// Manual paper trade (BUY or SELL at live price)
export const manualTrade = (ticker, action, qty) => {
  return service.post('/api/trade/wallet/trade', { ticker, action, qty })
}

// Fast intraday signal — pure technical, no LLM, < 1s
export const getIntradaySignal = (ticker) => {
  return service.get(`/api/trade/intraday-signal/${ticker}`)
}

// vectorbt multi-strategy backtest
export const runVbtBacktest = (ticker, days = 365) => {
  return service.get(`/api/trade/vbt-backtest/${ticker}`, { params: { days } })
}

// Search tickers by symbol or company name
export const searchTicker = (q) => {
  return service.get('/api/market/search', { params: { q } })
}

// Portfolio simulation — SSE stream (EventSource, caller manages lifecycle)
export const createPortfolioSimStream = (capital, horizon, scope = 'all') => {
  const base = import.meta.env.VITE_API_BASE_URL || 'http://localhost:5001'
  return new EventSource(`${base}/api/market/portfolio-sim-stream?capital=${capital}&horizon=${horizon}&scope=${scope}`)
}

// ── INDmoney / INDstocks ─────────────────────────────────────────────────────
export const getIndmoneyStatus   = () => service.get('/api/indmoney/status')
export const getIndmoneyProfile  = () => service.get('/api/indmoney/profile')
export const getIndmoneyTick     = (ticker) => service.get(`/api/indmoney/tick/${ticker}`)
export const getIndmoneyQuote    = (ticker) => service.get(`/api/indmoney/quote/${ticker}`)
export const getIndmoneyPositions = () => service.get('/api/indmoney/positions')
export const getIndmoneyHoldings  = () => service.get('/api/indmoney/holdings')
export const getIndmoneyOrderBook = () => service.get('/api/indmoney/order-book')
export const placeIndmoneyOrder  = (payload) => service.post('/api/indmoney/order', payload)
export const cancelIndmoneyOrder = (orderId) => service.post('/api/indmoney/order/cancel', { order_id: orderId })

export const createIndmoneyStream = (ticker) => {
  const base = import.meta.env.VITE_API_BASE_URL || 'http://localhost:5001'
  return new EventSource(`${base}/api/indmoney/stream/${ticker}`)
}

export const getOptionChain = (underlying, strikes = 11) =>
  service.get(`/api/trade/option-chain`, { params: { underlying, strikes } })


// ── F&O Scanner ───────────────────────────────────────────────────────────────
export const startFoScanner     = () => service.post('/api/trade/fo-scanner/start')
export const stopFoScanner      = () => service.post('/api/trade/fo-scanner/stop')
export const triggerFoScan      = () => service.post('/api/trade/fo-scanner/trigger')
export const getFoScannerStatus = () => service.get('/api/trade/fo-scanner/status')

export const createFoScannerStream = () => {
  const base = import.meta.env.VITE_API_BASE_URL || 'http://localhost:5001'
  return new EventSource(`${base}/api/trade/fo-scanner/stream`)
}

export const createMonitorStream = () => {
  const base = import.meta.env.VITE_API_BASE_URL || 'http://localhost:5001'
  return new EventSource(`${base}/api/trade/monitor/stream`)
}

// ── LLM token budget / cost tracking ──────────────────────────────────────────
export const getBudget   = () => service.get('/api/market/budget')
export const resetBudget = () => service.post('/api/market/budget/reset')
