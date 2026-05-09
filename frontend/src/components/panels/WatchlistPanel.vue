<template>
  <div class="wl-list wl-watch">
    <div class="wl-watch-add">
      <input
        v-model="wlInput"
        class="wl-watch-input mono"
        placeholder="Add ticker (e.g. RELIANCE.NS)"
        maxlength="32"
        @keyup.enter="addToWatchlist(wlInput)"
      />
      <button
        class="wl-watch-btn"
        @click="addToWatchlist(wlInput)"
        :disabled="!wlInput"
      >
        + Add
      </button>
      <button
        class="wl-watch-btn wl-watch-pin"
        v-if="chartTicker"
        @click="pinCurrentTicker"
        :title="`Pin ${displayName} (currently charted) to your watchlist`"
      >
        📌 Pin {{ displayName }}
      </button>
    </div>
    <div
      v-for="w in watchlist"
      :key="w.symbol"
      :class="['wl-row', { selected: isSelected(w.symbol) }]"
      @click="emit('select-ticker', { symbol: w.symbol, name: w.name })"
    >
      <div class="wl-left">
        <span class="wl-flag">★</span>
        <div class="wl-names">
          <span class="wl-name">{{ w.name }}</span>
          <span class="wl-exch mono"
            >{{ w.symbol
            }}<span v-if="w.exchange"> · {{ w.exchange }}</span></span
          >
        </div>
      </div>
      <div class="wl-right">
        <button
          class="wl-watch-rm"
          @click.stop="removeFromWatchlist(w.symbol)"
          title="Remove"
        >
          ✕
        </button>
      </div>
    </div>
    <div class="feed-empty" v-if="!watchlist.length">
      <div>Your watchlist is empty.</div>
      <div class="empty-sub">
        Type a ticker above, or click 📌 Pin while viewing any chart.
      </div>
    </div>
  </div>
</template>

<script setup>
import { ref, computed, onMounted } from 'vue'
import { storeToRefs } from 'pinia'
import { useMarketStore } from '../../stores/useMarketStore'

const emit = defineEmits(['select-ticker'])

const store = useMarketStore()
const { watchlist, allAssets, chartTicker } = storeToRefs(store)

const WATCHLIST_KEY = 'phoenix.watchlist.v1'
const wlInput = ref('')

const displayName = computed(() =>
  (chartTicker.value || '').replace(/\.(NS|BO)$/i, ''),
)

function isSelected(sym) {
  return sym.toUpperCase() === (chartTicker.value || '').toUpperCase()
}

function loadWatchlist() {
  try {
    const raw = localStorage.getItem(WATCHLIST_KEY)
    watchlist.value = raw ? JSON.parse(raw) : []
  } catch {
    watchlist.value = []
  }
}

function saveWatchlist() {
  try {
    localStorage.setItem(WATCHLIST_KEY, JSON.stringify(watchlist.value))
  } catch {}
}

function addToWatchlist(sym) {
  sym = (sym || '').trim().toUpperCase()
  if (!sym) return
  if (watchlist.value.some((w) => w.symbol === sym)) {
    wlInput.value = ''
    return
  }
  const known = allAssets.value.find((a) => a.symbol.toUpperCase() === sym)
  watchlist.value.unshift({
    symbol: sym,
    name: known?.name || sym,
    exchange: known?.exchange || '',
    addedAt: Date.now(),
  })
  saveWatchlist()
  wlInput.value = ''
}

function removeFromWatchlist(sym) {
  watchlist.value = watchlist.value.filter((w) => w.symbol !== sym)
  saveWatchlist()
}

function pinCurrentTicker() {
  if (chartTicker.value) addToWatchlist(chartTicker.value)
}

onMounted(loadWatchlist)
</script>

<style src="../../styles/WatchlistPanel.css"></style>
