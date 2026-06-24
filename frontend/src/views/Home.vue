<template>
  <div :class="['terminal', { light: lightMode }]">
    <!-- ══ TOP BAR ═══════════════════════════════════════════════════════════ -->
    <TopBar
      v-model:viewMode="viewMode"
      :lightMode="lightMode"
      :brokerConnected="brokerConnected"
      :brokerName="brokerNameRef"
      :brokerUserName="brokerUserName"
      :brokerLivePrice="brokerLivePrice"
      :brokerAvailable="brokerAvailable"
      :brokerAvailableCash="brokerAvailableCash"
      @toggle-theme="toggleTheme"
      @broker-open="brokerOpen"
      @select-ticker="selectTicker"
    />

    <!-- ══ MAIN: SWING VIEW ═══════════════════════════════════════════════════ -->
    <div class="main-area" v-show="viewMode === 'swing'">
      <!-- CENTER: Chart + Feed -->
      <section class="chart-section">
        <!-- Chart header stats -->
        <ChartHeader
          v-if="chartTicker"
          :displayTicker="displayTicker"
          :tickerStats="tickerStats"
          :chartHeaderPrice="chartHeaderPrice"
          :chartHeaderChangePct="chartHeaderChangePct"
          :chartLoading="false"
          :fomoScore="fomoScore"
          :marketStatus="marketStatus"
          :brokerLivePrice="brokerLivePrice"
          :currencySymbol="currencySymbol"
        />

        <ChartGrid
          :chartTicker="chartTicker"
          :displayTicker="displayTicker"
          :interval="interval"
          :levels="levels"
          :lightMode="lightMode"
          :marketOpen="marketStatus.open"
          @update:chartTicker="chartTicker = $event"
          @update:activeTicker="activeTicker = $event"
          @close-third="selectTicker('^NSEI')"
        />

        <!-- Feed panel below chart -->
        <IntelFeedPanel
          :chartTicker="chartTicker"
          :feedItems="feedItems"
          :signalsLoading="signalsLoading"
          :fomoScore="fomoScore"
          :tickerStats="tickerStats"
          :aiPredLoading="aiPredLoading"
          :redditSent="redditSent"
          @run-ai-predict="runAiPredict"
        />
      </section>

      <!-- RIGHT SIDEBAR: Signal+Levels | F&O Scanner | Backtest -->
      <aside class="right-sidebar">
        <!-- Panel 1: F&O SCANNER -->
        <FoScannerPanel @select-ticker="selectTicker" />

        <!-- Panel 2: LIVE TRADING (WATCHING) -->
        <LiveTradingPanel />

        <!-- Panel 3: SIGNAL & LEVELS (merged) -->
        <SignalLevelsPanel
          :signal="signal"
          :signalLoading="signalLoading"
          :signalError="signalError"
          :levels="levels"
          :levelsLoading="levelsLoading"
          :chartTicker="chartTicker"
          :currencySymbol="currencySymbol"
          @run-signal="runIntradaySignal"
          @load-levels="loadLevels"
        />

        <!-- Panel 4: BACKTEST -->
        <BacktestPanel :chartTicker="chartTicker" />
      </aside>
    </div>

    <!-- ══ SCALP MODE VIEW ═══════════════════════════════════════════════════ -->
    <ScalpPanel v-show="viewMode === 'scalp'" />

    <!-- ══ CRYPTO VIEW ════════════════════════════════════════════════════════ -->
    <CryptoPanel v-show="viewMode === 'crypto'" />

    <!-- ══ POLYMARKET VIEW ════════════════════════════════════════════════════ -->
    <PolyPanel v-show="viewMode === 'poly'" />

    <!-- ══ FOREX VIEW ══════════════════════════════════════════════════════════ -->
    <ForexPanel v-show="viewMode === 'forex'" />

  </div>
</template>

