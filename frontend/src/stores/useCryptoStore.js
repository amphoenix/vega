import { defineStore } from 'pinia'
import { ref } from 'vue'

export const useCryptoStore = defineStore('crypto', () => {
  // ── Active pair ─────────────────────────────────────────────────────────────
  const activePair    = ref('BTC/USDT')
  const interval      = ref('1h')
  const searchQuery   = ref('')

  // ── Price data ──────────────────────────────────────────────────────────────
  const ticker        = ref(null)   // { bid, ask, last, volume_24h, change_24h }
  const ohlcv         = ref([])     // candlestick data
  const orderBook     = ref({ bids: [], asks: [] })

  // ── Portfolio ───────────────────────────────────────────────────────────────
  const balances      = ref([])     // [{ asset, free, locked, usd_value }]
  const positions     = ref([])     // open positions

  // ── Watchlist ───────────────────────────────────────────────────────────────
  const watchlist     = ref([
    'BTC/USDT', 'ETH/USDT', 'SOL/USDT', 'BNB/USDT',
    'XRP/USDT', 'DOGE/USDT', 'ADA/USDT', 'AVAX/USDT',
  ])

  // ── UI state ────────────────────────────────────────────────────────────────
  const loading       = ref(false)
  const error         = ref('')
  const connected     = ref(false)  // exchange connection status

  function selectPair(pair) {
    activePair.value = pair
  }

  function $reset() {
    ticker.value = null
    ohlcv.value = []
    orderBook.value = { bids: [], asks: [] }
    balances.value = []
    positions.value = []
    loading.value = false
    error.value = ''
  }

  return {
    activePair, interval, searchQuery,
    ticker, ohlcv, orderBook,
    balances, positions,
    watchlist,
    loading, error, connected,
    selectPair, $reset,
  }
})
