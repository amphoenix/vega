<template>
  <div class="candle-chart-wrap">
    <!-- Config bar -->
    <div class="chart-config-bar">
      <input
        v-model="ticker"
        class="ticker-input mono"
        placeholder="TICKER (e.g. NVDA)"
        maxlength="10"
        @keyup.enter="load"
      />
      <input v-model="startDate" type="date" class="date-input mono" />
      <span class="date-sep">→</span>
      <input v-model="endDate" type="date" class="date-input mono" />
      <select v-model="interval" class="interval-select mono">
        <option value="1d">1D</option>
        <option value="1h">1H</option>
        <option value="30m">30M</option>
        <option value="1wk">1W</option>
      </select>
      <button class="load-btn" :disabled="loading" @click="load">
        <span v-if="loading" class="spin"></span>
        <span v-else>↻</span>
        {{ loading ? 'Loading…' : 'Load' }}
      </button>
      <span v-if="error" class="chart-error">{{ error }}</span>
      <span v-if="ohlcv.length" class="chart-info mono">
        {{ ohlcv.length }} candles · {{ sentimentByRound.length }} sentiment pts
      </span>
    </div>

    <!-- Chart container -->
    <div class="chart-container" ref="containerRef">
      <div v-if="!ohlcv.length" class="chart-empty">
        <div class="empty-icon">📈</div>
        <div>Enter a ticker and date range to overlay market data</div>
        <div class="empty-sub">Simulation sentiment will appear as markers on the chart</div>
      </div>

      <div ref="chartContainer" class="lw-host"></div>

      <!-- Hover tooltip -->
      <div
        class="chart-tooltip"
        v-show="tooltip.visible"
        :style="{ left: tooltip.x + 'px', top: tooltip.y + 'px' }"
      >
        <div class="tt-date mono">{{ tooltip.date }}</div>
        <div class="tt-row"><span>O</span><span>{{ tooltip.open }}</span></div>
        <div class="tt-row"><span>H</span><span>{{ tooltip.high }}</span></div>
        <div class="tt-row"><span>L</span><span>{{ tooltip.low }}</span></div>
        <div class="tt-row">
          <span>C</span>
          <span class="tt-close" :class="tooltip.up ? 'up' : 'dn'">{{ tooltip.close }}</span>
        </div>
        <div class="tt-row"><span>VOL</span><span>{{ tooltip.volume }}</span></div>
        <template v-if="tooltip.sentiment !== null">
          <div class="tt-divider"></div>
          <div class="tt-row sentiment">
            <span>BULL%</span>
            <span :class="tooltip.sentiment >= 0.5 ? 'up' : 'dn'">
              {{ (tooltip.sentiment * 100).toFixed(0) }}%
            </span>
          </div>
          <div v-if="tooltip.topPosts && tooltip.topPosts.length" class="tt-posts">
            <div v-for="(p, i) in tooltip.topPosts" :key="i" class="tt-post">{{ p }}</div>
          </div>
        </template>
      </div>
    </div>
  </div>
</template>

<script setup>
import { ref, onMounted, onUnmounted } from 'vue'
import {
  createChart, CandlestickSeries, HistogramSeries, AreaSeries,
  CrosshairMode, createSeriesMarkers,
} from 'lightweight-charts'
import { getMarketOHLCV, getOHLCV } from '../../api/market'
import { fmtPrice } from '../../utils/formatters'

const props = defineProps({
  simulationId: { type: String, default: '' },
  externalOhlcv: { type: Array, default: null },
})

// ── form state ────────────────────────────────────────────────────────────────
const ticker    = ref('AAPL')
const startDate = ref('')
const endDate   = ref('')
const interval  = ref('1d')
const loading   = ref(false)
const error     = ref(null)

// ── DOM refs ──────────────────────────────────────────────────────────────────
const containerRef   = ref(null)
const chartContainer = ref(null)

// ── chart data ────────────────────────────────────────────────────────────────
const ohlcv            = ref([])
const sentimentByRound = ref([])

// ── tooltip state ─────────────────────────────────────────────────────────────
const tooltip = ref({
  visible: false, x: 0, y: 0,
  date: '', open: '', high: '', low: '', close: '', volume: '',
  up: true, sentiment: null, topPosts: [],
})

