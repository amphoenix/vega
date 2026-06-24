<template>
  <div class="cc-wrap" ref="wrapRef">
    <!-- Toolbar -->
    <div class="cc-toolbar">
      <button
        v-for="iv in INTERVALS"
        :key="iv.v"
        :class="['cc-iv', { active: localInterval === iv.v }]"
        @click="changeInterval(iv.v)"
      >{{ iv.l }}</button>
      <span class="cc-sym">{{ chartPairName }}</span>
      <span class="cc-exch">FOREX</span>
      <span v-if="loading" class="cc-spin">⟳</span>
      <span v-if="error" class="cc-err" :title="error">⚠</span>
    </div>

    <!-- LW host -->
    <div ref="chartRef" class="cc-host"></div>

    <!-- Hover tooltip -->
    <div
      class="cc-tt"
      v-show="tt.visible"
      :style="{ left: tt.x + 'px', top: tt.y + 'px' }"
    >
      <div class="tt-date">{{ tt.date }}</div>
      <div class="tt-row"><span>O</span><span>{{ tt.open }}</span></div>
      <div class="tt-row"><span>H</span><span>{{ tt.high }}</span></div>
      <div class="tt-row"><span>L</span><span>{{ tt.low }}</span></div>
      <div class="tt-row">
        <span>C</span><span :class="tt.up ? 'up' : 'dn'">{{ tt.close }}</span>
      </div>
      <div class="tt-row"><span>VOL</span><span>{{ tt.vol }}</span></div>
    </div>
  </div>
</template>

<script setup>
import { ref, computed, watch, onMounted, onUnmounted } from 'vue'
import {
  createChart, CandlestickSeries, HistogramSeries,
  LineSeries, CrosshairMode,
} from 'lightweight-charts'
import { getForexOHLCV } from '../../api/market'

const props = defineProps({
  pair:      { type: String,  default: 'EURUSD=X' },
  interval:  { type: String,  default: '1h' },
  livePrice: { type: Number,  default: null },
})

function _displayPair(symbol) {
  return symbol.replace('=X', '').replace(/^(.{3})(.{3})$/, '$1 / $2')
}
const chartPairName = computed(() => _displayPair(props.pair))

const INTERVALS = [
  { v: '1m',  l: '1m'  },
  { v: '5m',  l: '5m'  },
  { v: '15m', l: '15m' },
  { v: '1h',  l: '1h'  },
  { v: '4h',  l: '4h'  },
  { v: '1d',  l: '1D'  },
]

const localInterval = ref(props.interval)
const loading       = ref(false)
const error         = ref(null)
const wrapRef       = ref(null)
const chartRef      = ref(null)

const tt = ref({ visible: false, x: 0, y: 0, date: '', open: '', high: '', low: '', close: '', vol: '', up: true })

let _chart  = null
let _candle = null
let _vol    = null
let _ema20  = null
let _ema50  = null
let _ro     = null
let _ohlcv  = []          // raw candle array (sorted ascending)

// ── Helpers ───────────────────────────────────────────────────────────────────

function _ms2s(ms) { return Math.floor(ms / 1000) }

function formatPrice(v) {
  if (v == null) return '—'
  return Number(v).toFixed(Number(v) >= 100 ? 2 : 4)
}

function _fmtVol(v) {
  if (v == null) return '—'
  if (v >= 1e9) return (v / 1e9).toFixed(2) + 'B'
  if (v >= 1e6) return (v / 1e6).toFixed(2) + 'M'
  if (v >= 1e3) return (v / 1e3).toFixed(1) + 'K'
  return v.toFixed(0)
}

function _ema(data, period) {
  const k = 2 / (period + 1)
  const out = []
  let prev = null
  for (const d of data) {
    if (prev === null) {
      prev = d.close
    } else {
      prev = d.close * k + prev * (1 - k)
    }
    out.push({ time: d.time, value: prev })
  }
  return out
}

// ── Chart setup ───────────────────────────────────────────────────────────────

