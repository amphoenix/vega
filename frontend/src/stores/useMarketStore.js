import { defineStore } from 'pinia'
import { ref, computed } from 'vue'

export const useMarketStore = defineStore('market', () => {
  // Ticker input and resolved chart ticker
  const activeTicker = ref('^NSEI')
  const chartTicker = ref('')
  const interval = ref('5m')
  const searchSuggestions = ref([])

  // OHLCV chart data
  const ohlcv = ref([])
  const chartLoading = ref(false)
  const chartVisibleCount = ref(0)
  const chartOffset = ref(0)

  // Chart header stats (populated after OHLCV loads)
  const tickerStats = ref({})
  const fomoScore = ref(null)

  // Intelligence feed (news + Reddit + FOMO cards)
  const feedItems = ref([])
  const signalsLoading = ref(false)

  // Intraday signal
  const signal = ref(null)
  const signalLoading = ref(false)
  const signalError = ref('')

  // Trade levels (entry, SL, targets)
  const levels = ref(null)
  const levelsLoading = ref(false)

  // AI prediction
  const aiPredLoading = ref(false)
  const prediction = ref({
    ready: false,
    short: '', shortPct: 0, shortClass: '',
    long: '',  longPct: 0,  longClass: ''
  })

  // Multi-agent invest analysis (analysis tab)
  const investData = ref(null)
  const investLoading = ref(false)
  const investError = ref('')
  const selectedAgent = ref(null)
  const investPhase = ref('')
  const investAgentsDone = ref(0)
  const investAgentsTotal = ref(0)

  // Backtest
  const btResult = ref(null)
  const btLoading = ref(false)
  const btError = ref('')
  const btExpanded = ref(false)

  // Quick sim (launched from home, no file upload)
  const activeSimId = ref(null)
  const simRound = ref(0)
  const simRunning = ref(false)
  const simAgents = ref(0)

  // Universe / watchlist sidebar
  const allAssets = ref([])
  const scanResults = ref([])
  const scanMarket = ref('india')
  const scanError = ref('')
  const indicesLoading = ref(false)
  const scanLoading = ref(false)
  const assetClassFilter = ref('index')
  const countryFilter = ref('ALL')
  const watchlist = ref([])

  // Budget / LLM cost tracking
  const budgetData = ref(null)
  const budgetLoading = ref(false)
  const budgetPeriod = ref('today')

  return {
    activeTicker, chartTicker, interval, searchSuggestions,
    ohlcv, chartLoading, chartVisibleCount, chartOffset,
    tickerStats, fomoScore,
    feedItems, signalsLoading,
    signal, signalLoading, signalError,
    levels, levelsLoading,
    aiPredLoading, prediction,
    investData, investLoading, investError, selectedAgent, investPhase, investAgentsDone, investAgentsTotal,
    btResult, btLoading, btError, btExpanded,
    activeSimId, simRound, simRunning, simAgents,
    allAssets, scanResults, scanMarket, scanError, indicesLoading, scanLoading,
    assetClassFilter, countryFilter, watchlist,
    budgetData, budgetLoading, budgetPeriod
  }
})
