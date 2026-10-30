// Sends the Vega API key (VEGA_API_KEY on the server) with every call to the API.
//
// Installed once in main.js, so no call site needs to know about it:
//   - fetch()        → header  X-Vega-Key
//   - EventSource    → query   ?key=…   (browsers cannot set headers on streams)
//   - axios          → header, via apiKey() in api/index.js
//
// The key comes from VITE_API_KEY (written to .env.local by start.sh from VEGA_API_KEY) or, on a
// device where you don't run start.sh, from localStorage:  localStorage.setItem('vega_api_key', '…')

const STORAGE_KEY = 'vega_api_key'

function storedKey() {
  try {
    return (typeof localStorage !== 'undefined' && localStorage.getItem(STORAGE_KEY)) || ''
  } catch {
    return ''
  }
}

export function apiKey() {
  return import.meta.env.VITE_API_KEY || storedKey()
}

// Calls to anything other than the API itself (fonts, other sites) never get the key.
export function isApiUrl(url, base, origin) {
  try {
    const target = new URL(String(url), origin)
    const apiOrigin = new URL(base || origin, origin).origin
    return target.origin === apiOrigin && (target.pathname.startsWith('/api/') || target.pathname.startsWith('/paper/'))
  } catch {
    return false
  }
}

export function withKey(url, key) {
  const absolute = /^https?:/i.test(String(url))
  const u = new URL(String(url), 'http://placeholder.invalid')
  u.searchParams.set('key', key)
  return absolute ? u.toString() : u.pathname + u.search + u.hash
}

// Pure and testable: patches the given `win` (a browser window, or a fake one in tests).
export function installOn(win, { key, base }) {
  if (!key) return false
  const origin = win.location.origin
  const realFetch = win.fetch.bind(win)
  win.fetch = (input, init = {}) => {
    const url = typeof input === 'string' ? input : (input && input.url) || String(input)
    if (!isApiUrl(url, base, origin)) return realFetch(input, init)
    const headers = new win.Headers(init.headers || (typeof input === 'string' ? undefined : input.headers))
    headers.set('X-Vega-Key', key)
    return realFetch(input, { ...init, headers })
  }
  const RealEventSource = win.EventSource
  if (RealEventSource) {
    win.EventSource = class extends RealEventSource {
      constructor(url, config) {
        super(isApiUrl(url, base, origin) ? withKey(url, key) : url, config)
      }
    }
  }
  return true
}

export function installApiAuth() {
  return installOn(window, { key: apiKey(), base: import.meta.env.VITE_API_BASE_URL || '' })
}
