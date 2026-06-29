<template>
  <div class="feed-panel">
    <div class="feed-panel-header">
      <span class="live-dot"></span>
      <span class="fp-title">INTELLIGENCE FEED</span>
      <div class="intel-tabs">
        <button
          v-for="tab in intelTabs"
          :key="tab"
          :class="['itab', { active: intelTab === tab }]"
          @click="intelTab = tab"
        >
          {{ tab }}<template v-if="tab === 'ORDERS' && livePnl !== 0">
            <span :class="['tab-pnl', livePnl >= 0 ? 'up' : 'dn']">
              {{ livePnl >= 0 ? '+' : '' }}₹{{ livePnl.toFixed(0) }}
            </span>
          </template>
        </button>
      </div>
      <button
        class="ai-pred-btn"
        @click="$emit('run-ai-predict')"
        :disabled="aiPredLoading || !chartTicker"
      >
        <span v-if="aiPredLoading" class="spinner"></span>
        <span v-else>🧠</span>
        {{ aiPredLoading ? 'Analysing…' : 'AI Predict' }}
      </button>
      <span v-if="signalsLoading" class="fp-loading">
        <span class="spinner"></span> loading…
      </span>
    </div>

    <!-- ── Feed carousel (default tabs except ORDERS and NEWS) ── -->
    <div v-if="intelTab !== 'ORDERS' && intelTab !== 'NEWS'" class="feed-carousel" ref="feedRef">
      <TransitionGroup name="feed" tag="div" class="feed-cards-inner">
        <div
          v-for="item in visibleFeed"
          :key="item.id"
          :class="['feed-card', item.type]"
          @mouseenter="showFeedTooltip(item, $event)"
          @mouseleave="hideFeedTooltip"
          @mousemove="moveFeedTooltip($event)"
        >
          <div class="fc-top">
            <span class="fc-badge" :class="item.type">{{ badgeLabel(item.type) }}</span>
            <span
              v-if="item.sentiment !== undefined"
              :class="['fc-sent', item.sentiment >= 0.55 ? 'up' : item.sentiment < 0.45 ? 'dn' : 'neu']"
            >
              {{ item.sentiment >= 0.55 ? '▲ Bull' : item.sentiment < 0.45 ? '▼ Bear' : '— Neu' }}
            </span>
          </div>
          <div class="fc-title">{{ item.title }}</div>
          <div class="fc-meta" v-if="item.meta">{{ item.meta }}</div>
        </div>
      </TransitionGroup>
      <div class="feed-empty" v-if="!visibleFeed.length && !signalsLoading">
        Select a ticker to load signals
      </div>
    </div>

    <!-- ── NEWS tab ── -->
    <div v-if="intelTab === 'NEWS'" class="feed-carousel">
      <div class="feed-cards-inner">
        <a
          v-for="(n, idx) in newsItems"
          :key="idx"
          :href="n.link"
          target="_blank"
          rel="noopener"
          class="feed-card news news-link-card"
        >
          <div class="fc-top">
            <span class="fc-badge news">{{ n.source.toUpperCase() }}</span>
            <span class="fc-meta">{{ fmtNewsTime(n.published) }}</span>
          </div>
          <div class="fc-title">{{ n.title }}</div>
          <div class="fc-meta" v-if="n.summary">{{ stripHtml(n.summary) }}</div>
        </a>
      </div>
      <div class="feed-empty" v-if="!newsItems.length && !newsLoading">
        No news — click <button class="news-refresh-inline" @click.prevent="fetchNews">↻ Refresh</button>
      </div>
      <div class="feed-empty" v-if="newsLoading">
        <span class="spinner"></span> loading…
      </div>
    </div>

    <!-- ── ORDERS tab content ── -->
    <div v-if="intelTab === 'ORDERS'" class="orders-tab">
      <!-- P&L summary bar -->
      <div class="pnl-summary-bar" v-if="mergedRows.length">
        <span class="pnl-label">Realized</span>
        <span :class="['pnl-value', realizedPnl >= 0 ? 'up' : 'dn']">
          {{ realizedPnl >= 0 ? '+' : '' }}₹{{ realizedPnl.toFixed(0) }}
        </span>
        <span class="pnl-sep">|</span>
        <span class="pnl-label">Paper</span>
        <span :class="['pnl-value', paperPnl >= 0 ? 'up' : 'dn']">
          {{ paperPnl >= 0 ? '+' : '' }}₹{{ paperPnl.toFixed(0) }}
        </span>
        <span class="pnl-sep">|</span>
        <span class="pnl-label">System</span>
        <span :class="['pnl-value', systemTotalPnl >= 0 ? 'up' : 'dn']">
          {{ systemTotalPnl >= 0 ? '+' : '' }}₹{{ systemTotalPnl.toFixed(0) }}
        </span>
        <span class="pnl-sep">|</span>
        <span class="pnl-label">Symbols</span>
        <span class="pnl-value">{{ symbolCount }}</span>
        <span class="pnl-label" style="margin-left:4px">Orders</span>
        <span class="pnl-value">{{ mergedRows.length }}</span>
        <span class="orders-refresh-icon" @click="fetchOrders" :class="{ spinning: ordersLoading }" title="Refresh" style="margin-left:auto">↻</span>
      </div>

      <!-- Unified orders table: broker orders + paper system trades merged -->
      <div class="orders-section" v-if="mergedRows.length">
        <div class="orders-table merged-grid">
          <div class="orders-row orders-hdr">
            <span class="o-col o-sym">Symbol</span>
            <span class="o-col o-side">Side</span>
            <span class="o-col o-qty">Qty</span>
            <span class="o-col o-price">Price</span>
            <span class="o-col o-mode">Mode</span>
            <span class="o-col o-type">Type</span>
            <span class="o-col o-src">Source</span>
            <span class="o-col o-pnl">P&L</span>
            <span class="o-col o-time">Time</span>
            <span class="o-col o-status">Status</span>
          </div>
          <div v-for="r in mergedRows" :key="r.id" class="orders-row" :class="r.statusClass">
            <span class="o-col o-sym" :title="r.symbol">{{ r.symbol }}</span>
            <span :class="['o-col', 'o-side', r.side === 'BUY' ? 'o-buy' : 'o-sell']">{{ r.side }}</span>
            <span class="o-col o-qty">{{ r.qty }}</span>
            <span class="o-col o-price">{{ r.priceDisplay }}</span>
            <span :class="['o-col', 'o-mode', r.isLive ? 'o-live' : 'o-paper']">{{ r.isLive ? 'LIVE' : 'PAPER' }}</span>
            <span :class="['o-col', 'o-type', 'o-type-' + r.type]">{{ r.type }}</span>
            <span :class="['o-col', 'o-src', r.source === 'Manual' ? 'o-manual' : 'o-system']">{{ r.source }}</span>
            <span :class="['o-col', 'o-pnl', r.pnl > 0 ? 'up' : r.pnl < 0 ? 'dn' : '']">{{ r.pnlDisplay }}</span>
            <span class="o-col o-time">{{ r.time }}</span>
            <span class="o-col o-status">{{ r.status }}</span>
          </div>
        </div>
      </div>

      <div class="feed-empty" v-if="!mergedRows.length && !ordersLoading">
        No F&O trades today
      </div>
    </div>

    <div class="pred-bar" v-if="prediction.ready && intelTab !== 'ORDERS'">
      <span class="pred-label">AI PREDICTION</span>
      <span class="pred-item" :class="prediction.shortClass">SHORT: <b>{{ prediction.short }}</b> {{ prediction.shortPct }}%</span>
      <span class="pred-divider">|</span>
      <span class="pred-item" :class="prediction.longClass">LONG: <b>{{ prediction.long }}</b> {{ prediction.longPct }}%</span>
      <span
        class="pred-fomo"
        v-if="fomoScore !== null"
        title="Retail Hype Score: 0=nobody cares, 100=extreme excitement/panic"
      >Hype Score {{ fomoScore }}/100</span>
    </div>
  </div>

  <Teleport to="body">
    <div
      class="fc-tooltip"
      v-show="feedTip.visible"
      :style="{ left: feedTip.x + 'px', top: feedTip.y + 'px' }"
    >
      <div class="fc-tt-title">{{ feedTip.title }}</div>
      <div class="fc-tt-meta" v-if="feedTip.meta">{{ feedTip.meta }}</div>
    </div>
  </Teleport>
