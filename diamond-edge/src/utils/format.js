export function toFiniteNumber(value) {
  if (value == null || value === '') return null
  const num = typeof value === 'number' ? value : Number(String(value).trim())
  return Number.isFinite(num) ? num : null
}

export function fmtNumber(value, decimals = 2, fallback = '—') {
  const num = toFiniteNumber(value)
  return num == null ? fallback : num.toFixed(decimals)
}

export function fmtPercent(value, decimals = 1, fallback = '—') {
  const num = toFiniteNumber(value)
  return num == null ? fallback : `${(num * 100).toFixed(decimals)}%`
}

export function fmtMoney(value, decimals = 2, fallback = '—', currency = '€') {
  const num = toFiniteNumber(value)
  return num == null ? fallback : `${currency}${num.toFixed(decimals)}`
}

export function fmtSignedMoney(value, decimals = 2, fallback = '—', currency = '€') {
  const num = toFiniteNumber(value)
  if (num == null) return fallback
  const sign = num > 0 ? '+' : num < 0 ? '-' : ''
  return `${sign}${currency}${Math.abs(num).toFixed(decimals)}`
}

export function safeText(value, fallback = '—') {
  if (value == null || value === '') return fallback
  return String(value)
}
