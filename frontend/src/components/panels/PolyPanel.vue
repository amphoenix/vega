<template>
  <div class="poly-view">
    <!-- ═══ HEADER ═══════════════════════════════════════════════════════════ -->
    <div class="poly-header">
      <div class="poly-title-row">
        <div class="poly-title-left">
          <h2 class="poly-title">🎯 POLYMARKET</h2>
          <span class="mode-badge paper">PAPER</span>
        </div>
        <div class="poly-controls">
          <span class="poly-status" :class="connected ? 'online' : 'offline'">
            <span class="status-dot"></span>
            {{ connected ? 'Connected' : 'Offline' }}
          </span>
          <span class="poly-balance" v-if="balance > 0">
            ${{ balance.toFixed(2) }}
          </span>
          <label class="poly-toggle" :title="autoTrading ? 'Auto-execute trades' : 'Monitor only'">
            <input type="checkbox" v-model="autoTrading" @change="toggleAuto" />
            <span class="poly-toggle-slider"></span>
            <span class="poly-toggle-label">{{ autoTrading ? 'Auto' : 'Monitor' }}</span>
          </label>
        </div>
      </div>

      <!-- Poly P&L strip -->
      <div class="poly-pnl-strip">
        <span class="ppnl-label">Poly P&amp;L</span>
        <span :class="['ppnl-net', segPnl.net >= 0 ? 'up' : 'dn']">
          Today: {{ segPnl.net >= 0 ? '+' : '' }}${{ segPnl.net.toFixed(2) }}
        </span>
        <span class="ppnl-trades" title="Today's closed trades">{{ segPnl.trades }}T</span>
        <span class="ppnl-brk" title="Total brokerage today">B: ${{ segPnl.brokerage.toFixed(2) }}</span>
        <span class="ppnl-paper">PAPER</span>
      </div>

      <!-- Category filter -->
      <div class="poly-categories">
        <button
          v-for="cat in categories"
          :key="cat.value"
          :class="['poly-cat-btn', { active: category === cat.value }]"
          @click="category = cat.value"
        >{{ cat.label }}</button>
      </div>

      <!-- Search -->
      <div class="poly-search-wrap">
        <input
          v-model="searchQuery"
          class="poly-search"
          placeholder="Search markets…"
        />
        <select class="poly-sort" v-model="sortBy">
          <option value="volume">Volume</option>
          <option value="newest">Newest</option>
          <option value="ending_soon">Ending Soon</option>
          <option value="liquidity">Liquidity</option>
        </select>
      </div>
    </div>

    <div class="poly-body">
      <!-- LEFT: Market list -->
      <section class="poly-markets">
        <div v-if="loading" class="poly-loading">Loading markets…</div>
        <div v-else-if="!filteredMarkets.length" class="poly-empty">
          No markets found{{ searchQuery ? ' for "' + searchQuery + '"' : '' }}
        </div>
        <div
          v-for="market in filteredMarkets"
          :key="market.id"
          :class="['pm-card', { active: activeMarket?.id === market.id }]"
          @click="selectMarket(market)"
        >
          <div class="pm-question">{{ market.question }}</div>
          <div class="pm-meta">
            <span class="pm-volume">Vol ${{ formatVolume(market.volume) }}</span>
            <span class="pm-liquidity">Liq ${{ formatVolume(market.liquidity) }}</span>
            <span class="pm-end" v-if="market.end_date">
              Ends {{ formatDate(market.end_date) }}
            </span>
          </div>
          <div class="pm-outcomes" v-if="market.outcomes?.length">
            <div
              v-for="outcome in market.outcomes"
              :key="outcome.name"
              class="pm-outcome"
            >
              <span class="pmo-name">{{ outcome.name }}</span>
              <span class="pmo-price" :class="outcome.price >= 0.5 ? 'yes' : 'no'">
                {{ (outcome.price * 100).toFixed(0) }}¢
              </span>
            </div>
          </div>
        </div>
      </section>

      <!-- RIGHT: Market detail + chart + order panel -->
      <aside class="poly-detail" v-if="activeMarket">
        <div class="pd-header">
          <h3 class="pd-question">{{ activeMarket.question }}</h3>
          <span class="pd-category">{{ activeMarket.category }}</span>
        </div>

        <!-- Probability chart -->
        <div class="pd-chart-wrap">
          <PolyChart
            :market-id="activeMarket.id || activeMarket.condition_id || ''"
            :live-price="activeMarketYesPrice"
          />
        </div>

        <div class="pd-outcomes">
          <div
            v-for="outcome in activeMarket.outcomes"
            :key="outcome.name"
            class="pd-outcome-card"
          >
            <span class="pdo-name">{{ outcome.name }}</span>
            <span class="pdo-price" :class="outcome.price >= 0.5 ? 'yes' : 'no'">
              {{ (outcome.price * 100).toFixed(1) }}¢
            </span>
            <div class="pdo-actions">
              <button class="pdo-buy yes-btn" @click="placeOrder(outcome.name, 'buy')">Buy Yes</button>
              <button class="pdo-buy no-btn" @click="placeOrder(outcome.name, 'sell')">Buy No</button>
            </div>
          </div>
        </div>

        <div class="pd-stats">
          <div class="pds-item">
            <span class="pds-label">Volume</span>
            <span class="pds-value">${{ formatVolume(activeMarket.volume) }}</span>
          </div>
          <div class="pds-item">
            <span class="pds-label">Liquidity</span>
            <span class="pds-value">${{ formatVolume(activeMarket.liquidity) }}</span>
          </div>
          <div class="pds-item" v-if="activeMarket.end_date">
            <span class="pds-label">End Date</span>
            <span class="pds-value">{{ formatDate(activeMarket.end_date) }}</span>
          </div>
        </div>

        <!-- AI Signal -->
        <div class="pd-ai-section">
          <div class="pd-section-header">
            <span>🤖 AI Analysis</span>
            <button class="pd-refresh-btn" @click="fetchAI" :disabled="aiLoading">
              {{ aiLoading ? '⟳ Analyzing…' : '↻ Refresh' }}
            </button>
          </div>
          <div v-if="aiError" class="pd-ai-error">{{ aiError }}</div>
          <div v-else-if="aiResult" class="pd-ai-result">
            <div class="pd-ai-signal-row">
              <span :class="['pd-ai-signal', `signal-${aiResult.signal?.toLowerCase()}`]">
                {{ aiResult.signal === 'YES' ? '▲ YES' : aiResult.signal === 'NO' ? '▼ NO' : '● NEUTRAL' }}
              </span>
              <span class="pd-ai-conf">{{ aiResult.confidence }}% confidence</span>
              <span :class="['pd-ai-action', aiResult.suggested_action?.includes('YES') ? 'yes' : aiResult.suggested_action?.includes('NO') ? 'no' : 'hold']">
                {{ aiResult.suggested_action || 'HOLD' }}
              </span>
            </div>
            <p class="pd-ai-reasoning">{{ aiResult.reasoning }}</p>
            <div class="pd-ai-factors" v-if="aiResult.key_factors?.length">
              <span v-for="f in aiResult.key_factors" :key="f" class="pd-ai-tag">{{ f }}</span>
            </div>
          </div>
          <div v-else class="pd-ai-placeholder">Click Refresh to get AI analysis</div>
        </div>

        <!-- News Feed -->
        <div class="pd-news-section">
          <div class="pd-section-header">
            <span>📰 News</span>
            <button class="pd-refresh-btn" @click="fetchNews" :disabled="newsLoading">
              {{ newsLoading ? '⟳ Loading…' : '↻ Refresh' }}
            </button>
          </div>
          <div v-if="newsError" class="pd-news-error">{{ newsError }}</div>
          <div v-else-if="newsArticles.length" class="pd-news-list">
            <a
              v-for="article in newsArticles"
              :key="article.url"
              :href="article.url"
              target="_blank"
              rel="noopener noreferrer"
              class="pd-news-item"
            >
              <span class="pni-source">{{ article.source || 'news' }}</span>
              <span class="pni-title">{{ article.title }}</span>
            </a>
          </div>
          <div v-else class="pd-ai-placeholder">Click Refresh to load news</div>
        </div>

        <!-- Portfolio positions for this market -->
        <div class="pd-positions" v-if="marketPositions.length">
          <div class="pd-pos-header">YOUR POSITIONS</div>
          <div v-for="pos in marketPositions" :key="pos.outcome" class="pd-pos-row">
            <span class="pdp-outcome">{{ pos.outcome }}</span>
            <span class="pdp-shares">{{ pos.shares }} shares</span>
            <span class="pdp-avg">Avg {{ (pos.avg_price * 100).toFixed(1) }}¢</span>
            <span class="pdp-pnl" :class="pos.current_price >= pos.avg_price ? 'up' : 'dn'">
              {{ ((pos.current_price - pos.avg_price) * pos.shares * 100).toFixed(0) }}¢
            </span>
          </div>
        </div>
      </aside>

      <aside class="poly-detail poly-detail-empty" v-else>
        <p>Select a market to view details and trade</p>
      </aside>
    </div>
  </div>
