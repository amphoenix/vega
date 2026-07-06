<template>
  <div class="rs-panel rs-live">
    <div class="rs-header hdr-live">
      <span
        class="rs-title"
        title="Tick-driven trade tickets, option chain, and per-position monitor"
        >📡 LIVE</span
      >
      <span
        class="lv-pulse"
        :class="{ 'lv-pulse-on': liveConnected }"
        :title="liveConnected ? 'tick stream connected' : 'disconnected'"
      ></span>
      <span class="lv-clock">{{ liveClock }}</span>
    </div>

    <!-- Live scanner progress bar — visible only while a scan is running -->
    <div
      v-if="foAnalysing"
      class="lv-scan-bar"
      :title="'Scanning ' + foAnalysing.ticker_clean"
    >
      <div
        class="lv-scan-bar-fill"
        :style="{
          width: (foAnalysing.index / foAnalysing.total) * 100 + '%',
        }"
      ></div>
      <span class="lv-scan-bar-txt"
        >⟳ {{ foAnalysing.ticker_clean }} — {{ foAnalysing.index }}/{{
          foAnalysing.total
        }}</span
      >
    </div>

    <!-- Empty state — chain not yet loaded AND no tickets/positions -->
    <div
      v-if="
        !liveOptionChain &&
        !chainLoading &&
        !liveTicketCards.length &&
        !Object.keys(liveStatus).length
      "
      class="lv-empty"
    >
      <div class="lv-empty-line">No live data yet</div>
      <div class="lv-empty-sub">
        <button
          class="lv-empty-btn"
          @click="triggerScan"
          :disabled="!!foAnalysing"
        >
          {{ foAnalysing ? "Scanning…" : "🔍 Run scanner now" }}
        </button>
        <div
          v-if="foScannerState?.last_scan"
          class="lv-scan-foot"
        >
          last scan: {{ fmtTime(foScannerState.last_scan) }}
          <span v-if="foScannerState.trades_placed?.length">
            · 🟢 {{ foScannerState.trades_placed.length }} trades</span
          >
        </div>
      </div>
    </div>

    <!-- ═ Trade Ticket cards — one per index, re-pricing independently on each tick -->
    <div
      v-for="liveTicket in liveTicketCards"
      :key="liveTicket._underlying"
      :class="[
        'lv-ticket',
        'lv-ticket-' + (liveTicket.option_type === 'CE' ? 'ce' : 'pe'),
      ]"
    >
      <!-- Header: contract symbol, bias, expiry -->
      <div class="lv-ticket-head">
        <span
          class="lv-ticket-sym"
          :title="'Contract symbol: ' + liveTicket.trading_symbol"
          >{{ liveTicket.display_symbol || liveTicket.trading_symbol }}</span
        >
        <span
          :class="[
            'lv-ticket-bias',
            'lv-bias-' + liveTicket.bias?.toLowerCase(),
          ]"
          :title="TT.bias"
          >{{ liveTicket.bias }} {{ liveTicket.option_type }}</span
        >
        <span class="lv-ticket-exp" :title="TT.dte + ' · ' + TT.lot"
          >{{ liveTicket.expiry }} · {{ liveTicket.days_to_expiry }}DTE ·
          lot {{ liveTicket.lot_size }}</span
        >
      </div>

      <!-- LIVE BANNER: current premium vs entry, %change, pulse -->
      <div v-if="liveTicket.live" class="lv-ticket-live">
        <div class="lv-ticket-live-row">
          <span class="lv-live-label" :title="TT.premium_now">
            <span
              class="lv-tick-dot"
              :class="{ 'lv-tick-fresh': liveTicket._tick_age_sec < 4 }"
            ></span>
            LIVE premium
          </span>
          <span class="lv-live-prem"
            >₹{{ liveTicket.live.premium_now }}</span
          >
          <span
            class="lv-tick-age"
            :title="'Time since last underlying tick. Ticket re-prices every 2s. Age &gt; 8s usually means market is closed or stream stalled.'"
          >
            {{
              liveTicket._tick_age_sec
                ? liveTicket._tick_age_sec + "s ago"
                : "waiting…"
            }}
          </span>
          <span
            :class="[
              'lv-live-chg',
              liveTicket.live.pct_from_entry >= 0 ? 'up' : 'dn',
            ]"
            :title="
              'P&amp;L vs entry per unit. Multiply by lot (' +
              liveTicket.lot_size +
              ') for per-lot ₹.'
            "
          >
            {{ liveTicket.live.pct_from_entry >= 0 ? "+" : ""
            }}{{ liveTicket.live.pct_from_entry }}% ·
            {{ liveTicket.live.pnl_per_lot >= 0 ? "+" : "" }}₹{{
              liveTicket.live.pnl_per_lot?.toLocaleString("en")
            }}/lot
          </span>
        </div>
        <div class="lv-ticket-live-row lv-ticket-live-sub">
          <span
            :class="[
              'lv-mny',
              'lv-mny-' + (liveTicket.live.moneyness || '').toLowerCase(),
            ]"
            :title="liveTicket.live.moneyness === 'ITM' ? TT.itm : TT.otm"
            >{{ liveTicket.live.moneyness }}</span
          >
          <span
            :title="
              'Spot ₹' +
              liveTicket.live.spot_now +
              ' vs Strike ' +
              liveTicket.strike +
              ' — points away from being ATM'
            "
          >
            {{ Math.abs(liveTicket.live.dist_to_strike) }} pts
            {{
              liveTicket.live.dist_to_strike >= 0 ? "below" : "above"
            }}
            strike
          </span>
          <span
            :title="'How far the live premium is from each exit level. Negative % = level is below current premium.'"
          >
            → T1 {{ liveTicket.live.pct_to_t1 >= 0 ? "+" : ""
            }}{{ liveTicket.live.pct_to_t1 }}% · SL
            {{ liveTicket.live.pct_to_sl }}%
          </span>
        </div>
      </div>

      <!-- Static plan: spot at signal time, strike, entry, SL, T1, T2 -->
      <div class="lv-ticket-grid">
        <div
          class="lv-tk-cell"
          :title="
            TT.spot + ' Snapshot at the moment the signal was generated.'
          "
        >
          <div class="lv-lab">Spot</div>
          <div class="lv-val">
            ₹{{ liveTicket.spot?.toLocaleString("en") }}
          </div>
        </div>
        <div class="lv-tk-cell" :title="TT.strike">
          <div class="lv-lab">Strike</div>
          <div class="lv-val">{{ liveTicket.strike }}</div>
        </div>
        <div class="lv-tk-cell" :title="TT.entry">
          <div class="lv-lab">Entry</div>
          <div class="lv-val lv-entry">
            ₹{{ liveTicket.entry?.expected_premium_inr }}
          </div>
        </div>
        <div class="lv-tk-cell" :title="TT.sl">
          <div class="lv-lab">SL</div>
          <div class="lv-val lv-sl">
            ₹{{ liveTicket.exit?.stop_loss_inr }}
          </div>
        </div>
        <div class="lv-tk-cell" :title="TT.t1">
          <div class="lv-lab">T1</div>
          <div class="lv-val lv-t1">
            ₹{{ liveTicket.exit?.target_1_inr }}
          </div>
        </div>
        <div class="lv-tk-cell" :title="TT.t2">
          <div class="lv-lab">T2</div>
          <div class="lv-val lv-t2">
            ₹{{ liveTicket.exit?.target_2_inr }}
          </div>
        </div>
      </div>

      <!-- LIVE Greeks row (recomputes on every tick) -->
      <div class="lv-ticket-greeks">
        <span :title="TT.delta"
          >Δ
          <b>{{
            (liveTicket.live?.delta ?? liveTicket.greeks?.delta)?.toFixed(
              2,
            )
          }}</b></span
        >
        <span :title="TT.theta"
          >θ/d
          <b
            >₹{{
              (
                liveTicket.live?.theta_per_day ??
                liveTicket.greeks?.theta_per_day
              )?.toFixed(2)
            }}</b
          ></span
        >
        <span :title="TT.iv"
          >IV
          <b
            >{{
              (
                (liveTicket.live?.iv_used ??
                  liveTicket.greeks?.iv_used ??
                  0) * 100
              ).toFixed(1)
            }}%</b
          ></span
        >
        <span :title="TT.be"
          >BE <b>{{ liveTicket.risk?.breakeven_spot }}</b></span
        >
        <span class="lv-loss" :title="TT.max_loss"
          >max -₹{{
            liveTicket.risk?.max_loss_inr?.toLocaleString("en", {
              maximumFractionDigits: 0,
            })
          }}</span
        >
        <span
          v-if="liveTicket.risk?.risk_reward_ratio !== undefined"
          :class="[
            'lv-rr',
            liveTicket.risk.risk_reward_ratio >= 1
              ? 'good'
              : liveTicket.risk.risk_reward_ratio >= 0.5
                ? 'ok'
                : 'bad',
          ]"
          :title="TT.rr"
          >R:R 1:{{ liveTicket.risk.risk_reward_ratio }}</span
        >
      </div>

      <!-- Theta-warning banner when system flagged the trade as theta-trap -->
      <div
        v-if="liveTicket.risk?.theta_warning"
        class="lv-theta-warn"
        :title="TT.warning"
      >
        ⚠️ {{ liveTicket.risk.theta_warning }}
      </div>

      <!-- Footer: timing window, order type, time-exit -->
      <div class="lv-ticket-foot">
        <span
          class="lv-window"
          :title="'Recommended entry window. After this, theta + low momentum hurt.'"
          >⏱ {{ liveTicket.entry?.window_ist }}</span
        >
        <span
          class="lv-order-type"
          :title="
            liveTicket.entry?.order_type === 'MARKET'
              ? 'Spread is tight — MARKET order is safe.'
              : 'Spread wide — use LIMIT @ mid to avoid slippage.'
          "
          >{{ liveTicket.entry?.order_type }}</span
        >
        <span
          class="lv-time-exit"
          :title="'Forced exit time — close before broker auto-square-off / theta cliff.'"
          >exit by {{ liveTicket.exit?.time_exit_ist }}</span
        >
      </div>

      <!-- Manual position tracker — "I entered this trade" -->
      <div class="lv-ticket-track">
        <button
          v-if="!isTracked(liveTicket.trading_symbol)"
          class="lv-track-btn"
          @click="trackEntered(liveTicket)"
          :title="'Pin this contract — I executed this trade in my real broker. The card stays here regardless of new AI verdicts and continues to live-reprice + fire SL/T1/T2/time-exit alerts until I click Exited.'"
        >
          ✋ I entered this
        </button>
        <span
          v-else
          class="lv-track-pinned"
          :title="'Tracking — see WATCHING section below'"
          >📌 Tracking active</span
        >
      </div>
    </div>

    <!-- ═ Auto-trading toggle + Daily P&L strip -->
    <div class="lv-daily-pnl">
      <label
        class="lv-toggle"
        :title="autoTradingEnabled ? 'Auto-trading ON — click to switch to monitor-only' : 'Monitor only — click to enable auto-trading'"
      >
        <input type="checkbox" :checked="autoTradingEnabled" @change="toggleAutoTrading" />
        <span class="lv-toggle-track">
          <span class="lv-toggle-knob"></span>
        </span>
        <span class="lv-toggle-label">{{ autoTradingEnabled ? 'Auto' : 'Monitor' }}</span>
      </label>
      <span class="lv-daily-label">Today's P&amp;L</span>
      <span :class="['lv-daily-realized', dailyPnlComputed.realized >= 0 ? 'up' : 'dn']"
        title="Realized P&L from closed trades today"
        >Realized: {{ dailyPnlComputed.realized >= 0 ? '+' : '' }}₹{{ dailyPnlComputed.realized.toFixed(0) }}</span
      >
      <span :class="['lv-daily-unrealized', dailyPnlComputed.unrealized >= 0 ? 'up' : 'dn']"
        title="Unrealized P&L from open positions"
        >Open: {{ dailyPnlComputed.unrealized >= 0 ? '+' : '' }}₹{{ dailyPnlComputed.unrealized.toFixed(0) }}</span
      >
      <span :class="['lv-daily-total', (dailyPnlComputed.realized + dailyPnlComputed.unrealized) >= 0 ? 'up' : 'dn']"
        title="Total = Realized + Unrealized"
        >Net: {{ (dailyPnlComputed.realized + dailyPnlComputed.unrealized) >= 0 ? '+' : '' }}₹{{ (dailyPnlComputed.realized + dailyPnlComputed.unrealized).toFixed(0) }}</span
      >
      <span class="lv-daily-limit"
        :title="`Daily loss limit: ₹${dailyPnlComputed.limit}. Kill switch ${dailyPnlComputed.kill_switch ? 'ACTIVE — no more trades' : 'off'}`"
        >Limit: -₹{{ dailyPnlComputed.limit }}</span
      >
      <span v-if="dailyPnlComputed.kill_switch" class="lv-daily-kill">🛑 KILL SWITCH</span>
      <button v-if="dailyPnlComputed.kill_switch" class="lv-ks-reset-btn" @click="resetSwingKillSwitch" title="Re-enable swing trading (P&L not reset)">Reset</button>
    </div>

    <!-- ═ WATCHING — open tracked positions + today's exited fills. Always rendered. -->
    <div class="lv-watch">
      <div class="lv-watch-head">
        <span class="lv-watch-title"
          >👁 WATCHING ({{ trackedCards.length }} open<span v-if="exitedCards.length"> · {{ exitedCards.length }} closed</span>)</span
        >
        <span
          :class="[
            'lv-watch-dot',
            alertsConnected ? 'lv-dot-on' : 'lv-dot-off',
          ]"
          :title="
            alertsConnected
              ? 'Server-side watcher LIVE — alerts will fire even if this tab is hidden'
              : 'Server alerts disconnected — only browser-side beeps'
          "
        ></span>
        <button
          class="lv-watch-test-sound"
          @click="_playExitAlert('past_t1')"
          :title="'Play a test beep (also unlocks audio — browser blocks beeps until first user gesture)'"
        >
          🔔 Test sound
        </button>
        <button
          v-if="notifPermission !== 'granted'"
          class="lv-watch-test-sound"
          @click="requestNotifPermission"
          :title="'Allow desktop popups so SL/T1/T2 alerts reach you even when this tab is in background'"
        >
          🔔 Enable desktop alerts
        </button>
        <span
          v-else
          class="lv-watch-sub lv-alerts-on"
          >desktop alerts ON</span
        >
        <span class="lv-watch-sub"
          >positions you entered — server watches SL/T1/T2/15:00</span
        >
      </div>

      <!-- "While you were away" panel — server-side alerts that fired while this tab was closed/hidden -->
      <div v-if="missedAlerts.length" class="lv-missed">
        <div class="lv-missed-head">
          <span>📬 While you were away ({{ missedAlerts.length }})</span>
          <button class="lv-missed-clear" @click="clearMissedAlerts">
            clear
          </button>
        </div>
        <div
          v-for="(a, i) in missedAlerts.slice(0, 5)"
          :key="i"
          class="lv-missed-row"
        >
          <span :class="'lv-missed-tag lv-missed-' + (a.status || 'alert')">{{
            (a.status || "alert").replace("_", " ").toUpperCase()
          }}</span>
          <span class="lv-missed-sym">{{ a.display_symbol || a.trading_symbol }}</span>
          <span class="lv-missed-msg">{{ a.message }}</span>
          <span class="lv-missed-time">{{
            _fmtAlertTime(a.timestamp)
          }}</span>
        </div>
      </div>

      <div
        v-for="card in trackedCards"
        :key="card.id"
        :class="['lv-watch-card', 'lv-watch-' + card.status]"
      >
        <div class="lv-watch-row">
          <span class="lv-watch-sym">{{ card.display_symbol || card.trading_symbol }}</span>
          <span class="lv-watch-qty" :title="`${card.qty} qty (${card.lot}/lot)`">{{ card.qty }} qty</span>
          <span :class="['lv-watch-badge', 'lv-watch-' + card.status]">{{
            card.statusLabel
          }}</span>
          <button
            class="lv-watch-force-exit"
            @click="forceExit(card)"
            title="Force-exit: auto-places SELL order at market NOW"
          >
            🚫 Force Exit
          </button>
          <button
            :class="[
              'lv-watch-exit',
              isExitArmed(card.id) && 'lv-watch-exit-armed',
            ]"
            @click="trackExited(card)"
            :title="
              isExitArmed(card.id)
                ? 'Click again to confirm exit'
                : 'Mark this position as exited. Click twice to confirm.'
            "
          >
            {{ isExitArmed(card.id) ? "⚠ Click again" : "✓ Exited" }}
          </button>
        </div>
        <div class="lv-watch-row lv-watch-prices">
          <span :title="'Entry premium (when you clicked I entered)'"
            >entry <b>₹{{ card.entry_premium?.toFixed(2) }}</b></span
          >
          <span
            :title="'Live premium — Black-Scholes repriced on every spot tick. Pulsing dot = live ticks flowing.'"
          >
            <span
              :class="[
                'lv-watch-tick',
                card.is_live && 'lv-watch-tick-on',
              ]"
              :title="
                card.is_live
                  ? `Tracking LIVE — last tick ${card.tick_age}s ago`
                  : 'No tick yet — server polling'
              "
            ></span>
            now
            <b :class="card.pnl_pct >= 0 ? 'up' : 'dn'"
              >₹{{ card.now_premium?.toFixed(2) }}</b
            >
          </span>
          <span
            :class="['lv-watch-pnl', card.pnl_pct >= 0 ? 'up' : 'dn']"
            :title="`P&L = (now − entry) × qty ${card.qty}. qty = lot_size(${card.lot}) × lots. CE/PE buyers — premium up = profit.`"
          >
            {{ card.pnl_inr >= 0 ? "+" : "−" }}₹{{
              Math.abs(card.pnl_inr || 0).toFixed(0)
            }}
            <small
              >({{ card.pnl_pct >= 0 ? "+" : ""
              }}{{ card.pnl_pct?.toFixed(2) }}%)</small
            >
          </span>
        </div>
        <div
          class="lv-watch-row lv-watch-levels"
          :title="'These SL/T1/T2 are FROZEN from the ticket you entered. New scanner signals do NOT update them — they always reflect your original plan.'"
        >
          <span
            class="lv-watch-sl"
            :title="'Hard stop loss (50% premium floor enforced) — frozen from entry'"
            >SL ₹{{ card.sl?.toFixed(2) }}</span
          >
          <span
            class="lv-watch-t1"
            :title="'First profit target — exit 50% partial here'"
            >T1 ₹{{ card.t1?.toFixed(2) }}</span
          >
          <span
            class="lv-watch-t2"
            :title="'Final profit target — exit remainder'"
            >T2 ₹{{ card.t2?.toFixed(2) }}</span
          >
        </div>
        <div v-if="card.alert" class="lv-watch-alert">
          ⚠ {{ card.alert }}
        </div>
      </div>

      <!-- Exited today — read-only, from the P&L ledger. Shows what was bought/sold. -->
      <div
        v-for="card in exitedCards"
        :key="card.id"
        class="lv-watch-card lv-watch-exited"
      >
        <div class="lv-watch-row">
          <span class="lv-watch-sym" :title="card.symbol">{{ card.label }}</span>
          <span class="lv-watch-qty">{{ card.qty }} qty</span>
          <span class="lv-watch-badge lv-watch-exited-badge">EXITED</span>
          <span class="lv-watch-exit-reason">{{ card.exit_reason }}</span>
        </div>
        <div class="lv-watch-row lv-watch-prices">
          <span>entry <b>₹{{ card.entry_prem?.toFixed(2) }}</b></span>
          <span>exit <b>₹{{ card.exit_prem?.toFixed(2) }}</b></span>
          <span :class="['lv-watch-pnl', card.net_pnl >= 0 ? 'up' : 'dn']">
            {{ card.net_pnl >= 0 ? "+" : "−" }}₹{{ Math.abs(card.net_pnl || 0).toFixed(0) }}
            <small>({{ card.pct >= 0 ? "+" : "" }}{{ card.pct.toFixed(1) }}%)</small>
          </span>
        </div>
      </div>

      <!-- Empty state — nothing open and nothing closed today -->
      <div
        v-if="!trackedCards.length && !exitedCards.length"
        class="lv-watch-empty"
      >
        No positions today. Auto-scanner adds them here on entry.
      </div>
    </div>

    <!-- ═ Option chain — transitional states (fetch in progress / errored) -->
    <div
      v-if="!liveOptionChain && (chainLoading || chainError)"
      class="lv-chain"
    >
      <div class="lv-chain-head">
        <span class="lv-chain-title">📊 OPTION CHAIN</span>
        <select class="lv-chain-sel" v-model="chainUnderlying">
          <option value="^NSEI">NIFTY 50</option>
          <option value="^NSEBANK">BANKNIFTY</option>
        </select>
        <span v-if="chainLoading" class="lv-chain-meta"
          ><span class="lv-spin">◐</span> loading
          {{ chainUnderlying }}…</span
        >
        <span
          v-else-if="chainError"
          class="lv-chain-meta lv-chain-err"
          :title="chainError"
          >⚠ {{ chainError }}</span
        >
      </div>
    </div>

    <!-- ═ Option chain — live-priced ladder of strikes around ATM ───── -->
    <div v-if="liveOptionChain" class="lv-chain">
      <div class="lv-chain-head">
        <span class="lv-chain-title">📊 OPTION CHAIN</span>
        <select
          class="lv-chain-sel"
          v-model="chainUnderlying"
          :title="'Pick underlying'"
        >
          <option value="^NSEI">NIFTY 50</option>
          <option value="^NSEBANK">BANKNIFTY</option>
          <option value="^BSESN">SENSEX</option>
        </select>
        <span class="lv-chain-meta">
          <span
            :class="[
              'lv-chain-dot',
              liveOptionChain.is_live && 'lv-chain-dot-on',
            ]"
            :title="
              liveOptionChain.is_live
                ? 'live tick stream connected'
                : 'no tick yet'
            "
          ></span>
          spot
          <b class="lv-spot-val"
            >₹{{ liveOptionChain.spot.toLocaleString("en-IN") }}</b
          >
          <span
            class="lv-chain-age"
            :title="'seconds since last broker tick'"
            >{{ liveOptionChain.tick_age }}s</span
          >
          · exp {{ liveOptionChain.expiry }} ({{
            liveOptionChain.dte
          }}DTE) · IV {{ liveOptionChain.iv_pct }}% · lot
          {{ liveOptionChain.lot }}
        </span>
        <!-- Sentiment / smart-money strip -->
        <div
          class="lv-chain-sentiment"
          v-if="
            liveOptionChain.pcr_oi != null || liveOptionChain.max_pain
          "
        >
          <span
            v-if="liveOptionChain.pcr_oi != null"
            :class="[
              'lv-chain-pcr',
              liveOptionChain.pcr_oi > 1.3
                ? 'pcr-bull'
                : liveOptionChain.pcr_oi < 0.7
                  ? 'pcr-bear'
                  : 'pcr-neutral',
            ]"
          >
            <span class="sm-lab">PCR <small>(Put/Call)</small></span>
            <b>{{ liveOptionChain.pcr_oi }}</b>
            <span class="sm-meaning">
              {{
                liveOptionChain.pcr_oi > 1.3
                  ? "→ put-heavy, contrarian BULLISH"
                  : liveOptionChain.pcr_oi < 0.7
                    ? "→ call-heavy, contrarian BEARISH"
                    : "→ balanced sentiment"
              }}
            </span>
          </span>
          <span v-if="liveOptionChain.max_pain" class="lv-chain-mp">
            <span class="sm-lab">Max Pain</span>
            <b>{{ liveOptionChain.max_pain }}</b>
            <span class="sm-meaning">
              → spot magnet near expiry ({{
                liveOptionChain.spot > liveOptionChain.max_pain
                  ? "spot above, expect drift down"
                  : liveOptionChain.spot < liveOptionChain.max_pain
                    ? "spot below, expect drift up"
                    : "at magnet"
              }})
            </span>
          </span>
          <span
            v-if="liveOptionChain.peak_oi_strike"
            class="lv-chain-peak"
          >
            <span class="sm-lab">Peak OI</span>
            <b>{{ liveOptionChain.peak_oi_strike }}</b>
            <span class="sm-meaning"
              >→ strongest support/resistance level (price likely to
              bounce off this strike)</span
            >
          </span>
        </div>
        <label class="lv-chain-filter">
          <input type="checkbox" v-model="chainBuyOnly" />
          Hide deep OTM strikes
          <span class="lv-chain-filter-hint"
            >(only show strikes with |Δ| ≥ 0.30 — i.e. real chance of
            profit, not lottery tickets)</span
          >
        </label>
      </div>
      <!-- Verdict tier legend -->
      <div class="lv-chain-legend">
        <span
          ><b class="lv-verdict-conservative">CONSERVATIVE</b> deep ITM,
          safest, lowest leverage</span
        >
        <span
          ><b class="lv-verdict-balanced">BALANCED</b> ATM, best
          risk/reward for directional bets</span
        >
        <span
          ><b class="lv-verdict-aggressive">AGGRESSIVE</b> slight OTM,
          cheap + high leverage</span
        >
        <span
          ><b class="lv-verdict-lottery">LOTTERY</b> deep OTM, theta will
          eat you alive — avoid</span
        >
        <span
          ><b class="lv-verdict-expensive">EXPENSIVE</b> too deep ITM, use
          futures instead</span
        >
      </div>
      <div class="lv-chain-grid">
        <div class="lv-chain-th">CE Δ</div>
        <div class="lv-chain-th">CE ₹</div>
        <div class="lv-chain-th lv-chain-th-strike">Strike</div>
        <div class="lv-chain-th">PE ₹</div>
        <div class="lv-chain-th">PE Δ</div>
        <template v-for="r in liveOptionChain.rows" :key="r.strike">
          <!-- Row 1 — premium + delta + AI-pick highlight -->
          <div
            :class="[
              'lv-chain-cell',
              'lv-chain-mny-' + r.ce_moneyness.toLowerCase(),
              r.ce_is_pick && 'lv-chain-pick',
            ]"
            :title="r.ce_symbol + (r.ce_is_pick ? ' (AI pick)' : '')"
          >
            {{ r.ce_delta?.toFixed(2) }}
          </div>
          <div
            :class="[
              'lv-chain-cell',
              'lv-chain-cep',
              r.ce_dir && 'lv-flash-' + r.ce_dir,
              r.ce_is_pick && 'lv-chain-pick',
            ]"
            :title="
              r.ce_symbol +
              (r.ce_is_pick ? ' — AI pick (see ticket above)' : '')
            "
          >
            <span
              v-if="r.ce_is_pick"
              class="lv-chain-pick-mark"
              title="AI-recommended strike"
              >★</span
            >
            ₹{{ r.ce_premium?.toFixed(2) ?? "—" }}
          </div>
          <div
            :class="[
              'lv-chain-cell',
              'lv-chain-strike',
              r.is_atm ? 'lv-chain-atm' : '',
            ]"
          >
            {{ r.strike.toLocaleString("en-IN") }}
          </div>
          <div
            :class="[
              'lv-chain-cell',
              'lv-chain-pep',
              r.pe_dir && 'lv-flash-' + r.pe_dir,
              r.pe_is_pick && 'lv-chain-pick',
            ]"
            :title="
              r.pe_symbol +
              (r.pe_is_pick ? ' — AI pick (see ticket above)' : '')
            "
          >
            <span
              v-if="r.pe_is_pick"
              class="lv-chain-pick-mark"
              title="AI-recommended strike"
              >★</span
            >
            ₹{{ r.pe_premium?.toFixed(2) ?? "—" }}
          </div>
          <div
            :class="[
              'lv-chain-cell',
              'lv-chain-mny-' + r.pe_moneyness.toLowerCase(),
              r.pe_is_pick && 'lv-chain-pick',
            ]"
            :title="r.pe_symbol + (r.pe_is_pick ? ' (AI pick)' : '')"
          >
            {{ r.pe_delta?.toFixed(2) }}
          </div>
          <!-- Row 2 — verdict + cost + breakeven (sub-row) -->
          <div
            :class="[
              'lv-chain-sub',
              'lv-verdict-' + r.ce_verdict.toLowerCase(),
            ]"
            :title="'Buy quality: ' + r.ce_verdict + ' (based on |Δ|)'"
          >
            {{ r.ce_verdict }}
          </div>
          <div
            class="lv-chain-sub"
            :title="
              'Cost per lot — Premium × Lot. Spot must reach ₹' +
              r.ce_be +
              ' to break even.'
            "
          >
            ₹{{ r.ce_cost?.toLocaleString("en-IN") ?? "—"
            }}<span class="lv-chain-be">
              · BE {{ r.ce_be?.toLocaleString("en-IN") }}</span
            >
          </div>
          <div class="lv-chain-sub lv-chain-sub-mid"></div>
          <div
            class="lv-chain-sub"
            :title="
              'Cost per lot. Spot must drop to ₹' +
              r.pe_be +
              ' to break even.'
            "
          >
            ₹{{ r.pe_cost?.toLocaleString("en-IN") ?? "—"
            }}<span class="lv-chain-be">
              · BE {{ r.pe_be?.toLocaleString("en-IN") }}</span
            >
          </div>
          <div
            :class="[
              'lv-chain-sub',
              'lv-verdict-' + r.pe_verdict.toLowerCase(),
            ]"
            :title="'Buy quality: ' + r.pe_verdict + ' (based on |Δ|)'"
          >
            {{ r.pe_verdict }}
          </div>

          <!-- Row 3 — Open Interest per side + strike-level PCR -->
          <div
            class="lv-chain-oi lv-chain-oi-ce"
            :title="
              'Call OI: ' +
              (r.ce_oi?.toLocaleString('en-IN') || 0) +
              ' contracts'
            "
          >
            OI {{ fmtIndian(r.ce_oi) }}
          </div>
          <div
            class="lv-chain-oi lv-chain-oi-vol"
            v-if="r.ce_volume"
            :title="
              'CE volume today: ' + r.ce_volume.toLocaleString('en-IN')
            "
          >
            Vol {{ fmtIndian(r.ce_volume) }}
          </div>
          <div class="lv-chain-oi lv-chain-oi-vol" v-else></div>
          <div
            class="lv-chain-oi lv-chain-oi-pcr"
            :title="'PCR at this strike = PE OI / CE OI. >1 means more put open interest (support); <1 means more call interest (resistance).'"
          >
            <span v-if="r.ce_oi > 0"
              >PCR {{ (r.pe_oi / r.ce_oi).toFixed(2) }}</span
            >
          </div>
          <div
            class="lv-chain-oi lv-chain-oi-vol"
            v-if="r.pe_volume"
            :title="
              'PE volume today: ' + r.pe_volume.toLocaleString('en-IN')
            "
          >
            Vol {{ fmtIndian(r.pe_volume) }}
          </div>
          <div class="lv-chain-oi lv-chain-oi-vol" v-else></div>
          <div
            class="lv-chain-oi lv-chain-oi-pe"
            :title="
              'Put OI: ' +
              (r.pe_oi?.toLocaleString('en-IN') || 0) +
              ' contracts'
            "
          >
            OI {{ fmtIndian(r.pe_oi) }}
          </div>
        </template>
      </div>
    </div>

    <!-- ═ Per-position live tick cards -->
    <div
      v-for="(s, k) in liveStatus"
      :key="k"
      :class="['lv-pos', s.decision ? 'lv-pos-decision' : '']"
    >
      <div class="lv-pos-head">
        <span class="lv-pos-sym">{{ k }}</span>
        <span :class="['lv-pnl', (s.pnl_pct || 0) >= 0 ? 'up' : 'dn']"
          >{{ (s.pnl_pct || 0) >= 0 ? "+" : ""
          }}{{ s.pnl_pct?.toFixed(2) }}%</span
        >
        <span class="lv-pos-prem">₹{{ s.premium }}</span>
        <span class="lv-pos-hwm" v-if="s.high_water > s.avg_entry"
          >peak ₹{{ s.high_water }}</span
        >
      </div>
      <!-- Premium ladder (SL ── now ── T1 ── T2) -->
      <div class="lv-ladder">
        <div class="lv-ladder-track">
          <div
            class="lv-ladder-fill"
            :style="{
              left: ladderPct(s, 'left'),
              width: ladderPct(s, 'width'),
            }"
          ></div>
          <div
            class="lv-ladder-pin"
            :style="{ left: ladderPct(s, 'now') }"
          >
            ●
          </div>
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
        <span>IV {{ ((s.greeks.iv_used || 0) * 100).toFixed(1) }}%</span>
        <span v-if="s.trail_sl">trail ₹{{ s.trail_sl }}</span>
      </div>
      <div
        v-if="s.decision"
        class="lv-decision"
        :class="'lv-dec-' + s.decision.action.toLowerCase()"
      >
        <span v-if="s.decision.action === 'EXIT_FULL'">🔴 EXIT NOW</span>
        <span v-else-if="s.decision.action === 'EXIT_PARTIAL'"
          >🟡 SELL HALF</span
        >
        <span class="lv-dec-reason">{{ s.decision.reason }}</span>
      </div>
    </div>

  </div>
