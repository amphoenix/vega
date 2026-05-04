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

      <svg v-show="ohlcv.length" ref="svgRef" class="chart-svg"></svg>

      <!-- Hover tooltip -->
      <div
        class="chart-tooltip"
        ref="tooltipRef"
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
import * as d3 from 'd3'
import { getMarketOHLCV, getOHLCV } from '../api/market'

const props = defineProps({
  simulationId: { type: String, default: '' },  // optional — if empty, no sentiment overlay
  externalOhlcv: { type: Array, default: null }, // if provided, skip fetch and use directly
})

// ── form state ────────────────────────────────────────────────────────────────
const ticker    = ref('AAPL')
const startDate = ref('')
const endDate   = ref('')
const interval  = ref('1d')
const loading   = ref(false)
const error     = ref(null)

// ── DOM refs ──────────────────────────────────────────────────────────────────
const containerRef = ref(null)
const svgRef       = ref(null)

// ── chart data ────────────────────────────────────────────────────────────────
let ohlcv             = ref([])
let sentimentByRound  = ref([])

// ── tooltip state ─────────────────────────────────────────────────────────────
const tooltip = ref({
  visible: false, x: 0, y: 0,
  date: '', open: '', high: '', low: '', close: '', volume: '',
  up: true, sentiment: null, topPosts: []
})

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

// ── D3 chart ──────────────────────────────────────────────────────────────────
const MARGIN = { top: 16, right: 52, bottom: 72, left: 8 }
const VOL_RATIO  = 0.16
const SENT_RATIO = 0.09
const GREEN = '#1A936F'
const RED   = '#C5283D'