// ── chart instances ───────────────────────────────────────────────────────────
let chart            = null
let candleSeries     = null
let volumeSeries     = null
let sentimentSeries  = null
let sentimentMarkers = null
let ro               = null
let _sentMap         = new Map()

// ── helpers ───────────────────────────────────────────────────────────────────
function toTime(dateStr) {
  if (!dateStr) return 0
  if (dateStr.length > 10) return Math.floor(new Date(dateStr.replace(' ', 'T')).getTime() / 1000)
  return Math.floor(new Date(dateStr + 'T00:00:00.000Z').getTime() / 1000)
}

// ── chart setup ───────────────────────────────────────────────────────────────
function _setupChart() {
  if (!chartContainer.value) return
  if (chart) { chart.remove(); chart = null }

  const el = chartContainer.value
  const w  = el.clientWidth  || el.parentElement?.clientWidth  || 800
  const h  = el.clientHeight || el.parentElement?.clientHeight || 400

  chart = createChart(el, {
    width:  w,
    height: h,
    layout: {
      background: { color: '#FFFFFF' },
      textColor:  '#444444',
    },
    grid: {
      vertLines: { color: '#F5F5F5' },
      horzLines: { color: '#F5F5F5' },
    },
    crosshair: { mode: CrosshairMode.Normal },
    rightPriceScale: { visible: true, borderVisible: false },
    timeScale: {
      timeVisible:    true,
      secondsVisible: false,
      borderVisible:  false,
    },
    handleScroll: true,
    handleScale:  true,
  })

  // Candlestick series — pane 0
  candleSeries = chart.addSeries(CandlestickSeries, {
    upColor:         '#1A936F',
    downColor:       '#C5283D',
    borderUpColor:   '#1A936F',
    borderDownColor: '#C5283D',
    wickUpColor:     '#1A936F',
    wickDownColor:   '#C5283D',
  }, 0)

  candleSeries.priceScale().applyOptions({
    scaleMargins: { top: 0.05, bottom: 0.22 },
  })

  // Volume histogram — pane 0 bottom section
  volumeSeries = chart.addSeries(HistogramSeries, {
    priceFormat:  { type: 'volume' },
    priceScaleId: 'vol',
    color:        '#1A936F55',
  }, 0)

  volumeSeries.priceScale().applyOptions({
    scaleMargins: { top: 0.82, bottom: 0 },
    visible: false,
  })

  // Sentiment area series — pane 1
  sentimentSeries = chart.addSeries(AreaSeries, {
    lineColor:   '#1A936F',
    topColor:    'rgba(26,147,111,0.18)',
    bottomColor: 'rgba(26,147,111,0.02)',
    lineWidth:   1.5,
    priceFormat: { type: 'custom', formatter: v => (v * 100).toFixed(0) + '%' },
  }, 1)

  sentimentSeries.priceScale().applyOptions({
    scaleMargins: { top: 0.08, bottom: 0.08 },
    visible:     true,
    minimumWidth: 44,
    autoScale:   false,
  })

  // Lock sentiment axis to 0–1 range
  sentimentSeries.applyOptions({
    autoscaleInfoProvider: () => ({
      priceRange: { minValue: 0, maxValue: 1 },
      margins: { above: 0.1, below: 0.1 },
    }),
  })

  // Markers plugin attached to candlestick series
  sentimentMarkers = createSeriesMarkers(candleSeries, [])

  // Crosshair tooltip
  chart.subscribeCrosshairMove(param => {
    if (!param.point || !param.time) {
      tooltip.value.visible = false
      return
    }
    const cData = param.seriesData.get(candleSeries)
    const vData = param.seriesData.get(volumeSeries)
    if (!cData) { tooltip.value.visible = false; return }

    const sent = _sentMap.get(param.time)

    tooltip.value = {
      visible:   true,
      x:         param.point.x + 14,
      y:         Math.max(4, param.point.y - 60),
      date:      new Date(param.time * 1000).toISOString().slice(0, 10),
      open:      fmtPrice(cData.open),
      high:      fmtPrice(cData.high),
      low:       fmtPrice(cData.low),
      close:     fmtPrice(cData.close),
      volume:    vData ? `${(vData.value / 1e6).toFixed(2)}M` : '—',
      up:        cData.close >= cData.open,
      sentiment: sent ? sent.bullish_pct : null,
      topPosts:  sent ? sent.top_posts   : [],
    }
  })
}

