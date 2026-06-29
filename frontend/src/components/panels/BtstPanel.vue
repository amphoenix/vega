<template>
  <div class="btst-sidebar">
    <!-- Header -->
    <div class="btst-header">
      <div class="btst-title-row">
        <span class="btst-title">🌙 BTST</span>
        <span class="mode-badge paper">PAPER</span>
        <span class="scanner-pill" :class="statusClass">
          <span class="pill-dot"></span>
          {{ state.status }}
        </span>
      </div>
      <div class="btst-controls">
        <button class="btst-btn start" @click="handleStart" :disabled="state.status !== 'IDLE'">Start</button>
        <button class="btst-btn stop"  @click="handleStop"  :disabled="state.status === 'IDLE'">Stop</button>
        <button class="btst-btn trigger" @click="handleTrigger" title="Manual scan trigger">🔍 Scan</button>
      </div>
    </div>

    <!-- Today's P&L -->
    <div class="btst-today-pnl" v-if="todayPnl.trades > 0">
      <span class="btst-today-label">Today</span>
      <span class="btst-today-trades">{{ todayPnl.trades }} trade{{ todayPnl.trades > 1 ? 's' : '' }}</span>
      <span class="btst-today-val" :class="todayPnl.pnl_abs >= 0 ? 'up' : 'dn'">
        {{ formatPnl(todayPnl.pnl_abs) }}
      </span>
    </div>

    <!-- Stats row -->
    <div class="btst-stats-row">
      <div class="btst-stat">
        <span class="btst-stat-label">Candidates</span>
        <span class="btst-stat-val">{{ state.candidates_found }}</span>
      </div>
      <div class="btst-stat">
        <span class="btst-stat-label">Positions</span>
        <span class="btst-stat-val">{{ state.positions_count }}</span>
      </div>
      <div class="btst-stat" :class="stats.total_pnl_abs >= 0 ? 'up' : 'dn'">
        <span class="btst-stat-label">All-time P&amp;L</span>
        <span class="btst-stat-val">{{ formatPnl(stats.total_pnl_abs) }}</span>
      </div>
      <div class="btst-stat">
        <span class="btst-stat-label">Win%</span>
        <span class="btst-stat-val">{{ stats.win_rate }}%</span>
      </div>
    </div>

    <!-- Overnight Positions -->
    <div class="btst-section" v-if="positions.length">
      <div class="btst-section-title">Positions 🌙</div>
      <div
        v-for="pos in positions"
        :key="pos.symbol + pos.ts"
        class="btst-pos-card"
        :class="pos.direction"
      >
        <div class="btst-card-top">
          <span class="btst-sym">{{ cleanSymbol(pos.symbol) }}</span>
          <span class="btst-dir-badge">{{ pos.opt_type || (pos.direction === 'bull' ? 'CE' : 'PE') }}</span>
          <span class="btst-status-badge" :class="pos.status?.toLowerCase()">{{ pos.status }}</span>
          <span v-if="pos.pnl_abs !== null && pos.pnl_abs !== undefined"
                class="btst-pnl" :class="pos.pnl_abs >= 0 ? 'up' : 'dn'">
            {{ pos.pnl_abs >= 0 ? '+' : '' }}₹{{ pos.pnl_abs?.toFixed(2) }}
          </span>
          <span v-if="pos.pnl_pct !== null && pos.pnl_pct !== undefined"
                class="btst-pnl-pct" :class="pos.pnl_pct >= 0 ? 'up' : 'dn'">
            ({{ pos.pnl_pct >= 0 ? '+' : '' }}{{ pos.pnl_pct?.toFixed(2) }}%)
          </span>
        </div>
        <div class="btst-option-trade" v-if="pos.strike">
          <span class="btst-option-tag">{{ pos.opt_type }} {{ pos.strike }}</span>
          <span class="btst-expiry">Exp {{ pos.expiry }}</span>
          <span class="btst-lot" v-if="pos.lot_size">Lot {{ pos.lot_size }}</span>
        </div>
        <div class="btst-card-detail">
          <span v-if="pos.entry_premium" class="btst-prem-entry">Premium ₹{{ pos.entry_premium?.toFixed(1) }}</span>
          <span v-if="pos.target_premium" class="btst-prem-target">T ₹{{ pos.target_premium?.toFixed(1) }}</span>
          <span v-if="pos.sl_premium" class="btst-prem-sl">SL ₹{{ pos.sl_premium?.toFixed(1) }}</span>
          <span>Qty {{ pos.qty }}{{ pos.lot_size > 1 ? ' lots' : '' }}</span>
          <span>₹{{ pos.notional?.toLocaleString() }}</span>
        </div>
        <div class="btst-card-detail btst-spot-row">
          <span>Spot ₹{{ pos.entry_price?.toFixed(2) }}</span>
          <span class="btst-dir">{{ pos.direction === 'bull' ? '▲ BUY' : '▼ SHORT' }}</span>
          <span>Score {{ pos.ai_score }}</span>
        </div>
        <div class="btst-card-rationale" v-if="pos.rationale">{{ pos.rationale }}</div>
      </div>
    </div>

    <!-- Signals -->
    <div class="btst-section">
      <div class="btst-section-title">Signals</div>
      <div v-if="!signals.length" class="btst-empty">No signals yet — scan runs at 15:20 IST</div>
      <div
        v-for="sig in signals"
        :key="sig.symbol + sig.ts"
        class="btst-signal-card"
        :class="[sig.direction, sig.rejected ? 'rejected' : '']"
      >
        <div class="btst-card-top">
          <span class="btst-sym">{{ cleanSymbol(sig.symbol) }}</span>
          <span class="btst-score-badge" :class="scoreClass(sig.score)">{{ sig.score }}</span>
          <span class="btst-dir-badge">{{ sig.direction === 'bull' ? '▲' : '▼' }}</span>
          <span v-if="sig._source === 'technical' || sig._source === 'technical_fallback'" class="btst-source-tag tech">TECH</span>
          <span v-if="sig.rejected" class="btst-rejected-tag">SKIP</span>
        </div>
        <div class="btst-card-rationale" :class="{ bright: !sig.rejected }">{{ sig.rationale }}</div>
        <div class="btst-option-trade" v-if="sig.strike">
          <span class="btst-option-tag" :class="sig.direction">{{ sig.opt_type || (sig.direction === 'bull' ? 'CE' : 'PE') }} {{ sig.strike }}</span>
          <span class="btst-expiry">Exp {{ sig.expiry }}</span>
        </div>
        <div class="btst-premium-row" v-if="sig.entry_premium || sig.target_premium || sig.sl_premium">
          <span class="btst-premium-entry" v-if="sig.entry_premium">Entry ₹{{ sig.entry_premium?.toFixed(1) }}</span>
          <span class="btst-premium-target" v-if="sig.target_premium">Tgt ₹{{ sig.target_premium?.toFixed(1) }}</span>
          <span class="btst-premium-sl" v-if="sig.sl_premium">SL ₹{{ sig.sl_premium?.toFixed(1) }}</span>
        </div>
        <div class="btst-card-meta" v-if="sig.target_pct || sig.risk_note">
          <span>Target {{ sig.target_pct?.toFixed(1) }}%</span>
          <span class="btst-risk" v-if="sig.risk_note">⚠ {{ sig.risk_note }}</span>
        </div>
      </div>
    </div>

    <!-- Trade Log -->
    <div class="btst-section btst-log-section">
      <div class="btst-section-title">Log</div>
      <div class="btst-log-feed">
        <div v-for="(entry, i) in logEntries" :key="i" class="log-entry" :class="entry.level">
          <span class="log-time">{{ formatTime(entry.ts) }}</span>
          <span class="log-msg">{{ entry.message }}</span>
        </div>
        <div v-if="!logEntries.length" class="btst-empty">No activity yet</div>
      </div>
    </div>
  </div>