<script setup>
import { ref, computed, onMounted, onUnmounted, watch } from "vue";
import { storeToRefs } from "pinia";
import { useMarketStore } from "../stores/useMarketStore";
import { useLiveTradingStore } from "../stores/useLiveTradingStore";
import {
  getSignals,
  getAiPredict,
  getTradeLevels,
  getIntradaySignal,
  getBrokerStatus,
  getBrokerTick,
} from "../api/market";
import BacktestPanel from "../components/panels/BacktestPanel.vue";
import FoScannerPanel from "../components/panels/FoScannerPanel.vue";
import ChartGrid from "../components/chart/ChartGrid.vue";
import IntelFeedPanel from "../components/panels/IntelFeedPanel.vue"
import TopBar from "../components/panels/TopBar.vue"
import ChartHeader from "../components/panels/ChartHeader.vue"
import SignalLevelsPanel from "../components/panels/SignalLevelsPanel.vue"
import LiveTradingPanel from "../components/panels/LiveTradingPanel.vue"
import ScalpPanel from "../components/panels/ScalpPanel.vue"
import CryptoPanel from "../components/panels/CryptoPanel.vue"
import PolyPanel from "../components/panels/PolyPanel.vue"
import ForexPanel from "../components/panels/ForexPanel.vue"
import { fmtTime } from "../utils/formatters";

// ── State ─────────────────────────────────────────────────────────────────────
const {
  activeTicker, chartTicker, interval, searchSuggestions,
  tickerStats, fomoScore,
  feedItems, signalsLoading,
  signal, signalLoading, signalError,
  levels, levelsLoading,
  aiPredLoading,
} = storeToRefs(useMarketStore());
const { brokerConnected, brokerAvailable, brokerLivePrice, brokerNameRef, brokerUserName, brokerAvailableCash } = storeToRefs(useLiveTradingStore());
const redditSent = ref(null);

let feedIdCounter = 0;

// ── Currency & display helpers ────────────────────────────────────────────────
// Default to ₹ since this is an Indian-market trading app — only fall back to $
// for unambiguously US tickers (^DJI, ^GSPC, AAPL, etc). The previous logic
// inverted this and required every Indian index to be whitelisted, leaving
// ^CNXFIN, ^NSEMDCP50, ^INDIAVIX, ^CNXPHARMA, ^CNXIT (and the world-indices
// tray) all incorrectly showing $.
const US_INDICES = new Set([
  "^DJI",
  "^GSPC",
  "^IXIC",
  "^RUT",
  "^VIX",
  "^FTSE",
  "^GDAXI",
  "^N225",
  "^HSI",
]);
const US_TICKER_RE = /\.(US|L|TO|HK|DE|PA|MI|AS|SW)$/i;
const currencySymbol = computed(() => {
  const t = (chartTicker.value || activeTicker.value || "").toUpperCase();
  if (!t) return "₹";
  // Explicit US/world index → $ (USD-quoted)
  if (US_INDICES.has(t)) return "$";
  // Foreign-exchange-suffixed ticker → $
  if (US_TICKER_RE.test(t)) return "$";
  // Bare AAPL/MSFT-style US tickers (no suffix, no caret, 1-5 letters,
  // and NOT a known Indian-index/F&O pattern) → $
  // Indian F&O contracts always have hyphens (NIFTY-MAY2026-24100-CE),
  // and Indian indices always start with ^. So a clean uppercase string
  // with no hyphen and no caret is most likely a US equity.
  if (/^[A-Z]{1,5}$/.test(t) && !t.startsWith("^")) {
    // Heuristic — but harmless since user can disambiguate by typing the
    // full Indian symbol (RELIANCE.NS, SBIN.NS) which the .NS suffix
    // handles below.
    return "$";
  }
  return "₹";
});
// Friendly contract label for the chart header / title bar.
// Sourced directly from broker tick API (CUSTOM_SYMBOL for F&O + equity,
// SEGMENT for indices) — never constructed client-side.
const chartDisplaySymbol = ref("");
watch(
  chartTicker,
  async (sym) => {
    chartDisplaySymbol.value = "";
    if (!sym) return;
    try {
      const res = await getBrokerTick(encodeURIComponent(sym));
      const d = res?.data?.data || res?.data;
      if (d?.display_symbol) chartDisplaySymbol.value = d.display_symbol;
    } catch {
      /* leave empty → falls back to the raw ticker */
    }
  },
  { immediate: true },
);

const displayTicker = computed(
  () =>
    chartDisplaySymbol.value ||
    (chartTicker.value || "").replace(/\.(NS|BO)$/i, ""),
);

const chartHeaderPrice = computed(() => {
  const live = Number(brokerLivePrice.value);
  const static_ = Number(tickerStats.value?.price);
  const p = live > 0 ? live : static_ > 0 ? static_ : null;
  return p ? p.toLocaleString("en-IN", { maximumFractionDigits: 2 }) : null;
});

