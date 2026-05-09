<template>
  <div class="port-panel">
    <!-- ── Config card ─────────────────────────────────────────────────────── -->
    <div
      class="port-config-card"
      v-if="!portfolioLoading && !portfolioResult"
    >
      <!-- Capital -->
      <div class="pcc-section">
        <div class="pcc-label">💰 How much do you want to invest?</div>
        <div class="pcc-denom-row">
          <button
            v-for="d in portfolioDenoms"
            :key="d.val"
            :class="[
              'pcc-denom-btn',
              portfolioCapital === d.val && !portfolioCustomMode ? 'active' : '',
            ]"
            @click="portfolioCapital = d.val; portfolioCustomMode = false;"
          >
            {{ d.label }}
          </button>
          <button
            :class="['pcc-denom-btn', portfolioCustomMode ? 'active' : '']"
            @click="portfolioCustomMode = true; nextTick(() => customInput?.focus());"
          >
            Custom
          </button>
        </div>
        <div class="pcc-custom-row" v-if="portfolioCustomMode">
          <span class="pcc-rupee">₹</span>
          <input
            ref="customInput"
            v-model.number="portfolioCapital"
            type="number"
            min="100"
            step="100"
            class="pcc-custom-input"
            placeholder="Enter amount"
          />
          <span class="pcc-custom-hint">{{
            portfolioCapital ? "= " + formatCapital(portfolioCapital) : ""
          }}</span>
        </div>
        <div class="pcc-capital-display" v-else>
          <span class="pcc-amt">₹{{ portfolioCapital.toLocaleString("en-IN") }}</span>
          <span class="pcc-amt-words">{{ formatCapital(portfolioCapital) }}</span>
        </div>
      </div>

      <!-- Timeline -->
      <div class="pcc-section">
        <div class="pcc-label">📅 Investment horizon</div>
        <div class="pcc-timeline-row">
          <button
            v-for="h in portfolioHorizons"
            :key="h.val"
            :class="['pcc-hz-btn', portfolioHorizon === h.val ? 'active' : '']"
            @click="portfolioHorizon = h.val"
          >
            <span class="pcc-hz-dur">{{ h.label }}</span>
            <span class="pcc-hz-desc">{{ h.desc }}</span>
          </button>
        </div>
      </div>

      <!-- Market scope -->
      <div class="pcc-section">
        <div class="pcc-label">🎯 Market focus</div>
        <div class="pcc-scope-row">
          <button
            v-for="s in portfolioScopes"
            :key="s.val"
            :class="['pcc-scope-btn', portfolioScope === s.val ? 'active' : '']"
            @click="portfolioScope = s.val"
          >
            <span class="pcc-scope-icon">{{ s.icon }}</span>
            <span class="pcc-scope-name">{{ s.label }}</span>
            <span class="pcc-scope-sub">{{ s.sub }}</span>
          </button>
        </div>
      </div>

      <!-- CTA -->
      <div class="pcc-cta-row">
        <button class="port-run-btn" @click="runPortfolio">
          <span>🤖</span>
          Build My Portfolio
        </button>
        <div class="pcc-cta-meta">
          30 global agents · 3 debate rounds · live market data
        </div>
      </div>
    </div>

    <!-- ── Running state ────────────────────────────────────────────────────── -->
    <div class="port-running" v-if="portfolioLoading">
      <div class="prn-top">
        <div class="prn-capital">
          ₹{{ portfolioCapital.toLocaleString("en-IN") }}
        </div>
        <div class="prn-meta">
          {{ portfolioHorizons.find((h) => h.val === portfolioHorizon)?.label }}
          ·
          {{ portfolioScopes.find((s) => s.val === portfolioScope)?.label }}
        </div>
      </div>
      <div class="prn-rounds">
        <div
          :class="['prn-round', portfolioRound >= 1 ? 'done' : '']"
        >
          <span class="prn-r-num">1</span>
          <span class="prn-r-label">Independent picks</span>
        </div>
        <div class="prn-arrow">→</div>
        <div
          :class="[
            'prn-round',
            portfolioRound >= 2 ? 'done' : portfolioRound === 1 ? 'active' : '',
          ]"
        >
          <span class="prn-r-num">2</span>
          <span class="prn-r-label">Cross-debate</span>
        </div>
        <div class="prn-arrow">→</div>
        <div
          :class="[
            'prn-round',
            portfolioRound >= 3 ? 'done' : portfolioRound === 2 ? 'active' : '',
          ]"
        >
          <span class="prn-r-num">3</span>
          <span class="prn-r-label">PM synthesis</span>
        </div>
      </div>
      <div class="port-feed">
        <div
          v-for="(ev, i) in [...portfolioFeed].reverse().slice(0, 14)"
          :key="i"
          :class="['pf-item', 'pf-' + ev.type]"
        >
          <span v-if="ev.type === 'round_start'" class="pf-round-badge">{{ ev.label }}</span>
          <template v-else-if="ev.type === 'agent_action'">
            <span class="pf-agent">{{ ev.agent }}</span>
            <span class="pf-city" v-if="ev.city">· {{ ev.city }}</span>
            <span class="pf-msg">{{
              ev.msg
                ?.replace(ev.agent + " (" + ev.city + "): ", "")
                .replace(ev.agent + ": ", "")
            }}</span>
          </template>
          <span v-else class="pf-msg">{{ ev.msg }}</span>
        </div>
      </div>
    </div>

    <!-- ── Results ──────────────────────────────────────────────────────────── -->
    <div class="port-results" v-if="portfolioResult && !portfolioLoading">
      <div class="prr-topbar">
        <div class="prr-meta">
          <span class="prr-capital">₹{{ portfolioCapital.toLocaleString("en-IN") }}</span>
          <span class="prr-dot">·</span>
          <span>{{ portfolioHorizons.find((h) => h.val === portfolioHorizon)?.label }}</span>
          <span class="prr-dot">·</span>
          <span>{{ portfolioScopes.find((s) => s.val === portfolioScope)?.label }}</span>
          <span class="prr-dot">·</span>
          <span>30 agents · 3 rounds</span>
        </div>
        <button
          class="prr-reset-btn"
          @click="portfolioResult = null; portfolioError = '';"
        >
          ← New Simulation
        </button>
      </div>

      <div class="port-stats" v-if="portfolioResult.portfolio">
        <div class="pst-i">
          <div class="pst-v">{{ portfolioResult.portfolio.diversification_score || "—" }}</div>
          <div class="pst-l">Diversification</div>
        </div>
        <div class="pst-i">
          <div class="pst-v up">+{{ portfolioResult.portfolio.expected_return_pct || "—" }}%</div>
          <div class="pst-l">Expected Return</div>
        </div>
        <div class="pst-i">
          <div class="pst-v dn">-{{ portfolioResult.portfolio.max_risk_pct || "—" }}%</div>
          <div class="pst-l">Max Risk</div>
        </div>
        <div class="pst-i">
          <div class="pst-v">
            ₹{{ Math.round(portfolioResult.portfolio.cash_kept || 0).toLocaleString("en-IN") }}
          </div>
          <div class="pst-l">Cash Buffer</div>
        </div>
      </div>

      <div class="prr-summary" v-if="portfolioResult.portfolio?.summary">
        {{ portfolioResult.portfolio.summary }}
      </div>

      <div
        class="port-table-wrap"
        v-if="portfolioResult.portfolio?.portfolio?.length"
      >
        <table class="port-table">
          <thead>
            <tr>
              <th>Asset Class</th>
              <th>Stock / Fund</th>
              <th>Allocation</th>
              <th>Amount</th>
              <th>Entry</th>
              <th>Stop Loss</th>
              <th>Target 1</th>
              <th>Target 2</th>
              <th>Timeframe</th>
              <th>Confidence</th>
            </tr>
          </thead>
          <tbody>
            <tr
              v-for="p in portfolioResult.portfolio.portfolio"
              :key="p.ticker"
              :class="['port-row', p.ticker === 'CASH' ? 'port-row-cash' : '']"
            >
              <td>
                <span
                  class="port-ac-tag"
                  :class="
                    'pac-' +
                    p.asset_class
                      ?.toLowerCase()
                      .replace(/\s+/g, '-')
                      .replace(/[^a-z-]/g, '')
                  "
                >
                  {{ p.asset_class }}
                </span>
              </td>
              <td
                class="port-ticker"
                @click="p.ticker !== 'CASH' && emit('select-ticker', p.ticker)"
                :class="{ 'cur-pointer': p.ticker !== 'CASH' }"
              >
                {{ p.ticker?.split(".")[0] }}
              </td>
              <td>
                <div class="port-alloc-bar-wrap">
                  <div
                    class="port-alloc-bar"
                    :style="{ width: Math.min(p.allocation_pct || 0, 100) + '%' }"
                  ></div>
                  <span>{{ p.allocation_pct }}%</span>
                </div>
              </td>
              <td>₹{{ Math.round(p.allocation_amt || 0).toLocaleString("en-IN") }}</td>
              <td class="mono">{{ p.entry ? "₹" + p.entry : "—" }}</td>
              <td class="mono dn">{{ p.stop_loss ? "₹" + p.stop_loss : "—" }}</td>
              <td class="mono up">{{ p.target_1 ? "₹" + p.target_1 : "—" }}</td>
              <td class="mono up">{{ p.target_2 ? "₹" + p.target_2 : "—" }}</td>
              <td class="port-tf">{{ p.timeframe }}</td>
              <td>
                <span
                  class="port-conf"
                  :class="
                    (p.confidence || 0) >= 75
                      ? 'conf-hi'
                      : (p.confidence || 0) >= 55
                        ? 'conf-mid'
                        : 'conf-lo'
                  "
                >
                  {{ p.confidence || "—" }}%
                </span>
              </td>
            </tr>
          </tbody>
        </table>

        <div class="port-rationale-grid">
          <div
            v-for="p in portfolioResult.portfolio.portfolio.filter((x) => x.ticker !== 'CASH')"
            :key="'r' + p.ticker"
            class="port-rat-card"
          >
            <div class="prc-header">
              <b>{{ p.ticker?.split(".")[0] }}</b>
              <span
                class="port-conf"
                :class="(p.confidence || 0) >= 75 ? 'conf-hi' : 'conf-mid'"
              >{{ p.verdict }}</span>
              <span class="prc-agent">{{ p.agent }}</span>
            </div>
            <div class="prc-body">{{ p.rationale }}</div>
          </div>
        </div>
      </div>
    </div>

    <div class="port-error" v-if="portfolioError">
      <span>⚠ {{ portfolioError }}</span>
      <button
        class="prr-reset-btn"
        @click="portfolioError = ''; portfolioResult = null;"
      >
        Try again
      </button>
    </div>
  </div>
