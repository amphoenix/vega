<template>
  <div class="scalp-view">
    <!-- ═══ HEADER ═══════════════════════════════════════════════════════════ -->
    <div class="scalp-header">
      <div class="scalp-title-row">
        <div class="scalp-title-left">
          <h2 class="scalp-title">⏱ SCALP MODE</h2>
          <span class="mode-badge" :class="config.live_trading ? 'live' : 'paper'">
            {{ config.live_trading ? 'LIVE' : 'PAPER' }}
          </span>
        </div>
        <div class="scalp-controls">
          <label class="scalp-toggle" :title="autoTrading ? 'Auto-execute trades' : 'Monitor only — signals but no orders'">
            <input type="checkbox" v-model="autoTrading" @change="toggleAutoTrading" />
            <span class="scalp-toggle-slider"></span>
            <span class="scalp-toggle-label">{{ autoTrading ? 'Auto' : 'Monitor' }}</span>
          </label>
          <div class="scanner-pill running">
            <span class="pill-dot"></span>
            <span>Scanner ON</span>
          </div>
        </div>
      </div>

      <!-- ═══ P&L STRIP (like swing) ═════════════════════════════════════════ -->
      <div class="scalp-pnl-strip">
        <span class="scalp-pnl-label">Today's P&amp;L</span>
        <span :class="['scalp-pnl-realized', scalpPnl.realized >= 0 ? 'up' : 'dn']"
          title="Realized P&L from closed scalp trades"
          >Realized: {{ scalpPnl.realized >= 0 ? '+' : '' }}₹{{ scalpPnl.realized.toFixed(0) }}</span>
        <span :class="['scalp-pnl-unrealized', scalpPnl.unrealized >= 0 ? 'up' : 'dn']"
          title="Unrealized P&L from open scalp positions"
          >Open: {{ scalpPnl.unrealized >= 0 ? '+' : '' }}₹{{ scalpPnl.unrealized.toFixed(0) }}</span>
        <span :class="['scalp-pnl-net', scalpPnl.net >= 0 ? 'up' : 'dn']"
          title="Net = Realized + Unrealized"
          >Net: {{ scalpPnl.net >= 0 ? '+' : '' }}₹{{ scalpPnl.net.toFixed(0) }}</span>
        <span class="scalp-pnl-limit"
          :title="`Daily loss limit: ₹${config.daily_loss || 500}. Kill switch ${stats.kill_switch ? 'ACTIVE' : 'off'}`"
          >Limit ₹{{ config.daily_loss || 500 }}</span>
        <span v-if="stats.kill_switch" class="scalp-kill-badge">KILL SWITCH</span>
        <button v-if="stats.kill_switch" class="scalp-reset-ks-btn" @click="resetKillSwitch" title="Re-enable scalp trading (P&L not reset)">Reset Kill Switch</button>
      </div>

      <!-- ═══ STATS STRIP ════════════════════════════════════════════════════ -->
      <div class="scalp-stats-strip">
        <div class="stat-item">
          <span class="stat-label">Trades</span>
          <span class="stat-value">{{ stats.trades_today || 0 }}</span>
        </div>
        <div class="stat-item">
          <span class="stat-label">Avg Hold</span>
          <span class="stat-value">{{ formatHoldTime(stats.avg_hold_sec) }}</span>
        </div>
        <div class="stat-item">
          <span class="stat-label">SL</span>
          <span class="stat-value">{{ config.sl_pts || 8 }}pts</span>
        </div>
        <div class="stat-item">
          <span class="stat-label">T1</span>
          <span class="stat-value">{{ config.t1_pts || 15 }}pts</span>
        </div>
        <div class="stat-item">
          <span class="stat-label">Max Hold</span>
          <span class="stat-value">{{ config.max_hold_min || 10 }}m</span>
        </div>
      </div>
    </div>

    <!-- ═══ MAIN: CHART GRID + SIGNALS/POSITIONS ═════════════════════════════ -->
    <div class="scalp-main">
      <!-- LEFT: 3-pane chart grid (NIFTY, SENSEX, active scalp ticker) -->
      <div class="scalp-chart-col">
        <div :class="['scalp-chart-grid', { 'three-pane': !!activeScalpTicker }]">
          <!-- Pane 1: NIFTY 50 -->
          <div class="chart-pane">
            <div class="pane-hdr">
              <span class="pane-sym">NIFTY 50</span>
              <span class="pane-ltp" v-if="niftyPrice">₹{{ fmtPrice(niftyPrice) }}</span>
            </div>
            <HomeChart
              chartTicker="^NSEI"
              interval="5m"
              :indmoneyLivePrice="niftyPrice"
              :lightMode="false"
              currencySymbol="₹"
              :marketOpen="true"
            />
          </div>
          <!-- Pane 2: SENSEX -->
          <div class="chart-pane">
            <div class="pane-hdr">
              <span class="pane-sym">SENSEX</span>
              <span class="pane-ltp" v-if="sensexPrice">₹{{ fmtPrice(sensexPrice) }}</span>
            </div>
            <HomeChart
              chartTicker="^BSESN"
              interval="5m"
              :indmoneyLivePrice="sensexPrice"
              :lightMode="false"
              currencySymbol="₹"
              :marketOpen="true"
            />
          </div>
          <!-- Pane 3: Active scalp ticker -->
          <div class="chart-pane" v-if="activeScalpTicker">
            <div class="pane-hdr">
              <span class="pane-sym">{{ cleanTicker(activeScalpTicker) }}</span>
              <span class="pane-ltp" v-if="activeScalpPrice">₹{{ fmtPrice(activeScalpPrice) }}</span>
            </div>
            <HomeChart
              :chartTicker="activeScalpTicker"
              interval="5m"
              :indmoneyLivePrice="activeScalpPrice"
              :lightMode="false"
              currencySymbol="₹"
              :marketOpen="true"
            />
          </div>
        </div>
      </div>

      <!-- RIGHT: Signals + Positions stacked -->
      <div class="scalp-right-col">
        <!-- MOMENTUM SIGNALS -->
        <div class="scalp-feed">
          <h3 class="section-title">MOMENTUM SIGNALS</h3>
          <div class="signal-list" v-if="signals.length">
            <div
              v-for="sig in signals"
              :key="sig.ticker + sig.timestamp"
              class="scalp-signal-card"
              :class="sig.direction === 'BUY' ? 'bullish' : 'bearish'"
              @click="focusTicker(sig.ticker)"
            >
              <div class="sig-header">
                <span class="sig-ticker">{{ cleanTicker(sig.ticker) }}</span>
                <span class="sig-direction" :class="sig.direction.toLowerCase()">{{ sig.direction }}</span>
                <span class="sig-conf">{{ sig.confidence }}%</span>
              </div>
              <div class="sig-details">
                <span class="sig-detail">{{ sig.instrument_type }}</span>
                <span class="sig-detail">Spot ₹{{ fmtNum(sig.spot) }}</span>
                <span class="sig-detail" v-if="sig.vol_ratio">Vol {{ sig.vol_ratio }}×</span>
                <span class="sig-detail" v-if="sig.rsi">RSI {{ sig.rsi }}</span>
              </div>
              <div class="sig-ticket" v-if="sig.ticket">
                <span class="sig-strike">{{ sig.ticket.display_symbol || sig.ticket.trading_symbol }}</span>
                <span class="sig-premium">₹{{ fmtNum(sig.ticket.entry?.expected_premium_inr) }}</span>
              </div>
              <div class="sig-time">{{ formatTime(sig.timestamp) }}</div>
            </div>
          </div>
          <div class="no-signals" v-else>
            <div class="no-signals-icon">⏱</div>
            <p>Scanning for momentum breakouts…</p>
          </div>
        </div>

        <!-- ACTIVE SCALP POSITIONS -->
        <div class="scalp-positions">
          <h3 class="section-title">ACTIVE POSITIONS ({{ scalpCards.length }})</h3>
          <div class="position-list" v-if="scalpCards.length">
            <div
              v-for="card in scalpCards"
              :key="card.id"
              :class="['scalp-pos-card', 'scalp-pos-' + card.status]"
            >
              <div class="pos-header">
                <span class="pos-symbol">{{ card.display_symbol }}</span>
                <span :class="['pos-status-badge', 'pos-status-' + card.status]">{{ card.statusLabel }}</span>
                <button class="pos-force-exit-btn" @click="forceExit(card.id)" title="Force exit — sells at market immediately">
                  Force Exit
                </button>
              </div>
              <div class="pos-prices-row">
                <span class="pos-entry-tag">entry <b>₹{{ card.entry_premium.toFixed(2) }}</b></span>
                <span class="pos-now-tag">now <b :class="card.pnl_pct >= 0 ? 'up' : 'dn'">₹{{ card.now_premium.toFixed(2) }}</b></span>
                <span :class="['pos-pnl-tag', card.pnl_pct >= 0 ? 'up' : 'dn']"
                  :title="`P&L = (now − entry) × qty ${card.qty}`"
                >
                  {{ card.pnl_inr >= 0 ? '+' : '' }}₹{{ card.pnl_inr.toFixed(0) }}
                  <small>({{ card.pnl_pct >= 0 ? '+' : '' }}{{ card.pnl_pct.toFixed(1) }}%)</small>
                </span>
              </div>
              <div class="pos-levels">
                <span class="pos-sl">SL ₹{{ card.sl.toFixed(2) }}</span>
                <span class="pos-t1">T1 ₹{{ card.t1.toFixed(2) }}</span>
                <span class="pos-t2">T2 ₹{{ card.t2.toFixed(2) }}</span>
                <span class="pos-qty">Qty {{ card.qty }}</span>
              </div>
              <div class="pos-timer">
                <div class="timer-bar">
                  <div class="timer-fill" :style="{ width: holdPct(card.pos) + '%' }"></div>
                </div>
                <span class="timer-text">{{ holdTimeStr(card.pos) }} / {{ card.pos.ticket?.scalp_meta?.max_hold_min || 10 }}m</span>
              </div>
            </div>
          </div>
          <div class="no-positions" v-else>
            <div class="no-positions-icon">📭</div>
            <p>No active scalp positions</p>
          </div>
        </div>
      </div>
    </div>

    <!-- ═══ LIVE TRADE FEED ══════════════════════════════════════════════════ -->
    <div class="scalp-log">
      <h3 class="section-title">LIVE TRADE FEED</h3>
      <div class="log-entries">
        <div v-for="(ev, i) in tradeFeed.slice(-30).reverse()" :key="i" class="log-entry" :class="feedClass(ev)">
          <span class="log-time">{{ formatTime(ev.timestamp) }}</span>
          <span class="log-badge" :class="feedClass(ev)">{{ ev.status || ev.type }}</span>
          <span class="log-msg">{{ ev.title || ev.message || ev.type }}</span>
          <span class="log-sub" v-if="ev.message && ev.title">{{ ev.message }}</span>
        </div>
        <div v-if="!tradeFeed.length" class="log-empty">
          No trade events yet — {{ config.live_trading ? 'LIVE' : 'PAPER' }} mode active
        </div>
      </div>
    </div>
  </div>
