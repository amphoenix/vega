import service from '../index'

// ── Ticker & OHLCV ────────────────────────────────────────────────────────────
export const getCryptoTicker   = (pair) => service.get(`/api/crypto/ticker/${encodeURIComponent(pair)}`)
export const getCryptoOHLCV    = (pair, interval = '1h', limit = 200) =>
  service.get(`/api/crypto/ohlcv/${encodeURIComponent(pair)}`, { params: { interval, limit } })

// ── Order book ────────────────────────────────────────────────────────────────
export const getCryptoOrderBook = (pair, depth = 20) =>
  service.get(`/api/crypto/order-book/${encodeURIComponent(pair)}`, { params: { depth } })

// ── Portfolio ─────────────────────────────────────────────────────────────────
export const getCryptoBalances  = () => service.get('/api/crypto/balances')
export const getCryptoPositions = () => service.get('/api/crypto/positions')

// ── Trading ───────────────────────────────────────────────────────────────────
export const placeCryptoOrder   = (payload) => service.post('/api/crypto/order', payload)
export const cancelCryptoOrder  = (orderId) => service.post('/api/crypto/order/cancel', { order_id: orderId })
export const quickCryptoTrade   = (symbol, side, notionalUsd) =>
  service.post('/api/crypto/quick-trade', { symbol, side, notional_usd: notionalUsd })

// ── Search / universe ─────────────────────────────────────────────────────────
export const searchCryptoPairs  = (q) => service.get('/api/crypto/search', { params: { q } })
export const getCryptoUniverse  = () => service.get('/api/crypto/universe')

// ── Bybit ─────────────────────────────────────────────────────────────────────
export const getBybitTicker     = (pair, category = 'linear') =>
  service.get(`/api/crypto/bybit/ticker/${encodeURIComponent(pair)}`, { params: { category } })

// ── Status / auto-trading ─────────────────────────────────────────────────────
export const getCryptoStatus           = () => service.get('/api/crypto/status')
export const toggleCryptoAutoTrading   = (enabled) => service.post('/api/crypto/auto-trading', { enabled })

// ── Scanner ───────────────────────────────────────────────────────────────────
export const getCryptoScannerStatus    = () => service.get('/api/crypto/scanner/status')
export const getCryptoScannerSignals   = (limit = 30) => service.get('/api/crypto/scanner/signals', { params: { limit } })
export const getCryptoScannerPnl       = () => service.get('/api/crypto/scanner/pnl')
export const getCryptoScannerPositions = () => service.get('/api/crypto/scanner/positions')
export const getCryptoScannerConfig    = () => service.get('/api/crypto/scanner/config')
export const startCryptoScanner        = () => service.post('/api/crypto/scanner/start')
export const stopCryptoScanner         = () => service.post('/api/crypto/scanner/stop')
export const closeCryptoPosition       = (symbol) => service.post(`/api/crypto/scanner/close/${encodeURIComponent(symbol)}`)
export const resetCryptoDailyPnl       = () => service.post('/api/crypto/scanner/reset-daily')

// ── Trade history & P&L ──────────────────────────────────────────────────────
export const getCryptoTradeHistory = (limit = 50) => service.get('/api/crypto/trades', { params: { limit } })
export const getCryptoPnlSummary   = () => service.get('/api/crypto/pnl/summary')

// ── Streaming ─────────────────────────────────────────────────────────────────
export const createCryptoTickStream = (pair) => {
  const base = import.meta.env.VITE_API_BASE_URL || ''
  return new EventSource(`${base}/api/crypto/stream/${encodeURIComponent(pair)}`)
}

export const createCryptoScannerStream = () => {
  const base = import.meta.env.VITE_API_BASE_URL || ''
  return new EventSource(`${base}/api/crypto/scanner/stream`)
}

// ── Bots ──────────────────────────────────────────────────────────────────────
export const listCryptoBots          = () => service.get('/api/crypto/bots')
export const getCryptoBotsPnl        = () => service.get('/api/crypto/bots/pnl')
export const createCryptoBot         = (payload) => service.post('/api/crypto/bots', payload)
export const getCryptoBot            = (botId) => service.get(`/api/crypto/bots/${botId}`)
export const updateCryptoBot         = (botId, payload) => service.put(`/api/crypto/bots/${botId}`, payload)
export const deleteCryptoBot         = (botId) => service.delete(`/api/crypto/bots/${botId}`)
export const startCryptoBot          = (botId) => service.post(`/api/crypto/bots/${botId}/start`)
export const stopCryptoBot           = (botId) => service.post(`/api/crypto/bots/${botId}/stop`)
export const getCryptoBotSignals     = (botId, limit = 30) => service.get(`/api/crypto/bots/${botId}/signals`, { params: { limit } })
export const getCryptoBotPositions   = (botId) => service.get(`/api/crypto/bots/${botId}/positions`)
export const closeCryptoBotPosition  = (botId, symbol) => service.post(`/api/crypto/bots/${botId}/close/${encodeURIComponent(symbol)}`)
export const startAllCryptoBots      = () => service.post('/api/crypto/bots/start-all')
export const stopAllCryptoBots       = () => service.post('/api/crypto/bots/stop-all')

// ── Crypto FNO (Deribit Derivatives — Options + Perpetuals) ──────────────────
export const getCryptoFoState     = () => service.get('/api/crypto/fno/state')
export const getCryptoFoPnl       = () => service.get('/api/crypto/fno/pnl')
export const getCryptoFoPositions = () => service.get('/api/crypto/fno/positions')
export const getCryptoFoSignals   = (limit = 50) => service.get('/api/crypto/fno/signals', { params: { limit } })
export const startCryptoFo        = () => service.post('/api/crypto/fno/start')
export const stopCryptoFo         = () => service.post('/api/crypto/fno/stop')
export const flattenCryptoFo      = () => service.post('/api/crypto/fno/flatten')
