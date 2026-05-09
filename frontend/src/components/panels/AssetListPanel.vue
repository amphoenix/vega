<template>
  <div class="wl-content">
    <div class="wl-header">
      <div class="asset-tabs">
        <button
          v-for="ac in assetClasses"
          :key="ac.v"
          :class="['ac-btn', { active: assetClassFilter === ac.v }]"
          @click="switchAssetClass(ac.v)"
        >
          {{ ac.l }}
        </button>
      </div>
      <div class="country-filters" v-if="assetClassFilter !== 'scanner'">
        <button
          v-for="c in activeCountries"
          :key="c"
          :class="['cf-btn', { active: countryFilter === c }]"
          @click="countryFilter = c"
        >
          {{ c }}
        </button>
      </div>
      <div class="scanner-controls" v-if="assetClassFilter === 'scanner'">
        <select class="scan-mkt-sel" v-model="scanMarket">
          <option value="india">🇮🇳 India</option>
          <option value="america">🇺🇸 US</option>
          <option value="both">🌐 Both</option>
        </select>
        <button class="scan-btn" @click="runScanner" :disabled="scanLoading">
          <span v-if="scanLoading" class="spinner"></span>
          <span v-else>⟳</span> Scan
        </button>
      </div>
    </div>

    <div
      class="wl-loading"
      v-if="scanLoading || (indicesLoading && assetClassFilter !== 'scanner')"
    >
      <span class="spinner"></span>
      {{ scanLoading ? 'Scanning universe…' : 'Fetching markets…' }}
    </div>

    <!-- Scanner results -->
    <div class="wl-list" v-else-if="assetClassFilter === 'scanner'">
      <div class="scan-section-label buy-label" v-if="scanBuys.length">▲ TOP BUY PICKS</div>
      <div
        v-for="item in scanBuys"
        :key="item.symbol"
        :class="['wl-row scan-buy-row', { selected: selectedIndex === item.symbol }]"
        @click="selectItem(item)"
      >
        <div class="wl-left">
          <span class="wl-flag">{{ countryFlag(item.country) }}</span>
          <div class="wl-names">
            <span class="wl-name">{{ item.name }}</span>
            <span class="wl-exch">Entry {{ item.entry }} · Stop Loss {{ item.sl }} · Target {{ item.t1 }}</span>
          </div>
        </div>
        <div class="wl-right">
          <span :class="['wl-action', actionClass(item.action)]">{{ item.action }}</span>
          <span class="wl-score up scan-score-tip" :data-tip="scoreTip(item)">{{ item.score }}</span>
        </div>
      </div>
      <div class="scan-section-label sell-label" v-if="scanSells.length">▼ TOP SELL PICKS</div>
      <div
        v-for="item in scanSells"
        :key="item.symbol"
        :class="['wl-row scan-sell-row', { selected: selectedIndex === item.symbol }]"
        @click="selectItem(item)"
      >
        <div class="wl-left">
          <span class="wl-flag">{{ countryFlag(item.country) }}</span>
          <div class="wl-names">
            <span class="wl-name">{{ item.name }}</span>
            <span class="wl-exch">Entry {{ item.entry }} · Stop Loss {{ item.sl }} · Target {{ item.t1 }}</span>
          </div>
        </div>
        <div class="wl-right">
          <span :class="['wl-action', actionClass(item.action)]">{{ item.action }}</span>
          <span class="wl-score dn scan-score-tip" :data-tip="scoreTip(item)">{{ item.score }}</span>
        </div>
      </div>
      <div class="scan-section-label" v-if="scanHolds.length">— HOLD / WATCH</div>
      <div
        v-for="item in scanHolds"
        :key="item.symbol"
        :class="['wl-row', { selected: selectedIndex === item.symbol }]"
        @click="selectItem(item)"
      >
        <div class="wl-left">
          <span class="wl-flag">{{ countryFlag(item.country) }}</span>
          <div class="wl-names">
            <span class="wl-name">{{ item.name }}</span>
            <span class="wl-exch">RSI (momentum) {{ item.rsi }} · {{ item.trend }}</span>
          </div>
        </div>
        <div class="wl-right">
          <span :class="['wl-action', actionClass(item.action)]">{{ item.action }}</span>
          <span class="wl-score neu scan-score-tip" :data-tip="scoreTip(item)">{{ item.score }}</span>
        </div>
      </div>
      <div class="feed-empty scan-err" v-if="scanError && !scanLoading">⚠ {{ scanError }}</div>
      <div class="feed-empty" v-else-if="!scanResults.length && !scanLoading">
        Click Scan to find opportunities
      </div>
    </div>

    <!-- Watchlist tab -->
    <WatchlistPanel
      v-else-if="assetClassFilter === 'watchlist'"
      @select-ticker="(sym) => emit('select-ticker', sym)"
    />

    <!-- Budget tab -->
    <BudgetPanel v-else-if="assetClassFilter === 'budget'" />

    <!-- Normal asset list -->
    <div class="wl-list" v-else>
      <div
        v-for="idx in filteredAssets"
        :key="idx.symbol"
        :class="['wl-row', { selected: selectedIndex === idx.symbol }]"
        @click="selectItem(idx)"
      >
        <div class="wl-left">
          <span class="wl-flag">{{ assetIcon(idx) }}</span>
          <div class="wl-names">
            <span class="wl-name">{{ idx.name }}</span>
            <span class="wl-exch">{{ idx.exchange }}</span>
          </div>
        </div>
        <div class="wl-right">
          <span class="wl-price">{{ idx.price ? fmtPrice(idx.price) : '—' }}</span>
          <span :class="['wl-chg', (idx.change_pct ?? idx.change_1d) >= 0 ? 'up' : 'dn']">
            {{ fmtChg(idx.change_pct ?? idx.change_1d) }}
          </span>
        </div>
      </div>
    </div>
  </div>
