<template>
  <div class="crypto-fo-view">
    <!-- ═══ HEADER ════════════════════════════════════════════════════════════ -->
    <header class="cfo-h">
      <h2 class="cfo-title">🔮 CRYPTO F&O</h2>
      <span :class="['cfo-mode', state.mode === 'live' ? 'cfo-live' : 'cfo-paper']">
        {{ state.mode === 'live' ? 'LIVE' : 'PAPER' }}
      </span>
      <span class="cfo-sep">|</span>
      <span class="cfo-tag" :class="state.testnet ? 'muted' : 'cfo-live'">
        {{ state.testnet ? 'TESTNET' : 'MAINNET' }}
      </span>
      <span class="cfo-sep">|</span>
      <span class="cfo-stat" :class="state.connected ? 'up' : 'muted'">
        <span class="cfo-dot" :class="state.connected ? 'cfo-dot-on' : ''"></span>
        {{ state.connected ? 'Deribit' : 'Offline' }}
      </span>

      <!-- P&L inline -->
      <span class="cfo-sep">|</span>
      <span :class="['cfo-pnl', pnl.realized >= 0 ? 'up' : 'dn']">
        R {{ pnl.realized >= 0 ? '+' : '' }}${{ pnl.realized.toFixed(2) }}
      </span>
      <span :class="['cfo-pnl', pnl.unrealized >= 0 ? 'up' : 'dn']">
        U {{ pnl.unrealized >= 0 ? '+' : '' }}${{ pnl.unrealized.toFixed(2) }}
      </span>
      <span :class="['cfo-pnl cfo-pnl-net', pnl.net >= 0 ? 'up' : 'dn']">
        Net {{ pnl.net >= 0 ? '+' : '' }}${{ pnl.net.toFixed(2) }}
      </span>
      <span class="cfo-detail muted">{{ pnl.option_positions || 0 }}O · {{ pnl.perp_positions || 0 }}P</span>

      <!-- Kill switch -->
      <span v-if="state.kill_switch?.halted" class="cfo-kill">
        🛑 {{ state.kill_switch.reason }}
      </span>

      <span class="cfo-spacer"></span>

      <button
        :class="['cfo-btn', state.running ? 'cfo-btn-on' : '']"
        @click="onToggle"
      >
        <span class="cfo-btn-dot"></span>
        {{ state.running ? 'RUNNING' : 'START' }}
      </button>
      <button class="cfo-btn cfo-btn-danger" @click="onFlatten" v-if="hasPositions">
        FLATTEN ALL
      </button>
    </header>

    <!-- ═══ BODY (3-col) ═════════════════════════════════════════════════════ -->
    <div class="cfo-body">

      <!-- COL 1: Market State -->
      <section class="cfo-col">
        <h3 class="cfo-col-title">Market State</h3>

        <div class="cfo-cards">
          <div class="cfo-card" v-for="asset in assets" :key="asset">
            <div class="cfo-card-head">
              <span class="cfo-card-sym">{{ asset }}</span>
              <span class="cfo-card-price">${{ formatPrice(state.spot_prices?.[asset]) }}</span>
            </div>
            <div class="cfo-card-row">
              <span class="muted">RV 1h</span>
              <span>{{ fmtPct(state.rv_cache?.[asset]?.rv_1h) }}</span>
            </div>
            <div class="cfo-card-row">
              <span class="muted">RV 24h</span>
              <span>{{ fmtPct(state.rv_cache?.[asset]?.rv_24h) }}</span>
            </div>
            <div class="cfo-card-row">
              <span class="muted">IV (ATM)</span>
              <span>{{ fmtPct(state.iv_cache?.[asset]) }}</span>
            </div>
            <div class="cfo-card-row" v-if="state.iv_cache?.[asset] && state.rv_cache?.[asset]?.rv_24h">
              <span class="muted">IV-RV</span>
              <span :class="volPremium(asset) >= 0 ? 'up' : 'dn'">
                {{ fmtPct(volPremium(asset)) }}
              </span>
            </div>
            <div class="cfo-card-row" v-if="state.funding_cache?.[asset]">
              <span class="muted">Funding</span>
              <span :class="(state.funding_cache[asset].ann || 0) >= 0 ? 'up' : 'dn'">
                {{ ((state.funding_cache[asset].ann || 0) * 100).toFixed(1) }}% ann
              </span>
            </div>
          </div>
        </div>

        <div class="cfo-meta muted">
          Cycle #{{ state.cycle_count || 0 }}
        </div>
      </section>

      <!-- COL 2: Positions -->
      <section class="cfo-col">
        <h3 class="cfo-col-title">Positions</h3>

        <!-- Options -->
        <div v-if="positions.options?.length" class="cfo-pos-group">
          <h4 class="cfo-group-title">Options (Gamma Scalp)</h4>
          <div class="cfo-pos" v-for="pos in positions.options" :key="pos.asset">
            <div class="cfo-pos-head">
              <span class="cfo-pos-sym">{{ pos.asset }} {{ pos.structure?.toUpperCase() }}</span>
              <span :class="['cfo-pos-side', pos.side === 'SHORT' ? 'dn' : 'up']">{{ pos.side }}</span>
            </div>
            <div class="cfo-pos-row"><span class="muted">Strike</span> ${{ formatPrice(pos.strike) }}</div>
            <div class="cfo-pos-row"><span class="muted">DTE</span> {{ pos.dte }}d</div>
            <div class="cfo-pos-row"><span class="muted">Notional</span> ${{ pos.notional_usd?.toLocaleString() }}</div>
            <div class="cfo-pos-row"><span class="muted">Net Δ</span> {{ pos.net_delta?.toFixed(4) }}</div>
            <div class="cfo-pos-row">
              <span class="muted">Gross</span>
              <span :class="(pos.pnl || 0) >= 0 ? 'up' : 'dn'">${{ (pos.pnl || 0).toFixed(2) }}</span>
            </div>
            <div class="cfo-pos-row">
              <span class="muted">Brk+Tax</span>
              <span class="dn" :title="taxBreakdown(pos, 'options')">-${{ optionBrokerage(pos).toFixed(2) }}</span>
            </div>
            <div class="cfo-pos-row cfo-pos-net">
              <span class="muted">Net</span>
              <span :class="netPnl(pos, 'options') >= 0 ? 'up' : 'dn'">
                ${{ netPnl(pos, 'options').toFixed(2) }}
              </span>
            </div>
          </div>
        </div>

        <!-- Perps -->
        <div v-if="positions.perps?.length" class="cfo-pos-group">
          <h4 class="cfo-group-title">Perpetuals</h4>
          <div class="cfo-pos" v-for="pos in positions.perps" :key="pos.asset">
            <div class="cfo-pos-head">
              <span class="cfo-pos-sym">{{ pos.asset }}-PERP</span>
              <span :class="['cfo-pos-side', pos.side === 'LONG' ? 'up' : 'dn']">{{ pos.side }}</span>
            </div>
            <div class="cfo-pos-row"><span class="muted">Entry</span> ${{ formatPrice(pos.entry_price) }}</div>
            <div class="cfo-pos-row"><span class="muted">Current</span> ${{ formatPrice(pos.current_price) }}</div>
            <div class="cfo-pos-row"><span class="muted">SL</span> ${{ formatPrice(pos.sl) }}</div>
            <div class="cfo-pos-row"><span class="muted">TP</span> ${{ formatPrice(pos.tp) }}</div>
            <div class="cfo-pos-row">
              <span class="muted">Gross</span>
              <span :class="(pos.pnl || 0) >= 0 ? 'up' : 'dn'">${{ (pos.pnl || 0).toFixed(2) }}</span>
            </div>
            <div class="cfo-pos-row">
              <span class="muted">Brk+Tax</span>
              <span class="dn" :title="taxBreakdown(pos, 'perp')">-${{ perpBrokerage(pos).toFixed(2) }}</span>
            </div>
            <div class="cfo-pos-row cfo-pos-net">
              <span class="muted">Net</span>
              <span :class="netPnl(pos, 'perp') >= 0 ? 'up' : 'dn'">
                ${{ netPnl(pos, 'perp').toFixed(2) }}
              </span>
            </div>
          </div>
        </div>

        <div v-if="!positions.options?.length && !positions.perps?.length" class="cfo-empty">
          No open positions
        </div>
      </section>

      <!-- COL 3: Signal Log -->
      <section class="cfo-col">
        <h3 class="cfo-col-title">Signals</h3>
        <div class="cfo-signals">
          <div
            v-for="sig in signals"
            :key="sig.id"
            :class="['cfo-sig', sig.action?.startsWith('OPEN') || sig.action === 'SELL_STRADDLE' ? 'cfo-sig-action' : '']"
          >
            <div class="cfo-sig-head">
              <span class="cfo-sig-asset">{{ sig.asset }}</span>
              <span :class="['cfo-sig-type', sig.type === 'options' ? 'cfo-sig-opt' : 'cfo-sig-perp']">
                {{ sig.type === 'options' ? 'OPT' : 'PERP' }}
              </span>
              <span :class="['cfo-sig-action-tag',
                sig.action === 'WAIT' ? '' :
                sig.action?.includes('LONG') ? 'up' :
                sig.action?.includes('SHORT') || sig.action === 'SELL_STRADDLE' ? 'dn' : 'up']">
                {{ sig.action }}
              </span>
              <span class="cfo-sig-time muted">{{ fmtTime(sig.ts) }}</span>
            </div>
            <div class="cfo-sig-detail muted" v-if="sig.reason">{{ sig.reason }}</div>
            <div class="cfo-sig-detail" v-if="sig.type === 'options'">
              IV={{ sig.iv }}% RV={{ sig.rv }}% Prem={{ sig.vol_premium }}%
              Strike=${{ formatPrice(sig.strike) }} DTE={{ sig.dte }}d
            </div>
            <div class="cfo-sig-detail" v-if="sig.type === 'perp'">
              Conf={{ sig.confidence }} RSI={{ sig.rsi?.toFixed(1) }}
              MACD={{ sig.macd_hist }} Regime={{ sig.funding_regime }}
            </div>
          </div>
          <div v-if="!signals.length" class="cfo-empty">No signals yet</div>
        </div>
      </section>
    </div>
  </div>
