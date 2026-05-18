<template>
  <div class="rs-panel">
    <div class="rs-header hdr-scanner">
      <span class="rs-title">F&amp;O SCANNER</span>
      <div class="rs-hdr-actions">
        <button
          class="fo-scan-now"
          @click="triggerScan"
          :disabled="!!foAnalysing"
          :title="
            foAnalysing
              ? `Scanning ${foAnalysing.ticker_clean} (${foAnalysing.index}/${foAnalysing.total})`
              : 'Run a one-shot scan across all indices right now (LLM cost ~$0.05)'
          "
        >
          <span v-if="foAnalysing">
            <span class="lv-spin">◐</span>
            Scanning {{ foAnalysing.ticker_clean }} ({{
              foAnalysing.index
            }}/{{ foAnalysing.total }})
          </span>
          <span v-else>⚡ Scan now</span>
        </button>
        <span
          class="fo-auto-indicator"
          :class="{
            'fo-auto-live': scannerRunning && nseMarketOpen,
            'fo-auto-paused': scannerRunning && !nseMarketOpen,
          }"
          :title="
            !scannerRunning
              ? 'Connecting to scanner…'
              : nseMarketOpen
                ? `Live — auto-scanning every ${foIntervalLabel}`
                : 'NSE market is closed (09:15–15:30 IST). Scanner thread is alive but cycles are paused. Click ⚡ to force a one-shot scan on cached data.'
          "
        >
          <span class="fo-auto-dot"></span>
          {{
            !scannerRunning
              ? "connecting…"
              : nseMarketOpen
                ? `LIVE · ${foIntervalLabel}`
                : "PAUSED · NSE CLOSED"
          }}
        </span>
      </div>
    </div>
    <!-- Currently analysing ticker -->
    <div class="fo-analysing" v-if="foAnalysing">
      <span class="fo-spin">⟳</span>
      <span class="fo-analysing-sym">{{ foAnalysing.ticker_clean }}</span>
      <span class="fo-analysing-prog"
        >{{ foAnalysing.index }}/{{ foAnalysing.total }}</span
      >
    </div>
    <div class="rs-empty" v-if="!foFeed.length && !foAnalysing">
      <span v-if="nseMarketOpen"
        >{{ foUniverseSize }} tickers · auto every {{ foIntervalLabel }} ·
        ⚡ to scan now</span
      >
      <span v-else
        >🌙 NSE closed · auto-scan paused · ⚡ to force-scan on cached
        data</span
      >
    </div>
    <div class="fo-feed" v-if="foFeed.length">
      <div
        v-for="(ev, i) in foFeed
          .filter((e) => e.type !== 'scan_analysing')
          .slice(0, 30)"
        :key="
          (ev.ticker || '') +
          '|' +
          (ev.type || '') +
          '|' +
          (ev.timestamp || i)
        "
        :class="[
          'fo-evt',
          'fo-evt-' + ev.type,
          ev.verdict
            ? 'fo-evt-verdict-' +
              (ev.verdict || '').replace(/ /g, '_').toLowerCase()
            : '',
          ev.option_symbol || ev.ticker ? 'fo-evt-clickable' : '',
        ]"
        :title="
          transactionTooltip(ev) ||
          (ev.option_symbol
            ? 'Click to chart ' + ev.option_symbol + ' premium'
            : ev.ticker
              ? 'Click to view ' + ev.ticker + ' chart'
              : '')
        "
        @click="
          (ev.option_symbol || ev.ticker) &&
          emit('select-ticker', ev.option_symbol || ev.ticker)
        "
      >
        <!-- Badge -->
        <span
          v-if="ev.type === 'scan_trade'"
          class="fo-evt-badge fo-badge-scan_trade"
          >TRADE</span
        >
        <span
          v-else-if="ev.type === 'scan_signal'"
          :class="[
            'fo-evt-badge',
            'fo-badge-verdict-' + verdictClass(ev.verdict),
          ]"
          >{{ shortVerdict(ev.verdict) }}</span
        >
        <span
          v-else-if="ev.type === 'scan_skip'"
          class="fo-evt-badge fo-badge-scan_skip"
          >SKIP</span
        >

        <!-- Option contract symbol (primary) — always show if available -->
        <span class="fo-sym fo-sym-option" v-if="ev.display_symbol || ev.option_symbol">{{
          ev.display_symbol || ev.option_symbol
        }}</span>
        <span class="fo-sym" v-else>{{
          (ev.ticker || ev.trading_symbol || ev.underlying || "")
            .replace(/\.(NS|BO)$/i, "")
            .replace(/^\^/, "")
        }}</span>
        <!-- Underlying reference when option symbol is shown -->
        <span class="fo-underlying" v-if="ev.option_symbol">{{
          (ev.ticker || ev.underlying || "")
            .replace(/\.(NS|BO)$/i, "")
            .replace(/^\^/, "")
        }}</span>

        <!-- Instrument type (CE / PE / FUT / EQ) -->
        <span
          class="fo-inst-tag"
          v-if="ev.instrument || ev.instrument_type"
          :class="
            'itype-' +
            (ev.instrument || ev.instrument_type || '').toLowerCase()
          "
          >{{ ev.instrument || ev.instrument_type }}</span
        >

        <!-- Strike (shown on all signal types, not just trade) -->
        <span class="fo-strike" v-if="ev.strike">@{{ ev.strike }}</span>

        <!-- Confidence -->
        <span
          :class="[
            'fo-conf',
            ev.confidence >= 70
              ? 'conf-hi'
              : ev.confidence >= 50
                ? 'conf-mid'
                : 'conf-lo',
          ]"
          v-if="ev.confidence != null"
          >{{ ev.confidence }}%</span
        >

        <!-- Live option premium from IndMoney / Greeks-based estimate -->
        <span class="fo-option-ltp" v-if="ev.option_ltp"
          >₹{{ Number(ev.option_ltp).toFixed(0) }}</span
        >

        <!-- Greeks row: delta + expiry (from ticket) -->
        <span class="fo-greeks" v-if="ev.ticket">
          Δ{{ ev.ticket.greeks?.delta?.toFixed(2) }} · θ₹{{
            Math.abs(ev.ticket.greeks?.theta_per_day || 0).toFixed(0)
          }}/d · {{ ev.ticket.days_to_expiry }}DTE
        </span>

        <!-- SL / T1 / T2 — prefer ticket exit (option premium ₹), fall back to underlying levels -->
        <span
          class="fo-levels"
          v-if="ev.ticket?.exit"
          :title="'Stop-loss / Target-1 / Target-2 in option PREMIUM. Exit when premium hits SL (loss) or T1/T2 (profit).'"
        >
          SL ₹{{ ev.ticket.exit.stop_loss_inr?.toFixed(0) }} · T1 ₹{{
            ev.ticket.exit.target_1_inr?.toFixed(0)
          }}
          <span v-if="ev.ticket.exit.target_2_inr">
            · T2 ₹{{ ev.ticket.exit.target_2_inr?.toFixed(0) }}</span
          >
        </span>
        <span
          class="fo-levels"
          v-else-if="ev.stop_loss || ev.target_1"
          :title="'Stop-loss / Target on the UNDERLYING price (not option premium). Use as reference; option SL/T1 are computed from these.'"
        >
          <span v-if="ev.stop_loss">SL {{ ev.stop_loss }}</span>
          <span v-if="ev.target_1"> · T1 {{ ev.target_1 }}</span>
          <span v-if="ev.target_2"> · T2 {{ ev.target_2 }}</span>
        </span>

        <!-- Entry/premium for executed trades -->
        <span
          class="fo-entry"
          v-if="ev.type === 'scan_trade' && ev.premium"
          >₹{{ ev.premium }}</span
        >
        <!-- P&L for exits -->
        <span
          :class="['fo-pnl', (ev.pnl || 0) >= 0 ? 'up' : 'dn']"
          v-if="ev.pnl != null"
          >{{ ev.pnl >= 0 ? "+" : "" }}₹{{
            Math.abs(ev.pnl || 0).toFixed(0)
          }}</span
        >
        <!-- Skip reason -->
        <span
          class="fo-skip-reason"
          v-if="ev.type === 'scan_skip' && ev.reason"
          >{{
            ev.reason
              .replace(
                "Outside safe trading hours (09:15-15:00)",
                "⏰ hours",
              )
              .replace(/^Confidence \d+ < \d+$/, "low conf")
              .replace(/^Already holding/, "dup pos")
          }}</span
        >
        <!-- Real broker transaction (BUY CE / BUY PE / SELL FUT) -->
        <span
          :class="[
            'fo-action',
            transactionLabel(ev).startsWith('🔴')
              ? 'fo-action-sell'
              : 'fo-action-buy',
          ]"
          v-if="ev.action && ev.type !== 'scan_skip'"
          :title="transactionTooltip(ev)"
          >{{ transactionLabel(ev) }}</span
        >

        <!-- Add-to-Watching button -->
        <button
          v-if="
            ev.ticket?.trading_symbol &&
            ev.type !== 'scan_skip' &&
            !isTracked(ev.ticket.trading_symbol)
          "
          class="fo-watch-btn"
          @click.stop="trackEntered(ev.ticket)"
          :title="`Add ${ev.ticket.trading_symbol} to WATCHING — server-side tick monitor will fire SL/T1/T2/time-exit alerts even when this tab is closed.`"
        >
          👁 Watch
        </button>
        <span
          v-else-if="
            ev.ticket?.trading_symbol &&
            isTracked(ev.ticket.trading_symbol)
          "
          class="fo-watch-pinned"
          :title="'Already tracking — see WATCHING section below'"
          >📌 Watching</span
        >
      </div>
    </div>
    <div class="fo-scan-status" v-if="foScannerState?.last_scan">
      Last scan: {{ fmtTime(foScannerState.last_scan) }}
      <span
        v-if="foScannerState.trades_placed?.length"
        class="fo-trades-count"
      >
        · 🟢 {{ foScannerState.trades_placed.length }} trades</span
      >
    </div>
  </div>
