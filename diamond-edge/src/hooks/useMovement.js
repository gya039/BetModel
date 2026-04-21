import { useState, useEffect } from 'react'
import { predictionsPath, updatedPath } from '../utils/paths'
import { decisionForRow } from '../utils/decisions'

export function useMovement(dateStr) {
  const [morning,   setMorning]   = useState(null)
  const [afternoon, setAfternoon] = useState(null)
  const [loading,   setLoading]   = useState(true)
  const [error,     setError]     = useState(null)

  useEffect(() => {
    if (!dateStr) return
    let cancelled = false
    setLoading(true)

    Promise.all([
      fetch(predictionsPath(dateStr)).then(r => r.ok ? r.json() : null).catch(() => null),
      fetch(updatedPath(dateStr)).then(r => r.ok ? r.json() : null).catch(() => null),
    ]).then(([m, a]) => {
      if (cancelled) return
      setMorning(m)
      setAfternoon(a)
      setLoading(false)
    }).catch(err => {
      if (cancelled) return
      setError(err.message)
      setLoading(false)
    })

    return () => { cancelled = true }
  }, [dateStr])

  const movements = computeMovements(morning, afternoon)
  const hasUpdated = afternoon !== null

  return { morning, afternoon, movements, hasUpdated, loading, error }
}

function pickLabelFor(game) {
  if (!game || game.pickSide === 'none') return null
  const abbr = game.pickSide === 'home' ? game.homeAbbr : game.awayAbbr
  if (!game.useRl) return `${abbr} ML`
  const point = Number.isFinite(Number(game.spreadPoint)) ? Number(game.spreadPoint) : -1.5
  return `${abbr} ${point > 0 ? '+' : ''}${point}`
}

function computeMovements(morning, afternoon) {
  if (!morning || !afternoon) return []

  return morning.predictions
    .map(mGame => {
      // Live/final games are excluded — not market signals
      if (['LIVE', 'FINAL'].includes(mGame.gameStatus)) return null

      const aGame = afternoon.predictions.find(g => g.gamePk === mGame.gamePk)
      if (!aGame) return null

      const mDecision = decisionForRow(mGame)
      const aDecision = decisionForRow(aGame)
      const isNewBet  = mDecision !== 'BET' && aDecision === 'BET'

      // For new afternoon bets, use afternoon pick odds; for existing picks use morning
      const morningPickOdds   = mGame.useRl ? mGame.rlPickOdds : mGame.pickOdds
      const afternoonPickOdds = aGame.useRl ? aGame.rlPickOdds : aGame.pickOdds

      const pickOddsDelta =
        afternoonPickOdds != null && morningPickOdds != null
          ? afternoonPickOdds - morningPickOdds
          : null

      const edgeDelta =
        aGame.edge != null && mGame.edge != null
          ? aGame.edge - mGame.edge
          : null

      // Use afternoon game for SP info (most current)
      const spGame = aGame

      return {
        gamePk:            mGame.gamePk,
        homeTeam:          mGame.homeTeam,
        awayTeam:          mGame.awayTeam,
        homeAbbr:          mGame.homeAbbr,
        awayAbbr:          mGame.awayAbbr,
        // Pick label: prefer afternoon when it's a new bet
        pickLabel:         pickLabelFor(isNewBet ? aGame : mGame),
        morningPickLabel:  pickLabelFor(mGame),
        afternoonPickLabel:pickLabelFor(aGame),
        isNewBet,
        morningPickOdds,
        afternoonPickOdds,
        pickOddsDelta,
        morningEdge:       mGame.edge,
        afternoonEdge:     aGame.edge,
        edgeDelta,
        morningStakeEur:   mGame.stake?.eur ?? 0,
        afternoonStakeEur: aGame.stake?.eur ?? 0,
        afternoonReturn:   afternoonPickOdds != null && aGame.stake?.eur > 0
                             ? afternoonPickOdds * aGame.stake.eur
                             : null,
        morningDecision:   mDecision,
        afternoonDecision: aDecision,
        decisionChanged:   mDecision !== aDecision,
        // SP info from afternoon (most current)
        homeSpName:  spGame.homeSpName,
        awaySpName:  spGame.awaySpName,
        homeSpEra:   spGame.homeSpEra,
        awaySpEra:   spGame.awaySpEra,
        homeSpIp:    spGame.homeSpIp ?? 0,
        awaySpIp:    spGame.awaySpIp ?? 0,
        pickSide:    aDecision === 'BET' ? aGame.pickSide : mGame.pickSide,
      }
    })
    .filter(Boolean)
}
