<template>
  <div class="main-view">
    <header class="app-header">
      <div class="header-left">
        <div class="brand" @click="router.push('/')">Vega</div>
      </div>

      <div class="header-center">
        <div class="view-switcher">
          <button
            v-for="mode in ['graph', 'split', 'workbench']"
            :key="mode"
            class="switch-btn"
            :class="{ active: viewMode === mode }"
            @click="$emit('update:viewMode', mode)"
          >
            {{ { graph: 'Graph', split: 'Split', workbench: 'Workbench' }[mode] }}
          </button>
        </div>
      </div>

      <div class="header-right">
        <div class="workflow-step">
          <span class="step-num">Step {{ stepNum }}/5</span>
          <span class="step-name">{{ stepName }}</span>
        </div>
        <div class="step-divider"></div>
        <span class="status-indicator" :class="statusClass">
          <span class="dot"></span>
          {{ statusText }}
        </span>
      </div>
    </header>

    <main class="content-area">
      <div class="panel-wrapper left" :style="leftPanelStyle">
        <slot name="left" />
      </div>
      <div class="panel-wrapper right" :style="rightPanelStyle">
        <slot name="right" />
      </div>
    </main>
  </div>
</template>

<script setup>
import { computed } from 'vue'
import { useRouter } from 'vue-router'

const router = useRouter()

const props = defineProps({
  viewMode:    { type: String, required: true },
  stepNum:     { type: Number, default: 1 },
  stepName:    { type: String, default: '' },
  statusClass: { type: String, default: '' },
  statusText:  { type: String, default: '' }
})

defineEmits(['update:viewMode'])

const leftPanelStyle = computed(() => {
  if (props.viewMode === 'graph')     return { width: '100%', opacity: 1, transform: 'translateX(0)' }
  if (props.viewMode === 'workbench') return { width: '0%',   opacity: 0, transform: 'translateX(-20px)' }
  return { width: '50%', opacity: 1, transform: 'translateX(0)' }
})

const rightPanelStyle = computed(() => {
  if (props.viewMode === 'workbench') return { width: '100%', opacity: 1, transform: 'translateX(0)' }
  if (props.viewMode === 'graph')     return { width: '0%',   opacity: 0, transform: 'translateX(20px)' }
  return { width: '50%', opacity: 1, transform: 'translateX(0)' }
})
</script>

<style src="../../styles/AppLayout.css"></style>
