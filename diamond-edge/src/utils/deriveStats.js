// Derive display values from raw prediction row data
import { fmtPercent, fmtSignedMoney, toFiniteNumber } from './format'

export function deriveConfidence(game) {
  const edge = toFiniteNumber(game.edge) || 0
  if (edge < 0.03) return 1
  if (edge < 0.05) return 3
  if (edge < 0.07) return 4
  if (edge < 0.10) return 5
  if (edge < 0.13) return 6
  if (edge < 0.15) return 7
  if (edge < 0.18) return 8
  if (edge < 0.20) return 9
  return 10
}

export function deriveReason(game) {
  const edge = toFiniteNumber(game.edge) || 0

  if (edge < 0.03) {
    if (game.pickSide === 'none') return 'No clear model direction on this matchup'
    return `Edge ${(edge * 100).toFixed(1)}% is below the 3% threshold`
  }

  const reasons = []

  if (game.useRl) {
    reasons.push('High-confidence pick — run line selected')
  }

  if (game.eraDiff !== undefined) {
    const rawEraDiff = toFiniteNumber(game.eraDiff)
    const eraDiff = game.pickSide === 'home' ? rawEraDiff : -rawEraDiff
    if (eraDiff > 0.5) {
      const sp = game.pickSide === 'home' ? game.homeSpName : game.awaySpName
      const lastName = sp?.split(' ').pop() || 'SP'
      reasons.push(`${lastName} ERA advantage (+${eraDiff.toFixed(2)})`)
    }
  }

  if (game.homeL10WP !== undefined && game.awayL10WP !== undefined) {
    const formDiff =
      game.pickSide === 'home'
        ? game.homeL10WP - game.awayL10WP
        : game.awayL10WP - game.homeL10WP
    if (formDiff > 0.15) reasons.push('Better recent form (L10)')
  }

  const modelProb = toFiniteNumber(game.modelProb)
  if (modelProb != null && modelProb >= 0.62 && reasons.length < 2) {
    reasons.push(`Model: ${(modelProb * 100).toFixed(0)}% win probability`)
  }

  if (reasons.length === 0) {
    reasons.push(`${(edge * 100).toFixed(1)}% edge over market implied`)
  }

  return reasons.slice(0, 2).join(' · ')
}

export function betterPitcherSide(game) {
  const homeEra = toFiniteNumber(game.homeSpEra)
  const awayEra = toFiniteNumber(game.awaySpEra)
  if (homeEra == null || awayEra == null) return null
  return homeEra < awayEra ? 'home' : 'away'
}

export function formSegments(winPct, total = 10) {
  return Math.round((toFiniteNumber(winPct) || 0) * total)
}

export function pnlColor(pnl) {
  pnl = toFiniteNumber(pnl) || 0
  if (pnl > 0) return 'var(--bet)'
  if (pnl < 0) return 'var(--skip)'
  return 'var(--muted)'
}

export function roiColor(roi) {
  roi = toFiniteNumber(roi) || 0
  if (roi > 0.05) return 'var(--bet)'
  if (roi > 0) return 'var(--accent)'
  return 'var(--skip)'
}

export function impliedProb(decimalOdds) {
  decimalOdds = toFiniteNumber(decimalOdds)
  if (!decimalOdds || decimalOdds <= 1) return null
  return 1 / decimalOdds
}

export function fmtPct(val, decimals = 1) {
  return fmtPercent(val, decimals)
}

export function fmtEur(val) {
  return fmtSignedMoney(val)
}
