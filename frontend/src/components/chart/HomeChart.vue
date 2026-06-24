<template>
  <div class="chart-wrap" ref="chartWrap">
    <!-- LW Charts mounts here — always in DOM so it gets real dimensions -->
    <div ref="chartContainer" class="lw-host"></div>

    <!-- Overlays (sit on top of the canvas) -->
    <div class="chart-overlay chart-empty" v-if="!ohlcv.length && !chartLoading">
      Enter a ticker above
    </div>
    <div class="chart-overlay chart-loading" v-if="chartLoading">
      <span class="spinner"></span>
    </div>

    <!-- EMA / VWAP legend -->
    <div class="chart-legend" v-if="ohlcv.length && !chartLoading">
      <span
        v-for="ln in activeEmaLegend"
        :key="ln.label"
        class="legend-item"
        :style="{ color: ln.color }"
        :title="ln.desc"
      >━ {{ ln.label }}</span>
    </div>

    <!-- Zoom controls -->
    <div v-if="ohlcv.length" class="chart-zoom-controls">
      <button @click="chartZoom(-1)" title="Zoom in (scroll up)">+</button>
      <button @click="chartZoom(+1)" title="Zoom out (scroll down)">−</button>
      <button @click="chartZoomReset" title="Reset to full range">⟲</button>
      <span
        v-if="chartVisibleCount"
        class="chart-zoom-info"
        :title="`Showing ~${chartVisibleCount} of ${ohlcv.length} candles · Drag to pan`"
      >{{ chartVisibleCount }}/{{ ohlcv.length }} candles</span>
    </div>

    <!-- Hover tooltip -->
    <div
      class="c-tooltip"
      v-show="tooltip.visible"
      :style="{ left: tooltip.x + 'px', top: tooltip.y + 'px' }"
    >
      <div class="tt-date">{{ tooltip.date }}</div>
      <div class="tt-row"><span>O</span><span>{{ tooltip.open }}</span></div>
      <div class="tt-row"><span>H</span><span>{{ tooltip.high }}</span></div>
      <div class="tt-row"><span>L</span><span>{{ tooltip.low }}</span></div>
      <div class="tt-row">
        <span>C</span>
        <span :class="tooltip.up ? 'up' : 'dn'">{{ tooltip.close }}</span>
      </div>
      <div class="tt-row"><span>VOL</span><span>{{ tooltip.volume }}</span></div>
      <div
        v-if="tooltip.pattern"
        class="tt-pattern"
        :style="{
          color:
            tooltip.pattern.signal === 'bull'
              ? '#26a69a'
              : tooltip.pattern.signal === 'bear'
                ? '#ef5350'
                : '#fbbf24',
        }"
      >
        <span class="tt-pat-name">
          {{ tooltip.pattern.signal === 'bull' ? '▲' : tooltip.pattern.signal === 'bear' ? '▼' : '◆' }}
          {{ tooltip.pattern.name }}
        </span>
        <span class="tt-pat-desc">{{ tooltip.pattern.desc }}</span>
      </div>
    </div>
  </div>
</template>

<script setup>
import { ref, computed, watch, onMounted, onUnmounted, nextTick } from 'vue'
import {
  createChart,
  CandlestickSeries,
  LineSeries,
  HistogramSeries,
  LineStyle,
  CrosshairMode,
  createSeriesMarkers,
} from 'lightweight-charts'
import { getOHLCV } from '../../api/market'
import { fmtPrice } from '../../utils/formatters'

const props = defineProps({
  chartTicker: { type: String, default: '' },
  interval: { type: String, default: '1d' },
  brokerLivePrice: { type: Number, default: null },
  levels: { type: Object, default: null },
  lightMode: { type: Boolean, default: false },
  currencySymbol: { type: String, default: '₹' },
  marketOpen: { type: Boolean, default: false },
})

const emit = defineEmits(['update:chartTicker', 'update:activeTicker'])

// ── State ─────────────────────────────────────────────────────────────────────
const ohlcv = ref([])
const chartLoading = ref(false)
const chartVisibleCount = ref(0)

const chartWrap = ref(null)
const chartContainer = ref(null)

const tooltip = ref({
  visible: false,
  x: 0, y: 0,
  date: '', open: '', high: '', low: '', close: '', volume: '',
  up: true, pattern: null,
})

// Chart instances — plain variables, not reactive
let _chart = null
let _candleSeries = null
let _labelSeries = null   // invisible right-scale series that hosts all price-line label boxes
let _volumeSeries = null
let _emaSeries = []      // [{ def, series }]
let _priceLines = []     // [{ series, line }]
let _markersPlugin = null
let _patternByTime = new Map()