</template>

<script setup>
import { ref, computed, onMounted, onUnmounted } from 'vue'
import { storeToRefs } from 'pinia'
import { usePolyStore } from '../../stores/usePolyStore'
import {
  getPolyMarkets, getPolyPortfolio, getPolyBalance, placePolyOrder,
  getPolyStatus, togglePolyAutoTrading, getPolyNews, getPolyAIAnalysis,
  getPnlSummary,
} from '../../api/market'
import PolyChart from '../chart/PolyChart.vue'

const store = usePolyStore()
const {
  markets, activeMarket, category, portfolio, balance,
  searchQuery, sortBy,
  loading, connected, filteredMarkets,
} = storeToRefs(store)

const autoTrading = ref(false)
const segPnl      = ref({ trades: 0, gross: 0, brokerage: 0, net: 0 })

// AI & News state
const aiLoading    = ref(false)
const aiError      = ref(null)
const aiResult     = ref(null)
const newsLoading  = ref(false)
const newsError    = ref(null)
const newsArticles = ref([])

const categories = [
  { label: 'All',       value: 'all' },
  { label: 'Politics',  value: 'politics' },
  { label: 'Crypto',    value: 'crypto' },
  { label: 'Sports',    value: 'sports' },
  { label: 'Science',   value: 'science' },
  { label: 'Culture',   value: 'culture' },
]