</template>

<script setup>
import { ref, computed, onMounted, onUnmounted } from 'vue'
import {
  getCryptoFoState,
  getCryptoFoPnl,
  getCryptoFoPositions,
  getCryptoFoSignals,
  startCryptoFo,
  stopCryptoFo,
  flattenCryptoFo,
} from '../../api/market/crypto'

// ── State ────────────────────────────────────────────────────────────────────
const state = ref({
  running: false, connected: false, mode: 'paper', testnet: true,
  cycle_count: 0, kill_switch: {}, spot_prices: {}, rv_cache: {},
  iv_cache: {}, funding_cache: {},
})
const pnl = ref({ realized: 0, unrealized: 0, net: 0, option_positions: 0, perp_positions: 0 })
const positions = ref({ options: [], perps: [] })
const signals = ref([])

const assets = computed(() => {
  const spots = state.value.spot_prices || {}
  return Object.keys(spots).length ? Object.keys(spots) : ['BTC', 'ETH']
})

const hasPositions = computed(() =>
  (positions.value.options?.length || 0) + (positions.value.perps?.length || 0) > 0
)

// ── Formatters ───────────────────────────────────────────────────────────────
const formatPrice = (v) => {
  if (!v) return '—'
  return Number(v) >= 1000 ? Number(v).toLocaleString(undefined, { maximumFractionDigits: 0 })
    : Number(v).toFixed(2)
}
const fmtPct = (v) => v != null ? (v * 100).toFixed(1) + '%' : '—'
const fmtTime = (ts) => {
  if (!ts) return ''
  return new Date(ts).toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' })
}
const volPremium = (asset) => {
  const iv = state.value.iv_cache?.[asset] || 0
  const rv = state.value.rv_cache?.[asset]?.rv_24h || 0
  return iv - rv
}

