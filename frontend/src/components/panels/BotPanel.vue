<template>
  <div class="bot-view">
    <!-- ═══ HEADER ═══════════════════════════════════════════════════════════ -->
    <header class="bh">
      <h2 class="bh-title">🤖 CRYPTO BOTS</h2>
      <span class="bh-count">{{ bots.length }} bot{{ bots.length !== 1 ? 's' : '' }}</span>
      <span class="bh-sep">|</span>
      <span :class="['bh-pnl', aggPnl.net >= 0 ? 'up' : 'dn']">
        Net {{ aggPnl.net >= 0 ? '+' : '' }}${{ aggPnl.net.toFixed(2) }}
      </span>
      <span class="bh-stat muted">{{ aggPnl.total_positions }}P · {{ aggPnl.running_bots }}/{{ aggPnl.total_bots }} running</span>
      <span class="bh-spacer"></span>
      <button class="bh-btn" @click="startAll" title="Start all enabled bots">▶ Start All</button>
      <button class="bh-btn bh-btn-stop" @click="stopAll" title="Stop all bots">■ Stop All</button>
      <button class="bh-btn bh-btn-new" @click="showCreate = true">+ New Bot</button>
    </header>

    <!-- ═══ CREATE MODAL ═════════════════════════════════════════════════════ -->
    <div class="bm-overlay" v-if="showCreate" @click.self="showCreate = false">
      <div class="bm-modal">
        <div class="bm-header">
          <span>Create New Bot</span>
          <button class="bm-close" @click="showCreate = false">✕</button>
        </div>
        <div class="bm-body">
          <label class="bm-label">Name</label>
          <input v-model="newBot.name" class="bm-input" placeholder="BTC Trend Bot" />

          <label class="bm-label">Symbols (comma-separated)</label>
          <input v-model="newBot.symbolsStr" class="bm-input" placeholder="BTCUSDT, ETHUSDT, SOLUSDT" />

          <label class="bm-label">Strategy</label>
          <select v-model="newBot.strategy" class="bm-input">
            <option value="trend_v4">Trend v4 (default)</option>
            <option value="mean_revert">Mean Reversion</option>
            <option value="breakout">Breakout</option>
          </select>

          <label class="bm-label">Scan Interval (sec)</label>
          <input v-model.number="newBot.config.scan_interval_sec" type="number" class="bm-input" />

          <label class="bm-label">Min Confidence (%)</label>
          <input v-model.number="newBot.config.min_confidence" type="number" class="bm-input" />

          <label class="bm-label">Position Size (USD)</label>
          <input v-model.number="newBot.config.position_size_usd" type="number" class="bm-input" />

          <label class="bm-label">Max Positions</label>
          <input v-model.number="newBot.config.max_positions" type="number" class="bm-input" />

          <label class="bm-label">Daily Loss Limit (USD)</label>
          <input v-model.number="newBot.config.daily_loss_limit_usd" type="number" class="bm-input" />

          <div class="bm-row">
            <div class="bm-half">
              <label class="bm-label">SL ATR Mult</label>
              <input v-model.number="newBot.config.sl_atr_mult" type="number" step="0.1" class="bm-input" />
            </div>
            <div class="bm-half">
              <label class="bm-label">TP ATR Mult</label>
              <input v-model.number="newBot.config.tp_atr_mult" type="number" step="0.1" class="bm-input" />
            </div>
          </div>

          <div class="bm-error" v-if="createError">{{ createError }}</div>
        </div>
        <div class="bm-footer">
          <button class="bm-cancel" @click="showCreate = false">Cancel</button>
          <button class="bm-submit" @click="doCreate" :disabled="creating">
            {{ creating ? 'Creating...' : 'Create Bot' }}
          </button>
        </div>
      </div>
    </div>

    <!-- ═══ BOT CARDS ════════════════════════════════════════════════════════ -->
    <div class="bot-grid">
      <div v-if="!bots.length" class="bot-empty">
        No bots yet. Click <strong>+ New Bot</strong> to create one.
      </div>
      <div
        v-for="bot in bots"
        :key="bot.bot_id"
        :class="['bot-card', { 'bot-running': bot.running }]"
      >
        <div class="bc-top">
          <span class="bc-name">{{ bot.name }}</span>
          <span :class="['bc-badge', bot.running ? 'bc-live' : 'bc-off']">
            {{ bot.running ? 'RUNNING' : 'STOPPED' }}
          </span>
          <span class="bc-strategy muted">{{ bot.strategy }}</span>
        </div>

        <div class="bc-symbols">
          <span class="bc-sym" v-for="s in (bot.symbols || [])" :key="s">{{ s }}</span>
        </div>

        <div class="bc-stats">
          <div class="bc-stat">
            <span class="bc-stat-label">Realized</span>
            <span :class="['bc-stat-val', (bot.pnl?.realized || 0) >= 0 ? 'up' : 'dn']">
              ${{ (bot.pnl?.realized || 0).toFixed(2) }}
            </span>
          </div>
          <div class="bc-stat">
            <span class="bc-stat-label">Unrealized</span>
            <span :class="['bc-stat-val', (bot.pnl?.unrealized || 0) >= 0 ? 'up' : 'dn']">
              ${{ (bot.pnl?.unrealized || 0).toFixed(2) }}
            </span>
          </div>
          <div class="bc-stat">
            <span class="bc-stat-label">Positions</span>
            <span class="bc-stat-val">{{ bot.positions || 0 }}</span>
          </div>
          <div class="bc-stat">
            <span class="bc-stat-label">Signals</span>
            <span class="bc-stat-val">{{ bot.signal_count || 0 }}</span>
          </div>
        </div>

        <!-- Position list (expanded) -->
        <div class="bc-positions" v-if="bot.open_positions?.length">
          <div class="bc-pos-title">OPEN POSITIONS</div>
          <div
            v-for="pos in bot.open_positions"
            :key="pos.symbol"
            :class="['bc-pos', (pos.pnl || 0) >= 0 ? 'bc-pos-up' : 'bc-pos-dn']"
          >
            <span class="bc-pos-sym">{{ pos.symbol }}</span>
            <span :class="['bc-pos-side', pos.side === 'LONG' ? 'up' : 'dn']">{{ pos.side }}</span>
            <span :class="['bc-pos-pnl', (pos.pnl || 0) >= 0 ? 'up' : 'dn']">
              {{ (pos.pnl || 0) >= 0 ? '+' : '' }}${{ (pos.pnl || 0).toFixed(2) }}
            </span>
            <button class="bc-pos-close" @click="closePos(bot.bot_id, pos.symbol)" title="Close">✕</button>
          </div>
        </div>

        <div class="bc-actions">
          <button
            v-if="!bot.running"
            class="bc-act bc-start"
            @click="doStart(bot.bot_id)"
          >▶ Start</button>
          <button
            v-else
            class="bc-act bc-stop"
            @click="doStop(bot.bot_id)"
          >■ Stop</button>
          <button class="bc-act bc-del" @click="doDelete(bot.bot_id)" title="Delete bot">🗑</button>
        </div>
      </div>
    </div>
  </div>