</template>

<script setup>
import { ref, computed, onMounted, onUnmounted } from 'vue'
import { fmtTime } from '../../utils/formatters'
import { storeToRefs } from 'pinia'
import { useFoScannerStore } from '../../stores/useFoScannerStore'
import { useLiveTradingStore } from '../../stores/useLiveTradingStore'

const emit = defineEmits(['select-ticker'])

const foStore = useFoScannerStore()
const ltStore = useLiveTradingStore()
const { scannerRunning, foScannerState, foFeed, foAnalysing, foUniverseSize, foIntervalLabel } = storeToRefs(foStore)
const { trackedPositions, liveTickets } = storeToRefs(ltStore)

// 1 Hz tick so nseMarketOpen flips at exact market open/close times
const _clockTick = ref(0)
let _clockTimer = null

const NSE_HOLIDAYS = new Set([
  '2025-01-26', '2025-03-14', '2025-04-14', '2025-04-18', '2025-05-01',
  '2025-08-15', '2025-08-27', '2025-10-02', '2025-10-20', '2025-10-21',
  '2025-11-05', '2025-12-25', '2026-01-26', '2026-03-02', '2026-04-03',
  '2026-04-14', '2026-05-01', '2026-08-17', '2026-09-15', '2026-10-02',
  '2026-11-03', '2026-11-25', '2026-12-25',
])