// Indian crypto tax: exchange fee + 1% TDS on sell + 30% tax on gains
const optionBrokerage = (pos) => {
  const notional = pos.notional_usd || 0
  const exchangeFee = notional * 0.0003 * 2  // 0.03% per leg x 2
  const tds = notional * 0.01
  const tax = Math.max(0, pos.pnl || 0) * 0.30
  return exchangeFee + tds + tax
}
const perpBrokerage = (pos) => {
  const entryN = (pos.entry_price || 0) * (pos.qty || 0)
  const exitN = (pos.current_price || 0) * (pos.qty || 0)
  const exchangeFee = (entryN + exitN) * 0.0005  // 0.05% per side
  const tds = exitN * 0.01
  const tax = Math.max(0, pos.pnl || 0) * 0.30
  return exchangeFee + tds + tax
}
const netPnl = (pos, type) => {
  const gross = pos.pnl || 0
  const brk = type === 'options' ? optionBrokerage(pos) : perpBrokerage(pos)
  return gross - brk
}
const taxBreakdown = (pos, type) => {
  if (type === 'options') {
    const n = pos.notional_usd || 0
    return `Fee: $${(n*0.0006).toFixed(2)} | TDS 1%: $${(n*0.01).toFixed(2)} | Tax 30%: $${(Math.max(0,pos.pnl||0)*0.3).toFixed(2)}`
  }
  const eN = (pos.entry_price||0)*(pos.qty||0), xN = (pos.current_price||0)*(pos.qty||0)
  return `Fee: $${((eN+xN)*0.0005).toFixed(2)} | TDS 1%: $${(xN*0.01).toFixed(2)} | Tax 30%: $${(Math.max(0,pos.pnl||0)*0.3).toFixed(2)}`
}