</template>

<script setup>
import { ref, computed, watch, onMounted, onUnmounted } from 'vue'
import { storeToRefs } from 'pinia'
import { useMarketStore } from '../../stores/useMarketStore'
import { useFoScannerStore } from '../../stores/useFoScannerStore'
import { useLiveTradingStore } from '../../stores/useLiveTradingStore'
import { repriceTicket, GLOSSARY } from '../../utils/blackScholes'
import { fmtIndian, fmtTime } from '../../utils/formatters'
import { getOptionChain } from '../../api/market'
import { snack } from '../../utils/snack'
import { playNotifSound, isMuted } from '../../utils/notifSound'
import { onOrderEvent, onTrackedAlert } from '../../composables/useSSE'
import { useTrackedAlerts } from '../../composables/useTrackedAlerts'

const { chartTicker } = storeToRefs(useMarketStore())
const { foAnalysing, foScannerState } = storeToRefs(useFoScannerStore())

const ltStore = useLiveTradingStore()
const {
  brokerConnected, brokerLivePrice, brokerAvailableCash,
  liveSpots, liveSpotTickAt, liveTickAges,
  liveTickets, liveStatus, liveConnected,
  trackedPositions, alertsConnected, missedAlerts,
  optionChain, chainUnderlying, chainBuyOnly, chainLoading, chainError,
} = storeToRefs(ltStore)