// YES outcome price for the active market (feeds PolyChart seed)
const activeMarketYesPrice = computed(() => {
  if (!activeMarket.value?.outcomes) return null
  const yes = activeMarket.value.outcomes.find(o => o.name?.toLowerCase() === 'yes')
  return yes?.price ?? activeMarket.value.outcomes[0]?.price ?? null
})

// ── Positions filtered to active market ──────────────────────────────────────

const marketPositions = computed(() => {
  if (!activeMarket.value) return []
  const id = activeMarket.value.id || activeMarket.value.condition_id || ''
  return portfolio.value.filter(p => p.market_id === id)
})

// ── Status / auto-trading ─────────────────────────────────────────────────────

async function loadStatus() {
  try {
    const res = await getPolyStatus()
    const d = res.data?.data || res.data || {}
    autoTrading.value = d.auto_trade === true
  } catch {}
}

async function toggleAuto() {
  try {
    await togglePolyAutoTrading(autoTrading.value)
  } catch {
    autoTrading.value = !autoTrading.value
  }
}

// ── AI & News ─────────────────────────────────────────────────────────────────

async function fetchAI() {
  if (!activeMarket.value) return
  const id = activeMarket.value.id || activeMarket.value.condition_id || ''
  aiLoading.value = true
  aiError.value   = null
  try {
    const res = await getPolyAIAnalysis(id)
    const d = res.data?.data || res.data || {}
    aiResult.value = d.analysis || null
    if (!aiResult.value) aiError.value = 'No analysis returned'
  } catch (e) {
    aiError.value = e?.message || 'AI analysis failed'
  } finally {
    aiLoading.value = false
  }
}

async function fetchNews() {
  if (!activeMarket.value) return
  const id = activeMarket.value.id || activeMarket.value.condition_id || ''
  newsLoading.value = true
  newsError.value   = null
  try {
    const res = await getPolyNews(id)
    const d = res.data?.data || res.data || {}
    newsArticles.value = d.articles || []
  } catch (e) {
    newsError.value = e?.message || 'Failed to load news'
  } finally {
    newsLoading.value = false
  }
}

// ── Market selection ──────────────────────────────────────────────────────────

