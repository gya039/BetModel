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
    ])
      .then(([currentJson, updatedJson]) => {
        if (cancelled) return
        const merged = {
          current: currentJson,
          updated: updatedJson ?? null,
        }
        // Only cache when updated data is present — if it's null (check-movement
        // hasn't run yet), skip caching so the next render re-fetches and picks
        // it up once the file exists.
        if (merged.updated !== null) cache.set(dateStr, merged)
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