</template>

<script setup>
import { ref, computed, watch, onMounted, onUnmounted } from 'vue'
import { storeToRefs } from 'pinia'
import { useLiveTradingStore } from '../../stores/useLiveTradingStore'
import HomeChart from '../chart/HomeChart.vue'
import { fmtPrice } from '../../utils/formatters'
import { snack } from '../../utils/snack'
import { playNotifSound } from '../../utils/notifSound'
import {
  getScalpScannerStatus,
  getScalpScannerConfig,
  createScalpStream,
} from '../../api/market'

const { liveSpots, liveSpotTickAt, trackedPositions: storeTrackedPositions } = storeToRefs(useLiveTradingStore())

// ── State ────────────────────────────────────────────────────────────────────
const autoTrading = ref(true)
const signals = ref([])
const stats = ref({})
const config = ref({})
const tradeFeed = ref([])
const activeScalpTicker = ref(null) // 3rd chart pane ticker (from signal or active position)

let scalpSSE = null
let orderSSE = null
let timerTick = null
let positionPoll = null
// Registry of SSE tick streams opened by this component
const _ownStreams = {}

// ── Live prices from store (no polling) ──────────────────────────────────────
const niftyPrice  = computed(() => Number(liveSpots.value['^NSEI']) || null)
const sensexPrice = computed(() => Number(liveSpots.value['^BSESN']) || null)
const activeScalpPrice = computed(() => {
  if (!activeScalpTicker.value) return null
  return Number(liveSpots.value[activeScalpTicker.value]) || null
})

