<template>
  <div class="analysis-view">
    <!-- Status bar -->
    <div class="av-statusbar">
      <span class="av-ticker">{{ displayTicker }}</span>
      <span class="av-company" v-if="investData?.company">{{ investData.company }}</span>
      <span class="av-price" v-if="investData?.price">{{ currencySymbol }}{{ investData.price }}</span>
      <span class="av-agents" v-if="investData?.agent_count">
        {{ investData.agent_count }} agents
        <span v-if="investData.real_agent_count" class="av-real-tag">
          · {{ investData.real_agent_count }} real
        </span>
      </span>
      <span
        class="av-verdict-pill"
        v-if="investData?.final?.final_verdict"
        :class="'vc-' + verdictKey(investData.final.final_verdict)"
      >
        {{ investData.final.final_verdict }} ·
        {{ investData.final.consensus_score }}% consensus
      </span>
      <span class="av-horizon" v-if="investData?.final?.time_horizon">
        {{ investData.final.time_horizon }}
      </span>
      <div class="av-spacer"></div>
      <button
        class="av-run-btn"
        @click="loadInvestAnalysis"
        :disabled="investLoading || !chartTicker"
      >
        <span v-if="investLoading" class="spinner"></span>
        <span v-else>⚡</span>
        {{
          investLoading
            ? investAgentsTotal > 0
              ? `${investAgentsDone}/${investAgentsTotal} agents…`
              : investPhase || "Starting…"
            : "Run Analysis"
        }}
      </button>
    </div>

    <!-- Main body: graph left + report right -->
    <div class="av-body">
      <!-- D3 graph (large) -->
      <div class="av-graph-col">
        <div class="av-graph-wrap" ref="investGraphWrap">
          <svg ref="investGraphSvg" class="av-svg"></svg>
        </div>
        <div class="av-legend" v-if="investData">
          <span class="avl-item"><span class="avl-dot avl-dot-strong-buy"></span>Strong Buy</span>
          <span class="avl-item"><span class="avl-dot avl-dot-buy"></span>Buy</span>
          <span class="avl-item"><span class="avl-dot avl-dot-hold"></span>Hold</span>
          <span class="avl-item"><span class="avl-dot avl-dot-sell"></span>Sell</span>
          <span class="avl-item"><span class="avl-dot avl-dot-strong-sell"></span>Strong Sell</span>
          <span class="avl-item"><span class="avl-dot avl-dot-risk"></span>Risk</span>
        </div>
        <div class="av-graph-empty" v-if="!investData && !investLoading && !investError">
          Click "Run Analysis" to launch {{ displayTicker }} through 20 expert AI agents
        </div>
        <div class="av-graph-empty av-graph-error" v-if="investError && !investLoading">
          ⚠ {{ investError }}
        </div>
        <div class="av-graph-loading" v-if="investLoading">
          <span class="spinner"></span>
          Extracting real entities → Running parallel expert analyses → Bull vs Bear debate → CIO verdict…
        </div>
      </div>

      <!-- Report panel (scrollable) -->
      <div class="av-report-col" v-if="investData">
        <!-- TODAY'S ACTION -->
        <div
          class="avr-action"
          v-if="investData.final?.short_term_action"
          :class="'vc-' + verdictKey(investData.final.final_verdict)"
        >
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
          <div class="avrl-row entry">
            <span>ENTRY</span><span>{{ currencySymbol }}{{ investData.final?.entry_price?.toFixed(2) }}</span>
          </div>
          <div class="avrl-row sl">
            <span>STOP LOSS</span><span>{{ currencySymbol }}{{ investData.final?.stop_loss?.toFixed(2) }}</span>
          </div>
          <div class="avrl-row t1">
            <span>TARGET 1</span><span>{{ currencySymbol }}{{ investData.final?.target_1?.toFixed(2) }}</span>
          </div>
          <div class="avrl-row t2">
            <span>TARGET 2</span><span>{{ currencySymbol }}{{ investData.final?.target_2?.toFixed(2) }}</span>
          </div>
          <div class="avrl-row t3">
            <span>TARGET 3</span><span>{{ currencySymbol }}{{ investData.final?.target_3?.toFixed(2) }}</span>
          </div>
          <div class="avrl-row pos">
            <span>POSITION SIZE</span><span>{{ investData.final?.position_size_pct }}% of portfolio</span>
          </div>
        </div>

        <!-- Thesis -->
        <div class="avr-thesis">
          <div class="avrt-title">Investment Thesis</div>
          <p>{{ investData.final?.investment_thesis }}</p>
          <div class="avrt-bull" v-if="investData.final?.bull_case">
            🟢 Bull: {{ investData.final.bull_case }}
          </div>
          <div class="avrt-bear" v-if="investData.final?.bear_case">
            🔴 Bear: {{ investData.final.bear_case }}
          </div>
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
          <div
            v-for="(result, name) in investData.agents"
            :key="name"
            class="avra-card"
            :class="'vc-' + verdictKey(result.verdict)"
            @click="selectedAgent = selectedAgent?.agent === name ? null : { ...result, label: name }"
          >
            <div class="avra-top">
              <span class="avra-verdict">{{ result.verdict }}</span>
              <span class="avra-conf">{{ result.confidence }}%</span>
              <span class="avra-name">{{ name }}</span>
            </div>
            <div class="avra-firm">{{ result.role }} · {{ result.firm }}</div>
            <div class="avra-reason">{{ result.reasoning }}</div>
            <div v-if="selectedAgent?.agent === name" class="avra-findings">
              <div v-for="f in result.key_findings || []" :key="f">• {{ f }}</div>
              <div class="avra-levels-row" v-if="result.entry_price">
                Entry {{ currencySymbol }}{{ result.entry_price?.toFixed(2) }} ·
                Stop Loss {{ currencySymbol }}{{ result.stop_loss?.toFixed(2) }} ·
                Target {{ currencySymbol }}{{ result.target_price?.toFixed(2) }}
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
</template>