let _candleRefreshTimer = null
let ro = null

// ── EMA / VWAP definitions ────────────────────────────────────────────────────
const GREEN = '#00d4a8'
const RED = '#ff4757'

const EMA_DEFS = [
  {
    label: 'EMA20', period: 20, color: '#f59e0b', minCandles: 0,
    desc: 'EMA 20 — Short-term trend. Price above = mild bullish. Crossover with EMA50 is a key swing signal.',
  },
  {
    label: 'EMA50', period: 50, color: '#3b82f6', minCandles: 0,
    desc: 'EMA 50 — Medium-term trend. Golden Cross (EMA20 > EMA50) = bullish; Death Cross = bearish.',
  },
  {
    label: 'EMA200', period: 200, color: '#a855f7', minCandles: 200,
    desc: 'EMA 200 — Long-term trend. Price above = bull market; below = bear market. Strongest dynamic S/R.',
  },
  {
    label: 'VWAP', period: null, color: '#f97316', minCandles: 0,
    desc: 'VWAP — Volume Weighted Average Price. Institutional benchmark. Price above = bullish bias; below = bearish.',
  },
]

const activeEmaLegend = computed(() =>
  EMA_DEFS.filter(e => !e.minCandles || ohlcv.value.length >= e.minCandles),
)

// ── Time helpers ──────────────────────────────────────────────────────────────
// Always use Unix seconds so param.time in crosshair callbacks is consistently a number
function toTime(dateStr) {
  if (!dateStr) return 0
  if (dateStr.length > 10) {
    // Backend returns 'YYYY-MM-DD HH:MM' in IST. lightweight-charts renders
    // the time-axis in UTC. Append 'Z' so the IST string is treated AS IF it
    // were UTC — the rendered axis label then matches IST regardless of
    // browser TZ. Side effect: timestamps passed to lightweight-charts are
    // 5:30h ahead of real time; only matters if external code reverses toTime.
    return Math.floor(new Date(dateStr.replace(' ', 'T') + 'Z').getTime() / 1000)
  }
  // daily 'YYYY-MM-DD' — midnight UTC
  return Math.floor(new Date(dateStr + 'T00:00:00.000Z').getTime() / 1000)
}

// ── EMA computation ───────────────────────────────────────────────────────────
function _computeEMA(arr, n) {
  if (arr.length < n) return arr.map(() => null)
  const k = 2 / (n + 1)
  const out = new Array(arr.length).fill(null)
  let ema = arr.slice(0, n).reduce((a, b) => a + b, 0) / n
  out[n - 1] = ema
  for (let i = n; i < arr.length; i++) {
    ema = arr[i] * k + ema * (1 - k)
    out[i] = ema
  }
  return out
}

