<template>
  <div class="wl-list wl-budget">
    <div class="bud-loading" v-if="budgetLoading && !budgetData">
      <span class="spinner"></span> Loading…
    </div>
    <template v-else-if="budgetData">
      <!-- Per-period tiles (persisted to SQLite — survives restarts).
           Click a tile to filter the breakdowns below. -->
      <div class="bud-periods">
        <div
          v-for="p in PERIODS"
          :key="p.k"
          :class="['bud-period-tile', { active: budgetPeriod === p.k }]"
          @click="budgetPeriod = p.k"
          :title="`Switch breakdowns to ${p.l}`"
        >
          <div class="bud-label">{{ p.l }}</div>
          <div class="bud-val bud-cost">
            {{ fmtUSD(budgetData[p.k]?.total_cost_usd || 0) }}
          </div>
          <div class="bud-sub">
            {{ fmtINR(budgetData[p.k]?.total_cost_usd || 0) }}
          </div>
          <div class="bud-sub">
            {{ budgetData[p.k]?.total_calls || 0 }} calls ·
            {{ fmtTokens(budgetData[p.k]?.total_tokens) }}
          </div>
        </div>
      </div>

      <!-- By model — uses the period selected via tile above -->
      <div class="bud-section-label">BY MODEL · {{ budgetPeriodLabel }}</div>
      <div
        v-for="(m, name) in budgetData[budgetPeriod]?.by_model || {}"
        :key="name"
        class="bud-row"
      >
        <div class="bud-row-name mono">{{ name }}</div>
        <div class="bud-row-meta">
          <span>{{ m.calls }} calls</span>
          <span>{{ fmtTokens(m.tokens) }} tok</span>
          <span class="bud-cost">{{ fmtUSD(m.cost_usd) }}</span>
        </div>
      </div>

      <!-- By agent -->
      <div class="bud-section-label">BY AGENT · {{ budgetPeriodLabel }}</div>
      <div
        v-for="(a, name) in budgetData[budgetPeriod]?.by_agent || {}"
        :key="name"
        class="bud-row"
      >
        <div class="bud-row-name">{{ name }}</div>
        <div class="bud-row-meta">
          <span>{{ a.calls }} calls</span>
          <span>{{ fmtTokens(a.tokens) }} tok</span>
          <span class="bud-cost">{{ fmtUSD(a.cost_usd) }}</span>
        </div>
      </div>

      <!-- Recent calls (last 20 in the selected period) -->
      <div
        class="bud-section-label"
        v-if="(budgetData[budgetPeriod]?.recent_entries || []).length"
      >
        RECENT CALLS · {{ budgetPeriodLabel }}
      </div>
      <div
        v-for="(e, i) in budgetData[budgetPeriod]?.recent_entries || []"
        :key="i"
        class="bud-recent"
      >
        <span class="bud-recent-agent">{{ e.agent_id }}</span>
        <span class="bud-recent-tokens mono">{{ fmtTokens(e.total_tokens) }}</span>
        <span class="bud-recent-cost mono">{{ fmtUSD(e.cost_usd) }}</span>
      </div>

      <!-- Actions -->
      <div class="bud-actions">
        <button
          class="wl-watch-btn"
          @click="refreshBudget"
          title="Refresh budget data — auto-refreshes every 30s while this tab is open."
        >
          ⟳ Refresh
        </button>
        <button
          class="wl-watch-btn wl-watch-rm-btn"
          @click="clearBudget"
          title="Reset all token counters to zero. Use this at the start of a new session to track only that session's spend."
        >
          ✕ Reset
        </button>
      </div>
    </template>
    <div class="feed-empty" v-else>No LLM activity yet.</div>
  </div>
</template>

<script setup>
import { computed, onMounted, onUnmounted } from 'vue'
import { storeToRefs } from 'pinia'
import { useMarketStore } from '../../stores/useMarketStore'
import { getBudget, resetBudget } from '../../api/market'

const store = useMarketStore()
const { budgetData, budgetLoading, budgetPeriod } = storeToRefs(store)

const PERIODS = [
  { k: 'today', l: 'Today (IST)' },
  { k: 'month', l: 'This Month' },
  { k: 'session', l: 'Session' },
  { k: 'all', l: 'All-Time' },
]

const budgetPeriodLabel = computed(
  () =>
    ({ today: 'Today', month: 'This Month', session: 'Session', all: 'All-Time' })[
      budgetPeriod.value
    ] || 'Today',
)

let _budgetTimer = null

async function refreshBudget() {
  budgetLoading.value = true
  try {
    const r = await getBudget()
    budgetData.value = r?.data || null
  } catch (e) {
    console.error('Budget fetch failed', e)
  } finally {
    budgetLoading.value = false
  }
  if (_budgetTimer) clearInterval(_budgetTimer)
  _budgetTimer = setInterval(async () => {
    try {
      const r = await getBudget()
      budgetData.value = r?.data || null
    } catch {}
  }, 30_000)
}

async function clearBudget() {
  if (!confirm('Reset the LLM cost ledger? This zeros all counters.')) return
  try {
    await resetBudget()
    await refreshBudget()
  } catch (e) {
    alert('Reset failed: ' + (e?.message || e))
  }
}

function fmtUSD(v) {
  return '$' + (Number(v) || 0).toFixed(4)
}
function fmtINR(v) {
  return '₹' + ((Number(v) || 0) * 83).toFixed(2)
}
function fmtTokens(n) {
  n = Number(n) || 0
  if (n >= 1_000_000) return (n / 1_000_000).toFixed(2) + 'M'
  if (n >= 1_000) return (n / 1_000).toFixed(1) + 'K'
  return String(n)
}

onMounted(refreshBudget)
onUnmounted(() => {
  if (_budgetTimer) clearInterval(_budgetTimer)
})
</script>

<style src="../../styles/BudgetPanel.css"></style>