const TT = GLOSSARY

// ── Component-local state ──────────────────────────────────────────────────
const liveClock = ref('')
const notifPermission = ref(
  typeof Notification !== 'undefined' ? Notification.permission : 'denied',
)

let _liveClockTimer = null
let _ticketAgeTimer = null
let _audioCtx = null

const _prevTrackStatus = {}
const _trackInFlight = new Set()
const _trackExitArmed = ref(new Set())
const _chainPrevPrices = { value: {} }
const _MISSED_KEY = 'vega_missed_alerts_v1'

// Per-underlying SSE stream registry — keyed on window so HMR reloads
// kill old streams before the new module opens fresh ones.
if (typeof window !== 'undefined') {
  const prev = window.__vegaTicketStreams
  if (prev) {
    for (const k of Object.keys(prev)) {
      try { prev[k]?.close?.() } catch {}
    }
  }
  window.__vegaTicketStreams = {}
}
const _ticketStreams =
  typeof window !== 'undefined' ? window.__vegaTicketStreams : {}

// ── Stream management ──────────────────────────────────────────────────────
function _openTicketStream(symbol) {
  if (!symbol || _ticketStreams[symbol]) return
  const base = import.meta.env.VITE_API_BASE_URL || ''
  const es = new EventSource(
    `${base}/api/broker/stream/${encodeURIComponent(symbol)}`,
  )
  es.onmessage = (e) => {
    try {
      const m = JSON.parse(e.data)
      if (m.heartbeat || m.error) return
      const p = Number(m.price ?? m.ltp ?? m.last_price)
      if (p > 0) {
        liveSpots.value = { ...liveSpots.value, [symbol]: p }
        liveSpotTickAt.value = { ...liveSpotTickAt.value, [symbol]: Date.now() }
      }
    } catch {}
  }
  es.onerror = () => console.warn('Ticket stream interrupted, reconnecting…', symbol)
  _ticketStreams[symbol] = es
}