// ── Candlestick pattern detector ──────────────────────────────────────────────
function detectCandlePatterns(candles) {
  const pats = []
  for (let i = 1; i < candles.length; i++) {
    const c = candles[i], p = candles[i - 1]
    const pp = i >= 2 ? candles[i - 2] : null
    const body = Math.abs(c.close - c.open)
    const range = c.high - c.low
    if (range === 0) continue
    const upper = c.high - Math.max(c.open, c.close)
    const lower = Math.min(c.open, c.close) - c.low
    const bull = c.close > c.open
    const pBull = p.close > p.open
    const pBody = Math.abs(p.close - p.open)
    const pRange = p.high - p.low

    if (body / range < 0.1) {
      pats.push({ i, signal: 'neutral', name: 'Doji', desc: 'Indecision: open ≈ close. At a trend extreme often signals reversal — wait for confirmation.' })
      continue
    }
    if (lower > 2 * body && upper < body && i >= 3) {
      if (candles[i - 3].close > c.close) {
        pats.push({ i, signal: 'bull', name: 'Hammer', desc: 'Bullish reversal. Sellers drove price far down but buyers clawed back. Buy when next candle closes green above this high.' })
        continue
      }
    }
    if (upper > 2 * body && lower < body && i >= 3) {
      if (candles[i - 3].close > c.close && !bull) {
        pats.push({ i, signal: 'bull', name: 'Inverted Hammer', desc: 'Tentative bullish reversal. Buyers attempted a push — confirms weakening selling. Confirm with a strong green candle.' })
        continue
      }
      if (candles[i - 3].close < c.close) {
        pats.push({ i, signal: 'bear', name: 'Shooting Star', desc: 'Bearish reversal at resistance. Buyers spiked price but sellers rejected hard. Tighten stop or exit longs.' })
        continue
      }
    }
    if (lower > 2 * body && upper < body && i >= 3 && candles[i - 3].close < c.close) {
      pats.push({ i, signal: 'bear', name: 'Hanging Man', desc: 'Bearish warning after uptrend. Sellers entered aggressively. Confirm with a red candle below this low.' })
      continue
    }
    if (!pBull && bull && body > pBody && c.open <= p.close && c.close >= p.open) {
      pats.push({ i, signal: 'bull', name: 'Bullish Engulfing', desc: 'Strong reversal. Green candle swallows prior red — buyers overwhelmed sellers. High-probability entry on next open.' })
      continue
    }
    if (pBull && !bull && body > pBody && c.open >= p.close && c.close <= p.open) {
      pats.push({ i, signal: 'bear', name: 'Bearish Engulfing', desc: 'Strong reversal. Red candle swallows prior green — sellers took full control. Consider exiting longs.' })
      continue
    }
    if (body / range > 0.9) {
      pats.push({ i, signal: bull ? 'bull' : 'bear', name: bull ? 'Bullish Marubozu' : 'Bearish Marubozu', desc: bull ? 'Buyers in control every minute — no hesitation. Strong continuation signal.' : 'Sellers dominated entire session. Strong continuation or breakdown signal.' })
      continue
    }
    if (pp && !(pp.close > pp.open) && pRange < Math.abs(pp.close - pp.open) * 0.5 && bull && body > Math.abs(pp.close - pp.open) * 0.5) {
      pats.push({ i, signal: 'bull', name: 'Morning Star', desc: '3-candle bullish reversal: big red → indecision star → big green closing into red. One of the most reliable reversal signals.' })
      continue
    }
    if (pp && pp.close > pp.open && pRange < Math.abs(pp.close - pp.open) * 0.5 && !bull && body > Math.abs(pp.close - pp.open) * 0.5) {
      pats.push({ i, signal: 'bear', name: 'Evening Star', desc: '3-candle bearish reversal: big green → indecision star → big red closing into green. Bulls are exhausted.' })
      continue
    }
    if (!pBull && bull && c.open < p.low && c.close > (p.open + p.close) / 2 && c.close < p.open) {
      pats.push({ i, signal: 'bull', name: 'Piercing Line', desc: 'Bullish reversal: green opens below prior red\'s low but closes above its midpoint. Watch for follow-through.' })
      continue
    }
    if (pBull && !bull && c.open > p.high && c.close < (p.open + p.close) / 2 && c.close > p.open) {
      pats.push({ i, signal: 'bear', name: 'Dark Cloud Cover', desc: 'Bearish reversal: red opens above prior green\'s high but closes below its midpoint. Momentum shifting down.' })
      continue
    }
    if (!pBull && bull && Math.abs(c.low - p.low) / (range || 1) < 0.03) {
      pats.push({ i, signal: 'bull', name: 'Tweezer Bottom', desc: 'Same low hit twice — sellers failed both times to break support. Clear double rejection. Bullish reversal.' })
    }
    if (pBull && !bull && Math.abs(c.high - p.high) / (range || 1) < 0.03) {
      pats.push({ i, signal: 'bear', name: 'Tweezer Top', desc: 'Same high hit twice — buyers failed both times at resistance. Clear double rejection. Bearish reversal.' })
    }
    if (!pBull && bull && c.open > p.close && c.close < p.open && body < pBody * 0.6) {
      pats.push({ i, signal: 'bull', name: 'Bullish Harami', desc: 'Small green inside large red. Selling momentum slowing. Wait for green confirmation candle.' })
      continue
    }
    if (pBull && !bull && c.open < p.close && c.close > p.open && body < pBody * 0.6) {
      pats.push({ i, signal: 'bear', name: 'Bearish Harami', desc: 'Small red inside large green. Buyers losing steam. Confirm with red candle below small candle\'s low.' })
      continue
    }
    if (!pBull && body / (range || 1) < 0.1 && c.open > p.close && c.close < p.open) {
      pats.push({ i, signal: 'bull', name: 'Bullish Harami Cross', desc: 'Doji inside large red. Perfect indecision after strong selling — high-probability reversal candidate.' })
      continue
    }
    if (pBull && body / (range || 1) < 0.1 && c.open < p.close && c.close > p.open) {
      pats.push({ i, signal: 'bear', name: 'Bearish Harami Cross', desc: 'Doji inside large green. Bulls exhausted their push. Stronger reversal signal than regular Bearish Harami.' })
      continue
    }
  }

  for (let i = 4; i < candles.length; i++) {
    const c = candles[i], p = candles[i - 1], pp = candles[i - 2]
    const c3 = candles[i - 3], c4 = candles[i - 4]
    const bull = c.close > c.open
    const pBull = p.close > p.open, ppBull = pp.close > pp.open, c3Bull = c3.close > c3.open
    const pBody = Math.abs(p.close - p.open)
    const ppBody = Math.abs(pp.close - pp.open), c3Body = Math.abs(c3.close - c3.open)

    if (pBull && ppBull && c3Bull && p.close > pp.close && pp.close > c3.close && pBody > ppBody * 0.5 && ppBody > c3Body * 0.5) {
      if (bull && c.close > p.close)
        pats.push({ i: i - 2, signal: 'bull', name: 'Three White Soldiers', desc: 'Three consecutive strong green candles each closing at new high. Highly reliable bullish signal.' })
      continue
    }
    if (!pBull && !ppBull && !c3Bull && p.close < pp.close && pp.close < c3.close && pBody > ppBody * 0.5 && ppBody > c3Body * 0.5) {
      if (!bull && c.close < p.close)
        pats.push({ i: i - 2, signal: 'bear', name: 'Three Black Crows', desc: 'Three consecutive strong red candles each closing at new low. Sustained selling — often signals start of downtrend.' })
      continue
    }
    if (c3Bull && c3Body > ppBody * 1.5 && !ppBull && !pBull && pp.high < c3.close && pp.low > c3.open && p.high < c3.close && p.low > c3.open && bull && c.close > c3.close) {
      pats.push({ i, signal: 'bull', name: 'Rising Three Methods', desc: 'Strong green → 3 contained red candles → new green high. Bulls paused then resumed. Classic bullish continuation.' })
      continue
    }
    if (!c3Bull && c3Body > ppBody * 1.5 && ppBull && pBull && pp.high < c3.open && pp.low > c3.close && p.high < c3.open && p.low > c3.close && !bull && c.close < c3.close) {
      pats.push({ i, signal: 'bear', name: 'Falling Three Methods', desc: 'Strong red → 3 contained green candles → new red low. Bears paused then resumed. Classic bearish continuation.' })
      continue
    }
  }

  return pats
}