</template>

<script setup>
import { ref, computed, onMounted, onUnmounted } from 'vue'
import {
  startBtst, stopBtst, triggerBtst,
  getBtstStatus, getBtstStats, createBtstStream,
} from '../../api/market/btst.js'

const state      = ref({ status: 'IDLE', positions_count: 0, candidates_found: 0, signals_count: 0 })
const stats      = ref({ total_pnl_abs: 0, total_pnl_pct: 0, win_rate: 0, total_trades: 0 })
const signals    = ref([])
const positions  = ref([])
const logEntries = ref([])

let eventSource = null

// ── SSE ───────────────────────────────────────────────────────────────────────

function connectSSE() {
  if (eventSource) return
  eventSource = createBtstStream()

  eventSource.onmessage = (e) => {
    try { handleSSEEvent(JSON.parse(e.data)) } catch {}
  }

  eventSource.onerror = () => {
    eventSource?.close()
    eventSource = null
    setTimeout(connectSSE, 5000)
  }
}

function handleSSEEvent(data) {
  if (data.type === 'heartbeat') return

  if (data.type === 'signal') {
    const idx = signals.value.findIndex(s => s.symbol === data.symbol && s.direction === data.direction)
    if (idx >= 0) signals.value.splice(idx, 1, data)
    else signals.value.unshift(data)
    signals.value = [...signals.value].sort((a, b) => b.score - a.score).slice(0, 50)
  }

  if (data.type === 'position_update') {
    const idx = positions.value.findIndex(p => p.symbol === data.symbol && p.ts === data.ts)
    if (idx >= 0) positions.value.splice(idx, 1, data)
    else positions.value.unshift(data)
  }

  if (data.type === 'exit') {
    const idx = positions.value.findIndex(p => p.symbol === data.symbol && p.status === 'HOLDING')
    if (idx >= 0) {
      positions.value[idx] = {
        ...positions.value[idx],
        status: 'EXITED',
        exit_price: data.exit_price,
        pnl_pct: data.pnl_pct,
        exit_reason: data.reason,
      }
    }
  }

  if (data.type === 'stats') {
    stats.value = { ...stats.value, ...data }
    if (data.status) state.value = { ...state.value, status: data.status }
  }

  if (data.type === 'log') {
    logEntries.value.unshift(data)
    if (logEntries.value.length > 30) logEntries.value.pop()
  }
}