function _closeTicketStream(symbol) {
  const es = _ticketStreams[symbol]
  if (es) {
    try { es.close() } catch {}
    delete _ticketStreams[symbol]
  }
}

function _closeAllTicketStreams() {
  Object.keys(_ticketStreams).forEach(_closeTicketStream)
}


// ── Daily P&L ──────────────────────────────────────────────────────────────
const _dailyRealized = ref(0)
const _dailyKillSwitch = ref(false)
const _dailyLimit = ref(1000)
const autoTradingEnabled = ref(false)

// Today's exited swing fills — sourced from the P&L ledger so WATCHING keeps
// showing what was bought/sold even after the live position closes.
const exitedToday = ref([])

async function _loadExitedToday() {
  try {
    const base = import.meta.env.VITE_API_BASE_URL || ''
    const today = new Date().toLocaleDateString('en-CA', { timeZone: 'Asia/Kolkata' })
    const r = await fetch(`${base}/api/trade/pnl/trades?mode=swing&limit=30`).then((x) => x.json())
    // BTST records under mode='swing' too (reason prefixed "BTST:") — it has its
    // own panel, so keep the swing WATCHING swing-only.
    exitedToday.value = (r.trades || []).filter(
      (t) => t.date === today && !String(t.exit_reason || '').startsWith('BTST:'),
    )
  } catch (e) {
    console.warn('_loadExitedToday failed', e)
  }
}

