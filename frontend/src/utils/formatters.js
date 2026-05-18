export function fmtPrice(n) {
  if (n === null || n === undefined) return '—'
  if (n >= 10000) return n.toLocaleString('en', { maximumFractionDigits: 0 })
  if (n >= 100) return n.toLocaleString('en', { maximumFractionDigits: 2 })
  return n.toFixed(4)
}

export function fmtChg(v) {
  if (v === null || v === undefined) return '—'
  return (v >= 0 ? '+' : '') + Number(v).toFixed(2) + '%'
}

export function fmtNum(n) {
  if (n >= 1000) return (n / 1000).toFixed(1) + 'k'
  return n
}

// Indian-style large number formatter: 1.54 Cr, 85.6 L, 12.5k
export function fmtIndian(n) {
  const v = Number(n) || 0
  if (v === 0) return '—'
  if (v >= 1e7) return (v / 1e7).toFixed(2).replace(/\.?0+$/, '') + ' Cr'
  if (v >= 1e5) return (v / 1e5).toFixed(2).replace(/\.?0+$/, '') + ' L'
  if (v >= 1e3) return (v / 1e3).toFixed(1).replace(/\.?0+$/, '') + 'k'
  return v.toLocaleString('en-IN')
}

// Single IST formatter for all date/time display across the app.
// Always renders in Asia/Kolkata regardless of browser TZ.
//
//   fmtTime(iso)                              → 'HH:MM:SS'          (default)
//   fmtTime(iso, { seconds: false })          → 'HH:MM'
//   fmtTime(iso, { ms: true })                → 'HH:MM:SS.mmm'
//   fmtTime(iso, { mode: 'date' })            → 'DD/MM/YYYY'
//   fmtTime(iso, { mode: 'datetime' })        → 'DD/MM/YYYY, HH:MM:SS'
//
// Accepts: ISO string, Date, epoch ms, or null/undefined (returns '').
export function fmtTime(value, opts = {}) {
  if (value == null || value === '') return ''
  const d = value instanceof Date ? value : new Date(value)
  if (isNaN(d.getTime())) return String(value)

  const mode = opts.mode || 'time'  // 'time' | 'date' | 'datetime'
  const seconds = opts.seconds !== false  // default true

  const fmt = { timeZone: 'Asia/Kolkata', hour12: false }
  if (mode === 'time' || mode === 'datetime') {
    fmt.hour = '2-digit'
    fmt.minute = '2-digit'
    if (seconds) fmt.second = '2-digit'
  }
  if (mode === 'date' || mode === 'datetime') {
    fmt.year = 'numeric'
    fmt.month = '2-digit'
    fmt.day = '2-digit'
  }

  let s = new Intl.DateTimeFormat('en-IN', fmt).format(d)
  if (opts.ms && (mode === 'time' || mode === 'datetime')) {
    s += '.' + String(d.getMilliseconds()).padStart(3, '0')
  }
  return s
}
