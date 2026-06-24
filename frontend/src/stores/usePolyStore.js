import { defineStore } from 'pinia'
import { ref, computed } from 'vue'

export const usePolyStore = defineStore('poly', () => {
  // ── Markets ─────────────────────────────────────────────────────────────────
  const markets       = ref([])     // [{ id, question, end_date, volume, liquidity, outcomes[] }]
  const activeMarket  = ref(null)   // currently selected market detail
  const category      = ref('all')  // filter: all | politics | crypto | sports | science | culture

  // ── Portfolio ───────────────────────────────────────────────────────────────
  const portfolio     = ref([])     // [{ market_id, question, outcome, shares, avg_price, current_price }]
  const balance       = ref(0)      // available USDC/cash

  // ── Order book / pricing ────────────────────────────────────────────────────
  const orderBook     = ref({ yes: [], no: [] })

  // ── Search & sort ───────────────────────────────────────────────────────────
  const searchQuery   = ref('')
  const sortBy        = ref('volume') // volume | newest | ending_soon | liquidity

  // ── UI state ────────────────────────────────────────────────────────────────
  const loading       = ref(false)
  const error         = ref('')
  const connected     = ref(false)

  // ── Derived ─────────────────────────────────────────────────────────────────
  const filteredMarkets = computed(() => {
    let list = markets.value
    if (category.value !== 'all') {
      list = list.filter(m => m.category === category.value)
    }
    if (searchQuery.value.trim()) {
      const q = searchQuery.value.toLowerCase()
      list = list.filter(m => m.question.toLowerCase().includes(q))
    }
    return list
  })

  function selectMarket(market) {
    activeMarket.value = market
  }

  function $reset() {
    markets.value = []
    activeMarket.value = null
    portfolio.value = []
    balance.value = 0
    orderBook.value = { yes: [], no: [] }
    loading.value = false
    error.value = ''
  }

  return {
    markets, activeMarket, category,
    portfolio, balance,
    orderBook,
    searchQuery, sortBy,
    loading, error, connected,
    filteredMarkets,
    selectMarket, $reset,
  }
})