// ── Actions ───────────────────────────────────────────────────────────────────

async function handleStart() {
  await startBtst()
  await refreshStatus()
}

async function handleStop() {
  await stopBtst()
  await refreshStatus()
}

async function handleTrigger() {
  await triggerBtst()
}

async function refreshStatus() {
  try {
    const [statusRes, statsRes] = await Promise.all([getBtstStatus(), getBtstStats()])
    const sd = statusRes?.data || statusRes
    if (sd) {
      state.value = { ...state.value, ...sd }
      if (sd.signals)   signals.value   = sd.signals
      if (sd.positions) positions.value = sd.positions
    }
    const st = statsRes?.data || statsRes
    if (st) stats.value = st
  } catch (e) { console.error('[btst] refreshStatus failed:', e) }
}

// ── Computed ──────────────────────────────────────────────────────────────────

const statusClass = computed(() => ({
  running:      state.value.status === 'SCANNING',
  holding:      state.value.status === 'HOLDING',
  morning_exit: state.value.status === 'MORNING_EXIT',
  idle:         state.value.status === 'IDLE',
}))

const todayPnl = computed(() => stats.value.today || { trades: 0, pnl_abs: 0, pnl_pct: 0 })

// ── Helpers ───────────────────────────────────────────────────────────────────

function cleanSymbol(sym) {
  return sym?.replace('.NS', '').replace('^', '').replace('NSEI', 'NIFTY').replace('BSESN', 'SENSEX') ?? sym
}

function scoreClass(score) {
  if (score >= 80) return 'score-high'
  if (score >= 65) return 'score-mid'
  return 'score-low'
}

function formatPnl(val) {
  if (val == null) return '₹0'
  const sign = val >= 0 ? '+' : '-'
  return `${sign}₹${Math.abs(val).toFixed(0)}`
}

function formatTime(ts) {
  if (!ts) return ''
  try {
    return new Date(ts).toLocaleTimeString('en-IN', { hour: '2-digit', minute: '2-digit' })
  } catch { return ts }
}

// ── Lifecycle ─────────────────────────────────────────────────────────────────

onMounted(async () => {
  await refreshStatus()
  connectSSE()
})

onUnmounted(() => {
  eventSource?.close()
  eventSource = null
})
</script>

<style scoped>
.btst-sidebar {
  width: 260px;
  min-width: 220px;
  max-width: 300px;
  height: 100%;
  display: flex;
  flex-direction: column;
  overflow-y: auto;
  background: var(--bg0);
  color: var(--tx0);
  border-left: 1px solid var(--bd1);
  flex-shrink: 0;
}

.btst-header {
  padding: 10px 12px 8px;
  border-bottom: 2px solid var(--acc);
  flex-shrink: 0;
}

.btst-title-row {
  display: flex;
  align-items: center;
  gap: 8px;
  margin-bottom: 6px;
}

.btst-title {
  font-size: 13px;
  font-weight: 700;
  color: var(--acc);
  letter-spacing: 0.05em;
}

.btst-controls {
  display: flex;
  gap: 6px;
}

.btst-btn {
  font-size: 11px;
  padding: 3px 10px;
  border-radius: 4px;
  border: none;
  cursor: pointer;
  font-weight: 600;
  transition: opacity 0.15s;
}