function drawChart() {
  const candles   = ohlcv.value
  const sentRnds  = sentimentByRound.value
  const container = containerRef.value
  if (!container || !candles.length) return

  const totalW = container.clientWidth  || 860
  const totalH = container.clientHeight || 460

  const innerW  = totalW - MARGIN.left - MARGIN.right
  const volH    = Math.floor((totalH - MARGIN.top - MARGIN.bottom) * VOL_RATIO)
  const sentH   = Math.floor((totalH - MARGIN.top - MARGIN.bottom) * SENT_RATIO)
  const candleH = totalH - MARGIN.top - MARGIN.bottom - volH - sentH - 8

  // date → sentiment map
  const sentMap = {}
  sentRnds.forEach(s => {
    if (!sentMap[s.date] || s.bullish_pct > sentMap[s.date].bullish_pct) {
      sentMap[s.date] = s
    }
  })

  // ── scales ─────────────────────────────────────────────────────────────────
  const xBand = d3.scaleBand()
    .domain(candles.map(d => d.date))
    .range([0, innerW])
    .padding(0.2)

  const parseDate = d3.timeParse('%Y-%m-%d')
  const xTime = d3.scaleTime()
    .domain([parseDate(candles[0].date), parseDate(candles[candles.length - 1].date)])
    .range([0, innerW])

  const priceMin = d3.min(candles, d => d.low)
  const priceMax = d3.max(candles, d => d.high)
  const pBuf = (priceMax - priceMin) * 0.06
  const yPrice = d3.scaleLinear()
    .domain([priceMin - pBuf, priceMax + pBuf])
    .range([candleH, 0])

  const yVol = d3.scaleLinear()
    .domain([0, d3.max(candles, d => d.volume) * 1.15])
    .range([volH, 0])

  const ySent = d3.scaleLinear().domain([0, 1]).range([sentH, 0])

  // ── SVG setup ───────────────────────────────────────────────────────────────
  const svg = d3.select(svgRef.value)
  svg.selectAll('*').remove()
  svg.attr('width', totalW).attr('height', totalH)

  const g = svg.append('g').attr('transform', `translate(${MARGIN.left},${MARGIN.top})`)

  // ── PANEL 1: Candlesticks ──────────────────────────────────────────────────
  const cG = g.append('g')

  // grid
  cG.append('g').call(
    d3.axisLeft(yPrice).ticks(5).tickSize(-innerW).tickFormat('')
  ).call(n => n.select('.domain').remove())
   .call(n => n.selectAll('.tick line').attr('stroke', '#F0F0F0').attr('stroke-dasharray', '3,3'))

  // wicks
  cG.selectAll('.wick').data(candles).join('line')
    .attr('x1', d => xBand(d.date) + xBand.bandwidth() / 2)
    .attr('x2', d => xBand(d.date) + xBand.bandwidth() / 2)
    .attr('y1', d => yPrice(d.high))
    .attr('y2', d => yPrice(d.low))
    .attr('stroke', d => d.close >= d.open ? GREEN : RED)
    .attr('stroke-width', 1)

  // bodies
  cG.selectAll('.body').data(candles).join('rect')
    .attr('x',      d => xBand(d.date))
    .attr('y',      d => yPrice(Math.max(d.open, d.close)))
    .attr('width',  xBand.bandwidth())
    .attr('height', d => Math.max(1, Math.abs(yPrice(d.open) - yPrice(d.close))))
    .attr('fill',   d => d.close >= d.open ? GREEN : RED)
    .attr('rx', 1)

  // price Y axis (right side)
  cG.append('g')
    .attr('transform', `translate(${innerW},0)`)
    .call(d3.axisRight(yPrice).ticks(5).tickFormat(d => `$${d}`))
    .call(n => n.select('.domain').remove())
    .call(n => n.selectAll('text').attr('fill', '#888').attr('font-size', '11px'))

  // simulation round markers on candle panel
  const uniqueSentDates = [...new Set(sentRnds.map(s => s.date).filter(d => xBand(d) !== undefined))]
  uniqueSentDates.forEach(dateStr => {
    const cx    = xBand(dateStr) + xBand.bandwidth() / 2
    const s     = sentMap[dateStr]
    const bull  = s ? s.bullish_pct : 0.5
    const color = bull >= 0.5 ? GREEN : RED

    cG.append('line')
      .attr('x1', cx).attr('x2', cx)
      .attr('y1', 0).attr('y2', candleH)
      .attr('stroke', color).attr('stroke-width', 1)
      .attr('stroke-dasharray', '2,5').attr('opacity', 0.5)

    cG.append('circle')
      .attr('cx', cx).attr('cy', 8).attr('r', 4)
      .attr('fill', color).attr('opacity', 0.9)
  })

  // ── PANEL 2: Sentiment band ────────────────────────────────────────────────
  const sentOffY = candleH + 4
  const sG = g.append('g').attr('transform', `translate(0,${sentOffY})`)

  sG.append('rect').attr('width', innerW).attr('height', sentH).attr('fill', '#FAFAFA').attr('rx', 2)

  sG.append('line')
    .attr('x1', 0).attr('x2', innerW)
    .attr('y1', ySent(0.5)).attr('y2', ySent(0.5))
    .attr('stroke', '#E0E0E0').attr('stroke-dasharray', '4,4')

  const sentLineData = candles
    .filter(d => sentMap[d.date])
    .map(d => ({ date: d.date, v: sentMap[d.date].bullish_pct }))

  if (sentLineData.length > 0) {
    if (sentLineData.length > 1) {
      sG.append('path')
        .datum(sentLineData)
        .attr('d', d3.area()
          .x(d => xBand(d.date) + xBand.bandwidth() / 2)
          .y0(ySent(0)).y1(d => ySent(d.v))
          .curve(d3.curveMonotoneX)
        )
        .attr('fill', 'rgba(26,147,111,0.10)')

      sG.append('path')
        .datum(sentLineData)
        .attr('d', d3.line()
          .x(d => xBand(d.date) + xBand.bandwidth() / 2)
          .y(d => ySent(d.v))
          .curve(d3.curveMonotoneX)
        )
        .attr('fill', 'none').attr('stroke', GREEN).attr('stroke-width', 1.5)
    }

    // dots
    sG.selectAll('.sent-dot').data(sentLineData).join('circle')
      .attr('cx', d => xBand(d.date) + xBand.bandwidth() / 2)
      .attr('cy', d => ySent(d.v))
      .attr('r', 3)
      .attr('fill', d => d.v >= 0.5 ? GREEN : RED)
  }

  sG.append('text')
    .attr('x', innerW + 4).attr('y', ySent(0.5))
    .attr('dominant-baseline', 'middle')
    .attr('fill', '#AAA').attr('font-size', '9px')
    .text('BULL')

  // ── PANEL 3: Volume bars ──────────────────────────────────────────────────
  const volOffY = sentOffY + sentH + 4
  const vG = g.append('g').attr('transform', `translate(0,${volOffY})`)

  vG.selectAll('.vbar').data(candles).join('rect')
    .attr('x',      d => xBand(d.date))
    .attr('y',      d => yVol(d.volume))
    .attr('width',  xBand.bandwidth())
    .attr('height', d => volH - yVol(d.volume))
    .attr('fill',   d => d.close >= d.open ? `${GREEN}55` : `${RED}55`)
    .attr('rx', 1)

  vG.append('g')
    .attr('transform', `translate(${innerW},0)`)
    .call(d3.axisRight(yVol).ticks(2).tickFormat(d => `${(d / 1e6).toFixed(0)}M`))
    .call(n => n.select('.domain').remove())
    .call(n => n.selectAll('text').attr('fill', '#BBB').attr('font-size', '10px'))

  // ── X axis ────────────────────────────────────────────────────────────────
  g.append('g')
    .attr('transform', `translate(0,${volOffY + volH + 4})`)
    .call(d3.axisBottom(xTime)
      .ticks(Math.min(candles.length, 8))
      .tickFormat(d3.timeFormat('%m/%d'))
    )
    .call(n => n.select('.domain').remove())
    .call(n => n.selectAll('text').attr('fill', '#888').attr('font-size', '11px'))

  // ── Mouse interaction overlay ─────────────────────────────────────────────
  const crossV = cG.append('line')
    .attr('y1', 0).attr('y2', candleH)
    .attr('stroke', '#CCC').attr('stroke-width', 1).attr('stroke-dasharray', '3,3')
    .attr('visibility', 'hidden')

  cG.append('rect')
    .attr('width', innerW).attr('height', candleH)
    .attr('fill', 'none').attr('pointer-events', 'all')
    .on('mousemove', function(event) {
      const [mx]  = d3.pointer(event)
      const idx   = Math.max(0, Math.min(candles.length - 1, Math.floor(mx / xBand.step())))
      const d     = candles[idx]
      const cx    = xBand(d.date) + xBand.bandwidth() / 2
      crossV.attr('x1', cx).attr('x2', cx).attr('visibility', 'visible')

      const rect = containerRef.value.getBoundingClientRect()
      const sent = sentMap[d.date]

      tooltip.value = {
        visible:   true,
        x:         event.clientX - rect.left + 14,
        y:         Math.max(4, event.clientY - rect.top - 60),
        date:      d.date,
        open:      `$${d.open.toFixed(2)}`,
        high:      `$${d.high.toFixed(2)}`,
        low:       `$${d.low.toFixed(2)}`,
        close:     `$${d.close.toFixed(2)}`,
        volume:    `${(d.volume / 1e6).toFixed(2)}M`,
        up:        d.close >= d.open,
        sentiment: sent ? sent.bullish_pct : null,
        topPosts:  sent ? sent.top_posts : [],
      }
    })
    .on('mouseleave', () => {
      crossV.attr('visibility', 'hidden')
      tooltip.value.visible = false
    })
}

