<template>
  <div class="rs-panel">
    <div class="rs-header hdr-signal">
      <span class="rs-title">SIGNAL &amp; LEVELS</span>
      <div class="rs-hdr-actions">
        <button
          class="rs-icon-btn"
          @click="$emit('run-signal')"
          :disabled="signalLoading || !chartTicker"
          title="Get Signal"
        >
          <span v-if="signalLoading" class="spinner"></span>
          <span v-else>⚡</span>
        </button>
        <button
          class="rs-icon-btn"
          @click="$emit('load-levels')"
          :disabled="levelsLoading || !chartTicker"
          title="Refresh Levels"
        >
          <span v-if="levelsLoading" class="spinner"></span>
          <span v-else>⟳</span>
        </button>
      </div>
    </div>

    <div
      class="rs-empty"
      v-if="!signal && !levels && !signalLoading && !levelsLoading && !signalError"
    >
      Momentum · Trend · Volume
    </div>
    <div class="rs-err" v-if="signalError && !signalLoading">
      ⚠ {{ signalError }}
    </div>

    <div v-if="signal">
      <div
        :class="[
          'rs-action',
          signal.action === 'BUY' ? 'rs-act-buy'
            : signal.action === 'SELL' ? 'rs-act-sell'
            : 'rs-act-hold',
        ]"
      >
        {{ signal.action }}
        <span class="rs-score">{{ signal.score }}/100</span>
      </div>
      <div class="rs-inds">
        <span>RSI <b :class="signal.rsi < 35 ? 'up' : signal.rsi > 65 ? 'dn' : ''">{{ signal.rsi }}</b></span>
        <span>Trend <b :class="signal.ema_bull ? 'up' : 'dn'">{{ signal.ema_bull ? '↑' : '↓' }}</b></span>
        <span>VWAP <b :class="signal.above_vwap ? 'up' : 'dn'">{{ signal.above_vwap ? '↑' : '↓' }}</b></span>
        <span>VOL <b :class="signal.vol_ratio >= 1.5 ? 'up' : signal.vol_ratio <= 0.6 ? 'dn' : ''">{{ signal.vol_ratio }}x</b></span>
      </div>
    </div>

    <div v-if="levels">
      <div class="rs-bias" :class="(levels.bias || '').toLowerCase()">
        {{ levels.bias || '—' }} · RSI {{ levels.rsi }}
      </div>
      <div class="rs-lv-rows">
        <div v-for="(r, i) in (levels.resistances || [])" :key="'r'+i" class="rs-lv res">
          <span>R{{ i + 1 }}</span><span>{{ fmtPrice(r) }}</span>
        </div>
        <div class="rs-lv t3"><span>T3</span><span>{{ fmtPrice(levels.target_3) }}</span></div>
        <div class="rs-lv t2"><span>T2</span><span>{{ fmtPrice(levels.target_2) }}</span></div>
        <div class="rs-lv t1">
          <span>T1</span>
          <span>{{ fmtPrice(levels.target_1) }}<span v-if="levels.rr_t1" class="rs-rr"> {{ levels.rr_t1 }}R</span></span>
        </div>
        <div class="rs-lv en"><span>ENTRY</span><span>{{ fmtPrice(levels.entry) }}</span></div>
        <div class="rs-lv sl"><span>SL</span><span>{{ fmtPrice(levels.stop_loss) }}</span></div>
        <div v-for="(s, i) in (levels.supports || [])" :key="'s'+i" class="rs-lv sup">
          <span>S{{ i + 1 }}</span><span>{{ fmtPrice(s) }}</span>
        </div>
      </div>
      <div class="rs-lv-foot">
        ATR {{ fmtPrice(levels.atr) }} · R/R T1 {{ levels.rr_t1 }} · T2 {{ levels.rr_t2 }}
      </div>
      <div v-if="levels.cpr_tc" class="rs-cpr" :class="levels.cpr_type">
        <span class="rs-cpr-label">CPR <span class="rs-cpr-type">{{ levels.cpr_type }}</span></span>
        <span class="rs-cpr-vals">Top {{ fmtPrice(levels.cpr_tc) }} · Pivot {{ fmtPrice(levels.cpr_pp) }} · Bottom {{ fmtPrice(levels.cpr_bc) }}</span>
      </div>
    </div>
  </div>
</template>

<script setup>
import { fmtPrice } from '../../utils/formatters'

defineProps({
  signal:        { type: Object,  default: null },
  signalLoading: { type: Boolean, default: false },
  signalError:   { type: String,  default: '' },
  levels:        { type: Object,  default: null },
  levelsLoading: { type: Boolean, default: false },
  chartTicker:   { type: String,  default: '' },
  currencySymbol:{ type: String,  default: '₹' },
})

defineEmits(['run-signal', 'load-levels'])
</script>

<style src="../../styles/SignalLevelsPanel.css"></style>