// "NIFTY-JUL2026-24300-CE" → "NIFTY 24300 CALL · JUL 2026". The P&L ledger only
// stores this synthetic symbol (no expiry day), so we can't match the F&O panel's
// day-level display ("… 09 JUL …") — but this is readable. Falls back to raw.
function _fmtContract(sym) {
  if (!sym) return ''
  const m = String(sym).match(/^([A-Z&]+)-([A-Za-z]{3})(\d{2,4})-(\d+(?:\.\d+)?)-(CE|PE)$/)
  if (!m) return sym
  const [, und, mon, yr, strike, type] = m
  const yy = yr.length === 4 ? yr : '20' + yr
  return `${und} ${strike} ${type === 'CE' ? 'CALL' : 'PUT'} · ${mon.toUpperCase()} ${yy}`
}

const exitedCards = computed(() =>
  (exitedToday.value || []).map((t) => {
    try {
      return {
        id: 'ex-' + (t.id ?? t.timestamp),
        symbol: t.symbol,
        // Prefer broker display_symbol persisted at exit (has the expiry day,
        // e.g. "NIFTY 07 JUL 24350 CALL"); older rows lack it → readable fallback.
        label: t.display_symbol || _fmtContract(t.symbol),
        direction: t.direction,
        entry_prem: t.entry_prem,
        exit_prem: t.exit_prem,
        qty: t.qty,
        net_pnl: t.net_pnl,
        exit_reason: t.exit_reason || 'exit',
        pct: t.entry_prem ? ((t.exit_prem - t.entry_prem) / t.entry_prem) * 100 : 0,
      }
    } catch (e) {
      console.warn('exitedCards: skipping bad ledger row', t?.id, e)
      return null
    }
  }).filter(Boolean)
)

async function _loadDailyPnl() {
  _loadExitedToday()
  try {
    const base = import.meta.env.VITE_API_BASE_URL || ''
    const r = await fetch(`${base}/api/trade/executor/status`).then((x) => x.json())
    if (r.success) {
      _dailyRealized.value = r.data.swing_realized_pnl ?? r.data.daily_realized_pnl ?? 0
      _dailyKillSwitch.value = r.data.kill_switch_active || false
      _dailyLimit.value = r.data.daily_loss_limit_inr || 1000
      if (r.data.auto_trading_enabled !== undefined) {
        autoTradingEnabled.value = r.data.auto_trading_enabled
      }
    }
  } catch (e) {
    console.warn('_loadDailyPnl failed', e)
  }
}

async function toggleAutoTrading() {
  try {
    const base = import.meta.env.VITE_API_BASE_URL || ''
    const next = !autoTradingEnabled.value
    const r = await fetch(`${base}/api/trade/executor/auto-trading`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ enabled: next }),
    }).then((x) => x.json())
    if (r.success) {
      // Response shape is {success, data:{auto_trading_enabled}}; r.auto_trading_enabled
      // was undefined → checkbox snapped back to OFF after every toggle. Read data,
      // fall back to the value we just requested.
      autoTradingEnabled.value = r.data?.auto_trading_enabled ?? next
    }
  } catch (e) {
    console.warn('toggleAutoTrading failed', e)
  }
}

// Unrealized P&L recomputes reactively every time trackedCards change (every 3s tick)
const dailyPnlComputed = computed(() => {
  let unrealized = 0
  for (const card of trackedCards.value || []) {
    unrealized += card.pnl_inr || 0
  }
  return {
    realized: _dailyRealized.value,
    unrealized,
    kill_switch: _dailyKillSwitch.value,
    limit: _dailyLimit.value,
  }
})

// ── Tracked-position persistence ───────────────────────────────────────────
async function _loadTracked() {
  try {
    const base = import.meta.env.VITE_API_BASE_URL || ''
    const r = await fetch(`${base}/api/trade/tracked`).then((x) => x.json())
    if (r.success) trackedPositions.value = r.data || []
  } catch (e) {
    console.warn('_loadTracked failed', e)
  }
}

function isTracked(symbol) {
  if (!symbol) return false
  return trackedPositions.value.some((p) => p.ticket?.trading_symbol === symbol)
}

async function trackEntered(ticket) {
  if (!ticket?.trading_symbol) {
    alert('Cannot watch: this signal has no contract symbol. The scanner ticket build may have failed for this ticker.')
    return
  }
  if (isTracked(ticket.trading_symbol)) {
    alert(`${ticket.trading_symbol} is already in your WATCHING list.`)
    return
  }
  if (_trackInFlight.has(ticket.trading_symbol)) {
    alert(`Already adding ${ticket.trading_symbol}… give it a moment.`)
    return
  }
  _trackInFlight.add(ticket.trading_symbol)
  try {
    const base = import.meta.env.VITE_API_BASE_URL || ''
    const resp = await fetch(`${base}/api/trade/tracked`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ ticket, qty: ticket.qty || 1 }),
    })
    const r = await resp.json().catch(() => ({}))
    if (!resp.ok || !r.success) {
      alert(`Failed to add ${ticket.trading_symbol} to watching:\n${r.error || resp.statusText || 'unknown error'}`)
      return
    }
    const existing = trackedPositions.value.find((p) => p.id === r.data?.id)
    if (!existing) trackedPositions.value = [...trackedPositions.value, r.data]
  } catch (e) {
    alert(`Network error adding ${ticket.trading_symbol} to watching: ${e.message || e}`)
  } finally {
    _trackInFlight.delete(ticket.trading_symbol)
  }
}

async function trackExited(card) {
  if (!card?.id) return
  if (!_trackExitArmed.value.has(card.id)) {
    _trackExitArmed.value = new Set([..._trackExitArmed.value, card.id])
    setTimeout(() => {
      _trackExitArmed.value = new Set([..._trackExitArmed.value].filter((x) => x !== card.id))
    }, 4000)
    return
  }
  try {
    const base = import.meta.env.VITE_API_BASE_URL || ''
    const qs = new URLSearchParams({
      exit_premium: String(card.now_premium ?? ''),
      exit_reason: 'manual',
    }).toString()
    const r = await fetch(`${base}/api/trade/tracked/${card.id}?${qs}`, { method: 'DELETE' })
    if (!r.ok) {
      console.error('trackExited HTTP', r.status, await r.text().catch(() => ''))
      return
    }
    trackedPositions.value = trackedPositions.value.filter((p) => p.id !== card.id)
    _trackExitArmed.value = new Set([..._trackExitArmed.value].filter((x) => x !== card.id))
  } catch (e) {
    console.error('trackExited', e)
  }
}

