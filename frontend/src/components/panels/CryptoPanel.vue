<template>
  <div class="crypto-view">
    <!-- ═══ HEADER (single row) ════════════════════════════════════════════════ -->
    <header class="ch">
      <h2 class="ch-title">₿ CRYPTO</h2>
      <span :class="['ch-mode', status.mode === 'live' ? 'ch-live' : 'ch-paper']">
        {{ status.mode === 'live' ? 'LIVE' : 'PAPER' }}
      </span>
      <span class="ch-sep">|</span>
      <span class="ch-stat" :class="connected ? 'up' : 'muted'">
        <span class="ch-dot" :class="connected ? 'ch-dot-on' : ''"></span>
        {{ connected ? 'Connected' : 'Offline' }}
      </span>

      <!-- P&L inline -->
      <span class="ch-sep">|</span>
      <span :class="['ch-pnl', cryptoPnl.realized >= 0 ? 'up' : 'dn']">
        R {{ cryptoPnl.realized >= 0 ? '+' : '' }}${{ cryptoPnl.realized.toFixed(2) }}
      </span>
      <span :class="['ch-pnl', cryptoPnl.unrealized >= 0 ? 'up' : 'dn']">
        U {{ cryptoPnl.unrealized >= 0 ? '+' : '' }}${{ cryptoPnl.unrealized.toFixed(2) }}
      </span>
      <span :class="['ch-pnl', 'ch-pnl-net', cryptoPnl.net >= 0 ? 'up' : 'dn']">
        Net {{ cryptoPnl.net >= 0 ? '+' : '' }}${{ cryptoPnl.net.toFixed(2) }}
      </span>
      <span class="ch-pnl-detail muted" v-if="cryptoPnl.brokerage" title="Total brokerage fees today">
        Fee ${{ cryptoPnl.brokerage.toFixed(2) }}
      </span>
      <span class="ch-pnl-detail muted">{{ cryptoPnl.positions || 0 }}P · {{ cryptoPnl.trades || 0 }}T</span>

      <!-- Bybit futures data -->
      <template v-if="bybitTicker">
        <span class="ch-sep">|</span>
        <span class="ch-bybit muted" title="Mark Price (Perp Futures)">
          Mark ${{ formatPrice(bybitTicker.mark_price) }}
        </span>
        <span class="ch-bybit muted" title="Index Price (Spot)">
          Idx ${{ formatPrice(bybitTicker.index_price) }}
        </span>
        <span class="ch-bybit muted" title="Open Interest">OI {{ formatVolume(bybitTicker.open_interest) }}</span>
        <span :class="['ch-bybit', bybitTicker.funding_rate >= 0 ? 'up' : 'dn']"
              :title="'Next funding: ' + fundingCountdown">
          Fund {{ (bybitTicker.funding_rate * 100).toFixed(4) }}% · {{ fundingCountdown }}
        </span>
      </template>

      <span class="ch-spacer"></span>

      <button
        :class="['ch-auto', scannerRunning ? 'ch-auto-on' : '']"
        @click="onAutoClick"
        :title="scannerRunning ? 'Paper trading active — click to stop scanner' : 'Start paper trading'"
      >
        <span class="ch-auto-dot"></span>
        {{ scannerRunning ? 'PAPER TRADING' : 'START' }}
      </button>
    </header>

    <!-- ═══ BODY (3-col) ═══════════════════════════════════════════════════════ -->
    <div class="crypto-body">

      <!-- LEFT SIDEBAR ──────────────────────────────────────────────────────── -->
      <aside class="cl">
        <input
          v-model="searchQuery"
          class="cl-search"
          placeholder="Search pairs…"
          @input="onSearch"
        />
        <div class="cl-watchlist">
          <div
            v-for="pair in displayPairs"
            :key="pair"
            :class="['cl-pair', { 'cl-active': pair === activePair }]"
            @click="selectPair(pair)"
          >
            <span class="cl-sym">{{ coinName(pair) }}</span>
            <span class="cl-quote">/ USDT</span>
            <span class="cl-price" v-if="pair === activePair && ticker">
              ${{ formatPrice(ticker.last) }}
            </span>
          </div>
        </div>

        <!-- Scanner -->
        <div class="cl-scanner">
          <div class="cl-sh">
            <span class="cl-sh-title">SCANNER</span>
            <span :class="['cl-sh-badge', scannerRunning ? 'cl-sh-live' : '']">
              {{ scannerRunning ? 'LIVE' : 'OFF' }}
            </span>
          </div>
          <div class="cl-sf" v-if="signals.length">
            <div
              v-for="sig in signals.slice(0, 20)"
              :key="sig.id || sig.symbol + sig.side"
              :class="['cl-sig', sig.side === 'BUY' ? 'cl-sig-buy' : 'cl-sig-sell']"
              @click="selectPair(sig.symbol)"
            >
              <span :class="['cl-dir', sig.side === 'BUY' ? 'up' : 'dn']">{{ sig.side === 'BUY' ? '▲' : '▼' }}</span>
              <span class="cl-sig-coin">{{ coinName(sig.symbol) }}</span>
              <span :class="['cl-sig-conf', sig.confidence >= 60 ? 'up' : sig.confidence >= 40 ? 'warn' : 'muted']">
                {{ sig.confidence }}%
              </span>
              <span class="cl-sig-price">${{ formatPrice(sig.price) }}</span>
              <span class="cl-sig-pat">
                {{ sig.indicators?.patterns?.length ? sig.indicators.patterns[0] : ('RSI ' + (sig.indicators?.rsi?.toFixed(0) || '—')) }}
              </span>
              <span :class="['cl-sig-exec', sig.executed ? 'cl-sig-filled' : 'cl-sig-skip']">
                {{ sig.executed ? 'FILLED' : 'SKIP' }}
              </span>
            </div>
          </div>
          <div class="cl-empty" v-else>
            {{ scannerRunning ? 'Scanning… waiting for signals' : 'Scanner stopped — click START to begin paper trading' }}
          </div>
        </div>
      </aside>

      <!-- CENTER ────────────────────────────────────────────────────────────── -->
      <section class="cc">
        <div class="cc-ticker" v-if="ticker">
          <span class="cc-pair">{{ coinName(activePair) }} / USDT</span>
          <span :class="['cc-price', ticker.change_24h >= 0 ? 'up' : 'dn']">
            ${{ formatPrice(ticker.last) }}
          </span>
          <span :class="['cc-chg', ticker.change_24h >= 0 ? 'up' : 'dn']">
            {{ ticker.change_24h >= 0 ? '+' : '' }}{{ ticker.change_24h?.toFixed(2) }}%
          </span>
          <span class="cc-vol muted">Vol ${{ formatVolume(ticker.volume_24h) }}</span>
          <span class="cc-spacer"></span>
          <div class="cc-intervals">
            <button
              v-for="tf in ['1m','5m','15m','1h','4h','1D']"
              :key="tf"
              :class="['cc-tf', chartInterval === tf ? 'cc-tf-on' : '']"
              @click="chartInterval = tf"
            >{{ tf }}</button>
          </div>
        </div>
        <div class="cc-chart">
          <CryptoChart
            :pair="activePair"
            :interval="chartInterval"
            :live-price="ticker?.last ?? null"
          />
        </div>
      </section>

      <!-- RIGHT SIDEBAR ─────────────────────────────────────────────────────── -->
      <aside class="cr">
        <!-- Positions -->
        <div class="cr-sec" v-if="positionCards.length">
          <div class="cr-sh">POSITIONS <span class="cr-count">{{ positionCards.length }}</span></div>
          <div class="cr-positions">
            <div
              v-for="pc in positionCards"
              :key="pc.symbol"
              :class="['cr-pos', pc.pnl >= 0 ? 'cr-pos-up' : 'cr-pos-dn']"
            >
              <div class="cr-pos-top">
                <span class="cr-pos-sym">{{ coinName(pc.symbol) }}</span>
                <span :class="['cr-pos-side', pc.side === 'LONG' ? 'up' : 'dn']">{{ pc.side }}</span>
                <span :class="['cr-pos-pnl', estNet(pc) >= 0 ? 'up' : 'dn']"
                      :title="estBreakdownTip(pc)">
                  {{ estNet(pc) >= 0 ? '+' : '' }}${{ estNet(pc).toFixed(2) }}
                </span>
                <button class="cr-pos-x" @click="closePosition(pc.symbol)" title="Close">✕</button>
              </div>
              <div class="cr-pos-bot">
                <span class="muted">{{ pc.confidence }}% · {{ pc.holdTime }}</span>
                <span class="cr-pos-levels">
                  <span class="dn">SL {{ formatPrice(pc.sl) }}</span>
                  <span class="up">TP {{ formatPrice(pc.tp) }}</span>
                </span>
              </div>
              <div class="cr-pos-tax">
                <span class="muted">Gross ${{ (pc.pnl||0).toFixed(2) }}</span>
                <span class="dn" :title="estBreakdownTip(pc)">Fee+Tax -${{ estBrokerage(pc).toFixed(2) }}</span>
              </div>
            </div>
          </div>
        </div>

        <!-- Quick Trade -->
        <div class="cr-sec">
          <div class="cr-sh">TRADE · {{ coinName(activePair) }}</div>
          <div class="cr-trade">
            <div class="cr-trade-price" v-if="ticker">${{ formatPrice(ticker.last) }}</div>
            <div class="cr-trade-row">
              <span class="muted">USD</span>
              <input
                v-model.number="tradeNotional"
                type="number"
                class="cr-trade-input"
                placeholder="1000"
                min="10"
                step="100"
              />
            </div>
            <div class="cr-trade-hint muted" v-if="ticker && tradeNotional > 0">
              ≈ {{ (tradeNotional / ticker.last).toFixed(6) }} {{ coinName(activePair) }}
              <span class="cr-trade-fee" title="Estimated 0.1% maker+taker fee (both sides)">
                · Fee ~${{ (tradeNotional * 0.002).toFixed(2) }}
              </span>
            </div>
            <div class="cr-trade-btns">
              <button class="cr-btn-buy" @click="executeTrade('BUY')" :disabled="tradeBusy">
                {{ tradeBusy ? '...' : 'BUY' }}
              </button>
              <button class="cr-btn-sell" @click="executeTrade('SELL')" :disabled="tradeBusy">
                {{ tradeBusy ? '...' : 'SELL' }}
              </button>
            </div>
            <div class="cr-trade-result" v-if="tradeResult" :class="tradeResult.success ? 'up' : 'dn'">
              {{ tradeResult.msg }}
            </div>
          </div>
        </div>

        <!-- Order Book -->
        <div class="cr-sec">
          <div class="cr-sh">ORDER BOOK</div>
          <div class="cr-ob">
            <div class="cr-ob-head">
              <span>PRICE</span><span>SIZE</span>
            </div>
            <div class="cr-ob-asks">
              <div v-for="(ask, i) in orderBook.asks.slice(0, 5)" :key="'a'+i" class="cr-ob-row">
                <span class="dn">{{ formatPrice(ask[0]) }}</span>
                <span class="muted">{{ Number(ask[1]).toFixed(4) }}</span>
              </div>
            </div>
            <div class="cr-ob-mid" v-if="ticker">
              {{ formatPrice(ticker.last) }}
              <span class="muted">spread {{ formatPrice((orderBook.asks[0]?.[0] || 0) - (orderBook.bids[0]?.[0] || 0)) }}</span>
            </div>
            <div class="cr-ob-bids">
              <div v-for="(bid, i) in orderBook.bids.slice(0, 5)" :key="'b'+i" class="cr-ob-row">
                <span class="up">{{ formatPrice(bid[0]) }}</span>
                <span class="muted">{{ Number(bid[1]).toFixed(4) }}</span>
              </div>
            </div>
          </div>
        </div>

        <!-- Trade History -->
        <div class="cr-sec">
          <div class="cr-sh">RECENT TRADES</div>
          <div class="cr-history">
            <div v-if="!tradeHistory.length" class="cr-empty">No trades yet</div>
            <div v-for="t in tradeHistory.slice(0, 8)" :key="t.id" class="cr-hist-row"
                 :title="'Gross: $' + (t.gross_pnl||0).toFixed(2) + ' | Fee: $' + (t.brokerage||0).toFixed(2) + ' | Net: $' + (t.net_pnl||0).toFixed(2)">
              <span class="cr-hist-sym">{{ coinName(t.underlying || t.symbol) }}</span>
              <span :class="['cr-hist-dir', t.direction === 'LONG' ? 'up' : 'dn']">{{ t.direction }}</span>
              <span :class="['cr-hist-pnl', (t.net_pnl || 0) >= 0 ? 'up' : 'dn']">
                ${{ (t.net_pnl || 0).toFixed(2) }}
              </span>
              <span class="cr-hist-fee muted" v-if="t.brokerage">-${{ t.brokerage.toFixed(2) }}</span>
              <span class="cr-hist-time muted" v-if="t.exit_time">{{ fmtTime(Number(t.exit_time)) }}</span>
            </div>
          </div>
        </div>
      </aside>
    </div>
  </div>
