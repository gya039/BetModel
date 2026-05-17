import { useState, useEffect } from 'react'
import { fetchCsv } from '../utils/parseCsv'
import { todayStr, prevDate, predictionsPath } from '../utils/paths'

let cache = null

export function useAccumulators() {
  const [accas, setAccas]     = useState(null)
  const [loading, setLoading] = useState(true)
  const [error, setError]     = useState(null)

  useEffect(() => {
    if (cache) {
      setAccas(cache)
      setLoading(false)
      return
    }

    const today     = todayStr()
    const yesterday = prevDate(today)

    Promise.all([
      fetchCsv('mlb/predictions/accumulators_log.csv').catch(() => []),
      // Try today's JSON first, fall back to yesterday's
      fetch(predictionsPath(today)).then(r => r.ok ? r.json() : null).catch(() => null),
      fetch(predictionsPath(yesterday)).then(r => r.ok ? r.json() : null).catch(() => null),
    ])
      .then(([settled, todayJson, yestJson]) => {
        const mainJson    = todayJson ?? yestJson
        const pendingDate = todayJson ? today : yesterday

        // Build set of already-settled acca keys for today to avoid duplicates
        const settledKeys = new Set(
          (settled ?? [])
            .filter(r => r.date === pendingDate)
            .map(r => r.type + '_' + r.date)
        )

        // Pull pending accas from predictions JSON
        const pendingRows = []
        for (const acca of (mainJson?.accumulators ?? [])) {
          const key = acca.type + '_' + pendingDate
          if (settledKeys.has(key)) continue
          pendingRows.push({
            date:           pendingDate,
            type:           acca.type,
            legs:           JSON.stringify(
              (acca.legs ?? []).map(l => ({
                label:    l.label,
                odds:     l.odds,
                line:     l.line ?? 'ml',
                pickSide: l.pickSide ?? '',
                legResult: '',   // not yet settled
              }))
            ),
            combined_odds:  String(acca.combined_odds ?? ''),
            stake:          String(acca.stake ?? ''),
            result:         'Pending',
            pnl:            '',
            _pending:       true,
          })
        }

        const all = [...(settled ?? []), ...pendingRows]
        cache = all
        setAccas(all)
        setLoading(false)
      })
      .catch(err => {
        setError(err.message)
        setLoading(false)
      })
  }, [])

  const stats = accas ? computeAccaStats(accas) : null
  return { accas, loading, error, stats }
}

function computeAccaStats(rows) {
  const settled = rows.filter(r => r.result && r.result !== 'Pending' && r.result !== '')
  const wins    = settled.filter(r => r.result === 'Win').length
  const losses  = settled.filter(r => r.result === 'Loss').length
  const staked  = settled.reduce((s, r) => s + (Number(r.stake) || 0), 0)
  const pnl     = settled.reduce((s, r) => s + (Number(r.pnl) || 0), 0)
  const roi     = staked > 0 ? pnl / staked : 0
  const pending = rows.filter(r => r.result === 'Pending').length

  const byType = {}
  for (const r of settled) {
    if (!byType[r.type]) byType[r.type] = { wins: 0, losses: 0, pnl: 0 }
    byType[r.type].wins   += r.result === 'Win' ? 1 : 0
    byType[r.type].losses += r.result === 'Loss' ? 1 : 0
    byType[r.type].pnl    += Number(r.pnl) || 0
  }

  return { total: settled.length, wins, losses, pending, staked, pnl, roi, byType }
}