// ── update chart with loaded data ─────────────────────────────────────────────
function _updateData() {
  if (!chart || !ohlcv.value.length) return

  const candles  = ohlcv.value
  const sentRnds = sentimentByRound.value

  // Build sentiment map keyed by Unix timestamp
  _sentMap = new Map()
  sentRnds.forEach(s => {
    const t       = toTime(s.date)
    const current = _sentMap.get(t)
    if (!current || s.bullish_pct > current.bullish_pct) _sentMap.set(t, s)
  })

  const candleData = candles
    .map(d => ({ time: toTime(d.date), open: d.open, high: d.high, low: d.low, close: d.close }))
    .sort((a, b) => a.time - b.time)

  const volumeData = candles
    .map(d => ({
      time:  toTime(d.date),
      value: d.volume,
      color: d.close >= d.open ? '#1A936F55' : '#C5283D55',
    }))
    .sort((a, b) => a.time - b.time)

  const sentimentData = candles
    .filter(d => _sentMap.has(toTime(d.date)))
    .map(d => ({ time: toTime(d.date), value: _sentMap.get(toTime(d.date)).bullish_pct }))
    .sort((a, b) => a.time - b.time)

  candleSeries.setData(candleData)
  volumeSeries.setData(volumeData)
  sentimentSeries.setData(sentimentData)

  // Simulation round markers — colored circle above/below bar by bullishness
  const markers = [..._sentMap.entries()]
    .map(([t, s]) => ({
      time:     t,
      position: s.bullish_pct >= 0.5 ? 'belowBar' : 'aboveBar',
      color:    s.bullish_pct >= 0.5 ? '#1A936F'  : '#C5283D',
      shape:    'circle',
      text:     '',
      size:     0.6,
    }))
    .sort((a, b) => a.time - b.time)

  sentimentMarkers.setMarkers(markers)
  chart.timeScale().fitContent()
}

function drawChart() {
  if (!chart) _setupChart()
  _updateData()
}

// ── load data ─────────────────────────────────────────────────────────────────
async function load() {
  if (!props.simulationId || !ticker.value || !startDate.value || !endDate.value) {
    error.value = 'Fill in ticker and both dates'
    return
  }
  loading.value = true
  error.value   = null

  try {
    let res
    if (props.externalOhlcv) {
      ohlcv.value            = props.externalOhlcv
      sentimentByRound.value = []
      drawChart()
      loading.value = false
      return
    } else if (props.simulationId) {
      res = await getMarketOHLCV(props.simulationId, {
        ticker:    ticker.value,
        startDate: startDate.value,
        endDate:   endDate.value,
        interval:  interval.value,
      })
      ohlcv.value            = res.data.data.ohlcv
      sentimentByRound.value = res.data.data.sentiment_by_round || []
    } else {
      res = await getOHLCV({
        ticker:    ticker.value,
        startDate: startDate.value,
        endDate:   endDate.value,
        interval:  interval.value,
      })
      ohlcv.value            = res.data.data.ohlcv
      sentimentByRound.value = []
    }
    drawChart()
  } catch (e) {
    error.value = e?.response?.data?.error || e.message || 'Failed to load'
  } finally {
    loading.value = false
  }
}

// ── lifecycle ─────────────────────────────────────────────────────────────────
onMounted(() => {
  _setupChart()

  ro = new ResizeObserver(() => {
    if (!chart || !chartContainer.value) return
    chart.applyOptions({
      width:  chartContainer.value.clientWidth,
      height: chartContainer.value.clientHeight,
    })
  })
  if (containerRef.value) ro.observe(containerRef.value)
})

onUnmounted(() => {
  if (ro) ro.disconnect()
  if (chart) { chart.remove(); chart = null }
})
</script>

<style src="../../styles/CandleChart.css"></style>
