import service from '../index'

export const backtest = (ticker, days = 365, cash = 10000) => {
  return service.get(`/api/trade/backtest/${ticker}`, { params: { days, cash } })
}

export const runVbtBacktest = (ticker, days = 365) => {
  return service.get(`/api/trade/vbt-backtest/${ticker}`, { params: { days } })
}

export const createPortfolioSimStream = (capital, horizon, scope = 'all') => {
  const base = import.meta.env.VITE_API_BASE_URL || 'https://localhost:47291'
  return new EventSource(`${base}/api/market/portfolio-sim-stream?capital=${capital}&horizon=${horizon}&scope=${scope}`)
}