</template>

<script setup>
import { ref, computed, onMounted, onUnmounted } from 'vue'
import { storeToRefs } from 'pinia'
import { useCryptoStore } from '../../stores/useCryptoStore'
import {
  getCryptoOrderBook, getCryptoBalances,
  createCryptoTickStream,
  getCryptoStatus, toggleCryptoAutoTrading,
  getBybitTicker,
  getCryptoScannerPnl, getCryptoScannerSignals,
  getCryptoScannerPositions, getCryptoScannerStatus,
  getCryptoTradeHistory,
  quickCryptoTrade, closeCryptoPosition,
  startCryptoScanner, stopCryptoScanner,
} from '../../api/market'
import CryptoChart from '../chart/CryptoChart.vue'
import { fmtDuration, fmtTime } from '../../utils/formatters'

const store = useCryptoStore()
const {
  activePair, searchQuery, ticker, orderBook, balances,
  watchlist, loading, connected,
} = storeToRefs(store)

const chartInterval   = ref('1h')
const autoTrading     = ref(false)
const scannerRunning  = ref(false)
const status          = ref({ mode: 'paper', auto_trade: false })
const bybitTicker     = ref(null)
const cryptoPnl       = ref({ realized: 0, unrealized: 0, net: 0, daily: 0, positions: 0 })
const signals         = ref([])
const positions       = ref([])
const tradeHistory    = ref([])
const tradeNotional   = ref(1000)
const tradeBusy       = ref(false)
const tradeResult     = ref(null)

