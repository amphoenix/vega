<template>
  <header class="topbar">
    <div class="topbar-left">
      <div class="brand">
        Vega
      </div>
      <div class="ticker-row" v-if="showSearch">
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
        <select
          v-if="viewMode === 'swing'"
          class="tf-select"
          :value="interval"
          @change="interval = $event.target.value"
        >
          <option v-for="tf in timeframes" :key="tf.v" :value="tf.v">{{ tf.l }}</option>
        </select>
        <button class="go-btn" @click="submitSearch">▶ Load</button>
      </div>
    </div>

    <div class="topbar-center">
      <button
        :class="['vtn-tab', { active: viewMode === 'swing' }]"
        @click="$emit('update:viewMode', 'swing')"
      >📊 Swing</button>
      <button
        :class="['vtn-tab scalp-tab', { active: viewMode === 'scalp' }]"
        @click="$emit('update:viewMode', 'scalp')"
      >⏱ Scalp</button>
      <button
        :class="['vtn-tab', { active: viewMode === 'crypto' }]"
        @click="$emit('update:viewMode', 'crypto')"
      >₿ Crypto</button>
      <!-- <button
        :class="['vtn-tab', { active: viewMode === 'crypto_fo' }]"
        @click="$emit('update:viewMode', 'crypto_fo')"
      >🔮 Crypto F&O</button> -->
      <button
        :class="['vtn-tab', { active: viewMode === 'poly' }]"
        @click="$emit('update:viewMode', 'poly')"
      >🎯 Poly</button>
      <button
        :class="['vtn-tab', { active: viewMode === 'forex' }]"
        @click="$emit('update:viewMode', 'forex')"
      >💱 Forex</button>
    </div>

    <div class="topbar-right">
      <div
        class="ind-status connected"
        v-if="brokerConnected"
        :title="brokerDisplayName + ' connected' + (brokerUserName ? ' — ' + brokerUserName : '')"
      >
        <span class="ind-dot"></span>
        <span class="ind-label">{{ brokerBadge }}</span>
      </div>
      <div class="ind-cash" v-if="brokerConnected && brokerAvailableCash != null" :title="'Available cash in your ' + brokerDisplayName + ' account'">
        <span class="ind-cash-label">Cash</span>
        <span class="ind-cash-val" :class="{ 'ind-cash-low': brokerAvailableCash < 1000 }">₹{{ fmtCash(brokerAvailableCash) }}</span>
      </div>
      <div class="pnl-badge-wrap" @click="pnlExpanded = !pnlExpanded" @mouseleave="pnlExpanded = false">
        <div class="pnl-badge" :class="segmentPnl.total.net >= 0 ? 'pnl-up' : 'pnl-dn'">
          <span class="pnl-badge-label">P&amp;L</span>
          <span v-if="segmentPnl.total.inr_net != null"
                class="pnl-badge-total" :class="(segmentPnl.total.inr_net || 0) >= 0 ? 'up' : 'dn'">
            ₹{{ (segmentPnl.total.inr_net || 0).toFixed(0) }}
          </span>
          <span v-if="segmentPnl.total.usd_net"
                class="pnl-badge-total" :class="(segmentPnl.total.usd_net || 0) >= 0 ? 'up' : 'dn'">
            ${{ (segmentPnl.total.usd_net || 0).toFixed(0) }}
          </span>
          <span v-if="!segmentPnl.total.inr_net && !segmentPnl.total.usd_net"
                class="pnl-badge-total" :class="segmentPnl.total.net >= 0 ? 'up' : 'dn'">
            ₹{{ segmentPnl.total.net.toFixed(0) }}
          </span>
          <span class="pnl-badge-trades">{{ segmentPnl.total.trades }}T</span>
          <span class="pnl-expand-icon">{{ pnlExpanded ? '▲' : '▼' }}</span>
        </div>
        <div class="pnl-dropdown" v-if="pnlExpanded">
          <div class="pnl-seg-row" v-for="seg in pnlSegments" :key="seg.key">
            <span class="pnl-seg-label">{{ seg.label }}</span>
            <span class="pnl-seg-cap" v-if="seg.data.capital" title="Allocated capital">
              {{ seg.data.currency === 'USD' ? '$' : '₹' }}{{ (seg.data.capital / 1000).toFixed(0) }}K
            </span>
            <span class="pnl-seg-trades">{{ seg.data.trades }}T</span>
            <span class="pnl-seg-net" :class="seg.data.net >= 0 ? 'up' : 'dn'">
              {{ seg.data.net >= 0 ? '+' : '' }}{{ seg.data.currency === 'USD' ? '$' : '₹' }}{{ seg.data.net.toFixed(seg.data.currency === 'USD' ? 2 : 0) }}
            </span>
            <span class="pnl-seg-roi" v-if="seg.data.roi_pct != null" :class="seg.data.roi_pct >= 0 ? 'up' : 'dn'">
              {{ seg.data.roi_pct >= 0 ? '+' : '' }}{{ seg.data.roi_pct.toFixed(2) }}%
            </span>
          </div>
          <div class="pnl-seg-row pnl-seg-total">
            <span class="pnl-seg-label">Total</span>
            <span class="pnl-seg-trades">{{ segmentPnl.total.trades }}T</span>
            <span v-if="segmentPnl.total.inr_net != null" class="pnl-seg-net" :class="segmentPnl.total.inr_net >= 0 ? 'up' : 'dn'">
              ₹{{ segmentPnl.total.inr_net.toFixed(0) }}
            </span>
            <span v-if="segmentPnl.total.usd_net" class="pnl-seg-net" :class="segmentPnl.total.usd_net >= 0 ? 'up' : 'dn'">
              ${{ segmentPnl.total.usd_net.toFixed(2) }}
            </span>
          </div>
        </div>
      </div>
      <button
        v-if="!brokerConnected"
        class="ind-btn"
        @click="$emit('broker-open')"
        :class="{ configured: brokerAvailable }"
        :title="brokerAvailable ? 'Token set — click to verify connection' : 'Configure broker credentials'"
      >
        <span class="ind-icon">📈</span>
        {{ brokerAvailable ? brokerDisplayName + ' ●' : 'Connect Broker' }}
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
    </div>
  </header>
