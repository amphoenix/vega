<template>
  <div :class="['terminal', { light: lightMode }]">

    <!-- ══ TOP BAR ═══════════════════════════════════════════════════════════ -->
    <header class="topbar">
      <div class="topbar-left">
        <div class="brand">PhoenixTrade<span class="brand-sub">AI Trading Intelligence</span></div>
        <div class="ticker-row">
          <div class="search-wrap">
            <input v-model="activeTicker" class="ticker-input" placeholder="SBIN or State Bank…"
                   @keyup.enter="selectTicker(activeTicker); searchSuggestions=[]"
                   @input="onSearchInput" @blur="hideSuggestionsDelayed" autocomplete="off" />
            <div class="search-suggestions" v-if="searchSuggestions.length">
              <div v-for="s in searchSuggestions" :key="s.symbol"
                   class="ss-item" @mousedown.prevent="pickSuggestion(s)">
                <span class="ss-sym">{{ s.symbol.replace(/\.(NS|BO)$/i,'') }}</span>
                <span class="ss-name">{{ s.name }}</span>
                <span class="ss-ex">{{ s.indian ? (s.symbol.endsWith('.NS')?'NSE':'BSE') : s.exchange }}</span>
              </div>
            </div>
          </div>
          <div class="tf-btns">
            <button v-for="tf in timeframes" :key="tf.v"
                    :class="['tf-btn', { active: interval === tf.v }]"
                    @click="interval = tf.v; reloadChart()">{{ tf.l }}</button>
          </div>
          <button class="go-btn" @click="selectTicker(activeTicker); searchSuggestions=[]">▶ Load</button>
        </div>
      </div>

      <div class="topbar-center">
        <button :class="['vtn-tab', { active: viewMode === 'chart' }]" @click="viewMode = 'chart'">📊 Chart</button>
        <button :class="['vtn-tab', { active: viewMode === 'analysis' }]" @click="switchToAnalysis">🧠 Analysis</button>
        <button :class="['vtn-tab', { active: viewMode === 'portfolio' }]" @click="viewMode = 'portfolio'">🎯 Portfolio</button>
      </div>

      <div class="topbar-right">
        <!-- INDmoney broker status -->
        <div class="ind-status connected" v-if="indmoneyConnected" :title="indmoneyName || 'INDmoney connected'">
          <span class="ind-dot"></span>
          <span class="ind-label">{{ indmoneyName || 'INDmoney' }}</span>
          <span class="ind-price" v-if="indmoneyLivePrice">₹{{ fmtPrice(indmoneyLivePrice) }}</span>
        </div>
        <button v-else class="ind-btn" @click="indmoneyOpen"
          :class="{ configured: indmoneyAvailable }"
          :title="indmoneyAvailable ? 'Token set — click to verify connection' : 'Open INDstocks to get your token'">
          <span class="ind-icon">📈</span>
          {{ indmoneyAvailable ? 'INDmoney ●' : 'Connect INDmoney' }}
        </button>
        <button class="theme-btn" @click="toggleTheme" :title="lightMode ? 'Switch to dark' : 'Switch to light'">
          {{ lightMode ? '🌙' : '☀️' }}
        </button>
        <span class="live-dot"></span>
        <span class="live-label">LIVE</span>
        <button class="sim-btn" @click="startQuickSim(chartTicker)" :disabled="simRunning || !chartTicker">
          <span v-if="simRunning" class="spinner"></span>
          <span v-else>⚡</span>
          {{ simRunning ? 'Simulating…' : 'Simulate' }}
        </button>
      </div>
    </header>

    <!-- ══ MAIN: EXCHANGES + CHART ═══════════════════════════════════════════ -->
    <div class="main-area" v-if="viewMode === 'chart'">

      <!-- LEFT: Watchlist -->
      <aside class="watchlist">
        <div class="wl-header">
          <!-- Asset class tabs -->
          <div class="asset-tabs">
            <button v-for="ac in assetClasses" :key="ac.v"
                    :class="['ac-btn', { active: assetClassFilter === ac.v }]"
                    @click="switchAssetClass(ac.v)">{{ ac.l }}</button>
          </div>
          <!-- Country / region filter (hidden in scanner mode) -->
          <div class="country-filters" v-if="assetClassFilter !== 'scanner'">
            <button v-for="c in activeCountries" :key="c"
                    :class="['cf-btn', { active: countryFilter === c }]"
                    @click="countryFilter = c">{{ c }}</button>
          </div>
          <!-- Scanner controls -->
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

        <div class="wl-loading" v-if="scanLoading || (indicesLoading && assetClassFilter !== 'scanner')">
          <span class="spinner"></span> {{ scanLoading ? 'Scanning universe…' : 'Fetching markets…' }}
        </div>

        <!-- Scanner results -->
        <div class="wl-list" v-else-if="assetClassFilter === 'scanner'">
          <!-- BUY picks -->
          <div class="scan-section-label buy-label" v-if="scanBuys.length">▲ TOP BUY PICKS</div>
          <div v-for="item in scanBuys" :key="item.symbol"
               :class="['wl-row scan-buy-row', { selected: selectedIndex === item.symbol }]"
               @click="selectIndex(item)">
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
          <!-- SELL picks -->
          <div class="scan-section-label sell-label" v-if="scanSells.length">▼ TOP SELL PICKS</div>
          <div v-for="item in scanSells" :key="item.symbol"
               :class="['wl-row scan-sell-row', { selected: selectedIndex === item.symbol }]"
               @click="selectIndex(item)">
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
          <!-- Rest -->
          <div class="scan-section-label" v-if="scanHolds.length">— HOLD / WATCH</div>
          <div v-for="item in scanHolds" :key="item.symbol"
               :class="['wl-row', { selected: selectedIndex === item.symbol }]"
               @click="selectIndex(item)">
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
          <div class="feed-empty" v-else-if="!scanResults.length && !scanLoading">Click Scan to find opportunities</div>
        </div>

        <!-- ═ WATCHLIST tab — pinned tickers (localStorage) ══════════════════ -->
        <div class="wl-list wl-watch" v-else-if="assetClassFilter === 'watchlist'">
          <div class="wl-watch-add">
            <input v-model="wlInput"
                   class="wl-watch-input mono"
                   placeholder="Add ticker (e.g. RELIANCE.NS)"
                   maxlength="32"
                   @keyup.enter="addToWatchlist(wlInput)" />
            <button class="wl-watch-btn" @click="addToWatchlist(wlInput)" :disabled="!wlInput">+ Add</button>
            <button class="wl-watch-btn wl-watch-pin" v-if="chartTicker"
                    @click="pinCurrentTicker"
                    :title="`Pin ${chartTicker} (currently charted) to your watchlist`">
              📌 Pin {{ chartTicker }}
            </button>
          </div>
          <div v-for="w in watchlist" :key="w.symbol"
               :class="['wl-row', { selected: selectedIndex === w.symbol }]"
               @click="selectIndex({ symbol: w.symbol, name: w.name })">
            <div class="wl-left">
              <span class="wl-flag">★</span>
              <div class="wl-names">
                <span class="wl-name">{{ w.name }}</span>
                <span class="wl-exch mono">{{ w.symbol }}<span v-if="w.exchange"> · {{ w.exchange }}</span></span>
              </div>
            </div>
            <div class="wl-right">
              <button class="wl-watch-rm" @click.stop="removeFromWatchlist(w.symbol)" title="Remove">✕</button>
            </div>
          </div>
          <div class="feed-empty" v-if="!watchlist.length">
            <div>Your watchlist is empty.</div>
            <div class="empty-sub">Type a ticker above, or click 📌 Pin while viewing any chart.</div>
          </div>
        </div>

        <!-- ═ BUDGET tab — LLM token consumption + cost ══════════════════════ -->
        <div class="wl-list wl-budget" v-else-if="assetClassFilter === 'budget'">
          <div class="bud-loading" v-if="budgetLoading && !budgetData"><span class="spinner"></span> Loading…</div>
          <template v-else-if="budgetData">
            <!-- Per-period tiles (persisted to SQLite — survives restarts).
                 Click a tile to filter the breakdowns below. -->
            <div class="bud-periods">
              <div v-for="p in [
                     { k: 'today',   l: 'Today (IST)' },
                     { k: 'month',   l: 'This Month'  },
                     { k: 'session', l: 'Session'     },
                     { k: 'all',     l: 'All-Time'    },
                   ]" :key="p.k"
                   :class="['bud-period-tile', { active: budgetPeriod === p.k }]"
                   @click="budgetPeriod = p.k"
                   :title="`Switch breakdowns to ${p.l}`">
                <div class="bud-label">{{ p.l }}</div>
                <div class="bud-val bud-cost">{{ fmtUSD(budgetData[p.k]?.total_cost_usd || 0) }}</div>
                <div class="bud-sub">{{ fmtINR(budgetData[p.k]?.total_cost_usd || 0) }}</div>
                <div class="bud-sub">{{ budgetData[p.k]?.total_calls || 0 }} calls · {{ fmtTokens(budgetData[p.k]?.total_tokens) }}</div>
              </div>
            </div>

            <!-- By model — uses the period selected via tile above -->
            <div class="bud-section-label">BY MODEL · {{ budgetPeriodLabel }}</div>
            <div v-for="(m, name) in (budgetData[budgetPeriod]?.by_model || {})" :key="name" class="bud-row">
              <div class="bud-row-name mono">{{ name }}</div>
              <div class="bud-row-meta">
                <span>{{ m.calls }} calls</span>
                <span>{{ fmtTokens(m.tokens) }} tok</span>
                <span class="bud-cost">{{ fmtUSD(m.cost_usd) }}</span>
              </div>
            </div>

            <!-- By agent -->
            <div class="bud-section-label">BY AGENT · {{ budgetPeriodLabel }}</div>
            <div v-for="(a, name) in (budgetData[budgetPeriod]?.by_agent || {})" :key="name" class="bud-row">
              <div class="bud-row-name">{{ name }}</div>
              <div class="bud-row-meta">
                <span>{{ a.calls }} calls</span>
                <span>{{ fmtTokens(a.tokens) }} tok</span>
                <span class="bud-cost">{{ fmtUSD(a.cost_usd) }}</span>
              </div>
            </div>

            <!-- Recent calls (last 20 in the selected period) -->
            <div class="bud-section-label" v-if="(budgetData[budgetPeriod]?.recent_entries || []).length">
              RECENT CALLS · {{ budgetPeriodLabel }}
            </div>
            <div v-for="(e, i) in (budgetData[budgetPeriod]?.recent_entries || [])" :key="i"
                 class="bud-recent">
              <span class="bud-recent-agent">{{ e.agent_id }}</span>
              <span class="bud-recent-tokens mono">{{ fmtTokens(e.total_tokens) }}</span>
              <span class="bud-recent-cost mono">{{ fmtUSD(e.cost_usd) }}</span>
            </div>

            <!-- Actions -->
            <div class="bud-actions">
              <button class="wl-watch-btn" @click="refreshBudget"
                      :title="'Refresh budget data — auto-refreshes every 30s while this tab is open.'">
                ⟳ Refresh
              </button>
              <button class="wl-watch-btn wl-watch-rm-btn" @click="clearBudget"
                      :title="'Reset all token counters to zero. Use this at the start of a new session to track only that session\'s spend.'">
                ✕ Reset
              </button>
            </div>
          </template>
          <div class="feed-empty" v-else>No LLM activity yet.</div>
        </div>

        <!-- Normal watchlist -->
        <div class="wl-list" v-else>
          <div v-for="idx in filteredAssets" :key="idx.symbol"
               :class="['wl-row', { selected: selectedIndex === idx.symbol }]"
               @click="selectIndex(idx)">
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
      </aside>

      <!-- CENTER: Chart + Feed -->
      <section class="chart-section" v-if="viewMode === 'chart'">
        <!-- Chart header stats -->
        <div class="chart-header" v-if="chartTicker">
          <span class="ch-ticker">{{ displayTicker }}</span>
          <span class="ch-company" v-if="tickerStats.company_name && tickerStats.company_name !== chartTicker">{{ tickerStats.company_name }}</span>
          <span class="ch-price" v-if="chartHeaderPrice"
                :title="indmoneyLivePrice ? 'Live tick from IndStocks SSE' : 'Last cached price (live stream not connected yet)'">
            {{ currencySymbol }}{{ chartHeaderPrice }}
          </span>
          <span :class="['ch-chg', chartHeaderChangePct >= 0 ? 'up' : 'dn']" v-if="chartHeaderChangePct !== null">
            {{ chartHeaderChangePct >= 0 ? '+' : '' }}{{ chartHeaderChangePct }}%
          </span>
          <span class="ch-vol" v-if="tickerStats.volume_ratio">Vol {{ tickerStats.volume_ratio }}x avg</span>
          <span class="ch-live" v-if="!chartLoading"><span class="ch-live-dot"></span>LIVE</span>
          <span class="ch-live ch-loading" v-if="chartLoading">⟳</span>
          <span class="ch-fomo" v-if="fomoScore !== null" :class="fomoClass" title="FOMO Score: measures retail investor excitement/panic. High score = everyone is rushing in (risky). Low score = ignored (potential opportunity).">Hype {{ fomoScore }}</span>
          <span v-if="chartTicker" :class="['ch-mkt', marketStatus.open ? 'ch-mkt-open' : 'ch-mkt-closed']"
                :title="marketStatus.open ? `${marketStatus.exchange} is trading now` : `${marketStatus.exchange} is closed${marketStatus.reason ? ' — ' + marketStatus.reason : ''}`">
            {{ marketStatus.label }}
          </span>
        </div>

        <div class="chart-wrap" ref="chartWrap" @wheel.prevent="onChartWheel">
          <div class="chart-empty" v-if="!ohlcv.length && !chartLoading">Enter a ticker above</div>
          <div class="chart-loading" v-if="chartLoading"><span class="spinner"></span></div>
          <svg v-show="ohlcv.length && !chartLoading" ref="svgRef" class="chart-svg"></svg>
          <!-- Zoom controls (top-right; show only once chart has data) -->
          <div v-if="ohlcv.length" class="chart-zoom-controls">
            <button @click="chartZoom(-1)" title="Zoom in (or scroll wheel up)">+</button>
            <button @click="chartZoom(+1)" title="Zoom out (or scroll wheel down)">−</button>
            <button @click="chartZoomReset" title="Reset to full range" :disabled="!chartVisibleCount">⟲</button>
            <span v-if="chartVisibleCount" class="chart-zoom-info" :title="'Showing last '+chartVisibleCount+' of '+ohlcv.length+' candles'">{{ chartVisibleCount }}/{{ ohlcv.length }}</span>
          </div>
          <div class="c-tooltip" v-show="tooltip.visible"
               :style="{ left: tooltip.x + 'px', top: tooltip.y + 'px' }">
            <div class="tt-date">{{ tooltip.date }}</div>
            <div class="tt-row"><span>O</span><span>{{ tooltip.open }}</span></div>
            <div class="tt-row"><span>H</span><span>{{ tooltip.high }}</span></div>
            <div class="tt-row"><span>L</span><span>{{ tooltip.low }}</span></div>
            <div class="tt-row"><span>C</span><span :class="tooltip.up ? 'up' : 'dn'">{{ tooltip.close }}</span></div>
            <div class="tt-row"><span>VOL</span><span>{{ tooltip.volume }}</span></div>
            <div v-if="tooltip.pattern" class="tt-pattern"
                 :style="{ color: tooltip.pattern.signal==='bull'?'#26a69a':tooltip.pattern.signal==='bear'?'#ef5350':'#fbbf24' }">
              <span class="tt-pat-name">{{ tooltip.pattern.signal==='bull'?'▲':tooltip.pattern.signal==='bear'?'▼':'◆' }} {{ tooltip.pattern.name }}</span>
              <span class="tt-pat-desc">{{ tooltip.pattern.desc }}</span>
            </div>
          </div>
        </div>

        <!-- Feed panel below chart -->
        <div class="feed-panel">
          <div class="feed-panel-header">
            <span class="live-dot"></span>
            <span class="fp-title">INTELLIGENCE FEED</span>
            <div class="intel-tabs">
              <button v-for="tab in intelTabs" :key="tab"
                      :class="['itab', { active: intelTab === tab }]"
                      @click="intelTab = tab">{{ tab }}</button>
            </div>
            <button class="ai-pred-btn" @click="runAiPredict" :disabled="aiPredLoading || !chartTicker">
              <span v-if="aiPredLoading" class="spinner"></span>
              <span v-else>⚡</span> {{ aiPredLoading ? 'Analysing…' : 'AI Predict' }}
            </button>
            <button class="sim-quick-btn" @click="startQuickSim(chartTicker)" :disabled="simRunning || !chartTicker">
              <span v-if="simRunning" class="spinner"></span>
              <span v-if="simRunning">Sim R{{ simRound }}/6</span>
              <span v-else>▶ Simulate</span>
            </button>
            <span v-if="signalsLoading" class="fp-loading"><span class="spinner"></span> loading…</span>
          </div>

          <!-- Horizontal card carousel -->
          <div class="feed-carousel" ref="feedRef">
            <TransitionGroup name="feed" tag="div" class="feed-cards-inner">
              <div v-for="item in visibleFeed" :key="item.id" :class="['feed-card', item.type]"
                   @mouseenter="showFeedTooltip(item, $event)" @mouseleave="hideFeedTooltip"
                   @mousemove="moveFeedTooltip($event)">
                <div class="fc-top">
                  <span class="fc-badge" :class="item.type">{{ badgeLabel(item.type) }}</span>
                  <span v-if="item.sentiment !== undefined"
                        :class="['fc-sent', item.sentiment >= 0.55 ? 'up' : item.sentiment < 0.45 ? 'dn' : 'neu']">
                    {{ item.sentiment >= 0.55 ? '▲ Bull' : item.sentiment < 0.45 ? '▼ Bear' : '— Neu' }}
                  </span>
                </div>
                <div class="fc-title">{{ item.title }}</div>
                <div class="fc-meta" v-if="item.meta">{{ item.meta }}</div>
              </div>
            </TransitionGroup>
            <div class="feed-empty" v-if="!visibleFeed.length && !signalsLoading">
              Select a ticker to load signals
            </div>
          </div>

          <!-- AI prediction bar -->
          <div class="pred-bar" v-if="prediction.ready">
            <span class="pred-label">AI PREDICTION</span>
            <span class="pred-item" :class="prediction.shortClass">SHORT: <b>{{ prediction.short }}</b> {{ prediction.shortPct }}%</span>
            <span class="pred-divider">|</span>
            <span class="pred-item" :class="prediction.longClass">LONG: <b>{{ prediction.long }}</b> {{ prediction.longPct }}%</span>
            <span class="pred-fomo" v-if="fomoScore !== null" title="Retail Hype Score: 0=nobody cares, 100=extreme excitement/panic">Hype Score {{ fomoScore }}/100</span>
          </div>
        </div>
      </section>

      <!-- RIGHT SIDEBAR: Signal+Levels | F&O Scanner | Backtest -->
      <aside class="right-sidebar" v-if="viewMode === 'chart'">

        <!-- Panel 1: SIGNAL & LEVELS (merged) -->
        <div class="rs-panel">
          <div class="rs-header hdr-signal">
            <span class="rs-title">SIGNAL &amp; LEVELS</span>
            <div class="rs-hdr-actions">
              <button class="rs-icon-btn" @click="runIntradaySignal" :disabled="signalLoading || !chartTicker" title="Get Signal">
                <span v-if="signalLoading" class="spinner"></span>
                <span v-else>⚡</span>
              </button>
              <button class="rs-icon-btn" @click="loadLevels" :disabled="levelsLoading || !chartTicker" title="Refresh Levels">
                <span v-if="levelsLoading" class="spinner"></span>
                <span v-else>⟳</span>
              </button>
            </div>
          </div>
          <div class="rs-empty" v-if="!signal && !levels && !signalLoading && !levelsLoading && !signalError">Momentum · Trend · Volume</div>
          <div class="rs-err" v-if="signalError && !signalLoading">⚠ {{ signalError }}</div>
          <div v-if="signal">
            <div :class="['rs-action', signal.action==='BUY'?'rs-act-buy':signal.action==='SELL'?'rs-act-sell':'rs-act-hold']">
              {{ signal.action }} <span class="rs-score">{{ signal.score }}/100</span>
            </div>
            <div class="rs-inds">
              <span>RSI <b :class="signal.rsi<35?'up':signal.rsi>65?'dn':''">{{ signal.rsi }}</b></span>
              <span>Trend <b :class="signal.ema_bull?'up':'dn'">{{ signal.ema_bull?'↑':'↓' }}</b></span>
              <span>VWAP <b :class="signal.above_vwap?'up':'dn'">{{ signal.above_vwap?'↑':'↓' }}</b></span>
              <span>VOL <b :class="signal.vol_ratio>=1.5?'up':signal.vol_ratio<=0.6?'dn':''">{{ signal.vol_ratio }}x</b></span>
            </div>
          </div>
          <div v-if="levels">
            <div class="rs-bias" :class="levels.bias.toLowerCase()">{{ levels.bias }} · RSI {{ levels.rsi }}</div>
            <div class="rs-lv-rows">
              <div class="rs-lv t3"><span>T3</span><span>{{ fmtPrice(levels.target_3) }}</span></div>
              <div class="rs-lv t2"><span>T2</span><span>{{ fmtPrice(levels.target_2) }}</span></div>
              <div class="rs-lv t1"><span>T1</span><span>{{ fmtPrice(levels.target_1) }}</span></div>
              <div class="rs-lv en"><span>ENTRY</span><span>{{ fmtPrice(levels.entry) }}</span></div>
              <div class="rs-lv sl"><span>SL</span><span>{{ fmtPrice(levels.stop_loss) }}</span></div>
            </div>
            <div class="rs-lv-foot">Qty {{ levels.recommended_qty }} · Risk {{ currencySymbol }}{{ levels.capital_at_risk }}</div>
          </div>
          <div v-if="lastTradeMsg">
            <div :class="['rs-result', lastTradeAction==='BUY'?'rs-res-buy':lastTradeAction==='SELL'?'rs-res-sell':'rs-res-hold']">
              <div class="rs-res-top">
                <b class="rs-res-action">{{ lastTradeAction }}</b>
                <span class="rs-res-conf" v-if="lastTradeConf">{{ lastTradeConf }}%</span>
              </div>
              <div class="rs-res-reason">{{ lastTradeReason }}</div>
              <div class="rs-res-levels" v-if="lastTradeSL || lastTradeT1">
                <span v-if="lastTradeSL" class="rrl-sl">SL {{ currencySymbol }}{{ fmtPrice(lastTradeSL) }}</span>
                <span v-if="lastTradeT1" class="rrl-t1">T1 {{ currencySymbol }}{{ fmtPrice(lastTradeT1) }}</span>
              </div>
              <div class="rs-res-signals" v-if="lastTradeSignals.length">
                <span v-for="s in lastTradeSignals.slice(0,4)" :key="s" class="rs-sig-tag">{{ s }}</span>
              </div>
              <div v-if="lastTradeExecuted" class="rs-trade-status rs-trade-done">
                <span v-if="lastTradeExecuted.action==='BUY'">✅ Bought {{ lastTradeExecuted.qty }} @ {{ currencySymbol }}{{ fmtPrice(lastTradeExecuted.price) }}</span>
                <span v-else>✅ Sold <b :class="lastTradeExecuted.pnl>=0?'up':'dn'">{{ lastTradeExecuted.pnl>=0?'+':'' }}{{ currencySymbol }}{{ lastTradeExecuted.pnl?.toFixed(0) }}</b></span>
              </div>
              <div v-else-if="lastTradeSkipped" class="rs-trade-status rs-trade-skip">⏸ {{ lastTradeSkipped }}</div>
            </div>
          </div>
        </div>

        <!-- Panel 2: F&O SCANNER -->
        <div class="rs-panel">
          <div class="rs-header hdr-scanner">
            <span class="rs-title">F&amp;O SCANNER</span>
            <div class="rs-hdr-actions">
              <button class="fo-scan-now"
                      @click="triggerScan"
                      :disabled="!!foAnalysing"
                      :title="foAnalysing ? `Scanning ${foAnalysing.ticker_clean} (${foAnalysing.index}/${foAnalysing.total})` : 'Run a one-shot scan across all indices right now (LLM cost ~$0.05)'">
                <span v-if="foAnalysing">
                  <span class="lv-spin">◐</span>
                  Scanning {{ foAnalysing.ticker_clean }} ({{ foAnalysing.index }}/{{ foAnalysing.total }})
                </span>
                <span v-else>⚡ Scan now</span>
              </button>
              <span class="fo-auto-indicator"
                    :class="{
                      'fo-auto-live':   scannerRunning && nseMarketOpen,
                      'fo-auto-paused': scannerRunning && !nseMarketOpen,
                    }"
                    :title="!scannerRunning ? 'Connecting to scanner…'
                            : nseMarketOpen ? `Live — auto-scanning every ${foIntervalLabel}`
                            : 'NSE market is closed (09:15–15:30 IST). Scanner thread is alive but cycles are paused. Click ⚡ to force a one-shot scan on cached data.'">
                <span class="fo-auto-dot"></span>
                {{ !scannerRunning ? 'connecting…'
                   : nseMarketOpen   ? `LIVE · ${foIntervalLabel}`
                   :                   'PAUSED · NSE CLOSED' }}
              </span>
            </div>
          </div>
          <!-- Currently analysing ticker -->
          <div class="fo-analysing" v-if="foAnalysing">
            <span class="fo-spin">⟳</span>
            <span class="fo-analysing-sym">{{ foAnalysing.ticker_clean }}</span>
            <span class="fo-analysing-prog">{{ foAnalysing.index }}/{{ foAnalysing.total }}</span>
          </div>
          <div class="rs-empty" v-if="!foFeed.length && !foAnalysing">
            <span v-if="nseMarketOpen">{{ foUniverseSize }} tickers · auto every {{ foIntervalLabel }} · ⚡ to scan now</span>
            <span v-else>🌙 NSE closed · auto-scan paused · ⚡ to force-scan on cached data</span>
          </div>
          <div class="fo-feed" v-if="foFeed.length">
            <div v-for="(ev, i) in foFeed.filter(e=>e.type!=='scan_analysing').slice(0,30)"
                 :key="(ev.ticker||'')+'|'+(ev.type||'')+'|'+(ev.timestamp||i)"
                 :class="['fo-evt', 'fo-evt-'+ev.type, ev.verdict?'fo-evt-verdict-'+(ev.verdict||'').replace(/ /g,'_').toLowerCase():'', (ev.option_symbol||ev.ticker)?'fo-evt-clickable':'']"
                 :title="transactionTooltip(ev) || (ev.option_symbol ? 'Click to chart ' + ev.option_symbol + ' premium' : ev.ticker ? 'Click to view ' + ev.ticker + ' chart' : '')"
                 @click="(ev.option_symbol || ev.ticker) && selectTicker(ev.option_symbol || ev.ticker)">
              <!-- Badge -->
              <span v-if="ev.type==='scan_trade'" class="fo-evt-badge fo-badge-scan_trade">TRADE</span>
              <span v-else-if="ev.type==='scan_signal'" :class="['fo-evt-badge', 'fo-badge-verdict-'+verdictClass(ev.verdict)]">{{ shortVerdict(ev.verdict) }}</span>
              <span v-else-if="ev.type==='scan_skip'" class="fo-evt-badge fo-badge-scan_skip">SKIP</span>
              <span v-else-if="ev.type==='position_exit'" :class="['fo-evt-badge', (ev.pnl||0)>=0?'fo-badge-win':'fo-badge-loss']">{{ (ev.pnl||0)>=0?'WIN':'LOSS' }}</span>

              <!-- Option contract symbol (primary) — always show if available -->
              <span class="fo-sym fo-sym-option" v-if="ev.option_symbol">{{ ev.option_symbol }}</span>
              <span class="fo-sym" v-else>{{ (ev.ticker||ev.trading_symbol||ev.underlying||'').replace(/\.(NS|BO)$/i,'').replace(/^\^/,'') }}</span>
              <!-- Underlying reference when option symbol is shown -->
              <span class="fo-underlying" v-if="ev.option_symbol">{{ (ev.ticker||ev.underlying||'').replace(/\.(NS|BO)$/i,'').replace(/^\^/,'') }}</span>

              <!-- Instrument type (CE / PE / FUT / EQ) -->
              <span class="fo-inst-tag" v-if="ev.instrument||ev.instrument_type" :class="'itype-'+(ev.instrument||ev.instrument_type||'').toLowerCase()">{{ ev.instrument||ev.instrument_type }}</span>

              <!-- Strike (shown on all signal types, not just trade) -->
              <span class="fo-strike" v-if="ev.strike">@{{ ev.strike }}</span>

              <!-- Confidence -->
              <span :class="['fo-conf', ev.confidence>=70?'conf-hi':ev.confidence>=50?'conf-mid':'conf-lo']" v-if="ev.confidence!=null">{{ ev.confidence }}%</span>

              <!-- Live option premium from IndMoney / Greeks-based estimate -->
              <span class="fo-option-ltp" v-if="ev.option_ltp">₹{{ Number(ev.option_ltp).toFixed(0) }}</span>

              <!-- Greeks row: delta + expiry (from ticket) -->
              <span class="fo-greeks" v-if="ev.ticket">
                Δ{{ ev.ticket.greeks?.delta?.toFixed(2) }}
                · θ₹{{ Math.abs(ev.ticket.greeks?.theta_per_day||0).toFixed(0) }}/d
                · {{ ev.ticket.days_to_expiry }}DTE
              </span>

              <!-- SL / T1 / T2 — prefer ticket exit (option premium ₹), fall back to underlying levels -->
              <span class="fo-levels" v-if="ev.ticket?.exit"
                    :title="'Stop-loss / Target-1 / Target-2 in option PREMIUM. Exit when premium hits SL (loss) or T1/T2 (profit).'">
                SL ₹{{ ev.ticket.exit.stop_loss_inr?.toFixed(0) }}
                · T1 ₹{{ ev.ticket.exit.target_1_inr?.toFixed(0) }}
                <span v-if="ev.ticket.exit.target_2_inr"> · T2 ₹{{ ev.ticket.exit.target_2_inr?.toFixed(0) }}</span>
              </span>
              <span class="fo-levels" v-else-if="ev.stop_loss || ev.target_1"
                    :title="'Stop-loss / Target on the UNDERLYING price (not option premium). Use as reference; option SL/T1 are computed from these.'">
                <span v-if="ev.stop_loss">SL {{ ev.stop_loss }}</span>
                <span v-if="ev.target_1"> · T1 {{ ev.target_1 }}</span>
                <span v-if="ev.target_2"> · T2 {{ ev.target_2 }}</span>
              </span>

              <!-- Entry/premium for executed trades -->
              <span class="fo-entry" v-if="ev.type==='scan_trade'&&ev.premium">₹{{ ev.premium }}</span>
              <!-- P&L for exits -->
              <span :class="['fo-pnl', (ev.pnl||0)>=0?'up':'dn']" v-if="ev.pnl!=null">{{ ev.pnl>=0?'+':'' }}₹{{ Math.abs(ev.pnl||0).toFixed(0) }}</span>
              <!-- Skip reason -->
              <span class="fo-skip-reason" v-if="ev.type==='scan_skip'&&ev.reason">{{ ev.reason.replace('Outside safe trading hours (09:15-15:00)','⏰ hours').replace(/^Confidence \d+ < \d+$/,'low conf').replace(/^Already holding/,'dup pos') }}</span>
              <!-- Real broker transaction (BUY CE / BUY PE / SELL FUT) -->
              <span :class="['fo-action', transactionLabel(ev).startsWith('🔴')?'fo-action-sell':'fo-action-buy']"
                    v-if="ev.action && ev.type!=='scan_skip'"
                    :title="transactionTooltip(ev)">{{ transactionLabel(ev) }}</span>

              <!-- Add-to-Watching button: pin this scanner suggestion as a tracked
                   position so SL/T1/T2 alerts fire on every tick.
                   Requires a full ticket (plan_option_trade succeeded). -->
              <button v-if="ev.ticket?.trading_symbol && ev.type!=='scan_skip' && !isTracked(ev.ticket.trading_symbol)"
                      class="fo-watch-btn"
                      @click.stop="trackEntered(ev.ticket)"
                      :title="`Add ${ev.ticket.trading_symbol} to WATCHING — server-side tick monitor will fire SL/T1/T2/time-exit alerts even when this tab is closed.`">
                👁 Watch
              </button>
              <span v-else-if="ev.ticket?.trading_symbol && isTracked(ev.ticket.trading_symbol)"
                    class="fo-watch-pinned"
                    :title="'Already tracking — see WATCHING section below'">📌 Watching</span>
            </div>
          </div>
          <div class="fo-scan-status" v-if="foScannerState?.last_scan">
            Last scan: {{ fmtTime(foScannerState.last_scan) }}
            <span v-if="foScannerState.trades_placed?.length" class="fo-trades-count"> · 🟢 {{ foScannerState.trades_placed.length }} trades</span>
          </div>
        </div>

        <!-- ═══ Panel: LIVE TRADING (tick-driven, real-time) ═══════════════ -->
        <div class="rs-panel rs-live">
          <div class="rs-header hdr-live">
            <span class="rs-title" title="Tick-driven trade tickets, option chain, and per-position monitor">⚡ LIVE</span>
            <span class="lv-pulse" :class="{ 'lv-pulse-on': liveConnected }" :title="liveConnected?'tick stream connected':'disconnected'"></span>
            <span class="lv-clock">{{ liveClock }}</span>
          </div>

          <!-- Live scanner progress bar — visible only while a scan is running -->
          <div v-if="foAnalysing" class="lv-scan-bar" :title="'Scanning '+foAnalysing.ticker_clean">
            <div class="lv-scan-bar-fill" :style="{ width: ((foAnalysing.index / foAnalysing.total) * 100) + '%' }"></div>
            <span class="lv-scan-bar-txt">⟳ {{ foAnalysing.ticker_clean }} — {{ foAnalysing.index }}/{{ foAnalysing.total }}</span>
          </div>

          <!-- Empty state — chain not yet loaded AND no tickets/positions -->
          <div v-if="!liveOptionChain && !chainLoading && !liveTicketCards.length && !Object.keys(liveStatus).length" class="lv-empty">
            <div class="lv-empty-line">No live data yet</div>
            <div class="lv-empty-sub">
              <button class="lv-empty-btn" @click="triggerScan" :disabled="!!foAnalysing">
                {{ foAnalysing ? 'Scanning…' : '⚡ Run scanner now' }}
              </button>
              <div v-if="foScannerState?.last_scan" style="margin-top:6px;color:#586069;font-size:9px">
                last scan: {{ fmtTime(foScannerState.last_scan) }}
                <span v-if="foScannerState.trades_placed?.length"> · 🟢 {{ foScannerState.trades_placed.length }} trades</span>
              </div>
            </div>
          </div>

          <!-- ═ Trade Ticket cards — one per index, re-pricing independently on each tick -->
          <div v-for="liveTicket in liveTicketCards" :key="liveTicket._underlying"
               :class="['lv-ticket', 'lv-ticket-' + (liveTicket.option_type==='CE'?'ce':'pe')]">
            <!-- Header: contract symbol, bias, expiry -->
            <div class="lv-ticket-head">
              <span class="lv-ticket-sym" :title="'Contract symbol: ' + liveTicket.trading_symbol">{{ liveTicket.trading_symbol }}</span>
              <span :class="['lv-ticket-bias', 'lv-bias-' + liveTicket.bias?.toLowerCase()]" :title="TT.bias">{{ liveTicket.bias }} {{ liveTicket.option_type }}</span>
              <span class="lv-ticket-exp" :title="TT.dte + ' · ' + TT.lot">{{ liveTicket.expiry }} · {{ liveTicket.days_to_expiry }}DTE · lot {{ liveTicket.lot_size }}</span>
            </div>

            <!-- LIVE BANNER: current premium vs entry, %change, pulse -->
            <div v-if="liveTicket.live" class="lv-ticket-live">
              <div class="lv-ticket-live-row">
                <span class="lv-live-label" :title="TT.premium_now">
                  <span class="lv-tick-dot" :class="{ 'lv-tick-fresh': liveTicket._tick_age_sec < 4 }"></span>
                  LIVE premium
                </span>
                <span class="lv-live-prem">₹{{ liveTicket.live.premium_now }}</span>
                <span class="lv-tick-age" :title="'Time since last underlying tick. Ticket re-prices every 2s. Age &gt; 8s usually means market is closed or stream stalled.'">
                  {{ liveTicket._tick_age_sec ? liveTicket._tick_age_sec + 's ago' : 'waiting…' }}
                </span>
                <span :class="['lv-live-chg', liveTicket.live.pct_from_entry>=0?'up':'dn']"
                      :title="'P&amp;L vs entry per unit. Multiply by lot ('+liveTicket.lot_size+') for per-lot ₹.'">
                  {{ liveTicket.live.pct_from_entry>=0?'+':'' }}{{ liveTicket.live.pct_from_entry }}%
                  · {{ liveTicket.live.pnl_per_lot>=0?'+':'' }}₹{{ liveTicket.live.pnl_per_lot?.toLocaleString('en') }}/lot
                </span>
              </div>
              <div class="lv-ticket-live-row lv-ticket-live-sub">
                <span :class="['lv-mny', 'lv-mny-' + (liveTicket.live.moneyness||'').toLowerCase()]"
                      :title="liveTicket.live.moneyness==='ITM' ? TT.itm : TT.otm">{{ liveTicket.live.moneyness }}</span>
                <span :title="'Spot ₹'+liveTicket.live.spot_now+' vs Strike '+liveTicket.strike+' — points away from being ATM'">
                  {{ Math.abs(liveTicket.live.dist_to_strike) }} pts {{ liveTicket.live.dist_to_strike>=0?'below':'above' }} strike
                </span>
                <span :title="'How far the live premium is from each exit level. Negative % = level is below current premium.'">
                  → T1 {{ liveTicket.live.pct_to_t1>=0?'+':'' }}{{ liveTicket.live.pct_to_t1 }}%
                  · SL {{ liveTicket.live.pct_to_sl }}%
                </span>
              </div>
            </div>

            <!-- Static plan: spot at signal time, strike, entry, SL, T1, T2 -->
            <div class="lv-ticket-grid">
              <div class="lv-tk-cell" :title="TT.spot + ' Snapshot at the moment the signal was generated.'">
                <div class="lv-lab">Spot</div><div class="lv-val">₹{{ liveTicket.spot?.toLocaleString('en') }}</div>
              </div>
              <div class="lv-tk-cell" :title="TT.strike">
                <div class="lv-lab">Strike</div><div class="lv-val">{{ liveTicket.strike }}</div>
              </div>
              <div class="lv-tk-cell" :title="TT.entry">
                <div class="lv-lab">Entry</div><div class="lv-val lv-entry">₹{{ liveTicket.entry?.expected_premium_inr }}</div>
              </div>
              <div class="lv-tk-cell" :title="TT.sl">
                <div class="lv-lab">SL</div><div class="lv-val lv-sl">₹{{ liveTicket.exit?.stop_loss_inr }}</div>
              </div>
              <div class="lv-tk-cell" :title="TT.t1">
                <div class="lv-lab">T1</div><div class="lv-val lv-t1">₹{{ liveTicket.exit?.target_1_inr }}</div>
              </div>
              <div class="lv-tk-cell" :title="TT.t2">
                <div class="lv-lab">T2</div><div class="lv-val lv-t2">₹{{ liveTicket.exit?.target_2_inr }}</div>
              </div>
            </div>

            <!-- LIVE Greeks row (recomputes on every tick) -->
            <div class="lv-ticket-greeks">
              <span :title="TT.delta">Δ <b>{{ (liveTicket.live?.delta ?? liveTicket.greeks?.delta)?.toFixed(2) }}</b></span>
              <span :title="TT.theta">θ/d <b>₹{{ (liveTicket.live?.theta_per_day ?? liveTicket.greeks?.theta_per_day)?.toFixed(2) }}</b></span>
              <span :title="TT.iv">IV <b>{{ ((liveTicket.live?.iv_used ?? liveTicket.greeks?.iv_used ?? 0)*100).toFixed(1) }}%</b></span>
              <span :title="TT.be">BE <b>{{ liveTicket.risk?.breakeven_spot }}</b></span>
              <span class="lv-loss" :title="TT.max_loss">max -₹{{ liveTicket.risk?.max_loss_inr?.toLocaleString('en',{maximumFractionDigits:0}) }}</span>
              <span v-if="liveTicket.risk?.risk_reward_ratio !== undefined"
                    :class="['lv-rr', liveTicket.risk.risk_reward_ratio>=1?'good':liveTicket.risk.risk_reward_ratio>=0.5?'ok':'bad']"
                    :title="TT.rr">R:R 1:{{ liveTicket.risk.risk_reward_ratio }}</span>
            </div>

            <!-- Theta-warning banner when system flagged the trade as theta-trap -->
            <div v-if="liveTicket.risk?.theta_warning" class="lv-theta-warn" :title="TT.warning">
              ⚠️ {{ liveTicket.risk.theta_warning }}
            </div>

            <!-- Footer: timing window, order type, time-exit -->
            <div class="lv-ticket-foot">
              <span class="lv-window" :title="'Recommended entry window. After this, theta + low momentum hurt.'">⏱ {{ liveTicket.entry?.window_ist }}</span>
              <span class="lv-order-type" :title="liveTicket.entry?.order_type==='MARKET' ? 'Spread is tight — MARKET order is safe.' : 'Spread wide — use LIMIT @ mid to avoid slippage.'">{{ liveTicket.entry?.order_type }}</span>
              <span class="lv-time-exit" :title="'Forced exit time — close before broker auto-square-off / theta cliff.'">exit by {{ liveTicket.exit?.time_exit_ist }}</span>
            </div>

            <!-- Manual position tracker — "I entered this trade" -->
            <div class="lv-ticket-track">
              <button v-if="!isTracked(liveTicket.trading_symbol)"
                      class="lv-track-btn"
                      @click="trackEntered(liveTicket)"
                      :title="'Pin this contract — I executed this trade in my real broker. The card stays here regardless of new AI verdicts and continues to live-reprice + fire SL/T1/T2/time-exit alerts until I click Exited.'">
                ✋ I entered this
              </button>
              <span v-else class="lv-track-pinned" :title="'Tracking — see WATCHING section below'">📌 Tracking active</span>
            </div>
          </div>

          <!-- ═ WATCHING — positions the user manually entered, persists across refreshes -->
          <div v-if="trackedCards.length" class="lv-watch">
            <div class="lv-watch-head">
              <span class="lv-watch-title">👁 WATCHING ({{ trackedCards.length }})</span>
              <span :class="['lv-watch-dot', alertsConnected ? 'lv-dot-on' : 'lv-dot-off']"
                    :title="alertsConnected ? 'Server-side watcher LIVE — alerts will fire even if this tab is hidden' : 'Server alerts disconnected — only browser-side beeps'"></span>
              <button class="lv-watch-test-sound" @click="_playExitAlert('past_t1')"
                      :title="'Play a test beep (also unlocks audio — browser blocks beeps until first user gesture)'">🔔 Test sound</button>
              <button v-if="notifPermission !== 'granted'" class="lv-watch-test-sound"
                      @click="requestNotifPermission"
                      :title="'Allow desktop popups so SL/T1/T2 alerts reach you even when this tab is in background'">
                🔔 Enable desktop alerts
              </button>
              <span v-else class="lv-watch-sub" style="color:#26a69a;font-weight:700">desktop alerts ON</span>
              <span class="lv-watch-sub">positions you entered — server watches SL/T1/T2/15:00</span>
            </div>

            <!-- "While you were away" panel — server-side alerts that fired while this tab was closed/hidden -->
            <div v-if="missedAlerts.length" class="lv-missed">
              <div class="lv-missed-head">
                <span>📬 While you were away ({{ missedAlerts.length }})</span>
                <button class="lv-missed-clear" @click="clearMissedAlerts">clear</button>
              </div>
              <div v-for="(a, i) in missedAlerts.slice(0, 5)" :key="i" class="lv-missed-row">
                <span :class="'lv-missed-tag lv-missed-' + a.status">{{ a.status.replace('_', ' ').toUpperCase() }}</span>
                <span class="lv-missed-sym">{{ a.trading_symbol }}</span>
                <span class="lv-missed-msg">{{ a.message }}</span>
                <span class="lv-missed-time">{{ _fmtAlertTime(a.timestamp) }}</span>
              </div>
            </div>

            <div v-for="card in trackedCards" :key="card.id"
                 :class="['lv-watch-card', 'lv-watch-' + card.status]">
              <div class="lv-watch-row">
                <span class="lv-watch-sym">{{ card.trading_symbol }}</span>
                <span :class="['lv-watch-badge', 'lv-watch-' + card.status]">{{ card.statusLabel }}</span>
                <button :class="['lv-watch-exit', isExitArmed(card.id) && 'lv-watch-exit-armed']"
                        @click="trackExited(card)"
                        :title="isExitArmed(card.id) ? 'Click again to confirm exit' : 'Mark this position as exited. Click twice to confirm.'">
                  {{ isExitArmed(card.id) ? '⚠ Click again' : '✓ Exited' }}
                </button>
              </div>
              <div class="lv-watch-row lv-watch-prices">
                <span :title="'Entry premium (when you clicked I entered)'">entry <b>₹{{ card.entry_premium?.toFixed(2) }}</b></span>
                <span :title="'Live premium — Black-Scholes repriced on every spot tick. Pulsing dot = live ticks flowing.'">
                  <span :class="['lv-watch-tick', card.is_live && 'lv-watch-tick-on']"
                        :title="card.is_live ? `Tracking LIVE — last tick ${card.tick_age}s ago` : 'No tick yet — server polling'"></span>
                  now <b :class="card.pnl_pct >= 0 ? 'up' : 'dn'">₹{{ card.now_premium?.toFixed(2) }}</b>
                </span>
                <span :class="['lv-watch-pnl', card.pnl_pct >= 0 ? 'up' : 'dn']"
                      :title="`P&L = (now − entry) × lot ${card.lot} × qty ${card.qty}. Same formula for CE and PE buyers — premium up = profit.`">
                  {{ card.pnl_inr >= 0 ? '+' : '−' }}₹{{ Math.abs(card.pnl_inr || 0).toFixed(0) }}
                  <small>({{ card.pnl_pct >= 0 ? '+' : '' }}{{ card.pnl_pct?.toFixed(2) }}%)</small>
                </span>
              </div>
              <div class="lv-watch-row lv-watch-levels"
                   :title="'These SL/T1/T2 are FROZEN from the ticket you entered. New scanner signals do NOT update them — they always reflect your original plan.'">
                <span class="lv-watch-sl" :title="'Hard stop loss (50% premium floor enforced) — frozen from entry'">SL ₹{{ card.sl?.toFixed(2) }}</span>
                <span class="lv-watch-t1" :title="'First profit target — exit 50% partial here'">T1 ₹{{ card.t1?.toFixed(2) }}</span>
                <span class="lv-watch-t2" :title="'Final profit target — exit remainder'">T2 ₹{{ card.t2?.toFixed(2) }}</span>
              </div>
              <div v-if="card.alert" class="lv-watch-alert">⚠ {{ card.alert }}</div>
            </div>
          </div>

          <!-- ═ Option chain — transitional states (fetch in progress / errored) -->
          <div v-if="!liveOptionChain && (chainLoading || chainError)" class="lv-chain">
            <div class="lv-chain-head">
              <span class="lv-chain-title">📊 OPTION CHAIN</span>
              <select class="lv-chain-sel" v-model="chainUnderlying">
                <option value="^NSEI">NIFTY 50</option>
                <option value="^NSEBANK">BANKNIFTY</option>
              </select>
              <span v-if="chainLoading" class="lv-chain-meta"><span class="lv-spin">◐</span> loading {{ chainUnderlying }}…</span>
              <span v-else-if="chainError" class="lv-chain-meta" style="color:#ef5350" :title="chainError">⚠ {{ chainError }}</span>
            </div>
          </div>

          <!-- ═ Option chain — live-priced ladder of strikes around ATM ───── -->
          <div v-if="liveOptionChain" class="lv-chain">
            <div class="lv-chain-head">
              <span class="lv-chain-title">📊 OPTION CHAIN</span>
              <select class="lv-chain-sel" v-model="chainUnderlying" :title="'Pick underlying'">
                <option value="^NSEI">NIFTY 50</option>
                <option value="^NSEBANK">BANKNIFTY</option>
              </select>
              <span class="lv-chain-meta">
                <span :class="['lv-chain-dot', liveOptionChain.is_live && 'lv-chain-dot-on']"
                      :title="liveOptionChain.is_live ? 'live tick stream connected' : 'no tick yet'"></span>
                spot <b style="color:#e6edf3">₹{{ liveOptionChain.spot.toLocaleString('en-IN') }}</b>
                <span class="lv-chain-age" :title="'seconds since last broker tick'">{{ liveOptionChain.tick_age }}s</span>
                · exp {{ liveOptionChain.expiry }} ({{ liveOptionChain.dte }}DTE)
                · IV {{ liveOptionChain.iv_pct }}%
                · lot {{ liveOptionChain.lot }}
              </span>
              <!-- Sentiment / smart-money strip — inline meaning, no hover needed -->
              <div class="lv-chain-sentiment" v-if="liveOptionChain.pcr_oi != null || liveOptionChain.max_pain">
                <span v-if="liveOptionChain.pcr_oi != null"
                      :class="['lv-chain-pcr', liveOptionChain.pcr_oi > 1.3 ? 'pcr-bull' : liveOptionChain.pcr_oi < 0.7 ? 'pcr-bear' : 'pcr-neutral']">
                  <span class="sm-lab">PCR <small>(Put/Call)</small></span>
                  <b>{{ liveOptionChain.pcr_oi }}</b>
                  <span class="sm-meaning">
                    {{ liveOptionChain.pcr_oi > 1.3 ? '→ put-heavy, contrarian BULLISH'
                       : liveOptionChain.pcr_oi < 0.7 ? '→ call-heavy, contrarian BEARISH'
                       : '→ balanced sentiment' }}
                  </span>
                </span>
                <span v-if="liveOptionChain.max_pain" class="lv-chain-mp">
                  <span class="sm-lab">Max Pain</span>
                  <b>{{ liveOptionChain.max_pain }}</b>
                  <span class="sm-meaning">
                    → spot magnet near expiry
                    ({{ liveOptionChain.spot > liveOptionChain.max_pain ? 'spot above, expect drift down' : liveOptionChain.spot < liveOptionChain.max_pain ? 'spot below, expect drift up' : 'at magnet' }})
                  </span>
                </span>
                <span v-if="liveOptionChain.peak_oi_strike" class="lv-chain-peak">
                  <span class="sm-lab">Peak OI</span>
                  <b>{{ liveOptionChain.peak_oi_strike }}</b>
                  <span class="sm-meaning">→ strongest support/resistance level (price likely to bounce off this strike)</span>
                </span>
              </div>
              <label class="lv-chain-filter">
                <input type="checkbox" v-model="chainBuyOnly">
                Hide deep OTM strikes
                <span class="lv-chain-filter-hint">(only show strikes with |Δ| ≥ 0.30 — i.e. real chance of profit, not lottery tickets)</span>
              </label>
            </div>
            <!-- Verdict tier legend — explains the labels in the chain rows below -->
            <div class="lv-chain-legend">
              <span><b class="lv-verdict-conservative">CONSERVATIVE</b> deep ITM, safest, lowest leverage</span>
              <span><b class="lv-verdict-balanced">BALANCED</b> ATM, best risk/reward for directional bets</span>
              <span><b class="lv-verdict-aggressive">AGGRESSIVE</b> slight OTM, cheap + high leverage</span>
              <span><b class="lv-verdict-lottery">LOTTERY</b> deep OTM, theta will eat you alive — avoid</span>
              <span><b class="lv-verdict-expensive">EXPENSIVE</b> too deep ITM, use futures instead</span>
            </div>
            <div class="lv-chain-grid">
              <div class="lv-chain-th">CE Δ</div>
              <div class="lv-chain-th">CE ₹</div>
              <div class="lv-chain-th lv-chain-th-strike">Strike</div>
              <div class="lv-chain-th">PE ₹</div>
              <div class="lv-chain-th">PE Δ</div>
              <template v-for="r in liveOptionChain.rows" :key="r.strike">
                <!-- Row 1 — premium + delta + AI-pick highlight -->
                <div :class="['lv-chain-cell', 'lv-chain-mny-' + r.ce_moneyness.toLowerCase(), r.ce_is_pick && 'lv-chain-pick']"
                     :title="r.ce_symbol + (r.ce_is_pick ? ' (AI pick)' : '')">{{ r.ce_delta?.toFixed(2) }}</div>
                <div :class="['lv-chain-cell', 'lv-chain-cep', r.ce_dir && ('lv-flash-' + r.ce_dir), r.ce_is_pick && 'lv-chain-pick']"
                     :title="r.ce_symbol + (r.ce_is_pick ? ' — AI pick (see ticket above)' : '')">
                  <span v-if="r.ce_is_pick" class="lv-chain-pick-mark" title="AI-recommended strike">★</span>
                  ₹{{ r.ce_premium?.toFixed(2) ?? '—' }}
                </div>
                <div :class="['lv-chain-cell', 'lv-chain-strike', r.is_atm?'lv-chain-atm':'']">
                  {{ r.strike.toLocaleString('en-IN') }}
                </div>
                <div :class="['lv-chain-cell', 'lv-chain-pep', r.pe_dir && ('lv-flash-' + r.pe_dir), r.pe_is_pick && 'lv-chain-pick']"
                     :title="r.pe_symbol + (r.pe_is_pick ? ' — AI pick (see ticket above)' : '')">
                  <span v-if="r.pe_is_pick" class="lv-chain-pick-mark" title="AI-recommended strike">★</span>
                  ₹{{ r.pe_premium?.toFixed(2) ?? '—' }}
                </div>
                <div :class="['lv-chain-cell', 'lv-chain-mny-' + r.pe_moneyness.toLowerCase(), r.pe_is_pick && 'lv-chain-pick']"
                     :title="r.pe_symbol + (r.pe_is_pick ? ' (AI pick)' : '')">{{ r.pe_delta?.toFixed(2) }}</div>
                <!-- Row 2 — verdict + cost + breakeven (sub-row) -->
                <div :class="['lv-chain-sub', 'lv-verdict-' + r.ce_verdict.toLowerCase()]"
                     :title="'Buy quality: ' + r.ce_verdict + ' (based on |Δ|)'">{{ r.ce_verdict }}</div>
                <div class="lv-chain-sub" :title="'Cost per lot — Premium × Lot. Spot must reach ₹'+r.ce_be+' to break even.'">
                  ₹{{ r.ce_cost?.toLocaleString('en-IN') ?? '—' }}<span class="lv-chain-be"> · BE {{ r.ce_be?.toLocaleString('en-IN') }}</span>
                </div>
                <div class="lv-chain-sub lv-chain-sub-mid"></div>
                <div class="lv-chain-sub" :title="'Cost per lot. Spot must drop to ₹'+r.pe_be+' to break even.'">
                  ₹{{ r.pe_cost?.toLocaleString('en-IN') ?? '—' }}<span class="lv-chain-be"> · BE {{ r.pe_be?.toLocaleString('en-IN') }}</span>
                </div>
                <div :class="['lv-chain-sub', 'lv-verdict-' + r.pe_verdict.toLowerCase()]"
                     :title="'Buy quality: ' + r.pe_verdict + ' (based on |Δ|)'">{{ r.pe_verdict }}</div>

                <!-- Row 3 — Open Interest per side + strike-level PCR (matches demat app layout) -->
                <div class="lv-chain-oi lv-chain-oi-ce" :title="'Call OI: ' + (r.ce_oi?.toLocaleString('en-IN') || 0) + ' contracts'">
                  OI {{ fmtIndian(r.ce_oi) }}
                </div>
                <div class="lv-chain-oi lv-chain-oi-vol" v-if="r.ce_volume" :title="'CE volume today: ' + r.ce_volume.toLocaleString('en-IN')">
                  Vol {{ fmtIndian(r.ce_volume) }}
                </div>
                <div class="lv-chain-oi lv-chain-oi-vol" v-else></div>
                <div class="lv-chain-oi lv-chain-oi-pcr"
                     :title="'PCR at this strike = PE OI / CE OI. >1 means more put open interest (support); <1 means more call interest (resistance).'">
                  <span v-if="r.ce_oi > 0">PCR {{ (r.pe_oi / r.ce_oi).toFixed(2) }}</span>
                </div>
                <div class="lv-chain-oi lv-chain-oi-vol" v-if="r.pe_volume" :title="'PE volume today: ' + r.pe_volume.toLocaleString('en-IN')">
                  Vol {{ fmtIndian(r.pe_volume) }}
                </div>
                <div class="lv-chain-oi lv-chain-oi-vol" v-else></div>
                <div class="lv-chain-oi lv-chain-oi-pe" :title="'Put OI: ' + (r.pe_oi?.toLocaleString('en-IN') || 0) + ' contracts'">
                  OI {{ fmtIndian(r.pe_oi) }}
                </div>
              </template>
            </div>
          </div>

          <!-- ═ Per-position live tick cards -->
          <div v-for="(s, k) in liveStatus" :key="k" :class="['lv-pos', s.decision?'lv-pos-decision':'']">
            <div class="lv-pos-head">
              <span class="lv-pos-sym">{{ k }}</span>
              <span :class="['lv-pnl', (s.pnl_pct||0)>=0?'up':'dn']">{{ (s.pnl_pct||0)>=0?'+':'' }}{{ s.pnl_pct?.toFixed(2) }}%</span>
              <span class="lv-pos-prem">₹{{ s.premium }}</span>
              <span class="lv-pos-hwm" v-if="s.high_water > s.avg_entry">peak ₹{{ s.high_water }}</span>
            </div>
            <!-- Premium ladder (SL ── now ── T1 ── T2) -->
            <div class="lv-ladder">
              <div class="lv-ladder-track">
                <div class="lv-ladder-fill" :style="{ left: ladderPct(s, 'left'), width: ladderPct(s, 'width') }"></div>
                <div class="lv-ladder-pin" :style="{ left: ladderPct(s, 'now') }">●</div>
              </div>
              <div class="lv-ladder-legend">
                <span class="lv-sl">SL ₹{{ s.sl }}</span>
                <span class="lv-entry">entry ₹{{ s.avg_entry }}</span>
                <span class="lv-t1">T1 ₹{{ s.t1 }}</span>
                <span class="lv-t2">T2 ₹{{ s.t2 }}</span>
              </div>
            </div>
            <div class="lv-pos-greeks" v-if="s.greeks">
              <span>Δ {{ s.greeks.delta?.toFixed(2) }}</span>
              <span>θ/d ₹{{ s.greeks.theta_per_day?.toFixed(2) }}</span>
              <span>IV {{ ((s.greeks.iv_used||0)*100).toFixed(1) }}%</span>
              <span v-if="s.trail_sl">trail ₹{{ s.trail_sl }}</span>
            </div>
            <div v-if="s.decision" class="lv-decision" :class="'lv-dec-'+s.decision.action.toLowerCase()">
              <span v-if="s.decision.action==='EXIT_FULL'">🔴 EXIT NOW</span>
              <span v-else-if="s.decision.action==='EXIT_PARTIAL'">🟡 SELL HALF</span>
              <span class="lv-dec-reason">{{ s.decision.reason }}</span>
            </div>
          </div>

          <!-- ═ AI Commentary ticker -->
          <div class="lv-commentary" v-if="latestCommentary">
            <div class="lv-comm-head">🗨 AI commentary <span class="lv-comm-ts">{{ fmtTime(latestCommentary.ts) }}</span></div>
            <div class="lv-comm-text">{{ latestCommentary.text }}</div>
          </div>
        </div>

        <!-- Panel 4: BACKTEST (collapsible, collapsed by default) -->
        <div class="rs-panel">
          <div class="rs-header hdr-backtest" @click="btExpanded=!btExpanded" style="cursor:pointer">
            <span class="rs-title">BACKTEST</span>
            <div class="rs-hdr-actions">
              <button class="rs-icon-btn" @click.stop="runBacktest" :disabled="btLoading || !chartTicker" title="Run backtest">
                <span v-if="btLoading" class="spinner"></span>
                <span v-else>▶</span>
              </button>
              <span style="font-size:10px;color:#8b949e;padding:0 4px">{{ btExpanded?'▲':'▼' }}</span>
            </div>
          </div>
          <template v-if="btExpanded">
            <div class="rs-empty" v-if="!btResult && !btLoading && !btError">Test 4 strategies on 1 year of data</div>
            <div class="rs-err"   v-if="btError && !btLoading">⚠ {{ btError }}</div>
            <div class="rs-loading" v-if="btLoading"><span class="spinner"></span> Testing strategies…</div>
            <div v-if="btResult" class="bt-results">
              <div class="bt-bh-row">Buy &amp; hold: <b :class="btResult.buy_and_hold_pct>=0?'up':'dn'">{{ btResult.buy_and_hold_pct>=0?'+':'' }}{{ btResult.buy_and_hold_pct }}%</b></div>
              <div v-for="(s, name) in btResult.strategies" :key="name"
                   :class="['bt-card', name===btResult.best_strategy?'bt-winner':'', s.error?'bt-err':'']">
                <template v-if="!s.error">
                  <div class="bt-card-top">
                    <span class="bt-grade" :class="btGradeClass(s)">{{ btGrade(s) }}</span>
                    <span class="bt-card-name">{{ btShortName(name) }}</span>
                    <span :class="['bt-ret', s.total_return_pct>=0?'up':'dn']">{{ s.total_return_pct>=0?'+':'' }}{{ s.total_return_pct }}%</span>
                  </div>
                  <div class="bt-card-line">{{ s.total_trades }} trades · {{ s.win_rate_pct }}% wins · DD {{ s.max_drawdown_pct }}%</div>
                  <div class="bt-bar-wrap">
                    <div class="bt-bar" :class="s.total_return_pct>=0?'bt-bar-up':'bt-bar-dn'"
                         :style="{ width: Math.min(100, Math.abs(s.total_return_pct) * 2) + '%' }"></div>
                    <div class="bt-bar-bh" :style="{ left: Math.min(100, Math.abs(btResult.buy_and_hold_pct) * 2) + '%' }"></div>
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

      </aside>

    </div>

    <!-- ══ ANALYSIS VIEW (full-screen, analysis mode) ════════════════════════ -->
    <div class="analysis-view" v-if="viewMode === 'analysis'">

      <!-- Status bar -->
      <div class="av-statusbar">
        <span class="av-ticker">{{ displayTicker }}</span>
        <span class="av-company" v-if="investData?.company">{{ investData.company }}</span>
        <span class="av-price" v-if="investData?.price">{{ currencySymbol }}{{ investData.price }}</span>
        <span class="av-agents" v-if="investData?.agent_count">
          {{ investData.agent_count }} agents
          <span v-if="investData.real_agent_count" class="av-real-tag">· {{ investData.real_agent_count }} real</span>
        </span>
        <span class="av-verdict-pill" v-if="investData?.final?.final_verdict"
              :class="'vc-' + verdictKey(investData.final.final_verdict)">
          {{ investData.final.final_verdict }} · {{ investData.final.consensus_score }}% consensus
        </span>
        <span class="av-horizon" v-if="investData?.final?.time_horizon">{{ investData.final.time_horizon }}</span>
        <div class="av-spacer"></div>
        <button class="av-run-btn" @click="loadInvestAnalysis" :disabled="investLoading || !chartTicker">
          <span v-if="investLoading" class="spinner"></span>
          <span v-else>⚡</span>
          {{ investLoading ? (investAgentsTotal > 0 ? `${investAgentsDone}/${investAgentsTotal} agents…` : investPhase || 'Starting…') : 'Run Analysis' }}
        </button>
      </div>

      <!-- Main body: graph left + report right -->
      <div class="av-body">

        <!-- D3 graph (large) -->
        <div class="av-graph-col">
          <div class="av-graph-wrap" ref="investGraphWrap">
            <svg ref="investGraphSvg" class="av-svg"></svg>
          </div>
          <!-- Legend -->
          <div class="av-legend" v-if="investData">
            <span class="avl-item"><span class="avl-dot" style="background:#22c55e"></span>Strong Buy</span>
            <span class="avl-item"><span class="avl-dot" style="background:#86efac"></span>Buy</span>
            <span class="avl-item"><span class="avl-dot" style="background:#fbbf24"></span>Hold</span>
            <span class="avl-item"><span class="avl-dot" style="background:#f97316"></span>Sell</span>
            <span class="avl-item"><span class="avl-dot" style="background:#ef4444"></span>Strong Sell</span>
            <span class="avl-item"><span class="avl-dot" style="background:#f87171;border-radius:0"></span>Risk</span>
          </div>
          <div class="av-graph-empty" v-if="!investData && !investLoading && !investError">
            Click "Run Analysis" to launch {{ chartTicker }} through 20 expert AI agents
          </div>
          <div class="av-graph-empty" v-if="investError && !investLoading" style="color:#ef5350">
            ⚠ {{ investError }}
          </div>
          <div class="av-graph-loading" v-if="investLoading">
            <span class="spinner"></span>
            Extracting real entities → Running parallel expert analyses → Bull vs Bear debate → CIO verdict…
          </div>
        </div>

        <!-- Report panel (scrollable) -->
        <div class="av-report-col" v-if="investData">

          <!-- SHORT-TERM ACTION (what to do today) -->
          <div class="avr-action" v-if="investData.final?.short_term_action"
               :class="'vc-' + verdictKey(investData.final.final_verdict)">
            <div class="avract-label">TODAY'S ACTION</div>
            <div class="avract-value">{{ investData.final.short_term_action }}</div>
            <div class="avract-reason">{{ investData.final.short_term_reason }}</div>
          </div>

          <!-- Final verdict -->
          <div class="avr-verdict" :class="'vc-' + verdictKey(investData.final?.final_verdict)">
            <div class="avrv-main">
              <span class="avrv-label">{{ investData.final?.final_verdict }}</span>
              <span class="avrv-consensus">{{ investData.final?.consensus_score }}% consensus</span>
            </div>
            <div class="avrv-counts">
              <span class="avrv-bull">▲ {{ investData.final?.bull_count }} bullish</span>
              <span class="avrv-bear">▼ {{ investData.final?.bear_count }} bearish</span>
              <span class="avrv-neut">— {{ investData.final?.neutral_count }} neutral</span>
            </div>
          </div>

          <!-- Entry / exit levels -->
          <div class="avr-levels">
            <div class="avrl-row entry"><span>ENTRY</span><span>{{ currencySymbol }}{{ investData.final?.entry_price?.toFixed(2) }}</span></div>
            <div class="avrl-row sl"><span>STOP LOSS</span><span>{{ currencySymbol }}{{ investData.final?.stop_loss?.toFixed(2) }}</span></div>
            <div class="avrl-row t1"><span>TARGET 1</span><span>{{ currencySymbol }}{{ investData.final?.target_1?.toFixed(2) }}</span></div>
            <div class="avrl-row t2"><span>TARGET 2</span><span>{{ currencySymbol }}{{ investData.final?.target_2?.toFixed(2) }}</span></div>
            <div class="avrl-row t3"><span>TARGET 3</span><span>{{ currencySymbol }}{{ investData.final?.target_3?.toFixed(2) }}</span></div>
            <div class="avrl-row pos"><span>POSITION SIZE</span><span>{{ investData.final?.position_size_pct }}% of portfolio</span></div>
          </div>

          <!-- Thesis -->
          <div class="avr-thesis">
            <div class="avrt-title">Investment Thesis</div>
            <p>{{ investData.final?.investment_thesis }}</p>
            <div class="avrt-bull" v-if="investData.final?.bull_case">🟢 Bull: {{ investData.final.bull_case }}</div>
            <div class="avrt-bear" v-if="investData.final?.bear_case">🔴 Bear: {{ investData.final.bear_case }}</div>
          </div>

          <!-- Adversarial Debate -->
          <div class="avr-debate" v-if="investData.debate?.bull_arg || investData.debate?.bear_arg">
            <div class="avrd-header">
              <span class="avrd-title">⚔ ADVERSARIAL DEBATE</span>
              <span class="avrd-counts">
                <span class="avrd-bull-count">▲ {{ investData.debate.bull_agent_count }} bulls</span>
                <span class="avrd-bear-count">▼ {{ investData.debate.bear_agent_count }} bears</span>
              </span>
            </div>
            <div class="avrd-side avrd-bull" v-if="investData.debate.bull_arg">
              <div class="avrd-side-label">🟢 BULL ADVOCATE</div>
              <div class="avrd-side-text">{{ investData.debate.bull_arg }}</div>
            </div>
            <div class="avrd-side avrd-bear" v-if="investData.debate.bear_arg">
              <div class="avrd-side-label">🔴 BEAR ADVOCATE</div>
              <div class="avrd-side-text">{{ investData.debate.bear_arg }}</div>
            </div>
          </div>

          <!-- Agent cards -->
          <div class="avr-agents-header">
            <span>AGENT VERDICTS</span>
            <span class="avra-count">{{ Object.keys(investData.agents || {}).length }} analysts</span>
          </div>
          <div class="avr-agents-list">
            <div v-for="(result, name) in investData.agents" :key="name"
                 class="avra-card" :class="'vc-' + verdictKey(result.verdict)"
                 @click="selectedAgent = selectedAgent?.agent === name ? null : { ...result, label: name }">
              <div class="avra-top">
                <span class="avra-verdict">{{ result.verdict }}</span>
                <span class="avra-conf">{{ result.confidence }}%</span>
                <span class="avra-name">{{ name }}</span>
              </div>
              <div class="avra-firm">{{ result.role }} · {{ result.firm }}</div>
              <div class="avra-reason">{{ result.reasoning }}</div>
              <div v-if="selectedAgent?.agent === name" class="avra-findings">
                <div v-for="f in (result.key_findings || [])" :key="f">• {{ f }}</div>
                <div class="avra-levels-row" v-if="result.entry_price">
                  Entry {{ currencySymbol }}{{ result.entry_price?.toFixed(2) }} · Stop Loss {{ currencySymbol }}{{ result.stop_loss?.toFixed(2) }} · Target {{ currencySymbol }}{{ result.target_price?.toFixed(2) }}
                </div>
              </div>
            </div>
          </div>

          <!-- Key risks -->
          <div class="avr-risks" v-if="investData.final?.key_risks?.length">
            <div class="avrr-title">⚠ KEY RISKS</div>
            <div v-for="r in investData.final.key_risks" :key="r" class="avrr-item">{{ r }}</div>
          </div>

        </div>

        <!-- Empty state when no data yet -->
        <div class="av-report-empty" v-if="!investData && !investLoading">
          Run analysis to see the full report
        </div>

      </div>
    </div>

    <!-- ══ PORTFOLIO ALLOCATOR VIEW ═════════════════════════════════════════ -->
    <!-- PORTFOLIO ALLOCATOR TAB -->
    <div class="port-panel" v-show="viewMode==='portfolio'">

      <!-- ── Config card ────────────────────────────────────────────────── -->
      <div class="port-config-card" v-if="!portfolioLoading && !portfolioResult">

        <!-- Capital -->
        <div class="pcc-section">
          <div class="pcc-label">💰 How much do you want to invest?</div>
          <div class="pcc-denom-row">
            <button v-for="d in portfolioDenoms" :key="d.val"
                    :class="['pcc-denom-btn', portfolioCapital===d.val && !portfolioCustomMode ? 'active' : '']"
                    @click="portfolioCapital=d.val; portfolioCustomMode=false">
              {{ d.label }}
            </button>
            <button :class="['pcc-denom-btn', portfolioCustomMode?'active':'']"
                    @click="portfolioCustomMode=true; $nextTick(()=>$refs.customInput?.focus())">
              Custom
            </button>
          </div>
          <div class="pcc-custom-row" v-if="portfolioCustomMode">
            <span class="pcc-rupee">₹</span>
            <input ref="customInput" v-model.number="portfolioCapital" type="number" min="100" step="100"
                   class="pcc-custom-input" placeholder="Enter amount" />
            <span class="pcc-custom-hint">{{ portfolioCapital ? '= ' + formatCapital(portfolioCapital) : '' }}</span>
          </div>
          <div class="pcc-capital-display" v-else>
            <span class="pcc-amt">₹{{ portfolioCapital.toLocaleString('en-IN') }}</span>
            <span class="pcc-amt-words">{{ formatCapital(portfolioCapital) }}</span>
          </div>
        </div>

        <!-- Timeline -->
        <div class="pcc-section">
          <div class="pcc-label">📅 Investment horizon</div>
          <div class="pcc-timeline-row">
            <button v-for="h in portfolioHorizons" :key="h.val"
                    :class="['pcc-hz-btn', portfolioHorizon===h.val?'active':'']"
                    @click="portfolioHorizon=h.val">
              <span class="pcc-hz-dur">{{ h.label }}</span>
              <span class="pcc-hz-desc">{{ h.desc }}</span>
            </button>
          </div>
        </div>

        <!-- Market scope -->
        <div class="pcc-section">
          <div class="pcc-label">🎯 Market focus</div>
          <div class="pcc-scope-row">
            <button v-for="s in portfolioScopes" :key="s.val"
                    :class="['pcc-scope-btn', portfolioScope===s.val?'active':'']"
                    @click="portfolioScope=s.val">
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

      <!-- ── Running state ──────────────────────────────────────────────── -->
      <div class="port-running" v-if="portfolioLoading">
        <div class="prn-top">
          <div class="prn-capital">₹{{ portfolioCapital.toLocaleString('en-IN') }}</div>
          <div class="prn-meta">
            {{ portfolioHorizons.find(h=>h.val===portfolioHorizon)?.label }} ·
            {{ portfolioScopes.find(s=>s.val===portfolioScope)?.label }}
          </div>
        </div>
        <div class="prn-rounds">
          <div :class="['prn-round', portfolioRound>=1?'done':portfolioRound===0?'':'']">
            <span class="prn-r-num">1</span>
            <span class="prn-r-label">Independent picks</span>
          </div>
          <div class="prn-arrow">→</div>
          <div :class="['prn-round', portfolioRound>=2?'done':portfolioRound===1?'active':'']">
            <span class="prn-r-num">2</span>
            <span class="prn-r-label">Cross-debate</span>
          </div>
          <div class="prn-arrow">→</div>
          <div :class="['prn-round', portfolioRound>=3?'done':portfolioRound===2?'active':'']">
            <span class="prn-r-num">3</span>
            <span class="prn-r-label">PM synthesis</span>
          </div>
        </div>
        <div class="port-feed">
          <div v-for="(ev, i) in [...portfolioFeed].reverse().slice(0,14)" :key="i"
               :class="['pf-item', 'pf-'+ev.type]">
            <span v-if="ev.type==='round_start'" class="pf-round-badge">{{ ev.label }}</span>
            <template v-else-if="ev.type==='agent_action'">
              <span class="pf-agent">{{ ev.agent }}</span>
              <span class="pf-city" v-if="ev.city">· {{ ev.city }}</span>
              <span class="pf-msg">{{ ev.msg?.replace(ev.agent+' ('+ev.city+'): ','').replace(ev.agent+': ','') }}</span>
            </template>
            <span v-else class="pf-msg">{{ ev.msg }}</span>
          </div>
        </div>
      </div>

      <!-- ── Results ────────────────────────────────────────────────────── -->
      <div class="port-results" v-if="portfolioResult && !portfolioLoading">

        <!-- Results header bar -->
        <div class="prr-topbar">
          <div class="prr-meta">
            <span class="prr-capital">₹{{ portfolioCapital.toLocaleString('en-IN') }}</span>
            <span class="prr-dot">·</span>
            <span>{{ portfolioHorizons.find(h=>h.val===portfolioHorizon)?.label }}</span>
            <span class="prr-dot">·</span>
            <span>{{ portfolioScopes.find(s=>s.val===portfolioScope)?.label }}</span>
            <span class="prr-dot">·</span>
            <span>30 agents · 3 rounds</span>
          </div>
          <button class="prr-reset-btn" @click="portfolioResult=null; portfolioError=''">← New Simulation</button>
        </div>

        <!-- Stats row -->
        <div class="port-stats" v-if="portfolioResult.portfolio">
          <div class="pst-i"><div class="pst-v">{{ portfolioResult.portfolio.diversification_score || '—' }}</div><div class="pst-l">Diversification</div></div>
          <div class="pst-i"><div class="pst-v up">+{{ portfolioResult.portfolio.expected_return_pct || '—' }}%</div><div class="pst-l">Expected Return</div></div>
          <div class="pst-i"><div class="pst-v dn">-{{ portfolioResult.portfolio.max_risk_pct || '—' }}%</div><div class="pst-l">Max Risk</div></div>
          <div class="pst-i"><div class="pst-v">₹{{ Math.round(portfolioResult.portfolio.cash_kept || 0).toLocaleString('en-IN') }}</div><div class="pst-l">Cash Buffer</div></div>
        </div>

        <!-- Summary -->
        <div class="prr-summary" v-if="portfolioResult.portfolio?.summary">
          {{ portfolioResult.portfolio.summary }}
        </div>

        <!-- Allocation table -->
        <div class="port-table-wrap" v-if="portfolioResult.portfolio?.portfolio?.length">
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
              <tr v-for="p in portfolioResult.portfolio.portfolio" :key="p.ticker"
                  :class="['port-row', p.ticker==='CASH'?'port-row-cash':'']">
                <td>
                  <span class="port-ac-tag" :class="'pac-'+p.asset_class?.toLowerCase().replace(/\s+/g,'-').replace(/[^a-z-]/g,'')">
                    {{ p.asset_class }}
                  </span>
                </td>
                <td class="port-ticker" @click="p.ticker!=='CASH' && selectTicker(p.ticker)" :style="p.ticker!=='CASH'?'cursor:pointer':''">
                  {{ p.ticker?.split('.')[0] }}
                </td>
                <td>
                  <div class="port-alloc-bar-wrap">
                    <div class="port-alloc-bar" :style="{width: Math.min(p.allocation_pct||0, 100)+'%'}"></div>
                    <span>{{ p.allocation_pct }}%</span>
                  </div>
                </td>
                <td>₹{{ Math.round(p.allocation_amt||0).toLocaleString('en-IN') }}</td>
                <td class="mono">{{ p.entry ? '₹'+p.entry : '—' }}</td>
                <td class="mono dn">{{ p.stop_loss ? '₹'+p.stop_loss : '—' }}</td>
                <td class="mono up">{{ p.target_1 ? '₹'+p.target_1 : '—' }}</td>
                <td class="mono up">{{ p.target_2 ? '₹'+p.target_2 : '—' }}</td>
                <td class="port-tf">{{ p.timeframe }}</td>
                <td>
                  <span class="port-conf" :class="(p.confidence||0)>=75?'conf-hi':(p.confidence||0)>=55?'conf-mid':'conf-lo'">
                    {{ p.confidence || '—' }}%
                  </span>
                </td>
              </tr>
            </tbody>
          </table>

          <!-- Rationale cards -->
          <div class="port-rationale-grid">
            <div v-for="p in portfolioResult.portfolio.portfolio.filter(x=>x.ticker!=='CASH')" :key="'r'+p.ticker" class="port-rat-card">
              <div class="prc-header">
                <b>{{ p.ticker?.split('.')[0] }}</b>
                <span class="port-conf" :class="(p.confidence||0)>=75?'conf-hi':'conf-mid'">{{ p.verdict }}</span>
                <span class="prc-agent">{{ p.agent }}</span>
              </div>
              <div class="prc-body">{{ p.rationale }}</div>
            </div>
          </div>
        </div>
      </div>

      <div class="port-error" v-if="portfolioError">
        <span>⚠ {{ portfolioError }}</span>
        <button class="prr-reset-btn" @click="portfolioError=''; portfolioResult=null">Try again</button>
      </div>
    </div>


  <!-- Feed card floating tooltip -->
  <Teleport to="body">
    <div class="fc-tooltip" v-show="feedTip.visible"
         :style="{ left: feedTip.x + 'px', top: feedTip.y + 'px' }">
      <div class="fc-tt-title">{{ feedTip.title }}</div>
      <div class="fc-tt-meta" v-if="feedTip.meta">{{ feedTip.meta }}</div>
    </div>
  </Teleport>

  </div>
</template>

<script setup>
import { ref, computed, onMounted, onUnmounted, watch, nextTick } from 'vue'
import * as d3 from 'd3'
import { getWorldIndices, getOHLCV, getSignals, getAiPredict, launchQuickSim, getQuickSimStatus, scanUniverse, getEntityGraph, getTradeLevels, getInvestAnalysis, createInvestAnalysisStream, getIntradaySignal, runVbtBacktest, searchTicker, createPortfolioSimStream, getIndmoneyStatus, getIndmoneyTick, getOptionChain, getBudget, resetBudget } from '../api/market'
import { repriceTicket, GLOSSARY } from '../utils/blackScholes'

// ── State ─────────────────────────────────────────────────────────────────────
const activeTicker   = ref('^NSEI')
const chartTicker    = ref('')
const interval       = ref('1d')
const selectedIndex  = ref('')
const countryFilter  = ref('ALL')

const indices         = ref([])   // world indices (legacy)
const allAssets       = ref([])   // full universe with prices
const scanResults     = ref([])
const scanMarket      = ref('india')
const scanError       = ref('')
const indicesLoading  = ref(false)
const scanLoading     = ref(false)
const assetClassFilter = ref('index')

const ohlcv          = ref([])
const chartLoading   = ref(false)
// Zoom: number of most-recent candles to show (0 = all)
const chartVisibleCount = ref(0)
function chartZoom(direction) {
  const total = ohlcv.value.length
  if (!total) return
  const cur = chartVisibleCount.value || total
  // direction = -1 (in) → fewer candles ; +1 (out) → more candles
  const factor = direction < 0 ? 0.7 : 1.4
  let next = Math.round(cur * factor)
  next = Math.max(15, Math.min(total, next))
  chartVisibleCount.value = next >= total ? 0 : next
  drawChart()
}
function chartZoomReset() { chartVisibleCount.value = 0; drawChart() }
function onChartWheel(e) {
  if (!ohlcv.value.length) return
  e.preventDefault()
  chartZoom(e.deltaY > 0 ? +1 : -1)
}
const tickerStats    = ref({})
const fomoScore      = ref(null)
const redditSent     = ref(null)

const feedItems      = ref([])
const signalsLoading = ref(false)
const intelTab       = ref('ALL')

const chartWrap = ref(null)
const svgRef    = ref(null)
const feedRef   = ref(null)

const tooltip    = ref({ visible: false, x: 0, y: 0, date: '', open: '', high: '', low: '', close: '', volume: '', up: true, pattern: null })
const feedTip    = ref({ visible: false, x: 0, y: 0, title: '', meta: '' })

function showFeedTooltip(item, e) {
  const card     = e.currentTarget
  const titleEl  = card.querySelector('.fc-title')
  const metaEl   = card.querySelector('.fc-meta')
  const titleClipped = titleEl && titleEl.scrollHeight > titleEl.clientHeight + 1
  const metaClipped  = metaEl  && metaEl.scrollWidth  > metaEl.clientWidth  + 1
  if (!titleClipped && !metaClipped) return
  feedTip.value = {
    visible: true,
    x: e.clientX + 14, y: e.clientY + 14,
    title: titleClipped ? (item.title || '') : '',
    meta:  metaClipped  ? (item.meta  || '') : '',
  }
}
function moveFeedTooltip(e) {
  if (!feedTip.value.visible) return
  const tipW = 300, tipH = 100
  feedTip.value.x = e.clientX + 14 + tipW > window.innerWidth  ? e.clientX - tipW - 8 : e.clientX + 14
  feedTip.value.y = e.clientY + 14 + tipH > window.innerHeight ? e.clientY - tipH - 8 : e.clientY + 14
}
function hideFeedTooltip() { feedTip.value.visible = false }

let refreshTimer    = null
let aiPredTimer     = null
let simPollTimer    = null
let chartTimer      = null
let feedIdCounter   = 0
let liveDrawPending = false   // unused — kept for safety

const activeSimId   = ref(null)
const simRound      = ref(0)
const simRunning    = ref(false)
const simAgents     = ref(0)

// ── Graph state ───────────────────────────────────────────────────────────────
const graphNodes    = ref([])
const graphLinks    = ref([])
const graphLoading  = ref(false)
const graphWrap     = ref(null)
const graphSvg      = ref(null)

const graphTypes = [
  { type: 'ticker',  color: '#00d4a8' },
  { type: 'company', color: '#3d9eff' },
  { type: 'person',  color: '#ffb300' },
  { type: 'sector',  color: '#a78bfa' },
  { type: 'event',   color: '#ff4757' },
  { type: 'concept', color: '#888'    },
]
const graphColorMap = Object.fromEntries(graphTypes.map(t => [t.type, t.color]))

// ── Scanner computed splits ───────────────────────────────────────────────────
const scanBuys  = computed(() => scanResults.value.filter(r => r.action.includes('BUY')))
const scanSells = computed(() => scanResults.value.filter(r => r.action.includes('SELL')))
const scanHolds = computed(() => scanResults.value.filter(r => r.action === 'HOLD'))

// ── Currency & display helpers ────────────────────────────────────────────────
// Default to ₹ since this is an Indian-market trading app — only fall back to $
// for unambiguously US tickers (^DJI, ^GSPC, AAPL, etc). The previous logic
// inverted this and required every Indian index to be whitelisted, leaving
// ^CNXFIN, ^NSEMDCP50, ^INDIAVIX, ^CNXPHARMA, ^CNXIT (and the world-indices
// tray) all incorrectly showing $.
const US_INDICES = new Set([
  '^DJI', '^GSPC', '^IXIC', '^RUT', '^VIX',
  '^FTSE', '^GDAXI', '^N225', '^HSI',
])
const US_TICKER_RE = /\.(US|L|TO|HK|DE|PA|MI|AS|SW)$/i
const currencySymbol = computed(() => {
  const t = (chartTicker.value || activeTicker.value || '').toUpperCase()
  if (!t) return '₹'
  // Explicit US/world index → $ (USD-quoted)
  if (US_INDICES.has(t)) return '$'
  // Foreign-exchange-suffixed ticker → $
  if (US_TICKER_RE.test(t)) return '$'
  // Bare AAPL/MSFT-style US tickers (no suffix, no caret, 1-5 letters,
  // and NOT a known Indian-index/F&O pattern) → $
  // Indian F&O contracts always have hyphens (NIFTY-MAY2026-24100-CE),
  // and Indian indices always start with ^. So a clean uppercase string
  // with no hyphen and no caret is most likely a US equity.
  if (/^[A-Z]{1,5}$/.test(t) && !t.startsWith('^')) {
    // Heuristic — but harmless since user can disambiguate by typing the
    // full Indian symbol (RELIANCE.NS, SBIN.NS) which the .NS suffix
    // handles below.
    return '$'
  }
  return '₹'
})
// Strip .NS / .BO suffix for display — show "SBIN" not "SBIN.NS"
const displayTicker = computed(() =>
  (chartTicker.value || '').replace(/\.(NS|BO)$/i, '')
)

const chartHeaderPrice = computed(() => {
  const live   = Number(indmoneyLivePrice.value)
  const static_ = Number(tickerStats.value?.price)
  const p = live > 0 ? live : (static_ > 0 ? static_ : null)
  return p ? p.toLocaleString('en-IN', { maximumFractionDigits: 2 }) : null
})

const chartHeaderChangePct = computed(() => {
  const live = Number(indmoneyLivePrice.value)
  const prev = Number(tickerStats.value?.prev_close) || Number(tickerStats.value?.price_open)
  if (live > 0 && prev > 0) return Number(((live - prev) / prev * 100).toFixed(2))
  // Fallback to whatever the backend returned in tickerStats
  if (tickerStats.value?.change_1d !== undefined) return Number(tickerStats.value.change_1d)
  return null
})

// ── View mode ─────────────────────────────────────────────────────────────────
const viewMode = ref('chart')   // 'chart' | 'analysis'

// ── Investment analysis state ─────────────────────────────────────────────────
const investData         = ref(null)
const investLoading      = ref(false)
const investError        = ref('')
const selectedAgent      = ref(null)
const investPhase        = ref('')      // current streaming phase label
const investAgentsDone   = ref(0)      // agents completed so far
const investAgentsTotal  = ref(0)      // total agents expected
let   investStream       = null        // active EventSource
const investGraphWrap = ref(null)
const investGraphSvg  = ref(null)

// ── Trade levels state ────────────────────────────────────────────────────────
const levels        = ref(null)
const levelsLoading = ref(false)

// ── Trade UI state (no wallet — live broker mode) ────────────────────────────────────
const aiTradeLoading = ref(false)
const lastTradeMsg      = ref('')
const lastTradeAction   = ref('')

// Market open status — computed client-side with holiday awareness.
// IMPORTANT: this computed depends on the wall clock, but `new Date()` is
// not a reactive dependency. Without an explicit reactive trigger Vue would
// cache the result and never re-evaluate when 09:15/15:30 IST is crossed,
// so the "NSE OPEN/CLOSED" badge would only update on hard page refresh.
// `liveClock` is a ref that ticks every 1s via tickClock() in onMounted —
// reading it here registers it as a dep so the computed re-runs each tick.
// NSE-specific market status — always evaluated regardless of charted
// ticker. Used by the F&O scanner badge and any other India-only widget.
// Reactive on liveClock so it flips at 09:15:00 / 15:30:00 IST exactly.
const nseMarketOpen = computed(() => {
  liveClock.value                                    // 1Hz reactive dep
  const NSE_HOLIDAYS = new Set([
    '2025-01-26','2025-03-14','2025-04-14','2025-04-18',
    '2025-05-01','2025-08-15','2025-08-27','2025-10-02',
    '2025-10-20','2025-10-21','2025-11-05','2025-12-25',
    '2026-01-26','2026-03-02','2026-04-03','2026-04-14',
    '2026-05-01','2026-08-17','2026-09-15','2026-10-02',
    '2026-11-03','2026-11-25','2026-12-25',
  ])
  const parts = Object.fromEntries(
    new Intl.DateTimeFormat('en-US', {
      timeZone: 'Asia/Kolkata',
      year: 'numeric', month: '2-digit', day: '2-digit',
      weekday: 'short', hour: '2-digit', minute: '2-digit', hour12: false,
    }).formatToParts(new Date()).map(p => [p.type, p.value])
  )
  if (parts.weekday === 'Sat' || parts.weekday === 'Sun') return false
  const dateStr = `${parts.year}-${parts.month}-${parts.day}`
  if (NSE_HOLIDAYS.has(dateStr)) return false
  const mins = parseInt(parts.hour) * 60 + parseInt(parts.minute)
  return mins >= 555 && mins <= 930      // 09:15–15:30 IST
})

const marketStatus = computed(() => {
  liveClock.value                                    // reactive clock dep
  const ticker = chartTicker.value || ''
  // Indian: .NS/.BO suffix, NSE/BSE indices (^NSEI, ^NSEBANK, ^BSESN, ^CNXFIN…),
  // or bare symbols with no exchange suffix (SBIN, RELIANCE, NIFTY etc.) — this app is India-first
  const isIndian = ticker.endsWith('.NS') || ticker.endsWith('.BO')
    || /^\^(NSEI|NSEBANK|BSESN|CNXIT|CNXFIN|INDIAVIX|NSEMDCP)/.test(ticker)
    || (!ticker.includes('.') && !ticker.startsWith('^'))
  const exchange = isIndian ? 'NSE' : 'NYSE'

  // NSE holidays 2025–2026 (YYYY-MM-DD)
  const nseHolidays = new Set([
    '2025-01-26','2025-03-14','2025-04-14','2025-04-18',
    '2025-05-01','2025-08-15','2025-08-27','2025-10-02',
    '2025-10-20','2025-10-21','2025-11-05','2025-12-25',
    '2026-01-26','2026-03-02','2026-04-03','2026-04-14',
    '2026-05-01','2026-08-17','2026-09-15','2026-10-02',
    '2026-11-03','2026-11-25','2026-12-25',
  ])
  // NYSE holidays 2025–2026 (YYYY-MM-DD)
  const nyseHolidays = new Set([
    '2025-01-01','2025-01-20','2025-02-17','2025-04-18',
    '2025-05-26','2025-06-19','2025-07-04','2025-09-01',
    '2025-11-27','2025-12-25',
    '2026-01-01','2026-01-19','2026-02-16','2026-04-03',
    '2026-05-25','2026-06-19','2026-07-04','2026-09-07',
    '2026-11-26','2026-12-25',
  ])

  // Use Intl for DST-aware timezone conversion
  const tz = isIndian ? 'Asia/Kolkata' : 'America/New_York'
  const parts = Object.fromEntries(
    new Intl.DateTimeFormat('en-US', {
      timeZone: tz,
      year: 'numeric', month: '2-digit', day: '2-digit',
      weekday: 'short', hour: '2-digit', minute: '2-digit', hour12: false
    }).formatToParts(new Date()).map(p => [p.type, p.value])
  )
  const weekday = parts.weekday   // 'Sat', 'Sun', etc.
  const dateStr = `${parts.year}-${parts.month}-${parts.day}`
  const mins    = parseInt(parts.hour) * 60 + parseInt(parts.minute)

  if (weekday === 'Sat' || weekday === 'Sun')
    return { open: false, exchange, label: `${exchange} CLOSED`, reason: 'Weekend' }

  const holidays = isIndian ? nseHolidays : nyseHolidays
  if (holidays.has(dateStr))
    return { open: false, exchange, label: `${exchange} CLOSED`, reason: 'Holiday' }

  const inHours = isIndian ? (mins >= 555 && mins <= 930) : (mins >= 570 && mins <= 960)
  return {
    open: inHours, exchange,
    label: inHours ? `${exchange} OPEN` : `${exchange} CLOSED`,
    reason: inHours ? '' : 'After hours',
  }
})
const marketOpen = computed(() => marketStatus.value.open)
const lastTradeReason   = ref('')
const lastTradeSignals  = ref([])
const lastTradeConf     = ref(null)
const lastTradeSL       = ref(null)
const lastTradeT1       = ref(null)
const lastTradeExecuted = ref(null)
const lastTradeSkipped  = ref(null)
const btLoading          = ref(false)
const btResult           = ref(null)
const btError            = ref('')
const signalError        = ref('')
const searchSuggestions  = ref([])
let   searchTimer        = null
const lightMode          = ref(localStorage.getItem('theme') === 'light')

// ── Portfolio allocator state ─────────────────────────────────────────────────
const portfolioCapital    = ref(10000)
const portfolioHorizon    = ref('3m')
const portfolioScope      = ref('all')
const portfolioCustomMode = ref(false)
const portfolioLoading    = ref(false)
const portfolioResult     = ref(null)
const portfolioError      = ref('')
const portfolioFeed       = ref([])
const portfolioRound      = ref(0)
let   _portEventSource    = null

const portfolioDenoms = [
  { val: 1000,    label: '₹1K'  },
  { val: 5000,    label: '₹5K'  },
  { val: 10000,   label: '₹10K' },
  { val: 25000,   label: '₹25K' },
  { val: 50000,   label: '₹50K' },
  { val: 100000,  label: '₹1L'  },
  { val: 500000,  label: '₹5L'  },
  { val: 1000000, label: '₹10L' },
]

const portfolioHorizons = [
  { val: '1w',  label: '1 Week',   desc: 'Intraday swing'  },
  { val: '2w',  label: '2 Weeks',  desc: 'Momentum play'   },
  { val: '1m',  label: '1 Month',  desc: 'Event driven'    },
  { val: '3m',  label: '3 Months', desc: 'Sector rotation' },
  { val: '6m',  label: '6 Months', desc: 'Growth + value'  },
  { val: '1y',  label: '1 Year',   desc: 'Compounders'     },
  { val: '3y',  label: '3 Years',  desc: 'Buy & hold'      },
]

const portfolioScopes = [
  { val: 'all',        icon: '🌍', label: 'Global Mix',   sub: 'All asset classes' },
  { val: 'large_cap',  icon: '📊', label: 'Large Cap',    sub: 'Nifty 50 stocks'   },
  { val: 'mid_cap',    icon: '🚀', label: 'Mid / Small',  sub: 'Growth stocks'     },
  { val: 'etf_index',  icon: '📈', label: 'ETF & Index',  sub: 'Passive / factor'  },
  { val: 'commodity',  icon: '🪙', label: 'Commodities',  sub: 'Gold, Silver, Oil' },
  { val: 'reit_invit', icon: '🏢', label: 'REIT / InvIT', sub: 'Real estate infra'  },
]

function formatCapital(n) {
  if (!n) return ''
  if (n >= 10000000) return (n / 10000000).toFixed(1) + ' Cr'
  if (n >= 100000)   return (n / 100000).toFixed(1) + ' Lakh'
  if (n >= 1000)     return (n / 1000).toFixed(0) + ' Thousand'
  return '₹' + n
}

// ── Intraday signal state ─────────────────────────────────────────────────────
const signal        = ref(null)
const signalLoading = ref(false)

// ── F&O Scanner state ─────────────────────────────────────────────────────────
const scannerRunning  = ref(false)
const foScannerState  = ref(null)
const foFeed          = ref([])
const foAnalysing     = ref(null)   // current ticker being analysed by scanner
const foUniverseSize  = ref(2)

// Human-readable scanner interval, derived from backend state.
// Falls back to "5min" if backend hasn't reported yet.
const foIntervalLabel = computed(() => {
  const sec = Number(foScannerState.value?.interval_seconds || 300)
  if (sec < 60)        return `${sec}s`
  if (sec % 60 === 0)  return `${sec / 60}min`
  return `${(sec / 60).toFixed(1)}min`
})
const btExpanded      = ref(false)

// ── LIVE feed (tick-driven) state ─────────────────────────────────────────────
// Tickets keyed by underlying (e.g. "^NSEI" → ticket). One card rendered per underlying.
const liveTickets      = ref({})           // { [underlying]: ticket }
const liveStatus       = ref({})           // pos_key → tick decision payload
const latestCommentary = ref(null)
const liveConnected    = ref(false)
const TT = GLOSSARY

// Per-underlying live spot map; populated by per-underlying SSE streams.
const liveSpots        = ref({})            // { [underlying]: price }
const liveSpotTickAt   = ref({})            // { [underlying]: epoch ms }
const liveTickAges     = ref({})            // { [underlying]: seconds since last tick }
// EventSource registry. On every fresh module load (including Vite HMR
// re-runs of <script setup>) we kill any streams the *previous* module
// instance left behind on `window`, then start clean. This is the only
// reliable way to avoid orphan streams whose onmessage handlers point
// at dead reactive refs from the old closure.
if (typeof window !== 'undefined') {
  const prev = window.__phoenixTicketStreams
  if (prev) {
    for (const k of Object.keys(prev)) {
      try { prev[k]?.close?.() } catch (_) {}
    }
  }
  window.__phoenixTicketStreams = {}
}
const _ticketStreams = (typeof window !== 'undefined')
  ? window.__phoenixTicketStreams
  : {}                                       // { [underlying]: EventSource }
let _ticketAgeTimer    = null

// Backwards-compat shim: latestTicket = the most recently received ticket
// (used by other code paths like `liveTicket.underlying` watch).
const latestTicket = computed(() => {
  const all = Object.values(liveTickets.value)
  if (!all.length) return null
  return all.reduce((a, b) => (a.generated_at > b.generated_at ? a : b))
})

function _openTicketStream(symbol) {
  if (!symbol || _ticketStreams[symbol]) return
  const base = import.meta.env.VITE_API_BASE_URL || 'http://localhost:5001'
  const es = new EventSource(`${base}/api/indmoney/stream/${encodeURIComponent(symbol)}`)
  es.onmessage = (e) => {
    try {
      const m = JSON.parse(e.data)
      if (m.heartbeat || m.error) return
      const p = Number(m.price ?? m.ltp ?? m.last_price)
      if (p > 0) {
        liveSpots.value      = { ...liveSpots.value,      [symbol]: p }
        liveSpotTickAt.value = { ...liveSpotTickAt.value, [symbol]: Date.now() }
      }
    } catch(_) {}
  }
  es.onerror = () => console.warn('Ticket stream interrupted, reconnecting…', symbol)
  _ticketStreams[symbol] = es
}

function _closeTicketStream(symbol) {
  const es = _ticketStreams[symbol]
  if (es) { try { es.close() } catch(_) {} ; delete _ticketStreams[symbol] }
}

function _closeAllTicketStreams() {
  Object.keys(_ticketStreams).forEach(_closeTicketStream)
}

// ── Manual position tracker — "I entered this trade" pins ───────────────────
// Persisted server-side at /api/trade/tracked. Survives refreshes. Live-repriced
// on every spot tick. Independent of any paper-wallet logic.
const trackedPositions = ref([])    // [{ id, ticket, qty, notes, entered_at }]

async function _loadTracked() {
  try {
    const base = import.meta.env.VITE_API_BASE_URL || 'http://localhost:5001'
    const r = await fetch(`${base}/api/trade/tracked`).then(x => x.json())
    if (r.success) trackedPositions.value = r.data || []
  } catch (e) { console.warn('_loadTracked failed', e) }
}

function isTracked(symbol) {
  if (!symbol) return false
  return trackedPositions.value.some(p => p.ticket?.trading_symbol === symbol)
}

// In-flight guard so rapid double-clicks don't race the isTracked() check
// and POST multiple times before the first response updates state.
const _trackInFlight = new Set()

async function trackEntered(ticket) {
  if (!ticket?.trading_symbol) {
    console.warn('[trackEntered] aborted — ticket has no trading_symbol', ticket)
    alert('Cannot watch: this signal has no contract symbol. The scanner ticket build may have failed for this ticker.')
    return
  }
  if (isTracked(ticket.trading_symbol)) {
    console.info('[trackEntered] already tracking', ticket.trading_symbol)
    alert(`${ticket.trading_symbol} is already in your WATCHING list.`)
    return
  }
  if (_trackInFlight.has(ticket.trading_symbol)) {
    console.info('[trackEntered] request already in flight', ticket.trading_symbol)
    alert(`Already adding ${ticket.trading_symbol}… give it a moment.`)
    return
  }
  _trackInFlight.add(ticket.trading_symbol)
  try {
    const base = import.meta.env.VITE_API_BASE_URL || 'http://localhost:5001'
    const resp = await fetch(`${base}/api/trade/tracked`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ ticket, qty: ticket.qty || 1 }),
    })
    const r = await resp.json().catch(() => ({}))
    if (!resp.ok || !r.success) {
      console.error('[trackEntered] server error', resp.status, r)
      alert(`Failed to add ${ticket.trading_symbol} to watching:\n${r.error || resp.statusText || 'unknown error'}`)
      return
    }
    // Backend dedups by trading_symbol — only push if not already present
    // (covers the case where two POSTs both got the same existing record back).
    const existing = trackedPositions.value.find(p => p.id === r.data?.id)
    if (!existing) {
      trackedPositions.value = [...trackedPositions.value, r.data]
    }
    console.info('[trackEntered] OK', r.data?.id, ticket.trading_symbol)
  } catch (e) {
    console.error('[trackEntered] network error', e)
    alert(`Network error adding ${ticket.trading_symbol} to watching: ${e.message || e}`)
  } finally {
    _trackInFlight.delete(ticket.trading_symbol)
  }
}

// 2-stage exit button — first click arms, second click confirms within 4s.
// Avoids browser confirm() dialogs which can be blocked or focus-stolen.
const _trackExitArmed = ref(new Set())
async function trackExited(card) {
  if (!card?.id) return
  if (!_trackExitArmed.value.has(card.id)) {
    _trackExitArmed.value = new Set([..._trackExitArmed.value, card.id])
    setTimeout(() => {
      _trackExitArmed.value = new Set([..._trackExitArmed.value].filter(x => x !== card.id))
    }, 4000)
    return
  }
  try {
    const base = import.meta.env.VITE_API_BASE_URL || 'http://localhost:5001'
    // Use query params (no body) — DELETE with body is non-standard and some
    // browser/proxy combos hang on it indefinitely.
    const qs = new URLSearchParams({
      exit_premium: String(card.now_premium ?? ''),
      exit_reason:  'manual',
    }).toString()
    const r = await fetch(`${base}/api/trade/tracked/${card.id}?${qs}`, {
      method: 'DELETE',
    })
    if (!r.ok) {
      const txt = await r.text().catch(() => '')
      console.error('trackExited HTTP', r.status, txt)
      return
    }
    trackedPositions.value = trackedPositions.value.filter(p => p.id !== card.id)
    _trackExitArmed.value = new Set([..._trackExitArmed.value].filter(x => x !== card.id))
  } catch (e) { console.error('trackExited', e) }
}
function isExitArmed(id) { return _trackExitArmed.value.has(id) }

// ── Audio alerts (Web Audio API beeps) ───────────────────────────────────────
// Beep frequencies tuned to be distinct & urgent without being headache-grade.
let _audioCtx = null
function _beep(freq = 880, duration = 0.18, volume = 0.18, type = 'sine') {
  try {
    if (!_audioCtx) _audioCtx = new (window.AudioContext || window.webkitAudioContext)()
    if (_audioCtx.state === 'suspended') _audioCtx.resume()
    const osc = _audioCtx.createOscillator()
    const gain = _audioCtx.createGain()
    osc.type = type
    osc.frequency.value = freq
    gain.gain.setValueAtTime(0, _audioCtx.currentTime)
    gain.gain.linearRampToValueAtTime(volume, _audioCtx.currentTime + 0.01)
    gain.gain.exponentialRampToValueAtTime(0.001, _audioCtx.currentTime + duration)
    osc.connect(gain).connect(_audioCtx.destination)
    osc.start()
    osc.stop(_audioCtx.currentTime + duration)
  } catch(e) { /* audio policy may block until user interacts; safe to ignore */ }
}
function _playExitAlert(status) {
  // Distinct sounds per trigger so you can identify the event without looking
  if      (status === 'sl_hit')    { _beep(220, 0.25, 0.25); setTimeout(() => _beep(180, 0.25, 0.25), 200); setTimeout(() => _beep(160, 0.4, 0.25), 420) }
  else if (status === 'time_exit') { _beep(440, 0.20); setTimeout(() => _beep(440, 0.20), 280); setTimeout(() => _beep(440, 0.40), 560) }
  else if (status === 'past_t2')   { _beep(880, 0.15); setTimeout(() => _beep(1175, 0.15), 160); setTimeout(() => _beep(1568, 0.35), 320) }
  else if (status === 'past_t1')   { _beep(660, 0.15); setTimeout(() => _beep(880, 0.25), 180) }
  else if (status === 'near_sl')   { _beep(300, 0.12, 0.12) }
  else if (status === 'near_t1')   { _beep(700, 0.12, 0.12) }
}

// Track previous status per card to fire sound only on transition (not every tick)
const _prevTrackStatus = {}
function _maybeAlert(cards) {
  const ALERT_STATES = new Set(['sl_hit','time_exit','past_t1','past_t2','near_sl','near_t1'])
  for (const c of cards) {
    const prev = _prevTrackStatus[c.id]
    if (c.status !== prev && ALERT_STATES.has(c.status)) {
      _playExitAlert(c.status)
    }
    _prevTrackStatus[c.id] = c.status
  }
}

// Live-priced tracked-position cards — re-runs on every spot tick + clock tick.
// Each card carries:
//   trading_symbol, entry_premium, now_premium, sl, t1, t2, pnl_pct,
//   status ∈ { 'safe', 'near_t1', 'past_t1', 'past_t2', 'near_sl', 'sl_hit', 'time_exit' },
//   statusLabel — short human label for the badge,
//   alert — non-empty string when an exit condition has fired.
const trackedCards = computed(() => {
  const out = []
  // pull current IST clock to detect 13:00 / 15:00 force-exit windows
  const nowIST = new Date(new Date().toLocaleString('en-US', { timeZone: 'Asia/Kolkata' }))
  const istMins = nowIST.getHours() * 60 + nowIST.getMinutes()

  for (const rec of trackedPositions.value) {
    const t = rec.ticket || {}
    const under = t.underlying
    const spot  = Number(liveSpots.value[under])
                || (chartTicker.value === under ? Number(indmoneyLivePrice.value) : 0)
                || Number(t.spot) || 0

    const live = spot ? repriceTicket(t, spot) : null
    // Prefer the option contract's REAL broker LTP (fed by the option's own
    // SSE stream, see `_openTicketStream(opt)` setup in onMounted). Falls
    // back to Black-Scholes reprice from the underlying spot if the option
    // tick hasn't arrived yet, then to entry premium as last resort. This
    // keeps the card's "now" price in lockstep with the chart's last bar.
    const opt_ltp      = Number(liveSpots.value[t.trading_symbol]) || 0
    const now_premium  = opt_ltp
                       || live?.premium_now
                       || Number(t.entry?.expected_premium_inr)
                       || 0
    const entry_prem   = Number(t.entry?.expected_premium_inr) || 0
    const sl           = Number(t.exit?.stop_loss_inr) || 0
    const t1           = Number(t.exit?.target_1_inr)  || 0
    const t2           = Number(t.exit?.target_2_inr)  || 0
    const pnl_pct      = entry_prem ? ((now_premium - entry_prem) / entry_prem) * 100 : 0

    // Determine status — checked in same priority order as the position monitor
    let status = 'safe', statusLabel = 'OK', alert = ''
    if (istMins >= 15 * 60) {
      status = 'time_exit'; statusLabel = 'FORCE EXIT'
      alert = 'It\'s past 15:00 IST — close immediately to beat broker auto-square-off.'
    } else if (now_premium <= sl) {
      status = 'sl_hit'; statusLabel = 'SL HIT'
      alert = `Premium ₹${now_premium.toFixed(2)} ≤ SL ₹${sl.toFixed(2)} — EXIT NOW.`
    } else if (now_premium >= t2) {
      status = 'past_t2'; statusLabel = 'T2 HIT'
      alert = `Premium reached T2 ₹${t2.toFixed(2)} — full exit.`
    } else if (now_premium >= t1) {
      status = 'past_t1'; statusLabel = 'T1 HIT'
      alert = `Premium reached T1 ₹${t1.toFixed(2)} — exit 50% and trail SL to breakeven.`
    } else if (now_premium <= sl * 1.10) {
      status = 'near_sl'; statusLabel = 'NEAR SL'
    } else if (t1 && now_premium >= t1 * 0.92) {
      status = 'near_t1'; statusLabel = 'NEAR T1'
    }

    const lot          = Number(t.lot_size) || 1
    const qty          = Number(rec.qty) || 1
    const pnl_inr      = (now_premium - entry_prem) * lot * qty
    const tick_age     = Number(liveTickAges.value[under]) || 0
    const is_live      = !!liveSpots.value[under] && tick_age < 10
    out.push({
      id: rec.id,
      trading_symbol: t.trading_symbol,
      option_type:    t.option_type,        // 'CE' | 'PE'
      strike:         t.strike,
      expiry:         t.expiry,
      dte:            t.days_to_expiry,
      lot, qty,
      entry_premium: entry_prem,
      now_premium,
      sl, t1, t2, pnl_pct, pnl_inr,
      tick_age, is_live,
      status, statusLabel, alert,
    })
  }
  return out
})

// ── Option chain (live-repriced ladder of strikes around ATM) ────────────────
const optionChain     = ref(null)            // { underlying, spot, expiry, dte, iv, strikes:[…] } from /option-chain
const chainUnderlying = ref('^NSEI')         // which underlying the chain is for
const chainBuyOnly    = ref(true)            // filter to BUY candidates only
const chainLoading    = ref(false)           // in-flight fetch state for UI
const chainError      = ref('')              // last fetch error (if any)
let _chainTimer       = null                 // periodic refetch (master changes intra-day)
// Non-reactive snapshot of last computed CE/PE premiums per strike, used to
// derive a tick direction (up/dn) for the flash animation on each render.
const _chainPrevPrices = { value: {} }

async function _fetchOptionChain(sym) {
  // Clear stale data immediately so a failed/slow fetch never leaves the UI
  // showing the previous underlying's strikes (caused "I picked SENSEX but
  // see NIFTY" bug when the SENSEX fetch errored silently).
  optionChain.value = null
  chainError.value  = ''
  chainLoading.value = true
  _chainPrevPrices.value = {}
  try {
    const res = await getOptionChain(sym, 13)
    const data = res?.data || res
    if (data && data.strikes) optionChain.value = data
    else { chainError.value = data?.error || 'no chain data'; console.warn('[option-chain] empty', data) }
  } catch (e) {
    chainError.value = e?.response?.data?.error || e?.message || 'fetch failed'
    console.error('[option-chain] FETCH FAILED', sym, chainError.value)
  } finally {
    chainLoading.value = false
  }
}

// Live-priced chain rows: BS-reprices each strike (CE & PE) against current
// underlying spot every render. Returns sorted strikes with all metrics.
const liveOptionChain = computed(() => {
  const ch = optionChain.value
  if (!ch) return null
  const sym  = ch.underlying
  const spot = Number(liveSpots.value[sym])
              || (chartTicker.value === sym ? Number(indmoneyLivePrice.value) : 0)
              || Number(ch.spot)
  const dte  = Number(ch.days_to_expiry) || 1
  const iv   = Number(ch.iv_estimate) || 0.18
  const lot  = Number(ch.lot_size) || 1

  const pseudoTicket = (meta, type) => ({
    strike: meta.strike, days_to_expiry: dte, option_type: type,
    lot_size: lot, spot,
    greeks: { iv_used: iv },
    entry: { expected_premium_inr: 0 },
    exit:  { stop_loss_inr: 0, target_1_inr: 0, target_2_inr: 0 },
  })

  const prev = _chainPrevPrices.value

  // Tag a strike's buy-quality from absolute delta:
  //   |Δ| 0.65-0.85 → CONSERVATIVE  (ITM, high cost, low theta risk)
  //   |Δ| 0.40-0.65 → BALANCED      (ATM, sweet spot for directional bets)
  //   |Δ| 0.25-0.40 → AGGRESSIVE    (slightly OTM, lower cost, higher leverage)
  //   |Δ| 0.10-0.25 → LOTTERY       (deep OTM, theta will eat you alive)
  //   |Δ| > 0.85   → EXPENSIVE     (deep ITM, behaves like the underlying — use FUT instead)
  const verdictFromDelta = (d) => {
    const a = Math.abs(d ?? 0)
    if (!a) return ''
    if (a > 0.85) return 'EXPENSIVE'
    if (a >= 0.65) return 'CONSERVATIVE'
    if (a >= 0.40) return 'BALANCED'
    if (a >= 0.25) return 'AGGRESSIVE'
    return 'LOTTERY'
  }

  // The AI-picked option this session — link the chain to the ticket card.
  const aiTicket  = liveTickets.value?.[sym]
  const aiStrike  = aiTicket?.strike
  const aiSide    = aiTicket?.option_type    // 'CE' | 'PE'

  const rows = ch.strikes.map(r => {
    const ce_live = r.ce ? repriceTicket(pseudoTicket(r.ce, 'CE'), spot) : null
    const pe_live = r.pe ? repriceTicket(pseudoTicket(r.pe, 'PE'), spot) : null
    const cep = ce_live?.premium_now ?? null
    const pep = pe_live?.premium_now ?? null
    const pcep = prev[r.strike]?.ce ?? cep
    const ppep = prev[r.strike]?.pe ?? pep
    const ceCost = cep != null ? Math.round(cep * lot) : null
    const peCost = pep != null ? Math.round(pep * lot) : null
    return {
      strike:     r.strike,
      is_atm:     r.strike === ch.atm_strike,
      ce_symbol:  r.ce?.trading_symbol,
      pe_symbol:  r.pe?.trading_symbol,
      ce_premium: cep,
      pe_premium: pep,
      ce_delta:   ce_live?.delta ?? null,
      pe_delta:   pe_live?.delta ?? null,
      ce_cost:    ceCost,
      pe_cost:    peCost,
      ce_be:      cep != null ? Math.round(r.strike + cep) : null,
      pe_be:      pep != null ? Math.round(r.strike - pep) : null,
      ce_verdict: verdictFromDelta(ce_live?.delta),
      pe_verdict: verdictFromDelta(pe_live?.delta),
      ce_is_pick: aiSide === 'CE' && aiStrike === r.strike,
      pe_is_pick: aiSide === 'PE' && aiStrike === r.strike,
      ce_moneyness: r.strike <= spot ? 'ITM' : 'OTM',
      pe_moneyness: r.strike >= spot ? 'ITM' : 'OTM',
      ce_dir:    (cep != null && pcep != null && Math.abs(cep - pcep) > 0.0005) ? (cep > pcep ? 'up' : 'dn') : '',
      pe_dir:    (pep != null && ppep != null && Math.abs(pep - ppep) > 0.0005) ? (pep > ppep ? 'up' : 'dn') : '',
      // Live broker quote enrichment (bid/ask/oi/volume/spread) — from server
      ce_oi:        r.ce?.oi ?? 0,
      pe_oi:        r.pe?.oi ?? 0,
      ce_volume:    r.ce?.volume ?? 0,
      pe_volume:    r.pe?.volume ?? 0,
      ce_bid:       r.ce?.bid ?? null,
      ce_ask:       r.ce?.ask ?? null,
      pe_bid:       r.pe?.bid ?? null,
      pe_ask:       r.pe?.ask ?? null,
      ce_spread:    r.ce?.spread_pct ?? null,
      pe_spread:    r.pe?.spread_pct ?? null,
      ce_ltp:       r.ce?.ltp ?? null,
      pe_ltp:       r.pe?.ltp ?? null,
    }
  })

  // BUY-only filter: keep strikes with healthy delta on at least one side.
  // For CE we want |Δ| ≥ 0.30 (avoid lottery OTMs); for PE same.
  // ATM ± 3 strikes is the sweet spot for buying.
  const filtered = chainBuyOnly.value
    ? rows.filter(r => Math.abs(r.ce_delta || 0) >= 0.30 || Math.abs(r.pe_delta || 0) >= 0.30)
    : rows

  // Snapshot prices for next render's direction comparison.
  const snap = {}
  for (const r of rows) snap[r.strike] = { ce: r.ce_premium, pe: r.pe_premium }
  _chainPrevPrices.value = snap

  // Identify peak-OI strike (highest CE OI + PE OI) — a strong S/R level.
  let peak_oi_strike = null, peak_oi_total = 0
  for (const r of rows) {
    const t = (r.ce_oi || 0) + (r.pe_oi || 0)
    if (t > peak_oi_total) { peak_oi_total = t; peak_oi_strike = r.strike }
  }

  return {
    underlying: sym,
    spot:       Math.round(spot * 100) / 100,
    expiry:     ch.expiry,
    dte,
    iv_pct:     Math.round(iv * 1000) / 10,
    lot,
    rows:       filtered,
    tick_age:   liveTickAges.value[sym] || 0,
    is_live:    !!liveSpots.value[sym],
    // Aggregate sentiment metrics from server
    pcr_oi:     ch.totals?.pcr_oi ?? null,
    pcr_volume: ch.totals?.pcr_volume ?? null,
    max_pain:   ch.totals?.max_pain ?? null,
    ce_oi_total: ch.totals?.ce_oi ?? 0,
    pe_oi_total: ch.totals?.pe_oi ?? 0,
    peak_oi_strike,
    peak_oi_total,
  }
})

// All live ticket cards — one per unique underlying that has a ticket.
// Each card re-prices independently against its own underlying's spot tick.
//
// Premium-display priority (matches trackedCards):
//   1. Real broker LTP for the option contract (liveSpots[trading_symbol])
//   2. Black-Scholes reprice from the underlying spot (theoretical fallback)
//   3. Original ticket spot (boot-time placeholder)
// Without (1) the displayed "LIVE premium" diverges from the chart's last
// bar by ₹5-15 — exactly the bug the user reported (chart ₹197.95 vs
// LIVE panel ₹208.07).
const liveTicketCards = computed(() => {
  const tickets = liveTickets.value
  const out = []
  for (const [under, t] of Object.entries(tickets)) {
    if (!t) continue
    const spot = Number(liveSpots.value[under])
                || (chartTicker.value === under ? Number(indmoneyLivePrice.value) : 0)
                || Number(t.spot)
    const live = repriceTicket(t, spot)
    // Override Black-Scholes premium with the option's REAL broker LTP if
    // the option's SSE stream has delivered a tick. The Greeks/IV/%-fields
    // stay BS-derived since the broker LTP doesn't include them.
    const opt_ltp = Number(liveSpots.value[t.trading_symbol]) || 0
    if (live && opt_ltp > 0) {
      live.premium_now      = Number(opt_ltp.toFixed(2))
      const entry           = Number(t.entry?.expected_premium_inr) || live.premium_now
      live.pct_from_entry   = entry ? Number((((opt_ltp - entry) / entry) * 100).toFixed(2)) : 0
      live.pnl_per_lot      = Math.round((opt_ltp - entry) * (Number(t.lot_size) || 1))
      const t1 = Number(t.exit?.target_1_inr) || 0
      const sl = Number(t.exit?.stop_loss_inr) || 0
      live.pct_to_t1        = t1 ? Number((((t1 - opt_ltp) / opt_ltp) * 100).toFixed(2)) : 0
      live.pct_to_sl        = sl ? Number((((sl - opt_ltp) / opt_ltp) * 100).toFixed(2)) : 0
      live._source          = 'broker_ltp'
    } else if (live) {
      live._source          = 'black_scholes'
    }
    out.push({
      ...t,
      live,
      _tick_age_sec: liveTickAges.value[under] || 0,
      _underlying:   under,
    })
  }
  return out.sort((a, b) => (b.generated_at || '').localeCompare(a.generated_at || ''))
})
const liveClock        = ref('')
let _liveES = null
let _liveClockTimer = null

// ── Server-side tracked-position watcher (SSE) ──────────────────────────────
// The backend's tracked_monitor.py polls broker spot every 3s, reprices each
// pinned position via Black-Scholes, and emits 'tracked_alert' events on status
// transitions (sl_hit, near_sl, past_t1, past_t2, near_t1, time_exit).
//
// This stream is what makes alerts reliable when:
//   - The browser tab is in background (JS throttled)
//   - The user is on another desktop / monitor
//   - The laptop briefly slept and woke up — replay arrives on reconnect
//
// On every event we (a) play distinct beep, (b) fire desktop Notification
// (works when tab hidden), (c) append to localStorage-backed missedAlerts list.
let   _alertsES         = null
const alertsConnected   = ref(false)
const notifPermission   = ref(typeof Notification !== 'undefined' ? Notification.permission : 'denied')
const missedAlerts      = ref([])         // [{ id, trading_symbol, status, message, timestamp, ... }]
const _MISSED_KEY       = 'phoenix_missed_alerts_v1'

function _loadMissedAlerts() {
  try {
    const raw = localStorage.getItem(_MISSED_KEY)
    if (raw) missedAlerts.value = JSON.parse(raw).slice(0, 20)
  } catch (e) { /* ignore corrupt blob */ }
}
function _saveMissedAlerts() {
  try { localStorage.setItem(_MISSED_KEY, JSON.stringify(missedAlerts.value.slice(0, 20))) }
  catch (e) { /* quota / private mode */ }
}
function clearMissedAlerts() { missedAlerts.value = []; _saveMissedAlerts() }
function _fmtAlertTime(iso) {
  if (!iso) return ''
  try {
    const d = new Date(iso)
    return d.toLocaleTimeString('en-IN', { hour: '2-digit', minute: '2-digit', timeZone: 'Asia/Kolkata' })
  } catch (e) { return '' }
}

async function requestNotifPermission() {
  if (typeof Notification === 'undefined') {
    alert('This browser does not support desktop notifications.')
    return
  }
  try {
    const p = await Notification.requestPermission()
    notifPermission.value = p
    if (p === 'granted') {
      // Smoke-test so the user sees the permission worked
      new Notification('PhoenixTrade alerts enabled', {
        body: 'You will get a desktop popup on SL / T1 / T2 / 15:00 events even when this tab is hidden.',
        icon: '/favicon.ico',
      })
    }
  } catch (e) { console.warn('Notification.requestPermission failed', e) }
}

function _showDesktopNotification(payload) {
  if (notifPermission.value !== 'granted' || typeof Notification === 'undefined') return
  const titleMap = {
    sl_hit   : '🔴 SL HIT — EXIT NOW',
    past_t2  : '🟢 T2 HIT — Full exit',
    past_t1  : '🟢 T1 HIT — Exit 50%, trail SL',
    near_sl  : '🟡 NEAR SL',
    near_t1  : '🔵 NEAR T1',
    time_exit: '🚨 15:00 IST — FORCE EXIT',
  }
  try {
    const n = new Notification(titleMap[payload.status] || `Alert: ${payload.status}`, {
      body : `${payload.trading_symbol}\n${payload.message || ''}\nentry ₹${(payload.entry_premium || 0).toFixed(2)} → now ₹${(payload.premium || 0).toFixed(2)} (${(payload.pnl_pct || 0) >= 0 ? '+' : ''}${(payload.pnl_pct || 0).toFixed(2)}%)`,
      icon : '/favicon.ico',
      tag  : payload.id,                 // de-dupe re-fires for the same position
      requireInteraction: payload.status === 'sl_hit' || payload.status === 'time_exit',
    })
    n.onclick = () => { window.focus(); n.close() }
  } catch (e) { console.warn('Notification show failed', e) }
}

function _onTrackedAlert(payload) {
  if (!payload || payload.type !== 'tracked_alert') return
  // Append to missed-alerts (drop duplicates of same id+status combo from last 60s)
  const key = `${payload.id}|${payload.status}`
  const cutoff = Date.now() - 60_000
  missedAlerts.value = [
    payload,
    ...missedAlerts.value.filter(a => `${a.id}|${a.status}` !== key
                                      || (a.timestamp && new Date(a.timestamp).getTime() < cutoff)),
  ].slice(0, 20)
  _saveMissedAlerts()
  // Audio + desktop popup
  _playExitAlert(payload.status)
  _showDesktopNotification(payload)
}

function _openTrackedAlertsStream() {
  if (_alertsES) return
  const base = import.meta.env.VITE_API_BASE_URL || 'http://localhost:5001'
  _alertsES = _registerSingletonStream('alertsES', new EventSource(`${base}/api/trade/tracked/alerts/stream`))
  _alertsES.onopen  = () => { alertsConnected.value = true }
  _alertsES.onerror = () => {
    alertsConnected.value = false
    // Browser auto-reconnects EventSource by default; no manual reconnect needed.
  }
  _alertsES.onmessage = (e) => {
    try {
      const m = JSON.parse(e.data)
      if (m.type === 'alerts_connected') { alertsConnected.value = true; return }
      if (m.type === 'heartbeat')        { return }
      if (m.type === 'tracked_alert')    { _onTrackedAlert(m); return }
    } catch (err) { console.warn('alerts SSE parse', err) }
  }
}

function ladderPct(s, which) {
  // Map premium ₹ to position on the SL──entry──T1──T2 ladder (0–100 %)
  const lo = +s.sl || 0
  const hi = +s.t2 || (lo * 6) || 1
  const span = Math.max(1, hi - lo)
  const clamp = v => Math.max(0, Math.min(100, ((v - lo) / span) * 100))
  if (which === 'now')   return clamp(s.premium) + '%'
  if (which === 'left')  return clamp(Math.min(s.premium, s.avg_entry || s.premium)) + '%'
  if (which === 'width') {
    const a = clamp(Math.min(s.premium, s.avg_entry || s.premium))
    const b = clamp(Math.max(s.premium, s.avg_entry || s.premium))
    return Math.max(2, b - a) + '%'
  }
  return '0%'
}
let   _foScannerES    = null
let   _monitorES      = null

function shortVerdict(v) {
  if (!v) return 'SIG'
  if (v === 'STRONG BUY')  return '⬆⬆ STRONG BUY'
  if (v === 'BUY')         return '⬆ BUY'
  if (v === 'STRONG SELL') return '⬇⬇ STRONG SELL'
  if (v === 'SELL')        return '⬇ SELL'
  return 'HOLD'
}

// Real broker transaction implied by the signal.
// CE/PE always means BUY (this scanner long-only on options — never writes).
// FUT/EQ buys or sells based on the underlying direction.
function transactionLabel(ev) {
  const itype  = String(ev.instrument || ev.instrument_type || '').toUpperCase()
  const action = String(ev.action || '').toUpperCase()
  if (itype === 'CE')  return '🟢 BUY CE'
  if (itype === 'PE')  return '🟢 BUY PE'
  if (itype === 'FUT') return action.includes('SELL') ? '🔴 SELL FUT' : '🟢 BUY FUT'
  if (action.includes('SELL')) return '🔴 SELL'
  return '🟢 BUY'
}

// Tooltip explaining the trade in plain English.
function transactionTooltip(ev) {
  const itype = String(ev.instrument || ev.instrument_type || '').toUpperCase()
  const verdict = String(ev.verdict || '').toUpperCase()
  const isBear = verdict.includes('SELL')
  const isBull = verdict.includes('BUY')
  if (itype === 'PE') {
    return `Bearish on underlying — BUY this Put option. You profit if underlying falls below ${ev.strike || 'strike'}. The "SELL" verdict refers to the underlying direction; you transact BUY on the option.`
  }
  if (itype === 'CE') {
    return `Bullish on underlying — BUY this Call option. You profit if underlying rises above ${ev.strike || 'strike'}.`
  }
  if (itype === 'FUT') {
    if (isBear) return 'Bearish on underlying — SHORT the futures contract.'
    return 'Bullish on underlying — LONG the futures contract.'
  }
  return ''
}
function verdictClass(v) {
  if (!v) return 'hold'
  if (v.includes('STRONG BUY'))  return 'strongbuy'
  if (v.includes('BUY'))         return 'buy'
  if (v.includes('STRONG SELL')) return 'strongsell'
  if (v.includes('SELL'))        return 'sell'
  return 'hold'
}

function fmtTime(iso) {
  if (!iso) return ''
  try {
    return new Date(iso).toLocaleTimeString('en-IN', { hour: '2-digit', minute: '2-digit', second: '2-digit' })
  } catch { return iso }
}

async function startScanner() {
  try {
    const base = import.meta.env.VITE_API_BASE_URL || 'http://localhost:5001'
    await fetch(`${base}/api/trade/fo-scanner/start`, { method: 'POST' })
    scannerRunning.value = true
    _openFoScannerStream()
  } catch(e) { console.error('startScanner', e) }
}

async function stopScanner() {
  try {
    const base = import.meta.env.VITE_API_BASE_URL || 'http://localhost:5001'
    await fetch(`${base}/api/trade/fo-scanner/stop`, { method: 'POST' })
    scannerRunning.value = false
  } catch(e) { console.error('stopScanner', e) }
}

async function triggerScan() {
  try {
    const base = import.meta.env.VITE_API_BASE_URL || 'http://localhost:5001'
    await fetch(`${base}/api/trade/fo-scanner/trigger`, { method: 'POST' })
    scannerRunning.value = true
    _openFoScannerStream()
  } catch(e) { console.error('triggerScan', e) }
}

function _openFoScannerStream() {
  if (_foScannerES) return
  const base = import.meta.env.VITE_API_BASE_URL || 'http://localhost:5001'

  // Replay the latest scanner state on connect so the LIVE ticket card
  // populates immediately on page load (otherwise we wait for the next
  // scan cycle, which can be up to 5 min).
  fetch(`${base}/api/trade/fo-scanner/status`)
    .then(r => r.json())
    .then(j => {
      const data = j?.data || {}
      foScannerState.value  = data
      scannerRunning.value  = !!data.running
      const sigs = data.signals || []
      // Backfill the feed with the most recent signals (newest first)
      for (let i = sigs.length - 1; i >= 0; i--) {
        const s = sigs[i]
        foFeed.value.unshift({ type: 'scan_signal', ...s })
      }
      // Backfill all tickets keyed by underlying
      const fresh = {}
      for (const s of sigs) {
        if (s.ticket && s.ticket.underlying) fresh[s.ticket.underlying] = s.ticket
      }
      liveTickets.value = { ...liveTickets.value, ...fresh }
      if (foFeed.value.length > 100) foFeed.value.splice(100)
    })
    .catch(() => {})

  _foScannerES = _registerSingletonStream('foScannerES', new EventSource(`${base}/api/trade/fo-scanner/stream`))
  _foScannerES.onmessage = (e) => {
    try {
      const ev = JSON.parse(e.data)
      if (ev.type === 'heartbeat') return
      if (ev.type === 'scan_analysing') {
        foAnalysing.value = ev
        return
      }
      if (ev.type === 'scan_complete') {
        foAnalysing.value = null
        // Refresh scanner status so UI picks up any backend config changes
        // (e.g. FO_SCAN_INTERVAL_SEC tweaked in .env across restarts).
        const base = import.meta.env.VITE_API_BASE_URL || 'http://localhost:5001'
        fetch(`${base}/api/trade/fo-scanner/status`).then(r => r.json()).then(j => {
          if (j?.success) foScannerState.value = j.data
        }).catch(() => {})
      }
      // Capture the rich trade ticket from scan_signal events
      if (ev.type === 'scan_signal' && ev.ticket && ev.ticket.underlying) {
        liveTickets.value = { ...liveTickets.value, [ev.ticket.underlying]: ev.ticket }
      }
      // Dedupe: replace any existing row for the same ticker+side with the
      // newer one rather than stacking duplicates from successive cycles.
      if (ev.type === 'scan_signal' || ev.type === 'scan_skip') {
        const key = (ev.ticker || '') + '|' + (ev.option_type || ev.instrument || '')
        foFeed.value = foFeed.value.filter(x =>
          !((x.type === 'scan_signal' || x.type === 'scan_skip') &&
            ((x.ticker || '') + '|' + (x.option_type || x.instrument || '')) === key))
      }
      foFeed.value.unshift(ev)
      if (foFeed.value.length > 100) foFeed.value.splice(100)
    } catch {}
  }
  _foScannerES.onerror = () => { scannerRunning.value = false; foAnalysing.value = null }
}

function _openLiveFeed() {
  if (_liveES) return
  const base = import.meta.env.VITE_API_BASE_URL || 'http://localhost:5001'
  _liveES = _registerSingletonStream('liveES', new EventSource(`${base}/api/trade/live-feed`))
  _liveES.onopen  = () => { liveConnected.value = true }
  _liveES.onerror = () => { liveConnected.value = false }
  _liveES.onmessage = (e) => {
    try {
      const ev = JSON.parse(e.data)
      if (ev.type === 'tick_update') {
        liveStatus.value = { ...liveStatus.value, [ev.pos_key]: ev }
      } else if (ev.type === 'exit_full') {
        const next = { ...liveStatus.value }; delete next[ev.pos_key]
        liveStatus.value = next
      } else if (ev.type === 'exit_partial') {
        // live broker pushes the partial fill via its own stream
      } else if (ev.type === 'commentary') {
        latestCommentary.value = { text: ev.text, ts: ev.ts }
      }
    } catch {}
  }
  // Also fetch initial snapshot so cards render immediately on page reload
  fetch(`${base}/api/trade/live-status`)
    .then(r => r.json())
    .then(j => { if (j?.success && j.data) liveStatus.value = j.data })
    .catch(() => {})
}

function _openMonitorStream() {
  if (_monitorES) return
  const base = import.meta.env.VITE_API_BASE_URL || 'http://localhost:5001'
  _monitorES = _registerSingletonStream('monitorES', new EventSource(`${base}/api/trade/monitor/stream`))
  _monitorES.onmessage = (e) => {
    try {
      const ev = JSON.parse(e.data)
      if (ev.type === 'heartbeat') return
      foFeed.value.unshift(ev)
      if (foFeed.value.length > 100) foFeed.value.splice(100)
    } catch {}
  }
}

// ── INDmoney / INDstocks state ────────────────────────────────────────────────
const indmoneyConnected  = ref(false)
const indmoneyAvailable  = ref(false)   // token configured on backend
const indmoneyLivePrice  = ref(null)
const indmoneyName       = ref('')      // account display name
let   _ltpPollTimer      = null         // fallback 5s LTP poll
let   _candleRefreshTimer = null        // silent periodic candle refresh
let   indmoneyStream     = null

// ── Constants ─────────────────────────────────────────────────────────────────
const timeframes = [
  { l: '1D', v: '1d' }, { l: '1H', v: '1h' },
  { l: '30M', v: '30m' }, { l: '1Y', v: '1wk' },
]

const assetClasses = [
  { l: 'Indices',     v: 'index'     },
  { l: 'Stocks',      v: 'stock'     },
  { l: 'Commodities', v: 'commodity' },
  { l: 'ETFs',        v: 'etf'       },
  { l: '⟳ Scanner',   v: 'scanner'   },
  { l: '★ Watchlist', v: 'watchlist' },
  { l: '$ Budget',    v: 'budget'    },
]

const countryMap = {
  index:     ['ALL', 'US', 'IN', 'UK', 'EU', 'JP', 'CN', 'HK', 'AU', 'KR', 'CA', 'BR'],
  stock:     ['ALL', 'US', 'IN', 'UK', 'DE', 'JP', 'HK'],
  commodity: ['ALL'],
  etf:       ['ALL', 'US', 'IN', 'CN', 'JP', 'EU', 'BR'],
  scanner:   [],
  watchlist: [],
  budget:    [],
}

const activeCountries = computed(() => countryMap[assetClassFilter.value] || ['ALL'])

const intelTabs = ['ALL', 'NEWS', 'ANALYSTS', 'REDDIT', 'FOMO', 'PREDICTIONS', 'SIM']

// ── Computed ──────────────────────────────────────────────────────────────────
const filteredAssets = computed(() => {
  const ac = assetClassFilter.value
  if (ac === 'scanner') return []
  let list = allAssets.value.filter(a => a.asset_class === ac)
  if (countryFilter.value !== 'ALL') list = list.filter(a => a.country === countryFilter.value)
  return list
})

const fomoClass = computed(() => {
  if (fomoScore.value === null) return ''
  if (fomoScore.value >= 70) return 'hot'
  if (fomoScore.value >= 45) return 'neu'
  return 'dn'
})

const visibleFeed = computed(() => {
  if (intelTab.value === 'ALL') return feedItems.value.slice(0, 40)
  const map = { NEWS: 'news', ANALYSTS: 'twitter', REDDIT: 'reddit', FOMO: 'fomo', PREDICTIONS: 'prediction', SIM: 'simulation' }
  return feedItems.value.filter(i => i.type === map[intelTab.value]).slice(0, 40)
})

const prediction = computed(() => {
  if (fomoScore.value === null || !tickerStats.value.change_1d) return { ready: false }
  const f = fomoScore.value
  const m = tickerStats.value.change_1d || 0
  const rs = (redditSent.value || 0.5)

  const shortScore = Math.round(f * 0.4 + (m > 0 ? 65 : 35) * 0.3 + rs * 100 * 0.3)
  const longScore  = Math.round(rs * 100 * 0.5 + (m > 0 ? 58 : 42) * 0.3 + Math.min(f, 60) * 0.2)

  return {
    ready: true,
    short: shortScore >= 55 ? 'BULLISH' : shortScore <= 45 ? 'BEARISH' : 'NEUTRAL',
    shortPct: shortScore,
    shortClass: shortScore >= 55 ? 'up' : shortScore <= 45 ? 'dn' : 'neu',
    long: longScore >= 55 ? 'BULLISH' : longScore <= 45 ? 'BEARISH' : 'NEUTRAL',
    longPct: longScore,
    longClass: longScore >= 55 ? 'up' : longScore <= 45 ? 'dn' : 'neu',
  }
})

// ── Helpers ───────────────────────────────────────────────────────────────────
function fmtPrice(n) {
  if (n === null || n === undefined) return '—'
  if (n >= 10000) return n.toLocaleString('en', { maximumFractionDigits: 0 })
  if (n >= 100)   return n.toLocaleString('en', { maximumFractionDigits: 2 })
  return n.toFixed(4)
}

function fmtNum(n) {
  if (n >= 1000) return (n / 1000).toFixed(1) + 'k'
  return n
}

// Indian-style large number formatter (matches demat apps: 1.54 Cr, 85.6 L, 12.5k).
function fmtIndian(n) {
  const v = Number(n) || 0
  if (v === 0)        return '—'
  if (v >= 1e7)       return (v / 1e7).toFixed(2).replace(/\.?0+$/, '') + ' Cr'
  if (v >= 1e5)       return (v / 1e5).toFixed(2).replace(/\.?0+$/, '') + ' L'
  if (v >= 1e3)       return (v / 1e3).toFixed(1).replace(/\.?0+$/, '') + 'k'
  return v.toLocaleString('en-IN')
}

function countryFlag(c) {
  const flags = { US:'🇺🇸', UK:'🇬🇧', DE:'🇩🇪', FR:'🇫🇷', EU:'🇪🇺', JP:'🇯🇵',
                  CN:'🇨🇳', HK:'🇭🇰', IN:'🇮🇳', AU:'🇦🇺', KR:'🇰🇷',
                  SG:'🇸🇬', CA:'🇨🇦', BR:'🇧🇷', MX:'🇲🇽', NL:'🇳🇱' }
  return flags[c] || '🌐'
}

function assetIcon(item) {
  if (item.asset_class === 'commodity') {
    const icons = { 'GC=F':'🥇', 'SI=F':'🥈', 'CL=F':'🛢️', 'BZ=F':'🛢️',
                    'NG=F':'🔥', 'HG=F':'🔧', 'ZW=F':'🌾', 'ZC=F':'🌽',
                    'ZS=F':'🫘', 'KC=F':'☕', 'SB=F':'🍬', 'PL=F':'⚪', 'PA=F':'⚪' }
    return icons[item.symbol] || '📦'
  }
  if (item.asset_class === 'etf') return '📊'
  return countryFlag(item.country)
}

function actionClass(action) {
  if (!action) return ''
  if (action.includes('STRONG BUY'))  return 'act-sbuy'
  if (action.includes('BUY'))         return 'act-buy'
  if (action.includes('STRONG SELL')) return 'act-ssell'
  if (action.includes('SELL'))        return 'act-sell'
  return 'act-hold'
}

function scoreTip(item) {
  const d = item.score_detail
  if (!d) return `Score: ${item.score} | Momentum: ${item.rsi} | Trend: ${item.trend} | Volume: ${item.volume_ratio}x avg | 5-day change: ${item.change_5d}%`
  return [
    `Overall Score: ${item.score}/100`,
    `Momentum (RSI ${item.rsi}) → ${d.rsi_score}/100  weight 30%`,
    `Price Trend (${item.trend}) → ${d.ema_score}/100  weight 25%`,
    `Volume spike ${item.volume_ratio}x avg → ${d.vol_score}/100  weight 25%`,
    `5-day price change ${item.change_5d}% → ${d.mom_score}/100  weight 20%`,
  ].join('\n')
}

function fmtChg(v) {
  if (v === null || v === undefined) return '—'
  return (v >= 0 ? '+' : '') + Number(v).toFixed(2) + '%'
}

function badgeLabel(type) {
  return { news: 'NEWS', twitter: 'ANALYST', reddit: 'REDDIT', fomo: 'FOMO', prediction: 'AI PRED', simulation: 'SIM AGENT' }[type] || type.toUpperCase()
}

// ── Data loading ──────────────────────────────────────────────────────────────
async function loadIndices() {
  indicesLoading.value = true
  try {
    const res = await getWorldIndices()
    const priced = res.data || []
    // Merge prices into allAssets for index class
    priced.forEach(p => {
      const existing = allAssets.value.find(a => a.symbol === p.symbol)
      if (existing) {
        existing.price = p.price; existing.change_pct = p.change_pct
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
  countryFilter.value    = 'ALL'
  // Lazy-load tab data on first switch to avoid hitting these endpoints
  // on every page load even when the user never opens these tabs.
  if (ac === 'budget')    refreshBudget()
  if (ac === 'watchlist') loadWatchlist()
}

// ── Budget tab — LLM token consumption ─────────────────────────────────────
// Backend returns a bundle: { today, month, session, all, ...today (top-level
// for back-compat) }. Each period contains total_*/by_model/by_agent/recent_entries.
const budgetData    = ref(null)
const budgetLoading = ref(false)
const budgetPeriod  = ref('today')     // user's selected tile: today|month|session|all
let _budgetTimer    = null

const budgetPeriodLabel = computed(() => ({
  today:   'Today',
  month:   'This Month',
  session: 'Session',
  all:     'All-Time',
}[budgetPeriod.value] || 'Today'))

async function refreshBudget() {
  budgetLoading.value = true
  try {
    // The axios `service` interceptor unwraps response.data already, so
    // getBudget() returns the JSON body directly: { data: { ... } }.
    // Earlier I was reading r.data.data which over-unwrapped to undefined.
    const r = await getBudget()
    budgetData.value = r?.data || null
  } catch (e) {
    console.error('Budget fetch failed', e)
  } finally {
    budgetLoading.value = false
  }
  // Refresh every 30s while the Budget tab is the active view.
  if (_budgetTimer) clearInterval(_budgetTimer)
  _budgetTimer = setInterval(() => {
    if (assetClassFilter.value === 'budget') {
      getBudget().then(r => { budgetData.value = r?.data || null }).catch(() => {})
    } else {
      clearInterval(_budgetTimer); _budgetTimer = null
    }
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

function fmtUSD(v) { return '$' + (Number(v) || 0).toFixed(4) }
function fmtINR(v) {
  // Approx conversion at ~83 INR/USD; gives users a feel for ₹ cost.
  return '₹' + ((Number(v) || 0) * 83).toFixed(2)
}
function fmtTokens(n) {
  n = Number(n) || 0
  if (n >= 1_000_000) return (n / 1_000_000).toFixed(2) + 'M'
  if (n >= 1_000)     return (n / 1_000).toFixed(1) + 'K'
  return String(n)
}

// ── Watchlist tab — pinned tickers persisted in localStorage ───────────────
const WATCHLIST_KEY = 'phoenix.watchlist.v1'
const watchlist     = ref([])           // [{ symbol, name, addedAt }]
const wlInput       = ref('')

function loadWatchlist() {
  try {
    const raw = localStorage.getItem(WATCHLIST_KEY)
    watchlist.value = raw ? JSON.parse(raw) : []
  } catch (e) {
    watchlist.value = []
  }
}

function saveWatchlist() {
  try { localStorage.setItem(WATCHLIST_KEY, JSON.stringify(watchlist.value)) } catch (_) {}
}

function addToWatchlist(sym) {
  sym = (sym || '').trim().toUpperCase()
  if (!sym) return
  if (watchlist.value.some(w => w.symbol === sym)) {
    wlInput.value = ''
    return
  }
  // Try to enrich with name/exchange from allAssets if we know it
  const known = allAssets.value.find(a => a.symbol.toUpperCase() === sym)
  watchlist.value.unshift({
    symbol:  sym,
    name:    known?.name     || sym,
    exchange: known?.exchange || '',
    addedAt: Date.now(),
  })
  saveWatchlist()
  wlInput.value = ''
}

function removeFromWatchlist(sym) {
  watchlist.value = watchlist.value.filter(w => w.symbol !== sym)
  saveWatchlist()
}

// Add the current chart ticker to the watchlist with one click.
function pinCurrentTicker() {
  if (chartTicker.value) addToWatchlist(chartTicker.value)
}

async function runScanner() {
  scanLoading.value = true
  scanResults.value = []
  scanError.value   = ''
  try {
    const res = await scanUniverse(scanMarket.value, 60)
    scanResults.value = res.data || []
    if (!scanResults.value.length) {
      scanError.value = 'No signals found — try a different market or try again shortly.'
    }
    // Feed top 5 into intelligence as insights
    const top5 = scanResults.value.slice(0, 5)
    for (const item of top5) {
      feedItems.value.unshift({
        id:    feedIdCounter++,
        type:  item.action.includes('BUY') ? 'prediction' : 'fomo',
        title: `${item.action}: ${item.name} (${item.symbol}) — Score ${item.score}/100`,
        meta:  `RSI ${item.rsi} · 1D ${item.change_1d > 0 ? '+' : ''}${item.change_1d}% · Vol ${item.volume_ratio}x · Trend: ${item.trend}`,
        sentiment: item.score / 100,
      })
    }
  } catch (e) {
    console.error('Scanner failed', e)
    scanError.value = e.message || 'Scanner failed — check that the backend is running.'
  } finally {
    scanLoading.value = false
  }
}

function selectIndex(idx) {
  selectedIndex.value = idx.symbol
  activeTicker.value  = idx.symbol
  selectTicker(idx.symbol)
}

async function selectTicker(sym) {
  if (!sym) return
  chartTicker.value = sym.toUpperCase()
  activeTicker.value = chartTicker.value
  // Reset sim state for new ticker
  if (simPollTimer) { clearInterval(simPollTimer); simPollTimer = null }
  activeSimId.value = null
  simRound.value    = 0
  simRunning.value  = false
  await Promise.all([reloadChart(), loadSignals()])
  loadLevels()
  indmoneyLivePrice.value = null
  if (indmoneyConnected.value) startIndmoneyStream(chartTicker.value)
  // Simulation must be triggered manually via the Simulate button
}

async function reloadChart() {
  if (!chartTicker.value) return
  chartLoading.value = true
  ohlcv.value = []
  try {
    const end   = new Date()
    const days  = interval.value === '1h' ? 30 : interval.value === '30m' ? 7 : interval.value === '1wk' ? 365 : 90
    const start = new Date(end - days * 86400000)
    const res = await getOHLCV({
      ticker:    chartTicker.value,
      startDate: start.toISOString().slice(0, 10),
      endDate:   end.toISOString().slice(0, 10),
      interval:  interval.value,
    })
    ohlcv.value = res.data?.ohlcv || []
    // Sync chartTicker to backend-resolved ticker (e.g. SBIN → SBIN.NS for currency detection)
    if (res.data?.ticker) {
      chartTicker.value  = res.data.ticker.toUpperCase()
      // Keep input box clean — show SBIN not SBIN.NS
      activeTicker.value = chartTicker.value.replace(/\.(NS|BO)$/i, '')
    }
    chartLoading.value = false          // clear loading BEFORE drawing so v-show reveals SVG
    if (ohlcv.value.length) { await nextTick(); await nextTick(); setTimeout(drawChart, 50) }
  } catch (e) {
    console.error('Chart load failed', e)
  } finally {
    chartLoading.value = false          // also clear on error path
  }
}

// Silent background refresh — no loading spinner, no chart blank-out.
// Merges fresh candles into existing data so new bars appear without visual flash.
async function _silentReloadCandles() {
  if (!chartTicker.value || chartLoading.value) return
  try {
    const end   = new Date()
    const days  = interval.value === '1h' ? 30 : interval.value === '30m' ? 7 : interval.value === '1wk' ? 365 : 90
    const start = new Date(end - days * 86400000)
    const res   = await getOHLCV({
      ticker:    chartTicker.value,
      startDate: start.toISOString().slice(0, 10),
      endDate:   end.toISOString().slice(0, 10),
      interval:  interval.value,
    })
    const fresh = res.data?.ohlcv || []
    if (!fresh.length) return

    // Merge: keep all old candles up to the second-to-last existing bar,
    // then replace from there with fresh data (catches new bars + updated current bar)
    const existing = ohlcv.value
    if (existing.length >= 2) {
      const cutoffDate = existing[existing.length - 2].date
      const keptOld    = existing.filter(c => c.date < cutoffDate)
      const newPart    = fresh.filter(c => c.date >= cutoffDate)
      ohlcv.value = [...keptOld, ...newPart]
    } else {
      ohlcv.value = fresh
    }
    drawChart()
  } catch (_) {}
}

// How often to refresh candles silently based on interval
function _candleRefreshMs() {
  switch (interval.value) {
    case '1d':  return 60_000     // daily: refresh every 60s (live today's candle)
    case '1h':  return 120_000    // hourly: every 2 min
    case '30m': return 90_000     // 30m: every 90s
    default:    return 60_000     // 5m/15m etc: every 60s
  }
}

function _startCandleRefresh() {
  if (_candleRefreshTimer) clearInterval(_candleRefreshTimer)
  _candleRefreshTimer = setInterval(() => {
    if (marketStatus.value.open) _silentReloadCandles()
  }, _candleRefreshMs())
}

function _stopCandleRefresh() {
  if (_candleRefreshTimer) { clearInterval(_candleRefreshTimer); _candleRefreshTimer = null }
}

async function loadSignals() {
  if (!chartTicker.value) return
  signalsLoading.value = true
  try {
    const res   = await getSignals(chartTicker.value)
    const d     = res.data || {}
    tickerStats.value = d.stats || {}
    fomoScore.value   = d.fomo_score ?? null
    redditSent.value  = d.reddit_sentiment ?? null

    // Build feed items
    const newItems = []

    // FOMO entry
    if (d.fomo_score !== undefined) {
      newItems.push({
        id: feedIdCounter++, type: 'fomo',
        title: `FOMO score ${d.fomo_score}/100 — ${d.fomo_score >= 70 ? 'EXTREME: high retail interest, elevated risk' : d.fomo_score >= 45 ? 'Elevated: momentum building' : 'Low: potential accumulation zone'}`,
        meta: `Volume ratio ${d.stats?.volume_ratio || '?'}x avg · 1D ${d.stats?.change_1d || 0}% · 5D ${d.stats?.change_5d || 0}%`,
        sentiment: d.fomo_score / 100,
      })
    }

    // News items
    for (const n of (d.news || [])) {
      newItems.push({
        id: feedIdCounter++, type: 'news',
        title: n.title,
        meta: `${n.source || 'News'} · ${n.pub_date ? new Date(n.pub_date).toLocaleDateString() : ''}`,
        sentiment: undefined,
      })
    }

    // Reddit items (real posts)
    for (const r of (d.reddit || [])) {
      newItems.push({
        id: feedIdCounter++, type: 'reddit',
        title: r.title,
        meta: `r/${r.subreddit} · ▲${r.score} · ${r.comments} comments`,
        score: r.score,
        sentiment: r.upvote_ratio,
      })
    }

    // Seeking Alpha analyst posts (real) → TWITTER tab
    for (const s of (d.stocktwits || [])) {
      newItems.push({
        id: feedIdCounter++, type: 'twitter',
        title: s.title,
        meta: `${s.author} · ${s.source} · ${s.pub_date ? new Date(s.pub_date).toLocaleDateString() : ''}`,
        sentiment: undefined,
      })
    }

    // Prediction entry
    if (d.fomo_score !== undefined) {
      const f = d.fomo_score
      const m = d.stats?.change_1d || 0
      const rs = d.reddit_sentiment || 0.5
      const st = Math.round(f * 0.4 + (m > 0 ? 65 : 35) * 0.3 + rs * 100 * 0.3)
      newItems.push({
        id: feedIdCounter++, type: 'prediction',
        title: `${displayTicker.value} — Short term: ${st >= 55 ? '▲ BULLISH' : st <= 45 ? '▼ BEARISH' : '— NEUTRAL'} (${st}%) · Reddit sentiment ${(rs * 100).toFixed(0)}% bullish`,
        meta: `Based on FOMO ${f}, momentum ${m > 0 ? '+' : ''}${m}%, ${(d.news || []).length} news, ${(d.reddit || []).length} Reddit posts`,
        sentiment: st / 100,
      })
    }

    // Prepend to feed (newest first)
    feedItems.value = [...newItems, ...feedItems.value].slice(0, 100)
  } catch (e) {
    console.error('Signals load failed', e)
  } finally {
    signalsLoading.value = false
  }
}

// ── AI auto-prediction ────────────────────────────────────────────────────────
const aiPredLoading = ref(false)

async function runAiPredict() {
  if (!chartTicker.value || aiPredLoading.value) return
  // Skip option contracts — AI predict needs news/fundamentals which don't
  // exist for an option symbol. Use the F&O scanner / option chain instead.
  if (/^[A-Z]+-[A-Z]{3}\d{4}-\d+-(CE|PE)$/.test(chartTicker.value)) {
    console.info('[runAiPredict] skipped — option contract', chartTicker.value)
    return
  }
  aiPredLoading.value = true
  try {
    const res = await getAiPredict(chartTicker.value)
    const d   = (res.data || res) // interceptor returns res directly

    const shortDir  = d.short_term        || 'NEUTRAL'
    const longDir   = d.long_term         || 'NEUTRAL'
    const shortConf = d.short_confidence  || 50
    const longConf  = d.long_confidence   || 50
    const factors   = (d.key_factors || []).join(' · ')
    const risk      = d.risk_level        || 'MEDIUM'
    const reasoning = d.reasoning         || 'No reasoning provided'

    feedItems.value.unshift({
      id:   feedIdCounter++,
      type: 'prediction',
      title: `${displayTicker.value} — Short: ${shortDir === 'BULLISH' ? '▲' : shortDir === 'BEARISH' ? '▼' : '—'} ${shortDir} ${shortConf}% · Long: ${longDir === 'BULLISH' ? '▲' : longDir === 'BEARISH' ? '▼' : '—'} ${longDir} ${longConf}%`,
      meta:  `${reasoning}${risk ? ' · Risk: ' + risk : ''}${factors ? ' · ' + factors : ''}`,
      sentiment: shortConf / 100,
      ai: true,
    })

    if (d.fomo_score !== undefined) fomoScore.value = d.fomo_score
    if (d.stats?.price) tickerStats.value = { ...tickerStats.value, ...d.stats }
    if (feedItems.value.length > 120) feedItems.value = feedItems.value.slice(0, 100)
  } catch (e) {
    console.error('AI predict failed', e)
    // Show error in feed so user knows something happened
    feedItems.value.unshift({
      id:   feedIdCounter++,
      type: 'prediction',
      title: `⚠ AI Predict failed for ${displayTicker.value}`,
      meta:  e?.message || 'Check backend logs',
      sentiment: 0.5,
    })
  } finally {
    aiPredLoading.value = false
  }
}

// ── OASIS Quick Simulation ────────────────────────────────────────────────────
async function startQuickSim(ticker) {
  if (simRunning.value) return  // Already running
  simRunning.value = true
  try {
    const res = await launchQuickSim(ticker, 6)
    const d = res.data
    if (!d?.simulation_id) return

    activeSimId.value = d.simulation_id
    simRound.value    = 0
    simAgents.value   = d.agents || 0

    feedItems.value.unshift({
      id: feedIdCounter++, type: 'prediction',
      title: `⚡ OASIS Simulation started for ${ticker.replace(/\.(NS|BO)$/i, '')} — ${d.agents} agents (${d.max_rounds} rounds)`,
      meta: d.sim_req,
      sentiment: 0.5,
    })

    // Start polling for agent actions
    if (simPollTimer) clearInterval(simPollTimer)
    simPollTimer = setInterval(() => pollSimulation(), 8000)
  } catch (e) {
    simRunning.value = false
    feedItems.value.unshift({
      id: feedIdCounter++, type: 'simulation',
      title: `⚠ Simulation failed for ${displayTicker.value}`,
      meta: e?.message || 'Check backend logs',
      sentiment: 0.5,
    })
  }
}

async function pollSimulation() {
  if (!activeSimId.value) return
  try {
    const res  = await getQuickSimStatus(activeSimId.value, simRound.value)
    const d    = res.data
    if (!d) return

    // Stream new agent posts into the feed — always tagged as 'simulation'
    for (const action of (d.new_actions || [])) {
      const content = action.action_args?.content || action.action_args?.post_content || ''
      if (!content) continue
      feedItems.value.unshift({
        id:   feedIdCounter++,
        type: 'simulation',
        title: `${action.agent_name}: ${content.slice(0, 200)}`,
        meta:  `Round ${action.round_num} · ${action.platform?.toUpperCase()} · ${action.action_type}`,
        sentiment: undefined,
        simAction: true,
      })
      simRound.value = Math.max(simRound.value, action.round_num)
    }

    if (feedItems.value.length > 150) feedItems.value = feedItems.value.slice(0, 120)

    if (d.completed) {
      clearInterval(simPollTimer)
      simPollTimer  = null
      simRunning.value = false
      feedItems.value.unshift({
        id: feedIdCounter++, type: 'prediction',
        title: `✓ Simulation complete — ${simRound.value} rounds, ${simAgents.value} agents`,
        meta: `${activeSimId.value}`,
        sentiment: 0.5,
      })
    }
  } catch (e) {
    console.error('Sim poll failed', e)
  }
}

// ── Entity Graph ──────────────────────────────────────────────────────────────
async function loadGraph() {
  if (!chartTicker.value || graphLoading.value) return
  graphLoading.value = true
  graphNodes.value = []
  graphLinks.value = []
  try {
    const res = await getEntityGraph(chartTicker.value)
    const d = res.data || {}
    graphNodes.value = d.nodes || []
    graphLinks.value = d.links || []
    await nextTick()
    drawGraph()
  } catch (e) {
    console.error('Graph load failed', e)
  } finally {
    graphLoading.value = false
  }
}

function drawGraph() {
  const wrap = graphWrap.value
  if (!wrap || !graphNodes.value.length) return
  const W = wrap.clientWidth || 360
  const H = wrap.clientHeight || 220

  const nodes = graphNodes.value.map(n => ({ ...n }))
  const links = graphLinks.value.map(l => ({ ...l }))

  const svg = d3.select(graphSvg.value)
  svg.selectAll('*').remove()
  svg.attr('width', W).attr('height', H)

  const sim = d3.forceSimulation(nodes)
    .force('link',   d3.forceLink(links).id(d => d.id).distance(70))
    .force('charge', d3.forceManyBody().strength(-120))
    .force('center', d3.forceCenter(W / 2, H / 2))
    .force('collide', d3.forceCollide(22))

  const g = svg.append('g')

  svg.call(d3.zoom().scaleExtent([0.4, 3]).on('zoom', e => g.attr('transform', e.transform)))

  const link = g.append('g').selectAll('line').data(links).enter().append('line')
    .attr('stroke', '#2a2a2a').attr('stroke-width', d => Math.max(1, (d.weight || 0.5) * 2))

  const linkLabel = g.append('g').selectAll('text').data(links).enter().append('text')
    .attr('font-size', 8).attr('fill', '#444').attr('text-anchor', 'middle').text(d => d.relation || '')

  const node = g.append('g').selectAll('circle').data(nodes).enter().append('circle')
    .attr('r', d => d.type === 'ticker' ? 14 : 9)
    .attr('fill', d => graphColorMap[d.type] || '#888')
    .attr('stroke', '#0a0a0a').attr('stroke-width', 1.5)
    .call(d3.drag()
      .on('start', (e, d) => { if (!e.active) sim.alphaTarget(0.3).restart(); d.fx = d.x; d.fy = d.y })
      .on('drag',  (e, d) => { d.fx = e.x; d.fy = e.y })
      .on('end',   (e, d) => { if (!e.active) sim.alphaTarget(0); d.fx = null; d.fy = null }))

  const label = g.append('g').selectAll('text').data(nodes).enter().append('text')
    .attr('font-size', d => d.type === 'ticker' ? 11 : 9)
    .attr('fill', d => d.type === 'ticker' ? '#00d4a8' : '#aaa')
    .attr('text-anchor', 'middle').attr('dy', d => d.type === 'ticker' ? -18 : -13)
    .text(d => d.label || d.id)

  sim.on('tick', () => {
    link.attr('x1', d => d.source.x).attr('y1', d => d.source.y)
        .attr('x2', d => d.target.x).attr('y2', d => d.target.y)
    linkLabel.attr('x', d => (d.source.x + d.target.x) / 2)
             .attr('y', d => (d.source.y + d.target.y) / 2)
    node.attr('cx', d => d.x).attr('cy', d => d.y)
    label.attr('x', d => d.x).attr('y', d => d.y)
  })
}

// ── Investment Analysis ───────────────────────────────────────────────────────
async function switchToAnalysis() {
  viewMode.value = 'analysis'
  if (!investData.value && !investLoading.value && chartTicker.value) {
    loadInvestAnalysis()
  } else if (investData.value) {
    // data already loaded — just redraw the graph after DOM settles
    await nextTick()
    await nextTick()
    setTimeout(drawInvestGraph, 50)
  }
}

function verdictKey(v) {
  const map = { 'STRONG BUY': 'sbuy', 'BUY': 'buy', 'HOLD': 'hold', 'SELL': 'sell', 'STRONG SELL': 'ssell' }
  return map[v] || 'hold'
}

function runPortfolio() {
  if (portfolioLoading.value) return
  if (_portEventSource) { _portEventSource.close(); _portEventSource = null }

  portfolioLoading.value = true
  portfolioError.value   = ''
  portfolioResult.value  = null
  portfolioFeed.value    = []
  portfolioRound.value   = 0

  const es = createPortfolioSimStream(portfolioCapital.value, portfolioHorizon.value, portfolioScope.value)
  _portEventSource = es

  es.onmessage = (event) => {
    try {
      const ev = JSON.parse(event.data)
      if (ev.type === 'heartbeat') return
      if (ev.type === 'round_start') portfolioRound.value = ev.round
      if (['agent_action','round_start','round_end','status'].includes(ev.type))
        portfolioFeed.value.push(ev)
      if (ev.type === 'completed') {
        portfolioResult.value  = ev.portfolio
        portfolioLoading.value = false
        es.close()
      }
      if (ev.type === 'error') {
        portfolioError.value   = ev.msg || 'Simulation error'
        portfolioLoading.value = false
        es.close()
      }
    } catch (_) {}
  }

  es.onerror = () => {
    if (portfolioLoading.value) {
      portfolioError.value   = 'Connection lost — try again'
      portfolioLoading.value = false
    }
    es.close()
  }
}

function loadInvestAnalysis() {
  if (!chartTicker.value || investLoading.value) return

  // Close any previous stream
  if (investStream) { investStream.close(); investStream = null }

  investLoading.value    = true
  investData.value       = null
  investError.value      = ''
  selectedAgent.value    = null
  investPhase.value      = 'Connecting…'
  investAgentsDone.value = 0
  investAgentsTotal.value = 0

  investStream = createInvestAnalysisStream(chartTicker.value)

  investStream.onmessage = async (e) => {
    try {
      const msg = JSON.parse(e.data)

      if (msg.type === 'heartbeat') return

      if (msg.type === 'phase') {
        investPhase.value = msg.msg
        if (msg.agent_count) investAgentsTotal.value = msg.agent_count
      }

      if (msg.type === 'agent') {
        investAgentsDone.value++
      }

      if (msg.type === 'debate') {
        investPhase.value = 'Debate complete — CIO deliberating…'
      }

      if (msg.type === 'done' || msg._cached) {
        const data = msg.data || msg
        investData.value = data
        investLoading.value = false
        investStream?.close(); investStream = null
        await nextTick()
        await nextTick()
        drawInvestGraph()
      }

      if (msg.type === 'error') {
        investError.value = msg.msg || 'Analysis failed'
        investLoading.value = false
        investStream?.close(); investStream = null
      }
    } catch (err) {
      console.warn('invest stream parse error', err)
    }
  }

  investStream.onerror = () => {
    investError.value = 'Stream connection lost — please retry'
    investLoading.value = false
    investStream?.close(); investStream = null
  }
}

function drawInvestGraph() {
  const wrap = investGraphWrap.value
  if (!wrap || !investData.value?.nodes?.length) return

  const W = wrap.clientWidth  || 700
  const H = wrap.clientHeight || 500

  const vColor = {
    'STRONG BUY': '#22c55e', 'BUY': '#86efac', 'HOLD': '#fbbf24',
    'SELL': '#f97316', 'STRONG SELL': '#ef4444'
  }
  const typeColor = {
    ticker:   '#00d4a8',
    agent:    null,          // uses verdict color
    evidence: null,          // uses verdict color dimmed
    verdict:  null,          // uses verdict color bright
    risk:     '#f87171',
  }

  const nodes = investData.value.nodes.map(n => ({ ...n }))
  const links = investData.value.links.map(l => ({ ...l }))

  function nodeColor(d) {
    if (d.type === 'ticker')   return typeColor.ticker
    if (d.type === 'risk')     return typeColor.risk
    const v = d.verdict || (d.type === 'verdict' ? investData.value.final?.final_verdict : null)
    const base = vColor[v] || '#888'
    if (d.type === 'evidence') return base + '66'
    return base
  }
  function nodeRadius(d) {
    if (d.type === 'ticker')  return 22
    if (d.type === 'agent')   return 16
    if (d.type === 'verdict') return 18
    if (d.type === 'risk')    return 9
    return 6
  }

  const svg = d3.select(investGraphSvg.value)
  svg.selectAll('*').remove()
  svg.attr('width', W).attr('height', H)

  // Arrow marker
  svg.append('defs').append('marker')
    .attr('id', 'ia-arrow').attr('viewBox', '0 -4 8 8').attr('refX', 14)
    .attr('markerWidth', 6).attr('markerHeight', 6).attr('orient', 'auto')
    .append('path').attr('d', 'M0,-4L8,0L0,4').attr('fill', '#333')

  const zoomBehavior = d3.zoom().scaleExtent([0.3, 4]).on('zoom', e => g.attr('transform', e.transform))
  const g = svg.append('g')
  svg.call(zoomBehavior)

  // Scale force distances based on node count — fewer nodes = more spread
  const agentCount = nodes.filter(n => n.type === 'agent').length
  const distScale  = agentCount <= 8 ? 2.2 : agentCount <= 12 ? 1.8 : 1.4

  const sim = d3.forceSimulation(nodes)
    .force('link',    d3.forceLink(links).id(d => d.id).distance(d => (d.relation === 'supports' ? 70 : 130) * distScale))
    .force('charge',  d3.forceManyBody().strength(-400 * distScale))
    .force('center',  d3.forceCenter(W / 2, H / 2))
    .force('collide', d3.forceCollide(d => nodeRadius(d) + 18))

  const link = g.append('g').selectAll('line').data(links).enter().append('line')
    .attr('stroke', d => d.relation === 'risk' ? '#7f1d1d44' : '#2a2a2a')
    .attr('stroke-width', d => d.relation === 'supports' ? 0.8 : 1.5)
    .attr('stroke-dasharray', d => d.relation === 'risk' ? '3,3' : null)
    .attr('marker-end', d => d.relation === 'vote' ? 'url(#ia-arrow)' : null)

  const node = g.append('g').selectAll('circle').data(nodes).enter().append('circle')
    .attr('r', nodeRadius)
    .attr('fill', nodeColor)
    .attr('stroke', d => d.type === 'ticker' ? '#00d4a8' : d.type === 'verdict' ? '#fff3' : '#1a1a1a')
    .attr('stroke-width', d => d.type === 'ticker' || d.type === 'verdict' ? 2 : 1)
    .style('cursor', d => d.type === 'agent' ? 'pointer' : 'default')
    .on('click', (e, d) => {
      if (d.type === 'agent') {
        selectedAgent.value = d.type === 'agent' ? d : null
      }
    })
    .call(d3.drag()
      .on('start', (e, d) => { if (!e.active) sim.alphaTarget(0.3).restart(); d.fx = d.x; d.fy = d.y })
      .on('drag',  (e, d) => { d.fx = e.x; d.fy = e.y })
      .on('end',   (e, d) => { if (!e.active) sim.alphaTarget(0); d.fx = null; d.fy = null }))

  // Labels — show for ticker, agent, verdict
  const label = g.append('g').selectAll('text').data(nodes).enter().append('text')
    .attr('font-size', d => d.type === 'ticker' ? 13 : d.type === 'agent' ? 11 : d.type === 'verdict' ? 12 : 8)
    .attr('fill', d => d.type === 'ticker' ? '#00d4a8' : d.type === 'verdict' ? '#fff' : d.type === 'agent' ? '#ccc' : '#555')
    .attr('text-anchor', 'middle')
    .attr('dy', d => -(nodeRadius(d) + 4))
    .text(d => {
      if (d.type === 'evidence' || d.type === 'risk') return ''
      if (d.type === 'ticker') {
        const company = investData.value?.company || tickerStats.value?.company_name || ''
        return company || d.label.replace(/\.(NS|BO)$/i, '')
      }
      if (d.type === 'agent') return d.label.replace(' Analyst', '').replace(' Manager', ' Mgr')
      return d.label.replace(/\.(NS|BO)$/i, '')
    })

  // Confidence ring on agent nodes
  const ring = g.append('g').selectAll('circle.ring').data(nodes.filter(n => n.type === 'agent')).enter()
    .append('circle')
    .attr('r', d => nodeRadius(d) + 4)
    .attr('fill', 'none')
    .attr('stroke', d => nodeColor(d))
    .attr('stroke-width', 1)
    .attr('stroke-dasharray', d => {
      const circ = 2 * Math.PI * (nodeRadius(d) + 4)
      const filled = circ * (d.confidence || 50) / 100
      return `${filled} ${circ - filled}`
    })
    .attr('opacity', 0.5)

  function tick() {
    link.attr('x1', d => d.source.x).attr('y1', d => d.source.y)
        .attr('x2', d => d.target.x).attr('y2', d => d.target.y)
    node.attr('cx', d => d.x).attr('cy', d => d.y)
    label.attr('x', d => d.x).attr('y', d => d.y)
    ring.attr('cx', d => d.x).attr('cy', d => d.y)
      .attr('stroke-dashoffset', d => {
        const circ = 2 * Math.PI * (nodeRadius(d) + 4)
        return circ * 0.25
      })
  }

  sim.on('tick', tick)

  // Auto-fit zoom once simulation stabilizes — fill ~88% of the canvas
  sim.on('end', () => {
    const visNodes = nodes.filter(n => n.type === 'agent' || n.type === 'ticker' || n.type === 'verdict')
    if (!visNodes.length) return
    const pad = 60
    const xs = visNodes.map(n => n.x), ys = visNodes.map(n => n.y)
    const x0 = Math.min(...xs) - pad, x1 = Math.max(...xs) + pad
    const y0 = Math.min(...ys) - pad, y1 = Math.max(...ys) + pad
    const scaleX = W / (x1 - x0), scaleY = H / (y1 - y0)
    const scale  = Math.min(scaleX, scaleY, 1.8) * 0.88
    const tx = (W - scale * (x0 + x1)) / 2
    const ty = (H - scale * (y0 + y1)) / 2
    svg.transition().duration(800)
       .call(zoomBehavior.transform, d3.zoomIdentity.translate(tx, ty).scale(scale))
  })
}

// ── Trade Levels ──────────────────────────────────────────────────────────────
async function loadLevels() {
  if (!chartTicker.value || levelsLoading.value) return
  levelsLoading.value = true
  try {
    const res = await getTradeLevels(chartTicker.value)
    levels.value = res.data || null
  } catch (e) {
    levels.value = null
  } finally {
    levelsLoading.value = false
  }
}

function toggleTheme() {
  lightMode.value = !lightMode.value
  localStorage.setItem('theme', lightMode.value ? 'light' : 'dark')
}

function onSearchInput() {
  clearTimeout(searchTimer)
  const q = activeTicker.value.trim()
  if (q.length < 2) { searchSuggestions.value = []; return }
  searchTimer = setTimeout(async () => {
    try {
      const res = await searchTicker(q)
      searchSuggestions.value = res.data || []
    } catch { searchSuggestions.value = [] }
  }, 350)
}
function pickSuggestion(s) {
  // Show clean name in input (SBIN, not SBIN.NS) but pass full symbol to loader
  activeTicker.value      = s.symbol.replace(/\.(NS|BO)$/i, '')
  searchSuggestions.value = []
  selectTicker(s.symbol)
}
function hideSuggestionsDelayed() {
  setTimeout(() => { searchSuggestions.value = [] }, 200)
}

function btGrade(s) {
  const score = (s.sharpe > 1.5 ? 3 : s.sharpe > 0.8 ? 2 : s.sharpe > 0.3 ? 1 : 0)
              + (s.total_return_pct > 20 ? 3 : s.total_return_pct > 10 ? 2 : s.total_return_pct > 0 ? 1 : 0)
              + (s.win_rate_pct > 60 ? 2 : s.win_rate_pct > 45 ? 1 : 0)
  return score >= 6 ? 'A' : score >= 4 ? 'B' : score >= 2 ? 'C' : 'D'
}
function btGradeClass(s) {
  const g = btGrade(s)
  return g === 'A' ? 'grade-a' : g === 'B' ? 'grade-b' : g === 'C' ? 'grade-c' : 'grade-d'
}
function btShortName(name) {
  return name.replace(' (14, 35/65)', '').replace(' (9/21)', ' 9/21').replace(' (21/50)', ' 21/50').replace(' (20, 2σ)', '')
}

async function runBacktest() {
  if (!chartTicker.value || btLoading.value) return
  btLoading.value = true
  btResult.value  = null
  btError.value   = ''
  try {
    const res = await runVbtBacktest(chartTicker.value)
    btResult.value = res.data
  } catch (e) {
    btError.value = e?.message || 'Backtest failed'
  } finally {
    btLoading.value = false
  }
}

// doManualTrade removed — wallet/manual-trade concept eliminated.

// ── Kite Connect ──────────────────────────────────────────────────────────────
// Direct real-time chart redraw on every tick.
function scheduleDrawChart() {
  drawChart()
}

// ── INDmoney / INDstocks ──────────────────────────────────────────────────────
async function checkIndmoneyStatus() {
  try {
    const res = await getIndmoneyStatus()
    const d   = res.data?.data || res.data || res
    indmoneyAvailable.value  = d.token_configured || false
    indmoneyConnected.value  = d.connected || false
    indmoneyName.value       = d.name || ''
    if (indmoneyConnected.value && chartTicker.value) startIndmoneyStream(chartTicker.value)
  } catch (e) { indmoneyConnected.value = false }
}

async function indmoneyOpen() {
  // Re-check status — token may have been added to .env since page load
  await checkIndmoneyStatus()
  if (indmoneyConnected.value) {
    // Already connected — nothing to do; status dot will be visible
    return
  }
  if (indmoneyAvailable.value) {
    alert('Token is configured but connection check failed.\nCheck that INDMONEY_ACCESS_TOKEN is valid and restart the backend.')
  } else {
    alert('INDmoney not connected.\n\nAdd to your .env:\n  INDMONEY_ACCESS_TOKEN=<your-token>\n\nGet it from indstocks.com → Dashboard → API section.\nThen restart the backend.')
  }
}

// Chart ticker live price now reuses the SAME per-underlying SSE stream as
// liveSpots / trackedPositions / liveTickets. Previously we had a SECOND
// independent EventSource (startIndmoneyStream) hitting the same backend
// endpoint, doubling SSE load and producing duplicate "pending" rows in the
// browser Network tab. The watcher below mirrors liveSpots[chartTicker] →
// indmoneyLivePrice for the chart's existing real-time updates.
function startIndmoneyStream(ticker) {
  if (!ticker) return
  const sym = ticker.toUpperCase()
  // Just ensure the per-underlying stream is open; the dedup logic in
  // _openTicketStream silently no-ops if it's already running.
  _openTicketStream(sym)
}

function stopIndmoneyStream() { /* no-op — streams are managed by the
  liveTickets/trackedPositions watcher; closing here would kill ticket
  repricing for whichever ticker we're switching away from. */ }

function _applyLivePrice(price) {
  if (!price || !ohlcv.value.length) return
  const candles = [...ohlcv.value]
  const last    = { ...candles[candles.length - 1] }
  last.close    = price
  if (price > last.high) last.high = price
  if (price < last.low)  last.low  = price
  candles[candles.length - 1] = last
  ohlcv.value = candles
  scheduleDrawChart()
}

async function runIntradaySignal() {
  if (!chartTicker.value) return
  signalLoading.value = true
  signal.value = null
  signalError.value = ''
  try {
    const res = await getIntradaySignal(chartTicker.value)
    signal.value = res.data || null
  } catch (e) {
    signalError.value = e?.message || 'Signal fetch failed'
  } finally {
    signalLoading.value = false
  }
}

// confirmReset removed — wallet concept eliminated in live mode.

// ── D3 Chart ──────────────────────────────────────────────────────────────────
const GREEN = '#00d4a8'
const RED   = '#ff4757'
const M     = { top: 12, right: 56, bottom: 64, left: 8 }
const VOL_R = 0.15

// ── Candlestick pattern detector ─────────────────────────────────────────────
function detectCandlePatterns(candles) {
  const pats = []
  for (let i = 1; i < candles.length; i++) {
    const c  = candles[i],     p  = candles[i - 1]
    const pp = i >= 2 ? candles[i - 2] : null
    const body  = Math.abs(c.close - c.open)
    const range = c.high - c.low
    if (range === 0) continue
    const upper  = c.high - Math.max(c.open, c.close)
    const lower  = Math.min(c.open, c.close) - c.low
    const bull   = c.close > c.open
    const pBull  = p.close > p.open
    const pBody  = Math.abs(p.close - p.open)
    const pRange = p.high - p.low

    // Doji — body < 10% of range
    if (body / range < 0.10 && range > 0) {
      pats.push({ i, signal: 'neutral', name: 'Doji',
        desc: 'Indecision: open ≈ close, neither bulls nor bears in control. At a trend extreme this often signals a reversal — wait for the next candle to confirm direction.' })
      continue
    }
    // Hammer — long lower wick, small body near top, after downtrend (bull or bear candle both valid)
    if (lower > 2 * body && upper < body && i >= 3) {
      const trend = candles[i-3].close > c.close
      if (trend) { pats.push({ i, signal: 'bull', name: 'Hammer',
        desc: 'Bullish reversal. Sellers drove price far down but buyers clawed it back to near the open. The long lower wick is the fingerprint. Buy when next candle closes green above this candle\'s high.' })
        continue }
    }
    // Inverted Hammer — long upper wick, small body near bottom, after downtrend
    if (upper > 2 * body && lower < body && i >= 3) {
      const downtrend = candles[i-3].close > c.close
      if (downtrend && !bull) { pats.push({ i, signal: 'bull', name: 'Inverted Hammer',
        desc: 'Tentative bullish reversal. Buyers tried to push high but failed — however this attempt signals weakening selling pressure. Confirm with a strong green candle next.' })
        continue }
    }
    // Shooting Star — long upper wick, small body near bottom, after uptrend
    if (upper > 2 * body && lower < body && i >= 3) {
      const uptrend = candles[i-3].close < c.close
      if (uptrend) { pats.push({ i, signal: 'bear', name: 'Shooting Star',
        desc: 'Bearish reversal at resistance. Buyers spiked price up but sellers rejected it hard — long upper wick shows sellers won. Tighten stop or consider exiting longs.' })
        continue }
    }
    // Hanging Man — hammer shape but after uptrend = bearish
    if (lower > 2 * body && upper < body && i >= 3) {
      const uptrend = candles[i-3].close < c.close
      if (uptrend) { pats.push({ i, signal: 'bear', name: 'Hanging Man',
        desc: 'Bearish warning after uptrend. Same shape as Hammer but context is reversed — sellers entered aggressively. Confirm with a red candle closing below this candle\'s low.' })
        continue }
    }
    // Bullish Engulfing
    if (!pBull && bull && body > pBody && c.open <= p.close && c.close >= p.open) {
      pats.push({ i, signal: 'bull', name: 'Bullish Engulfing',
        desc: 'Strong reversal signal. This green candle completely swallows the prior red candle — buyers overwhelmed sellers in a single session. High-probability entry on next open.' })
      continue
    }
    // Bearish Engulfing
    if (pBull && !bull && body > pBody && c.open >= p.close && c.close <= p.open) {
      pats.push({ i, signal: 'bear', name: 'Bearish Engulfing',
        desc: 'Strong reversal signal. This red candle completely swallows the prior green — sellers took full control. Consider exiting longs or entering short on next candle open.' })
      continue
    }
    // Marubozu — full body, virtually no wicks (body > 90% of range)
    if (body / range > 0.90) {
      pats.push({ i, signal: bull ? 'bull' : 'bear',
        name: bull ? 'Bullish Marubozu' : 'Bearish Marubozu',
        desc: bull
          ? 'Opened at low, closed at high — buyers were in control every minute of this session. Strong continuation signal. No wick means zero hesitation.'
          : 'Opened at high, closed at low — sellers dominated the entire session with no pushback. Strong continuation or breakdown signal.' })
      continue
    }
    // Morning Star (3-candle bullish reversal)
    if (pp && !(pp.close > pp.open) && pRange < Math.abs(pp.close - pp.open) * 0.5 && bull && body > Math.abs(pp.close - pp.open) * 0.5) {
      pats.push({ i, signal: 'bull', name: 'Morning Star',
        desc: '3-candle bullish reversal: big red candle → small indecision candle (the star, gaps lower) → big green candle closing back into the red. One of the most reliable reversal signals — marks end of downtrend.' })
      continue
    }
    // Evening Star (3-candle bearish reversal)
    if (pp && pp.close > pp.open && pRange < Math.abs(pp.close - pp.open) * 0.5 && !bull && body > Math.abs(pp.close - pp.open) * 0.5) {
      pats.push({ i, signal: 'bear', name: 'Evening Star',
        desc: '3-candle bearish reversal: big green candle → small indecision candle (the star, gaps higher) → big red candle closing back into the green. Signals bulls are exhausted — high-probability top.' })
      continue
    }
    // Piercing Line (bullish)
    if (!pBull && bull && c.open < p.low && c.close > (p.open + p.close) / 2 && c.close < p.open) {
      pats.push({ i, signal: 'bull', name: 'Piercing Line',
        desc: 'Bullish reversal: green candle opens below prior red candle\'s low (gap down) but closes above its midpoint. Buyers absorbed all the selling and fought back — watch for follow-through.' })
      continue
    }
    // Dark Cloud Cover (bearish)
    if (pBull && !bull && c.open > p.high && c.close < (p.open + p.close) / 2 && c.close > p.open) {
      pats.push({ i, signal: 'bear', name: 'Dark Cloud Cover',
        desc: 'Bearish reversal: red candle opens above prior green candle\'s high (gap up) but closes below its midpoint. Sellers absorbed all the buying — momentum is shifting down.' })
      continue
    }
    // Tweezer Bottom
    if (!pBull && bull && Math.abs(c.low - p.low) / (range || 1) < 0.03) {
      pats.push({ i, signal: 'bull', name: 'Tweezer Bottom',
        desc: 'Two consecutive candles hit the exact same low — sellers tried twice to break that level and failed both times. A clear double-rejection at support. Bullish reversal.' })
    }
    // Tweezer Top
    if (pBull && !bull && Math.abs(c.high - p.high) / (range || 1) < 0.03) {
      pats.push({ i, signal: 'bear', name: 'Tweezer Top',
        desc: 'Two consecutive candles hit the exact same high — buyers tried twice to break that level and failed both times. A clear double-rejection at resistance. Bearish reversal.' })
    }
    // Bullish Harami — small green candle inside a large red candle (reversal)
    if (!pBull && bull && c.open > p.close && c.close < p.open && body < pBody * 0.6) {
      pats.push({ i, signal: 'bull', name: 'Bullish Harami',
        desc: 'Small green candle nestled inside a large red candle. "Harami" means pregnant in Japanese — the mother (red) contains the baby (green). Selling momentum is slowing. Not a strong signal alone; wait for a green confirmation candle.' })
      continue
    }
    // Bearish Harami — small red candle inside a large green candle (reversal)
    if (pBull && !bull && c.open < p.close && c.close > p.open && body < pBody * 0.6) {
      pats.push({ i, signal: 'bear', name: 'Bearish Harami',
        desc: 'Small red candle nestled inside a large green candle. Buyers are losing steam after a strong up-move. The small body shows uncertainty. Confirm with a red candle closing below the small candle\'s low before acting.' })
      continue
    }
    // Bullish Harami Cross — doji inside a large red candle
    if (!pBull && body / (range || 1) < 0.10 && c.open > p.close && c.close < p.open) {
      pats.push({ i, signal: 'bull', name: 'Bullish Harami Cross',
        desc: 'A doji (open ≈ close) sitting completely inside a large red candle. Stronger than a regular Harami — the perfect indecision of the doji after strong selling signals a sharper shift. High-probability reversal candidate.' })
      continue
    }
    // Bearish Harami Cross — doji inside a large green candle
    if (pBull && body / (range || 1) < 0.10 && c.open < p.close && c.close > p.open) {
      pats.push({ i, signal: 'bear', name: 'Bearish Harami Cross',
        desc: 'A doji sitting completely inside a large green candle. After a strong up-move, complete indecision appears — bulls have exhausted their push. Stronger reversal signal than a regular Bearish Harami.' })
      continue
    }
  }

  // ── Multi-candle patterns (need 4-5 candles) ─────────────────────────────
  for (let i = 4; i < candles.length; i++) {
    const c  = candles[i]
    const p  = candles[i-1]
    const pp = candles[i-2]
    const c3 = candles[i-3]
    const c4 = candles[i-4]

    const bull  = c.close  > c.open
    const pBull = p.close  > p.open
    const ppBull= pp.close > pp.open
    const c3Bull= c3.close > c3.open

    const body  = Math.abs(c.close - c.open)
    const pBody = Math.abs(p.close - p.open)
    const ppBody= Math.abs(pp.close - pp.open)
    const c3Body= Math.abs(c3.close - c3.open)
    const c4Body= Math.abs(c4.close - c4.open)

    // Three White Soldiers — 3 consecutive strong green candles, each closing higher
    if (pBull && ppBull && c3Bull &&
        p.close > pp.close && pp.close > c3.close &&
        p.open > pp.open   && pp.open > c3.open &&
        pBody  > pBody * 0.5 && ppBody > c3Body * 0.5) {
      if (bull && c.close > p.close) {
        // mark on the 3rd candle
        pats.push({ i: i-2, signal: 'bull', name: 'Three White Soldiers',
          desc: 'Three consecutive strong green candles, each opening within the prior body and closing at a new high. One of the most reliable bullish reversal/continuation signals — sustained buying across three sessions with no hesitation.' })
      }
      continue
    }
    // Three Black Crows — 3 consecutive strong red candles, each closing lower
    if (!pBull && !ppBull && !c3Bull &&
        p.close < pp.close && pp.close < c3.close &&
        p.open  < pp.open  && pp.open  < c3.open &&
        pBody   > ppBody * 0.5 && ppBody > c3Body * 0.5) {
      if (!bull && c.close < p.close) {
        pats.push({ i: i-2, signal: 'bear', name: 'Three Black Crows',
          desc: 'Three consecutive strong red candles, each opening within the prior body and closing at a new low. Sustained selling across three sessions with no recovery — one of the most bearish patterns, often signals start of a significant downtrend.' })
      }
      continue
    }
    // Rising Three Methods — long green, 3 small reds staying within range, strong green (continuation)
    if (c3Bull && c3Body > ppBody * 1.5 &&
        !ppBull && !pBull &&
        pp.high < c3.close && pp.low > c3.open &&
        p.high  < c3.close && p.low  > c3.open &&
        bull && c.close > c3.close) {
      pats.push({ i, signal: 'bull', name: 'Rising Three Methods',
        desc: 'Strong green candle → 3 small red candles (pullback contained within the green\'s range) → another strong green breaking to new high. Bulls paused to reload then resumed. Classic bullish continuation — the pullback was just profit-taking, not a reversal.' })
      continue
    }
    // Falling Three Methods — long red, 3 small greens staying within range, strong red (continuation)
    if (!c3Bull && c3Body > ppBody * 1.5 &&
        ppBull && pBull &&
        pp.high < c3.open && pp.low > c3.close &&
        p.high  < c3.open && p.low  > c3.close &&
        !bull && c.close < c3.close) {
      pats.push({ i, signal: 'bear', name: 'Falling Three Methods',
        desc: 'Strong red candle → 3 small green candles (bounce contained within the red\'s range) → another strong red breaking to new low. Bears paused then resumed selling. Classic bearish continuation — the bounce was just short covering, not a reversal.' })
      continue
    }
  }

  return pats
}

const patternTooltip = ref({ visible: false, x: 0, y: 0, name: '', desc: '', signal: '' })

function drawChart() {
  try {
    return _drawChartImpl()
  } catch (e) {
    console.error('[drawChart] failed for', chartTicker.value, e)
  }
}

function _drawChartImpl() {
  const all     = ohlcv.value
  const wrap    = chartWrap.value
  if (!wrap || !all.length) return
  // Mouse-wheel zoom keeps the right edge fixed and slices to the last N candles.
  const candles = chartVisibleCount.value && chartVisibleCount.value < all.length
    ? all.slice(-chartVisibleCount.value)
    : all

  const W = wrap.clientWidth
  const H = wrap.clientHeight
  // DOM not laid out yet — retry once it has dimensions
  if (!W || !H) { setTimeout(drawChart, 80); return }
  const iW   = Math.max(0, W - M.left - M.right)
  const volH = Math.max(0, Math.floor((H - M.top - M.bottom) * VOL_R))
  const cH   = Math.max(0, H - M.top - M.bottom - volH - 6)
  // Not enough room to draw anything meaningful
  if (cH < 20 || iW < 20) return

  const xB = d3.scaleBand().domain(candles.map(d => d.date)).range([0, iW]).padding(0.2)
  const pd  = d3.timeParse('%Y-%m-%d')
  const xT  = d3.scaleTime().domain([pd(candles[0].date), pd(candles[candles.length-1].date)]).range([0, iW])

  const lp   = indmoneyLivePrice.value
  const pMin = Math.min(d3.min(candles, d => d.low),  lp || Infinity)
  const pMax = Math.max(d3.max(candles, d => d.high), lp || -Infinity)
  const buf  = (pMax - pMin) * 0.06
  const yP   = d3.scaleLinear().domain([pMin - buf, pMax + buf]).range([cH, 0])
  const _vMax = d3.max(candles, d => Number(d.volume) || 0) || 1
  const yV   = d3.scaleLinear().domain([0, _vMax * 1.2]).range([volH, 0])

  const svg = d3.select(svgRef.value)
  svg.selectAll('*').remove()
  svg.attr('width', W).attr('height', H)

  const g = svg.append('g').attr('transform', `translate(${M.left},${M.top})`)

  // grid
  g.append('g')
    .call(d3.axisLeft(yP).ticks(6).tickSize(-iW).tickFormat(''))
    .call(n => n.select('.domain').remove())
    .call(n => n.selectAll('.tick line').attr('stroke', lightMode.value ? '#e0e0e0' : '#1e1e1e'))

  // wicks
  g.selectAll('.wick').data(candles).join('line')
    .attr('x1', d => xB(d.date) + xB.bandwidth() / 2)
    .attr('x2', d => xB(d.date) + xB.bandwidth() / 2)
    .attr('y1', d => yP(d.high)).attr('y2', d => yP(d.low))
    .attr('stroke', d => d.close >= d.open ? GREEN : RED).attr('stroke-width', 1)

  // bodies
  g.selectAll('.body').data(candles).join('rect')
    .attr('x',      d => xB(d.date))
    .attr('y',      d => yP(Math.max(d.open, d.close)))
    .attr('width',  xB.bandwidth())
    .attr('height', d => Math.max(1, Math.abs(yP(d.open) - yP(d.close))))
    .attr('fill',   d => d.close >= d.open ? GREEN : RED)
    .attr('rx', 1)

  // price axis
  g.append('g').attr('transform', `translate(${iW},0)`)
    .call(d3.axisRight(yP).ticks(6).tickFormat(d => fmtPrice(d)))
    .call(n => n.select('.domain').remove())
    .call(n => n.selectAll('text').attr('fill', '#666').attr('font-size', '11px'))

  // volume panel
  const vG = g.append('g').attr('transform', `translate(0,${cH + 6})`)
  vG.selectAll('.vbar').data(candles).join('rect')
    .attr('x',      d => xB(d.date))
    .attr('y',      d => yV(d.volume))
    .attr('width',  xB.bandwidth())
    .attr('height', d => Math.max(0, volH - yV(d.volume)))
    .attr('fill',   d => d.close >= d.open ? `${GREEN}44` : `${RED}44`).attr('rx', 1)

  vG.append('g').attr('transform', `translate(${iW},0)`)
    .call(d3.axisRight(yV).ticks(2).tickFormat(d => `${(d/1e6).toFixed(0)}M`))
    .call(n => n.select('.domain').remove())
    .call(n => n.selectAll('text').attr('fill', '#444').attr('font-size', '10px'))

  // x axis
  g.append('g').attr('transform', `translate(0,${cH + 6 + volH + 4})`)
    .call(d3.axisBottom(xT).ticks(Math.min(candles.length, 9)).tickFormat(d3.timeFormat('%m/%d')))
    .call(n => n.select('.domain').attr('stroke', '#2a2a2a'))
    .call(n => n.selectAll('text').attr('fill', '#555').attr('font-size', '11px'))
    .call(n => n.selectAll('.tick line').attr('stroke', '#2a2a2a'))

  // ── Previous Day High / Low lines ───────────────────────────────────────────
  if (candles.length >= 2) {
    // Find yesterday's candle (last candle before today)
    const today = candles[candles.length - 1].date
    let pdCandle = null
    for (let i = candles.length - 2; i >= 0; i--) {
      if (candles[i].date !== today) { pdCandle = candles[i]; break }
    }
    if (pdCandle) {
      const pdLevels = [
        { price: pdCandle.high, label: 'Prev Day High', color: '#26a69a' },
        { price: pdCandle.low,  label: 'Prev Day Low',  color: '#ef5350' },
      ]
      pdLevels.forEach(({ price, label, color }) => {
        const ly = yP(price)
        g.append('line')
          .attr('x1', 0).attr('x2', iW)
          .attr('y1', ly).attr('y2', ly)
          .attr('stroke', color).attr('stroke-width', 1)
          .attr('stroke-dasharray', '5,4').attr('opacity', 0.5)
        g.append('text')
          .attr('x', 4).attr('y', ly - 3)
          .attr('fill', color).attr('font-size', '9px').attr('font-family', 'monospace')
          .attr('opacity', 0.8)
          .text(`${label} ${fmtPrice(price)}`)
      })
    }
  }

  // ── Live price line (Kite) ──────────────────────────────────────────────────
  const livePrice = indmoneyLivePrice.value || candles[candles.length - 1]?.close
  if (livePrice) {
    const ly = yP(livePrice)
    const lastCandle = candles[candles.length - 1]
    const isUp = livePrice >= (lastCandle?.open || livePrice)
    const liveColor = isUp ? '#26a69a' : '#ef5350'

    // dashed horizontal line across entire chart
    g.append('line')
      .attr('x1', 0).attr('x2', iW)
      .attr('y1', ly).attr('y2', ly)
      .attr('stroke', liveColor).attr('stroke-width', 1)
      .attr('stroke-dasharray', '4,3')
      .attr('opacity', 0.85)

    // price badge on the right
    const badgeW = M.right - 2
    g.append('rect')
      .attr('x', iW).attr('y', ly - 9)
      .attr('width', badgeW).attr('height', 18).attr('rx', 2)
      .attr('fill', liveColor)
    g.append('text')
      .attr('x', iW + badgeW / 2).attr('y', ly)
      .attr('text-anchor', 'middle').attr('dominant-baseline', 'middle')
      .attr('fill', '#fff').attr('font-size', '10px').attr('font-weight', '700')
      .text(livePrice.toFixed(2))
  }

  // ── Pattern markers ──────────────────────────────────────────────────────
  const patterns = detectCandlePatterns(candles)
  const patternByIndex = {}
  patterns.forEach(p => { patternByIndex[p.i] = p })
  const BULL_C = '#26a69a', BEAR_C = '#ef5350', NEUT_C = '#fbbf24'
  const MARKER_OFF = 10  // px above/below candle extremes
  patterns.forEach(pat => {
    const d   = candles[pat.i]
    if (!d) return
    const cx  = xB(d.date) + xB.bandwidth() / 2
    const isBull = pat.signal === 'bull', isNeut = pat.signal === 'neutral'
    const col = isBull ? BULL_C : isNeut ? NEUT_C : BEAR_C
    const my  = isBull ? yP(d.low)  - MARKER_OFF : yP(d.high) + MARKER_OFF
    const tri = isBull ? `${cx},${my} ${cx-7},${my+11} ${cx+7},${my+11}`
                       : isNeut ? `${cx-7},${my-5} ${cx+7},${my-5} ${cx},${my+6}`
                       : `${cx},${my} ${cx-7},${my-11} ${cx+7},${my-11}`
    // dotted line from candle to marker
    g.append('line')
      .attr('x1', cx).attr('x2', cx)
      .attr('y1', isBull ? yP(d.low) : yP(d.high))
      .attr('y2', my + (isBull ? 4 : -4))
      .attr('stroke', col).attr('stroke-width', 1).attr('stroke-dasharray', '2,2').attr('opacity', 0.6)
    // triangle marker
    g.append('polygon').attr('points', tri)
      .attr('fill', col).attr('opacity', 0.85)
      .style('pointer-events', 'none')
  })

  // crosshair + tooltip
  const crossV = g.append('line').attr('y1', 0).attr('y2', cH)
    .attr('stroke', '#333').attr('stroke-width', 1).attr('stroke-dasharray', '3,3').attr('visibility', 'hidden')

  const priceTag = g.append('rect').attr('x', iW).attr('width', M.right - 2).attr('height', 18).attr('rx', 2)
    .attr('fill', '#1a1a1a').attr('visibility', 'hidden')
  const priceTagText = g.append('text').attr('x', iW + 4).attr('font-size', '11px').attr('dominant-baseline', 'middle')
    .attr('fill', '#ccc').attr('visibility', 'hidden')

  g.append('rect').attr('width', iW).attr('height', cH)
    .attr('fill', 'none').attr('pointer-events', 'all')
    .on('mousemove', function(event) {
      const [mx] = d3.pointer(event)
      const idx  = Math.max(0, Math.min(candles.length - 1, Math.floor(mx / xB.step())))
      const d    = candles[idx]
      const cx   = xB(d.date) + xB.bandwidth() / 2
      const cy   = yP(d.close)

      crossV.attr('x1', cx).attr('x2', cx).attr('visibility', 'visible')
      priceTag.attr('y', cy - 9).attr('visibility', 'visible')
      priceTagText.attr('x', iW + 4).attr('y', cy).text(currencySymbol.value + d.close.toFixed(2)).attr('visibility', 'visible')

      const rect = chartWrap.value.getBoundingClientRect()
      tooltip.value = {
        visible: true,
        x: event.clientX - rect.left + 14,
        y: Math.max(8, event.clientY - rect.top - 70),
        date: d.date, up: d.close >= d.open,
        open:  `${currencySymbol.value}${d.open.toFixed(2)}`,  high: `${currencySymbol.value}${d.high.toFixed(2)}`,
        low:   `${currencySymbol.value}${d.low.toFixed(2)}`,   close: `${currencySymbol.value}${d.close.toFixed(2)}`,
        volume: `${(d.volume/1e6).toFixed(2)}M`,
        pattern: patternByIndex[idx] || null,
      }
    })
    .on('mouseleave', () => {
      crossV.attr('visibility', 'hidden')
      priceTag.attr('visibility', 'hidden')
      priceTagText.attr('visibility', 'hidden')
      tooltip.value.visible = false
    })
}

// ── Simulation launch ─────────────────────────────────────────────────────────

// ── Resize observer ───────────────────────────────────────────────────────────
let ro = null
onMounted(async () => {
  loadIndices()   // background — sidebar populates when ready
  _openMonitorStream()      // live position exit events
  _openLiveFeed()           // tick-driven SSE: tickets, decisions, commentary
  _openFoScannerStream()    // scanner SSE + replay latest signal/ticket on load
  _loadTracked()            // restore manually-tracked positions from server
  _loadMissedAlerts()       // restore alerts that fired while tab was closed
  _openTrackedAlertsStream()// server-side SL/T1/T2/time-exit alert pipeline
  // IST clock for the LIVE panel header
  const tickClock = () => {
    const f = new Intl.DateTimeFormat('en-IN', {
      timeZone: 'Asia/Kolkata', hour: '2-digit', minute: '2-digit',
      second: '2-digit', hour12: false,
    })
    liveClock.value = f.format(new Date()) + ' IST'
  }
  tickClock(); _liveClockTimer = setInterval(tickClock, 1000)

  // Play audio alert whenever a tracked card's status crosses into a critical
  // bucket (NEAR SL / SL HIT / NEAR T1 / T1 / T2 / FORCE EXIT). The alert
  // fires once per transition, not every tick.
  watch(trackedCards, (cards) => _maybeAlert(cards), { deep: true })
  await checkIndmoneyStatus()

  watch(
    [indmoneyConnected, chartTicker],
    ([connected, ticker]) => {
      if (connected && ticker) startIndmoneyStream(ticker)
    },
    { immediate: true },
  )

  // Mirror per-underlying tick price → indmoneyLivePrice for the charted
  // ticker. Replaces the old dedicated startIndmoneyStream EventSource —
  // we now share the single _openTicketStream connection.
  watch(
    () => liveSpots.value[chartTicker.value],
    (price) => {
      if (!price) return
      indmoneyLivePrice.value = price
      _applyLivePrice(price)
    },
  )

  // Subscribe to live tick streams for ANY underlying we care about:
  //   - AI-generated live tickets (liveTickets) and
  //   - Manually-tracked WATCHING positions (trackedPositions).
  // Without this, a tracked position whose underlying has no AI ticket would
  // never receive ticks → "now" premium would stay frozen at entry.
  watch(
    () => {
      // Open per-underlying tick streams only for things the UI actually
      // renders tick-by-tick:
      //   - tracked WATCHING positions (premium → SL/T1/T2 progress)
      //   - the currently charted ticker
      // Tick data for non-watched/non-charted underlyings flows via
      // /api/trade/live-feed already; opening a duplicate stream just
      // wastes backend resources.
      const set = new Set()
      for (const rec of trackedPositions.value || []) {
        const u   = rec?.ticket?.underlying
        const opt = rec?.ticket?.trading_symbol
        if (u)   set.add(u)
        // Also subscribe to the OPTION contract's own LTP so the card's
        // "now" price matches the broker tick (and the chart's last bar)
        // exactly — instead of the Black-Scholes synthetic derived from
        // the underlying spot, which lags actual market premium by ₹5-15.
        if (opt) set.add(opt)
      }
      // Same treatment for F&O-scanner liveTickets — without this the LIVE
      // ticket card on the right rail re-prices via Black-Scholes alone and
      // diverges from the chart's real-tick last bar (₹5-15 mismatch on
      // moving option premiums).
      for (const t of Object.values(liveTickets.value || {})) {
        if (!t) continue
        if (t.underlying)     set.add(t.underlying)
        if (t.trading_symbol) set.add(t.trading_symbol)
      }
      // Always include the charted ticker so the chart gets live ticks
      // through the same shared stream (no duplicate EventSources).
      if (chartTicker.value) set.add(chartTicker.value)
      return [...set]
    },
    (underlyings, prev) => {
      const prevSet = new Set(prev || [])
      const nextSet = new Set(underlyings || [])
      for (const sym of nextSet) if (!prevSet.has(sym)) _openTicketStream(sym)
      for (const sym of prevSet) if (!nextSet.has(sym)) _closeTicketStream(sym)
    },
    { immediate: true, deep: true },
  )

  // Option chain: fetch on mount + whenever user changes chainUnderlying.
  watch(
    chainUnderlying,
    (sym) => { if (sym) { _openTicketStream(sym); _fetchOptionChain(sym) } },
    { immediate: true },
  )
  _ticketAgeTimer = setInterval(() => {
    const now = Date.now()
    const ages = {}
    for (const [sym, t] of Object.entries(liveSpotTickAt.value)) {
      ages[sym] = t ? Math.round((now - t) / 1000) : 0
    }
    liveTickAges.value = ages
  }, 1000)

  await selectTicker('^NSEI')   // default chart: NIFTY 50

  ro = new ResizeObserver(() => { if (ohlcv.value.length) drawChart() })
  if (chartWrap.value) ro.observe(chartWrap.value)

  setTimeout(() => {
    runAiPredict()
    aiPredTimer = setInterval(() => {
      if (chartTicker.value) runAiPredict()
    }, 60000)
  }, 5000)
})

onUnmounted(() => {
  if (ro) ro.disconnect()
  if (refreshTimer) clearInterval(refreshTimer)
  if (aiPredTimer)  clearInterval(aiPredTimer)
  if (simPollTimer) clearInterval(simPollTimer)
  if (chartTimer)   clearInterval(chartTimer)
  _closeAllTicketStreams()
  if (_ticketAgeTimer)  clearInterval(_ticketAgeTimer)
  if (_liveClockTimer)  clearInterval(_liveClockTimer)
  if (_budgetTimer)     clearInterval(_budgetTimer)
  stopIndmoneyStream()
  if (investStream) { investStream.close(); investStream = null }
  if (_foScannerES) { _foScannerES.close(); _foScannerES = null }
  if (_monitorES)   { _monitorES.close();   _monitorES   = null }
  if (_liveES)      { _liveES.close();      _liveES      = null }
  if (_alertsES)    { _alertsES.close();    _alertsES    = null }
})

// On every module evaluation (including Vue SFC HMR), close any
// previously-opened singleton EventSources stashed on `window` so they
// don't keep accumulating one extra connection per save.
if (typeof window !== 'undefined') {
  for (const k of ['foScannerES', 'monitorES', 'liveES', 'alertsES', 'investStream']) {
    const key = `__phoenix_${k}`
    if (window[key]) { try { window[key].close?.() } catch {} ; window[key] = null }
  }
}
function _registerSingletonStream(name, es) {
  if (typeof window !== 'undefined') window[`__phoenix_${name}`] = es
  return es
}

watch(interval, () => {
  if (chartTicker.value) reloadChart()
})

// Redraw chart when switching back from analysis tab
watch(viewMode, async (val) => {
  if (val === 'chart' && ohlcv.value.length) {
    await nextTick()
    setTimeout(drawChart, 80)
  }
})
</script>

<style scoped>
/* ── CSS Theme Variables ──────────────────────────────────────────────────── */
.terminal {
  --bg0:     #0d1117;
  --bg1:     #161b22;
  --bg2:     #13181f;
  --bg3:     #21262d;
  --bd1:     #30363d;
  --bd2:     #3a404a;
  --tx0:     #e6edf3;
  --tx1:     #cdd9e5;
  --tx2:     #8b949e;
  --tx3:     #6e7681;
  --acc:     #2dd4bf;
  --bull:    #26a69a;
  --bear:    #ef5350;
  --chart-grid: #1e1e1e;
  --chart-axis: #555;
}
.terminal.light {
  --bg0:     #f6f8fa;
  --bg1:     #ffffff;
  --bg2:     #eaeef2;
  --bg3:     #f6f8fa;
  --bd1:     #d0d7de;
  --bd2:     #afc1cc;
  --tx0:     #1f2328;
  --tx1:     #424a53;
  --tx2:     #656d76;
  --tx3:     #9198a1;
  --acc:     #0d8a7e;
  --bull:    #116329;
  --bear:    #cf222e;
  --chart-grid: #e0e0e0;
  --chart-axis: #888;
}

/* ── Reset + theme ───────────────────────────────────────────────────────── */
* { box-sizing: border-box; }

.terminal {
  display: flex;
  flex-direction: column;
  height: 100vh;
  background: var(--bg0);
  color: var(--tx0);
  font-family: 'Inter', system-ui, sans-serif;
  font-size: 13px;
  overflow: hidden;
}

/* ── Top bar ─────────────────────────────────────────────────────────────── */
.topbar {
  height: 40px;
  background: var(--bg2);
  border-bottom: 1px solid var(--bd1);
  display: flex;
  align-items: center;
  padding: 0 12px;
  flex-shrink: 0;
  position: relative;
}
.topbar-left {
  display: flex; align-items: center; gap: 12px; flex: 1;
}
.topbar-center {
  display: flex; align-items: center; gap: 4px;
  position: absolute; left: 50%; transform: translateX(-50%);
}

.brand {
  font-family: 'JetBrains Mono', monospace;
  font-weight: 800;
  font-size: 14px;
  color: #2dd4bf;
  letter-spacing: 1px;
  white-space: nowrap;
}

.brand-sub {
  font-size: 9px;
  color: #6e7681;
  display: block;
  font-weight: 400;
  letter-spacing: 0.5px;
}

.ticker-row {
  display: flex;
  align-items: center;
  gap: 6px;
  flex: 1;
}

.search-wrap { position: relative; }
.search-suggestions {
  position: absolute; top: 32px; left: 0; z-index: 999;
  background: #161b22; border: 1px solid #30363d; border-radius: 6px;
  min-width: 280px; box-shadow: 0 8px 24px #0008; overflow: hidden;
}
.ss-item {
  display: flex; align-items: center; gap: 8px; padding: 7px 12px;
  cursor: pointer; border-bottom: 1px solid #21262d;
}
.ss-item:last-child { border-bottom: none; }
.ss-item:hover { background: #21262d; }
.ss-sym  { font-size: 12px; font-weight: 700; font-family: monospace; color: #e6edf3; min-width: 60px; }
.ss-name { font-size: 11px; color: #8b949e; flex: 1; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
.ss-ex   { font-size: 10px; color: #2dd4bf; font-family: monospace; }

.ticker-input {
  width: 160px;
  height: 28px;
  padding: 0 8px;
  background: #21262d;
  border: 1px solid #3a404a;
  border-radius: 4px;
  color: #eee;
  font-family: 'JetBrains Mono', monospace;
  font-size: 12px;
  text-transform: uppercase;
  outline: none;
}

.ticker-input:focus { border-color: #2dd4bf; }

.tf-btns { display: flex; gap: 2px; }

.tf-btn {
  height: 26px;
  padding: 0 10px;
  background: #21262d;
  border: 1px solid #3a404a;
  border-radius: 3px;
  color: #8b949e;
  font-size: 11px;
  cursor: pointer;
  font-family: 'JetBrains Mono', monospace;
}

.tf-btn:hover { color: #adbac7; border-color: #6e7681; }
.tf-btn.active { background: #1a3028; border-color: #2dd4bf; color: #2dd4bf; }

.go-btn {
  height: 28px;
  padding: 0 14px;
  background: #00d4a8;
  color: #000;
  border: none;
  border-radius: 4px;
  font-size: 12px;
  font-weight: 700;
  cursor: pointer;
}

.go-btn:hover { background: #00bfa0; }

.topbar-right { display: flex; align-items: center; gap: 10px; margin-left: auto; }

.live-dot {
  width: 7px; height: 7px;
  border-radius: 50%;
  background: #00d4a8;
  animation: pulse 1.5s infinite;
}

@keyframes pulse {
  0%, 100% { opacity: 1; }
  50% { opacity: 0.3; }
}

.live-label { font-size: 11px; color: #2dd4bf; font-family: monospace; }

/* INDmoney broker widget */
.ind-status  { display: flex; align-items: center; gap: 5px; background: #0d1e2a; border: 1px solid #3b82f633; border-radius: 6px; padding: 3px 10px; }
.ind-dot     { width: 7px; height: 7px; border-radius: 50%; background: #3b82f6; animation: pulse 1.2s infinite; flex-shrink: 0; }
.ind-label   { font-size: 10px; color: #93c5fd; font-family: monospace; font-weight: 700; }
.ind-price   { font-size: 12px; color: #fff; font-family: monospace; font-weight: 600; }
.ind-btn     { height: 28px; padding: 0 12px; background: #0d1e2a; border: 1px solid #3b82f6; border-radius: 6px; color: #3b82f6; font-size: 11px; cursor: pointer; font-family: monospace; display: flex; align-items: center; gap: 5px; transition: all .15s; }
.ind-btn:hover { background: #3b82f6; color: #fff; }
.ind-btn.configured { border-color: #22c55e; color: #22c55e; }
.ind-btn.configured:hover { background: #22c55e; color: #000; }
.ind-icon    { font-size: 13px; }

.sim-btn {
  height: 28px;
  padding: 0 14px;
  background: transparent;
  border: 1px solid #00d4a8;
  border-radius: 4px;
  color: #2dd4bf;
  font-size: 12px;
  cursor: pointer;
  font-weight: 500;
}

.sim-btn:hover { background: #00d4a820; }

/* ── Main area ───────────────────────────────────────────────────────────── */
.main-area {
  flex: 1;
  display: flex;
  overflow: hidden;
  min-height: 0;
}

/* ── Watchlist ───────────────────────────────────────────────────────────── */
.watchlist {
  width: 260px;
  flex-shrink: 0;
  display: flex;
  flex-direction: column;
  border-right: 1px solid #1e1e1e;
  background: #161b22;
  overflow: hidden;
}

.wl-header {
  padding: 8px 10px 6px;
  border-bottom: 1px solid #30363d;
  flex-shrink: 0;
}

.asset-tabs { display: flex; flex-wrap: wrap; gap: 3px; margin-bottom: 7px; }

.ac-btn {
  height: 26px; padding: 0 10px;
  background: #1c2128; border: 1px solid #30363d;
  border-radius: 4px; color: #8b949e; font-size: 11px;
  cursor: pointer; font-family: monospace; white-space: nowrap;
}
.ac-btn:hover { color: #bbb; border-color: #6e7681; }
.ac-btn.active { background: #1a3028; border-color: #2dd4bf; color: #2dd4bf; }

.scanner-controls { display: flex; align-items: center; gap: 8px; margin-top: 4px; }
.scan-mkt-sel { background: var(--bg2); color: var(--tx1); border: 1px solid var(--bd1); border-radius: 4px; padding: 3px 6px; font-size: 11px; cursor: pointer; }
.scan-btn {
  height: 26px; padding: 0 12px;
  background: #1a2820; border: 1px solid #00d4a8;
  border-radius: 4px; color: #2dd4bf; font-size: 12px;
  cursor: pointer; font-family: monospace;
  display: flex; align-items: center; gap: 4px;
}
.scan-btn:hover { background: #00d4a820; }
.scan-btn:disabled { opacity: 0.5; cursor: default; }
.scan-hint { font-size: 11px; color: #6e7681; font-family: monospace; }

.wl-action {
  font-size: 10px; font-family: monospace; font-weight: 700;
  padding: 2px 6px; border-radius: 3px; margin-bottom: 2px;
}
.act-sbuy  { color: #2dd4bf; background: #00d4a815; }
.act-buy   { color: #5dbb7d; background: #5dbb7d15; }
.act-hold  { color: #adbac7;    background: #88888815; }
.act-sell  { color: #ff9f43; background: #ff9f4315; }
.act-ssell { color: #f85149; background: #ff475715; }

.wl-score { font-size: 12px; font-family: monospace; font-weight: 700; }
.scan-score-tip { position: relative; cursor: help; }
.scan-score-tip::after {
  content: attr(data-tip);
  position: absolute;
  right: 0; bottom: calc(100% + 6px);
  background: #1e293b; color: #e2e8f0;
  font-size: 11px; font-family: monospace; font-weight: 400;
  white-space: pre; line-height: 1.6;
  padding: 6px 10px; border-radius: 6px;
  border: 1px solid #334155;
  pointer-events: none; opacity: 0;
  transition: opacity 0.15s;
  z-index: 999; min-width: 220px;
}
.scan-score-tip:hover::after { opacity: 1; }

.scan-section-label {
  padding: 4px 10px; font-size: 10px; font-weight: 700; letter-spacing: 1px;
  font-family: monospace; color: #6e7681; background: #0d1117;
}
.buy-label  { color: #26a69a; border-left: 2px solid #26a69a; }
.sell-label { color: #ef5350; border-left: 2px solid #ef5350; }
.scan-buy-row  { border-left: 2px solid #26a69a22; }
.scan-sell-row { border-left: 2px solid #ef535022; }

.country-filters { display: flex; flex-wrap: wrap; gap: 3px; }

.cf-btn {
  height: 22px;
  padding: 0 7px;
  background: #21262d;
  border: 1px solid #3a404a;
  border-radius: 3px;
  color: #8b949e;
  font-size: 11px;
  cursor: pointer;
  font-family: monospace;
}

.cf-btn:hover { color: #adbac7; border-color: #6e7681; }
.cf-btn.active { background: #1a3028; border-color: #2dd4bf; color: #2dd4bf; }

.wl-list {
  flex: 1;
  overflow-y: auto;
}

.wl-list::-webkit-scrollbar { width: 3px; }
.wl-list::-webkit-scrollbar-track { background: transparent; }
.wl-list::-webkit-scrollbar-thumb { background: #2a2a2a; border-radius: 2px; }

.wl-row {
  display: flex;
  justify-content: space-between;
  align-items: center;
  padding: 8px 10px;
  border-bottom: 1px solid #21262d;
  cursor: pointer;
  transition: background 0.1s;
}

.wl-row:hover { background: #1c2128; }
.wl-row.selected { background: #1a3028; border-left: 2px solid #2dd4bf; }

.wl-left { display: flex; align-items: center; gap: 6px; min-width: 0; }
.wl-flag { font-size: 14px; flex-shrink: 0; }
.wl-names { display: flex; flex-direction: column; min-width: 0; }
.wl-name { font-size: 13px; color: #e6edf3; font-weight: 600; white-space: nowrap; overflow: hidden; text-overflow: ellipsis; }
.wl-exch { font-size: 10px; color: #8b949e; font-family: monospace; }

.wl-right { display: flex; flex-direction: column; align-items: flex-end; gap: 2px; flex-shrink: 0; }
.wl-price { font-size: 13px; color: #cdd9e5; font-family: monospace; font-weight: 600; }
.wl-chg { font-size: 12px; font-family: monospace; font-weight: 700; }

.wl-loading { padding: 20px; text-align: center; color: #6e7681; font-size: 12px; }

/* ── Chart section ───────────────────────────────────────────────────────── */
.chart-section {
  flex: 1;
  min-width: 0;
  display: flex;
  flex-direction: column;
  background: #0d1117;
}

.chart-header {
  display: flex;
  align-items: center;
  gap: 8px;
  padding: 4px 10px;
  border-bottom: 1px solid #30363d;
  flex-shrink: 0;
  background: #161b22;
  height: 32px;
}

.ch-ticker  { font-size: 14px; font-weight: 700; color: #fff; font-family: monospace; }
.ch-company { font-size: 11px; color: #8b949e; }
.ch-price   { font-size: 14px; font-weight: 600; color: #cdd9e5; font-family: monospace; }
.ch-live    { display: flex; align-items: center; gap: 4px; font-size: 10px; color: #2dd4bf; font-family: monospace; margin-left: auto; }
.ch-live-dot { width: 5px; height: 5px; border-radius: 50%; background: #00d4a8; animation: pulse 1.5s infinite; }
.ch-loading { color: #7d8590; }
@keyframes pulse { 0%,100% { opacity: 1; } 50% { opacity: 0.3; } }
.ch-chg  { font-size: 12px; font-weight: 600; font-family: monospace; }
.ch-vol  { font-size: 10px; color: #6e7681; }
.ch-fomo { font-size: 10px; font-family: monospace; padding: 1px 6px; border-radius: 3px; border: 1px solid currentColor; }
.ch-mkt  { font-size: 10px; font-weight: 700; padding: 2px 7px; border-radius: 3px; letter-spacing: 0.5px; font-family: monospace; cursor: default; }
.ch-mkt-open   { background: rgba(0,200,100,0.12); color: #00c864; border: 1px solid rgba(0,200,100,0.3); }
.ch-mkt-closed { background: rgba(239,83,80,0.12); color: #ef5350;  border: 1px solid rgba(239,83,80,0.3);  }

.chart-zoom-controls {
  position: absolute; top: 8px; right: 8px; z-index: 5;
  display: flex; align-items: center; gap: 4px;
  background: rgba(13,17,23,0.85); border: 1px solid #30363d;
  border-radius: 4px; padding: 2px 4px; backdrop-filter: blur(4px);
  font-family: monospace; font-size: 11px;
}
.chart-zoom-controls button {
  background: transparent; border: 1px solid transparent; color: #adbac7;
  font-size: 13px; line-height: 1; padding: 2px 7px; cursor: pointer;
  border-radius: 3px; min-width: 22px;
}
.chart-zoom-controls button:hover:not(:disabled) { background: #21262d; border-color: #444c56; color: #e6edf3; }
.chart-zoom-controls button:disabled { opacity: .35; cursor: default; }
.chart-zoom-info { color: #6e7681; font-size: 10px; padding: 0 4px; }

.chart-wrap {
  flex: 1;
  min-height: 0;
  position: relative;
  overflow: hidden;
}

.chart-svg { width: 100%; height: 100%; display: block; }

.chart-empty, .chart-loading {
  position: absolute; inset: 0;
  display: flex; align-items: center; justify-content: center;
  color: #333; font-size: 13px; gap: 8px;
}

/* ── Feed panel ──────────────────────────────────────────────────────────── */
.feed-panel {
  height: 220px;
  flex-shrink: 0;
  border-top: 1px solid #30363d;
  display: flex;
  flex-direction: column;
  background: #13181f;
}
.feed-panel-header {
  display: flex; align-items: center; gap: 8px;
  padding: 0 14px; border-bottom: 1px solid #30363d;
  flex-shrink: 0; height: 36px;
}
.fp-title { font-size: 11px; font-weight: 700; letter-spacing: 1px; color: #8b949e; font-family: monospace; }
.fp-loading { font-size: 11px; color: #8b949e; display: flex; align-items: center; gap: 5px; margin-left: auto; }

/* Horizontal card carousel */
.feed-carousel {
  flex: 1;
  overflow-x: auto;
  overflow-y: hidden;
  display: flex;
  align-items: stretch;
  padding: 8px 10px;
  gap: 0;
  scrollbar-width: thin;
  scrollbar-color: #30363d transparent;
}
.feed-carousel::-webkit-scrollbar { height: 4px; }
.feed-carousel::-webkit-scrollbar-track { background: transparent; }
.feed-carousel::-webkit-scrollbar-thumb { background: #30363d; border-radius: 2px; }

.feed-cards-inner {
  display: flex;
  gap: 8px;
  align-items: stretch;
  min-width: max-content;
}

.feed-card {
  width: 240px;
  flex-shrink: 0;
  background: #161b22;
  border: 1px solid #30363d;
  border-radius: 6px;
  padding: 10px 12px;
  display: flex;
  flex-direction: column;
  gap: 6px;
  cursor: default;
  transition: border-color 0.15s;
}
.feed-card:hover { border-color: #58a6ff44; }
.feed-card.news       { border-left: 3px solid #58a6ff; }
.feed-card.twitter    { border-left: 3px solid #2dd4bf; }
.feed-card.reddit     { border-left: 3px solid #f0883e; }
.feed-card.fomo       { border-left: 3px solid #fbbf24; }
.feed-card.prediction { border-left: 3px solid #c084fc; }
.feed-card.simulation { border-left: 3px solid #94a3b8; }

.fc-top { display: flex; align-items: center; justify-content: space-between; }
.fc-badge {
  font-size: 10px; font-weight: 700; font-family: monospace;
  padding: 2px 7px; border-radius: 3px;
  background: #21262d; color: #8b949e;
}
.fc-badge.news        { color: #58a6ff; background: #0d1f3a; }
.fc-badge.twitter     { color: #2dd4bf; background: #0d2a1e; }
.fc-badge.reddit      { color: #f0883e; background: #1f1a0d; }
.fc-badge.fomo        { color: #fbbf24; background: #1f1a0d; }
.fc-badge.prediction  { color: #c084fc; background: #1a0d2a; }
.fc-badge.simulation  { color: #94a3b8; background: #141820; }
.fc-sent { font-size: 11px; font-weight: 700; font-family: monospace; }
.fc-title { font-size: 13px; color: #cdd9e5; line-height: 1.4;
            display: -webkit-box; -webkit-line-clamp: 2; -webkit-box-orient: vertical; overflow: hidden; }
.fc-meta  { font-size: 11px; color: #8b949e; font-family: monospace; line-height: 1.3;
            white-space: nowrap; overflow: hidden; text-overflow: ellipsis; }

.feed-empty { font-size: 13px; color: #8b949e; padding: 20px; display: flex; align-items: center; flex-direction: column; gap: 4px; }
.feed-empty .empty-sub { font-size: 11px; color: #6e7681; }

/* ── Watchlist tab (★) ───────────────────────────────────────────────────── */
.wl-watch-add {
  display: flex; gap: 6px; padding: 8px; border-bottom: 1px solid #21262d;
  flex-wrap: wrap;
}
.wl-watch-input {
  flex: 1; min-width: 140px;
  background: #0d1117; border: 1px solid #30363d; color: #e6edf3;
  padding: 5px 8px; border-radius: 4px; font-size: 12px;
}
.wl-watch-input:focus { border-color: #58a6ff; outline: none; }
.wl-watch-btn {
  background: #21262d; color: #c9d1d9; border: 1px solid #30363d;
  padding: 5px 10px; border-radius: 4px; font-size: 12px; cursor: pointer;
  white-space: nowrap;
}
.wl-watch-btn:hover { background: #30363d; }
.wl-watch-btn:disabled { opacity: 0.4; cursor: not-allowed; }
.wl-watch-pin { background: #1f2937; border-color: #374151; color: #93c5fd; }
.wl-watch-rm { background: transparent; color: #6e7681; border: 0; cursor: pointer;
  padding: 2px 8px; font-size: 13px; border-radius: 3px; }
.wl-watch-rm:hover { color: #f85149; background: #2a1416; }

/* ── Budget tab ($) ──────────────────────────────────────────────────────── */
.wl-budget { padding: 8px; }
.bud-loading { padding: 20px; color: #8b949e; display: flex; gap: 8px; align-items: center; }

/* Period tiles row — Today / Month / Session / All-Time. Click to filter. */
.bud-periods {
  display: grid; grid-template-columns: 1fr 1fr; gap: 6px; margin-bottom: 12px;
}
.bud-period-tile {
  background: #0d1117; border: 1px solid #21262d; border-radius: 5px;
  padding: 8px 10px; cursor: pointer; transition: all 0.15s ease;
}
.bud-period-tile:hover { border-color: #30363d; background: #11161d; }
.bud-period-tile.active {
  border-color: #2dd4bf; background: #0a1f1c;
  box-shadow: 0 0 0 1px #2dd4bf33;
}
.bud-period-tile.active .bud-cost { color: #5eead4; }

.bud-totals {
  display: grid; grid-template-columns: 1fr 1fr; gap: 6px; margin-bottom: 12px;
}
.bud-tile {
  background: #0d1117; border: 1px solid #21262d; border-radius: 5px;
  padding: 8px 10px;
}
.bud-label { font-size: 10px; color: #6e7681; text-transform: uppercase; letter-spacing: 0.5px; }
.bud-val   { font-size: 16px; color: #e6edf3; font-weight: 600; margin-top: 2px; font-family: monospace; }
.bud-val.bud-cost { color: #2dd4bf; }
.bud-sub   { font-size: 10px; color: #8b949e; font-family: monospace; }
.bud-section-label {
  font-size: 10px; color: #6e7681; letter-spacing: 0.8px;
  padding: 10px 4px 4px; border-bottom: 1px dashed #21262d; margin-bottom: 4px;
}
.bud-row {
  display: flex; justify-content: space-between; align-items: center;
  padding: 5px 6px; border-bottom: 1px solid #161b22; font-size: 12px;
}
.bud-row-name { color: #c9d1d9; flex: 1; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
.bud-row-meta { display: flex; gap: 8px; color: #8b949e; font-family: monospace; font-size: 11px; }
.bud-row-meta .bud-cost { color: #2dd4bf; min-width: 60px; text-align: right; }
.bud-recent {
  display: flex; justify-content: space-between; padding: 3px 6px;
  font-size: 11px; color: #8b949e; border-bottom: 1px solid #161b22;
}
.bud-recent-agent  { color: #c9d1d9; flex: 1; overflow: hidden; text-overflow: ellipsis; }
.bud-recent-tokens { color: #8b949e; min-width: 60px; text-align: right; }
.bud-recent-cost   { color: #2dd4bf; min-width: 70px; text-align: right; }
.bud-actions { display: flex; gap: 6px; padding: 12px 4px 4px; border-top: 1px dashed #21262d; margin-top: 8px; }
.wl-watch-rm-btn { color: #f85149; }
.wl-watch-rm-btn:hover { background: #2a1416; }

/* ── Right sidebar ───────────────────────────────────────────────────────── */
.right-sidebar {
  /* Adaptive width: min 340 / preferred 24vw / max 460. Wider monitors get more
     breathing room; the option chain table never feels cramped. */
  width: clamp(340px, 24vw, 460px);
  flex-shrink: 0;
  border-left: 1px solid #30363d;
  display: flex;
  flex-direction: column;
  overflow-y: auto;
  overflow-x: hidden;
  background: #161b22;
}
.rs-panel {
  border-bottom: 2px solid #21262d;
  flex-shrink: 0;
}
.rs-header {
  display: flex; align-items: center;
  padding: 6px 10px 6px 12px; background: #13181f; border-bottom: 1px solid #30363d;
  min-height: 34px; border-left: 3px solid transparent;
}
.rs-title { font-size: 10px; font-weight: 700; flex: 1; letter-spacing: 0.8px; }

/* Unique color per panel */
.hdr-signal   { border-left-color: #2dd4bf; background: #0d1e1e; }
.hdr-signal   .rs-title { color: #2dd4bf; }
.hdr-levels   { border-left-color: #fbbf24; background: #1a1500; }
.hdr-levels   .rs-title { color: #fbbf24; }
.hdr-wallet   { border-left-color: #4ade80; background: #0a1a0a; }
.hdr-wallet   .rs-title { color: #4ade80; }
.hdr-backtest { border-left-color: #a78bfa; background: #110e1a; }
.hdr-backtest .rs-title { color: #a78bfa; }
.rs-icon-btn {
  background: none; border: none; color: #6e7681;
  width: 26px; height: 26px; display: flex; align-items: center; justify-content: center;
  font-size: 14px; cursor: pointer; border-radius: 4px; flex-shrink: 0;
}
.rs-icon-btn:hover:not(:disabled) { background: #21262d; color: #e6edf3; }
.rs-icon-btn:disabled { opacity: 0.35; cursor: not-allowed; }
.rs-btn   { background: #21262d; border: 1px solid #3a404a; color: #cdd9e5;
            border-radius: 4px; padding: 4px 10px; font-size: 11px; cursor: pointer; white-space: nowrap; }
.rs-btn:hover:not(:disabled) { border-color: #2dd4bf; color: #2dd4bf; }
.rs-btn:disabled { opacity: 0.4; cursor: not-allowed; }
.rs-btn-ghost { background: none; border: 1px solid #30363d; color: #8b949e;
                border-radius: 4px; padding: 3px 8px; font-size: 11px; cursor: pointer; white-space: nowrap; }
.rs-btn-ghost:hover { border-color: #8b949e; color: #e6edf3; }
.rs-wallet-summary { padding: 10px 14px 8px; border-bottom: 1px solid #30363d; }
.rws-val { font-size: 22px; font-weight: 700; font-family: monospace; color: #e6edf3; }
.rws-pnl { font-size: 13px; font-family: monospace; font-weight: 600; margin-top: 2px; }
.rs-empty, .rs-loading { padding: 10px 12px; font-size: 12px; color: #8b949e; font-family: monospace; }
.rs-err { padding: 10px 12px; font-size: 11px; color: #ef5350; font-family: monospace; }
.rs-action {
  margin: 6px 8px 4px; padding: 5px 10px; border-radius: 3px;
  font-size: 16px; font-weight: 700; font-family: monospace;
  display: flex; align-items: center; gap: 8px;
}
.rs-buy  { background: #0d2a1e; color: #26a69a; border: 1px solid #26a69a44; }
.rs-sell { background: #2d1010; color: #ef5350; border: 1px solid #ef535044; }
.rs-hold { background: #21262d; color: #8b949e;    border: 1px solid #3a404a; }
.rs-score { font-size: 12px; font-weight: 400; opacity: 0.8; }
.rs-inds {
  display: flex; flex-wrap: wrap; gap: 8px;
  padding: 6px 10px 6px; font-size: 12px; color: #8b949e; font-family: monospace;
}
.rs-inds span { color: #8b949e; }
.rs-inds b    { margin-left: 3px; }
.rs-levels {
  display: flex; gap: 5px; padding: 4px 10px 10px; flex-wrap: wrap;
}
.rsl { font-size: 11px; font-family: monospace; padding: 3px 7px; border-radius: 3px; }
.rsl.e { background: #21262d; color: #adbac7; }
.rsl.s { background: #2d1010; color: #ef5350; }
.rsl.t { background: #0d2a1e; color: #26a69a; }
.rs-bias { padding: 6px 12px; font-size: 13px; font-family: monospace; font-weight: 700; }
.rs-bias.bullish { color: #2dd4bf; } .rs-bias.bearish { color: #f85149; } .rs-bias.neutral { color: #cdd9e5; }
.rs-lv-rows { padding: 4px 8px; }
.rs-lv {
  display: flex; justify-content: space-between; align-items: center;
  padding: 4px 8px; margin: 2px 0; border-radius: 3px; font-size: 12px; font-family: monospace;
}
.rs-lv.t3,.rs-lv.t2,.rs-lv.t1 { color: #26a69a; background: #0d2a1e11; }
.rs-lv.en { color: #cdd9e5; background: #21262d; font-weight: 700; }
.rs-lv.sl { color: #ef5350; background: #2d101011; }
.rs-lv-foot { padding: 4px 12px 10px; font-size: 11px; color: #8b949e; font-family: monospace; }
.rs-trade-row {
  display: flex; gap: 4px; padding: 6px 8px; border-bottom: 1px solid #30363d;
}
.rs-qty  { width: 50px; background: #1c2128; border: 1px solid #3a404a; color: #cdd9e5;
           border-radius: 3px; padding: 3px 5px; font-size: 11px; font-family: monospace; text-align: center; }
.rs-buy, .rs-sell, .rs-ai {
  flex: 1; padding: 4px 0; border-radius: 3px; font-size: 10px;
  font-weight: 700; letter-spacing: 1px; cursor: pointer; border: none;
}
.rs-buy  { background: #0e3d2e; color: #26a69a; }
.rs-sell { background: #3d1212; color: #ef5350; }
.rs-ai   { background: #1f2010; color: #fbbf24; border: 1px solid #fbbf2444; }
.rs-buy:hover:not(:disabled)  { background: #26a69a; color: #000; }
.rs-sell:hover:not(:disabled) { background: #ef5350; color: #000; }
.rs-ai:hover:not(:disabled)   { background: #fbbf24; color: #000; }
.rs-buy:disabled,.rs-sell:disabled,.rs-ai:disabled { opacity: 0.35; cursor: not-allowed; }
.rs-result {
  margin: 6px 8px; padding: 8px 10px; border-radius: 4px;
  font-size: 12px; font-family: monospace; line-height: 1.5;
}
.rs-res-buy  { background: #0d2a1e; border: 1px solid #26a69a55; color: #26a69a; }
.rs-res-sell { background: #2d1010; border: 1px solid #ef535055; color: #ef5350; }
.rs-res-hold { background: #21262d; border: 1px solid #3a404a;   color: #8b949e; }
.rs-res-top  { display: flex; align-items: baseline; gap: 6px; margin-bottom: 3px; }
.rs-res-action { font-size: 14px; font-weight: 800; letter-spacing: 1px; }
.rs-res-conf   { font-size: 11px; opacity: 0.75; }
.rs-res-reason { font-size: 11px; opacity: 0.9; margin-bottom: 4px; white-space: normal; word-break: break-word; }
.rs-res-levels { display: flex; gap: 8px; font-size: 11px; margin-bottom: 4px; }
.rrl-sl { color: #ef5350; }
.rrl-t1 { color: #26a69a; }
.rs-res-signals { display: flex; flex-wrap: wrap; gap: 3px; }
.rs-sig-tag { background: #21262d; border: 1px solid #3a404a; border-radius: 3px;
              padding: 1px 5px; font-size: 10px; color: #8b949e; }
.rs-trade-status { margin-top: 6px; padding: 5px 8px; border-radius: 5px; font-size: 11px; }
.rs-trade-done { background: rgba(38,166,154,0.15); border: 1px solid rgba(38,166,154,0.4); color: #26a69a; }
.rs-trade-skip { background: rgba(139,148,158,0.1); border: 1px solid rgba(139,148,158,0.25); color: #8b949e; }
.rs-stats {
  display: flex; padding: 6px 8px; gap: 2px; border-bottom: 1px solid #30363d;
}
.rss-i { flex: 1; text-align: center; }
.rss-v { font-size: 13px; font-weight: 700; font-family: monospace; color: #e6edf3; }
.rss-l { font-size: 10px; color: #8b949e; margin-top: 2px; }
.rs-positions { padding: 4px 8px; }
.rs-pos-hdr { font-size: 10px; letter-spacing: 1px; color: #8b949e; font-family: monospace; font-weight: 700; padding: 4px 0 4px; }
.bt-results { padding: 6px; display: flex; flex-direction: column; gap: 5px; }
.bt-bh-row  { font-size: 11px; color: #8b949e; padding: 2px 4px 6px; border-bottom: 1px solid #30363d; margin-bottom: 2px; }
.bt-card { background: #21262d; border: 1px solid #30363d; border-radius: 6px; padding: 7px 9px; }
.bt-winner { border-color: #2dd4bf55; background: #0d2a22; }
.bt-err { opacity: 0.5; }
.bt-card-top { display: flex; align-items: center; gap: 6px; margin-bottom: 3px; }
.bt-grade { font-size: 14px; font-weight: 800; font-family: monospace; width: 20px; }
.grade-a { color: #26a69a; }
.grade-b { color: #86efac; }
.grade-c { color: #fbbf24; }
.grade-d { color: #ef5350; }
.bt-card-name { font-size: 11px; font-weight: 600; color: #cdd9e5; flex: 1; }
.bt-ret { font-size: 12px; font-weight: 700; font-family: monospace; }
.bt-card-line { font-size: 10px; color: #8b949e; margin-bottom: 5px; line-height: 1.4; }
.bt-bar-wrap { position: relative; height: 4px; background: #30363d; border-radius: 2px; margin-bottom: 4px; }
.bt-bar { height: 4px; border-radius: 2px; min-width: 2px; }
.bt-bar-up { background: #26a69a; }
.bt-bar-dn { background: #ef5350; }
.bt-bar-bh { position: absolute; top: -2px; width: 2px; height: 8px; background: #fbbf24; border-radius: 1px; }
.bt-risk { font-size: 10px; color: #6e7681; font-family: monospace; }
.rs-pos-row { display: flex; align-items: center; gap: 8px; padding: 3px 0; font-size: 12px; font-family: monospace; }
.rsp-ticker { color: #cdd9e5; font-weight: 700; }
.rsp-qty    { color: #8b949e; }
.rsp-pnl    { margin-left: auto; font-weight: 700; }

/* ── Right sidebar header actions row ───────────────────────────────────── */
.rs-hdr-actions { display: flex; align-items: center; gap: 2px; margin-left: auto; }

/* ── Signal action badge (renamed to avoid conflict with button classes) ─── */
.rs-action { margin: 6px 8px 4px; padding: 5px 10px; border-radius: 3px; font-size: 15px; font-weight: 700; font-family: monospace; display: flex; align-items: center; gap: 8px; }
.rs-act-buy  { background: #0d2a1e; color: #26a69a; border: 1px solid #26a69a44; }
.rs-act-sell { background: #2d1010; color: #ef5350; border: 1px solid #ef535044; }
.rs-act-hold { background: #21262d; color: #8b949e; border: 1px solid #3a404a; }

/* ── Wallet trade buttons ────────────────────────────────────────────────── */
.rs-buy-btn, .rs-sell-btn, .rs-ai-btn {
  flex: 1; padding: 4px 0; border-radius: 3px; font-size: 10px;
  font-weight: 700; letter-spacing: 1px; cursor: pointer; border: none;
}
.rs-buy-btn  { background: #0e3d2e; color: #26a69a; }
.rs-sell-btn { background: #3d1212; color: #ef5350; }
.rs-ai-btn   { background: #1f2010; color: #fbbf24; border: 1px solid #fbbf2444; }
.rs-buy-btn:hover:not(:disabled)  { background: #26a69a; color: #000; }
.rs-sell-btn:hover:not(:disabled) { background: #ef5350; color: #000; }
.rs-ai-btn:hover:not(:disabled)   { background: #fbbf24; color: #000; }
.rs-buy-btn:disabled,.rs-sell-btn:disabled,.rs-ai-btn:disabled { opacity: 0.35; cursor: not-allowed; }

/* ── Wallet summary ─────────────────────────────────────────────────────── */
.rws-main  { display: flex; align-items: baseline; gap: 8px; }
.rws-stats { display: flex; gap: 8px; font-size: 10px; color: #8b949e; font-family: monospace; margin-top: 3px; flex-wrap: wrap; }

/* ── F&O Scanner panel ───────────────────────────────────────────────────── */
.hdr-scanner { border-left-color: #f97316; background: #1a0e00; }
.hdr-scanner .rs-title { color: #f97316; }
.fo-status-dot { width: 7px; height: 7px; border-radius: 50%; margin-right: 2px; flex-shrink: 0; }
.fo-dot-on  { background: #26a69a; box-shadow: 0 0 5px #26a69a; animation: pulse-dot 1.5s ease-in-out infinite; }
.fo-dot-off { background: #3a404a; }

/* Cleaner scanner controls — 2 labelled buttons replace 4 cryptic icons */
.fo-scan-now {
  background: linear-gradient(135deg,#fbbf24,#f97316); color:#0d1117;
  border: 0; padding: 4px 10px; border-radius: 4px; font-size: 10px;
  font-weight: 800; cursor: pointer; letter-spacing: .3px;
  font-family: monospace; min-width: 78px;
  transition: transform .1s, box-shadow .1s;
}
.fo-scan-now:hover:not(:disabled) { transform: translateY(-1px); box-shadow: 0 3px 8px #f9731666; }
.fo-scan-now:disabled { opacity: .55; cursor: not-allowed; }
/* Passive 'LIVE · 3min' indicator — replaces the manual Auto: ON/OFF toggle.
   Scanner is always-on now; this just shows current state. */
.fo-auto-indicator {
  display: inline-flex; align-items: center; gap: 4px;
  padding: 4px 8px; border-radius: 4px; font-size: 10px; font-weight: 700;
  font-family: monospace; letter-spacing: .3px;
  background: #161b22; color: #6e7681; border: 1px solid #30363d;
  cursor: default; user-select: none;
}
.fo-auto-indicator.fo-auto-live {
  background: #0d2a1e; color: #26a69a; border-color: #26a69a55;
}
/* Market closed — scanner thread alive but cycles paused. Amber, no pulse. */
.fo-auto-indicator.fo-auto-paused {
  background: #1f1407; color: #f59e0b; border-color: #f59e0b55;
}
.fo-auto-dot {
  width: 6px; height: 6px; border-radius: 50%;
  background: #586069; flex-shrink: 0;
}
.fo-auto-indicator.fo-auto-live .fo-auto-dot {
  background: #26a69a; box-shadow: 0 0 6px #26a69a99;
  animation: pulse-dot 1.5s ease-in-out infinite;
}
.fo-auto-indicator.fo-auto-paused .fo-auto-dot {
  background: #f59e0b; box-shadow: 0 0 4px #f59e0b66;
  /* No pulse animation — static amber dot signals "asleep, not dead". */
}
@keyframes pulse-dot { 0%,100% { opacity:1; } 50% { opacity:0.4; } }

/* Analysing ticker bar */
.fo-analysing { display: flex; align-items: center; gap: 6px; padding: 4px 8px; background: #0d1a2a; border-bottom: 1px solid #1d3a5a; font-size: 11px; font-family: monospace; }
.fo-spin { color: #3d9eff; animation: spin 1s linear infinite; display: inline-block; }
@keyframes spin { to { transform: rotate(360deg); } }
.fo-analysing-sym { color: #e6edf3; font-weight: 800; letter-spacing: 1px; }
.fo-analysing-prog { color: #6e7681; font-size: 10px; margin-left: auto; }

.fo-feed { max-height: 240px; overflow-y: auto; padding: 4px 6px; display: flex; flex-direction: column; gap: 3px; }
.fo-evt  { display: flex; align-items: center; gap: 4px; font-size: 11px; font-family: monospace; padding: 4px 5px; border-radius: 4px; flex-wrap: wrap; transition: background .12s, transform .12s; }
.fo-evt-clickable { cursor: pointer; }
.fo-evt-clickable:hover {
  background: #1c2230 !important;
  outline: 1px solid #3d9eff66;
  transform: translateX(2px);
}
.fo-evt-clickable:active { transform: translateX(1px) scale(0.99); }
.fo-evt-scan_signal { border-left: 2px solid #3d9eff44; }
.fo-evt-scan_trade        { background: #0d2a1e; border: 1px solid #26a69a44; }
.fo-evt-scan_signal       { background: #13181f; }
.fo-evt-scan_skip         { background: #0d0f13; opacity: 0.8; }
.fo-evt-position_exit     { background: #1a1500; border: 1px solid #fbbf2433; }

/* Verdict-coloured signal rows */
.fo-evt-verdict-strong_buy  { background: #0a2216; border-left: 2px solid #00e676; }
.fo-evt-verdict-buy         { background: #0d1f14; border-left: 2px solid #26a69a; }
.fo-evt-verdict-strong_sell { background: #2a0a0a; border-left: 2px solid #ff1744; }
.fo-evt-verdict-sell        { background: #1f0d0d; border-left: 2px solid #ef5350; }

.fo-evt-badge { font-size: 9px; font-weight: 800; padding: 1px 5px; border-radius: 2px; letter-spacing: 0.5px; flex-shrink: 0; white-space: nowrap; }
.fo-badge-scan_trade     { background: #26a69a; color: #000; }
.fo-badge-scan_skip      { background: #2a2f3a; color: #6e7681; }
.fo-badge-win            { background: #00e676; color: #000; }
.fo-badge-loss           { background: #ef5350; color: #fff; }

/* Verdict badges — colour-coded */
.fo-badge-verdict-strongbuy  { background: #00e676; color: #000; }
.fo-badge-verdict-buy        { background: #26a69a; color: #000; }
.fo-badge-verdict-hold       { background: #3a404a; color: #8b949e; }
.fo-badge-verdict-sell       { background: #ef5350; color: #fff; }
.fo-badge-verdict-strongsell { background: #ff1744; color: #fff; }

.fo-sym           { color: #cdd9e5; font-weight: 700; }
.fo-sym-option    { color: #79c0ff; font-weight: 800; letter-spacing: 0.3px; }
.fo-underlying    { color: #6e7681; font-size: 9px; }
.fo-option-ltp    { color: #00e676; font-weight: 700; font-size: 11px; background: #0d2a1e; border-radius: 2px; padding: 0 3px; }
.fo-strike        { color: #f0a500; font-size: 10px; font-weight: 700; }
.fo-greeks        { color: #6e7681; font-size: 9px; width: 100%; margin-top: 1px; }
.fo-levels        { color: #8b949e; font-size: 9px; width: 100%; }
.fo-entry      { color: #f0a500; font-size: 10px; }
.fo-conf       { font-size: 10px; font-weight: 700; }
.conf-hi       { color: #00e676; }
.conf-mid      { color: #f0a500; }
.conf-lo       { color: #6e7681; }
.fo-action     { color: #6e7681; font-size: 9px; margin-left: auto; white-space: nowrap; }
.fo-pnl        { font-weight: 700; font-size: 11px; margin-left: auto; }
.fo-skip-reason { color: #6e7681; font-size: 10px; flex: 1; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
.fo-trades-count { color: #26a69a; font-weight: 700; }
.fo-scan-status { font-size: 10px; color: #6e7681; padding: 3px 8px; }

/* ── Instrument type tags ────────────────────────────────────────────────── */
.fo-inst-tag, .rsp-itype {
  font-size: 9px; font-weight: 800; padding: 1px 5px; border-radius: 2px; letter-spacing: 0.5px;
}
.itype-fut { background: #1a1000; color: #fbbf24; border: 1px solid #fbbf2444; }
.itype-ce  { background: #0d1e2a; color: #3d9eff; border: 1px solid #3d9eff44; }
.itype-pe  { background: #2a0d0d; color: #f97316; border: 1px solid #f9731644; }
.itype-eq  { background: #21262d; color: #8b949e; border: 1px solid #3a404a; }

/* ── F&O Position rows ───────────────────────────────────────────────────── */
.rsp-row    { padding: 5px 8px; border-bottom: 1px solid #21262d; font-family: monospace; }
.rsp-top    { display: flex; align-items: center; gap: 5px; font-size: 12px; margin-bottom: 2px; }
.rsp-detail { display: flex; gap: 8px; font-size: 10px; color: #8b949e; }
.rsp-sl-t1  { display: flex; gap: 8px; font-size: 10px; margin-top: 2px; }
.rsp-lots   { color: #8b949e; font-size: 10px; }
.rsp-expiry { color: #a78bfa; }
.rsp-entry  { color: #8b949e; }
.rsl-sl { color: #ef5350; }
.rsl-t1 { color: #26a69a; }

.fo-scan-status { font-size: 10px; color: #6e7681; font-family: monospace; padding: 4px 10px 6px; }

/* ═══ LIVE panel ═══════════════════════════════════════════════════════════ */
.rs-live { border-top: 1px solid #1f2630; }
.hdr-live { background: linear-gradient(90deg, #0d1117 0%, #161b22 100%); }
.lv-empty { padding: 14px 14px 16px; text-align: center; }
.lv-empty-line { color: #8b949e; font-size: 12px; font-weight: 600; margin-bottom: 6px; }
.lv-empty-sub  { color: #6e7681; font-size: 10px; line-height: 1.5; font-family: monospace; }
.lv-empty-btn {
  background: linear-gradient(135deg,#fbbf24,#f59e0b); color:#0d1117;
  border: 0; padding: 7px 14px; border-radius: 4px; font-size: 11px;
  font-weight: 800; cursor: pointer; letter-spacing: .3px;
  transition: transform .1s, box-shadow .1s;
}
.lv-empty-btn:hover:not(:disabled) { transform: translateY(-1px); box-shadow: 0 4px 12px #fbbf2466; }
.lv-empty-btn:disabled { opacity: .5; cursor: not-allowed; }

/* Live scan progress bar shown in LIVE panel header */
.lv-scan-bar {
  position: relative; margin: 6px 8px; height: 18px; border-radius: 3px;
  background: #0d1117; border: 1px solid #21262d; overflow: hidden;
}
.lv-scan-bar-fill {
  position: absolute; top: 0; left: 0; bottom: 0;
  background: linear-gradient(90deg,#fbbf2433,#fbbf2488);
  transition: width .3s ease-out;
}
.lv-scan-bar-txt {
  position: relative; display: block; text-align: center; line-height: 18px;
  font-size: 10px; color: #fbbf24; font-family: monospace; font-weight: 700;
  letter-spacing: .3px;
}
.lv-empty-sub b { color: #adbac7; }
.lv-pulse {
  width: 8px; height: 8px; border-radius: 50%; background: #444;
  margin-left: 8px; transition: background .3s;
}
.lv-pulse-on { background: #26a69a; box-shadow: 0 0 6px #26a69a99;
  animation: lv-pulse 1.6s infinite ease-in-out; }
@keyframes lv-pulse { 0%,100% { opacity: 1 } 50% { opacity: .35 } }
.lv-clock { margin-left: auto; font-family: monospace; font-size: 11px;
  color: #6e7681; letter-spacing: .5px; }

/* ── Trade ticket card ─────────────────────────────────────────────────── */
.lv-ticket {
  margin: 6px; padding: 8px 10px; border-radius: 6px;
  background: #0d1117; border: 1px solid #21262d;
  font-family: monospace; font-size: 11px;
}
.lv-ticket-ce { border-left: 3px solid #26a69a; }
.lv-ticket-pe { border-left: 3px solid #ef5350; }
.lv-ticket-head { display: flex; align-items: center; gap: 6px;
  flex-wrap: wrap; margin-bottom: 6px; }
.lv-ticket-sym { font-weight: 800; color: #e6edf3; font-size: 12px; letter-spacing: .3px; }
.lv-ticket-bias { padding: 1px 6px; border-radius: 3px; font-weight: 700;
  font-size: 10px; letter-spacing: .5px; }
.lv-bias-bull { background: #0d2a1e; color: #26a69a; border: 1px solid #26a69a55; }
.lv-bias-bear { background: #2a0d0d; color: #ef5350; border: 1px solid #ef535055; }
.lv-ticket-exp { color: #6e7681; font-size: 10px; margin-left: auto; }

.lv-ticket-grid {
  display: grid; grid-template-columns: repeat(6, 1fr);
  gap: 4px; margin-bottom: 6px;
}
.lv-tk-cell { background: #161b22; padding: 4px 6px; border-radius: 3px; }
.lv-lab { color: #6e7681; font-size: 9px; text-transform: uppercase; letter-spacing: .5px; }
.lv-val { font-weight: 700; font-size: 12px; color: #e6edf3; margin-top: 2px; }
.lv-val.lv-entry { color: #58a6ff; }
.lv-val.lv-sl    { color: #ef5350; }
.lv-val.lv-t1    { color: #fbbf24; }
.lv-val.lv-t2    { color: #26a69a; }

.lv-ticket-greeks {
  display: flex; gap: 10px; flex-wrap: wrap;
  background: #0a0d11; padding: 5px 8px; border-radius: 3px;
  margin-bottom: 4px; font-size: 10px; color: #8b949e;
}
.lv-ticket-greeks b { color: #e6edf3; font-weight: 700; }
.lv-loss { margin-left: auto; color: #ef5350; }

.lv-ticket-foot { display: flex; gap: 10px; font-size: 10px; color: #6e7681;
  flex-wrap: wrap; }

/* "Watch" button on each scanner feed row */
.fo-watch-btn {
  background: #1d3a5a; color: #58a6ff; border: 1px solid #58a6ff55;
  padding: 1px 7px; border-radius: 3px; font-size: 9px; font-weight: 700;
  cursor: pointer; font-family: monospace; letter-spacing: .2px;
  transition: all .12s; margin-left: auto;
}
.fo-watch-btn:hover { background: #58a6ff; color: #0d1117; border-color: #58a6ff; }
.fo-watch-pinned {
  color: #26a69a; font-size: 9px; font-weight: 700;
  padding: 1px 7px; margin-left: auto;
  background: #0d2a1e; border: 1px solid #26a69a55; border-radius: 3px;
}

/* "I entered this trade" button + tracking indicator on ticket card */
.lv-ticket-track { margin-top: 8px; display: flex; align-items: center; gap: 8px; }
.lv-track-btn {
  background: linear-gradient(135deg,#26a69a,#1f8d83); color: #0d1117;
  border: 0; padding: 6px 12px; border-radius: 4px; font-size: 11px;
  font-weight: 800; cursor: pointer; letter-spacing: .3px;
  transition: transform .1s, box-shadow .1s; width: 100%;
}
.lv-track-btn:hover { transform: translateY(-1px); box-shadow: 0 4px 12px #26a69a66; }
.lv-track-pinned { color: #fbbf24; font-size: 11px; font-weight: 700;
  font-family: monospace; padding: 6px 0; width: 100%; text-align: center;
  background: #1c1607; border: 1px dashed #fbbf2455; border-radius: 4px; }

/* WATCHING section — pinned positions */
.lv-watch { margin: 8px 6px; padding: 8px; background: #0a0d11;
  border: 1px solid #21262d; border-radius: 6px; }
.lv-watch-head { display: flex; justify-content: space-between; align-items: baseline;
  margin-bottom: 8px; flex-wrap: wrap; gap: 4px; }
.lv-watch-title { color: #fbbf24; font-weight: 800; font-size: 11px; letter-spacing: .5px; }
.lv-watch-sub   { color: #6e7681; font-size: 9px; font-family: monospace; }
.lv-watch-card  {
  background: #0d1117; border: 1px solid #21262d; border-left: 3px solid #26a69a;
  border-radius: 4px; padding: 6px 8px; margin-bottom: 6px; font-family: monospace;
}
.lv-watch-card.lv-watch-near_sl    { border-left-color: #fbbf24; background: #1c1607; }
.lv-watch-card.lv-watch-sl_hit     { border-left-color: #ef5350; background: #2a0d0d;
  animation: lv-watch-flash 1s ease-in-out infinite; }
.lv-watch-card.lv-watch-near_t1    { border-left-color: #58a6ff; }
.lv-watch-card.lv-watch-past_t1    { border-left-color: #26a69a; background: #0d2a1e; }
.lv-watch-card.lv-watch-past_t2    { border-left-color: #26a69a; background: #0d3a28;
  animation: lv-watch-flash 1.2s ease-in-out infinite; }
.lv-watch-card.lv-watch-time_exit  { border-left-color: #ef5350; background: #2a0d0d;
  animation: lv-watch-flash 0.8s ease-in-out infinite; }
@keyframes lv-watch-flash { 0%,100% { box-shadow: 0 0 0 transparent; } 50% { box-shadow: 0 0 12px currentColor; } }

.lv-watch-row   { display: flex; align-items: center; gap: 8px; margin-bottom: 3px;
  font-size: 10px; color: #adbac7; }
.lv-watch-tick { display: inline-block; width: 6px; height: 6px; border-radius: 50%;
  background: #586069; margin-right: 4px; vertical-align: middle; }
.lv-watch-tick-on { background: #26a69a; box-shadow: 0 0 6px #26a69a99;
  animation: lv-chain-pulse 1.5s ease-in-out infinite; }
.lv-watch-pnl small { color: #6e7681; font-size: 9px; margin-left: 2px; }
.lv-watch-sym   { font-weight: 800; color: #e6edf3; flex: 1; font-size: 11px; }
.lv-watch-badge {
  font-size: 9px; font-weight: 800; padding: 2px 6px; border-radius: 3px;
  letter-spacing: .3px;
}
.lv-watch-badge.lv-watch-safe      { background: #0d2a1e; color: #26a69a; }
.lv-watch-badge.lv-watch-near_sl   { background: #1c1607; color: #fbbf24; }
.lv-watch-badge.lv-watch-sl_hit    { background: #2a0d0d; color: #ef5350; }
.lv-watch-badge.lv-watch-near_t1   { background: #07142a; color: #58a6ff; }
.lv-watch-badge.lv-watch-past_t1,
.lv-watch-badge.lv-watch-past_t2   { background: #0d2a1e; color: #26a69a; }
.lv-watch-badge.lv-watch-time_exit { background: #2a0d0d; color: #ef5350; }
.lv-watch-exit  {
  background: #21262d; color: #adbac7; border: 1px solid #30363d;
  border-radius: 3px; padding: 2px 7px; font-size: 9px; font-family: monospace;
  cursor: pointer; font-weight: 700;
}
.lv-watch-exit:hover { background: #30363d; color: #e6edf3; }
.lv-watch-exit-armed {
  background: #ef5350 !important; color: #fff !important;
  border-color: #ef5350 !important; font-weight: 800;
  animation: lv-watch-flash 0.6s ease-in-out infinite;
}
.lv-watch-test-sound {
  background: transparent; color: #fbbf24; border: 1px solid #fbbf2455;
  padding: 1px 6px; border-radius: 3px; font-size: 9px; cursor: pointer;
  font-family: monospace; font-weight: 700; margin-left: auto;
}
.lv-watch-test-sound:hover { background: #1c1607; }

/* Server-side alerts connection state */
.lv-watch-dot { width: 8px; height: 8px; border-radius: 50%; flex-shrink: 0; }
.lv-dot-on  { background: #26a69a; box-shadow: 0 0 6px #26a69a;
              animation: lv-pulse-dot 2s ease-in-out infinite; }
.lv-dot-off { background: #ef5350; opacity: .7; }
@keyframes lv-pulse-dot { 0%,100% { opacity: 1; } 50% { opacity: .4; } }

/* "While you were away" panel — server alerts that fired pre-reconnect */
.lv-missed { background: #0d1a2a; border: 1px solid #1d3a5a; border-radius: 4px;
             padding: 6px; margin-bottom: 8px; }
.lv-missed-head { display: flex; justify-content: space-between; align-items: center;
                  font-size: 10px; color: #58a6ff; font-weight: 700; margin-bottom: 4px;
                  font-family: monospace; letter-spacing: .3px; }
.lv-missed-clear { background: transparent; border: 1px solid #30363d; color: #8b949e;
                   font-size: 9px; padding: 1px 6px; border-radius: 3px; cursor: pointer;
                   font-family: monospace; }
.lv-missed-clear:hover { color: #adbac7; border-color: #58a6ff; }
.lv-missed-row { display: flex; align-items: center; gap: 6px; padding: 3px 4px;
                 font-size: 10px; font-family: monospace; border-bottom: 1px dashed #1d3a5a; }
.lv-missed-row:last-child { border-bottom: 0; }
.lv-missed-tag { font-weight: 800; padding: 1px 5px; border-radius: 3px; font-size: 9px;
                 letter-spacing: .3px; flex-shrink: 0; }
.lv-missed-sl_hit    { background: #2a0d0d; color: #ef5350; }
.lv-missed-near_sl   { background: #1c1607; color: #fbbf24; }
.lv-missed-past_t1,
.lv-missed-past_t2   { background: #0d2a1e; color: #26a69a; }
.lv-missed-near_t1   { background: #07142a; color: #58a6ff; }
.lv-missed-time_exit { background: #2d1f0a; color: #fbbf24; }
.lv-missed-sym  { color: #e6edf3; font-weight: 700; flex-shrink: 0; }
.lv-missed-msg  { color: #8b949e; flex: 1; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
.lv-missed-time { color: #6e7681; font-size: 9px; flex-shrink: 0; }

.lv-watch-prices b   { color: #e6edf3; font-weight: 700; }
.lv-watch-prices b.up { color: #26a69a; }
.lv-watch-prices b.dn { color: #ef5350; }
.lv-watch-pnl   { margin-left: auto; font-weight: 800; }
.lv-watch-pnl.up { color: #26a69a; }
.lv-watch-pnl.dn { color: #ef5350; }
.lv-watch-levels { color: #8b949e; }
.lv-watch-sl { color: #ef5350; }
.lv-watch-t1 { color: #58a6ff; }
.lv-watch-t2 { color: #26a69a; }
.lv-watch-alert { font-size: 10px; color: #ef5350; font-weight: 700;
  padding: 4px 6px; margin-top: 4px; background: #2a0d0d;
  border: 1px solid #ef5350; border-radius: 3px; }
.lv-window { color: #fbbf24; }
.lv-order-type {
  padding: 1px 5px; background: #161b22; border-radius: 3px;
  border: 1px solid #21262d; font-weight: 700;
}

/* ── LIVE banner inside ticket: re-prices on every tick ───────────────── */
.lv-ticket-live {
  background: linear-gradient(90deg, #0a1f1a 0%, #0d1117 100%);
  border: 1px solid #1f3d36; border-radius: 5px;
  padding: 6px 10px; margin: 4px 0 6px; font-size: 11px;
}
.lv-ticket-live-row { display: flex; align-items: center; gap: 10px; flex-wrap: wrap; }
.lv-ticket-live-sub { font-size: 10px; color: #8b949e; margin-top: 3px; }
.lv-live-label { color: #26a69a; font-weight: 700; letter-spacing: .3px; }
.lv-live-prem  { color: #e6edf3; font-weight: 800; font-size: 14px; font-family: 'JetBrains Mono', monospace; }
.lv-live-chg   { font-weight: 700; font-size: 11px; }
.lv-live-chg.up { color: #26a69a; }
.lv-live-chg.dn { color: #ef5350; }

/* Live-tick freshness indicator */
.lv-tick-dot {
  display: inline-block; width: 7px; height: 7px; border-radius: 50%;
  background: #ef5350; margin-right: 5px; vertical-align: middle;
  transition: background .2s;
}
.lv-tick-dot.lv-tick-fresh {
  background: #26a69a; box-shadow: 0 0 5px #26a69a99;
  animation: lv-tick-flash 1.6s infinite ease-in-out;
}
@keyframes lv-tick-flash {
  0%, 100% { opacity: 1; transform: scale(1); }
  50%      { opacity: .55; transform: scale(1.45); }
}
.lv-tick-age {
  font-size: 9px; color: #6e7681; font-family: monospace;
  margin-left: 4px; padding: 0 4px;
  background: #161b22; border-radius: 2px;
}

/* Moneyness badges */
.lv-mny {
  font-weight: 800; font-size: 10px; padding: 1px 5px;
  border-radius: 3px; letter-spacing: .5px;
}
.lv-mny-itm { background: #1a3d2e; color: #26a69a; border: 1px solid #2d6650; }
.lv-mny-otm { background: #3d2a1a; color: #fbbf24; border: 1px solid #66472d; }

/* Risk:Reward ratio chip */
.lv-rr {
  margin-left: 6px; padding: 1px 6px; border-radius: 3px;
  font-weight: 800; font-size: 10px;
}
.lv-rr.good { background: #1a3d2e; color: #26a69a; }
.lv-rr.ok   { background: #3d3a1a; color: #fbbf24; }
.lv-rr.bad  { background: #3d1a1a; color: #ef5350; }

/* Theta-warning banner */
.lv-theta-warn {
  background: #2d1f0a; border: 1px solid #66472d;
  color: #fbbf24; padding: 5px 8px; border-radius: 4px;
  margin: 4px 0; font-size: 10px; line-height: 1.35;
  cursor: help;
}

/* Tooltip cursor hint — only on read-only informational tooltips. The earlier
 * blanket `[title]:hover { cursor: help }` override turned every button +
 * dropdown + select into a ❓ which broke clickability discovery — interactive
 * elements need their natural pointer/text cursor. */
.lv-ticket-greeks span[title], .lv-tk-cell[title] { cursor: help; }

/* ── Per-position tick card ───────────────────────────────────────────── */
.lv-pos {
  margin: 6px; padding: 8px 10px; border-radius: 6px;
  background: #0d1117; border: 1px solid #21262d;
  font-family: monospace; font-size: 11px;
}
.lv-pos-decision { border-color: #fbbf24; box-shadow: 0 0 8px #fbbf2455;
  animation: lv-flash 1.2s ease-in-out infinite; }
@keyframes lv-flash { 0%,100% { box-shadow: 0 0 8px #fbbf2433 }
  50% { box-shadow: 0 0 14px #fbbf24aa } }

/* ── Option chain ladder ──────────────────────────────────────────── */
.lv-chain { margin: 8px 6px 4px; padding: 8px 8px 6px;
  background: #0a0d11; border: 1px solid #21262d; border-radius: 6px; }
.lv-chain-head { display: flex; align-items: center; gap: 8px; flex-wrap: wrap;
  margin-bottom: 6px; font-family: monospace; font-size: 10px; color: #8b949e; }
.lv-chain-title { color: #fbbf24; font-weight: 800; letter-spacing: .5px; font-size: 11px; }
.lv-chain-sel { background: #161b22; color: #e6edf3; border: 1px solid #30363d;
  border-radius: 3px; padding: 1px 4px; font-size: 10px; font-family: monospace; cursor: pointer; }
.lv-chain-meta { color: #6e7681; display: inline-flex; align-items: center; gap: 4px; flex-wrap: wrap; }
.lv-chain-sentiment { width: 100%; display: flex; flex-direction: column; gap: 3px;
  margin: 4px 0 2px; font-family: monospace; font-size: 10px; }
.lv-chain-sentiment > span { padding: 3px 7px; border-radius: 3px;
  background: #161b22; border: 1px solid #30363d;
  display: flex; align-items: center; gap: 6px; flex-wrap: wrap; }
.lv-chain-sentiment .sm-lab { color: #6e7681; min-width: 64px; font-weight: 600; }
.lv-chain-sentiment > span b { color: #e6edf3; min-width: 50px; }
.lv-chain-sentiment .sm-meaning { color: #adbac7; font-size: 9px; }
.lv-chain-pcr.pcr-bull    { color: #26a69a; border-color: #26a69a55; }
.lv-chain-pcr.pcr-bear    { color: #ef5350; border-color: #ef535055; }
.lv-chain-pcr.pcr-neutral { color: #6e7681; }
.lv-chain-mp   { color: #fbbf24; border-color: #fbbf2455; }
.lv-chain-peak { color: #c084fc; border-color: #c084fc55; }
.lv-chain-dot { width: 6px; height: 6px; border-radius: 50%; background: #586069; flex-shrink: 0; }
.lv-chain-dot-on { background: #26a69a; box-shadow: 0 0 6px #26a69a99; animation: lv-chain-pulse 1.5s ease-in-out infinite; }
@keyframes lv-chain-pulse { 0%,100% { opacity: 1 } 50% { opacity: .35 } }
.lv-chain-age { color: #fbbf24; font-weight: 700; font-size: 9px; margin-left: 2px; }
.lv-chain-filter { margin-left: auto; cursor: pointer; user-select: none; color: #adbac7;
  display: flex; align-items: center; gap: 4px; flex-wrap: wrap; }
.lv-chain-filter input { vertical-align: middle; margin-right: 3px; }
.lv-chain-filter-hint { color: #6e7681; font-size: 9px; }
.lv-chain-legend { display: flex; flex-direction: column; gap: 2px;
  margin: 4px 0 6px; padding: 6px 8px; background: #0d1117;
  border: 1px dashed #30363d; border-radius: 4px;
  font-family: monospace; font-size: 9px; color: #8b949e; }
.lv-chain-legend span { line-height: 1.6; }
.lv-chain-legend b { display: inline-block; min-width: 90px; padding: 0 5px;
  border-radius: 2px; font-weight: 700; margin-right: 6px; font-size: 8px; }
.lv-chain-legend .lv-verdict-conservative { background: #26a69a22; color: #26a69a; }
.lv-chain-legend .lv-verdict-balanced     { background: #fbbf2422; color: #fbbf24; }
.lv-chain-legend .lv-verdict-aggressive   { background: #f9731622; color: #f97316; }
.lv-chain-legend .lv-verdict-lottery      { background: #ef535022; color: #ef5350; }
.lv-chain-legend .lv-verdict-expensive    { background: #6e768122; color: #adbac7; }
.lv-chain-oi { padding: 2px 6px; font-size: 9px; font-family: monospace;
  background: #0a0d11; border-top: 1px solid #21262d;
  display: flex; align-items: center; justify-content: center; min-height: 16px;
  color: #8b949e; cursor: help; }
.lv-chain-oi-ce  { color: #ff8a65; }
.lv-chain-oi-pe  { color: #4fc3f7; }
.lv-chain-oi-vol { color: #6e7681; font-size: 8px; }
.lv-chain-oi-pcr { color: #fbbf24; font-weight: 700; background: #161b22; }
.lv-chain-grid {
  display: grid;
  /* Fixed minimum column widths so verdict pills + ₹X,XXX never get clipped.
     Wrapper below provides horizontal scroll if the sidebar is too narrow. */
  grid-template-columns: minmax(56px,0.7fr) minmax(78px,1fr) minmax(72px,1.1fr) minmax(78px,1fr) minmax(56px,0.7fr);
  min-width: 340px;
  gap: 1px;
  background: #161b22;
  border: 1px solid #21262d;
  border-radius: 4px;
  overflow: hidden;
  font-family: 'JetBrains Mono', monospace; font-size: 11px;
}
.lv-chain { overflow-x: auto; -webkit-overflow-scrolling: touch; }
.lv-chain::-webkit-scrollbar { height: 6px; }
.lv-chain::-webkit-scrollbar-thumb { background: #30363d; border-radius: 3px; }
.lv-chain-th { background: #0d1117; color: #6e7681; padding: 4px;
  text-align: center; font-size: 9px; letter-spacing: .5px; font-weight: 700; }
.lv-chain-th-strike { color: #adbac7; }
.lv-chain-cell { padding: 4px 6px; background: #0d1117; text-align: center; color: #adbac7; }
.lv-chain-strike { font-weight: 800; color: #adbac7; background: #161b22; }
.lv-chain-atm { background: #1a3d2e !important; color: #26a69a !important;
  box-shadow: inset 0 0 0 1px #26a69a55; }
.lv-chain-cep, .lv-chain-pep { font-weight: 700; }
.lv-chain-cep { color: #26a69a; }
.lv-chain-pep { color: #ef5350; }
.lv-chain-mny-itm { background: #0d1f14; color: #26a69a; }
.lv-chain-mny-otm { background: #1f160a; color: #fbbf24; }

/* Tick-flash on premium cells: cell briefly glows on every up/down change */
.lv-flash-up { animation: lv-flash-up 600ms ease-out; }
.lv-flash-dn { animation: lv-flash-dn 600ms ease-out; }
@keyframes lv-flash-up {
  0%   { background: #1a4d33; box-shadow: inset 0 0 0 1px #26a69a; }
  100% { background: #0d1117; box-shadow: none; }
}
@keyframes lv-flash-dn {
  0%   { background: #4d1a1a; box-shadow: inset 0 0 0 1px #ef5350; }
  100% { background: #0d1117; box-shadow: none; }
}

.lv-spin { display: inline-block; animation: lv-spin 1.2s linear infinite; }
@keyframes lv-spin { from { transform: rotate(0) } to { transform: rotate(360deg) } }

/* Sub-row: cost / breakeven / verdict beneath each strike's premium row */
.lv-chain-sub { padding: 2px 4px; background: #0a0d11; text-align: center;
  font-size: 9px; color: #6e7681; border-top: 1px dashed #1f2630; }
.lv-chain-sub-mid { background: #161b22; }
.lv-chain-be { color: #586069; }

/* Buy-quality verdict pills */
.lv-verdict-balanced     { color: #26a69a; font-weight: 700; }
.lv-verdict-aggressive   { color: #fbbf24; font-weight: 700; }
.lv-verdict-conservative { color: #58a6ff; font-weight: 700; }
.lv-verdict-lottery      { color: #ef5350; font-weight: 700; }
.lv-verdict-expensive    { color: #a78bfa; font-weight: 700; }

/* AI-pick highlight — ring across the row's CE or PE side */
.lv-chain-pick { box-shadow: inset 0 0 0 1px #fbbf24; background: #2a1f0a !important; }
.lv-chain-pick-mark { color: #fbbf24; margin-right: 2px; font-size: 10px; }

.lv-pos-head { display: flex; align-items: center; gap: 8px; margin-bottom: 6px; }
.lv-pos-sym { font-weight: 800; color: #e6edf3; font-size: 12px; }
.lv-pnl { padding: 1px 6px; border-radius: 3px; font-weight: 800; font-size: 11px; }
.lv-pnl.up { background: #0d2a1e; color: #26a69a; }
.lv-pnl.dn { background: #2a0d0d; color: #ef5350; }
.lv-pos-prem { color: #58a6ff; font-weight: 700; margin-left: auto; }
.lv-pos-hwm  { color: #fbbf24; font-size: 10px; }

/* premium ladder */
.lv-ladder { margin: 4px 0; }
.lv-ladder-track {
  position: relative; height: 6px; background: #161b22; border-radius: 3px;
  overflow: visible;
}
.lv-ladder-fill {
  position: absolute; top: 0; height: 100%;
  background: linear-gradient(90deg, #58a6ff 0%, #26a69a 100%);
  border-radius: 3px; opacity: .55;
}
.lv-ladder-pin {
  position: absolute; top: -5px; transform: translateX(-50%);
  color: #fbbf24; font-size: 14px; line-height: 1;
  text-shadow: 0 0 4px #fbbf24aa;
}
.lv-ladder-legend {
  display: flex; justify-content: space-between;
  font-size: 9px; color: #6e7681; margin-top: 3px;
}
.lv-ladder-legend .lv-sl    { color: #ef5350; }
.lv-ladder-legend .lv-entry { color: #58a6ff; }
.lv-ladder-legend .lv-t1    { color: #fbbf24; }
.lv-ladder-legend .lv-t2    { color: #26a69a; }

.lv-pos-greeks { display: flex; gap: 10px; flex-wrap: wrap;
  font-size: 10px; color: #8b949e; margin-top: 4px; }

.lv-decision {
  margin-top: 6px; padding: 6px 8px; border-radius: 4px;
  font-weight: 800; font-size: 11px; display: flex; gap: 8px; align-items: center;
}
.lv-dec-exit_full    { background: #2a0d0d; color: #ef5350; border: 1px solid #ef535055; }
.lv-dec-exit_partial { background: #2a1d00; color: #fbbf24; border: 1px solid #fbbf2455; }
.lv-dec-reason { font-weight: 400; color: #c9d1d9; font-family: monospace; font-size: 10px; }

/* ── Commentary ticker ────────────────────────────────────────────────── */
.lv-commentary {
  margin: 6px; padding: 8px 10px; border-radius: 6px;
  background: #0a0d11; border: 1px solid #21262d;
  font-size: 11px;
}
.lv-comm-head { color: #6e7681; font-size: 10px; margin-bottom: 4px;
  display: flex; gap: 8px; align-items: center; }
.lv-comm-ts { margin-left: auto; }
.lv-comm-text { color: #c9d1d9; line-height: 1.5; white-space: pre-wrap; }


/* ── Intelligence panel ──────────────────────────────────────────────────── */
.intel-panel {
  height: 310px;
  flex-shrink: 0;
  border-top: 1px solid #30363d;
  background: #161b22;
  display: flex;
  flex-direction: column;
}

.intel-header {
  display: flex;
  align-items: center;
  gap: 10px;
  padding: 8px 14px;
  border-bottom: 1px solid #30363d;
  flex-shrink: 0;
}

.intel-title {
  font-size: 12px;
  color: #8b949e;
  letter-spacing: 1px;
  font-family: monospace;
  font-weight: 600;
}

.intel-tabs { display: flex; gap: 4px; }

.itab {
  height: 28px;
  padding: 0 14px;
  background: transparent;
  border: 1px solid #3a404a;
  border-radius: 4px;
  color: #8b949e;
  font-size: 12px;
  cursor: pointer;
  font-family: monospace;
}

.itab:hover { color: #adbac7; border-color: #6e7681; }
.itab.active { border-color: #2dd4bf; color: #2dd4bf; background: #00d4a810; }

.intel-ticker-label { font-size: 12px; color: #7d8590; margin-left: auto; }
.intel-ticker-label b { color: #999; }

.ai-pred-btn {
  height: 28px;
  padding: 0 12px;
  background: #00d4a815;
  border: 1px solid #00d4a855;
  border-radius: 4px;
  color: #2dd4bf;
  font-size: 12px;
  cursor: pointer;
  font-family: monospace;
  display: flex;
  align-items: center;
  gap: 4px;
}
.ai-pred-btn:hover:not(:disabled) { background: #00d4a830; border-color: #2dd4bf; }
.ai-pred-btn:disabled { opacity: 0.5; cursor: default; }

.sim-quick-btn {
  height: 28px;
  padding: 0 12px;
  background: #ff475715;
  border: 1px solid #ff475755;
  border-radius: 4px;
  color: #ff9f43;
  font-size: 12px;
  cursor: pointer;
  font-family: monospace;
  display: flex;
  align-items: center;
  gap: 4px;
}
.sim-quick-btn:hover:not(:disabled) { background: #ff475730; border-color: #f85149; color: #f85149; }
.sim-quick-btn:disabled { opacity: 0.5; cursor: default; }

.intel-refresh { font-size: 11px; color: #6e7681; font-family: monospace; }

.intel-feed {
  flex: 1;
  overflow-x: auto;
  overflow-y: hidden;
  display: flex;
  gap: 10px;
  padding: 10px 14px;
  align-items: stretch;
}

.intel-feed::-webkit-scrollbar { height: 4px; }
.intel-feed::-webkit-scrollbar-track { background: transparent; }
.intel-feed::-webkit-scrollbar-thumb { background: #2a2a2a; border-radius: 2px; }

.feed-card {
  flex-shrink: 0;
  width: 320px;
  background: #1c2128;
  border: 1px solid #1e1e1e;
  border-radius: 8px;
  padding: 10px 13px;
  display: flex;
  flex-direction: column;
  gap: 6px;
  transition: border-color 0.15s;
}

.feed-card:hover { border-color: #333; }
.feed-card.news       { border-left: 3px solid #3d9eff; }
.feed-card.twitter    { border-left: 3px solid #1d9bf0; }
.feed-card.reddit     { border-left: 3px solid #ff4500; }
.feed-card.fomo       { border-left: 3px solid #ffb300; }
.feed-card.prediction { border-left: 3px solid #00d4a8; }
.feed-card.simulation { border-left: 3px solid #a78bfa; }

.fc-badge {
  font-size: 10px;
  font-family: monospace;
  letter-spacing: 1px;
  font-weight: 700;
}

.fc-badge.news       { color: #3d9eff; }
.fc-badge.twitter    { color: #1d9bf0; }
.fc-badge.reddit     { color: #ff4500; }
.fc-badge.fomo       { color: #ffb300; }
.fc-badge.prediction { color: #2dd4bf; }
.fc-badge.simulation { color: #a78bfa; }

.fc-body { flex: 1; }
.fc-title { font-size: 13px; color: #ddd; line-height: 1.5;
            display: -webkit-box; -webkit-line-clamp: 3;
            -webkit-box-orient: vertical; overflow: hidden; }
.fc-meta  { font-size: 11px; color: #7d8590; margin-top: 3px; font-family: monospace; }

.fc-right { display: flex; justify-content: space-between; align-items: center; margin-top: 4px; }
.fc-sent  { font-size: 11px; font-weight: 600; font-family: monospace; }
.fc-score { font-size: 11px; color: #7d8590; font-family: monospace; }

.feed-empty, .feed-loading {
  color: #6e7681; font-size: 13px; padding: 20px; display: flex;
  align-items: center; gap: 8px;
}
.scan-err { color: #f85149; }

/* ── Prediction bar ──────────────────────────────────────────────────────── */
.pred-bar {
  display: flex;
  align-items: center;
  gap: 12px;
  padding: 6px 12px;
  background: #13181f;
  border-top: 1px solid #30363d;
  flex-shrink: 0;
  font-family: 'JetBrains Mono', monospace;
  font-size: 11px;
}

.pred-label { color: #2dd4bf; font-size: 10px; letter-spacing: 0.5px; }
.pred-item  { color: #adbac7; }
.pred-item b { font-weight: 700; }
.pred-divider { color: #2a2a2a; }
.pred-fomo  { color: #7d8590; margin-left: 4px; }

.pred-sim-btn {
  margin-left: auto;
  height: 24px;
  padding: 0 12px;
  background: #00d4a820;
  border: 1px solid #00d4a8;
  border-radius: 3px;
  color: #2dd4bf;
  font-size: 11px;
  cursor: pointer;
}

.pred-sim-btn:hover { background: #00d4a840; }

/* ── Shared colors ───────────────────────────────────────────────────────── */
.up  { color: #2dd4bf; }
.dn  { color: #f85149; }
.neu { color: #adbac7; }
.hot { color: #ffb300; }

/* ── Spinner ─────────────────────────────────────────────────────────────── */
@keyframes spin { to { transform: rotate(360deg); } }
.spinner {
  display: inline-block;
  width: 12px; height: 12px;
  border: 2px solid #2a2a2a;
  border-top-color: #2dd4bf;
  border-radius: 50%;
  animation: spin 0.7s linear infinite;
}

/* ── Lower row ───────────────────────────────────────────────────────────── */
.lower-row {
  display: flex;
  gap: 0;
  flex-shrink: 0;
  height: 320px;
  border-top: 1px solid #30363d;
  overflow: hidden;
}

.panel {
  display: flex;
  flex-direction: column;
  border-right: 1px solid #30363d;
  background: #161b22;
  overflow: hidden;
}

.panel:last-child { border-right: none; }

.graph-panel   { flex: 1.2; }
.levels-panel  { flex: 1.4; }
.signal-panel  { flex: 1.4; position: relative; }
.wallet-panel  { flex: 1.2; position: relative; }
.invest-panel  { flex: 2.2; }

/* ── Investment Analysis ────────────────────────────────────── */
.ia-body {
  display: flex; flex: 1; overflow: hidden;
}
.ia-graph-wrap {
  flex: 1.1; position: relative; overflow: hidden; border-right: 1px solid #30363d;
}
.ia-svg { width: 100%; height: 100%; display: block; }

.ia-agent-tip {
  position: absolute; top: 8px; left: 8px;
  background: #1c2128; border: 1px solid #3a404a;
  border-radius: 4px; padding: 8px 10px;
  max-width: 220px; font-size: 11px; z-index: 10;
}
.iat-name  { font-weight: 700; color: #cdd9e5; margin-bottom: 2px; font-size: 10px; }
.iat-conf  { color: #8b949e; font-size: 10px; }
.iat-reason { color: #adbac7; margin-top: 4px; line-height: 1.4; }
.iatf-item  { color: #777; margin-top: 2px; font-size: 10px; line-height: 1.3; }
.iat-levels { color: #2dd4bf; margin-top: 5px; font-family: monospace; font-size: 10px; }

.ia-report {
  flex: 1; overflow-y: auto; padding: 0;
  display: flex; flex-direction: column; gap: 0;
}
.ia-report::-webkit-scrollbar { width: 4px; }
.ia-report::-webkit-scrollbar-thumb { background: #222; }

.ia-verdict-banner {
  padding: 8px 12px;
  display: flex; align-items: center; gap: 8px;
  border-bottom: 1px solid #30363d; flex-shrink: 0;
}
.iav-label { font-size: 13px; font-weight: 800; font-family: monospace; letter-spacing: 1px; }
.iav-score { font-size: 11px; color: #adbac7; }
.iav-size  { font-size: 10px; color: #7d8590; margin-left: auto; }

.ia-levels {
  padding: 6px 12px; border-bottom: 1px solid #111; flex-shrink: 0;
  display: flex; flex-direction: column; gap: 2px;
}
.ial-row {
  display: flex; justify-content: space-between;
  font-size: 11px; font-family: monospace; padding: 1px 0;
}
.ial-row span:first-child { color: #7d8590; font-size: 10px; }
.ial-row.entry span:last-child { color: #fff; font-weight: 700; }
.ial-row.sl    span:last-child { color: #ef4444; }
.ial-row.t1    span:last-child { color: #86efac; }
.ial-row.t2    span:last-child { color: #4ade80; }
.ial-row.t3    span:last-child { color: #22c55e; }

.ia-thesis {
  padding: 8px 12px; font-size: 11px; color: #adbac7;
  line-height: 1.5; border-bottom: 1px solid #111; flex-shrink: 0;
}

.ia-agents-list { flex: 1; overflow-y: auto; }
.ia-agents-list::-webkit-scrollbar { width: 3px; }
.iaa-card {
  padding: 6px 12px; border-bottom: 1px solid #111;
  cursor: pointer; transition: background 0.15s;
}
.iaa-card:hover { background: #161616; }
.iaa-top { display: flex; align-items: center; gap: 6px; margin-bottom: 2px; }
.iaa-name   { font-size: 10px; color: #bbb; flex: 1; font-weight: 600; }
.iaa-verdict { font-size: 10px; font-weight: 700; font-family: monospace; }
.iaa-conf   { font-size: 10px; color: #7d8590; }
.iaa-meta   { display: flex; align-items: center; gap: 6px; margin-bottom: 3px; }
.iaa-firm   { font-size: 9px; color: #7d8590; flex: 1; }
.iaa-src    { font-size: 9px; padding: 1px 5px; border-radius: 3px; font-family: monospace; }
.src-company_officer    { background: rgba(0,212,168,0.12); color: #2dd4bf; }
.src-news_ner           { background: rgba(251,191,36,0.12); color: #fbbf24; }
.src-institutional_holder { background: rgba(96,165,250,0.12); color: #60a5fa; }
.src-analyst_rating     { background: rgba(167,139,250,0.12); color: #a78bfa; }
.src-news_ner_inst      { background: rgba(251,191,36,0.08); color: #d97706; }
.src-llm_grounded       { background: rgba(100,100,100,0.12); color: #8b949e; }
.src-unknown            { background: rgba(100,100,100,0.08); color: #7d8590; }
.iaa-reason { font-size: 10px; color: #8b949e; line-height: 1.4; }

.ia-risks { padding: 8px 12px; flex-shrink: 0; }
.iar-title { font-size: 10px; color: #f87171; font-weight: 700; margin-bottom: 4px; letter-spacing: 1px; }
.iar-item  { font-size: 10px; color: #8b949e; margin-bottom: 2px; }

/* verdict color classes */
.vc-sbuy .iav-label, .vc-sbuy .iaa-verdict { color: #22c55e; }
.vc-buy  .iav-label, .vc-buy  .iaa-verdict { color: #86efac; }
.vc-hold .iav-label, .vc-hold .iaa-verdict { color: #fbbf24; }
.vc-sell .iav-label, .vc-sell .iaa-verdict { color: #f97316; }
.vc-ssell .iav-label, .vc-ssell .iaa-verdict { color: #ef4444; }
.vc-sbuy .ia-verdict-banner { background: #052e1620; }
.vc-buy  .ia-verdict-banner { background: #052e1612; }
.vc-hold .ia-verdict-banner { background: #2d200010; }
.vc-sell .ia-verdict-banner { background: #2d100012; }
.vc-ssell .ia-verdict-banner { background: #2d050518; }
.vc-sbuy .iaa-card:hover { background: #052e1618; }
.vc-buy  .iaa-card:hover { background: #052e1612; }
.vc-hold .iaa-card:hover { background: #2d200012; }
.vc-sell .iaa-card:hover { background: #2d100012; }
.vc-ssell .iaa-card:hover { background: #2d050512; }

.iat-verdict { font-size: 12px; font-weight: 800; font-family: monospace; margin-bottom: 2px; }
.vc-sbuy .iat-verdict { color: #22c55e; }
.vc-buy  .iat-verdict { color: #86efac; }
.vc-hold .iat-verdict { color: #fbbf24; }
.vc-sell .iat-verdict { color: #f97316; }
.vc-ssell .iat-verdict { color: #ef4444; }

.ia-agents { color: #7d8590; font-size: 10px; }

/* ── View tabs inside navbar ─────────────────────────────────── */
.view-tabs-nav {
  display: flex; align-items: center; gap: 2px; margin-left: 8px;
}
.vtn-tab {
  height: 26px; padding: 0 14px;
  background: transparent; border: 1px solid #3a404a; border-radius: 4px;
  color: #7d8590; font-size: 11px; cursor: pointer; font-family: monospace;
  transition: all 0.15s;
}
.vtn-tab:hover { color: #adbac7; border-color: #6e7681; }
.vtn-tab.active { color: #2dd4bf; border-color: #2dd4bf; background: rgba(0,212,168,0.08); }

/* ── Full-screen Analysis View ──────────────────────────────── */
.analysis-view {
  display: flex; flex-direction: column;
  flex: 1; overflow: hidden; background: #0d1117;
}
.av-statusbar {
  display: flex; align-items: center; gap: 10px;
  padding: 6px 16px; border-bottom: 1px solid #30363d;
  flex-shrink: 0; background: #161b22;
}
.av-ticker  { font-family: monospace; font-weight: 700; font-size: 14px; color: #fff; }
.av-company { font-size: 11px; color: #8b949e; }
.av-price   { font-family: monospace; font-size: 12px; color: #2dd4bf; }
.av-agents  { font-size: 10px; color: #6e7681; }
.av-real-tag { color: #2dd4bf; }
.av-spacer  { flex: 1; }
.av-verdict-pill {
  font-size: 11px; font-weight: 700; font-family: monospace;
  padding: 3px 8px; border-radius: 3px; border: 1px solid currentColor;
}
.av-horizon { font-size: 10px; color: #7d8590; }
.av-run-btn {
  height: 26px; padding: 0 12px;
  background: #1a2820; border: 1px solid #00d4a855;
  border-radius: 3px; color: #2dd4bf; font-size: 11px;
  cursor: pointer; font-family: monospace;
  display: flex; align-items: center; gap: 5px;
}
.av-run-btn:disabled { opacity: 0.5; cursor: default; }
.av-run-btn:hover:not(:disabled) { background: #22342a; }

.av-body {
  display: flex; flex: 1; overflow: hidden;
}
.av-graph-col {
  flex: 2.2; display: flex; flex-direction: column;
  border-right: 1px solid #30363d; position: relative;
}
.av-graph-wrap { flex: 1; overflow: hidden; }
.av-svg { width: 100%; height: 100%; display: block; }

.av-legend {
  display: flex; gap: 10px; padding: 5px 12px;
  border-top: 1px solid #111; flex-shrink: 0;
}
.avl-item { display: flex; align-items: center; gap: 4px; font-size: 10px; color: #7d8590; }
.avl-dot  { width: 8px; height: 8px; border-radius: 50%; flex-shrink: 0; }

.av-graph-empty, .av-graph-loading {
  position: absolute; inset: 0; display: flex;
  align-items: center; justify-content: center;
  font-size: 12px; color: #6e7681; text-align: center; padding: 20px;
}
.av-graph-loading { flex-direction: column; gap: 10px; color: #8b949e; }

.av-report-col {
  width: 460px; flex-shrink: 0; overflow-y: auto;
  display: flex; flex-direction: column;
}
.av-report-col::-webkit-scrollbar { width: 4px; }
.av-report-col::-webkit-scrollbar-thumb { background: #222; }

.av-report-empty {
  flex: 1; display: flex; align-items: center; justify-content: center;
  font-size: 12px; color: #333;
}

.avr-action {
  padding: 14px 16px; border-bottom: 2px solid #1a1a1a; flex-shrink: 0;
  background: #161b22;
}
.avract-label { font-size: 10px; letter-spacing: 2px; color: #7d8590; margin-bottom: 4px; font-family: monospace; }
.avract-value { font-size: 22px; font-weight: 800; font-family: monospace; margin-bottom: 4px; }
.avract-reason { font-size: 12px; color: #adbac7; line-height: 1.5; }
.vc-sbuy .avract-value  { color: #22c55e; }
.vc-buy  .avract-value  { color: #86efac; }
.vc-hold .avract-value  { color: #fbbf24; }
.vc-sell .avract-value  { color: #f97316; }
.vc-ssell .avract-value { color: #ef4444; }

.avr-verdict {
  padding: 10px 14px; border-bottom: 1px solid #30363d; flex-shrink: 0;
}
.avrv-main { display: flex; align-items: center; gap: 8px; margin-bottom: 4px; }
.avrv-label { font-size: 16px; font-weight: 800; font-family: monospace; letter-spacing: 1px; }
.avrv-consensus { font-size: 12px; color: #adbac7; }
.avrv-counts { display: flex; gap: 10px; }
.avrv-bull { font-size: 11px; color: #22c55e; }
.avrv-bear { font-size: 11px; color: #ef4444; }
.avrv-neut { font-size: 11px; color: #adbac7; }

.avr-levels {
  padding: 8px 14px; border-bottom: 1px solid #111; flex-shrink: 0;
  display: flex; flex-direction: column; gap: 3px;
}
.avrl-row {
  display: flex; justify-content: space-between;
  font-size: 12px; font-family: monospace;
}
.avrl-row span:first-child { font-size: 10px; color: #7d8590; }
.avrl-row.entry span:last-child { color: #fff; font-weight: 700; }
.avrl-row.sl    span:last-child { color: #ef4444; }
.avrl-row.t1    span:last-child { color: #86efac; }
.avrl-row.t2    span:last-child { color: #4ade80; }
.avrl-row.t3    span:last-child { color: #22c55e; font-weight: 600; }
.avrl-row.pos   span:last-child { color: #2dd4bf; }

.avr-thesis {
  padding: 10px 14px; border-bottom: 1px solid #111; flex-shrink: 0;
  font-size: 13px; color: #999; line-height: 1.7;
}
.avrt-title { font-size: 10px; color: #7d8590; font-weight: 700; letter-spacing: 1px; margin-bottom: 5px; }
.avrt-bull  { margin-top: 6px; color: #4ade80; font-size: 11px; line-height: 1.4; }
.avrt-bear  { margin-top: 4px; color: #f87171; font-size: 11px; line-height: 1.4; }

.avr-debate {
  padding: 10px 14px; border-bottom: 1px solid #111; flex-shrink: 0;
}
.avrd-header {
  display: flex; align-items: center; justify-content: space-between;
  margin-bottom: 8px;
}
.avrd-title {
  font-size: 10px; color: #7d8590; font-weight: 700; letter-spacing: 1px;
}
.avrd-counts { display: flex; gap: 8px; font-size: 11px; }
.avrd-bull-count { color: #4ade80; }
.avrd-bear-count { color: #f87171; }
.avrd-side {
  border-radius: 6px; padding: 9px 11px; margin-bottom: 6px;
  font-size: 12px; line-height: 1.65;
}
.avrd-side:last-child { margin-bottom: 0; }
.avrd-bull { background: rgba(74,222,128,0.06); border: 1px solid rgba(74,222,128,0.15); }
.avrd-bear { background: rgba(248,113,113,0.06); border: 1px solid rgba(248,113,113,0.15); }
.avrd-side-label {
  font-size: 10px; font-weight: 700; letter-spacing: 0.8px; margin-bottom: 5px;
}
.avrd-bull .avrd-side-label { color: #4ade80; }
.avrd-bear .avrd-side-label { color: #f87171; }
.avrd-side-text { color: #8b949e; white-space: pre-wrap; }

.avr-agents-header {
  display: flex; align-items: center; justify-content: space-between;
  padding: 6px 14px; border-bottom: 1px solid #111;
  font-size: 10px; color: #7d8590; font-weight: 700; letter-spacing: 1px;
  flex-shrink: 0;
}
.avra-count { color: #6e7681; }

.avr-agents-list { flex: none; }

.avra-card {
  padding: 9px 14px; border-bottom: 1px solid #21262d;
  cursor: pointer; transition: background 0.1s;
}
.avra-card:hover { background: #141414; }
.avra-top  { display: flex; align-items: center; gap: 6px; margin-bottom: 3px; }
.avra-verdict { font-size: 11px; font-weight: 800; font-family: monospace; }
.avra-conf    { font-size: 11px; color: #7d8590; }
.avra-name    { font-size: 13px; color: #cdd9e5; font-weight: 600; flex: 1; }
.avra-firm    { font-size: 11px; color: #7d8590; margin-bottom: 3px; }
.avra-reason  { font-size: 13px; color: #777; line-height: 1.55; }
.avra-findings { margin-top: 6px; font-size: 12px; color: #8b949e; line-height: 1.6; }
.avra-levels-row { margin-top: 4px; font-family: monospace; color: #2dd4bf; font-size: 10px; }

.avr-risks { padding: 10px 14px; flex-shrink: 0; }
.avrr-title { font-size: 11px; color: #f87171; font-weight: 700; letter-spacing: 1px; margin-bottom: 6px; }
.avrr-item  { font-size: 13px; color: #777; margin-bottom: 4px; line-height: 1.5; }

/* verdict color: av status bar pill + report header */
.vc-sbuy .av-verdict-pill, .vc-sbuy .avrv-label, .vc-sbuy .avra-verdict { color: #22c55e; border-color: #22c55e40; }
.vc-buy  .av-verdict-pill, .vc-buy  .avrv-label, .vc-buy  .avra-verdict { color: #86efac; border-color: #86efac40; }
.vc-hold .av-verdict-pill, .vc-hold .avrv-label, .vc-hold .avra-verdict { color: #fbbf24; border-color: #fbbf2440; }
.vc-sell .av-verdict-pill, .vc-sell .avrv-label, .vc-sell .avra-verdict { color: #f97316; border-color: #f9731640; }
.vc-ssell .av-verdict-pill, .vc-ssell .avrv-label, .vc-ssell .avra-verdict { color: #ef4444; border-color: #ef444440; }

.panel-header {
  display: flex;
  align-items: center;
  gap: 8px;
  padding: 7px 12px;
  border-bottom: 1px solid #30363d;
  flex-shrink: 0;
}

.panel-title {
  font-size: 11px;
  font-family: monospace;
  color: #7d8590;
  letter-spacing: 1px;
  font-weight: 600;
}

.panel-sub {
  font-size: 12px;
  color: #2dd4bf;
  font-family: monospace;
  font-weight: 700;
}

.panel-btn {
  height: 24px; padding: 0 10px;
  background: #1a2820; border: 1px solid #00d4a855;
  border-radius: 3px; color: #2dd4bf; font-size: 11px;
  cursor: pointer; font-family: monospace;
  display: flex; align-items: center; gap: 4px;
  margin-left: auto;
}
.panel-btn:hover:not(:disabled) { border-color: #2dd4bf; background: #00d4a820; }
.panel-btn:disabled { opacity: 0.4; cursor: default; }

.panel-btn-ghost {
  height: 24px; width: 24px;
  background: transparent; border: 1px solid #3a404a;
  border-radius: 3px; color: #7d8590; font-size: 13px;
  cursor: pointer;
}
.panel-btn-ghost:hover { color: #f85149; border-color: #f85149; }

.panel-empty, .panel-loading {
  flex: 1; display: flex; align-items: center; justify-content: center;
  color: #333; font-size: 12px; gap: 8px;
}

/* ── Graph ───────────────────────────────────────────────────────────────── */
.graph-wrap {
  flex: 1; position: relative; overflow: hidden;
}
.graph-svg { width: 100%; height: 100%; display: block; }

.graph-legend {
  display: flex; flex-wrap: wrap; gap: 6px;
  padding: 4px 10px; border-top: 1px solid #30363d; flex-shrink: 0;
}
.gl-item { display: flex; align-items: center; gap: 4px; font-size: 10px; color: #7d8590; font-family: monospace; }
.gl-dot  { width: 8px; height: 8px; border-radius: 50%; flex-shrink: 0; }

/* ── Trade Levels ────────────────────────────────────────────────────────── */
.levels-body { flex: 1; display: flex; flex-direction: column; padding: 8px 12px; gap: 6px; overflow-y: auto; }

.levels-bias {
  font-size: 12px; font-family: monospace; font-weight: 700;
  padding: 3px 8px; border-radius: 3px; width: fit-content;
}
.levels-bias.bullish { color: #2dd4bf; background: #00d4a815; }
.levels-bias.bearish { color: #f85149; background: #ff475715; }
.levels-bias.neutral { color: #adbac7;    background: #88888815; }

.level-row {
  display: flex; flex-direction: column; gap: 4px; flex: 1;
}

.level-item {
  display: flex; align-items: center; gap: 8px;
  padding: 5px 8px; border-radius: 4px; border-left: 3px solid transparent;
}
.level-item.t3    { background: #00d4a808; border-left-color: #2dd4bf40; }
.level-item.t2    { background: #00d4a810; border-left-color: #2dd4bf70; }
.level-item.t1    { background: #00d4a818; border-left-color: #2dd4bf; }
.level-item.entry { background: #3d9eff15; border-left-color: #3d9eff; }
.level-item.sl    { background: #ff475715; border-left-color: #f85149; }

.li-label {
  font-size: 9px; font-family: monospace; font-weight: 700; color: #7d8590;
  width: 70px; flex-shrink: 0; letter-spacing: 0.5px;
}
.level-item.t3 .li-label, .level-item.t2 .li-label, .level-item.t1 .li-label { color: #2dd4bf; }
.level-item.entry .li-label { color: #3d9eff; }
.level-item.sl .li-label    { color: #f85149; }

.li-price { font-size: 14px; font-family: 'JetBrains Mono', monospace; font-weight: 700; color: #eee; }
.li-rr  { font-size: 11px; font-family: monospace; color: #7d8590; margin-left: auto; }
.li-sub { font-size: 10px; color: #6e7681; font-family: monospace; margin-left: auto; text-align: right; max-width: 140px; }

.levels-footer { font-size: 11px; color: #6e7681; font-family: monospace; padding-top: 4px; border-top: 1px solid #30363d; }
.levels-footer b { color: #adbac7; }

/* ── Wallet ──────────────────────────────────────────────────────────────── */
.wallet-value { font-size: 14px; font-family: 'JetBrains Mono', monospace; font-weight: 700; color: #eee; }
.wallet-pnl   { font-size: 11px; font-weight: 600; margin-left: 6px; }

.wallet-stats {
  display: flex; gap: 0; border-bottom: 1px solid #30363d; flex-shrink: 0;
}
.ws-item {
  flex: 1; padding: 6px 8px; text-align: center; border-right: 1px solid #141414;
}
.ws-item:last-child { border-right: none; }
.ws-val   { font-size: 14px; font-family: monospace; font-weight: 700; color: #eee; }
.ws-label { font-size: 9px; color: #6e7681; font-family: monospace; margin-top: 1px; }

.wallet-positions, .wallet-trades {
  flex-shrink: 0; overflow-y: auto; max-height: 110px;
}
.wp-header { font-size: 9px; color: #6e7681; font-family: monospace; letter-spacing: 1px; padding: 4px 10px 2px; }

.wp-row {
  display: flex; align-items: center; gap: 6px;
  padding: 4px 10px; border-bottom: 1px solid #141414; font-size: 11px;
}
.wp-ticker { font-family: monospace; font-weight: 700; color: #cdd9e5; width: 45px; }
.wp-qty    { color: #7d8590; font-family: monospace; }
.wp-entry  { color: #8b949e; font-family: monospace; }
.wp-pnl    { font-family: monospace; font-weight: 600; margin-left: auto; }
.wp-sl     { font-size: 10px; color: #f8514980; font-family: monospace; }

.wt-row {
  display: flex; align-items: center; gap: 6px;
  padding: 3px 10px; border-bottom: 1px solid #21262d; font-size: 11px;
}
.wt-result { font-size: 9px; font-family: monospace; font-weight: 700; width: 32px; }
.wt-ticker { font-family: monospace; font-weight: 600; color: #adbac7; }
.wt-detail { color: #7d8590; font-family: monospace; font-size: 10px; }
.wt-pnl    { font-family: monospace; font-weight: 600; margin-left: auto; }

.manual-trade-row {
  display: flex; align-items: center; gap: 6px;
  padding: 6px 10px; border-bottom: 1px solid #30363d;
}
.mt-qty {
  width: 60px; background: #1c2128; border: 1px solid #3a404a; color: #cdd9e5;
  border-radius: 3px; padding: 4px 6px; font-size: 12px; font-family: monospace;
  text-align: center;
}
.mt-buy, .mt-sell {
  flex: 1; padding: 5px 0; border-radius: 3px; font-size: 11px;
  font-weight: 700; letter-spacing: 1px; cursor: pointer; border: none;
}
.mt-buy  { background: #0e3d2e; color: #26a69a; }
.mt-sell { background: #3d1212; color: #ef5350; }
.mt-buy:hover:not(:disabled)  { background: #26a69a; color: #000; }
.mt-sell:hover:not(:disabled) { background: #ef5350; color: #000; }
.mt-buy:disabled, .mt-sell:disabled { opacity: 0.4; cursor: not-allowed; }

.ai-trade-result {
  margin: 8px 10px 4px;
  border-radius: 4px; padding: 8px 10px;
  font-family: monospace; font-size: 11px;
  display: flex; flex-direction: column; gap: 3px;
}
.atr-buy  { background: #0d2a1e; border: 1px solid #26a69a; color: #26a69a; }
.atr-sell { background: #2d1010; border: 1px solid #ef5350; color: #ef5350; }
.atr-hold { background: #21262d; border: 1px solid #555;    color: #adbac7; }
.atr-action { font-size: 13px; font-weight: 700; letter-spacing: 1px; }
.atr-reason { font-size: 10px; opacity: 0.85; line-height: 1.4; }

/* ── Intraday Signal Panel ───────────────────────────────────────────────── */
.signal-run-btn { background: #1a2820; border-color: #2dd4bf; color: #2dd4bf; }
.signal-run-btn:hover:not(:disabled) { background: #00d4a8; color: #000; }

.sig-body { display: flex; flex-direction: column; gap: 7px; padding: 8px 10px; overflow-y: auto; flex: 1; }

.sig-action {
  display: flex; align-items: baseline; gap: 8px;
  font-size: 28px; font-weight: 900; font-family: monospace; letter-spacing: 1px;
}
.sig-action.sig-buy  { color: #2dd4bf; }
.sig-action.sig-sell { color: #f85149; }
.sig-action.sig-hold { color: #fbbf24; }
.sig-score { font-size: 13px; font-weight: 600; color: #8b949e; }

.sig-price { font-size: 16px; font-weight: 700; font-family: monospace; color: #cdd9e5; margin-top: -4px; }

.sig-indicators { display: flex; gap: 6px; flex-wrap: wrap; }
.sig-ind { background: #141414; border: 1px solid #222; border-radius: 4px; padding: 3px 7px; display: flex; gap: 5px; align-items: center; }
.si-label { font-size: 9px; color: #6e7681; font-family: monospace; }
.si-val   { font-size: 11px; font-weight: 700; font-family: monospace; color: #adbac7; }
.si-val.up { color: #2dd4bf; }
.si-val.dn { color: #f85149; }

.sig-levels { display: flex; gap: 6px; flex-wrap: wrap; }
.sl-item { font-family: monospace; font-size: 11px; font-weight: 600; padding: 2px 6px; border-radius: 3px; background: #141414; }
.sl-item.entry { color: #2dd4bf; border: 1px solid #00d4a822; }
.sl-item.sl    { color: #f85149; border: 1px solid #ff475722; }
.sl-item.t1    { color: #86efac; border: 1px solid #86efac22; }
.sl-item.t2    { color: #4ade80; border: 1px solid #4ade8022; }

.sig-reasons { display: flex; flex-direction: column; gap: 2px; }
.sig-reason  { font-size: 10px; color: #7d8590; font-family: monospace; }

/* ── Feed transition ─────────────────────────────────────────────────────── */
.feed-enter-active { transition: all 0.3s ease; }
.feed-enter-from   { opacity: 0; transform: translateY(-10px); }

/* ── Tooltip ─────────────────────────────────────────────────────────────── */
.c-tooltip {
  position: fixed;
  background: #1c2128;
  border: 1px solid #3a404a;
  padding: 8px 10px;
  border-radius: 5px;
  font-size: 11px;
  pointer-events: none;
  z-index: 9999;
  min-width: 160px;
  max-width: 240px;
}

.tt-date { font-size: 10px; color: #7d8590; margin-bottom: 4px; font-family: monospace; }
.tt-row  { display: flex; justify-content: space-between; gap: 10px; color: #adbac7; line-height: 1.6; }
.tt-row span:last-child { color: #cdd9e5; font-family: monospace; }
.tt-pattern { margin-top: 6px; padding-top: 6px; border-top: 1px solid #2a2a2a; display: flex; flex-direction: column; gap: 3px; }
.tt-pat-name { font-size: 11px; font-weight: 700; }
.tt-pat-desc { font-size: 10px; color: #94a3b8; line-height: 1.5; white-space: normal; }

/* ── Feed card tooltip ───────────────────────────────────────────────────── */
.fc-tooltip {
  position: fixed;
  z-index: 9999;
  pointer-events: none;
  background: #1c2128;
  border: 1px solid #3a404a;
  border-radius: 6px;
  padding: 10px 12px;
  max-width: 300px;
  box-shadow: 0 4px 16px rgba(0,0,0,0.5);
}
.fc-tt-title { font-size: 12px; color: #cdd9e5; line-height: 1.5; word-break: break-word; }
.fc-tt-meta  { margin-top: 6px; font-size: 11px; color: #8b949e; font-family: monospace; word-break: break-word; }

/* ── Light mode overrides ─────────────────────────────────────────────────── */
.light .topbar,
.light .watchlist,
.light .right-sidebar,
.light .rs-panel,
.light .rs-header,
.light .feed-panel,
.light .feed-panel-header,
.light .analysis-view { background: var(--bg1) !important; border-color: var(--bd1) !important; }
.light .rs-wallet-summary { background: var(--bg2) !important; }
.light .wl-row,
.light .scan-buy-row,
.light .scan-sell-row { background: var(--bg1); border-color: var(--bd1); }
.light .wl-row:hover { background: var(--bg2) !important; }
.light .wl-row.selected { background: var(--bg3) !important; border-left-color: var(--acc) !important; }
.light .wl-name,
.light .ch-ticker,
.light .rs-title,
.light .wl-names,
.light .bt-name,
.light .rws-val { color: var(--tx0) !important; }
.light .wl-exch,
.light .rs-empty,
.light .ch-company,
.light .bt-trades,
.light .bt-sep,
.light .rss-l { color: var(--tx2) !important; }
.light .ticker-input,
.light .rs-qty { background: var(--bg2) !important; border-color: var(--bd1) !important; color: var(--tx0) !important; }
.light .feed-card { background: var(--bg2) !important; border-color: var(--bd1) !important; }
.light .fc-text { color: var(--tx1) !important; }
.light .brand { color: var(--tx0) !important; }
.light .brand-sub { color: var(--tx2) !important; }
.light .ac-btn,
.light .cf-btn,
.light .tf-btn { background: var(--bg2) !important; color: var(--tx2) !important; border-color: var(--bd1) !important; }
.light .ac-btn.active,
.light .cf-btn.active,
.light .tf-btn.active { background: var(--acc) !important; color: #fff !important; }
.light .chart-area { background: var(--bg1) !important; }
.light .rs-stats { border-color: var(--bd1) !important; }
.light .rss-v { color: var(--tx0) !important; }
.light .rs-pos-row,
.light .rs-pos-hdr { color: var(--tx2) !important; }
.light .rsp-ticker { color: var(--tx0) !important; }
.light .ss-item { background: var(--bg1); border-color: var(--bd1); }
.light .ss-item:hover { background: var(--bg2) !important; }
.light .search-suggestions { background: var(--bg1) !important; border-color: var(--bd1) !important; }

/* Pattern tooltip */
.pat-tooltip {
  position: absolute; pointer-events: none; z-index: 200;
  background: var(--bg2); border: 1px solid var(--bd1);
  border-radius: 6px; padding: 7px 10px; font-size: 11px;
  font-family: monospace; box-shadow: 0 4px 16px #0006;
  max-width: 220px; line-height: 1.5;
}
.pat-tooltip b { color: var(--acc); display: block; margin-bottom: 2px; font-size: 12px; }

/* Theme toggle button */
.theme-btn {
  background: none; border: 1px solid var(--bd1); color: var(--tx2);
  border-radius: 4px; padding: 3px 8px; font-size: 12px; cursor: pointer;
}
.theme-btn:hover { border-color: var(--acc); color: var(--acc); }

/* ── Portfolio Allocator ──────────────────────────────────────────────────── */
.port-panel { display: flex; flex-direction: column; height: calc(100vh - 48px); overflow-y: auto; background: var(--bg0); }

/* Config card */
.port-config-card { max-width: 760px; margin: 32px auto; padding: 0 16px; width: 100%; }
.pcc-section { margin-bottom: 28px; }
.pcc-label { font-size: 13px; font-weight: 600; color: var(--tx0); margin-bottom: 10px; }

/* Denomination buttons */
.pcc-denom-row { display: flex; flex-wrap: wrap; gap: 8px; }
.pcc-denom-btn { background: var(--bg1); border: 1px solid var(--bd); color: var(--tx1);
                 border-radius: 6px; padding: 7px 14px; font-size: 13px; cursor: pointer;
                 transition: all 0.15s; font-weight: 500; }
.pcc-denom-btn:hover { border-color: #6366f1; color: var(--tx0); }
.pcc-denom-btn.active { background: rgba(99,102,241,0.2); border-color: #6366f1; color: #a5b4fc; font-weight: 700; }
.pcc-custom-row { display: flex; align-items: center; gap: 8px; margin-top: 10px; }
.pcc-rupee { font-size: 18px; color: var(--tx1); }
.pcc-custom-input { background: var(--bg1); border: 1px solid #6366f1; color: var(--tx0); border-radius: 6px;
                    padding: 8px 12px; font-size: 15px; width: 180px; outline: none; }
.pcc-custom-hint { font-size: 12px; color: #a5b4fc; }
.pcc-capital-display { display: flex; align-items: baseline; gap: 10px; margin-top: 8px; }
.pcc-amt { font-size: 22px; font-weight: 700; color: var(--tx0); }
.pcc-amt-words { font-size: 13px; color: var(--tx1); }

/* Timeline */
.pcc-timeline-row { display: flex; flex-wrap: wrap; gap: 8px; }
.pcc-hz-btn { background: var(--bg1); border: 1px solid var(--bd); color: var(--tx1);
              border-radius: 8px; padding: 8px 14px; cursor: pointer; transition: all 0.15s;
              display: flex; flex-direction: column; align-items: center; gap: 2px; min-width: 80px; }
.pcc-hz-btn:hover { border-color: #6366f1; }
.pcc-hz-btn.active { background: rgba(99,102,241,0.2); border-color: #6366f1; }
.pcc-hz-dur { font-size: 13px; font-weight: 700; color: var(--tx0); }
.pcc-hz-btn.active .pcc-hz-dur { color: #a5b4fc; }
.pcc-hz-desc { font-size: 10px; color: var(--tx1); white-space: nowrap; }

/* Scope selector */
.pcc-scope-row { display: flex; flex-wrap: wrap; gap: 8px; }
.pcc-scope-btn { background: var(--bg1); border: 1px solid var(--bd); border-radius: 10px;
                 padding: 10px 14px; cursor: pointer; transition: all 0.15s;
                 display: flex; flex-direction: column; align-items: flex-start; gap: 3px; min-width: 110px; }
.pcc-scope-btn:hover { border-color: #6366f1; }
.pcc-scope-btn.active { background: rgba(99,102,241,0.15); border-color: #6366f1; }
.pcc-scope-icon { font-size: 20px; }
.pcc-scope-name { font-size: 12px; font-weight: 700; color: var(--tx0); }
.pcc-scope-btn.active .pcc-scope-name { color: #a5b4fc; }
.pcc-scope-sub { font-size: 10px; color: var(--tx1); line-height: 1.3; }

/* CTA */
.pcc-cta-row { display: flex; align-items: center; gap: 16px; padding-top: 8px; }
.port-run-btn { background: linear-gradient(135deg, #6366f1, #8b5cf6); border: none; color: #fff;
                border-radius: 8px; padding: 12px 28px; font-size: 14px; font-weight: 600;
                cursor: pointer; display: flex; align-items: center; gap: 8px; white-space: nowrap; }
.port-run-btn:disabled { opacity: 0.6; cursor: not-allowed; }
.pcc-cta-meta { font-size: 11px; color: var(--tx1); line-height: 1.6; }

/* Running state */
.port-running { display: flex; flex-direction: column; height: 100%; }
.prn-top { padding: 16px 20px; border-bottom: 1px solid var(--bd); display: flex; align-items: baseline; gap: 12px; }
.prn-capital { font-size: 22px; font-weight: 700; color: var(--tx0); }
.prn-meta { font-size: 12px; color: var(--tx1); }
.prn-rounds { display: flex; align-items: center; gap: 0; padding: 12px 20px; border-bottom: 1px solid var(--bd); flex-shrink: 0; }
.prn-round { display: flex; align-items: center; gap: 8px; padding: 6px 14px; border-radius: 20px;
             border: 1px solid var(--bd); background: var(--bg1); }
.prn-round.active { border-color: #6366f1; background: rgba(99,102,241,0.15); }
.prn-round.done { border-color: #22c55e; background: rgba(34,197,94,0.1); }
.prn-r-num { width: 20px; height: 20px; border-radius: 50%; background: var(--bd); display: flex; align-items: center; justify-content: center;
             font-size: 11px; font-weight: 700; color: var(--tx1); }
.prn-round.active .prn-r-num { background: #6366f1; color: #fff; }
.prn-round.done .prn-r-num { background: #22c55e; color: #fff; }
.prn-r-label { font-size: 12px; color: var(--tx1); }
.prn-round.active .prn-r-label { color: #a5b4fc; }
.prn-round.done .prn-r-label { color: #4ade80; }
.prn-arrow { flex: 1; text-align: center; color: var(--tx1); opacity: 0.4; font-size: 14px; }

/* Feed */
.port-feed { flex: 1; overflow-y: auto; padding: 8px 12px; display: flex; flex-direction: column; gap: 3px; }
.pf-item { font-size: 11px; padding: 4px 8px; border-radius: 5px; display: flex; gap: 6px; align-items: baseline; flex-wrap: wrap; }
.pf-round_start { background: rgba(99,102,241,0.15); }
.pf-round_end   { background: rgba(34,197,94,0.1); }
.pf-agent_action { background: var(--bg1); }
.pf-status { color: var(--tx1); font-style: italic; }
.pf-round-badge { font-weight: 700; color: #a5b4fc; font-size: 11px; }
.pf-agent { font-weight: 600; color: #58a6ff; white-space: nowrap; }
.pf-city  { color: var(--tx1); font-size: 10px; white-space: nowrap; }
.pf-msg   { color: var(--tx0); flex: 1; }

/* Results */
.port-results { display: flex; flex-direction: column; height: 100%; overflow: hidden; }
.prr-topbar { display: flex; align-items: center; justify-content: space-between;
              padding: 10px 16px; border-bottom: 1px solid var(--bd); flex-shrink: 0; }
.prr-meta { display: flex; align-items: center; gap: 6px; font-size: 12px; color: var(--tx1); flex-wrap: wrap; }
.prr-capital { font-size: 14px; font-weight: 700; color: var(--tx0); }
.prr-dot { color: var(--bd); }
.prr-reset-btn { background: var(--bg1); border: 1px solid var(--bd); color: var(--tx1); border-radius: 5px;
                 padding: 5px 12px; font-size: 11px; cursor: pointer; transition: all 0.15s; white-space: nowrap; }
.prr-reset-btn:hover { border-color: #6366f1; color: var(--tx0); }
.prr-summary { padding: 10px 16px; font-size: 12px; color: var(--tx1); font-style: italic; border-bottom: 1px solid var(--bd); flex-shrink: 0; }

/* Stats */
.port-stats { display: flex; gap: 0; border-bottom: 1px solid var(--bd); flex-shrink: 0; }
.pst-i { flex: 1; padding: 8px 12px; border-right: 1px solid var(--bd); text-align: center; }
.pst-i:last-child { border-right: none; }
.pst-v { font-size: 15px; font-weight: 600; color: var(--tx0); }
.pst-l { font-size: 10px; color: var(--tx1); text-transform: uppercase; margin-top: 2px; }

/* Table */
.port-table-wrap { flex: 1; overflow-y: auto; padding: 12px; }
.port-table { width: 100%; border-collapse: collapse; font-size: 12px; margin-bottom: 16px; }
.port-table th { background: var(--bg1); color: var(--tx1); padding: 6px 8px; text-align: left;
                 font-size: 10px; text-transform: uppercase; letter-spacing: 0.05em;
                 border-bottom: 1px solid var(--bd); position: sticky; top: 0; z-index: 1; }
.port-table td { padding: 7px 8px; border-bottom: 1px solid var(--bd); color: var(--tx0); }
.port-row:hover td { background: var(--bg1); }
.port-row-cash td { color: var(--tx1); font-style: italic; }
.port-ticker { font-weight: 600; color: #58a6ff; }
.port-ac-tag { padding: 2px 6px; border-radius: 3px; font-size: 10px; font-weight: 600; }
.pac-large-cap    { background: rgba(99,102,241,0.2); color: #a5b4fc; }
.pac-mid-cap      { background: rgba(16,185,129,0.2); color: #6ee7b7; }
.pac-etf-index    { background: rgba(245,158,11,0.2); color: #fcd34d; }
.pac-commodity    { background: rgba(239,68,68,0.2); color: #fca5a5; }
.pac-reit-invit   { background: rgba(6,182,212,0.2); color: #67e8f9; }
.port-alloc-bar-wrap { display: flex; align-items: center; gap: 6px; }
.port-alloc-bar { height: 4px; background: var(--acc); border-radius: 2px; max-width: 60px; min-width: 2px; }
.port-conf { padding: 1px 5px; border-radius: 3px; font-size: 10px; font-weight: 600; }
.conf-hi  { background: rgba(34,197,94,0.2); color: #4ade80; }
.conf-mid { background: rgba(245,158,11,0.2); color: #fbbf24; }
.conf-lo  { background: rgba(239,68,68,0.2);  color: #f87171; }
.port-tf { font-size: 11px; color: var(--tx1); white-space: nowrap; }
.mono { font-family: monospace; }
.port-rationale-grid { display: grid; grid-template-columns: repeat(auto-fill, minmax(260px, 1fr)); gap: 10px; margin-top: 4px; }
.port-rat-card { background: var(--bg1); border: 1px solid var(--bd); border-radius: 6px; padding: 10px; }
.prc-header { display: flex; align-items: center; gap: 8px; margin-bottom: 6px; font-size: 12px; }
.prc-agent { margin-left: auto; font-size: 10px; color: var(--tx1); }
.prc-body { font-size: 11px; color: var(--tx1); line-height: 1.5; }
.port-error { padding: 16px 20px; color: #f87171; font-size: 13px; display: flex; align-items: center; gap: 12px; }
</style>
