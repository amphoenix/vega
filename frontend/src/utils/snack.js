/**
 * Show a snackbar toast notification with sound.
 * Usage:  import { snack } from '@/utils/snack'
 *         snack({ severity: 'success', title: 'Order filled', message: 'BUY NIFTY CE' })
 *         snack.success('Order filled')
 *         snack.error('Order rejected', 'Insufficient funds')
 */
import { playNotifSound } from './notifSound'

export function snack({ severity = 'info', title = '', message = '', duration = 5000, sound = true } = {}) {
  if (sound) playNotifSound(severity)
  window.dispatchEvent(new CustomEvent('vega:snack', {
    detail: { severity, title, message, duration }
  }))
}

snack.success = (title, message) => snack({ severity: 'success', title, message })
snack.error   = (title, message) => snack({ severity: 'error', title, message, duration: 8000 })
snack.warning = (title, message) => snack({ severity: 'warning', title, message })
snack.info    = (title, message) => snack({ severity: 'info', title, message })