function isExitArmed(id) {
  return _trackExitArmed.value.has(id)
}

async function forceExit(card) {
  if (!confirm(`🚫 Force-exit ${card.display_symbol || card.trading_symbol}?\n\nThis will place a SELL MARKET order immediately.`)) return
  const base = import.meta.env.VITE_API_BASE_URL || ''
  try {
    const r = await fetch(`${base}/api/trade/executor/force-exit/${card.id}`, { method: 'POST' })
    const data = await r.json()
    if (data.success) {
      snack.warning('Force exit executing', `${card.display_symbol || card.trading_symbol} — selling at market now`)
      // Backend runs exit in a thread — poll after short delays to catch the result
      setTimeout(() => { _loadTracked(); _loadDailyPnl(); window.dispatchEvent(new Event('vega:pnl-changed')) }, 2000)
      setTimeout(() => { _loadTracked(); _loadDailyPnl() }, 5000)
    } else {
      snack.error('Force exit failed', data.error || 'Unknown error')
    }
  } catch (e) {
    snack.error('Force exit error', e.message)
  }
}

// ── Audio alerts ───────────────────────────────────────────────────────────
function _beep(freq = 880, duration = 0.18, volume = 0.18, type = 'sine') {
  try {
    if (isMuted()) return
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
  } catch {}
}

function _playExitAlert(status) {
  if (status === 'sl_hit') {
    _beep(220, 0.25, 0.25); setTimeout(() => _beep(180, 0.25, 0.25), 200); setTimeout(() => _beep(160, 0.4, 0.25), 420)
  } else if (status === 'time_exit') {
    _beep(440, 0.2); setTimeout(() => _beep(440, 0.2), 280); setTimeout(() => _beep(440, 0.4), 560)
  } else if (status === 'past_t2') {
    _beep(880, 0.15); setTimeout(() => _beep(1175, 0.15), 160); setTimeout(() => _beep(1568, 0.35), 320)
  } else if (status === 'past_t1') {
    _beep(660, 0.15); setTimeout(() => _beep(880, 0.25), 180)
  } else if (status === 'near_sl') {
    _beep(300, 0.12, 0.12)
  } else if (status === 'near_t1') {
    _beep(700, 0.12, 0.12)
  }
}

function _maybeAlert(cards) {
  const ALERT_STATES = new Set(['sl_hit', 'time_exit', 'past_t1', 'past_t2', 'near_sl', 'near_t1'])
  const currentIds = new Set(cards.map(c => c.id))
  for (const c of cards) {
    const prev = _prevTrackStatus[c.id]
    if (c.status !== prev && ALERT_STATES.has(c.status)) _playExitAlert(c.status)
    _prevTrackStatus[c.id] = c.status
  }
  for (const id of Object.keys(_prevTrackStatus)) {
    if (!currentIds.has(id)) delete _prevTrackStatus[id]
  }
}

// ── Missed-alert localStorage ──────────────────────────────────────────────
function _loadMissedAlerts() {
  try {
    const raw = localStorage.getItem(_MISSED_KEY)
    // Drop any previously-persisted malformed rows (no status/id → bogus "ALERT")
    if (raw) missedAlerts.value = JSON.parse(raw).filter((a) => a && a.status && a.id).slice(0, 20)
  } catch {}
}

function _saveMissedAlerts() {
  try {
    localStorage.setItem(_MISSED_KEY, JSON.stringify(missedAlerts.value.slice(0, 20)))
  } catch {}
}

function clearMissedAlerts() {
  missedAlerts.value = []
  _saveMissedAlerts()
}

const _fmtAlertTime = (iso) => fmtTime(iso, { seconds: false })

// ── Desktop notifications ──────────────────────────────────────────────────
async function requestNotifPermission() {
  if (typeof Notification === 'undefined') {
    alert('This browser does not support desktop notifications.')
    return
  }
  try {
    const p = await Notification.requestPermission()
    notifPermission.value = p
    if (p === 'granted') {
      new Notification('Vega alerts enabled', {
        body: 'You will get a desktop popup on SL / T1 / T2 / 15:00 events even when this tab is hidden.',
        icon: '/favicon.ico',
      })
    }
  } catch (e) {
    console.warn('Notification.requestPermission failed', e)
  }
}

function _showDesktopNotification(payload) {
  if (notifPermission.value !== 'granted' || typeof Notification === 'undefined') return
  const titleMap = {
    sl_hit: '🔴 SL HIT — EXIT NOW',
    past_t2: '🟢 T2 HIT — Full exit',
    past_t1: '🟢 T1 HIT — Exit 50%, trail SL',
    near_sl: '🟡 NEAR SL',
    near_t1: '🔵 NEAR T1',
    time_exit: '🚨 15:00 IST — FORCE EXIT',
  }
  try {
    const n = new Notification(titleMap[payload.status] || `Alert: ${payload.status}`, {
      body: `${payload.trading_symbol}\n${payload.message || ''}\nentry ₹${(payload.entry_premium || 0).toFixed(2)} → now ₹${(payload.premium || 0).toFixed(2)} (${(payload.pnl_pct || 0) >= 0 ? '+' : ''}${(payload.pnl_pct || 0).toFixed(2)}%)`,
      icon: '/favicon.ico',
      tag: payload.id,
      requireInteraction: payload.status === 'sl_hit' || payload.status === 'time_exit',
      silent: isMuted(),
    })
    n.onclick = () => { window.focus(); n.close() }
  } catch (e) {
    console.warn('Notification show failed', e)
  }
}

// ── Server-side tracked-position watcher (SSE) ─────────────────────────────
useTrackedAlerts({
  modes: ['swing'],
  onAlert(m) {
    // Skip malformed / non-alert messages (heartbeats, connection events) —
    // they have no status/id and would render as a bogus "ALERT" row.
    if (!m || !m.status || !m.id) return
    // Missed-alerts panel
    const key = `${m.id}|${m.status}`
    const cutoff = Date.now() - 60_000
    missedAlerts.value = [
      m,
      ...missedAlerts.value.filter(
        (a) => `${a.id}|${a.status}` !== key || (a.timestamp && new Date(a.timestamp).getTime() < cutoff),
      ),
    ].slice(0, 20)
    _saveMissedAlerts()
    _showDesktopNotification(m)
    if (['sl_hit', 'past_t2', 'time_exit', 'thesis_flip'].includes(m.status)) {
      setTimeout(() => _loadTracked(), 1000)
    }
  },
})

// ── Shared SSE: non-alert events (funds, daily PnL, tracked updates) ────────
onTrackedAlert((m) => {
  if (m.type === 'alerts_connected') { alertsConnected.value = true; return }
  if (m.type === 'heartbeat') return
  if (m.type === 'funds_update') { brokerAvailableCash.value = m.available_cash ?? null; return }
  if (m.type === 'daily_pnl') {
    _dailyRealized.value = m.realized ?? 0
    _dailyKillSwitch.value = m.kill_switch ?? false
    _dailyLimit.value = m.limit ?? 1000
    return
  }
  if (m.type === 'tracked_update') {
    for (const rec of trackedPositions.value) {
      if (rec.id === m.id && rec.ticket?.exit) {
        if (m.sl != null) rec.ticket.exit.stop_loss_inr = m.sl
        if (m.t1 != null) rec.ticket.exit.target_1_inr = m.t1
        if (m.t2 != null) rec.ticket.exit.target_2_inr = m.t2
        break
      }
    }
    return
  }
})

// ── Option chain ───────────────────────────────────────────────────────────
async function _fetchOptionChain(sym) {
  optionChain.value = null
  chainError.value = ''
  chainLoading.value = true
  _chainPrevPrices.value = {}
  try {
    const res = await getOptionChain(sym, 13)
    const data = res?.data || res
    if (data && data.strikes) optionChain.value = data
    else {
      chainError.value = data?.error || 'no chain data'
    }
  } catch (e) {
    chainError.value = e?.response?.data?.error || e?.message || 'fetch failed'
    console.error('[option-chain] FETCH FAILED', sym, chainError.value)
  } finally {
    chainLoading.value = false
  }
}

