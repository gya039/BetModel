// Exact match of Python pipeline decision logic — do not alter thresholds
import { toFiniteNumber } from './format'

export function decisionForRow(row) {
  if (row.pickSide === 'none') return 'SKIP'
  if (row.homeSpName === 'TBD' || row.awaySpName === 'TBD') return 'SKIP'
  if (!row.hasOdds || row.pickOdds == null) return 'SKIP'
  if ((toFiniteNumber(row.edge) || 0) < 0.01 || (toFiniteNumber(row.stake?.eur) || 0) <= 0) return 'SKIP'
  return 'BET'
}

export function pickLabel(row) {
  if (row.pickSide === 'none') return 'SKIP'
  const team = row.pickSide === 'home' ? row.homeAbbr : row.awayAbbr
  if (!team) return '—'
  const point = toFiniteNumber(row.spreadPoint)
  const pointLabel = point == null ? '-1.5' : `${point > 0 ? '+' : ''}${point}`
  return row.useRl ? `${team} ${pointLabel}` : `${team} ML`
}

export function stakeLabel(row) {
  const pct = toFiniteNumber(row.stake?.pctValue) ?? 0
  const eur = toFiniteNumber(row.stake?.eur) ?? 0
  return `${pct}% (EUR ${eur.toFixed(2)})`
}

export function skipReason(row) {
  if (row.pickSide === 'none') return 'No clear model direction'
  if (row.homeSpName === 'TBD' || row.awaySpName === 'TBD') return 'Starting pitcher not announced'
  if (!row.hasOdds || row.pickOdds == null) return 'Missing odds data'
  if ((toFiniteNumber(row.edge) || 0) < 0.01) {
    const e = ((toFiniteNumber(row.edge) || 0) * 100).toFixed(1)
    return `Edge below threshold (${e}% < 1%)`
  }
  if ((toFiniteNumber(row.stake?.eur) || 0) <= 0) return 'Stake: pass (bankroll protection)'
  return 'No qualifying edge'
}

export function edgeTier(edge) {
  edge = toFiniteNumber(edge) || 0
  if (edge < 0.01) return { label: 'PASS',   level: 0, color: 'var(--muted)' }
  if (edge < 0.03) return { label: 'MICRO',  level: 1, color: 'var(--muted)' }
  if (edge < 0.06) return { label: 'LOW',    level: 2, color: 'var(--skip)' }
  if (edge < 0.10) return { label: 'MID',    level: 3, color: 'var(--gold)' }
  if (edge < 0.15) return { label: 'SOLID',  level: 4, color: 'var(--accent)' }
  if (edge < 0.20) return { label: 'STRONG', level: 5, color: 'var(--bet)' }
  return               { label: 'ELITE',  level: 6, color: 'var(--bet)' }
}

// Stake tier pct from edge (mirrors Python)
export function stakePct(edge) {
  edge = toFiniteNumber(edge) || 0
  if (edge < 0.01) return 0
  if (edge < 0.03) return 0.5
  if (edge < 0.06) return 1
  if (edge < 0.10) return 2
  if (edge < 0.15) return 3
  if (edge < 0.20) return 4
  return 5
}