const COIN_NAMES = {
  BTC: 'Bitcoin', ETH: 'Ethereum', SOL: 'Solana', BNB: 'BNB',
  XRP: 'XRP', ADA: 'Cardano', DOGE: 'Dogecoin', DOT: 'Polkadot',
  LINK: 'Chainlink', AVAX: 'Avalanche', MATIC: 'Polygon',
  UNI: 'Uniswap', ATOM: 'Cosmos', LTC: 'Litecoin', SHIB: 'Shiba Inu',
  TON: 'Toncoin', TRX: 'TRON', OP: 'Optimism', ARB: 'Arbitrum',
}

function coinName(pairOrSym) {
  const sym = pairOrSym.replace('/USDT', '').replace('/BUSD', '').replace('USDT', '').replace('BUSD', '').replace('/', '')
  return COIN_NAMES[sym] || sym
}

const displayPairs = computed(() => {
  if (!searchQuery.value.trim()) return watchlist.value
  const q = searchQuery.value.toUpperCase()
  return watchlist.value.filter(p =>
    p.includes(q) || coinName(p).toUpperCase().includes(q)
  )
})

// Funding countdown timer
const fundingNow = ref(Date.now())
const fundingCountdown = computed(() => {
  const nft = bybitTicker.value?.next_funding_time
  if (!nft) return '--:--'
  const ms = Number(nft) - fundingNow.value
  if (ms <= 0) return 'now'
  const h = Math.floor(ms / 3600000)
  const m = Math.floor((ms % 3600000) / 60000)
  const s = Math.floor((ms % 60000) / 1000)
  return `${h}h${String(m).padStart(2,'0')}m${String(s).padStart(2,'0')}s`
})