function _setup() {
  if (!chartRef.value) return
  if (_chart) { _chart.remove(); _chart = null }

  const el = chartRef.value

  _chart = createChart(el, {
    autoSize: true,
    layout: {
      background: { color: '#0d0d1a' },
      textColor:  '#8b8fa8',
      fontSize:   11,
    },
    grid: {
      vertLines: { color: '#1a1a2e' },
      horzLines: { color: '#1a1a2e' },
    },
    crosshair: { mode: CrosshairMode.Normal },
    rightPriceScale: { visible: true, borderColor: '#1a1a2e' },
    timeScale: { timeVisible: true, secondsVisible: false, borderColor: '#1a1a2e', rightOffset: 5 },
    handleScroll: true,
    handleScale:  true,
  })

  _candle = _chart.addSeries(CandlestickSeries, {
    upColor:         '#00d4a8',
    downColor:       '#ff4757',
    borderUpColor:   '#00d4a8',
    borderDownColor: '#ff4757',
    wickUpColor:     '#00d4a8',
    wickDownColor:   '#ff4757',
  }, 0)

  _candle.priceScale().applyOptions({ scaleMargins: { top: 0.05, bottom: 0.22 } })

  _vol = _chart.addSeries(HistogramSeries, {
    priceFormat:  { type: 'volume' },
    priceScaleId: 'vol',
  }, 0)
  _vol.priceScale().applyOptions({ scaleMargins: { top: 0.82, bottom: 0 }, visible: false })

  _ema20 = _chart.addSeries(LineSeries, {
    color: '#f59e0b', lineWidth: 1, priceScaleId: 'right',
    crosshairMarkerVisible: false,
  }, 0)
  _ema50 = _chart.addSeries(LineSeries, {
    color: '#3b82f6', lineWidth: 1, priceScaleId: 'right',
    crosshairMarkerVisible: false,
  }, 0)

  _chart.subscribeCrosshairMove(param => {
    if (!param.point || !param.time) { tt.value.visible = false; return }
    const c = param.seriesData.get(_candle)
    const v = param.seriesData.get(_vol)
    if (!c) { tt.value.visible = false; return }
    tt.value = {
      visible: true,
      x: param.point.x + 16,
      y: Math.max(4, param.point.y - 80),
      date: new Date(param.time * 1000).toISOString().replace('T', ' ').slice(0, 16),
      open:  formatPrice(c.open),
      high:  formatPrice(c.high),
      low:   formatPrice(c.low),
      close: formatPrice(c.close),
      vol:   v ? _fmtVol(v.value) : '—',
      up:    c.close >= c.open,
    }
  })
}

// ── Render data ───────────────────────────────────────────────────────────────

function _render() {
  if (!_chart || !_ohlcv.length) return
  const candles = _ohlcv.map(d => ({
    time:  _ms2s(d.time_ms),
    open:  d.open, high: d.high, low: d.low, close: d.close,
  }))
  const volumes = _ohlcv.map(d => ({
    time:  _ms2s(d.time_ms),
    value: d.volume,
    color: d.close >= d.open ? '#00d4a855' : '#ff475755',
  }))
  _candle.setData(candles)
  _vol.setData(volumes)
  _ema20.setData(_ema(_ohlcv.map(d => ({ time: _ms2s(d.time_ms), close: d.close })), 20))
  _ema50.setData(_ema(_ohlcv.map(d => ({ time: _ms2s(d.time_ms), close: d.close })), 50))
  _fitRange()
}

// ── Fit visible range (safe even when container is zero-sized) ────────────────

let _lastWidth = 0

function _fitRange() {
  if (!_chart || !_ohlcv.length) return
  const w = chartRef.value?.clientWidth || 0
  if (w === 0) { _lastWidth = 0; return }   // hidden — skip, ResizeObserver will retry
  _lastWidth = w
  const totalBars = _ohlcv.length
  requestAnimationFrame(() => {
    if (!_chart) return
    if (totalBars > 80) {
      _chart.timeScale().setVisibleLogicalRange({ from: totalBars - 80, to: totalBars + 5 })
    } else {
      _chart.timeScale().fitContent()
    }
  })
}

// ── Live price → update last candle ──────────────────────────────────────────