</template>

<script setup>
import { ref, nextTick, onUnmounted } from 'vue'
import { storeToRefs } from 'pinia'
import { useLiveTradingStore } from '../../stores/useLiveTradingStore'
import { createPortfolioSimStream } from '../../api/market'

const emit = defineEmits(['select-ticker'])

const store = useLiveTradingStore()
const {
  portfolioCapital,
  portfolioHorizon,
  portfolioScope,
  portfolioLoading,
  portfolioResult,
  portfolioError,
  portfolioFeed,
  portfolioRound,
} = storeToRefs(store)

const portfolioCustomMode = ref(false)
const customInput = ref(null)

let _portEventSource = null

const portfolioDenoms = [
  { val: 1000, label: '₹1K' },
  { val: 5000, label: '₹5K' },
  { val: 10000, label: '₹10K' },
  { val: 25000, label: '₹25K' },
  { val: 50000, label: '₹50K' },
  { val: 100000, label: '₹1L' },
  { val: 500000, label: '₹5L' },
  { val: 1000000, label: '₹10L' },
]

const portfolioHorizons = [
  { val: '1w', label: '1 Week', desc: 'Intraday swing' },
  { val: '2w', label: '2 Weeks', desc: 'Momentum play' },
  { val: '1m', label: '1 Month', desc: 'Event driven' },
  { val: '3m', label: '3 Months', desc: 'Sector rotation' },
  { val: '6m', label: '6 Months', desc: 'Growth + value' },
  { val: '1y', label: '1 Year', desc: 'Compounders' },
  { val: '3y', label: '3 Years', desc: 'Buy & hold' },
]