// ── Chart setup ───────────────────────────────────────────────────────────────
function _chartTheme() {
  return props.lightMode
    ? {
        layout: { background: { color: '#f8f9fa' }, textColor: '#333' },
        grid: { vertLines: { color: '#e0e0e0' }, horzLines: { color: '#e0e0e0' } },
      }
    : {
        layout: { background: { color: '#0d1117' }, textColor: '#8b949e' },
        grid: { vertLines: { color: '#1e1e1e' }, horzLines: { color: '#1e1e1e' } },
      }
}

function _setupChart() {
  if (!chartContainer.value) return
  const w = chartWrap.value?.clientWidth || 600
  const h = chartWrap.value?.clientHeight || 400

  _chart = createChart(chartContainer.value, {
    width: w,
    height: h,
    ..._chartTheme(),
    crosshair: { mode: CrosshairMode.Normal },
    leftPriceScale:  { visible: false },
    rightPriceScale: { visible: true, borderColor: '#30363d' },
    timeScale: {
      borderColor: '#30363d',
      timeVisible: true,
      secondsVisible: false,
      rightOffset: 8,
    },
    handleScroll: true,
    handleScale: true,
  })

  _candleSeries = _chart.addSeries(CandlestickSeries, {
    upColor: GREEN,
    downColor: RED,
    borderUpColor: GREEN,
    borderDownColor: RED,
    wickUpColor: GREEN,
    wickDownColor: RED,
    priceLineVisible: false,
    priceScaleId: 'right',
  })


  _volumeSeries = _chart.addSeries(HistogramSeries, {
    priceFormat: { type: 'volume' },
    priceScaleId: 'volume',
    lastValueVisible: false,
    priceLineVisible: false,
  })
  _chart.priceScale('volume').applyOptions({
    scaleMargins: { top: 0.85, bottom: 0 },
    visible: false,
  })

  // EMA + VWAP line series
  _emaSeries = EMA_DEFS.map(def => ({
    def,
    series: _chart.addSeries(LineSeries, {
      color: def.color,
      lineWidth: 1,
      priceLineVisible: false,
      lastValueVisible: false,
      crosshairMarkerVisible: false,
      priceScaleId: 'right',
    }),
  }))

  // Invisible right-scale series — hosts all price-line label boxes on the right axis
  _labelSeries = _chart.addSeries(LineSeries, {
    priceScaleId: 'right',
    lastValueVisible: false,
    priceLineVisible: false,
    crosshairMarkerVisible: false,
    lineWidth: 0,
    color: 'rgba(0,0,0,0)',
  })

  // Markers plugin — update with setMarkers() after data loads
  _markersPlugin = createSeriesMarkers(_candleSeries, [])

  // Crosshair for tooltip
  _chart.subscribeCrosshairMove(_onCrosshairMove)

  // Range change for zoom info
  _chart.timeScale().subscribeVisibleLogicalRangeChange((range) => {
    chartVisibleCount.value = range ? Math.round(range.to - range.from) : 0
  })
}

