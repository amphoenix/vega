import { defineStore } from 'pinia'
import { ref, computed, onUnmounted } from 'vue'

export const useFoScannerStore = defineStore('foScanner', () => {
  const scannerRunning = ref(false)
  const foScannerState = ref(null)

  // Live event feed from scanner SSE stream
  const foFeed = ref([])

  // Currently-analysing ticker (scan_analysing events)
  const foAnalysing = ref(null)

  // Size of the F&O universe being scanned
  const foUniverseSize = ref(0)

  // 1 Hz tick for countdown
  const _now = ref(Date.now())
  const _timer = setInterval(() => { _now.value = Date.now() }, 1000)

  // Human-readable scan interval derived from backend state
  const foIntervalLabel = computed(() => {
    const sec = Number(foScannerState.value?.interval_seconds || 300)
    if (sec < 60) return `${sec}s`
    if (sec % 60 === 0) return `${sec / 60}min`
    return `${(sec / 60).toFixed(1)}min`
  })

  // Countdown seconds until next scan (null when unknown or scanning)
  const foCountdown = computed(() => {
    if (foAnalysing.value) return null
    const ns = foScannerState.value?.next_scan
    if (!ns) return null
    const diff = Math.round((new Date(ns).getTime() - _now.value) / 1000)
    return diff > 0 ? diff : null
  })

  // Formatted countdown string "M:SS"
  const foCountdownLabel = computed(() => {
    const s = foCountdown.value
    if (s == null) return null
    const m = Math.floor(s / 60)
    const ss = String(s % 60).padStart(2, '0')
    return `${m}:${ss}`
  })

  return {
    scannerRunning, foScannerState,
    foFeed, foAnalysing, foUniverseSize,
    foIntervalLabel, foCountdown, foCountdownLabel
  }
})
