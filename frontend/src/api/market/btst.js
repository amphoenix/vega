import service from '../index'

export const startBtst        = () => service.post('/api/trade/btst-scanner/start')
export const stopBtst         = () => service.post('/api/trade/btst-scanner/stop')
export const triggerBtst      = () => service.post('/api/trade/btst-scanner/trigger')
export const getBtstStatus    = () => service.get('/api/trade/btst-scanner/status')
export const getBtstStats     = () => service.get('/api/trade/btst-scanner/stats')
export const getBtstConfig    = () => service.get('/api/trade/btst-scanner/config')
export const updateBtstConfig = (params) => service.put('/api/trade/btst-scanner/config', params)

export const createBtstStream = () => {
  const base = import.meta.env.VITE_API_BASE_URL || ''
  return new EventSource(`${base}/api/trade/btst-scanner/stream`)
}