function _onCrosshairMove(param) {
  if (!param.time || !param.point || !chartWrap.value) {
    tooltip.value.visible = false
    return
  }
  const bar = param.seriesData.get(_candleSeries)
  const volBar = param.seriesData.get(_volumeSeries)
  if (!bar) { tooltip.value.visible = false; return }

  const idx = _patternByTime.get('idx:' + param.time)
  const pat = idx !== undefined ? _patternByTime.get('pat:' + idx) : null

  const rect = chartWrap.value.getBoundingClientRect()
  tooltip.value = {
    visible: true,
    x: Math.min(param.point.x + 14, rect.width - 190),
    y: Math.max(8, param.point.y - 70),
    date: bar.time,
    up: bar.close >= bar.open,
    open: `${props.currencySymbol}${bar.open.toFixed(2)}`,
    high: `${props.currencySymbol}${bar.high.toFixed(2)}`,
    low: `${props.currencySymbol}${bar.low.toFixed(2)}`,
    close: `${props.currencySymbol}${bar.close.toFixed(2)}`,
    volume: volBar ? `${(volBar.value / 1e6).toFixed(2)}M` : '—',
    pattern: pat || null,
  }
}

// ── Data rendering ────────────────────────────────────────────────────────────
function _updateData() {
  if (!_chart || !_candleSeries) return
  const candles = ohlcv.value
  if (!candles.length) return

  const lwCandles = candles.map(d => ({
    time: toTime(d.date),
    open: d.open, high: d.high, low: d.low, close: d.close,
  }))
  _candleSeries.setData(lwCandles)
  if (_labelSeries) _labelSeries.setData(lwCandles.map(c => ({ time: c.time, value: c.close })))

  _volumeSeries.setData(candles.map(d => ({
    time: toTime(d.date),
    value: Number(d.volume) || 0,
    color: d.close >= d.open ? `${GREEN}44` : `${RED}44`,
  })))

  // EMA / VWAP
  const closes = candles.map(d => d.close)
  let cumTPV = 0, cumV = 0
  const vwapVals = candles.map(d => {
    const tp = (d.high + d.low + d.close) / 3
    const v = Number(d.volume) || 0
    cumTPV += tp * v; cumV += v
    return cumV > 0 ? cumTPV / cumV : null
  })

  _emaSeries.forEach(({ def, series }) => {
    if (def.minCandles && candles.length < def.minCandles) { series.setData([]); return }
    const vals = def.label === 'VWAP' ? vwapVals : _computeEMA(closes, def.period)
    series.setData(
      candles
        .map((d, i) => vals[i] !== null ? { time: toTime(d.date), value: vals[i] } : null)
        .filter(Boolean),
    )
  })

  _updateLevels()
  _updateMarkers(candles)
}

// ── Price lines (S/R levels + live price + prev day H/L) ─────────────────────
function _clearPriceLines() {
  _priceLines.forEach(({ series, line }) => { try { series.removePriceLine(line) } catch {} })
  _priceLines = []
}

function _addPriceLine(series, opts) {
  const line = series.createPriceLine(opts)
  _priceLines.push({ series, line })
}

