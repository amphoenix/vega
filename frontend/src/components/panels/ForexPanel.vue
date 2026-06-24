<template>
  <div class="forex-view">
    <!-- ═══ HEADER ═══════════════════════════════════════════════════════════ -->
    <div class="forex-header">
      <div class="forex-title-row">
        <h2 class="forex-title">💱 FOREX</h2>
        <span class="mode-badge paper">PAPER</span>
        <span class="forex-status" :class="connected ? 'online' : 'offline'">
          <span class="status-dot"></span>
          {{ connected ? 'Live' : 'Offline' }}
        </span>
        <span class="forex-source">NSE CDS</span>
      </div>
    </div>

    <!-- ═══ P&L STRIP ═══════════════════════════════════════════════════════ -->
    <div class="forex-pnl-strip">
      <span class="fx-pnl-label">Today's P&amp;L</span>
      <span :class="['fx-pnl-val', forexPnl.realized >= 0 ? 'up' : 'dn']"
        title="Realized P&L from closed forex trades (incl. brokerage)"
        >Realized: {{ forexPnl.realized >= 0 ? '+' : '' }}₹{{ forexPnl.realized.toFixed(2) }}</span>
      <span :class="['fx-pnl-val', forexPnl.unrealized >= 0 ? 'up' : 'dn']"
        title="Unrealized P&L from open forex positions"
        >Open: {{ forexPnl.unrealized >= 0 ? '+' : '' }}₹{{ forexPnl.unrealized.toFixed(2) }}</span>
      <span :class="['fx-pnl-net', forexPnl.net >= 0 ? 'up' : 'dn']"
        title="Net = Realized + Unrealized"
        >Net: {{ forexPnl.net >= 0 ? '+' : '' }}₹{{ forexPnl.net.toFixed(2) }}</span>
      <span class="fx-pnl-count">{{ forexPositions.length }} open</span>
    </div>

    <div class="forex-body">
      <!-- LEFT: Pair watchlist -->
      <aside class="forex-sidebar">
        <div class="forex-watchlist">
          <div
            v-for="pair in PAIRS"
            :key="pair"
            :class="['fw-item', { active: pair === activePair }]"
            @click="selectPair(pair)"
          >
            <span class="fw-symbol">{{ pairFullName(pair) }}</span>
            <span
              v-if="tickers[pair]"
              class="fw-price"
              :class="(tickers[pair]?.change_pct ?? 0) >= 0 ? 'up' : 'dn'"
            >{{ formatPrice(tickers[pair]?.last) }}</span>
            <span
              v-if="tickers[pair]"
              class="fw-chg"
              :class="(tickers[pair]?.change_pct ?? 0) >= 0 ? 'up' : 'dn'"
            >
              {{ (tickers[pair]?.change_pct ?? 0) >= 0 ? '+' : '' }}{{ (tickers[pair]?.change_pct ?? 0).toFixed(2) }}%
            </span>
          </div>
        </div>
      </aside>

      <!-- CENTER: Ticker strip + Chart -->
      <section class="forex-center">
        <div class="forex-ticker-strip" v-if="ticker">
          <span class="ft-pair">{{ pairFullName(activePair) }}</span>
          <span class="ft-price" :class="(ticker.change_pct ?? 0) >= 0 ? 'up' : 'dn'">
            {{ formatPrice(ticker.last) }}
          </span>
          <span class="ft-change" :class="(ticker.change_pct ?? 0) >= 0 ? 'up' : 'dn'">
            {{ (ticker.change_pct ?? 0) >= 0 ? '+' : '' }}{{ (ticker.change_pct ?? 0).toFixed(4) }}%
          </span>
          <span class="ft-bid">Bid: {{ formatPrice(ticker.bid) }}</span>
          <span class="ft-ask">Ask: {{ formatPrice(ticker.ask) }}</span>
        </div>
        <div class="forex-ticker-strip" v-else>
          <span class="ft-pair">{{ pairFullName(activePair) }}</span>
          <span class="ft-loading">Loading…</span>
        </div>
        <div class="forex-chart-wrap">
          <ForexChart
            :pair="activePair"
            :interval="chartInterval"
            :live-price="ticker?.last ?? null"
          />
        </div>
      </section>

      <!-- RIGHT: Rate info panel -->
      <aside class="forex-right">
        <div class="fr-panel">
          <div class="fr-header">RATE INFO</div>
          <div class="fr-body" v-if="ticker">
            <div class="ri-row">
              <span
                class="ri-label"
                title="Current exchange rate (how much 1 unit of base currency costs in quote currency)"
              >Price</span>
              <span class="ri-val" :class="(ticker.change_pct ?? 0) >= 0 ? 'up' : 'dn'">
                {{ formatPrice(ticker.last) }}
              </span>
            </div>
            <div class="ri-row">
              <span
                class="ri-label"
                title="How much the rate moved since yesterday's close"
              >Change 24h</span>
              <span class="ri-val" :class="(ticker.change_pct ?? 0) >= 0 ? 'up' : 'dn'">
                {{ (ticker.change_pct ?? 0) >= 0 ? '+' : '' }}{{ (ticker.change_pct ?? 0).toFixed(4) }}%
              </span>
            </div>
            <div class="ri-row">
              <span class="ri-label">Bid</span>
              <span class="ri-val">{{ formatPrice(ticker.bid) }}</span>
            </div>
            <div class="ri-row">
              <span class="ri-label">Ask</span>
              <span class="ri-val">{{ formatPrice(ticker.ask) }}</span>
            </div>
            <div class="ri-row">
              <span
                class="ri-label"
                title="Difference between buy and sell price — lower is better"
              >Spread</span>
              <span class="ri-val ri-spread">{{ formatPrice(ticker.spread) }}</span>
            </div>
            <div class="ri-divider"></div>
            <div class="ri-row">
              <span
                class="ri-label"
                title="Today's highest and lowest price"
              >High</span>
              <span class="ri-val up">{{ formatPrice(ticker.high) }}</span>
            </div>
            <div class="ri-row">
              <span
                class="ri-label"
                title="Today's highest and lowest price"
              >Low</span>
              <span class="ri-val dn">{{ formatPrice(ticker.low) }}</span>
            </div>
            <div class="ri-row">
              <span class="ri-label">Prev Close</span>
              <span class="ri-val">{{ formatPrice(ticker.prev_close) }}</span>
            </div>
            <div class="ri-divider"></div>
            <div class="ri-row">
              <span class="ri-label">Volume</span>
              <span class="ri-val">{{ formatVol(ticker.volume) }}</span>
            </div>
            <div class="ri-note">Data: Yahoo Finance (15–20 min delay)</div>
          </div>
          <div class="fr-body" v-else>
            <div class="fr-empty">Select a pair to see rate info</div>
          </div>
        </div>

        <!-- Pair info card -->
        <div class="fr-panel fr-pair-info">
          <div class="fr-header">PAIR</div>
          <div class="fr-body">
            <div class="pi-name">{{ displayPair(activePair) }}</div>
            <div class="pi-full">{{ pairFullName(activePair) }}</div>
            <div class="pi-note">CDS hours: 09:00 – 17:00 IST</div>
          </div>
        </div>

        <!-- Active positions -->
        <div class="fr-panel">
          <div class="fr-header">POSITIONS ({{ forexPositions.length }})</div>
          <div class="fr-body" v-if="forexPositions.length">
            <div
              v-for="pos in forexPositions" :key="pos.id"
              class="fx-pos-card"
              :class="'fx-pos-' + pos.status"
            >
              <div class="fx-pos-top">
                <span class="fx-pos-sym">{{ pos.display_symbol }}</span>
                <span class="fx-pos-dir" :class="pos.direction === 'SHORT' ? 'dn' : 'up'">{{ pos.direction }}</span>
                <span class="fx-pos-badge" :class="'fx-pos-' + pos.status">{{ pos.statusLabel }}</span>
              </div>
              <div class="fx-pos-prices">
                <span>entry <b>₹{{ pos.entry?.toFixed(4) }}</b></span>
                <span>now <b :class="pos.pnl_pct >= 0 ? 'up' : 'dn'">₹{{ pos.now?.toFixed(4) }}</b></span>
                <span :class="['fx-pos-pnl', pos.pnl_pct >= 0 ? 'up' : 'dn']">
                  {{ pos.pnl_inr >= 0 ? '+' : '−' }}₹{{ Math.abs(pos.pnl_inr || 0).toFixed(2) }}
                  ({{ pos.pnl_pct >= 0 ? '+' : '' }}{{ pos.pnl_pct?.toFixed(2) }}%)
                </span>
              </div>
              <div class="fx-pos-levels">
                <span class="dn">SL ₹{{ pos.sl?.toFixed(4) }}</span>
                <span class="up">T1 ₹{{ pos.t1?.toFixed(4) }}</span>
                <span class="up">T2 ₹{{ pos.t2?.toFixed(4) }}</span>
                <span class="fx-pos-qty">{{ (pos.qty / pos.lot_size).toFixed(0) }} lot{{ (pos.qty / pos.lot_size) !== 1 ? 's' : '' }} ({{ pos.qty.toLocaleString() }})</span>
              </div>
              <div v-if="pos.alert" class="fx-pos-alert">⚠ {{ pos.alert }}</div>
              <div class="fx-pos-actions">
                <button class="fx-btn fx-btn-exit" @click="forceExitPos(pos)" title="Force-exit at market">🚫 Exit</button>
                <button
                  :class="['fx-btn fx-btn-done', exitArmed.has(pos.id) && 'fx-btn-armed']"
                  @click="markExited(pos)"
                >{{ exitArmed.has(pos.id) ? '⚠ Confirm' : '✓ Exited' }}</button>
              </div>
            </div>
          </div>
          <div class="fr-body" v-else>
            <div class="fr-empty">No active forex positions</div>
          </div>
        </div>

        <!-- Scanner signals -->
        <div class="fr-panel">
          <div class="fr-header">SCANNER SIGNALS</div>
          <div class="fr-body" v-if="scanSignals.length">
            <div
              v-for="sig in scanSignals" :key="sig.cds_base"
              class="sig-card"
              :class="sig.verdict?.includes('BUY') || sig.verdict?.includes('STRONG BUY') ? 'bull' : sig.verdict?.includes('SELL') || sig.verdict?.includes('STRONG SELL') ? 'bear' : 'neutral'"
            >
              <div class="sig-top">
                <span class="sig-pair">{{ sig.cds_base || sig.ticker }}</span>
                <span class="sig-verdict">{{ sig.verdict }}</span>
              </div>
              <div class="sig-row">
                <span class="sig-lbl">Conf</span>
                <span class="sig-val">{{ sig.confidence }}%</span>
              </div>
              <div class="sig-row">
                <span class="sig-lbl">Dir</span>
                <span class="sig-val" :class="sig.direction === 'LONG' ? 'up' : 'dn'">{{ sig.direction }}</span>
              </div>
              <div class="sig-row">
                <span class="sig-lbl">Entry</span>
                <span class="sig-val">{{ formatPrice(sig.entry) }}</span>
              </div>
              <div class="sig-row" v-if="sig.stop_loss">
                <span class="sig-lbl">SL</span>
                <span class="sig-val dn">{{ formatPrice(sig.stop_loss) }}</span>
              </div>
              <div class="sig-row" v-if="sig.target_1">
                <span class="sig-lbl">T1</span>
                <span class="sig-val up">{{ formatPrice(sig.target_1) }}</span>
              </div>
              <div class="sig-thesis" v-if="sig.thesis">{{ sig.thesis }}</div>
            </div>
          </div>
          <div class="fr-body" v-else>
            <div class="fr-empty">{{ scannerRunning ? 'Scanning…' : 'Scanner offline' }}</div>
          </div>
        </div>
      </aside>
    </div>
  </div>
