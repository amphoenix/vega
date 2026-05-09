<template>
  <div class="rs-panel">
    <div
      class="rs-header hdr-backtest rs-hdr-clickable"
      @click="btExpanded = !btExpanded"
    >
      <span class="rs-title">BACKTEST</span>
      <div class="rs-hdr-actions">
        <button
          class="rs-icon-btn"
          @click.stop="runBacktest"
          :disabled="btLoading || !chartTicker"
          title="Run backtest"
        >
          <span v-if="btLoading" class="spinner"></span>
          <span v-else>▶</span>
        </button>
        <span class="bt-chevron">{{ btExpanded ? "▲" : "▼" }}</span>
      </div>
    </div>
    <template v-if="btExpanded">
      <div class="rs-empty" v-if="!btResult && !btLoading && !btError">
        Test 4 strategies on 1 year of data
      </div>
      <div class="rs-err" v-if="btError && !btLoading">
        ⚠ {{ btError }}
      </div>
      <div class="rs-loading" v-if="btLoading">
        <span class="spinner"></span> Testing strategies…
      </div>
      <div v-if="btResult" class="bt-results">
        <div class="bt-bh-row">
          Buy &amp; hold:
          <b :class="btResult.buy_and_hold_pct >= 0 ? 'up' : 'dn'"
            >{{ btResult.buy_and_hold_pct >= 0 ? "+" : ""
            }}{{ btResult.buy_and_hold_pct }}%</b
          >
        </div>
        <div
          v-for="(s, name) in btResult.strategies"
          :key="name"
          :class="[
            'bt-card',
            name === btResult.best_strategy ? 'bt-winner' : '',
            s.error ? 'bt-err' : '',
          ]"
        >
          <template v-if="!s.error">
            <div class="bt-card-top">
              <span class="bt-grade" :class="btGradeClass(s)">{{
                btGrade(s)
              }}</span>
              <span class="bt-card-name">{{ btShortName(name) }}</span>
              <span
                :class="['bt-ret', s.total_return_pct >= 0 ? 'up' : 'dn']"
                >{{ s.total_return_pct >= 0 ? "+" : ""
                }}{{ s.total_return_pct }}%</span
              >
            </div>
            <div class="bt-card-line">
              {{ s.total_trades }} trades · {{ s.win_rate_pct }}% wins ·
              DD {{ s.max_drawdown_pct }}%
            </div>
            <div class="bt-bar-wrap">
              <div
                class="bt-bar"
                :class="
                  s.total_return_pct >= 0 ? 'bt-bar-up' : 'bt-bar-dn'
                "
                :style="{
                  width:
                    Math.min(100, Math.abs(s.total_return_pct) * 2) + '%',
                }"
              ></div>
              <div
                class="bt-bar-bh"
                :style="{
                  left:
                    Math.min(
                      100,
                      Math.abs(btResult.buy_and_hold_pct) * 2,
                    ) + '%',
                }"
              ></div>
            </div>
          </template>
          <template v-else>
            <div class="bt-card-name">{{ btShortName(name) }}</div>
            <div class="bt-risk dn">{{ s.error }}</div>
          </template>
        </div>
      </div>
    </template>
  </div>
</template>

<script setup>
import { ref } from 'vue'
import { runVbtBacktest } from '../../api/market'

const props = defineProps({
  chartTicker: { type: String, default: '' },
})

const btLoading = ref(false)
const btResult = ref(null)
const btError = ref('')
const btExpanded = ref(false)

function btGrade(s) {
  const score =
    (s.sharpe > 1.5 ? 3 : s.sharpe > 0.8 ? 2 : s.sharpe > 0.3 ? 1 : 0) +
    (s.total_return_pct > 20
      ? 3
      : s.total_return_pct > 10
        ? 2
        : s.total_return_pct > 0
          ? 1
          : 0) +
    (s.win_rate_pct > 60 ? 2 : s.win_rate_pct > 45 ? 1 : 0)
  return score >= 6 ? 'A' : score >= 4 ? 'B' : score >= 2 ? 'C' : 'D'
}

function btGradeClass(s) {
  const g = btGrade(s)
  return g === 'A' ? 'grade-a' : g === 'B' ? 'grade-b' : g === 'C' ? 'grade-c' : 'grade-d'
}

function btShortName(name) {
  return name
    .replace(' (14, 35/65)', '')
    .replace(' (9/21)', ' 9/21')
    .replace(' (21/50)', ' 21/50')
    .replace(' (20, 2σ)', '')
}

async function runBacktest() {
  if (!props.chartTicker || btLoading.value) return
  btLoading.value = true
  btResult.value = null
  btError.value = ''
  try {
    const res = await runVbtBacktest(props.chartTicker)
    btResult.value = res.data
  } catch (e) {
    btError.value = e?.message || 'Backtest failed'
  } finally {
    btLoading.value = false
  }
}
</script>

<style src="../../styles/BacktestPanel.css"></style>
