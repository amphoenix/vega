import service from '../index'

export const getWorldIndices = () => service.get('/api/market/world-indices')

export const getUniverse = (assetClass = '', country = '') => {
  return service.get('/api/market/universe', { params: { asset_class: assetClass, country } })
}

export const scanUniverse = (market = 'india', limit = 50) => {
  return service.get('/api/market/scan', { params: { market, limit } })
}

export const searchTicker = (q) => service.get('/api/market/search', { params: { q } })

export const getMcNews = (feed = 'latest', limit = 20) => {
  return service.get('/api/market/mc-news', { params: { feed, limit } })
}

export const getFundamentals = (ticker) => service.get(`/api/market/fundamentals/${ticker}`)