</template>

<script setup>
import { ref, computed, onMounted, onUnmounted } from 'vue'
import {
  createForexTickStream, getForexTicker,
  getForexScannerStatus, createForexScannerStream,
} from '../../api/market'
import ForexChart from '../chart/ForexChart.vue'
import { snack } from '../../utils/snack'
import { playNotifSound } from '../../utils/notifSound'
import { onOrderEvent } from '../../composables/useSSE'

const PAIRS = [
  'USDINR=X',
  'EURINR=X',
  'GBPINR=X',
  'JPYINR=X',
]

const PAIR_NAMES = {
  'USDINR=X': 'USD / INR',
  'EURINR=X': 'EUR / INR',
  'GBPINR=X': 'GBP / INR',
  'JPYINR=X': 'JPY / INR',
}

const activePair    = ref('USDINR=X')
const ticker        = ref(null)
const tickers       = ref({})
const connected     = ref(false)
const chartInterval = ref('1h')

// Scanner state
const scannerRunning = ref(false)
const scanSignals    = ref([])

// Tracked forex positions
const trackedRaw    = ref([])
const exitArmed     = ref(new Set())
const forexRealizedPnl = ref(0)


// ── Formatters ────────────────────────────────────────────────────────────────

function displayPair(symbol) {
  return symbol.replace('=X', '').replace(/^(.{3})(.{3})$/, '$1/$2')
}