function indicatorTip(sig) {
  const ind = sig.indicators || {}
  const parts = [
    `Trend: ${ind.trend_1h || '—'}`,
    `StochRSI: ${ind.srsi_k?.toFixed(0) ?? '—'}`,
    `Aroon: ${ind.aroon_up?.toFixed(0) ?? '—'}/${ind.aroon_dn?.toFixed(0) ?? '—'}`,
    `RSI: ${ind.rsi?.toFixed(0) || '—'}`,
    `MACD: ${ind.macd_cross || '—'}`,
    `ADX: ${ind.adx?.toFixed(0) || '—'}`,
    `Vol: ${ind.vol_ratio || '—'}x`,
  ]
  if (ind.conditions?.length) parts.push(`[${ind.conditions.join(',')}]`)
  else if (ind.patterns?.length) parts.push(`Pattern: ${ind.patterns.join(', ')}`)
  return parts.join(' | ')
}

// ── Position cards (Swing-style enrichment) ──────────────────────────────────

const positionCards = computed(() => {
  return positions.value.map(p => {
    const entry = Number(p.entry_price) || 0
    const current = Number(p.current_price) || Number(ticker.value?.last) || entry
    const pnl = Number(p.pnl) || 0
    const pnlPct = entry ? ((current - entry) / entry * 100 * (p.side === 'LONG' ? 1 : -1)).toFixed(2) : '0.00'
    const qty = p.qty || p.size || '—'
    const notional = entry && qty !== '—' ? Math.round(entry * Number(qty)) : 0
    const holdTime = fmtDuration(p.entry_time)
    return { ...p, current_price: current, pnlPct, qty, notional, holdTime }
  })
})

// Estimated brokerage + Indian crypto tax for open positions
// Matches backend crypto_scanner.py: 0.1% exchange fee per side + 1% TDS on sell + 30% tax on gains
function estBrokerage(pos) {
  const entry = Number(pos.entry_price) || 0
  const current = Number(pos.current_price) || entry
  const qty = Number(pos.qty) || 0
  const entryN = entry * qty
  const exitN = current * qty
  const exchangeFee = (entryN + exitN) * 0.001   // 0.1% per side
  const tds = exitN * 0.01                       // 1% TDS on sell
  const pnl = Number(pos.pnl) || 0
  const incomeTax = Math.max(0, pnl) * 0.30      // 30% on gains only
  return exchangeFee + tds + incomeTax
}
function estNet(pos) {
  return (Number(pos.pnl) || 0) - estBrokerage(pos)
}
function estBreakdownTip(pos) {
  const entry = Number(pos.entry_price) || 0
  const current = Number(pos.current_price) || entry
  const qty = Number(pos.qty) || 0
  const entryN = entry * qty, exitN = current * qty
  const fee = (entryN + exitN) * 0.001
  const tds = exitN * 0.01
  const tax = Math.max(0, Number(pos.pnl) || 0) * 0.30
  return `Exchange: $${fee.toFixed(2)} | TDS 1%: $${tds.toFixed(2)} | Tax 30%: $${tax.toFixed(2)}`
}

function ladderPct(pos, which) {
  const sl = Number(pos.sl) || 0
  const tp = Number(pos.tp) || sl * 2
  const lo = Math.min(sl, Number(pos.entry_price) * 0.95)
  const hi = Math.max(tp, Number(pos.entry_price) * 1.05)
  const span = Math.max(1e-8, hi - lo)
  const clamp = (v) => Math.max(0, Math.min(100, ((v - lo) / span) * 100))
  const nowPrice = Number(pos.current_price) || Number(pos.entry_price) || 0
  if (which === 'now') return clamp(nowPrice) + '%'
  if (which === 'left') return clamp(Math.min(nowPrice, Number(pos.entry_price))) + '%'
  if (which === 'width') {
    const a = clamp(Math.min(nowPrice, Number(pos.entry_price)))
    const b = clamp(Math.max(nowPrice, Number(pos.entry_price)))
    return Math.max(2, b - a) + '%'
  }
  return '0%'
}