function selectMarket(market) {
  store.selectMarket(market)
  aiResult.value     = null
  aiError.value      = null
  newsArticles.value = []
  newsError.value    = null
}

// ── Load markets ──────────────────────────────────────────────────────────────

async function loadMarkets() {
  loading.value = true
  try {
    const res = await getPolyMarkets({ category: category.value, sort: sortBy.value })
    markets.value   = res.data?.data || res.data || []
    connected.value = true
  } catch {
    connected.value = false
  } finally {
    loading.value = false
  }
}

// ── Load portfolio + balance ──────────────────────────────────────────────────

async function loadPortfolio() {
  try {
    const [portRes, balRes] = await Promise.all([getPolyPortfolio(), getPolyBalance()])
    portfolio.value = (portRes.data?.data || portRes.data || []).map(p => ({
      market_id:     p.symbol,
      outcome:       p.side,
      shares:        p.qty,
      avg_price:     p.avg_entry,
      current_price: p.avg_entry,
    }))
    const bal = balRes.data?.data || balRes.data || {}
    balance.value   = bal.free ?? bal.balance ?? 0
    connected.value = true
  } catch {}
}

// ── Paper order placement ─────────────────────────────────────────────────────

async function placeOrder(outcome, side) {
  if (!activeMarket.value) return

  const marketId = activeMarket.value.id || activeMarket.value.condition_id || ''
  const apiSide  = side === 'buy' ? 'BUY' : 'SELL'

  const outcomeData = activeMarket.value.outcomes?.find(o => o.name === outcome)
  const price = outcomeData?.price || 0

  if (!confirm(`PAPER ${apiSide} ${outcome} @ ${(price * 100).toFixed(1)}¢ × 100 shares`)) return

  try {
    const res = await placePolyOrder({
      market:     marketId,
      side:       apiSide,
      qty:        100,
      price:      price,
      order_type: 'LIMIT',
    })
    if (res.data?.success) await loadPortfolio()
  } catch (e) {
    console.error('Poly order failed:', e)
  }
}

// ── Formatters ────────────────────────────────────────────────────────────────

function formatVolume(v) {
  if (v == null) return '—'
  if (v >= 1e6) return (v / 1e6).toFixed(1) + 'M'
  if (v >= 1e3) return (v / 1e3).toFixed(1) + 'K'
  return v?.toFixed(0) ?? '—'
}

function formatDate(d) {
  if (!d) return ''
  return new Date(d).toLocaleDateString('en-IN', { month: 'short', day: 'numeric', year: 'numeric' })
}

async function loadSegmentPnl() {
  try {
    const res = await getPnlSummary({ market_type: 'poly' })
    const segs = res?.segments || res?.data?.segments || {}
    segPnl.value = segs.poly || { trades: 0, gross: 0, brokerage: 0, net: 0 }
  } catch {}
}

// ── Lifecycle ─────────────────────────────────────────────────────────────────

let segPnlTimer = null

onMounted(() => {
  loadStatus()
  loadMarkets()
  loadPortfolio()
  loadSegmentPnl()
  segPnlTimer = setInterval(loadSegmentPnl, 30_000)
  window.addEventListener('vega:pnl-changed', loadSegmentPnl)
})

onUnmounted(() => {
  clearInterval(segPnlTimer)
  window.removeEventListener('vega:pnl-changed', loadSegmentPnl)
})
</script>

<style src="../../styles/PolyPanel.css"></style>

<style scoped>
.poly-title-left { display: flex; align-items: center; gap: 8px; }
.poly-controls   { display: flex; align-items: center; gap: 10px; margin-left: auto; }

