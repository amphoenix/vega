import service from '../index'

export const startScalpScanner     = () => service.post('/api/trade/scalp-scanner/start')
export const stopScalpScanner      = () => service.post('/api/trade/scalp-scanner/stop')
export const triggerScalpScan      = () => service.post('/api/trade/scalp-scanner/trigger')
export const getScalpScannerStatus = () => service.get('/api/trade/scalp-scanner/status')
export const getScalpScannerStats  = () => service.get('/api/trade/scalp-scanner/stats')
export const resetScalpDaily       = () => service.post('/api/trade/scalp-scanner/reset-daily')

export const createScalpStream = () => {
  const base = import.meta.env.VITE_API_BASE_URL || ''
  return new EventSource(`${base}/api/trade/scalp-scanner/stream`)
}