const portfolioScopes = [
  { val: 'all', icon: '🌍', label: 'Global Mix', sub: 'All asset classes' },
  { val: 'large_cap', icon: '📊', label: 'Large Cap', sub: 'Nifty 50 stocks' },
  { val: 'mid_cap', icon: '🚀', label: 'Mid / Small', sub: 'Growth stocks' },
  { val: 'etf_index', icon: '📈', label: 'ETF & Index', sub: 'Passive / factor' },
  { val: 'commodity', icon: '🪙', label: 'Commodities', sub: 'Gold, Silver, Oil' },
  { val: 'reit_invit', icon: '🏢', label: 'REIT / InvIT', sub: 'Real estate infra' },
]

function formatCapital(n) {
  if (!n) return ''
  if (n >= 10000000) return (n / 10000000).toFixed(1) + ' Cr'
  if (n >= 100000) return (n / 100000).toFixed(1) + ' Lakh'
  if (n >= 1000) return (n / 1000).toFixed(0) + ' Thousand'
  return '₹' + n
}

function runPortfolio() {
  if (portfolioLoading.value) return
  if (_portEventSource) {
    _portEventSource.close()
    _portEventSource = null
  }

  portfolioLoading.value = true
  portfolioError.value = ''
  portfolioResult.value = null
  portfolioFeed.value = []
  portfolioRound.value = 0

  const es = createPortfolioSimStream(
    portfolioCapital.value,
    portfolioHorizon.value,
    portfolioScope.value,
  )
  _portEventSource = es

  es.onmessage = (event) => {
    try {
      const ev = JSON.parse(event.data)
      if (ev.type === 'heartbeat') return
      if (ev.type === 'round_start') portfolioRound.value = ev.round
      if (['agent_action', 'round_start', 'round_end', 'status'].includes(ev.type))
        portfolioFeed.value.push(ev)
      if (ev.type === 'completed') {
        portfolioResult.value = ev.portfolio
        portfolioLoading.value = false
        es.close()
      }
      if (ev.type === 'error') {
        portfolioError.value = ev.msg || 'Simulation error'
        portfolioLoading.value = false
        es.close()
      }
    } catch (_) {}
  }

  es.onerror = () => {
    if (portfolioLoading.value) {
      portfolioError.value = 'Connection lost — try again'
      portfolioLoading.value = false
    }
    es.close()
  }
}

onUnmounted(() => {
  if (_portEventSource) {
    _portEventSource.close()
    _portEventSource = null
  }
})
</script>

<style src="../../styles/PortfolioAllocator.css"></style>