// ── Actions ──────────────────────────────────────────────────────────────────
const onToggle = async () => {
  try {
    if (state.value.running) {
      await stopCryptoFo()
    } else {
      await startCryptoFo()
    }
    await refresh()
  } catch (e) { console.error('toggle:', e) }
}

const onFlatten = async () => {
  if (!confirm('Flatten all crypto F&O positions?')) return
  try {
    await flattenCryptoFo()
    await refresh()
  } catch (e) { console.error('flatten:', e) }
}

// ── Polling ──────────────────────────────────────────────────────────────────
let timer = null

const refresh = async () => {
  try {
    const [s, p, pos, sig] = await Promise.all([
      getCryptoFoState(), getCryptoFoPnl(),
      getCryptoFoPositions(), getCryptoFoSignals(50),
    ])
    if (s.data?.data) state.value = s.data.data
    if (p.data?.data) pnl.value = p.data.data
    if (pos.data?.data) positions.value = pos.data.data
    if (sig.data?.data) signals.value = (sig.data.data || []).reverse()
  } catch (e) { /* silent */ }
}

onMounted(() => {
  refresh()
  timer = setInterval(refresh, 10_000)
})
onUnmounted(() => { if (timer) clearInterval(timer) })
</script>

<style scoped>
.crypto-fo-view { padding: 8px 12px; font-family: 'SF Mono', monospace; font-size: 12px; }