.mode-badge {
  font-size: 10px; font-weight: 700; letter-spacing: 1px;
  padding: 2px 7px; border-radius: 3px;
}
.mode-badge.paper { background: #f59e0b; color: #000; }
.mode-badge.live  { background: #ff4757; color: #fff; }

.poly-toggle {
  display: flex; align-items: center; gap: 6px; cursor: pointer;
}
.poly-toggle input { display: none; }
.poly-toggle-slider {
  width: 30px; height: 16px; background: #2a2a3e; border-radius: 8px;
  position: relative; transition: background 0.2s;
}
.poly-toggle input:checked + .poly-toggle-slider { background: #00d4a8; }
.poly-toggle-slider::after {
  content: ''; position: absolute; width: 12px; height: 12px;
  background: #fff; border-radius: 50%; top: 2px; left: 2px; transition: left 0.2s;
}
.poly-toggle input:checked + .poly-toggle-slider::after { left: 16px; }
.poly-toggle-label { font-size: 11px; color: #8b8fa8; }

.pd-chart-wrap {
  height: 180px;
  margin-bottom: 12px;
  border-radius: 4px;
  overflow: hidden;
}

.poly-pnl-strip {
  display: flex; align-items: center; gap: 12px;
  padding: 5px 12px; background: #0e0e20; border-bottom: 1px solid #1a1a2e;
  font-size: 11px;
}
.ppnl-label  { color: #4a4e6a; text-transform: uppercase; font-size: 10px; }
.ppnl-net    { font-weight: 600; }
.ppnl-trades { color: #8b8fa8; font-size: 10px; }
.ppnl-brk    { color: #6e7681; font-size: 10px; }
.ppnl-paper  {
  font-size: 9px; background: #f59e0b; color: #000;
  padding: 1px 5px; border-radius: 3px; margin-left: auto; font-weight: 700;
}
.up { color: #00d4a8; }
.dn { color: #ff4757; }

/* AI & News shared */
.pd-ai-section,
.pd-news-section {
  margin-top: 12px;
  border: 1px solid #1a1a2e;
  border-radius: 4px;
  overflow: hidden;
}

.pd-section-header {
  display: flex; align-items: center; justify-content: space-between;
  padding: 6px 10px; background: #111125;
  font-size: 11px; font-weight: 600; color: #8b8fa8;
  border-bottom: 1px solid #1a1a2e;
}

.pd-refresh-btn {
  font-size: 10px; color: #00d4a8; background: transparent;
  border: 1px solid #00d4a833; border-radius: 3px;
  padding: 2px 8px; cursor: pointer; transition: background 0.15s;
}
.pd-refresh-btn:hover:not(:disabled) { background: #00d4a822; }
.pd-refresh-btn:disabled { opacity: 0.4; cursor: not-allowed; }

/* AI result */
.pd-ai-result { padding: 10px; }
.pd-ai-signal-row { display: flex; align-items: center; gap: 10px; margin-bottom: 8px; }
.pd-ai-signal {
  font-size: 13px; font-weight: 700; padding: 3px 10px; border-radius: 4px;
}
.signal-yes     { background: #00d4a822; color: #00d4a8; }
.signal-no      { background: #ff475722; color: #ff4757; }
.signal-neutral { background: #f59e0b22; color: #f59e0b; }

.pd-ai-conf { font-size: 11px; color: #6b6f8a; }
.pd-ai-action {
  font-size: 10px; font-weight: 700; padding: 2px 7px; border-radius: 3px;
  margin-left: auto;
}
.pd-ai-action.yes  { background: #00d4a8; color: #000; }
.pd-ai-action.no   { background: #ff4757; color: #fff; }
.pd-ai-action.hold { background: #2a2a3e; color: #8b8fa8; }

.pd-ai-reasoning { font-size: 11px; color: #a0a4b8; line-height: 1.5; margin: 0 0 8px; }
.pd-ai-factors { display: flex; flex-wrap: wrap; gap: 4px; }
.pd-ai-tag {
  font-size: 10px; background: #1a1a2e; color: #6b6f8a;
  padding: 2px 6px; border-radius: 3px;
}

.pd-ai-placeholder,
.pd-ai-error,
.pd-news-error {
  padding: 10px; font-size: 11px; color: #4a4e6a; text-align: center;
}
.pd-ai-error, .pd-news-error { color: #ff4757; }

/* News */
.pd-news-list { display: flex; flex-direction: column; max-height: 220px; overflow-y: auto; }
.pd-news-item {
  display: flex; flex-direction: column; gap: 2px;
  padding: 7px 10px; border-bottom: 1px solid #1a1a2e;
  text-decoration: none; transition: background 0.1s;
}
.pd-news-item:last-child { border-bottom: none; }
.pd-news-item:hover { background: #111125; }
.pni-source { font-size: 9px; color: #4a4e6a; text-transform: uppercase; letter-spacing: 0.5px; }
.pni-title  { font-size: 11px; color: #c0c4d8; line-height: 1.4; }
</style>