// ── Status ────────────────────────────────────────────────────────────────────

async function loadStatus() {
  try {
    const res = await getCryptoStatus()
    const d = res.data || {}
    status.value      = d
    autoTrading.value = d.auto_trade === true
  } catch {}
  try {
    const sRes = await getCryptoScannerStatus()
    const sd = sRes.data || {}
    scannerRunning.value = sd.running === true
  } catch {}
}

async function onAutoClick() {
  try {
    if (scannerRunning.value) {
      await stopCryptoScanner()
      scannerRunning.value = false
    } else {
      await startCryptoScanner()
      scannerRunning.value = true
    }
    console.log('[Crypto] Scanner toggled:', scannerRunning.value)
  } catch (err) {
    console.error('[Crypto] Scanner toggle failed:', err)
  }
}

async function ensureScannerRunning() {
  try {
    const sRes = await getCryptoScannerStatus()
    const sd = sRes.data || {}
    scannerRunning.value = sd.running === true
    if (!scannerRunning.value) {
      await startCryptoScanner()
      scannerRunning.value = true
      console.log('[Crypto] Scanner auto-started')
    }
  } catch {}
}

// ── Quick trade ───────────────────────────────────────────────────────────────

async function executeTrade(side) {
  tradeBusy.value = true
  tradeResult.value = null
  try {
    const res = await quickCryptoTrade(activePair.value, side, tradeNotional.value)
    const d = res.data || {}
    tradeResult.value = {
      success: true,
      msg: `${side} ${coinName(activePair.value)} @ $${formatPrice(d.price || d.fill_price)} (${d.mode})`,
    }
    loadPositions()
    loadScannerPnl()
  } catch (err) {
    tradeResult.value = {
      success: false,
      msg: err.response?.data?.error || 'Trade failed',
    }
  } finally {
    tradeBusy.value = false
    setTimeout(() => { tradeResult.value = null }, 5000)
  }
}

async function closePosition(symbol) {
  try {
    await closeCryptoPosition(symbol)
    loadPositions()
    loadScannerPnl()
    loadTradeHistory()
  } catch {}
}

// ── SSE ticker stream ─────────────────────────────────────────────────────────

let tickerSSE = null

function startTickerStream(pair) {
  if (tickerSSE) { tickerSSE.close(); tickerSSE = null }
  const sse = createCryptoTickStream(pair)

  sse.addEventListener('tick', (e) => {
    const d = JSON.parse(e.data)
    ticker.value = { ...d, change_24h: d.change_pct, volume_24h: d.volume }
    connected.value = true
    loading.value   = false
  })

  sse.addEventListener('heartbeat', () => { connected.value = true })
  sse.onerror = () => { connected.value = false }

  tickerSSE = sse
  loading.value = true
}

// ── Pair selection ────────────────────────────────────────────────────────────

function selectPair(pair) {
  store.selectPair(pair)
  startTickerStream(pair)
  loadOrderBook()
  loadBybitTicker(pair)
}

function onSearch() {}

// ── REST polling ──────────────────────────────────────────────────────────────

async function loadOrderBook() {
  if (!activePair.value) return
  try {
    const res = await getCryptoOrderBook(activePair.value)
    orderBook.value = res.data || { bids: [], asks: [] }
  } catch {
    orderBook.value = { bids: [], asks: [] }
  }
}

async function loadBalances() {
  try {
    const res = await getCryptoBalances()
    balances.value  = res.data || []
    connected.value = true
  } catch {
    connected.value = false
  }
}

async function loadBybitTicker(pair) {
  if (!pair) return
  try {
    const res = await getBybitTicker(pair)
    bybitTicker.value = res.data || null
  } catch {}
}

async function loadScannerPnl() {
  try {
    const res = await getCryptoScannerPnl()
    const d = res.data || {}
    cryptoPnl.value = {
      realized: d.realized || 0, unrealized: d.unrealized || 0,
      net: d.net || 0, daily: d.daily || 0, positions: d.positions || 0,
    }
  } catch {}
}

async function loadSignals() {
  try {
    const res = await getCryptoScannerSignals()
    signals.value = res.data || []
  } catch {}
}

async function loadPositions() {
  try {
    const res = await getCryptoScannerPositions()
    positions.value = res.data || []
  } catch {}
}

async function loadTradeHistory() {
  try {
    const res = await getCryptoTradeHistory(20)
    tradeHistory.value = res.data || []
  } catch {}
}

// ── Formatters ────────────────────────────────────────────────────────────────

