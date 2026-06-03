<template>
  <Teleport to="body">
    <TransitionGroup name="snack" tag="div" class="snackbar-stack">
      <div
        v-for="item in items"
        :key="item.id"
        :class="['snackbar-item', `snackbar-${item.severity}`]"
        @click="dismiss(item.id)"
      >
        <span class="snackbar-icon">{{ icon(item.severity) }}</span>
        <div class="snackbar-body">
          <span class="snackbar-title">{{ item.title }}</span>
          <span v-if="item.message" class="snackbar-msg">{{ item.message }}</span>
        </div>
        <span class="snackbar-close">&times;</span>
      </div>
    </TransitionGroup>
  </Teleport>
</template>

<script setup>
import { ref, onMounted, onUnmounted } from 'vue'

const items = ref([])
let nextId = 0
const MAX_VISIBLE = 3

function icon(severity) {
  switch (severity) {
    case 'success': return '✓'
    case 'error':   return '✕'
    case 'warning': return '!'
    default:        return 'ℹ'
  }
}

function push(opts) {
  const title = opts.title || ''
  const message = opts.message || ''

  // Deduplicate: if same title+message already visible, skip
  const dup = items.value.find(i => i.title === title && i.message === message)
  if (dup) return

  const id = ++nextId
  const item = {
    id,
    severity: opts.severity || 'info',
    title,
    message,
    duration: opts.duration ?? 5000,
  }
  items.value.push(item)

  // Cap visible snackbars — remove oldest when exceeding limit
  while (items.value.length > MAX_VISIBLE) {
    items.value.shift()
  }

  if (item.duration > 0) {
    setTimeout(() => dismiss(id), item.duration)
  }
}

function dismiss(id) {
  items.value = items.value.filter(i => i.id !== id)
}

// Expose push method globally via a simple event bus
function onSnack(e) {
  push(e.detail)
}

onMounted(() => {
  window.addEventListener('phoenix:snack', onSnack)
})

onUnmounted(() => {
  window.removeEventListener('phoenix:snack', onSnack)
})

defineExpose({ push })
</script>

<style src="../../styles/SnackBar.css"></style>