</template>

<script setup>
import { ref, reactive, onMounted, onUnmounted } from 'vue'
import {
  listCryptoBots, getCryptoBotsPnl, createCryptoBot,
  startCryptoBot, stopCryptoBot, deleteCryptoBot,
  closeCryptoBotPosition, startAllCryptoBots, stopAllCryptoBots,
} from '../../api/market'

const bots = ref([])
const aggPnl = ref({ net: 0, realized: 0, unrealized: 0, total_positions: 0, running_bots: 0, total_bots: 0 })
const showCreate = ref(false)
const creating = ref(false)
const createError = ref('')

const newBot = reactive({
  name: '',
  symbolsStr: 'BTCUSDT, ETHUSDT',
  strategy: 'trend_v4',
  config: {
    scan_interval_sec: 300,
    min_confidence: 65,
    position_size_usd: 1000,
    max_positions: 5,
    daily_loss_limit_usd: 500,
    sl_atr_mult: 2.0,
    tp_atr_mult: 6.0,
  },
})

async function loadBots() {
  try {
    const res = await listCryptoBots()
    bots.value = res.data?.data || res.data || []
  } catch {}
}

async function loadPnl() {
  try {
    const res = await getCryptoBotsPnl()
    const d = res.data?.data || res.data || {}
    aggPnl.value = {
      net: d.net || 0,
      realized: d.realized || 0,
      unrealized: d.unrealized || 0,
      total_positions: d.total_positions || 0,
      running_bots: d.running_bots || 0,
      total_bots: d.total_bots || 0,
    }
  } catch {}
}

