<template>
  <AppLayout
    v-model:viewMode="viewMode"
    :step-num="currentStep"
    :step-name="stepNames[currentStep - 1]"
    :status-class="statusClass"
    :status-text="statusText"
  >
    <template #left>
      <GraphPanel
        :graphData="graphData"
        :loading="graphLoading"
        :currentPhase="currentPhase"
        @refresh="refreshGraph"
        @toggle-maximize="toggleMaximize('graph')"
      />
    </template>
    <template #right>
      <Step1GraphBuild
        v-if="currentStep === 1"
        :currentPhase="currentPhase"
        :projectData="projectData"
        :ontologyProgress="ontologyProgress"
        :buildProgress="buildProgress"
        :graphData="graphData"
        :systemLogs="systemLogs"
        @next-step="handleNextStep"
      />
      <Step2EnvSetup
        v-else-if="currentStep === 2"
        :projectData="projectData"
        :graphData="graphData"
        :systemLogs="systemLogs"
        @go-back="handleGoBack"
        @next-step="handleNextStep"
        @add-log="addLog"
      />
    </template>
  </AppLayout>
</template>

<script setup>
import { ref, computed, onMounted, onUnmounted, nextTick } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import AppLayout from '../components/layout/AppLayout.vue'
import GraphPanel from '../components/chart/GraphPanel.vue'
import Step1GraphBuild from '../components/simulation/Step1GraphBuild.vue'
import Step2EnvSetup from '../components/simulation/Step2EnvSetup.vue'
import { generateOntology, getProject, buildGraph, getTaskStatus, getGraphData } from '../api/graph'
import { getPendingUpload, clearPendingUpload } from '../store/pendingUpload'

const route = useRoute()
const router = useRouter()

const viewMode = ref('split')

const currentStep = ref(1)
const stepNames = ['Graph Build', 'Environment Setup', 'Start Simulation', 'Report Generation', 'Deep Interaction']

const currentProjectId = ref(route.params.projectId)
const loading = ref(false)
const graphLoading = ref(false)
const error = ref('')
const projectData = ref(null)
const graphData = ref(null)
const currentPhase = ref(-1)
const ontologyProgress = ref(null)
const buildProgress = ref(null)
const systemLogs = ref([])

let pollTimer = null
let graphPollTimer = null

const statusClass = computed(() => {
  if (error.value) return 'error'
  if (currentPhase.value >= 2) return 'completed'
  return 'processing'
})

const statusText = computed(() => {
  if (error.value) return 'Error'
  if (currentPhase.value >= 2) return 'Ready'
  if (currentPhase.value === 1) return 'Building Graph'
  if (currentPhase.value === 0) return 'Generating Ontology'
  return 'Initializing'
})

const addLog = (msg) => {
  const time = new Date().toLocaleTimeString('en-US', { hour12: false, hour: '2-digit', minute: '2-digit', second: '2-digit' }) + '.' + new Date().getMilliseconds().toString().padStart(3, '0')
  systemLogs.value.push({ time, msg })
  if (systemLogs.value.length > 100) systemLogs.value.shift()
}

const toggleMaximize = (target) => {
  viewMode.value = viewMode.value === target ? 'split' : target
}

const handleNextStep = (params = {}) => {
  if (currentStep.value < 5) {
    currentStep.value++
    addLog(`Entering Step ${currentStep.value}: ${stepNames[currentStep.value - 1]}`)
    if (currentStep.value === 3 && params.maxRounds) {
      addLog(`Custom simulation rounds: ${params.maxRounds} rounds`)
    }
  }
}

const handleGoBack = () => {
  if (currentStep.value > 1) {
    currentStep.value--
    addLog(`Back to Step ${currentStep.value}: ${stepNames[currentStep.value - 1]}`)
  }
}

const initProject = async () => {
  addLog('Project view initialized.')
  if (currentProjectId.value === 'new') {
    await handleNewProject()
  } else {
    await loadProject()
  }
}

const handleNewProject = async () => {
  const pending = getPendingUpload()
  if (!pending.isPending || pending.files.length === 0) {
    error.value = 'No pending files found.'
    addLog('Error: No pending files found for new project.')
    return
  }
  try {
    loading.value = true
    currentPhase.value = 0
    ontologyProgress.value = { message: 'Uploading and analyzing docs...' }
    addLog('Starting ontology generation: Uploading files...')
    const formData = new FormData()
    pending.files.forEach(f => formData.append('files', f))
    formData.append('simulation_requirement', pending.simulationRequirement)
    const res = await generateOntology(formData)
    if (res.success) {
      clearPendingUpload()
      currentProjectId.value = res.data.project_id
      projectData.value = res.data
      router.replace({ name: 'Process', params: { projectId: res.data.project_id } })
      ontologyProgress.value = null
      addLog(`Ontology generated successfully for project ${res.data.project_id}`)
      await startBuildGraph()
    } else {
      error.value = res.error || 'Ontology generation failed'
      addLog(`Error generating ontology: ${error.value}`)
    }
  } catch (err) {
    error.value = err.message
    addLog(`Exception in handleNewProject: ${err.message}`)
  } finally {
    loading.value = false
  }
}

