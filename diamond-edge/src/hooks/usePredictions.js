import { useState, useEffect } from 'react'
import { predictionsPath, updatedPath } from '../utils/paths'

const cache = new Map()

export function usePredictions(dateStr) {
  const [data, setData]       = useState(null)
  const [loading, setLoading] = useState(true)
  const [error, setError]     = useState(null)

  useEffect(() => {
    if (!dateStr) return
    let cancelled = false

    if (cache.has(dateStr)) {
      setData(cache.get(dateStr))
      setLoading(false)
      setError(null)
      return
    }

    setLoading(true)
    setError(null)
    setData(null)

    Promise.all([
      fetch(predictionsPath(dateStr)).then(r => {
        if (!r.ok) throw new Error(`No predictions file found for ${dateStr}`)
        return r.json()
      }),
      fetch(updatedPath(dateStr))
        .then(r => (r.ok ? r.json() : null))
        .catch(() => null),
      fetch(`api/mlb-status?date=${dateStr}`)
        .then(r => (r.ok ? r.json() : null))
        .catch(() => null),
    ])
      .then(([currentJson, updatedJson, statuses]) => {
        if (cancelled) return
        const merged = {
          current: mergeGameStatuses(currentJson, statuses),
          updated: updatedJson ? mergeGameStatuses(updatedJson, statuses) : null,
        }
        cache.set(dateStr, merged)
        setData(merged)
        setLoading(false)
      })
      .catch(err => {
        if (cancelled) return
        setError(err.message)
        setLoading(false)
      })

    return () => { cancelled = true }
  }, [dateStr])

  return { data, loading, error }
}

function mergeGameStatuses(payload, statuses) {
  const games = statuses?.games
  if (!games || !payload?.predictions) return payload

  return {
    ...payload,
    predictions: payload.predictions.map(game => {
      const live = games[String(game.gamePk)]
      if (!live) return game
      return {
        ...game,
        gameStatus: live.gameStatus || game.gameStatus,
        detailedState: live.detailedState || game.detailedState,
        finalScore: live.gameStatus === 'FINAL'
          ? { away: live.awayScore, home: live.homeScore }
          : game.finalScore,
        liveScore: live.gameStatus === 'LIVE'
          ? {
              away: live.awayScore,
              home: live.homeScore,
              inning: live.currentInning,
              half: live.inningHalf,
              detail: live.detailedState,
            }
          : game.liveScore,
      }
    }),
  }
}
