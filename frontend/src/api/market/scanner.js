import service from '../index'

export const startFoScanner     = () => service.post('/api/trade/fo-scanner/start')
export const stopFoScanner      = () => service.post('/api/trade/fo-scanner/stop')
export const triggerFoScan      = () => service.post('/api/trade/fo-scanner/trigger')
export const getFoScannerStatus = () => service.get('/api/trade/fo-scanner/status')

export const createFoScannerStream = () => {
  const base = import.meta.env.VITE_API_BASE_URL || ''
  return new EventSource(`${base}/api/trade/fo-scanner/stream`)
}
