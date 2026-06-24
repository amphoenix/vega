import service from '../index'

export const getBudget   = () => service.get('/api/market/budget')
export const resetBudget = () => service.post('/api/market/budget/reset')