<script setup>
import { ref, nextTick, onMounted, onUnmounted } from 'vue'
import { storeToRefs } from 'pinia'
import * as d3 from 'd3'
import { useMarketStore } from '../../stores/useMarketStore'
import { createInvestAnalysisStream } from '../../api/market'

const props = defineProps({
  displayTicker: { type: String, default: '' },
  currencySymbol: { type: String, default: '₹' },
})

const store = useMarketStore()
const {
  investData,
  investLoading,
  investError,
  selectedAgent,
  investPhase,
  investAgentsDone,
  investAgentsTotal,
  chartTicker,
  tickerStats,
} = storeToRefs(store)

const investGraphWrap = ref(null)
const investGraphSvg = ref(null)
let investStream = null

function verdictKey(v) {
  const map = {
    'STRONG BUY': 'sbuy',
    BUY: 'buy',
    HOLD: 'hold',
    SELL: 'sell',
    'STRONG SELL': 'ssell',
  }
  return map[v] || 'hold'
}

function loadInvestAnalysis() {
  if (!chartTicker.value || investLoading.value) return

  if (investStream) {
    investStream.close()
    investStream = null
  }

  investLoading.value = true
  investData.value = null
  investError.value = ''
  selectedAgent.value = null
  investPhase.value = 'Connecting…'
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
        investStream?.close()
        investStream = null
        await nextTick()
        await nextTick()
        drawInvestGraph()
      }

      if (msg.type === 'error') {
        investError.value = msg.msg || 'Analysis failed'
        investLoading.value = false
        investStream?.close()
        investStream = null
      }
    } catch (err) {
      console.warn('invest stream parse error', err)
    }
  }

  investStream.onerror = () => {
    investError.value = 'Stream connection lost — please retry'
    investLoading.value = false
    investStream?.close()
    investStream = null
  }
}