function pairFullName(symbol) {
  return PAIR_NAMES[symbol] || symbol
}

function formatPrice(v) {
  if (v == null) return '—'
  return Number(v).toFixed(Number(v) >= 100 ? 2 : 4)
}

function formatVol(v) {
  if (v == null) return '—'
  if (v >= 1e9) return (v / 1e9).toFixed(1) + 'B'
  if (v >= 1e6) return (v / 1e6).toFixed(1) + 'M'
  if (v >= 1e3) return (v / 1e3).toFixed(1) + 'K'
  return String(v)
}

// ── Tracked forex positions ──────────────────────────────────────────────────

const PAIR_TO_UNDERLYING = {
  'USDINR': 'USDINR=X', 'EURINR': 'EURINR=X',
  'GBPINR': 'GBPINR=X', 'JPYINR': 'JPYINR=X',
}
const _CDS_LOT = { USDINR: 1000, EURINR: 1000, GBPINR: 1000, JPYINR: 100000 }

const forexPositions = computed(() => {
  const out = []
  for (const rec of trackedRaw.value) {
    const t = rec.ticket || {}
    if (t.trade_mode !== 'forex') continue
    const isShort = t.direction === 'SHORT'
    const entry = Number(t.entry?.expected_premium_inr) || 0
    const sl = Number(t.exit?.stop_loss_inr) || 0
    const t1 = Number(t.exit?.target_1_inr) || 0
    const t2 = Number(t.exit?.target_2_inr) || 0

    // Use broker futures LTP (set by tracked_monitor) — avoids spot vs futures mismatch
    // Fallback chain: broker LTP → Yahoo spot → entry price
    const sym = (t.trading_symbol || '').match(/^[A-Z]{6}/)?.[0] || (t.underlying || '')
    const brokerLtp = t._broker_ltp ? Number(t._broker_ltp) : 0
    const yPair = PAIR_TO_UNDERLYING[sym]
    const liveTick = yPair ? tickers.value[yPair] : null
    const now = brokerLtp > 0 ? brokerLtp : (liveTick?.last ? Number(liveTick.last) : entry)

    const pnl_pct = entry ? (isShort ? (entry - now) : (now - entry)) / entry * 100 : 0
    const lot_size = Number(t.lot_size) || _CDS_LOT[sym] || 1000
    const qty = Number(rec.qty) || lot_size
    const pnl_inr = (isShort ? (entry - now) : (now - entry)) * qty

    let status = 'safe', statusLabel = 'OK', alert = ''
    if (isShort) {
      if (sl && now >= sl) {
        status = 'sl_hit'; statusLabel = 'SL HIT'
        alert = `Price ₹${now.toFixed(4)} ≥ SL ₹${sl.toFixed(4)} — EXIT NOW.`
      } else if (t2 && now <= t2) {
        status = 'past_t2'; statusLabel = 'T2 HIT'
        alert = `Price reached T2 ₹${t2.toFixed(4)} — full exit.`
      } else if (t1 && now <= t1) {
        status = 'past_t1'; statusLabel = 'T1 HIT'
        alert = `Price reached T1 ₹${t1.toFixed(4)} — exit 50%.`
      } else if (sl && entry > 0 && (now - entry) / (sl - entry) >= 0.8) {
        status = 'near_sl'; statusLabel = 'NEAR SL'
      }
    } else {
      if (sl && now <= sl) {
        status = 'sl_hit'; statusLabel = 'SL HIT'
        alert = `Price ₹${now.toFixed(4)} ≤ SL ₹${sl.toFixed(4)} — EXIT NOW.`
      } else if (t2 && now >= t2) {
        status = 'past_t2'; statusLabel = 'T2 HIT'
        alert = `Price reached T2 ₹${t2.toFixed(4)} — full exit.`
      } else if (t1 && now >= t1) {
        status = 'past_t1'; statusLabel = 'T1 HIT'
        alert = `Price reached T1 ₹${t1.toFixed(4)} — exit 50%.`
      } else if (sl && now <= sl * 1.1) {
        status = 'near_sl'; statusLabel = 'NEAR SL'
      }
    }

    out.push({
      id: rec.id,
      trading_symbol: t.trading_symbol,
      display_symbol: t.display_symbol || sym,
      direction: t.direction || 'LONG',
      entry, now, sl, t1, t2, qty, lot_size,
      pnl_pct, pnl_inr,
      status, statusLabel, alert,
      is_live: !!liveTick,
    })
  }
  return out
})