// ── Computed ─────────────────────────────────────────────────────────────────
const scalpPositions = computed(() =>
  (storeTrackedPositions.value || []).filter(p =>
    p.ticket?.trade_mode === 'scalp' || p.ticket?.scalp_meta
  )
)

// ── Per-card P&L (like swing trackedCards) ────────────────────────────────────
const scalpCards = computed(() => {
  return scalpPositions.value.map(pos => {
    const t = pos.ticket || {}
    const entry_prem = Number(t.entry?.expected_premium_inr) || 0
    const sym = t.trading_symbol || ''
    const opt_ltp = Number(liveSpots.value[sym]) || 0
    const now_premium = opt_ltp || entry_prem
    const qty = Number(pos.qty) || Number(t.lot_size) || 1
    const pnl_inr = (now_premium - entry_prem) * qty
    const pnl_pct = entry_prem ? ((now_premium - entry_prem) / entry_prem) * 100 : 0
    const sl = Number(t.exit?.stop_loss_inr) || 0
    const t1 = Number(t.exit?.target_1_inr) || 0
    const t2 = Number(t.exit?.target_2_inr) || 0

    let status = 'safe', statusLabel = 'OK'
    if (now_premium <= sl && sl > 0) { status = 'sl_hit'; statusLabel = 'SL HIT' }
    else if (t2 > 0 && now_premium >= t2) { status = 'past_t2'; statusLabel = 'T2 HIT' }
    else if (t1 > 0 && now_premium >= t1) { status = 'past_t1'; statusLabel = 'T1 HIT' }
    else if (sl > 0 && now_premium <= sl * 1.1) { status = 'near_sl'; statusLabel = 'NEAR SL' }
    else if (t1 > 0 && now_premium >= t1 * 0.92) { status = 'near_t1'; statusLabel = 'NEAR T1' }

    return {
      id: pos.id,
      pos,
      display_symbol: t.display_symbol || t.trading_symbol || '—',
      entry_premium: entry_prem,
      now_premium,
      qty, sl, t1, t2,
      pnl_inr, pnl_pct,
      status, statusLabel,
    }
  })
})


