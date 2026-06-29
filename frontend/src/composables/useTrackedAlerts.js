/**
 * Reusable tracked-alert handler — filters by trade_mode and dispatches
 * sound + snack notifications.  Each panel calls this with its own mode(s).
 *
 * Usage:
 *   import { useTrackedAlerts } from '../composables/useTrackedAlerts'
 *   useTrackedAlerts({
 *     modes: ['swing'],                 // only these trade_modes
 *     onAlert(payload) { ... },         // extra per-panel logic (optional)
 *     currency: 'INR',                  // price formatting (default INR)
 *     priceDecimals: 0,                 // (default 0)
 *   })
 */
import { onTrackedAlert } from './useSSE'
import { snack } from '../utils/snack'
import { playNotifSound } from '../utils/notifSound'

const _SOUND_MAP = {
  sl_hit: 'sl_exit',
  past_t1: 't1_exit',
  past_t2: 't2_exit',
  time_exit: 'time_exit',
  near_sl: 'warning',
  near_t1: 'info',
}

const _SEVERITY = {
  sl_hit: 'error',
  time_exit: 'error',
  past_t2: 'success',
  past_t1: 'success',
  near_sl: 'warning',
  near_t1: 'info',
}

function _statusLabel(status) {
  return (status || '').toUpperCase().replace(/_/g, ' ')
}

export function useTrackedAlerts({ modes, onAlert, currency = 'INR', priceDecimals = 0 } = {}) {
  const modeSet = new Set(modes || [])
  const currSymbol = currency === 'USD' ? '$' : '₹'

  onTrackedAlert((m) => {
    // Pass through non-alert events (heartbeat, funds_update, etc.)
    if (m.type !== 'tracked_alert') {
      if (onAlert) onAlert(m)
      return
    }

    // Filter by mode
    const mode = m.trade_mode || 'swing'
    if (modeSet.size > 0 && !modeSet.has(mode)) return

    // Sound
    playNotifSound(_SOUND_MAP[m.status] || 'info')

    // Snack notification
    const prem = m.premium != null ? Number(m.premium).toFixed(priceDecimals) : '?'
    snack({
      severity: _SEVERITY[m.status] || 'info',
      title: `${_statusLabel(m.status)}`,
      message: m.message || `${m.display_symbol || m.trading_symbol} @ ${currSymbol}${prem}`,
      duration: ['sl_hit', 'time_exit'].includes(m.status) ? 8000 : 5000,
      sound: false,
    })

    // Panel-specific callback
    if (onAlert) onAlert(m)
  })
}
