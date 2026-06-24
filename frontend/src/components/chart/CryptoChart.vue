<template>
  <div class="cc-wrap" ref="wrapRef">
    <!-- Status indicators only (intervals controlled by parent) -->
    <div class="cc-toolbar" v-if="loading || error">
      <span v-if="loading" class="cc-spin">⟳</span>
      <span v-if="error" class="cc-err" :title="error">⚠</span>
    </div>

    <!-- LW host -->
    <div ref="chartRef" class="cc-host"></div>

    <!-- RSI sub-panel -->
    <div class="sub-label">RSI — Overbought &gt;70 (red zone) · Oversold &lt;30 (green zone)</div>
    <div ref="rsiRef" class="rsi-host"></div>

    <!-- MACD sub-panel -->
    <div class="sub-label">MACD — green bars = building momentum · red bars = losing momentum</div>
    <div ref="macdRef" class="macd-host"></div>

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
import { getCryptoOHLCV } from '../../api/market'
import { fmtTime } from '../../utils/formatters'

const props = defineProps({
  pair:      { type: String,  default: 'BTCUSDT' },
  interval:  { type: String,  default: '1h' },
  livePrice: { type: Number,  default: null },
})

const _COIN_NAMES = {
  BTC: 'Bitcoin', ETH: 'Ethereum', SOL: 'Solana', BNB: 'BNB',
  XRP: 'XRP', ADA: 'Cardano', DOGE: 'Dogecoin', DOT: 'Polkadot',
  LINK: 'Chainlink', AVAX: 'Avalanche', MATIC: 'Polygon',
  UNI: 'Uniswap', ATOM: 'Cosmos', LTC: 'Litecoin', SHIB: 'Shiba Inu',
  TON: 'Toncoin', TRX: 'TRON', OP: 'Optimism', ARB: 'Arbitrum',
}
const chartCoinName = computed(() => {
  const sym = props.pair.replace('/USDT', '').replace('/BUSD', '').replace('USDT', '').replace('BUSD', '').replace('/', '')
  return _COIN_NAMES[sym] || sym
})

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
const rsiRef        = ref(null)
const macdRef       = ref(null)

const tt = ref({ visible: false, x: 0, y: 0, date: '', open: '', high: '', low: '', close: '', vol: '', up: true })

let _chart  = null
let _candle = null
let _vol    = null
let _ema20  = null
let _ema50  = null
let _bbUpper = null
let _bbLower = null
let _ohlcv  = []          // raw candle array (sorted ascending)
let _resizeObs = null     // ResizeObserver for v-show visibility
let _lastLoadTs = 0       // track when data was last fetched

// RSI chart
let _rsiChart  = null
let _rsiSeries = null
let _rsi70     = null
let _rsi30     = null

// MACD chart
let _macdChart   = null
let _macdHist    = null
let _macdLine    = null
let _macdSignal  = null

// ── Helpers ───────────────────────────────────────────────────────────────────

function _ms2s(ms) { return Math.floor(ms / 1000) }

function _fmt(v) {
  if (v == null) return '—'
  return v >= 1
    ? v.toLocaleString(undefined, { minimumFractionDigits: 2, maximumFractionDigits: 2 })
    : v.toFixed(6)
}

function _fmtVol(v) {
  if (v == null) return '—'
  if (v >= 1e9) return (v / 1e9).toFixed(2) + 'B'
  if (v >= 1e6) return (v / 1e6).toFixed(2) + 'M'
  if (v >= 1e3) return (v / 1e3).toFixed(1) + 'K'
  return v.toFixed(0)
}

// Original _ema: returns [{time, value}] series from OHLCV objects
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

// New: EMA from plain number array, returns plain number array
function _ema_series_from_array(arr, period) {
  const k = 2 / (period + 1)
  const out = []
  let prev = null
  for (const v of arr) {
    if (prev === null) prev = v
    else prev = v * k + prev * (1 - k)
    out.push(prev)
  }
  return out
}

// Rolling stddev of closes over `period`, returns full-length array (0-padded at start)
function _stddev(data, period) {
  const out = []
  for (let i = 0; i < data.length; i++) {
    if (i < period - 1) { out.push(0); continue }
    const slice = data.slice(i - period + 1, i + 1).map(d => d.close)
    const mean = slice.reduce((a, b) => a + b, 0) / period
    const variance = slice.reduce((a, b) => a + (b - mean) ** 2, 0) / period
    out.push(Math.sqrt(variance))
  }
  return out
}