const scalpPnl = computed(() => {
  const realized = stats.value.daily_pnl || 0
  let unrealized = 0
  for (const card of scalpCards.value) {
    unrealized += card.pnl_inr || 0
  }
  return {
    realized,
    unrealized,
    net: realized + unrealized,
  }
})

// ── Formatting helpers ───────────────────────────────────────────────────────
function fmtNum(v) {
  if (v == null) return '—'
  return Number(v).toFixed(2)
}

function cleanTicker(t) {
  return (t || '').replace(/\.(NS|BO)$/i, '').replace(/^\^/, '')
}

function formatTime(ts) {
  if (!ts) return ''
  try {
    const d = new Date(ts)
    return d.toLocaleTimeString('en-IN', { hour: '2-digit', minute: '2-digit', second: '2-digit' })
  } catch { return '' }
}

function formatHoldTime(sec) {
  if (!sec) return '0s'
  const m = Math.floor(sec / 60)
  const s = Math.round(sec % 60)
  return m > 0 ? `${m}m${s}s` : `${s}s`
}

function holdPct(pos) {
  const meta = pos.ticket?.scalp_meta
  if (!meta?.entered_at) return 0
  const max = (meta.max_hold_min || 10) * 60
  const elapsed = (Date.now() - new Date(meta.entered_at).getTime()) / 1000
  return Math.min(100, (elapsed / max) * 100)
}