/* ── Header ─── */
.cfo-h { display: flex; align-items: center; gap: 8px; padding: 6px 0; border-bottom: 1px solid var(--border, #2a2a2e); flex-wrap: wrap; }
.cfo-title { font-size: 14px; font-weight: 700; margin: 0; }
.cfo-mode, .cfo-tag { font-size: 10px; font-weight: 700; padding: 1px 6px; border-radius: 3px; }
.cfo-paper { background: #3a3a00; color: #ffd700; }
.cfo-live { background: #003300; color: #00ff88; }
.cfo-sep { color: var(--text-muted, #666); }
.cfo-stat { display: flex; align-items: center; gap: 4px; }
.cfo-dot { width: 6px; height: 6px; border-radius: 50%; background: #666; }
.cfo-dot-on { background: #00ff88; box-shadow: 0 0 4px #00ff88; }
.cfo-pnl { font-weight: 600; font-size: 11px; }
.cfo-pnl-net { font-weight: 700; }
.cfo-detail { font-size: 10px; }
.cfo-kill { font-size: 10px; color: #ff4444; font-weight: 700; }
.cfo-spacer { flex: 1; }
.cfo-btn { font-size: 10px; font-weight: 700; padding: 3px 10px; border: 1px solid #444; border-radius: 4px; background: transparent; color: #ccc; cursor: pointer; display: flex; align-items: center; gap: 4px; }
.cfo-btn:hover { background: #333; }
.cfo-btn-on { border-color: #00ff88; color: #00ff88; }
.cfo-btn-on .cfo-btn-dot { background: #00ff88; box-shadow: 0 0 4px #00ff88; }
.cfo-btn-dot { width: 5px; height: 5px; border-radius: 50%; background: #666; }
.cfo-btn-danger { border-color: #ff4444; color: #ff4444; }
.cfo-btn-danger:hover { background: #330000; }

.up { color: #00ff88; }
.dn { color: #ff4444; }
.muted { color: var(--text-muted, #888); }

/* ── Body ─── */
.cfo-body { display: grid; grid-template-columns: 1fr 1fr 1fr; gap: 12px; margin-top: 8px; min-height: 300px; }
.cfo-col { background: var(--card-bg, #1a1a1e); border: 1px solid var(--border, #2a2a2e); border-radius: 6px; padding: 8px; overflow-y: auto; max-height: 500px; }
.cfo-col-title { font-size: 11px; font-weight: 700; text-transform: uppercase; color: var(--text-muted, #888); margin: 0 0 8px; letter-spacing: 0.5px; }

/* ── Cards ─── */
.cfo-cards { display: flex; flex-direction: column; gap: 8px; }
.cfo-card { background: var(--bg, #111); border: 1px solid var(--border, #2a2a2e); border-radius: 4px; padding: 6px 8px; }
.cfo-card-head { display: flex; justify-content: space-between; align-items: center; margin-bottom: 4px; }
.cfo-card-sym { font-weight: 700; font-size: 13px; }
.cfo-card-price { font-weight: 600; color: #fff; }
.cfo-card-row { display: flex; justify-content: space-between; font-size: 11px; padding: 1px 0; }
.cfo-meta { font-size: 10px; margin-top: 8px; text-align: center; }

/* ── Positions ─── */
.cfo-pos-group { margin-bottom: 8px; }
.cfo-group-title { font-size: 10px; font-weight: 700; color: #ffd700; margin: 0 0 4px; text-transform: uppercase; }
.cfo-pos { background: var(--bg, #111); border: 1px solid var(--border, #2a2a2e); border-radius: 4px; padding: 6px 8px; margin-bottom: 4px; }
.cfo-pos-head { display: flex; justify-content: space-between; align-items: center; margin-bottom: 2px; }
.cfo-pos-sym { font-weight: 700; }
.cfo-pos-side { font-size: 10px; font-weight: 700; padding: 1px 4px; border-radius: 2px; }
.cfo-pos-row { display: flex; justify-content: space-between; font-size: 11px; padding: 1px 0; }
.cfo-pos-net { font-weight: 700; border-top: 1px solid var(--border, #2a2a2e); margin-top: 2px; padding-top: 2px; }
.cfo-empty { color: var(--text-muted, #888); text-align: center; padding: 20px 0; font-size: 11px; }

/* ── Signals ─── */
.cfo-signals { display: flex; flex-direction: column; gap: 4px; }
.cfo-sig { background: var(--bg, #111); border: 1px solid var(--border, #2a2a2e); border-radius: 4px; padding: 4px 6px; }
.cfo-sig-action { border-left: 2px solid #ffd700; }
.cfo-sig-head { display: flex; align-items: center; gap: 6px; }
.cfo-sig-asset { font-weight: 700; }
.cfo-sig-type { font-size: 9px; font-weight: 700; padding: 1px 4px; border-radius: 2px; }
.cfo-sig-opt { background: #3a2a00; color: #ffd700; }
.cfo-sig-perp { background: #002a3a; color: #00ccff; }
.cfo-sig-action-tag { font-size: 10px; font-weight: 700; }
.cfo-sig-time { font-size: 10px; margin-left: auto; }
.cfo-sig-detail { font-size: 10px; margin-top: 2px; }
</style>
