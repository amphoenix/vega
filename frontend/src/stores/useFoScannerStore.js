import { defineStore } from 'pinia'
import { ref, computed } from 'vue'

export const useFoScannerStore = defineStore('foScanner', () => {
  const scannerRunning = ref(false)
  const foScannerState = ref(null)

  // Live event feed from scanner SSE stream
  const foFeed = ref([])

  // Currently-analysing ticker (scan_analysing events)
  const foAnalysing = ref(null)

  // Size of the F&O universe being scanned
  const foUniverseSize = ref(0)

  // Human-readable scan interval derived from backend state
  const foIntervalLabel = computed(() => {
    const sec = Number(foScannerState.value?.interval_seconds || 300)
    if (sec < 60) return `${sec}s`
    if (sec % 60 === 0) return `${sec / 60}min`
    return `${(sec / 60).toFixed(1)}min`
  })

  return {
    scannerRunning, foScannerState,
    foFeed, foAnalysing, foUniverseSize,
    foIntervalLabel
  }
})
