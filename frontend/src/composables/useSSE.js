import { ref, onUnmounted } from 'vue'

/**
 * Wraps EventSource lifecycle with automatic cleanup on component unmount.
 *
 * @param {Function} createStreamFn - Factory that returns a new EventSource
 * @param {object} [options]
 * @param {Function} [options.onMessage] - Called with the MessageEvent on each message
 * @param {Function} [options.onError]   - Called with the Event on connection error
 * @returns {{ connect: Function, disconnect: Function, isConnected: Ref<boolean> }}
 *
 * Usage:
 *   const { connect, disconnect, isConnected } = useSSE(
 *     () => createFoScannerStream(),
 *     { onMessage: (e) => handleEvent(JSON.parse(e.data)) }
 *   )
 *   onMounted(connect)
 */
export function useSSE(createStreamFn, { onMessage, onError } = {}) {
  const isConnected = ref(false)
  let es = null

  const connect = () => {
    if (es) return
    es = createStreamFn()
    isConnected.value = true

    es.onmessage = (e) => onMessage?.(e)
    es.onerror = (e) => {
      isConnected.value = false
      onError?.(e)
    }
  }

  const disconnect = () => {
    if (!es) return
    es.close()
    es = null
    isConnected.value = false
  }

  onUnmounted(disconnect)

  return { connect, disconnect, isConnected }
}