const forexPnl = computed(() => {
  let unrealized = 0
  for (const pos of forexPositions.value) {
    unrealized += pos.pnl_inr || 0
  }
  const realized = forexRealizedPnl.value
  return { realized, unrealized, net: realized + unrealized }
})

async function loadTrackedPositions() {
  try {
    const base = import.meta.env.VITE_API_BASE_URL || ''
    const r = await fetch(`${base}/api/trade/tracked`).then(x => x.json())
    if (r.success) trackedRaw.value = r.data || []
  } catch (e) {
    console.warn('loadTrackedPositions failed', e)
  }
}

async function loadForexRealizedPnl() {
  try {
    const base = import.meta.env.VITE_API_BASE_URL || ''
    const r = await fetch(`${base}/api/trade/executor/status`).then(x => x.json())
    if (r.success) forexRealizedPnl.value = r.data?.forex_realized_pnl || 0
  } catch {}
}

async function forceExitPos(pos) {
  if (!confirm(`🚫 Force-exit ${pos.display_symbol} ${pos.direction}?\n\nThis will place a market order immediately.`)) return
  const base = import.meta.env.VITE_API_BASE_URL || ''
  try {
    const r = await fetch(`${base}/api/trade/executor/force-exit/${pos.id}`, { method: 'POST' })
    const data = await r.json()
    if (data.success) {
      setTimeout(loadTrackedPositions, 2000)
    }
  } catch (e) {
    console.error('forceExit', e)
  }
}

