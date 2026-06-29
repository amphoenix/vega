/**
 * Play distinct notification sounds using the Web Audio API.
 * No external audio file needed — synthesized on-the-fly.
 *
 * Order event sounds:
 *   playNotifSound('entry_buy')       — bright rising arpeggio (you bought!)
 *   playNotifSound('sl_exit')         — urgent alarm (stop-loss hit)
 *   playNotifSound('t1_exit')         — positive cash-register ding
 *   playNotifSound('t2_exit')         — triumphant double-ding
 *   playNotifSound('time_exit')       — muted clock chime
 *   playNotifSound('thesis_exit')     — direction-flip warning siren
 *   playNotifSound('slippage_reject') — flat rejection buzz
 *
 * Generic severity fallbacks:
 *   playNotifSound('success')  — pleasant chime
 *   playNotifSound('error')    — urgent double beep
 *   playNotifSound('warning')  — single alert tone
 *   playNotifSound('info')     — soft ping
 */
let _audioCtx = null

// ── Global mute (persisted in localStorage) ────────────────────────────────
// Lazy read — avoids ReferenceError in Node/SSR where localStorage is undefined.
let _muted = false
try { _muted = localStorage.getItem('vega_muted') === '1' } catch {}

export function isMuted() { return _muted }

export function setMuted(val) {
  _muted = !!val
  localStorage.setItem('vega_muted', _muted ? '1' : '0')
  window.dispatchEvent(new CustomEvent('vega:mute-changed', { detail: _muted }))
}

export function toggleMute() { setMuted(!_muted); return _muted }

function _ctx() {
  if (!_audioCtx) {
    _audioCtx = new (window.AudioContext || window.webkitAudioContext)()
  }
  if (_audioCtx.state === 'suspended') _audioCtx.resume()
  return _audioCtx
}

function _beep(freq = 520, duration = 0.15, volume = 0.3, type = 'sine', delay = 0) {
  if (_muted) return
  try {
    const ctx = _ctx()
    const osc = ctx.createOscillator()
    const gain = ctx.createGain()
    osc.type = type
    osc.frequency.value = freq
    gain.gain.setValueAtTime(volume, ctx.currentTime + delay)
    gain.gain.exponentialRampToValueAtTime(0.001, ctx.currentTime + delay + duration)
    osc.connect(gain)
    gain.connect(ctx.destination)
    osc.start(ctx.currentTime + delay)
    osc.stop(ctx.currentTime + delay + duration + 0.05)
  } catch {
    // Audio not available — fail silently
  }
}

export function playNotifSound(severity = 'info') {
  switch (severity) {
    // ── Order-specific sounds ────────────────────────────────────────────
    case 'entry_buy':                              // bright rising arpeggio
      _beep(523, 0.1, 0.25, 'sine', 0)            // C5
      _beep(659, 0.1, 0.25, 'sine', 0.1)          // E5
      _beep(784, 0.1, 0.25, 'sine', 0.2)          // G5
      _beep(1046, 0.2, 0.2, 'sine', 0.3)          // C6
      break
    case 'sl_exit':                                // urgent alarm
      _beep(880, 0.12, 0.35, 'square', 0)         // A5 harsh
      _beep(660, 0.12, 0.35, 'square', 0.15)      // E5 drop
      _beep(880, 0.12, 0.35, 'square', 0.3)       // A5 repeat
      _beep(660, 0.2, 0.3, 'square', 0.45)        // E5 final
      break
    case 't1_exit':                                // cash-register ding
      _beep(1200, 0.08, 0.2, 'sine', 0)
      _beep(1500, 0.15, 0.25, 'sine', 0.1)
      break
    case 't2_exit':                                // triumphant double-ding
      _beep(1046, 0.1, 0.2, 'sine', 0)
      _beep(1318, 0.1, 0.2, 'sine', 0.12)
      _beep(1568, 0.1, 0.2, 'sine', 0.24)
      _beep(2093, 0.25, 0.18, 'sine', 0.36)
      break
    case 'time_exit':                              // muted clock chime
      _beep(440, 0.2, 0.15, 'triangle', 0)
      _beep(440, 0.2, 0.15, 'triangle', 0.3)
      _beep(440, 0.2, 0.15, 'triangle', 0.6)
      break
    case 'thesis_exit':                            // direction-flip siren
      _beep(600, 0.15, 0.3, 'sawtooth', 0)
      _beep(800, 0.15, 0.3, 'sawtooth', 0.15)
      _beep(600, 0.15, 0.3, 'sawtooth', 0.3)
      _beep(800, 0.2, 0.25, 'sawtooth', 0.45)
      break
    case 'slippage_reject':                        // flat rejection buzz
      _beep(200, 0.25, 0.2, 'square', 0)
      break
    case 'signal':                                 // scalp signal alert — loud attention grab
      _beep(880, 0.1, 0.4, 'sine', 0)             // A5
      _beep(1100, 0.1, 0.4, 'sine', 0.12)         // ~C#6
      _beep(880, 0.1, 0.4, 'sine', 0.24)          // A5 repeat
      _beep(1320, 0.2, 0.35, 'sine', 0.36)        // E6 resolve
      break
    case 'sl_hit':                                 // alias for sl_exit
      _beep(880, 0.12, 0.35, 'square', 0)
      _beep(660, 0.12, 0.35, 'square', 0.15)
      _beep(880, 0.12, 0.35, 'square', 0.3)
      _beep(660, 0.2, 0.3, 'square', 0.45)
      break
    case 'exit_sell':                              // alias for t1/successful exit
      _beep(1200, 0.08, 0.2, 'sine', 0)
      _beep(1500, 0.15, 0.25, 'sine', 0.1)
      break

    // ── Generic severity fallbacks ───────────────────────────────────────
    case 'success':
      _beep(523, 0.12, 0.25, 'sine', 0)
      _beep(659, 0.12, 0.25, 'sine', 0.1)
      _beep(784, 0.18, 0.2, 'sine', 0.2)
      break
    case 'error':
      _beep(440, 0.15, 0.35, 'square', 0)
      _beep(440, 0.15, 0.35, 'square', 0.2)
      break
    case 'warning':
      _beep(600, 0.2, 0.3, 'triangle', 0)
      break
    default: // info
      _beep(700, 0.12, 0.2, 'sine', 0)
  }
}
