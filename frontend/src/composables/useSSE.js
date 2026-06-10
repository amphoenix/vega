/**
 * Shared SSE connections — one connection per endpoint, many subscribers.
 * Prevents duplicate EventSource connections when multiple components listen
 * to the same server stream.
 *
 * Features:
 *  - Singleton per URL: only 1 EventSource regardless of how many components subscribe
 *  - Auto-reconnect with exponential backoff (5s → 10s → 20s, cap 30s)
 *  - Centralized error logging
 *  - Auto-cleanup on component unmount via onUnmounted
 *  - Connection state exposed via Pinia store (useLiveTradingStore)
 *
 * Usage:
 *   import { onOrderEvent, onTrackedAlert } from '../composables/useSSE'
 *   onOrderEvent((data) => { ... })
 *   onTrackedAlert((data) => { ... })
 */
import { onUnmounted } from 'vue'

const _streams = {}  // url → { es, subs: Set<Function>, backoff: number }
const _BASE = () => import.meta.env.VITE_API_BASE_URL || 'https://localhost:47291'

function _getOrCreate(url) {
  const existing = _streams[url]
  if (existing && existing.es.readyState !== EventSource.CLOSED) return existing

  // Preserve subscribers across reconnects
  const entry = {
    es: null,
    subs: existing?.subs || new Set(),
    backoff: existing?.backoff || 5000,
  }

  entry.es = new EventSource(`${_BASE()}${url}`)

  entry.es.onopen = () => {
    entry.backoff = 5000  // reset backoff on success
    // Update store connection state
    _updateStoreConnected(url, true)
  }

  entry.es.onmessage = (ev) => {
    try {
      const d = JSON.parse(ev.data)
      for (const fn of entry.subs) {
        try { fn(d) } catch (e) { console.warn('[SSE] subscriber error:', e) }
      }
    } catch (e) {
      console.warn('[SSE] parse error:', url, e)
    }
  }

  entry.es.onerror = () => {
    _updateStoreConnected(url, false)
    if (entry.subs.size > 0) {
      const delay = entry.backoff
      entry.backoff = Math.min(entry.backoff * 2, 30000)
      setTimeout(() => {
        if (entry.subs.size > 0) _getOrCreate(url)
      }, delay)
    }
  }

  _streams[url] = entry
  return entry
}

let _storeRef = null
function _updateStoreConnected(url, connected) {
  try {
    if (!_storeRef) {
      // Lazy import on first call (after Pinia is installed)
      import('../stores/useLiveTradingStore').then(m => {
        _storeRef = m.useLiveTradingStore
        _storeRef().alertsConnected = connected
      })
      return
    }
    const store = _storeRef()
    if (url.includes('tracked/alerts')) store.alertsConnected = connected
  } catch {}
}

function _subscribe(url, callback) {
  const entry = _getOrCreate(url)
  entry.subs.add(callback)

  const unsub = () => {
    entry.subs.delete(callback)
    if (entry.subs.size === 0) {
      entry.es?.close()
      delete _streams[url]
    }
  }

  // Auto-cleanup on component unmount
  try { onUnmounted(unsub) } catch {}
  return unsub
}

// ── Public API ────────────────────────────────────────────────────────────────

/** Subscribe to order fill/reject/modification events */
export function onOrderEvent(callback) {
  return _subscribe('/api/indmoney/order-events/stream', callback)
}

/** Subscribe to tracked position alerts (status, PnL, SL updates, daily PnL, funds) */
export function onTrackedAlert(callback) {
  return _subscribe('/api/trade/tracked/alerts/stream', callback)
}
