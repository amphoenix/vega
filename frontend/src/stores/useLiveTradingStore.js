import { defineStore } from 'pinia'
import { ref } from 'vue'

export const useLiveTradingStore = defineStore('liveTrading', () => {
  // Active broker connection (broker-agnostic)
  const brokerConnected = ref(false)
  const brokerAvailable = ref(false)
  const brokerLivePrice = ref(null)
  const brokerNameRef = ref('')
  const brokerUserName = ref('')
  const brokerAvailableCash = ref(null)

  // Per-underlying live spot prices from SSE tick streams
  // Key: symbol (e.g. "^NSEI"), Value: latest price
  const liveSpots = ref({})
  // Key: symbol, Value: epoch ms of last tick
  const liveSpotTickAt = ref({})
  // Key: symbol, Value: seconds since last tick (updated every 1s)
  const liveTickAges = ref({})

  // AI-generated live option tickets from F&O scanner
  // Key: underlying symbol, Value: ticket object
  const liveTickets = ref({})

  // Per-position tick decision payloads from alerts stream
  // Key: pos_key, Value: { status, entry, current, sl, t1, t2, ... }
  const liveStatus = ref({})

  // Manually-tracked positions
  const trackedPositions = ref([])

  // Latest CIO commentary from alerts stream
  const latestCommentary = ref(null)
  const liveConnected = ref(false)

  // Missed desktop alerts (persisted to localStorage)
  const missedAlerts = ref([])
  const alertsConnected = ref(false)

  // Option chain
  const optionChain = ref(null)
  const chainUnderlying = ref('^NSEI')
  const chainBuyOnly = ref(true)
  const chainLoading = ref(false)
  const chainError = ref('')

  return {
    brokerConnected, brokerAvailable, brokerLivePrice, brokerNameRef, brokerUserName, brokerAvailableCash,
    liveSpots, liveSpotTickAt, liveTickAges,
    liveTickets, liveStatus,
    trackedPositions, latestCommentary, liveConnected,
    missedAlerts, alertsConnected,
    optionChain, chainUnderlying, chainBuyOnly, chainLoading, chainError,
  }
})
