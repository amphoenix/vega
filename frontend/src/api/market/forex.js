import service from '../index'

export const getForexTicker = (pair) =>
  service.get(`/api/forex/ticker/${encodeURIComponent(pair)}`)

export const getForexOHLCV = (pair, interval = '1h', limit = 200) =>
  service.get(`/api/forex/ohlcv/${encodeURIComponent(pair)}`, { params: { interval, limit } })

export const getForexUniverse = () =>
  service.get('/api/forex/universe')

export const createForexTickStream = (pair) => {
  const base = import.meta.env.VITE_API_BASE_URL || ''
  return new EventSource(`${base}/api/forex/stream/${encodeURIComponent(pair)}`)
}

export const getForexStatus          = () => service.get('/api/forex/status')
export const toggleForexAutoTrading  = (enabled) => service.post('/api/forex/auto-trading', { enabled })

// Scanner endpoints
export const startForexScanner  = () => service.post('/api/forex/scanner/start')
export const stopForexScanner   = () => service.post('/api/forex/scanner/stop')
export const triggerForexScan   = () => service.post('/api/forex/scanner/trigger')
export const getForexScannerStatus = () => service.get('/api/forex/scanner/status')

export const createForexScannerStream = () => {
  const base = import.meta.env.VITE_API_BASE_URL || ''
  return new EventSource(`${base}/api/forex/scanner/stream`)
}
