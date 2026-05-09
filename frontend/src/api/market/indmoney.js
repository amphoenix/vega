import service from '../index'

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
  const base = import.meta.env.VITE_API_BASE_URL || 'https://localhost:47291'
  return new EventSource(`${base}/api/indmoney/stream/${ticker}`)
}