const nseMarketOpen = computed(() => {
  _clockTick.value
  const parts = Object.fromEntries(
    new Intl.DateTimeFormat('en-US', {
      timeZone: 'Asia/Kolkata',
      year: 'numeric', month: '2-digit', day: '2-digit',
      weekday: 'short', hour: '2-digit', minute: '2-digit', hour12: false,
    })
      .formatToParts(new Date())
      .map((p) => [p.type, p.value]),
  )
  if (parts.weekday === 'Sat' || parts.weekday === 'Sun') return false
  const dateStr = `${parts.year}-${parts.month}-${parts.day}`
  if (NSE_HOLIDAYS.has(dateStr)) return false
  const mins = parseInt(parts.hour) * 60 + parseInt(parts.minute)
  return mins >= 555 && mins <= 930
})

// ── Helpers ───────────────────────────────────────────────────────────────────
function shortVerdict(v) {
  if (!v) return 'SIG'
  if (v === 'STRONG BUY') return '⬆⬆ STRONG BUY'
  if (v === 'BUY') return '⬆ BUY'
  if (v === 'STRONG SELL') return '⬇⬇ STRONG SELL'
  if (v === 'SELL') return '⬇ SELL'
  return 'HOLD'
}

function transactionLabel(ev) {
  const itype = String(ev.instrument || ev.instrument_type || '').toUpperCase()
  const action = String(ev.action || '').toUpperCase()
  if (itype === 'CE') return '🟢 BUY CE'
  if (itype === 'PE') return '🟢 BUY PE'
  if (itype === 'FUT') return action.includes('SELL') ? '🔴 SELL FUT' : '🟢 BUY FUT'
  if (action.includes('SELL')) return '🔴 SELL'
  return '🟢 BUY'
}

