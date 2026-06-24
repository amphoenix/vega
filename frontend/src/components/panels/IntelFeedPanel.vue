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
          {{ tab }}
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
      <!-- Orders -->
      <div class="orders-section" v-if="orderRows.length">
        <div class="orders-section-title">
          ORDER BOOK
          <span class="orders-count">{{ orderRows.length }}</span>
          <span class="orders-refresh-icon" @click="fetchOrders" :class="{ spinning: ordersLoading }" title="Refresh">↻</span>
        </div>
        <div class="orders-table">
          <div class="orders-row orders-hdr">
            <span class="o-col o-sym">Symbol</span>
            <span class="o-col o-side">Side</span>
            <span class="o-col o-qty">Qty</span>
            <span class="o-col o-price">Price</span>
            <span class="o-col o-status">Status</span>
            <span class="o-col o-time">Time</span>
          </div>
          <div
            v-for="o in orderRows"
            :key="o.order_id || o.id"
            :class="['orders-row', orderStatusClass(o)]"
          >
            <span class="o-col o-sym" :title="o.trading_symbol || o.symbol">{{ o.trading_symbol || o.symbol || '—' }}</span>
            <span :class="['o-col', 'o-side', (o.txn_type || o.transaction_type || '').toUpperCase() === 'BUY' ? 'o-buy' : 'o-sell']">{{ (o.txn_type || o.transaction_type || '—').toUpperCase() }}</span>
            <span class="o-col o-qty">{{ o.qty || o.quantity || 0 }}</span>
            <span class="o-col o-price">₹{{ (o.price || o.avg_price || o.limit_price || 0).toFixed?.(2) ?? o.price }}</span>
            <span :class="['o-col', 'o-status', orderStatusClass(o)]">{{ (o.status || o.order_status || '—').toUpperCase() }}</span>
            <span class="o-col o-time">{{ fmtOrderTime(o.order_timestamp || o.exchange_timestamp || o.created_at) }}</span>
          </div>
        </div>
      </div>

      <!-- Positions -->
      <div class="orders-section" v-if="positionRows.length">
        <div class="orders-section-title">POSITIONS <span class="orders-count">{{ positionRows.length }}</span></div>
        <div class="orders-table">
          <div class="orders-row orders-hdr">
            <span class="o-col o-sym">Symbol</span>
            <span class="o-col o-side">Side</span>
            <span class="o-col o-qty">Qty</span>
            <span class="o-col o-price">Avg</span>
            <span class="o-col o-price">LTP</span>
            <span class="o-col o-pnl">P&L</span>
          </div>
          <div
            v-for="p in positionRows"
            :key="p.trading_symbol || p.symbol"
            class="orders-row"
          >
            <span class="o-col o-sym" :title="p.trading_symbol || p.symbol">{{ p.trading_symbol || p.symbol || '—' }}</span>
            <span :class="['o-col', 'o-side', (p.net_qty || p.quantity || 0) >= 0 ? 'o-buy' : 'o-sell']">{{ (p.net_qty || p.quantity || 0) >= 0 ? 'LONG' : 'SHORT' }}</span>
            <span class="o-col o-qty">{{ Math.abs(p.net_qty || p.quantity || 0) }}</span>
            <span class="o-col o-price">₹{{ (p.avg_price || 0).toFixed?.(2) ?? p.avg_price }}</span>
            <span class="o-col o-price">₹{{ (p.ltp || p.last_price || 0).toFixed?.(2) ?? p.ltp }}</span>
            <span :class="['o-col', 'o-pnl', (p.pnl || p.unrealized_pnl || 0) >= 0 ? 'up' : 'dn']">₹{{ (p.pnl || p.unrealized_pnl || 0).toFixed?.(2) ?? 0 }}</span>
          </div>
        </div>
      </div>

      <div class="feed-empty" v-if="!orderRows.length && !positionRows.length && !ordersLoading">
        No orders or positions found
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

const intelTabs = ['ALL', 'NEWS', 'ANALYSTS', 'REDDIT', 'FOMO', 'PREDICTIONS', 'SIM', 'ORDERS']

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
const positionRows = ref([])

async function fetchOrders() {
  const base = import.meta.env.VITE_API_BASE_URL || ''
  ordersLoading.value = true
  try {
    const [obResp, posResp] = await Promise.all([
      fetch(`${base}/api/broker/order-book`).then(r => r.json()),
      fetch(`${base}/api/broker/positions`).then(r => r.json()),
    ])
    orderRows.value = Array.isArray(obResp.data) ? obResp.data : []
    positionRows.value = Array.isArray(posResp.data) ? posResp.data.filter(p => (p.net_qty || p.quantity || 0) !== 0) : []
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

watch(intelTab, (tab) => {
  if (tab === 'ORDERS') fetchOrders()
  if (tab === 'NEWS' && !newsItems.value.length) fetchNews()
})

</script>

<style src="../../styles/IntelFeedPanel.css"></style>
