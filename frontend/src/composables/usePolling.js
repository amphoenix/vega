import { ref, onUnmounted } from 'vue'

/**
 * Wraps setInterval with automatic cleanup on component unmount.
 *
 * @param {Function} fn - Function to call on each tick
 * @param {number} intervalMs - Interval in milliseconds
 * @returns {{ start: Function, stop: Function, isRunning: Ref<boolean> }}
 *
 * Usage:
 *   const { start, stop, isRunning } = usePolling(fetchStatus, 2000)
 *   onMounted(start)
 */
export function usePolling(fn, intervalMs) {
  const isRunning = ref(false)
  let timer = null

  const start = () => {
    if (isRunning.value) return
    isRunning.value = true
    fn()
    timer = setInterval(fn, intervalMs)
  }

  const stop = () => {
    if (!isRunning.value) return
    clearInterval(timer)
    timer = null
    isRunning.value = false
  }

  onUnmounted(stop)

  return { start, stop, isRunning }
}