</template>

<script setup>
import { ref, computed, watch, onMounted, onUnmounted } from 'vue'
import { onOrderEvent } from '../../composables/useSSE'

const props = defineProps({
  chartTicker: { type: String, default: '' },
  feedItems: { type: Array, default: () => [] },
  signalsLoading: { type: Boolean, default: false },
  fomoScore: { type: Number, default: null },
  tickerStats: { type: Object, default: () => ({}) },
  aiPredLoading: { type: Boolean, default: false },
  redditSent: { type: Number, default: null },
})

defineEmits(['run-ai-predict'])

const intelTabs = ['ALL', 'NEWS', 'FOMO', 'PREDICTIONS', 'ORDERS']

// ── RSSHub news ─────────────────────────────────────────────────────────────
const newsSource = ref('')
const newsLoading = ref(false)
const newsItems = ref([])

async function fetchNews() {
  const base = import.meta.env.VITE_API_BASE_URL || ''
  newsLoading.value = true
  try {
    const params = newsSource.value ? `?source=${newsSource.value}&limit=40` : '?limit=40'
    const resp = await fetch(`${base}/api/market/news${params}`)
    const body = await resp.json()
    newsItems.value = Array.isArray(body.data) ? body.data : []
  } catch (e) {
    console.warn('fetchNews failed', e)
  } finally {
    newsLoading.value = false
  }
}

