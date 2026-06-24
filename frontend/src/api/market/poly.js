import service from '../index'

// ── Markets ───────────────────────────────────────────────────────────────────
export const getPolyMarkets     = (params = {}) =>
  service.get('/api/poly/markets', { params })

export const getPolyMarket      = (marketId) =>
  service.get(`/api/poly/markets/${marketId}`)

export const searchPolyMarkets  = (q) =>
  service.get('/api/poly/markets/search', { params: { q } })

// ── Order book / pricing ──────────────────────────────────────────────────────
export const getPolyOrderBook   = (marketId) =>
  service.get(`/api/poly/markets/${marketId}/order-book`)

export const getPolyPriceHistory = (marketId, interval = '1d') =>
  service.get(`/api/poly/markets/${marketId}/price-history`, { params: { interval } })

// ── Trading ───────────────────────────────────────────────────────────────────
export const placePolyOrder     = (payload) =>
  service.post('/api/poly/order', payload)

export const cancelPolyOrder    = (orderId) =>
  service.post('/api/poly/order/cancel', { order_id: orderId })

// ── Portfolio ─────────────────────────────────────────────────────────────────
export const getPolyPortfolio   = () => service.get('/api/poly/portfolio')
export const getPolyBalance     = () => service.get('/api/poly/balance')

// ── Status / auto-trading ─────────────────────────────────────────────────────
export const getPolyStatus           = () => service.get('/api/poly/status')
export const togglePolyAutoTrading   = (enabled) => service.post('/api/poly/auto-trading', { enabled })

// ── News & AI Analysis ────────────────────────────────────────────────────────
export const getPolyNews        = (marketId) => service.get(`/api/poly/news/${marketId}`)
export const getPolyAIAnalysis  = (marketId) => service.get(`/api/poly/ai-analysis/${marketId}`)

// ── Streaming ─────────────────────────────────────────────────────────────────
export const createPolyMarketStream = (marketId) => {
  const base = import.meta.env.VITE_API_BASE_URL || ''
  return new EventSource(`${base}/api/poly/stream/${marketId}`)
}