function markExited(pos) {
  if (!exitArmed.value.has(pos.id)) {
    exitArmed.value = new Set([...exitArmed.value, pos.id])
    setTimeout(() => {
      exitArmed.value = new Set([...exitArmed.value].filter(x => x !== pos.id))
    }, 4000)
    return
  }
  const base = import.meta.env.VITE_API_BASE_URL || ''
  const qs = new URLSearchParams({ exit_premium: String(pos.now ?? ''), exit_reason: 'manual' })
  fetch(`${base}/api/trade/tracked/${pos.id}?${qs}`, { method: 'DELETE' })
    .then(() => {
      trackedRaw.value = trackedRaw.value.filter(p => p.id !== pos.id)
      exitArmed.value = new Set([...exitArmed.value].filter(x => x !== pos.id))
    })
    .catch(e => console.error('markExited', e))
}

// ── Scanner controls ─────────────────────────────────────────────────────────

async function loadScannerStatus() {
  try {
    const res = await getForexScannerStatus()
    const d = res.data?.data || res.data || {}
    scannerRunning.value = d.running === true
  } catch {}
}


let scanSSE = null

function startScannerStream() {
  if (scanSSE) { scanSSE.close(); scanSSE = null }
  const sse = createForexScannerStream()

  sse.onmessage = (e) => {
    try {
      const msg = JSON.parse(e.data)
      if (msg.type === 'forex_scan_signal') {
        const base = msg.cds_base || ''
        const idx = scanSignals.value.findIndex(s => s.cds_base === base)
        if (idx >= 0) {
          scanSignals.value[idx] = msg
          scanSignals.value = [...scanSignals.value]
        } else {
          scanSignals.value = [...scanSignals.value, msg]
        }
      }
      if (msg.type === 'forex_scan_start') {
        scannerRunning.value = true
      }
    } catch {}
  }
  sse.onerror = () => { /* auto-reconnect by browser */ }
  scanSSE = sse
}

// ── SSE ticker stream ─────────────────────────────────────────────────────────

let tickerSSE = null

function startTickerStream(pair) {
  if (tickerSSE) { tickerSSE.close(); tickerSSE = null }
  const sse = createForexTickStream(pair)

  sse.addEventListener('tick', (e) => {
    const d = JSON.parse(e.data)
    ticker.value = d
    tickers.value = { ...tickers.value, [pair]: d }
    connected.value = true
  })

  sse.addEventListener('heartbeat', () => { connected.value = true })
  sse.onerror = () => { connected.value = false }

  tickerSSE = sse
}

// ── Snapshot load for watchlist ───────────────────────────────────────────────

