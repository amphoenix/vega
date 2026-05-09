<template>
  <div class="console-logs">
    <div class="log-header">
      <span class="log-title">CONSOLE OUTPUT</span>
      <span class="log-id">{{ reportId || 'NO_REPORT' }}</span>
    </div>
    <div class="log-content" ref="logContent">
      <div class="log-line" v-for="(log, idx) in consoleLogs" :key="idx">
        <span class="log-msg" :class="getLogLevelClass(log)">{{ log }}</span>
      </div>
    </div>
  </div>
</template>

<script setup>
import { ref, watch, onMounted, onUnmounted, nextTick } from 'vue'
import { getConsoleLog } from '../../api/report'

const props = defineProps({ reportId: String })

const consoleLogs = ref([])
const consoleLogLine = ref(0)
const logContent = ref(null)
let _timer = null

function getLogLevelClass(log) {
  if (log.includes('ERROR')) return 'error'
  if (log.includes('WARNING')) return 'warning'
  return ''
}

async function fetchLogs() {
  if (!props.reportId) return
  try {
    const res = await getConsoleLog(props.reportId, consoleLogLine.value)
    if (res.success && res.data) {
      const newLogs = res.data.logs || []
      if (newLogs.length > 0) {
        consoleLogs.value.push(...newLogs)
        consoleLogLine.value = res.data.from_line + newLogs.length
        nextTick(() => {
          if (logContent.value) logContent.value.scrollTop = logContent.value.scrollHeight
        })
      }
    }
  } catch (err) {
    console.warn('Failed to fetch console log:', err)
  }
}

function startPolling() {
  if (_timer) return
  fetchLogs()
  _timer = setInterval(fetchLogs, 1500)
}

function stopPolling() {
  if (_timer) { clearInterval(_timer); _timer = null }
}

watch(
  () => props.reportId,
  (id) => {
    consoleLogs.value = []
    consoleLogLine.value = 0
    stopPolling()
    if (id) startPolling()
  },
  { immediate: true },
)

onUnmounted(stopPolling)
</script>

<style src="../../styles/ConsoleLog.css"></style>