// RSI(14) from OHLCV array, returns [{time, value}]
function _computeRSI(data, period = 14) {
  if (data.length < period + 1) return []
  const out = []
  let avgGain = 0
  let avgLoss = 0

  // Seed with first `period` changes
  for (let i = 1; i <= period; i++) {
    const change = data[i].close - data[i - 1].close
    if (change >= 0) avgGain += change
    else avgLoss += Math.abs(change)
  }
  avgGain /= period
  avgLoss /= period

  const rs0 = avgLoss === 0 ? 100 : avgGain / avgLoss
  out.push({ time: data[period].time, value: 100 - 100 / (1 + rs0) })

  for (let i = period + 1; i < data.length; i++) {
    const change = data[i].close - data[i - 1].close
    const gain = change >= 0 ? change : 0
    const loss = change < 0 ? Math.abs(change) : 0
    avgGain = (avgGain * (period - 1) + gain) / period
    avgLoss = (avgLoss * (period - 1) + loss) / period
    const rs = avgLoss === 0 ? 100 : avgGain / avgLoss
    out.push({ time: data[i].time, value: 100 - 100 / (1 + rs) })
  }
  return out
}

// ── Chart setup ───────────────────────────────────────────────────────────────

function _subChartOptions(_el) {
  return {
    autoSize: true,
    layout: {
      background: { color: '#0d0d1a' },
      textColor:  '#8b8fa8',
      fontSize:   10,
    },
    grid: {
      vertLines: { color: '#1a1a2e' },
      horzLines: { color: '#1a1a2e' },
    },
    crosshair: { mode: CrosshairMode.Normal },
    rightPriceScale: { visible: true, borderColor: '#1a1a2e' },
    timeScale: { visible: false, borderColor: '#1a1a2e' },
    handleScroll: true,
    handleScale:  true,
  }
}

function _setupRSI() {
  if (!rsiRef.value) return
  if (_rsiChart) { _rsiChart.remove(); _rsiChart = null }

  _rsiChart = createChart(rsiRef.value, _subChartOptions(rsiRef.value))

  _rsiSeries = _rsiChart.addSeries(LineSeries, {
    color: '#a78bfa', lineWidth: 1,
    crosshairMarkerVisible: false,
  }, 0)

  _rsiSeries.applyOptions({
    autoscaleInfoProvider: () => ({
      priceRange: { minValue: 0, maxValue: 100 },
      margins: { above: 0.05, below: 0.05 },
    }),
  })

  // Reference lines at 70 and 30 — seeded with placeholder data, updated in _renderRSI
  _rsi70 = _rsiChart.addSeries(LineSeries, {
    color: 'rgba(255,71,87,0.5)', lineWidth: 1, lineStyle: 2,
    crosshairMarkerVisible: false,
    lastValueVisible: false, priceLineVisible: false,
  }, 0)
  _rsi70.applyOptions({
    autoscaleInfoProvider: () => ({
      priceRange: { minValue: 0, maxValue: 100 },
      margins: { above: 0.05, below: 0.05 },
    }),
  })

  _rsi30 = _rsiChart.addSeries(LineSeries, {
    color: 'rgba(0,212,168,0.5)', lineWidth: 1, lineStyle: 2,
    crosshairMarkerVisible: false,
    lastValueVisible: false, priceLineVisible: false,
  }, 0)
  _rsi30.applyOptions({
    autoscaleInfoProvider: () => ({
      priceRange: { minValue: 0, maxValue: 100 },
      margins: { above: 0.05, below: 0.05 },
    }),
  })

  // Sync time axis with main chart
  if (_chart) {
    _chart.timeScale().subscribeVisibleLogicalRangeChange(range => {
      if (range && _rsiChart) _rsiChart.timeScale().setVisibleLogicalRange(range)
    })
    _rsiChart.timeScale().subscribeVisibleLogicalRangeChange(range => {
      if (range && _chart) _chart.timeScale().setVisibleLogicalRange(range)
    })
  }
}

