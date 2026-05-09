<template>
  <AppLayout
    v-model:viewMode="viewMode"
    :step-num="2"
    step-name="Environment Setup"
    :status-class="statusClass"
    :status-text="statusText"
  >
    <template #left>
      <GraphPanel
        :graphData="graphData"
        :loading="graphLoading"
        :currentPhase="2"
        @refresh="refreshGraph"
        @toggle-maximize="toggleMaximize('graph')"
      />
    </template>
    <template #right>
      <Step2EnvSetup
        :simulationId="currentSimulationId"
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
import { ref, computed, onMounted } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import AppLayout from '../components/layout/AppLayout.vue'
import GraphPanel from '../components/chart/GraphPanel.vue'
import Step2EnvSetup from '../components/simulation/Step2EnvSetup.vue'
import { getProject, getGraphData } from '../api/graph'
import { getSimulation, stopSimulation, getEnvStatus, closeSimulationEnv } from '../api/simulation'

const route = useRoute()
const router = useRouter()

defineProps({ simulationId: String })

const viewMode = ref('split')
const currentSimulationId = ref(route.params.simulationId)
const projectData = ref(null)
const graphData = ref(null)
const graphLoading = ref(false)
const systemLogs = ref([])
const currentStatus = ref('processing')

const statusClass = computed(() => currentStatus.value)
const statusText = computed(() => {
  if (currentStatus.value === 'error') return 'Error'
  if (currentStatus.value === 'completed') return 'Ready'
  return 'Preparing'
})

const addLog = (msg) => {
  const time = new Date().toLocaleTimeString('en-US', { hour12: false, hour: '2-digit', minute: '2-digit', second: '2-digit' }) + '.' + new Date().getMilliseconds().toString().padStart(3, '0')
  systemLogs.value.push({ time, msg })
  if (systemLogs.value.length > 100) systemLogs.value.shift()
}

const updateStatus = (status) => { currentStatus.value = status }

const toggleMaximize = (target) => {
  viewMode.value = viewMode.value === target ? 'split' : target
}

const handleGoBack = () => {
  if (projectData.value?.project_id) {
    router.push({ name: 'Process', params: { projectId: projectData.value.project_id } })
  } else {
    router.push('/')
  }
}

const handleNextStep = (params = {}) => {
  addLog('Entering Step 3: Start Simulation')
  if (params.maxRounds) addLog(`Custom simulation rounds: ${params.maxRounds} rounds`)
  else addLog('Using auto-configured simulation rounds')
  const routeParams = { name: 'SimulationRun', params: { simulationId: currentSimulationId.value } }
  if (params.maxRounds) routeParams.query = { maxRounds: params.maxRounds }
  router.push(routeParams)
}

const checkAndStopRunningSimulation = async () => {
  if (!currentSimulationId.value) return
  try {
    const envStatusRes = await getEnvStatus({ simulation_id: currentSimulationId.value })
    if (envStatusRes.success && envStatusRes.data?.env_alive) {
      addLog('Detected running simulation environment, closing...')
      try {
        const closeRes = await closeSimulationEnv({ simulation_id: currentSimulationId.value, timeout: 10 })
        if (closeRes.success) addLog('✓ Simulation environment closed')
        else { addLog(`Failed to close: ${closeRes.error}`); await forceStopSimulation() }
      } catch (closeErr) {
        addLog(`Close error: ${closeErr.message}`); await forceStopSimulation()
      }
    } else {
      const simRes = await getSimulation(currentSimulationId.value)
      if (simRes.success && simRes.data?.status === 'running') {
        addLog('Detected simulation status as running, stopping...')
        await forceStopSimulation()
      }
    }
  } catch (err) {
    console.warn('Failed to check simulation status:', err)
  }
}

const forceStopSimulation = async () => {
  try {
    const stopRes = await stopSimulation({ simulation_id: currentSimulationId.value })
    if (stopRes.success) addLog('✓ Simulation force stopped')
    else addLog(`Failed to force stop: ${stopRes.error}`)
  } catch (err) {
    addLog(`Force stop error: ${err.message}`)
  }
}

const loadSimulationData = async () => {
  try {
    addLog(`Loading simulation data: ${currentSimulationId.value}`)
    const simRes = await getSimulation(currentSimulationId.value)
    if (simRes.success && simRes.data?.project_id) {
      const projRes = await getProject(simRes.data.project_id)
      if (projRes.success && projRes.data) {
        projectData.value = projRes.data
        addLog(`Project loaded successfully: ${projRes.data.project_id}`)
        if (projRes.data.graph_id) await loadGraph(projRes.data.graph_id)
      }
    } else {
      addLog(`Failed to load simulation data: ${simRes.error || 'Unknown error'}`)
    }
  } catch (err) {
    addLog(`Loading error: ${err.message}`)
  }
}

const loadGraph = async (graphId) => {
  graphLoading.value = true
  try {
    const res = await getGraphData(graphId)
    if (res.success) { graphData.value = res.data; addLog('Graph data loaded successfully') }
  } catch (err) {
    addLog(`Graph loading failed: ${err.message}`)
  } finally {
    graphLoading.value = false
  }
}

const refreshGraph = () => {
  if (projectData.value?.graph_id) loadGraph(projectData.value.graph_id)
}

onMounted(async () => {
  addLog('SimulationView initialized')
  await checkAndStopRunningSimulation()
  loadSimulationData()
})
</script>