function transactionTooltip(ev) {
  const itype = String(ev.instrument || ev.instrument_type || '').toUpperCase()
  const verdict = String(ev.verdict || '').toUpperCase()
  const isBear = verdict.includes('SELL')
  if (itype === 'PE') return `Bearish on underlying — BUY this Put option. You profit if underlying falls below ${ev.strike || 'strike'}. The "SELL" verdict refers to the underlying direction; you transact BUY on the option.`
  if (itype === 'CE') return `Bullish on underlying — BUY this Call option. You profit if underlying rises above ${ev.strike || 'strike'}.`
  if (itype === 'FUT') return isBear ? 'Bearish on underlying — SHORT the futures contract.' : 'Bullish on underlying — LONG the futures contract.'
  return ''
}

function verdictClass(v) {
  if (!v) return 'hold'
  if (v.includes('STRONG BUY')) return 'strongbuy'
  if (v.includes('BUY')) return 'buy'
  if (v.includes('STRONG SELL')) return 'strongsell'
  if (v.includes('SELL')) return 'sell'
  return 'hold'
}

// ── Tracked positions ──────────────────────────────────────────────────────────
function isTracked(symbol) {
  if (!symbol) return false
  return trackedPositions.value.some((p) => p.ticket?.trading_symbol === symbol)
}

const _trackInFlight = new Set()

async function trackEntered(ticket) {
  if (!ticket?.trading_symbol) {
    alert('Cannot watch: this signal has no contract symbol. The scanner ticket build may have failed for this ticker.')
    return
  }
  if (isTracked(ticket.trading_symbol)) {
    alert(`${ticket.trading_symbol} is already in your WATCHING list.`)
    return
  }
  if (_trackInFlight.has(ticket.trading_symbol)) {
    alert(`Already adding ${ticket.trading_symbol}… give it a moment.`)
    return
  }
  _trackInFlight.add(ticket.trading_symbol)
  try {
    const base = import.meta.env.VITE_API_BASE_URL || 'http://localhost:5001'
    const resp = await fetch(`${base}/api/trade/tracked`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ ticket, qty: ticket.qty || 1 }),
    })
    const r = await resp.json().catch(() => ({}))
    if (!resp.ok || !r.success) {
      alert(`Failed to add ${ticket.trading_symbol} to watching:\n${r.error || resp.statusText || 'unknown error'}`)
      return
    }
    const existing = trackedPositions.value.find((p) => p.id === r.data?.id)
    if (!existing) trackedPositions.value = [...trackedPositions.value, r.data]
  } catch (e) {
    alert(`Network error adding ${ticket.trading_symbol} to watching: ${e.message || e}`)
  } finally {
    _trackInFlight.delete(ticket.trading_symbol)
  }
}

// ── Audio ─────────────────────────────────────────────────────────────────────
let _audioCtx = null
function _beep(freq = 880, duration = 0.18, volume = 0.18, type = 'sine') {
  try {
    if (!_audioCtx) _audioCtx = new (window.AudioContext || window.webkitAudioContext)()
    if (_audioCtx.state === 'suspended') _audioCtx.resume()
    const osc = _audioCtx.createOscillator()
    const gain = _audioCtx.createGain()
    osc.type = type
    osc.frequency.value = freq
    gain.gain.setValueAtTime(0, _audioCtx.currentTime)
    gain.gain.linearRampToValueAtTime(volume, _audioCtx.currentTime + 0.01)
    gain.gain.exponentialRampToValueAtTime(0.0001, _audioCtx.currentTime + duration)
    osc.connect(gain)
    gain.connect(_audioCtx.destination)
    osc.start()
    osc.stop(_audioCtx.currentTime + duration)
  } catch {}
}

function _playStrongFnoAlert(instrument) {
  if (instrument === 'CE') {
    _beep(523, 0.18, 0.22); setTimeout(() => _beep(784, 0.18, 0.22), 180); setTimeout(() => _beep(1046, 0.35, 0.22), 360)
  } else if (instrument === 'PE') {
    _beep(523, 0.18, 0.22); setTimeout(() => _beep(392, 0.18, 0.22), 180); setTimeout(() => _beep(262, 0.35, 0.22), 360)
  }
}

const _prevFnoStrong = {}
function _fnoSignalKey(ev) {
  return (ev.ticker || ev.underlying || '') + '|' + String(ev.option_type || ev.instrument || '').toUpperCase()
}
function _isStrongFnoSignal(ev) {
  const itype = String(ev.option_type || ev.instrument || ev.instrument_type || '').toUpperCase()
  if (itype !== 'CE' && itype !== 'PE') return false
  const verdict = String(ev.verdict || ev.final_verdict || '').toUpperCase()
  return verdict === 'STRONG BUY' || verdict === 'STRONG SELL'
}