function _setupMACD() {
  if (!macdRef.value) return
  if (_macdChart) { _macdChart.remove(); _macdChart = null }

  _macdChart = createChart(macdRef.value, _subChartOptions(macdRef.value))

  _macdHist = _macdChart.addSeries(HistogramSeries, {
    priceScaleId: 'right',
    crosshairMarkerVisible: false,
  }, 0)

  _macdLine = _macdChart.addSeries(LineSeries, {
    color: '#3b82f6', lineWidth: 1,
    crosshairMarkerVisible: false,
    lastValueVisible: false, priceLineVisible: false,
  }, 0)

  _macdSignal = _macdChart.addSeries(LineSeries, {
    color: '#f97316', lineWidth: 1,
    crosshairMarkerVisible: false,
    lastValueVisible: false, priceLineVisible: false,
  }, 0)

  // Sync time axis with main chart
  if (_chart) {
    _chart.timeScale().subscribeVisibleLogicalRangeChange(range => {
      if (range && _macdChart) _macdChart.timeScale().setVisibleLogicalRange(range)
    })
    _macdChart.timeScale().subscribeVisibleLogicalRangeChange(range => {
      if (range && _chart) _chart.timeScale().setVisibleLogicalRange(range)
    })
  }
}

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

  // Bollinger Bands
  _bbUpper = _chart.addSeries(LineSeries, {
    color: 'rgba(245,158,11,0.4)', lineWidth: 1, lineStyle: 2, priceScaleId: 'right',
    crosshairMarkerVisible: false,
    lastValueVisible: false, priceLineVisible: false,
  }, 0)
  _bbLower = _chart.addSeries(LineSeries, {
    color: 'rgba(245,158,11,0.4)', lineWidth: 1, lineStyle: 2, priceScaleId: 'right',
    crosshairMarkerVisible: false,
    lastValueVisible: false, priceLineVisible: false,
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
      date: fmtTime(param.time * 1000, { mode: 'datetime', seconds: false }),
      open:  _fmt(c.open),
      high:  _fmt(c.high),
      low:   _fmt(c.low),
      close: _fmt(c.close),
      vol:   v ? _fmtVol(v.value) : '—',
      up:    c.close >= c.open,
    }
  })

  _setupRSI()
  _setupMACD()
}

// ── Render RSI ────────────────────────────────────────────────────────────────

function _renderRSI(timeSeries) {
  if (!_rsiChart || !_rsiSeries || !timeSeries.length) return
  const rsiData = _computeRSI(timeSeries)
  if (!rsiData.length) return

  _rsiSeries.setData(rsiData)

  const firstTime = rsiData[0].time
  const lastTime  = rsiData[rsiData.length - 1].time
  _rsi70.setData([{ time: firstTime, value: 70 }, { time: lastTime, value: 70 }])
  _rsi30.setData([{ time: firstTime, value: 30 }, { time: lastTime, value: 30 }])
}

// ── Render MACD ───────────────────────────────────────────────────────────────

function _renderMACD(timeSeries) {
  if (!_macdChart || !_macdHist || !timeSeries.length) return

  const closes = timeSeries.map(d => d.close)
  const ema12arr = _ema_series_from_array(closes, 12)
  const ema26arr = _ema_series_from_array(closes, 26)

  // MACD line values
  const macdVals = ema12arr.map((v, i) => v - ema26arr[i])
  const signalVals = _ema_series_from_array(macdVals, 9)

  const histData   = []
  const macdData   = []
  const signalData = []

  for (let i = 0; i < timeSeries.length; i++) {
    const t = timeSeries[i].time
    const macdVal = macdVals[i]
    const sigVal  = signalVals[i]
    const histVal = macdVal - sigVal

    histData.push({
      time:  t,
      value: histVal,
      color: histVal >= 0 ? '#00d4a855' : '#ff475755',
    })
    macdData.push({ time: t, value: macdVal })
    signalData.push({ time: t, value: sigVal })
  }

  _macdHist.setData(histData)
  _macdLine.setData(macdData)
  _macdSignal.setData(signalData)
}

// ── Render data ───────────────────────────────────────────────────────────────

