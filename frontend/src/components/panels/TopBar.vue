<template>
  <header class="topbar">
    <div class="topbar-left">
      <div class="brand">
        PhoenixTrade<span class="brand-sub">AI Trading Intelligence</span>
      </div>
      <div class="ticker-row">
        <div class="search-wrap">
          <input
            v-model="activeTicker"
            class="ticker-input"
            placeholder="SBIN or State Bank…"
            @keyup.enter="submitSearch"
            @input="onSearchInput"
            @blur="hideSuggestionsDelayed"
            autocomplete="off"
          />
          <div class="search-suggestions" v-if="searchSuggestions.length">
            <div
              v-for="s in searchSuggestions"
              :key="s.symbol"
              class="ss-item"
              @mousedown.prevent="pickSuggestion(s)"
            >
              <span class="ss-sym">{{ s.symbol.replace(/\.(NS|BO)$/i, '') }}</span>
              <span class="ss-name">{{ s.name }}</span>
              <span class="ss-ex">{{ s.indian ? (s.symbol.endsWith('.NS') ? 'NSE' : 'BSE') : s.exchange }}</span>
            </div>
          </div>
        </div>
        <div class="tf-btns">
          <button
            v-for="tf in timeframes"
            :key="tf.v"
            :class="['tf-btn', { active: interval === tf.v }]"
            @click="interval = tf.v"
          >
            {{ tf.l }}
          </button>
        </div>
        <button class="go-btn" @click="submitSearch">▶ Load</button>
      </div>
    </div>

    <div class="topbar-center">
      <button
        :class="['vtn-tab', { active: viewMode === 'chart' }]"
        @click="$emit('update:viewMode', 'chart')"
      >📊 Chart</button>
      <button
        :class="['vtn-tab', { active: viewMode === 'analysis' }]"
        @click="$emit('update:viewMode', 'analysis')"
      >🧠 Analysis</button>
      <button
        :class="['vtn-tab', { active: viewMode === 'portfolio' }]"
        @click="$emit('update:viewMode', 'portfolio')"
      >🎯 Portfolio</button>
    </div>

    <div class="topbar-right">
      <div
        class="ind-status connected"
        v-if="indmoneyConnected"
        :title="indmoneyName || 'INDmoney connected'"
      >
        <span class="ind-dot"></span>
        <span class="ind-label">{{ indmoneyName || 'INDmoney' }}</span>
        <span class="ind-price" v-if="indmoneyLivePrice">₹{{ fmtPrice(indmoneyLivePrice) }}</span>
      </div>
      <div class="ind-cash" v-if="indmoneyConnected && indmoneyAvailableCash != null" :title="'Available cash in your INDmoney account'">
        <span class="ind-cash-label">Cash</span>
        <span class="ind-cash-val" :class="{ 'ind-cash-low': indmoneyAvailableCash < 1000 }">₹{{ fmtCash(indmoneyAvailableCash) }}</span>
      </div>
      <button
        v-if="!indmoneyConnected"
        class="ind-btn"
        @click="$emit('indmoney-open')"
        :class="{ configured: indmoneyAvailable }"
        :title="indmoneyAvailable ? 'Token set — click to verify connection' : 'Open INDstocks to get your token'"
      >
        <span class="ind-icon">📈</span>
        {{ indmoneyAvailable ? 'INDmoney ●' : 'Connect INDmoney' }}
      </button>
      <button
        class="theme-btn"
        @click="$emit('toggle-theme')"
        :title="lightMode ? 'Switch to dark' : 'Switch to light'"
      >{{ lightMode ? '🌙' : '☀️' }}</button>
      <span class="live-dot"></span>
      <span class="live-label">LIVE</span>
      <button
        class="sim-btn"
        @click="$emit('simulate', chartTicker)"
        :disabled="simRunning || !chartTicker"
      >
        <span v-if="simRunning" class="spinner"></span>
        <span v-else>⚡</span>
        {{ simRunning ? 'Simulating…' : 'Simulate' }}
      </button>
    </div>
  </header>
</template>

<script setup>
import { storeToRefs } from 'pinia'
import { useMarketStore } from '../../stores/useMarketStore'
import { searchTicker } from '../../api/market'
import { fmtPrice } from '../../utils/formatters'

function fmtCash(v) {
  if (v == null) return '—'
  if (v >= 100000) return (v / 100000).toFixed(1) + 'L'
  if (v >= 1000) return (v / 1000).toFixed(1) + 'K'
  return v.toFixed(0)
}

const props = defineProps({
  viewMode: { type: String, default: 'chart' },
  simRunning: { type: Boolean, default: false },
  lightMode: { type: Boolean, default: false },
  indmoneyConnected: { type: Boolean, default: false },
  indmoneyName: { type: String, default: '' },
  indmoneyLivePrice: { type: Number, default: null },
  indmoneyAvailable: { type: Boolean, default: false },
  indmoneyAvailableCash: { type: Number, default: null },
})

const emit = defineEmits(['update:viewMode', 'toggle-theme', 'indmoney-open', 'simulate', 'select-ticker'])

const { activeTicker, interval, searchSuggestions, chartTicker } = storeToRefs(useMarketStore())

const timeframes = [
  { l: '5M',  v: '5m'  },
  { l: '30M', v: '30m' },
  { l: '1H',  v: '1h'  },
  { l: '1D',  v: '1d'  },
  { l: '1W',  v: '1wk' },
  { l: '1Y',  v: '1y'  },
]

let searchTimer = null

function onSearchInput() {
  clearTimeout(searchTimer)
  const q = activeTicker.value.trim()
  if (q.length < 2) {
    searchSuggestions.value = []
    return
  }
  searchTimer = setTimeout(async () => {
    try {
      const res = await searchTicker(q)
      searchSuggestions.value = res.data || []
    } catch {
      searchSuggestions.value = []
    }
  }, 350)
}

function pickSuggestion(s) {
  activeTicker.value = s.symbol.replace(/\.(NS|BO)$/i, '')
  searchSuggestions.value = []
  emit('select-ticker', s.symbol)
}

function submitSearch() {
  const typed = (activeTicker.value || '').trim()
  if (!typed) return
  const top = searchSuggestions.value?.[0]
  if (top && top.symbol && top.symbol.toUpperCase() !== typed.toUpperCase()) {
    pickSuggestion(top)
    return
  }
  searchSuggestions.value = []
  emit('select-ticker', typed)
}

function hideSuggestionsDelayed() {
  setTimeout(() => { searchSuggestions.value = [] }, 200)
}
</script>

<style src="../../styles/TopBar.css"></style>