const liveOptionChain = computed(() => {
  const ch = optionChain.value
  if (!ch) return null
  const sym = ch.underlying
  const spot =
    Number(liveSpots.value[sym]) ||
    (chartTicker.value === sym ? Number(brokerLivePrice.value) : 0) ||
    Number(ch.spot)
  const dte = Number(ch.days_to_expiry) || 1
  const iv = Number(ch.iv_estimate) || 0.18
  const lot = Number(ch.lot_size) || 1

  const pseudoTicket = (meta, type) => ({
    strike: meta.strike,
    days_to_expiry: dte,
    option_type: type,
    lot_size: lot,
    spot,
    greeks: { iv_used: iv },
    entry: { expected_premium_inr: 0 },
    exit: { stop_loss_inr: 0, target_1_inr: 0, target_2_inr: 0 },
  })

  const prev = _chainPrevPrices.value

  const verdictFromDelta = (d) => {
    const a = Math.abs(d ?? 0)
    if (!a) return ''
    if (a > 0.85) return 'EXPENSIVE'
    if (a >= 0.65) return 'CONSERVATIVE'
    if (a >= 0.4) return 'BALANCED'
    if (a >= 0.25) return 'AGGRESSIVE'
    return 'LOTTERY'
  }

  const aiTicket = liveTickets.value?.[sym]
  const aiStrike = aiTicket?.strike
  const aiSide = aiTicket?.option_type

  const rows = ch.strikes.map((r) => {
    const ce_live = r.ce ? repriceTicket(pseudoTicket(r.ce, 'CE'), spot) : null
    const pe_live = r.pe ? repriceTicket(pseudoTicket(r.pe, 'PE'), spot) : null
    const cep = ce_live?.premium_now ?? null
    const pep = pe_live?.premium_now ?? null
    const pcep = prev[r.strike]?.ce ?? cep
    const ppep = prev[r.strike]?.pe ?? pep
    const ceCost = cep != null ? Math.round(cep * lot) : null
    const peCost = pep != null ? Math.round(pep * lot) : null
    return {
      strike: r.strike,
      is_atm: r.strike === ch.atm_strike,
      ce_symbol: r.ce?.trading_symbol,
      pe_symbol: r.pe?.trading_symbol,
      ce_premium: cep,
      pe_premium: pep,
      ce_delta: ce_live?.delta ?? null,
      pe_delta: pe_live?.delta ?? null,
      ce_cost: ceCost,
      pe_cost: peCost,
      ce_be: cep != null ? Math.round(r.strike + cep) : null,
      pe_be: pep != null ? Math.round(r.strike - pep) : null,
      ce_verdict: verdictFromDelta(ce_live?.delta),
      pe_verdict: verdictFromDelta(pe_live?.delta),
      ce_is_pick: aiSide === 'CE' && aiStrike === r.strike,
      pe_is_pick: aiSide === 'PE' && aiStrike === r.strike,
      ce_moneyness: r.strike <= spot ? 'ITM' : 'OTM',
      pe_moneyness: r.strike >= spot ? 'ITM' : 'OTM',
      ce_dir: cep != null && pcep != null && Math.abs(cep - pcep) > 0.0005 ? (cep > pcep ? 'up' : 'dn') : '',
      pe_dir: pep != null && ppep != null && Math.abs(pep - ppep) > 0.0005 ? (pep > ppep ? 'up' : 'dn') : '',
      ce_oi: r.ce?.oi ?? 0, pe_oi: r.pe?.oi ?? 0,
      ce_volume: r.ce?.volume ?? 0, pe_volume: r.pe?.volume ?? 0,
      ce_bid: r.ce?.bid ?? null, ce_ask: r.ce?.ask ?? null,
      pe_bid: r.pe?.bid ?? null, pe_ask: r.pe?.ask ?? null,
      ce_spread: r.ce?.spread_pct ?? null, pe_spread: r.pe?.spread_pct ?? null,
      ce_ltp: r.ce?.ltp ?? null, pe_ltp: r.pe?.ltp ?? null,
    }
  })

  const filtered = chainBuyOnly.value
    ? rows.filter((r) => Math.abs(r.ce_delta || 0) >= 0.3 || Math.abs(r.pe_delta || 0) >= 0.3)
    : rows

  const snap = {}
  for (const r of rows) snap[r.strike] = { ce: r.ce_premium, pe: r.pe_premium }
  _chainPrevPrices.value = snap

  let peak_oi_strike = null, peak_oi_total = 0
  for (const r of rows) {
    const t = (r.ce_oi || 0) + (r.pe_oi || 0)
    if (t > peak_oi_total) { peak_oi_total = t; peak_oi_strike = r.strike }
  }

  return {
    underlying: sym, spot: Math.round(spot * 100) / 100,
    expiry: ch.expiry, dte,
    iv_pct: Math.round(iv * 1000) / 10, lot,
    rows: filtered,
    tick_age: liveTickAges.value[sym] || 0,
    is_live: !!liveSpots.value[sym],
    pcr_oi: ch.totals?.pcr_oi ?? null,
    pcr_volume: ch.totals?.pcr_volume ?? null,
    max_pain: ch.totals?.max_pain ?? null,
    ce_oi_total: ch.totals?.ce_oi ?? 0,
    pe_oi_total: ch.totals?.pe_oi ?? 0,
    peak_oi_strike, peak_oi_total,
  }
})

// ── Live ticket cards ──────────────────────────────────────────────────────
const liveTicketCards = computed(() => {
  // LIVE ticket cards ("✋ I entered this") removed — the FnO scanner card already
  // shows the full ticket + track button; this panel keeps only WATCHING + P&L +
  // the tick streams (which use the liveTickets store below, not this computed).
  return []
  // eslint-disable-next-line no-unreachable
  const tickets = liveTickets.value
  const out = []
  for (const [under, t] of Object.entries(tickets)) {
    if (!t) continue
    const spot =
      Number(liveSpots.value[under]) ||
      (chartTicker.value === under ? Number(brokerLivePrice.value) : 0) ||
      Number(t.spot)
    const live = repriceTicket(t, spot)
    const opt_ltp = Number(liveSpots.value[t.trading_symbol]) || 0
    if (live && opt_ltp > 0) {
      live.premium_now = Number(opt_ltp.toFixed(2))
      const entry = Number(t.entry?.expected_premium_inr) || live.premium_now
      live.pct_from_entry = entry ? Number((((opt_ltp - entry) / entry) * 100).toFixed(2)) : 0
      live.pnl_per_lot = Math.round((opt_ltp - entry) * (Number(t.lot_size) || 1))
      const t1 = Number(t.exit?.target_1_inr) || 0
      const sl = Number(t.exit?.stop_loss_inr) || 0
      live.pct_to_t1 = t1 ? Number((((t1 - opt_ltp) / opt_ltp) * 100).toFixed(2)) : 0
      live.pct_to_sl = sl ? Number((((sl - opt_ltp) / opt_ltp) * 100).toFixed(2)) : 0
      live._source = 'broker_ltp'
    } else if (live) {
      live._source = 'black_scholes'
    }
    out.push({ ...t, live, _tick_age_sec: liveTickAges.value[under] || 0, _underlying: under })
  }
  return out.sort((a, b) => (b.generated_at || '').localeCompare(a.generated_at || ''))
})

