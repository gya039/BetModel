export function ordinal(n) {
  const s = ['th', 'st', 'nd', 'rd']
  const v = n % 100
  return n + (s[(v - 20) % 10] || s[v] || s[0])
}

export function predictionsPath(dateStr) {
  const dt = new Date(dateStr + 'T12:00:00')
  const month = dt.toLocaleString('en-US', { month: 'long' })
  const day = dt.getDate()
  const year = dt.getFullYear()
  const ord = ordinal(day)
  return `mlb/predictions/${month} Predictions/${month} ${ord}/${month} ${ord} ${year} Predictions.json`
}

export function updatedPath(dateStr) {
  const dt = new Date(dateStr + 'T12:00:00')
  const month = dt.toLocaleString('en-US', { month: 'long' })
  const day = dt.getDate()
  const year = dt.getFullYear()
  const ord = ordinal(day)
  return `mlb/predictions/${month} Predictions/${month} ${ord}/${month} ${ord} ${year} Predictions (Updated).json`
}

export function todayStr() {
  return new Date().toISOString().split('T')[0]
}

export function formatDisplayDate(dateStr) {
  const dt = new Date(dateStr + 'T12:00:00')
  return dt.toLocaleDateString('en-US', { weekday: 'short', month: 'short', day: 'numeric' })
}

export function formatMonthYear(dateStr) {
  const dt = new Date(dateStr + 'T12:00:00')
  return dt.toLocaleDateString('en-US', { month: 'long', year: 'numeric' })
}

export function prevDate(dateStr) {
  const dt = new Date(dateStr + 'T12:00:00')
  dt.setDate(dt.getDate() - 1)
  return dt.toISOString().split('T')[0]
}

export function nextDate(dateStr) {
  const dt = new Date(dateStr + 'T12:00:00')
  dt.setDate(dt.getDate() + 1)
  return dt.toISOString().split('T')[0]
}

export function isFuture(dateStr) {
  return dateStr > todayStr()
}

export function datesInMonth(year, month) {
  const days = new Date(year, month + 1, 0).getDate()
  return Array.from({ length: days }, (_, i) => {
    const d = i + 1
    return `${year}-${String(month + 1).padStart(2, '0')}-${String(d).padStart(2, '0')}`
  })
}

// Try to find the most recent available predictions file
// Tries today, then walks back up to 14 days
export async function findLatestDate() {
  const today = todayStr()
  for (let i = 0; i < 14; i++) {
    const dt = new Date(today + 'T12:00:00')
    dt.setDate(dt.getDate() - i)
    const d = dt.toISOString().split('T')[0]
    const path = predictionsPath(d)
    try {
      const res = await fetch(path)
      if (res.ok && (res.headers.get('content-type') || '').includes('json')) return d
    } catch {
      // continue
    }
  }
  return today
}