// ── Scanner SSE stream ────────────────────────────────────────────────────────
let _foScannerES = null

function _openFoScannerStream() {
  if (_foScannerES) return
  const base = import.meta.env.VITE_API_BASE_URL || 'http://localhost:5001'

  fetch(`${base}/api/trade/fo-scanner/status`)
    .then((r) => r.json())
    .then((j) => {
      const data = j?.data || {}
      foScannerState.value = data
      scannerRunning.value = !!data.running
      const sigs = data.signals || []
      for (let i = sigs.length - 1; i >= 0; i--) {
        const s = sigs[i]
        foFeed.value.unshift({ type: 'scan_signal', ...s })
        _prevFnoStrong[_fnoSignalKey(s)] = _isStrongFnoSignal(s)
      }
      const fresh = {}
      for (const s of sigs) {
        if (s.ticket && s.ticket.underlying) fresh[s.ticket.underlying] = s.ticket
      }
      if (Object.keys(fresh).length) liveTickets.value = { ...liveTickets.value, ...fresh }
      if (foFeed.value.length > 100) foFeed.value.splice(100)
    })
    .catch(() => {})

  _foScannerES = new EventSource(`${base}/api/trade/fo-scanner/stream`)
  if (typeof window !== 'undefined') window.__phoenix_foScannerES = _foScannerES

  _foScannerES.onmessage = (e) => {
    try {
      const ev = JSON.parse(e.data)
      if (ev.type === 'heartbeat') return
      if (ev.type === 'scan_analysing') { foAnalysing.value = ev; return }
      if (ev.type === 'scan_complete') {
        foAnalysing.value = null
        fetch(`${base}/api/trade/fo-scanner/status`)
          .then((r) => r.json())
          .then((j) => { if (j?.success) foScannerState.value = j.data })
          .catch(() => {})
      }
      if (ev.type === 'scan_signal' && ev.ticket && ev.ticket.underlying) {
        liveTickets.value = { ...liveTickets.value, [ev.ticket.underlying]: ev.ticket }
      }
      if (ev.type === 'scan_signal') {
        const key = _fnoSignalKey(ev)
        const isStrong = _isStrongFnoSignal(ev)
        if (isStrong && !_prevFnoStrong[key]) {
          _playStrongFnoAlert(String(ev.option_type || ev.instrument || '').toUpperCase())
        }
        _prevFnoStrong[key] = isStrong
      }
      if (ev.type === 'scan_signal' || ev.type === 'scan_skip') {
        const key = (ev.ticker || '') + '|' + (ev.option_type || ev.instrument || '')
        foFeed.value = foFeed.value.filter(
          (x) =>
            !(
              (x.type === 'scan_signal' || x.type === 'scan_skip') &&
              (x.ticker || '') + '|' + (x.option_type || x.instrument || '') === key
            ),
        )
      }
      foFeed.value.unshift(ev)
      if (foFeed.value.length > 100) foFeed.value.splice(100)
    } catch {}
  }
  _foScannerES.onerror = () => {
    scannerRunning.value = false
    foAnalysing.value = null
  }
}

async function startScanner() {
  try {
    const base = import.meta.env.VITE_API_BASE_URL || 'http://localhost:5001'
    await fetch(`${base}/api/trade/fo-scanner/start`, { method: 'POST' })
    scannerRunning.value = true
    _openFoScannerStream()
  } catch (e) { console.error('startScanner', e) }
}

async function stopScanner() {
  try {
    const base = import.meta.env.VITE_API_BASE_URL || 'http://localhost:5001'
    await fetch(`${base}/api/trade/fo-scanner/stop`, { method: 'POST' })
    scannerRunning.value = false
  } catch (e) { console.error('stopScanner', e) }
}

async function triggerScan() {
  try {
    const base = import.meta.env.VITE_API_BASE_URL || 'http://localhost:5001'
    await fetch(`${base}/api/trade/fo-scanner/trigger`, { method: 'POST' })
    scannerRunning.value = true
    _openFoScannerStream()
  } catch (e) { console.error('triggerScan', e) }
}

onMounted(() => {
  _clockTimer = setInterval(() => _clockTick.value++, 1000)
  _openFoScannerStream()
})

onUnmounted(() => {
  clearInterval(_clockTimer)
  if (_foScannerES) { _foScannerES.close(); _foScannerES = null }
  if (typeof window !== 'undefined') window.__phoenix_foScannerES = null
})
</script>

<style src="../../styles/FoScannerPanel.css"></style>