async function loadAllTickers() {
  for (const pair of PAIRS) {
    try {
      const res = await getForexTicker(pair)
      const d = res.data?.data || res.data
      if (d) {
        tickers.value = { ...tickers.value, [pair]: d }
        if (pair === activePair.value) ticker.value = d
      }
    } catch {}
  }
}

// ── Pair selection ────────────────────────────────────────────────────────────

function selectPair(pair) {
  activePair.value = pair
  ticker.value = tickers.value[pair] || null
  startTickerStream(pair)
}

// ── Lifecycle ─────────────────────────────────────────────────────────────────

let refreshTimer = null
let _posRefresh = null

onMounted(() => {
  loadScannerStatus()
  loadAllTickers()
  loadTrackedPositions()
  loadForexRealizedPnl()
  startTickerStream(activePair.value)
  startScannerStream()
  refreshTimer = setInterval(loadAllTickers, 30_000)
  _posRefresh = setInterval(() => { loadTrackedPositions(); loadForexRealizedPnl() }, 15_000)
})

// ── Shared SSE: order events (forex entries, exits) ──────────────────────────
onOrderEvent((data) => {
  if (data.type === 'order_events_connected') return
  if (data.trade_mode !== 'forex') return
  const status = (data.status || '').toUpperCase()
  const statusSoundMap = {
    'ENTRY_PLACED': 'entry_buy', 'SIMULATED': 'entry_buy',
    'SL_HIT': 'sl_exit', 'PAST_T1': 't1_exit', 'PAST_T2': 't2_exit',
    'TIME_EXIT': 'time_exit', 'FORCE_EXIT': 'time_exit',
  }
  const soundType = statusSoundMap[data.status] || data.severity || 'info'
  playNotifSound(soundType)
  snack({
    severity: data.severity || 'info',
    title: data.title || `Forex ${status}`,
    message: data.message || '',
    duration: data.severity === 'error' ? 8000 : 5000,
    sound: false,
  })
  loadTrackedPositions()
  loadForexRealizedPnl()
})

onUnmounted(() => {
  if (tickerSSE) tickerSSE.close()
  if (scanSSE) scanSSE.close()
  clearInterval(refreshTimer)
  clearInterval(_posRefresh)
})
</script>

<style scoped>
/* ── Layout ──────────────────────────────────────────────────────────────────── */
.forex-view {
  display: flex;
  flex-direction: column;
  height: calc(100vh - 52px);
  background: #0d0d1a;
  color: #c0c4d8;
  overflow: hidden;
}

/* ── Header ──────────────────────────────────────────────────────────────────── */
.forex-header {
  flex-shrink: 0;
  padding: 8px 14px 0;
  border-bottom: 1px solid #1a1a2e;
  background: #0e0e1e;
}
.forex-title-row {
  display: flex;
  align-items: center;
  gap: 12px;
  padding-bottom: 8px;
}
.forex-title {
  margin: 0;
  font-size: 15px;
  font-weight: 700;
  color: #e0e0f0;
  letter-spacing: 1px;
}
.forex-source {
  font-size: 10px;
  color: #4a4e6a;
  margin-left: auto;
}