function fmtNewsTime(ts) {
  if (!ts) return ''
  try {
    const d = new Date(ts)
    if (isNaN(d.getTime())) return ts
    const now = new Date()
    const diffMs = now - d
    const diffMin = Math.floor(diffMs / 60000)
    if (diffMin < 1) return 'just now'
    if (diffMin < 60) return `${diffMin}m ago`
    const diffHr = Math.floor(diffMin / 60)
    if (diffHr < 24) return `${diffHr}h ago`
    return d.toLocaleDateString('en-IN', { day: 'numeric', month: 'short' })
  } catch { return ts }
}

function stripHtml(html) {
  return html.replace(/<[^>]*>/g, '').replace(/&amp;/g, '&').replace(/&lt;/g, '<').replace(/&gt;/g, '>').replace(/&quot;/g, '"').trim()
}
const intelTab = ref('ALL')
const feedRef = ref(null)
const feedTip = ref({ visible: false, x: 0, y: 0, title: '', meta: '' })

const visibleFeed = computed(() => {
  if (intelTab.value === 'ALL') return props.feedItems.slice(0, 40)
  const map = {
    NEWS: 'news',
    ANALYSTS: 'twitter',
    REDDIT: 'reddit',
    FOMO: 'fomo',
    PREDICTIONS: 'prediction',
    SIM: 'simulation',
  }
  return props.feedItems.filter(i => i.type === map[intelTab.value]).slice(0, 40)
})

const prediction = computed(() => {
  if (props.fomoScore === null || !props.tickerStats.change_1d) return { ready: false }
  const f = props.fomoScore
  const m = props.tickerStats.change_1d || 0
  const rs = props.redditSent || 0.5
  const shortScore = Math.round(f * 0.4 + (m > 0 ? 65 : 35) * 0.3 + rs * 100 * 0.3)
  const longScore = Math.round(rs * 100 * 0.5 + (m > 0 ? 58 : 42) * 0.3 + Math.min(f, 60) * 0.2)
  return {
    ready: true,
    short: shortScore >= 55 ? 'BULLISH' : shortScore <= 45 ? 'BEARISH' : 'NEUTRAL',
    shortPct: shortScore,
    shortClass: shortScore >= 55 ? 'up' : shortScore <= 45 ? 'dn' : 'neu',
    long: longScore >= 55 ? 'BULLISH' : longScore <= 45 ? 'BEARISH' : 'NEUTRAL',
    longPct: longScore,
    longClass: longScore >= 55 ? 'up' : longScore <= 45 ? 'dn' : 'neu',
  }
})

function badgeLabel(type) {
  return { news: 'NEWS', twitter: 'ANALYST', reddit: 'REDDIT', fomo: 'FOMO', prediction: 'AI PRED', simulation: 'SIM AGENT' }[type] || type.toUpperCase()
}

function showFeedTooltip(item, e) {
  const card = e.currentTarget
  const titleEl = card.querySelector('.fc-title')
  const metaEl = card.querySelector('.fc-meta')
  const titleClipped = titleEl && titleEl.scrollHeight > titleEl.clientHeight + 1
  const metaClipped = metaEl && metaEl.scrollWidth > metaEl.clientWidth + 1
  if (!titleClipped && !metaClipped) return
  feedTip.value = {
    visible: true,
    x: e.clientX + 14,
    y: e.clientY + 14,
    title: titleClipped ? item.title || '' : '',
    meta: metaClipped ? item.meta || '' : '',
  }
}