function _render() {
  if (!_chart || !_ohlcv.length) return
  const candles = _ohlcv.map(d => ({
    time:  _ms2s(d.time),
    open:  d.open, high: d.high, low: d.low, close: d.close,
  }))
  const volumes = _ohlcv.map(d => ({
    time:  _ms2s(d.time),
    value: d.volume,
    color: d.close >= d.open ? '#00d4a855' : '#ff475755',
  }))
  _candle.setData(candles)
  _vol.setData(volumes)

  const timeSeries = _ohlcv.map(d => ({ time: _ms2s(d.time), close: d.close }))
  _ema20.setData(_ema(timeSeries, 20))
  _ema50.setData(_ema(timeSeries, 50))

  // Bollinger Bands (using EMA20 as middle)
  const stddevs = _stddev(timeSeries, 20)
  const ema20vals = _ema(timeSeries, 20)
  const bbUpperData = []
  const bbLowerData = []
  for (let i = 0; i < timeSeries.length; i++) {
    if (stddevs[i] === 0) continue  // skip padding
    bbUpperData.push({ time: timeSeries[i].time, value: ema20vals[i].value + 2 * stddevs[i] })
    bbLowerData.push({ time: timeSeries[i].time, value: ema20vals[i].value - 2 * stddevs[i] })
  }
  _bbUpper.setData(bbUpperData)
  _bbLower.setData(bbLowerData)

  // Fit chart then zoom to show last ~80 candles
  _chart.timeScale().fitContent()
  // Double-rAF ensures layout is complete before setting visible range
  requestAnimationFrame(() => {
    requestAnimationFrame(() => {
      if (!_chart) return
      const totalBars = candles.length
      const visible = Math.min(totalBars, 80)
      _chart.timeScale().setVisibleLogicalRange({
        from: totalBars - visible,
        to: totalBars + 5,
      })
    })
  })

  _renderRSI(timeSeries)
  _renderMACD(timeSeries)
}

// ── Live price → update last candle ──────────────────────────────────────────

watch(() => props.livePrice, (price) => {
  if (!_candle || !_ohlcv.length || price == null) return
  const last = _ohlcv[_ohlcv.length - 1]
  _candle.update({
    time:  _ms2s(last.time),
    open:  last.open,
    high:  Math.max(last.high, price),
    low:   Math.min(last.low, price),
    close: price,
  })
  _vol.update({
    time:  _ms2s(last.time),
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
    const res = await getCryptoOHLCV(props.pair, localInterval.value, limit)
    _ohlcv = (res.data?.data || res.data || [])
      .filter(c => c && c.time)
      .sort((a, b) => a.time - b.time)
    _lastLoadTs = Date.now()
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

onMounted(() => {
  _setup()
  load()
  // ResizeObserver: re-fit when container becomes visible (v-show toggle)
  if (wrapRef.value && typeof ResizeObserver !== 'undefined') {
    let prevW = wrapRef.value.offsetWidth
    _resizeObs = new ResizeObserver((entries) => {
      const w = entries[0]?.contentRect?.width || 0
      if (prevW === 0 && w > 0) {
        // Container went from hidden → visible
        if (_chart) _chart.applyOptions({ autoSize: true })
        if (_rsiChart) _rsiChart.applyOptions({ autoSize: true })
        if (_macdChart) _macdChart.applyOptions({ autoSize: true })
        // Reload data if stale (>30s since last fetch)
        if (Date.now() - _lastLoadTs > 30_000) load()
        else if (_ohlcv.length) {
          requestAnimationFrame(() => {
            requestAnimationFrame(() => {
              if (!_chart) return
              const totalBars = _ohlcv.length
              const visible = Math.min(totalBars, 80)
              _chart.timeScale().setVisibleLogicalRange({
                from: totalBars - visible,
                to: totalBars + 5,
              })
            })
          })
        }
      }
      prevW = w
    })
    _resizeObs.observe(wrapRef.value)
  }
})

onUnmounted(() => {
  if (_resizeObs)  { _resizeObs.disconnect(); _resizeObs  = null }
  if (_chart)      { _chart.remove();         _chart      = null }
  if (_rsiChart)   { _rsiChart.remove();      _rsiChart   = null }
  if (_macdChart)  { _macdChart.remove();     _macdChart  = null }
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

.rsi-host  { height: 80px; flex-shrink: 0; }
.macd-host { height: 60px; flex-shrink: 0; }

.sub-label {
  font-size: 9px; color: #4a4e6a; padding: 2px 8px;
  background: #0d0d1a; flex-shrink: 0;
}

.cc-legend {
  display: flex; gap: 10px; padding: 3px 8px;
  background: #0d0d1a; flex-shrink: 0; flex-wrap: wrap;
}
.leg-item  { font-size: 10px; color: #4a4e6a; }
.leg-sep   { color: #2a2a3e; }
.ema20 { color: #f59e0b; }
.ema50 { color: #3b82f6; }
.bb    { color: rgba(245,158,11,0.6); }
.vol   { color: #4a4e6a; }

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
