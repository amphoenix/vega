<template>
  <header class="topbar">
    <div class="topbar-left">
      <div class="brand">
        Vega
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
        :class="['vtn-tab scalp-tab', { active: viewMode === 'scalp' }]"
        @click="$emit('update:viewMode', 'scalp')"
      >⏱ Scalp</button>
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
      <div class="pnl-badge" :class="dailyPnl.total.net >= 0 ? 'pnl-up' : 'pnl-dn'"
           :title="`Today — Swing: ₹${dailyPnl.swing.net >= 0 ? '+' : ''}${dailyPnl.swing.net.toFixed(0)} | Scalp: ₹${dailyPnl.scalp.net >= 0 ? '+' : ''}${dailyPnl.scalp.net.toFixed(0)} | Brokerage: ₹${dailyPnl.total.brokerage.toFixed(0)}`">
        <span class="pnl-badge-label">P&amp;L</span>
        <span class="pnl-badge-total" :class="dailyPnl.total.net >= 0 ? 'up' : 'dn'">
          {{ dailyPnl.total.net >= 0 ? '+' : '' }}₹{{ dailyPnl.total.net.toFixed(0) }}
        </span>
        <span class="pnl-badge-sep">|</span>
        <span class="pnl-badge-swing" :class="dailyPnl.swing.net >= 0 ? 'up' : 'dn'" title="Swing trades">S:{{ dailyPnl.swing.net >= 0 ? '+' : '' }}{{ dailyPnl.swing.net.toFixed(0) }}</span>
        <span class="pnl-badge-sep">·</span>
        <span class="pnl-badge-scalp" :class="dailyPnl.scalp.net >= 0 ? 'up' : 'dn'" title="Scalp trades">⏱{{ dailyPnl.scalp.net >= 0 ? '+' : '' }}{{ dailyPnl.scalp.net.toFixed(0) }}</span>
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
        class="mute-btn"
        :title="muted ? 'Unmute all sounds' : 'Mute all sounds'"
        @click="onToggleMute"
      >
        <svg v-if="!muted" xmlns="http://www.w3.org/2000/svg" width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><polygon points="11 5 6 9 2 9 2 15 6 15 11 19 11 5"/><path d="M19.07 4.93a10 10 0 0 1 0 14.14"/><path d="M15.54 8.46a5 5 0 0 1 0 7.07"/></svg>
        <svg v-else xmlns="http://www.w3.org/2000/svg" width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><polygon points="11 5 6 9 2 9 2 15 6 15 11 19 11 5"/><line x1="23" y1="9" x2="17" y2="15"/><line x1="17" y1="9" x2="23" y2="15"/></svg>
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
import { ref, onMounted, onUnmounted } from 'vue'
import { storeToRefs } from 'pinia'
import { useMarketStore } from '../../stores/useMarketStore'
import { searchTicker, getPnlSummary } from '../../api/market'
import { fmtPrice } from '../../utils/formatters'
import { isMuted, toggleMute } from '../../utils/notifSound'

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

// ── Daily P&L badge ──────────────────────────────────────────────────────────
const _zeroPnl = () => ({ trades: 0, gross: 0, brokerage: 0, net: 0 })
const dailyPnl = ref({ swing: _zeroPnl(), scalp: _zeroPnl(), total: _zeroPnl() })
let _pnlTimer = null

async function _fetchDailyPnl() {
  try {
    // axios interceptor returns response.data directly — res IS the payload, not {data: payload}
    const res = await getPnlSummary()
    if (res?.success) {
      const live = res.live || {}
      const d    = res.daily || {}
      const swingNet = live.swing != null ? live.swing : (d.swing?.net ?? 0)
      const scalpNet = live.scalp != null ? live.scalp : (d.scalp?.net ?? 0)
      dailyPnl.value = {
        swing:     { ...(d.swing  || _zeroPnl()), net: swingNet },
        scalp:     { ...(d.scalp  || _zeroPnl()), net: scalpNet },
        total:     {
          ...(d.total || _zeroPnl()),
          net: round2(swingNet + scalpNet),
          brokerage: d.total?.brokerage ?? 0,
        },
      }
    }
  } catch {}
}

function round2(v) { return Math.round(v * 100) / 100 }

// ── Mute toggle ─────────────────────────────────────────────────────────────
const muted = ref(isMuted())
function onToggleMute() { toggleMute(); muted.value = isMuted() }
function _onMuteChanged(e) { muted.value = !!e.detail }
onMounted(() => {
  window.addEventListener('vega:mute-changed', _onMuteChanged)
  _fetchDailyPnl()
  _pnlTimer = setInterval(_fetchDailyPnl, 30000)
})
onUnmounted(() => {
  window.removeEventListener('vega:mute-changed', _onMuteChanged)
  clearInterval(_pnlTimer)
})

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