const chartHeaderChangePct = computed(() => {
  const live = Number(brokerLivePrice.value);
  const prev =
    Number(tickerStats.value?.prev_close) ||
    Number(tickerStats.value?.price_open);
  if (live > 0 && prev > 0)
    return Number((((live - prev) / prev) * 100).toFixed(2));
  // Fallback to whatever the backend returned in tickerStats
  if (tickerStats.value?.change_1d !== undefined)
    return Number(tickerStats.value.change_1d);
  return null;
});

// ── View mode ─────────────────────────────────────────────────────────────────
const viewMode = ref("swing"); // 'swing' | 'scalp' | 'crypto' | 'poly'

const marketStatus = computed(() => {
  const ticker = chartTicker.value || "";
  // Indian: .NS/.BO suffix, NSE/BSE indices (^NSEI, ^NSEBANK, ^BSESN, ^CNXFIN…),
  // or bare symbols with no exchange suffix (SBIN, RELIANCE, NIFTY etc.) — this app is India-first
  const isIndian =
    ticker.endsWith(".NS") ||
    ticker.endsWith(".BO") ||
    /^\^(NSEI|NSEBANK|BSESN|CNXIT|CNXFIN|INDIAVIX|NSEMDCP)/.test(ticker) ||
    (!ticker.includes(".") && !ticker.startsWith("^"));
  const exchange = isIndian ? "NSE" : "NYSE";

  // NSE holidays 2025–2026 (YYYY-MM-DD)
  const nseHolidays = new Set([
    "2025-01-26",
    "2025-03-14",
    "2025-04-14",
    "2025-04-18",
    "2025-05-01",
    "2025-08-15",
    "2025-08-27",
    "2025-10-02",
    "2025-10-20",
    "2025-10-21",
    "2025-11-05",
    "2025-12-25",
    "2026-01-26",
    "2026-03-02",
    "2026-04-03",
    "2026-04-14",
    "2026-05-01",
    "2026-08-17",
    "2026-09-15",
    "2026-10-02",
    "2026-11-03",
    "2026-11-25",
    "2026-12-25",
  ]);
  // NYSE holidays 2025–2026 (YYYY-MM-DD)
  const nyseHolidays = new Set([
    "2025-01-01",
    "2025-01-20",
    "2025-02-17",
    "2025-04-18",
    "2025-05-26",
    "2025-06-19",
    "2025-07-04",
    "2025-09-01",
    "2025-11-27",
    "2025-12-25",
    "2026-01-01",
    "2026-01-19",
    "2026-02-16",
    "2026-04-03",
    "2026-05-25",
    "2026-06-19",
    "2026-07-04",
    "2026-09-07",
    "2026-11-26",
    "2026-12-25",
  ]);

  // Use Intl for DST-aware timezone conversion
  const tz = isIndian ? "Asia/Kolkata" : "America/New_York";
  const parts = Object.fromEntries(
    new Intl.DateTimeFormat("en-US", {
      timeZone: tz,
      year: "numeric",
      month: "2-digit",
      day: "2-digit",
      weekday: "short",
      hour: "2-digit",
      minute: "2-digit",
      hour12: false,
    })
      .formatToParts(new Date())
      .map((p) => [p.type, p.value]),
  );
  const weekday = parts.weekday; // 'Sat', 'Sun', etc.
  const dateStr = `${parts.year}-${parts.month}-${parts.day}`;
  const mins = parseInt(parts.hour) * 60 + parseInt(parts.minute);

  if (weekday === "Sat" || weekday === "Sun")
    return {
      open: false,
      exchange,
      label: `${exchange} CLOSED`,
      reason: "Weekend",
    };

  const holidays = isIndian ? nseHolidays : nyseHolidays;
  if (holidays.has(dateStr))
    return {
      open: false,
      exchange,
      label: `${exchange} CLOSED`,
      reason: "Holiday",
    };

  const inHours = isIndian
    ? mins >= 555 && mins <= 930
    : mins >= 570 && mins <= 960;
  return {
    open: inHours,
    exchange,
    label: inHours ? `${exchange} OPEN` : `${exchange} CLOSED`,
    reason: inHours ? "" : "After hours",
  };
});
const lightMode = ref(localStorage.getItem("theme") === "light");

// ── Computed ──────────────────────────────────────────────────────────────────

