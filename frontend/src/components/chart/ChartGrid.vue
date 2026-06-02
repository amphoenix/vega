<template>
  <div :class="['chart-grid', { 'three-pane': !!thirdTicker }]">
    <!-- Pane 1: NIFTY 50 (always visible) -->
    <div class="chart-pane">
      <div class="pane-hdr">
        <span class="pane-sym">NIFTY 50</span>
        <span class="pane-ltp" v-if="niftyPrice">₹{{ fmtPrice(niftyPrice) }}</span>
      </div>
      <HomeChart
        chartTicker="^NSEI"
        :interval="interval"
        :indmoneyLivePrice="niftyPrice"
        :lightMode="lightMode"
        currencySymbol="₹"
        :marketOpen="marketOpen"
      />
    </div>

    <!-- Pane 2: SENSEX (always visible) -->
    <div class="chart-pane">
      <div class="pane-hdr">
        <span class="pane-sym">SENSEX</span>
        <span class="pane-ltp" v-if="sensexPrice">₹{{ fmtPrice(sensexPrice) }}</span>
      </div>
      <HomeChart
        chartTicker="^BSESN"
        :interval="interval"
        :indmoneyLivePrice="sensexPrice"
        :lightMode="lightMode"
        currencySymbol="₹"
        :marketOpen="marketOpen"
      />
    </div>

    <!-- Pane 3: User-selected ticker (conditional) -->
    <div class="chart-pane" v-if="thirdTicker">
      <div class="pane-hdr">
        <span class="pane-sym">{{ displayThird }}</span>
        <span class="pane-ltp" v-if="thirdPrice">₹{{ fmtPrice(thirdPrice) }}</span>
        <button class="pane-close" @click="emit('close-third')" title="Close chart">✕</button>
      </div>
      <HomeChart
        :chartTicker="thirdTicker"
        :interval="interval"
        :indmoneyLivePrice="thirdPrice"
        :levels="levels"
        :lightMode="lightMode"
        currencySymbol="₹"
        :marketOpen="marketOpen"
        @update:chartTicker="(v) => emit('update:chartTicker', v)"
        @update:activeTicker="(v) => emit('update:activeTicker', v)"
      />
    </div>
  </div>
</template>

<script setup>
import { computed } from 'vue'
import { storeToRefs } from 'pinia'
import { useLiveTradingStore } from '../../stores/useLiveTradingStore'
import HomeChart from './HomeChart.vue'
import { fmtPrice } from '../../utils/formatters'

const props = defineProps({
  chartTicker:   { type: String, default: '' },
  displayTicker: { type: String, default: '' },
  interval:      { type: String, default: '1d' },
  levels:        { type: Object, default: null },
  lightMode:     { type: Boolean, default: false },
  marketOpen:    { type: Boolean, default: false },
})

const emit = defineEmits(['close-third', 'update:chartTicker', 'update:activeTicker'])

const { liveSpots } = storeToRefs(useLiveTradingStore())

const ALWAYS_ON = new Set(['^NSEI', '^BSESN'])

const niftyPrice  = computed(() => Number(liveSpots.value['^NSEI']) || null)
const sensexPrice = computed(() => Number(liveSpots.value['^BSESN']) || null)

const thirdTicker = computed(() => {
  const t = props.chartTicker
  if (!t || ALWAYS_ON.has(t)) return null
  return t
})

const thirdPrice = computed(() => {
  if (!thirdTicker.value) return null
  return Number(liveSpots.value[thirdTicker.value]) || null
})

const displayThird = computed(() => {
  if (thirdTicker.value) return props.displayTicker || thirdTicker.value
  return ''
})
</script>

<style src="../../styles/ChartGrid.css"></style>