</template>

<script setup>
import { ref, computed, onMounted } from 'vue'
import { fmtPrice, fmtChg } from '../../utils/formatters'
import { getWorldIndices, scanUniverse } from '../../api/market'
import WatchlistPanel from './WatchlistPanel.vue'
import BudgetPanel from './BudgetPanel.vue'

const emit = defineEmits(['select-ticker', 'add-feed-items'])

// ── State ─────────────────────────────────────────────────────────────────────
const selectedIndex = ref('')
const countryFilter = ref('ALL')
const scanResults = ref([])
const scanMarket = ref('india')
const scanError = ref('')
const indicesLoading = ref(false)
const scanLoading = ref(false)
const assetClassFilter = ref('index')
const indices = ref([])
const allAssets = ref([])

let _feedCounter = 0

// ── Constants ─────────────────────────────────────────────────────────────────
const assetClasses = [
  { l: 'Indices', v: 'index' },
  { l: 'Stocks', v: 'stock' },
  { l: 'Commodities', v: 'commodity' },
  { l: 'ETFs', v: 'etf' },
  { l: '⟳ Scanner', v: 'scanner' },
  { l: '★ Watchlist', v: 'watchlist' },
  { l: '$ Budget', v: 'budget' },
]

const countryMap = {
  index: ['ALL', 'US', 'IN', 'UK', 'EU', 'JP', 'CN', 'HK', 'AU', 'KR', 'CA', 'BR'],
  stock: ['ALL', 'US', 'IN', 'UK', 'DE', 'JP', 'HK'],
  commodity: ['ALL'],
  etf: ['ALL', 'US', 'IN', 'CN', 'JP', 'EU', 'BR'],
  scanner: [],
  watchlist: [],
  budget: [],
}

// ── Computed ──────────────────────────────────────────────────────────────────
const activeCountries = computed(() => countryMap[assetClassFilter.value] || ['ALL'])

const scanBuys = computed(() => scanResults.value.filter((r) => r.action.includes('BUY')))
const scanSells = computed(() => scanResults.value.filter((r) => r.action.includes('SELL')))
const scanHolds = computed(() => scanResults.value.filter((r) => r.action === 'HOLD'))

const filteredAssets = computed(() => {
  const ac = assetClassFilter.value
  if (ac === 'scanner') return []
  let list = allAssets.value.filter((a) => a.asset_class === ac)
  if (countryFilter.value !== 'ALL') list = list.filter((a) => a.country === countryFilter.value)
  return list
})

