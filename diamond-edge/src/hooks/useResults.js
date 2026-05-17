import { useState, useEffect } from 'react'
import { fetchCsv } from '../utils/parseCsv'
import { toFiniteNumber } from '../utils/format'
import { predictionsPath, updatedPath, todayStr, prevDate } from '../utils/paths'
import { decisionForRow, pickLabel } from '../utils/decisions'

export function clearResultsCache() {}

export function useResults() {
  const [results, setResults] = useState(null)
  const [updated, setUpdated] = useState(null)
  const [loading, setLoading] = useState(true)
  const [error, setError]     = useState(null)

  useEffect(() => {
    const today     = todayStr()
    const yesterday = prevDate(today)
    const jsonOpts  = { cache: 'no-store' }

    Promise.all([
      fetchCsv('mlb/predictions/results_log.csv'),
      fetchCsv('mlb/predictions/results_log_updated.csv').catch(() => []),
      // Try today first, fall back to yesterday if today's file doesn't exist yet
      fetch(predictionsPath(today), jsonOpts).then(r => r.ok ? r.json() : null).catch(() => null),
      fetch(updatedPath(today), jsonOpts).then(r => r.ok ? r.json() : null).catch(() => null),
      fetch(predictionsPath(yesterday), jsonOpts).then(r => r.ok ? r.json() : null).catch(() => null),
      fetch(updatedPath(yesterday), jsonOpts).then(r => r.ok ? r.json() : null).catch(() => null),
    ])
      .then(([r, u, todayJson, todayUpdatedJson, yestJson, yestUpdatedJson]) => {
        const resultsRows = normalizeRows(r)
        const updatedRows = normalizeRows(u)

        // Use today's predictions if they exist, otherwise fall back to yesterday's.
        // Never cross-dates: if today's morning JSON exists, the Updated JSON must
        // also be today's — falling back to yesterday's Updated JSON would inject
        // yesterday's picks as "today" pending rows.
        const mainJson    = todayJson ?? yestJson
        const updJson     = todayJson ? todayUpdatedJson : yestUpdatedJson
        const pendingDate = todayJson ? today : yesterday

        const pendingMain    = buildPendingRows(mainJson, resultsRows, pendingDate)
        const pendingUpdated = buildPendingRows(updJson, updatedRows, pendingDate)

        const finalResults = [...resultsRows, ...pendingMain]
        const finalUpdated = [...updatedRows, ...pendingUpdated]

        setResults(finalResults)
        setUpdated(finalUpdated)
        setLoading(false)
      })
      .catch(err => {
        setError(err.message)
        setLoading(false)
      })
  }, [])

  const stats  = results ? computeStats(results)  : null
  const statsU = updated?.length ? computeStats(updated) : null

  return { results, updated, loading, error, stats, statsU }
}

function normalizeRows(rows) {
  return (rows ?? []).map((row, rowIndex) => {
    const home = row.home_team || row.homeTeam || row.homeAbbr
    const away = row.away_team || row.awayTeam || row.awayAbbr
    const decision = row.decision || (row.pick_team || row.pick || row.pickLabel ? 'BET' : 'SKIP')
    const isSkip = decision === 'SKIP'
    const pick = isSkip ? 'SKIP' : (row.pick_team || row.pick || row.pickLabel)
    const odds = toFiniteNumber(row.pick_odds ?? row.odds)
    const stake = toFiniteNumber(row.stake_eur ?? row.stake)
    const pnl = toFiniteNumber(row.pnl ?? row.profit_loss)
    const bankrollAfter = toFiniteNumber(row.bankroll_after)
    const result = normalizeResult(row.result, decision)

    return {
      ...row,
      rowIndex,
      decision,
      date: row.date,
      game: row.game || (away && home ? `${away} @ ${home}` : '—'),
      pick: pick || '—',
      odds: isSkip ? null : odds,
      stake_eur: stake,
      result,
      profit_loss: pnl,
      bankroll_after: bankrollAfter,
    }
  })
}

// Build synthetic pending rows from today's predictions JSON for any BETs
// not already present in the settled results log.
function buildPendingRows(predictionsJson, settledRows, today) {
  if (!predictionsJson?.predictions) return []

  // Build a set of already-settled game names for today
  const alreadySettled = new Set(
    settledRows
      .filter(r => r.date === today && r.result && r.result !== 'Pending')
      .map(r => r.game)
  )

  const pending = []
  for (const row of predictionsJson.predictions) {
    if (row.gameStatus === 'FINAL') continue
    if (decisionForRow(row) !== 'BET') continue

    const game = `${row.awayAbbr} @ ${row.homeAbbr}`
    if (alreadySettled.has(game)) continue

    const useRl = row.useRl
    const odds  = useRl ? row.rlPickOdds : row.pickOdds
    const stake = toFiniteNumber(row.stake?.eur) ?? 0
    const label = pickLabel(row)

    pending.push({
      rowIndex:      9999 + pending.length,
      decision:      'BET',
      date:          today,
      game,
      pick:          label,
      odds:          toFiniteNumber(odds),
      stake_eur:     stake,
      result:        'Pending',
      profit_loss:   0,
      bankroll_after: null,
      _pending:      true,
    })
  }
  return pending
}

function normalizeResult(result, decision) {
  if (decision === 'SKIP') return 'SKIP'
  if (result == null || result === '') return decision === 'BET' ? 'Pending' : 'N/A'
  const label = String(result).trim()
  if (label.toUpperCase() === 'N/A') return 'N/A'
  return label.charAt(0).toUpperCase() + label.slice(1).toLowerCase()
}

function computeStats(rows) {
  const bets    = rows.filter(r => r.decision !== 'SKIP' && r.result && r.result !== 'N/A')
  const settled = bets.filter(r => ['Win', 'Loss', 'Push'].includes(r.result))
  const wins    = settled.filter(r => r.result === 'Win').length
  const losses  = settled.filter(r => r.result === 'Loss').length
  const pushes  = settled.filter(r => r.result === 'Push').length
  const pending = bets.filter(r => r.result === 'Pending').length

  const totalPnl    = settled.reduce((s, r) => s + (toFiniteNumber(r.profit_loss) || 0), 0)
  const totalStaked = settled.reduce((s, r) => s + (toFiniteNumber(r.stake_eur) || 0), 0)
  const roi = totalStaked > 0 ? totalPnl / totalStaked : 0

  const sorted = [...rows]
    .sort((a, b) => {
      if (a.date !== b.date) return a.date > b.date ? 1 : -1
      return (a.rowIndex ?? 0) - (b.rowIndex ?? 0)
    })
    .filter(r => toFiniteNumber(r.bankroll_after) != null)

  const bankrollHistory = sorted.map(r => ({
    date:     r.date,
    bankroll: toFiniteNumber(r.bankroll_after),
  }))

  const baseBankroll = sorted[0] ? (toFiniteNumber(sorted[0].bankroll_before) ?? 500) : 500
  const currentBankroll = baseBankroll + totalPnl

  return {
    totalBets: bets.length,
    settledBets: settled.length,
    wins,
    losses,
    pushes,
    pending,
    winRate: settled.length > 0 ? wins / settled.length : 0,
    totalPnl,
    totalStaked,
    roi,
    baseBankroll,
    currentBankroll,
    bankrollHistory,
  }
}