function formatPrice(v) {
  if (v == null) return '—'
  return Number(v) >= 1
    ? Number(v).toLocaleString(undefined, { minimumFractionDigits: 2, maximumFractionDigits: 2 })
    : Number(v).toFixed(6)
}

function formatVolume(v) {
  if (v == null) return '—'
  if (v >= 1e9) return (v / 1e9).toFixed(1) + 'B'
  if (v >= 1e6) return (v / 1e6).toFixed(1) + 'M'
  if (v >= 1e3) return (v / 1e3).toFixed(1) + 'K'
  return v.toFixed(0)
}

// ── Lifecycle ─────────────────────────────────────────────────────────────────

let obTimer = null, balTimer = null, signalTimer = null
let pnlTimer = null, posTimer = null, histTimer = null
let bybitTimer = null, fundingTick = null

onMounted(() => {
  loadStatus()
  ensureScannerRunning()
  startTickerStream(activePair.value)
  loadOrderBook()
  loadBalances()
  loadBybitTicker(activePair.value)
  loadSignals()
  loadScannerPnl()
  loadPositions()
  loadTradeHistory()
  obTimer      = setInterval(loadOrderBook,     5_000)
  balTimer     = setInterval(loadBalances,     30_000)
  signalTimer  = setInterval(loadSignals,       8_000)
  pnlTimer     = setInterval(loadScannerPnl,   10_000)
  posTimer     = setInterval(loadPositions,      5_000)
  histTimer    = setInterval(loadTradeHistory,  30_000)
  bybitTimer   = setInterval(() => loadBybitTicker(activePair.value), 15_000)
  fundingTick  = setInterval(() => { fundingNow.value = Date.now() }, 1_000)
})

onUnmounted(() => {
  if (tickerSSE) tickerSSE.close()
  clearInterval(obTimer)
  clearInterval(balTimer)
  clearInterval(signalTimer)
  clearInterval(pnlTimer)
  clearInterval(posTimer)
  clearInterval(histTimer)
  clearInterval(bybitTimer)
  clearInterval(fundingTick)
})
</script>

<style src="../../styles/CryptoPanel.css"></style>