const loadProject = async () => {
  try {
    loading.value = true
    addLog(`Loading project ${currentProjectId.value}...`)
    const res = await getProject(currentProjectId.value)
    if (res.success) {
      projectData.value = res.data
      updatePhaseByStatus(res.data.status)
      addLog(`Project loaded. Status: ${res.data.status}`)
      if (res.data.status === 'ontology_generated' && !res.data.graph_id) {
        await startBuildGraph()
      } else if (res.data.status === 'graph_building' && res.data.graph_build_task_id) {
        currentPhase.value = 1
        startPollingTask(res.data.graph_build_task_id)
        startGraphPolling()
      } else if (res.data.status === 'graph_completed' && res.data.graph_id) {
        currentPhase.value = 2
        await loadGraph(res.data.graph_id)
      }
    } else {
      error.value = res.error
      addLog(`Error loading project: ${res.error}`)
    }
  } catch (err) {
    error.value = err.message
    addLog(`Exception in loadProject: ${err.message}`)
  } finally {
    loading.value = false
  }
}

const updatePhaseByStatus = (status) => {
  switch (status) {
    case 'created':
    case 'ontology_generated': currentPhase.value = 0; break
    case 'graph_building':     currentPhase.value = 1; break
    case 'graph_completed':    currentPhase.value = 2; break
    case 'failed': error.value = 'Project failed'; break
  }
}

const startBuildGraph = async () => {
  try {
    currentPhase.value = 1
    buildProgress.value = { progress: 0, message: 'Starting build...' }
    addLog('Initiating graph build...')
    const res = await buildGraph({ project_id: currentProjectId.value })
    if (res.success) {
      addLog(`Graph build task started. Task ID: ${res.data.task_id}`)
      startGraphPolling()
      startPollingTask(res.data.task_id)
    } else {
      error.value = res.error
      addLog(`Error starting build: ${res.error}`)
    }
  } catch (err) {
    error.value = err.message
    addLog(`Exception in startBuildGraph: ${err.message}`)
  }
}

const startGraphPolling = () => {
  addLog('Started polling for graph data...')
  fetchGraphData()
  graphPollTimer = setInterval(fetchGraphData, 10000)
}

const fetchGraphData = async () => {
  try {
    const projRes = await getProject(currentProjectId.value)
    if (projRes.success && projRes.data.graph_id) {
      const gRes = await getGraphData(projRes.data.graph_id)
      if (gRes.success) {
        graphData.value = gRes.data
        const nodeCount = gRes.data.node_count || gRes.data.nodes?.length || 0
        const edgeCount = gRes.data.edge_count || gRes.data.edges?.length || 0
        addLog(`Graph data refreshed. Nodes: ${nodeCount}, Edges: ${edgeCount}`)
      }
    }
  } catch (err) {
    console.warn('Graph fetch error:', err)
  }
}

const startPollingTask = (taskId) => {
  pollTaskStatus(taskId)
  pollTimer = setInterval(() => pollTaskStatus(taskId), 2000)
}

const pollTaskStatus = async (taskId) => {
  try {
    const res = await getTaskStatus(taskId)
    if (res.success) {
      const task = res.data
      if (task.message && task.message !== buildProgress.value?.message) addLog(task.message)
      buildProgress.value = { progress: task.progress || 0, message: task.message }
      if (task.status === 'completed') {
        addLog('Graph build task completed.')
        stopPolling()
        stopGraphPolling()
        currentPhase.value = 2
        const projRes = await getProject(currentProjectId.value)
        if (projRes.success && projRes.data.graph_id) {
          projectData.value = projRes.data
          await loadGraph(projRes.data.graph_id)
        }
      } else if (task.status === 'failed') {
        stopPolling()
        error.value = task.error
        addLog(`Graph build task failed: ${task.error}`)
      }
    }
  } catch (e) {
    console.error(e)
  }
}

const loadGraph = async (graphId) => {
  graphLoading.value = true
  addLog(`Loading full graph data: ${graphId}`)
  try {
    const res = await getGraphData(graphId)
    if (res.success) {
      graphData.value = res.data
      addLog('Graph data loaded successfully.')
    } else {
      addLog(`Failed to load graph data: ${res.error}`)
    }
  } catch (e) {
    addLog(`Exception loading graph: ${e.message}`)
  } finally {
    graphLoading.value = false
  }
}

const refreshGraph = () => {
  if (projectData.value?.graph_id) {
    addLog('Manual graph refresh triggered.')
    loadGraph(projectData.value.graph_id)
  }
}

const stopPolling = () => {
  if (pollTimer) { clearInterval(pollTimer); pollTimer = null }
}

const stopGraphPolling = () => {
  if (graphPollTimer) { clearInterval(graphPollTimer); graphPollTimer = null; addLog('Graph polling stopped.') }
}

onMounted(() => { initProject() })
onUnmounted(() => { stopPolling(); stopGraphPolling() })
</script>