function _updateLevels() {
  if (!_candleSeries || !_labelSeries) return
  _clearPriceLines()

  const candles = ohlcv.value
  const livePrice = props.brokerLivePrice || candles[candles.length - 1]?.close
  if (livePrice) {
    const isUp = livePrice >= (candles[candles.length - 1]?.open || livePrice)
    _addPriceLine(_labelSeries, {
      price: livePrice,
      color: isUp ? '#26a69a' : '#ef5350',
      lineWidth: 1,
      lineStyle: LineStyle.Dashed,
      axisLabelVisible: false,
      title: fmtPrice(livePrice),
    })
  }

  if (props.levels) {
    const lv = props.levels
    const lines = [
      ...(lv.supports || []).slice(0, 3).map((p, i) => ({ p, label: `S${i + 1}`, color: '#22c55e', style: LineStyle.Dashed })),
      ...(lv.resistances || []).slice(0, 3).map((p, i) => ({ p, label: `R${i + 1}`, color: '#ef4444', style: LineStyle.Dashed })),
      lv.cpr_tc    ? { p: lv.cpr_tc,    label: 'CPR-TC', color: '#f97316', style: LineStyle.Solid  } : null,
      lv.cpr_pp    ? { p: lv.cpr_pp,    label: 'CPR-PP', color: '#f97316', style: LineStyle.Dashed } : null,
      lv.cpr_bc    ? { p: lv.cpr_bc,    label: 'CPR-BC', color: '#f97316', style: LineStyle.Solid  } : null,
      lv.entry     ? { p: lv.entry,     label: 'ENTRY', color: '#16a34a', style: LineStyle.Dotted } : null,
      lv.stop_loss ? { p: lv.stop_loss, label: 'SL',    color: '#dc2626', style: LineStyle.Dotted } : null,
      lv.target_1  ? { p: lv.target_1,  label: 'T1',    color: '#06b6d4', style: LineStyle.Dashed } : null,
      lv.target_2  ? { p: lv.target_2,  label: 'T2',    color: '#0ea5e9', style: LineStyle.Dashed } : null,
      lv.target_3  ? { p: lv.target_3,  label: 'T3',    color: '#6366f1', style: LineStyle.Dashed } : null,
    ].filter(Boolean)
    lines.forEach(({ p, label, color, style }) => {
      _addPriceLine(_labelSeries, {
        price: p, color, lineWidth: 1, lineStyle: style,
        axisLabelVisible: false,
        title: `${label} ${fmtPrice(p)}`,
      })
    })
  }

  // Previous Day High / Low
  if (candles.length >= 2) {
    const todayKey = candles[candles.length - 1].date.slice(0, 10)
    let prevKey = null
    for (let i = candles.length - 2; i >= 0; i--) {
      const k = candles[i].date.slice(0, 10)
      if (k !== todayKey) { prevKey = k; break }
    }
    if (prevKey) {
      const isIntraday = candles[0].date.length > 10
      const prev = isIntraday
        ? candles.filter(d => d.date.slice(0, 10) === prevKey)
        : candles.filter(d => d.date === prevKey)
      if (prev.length) {
        const pdHigh = Math.max(...prev.map(d => d.high))
        const pdLow  = Math.min(...prev.map(d => d.low))
        ;[
          { price: pdHigh, label: 'PDH', color: '#26a69a' },
          { price: pdLow,  label: 'PDL', color: '#ef5350' },
        ].forEach(({ price, label, color }) => {
          _addPriceLine(_labelSeries, {
            price, color, lineWidth: 1, lineStyle: LineStyle.Dashed,
            axisLabelVisible: false, title: `${label} ${fmtPrice(price)}`,
          })
        })
      }
    }
  }
}

// ── Markers ───────────────────────────────────────────────────────────────────
function _updateMarkers(candles) {
  if (!_markersPlugin) return

  const patterns = detectCandlePatterns(candles)
  _patternByTime = new Map()
  patterns.forEach(p => {
    const t = toTime(candles[p.i].date)
    _patternByTime.set('idx:' + t, p.i)
    _patternByTime.set('pat:' + p.i, p)
  })

  _markersPlugin.setMarkers(
    patterns.map(p => ({
      time: toTime(candles[p.i].date),
      position: p.signal === 'bull' ? 'belowBar' : p.signal === 'bear' ? 'aboveBar' : 'inBar',
      color: p.signal === 'bull' ? '#26a69a' : p.signal === 'bear' ? '#ef5350' : '#fbbf24',
      shape: p.signal === 'bull' ? 'arrowUp' : p.signal === 'bear' ? 'arrowDown' : 'circle',
      size: 0.5,
      text: '',
    })),
  )
}

// ── Live price update (tick-driven, creates new candles automatically) ────────
function _intervalMinutes() {
  const m = { '1m': 1, '5m': 5, '30m': 30, '1h': 60 }
  return m[props.interval] || 0  // 0 = daily/weekly, no auto-candle
}

function _candleDateStr(date) {
  // Format Date to 'YYYY-MM-DD HH:MM' in IST
  const ist = new Date(date.getTime() + 5.5 * 3600000)
  const y = ist.getUTCFullYear()
  const mo = String(ist.getUTCMonth() + 1).padStart(2, '0')
  const d = String(ist.getUTCDate()).padStart(2, '0')
  const h = String(ist.getUTCHours()).padStart(2, '0')
  const mi = String(ist.getUTCMinutes()).padStart(2, '0')
  return `${y}-${mo}-${d} ${h}:${mi}`
}