.mode-badge {
  font-size: 10px; font-weight: 700; letter-spacing: 1px;
  padding: 2px 7px; border-radius: 3px;
}
.mode-badge.paper { background: #f59e0b; color: #000; }

.forex-status {
  display: flex;
  align-items: center;
  gap: 5px;
  font-size: 11px;
  color: #4a4e6a;
}
.forex-status.online { color: #00d4a8; }
.forex-status.offline { color: #ff4757; }
.status-dot {
  width: 6px; height: 6px; border-radius: 50%;
  background: currentColor;
}

/* ── P&L strip ───────────────────────────────────────────────────────────────── */
.forex-pnl-strip {
  display: flex;
  align-items: center;
  gap: 14px;
  padding: 5px 14px;
  background: #0c0c1a;
  border-bottom: 1px solid #1a1a2e;
  font-size: 12px;
  flex-shrink: 0;
}
.fx-pnl-label {
  font-weight: 700;
  color: #6b6f8a;
  font-size: 11px;
  letter-spacing: 0.5px;
}
.fx-pnl-val {
  font-weight: 600;
  font-size: 12px;
}
.fx-pnl-net {
  font-weight: 700;
  font-size: 13px;
}
.fx-pnl-count {
  color: #4a4e6a;
  font-size: 11px;
  margin-left: auto;
}

/* ── Body (3-col) ────────────────────────────────────────────────────────────── */
.forex-body {
  display: flex;
  flex: 1;
  min-height: 0;
  overflow: hidden;
}

/* ── Left sidebar ────────────────────────────────────────────────────────────── */
.forex-sidebar {
  width: 240px;
  flex-shrink: 0;
  background: #0e0e1e;
  border-right: 1px solid #1a1a2e;
  overflow-y: auto;
}
.forex-watchlist { padding: 6px 0; }
.fw-item {
  display: flex;
  flex-direction: column;
  gap: 2px;
  padding: 8px 12px;
  cursor: pointer;
  border-left: 3px solid transparent;
  transition: background 0.12s;
}
.fw-item:hover { background: #141428; }
.fw-item.active {
  background: #141428;
  border-left-color: #00d4a8;
}
.fw-symbol {
  font-size: 12px;
  font-weight: 600;
  color: #c0c4d8;
}
.fw-price {
  font-size: 11px;
  font-weight: 500;
}
.fw-chg {
  font-size: 10px;
}

/* ── Center ──────────────────────────────────────────────────────────────────── */
.forex-center {
  flex: 1;
  min-width: 0;
  display: flex;
  flex-direction: column;
  overflow: hidden;
}
.forex-ticker-strip {
  display: flex;
  align-items: center;
  gap: 10px;
  padding: 6px 12px;
  background: #111125;
  border-bottom: 1px solid #1a1a2e;
  flex-shrink: 0;
  font-size: 12px;
}
.ft-pair  { font-weight: 700; color: #e0e0f0; font-size: 13px; }
.ft-price { font-weight: 600; font-size: 14px; }
.ft-change { font-size: 11px; }
.ft-bid, .ft-ask { color: #6b6f8a; font-size: 11px; }
.ft-loading { color: #4a4e6a; font-size: 11px; }
.forex-chart-wrap {
  flex: 1;
  min-height: 0;
  height: 100%;
}

/* ── Right panel ─────────────────────────────────────────────────────────────── */
.forex-right {
  width: 360px;
  flex-shrink: 0;
  background: #0e0e1e;
  border-left: 1px solid #1a1a2e;
  overflow-y: auto;
  display: flex;
  flex-direction: column;
  gap: 0;
}
.fr-panel {
  border-bottom: 1px solid #1a1a2e;
}
.fr-header {
  padding: 6px 12px;
  font-size: 10px;
  font-weight: 700;
  letter-spacing: 1px;
  color: #4a4e6a;
  background: #111125;
  border-bottom: 1px solid #1a1a2e;
}
.fr-body {
  padding: 8px 12px;
}
.fr-empty {
  color: #4a4e6a;
  font-size: 11px;
  text-align: center;
  padding: 12px 0;
}

/* Rate info rows */
.ri-row {
  display: flex;
  justify-content: space-between;
  align-items: center;
  padding: 3px 0;
  font-size: 11px;
}
.ri-label {
  color: #6b6f8a;
  cursor: help;
}
.ri-val { font-weight: 500; color: #c0c4d8; }
.ri-spread { color: #f59e0b; }
.ri-divider {
  border-top: 1px solid #1a1a2e;
  margin: 6px 0;
}
.ri-note {
  font-size: 10px;
  color: #3a3e5a;
  margin-top: 8px;
  line-height: 1.4;
}

/* Pair info */
.fr-pair-info .fr-body { padding: 10px 12px; }
.pi-name {
  font-size: 14px;
  font-weight: 700;
  color: #e0e0f0;
  margin-bottom: 4px;
}
.pi-full {
  font-size: 11px;
  color: #6b6f8a;
  margin-bottom: 8px;
}
.pi-note {
  font-size: 10px;
  color: #3a3e5a;
  line-height: 1.5;
}


/* ── Signal cards ────────────────────────────────────────────────────────────── */
.sig-card {
  background: #111125;
  border: 1px solid #1a1a2e;
  border-radius: 6px;
  padding: 8px 10px;
  margin-bottom: 8px;
  font-size: 11px;
}
.sig-card.bull { border-left: 3px solid #00d4a8; }
.sig-card.bear { border-left: 3px solid #ff4757; }
.sig-card.neutral { border-left: 3px solid #4a4e6a; }
.sig-top {
  display: flex; justify-content: space-between; align-items: center;
  margin-bottom: 6px;
}
.sig-pair { font-weight: 700; color: #e0e0f0; font-size: 12px; }
.sig-verdict {
  font-size: 10px; font-weight: 700; letter-spacing: 0.5px;
  padding: 1px 6px; border-radius: 3px;
}
.sig-card.bull .sig-verdict { background: rgba(0,212,168,0.15); color: #00d4a8; }
.sig-card.bear .sig-verdict { background: rgba(255,71,87,0.15); color: #ff4757; }
.sig-card.neutral .sig-verdict { background: rgba(74,78,106,0.3); color: #6b6f8a; }
.sig-row {
  display: flex; justify-content: space-between; padding: 1px 0;
}
.sig-lbl { color: #4a4e6a; }
.sig-val { color: #c0c4d8; font-weight: 500; }
.sig-thesis {
  font-size: 10px; color: #4a4e6a; margin-top: 4px;
  line-height: 1.3; border-top: 1px solid #1a1a2e; padding-top: 4px;
}

/* ── Forex position cards ────────────────────────────────────────────────────── */
.fx-pos-card {
  background: #111125;
  border: 1px solid #1a1a2e;
  border-radius: 6px;
  padding: 8px 10px;
  margin-bottom: 8px;
  font-size: 11px;
  border-left: 3px solid #4a4e6a;
}
.fx-pos-card.fx-pos-safe { border-left-color: #00d4a8; }
.fx-pos-card.fx-pos-near_sl { border-left-color: #fbbf24; }
.fx-pos-card.fx-pos-sl_hit { border-left-color: #ff4757; background: rgba(255,71,87,0.06); }
.fx-pos-card.fx-pos-past_t1,
.fx-pos-card.fx-pos-past_t2 { border-left-color: #00d4a8; background: rgba(0,212,168,0.06); }

.fx-pos-top {
  display: flex; align-items: center; gap: 6px; margin-bottom: 6px;
}
.fx-pos-sym { font-weight: 700; color: #e0e0f0; font-size: 12px; }
.fx-pos-dir {
  font-size: 9px; font-weight: 800; padding: 1px 5px; border-radius: 3px;
  letter-spacing: 0.3px;
}
.fx-pos-dir.up { background: rgba(0,212,168,0.15); color: #00d4a8; }
.fx-pos-dir.dn { background: rgba(255,71,87,0.15); color: #ff4757; }
.fx-pos-badge {
  font-size: 9px; font-weight: 800; padding: 1px 5px; border-radius: 3px;
  letter-spacing: 0.3px; margin-left: auto;
}
.fx-pos-badge.fx-pos-safe { background: rgba(0,212,168,0.15); color: #00d4a8; }
.fx-pos-badge.fx-pos-near_sl { background: rgba(251,191,36,0.15); color: #fbbf24; }
.fx-pos-badge.fx-pos-sl_hit { background: rgba(255,71,87,0.2); color: #ff4757; }
.fx-pos-badge.fx-pos-past_t1,
.fx-pos-badge.fx-pos-past_t2 { background: rgba(0,212,168,0.15); color: #00d4a8; }

.fx-pos-prices {
  display: flex; flex-wrap: wrap; gap: 4px 10px; align-items: center;
  padding: 4px 0; font-size: 11px;
}
.fx-pos-pnl { font-weight: 700; font-size: 13px; }
.fx-pos-qty { color: #6b6f8a; font-size: 10px; margin-left: auto; }
.fx-pos-levels {
  display: flex; gap: 10px; font-size: 10px; padding: 2px 0;
  color: #6b6f8a;
}
.fx-pos-alert {
  font-size: 10px; color: #fbbf24; margin-top: 4px;
  padding: 4px 6px; background: rgba(251,191,36,0.08); border-radius: 4px;
}
.fx-pos-actions {
  display: flex; gap: 6px; margin-top: 6px;
}
.fx-btn {
  font-size: 10px; font-weight: 600; padding: 3px 8px;
  border: 1px solid #2a2a4a; border-radius: 4px;
  background: #1a1a2e; color: #c0c4d8; cursor: pointer;
  transition: background 0.12s;
}
.fx-btn:hover { background: #252545; }
.fx-btn-exit { color: #ff4757; border-color: rgba(255,71,87,0.3); }
.fx-btn-exit:hover { background: rgba(255,71,87,0.1); }
.fx-btn-armed { color: #fbbf24; border-color: rgba(251,191,36,0.4); background: rgba(251,191,36,0.08); }

/* Shared colour utilities */
.up { color: #00d4a8; }
.dn { color: #ff4757; }
</style>