// ── Helpers ───────────────────────────────────────────────────────────────────
function countryFlag(c) {
  const flags = {
    US: '🇺🇸', UK: '🇬🇧', DE: '🇩🇪', FR: '🇫🇷', EU: '🇪🇺',
    JP: '🇯🇵', CN: '🇨🇳', HK: '🇭🇰', IN: '🇮🇳', AU: '🇦🇺',
    KR: '🇰🇷', SG: '🇸🇬', CA: '🇨🇦', BR: '🇧🇷', MX: '🇲🇽', NL: '🇳🇱',
  }
  return flags[c] || '🌐'
}

function assetIcon(item) {
  if (item.asset_class === 'commodity') {
    const icons = {
      'GC=F': '🥇', 'SI=F': '🥈', 'CL=F': '🛢️', 'BZ=F': '🛢️',
      'NG=F': '🔥', 'HG=F': '🔧', 'ZW=F': '🌾', 'ZC=F': '🌽',
      'ZS=F': '🫘', 'KC=F': '☕', 'SB=F': '🍬', 'PL=F': '⚪', 'PA=F': '⚪',
    }
    return icons[item.symbol] || '📦'
  }
  if (item.asset_class === 'etf') return '📊'
  return countryFlag(item.country)
}

function actionClass(action) {
  if (!action) return ''
  if (action.includes('STRONG BUY')) return 'act-sbuy'
  if (action.includes('BUY')) return 'act-buy'
  if (action.includes('STRONG SELL')) return 'act-ssell'
  if (action.includes('SELL')) return 'act-sell'
  return 'act-hold'
}

function scoreTip(item) {
  const d = item.score_detail
  if (!d) return `Score: ${item.score} | Momentum: ${item.rsi} | Trend: ${item.trend} | Volume: ${item.volume_ratio}x avg | 5-day change: ${item.change_5d}%`
  return [
    `Overall Score: ${item.score}/100`,
    `Momentum (RSI ${item.rsi}) → ${d.rsi_score}/100 weight 30%`,
    `Price Trend (${item.trend}) → ${d.ema_score}/100 weight 25%`,
    `Volume spike ${item.volume_ratio}x avg → ${d.vol_score}/100 weight 25%`,
    `5-day price change ${item.change_5d}% → ${d.mom_score}/100 weight 20%`,
  ].join('\n')
}

// ── Data loading ──────────────────────────────────────────────────────────────
async function loadIndices() {
  indicesLoading.value = true
  try {
    const res = await getWorldIndices()
    const priced = res.data || []
    priced.forEach((p) => {
      const existing = allAssets.value.find((a) => a.symbol === p.symbol)
      if (existing) {
        existing.price = p.price
        existing.change_pct = p.change_pct
      } else {
        allAssets.value.push({ ...p, asset_class: 'index' })
      }
    })
    indices.value = priced
  } catch (e) {
    console.error('Indices load failed', e)
  } finally {
    indicesLoading.value = false
  }
}

function switchAssetClass(ac) {
  assetClassFilter.value = ac
  countryFilter.value = 'ALL'
}

async function runScanner() {
  scanLoading.value = true
  scanResults.value = []
  scanError.value = ''
  try {
    const res = await scanUniverse(scanMarket.value, 60)
    scanResults.value = res.data || []
    if (!scanResults.value.length) {
      scanError.value = 'No signals found — try a different market or try again shortly.'
    }
    const top5 = scanResults.value.slice(0, 5)
    const newItems = top5.map((item) => ({
      type: item.action.includes('BUY') ? 'prediction' : 'fomo',
      title: `${item.action}: ${item.name} (${item.symbol}) — Score ${item.score}/100`,
      meta: `RSI ${item.rsi} · 1D ${item.change_1d > 0 ? '+' : ''}${item.change_1d}% · Vol ${item.volume_ratio}x · Trend: ${item.trend}`,
      sentiment: item.score / 100,
    }))
    if (newItems.length) emit('add-feed-items', newItems)
  } catch (e) {
    console.error('Scanner failed', e)
    scanError.value = e.message || 'Scanner failed — check that the backend is running.'
  } finally {
    scanLoading.value = false
  }
}

function selectItem(idx) {
  selectedIndex.value = idx.symbol
  emit('select-ticker', idx.symbol)
}

onMounted(() => {
  loadIndices()
})
</script>

<style src="../../styles/AssetListPanel.css"></style>