function moveFeedTooltip(e) {
  if (!feedTip.value.visible) return
  const tipW = 300, tipH = 100
  feedTip.value.x = e.clientX + 14 + tipW > window.innerWidth ? e.clientX - tipW - 8 : e.clientX + 14
  feedTip.value.y = e.clientY + 14 + tipH > window.innerHeight ? e.clientY - tipH - 8 : e.clientY + 14
}

function hideFeedTooltip() {
  feedTip.value.visible = false
}

// ── Orders tab ─────────────────────────────────────────────────────────────
const ordersLoading = ref(false)
const orderRows = ref([])
const systemTradesMap = ref({})
const systemOrderIds = ref(new Set())
const systemExitOrderIds = ref(new Set())

// ── P&L: Dhan positions API for total, pnl_trades DB for system ──────────
const positionRows = ref([])
const systemTradesList = ref([])
const realizedPnl = computed(() => positionRows.value.reduce((s, p) => s + (p.realized_pnl || 0), 0))
const unrealizedPnl = computed(() => positionRows.value.reduce((s, p) => s + (p.unrealized_pnl || 0), 0))
const totalPnl = computed(() => realizedPnl.value + unrealizedPnl.value)
const symbolCount = computed(() => new Set(positionRows.value.map(p => p.symbol)).size)
const _isPaperTrade = (t) => {
  const oid = (t.order_id || '').toUpperCase()
  return !oid || oid === 'PAPER'
}
const systemTotalPnl = computed(() => systemTradesList.value.reduce((s, t) => s + (t.net_pnl || 0), 0))
const livePnl = computed(() => realizedPnl.value)
const paperPnl = computed(() => systemTradesList.value.filter(_isPaperTrade).reduce((s, t) => s + (t.net_pnl || 0), 0))

// ── Merged rows: broker orders (LIVE) + paper system trades, sorted by time ──
const mergedRows = computed(() => {
  const rows = []

  // 1. Broker orders — all LIVE
  for (const o of orderRows.value) {
    const sym = (o.symbol || '').toUpperCase()
    const meta = systemTradesMap.value[sym] || {}
    // source: strict order_id match (System vs Manual)
    const isSystem = systemOrderIds.value.has(o.order_id) || systemExitOrderIds.value.has(o.order_id)
    // type: symbol-based — informational context even for manual orders on known symbols
    const type = meta.mode ? meta.mode.charAt(0).toUpperCase() + meta.mode.slice(1) : '—'
    const isFilled = ['TRADED', 'COMPLETE', 'FILLED', 'COMPLETED'].includes((o.status || '').toUpperCase())
    rows.push({
      id: o.order_id || ('b-' + rows.length),
      symbol: o.symbol || '',
      side: o.side || '',
      qty: o.qty || 0,
      price: isFilled ? (o.avg_price || 0) : 0,
      priceDisplay: isFilled ? (o.avg_price ? '₹' + o.avg_price.toFixed(2) : '—') : '—',
      isLive: true,
      type,
      source: isSystem ? 'System' : 'Manual',
      pnl: 0,
      pnlDisplay: '—',
      time: fmtOrderTime(o.time),
      _sortTime: o.time ? new Date(o.time).getTime() : 0,
      status: o.status || '',
      statusClass: orderStatusClass(o),
    })
  }

  // 2. Paper system trades — entry + exit rows with P&L on exit
  for (const t of systemTradesList.value) {
    const oid = (t.order_id || '').toUpperCase()
    if (oid && oid !== 'PAPER') continue  // live trades already in broker orders
    const mode = t.mode || 'scalp'
    const type = mode.charAt(0).toUpperCase() + mode.slice(1)
    const entryTime = t.entry_time || ''
    const exitTime = t.exit_time || t.timestamp || ''
    const ep = t.entry_prem || 0
    rows.push({
      id: 'pe-' + (t.id || rows.length),
      symbol: t.symbol || '',
      side: 'BUY',
      qty: t.qty || 0,
      price: ep,
      priceDisplay: ep > 0 ? '₹' + ep.toFixed(2) : '—',
      isLive: false,
      type,
      source: 'System',
      pnl: 0,
      pnlDisplay: '—',
      time: fmtOrderTime(entryTime),
      _sortTime: entryTime ? new Date(entryTime).getTime() : 0,
      status: 'SIMULATED',
      statusClass: 'o-pending',
    })
    const pnl = t.net_pnl || 0
    const xp = t.exit_prem || 0
    rows.push({
      id: 'px-' + (t.id || rows.length),
      symbol: t.symbol || '',
      side: 'SELL',
      qty: t.qty || 0,
      price: xp,
      priceDisplay: xp > 0 ? '₹' + xp.toFixed(2) : '—',
      isLive: false,
      type,
      source: 'System',
      pnl,
      pnlDisplay: (pnl >= 0 ? '+' : '') + '₹' + pnl.toFixed(0),
      time: fmtOrderTime(exitTime),
      _sortTime: exitTime ? new Date(exitTime).getTime() : 0,
      status: 'SIMULATED',
      statusClass: 'o-pending',
    })
  }

  // Sort by time descending (newest first)
  rows.sort((a, b) => (b._sortTime || 0) - (a._sortTime || 0))
  return rows
})