function holdTimeStr(pos) {
  const meta = pos.ticket?.scalp_meta
  if (!meta?.entered_at) return '0:00'
  const elapsed = Math.floor((Date.now() - new Date(meta.entered_at).getTime()) / 1000)
  const m = Math.floor(elapsed / 60)
  const s = elapsed % 60
  return `${m}:${String(s).padStart(2, '0')}`
}

function feedClass(ev) {
  const s = (ev.status || ev.severity || ev.type || '').toLowerCase()
  if (s.includes('success') || s.includes('scalp_entry') || s === 'buy') return 'feed-success'
  if (s.includes('error') || s.includes('fail') || s === 'sl_hit') return 'feed-error'
  if (s.includes('simul') || s.includes('paper')) return 'feed-paper'
  if (s.includes('exit') || s === 'sell') return 'feed-exit'
  return 'feed-info'
}

// ── Open live SSE tick stream for a symbol ───────────────────────────────────
function _openTickStream(symbol) {
  if (!symbol || _ownStreams[symbol]) return
  // Also skip if LiveTradingPanel already has it (shared via window)
  if (typeof window !== 'undefined' && window.__vegaTicketStreams?.[symbol]) return
  const base = import.meta.env.VITE_API_BASE_URL || 'https://localhost:47291'
  const es = new EventSource(`${base}/api/indmoney/stream/${encodeURIComponent(symbol)}`)
  es.onmessage = (e) => {
    try {
      const m = JSON.parse(e.data)
      if (m.heartbeat || m.error) return
      const p = Number(m.price ?? m.ltp ?? m.last_price)
      if (p > 0) {
        liveSpots.value = { ...liveSpots.value, [symbol]: p }
        liveSpotTickAt.value = { ...liveSpotTickAt.value, [symbol]: Date.now() }
      }
    } catch {}
  }
  _ownStreams[symbol] = es
}

function _closeOwnStreams() {
  for (const [sym, es] of Object.entries(_ownStreams)) {
    try { es.close() } catch {}
    delete _ownStreams[sym]
  }
}

// Clicking a signal card focuses it as 3rd chart pane
function focusTicker(ticker) {
  if (!ticker) return
  const ALWAYS_ON = new Set(['^NSEI', '^BSESN'])
  if (ALWAYS_ON.has(ticker)) return
  activeScalpTicker.value = ticker
  _openTickStream(ticker)
}


async function resetKillSwitch() {
  try {
    const base = import.meta.env.VITE_API_BASE_URL || 'https://localhost:47291'
    const res = await fetch(`${base}/api/trade/scalp-scanner/reset-killswitch`, { method: 'POST' })
    const data = await res.json()
    if (data.success) {
      const msg = data.new_limit ? `Re-enabled — new limit ₹${data.new_limit}` : 'Scalp trading re-enabled'
      snack({ severity: 'success', title: 'Kill Switch', message: msg })
      if (data.stats) stats.value = { ...stats.value, ...data.stats }
      if (data.new_limit) config.value = { ...config.value, daily_loss: data.new_limit }
    } else {
      snack({ severity: 'error', title: 'Kill Switch', message: data.message || 'Reset failed' })
    }
  } catch (e) {
    console.error('[scalp] resetKillSwitch failed:', e)
    snack({ severity: 'error', title: 'Kill Switch', message: 'Reset failed — check console' })
  }
}

