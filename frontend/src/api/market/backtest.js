import service from '../index'

export const backtest = (ticker, days = 365, cash = 10000) => {
  return service.get(`/api/trade/backtest/${ticker}`, { params: { days, cash } })
}

export const runVbtBacktest = (ticker, days = 365) => {
  return service.get(`/api/trade/vbt-backtest/${ticker}`, { params: { days } })
}
