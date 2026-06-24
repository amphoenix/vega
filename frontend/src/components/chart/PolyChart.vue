<template>
  <div class="pc-wrap" ref="wrapRef">
    <div class="pc-toolbar">
      <span class="pc-label">YES Probability</span>
      <span class="pc-price" :class="latestPrice >= 0.5 ? 'yes' : 'no'" title="Current YES probability in cents (100¢ = 100% chance)">
        {{ latestPrice != null ? (latestPrice * 100).toFixed(1) + '¢' : '—' }}
      </span>
      <span class="pc-mid" v-if="latestMid">mid {{ (latestMid * 100).toFixed(1) }}¢</span>
      <span class="pc-pts">{{ history.length }} pts</span>
    </div>
    <div class="pc-info">
      📊 This chart tracks what the crowd thinks the chance of YES is.
      <b>100¢ = certain YES</b> · <b>0¢ = certain NO</b> · <b>50¢ = coin flip</b>
    </div>
    <div ref="chartRef" class="pc-host"></div>
  </div>
</template>

<script setup>
import { ref, watch, onMounted, onUnmounted, computed, nextTick } from 'vue'
import { createChart, AreaSeries, CrosshairMode } from 'lightweight-charts'
import { createPolyMarketStream } from '../../api/market'

const props = defineProps({
  marketId:   { type: String, default: '' },
  livePrice:  { type: Number, default: null },  // 0-1 from parent ticker
})

const wrapRef  = ref(null)
const chartRef = ref(null)
const history  = ref([])   // [{ time: unix_seconds, value: 0-1 }]
const latestMid = ref(null)

const latestPrice = computed(() => {
  if (history.value.length) return history.value[history.value.length - 1].value
  return props.livePrice
})

let _chart  = null
let _series = null
let _ro     = null
let _sse    = null

// ── Setup chart ───────────────────────────────────────────────────────────────

function _setup() {
  if (!chartRef.value) return
  if (_chart) { _chart.remove(); _chart = null }

  const el = chartRef.value
  const w  = el.clientWidth  || 380
  const h  = el.clientHeight || 120

  _chart = createChart(el, {
    width:  w,
    height: h,
    layout: {
      background: { color: '#0d0d1a' },
      textColor:  '#8b8fa8',
      fontSize:   11,
    },
    grid: {
      vertLines: { color: '#1a1a2e' },
      horzLines: { color: '#1a1a2e' },
    },
    crosshair:  { mode: CrosshairMode.Normal },
    rightPriceScale: {
      visible: true,
      borderColor: '#1a1a2e',
      scaleMargins: { top: 0.1, bottom: 0.1 },
    },
    timeScale:  { timeVisible: true, secondsVisible: true, borderColor: '#1a1a2e' },
  })

  _ro = new ResizeObserver(entries => {
    if (!_chart) return
    for (const e of entries) {
      const { width, height } = e.contentRect
      if (width > 0 && height > 0) _chart.applyOptions({ width, height })
    }
  })
  _ro.observe(el)

  _series = _chart.addSeries(AreaSeries, {
    lineColor:   '#00d4a8',
    topColor:    'rgba(0,212,168,0.18)',
    bottomColor: 'rgba(0,212,168,0.02)',
    lineWidth:   2,
    priceFormat: {
      type:      'custom',
      formatter: v => (v * 100).toFixed(1) + '¢',
    },
  }, 0)

  _series.applyOptions({
    autoscaleInfoProvider: () => ({
      priceRange: { minValue: 0, maxValue: 1 },
      margins: { above: 0.1, below: 0.1 },
    }),
  })
}

function _pushPoint(priceVal) {
  const nowSec = Math.floor(Date.now() / 1000)
  history.value.push({ time: nowSec, value: priceVal })
  if (history.value.length > 500) history.value = history.value.slice(-500)
  if (_series) _series.update({ time: nowSec, value: priceVal })
}

// ── SSE stream ────────────────────────────────────────────────────────────────

function _openStream() {
  _closeStream()
  if (!props.marketId) return
  _sse = createPolyMarketStream(props.marketId)

  _sse.addEventListener('tick', (e) => {
    try {
      const d = JSON.parse(e.data)
      const price = d.last || d.mid || 0
      if (price > 0) {
        _pushPoint(price)
        latestMid.value = d.mid || null
      }
    } catch {}
  })

  _sse.onerror = () => {}
}

function _closeStream() {
  if (_sse) { _sse.close(); _sse = null }
}

// ── Seed with livePrice if no history yet ─────────────────────────────────────

watch(() => props.livePrice, (price) => {
  if (price == null) return
  // If we have synthetic history but no SSE ticks yet, update last point in place
  _pushPoint(price)
})

watch(() => props.marketId, async (id) => {
  if (!id) return
  history.value = []
  if (_series) _series.setData([])
  await nextTick()
  const seedPrice = props.livePrice ?? 0.5
  const now = Math.floor(Date.now() / 1000)
  for (let i = 9; i >= 0; i--) {
    const pt = { time: now - i * 5, value: seedPrice }
    history.value.push(pt)
    if (_series) _series.update(pt)
  }
  if (_chart) _chart.timeScale().fitContent()
  _openStream()
})

// ── Lifecycle ─────────────────────────────────────────────────────────────────

onMounted(async () => {
  await nextTick()  // wait for flex layout to compute before reading clientHeight
  _setup()
  const seedPrice = props.livePrice ?? 0.5
  const now = Math.floor(Date.now() / 1000)
  for (let i = 9; i >= 0; i--) {
    const pt = { time: now - i * 5, value: seedPrice }
    history.value.push(pt)
    if (_series) _series.update(pt)
  }
  if (_chart) _chart.timeScale().fitContent()
  _openStream()
})

onUnmounted(() => {
  _closeStream()
  if (_ro) _ro.disconnect()
  if (_chart) { _chart.remove(); _chart = null }
})
</script>

<style scoped>
.pc-wrap {
  position: relative;
  display: flex;
  flex-direction: column;
  height: 100%;
  background: #0d0d1a;
  border-radius: 4px;
  overflow: hidden;
}

.pc-toolbar {
  display: flex;
  align-items: center;
  gap: 8px;
  padding: 5px 10px;
  background: #111125;
  border-bottom: 1px solid #1a1a2e;
  flex-shrink: 0;
}

.pc-label { font-size: 11px; color: #6b6f8a; text-transform: uppercase; letter-spacing: 0.5px; }
.pc-price { font-size: 15px; font-weight: 700; }
.pc-price.yes { color: #00d4a8; }
.pc-price.no  { color: #ff4757; }
.pc-mid  { font-size: 11px; color: #4a4e6a; }
.pc-pts  { font-size: 10px; color: #3a3e5a; margin-left: auto; }

.pc-host { flex: 1; min-height: 0; }

.pc-info {
  font-size: 10px; color: #4a4e6a; padding: 3px 10px;
  background: #0d0d1a; border-bottom: 1px solid #1a1a2e;
  flex-shrink: 0;
}
.pc-info b { color: #8b8fa8; }
</style>
