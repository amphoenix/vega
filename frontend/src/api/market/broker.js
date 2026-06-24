import service from '../index'

export const getBrokerStatus    = () => service.get('/api/broker/status')
export const getBrokerProfile   = () => service.get('/api/broker/profile')
export const getBrokerTick      = (ticker) => service.get(`/api/broker/tick/${ticker}`)
export const getBrokerQuote     = (ticker) => service.get(`/api/broker/quote/${ticker}`)
export const getBrokerPositions = () => service.get('/api/broker/positions')
export const getBrokerHoldings  = () => service.get('/api/broker/holdings')
export const getBrokerOrderBook = () => service.get('/api/broker/order-book')
export const placeBrokerOrder   = (payload) => service.post('/api/broker/order', payload)
export const cancelBrokerOrder  = (orderId) => service.post('/api/broker/order/cancel', { order_id: orderId })

export const createBrokerTickStream = (ticker) => {
  const base = import.meta.env.VITE_API_BASE_URL || ''
  return new EventSource(`${base}/api/broker/stream/${ticker}`)
}

export const createBrokerOrderStream = () => {
  const base = import.meta.env.VITE_API_BASE_URL || ''
  return new EventSource(`${base}/api/broker/order-events/stream`)
}

// Legacy aliases — keep imports working during migration
export const getIndmoneyStatus    = getBrokerStatus
export const getIndmoneyProfile   = getBrokerProfile
export const getIndmoneyTick      = getBrokerTick
export const getIndmoneyQuote     = getBrokerQuote
export const getIndmoneyPositions = getBrokerPositions
export const getIndmoneyHoldings  = getBrokerHoldings
export const getIndmoneyOrderBook = getBrokerOrderBook
export const placeIndmoneyOrder   = placeBrokerOrder
export const cancelIndmoneyOrder  = cancelBrokerOrder
export const createIndmoneyStream = createBrokerTickStream
