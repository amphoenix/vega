import service from '../index'

export const getOptionChain = (underlying, strikes = 11) => {
  return service.get('/api/trade/option-chain', { params: { underlying, strikes } })
}