<style scoped>
/* ── Globals ──────────────────────────────────────────────────────────────── */
.up   { color: #00d4a8; }
.dn   { color: #ff4757; }
.warn { color: #e8b84b; }
.muted { color: #4a4e6a; }
@keyframes pulse { 0%,100% { opacity: 1; } 50% { opacity: 0.3; } }

/* ── Header ───────────────────────────────────────────────────────────────── */
.ch {
  display: flex; align-items: center; gap: 8px;
  padding: 5px 12px; background: #0b0b18;
  border-bottom: 1px solid #1a1a2e; flex-shrink: 0;
  font-size: 11px; font-family: 'SF Mono', monospace;
}
.ch-title { font-size: 13px; color: #f7931a; margin: 0; letter-spacing: 1px; }
.ch-mode {
  font-size: 9px; font-weight: 700; padding: 1px 6px; border-radius: 3px;
  letter-spacing: 0.5px;
}
.ch-live  { background: #ff4757; color: #fff; }
.ch-paper { background: #f59e0b; color: #000; }
.ch-sep   { color: #1a1a2e; font-size: 10px; }
.ch-stat  { display: flex; align-items: center; gap: 4px; font-size: 10px; }
.ch-dot   { width: 5px; height: 5px; border-radius: 50%; background: #4a4e6a; }
.ch-dot-on { background: #00d4a8; box-shadow: 0 0 4px #00d4a8; }
.ch-pnl   { font-weight: 700; font-size: 11px; font-family: monospace; }
.ch-pnl-net { font-size: 12px; padding: 1px 6px; border-radius: 3px; background: rgba(255,255,255,0.04); }
.ch-pnl-detail { font-size: 10px; }
.ch-bybit { font-size: 10px; }
.ch-spacer { flex: 1; }
.ch-auto {
  display: flex; align-items: center; gap: 5px;
  padding: 4px 10px; border-radius: 4px; cursor: pointer;
  font-size: 10px; font-weight: 700; letter-spacing: 0.3px;
  border: 1px solid #2a2a3e; background: #111125; color: #6b6f8a;
  transition: all 0.15s;
}
.ch-auto:hover { border-color: #00d4a8; color: #00d4a8; }
.ch-auto-on {
  background: rgba(0,212,168,0.1); border-color: #00d4a8; color: #00d4a8;
}
.ch-auto-dot { width: 6px; height: 6px; border-radius: 50%; background: #4a4e6a; }
.ch-auto-on .ch-auto-dot { background: #00d4a8; animation: pulse 1.5s infinite; }

/* ── Left sidebar ─────────────────────────────────────────────────────────── */
.cl {
  width: 220px; flex-shrink: 0; display: flex; flex-direction: column;
  border-right: 1px solid #1a1a2e; overflow: hidden;
}
.cl-search {
  margin: 4px; padding: 4px 6px; height: 24px; box-sizing: border-box;
  background: #111125; border: 1px solid #1a1a2e; border-radius: 3px;
  color: #c0c4d8; font-size: 10px; outline: none; flex-shrink: 0;
}
.cl-search:focus { border-color: #00d4a8; }
.cl-watchlist {
  max-height: 30%; overflow-y: auto; flex-shrink: 0;
  border-bottom: 1px solid #1a1a2e;
}
.cl-watchlist::-webkit-scrollbar { width: 2px; }
.cl-watchlist::-webkit-scrollbar-thumb { background: #1a1a2e; }
.cl-pair {
  padding: 5px 10px; cursor: pointer; font-size: 12px; font-family: monospace;
  display: flex; align-items: baseline; gap: 4px;
  border-bottom: 1px solid #0a0a16; transition: background 0.1s;
}
.cl-pair:hover { background: #111125; }
.cl-active { background: #0a1a15; border-left: 2px solid #00d4a8; }
.cl-sym   { color: #f7931a; font-weight: 700; font-size: 11px; }
.cl-quote { color: #3a3e56; font-size: 10px; }
.cl-price { color: #00d4a8; font-size: 10px; margin-left: auto; font-family: monospace; }

/* ── Scanner ──────────────────────────────────────────────────────────────── */
.cl-scanner { flex: 1; display: flex; flex-direction: column; min-height: 0; }
.cl-sh {
  display: flex; align-items: center; justify-content: space-between;
  padding: 5px 8px; background: #0a0e16; flex-shrink: 0;
}
.cl-sh-title {
  font-size: 9px; font-weight: 800; color: #f7931a;
  letter-spacing: 1px; font-family: monospace;
}
.cl-sh-badge {
  font-size: 8px; font-weight: 700; color: #4a4e6a;
  padding: 1px 5px; border-radius: 3px;
  background: #111125; border: 1px solid #1a1a2e;
}
.cl-sh-live { color: #00d4a8; border-color: rgba(0,212,168,0.3); background: rgba(0,212,168,0.08); }
.cl-sf {
  flex: 1; overflow-y: auto; padding: 2px 0;
}
.cl-sf::-webkit-scrollbar { width: 2px; }
.cl-sf::-webkit-scrollbar-thumb { background: #1a1a2e; }
.cl-sig {
  display: flex; align-items: center; gap: 5px;
  padding: 4px 10px; cursor: pointer; font-size: 11px;
  border-bottom: 1px solid #0a0a16; transition: background 0.1s;
  border-left: 2px solid transparent;
}
.cl-sig:hover { background: #111125; }
.cl-sig-buy  { border-left-color: #00d4a8; }
.cl-sig-sell { border-left-color: #ff4757; }
.cl-dir { font-size: 9px; font-weight: 700; }
.cl-sig-coin { font-weight: 700; color: #c0c4d8; font-size: 11px; }
.cl-sig-conf { font-size: 10px; font-weight: 700; margin-left: auto; }
.cl-sig-price { font-size: 9px; color: #6b6f8a; font-family: monospace; display: none; }
.cl-sig-pat { font-size: 8px; color: #4a4e6a; display: none; }
.cl-sig-exec { font-size: 7px; font-weight: 700; padding: 1px 3px; border-radius: 2px; margin-left: auto; }
.cl-sig-filled { background: rgba(0,212,168,0.18); color: #00d4a8; }
.cl-sig-skip { background: rgba(75,78,106,0.18); color: #4a4e6a; }
.cl-empty {
  padding: 12px 8px; color: #4a4e6a; font-size: 10px;
  font-style: italic; text-align: center;
}

/* ── Center ────────────────────────────────────────────────────────────────── */
.cc {
  flex: 1; min-width: 0; display: flex; flex-direction: column;
}
.cc-ticker {
  display: flex; align-items: center; gap: 10px;
  padding: 6px 12px; border-bottom: 1px solid #1a1a2e; flex-shrink: 0;
}
.cc-pair { font-size: 13px; font-weight: 700; color: #c0c4d8; font-family: monospace; }
.cc-price { font-size: 15px; font-weight: 800; font-family: monospace; }
.cc-chg { font-size: 11px; font-weight: 600; }
.cc-vol { font-size: 10px; }
.cc-spacer { flex: 1; }
.cc-intervals { display: flex; gap: 2px; }
.cc-tf {
  padding: 2px 7px; border-radius: 3px; cursor: pointer;
  font-size: 10px; font-family: monospace; font-weight: 600;
  background: transparent; border: 1px solid #1a1a2e; color: #4a4e6a;
  transition: all 0.12s;
}
.cc-tf:hover { color: #c0c4d8; border-color: #2a2a4e; }
.cc-tf-on { background: #00d4a8; color: #000; border-color: #00d4a8; }
.cc-chart { flex: 1; min-height: 0; overflow: hidden; }

/* ── Right sidebar ────────────────────────────────────────────────────────── */
.cr {
  width: clamp(240px, 22vw, 320px); flex-shrink: 0;
  border-left: 1px solid #1a1a2e;
  display: flex; flex-direction: column;
  overflow-y: auto; overflow-x: hidden;
}
.cr::-webkit-scrollbar { width: 2px; }
.cr::-webkit-scrollbar-thumb { background: #1a1a2e; }
.cr-sec + .cr-sec { border-top: 1px solid #1a1a2e; }
.cr-sh {
  font-size: 9px; font-weight: 700; color: #4a4e6a;
  letter-spacing: 0.8px; font-family: monospace;
  padding: 5px 8px; background: #0a0e16; flex-shrink: 0;
}
.cr-count {
  display: inline-block; min-width: 14px; text-align: center;
  background: #1a1a2e; border-radius: 3px; padding: 0 4px;
  font-size: 9px; color: #8b8fa8; margin-left: 4px;
}
.cr-empty { padding: 8px; color: #4a4e6a; font-size: 10px; font-style: italic; }

/* Positions */
.cr-pos {
  padding: 4px 8px; border-bottom: 1px solid #0f0f1e;
  border-left: 2px solid transparent;
}
.cr-pos-up { border-left-color: #00d4a8; }
.cr-pos-dn { border-left-color: #ff4757; }
.cr-pos-top { display: flex; align-items: center; gap: 6px; font-size: 12px; }
.cr-pos-sym { font-weight: 700; color: #c0c4d8; }
.cr-pos-side { font-weight: 700; font-size: 9px; }
.cr-pos-pnl { font-weight: 600; margin-left: auto; font-size: 11px; }
.cr-pos-x {
  background: none; border: 1px solid #ff475744; color: #ff4757;
  border-radius: 2px; cursor: pointer; font-size: 8px; padding: 0 3px;
  line-height: 1.3; transition: all 0.12s;
}
.cr-pos-x:hover { background: #ff4757; color: #fff; }
.cr-pos-bot {
  display: flex; align-items: center; justify-content: space-between;
  font-size: 9px; margin-top: 1px;
}
.cr-pos-levels { display: flex; gap: 6px; font-size: 9px; font-family: monospace; }
.cr-pos-tax {
  display: flex; justify-content: space-between; align-items: center;
  font-size: 9px; margin-top: 2px; padding-top: 2px;
  border-top: 1px solid #1a1a2e; font-family: monospace;
}

/* Trade form */
.cr-trade { padding: 6px 8px; display: flex; flex-direction: column; gap: 5px; }
.cr-trade-price { font-size: 18px; font-weight: 700; color: #00d4a8; font-family: monospace; }
.cr-trade-row { display: flex; align-items: center; gap: 5px; }
.cr-trade-input {
  flex: 1; background: #0e0e20; border: 1px solid #1a1a2e; border-radius: 3px;
  color: #c0c4d8; padding: 4px 6px; font-size: 12px; outline: none;
}
.cr-trade-input:focus { border-color: #00d4a8; }
.cr-trade-hint { font-size: 9px; }
.cr-trade-fee { color: #f59e0b; }
.cr-trade-btns { display: flex; gap: 4px; }
.cr-btn-buy, .cr-btn-sell {
  flex: 1; padding: 6px 0; border: none; border-radius: 3px;
  font-weight: 700; font-size: 12px; cursor: pointer; transition: opacity 0.12s;
}
.cr-btn-buy  { background: #00d4a8; color: #000; }
.cr-btn-sell { background: #ff4757; color: #fff; }
.cr-btn-buy:hover  { opacity: 0.85; }
.cr-btn-sell:hover { opacity: 0.85; }
.cr-btn-buy:disabled, .cr-btn-sell:disabled { opacity: 0.4; cursor: not-allowed; }
.cr-trade-result { font-size: 10px; padding: 3px; border-radius: 3px; text-align: center; }
.cr-trade-result.up { background: rgba(0,212,168,0.12); }
.cr-trade-result.dn { background: rgba(255,71,87,0.12); }

/* Order book */
.cr-ob { font-family: monospace; font-size: 10px; }
.cr-ob-head {
  display: flex; justify-content: space-between; padding: 2px 8px;
  font-size: 8px; color: #4a4e6a; text-transform: uppercase; letter-spacing: 0.5px;
}
.cr-ob-row {
  display: flex; justify-content: space-between; padding: 1px 8px;
}
.cr-ob-mid {
  text-align: center; padding: 3px 0; font-size: 11px; font-weight: 700; color: #c0c4d8;
  border-top: 1px solid #111125; border-bottom: 1px solid #111125;
}
.cr-ob-mid .muted { font-size: 9px; font-weight: 400; margin-left: 6px; }

/* Trade history */
.cr-hist-row {
  display: flex; align-items: center; gap: 6px;
  padding: 4px 10px; font-size: 11px;
  border-bottom: 1px solid #0a0a16;
}
.cr-hist-sym { font-weight: 700; color: #c0c4d8; }
.cr-hist-dir { font-weight: 700; font-size: 9px; }
.cr-hist-pnl { font-weight: 600; margin-left: auto; font-family: monospace; }
.cr-hist-fee { font-size: 9px; font-family: monospace; color: #6b6f8a; }
.cr-hist-time { font-size: 8px; font-family: monospace; }
</style>