function onAddFeedItems(items) {
  for (const item of items) {
    feedItems.value.unshift({ ...item, id: feedIdCounter++ })
  }
}

async function selectTicker(sym) {
  if (!sym) return;
  chartTicker.value = sym.toUpperCase();
  activeTicker.value = chartTicker.value;
  await loadSignals();
  loadLevels();
  brokerLivePrice.value = null;
}

async function loadSignals() {
  if (!chartTicker.value) return;
  signalsLoading.value = true;
  try {
    const res = await getSignals(chartTicker.value);
    const d = res.data || {};
    tickerStats.value = d.stats || {};
    fomoScore.value = d.fomo_score ?? null;
    redditSent.value = d.reddit_sentiment ?? null;

    // Build feed items
    const newItems = [];

    // FOMO entry
    if (d.fomo_score !== undefined) {
      newItems.push({
        id: feedIdCounter++,
        type: "fomo",
        title: `FOMO score ${d.fomo_score}/100 — ${d.fomo_score >= 70 ? "EXTREME: high retail interest, elevated risk" : d.fomo_score >= 45 ? "Elevated: momentum building" : "Low: potential accumulation zone"}`,
        meta: `Volume ratio ${d.stats?.volume_ratio || "?"}x avg · 1D ${d.stats?.change_1d || 0}% · 5D ${d.stats?.change_5d || 0}%`,
        sentiment: d.fomo_score / 100,
      });
    }

    // News items
    for (const n of d.news || []) {
      newItems.push({
        id: feedIdCounter++,
        type: "news",
        title: n.title,
        meta: `${n.source || "News"} · ${fmtTime(n.pub_date, { mode: 'date' })}`,
        sentiment: undefined,
      });
    }

    // Reddit items (real posts)
    for (const r of d.reddit || []) {
      newItems.push({
        id: feedIdCounter++,
        type: "reddit",
        title: r.title,
        meta: `r/${r.subreddit} · ▲${r.score} · ${r.comments} comments`,
        score: r.score,
        sentiment: r.upvote_ratio,
      });
    }

    // Seeking Alpha analyst posts (real) → TWITTER tab
    for (const s of d.stocktwits || []) {
      newItems.push({
        id: feedIdCounter++,
        type: "twitter",
        title: s.title,
        meta: `${s.author} · ${s.source} · ${fmtTime(s.pub_date, { mode: 'date' })}`,
        sentiment: undefined,
      });
    }

    // Prediction entry
    if (d.fomo_score !== undefined) {
      const f = d.fomo_score;
      const m = d.stats?.change_1d || 0;
      const rs = d.reddit_sentiment || 0.5;
      const st = Math.round(f * 0.4 + (m > 0 ? 65 : 35) * 0.3 + rs * 100 * 0.3);
      newItems.push({
        id: feedIdCounter++,
        type: "prediction",
        title: `${displayTicker.value} — Short term: ${st >= 55 ? "▲ BULLISH" : st <= 45 ? "▼ BEARISH" : "— NEUTRAL"} (${st}%) · Reddit sentiment ${(rs * 100).toFixed(0)}% bullish`,
        meta: `Based on FOMO ${f}, momentum ${m > 0 ? "+" : ""}${m}%, ${(d.news || []).length} news, ${(d.reddit || []).length} Reddit posts`,
        sentiment: st / 100,
      });
    }

    // Prepend to feed (newest first)
    feedItems.value = [...newItems, ...feedItems.value].slice(0, 100);
  } catch (e) {
    console.error("Signals load failed", e);
  } finally {
    signalsLoading.value = false;
  }
}