function _applyLivePrice(price) {
  if (!price || !ohlcv.value.length || !_candleSeries) return

  const intMin = _intervalMinutes()
  const candles = [...ohlcv.value]
  const last = candles[candles.length - 1]

  // Check if we need a new candle (tick crossed into next period)
  if (intMin > 0 && last.date) {
    const now = new Date()
    const istNow = new Date(now.getTime() + 5.5 * 3600000)
    const nowMin = istNow.getUTCHours() * 60 + istNow.getUTCMinutes()
    // Parse last candle's minute from 'YYYY-MM-DD HH:MM'
    const parts = last.date.split(' ')
    if (parts.length === 2) {
      const [lh, lm] = parts[1].split(':').map(Number)
      const lastMin = lh * 60 + lm
      const lastBucket = Math.floor(lastMin / intMin)
      const nowBucket = Math.floor(nowMin / intMin)
      if (nowBucket > lastBucket || parts[0] !== `${istNow.getUTCFullYear()}-${String(istNow.getUTCMonth()+1).padStart(2,'0')}-${String(istNow.getUTCDate()).padStart(2,'0')}`) {
        // Fill any gap candles between last candle and current bucket
        const todayStr = `${istNow.getUTCFullYear()}-${String(istNow.getUTCMonth()+1).padStart(2,'0')}-${String(istNow.getUTCDate()).padStart(2,'0')}`
        const sameDay = parts[0] === todayStr
        const gapStart = sameDay ? lastBucket + 1 : nowBucket
        let prevClose = last.close
        for (let b = gapStart; b < nowBucket; b++) {
          const gm = b * intMin
          const gh = Math.floor(gm / 60), gmi = gm % 60
          if (gh < 9 || (gh === 9 && gmi < 15) || gh >= 16) continue // skip outside market hours
          const gDate = `${todayStr} ${String(gh).padStart(2,'0')}:${String(gmi).padStart(2,'0')}`
          const gapCandle = { date: gDate, open: prevClose, high: prevClose, low: prevClose, close: prevClose, volume: 0 }
          candles.push(gapCandle)
          const gt = toTime(gDate)
          _candleSeries.update({ time: gt, open: prevClose, high: prevClose, low: prevClose, close: prevClose })
          _volumeSeries.update({ time: gt, value: 0, color: `${GREEN}44` })
        }
        // Now create the current candle
        const bucketMin = nowBucket * intMin
        const newH = Math.floor(bucketMin / 60)
        const newM = bucketMin % 60
        const dateStr = `${todayStr} ${String(newH).padStart(2,'0')}:${String(newM).padStart(2,'0')}`
        const newCandle = { date: dateStr, open: price, high: price, low: price, close: price, volume: 0 }
        candles.push(newCandle)
        ohlcv.value = candles
        const t = toTime(dateStr)
        _candleSeries.update({ time: t, open: price, high: price, low: price, close: price })
        _volumeSeries.update({ time: t, value: 0, color: `${GREEN}44` })
        _updateLevels()
        return
      }
    }
  }

  // Update current candle
  const updated = { ...last }
  updated.close = price
  if (price > updated.high) updated.high = price
  if (price < updated.low) updated.low = price
  candles[candles.length - 1] = updated
  ohlcv.value = candles

  const t = toTime(updated.date)
  _candleSeries.update({ time: t, open: updated.open, high: updated.high, low: updated.low, close: updated.close })
  _volumeSeries.update({ time: t, value: Number(updated.volume) || 0, color: updated.close >= updated.open ? `${GREEN}44` : `${RED}44` })
  _updateLevels()
}

// ── Zoom controls ─────────────────────────────────────────────────────────────
function chartZoom(direction) {
  if (!_chart) return
  const range = _chart.timeScale().getVisibleLogicalRange()
  if (!range) return
  const delta = (range.to - range.from) * (direction < 0 ? 0.2 : -0.2)
  _chart.timeScale().setVisibleLogicalRange({ from: range.from + delta, to: range.to - delta })
}

function chartZoomReset() {
  if (_chart) _chart.timeScale().fitContent()
}

// ── Candle refresh timer ──────────────────────────────────────────────────────
function _apiInterval() {
  return props.interval === '1y' ? '1d' : props.interval
}

function _candleRefreshMs() {
  // Candles are created live from ticks — this is just a background sync for volume/corrections
  return props.interval === '1m' ? 60_000 : props.interval === '5m' ? 60_000 : 120_000
}