async function toggleAutoTrading() {
  try {
    const service = (await import('../../api/index')).default
    await service.post('/api/trade/executor/scalp-auto-trading', { enabled: autoTrading.value })
    snack({ severity: 'info', title: 'Scalp Mode', message: autoTrading.value ? 'Auto-execute ON — trades will fire' : 'Monitor only — no orders' })
  } catch {
    snack({ severity: 'error', title: 'Scalp Mode', message: 'Failed to toggle auto-trading' })
  }
}

async function forceExit(trackId) {
  try {
    const service = (await import('../../api/index')).default
    await service.post(`/api/trade/executor/force-exit/${trackId}`)
    snack({ severity: 'warning', title: 'Force Exit', message: 'Selling at market — executing now' })
    setTimeout(async () => {
      await _loadTrackedPositions()
      await _refreshScalpStats()
    }, 2000)
  } catch {
    snack({ severity: 'error', title: 'Force Exit', message: 'Force exit failed' })
  }
}

// ── Notifications (snackbar + desktop + sound) ──────────────────────────────
function _desktopNotify(title, body) {
  if (typeof Notification === 'undefined' || Notification.permission !== 'granted') return
  try {
    const n = new Notification(title, { body, icon: '/favicon.ico' })
    n.onclick = () => { window.focus(); n.close() }
  } catch {}
}

function _notifySignal(sig) {
  const msg = `${sig.ticker} ${sig.direction} ${sig.instrument_type} conf=${sig.confidence}%`
  snack({ severity: 'info', title: '⚡ Scalp Signal', message: msg, sound: false })
  playNotifSound('signal')
  _desktopNotify('⚡ Scalp Signal', msg)
}

function _notifyOrderEvent(data) {
  const status = (data.status || '').toUpperCase()
  const title = data.title || `Scalp ${status}`
  const msg = data.message || data.trading_symbol || ''

  let severity = 'info'
  let sound = 'info'
  if (status.includes('FILL') || status.includes('BOUGHT') || status.includes('ENTRY')) {
    severity = 'success'; sound = 'entry_buy'
  } else if (status.includes('SOLD') || status.includes('EXIT') || status.includes('T1') || status.includes('T2')) {
    severity = 'success'; sound = 'exit_sell'
  } else if (status.includes('SL') || status.includes('STOP')) {
    severity = 'error'; sound = 'sl_hit'
  } else if (status.includes('PAPER') || status.includes('SIMULATED')) {
    severity = 'info'; sound = 'entry_buy'
  } else if (status.includes('REJECT') || status.includes('FAIL')) {
    severity = 'error'; sound = 'error'
  }

  snack({ severity, title, message: msg, sound: false })
  playNotifSound(sound)
  _desktopNotify(title, msg)
}

// ── SSE: scalp scanner signals + stats (replaces all polling) ────────────────
function connectScalpSSE() {
  if (scalpSSE) { scalpSSE.close(); scalpSSE = null }
  scalpSSE = createScalpStream()
  scalpSSE.onmessage = (ev) => {
    try {
      const data = JSON.parse(ev.data)
      switch (data.type) {
        case 'scalp_signal':
          signals.value = [data, ...signals.value].slice(0, 50)
          if (!data._replay) _notifySignal(data)
          // Auto-focus the first signal as 3rd chart pane
          if (!activeScalpTicker.value) focusTicker(data.ticker)
          break
        case 'scalp_scan_complete':
          if (data.stats) stats.value = data.stats
          break
        case 'scalp_state':
          if (data.stats) stats.value = data.stats
          if (data.config) config.value = data.config
          if (data.signals?.length && !signals.value.length) signals.value = data.signals
          break
        case 'heartbeat':
          break
      }
    } catch {}
  }
  scalpSSE.onerror = () => {
    setTimeout(() => connectScalpSSE(), 3000)
  }
}