watch(() => props.livePrice, (price) => {
  if (!_candle || !_ohlcv.length || price == null) return
  const last = _ohlcv[_ohlcv.length - 1]
  _candle.update({
    time:  _ms2s(last.time_ms),
    open:  last.open,
    high:  Math.max(last.high, price),
    low:   Math.min(last.low, price),
    close: price,
  })
  _vol.update({
    time:  _ms2s(last.time_ms),
    value: last.volume,
    color: price >= last.open ? '#00d4a855' : '#ff475755',
  })
})

// ── Load OHLCV ────────────────────────────────────────────────────────────────

async function load() {
  if (!props.pair) return
  loading.value = true
  error.value   = null
  try {
    const limit = localInterval.value.endsWith('m') ? 300 : 200
    const res = await getForexOHLCV(props.pair, localInterval.value, limit)
    _ohlcv = (res.data?.data || res.data || [])
      .filter(c => c && c.time_ms)
      .sort((a, b) => a.time_ms - b.time_ms)
    _render()
  } catch (e) {
    error.value = e?.message || 'Load failed'
  } finally {
    loading.value = false
  }
}

function changeInterval(iv) {
  localInterval.value = iv
  load()
}

// ── Watchers ──────────────────────────────────────────────────────────────────

watch(() => props.pair, () => load())
watch(() => props.interval, (iv) => { localInterval.value = iv; load() })

// ── Lifecycle ─────────────────────────────────────────────────────────────────

function _init() {
  if (_chart) return              // already initialised
  const w = chartRef.value?.clientWidth || 0
  if (w === 0) return             // still hidden (v-show) — wait for ResizeObserver
  _setup()
  load()
}

onMounted(() => {
  _init()                         // works immediately if container is visible
  // Observe resize — handles v-show hidden → visible transition
  _ro = new ResizeObserver(() => {
    const w = chartRef.value?.clientWidth || 0
    if (w > 0 && !_chart) {
      _init()                     // first time becoming visible — create chart + fetch
    } else if (w > 0 && _lastWidth === 0 && _ohlcv.length) {
      _lastWidth = w
      requestAnimationFrame(() => _fitRange())   // re-fit after re-show
    }
    _lastWidth = w
  })
  if (chartRef.value) _ro.observe(chartRef.value)
})

onUnmounted(() => {
  if (_ro) { _ro.disconnect(); _ro = null }
  if (_chart) { _chart.remove(); _chart = null }
})
</script>

<style scoped>
.cc-wrap {
  position: relative;
  display: flex;
  flex-direction: column;
  height: 100%;
  background: #0d0d1a;
  border-radius: 4px;
  overflow: hidden;
}

.cc-toolbar {
  display: flex;
  align-items: center;
  gap: 4px;
  padding: 4px 8px;
  background: #111125;
  border-bottom: 1px solid #1a1a2e;
  flex-shrink: 0;
}

.cc-iv {
  padding: 2px 7px;
  background: transparent;
  border: 1px solid #2a2a3e;
  border-radius: 3px;
  color: #6b6f8a;
  font-size: 11px;
  cursor: pointer;
  transition: all 0.15s;
}
.cc-iv:hover { border-color: #00d4a8; color: #00d4a8; }
.cc-iv.active { background: #00d4a822; border-color: #00d4a8; color: #00d4a8; }

.cc-sym  { margin-left: 8px; font-size: 12px; font-weight: 600; color: #e0e0f0; }
.cc-exch { font-size: 10px; color: #4a4e6a; margin-left: 4px; }
.cc-spin { font-size: 13px; color: #f59e0b; margin-left: auto; animation: spin 1s linear infinite; }
.cc-err  { font-size: 13px; color: #ff4757; margin-left: auto; cursor: help; }

@keyframes spin { to { transform: rotate(360deg); } }

.cc-host {
  flex: 1;
  min-height: 0;
}

.cc-tt {
  position: absolute;
  pointer-events: none;
  background: rgba(13,13,26,0.92);
  border: 1px solid #2a2a3e;
  border-radius: 4px;
  padding: 6px 10px;
  font-size: 11px;
  color: #c0c4d8;
  z-index: 10;
  white-space: nowrap;
}
.tt-date { font-weight: 600; color: #8b8fa8; margin-bottom: 4px; }
.tt-row  { display: flex; justify-content: space-between; gap: 10px; }
.up { color: #00d4a8; }
.dn { color: #ff4757; }
</style>