// ── AI auto-prediction ────────────────────────────────────────────────────────
async function runAiPredict() {
  if (!chartTicker.value || aiPredLoading.value) return;
  // Skip option contracts — AI predict needs news/fundamentals which don't
  // exist for an option symbol. Use the F&O scanner / option chain instead.
  if (/^[A-Z]+-[A-Z]{3}\d{4}-\d+-(CE|PE)$/.test(chartTicker.value)) {
    console.info("[runAiPredict] skipped — option contract", chartTicker.value);
    return;
  }
  aiPredLoading.value = true;
  try {
    const res = await getAiPredict(chartTicker.value);
    const d = res.data || res; // interceptor returns res directly

    const shortDir = d.short_term || "NEUTRAL";
    const longDir = d.long_term || "NEUTRAL";
    const shortConf = d.short_confidence || 50;
    const longConf = d.long_confidence || 50;
    const factors = (d.key_factors || []).join(" · ");
    const risk = d.risk_level || "MEDIUM";
    const reasoning = d.reasoning || "No reasoning provided";

    feedItems.value.unshift({
      id: feedIdCounter++,
      type: "prediction",
      title: `${displayTicker.value} — Short: ${shortDir === "BULLISH" ? "▲" : shortDir === "BEARISH" ? "▼" : "—"} ${shortDir} ${shortConf}% · Long: ${longDir === "BULLISH" ? "▲" : longDir === "BEARISH" ? "▼" : "—"} ${longDir} ${longConf}%`,
      meta: `${reasoning}${risk ? " · Risk: " + risk : ""}${factors ? " · " + factors : ""}`,
      sentiment: shortConf / 100,
      ai: true,
    });

    if (d.fomo_score !== undefined) fomoScore.value = d.fomo_score;
    if (d.stats?.price)
      tickerStats.value = { ...tickerStats.value, ...d.stats };
    if (feedItems.value.length > 120)
      feedItems.value = feedItems.value.slice(0, 100);
  } catch (e) {
    console.error("AI predict failed", e);
    // Show error in feed so user knows something happened
    feedItems.value.unshift({
      id: feedIdCounter++,
      type: "prediction",
      title: `⚠ AI Predict failed for ${displayTicker.value}`,
      meta: e?.message || "Check backend logs",
      sentiment: 0.5,
    });
  } finally {
    aiPredLoading.value = false;
  }
}

// ── Trade Levels ──────────────────────────────────────────────────────────────
async function loadLevels() {
  if (!chartTicker.value || levelsLoading.value) return;
  levelsLoading.value = true;
  try {
    const res = await getTradeLevels(chartTicker.value);
    levels.value = res.data || null;
  } catch (e) {
    levels.value = null;
  } finally {
    levelsLoading.value = false;
  }
}

function toggleTheme() {
  lightMode.value = !lightMode.value;
  localStorage.setItem("theme", lightMode.value ? "light" : "dark");
}

// ── Broker status ─────────────────────────────────────────────────────────────
async function checkBrokerStatus() {
  try {
    const res = await getBrokerStatus();
    const d = res.data?.data || res.data || res;
    brokerAvailable.value = d.token_configured || false;
    brokerConnected.value = d.connected || false;
    brokerNameRef.value = d.broker || "";
    brokerUserName.value = d.name || "";
    brokerAvailableCash.value = d.available_cash ?? null;
  } catch (e) {
    brokerConnected.value = false;
  }
}

async function brokerOpen() {
  await checkBrokerStatus();
  if (brokerConnected.value) {
    return;
  }
  if (brokerAvailable.value) {
    alert(
      "Token is configured but connection check failed.\nCheck your broker credentials and restart the backend.",
    );
  } else {
    alert(
      "Broker not connected.\n\nConfigure your broker credentials in .env and restart the backend.",
    );
  }
}

async function runIntradaySignal() {
  if (!chartTicker.value) return;
  signalLoading.value = true;
  signal.value = null;
  signalError.value = "";
  try {
    const res = await getIntradaySignal(chartTicker.value);
    signal.value = res.data || null;
  } catch (e) {
    signalError.value = e?.message || "Signal fetch failed";
  } finally {
    signalLoading.value = false;
  }
}

// ── Simulation launch ─────────────────────────────────────────────────────────
onMounted(async () => {
  await checkBrokerStatus();

  await selectTicker("^NSEI"); // default chart: NIFTY 50


  // Auto-fire of AI Predict (initial 5s delay + 60s polling) is disabled.
  // The button still works on user click; remove this comment + restore to re-enable.
  // setTimeout(() => {
  //   runAiPredict();
  //   aiPredTimer = setInterval(() => {
  //     if (chartTicker.value) runAiPredict();
  //   }, 60000);
  // }, 5000);
});

onUnmounted(() => {});

// On every module evaluation (including Vue SFC HMR), close any
// previously-opened singleton EventSources stashed on `window` so they
// don't keep accumulating one extra connection per save.
if (typeof window !== "undefined") {
  const key = "__vega_foScannerES";
  if (window[key]) {
    try { window[key].close?.(); } catch {}
    window[key] = null;
  }
}
</script>

<style src="../styles/Home.css"></style>