function drawInvestGraph() {
  const wrap = investGraphWrap.value
  if (!wrap || !investData.value?.nodes?.length) return

  const W = wrap.clientWidth || 700
  const H = wrap.clientHeight || 500

  const vColor = {
    'STRONG BUY': '#22c55e',
    BUY: '#86efac',
    HOLD: '#fbbf24',
    SELL: '#f97316',
    'STRONG SELL': '#ef4444',
  }
  const typeColor = {
    ticker: '#00d4a8',
    agent: null,
    evidence: null,
    verdict: null,
    risk: '#f87171',
  }

  const nodes = investData.value.nodes.map((n) => ({ ...n }))
  const links = investData.value.links.map((l) => ({ ...l }))

  function nodeColor(d) {
    if (d.type === 'ticker') return typeColor.ticker
    if (d.type === 'risk') return typeColor.risk
    const v = d.verdict || (d.type === 'verdict' ? investData.value.final?.final_verdict : null)
    const base = vColor[v] || '#888'
    if (d.type === 'evidence') return base + '66'
    return base
  }
  function nodeRadius(d) {
    if (d.type === 'ticker') return 22
    if (d.type === 'agent') return 16
    if (d.type === 'verdict') return 18
    if (d.type === 'risk') return 9
    return 6
  }

  const svg = d3.select(investGraphSvg.value)
  svg.selectAll('*').remove()
  svg.attr('width', W).attr('height', H)

  svg
    .append('defs')
    .append('marker')
    .attr('id', 'ia-arrow')
    .attr('viewBox', '0 -4 8 8')
    .attr('refX', 14)
    .attr('markerWidth', 6)
    .attr('markerHeight', 6)
    .attr('orient', 'auto')
    .append('path')
    .attr('d', 'M0,-4L8,0L0,4')
    .attr('fill', '#333')

  const zoomBehavior = d3
    .zoom()
    .scaleExtent([0.3, 4])
    .on('zoom', (e) => g.attr('transform', e.transform))
  const g = svg.append('g')
  svg.call(zoomBehavior)

  const agentCount = nodes.filter((n) => n.type === 'agent').length
  const distScale = agentCount <= 8 ? 2.2 : agentCount <= 12 ? 1.8 : 1.4

  const sim = d3
    .forceSimulation(nodes)
    .force(
      'link',
      d3
        .forceLink(links)
        .id((d) => d.id)
        .distance((d) => (d.relation === 'supports' ? 70 : 130) * distScale),
    )
    .force('charge', d3.forceManyBody().strength(-400 * distScale))
    .force('center', d3.forceCenter(W / 2, H / 2))
    .force('collide', d3.forceCollide((d) => nodeRadius(d) + 18))

  const link = g
    .append('g')
    .selectAll('line')
    .data(links)
    .enter()
    .append('line')
    .attr('stroke', (d) => (d.relation === 'risk' ? '#7f1d1d44' : '#2a2a2a'))
    .attr('stroke-width', (d) => (d.relation === 'supports' ? 0.8 : 1.5))
    .attr('stroke-dasharray', (d) => (d.relation === 'risk' ? '3,3' : null))
    .attr('marker-end', (d) => (d.relation === 'vote' ? 'url(#ia-arrow)' : null))

  const node = g
    .append('g')
    .selectAll('circle')
    .data(nodes)
    .enter()
    .append('circle')
    .attr('r', nodeRadius)
    .attr('fill', nodeColor)
    .attr('stroke', (d) =>
      d.type === 'ticker' ? '#00d4a8' : d.type === 'verdict' ? '#fff3' : '#1a1a1a',
    )
    .attr('stroke-width', (d) => (d.type === 'ticker' || d.type === 'verdict' ? 2 : 1))
    .style('cursor', (d) => (d.type === 'agent' ? 'pointer' : 'default'))
    .on('click', (e, d) => {
      if (d.type === 'agent') selectedAgent.value = d
    })
    .call(
      d3
        .drag()
        .on('start', (e, d) => {
          if (!e.active) sim.alphaTarget(0.3).restart()
          d.fx = d.x
          d.fy = d.y
        })
        .on('drag', (e, d) => {
          d.fx = e.x
          d.fy = e.y
        })
        .on('end', (e, d) => {
          if (!e.active) sim.alphaTarget(0)
          d.fx = null
          d.fy = null
        }),
    )

  const label = g
    .append('g')
    .selectAll('text')
    .data(nodes)
    .enter()
    .append('text')
    .attr('font-size', (d) =>
      d.type === 'ticker' ? 13 : d.type === 'agent' ? 11 : d.type === 'verdict' ? 12 : 8,
    )
    .attr('fill', (d) =>
      d.type === 'ticker'
        ? '#00d4a8'
        : d.type === 'verdict'
          ? '#fff'
          : d.type === 'agent'
            ? '#ccc'
            : '#555',
    )
    .attr('text-anchor', 'middle')
    .attr('dy', (d) => -(nodeRadius(d) + 4))
    .text((d) => {
      if (d.type === 'evidence' || d.type === 'risk') return ''
      if (d.type === 'ticker') {
        const company = investData.value?.company || tickerStats.value?.company_name || ''
        return company || d.label.replace(/\.(NS|BO)$/i, '')
      }
      if (d.type === 'agent')
        return d.label.replace(' Analyst', '').replace(' Manager', ' Mgr')
      return d.label.replace(/\.(NS|BO)$/i, '')
    })

  const ring = g
    .append('g')
    .selectAll('circle.ring')
    .data(nodes.filter((n) => n.type === 'agent'))
    .enter()
    .append('circle')
    .attr('r', (d) => nodeRadius(d) + 4)
    .attr('fill', 'none')
    .attr('stroke', (d) => nodeColor(d))
    .attr('stroke-width', 1)
    .attr('stroke-dasharray', (d) => {
      const circ = 2 * Math.PI * (nodeRadius(d) + 4)
      const filled = (circ * (d.confidence || 50)) / 100
      return `${filled} ${circ - filled}`
    })
    .attr('opacity', 0.5)

  function tick() {
    link
      .attr('x1', (d) => d.source.x)
      .attr('y1', (d) => d.source.y)
      .attr('x2', (d) => d.target.x)
      .attr('y2', (d) => d.target.y)
    node.attr('cx', (d) => d.x).attr('cy', (d) => d.y)
    label.attr('x', (d) => d.x).attr('y', (d) => d.y)
    ring
      .attr('cx', (d) => d.x)
      .attr('cy', (d) => d.y)
      .attr('stroke-dashoffset', (d) => {
        const circ = 2 * Math.PI * (nodeRadius(d) + 4)
        return circ * 0.25
      })
  }

  sim.on('tick', tick)

  sim.on('end', () => {
    const visNodes = nodes.filter(
      (n) => n.type === 'agent' || n.type === 'ticker' || n.type === 'verdict',
    )
    if (!visNodes.length) return
    const pad = 60
    const xs = visNodes.map((n) => n.x),
      ys = visNodes.map((n) => n.y)
    const x0 = Math.min(...xs) - pad,
      x1 = Math.max(...xs) + pad
    const y0 = Math.min(...ys) - pad,
      y1 = Math.max(...ys) + pad
    const scaleX = W / (x1 - x0),
      scaleY = H / (y1 - y0)
    const scale = Math.min(scaleX, scaleY, 1.8) * 0.88
    const tx = (W - scale * (x0 + x1)) / 2
    const ty = (H - scale * (y0 + y1)) / 2
    svg
      .transition()
      .duration(800)
      .call(zoomBehavior.transform, d3.zoomIdentity.translate(tx, ty).scale(scale))
  })
}

onMounted(async () => {
  if (!investData.value && !investLoading.value && chartTicker.value) {
    loadInvestAnalysis()
  } else if (investData.value) {
    await nextTick()
    await nextTick()
    setTimeout(drawInvestGraph, 50)
  }
})

onUnmounted(() => {
  if (investStream) {
    investStream.close()
    investStream = null
  }
})
</script>

<style src="../../styles/InvestmentAnalysis.css"></style>