// ── Tracked position cards ──────────────────────────────────────────────────
function _classifyPosition(rec) {
  const t = rec.ticket || {}
  const under = t.underlying
  const isShort = t.direction === 'SHORT'
  const spot =
    Number(liveSpots.value[under]) ||
    (chartTicker.value === under ? Number(brokerLivePrice.value) : 0) ||
    Number(t.spot) || 0

  const live = spot ? repriceTicket(t, spot) : null
  const opt_ltp = Number(liveSpots.value[t.trading_symbol]) || 0
  const now_premium = opt_ltp || live?.premium_now || Number(t.entry?.expected_premium_inr) || 0
  const entry_prem = Number(t.entry?.expected_premium_inr) || 0
  const sl = Number(t.exit?.stop_loss_inr) || 0
  const t1 = Number(t.exit?.target_1_inr) || 0
  const t2 = Number(t.exit?.target_2_inr) || 0
  // P&L: SHORT profits when price drops, LONG profits when price rises
  const pnl_pct = entry_prem
    ? (isShort ? (entry_prem - now_premium) : (now_premium - entry_prem)) / entry_prem * 100
    : 0

  const _now = new Date()
  const _utc = _now.getTime() + _now.getTimezoneOffset() * 60000
  const _ist = new Date(_utc + 5.5 * 3600000)
  const istMins = _ist.getHours() * 60 + _ist.getMinutes()

  let status = 'safe', statusLabel = 'OK', alert = ''
  if (istMins >= 15 * 60) {
    status = 'time_exit'; statusLabel = 'FORCE EXIT'
    alert = 'It\'s past 15:00 IST — close immediately to beat broker auto-square-off.'
  } else if (isShort) {
    // SHORT: SL above entry, targets below
    if (sl && now_premium >= sl) {
      status = 'sl_hit'; statusLabel = 'SL HIT'
      alert = `Price ₹${now_premium.toFixed(2)} ≥ SL ₹${sl.toFixed(2)} — EXIT NOW.`
    } else if (t2 && now_premium <= t2) {
      status = 'past_t2'; statusLabel = 'T2 HIT'
      alert = `Price reached T2 ₹${t2.toFixed(2)} — full exit.`
    } else if (t1 && now_premium <= t1) {
      status = 'past_t1'; statusLabel = 'T1 HIT'
      alert = `Price reached T1 ₹${t1.toFixed(2)} — exit 50% and trail SL.`
    } else if (sl && entry_prem > 0) {
      const slDist = sl - entry_prem
      if (slDist > 0 && (now_premium - entry_prem) / slDist >= 0.8) {
        status = 'near_sl'; statusLabel = 'NEAR SL'
      }
    }
    if (status === 'safe' && t1 && entry_prem > 0) {
      const t1Dist = entry_prem - t1
      if (t1Dist > 0 && (entry_prem - now_premium) / t1Dist >= 0.9) {
        status = 'near_t1'; statusLabel = 'NEAR T1'
      }
    }
  } else {
    // LONG (default): SL below entry, targets above
    if (now_premium <= sl) {
      status = 'sl_hit'; statusLabel = 'SL HIT'
      alert = `Premium ₹${now_premium.toFixed(2)} ≤ SL ₹${sl.toFixed(2)} — EXIT NOW.`
    } else if (now_premium >= t2) {
      status = 'past_t2'; statusLabel = 'T2 HIT'
      alert = `Premium reached T2 ₹${t2.toFixed(2)} — full exit.`
    } else if (now_premium >= t1) {
      status = 'past_t1'; statusLabel = 'T1 HIT'
      alert = `Premium reached T1 ₹${t1.toFixed(2)} — exit 50% and trail SL to breakeven.`
    } else if (now_premium <= sl * 1.1) {
      status = 'near_sl'; statusLabel = 'NEAR SL'
    } else if (t1 && now_premium >= t1 * 0.92) {
      status = 'near_t1'; statusLabel = 'NEAR T1'
    }
  }

  const lot = Number(t.lot_size) || 1
  const qty = Number(rec.qty) || 1
  const pnl_inr = (isShort ? (entry_prem - now_premium) : (now_premium - entry_prem)) * qty
  const tick_age = Number(liveTickAges.value[under]) || 0
  const is_live = !!liveSpots.value[under] && tick_age < 10

  return {
    id: rec.id,
    trading_symbol: t.trading_symbol,
    display_symbol: t.display_symbol || t.trading_symbol,
    option_type: t.option_type, strike: t.strike, expiry: t.expiry,
    dte: t.days_to_expiry, lot, qty,
    direction: t.direction || 'LONG',
    trade_mode: t.trade_mode,
    entry_premium: entry_prem, now_premium, sl, t1, t2,
    pnl_pct, pnl_inr, tick_age, is_live,
    status, statusLabel, alert,
  }
}

const trackedCards = computed(() => {
  const out = []
  for (const rec of trackedPositions.value) {
    const t = rec.ticket || {}
    // Skip scalp and forex positions — they have their own panels
    if (t.trade_mode === 'scalp' || t.scalp_meta) continue
    if (t.trade_mode === 'forex') continue
    // Fault-isolate: a single malformed position (e.g. repriceTicket throwing on
    // missing ticket fields) must NOT throw out of this computed — that would
    // blank the entire WATCHING section. Skip the bad one, keep the rest.
    try {
      out.push(_classifyPosition(rec))
    } catch (e) {
      console.warn('trackedCards: skipping unclassifiable position', rec?.id, e)
    }
  }
  return out
})

// ── Ladder helper ──────────────────────────────────────────────────────────
function ladderPct(s, which) {
  const lo = +s.sl || 0
  const hi = +s.t2 || lo * 6 || 1
  const span = Math.max(1, hi - lo)
  const clamp = (v) => Math.max(0, Math.min(100, ((v - lo) / span) * 100))
  if (which === 'now') return clamp(s.premium) + '%'
  if (which === 'left') return clamp(Math.min(s.premium, s.avg_entry || s.premium)) + '%'
  if (which === 'width') {
    const a = clamp(Math.min(s.premium, s.avg_entry || s.premium))
    const b = clamp(Math.max(s.premium, s.avg_entry || s.premium))
    return Math.max(2, b - a) + '%'
  }
  return '0%'
}

async function triggerScan() {
  const base = import.meta.env.VITE_API_BASE_URL || ''
  await fetch(`${base}/api/trade/fo-scanner/trigger`, { method: 'POST' }).catch(() => {})
}

async function resetSwingKillSwitch() {
  try {
    const base = import.meta.env.VITE_API_BASE_URL || ''
    const res = await fetch(`${base}/api/trade/executor/reset-killswitch`, { method: 'POST' })
    const data = await res.json()
    if (data.success) {
      _dailyKillSwitch.value = false
      snack({ severity: 'success', title: 'Kill Switch', message: 'Swing trading re-enabled' })
    } else {
      snack({ severity: 'error', title: 'Kill Switch', message: 'Reset failed' })
    }
  } catch {
    snack({ severity: 'error', title: 'Kill Switch', message: 'Reset failed' })
  }
}

// ── Shared SSE: order events (replaces _openOrderEventsStream) ──────────────
onOrderEvent((m) => {
  if (m.type === 'order_events_connected' || m.type === 'heartbeat') return
  if (m.type === 'order_update') {
    const statusSoundMap = {
      'ENTRY_PLACED': 'entry_buy',
      'SCALP_ENTRY':  'entry_buy',
      'SCALP_EXIT':   'exit_sell',
      'SL_HIT':       'sl_exit',
      'PAST_T1':      't1_exit',
      'PAST_T2':      't2_exit',
      'TIME_EXIT':    'time_exit',
      'THESIS_FLIP':  'thesis_exit',
      'SLIPPAGE_REJECT': 'slippage_reject',
      'SIMULATED':    'entry_buy',
      'ENTRY_FAILED': 'error',
    }
    const soundType = statusSoundMap[m.status] || m.severity || 'info'
    playNotifSound(soundType)
    snack({
      severity: m.severity || 'info',
      title:    m.title || `Order ${m.status}`,
      message:  m.message || '',
      duration: m.severity === 'error' ? 8000 : 5000,
      sound: false,
    })
    _loadTracked()
    _loadDailyPnl()
  }
})

// ── Lifecycle ──────────────────────────────────────────────────────────────
function _onPnlChanged() { _loadDailyPnl(); _loadTracked() }

onMounted(() => {
  _loadTracked()
  _loadDailyPnl()
  _loadMissedAlerts()
  window.addEventListener('vega:pnl-changed', _onPnlChanged)
  // Shared SSE for order events + tracked alerts is auto-connected via composable

  const tickClock = () => {
    liveClock.value = fmtTime(new Date()) + ' IST'
  }
  tickClock()
  _liveClockTimer = setInterval(tickClock, 1000)

  watch(trackedCards, (cards) => _maybeAlert(cards), { deep: true })

  // Open stream for chart ticker whenever connection + ticker are ready
  watch(
    [brokerConnected, chartTicker],
    ([connected, ticker]) => {
      if (connected && ticker) _openTicketStream(ticker.toUpperCase())
    },
    { immediate: true },
  )

  // Mirror per-underlying tick price → brokerLivePrice for the charted ticker
  watch(
    () => liveSpots.value[chartTicker.value],
    (price) => { if (price) brokerLivePrice.value = price },
  )

  // Subscribe to tick streams for the symbols we care about — CAPPED.
  // A large F&O universe can open 20+ positions; streaming every underlying +
  // option (~50 EventSources) floods the HTTP/2 connection and trips the
  // `priority` PriorityLoop bug → connection resets → UI can't load. Cap the
  // count; positions beyond the cap get P&L from the server-side reprice (/tracked
  // + tracked_monitor), not per-symbol ticks.
  const _MAX_TICK_STREAMS = 12
  watch(
    () => {
      const set = new Set(['^NSEI', '^BSESN'])
      if (chartTicker.value) set.add(chartTicker.value)
      // Active signal tickets first (what the user is about to trade)
      for (const t of Object.values(liveTickets.value || {})) {
        if (set.size >= _MAX_TICK_STREAMS) break
        if (t?.trading_symbol) set.add(t.trading_symbol)
      }
      // Then open positions' option symbols (for live P&L) up to the cap
      for (const rec of trackedPositions.value || []) {
        if (set.size >= _MAX_TICK_STREAMS) break
        const opt = rec?.ticket?.trading_symbol
        if (opt) set.add(opt)
      }
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

  // Option chain: fetch on mount + whenever user changes chainUnderlying
  watch(
    chainUnderlying,
    (sym) => {
      if (sym) { _openTicketStream(sym); _fetchOptionChain(sym) }
    },
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
})

onUnmounted(() => {
  window.removeEventListener('vega:pnl-changed', _onPnlChanged)
  _closeAllTicketStreams()
  if (_ticketAgeTimer) clearInterval(_ticketAgeTimer)
  if (_liveClockTimer) clearInterval(_liveClockTimer)
  if (_audioCtx) { try { _audioCtx.close() } catch {} ; _audioCtx = null }
})
</script>

<style src="../../styles/LiveTradingPanel.css"></style>