function _startCandleRefresh() {
  if (_candleRefreshTimer) clearInterval(_candleRefreshTimer)
  _candleRefreshTimer = setInterval(() => {
    if (props.marketOpen) _silentReloadCandles()
  }, _candleRefreshMs())
}

function _stopCandleRefresh() {
  if (_candleRefreshTimer) { clearInterval(_candleRefreshTimer); _candleRefreshTimer = null }
}

// ── Data loading ──────────────────────────────────────────────────────────────
async function reloadChart() {
  if (!props.chartTicker) return
  chartLoading.value = true
  ohlcv.value = []
  try {
    const end = new Date()
    const days = props.interval === '1m' ? 1 : props.interval === '5m' ? 3 : props.interval === '30m' ? 7 : props.interval === '1h' ? 30 : props.interval === '1y' ? 365 : props.interval === '1wk' ? 180 : 90
    const start = new Date(end - days * 86400000)
    const res = await getOHLCV({
      ticker: props.chartTicker,
      startDate: start.toISOString().slice(0, 10),
      endDate: end.toISOString().slice(0, 10),
      interval: _apiInterval(),
    })
    ohlcv.value = res.data?.ohlcv || []
    if (res.data?.ticker) {
      const resolved = res.data.ticker.toUpperCase()
      if (resolved !== props.chartTicker) {
        emit('update:chartTicker', resolved)
        emit('update:activeTicker', resolved.replace(/\.(NS|BO)$/i, ''))
      }
    }
    if (ohlcv.value.length) {
      await nextTick()
      _updateData()
      // Default to last ~30 candles for a tight intraday view
      const total = ohlcv.value.length
      if (total > 30) {
        _chart?.timeScale().setVisibleLogicalRange({ from: total - 30, to: total + 2 })
      } else {
        _chart?.timeScale().fitContent()
      }
      _startCandleRefresh()
    }
  } catch (e) {
    console.error('Chart load failed', e)
  } finally {
    chartLoading.value = false
  }
}

async function _silentReloadCandles() {
  if (!props.chartTicker || chartLoading.value) return
  try {
    const end = new Date()
    const days = props.interval === '1m' ? 1 : props.interval === '5m' ? 3 : props.interval === '30m' ? 7 : props.interval === '1h' ? 30 : props.interval === '1y' ? 365 : props.interval === '1wk' ? 180 : 90
    const start = new Date(end - days * 86400000)
    const res = await getOHLCV({
      ticker: props.chartTicker,
      startDate: start.toISOString().slice(0, 10),
      endDate: end.toISOString().slice(0, 10),
      interval: _apiInterval(),
    })
    const fresh = res.data?.ohlcv || []
    if (!fresh.length) return
    const existing = ohlcv.value
    if (!existing.length) { ohlcv.value = fresh; _updateData(); return }

    // Merge: use fresh data as the base, then append any live-created
    // candles that are NEWER than the last fresh candle (preserves
    // real-time bars that yfinance hasn't caught up to yet).
    const lastFreshDate = fresh[fresh.length - 1].date
    const liveTail = existing.filter(c => c.date > lastFreshDate)
    ohlcv.value = [...fresh, ...liveTail]
    _updateData()
  } catch (_) {}
}

// ── Watchers ──────────────────────────────────────────────────────────────────
watch(() => props.chartTicker, (v, old) => { if (v && v !== old) reloadChart() })
watch(() => props.interval, () => { if (props.chartTicker) reloadChart() })
watch(() => props.brokerLivePrice, price => { if (price) _applyLivePrice(price) })
watch(() => props.levels, () => { if (ohlcv.value.length) _updateLevels() })
watch(() => props.lightMode, () => {
  if (_chart) _chart.applyOptions(_chartTheme())
})

// ── Lifecycle ─────────────────────────────────────────────────────────────────
onMounted(() => {
  _setupChart()

  ro = new ResizeObserver(() => {
    if (_chart && chartWrap.value) {
      _chart.applyOptions({
        width: chartWrap.value.clientWidth,
        height: chartWrap.value.clientHeight,
      })
    }
  })
  if (chartWrap.value) ro.observe(chartWrap.value)

  if (props.chartTicker) reloadChart()
})

onUnmounted(() => {
  _stopCandleRefresh()
  _clearPriceLines()
  if (_chart) { _chart.remove(); _chart = null }
  _candleSeries = null; _labelSeries = null; _volumeSeries = null
  if (ro) { ro.disconnect(); ro = null }
})
</script>

<style src="../../styles/HomeChart.css"></style>