async function doCreate() {
  createError.value = ''
  const name = newBot.name.trim()
  if (!name) { createError.value = 'Bot name is required'; return }
  const symbols = newBot.symbolsStr.split(',').map(s => s.trim().toUpperCase()).filter(Boolean)
  if (!symbols.length) { createError.value = 'At least one symbol is required'; return }

  creating.value = true
  try {
    await createCryptoBot({
      name,
      symbols,
      strategy: newBot.strategy,
      config: { ...newBot.config },
    })
    showCreate.value = false
    newBot.name = ''
    newBot.symbolsStr = 'BTCUSDT, ETHUSDT'
    await loadBots()
  } catch (err) {
    createError.value = err.response?.data?.error || 'Create failed'
  } finally {
    creating.value = false
  }
}

async function doStart(botId) {
  try { await startCryptoBot(botId); await loadBots() } catch {}
}

async function doStop(botId) {
  try { await stopCryptoBot(botId); await loadBots() } catch {}
}

async function doDelete(botId) {
  if (!confirm('Delete this bot permanently?')) return
  try { await deleteCryptoBot(botId); await loadBots() } catch {}
}

async function closePos(botId, symbol) {
  try { await closeCryptoBotPosition(botId, symbol); await loadBots() } catch {}
}

async function startAll() {
  try { await startAllCryptoBots(); await loadBots() } catch {}
}

async function stopAll() {
  try { await stopAllCryptoBots(); await loadBots() } catch {}
}

let pollTimer = null

onMounted(() => {
  loadBots()
  loadPnl()
  pollTimer = setInterval(() => { loadBots(); loadPnl() }, 10_000)
})

onUnmounted(() => {
  clearInterval(pollTimer)
})
</script>