.btst-btn.start   { background: var(--bull); color: #fff; }
.btst-btn.stop    { background: var(--bear); color: #fff; }
.btst-btn.trigger { background: var(--bg3); color: var(--tx1); font-size: 10px; }
.btst-btn:disabled { opacity: 0.35; cursor: not-allowed; }

.btst-today-pnl {
  display: flex;
  align-items: center;
  gap: 8px;
  padding: 7px 12px;
  border-bottom: 1px solid var(--bd1);
  background: var(--bg1);
}

.btst-today-label {
  font-size: 10px;
  font-weight: 700;
  color: var(--tx2);
  text-transform: uppercase;
  letter-spacing: 0.04em;
}

.btst-today-trades {
  font-size: 10px;
  color: var(--tx3);
}

.btst-today-val {
  margin-left: auto;
  font-size: 13px;
  font-weight: 700;
}

.btst-stats-row {
  display: flex;
  flex-shrink: 0;
  border-bottom: 1px solid var(--bd1);
}

.btst-stat {
  flex: 1;
  padding: 8px 6px;
  text-align: center;
  border-right: 1px solid var(--bd1);
}

.btst-stat:last-child { border-right: none; }

.btst-stat-label {
  display: block;
  font-size: 9px;
  color: var(--tx2);
  text-transform: uppercase;
  letter-spacing: 0.04em;
  margin-bottom: 2px;
}

.btst-stat-val {
  display: block;
  font-size: 13px;
  font-weight: 700;
  color: var(--tx0);
}

.btst-stat.up .btst-stat-val { color: var(--bull); }
.btst-stat.dn .btst-stat-val { color: var(--bear); }

.btst-section {
  flex-shrink: 0;
  border-bottom: 1px solid var(--bd1);
}

.btst-section-title {
  font-size: 10px;
  font-weight: 700;
  color: var(--tx1);
  text-transform: uppercase;
  letter-spacing: 0.08em;
  padding: 8px 10px 5px;
  border-bottom: 1px solid var(--bd1);
}

.btst-signal-card,
.btst-pos-card {
  padding: 10px 12px;
  border-bottom: 1px solid var(--bd1);
  border-left: 3px solid transparent;
  transition: background 0.15s;
}

.btst-signal-card:hover,
.btst-pos-card:hover {
  background: var(--bg1);
}

.btst-signal-card.bull { border-left-color: var(--bull); }
.btst-signal-card.bear { border-left-color: var(--bear); }
.btst-signal-card.bull:not(.rejected) { background: color-mix(in srgb, var(--bull) 5%, transparent); }
.btst-signal-card.bear:not(.rejected) { background: color-mix(in srgb, var(--bear) 5%, transparent); }
.btst-signal-card.rejected { opacity: 0.55; border-left-style: dashed; }

.btst-pos-card.bull { border-left-color: var(--bull); background: color-mix(in srgb, var(--bull) 6%, transparent); }
.btst-pos-card.bear { border-left-color: var(--bear); background: color-mix(in srgb, var(--bear) 6%, transparent); }

.btst-card-top {
  display: flex;
  align-items: center;
  gap: 6px;
  margin-bottom: 3px;
}

.btst-sym {
  font-size: 13px;
  font-weight: 700;
  color: #fff;
  letter-spacing: 0.02em;
}

.btst-score-badge {
  font-size: 11px;
  font-weight: 700;
  padding: 2px 6px;
  border-radius: 4px;
  min-width: 24px;
  text-align: center;
}

.btst-score-badge.score-high { background: color-mix(in srgb, var(--bull) 20%, var(--bg2)); color: var(--bull); }
.btst-score-badge.score-mid  { background: color-mix(in srgb, var(--acc) 20%, var(--bg2)); color: var(--acc); }
.btst-score-badge.score-low  { background: var(--bg3); color: var(--tx3); }

.btst-dir-badge { font-size: 12px; }
.btst-signal-card.bull .btst-dir-badge { color: var(--bull); }
.btst-signal-card.bear .btst-dir-badge { color: var(--bear); }
.btst-rejected-tag { font-size: 9px; color: #f87171; background: rgba(248,113,113,0.12); padding: 1px 5px; border-radius: 3px; font-weight: 600; }

.btst-pnl { font-size: 11px; font-weight: 700; margin-left: auto; }

.btst-card-rationale {
  font-size: 11px;
  color: var(--tx2);
  line-height: 1.4;
  margin-bottom: 4px;
}
.btst-card-rationale.bright {
  color: var(--tx1);
}

.btst-card-detail {
  display: flex;
  gap: 8px;
  font-size: 10px;
  color: var(--tx3);
  margin-bottom: 2px;
}

.btst-dir { margin-left: auto; }

.btst-card-meta {
  display: flex;
  gap: 8px;
  font-size: 10px;
  color: var(--tx2);
}

.btst-risk { color: #f59e0b; }

.btst-option-trade {
  display: flex;
  flex-wrap: wrap;
  gap: 5px;
  margin: 4px 0 2px;
  align-items: center;
}
.btst-option-tag {
  font-size: 11px;
  font-weight: 700;
  padding: 2px 8px;
  border-radius: 4px;
  background: rgba(99,102,241,0.18);
  color: #a5b4fc;
}
.btst-option-tag.bull {
  background: color-mix(in srgb, var(--bull) 18%, transparent);
  color: #6ee7b7;
}
.btst-option-tag.bear {
  background: color-mix(in srgb, var(--bear) 18%, transparent);
  color: #fca5a5;
}
.btst-expiry { font-size: 10px; color: var(--tx2); }
.btst-lot { font-size: 9px; color: var(--tx3); }
.btst-premium-row {
  display: flex;
  gap: 10px;
  margin: 3px 0;
  align-items: center;
}
.btst-premium-entry { font-size: 11px; font-weight: 700; color: #e2e8f0; }
.btst-premium-target { font-size: 11px; color: #34d399; font-weight: 700; }
.btst-premium-sl { font-size: 11px; color: #f87171; font-weight: 600; }
.btst-prem-entry { font-weight: 700; color: var(--tx0); }
.btst-prem-target { color: #34d399; font-weight: 600; }
.btst-prem-sl { color: #f87171; font-weight: 600; }
.btst-spot-row { color: var(--tx3); font-size: 9px; margin-top: 2px; }

.btst-status-badge {
  font-size: 9px;
  font-weight: 700;
  padding: 1px 5px;
  border-radius: 3px;
  text-transform: uppercase;
}

.btst-status-badge.holding { background: color-mix(in srgb, var(--acc) 20%, var(--bg2)); color: var(--acc); }
.btst-status-badge.exited  { background: color-mix(in srgb, var(--bull) 20%, var(--bg2)); color: var(--bull); }

.scanner-pill {
  display: inline-flex;
  align-items: center;
  gap: 4px;
  font-size: 9px;
  font-weight: 700;
  padding: 2px 7px;
  border-radius: 10px;
  text-transform: uppercase;
  background: var(--bg3);
  color: var(--tx3);
}

.scanner-pill.running      { background: color-mix(in srgb, var(--bull) 20%, var(--bg2)); color: var(--bull); }
.scanner-pill.holding      { background: color-mix(in srgb, var(--acc) 20%, var(--bg2)); color: var(--acc); }
.scanner-pill.morning_exit { background: color-mix(in srgb, #f59e0b 20%, var(--bg2)); color: #f59e0b; }

.pill-dot {
  width: 5px;
  height: 5px;
  border-radius: 50%;
  background: currentColor;
}

.mode-badge {
  font-size: 9px;
  font-weight: 700;
  padding: 1px 5px;
  border-radius: 3px;
  text-transform: uppercase;
}

.mode-badge.paper { background: var(--bg3); color: var(--tx2); }

.btst-empty {
  font-size: 10px;
  color: var(--tx3);
  padding: 12px 10px;
  text-align: center;
}

.up { color: var(--bull); }
.dn { color: var(--bear); }

.btst-log-section { flex: 1; min-height: 0; }

.btst-log-feed {
  max-height: 240px;
  overflow-y: auto;
  scrollbar-width: thin;
}

.btst-source-tag {
  font-size: 8px;
  font-weight: 700;
  padding: 1px 4px;
  border-radius: 2px;
  text-transform: uppercase;
}

.btst-source-tag.tech {
  background: color-mix(in srgb, #f59e0b 20%, var(--bg2));
  color: #f59e0b;
}

.log-entry {
  display: flex;
  gap: 6px;
  padding: 3px 10px;
  font-size: 10px;
  line-height: 1.4;
  border-bottom: 1px solid var(--bd1);
}

.log-time { color: var(--tx3); flex-shrink: 0; }
.log-msg  { color: var(--tx1); }
.log-entry.error .log-msg { color: var(--bear); }
.log-entry.warn  .log-msg { color: #f59e0b; }
</style>
