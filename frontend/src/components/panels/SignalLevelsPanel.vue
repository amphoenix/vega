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
      <div class="rs-bias" :class="levels.bias.toLowerCase()">
        {{ levels.bias }} · RSI {{ levels.rsi }}
      </div>
      <div class="rs-lv-rows">
        <div class="rs-lv t3"><span>T3</span><span>{{ fmtPrice(levels.target_3) }}</span></div>
        <div class="rs-lv t2"><span>T2</span><span>{{ fmtPrice(levels.target_2) }}</span></div>
        <div class="rs-lv t1"><span>T1</span><span>{{ fmtPrice(levels.target_1) }}</span></div>
        <div class="rs-lv en"><span>ENTRY</span><span>{{ fmtPrice(levels.entry) }}</span></div>
        <div class="rs-lv sl"><span>SL</span><span>{{ fmtPrice(levels.stop_loss) }}</span></div>
      </div>
      <div class="rs-lv-foot">
        Qty {{ levels.recommended_qty }} · Risk {{ currencySymbol }}{{ levels.capital_at_risk }}
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