// Keep pnlRows as alias for summary bar count
const pnlRows = computed(() => mergedRows.value)

async function fetchOrders() {
  const base = import.meta.env.VITE_API_BASE_URL || ''
  ordersLoading.value = true
  try {
    const [obResp, trResp, posResp] = await Promise.all([
      fetch(`${base}/api/broker/order-book`).then(r => r.json()),
      fetch(`${base}/api/trade/pnl/trades?limit=50&date=${new Date().toLocaleDateString('en-CA')}`).then(r => r.json()).catch(() => ({ trades: [] })),
      fetch(`${base}/api/broker/positions`).then(r => r.json()).catch(() => ({ data: [] })),
    ])
    const _isFnO = (o) => {
      const seg = (o.exchange || '').toUpperCase()
      return seg.includes('FNO') || seg.includes('NFO') || seg.includes('BFO')
    }
    orderRows.value = (Array.isArray(obResp.data) ? obResp.data : []).filter(_isFnO)
    positionRows.value = (Array.isArray(posResp.data) ? posResp.data : []).filter(_isFnO)
    // System trades: raw list for P&L sum, map by uppercase symbol for metadata
    const _trades = Array.isArray(trResp.trades) ? trResp.trades : []
    systemTradesList.value = _trades
    const _map = {}
    const _oidSet = new Set()     // live system entry order_ids
    const _exitOidSet = new Set() // live system exit order_ids
    for (const t of _trades) {
      const sym = (t.symbol || '').toUpperCase()
      if (!sym) continue
      const oid = (t.order_id || '').toUpperCase()
      const isPaper = !oid || oid === 'PAPER'
      _map[sym] = {
        source: (t.mode || 'system').charAt(0).toUpperCase() + (t.mode || 'system').slice(1),
        exit_reason: t.exit_reason || '',
        mode: t.mode || '',
        symbol: t.symbol || '',
        isPaper,
      }
      if (!isPaper) {
        _oidSet.add(t.order_id)
        if (t.exit_order_id) _exitOidSet.add(t.exit_order_id)
      }
    }
    systemTradesMap.value = _map
    systemOrderIds.value = _oidSet
    systemExitOrderIds.value = _exitOidSet
  } catch (e) {
    console.warn('fetchOrders failed', e)
  } finally {
    ordersLoading.value = false
  }
}

function orderStatusClass(o) {
  const s = (o.status || o.order_status || '').toUpperCase()
  if (['COMPLETE', 'COMPLETED', 'TRADED', 'FILLED'].includes(s)) return 'o-filled'
  if (['REJECTED', 'CANCELLED', 'CANCELED', 'FAILED'].includes(s)) return 'o-rejected'
  if (['OPEN', 'PENDING', 'TRIGGER_PENDING'].includes(s)) return 'o-pending'
  return ''
}

function fmtOrderTime(ts) {
  if (!ts) return '—'
  try {
    const d = new Date(ts)
    return d.toLocaleTimeString('en-IN', { hour: '2-digit', minute: '2-digit', second: '2-digit', hour12: false })
  } catch { return ts }
}

// Shared SSE: refresh orders on any order event
onOrderEvent(() => {
  if (intelTab.value === 'ORDERS') fetchOrders()
})

// Auto-refresh positions every 10s when ORDERS tab is active
let _ordersInterval = null
watch(intelTab, (tab) => {
  if (tab === 'ORDERS') {
    fetchOrders()
    if (!_ordersInterval) _ordersInterval = setInterval(() => fetchOrders(), 10000)
  } else {
    if (_ordersInterval) { clearInterval(_ordersInterval); _ordersInterval = null }
  }
  if (tab === 'NEWS' && !newsItems.value.length) fetchNews()
})

onUnmounted(() => {
  if (_ordersInterval) { clearInterval(_ordersInterval); _ordersInterval = null }
})

</script>

<style src="../../styles/IntelFeedPanel.css"></style>
