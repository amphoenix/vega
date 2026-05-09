<template>
  <AppLayout
    v-model:viewMode="viewMode"
    :step-num="3"
    step-name="Start Simulation"
    :status-class="statusClass"
    :status-text="statusText"
  >
    <template #left>
      <GraphPanel
        :graphData="graphData"
        :loading="graphLoading"
        :currentPhase="3"
        :isSimulating="isSimulating"
        @refresh="refreshGraph"
        @toggle-maximize="toggleMaximize('graph')"
      />
    </template>
    <template #right>
      <Step3Simulation
        :simulationId="currentSimulationId"
        :maxRounds="maxRounds"
        :minutesPerRound="minutesPerRound"
        :projectData="projectData"
        :graphData="graphData"
        :systemLogs="systemLogs"
        @go-back="handleGoBack"
        @next-step="handleNextStep"
        @add-log="addLog"
        @update-status="updateStatus"
      />
    </template>
  </AppLayout>
</template>

<script setup>
import { ref, computed, onMounted, onUnmounted, watch } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import AppLayout from '../components/layout/AppLayout.vue'
import GraphPanel from '../components/chart/GraphPanel.vue'
import Step3Simulation from '../components/simulation/Step3Simulation.vue'
import { getProject, getGraphData } from '../api/graph'
import { getSimulation, getSimulationConfig, stopSimulation, closeSimulationEnv, getEnvStatus } from '../api/simulation'

const route = useRoute()
const router = useRouter()

defineProps({ simulationId: String })

const viewMode = ref('split')
const currentSimulationId = ref(route.params.simulationId)
const maxRounds = ref(route.query.maxRounds ? parseInt(route.query.maxRounds) : null)
const minutesPerRound = ref(30)
const projectData = ref(null)
const graphData = ref(null)
const graphLoading = ref(false)
const systemLogs = ref([])
const currentStatus = ref('processing')

const statusClass = computed(() => currentStatus.value)
const statusText = computed(() => {
  if (currentStatus.value === 'error') return 'Error'
  if (currentStatus.value === 'completed') return 'Completed'
  return 'Running'
})
const isSimulating = computed(() => currentStatus.value === 'processing')

const addLog = (msg) => {
  const time = new Date().toLocaleTimeString('en-US', { hour12: false, hour: '2-digit', minute: '2-digit', second: '2-digit' }) + '.' + new Date().getMilliseconds().toString().padStart(3, '0')
  systemLogs.value.push({ time, msg })
  if (systemLogs.value.length > 200) systemLogs.value.shift()
}

const updateStatus = (status) => { currentStatus.value = status }

const toggleMaximize = (target) => {
  viewMode.value = viewMode.value === target ? 'split' : target
}

const handleGoBack = async () => {
  addLog('Preparing to return to Step 2, closing simulation...')
  stopGraphRefresh()
  try {
    const envStatusRes = await getEnvStatus({ simulation_id: currentSimulationId.value })
    if (envStatusRes.success && envStatusRes.data?.env_alive) {
      addLog('Closing simulation environment...')
      try {
        await closeSimulationEnv({ simulation_id: currentSimulationId.value, timeout: 10 })
        addLog('✓ Simulation environment closed')
      } catch {
        try { await stopSimulation({ simulation_id: currentSimulationId.value }); addLog('✓ Simulation force stopped') }
        catch (stopErr) { addLog(`Force stop failed: ${stopErr.message}`) }
      }
    } else if (isSimulating.value) {
      addLog('Stopping simulation process...')
      try { await stopSimulation({ simulation_id: currentSimulationId.value }); addLog('✓ Simulation stopped') }
      catch (err) { addLog(`Failed to stop simulation: ${err.message}`) }
    }
  } catch (err) {
    addLog(`Failed to check simulation status: ${err.message}`)
  }
  router.push({ name: 'Simulation', params: { simulationId: currentSimulationId.value } })
}

const handleNextStep = () => {
  addLog('Entering Step 4: Report Generation')
}

const loadSimulationData = async () => {
  try {
    addLog(`Loading simulation data: ${currentSimulationId.value}`)
    const simRes = await getSimulation(currentSimulationId.value)
    if (simRes.success && simRes.data) {
      try {
        const configRes = await getSimulationConfig(currentSimulationId.value)
        if (configRes.success && configRes.data?.time_config?.minutes_per_round) {
          minutesPerRound.value = configRes.data.time_config.minutes_per_round
          addLog(`Time config: ${minutesPerRound.value} minutes per round`)
        }
      } catch { addLog(`Failed to get time config, using default: ${minutesPerRound.value} min/round`) }

      if (simRes.data.project_id) {
        const projRes = await getProject(simRes.data.project_id)
        if (projRes.success && projRes.data) {
          projectData.value = projRes.data
          addLog(`Project loaded successfully: ${projRes.data.project_id}`)
          if (projRes.data.graph_id) await loadGraph(projRes.data.graph_id)
        }
      }
    } else {
      addLog(`Failed to load simulation data: ${simRes.error || 'Unknown error'}`)
    }
  } catch (err) {
    addLog(`Loading exception: ${err.message}`)
  }
}

const loadGraph = async (graphId) => {
  if (!isSimulating.value) graphLoading.value = true
  try {
    const res = await getGraphData(graphId)
    if (res.success) {
      graphData.value = res.data
      if (!isSimulating.value) addLog('Graph data loaded successfully')
    }
  } catch (err) {
    addLog(`Graph loading failed: ${err.message}`)
  } finally {
    graphLoading.value = false
  }
}

const refreshGraph = () => {
  if (projectData.value?.graph_id) loadGraph(projectData.value.graph_id)
}

let graphRefreshTimer = null

const startGraphRefresh = () => {
  if (graphRefreshTimer) return
  addLog('Starting graph real-time refresh (30s)')
  graphRefreshTimer = setInterval(refreshGraph, 30000)
}

const stopGraphRefresh = () => {
  if (graphRefreshTimer) { clearInterval(graphRefreshTimer); graphRefreshTimer = null; addLog('Stopping graph real-time refresh') }
}

watch(isSimulating, (val) => { val ? startGraphRefresh() : stopGraphRefresh() }, { immediate: true })

onMounted(() => {
  addLog('SimulationRunView initialized')
  if (maxRounds.value) addLog(`Custom simulation rounds: ${maxRounds.value}`)
  loadSimulationData()
})

onUnmounted(() => { stopGraphRefresh() })
</script>
