import service from '../index'

export const getPnlSummary = (params = {}) => service.get('/api/trade/pnl/summary', { params })
export const getPnlTrades  = (params = {}) => service.get('/api/trade/pnl/trades',  { params })
