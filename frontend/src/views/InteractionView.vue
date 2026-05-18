<template>
  <AppLayout
    v-model:viewMode="viewMode"
    :step-num="5"
    step-name="Deep Interaction"
    :status-class="statusClass"
    :status-text="statusText"
  >
    <template #left>
      <GraphPanel
        :graphData="graphData"
        :loading="graphLoading"
        :currentPhase="5"
        :isSimulating="false"
        @refresh="refreshGraph"
        @toggle-maximize="toggleMaximize('graph')"
      />
    </template>
    <template #right>
      <Step5Interaction
        :reportId="currentReportId"
        :simulationId="simulationId"
        :systemLogs="systemLogs"
        @add-log="addLog"
        @update-status="updateStatus"
      />
    </template>
  </AppLayout>
</template>

<script setup>
import { ref, computed, onMounted, watch } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import AppLayout from '../components/layout/AppLayout.vue'
import GraphPanel from '../components/chart/GraphPanel.vue'
import Step5Interaction from '../components/simulation/Step5Interaction.vue'
import { getProject, getGraphData } from '../api/graph'
import { getSimulation } from '../api/simulation'
import { getReport } from '../api/report'
import { fmtTime } from '../utils/formatters'

const route = useRoute()
const router = useRouter()

defineProps({ reportId: String })

const viewMode = ref('workbench')
const currentReportId = ref(route.params.reportId)
const simulationId = ref(null)
const projectData = ref(null)
const graphData = ref(null)
const graphLoading = ref(false)
const systemLogs = ref([])
const currentStatus = ref('ready')

const statusClass = computed(() => currentStatus.value)
const statusText = computed(() => {
  if (currentStatus.value === 'error') return 'Error'
  if (currentStatus.value === 'completed') return 'Completed'
  if (currentStatus.value === 'processing') return 'Processing'
  return 'Ready'
})

const addLog = (msg) => {
  const time = fmtTime(new Date(), { ms: true })
  systemLogs.value.push({ time, msg })
  if (systemLogs.value.length > 200) systemLogs.value.shift()
}

const updateStatus = (status) => { currentStatus.value = status }

const toggleMaximize = (target) => {
  viewMode.value = viewMode.value === target ? 'split' : target
}

const loadReportData = async () => {
  try {
    addLog(`Loading report data: ${currentReportId.value}`)
    const reportRes = await getReport(currentReportId.value)
    if (reportRes.success && reportRes.data) {
      simulationId.value = reportRes.data.simulation_id
      if (simulationId.value) {
        const simRes = await getSimulation(simulationId.value)
        if (simRes.success && simRes.data?.project_id) {
          const projRes = await getProject(simRes.data.project_id)
          if (projRes.success && projRes.data) {
            projectData.value = projRes.data
            addLog(`Project loaded successfully: ${projRes.data.project_id}`)
            if (projRes.data.graph_id) await loadGraph(projRes.data.graph_id)
          }
        }
      }
    } else {
      addLog(`Failed to get report info: ${reportRes.error || 'Unknown error'}`)
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

watch(() => route.params.reportId, (newId) => {
  if (newId && newId !== currentReportId.value) { currentReportId.value = newId; loadReportData() }
}, { immediate: true })

onMounted(() => { addLog('InteractionView initialized'); loadReportData() })
</script>