// ── resize observer ───────────────────────────────────────────────────────────
let ro = null
onMounted(() => {
  ro = new ResizeObserver(() => { if (ohlcv.value.length) drawChart() })
  if (containerRef.value) ro.observe(containerRef.value)
})
onUnmounted(() => { if (ro) ro.disconnect() })
</script>

<style scoped>
.candle-chart-wrap {
  display: flex;
  flex-direction: column;
  height: 100%;
  background: #fff;
  font-family: 'Inter', system-ui, sans-serif;
}

/* ── Config bar ─────────────────────────────────────────────────────────────── */
.chart-config-bar {
  display: flex;
  align-items: center;
  gap: 6px;
  padding: 8px 12px;
  border-bottom: 1px solid #EAEAEA;
  flex-shrink: 0;
  flex-wrap: wrap;
}

.ticker-input {
  width: 90px;
  text-transform: uppercase;
}

.date-input {
  width: 130px;
}

.date-sep {
  color: #AAA;
  font-size: 12px;
}

.interval-select {
  width: 56px;
  padding: 4px 4px;
}

.ticker-input,
.date-input,
.interval-select {
  height: 28px;
  padding: 0 6px;
  border: 1px solid #DCDFE6;
  border-radius: 4px;
  font-size: 12px;
  background: #fff;
  color: #333;
  outline: none;
}

