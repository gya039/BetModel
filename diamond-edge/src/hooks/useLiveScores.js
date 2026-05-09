import { useState, useEffect, useRef } from 'react'

const MLB_API = 'https://statsapi.mlb.com/api/v1'

async function fetchMlbStatuses(dateStr) {
  const url = `${MLB_API}/schedule?sportId=1&date=${dateStr}&hydrate=linescore`
  const res = await fetch(url)
  if (!res.ok) throw new Error(`MLB API ${res.status}`)
  const json = await res.json()

  const games = {}
  for (const dateBlock of json.dates ?? []) {
    for (const g of dateBlock.games ?? []) {
      const abstract = g.status?.abstractGameState  // 'Preview' | 'Live' | 'Final'
      const detailed = g.status?.detailedState || ''
      const gameStatus = abstract === 'Live' ? 'LIVE' : abstract === 'Final' ? 'FINAL' : 'SCHEDULED'
      games[String(g.gamePk)] = {
        gameStatus,
        detailedState: detailed,
        awayScore: g.teams?.away?.score ?? null,
        homeScore: g.teams?.home?.score ?? null,
        currentInning: g.linescore?.currentInning ?? null,
        inningHalf: g.linescore?.inningHalf ?? null,
      }
    }
  }
  return { games }
}

export function useLiveScores(dateStr) {
  const [statuses, setStatuses] = useState(null)
  const timerRef = useRef(null)

  useEffect(() => {
    if (!dateStr) return
    let cancelled = false

    async function poll() {
      try {
        const data = await fetchMlbStatuses(dateStr)
        if (!cancelled) setStatuses(data)

        // Re-poll every 60s only if any game is LIVE
        const hasLive = Object.values(data.games).some(g => g.gameStatus === 'LIVE')
        if (hasLive && !cancelled) {
          timerRef.current = setTimeout(poll, 60_000)
        }
      } catch {
        // silently ignore — live scores are best-effort
      }
    }

    poll()
    return () => {
      cancelled = true
      clearTimeout(timerRef.current)
    }
  }, [dateStr])

  return statuses
}
