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

export function fmtTime(iso) {
  if (!iso) return ''
  try {
    return new Date(iso).toLocaleTimeString('en-IN', {
      hour: '2-digit',
      minute: '2-digit',
      second: '2-digit',
    })
  } catch {
    return iso
  }
}