// ── SSE: order events (entries, exits, paper trades) ─────────────────────────
function connectOrderSSE() {
  if (orderSSE) { orderSSE.close(); orderSSE = null }
  const base = import.meta.env.VITE_API_BASE_URL || 'https://localhost:47291'
  orderSSE = new EventSource(`${base}/api/indmoney/order-events/stream`)
  orderSSE.onmessage = (ev) => {
    try {
      const data = JSON.parse(ev.data)
      if (data.type === 'order_events_connected') return
      const status = (data.status || '').toUpperCase()
      const title = (data.title || '').toUpperCase()
      if (status.includes('SCALP') || title.includes('SCALP') ||
          status.includes('PAPER') || status.includes('SIMULATED') ||
          data.type === 'order_update') {
        tradeFeed.value.push({ ...data, timestamp: data.timestamp || new Date().toISOString() })
        if (tradeFeed.value.length > 100) tradeFeed.value = tradeFeed.value.slice(-100)
        _notifyOrderEvent(data)
      }
    } catch {}
  }
  orderSSE.onerror = () => {
    setTimeout(() => connectOrderSSE(), 5000)
  }
}

// ── Load tracked positions (independent of LiveTradingPanel) ──────────────────
async function _loadTrackedPositions() {
  try {
    const base = import.meta.env.VITE_API_BASE_URL || 'https://localhost:47291'
    const r = await fetch(`${base}/api/trade/tracked`).then(x => x.json())
    if (r.success) storeTrackedPositions.value = r.data || []
  } catch (e) {
    console.warn('[scalp] _loadTrackedPositions failed', e)
  }
}

async function _refreshScalpStats() {
  try {
    const res = await getScalpScannerStatus()
    const d = res.data?.data || res.data || {}
    if (d.stats) stats.value = d.stats
  } catch {}
}

// ── Initial load (once, then everything is SSE-driven) ───────────────────────
async function loadInitialState() {
  try {
    const [statusRes, configRes] = await Promise.all([
      getScalpScannerStatus(),
      getScalpScannerConfig(),
      _loadTrackedPositions(),
    ])
    const d = statusRes.data?.data || statusRes.data || {}
    stats.value = d.stats || {}
    if (d.signals?.length) signals.value = d.signals

    const cfg = configRes.data?.data || configRes.data || {}
    config.value = cfg
    autoTrading.value = cfg.auto_trading !== false
  } catch {}
}

// Auto-set 3rd pane from active scalp positions + open tick streams for option prices
watch(scalpPositions, (positions) => {
  if (positions.length && !activeScalpTicker.value) {
    const under = positions[0].ticket?.underlying
    if (under) focusTicker(under)
  }
  // Open tick streams for each scalp position's trading_symbol for live P&L
  for (const pos of positions) {
    const sym = pos.ticket?.trading_symbol
    if (sym) _openTickStream(sym)
  }
}, { immediate: true })

// ── Lifecycle ────────────────────────────────────────────────────────────────
onMounted(async () => {
  // One-time state load
  await loadInitialState()

  // Open live tick streams for the always-on charts
  _openTickStream('^NSEI')
  _openTickStream('^BSESN')

  // Connect SSE streams (everything live from here on)
  connectScalpSSE()
  connectOrderSSE()

  // Timer tick for hold-time bars (update every second)
  timerTick = setInterval(() => {
    // Force reactivity update for position timers
    storeTrackedPositions.value = [...storeTrackedPositions.value]
  }, 1000)

  // Poll tracked positions every 5s for P&L updates
  positionPoll = setInterval(_loadTrackedPositions, 5000)
})

onUnmounted(() => {
  if (scalpSSE) { scalpSSE.close(); scalpSSE = null }
  if (orderSSE) { orderSSE.close(); orderSSE = null }
  if (timerTick) clearInterval(timerTick)
  if (positionPoll) clearInterval(positionPoll)
  _closeOwnStreams()
})
</script>

<style src="../../styles/ScalpPanel.css"></style>