<style scoped>
.up   { color: #00d4a8; }
.dn   { color: #ff4757; }
.muted { color: #4a4e6a; }

.bot-view {
  display: flex; flex-direction: column; height: 100%;
  background: #080816; color: #c0c4d8; font-family: 'SF Mono', monospace;
}

/* ── Header ──────────────────────────────────────────────────────────── */
.bh {
  display: flex; align-items: center; gap: 8px;
  padding: 6px 12px; background: #0b0b18;
  border-bottom: 1px solid #1a1a2e; flex-shrink: 0;
  font-size: 11px;
}
.bh-title { font-size: 13px; color: #00d4a8; margin: 0; letter-spacing: 1px; }
.bh-count { font-size: 10px; color: #6b6f8a; }
.bh-sep { color: #1a1a2e; }
.bh-pnl { font-weight: 700; font-size: 12px; font-family: monospace; }
.bh-stat { font-size: 10px; }
.bh-spacer { flex: 1; }
.bh-btn {
  padding: 4px 10px; border-radius: 4px; cursor: pointer;
  font-size: 10px; font-weight: 700; border: 1px solid #2a2a3e;
  background: #111125; color: #00d4a8; transition: all 0.15s;
}
.bh-btn:hover { border-color: #00d4a8; background: rgba(0,212,168,0.08); }
.bh-btn-stop { color: #ff4757; }
.bh-btn-stop:hover { border-color: #ff4757; background: rgba(255,71,87,0.08); }
.bh-btn-new { color: #f7931a; }
.bh-btn-new:hover { border-color: #f7931a; background: rgba(247,147,26,0.08); }

/* ── Create Modal ────────────────────────────────────────────────────── */
.bm-overlay {
  position: fixed; inset: 0; background: rgba(0,0,0,0.7);
  display: flex; align-items: center; justify-content: center; z-index: 1000;
}
.bm-modal {
  background: #0e0e22; border: 1px solid #1a1a2e; border-radius: 8px;
  width: 420px; max-height: 80vh; overflow-y: auto;
}
.bm-header {
  display: flex; align-items: center; justify-content: space-between;
  padding: 10px 14px; border-bottom: 1px solid #1a1a2e;
  font-size: 13px; font-weight: 700; color: #f7931a;
}
.bm-close {
  background: none; border: none; color: #6b6f8a; cursor: pointer;
  font-size: 14px;
}
.bm-close:hover { color: #ff4757; }
.bm-body { padding: 12px 14px; display: flex; flex-direction: column; gap: 6px; }
.bm-label { font-size: 9px; color: #6b6f8a; text-transform: uppercase; letter-spacing: 0.5px; }
.bm-input {
  background: #111125; border: 1px solid #1a1a2e; border-radius: 4px;
  color: #c0c4d8; padding: 6px 8px; font-size: 12px; outline: none;
  font-family: monospace;
}
.bm-input:focus { border-color: #00d4a8; }
.bm-row { display: flex; gap: 8px; }
.bm-half { flex: 1; display: flex; flex-direction: column; gap: 4px; }
.bm-error { color: #ff4757; font-size: 11px; padding: 4px 0; }
.bm-footer {
  display: flex; justify-content: flex-end; gap: 8px;
  padding: 10px 14px; border-top: 1px solid #1a1a2e;
}
.bm-cancel {
  padding: 6px 14px; border-radius: 4px; cursor: pointer;
  background: #1a1a2e; border: none; color: #6b6f8a; font-size: 11px;
}
.bm-submit {
  padding: 6px 14px; border-radius: 4px; cursor: pointer;
  background: #00d4a8; border: none; color: #000; font-weight: 700; font-size: 11px;
}
.bm-submit:disabled { opacity: 0.5; cursor: not-allowed; }

/* ── Bot Grid ────────────────────────────────────────────────────────── */
.bot-grid {
  flex: 1; overflow-y: auto; padding: 12px;
  display: grid; grid-template-columns: repeat(auto-fill, minmax(340px, 1fr));
  gap: 12px; align-content: start;
}
.bot-grid::-webkit-scrollbar { width: 3px; }
.bot-grid::-webkit-scrollbar-thumb { background: #1a1a2e; }

.bot-empty {
  grid-column: 1 / -1; text-align: center; padding: 60px 20px;
  color: #4a4e6a; font-size: 13px;
}

.bot-card {
  background: #0e0e22; border: 1px solid #1a1a2e; border-radius: 8px;
  padding: 12px; display: flex; flex-direction: column; gap: 8px;
  transition: border-color 0.15s;
}
.bot-card:hover { border-color: #2a2a4e; }
.bot-running { border-left: 3px solid #00d4a8; }

.bc-top { display: flex; align-items: center; gap: 8px; }
.bc-name { font-size: 14px; font-weight: 700; color: #c0c4d8; }
.bc-badge {
  font-size: 8px; font-weight: 700; padding: 2px 6px; border-radius: 3px;
  letter-spacing: 0.5px;
}
.bc-live { background: rgba(0,212,168,0.15); color: #00d4a8; }
.bc-off { background: #1a1a2e; color: #4a4e6a; }
.bc-strategy { font-size: 10px; margin-left: auto; }

.bc-symbols { display: flex; flex-wrap: wrap; gap: 4px; }
.bc-sym {
  font-size: 9px; padding: 2px 6px; border-radius: 3px;
  background: #111125; color: #f7931a; font-weight: 600;
}

.bc-stats {
  display: grid; grid-template-columns: repeat(4, 1fr); gap: 6px;
}
.bc-stat {
  display: flex; flex-direction: column; align-items: center; gap: 2px;
  background: #0a0a16; border-radius: 4px; padding: 4px;
}
.bc-stat-label { font-size: 8px; color: #4a4e6a; text-transform: uppercase; }
.bc-stat-val { font-size: 12px; font-weight: 600; font-family: monospace; }

/* Positions */
.bc-positions { border-top: 1px solid #1a1a2e; padding-top: 6px; }
.bc-pos-title { font-size: 8px; color: #4a4e6a; letter-spacing: 0.5px; margin-bottom: 4px; }
.bc-pos {
  display: flex; align-items: center; gap: 6px; padding: 3px 0;
  font-size: 11px; border-left: 2px solid transparent;
  padding-left: 6px;
}
.bc-pos-up { border-left-color: #00d4a8; }
.bc-pos-dn { border-left-color: #ff4757; }
.bc-pos-sym { font-weight: 600; }
.bc-pos-side { font-size: 9px; font-weight: 700; }
.bc-pos-pnl { margin-left: auto; font-family: monospace; font-weight: 600; }
.bc-pos-close {
  background: none; border: 1px solid #ff475744; color: #ff4757;
  border-radius: 2px; cursor: pointer; font-size: 8px; padding: 0 3px;
  line-height: 1.3;
}
.bc-pos-close:hover { background: #ff4757; color: #fff; }

/* Actions */
.bc-actions {
  display: flex; gap: 6px; margin-top: 4px;
  border-top: 1px solid #1a1a2e; padding-top: 8px;
}
.bc-act {
  padding: 5px 12px; border-radius: 4px; cursor: pointer;
  font-size: 10px; font-weight: 700; border: 1px solid #2a2a3e;
  background: #111125; transition: all 0.15s;
}
.bc-start { color: #00d4a8; }
.bc-start:hover { border-color: #00d4a8; background: rgba(0,212,168,0.08); }
.bc-stop { color: #ff4757; }
.bc-stop:hover { border-color: #ff4757; background: rgba(255,71,87,0.08); }
.bc-del { margin-left: auto; color: #6b6f8a; font-size: 12px; border: none; background: none; }
.bc-del:hover { color: #ff4757; }
</style>