</template>

<script setup>
import { ref, computed, onMounted, onUnmounted } from 'vue'
import { storeToRefs } from 'pinia'
import { useMarketStore } from '../../stores/useMarketStore'
import { searchTicker, getPnlSummary } from '../../api/market'
import { isMuted, toggleMute } from '../../utils/notifSound'
import { onOrderEvent } from '../../composables/useSSE'

function fmtCash(v) {
  if (v == null) return '—'
  if (v >= 100000) return (v / 100000).toFixed(1) + 'L'
  if (v >= 1000) return (v / 1000).toFixed(1) + 'K'
  return v.toFixed(0)
}

const props = defineProps({
  viewMode: { type: String, default: 'swing' },
  lightMode: { type: Boolean, default: false },
  brokerConnected: { type: Boolean, default: false },
  brokerName: { type: String, default: '' },
  brokerUserName: { type: String, default: '' },
  brokerLivePrice: { type: Number, default: null },
  brokerAvailable: { type: Boolean, default: false },
  brokerAvailableCash: { type: Number, default: null },
})

const _BROKER_LABELS = {
  dhan: 'DHAN', indmoney: 'IND', zerodha: 'ZRDA', groww: 'GROW',
}
const brokerBadge = computed(() => _BROKER_LABELS[props.brokerName?.toLowerCase()] || props.brokerName?.toUpperCase()?.slice(0, 4) || 'N/A')
const brokerDisplayName = computed(() => {
  const n = props.brokerName?.toLowerCase()
  if (n === 'indmoney') return 'INDmoney'
  if (n === 'dhan') return 'Dhan'
  return props.brokerName || 'Broker'
})

const emit = defineEmits(['update:viewMode', 'toggle-theme', 'broker-open', 'select-ticker'])

const { activeTicker, interval, searchSuggestions } = storeToRefs(useMarketStore())

const timeframes = [
  { l: '5M',  v: '5m'  },
  { l: '30M', v: '30m' },
  { l: '1H',  v: '1h'  },
  { l: '1D',  v: '1d'  },
  { l: '1W',  v: '1wk' },
  { l: '1Y',  v: '1y'  },
]

// ── Search bar visibility ────────────────────────────────────────────────────
const showSearch = computed(() => ['swing', 'scalp'].includes(props.viewMode))

// ── Daily P&L badge ──────────────────────────────────────────────────────────
const _zeroPnl = () => ({ trades: 0, gross: 0, brokerage: 0, net: 0, capital: 0, roi_pct: 0 })
const pnlExpanded = ref(false)
const segmentPnl = ref({
  swing: _zeroPnl(), scalp: _zeroPnl(),
  crypto: _zeroPnl(), crypto_fo: _zeroPnl(), poly: _zeroPnl(), forex: _zeroPnl(),
  total: _zeroPnl(),
})

const pnlSegments = computed(() => [
  { key: 'swing',     label: 'Swing',     data: segmentPnl.value.swing },
  { key: 'scalp',     label: 'Scalp',     data: segmentPnl.value.scalp },
  { key: 'crypto',    label: 'Crypto',    data: segmentPnl.value.crypto },
  // { key: 'crypto_fo', label: 'Crypto F&O', data: segmentPnl.value.crypto_fo },
  { key: 'poly',      label: 'Poly',      data: segmentPnl.value.poly },
  { key: 'forex',     label: 'Forex',     data: segmentPnl.value.forex },
])

async function _fetchDailyPnl() {
  try {
    const res = await getPnlSummary()
    if (res?.success) {
      const segs = res.segments || {}
      segmentPnl.value = {
        swing:     segs.swing     || _zeroPnl(),
        scalp:     segs.scalp     || _zeroPnl(),
        crypto:    segs.crypto    || _zeroPnl(),
        crypto_fo: segs.crypto_fo || _zeroPnl(),
        poly:      segs.poly      || _zeroPnl(),
        forex:     segs.forex     || _zeroPnl(),
        total:     segs.total     || _zeroPnl(),
      }
    }
  } catch {}
}

// ── Live PnL via shared SSE (order events) ────────────────────────────────
onOrderEvent((d) => {
  if (d.type === 'order_events_connected') return
  const txt = ((d.title || '') + (d.status || '') + (d.type || '')).toUpperCase()
  if (/EXIT|SL_HIT|T1|T2|FILLED|COMPLETE/i.test(txt)) {
    setTimeout(_fetchDailyPnl, 1500)
  }
})

// ── Mute toggle ─────────────────────────────────────────────────────────────
const muted = ref(isMuted())
function onToggleMute() { toggleMute(); muted.value = isMuted() }
function _onMuteChanged(e) { muted.value = !!e.detail }
onMounted(() => {
  window.addEventListener('vega:mute-changed', _onMuteChanged)
  window.addEventListener('vega:pnl-changed', _fetchDailyPnl)
  _fetchDailyPnl()
})
onUnmounted(() => {
  window.removeEventListener('vega:mute-changed', _onMuteChanged)
  window.removeEventListener('vega:pnl-changed', _fetchDailyPnl)
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
