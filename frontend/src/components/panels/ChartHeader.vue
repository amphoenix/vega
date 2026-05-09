<template>
  <div class="chart-header">
    <span class="ch-ticker">{{ displayTicker }}</span>
    <span
      class="ch-company"
      v-if="tickerStats.company_name && tickerStats.company_name !== displayTicker"
    >{{ tickerStats.company_name }}</span>
    <span
      class="ch-price"
      v-if="chartHeaderPrice"
      :title="indmoneyLivePrice ? 'Live tick from IndStocks SSE' : 'Last cached price (live stream not connected yet)'"
    >{{ currencySymbol }}{{ chartHeaderPrice }}</span>
    <span
      :class="['ch-chg', chartHeaderChangePct >= 0 ? 'up' : 'dn']"
      v-if="chartHeaderChangePct !== null"
    >{{ chartHeaderChangePct >= 0 ? '+' : '' }}{{ chartHeaderChangePct }}%</span>
    <span class="ch-vol" v-if="tickerStats.volume_ratio">Vol {{ tickerStats.volume_ratio }}x avg</span>
    <span class="ch-live" v-if="!chartLoading"><span class="ch-live-dot"></span>LIVE</span>
    <span class="ch-live ch-loading" v-if="chartLoading">⟳</span>
    <span
      class="ch-fomo"
      v-if="fomoScore !== null"
      :class="fomoClass"
      title="FOMO Score: measures retail investor excitement/panic. High score = everyone is rushing in (risky). Low score = ignored (potential opportunity)."
    >Hype {{ fomoScore }}</span>
    <span
      :class="['ch-mkt', marketStatus.open ? 'ch-mkt-open' : 'ch-mkt-closed']"
      :title="marketStatus.open
        ? `${marketStatus.exchange} is trading now`
        : `${marketStatus.exchange} is closed${marketStatus.reason ? ' — ' + marketStatus.reason : ''}`"
    >{{ marketStatus.label }}</span>
  </div>
</template>

<script setup>
import { computed } from 'vue'

const props = defineProps({
  displayTicker:       { type: String,  default: '' },
  tickerStats:         { type: Object,  default: () => ({}) },
  chartHeaderPrice:    { type: String,  default: null },
  chartHeaderChangePct:{ type: Number,  default: null },
  chartLoading:        { type: Boolean, default: false },
  fomoScore:           { type: Number,  default: null },
  marketStatus:        { type: Object,  default: () => ({ open: false, label: '', exchange: '', reason: '' }) },
  indmoneyLivePrice:   { type: Number,  default: null },
  currencySymbol:      { type: String,  default: '₹' },
})

const fomoClass = computed(() => {
  if (props.fomoScore === null) return ''
  if (props.fomoScore >= 70) return 'hot'
  if (props.fomoScore >= 45) return 'neu'
  return 'dn'
})
</script>

<style src="../../styles/ChartHeader.css"></style>
