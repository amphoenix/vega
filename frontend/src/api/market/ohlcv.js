import service from '../index'

export const getMarketOHLCV = (simulationId, { ticker, startDate, endDate, interval = '1d' }) => {
  return service.get(`/api/market/${simulationId}/ohlcv`, {
    params: { ticker, start_date: startDate, end_date: endDate, interval }
  })
}

export const getOHLCV = ({ ticker, startDate, endDate, interval = '1d' }) => {
  return service.get('/api/market/ohlcv', {
    params: { ticker, start_date: startDate, end_date: endDate, interval }
  })
}