.ticker-input:focus,
.date-input:focus,
.interval-select:focus {
  border-color: #1A936F;
}

.load-btn {
  display: flex;
  align-items: center;
  gap: 4px;
  height: 28px;
  padding: 0 12px;
  background: #1A936F;
  color: #fff;
  border: none;
  border-radius: 4px;
  font-size: 12px;
  cursor: pointer;
  font-weight: 500;
}

.load-btn:disabled {
  opacity: 0.5;
  cursor: not-allowed;
}

.load-btn:not(:disabled):hover {
  background: #157a5c;
}

.chart-error {
  font-size: 11px;
  color: #C5283D;
}

.chart-info {
  font-size: 11px;
  color: #AAA;
  margin-left: 4px;
}

@keyframes spin { to { transform: rotate(360deg) } }
.spin {
  display: inline-block;
  width: 10px;
  height: 10px;
  border: 2px solid rgba(255,255,255,0.4);
  border-top-color: #fff;
  border-radius: 50%;
  animation: spin 0.7s linear infinite;
}

/* ── Chart container ────────────────────────────────────────────────────────── */
.chart-container {
  flex: 1;
  min-height: 0;
  position: relative;
  overflow: hidden;
}

.chart-svg {
  width: 100%;
  height: 100%;
  display: block;
}

/* ── Empty state ────────────────────────────────────────────────────────────── */
.chart-empty {
  position: absolute;
  inset: 0;
  display: flex;
  flex-direction: column;
  align-items: center;
  justify-content: center;
  gap: 8px;
  color: #AAA;
  font-size: 13px;
}

.empty-icon {
  font-size: 32px;
  margin-bottom: 4px;
}

.empty-sub {
  font-size: 11px;
  color: #CCC;
}

/* ── Tooltip ────────────────────────────────────────────────────────────────── */
.chart-tooltip {
  position: absolute;
  background: rgba(17, 17, 17, 0.93);
  color: #F0F0F0;
  padding: 8px 10px;
  border-radius: 5px;
  font-size: 11px;
  pointer-events: none;
  z-index: 50;
  min-width: 148px;
  box-shadow: 0 4px 12px rgba(0,0,0,0.25);
  backdrop-filter: blur(2px);
}

.tt-date {
  font-size: 10px;
  color: #888;
  margin-bottom: 4px;
  letter-spacing: 0.5px;
}

.tt-row {
  display: flex;
  justify-content: space-between;
  gap: 12px;
  line-height: 1.6;
}

.tt-row span:first-child {
  color: #777;
  font-size: 10px;
}

.tt-close { font-weight: 600; }
.up  { color: #1A936F; }
.dn  { color: #C5283D; }

.tt-divider {
  border-top: 1px solid #2a2a2a;
  margin: 4px 0;
}

.tt-posts {
  margin-top: 4px;
  border-top: 1px solid #2a2a2a;
  padding-top: 4px;
}

.tt-post {
  font-size: 10px;
  color: #888;
  white-space: nowrap;
  overflow: hidden;
  text-overflow: ellipsis;
  max-width: 180px;
  line-height: 1.4;
}
</style>
