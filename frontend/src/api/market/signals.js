import service from '../index'

export const getSignals = (ticker) => service.get(`/api/market/signals/${ticker}`)

export const getAiPredict = (ticker) => service.get(`/api/market/ai-predict/${ticker}`)

export const getIndicators = (ticker, interval = '1d', days = 365) => {
  return service.get(`/api/trade/indicators/${ticker}`, { params: { interval, days } })
}

export const rankTickers = (tickers) => service.post('/api/trade/rank', { tickers })

export const getInvestAnalysis = (ticker) => service.get(`/api/market/invest-analysis/${ticker}`)

export const createInvestAnalysisStream = (ticker) => {
  const base = import.meta.env.VITE_API_BASE_URL || 'https://localhost:47291'
  return new EventSource(`${base}/api/market/invest-analysis-stream/${ticker}`)
}

export const getTradeLevels = (ticker) => service.get(`/api/trade/levels/${ticker}`)

export const getIntradaySignal = (ticker) => service.get(`/api/trade/intraday-signal/${ticker}`)
