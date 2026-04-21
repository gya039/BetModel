import { useEffect, useMemo, useState } from 'react'
import { motion, AnimatePresence } from 'framer-motion'
import { usePredictions } from '../hooks/usePredictions'
import { useResults } from '../hooks/useResults'
import { decisionForRow } from '../utils/decisions'
import { todayStr, prevDate, nextDate, isFuture, formatDisplayDate, findLatestDate } from '../utils/paths'
import { fmtMoney, fmtPercent, fmtSignedMoney, toFiniteNumber } from '../utils/format'
import DateNav from '../components/DateNav'
import PickCard from '../components/PickCard'
import AccumulatorCard from '../components/AccumulatorCard'
import AccumulatorStatsCard from '../components/AccumulatorStatsCard'
import { PickCardSkeleton } from '../components/Skeleton'

export default function TodayView({ onBankrollLoaded, initialDate }) {
  const [date, setDate] = useState(() => initialDate || todayStr())
  const [oddsView, setOddsView] = useState('current')
  const [showFinals, setShowFinals] = useState(true)
  const [showAccums, setShowAccums] = useState(false)

  const { data, loading, error } = usePredictions(date)
  const { results } = useResults()
  const selectedData = oddsView === 'updated' && data?.updated ? data.updated : data?.current
  const hasUpdated = Boolean(data?.updated)

  useEffect(() => {
    let cancelled = false
    if (initialDate) { setDate(initialDate); return }
    findLatestDate().then(d => { if (!cancelled) setDate(d) })
    return () => { cancelled = true }
  }, [initialDate])

  const bankroll = toFiniteNumber(selectedData?.bankroll)
  if (bankroll != null && onBankrollLoaded) {
    onBankrollLoaded(bankroll)
  }

  const settledByGame = useMemo(() => {
    const map = new Map()
    for (const row of results ?? []) {
      if (row.date !== date || row.decision === 'SKIP') continue
      if (!['Win', 'Loss', 'Push'].includes(row.result)) continue
      map.set(String(row.game_pk), row)
    }
    return map
  }, [results, date])

  const predictions = useMemo(() => {
    return (selectedData?.predictions ?? []).map(game => {
      const settled = settledByGame.get(String(game.gamePk))
      if (!settled) return game
      return {
        ...game,
        gameStatus: game.gameStatus === 'LIVE' ? 'LIVE' : 'FINAL',
        settledResult: settled.result,
        settledPnl: settled.profit_loss,
        settledStake: settled.stake_eur,
        settledOdds: settled.odds,
      }
    })
  }, [selectedData?.predictions, settledByGame])

  const placedBets = predictions.filter(g => decisionForRow(g) === 'BET')
  const bets = placedBets.filter(g => !['LIVE', 'FINAL'].includes(g.gameStatus) && !g.settledResult)
  const live = placedBets.filter(g => g.gameStatus === 'LIVE')
  const finals = placedBets.filter(g => g.gameStatus === 'FINAL' || g.settledResult)

  const totalStaked = placedBets.reduce((s, g) => s + (toFiniteNumber(g.stake?.eur) || 0), 0)
  const totalProfit = placedBets.reduce((s, g) => s + (toFiniteNumber(g.settledPnl) || 0), 0)
  const hasSettled  = placedBets.some(g => g.settledResult)
  const accums = selectedData?.accumulators ?? []
  const rlDiagnostics = useMemo(() => buildRlDiagnostics(predictions), [predictions])

  function goNext() {
    const n = nextDate(date)
    if (!isFuture(n)) setDate(n)
  }

  return (
    <div>
      <DateNav
        date={date}
        onPrev={() => setDate(prevDate(date))}
        onNext={goNext}
        onToday={() => setDate(todayStr())}
      />

      {selectedData && (
        <div className="de-hero">
          <div className="de-hero__date-label">Today's Slate</div>
          <div className="de-hero__date">{formatDisplayDate(date)}</div>
          {hasUpdated && (
            <div className="de-segmented de-odds-toggle" aria-label="Odds snapshot">
              <button
                className={`de-segmented__btn${oddsView === 'current' ? ' active' : ''}`}
                onClick={() => setOddsView('current')}
              >
                Current
              </button>
              <button
                className={`de-segmented__btn${oddsView === 'updated' ? ' active' : ''}`}
                onClick={() => setOddsView('updated')}
              >
                Updated
              </button>
            </div>
          )}
          {(selectedData.regenerated || selectedData.snapshotKind === 'regenerated') && (
            <div className="de-snapshot-warning">
              Current file was regenerated after the morning odds lock
            </div>
          )}
          <div className="de-hero__stats">
            <div className="de-hero__stat">
              <div className="de-hero__stat-label">Bets</div>
              <div className="de-hero__stat-value green">{placedBets.length}</div>
              <div className="de-hero__stat-sub">{predictions.length} games</div>
            </div>
            <div className="de-hero__stat">
              <div className="de-hero__stat-label">Staked</div>
              <div className="de-hero__stat-value gold">{totalStaked > 0 ? fmtMoney(totalStaked) : '—'}</div>
            </div>
            <div className={`de-hero__stat${hasSettled ? totalProfit >= 0 ? ' de-hero__stat--pos' : ' de-hero__stat--neg' : ''}`}>
              <div className="de-hero__stat-label">Profit</div>
              <div className={`de-hero__stat-value${hasSettled ? totalProfit >= 0 ? ' green' : ' orange' : ''}`}>
                {hasSettled ? fmtSignedMoney(totalProfit) : '—'}
              </div>
            </div>
          </div>
        </div>
      )}

      {loading && (
        <div className="de-section">
          {[1, 2, 3].map(i => <PickCardSkeleton key={i} />)}
        </div>
      )}

      {error && !loading && (
        <div className="de-empty">
          <div className="de-empty__icon">⚾</div>
          <div className="de-empty__title">No predictions found</div>
          <div className="de-empty__sub">
            No file for {formatDisplayDate(date)}. Try navigating to a different date.
          </div>
        </div>
      )}

      {!loading && !error && (
        <>
          {bets.length > 0 && (
            <div className="de-section de-picks-section">
              <div className="de-section-header">
                <div className="de-section-header__dot" style={{ background: 'var(--bet)' }} />
                <span className="de-section-header__title">Today's Picks</span>
                <span className="de-section-header__count">{bets.length}</span>
              </div>
              {rlDiagnostics && <RlDiagnosticPanel diag={rlDiagnostics} />}
              <div className="de-pick-grid">
                {bets
                  .sort((a, b) => (toFiniteNumber(b.edge) || 0) - (toFiniteNumber(a.edge) || 0))
                  .map(game => (
                    <PickCard key={game.gamePk} game={game} defaultExpanded={false} />
                  ))}
              </div>
            </div>
          )}

          {live.length > 0 && (
            <div className="de-section">
              <div className="de-section-header">
                <div className="de-section-header__dot" style={{ background: 'var(--live)', animation: 'dotPulse 1.2s ease-in-out infinite' }} />
                <span className="de-section-header__title">In Play</span>
                <span className="de-section-header__count">{live.length}</span>
              </div>
              <div className="de-pick-grid">
                {live.map(game => (
                  <PickCard key={game.gamePk} game={game} defaultExpanded={false} />
                ))}
              </div>
            </div>
          )}

          {finals.length > 0 && (
            <div className="de-section">
              <button
                className="de-collapse-header"
                onClick={() => setShowFinals(s => !s)}
              >
                <div className="de-section-header__dot" style={{ background: 'var(--final)' }} />
                Final Results
                <span className="de-collapse-header__count">{finals.length}</span>
                <span className="de-collapse-header__chev">{showFinals ? '▲' : '▼'}</span>
              </button>
              <AnimatePresence initial={false}>
                {showFinals && (
                  <motion.div
                    initial={{ height: 0, opacity: 0 }}
                    animate={{ height: 'auto', opacity: 1 }}
                    exit={{ height: 0, opacity: 0 }}
                    transition={{ duration: 0.25, ease: [0.22, 1, 0.36, 1] }}
                    style={{ overflow: 'hidden' }}
                  >
                    <div className="de-pick-grid">
                      {finals.map(game => (
                        <PickCard key={game.gamePk} game={game} defaultExpanded={false} />
                      ))}
                    </div>
                  </motion.div>
                )}
              </AnimatePresence>
            </div>
          )}

          {accums.length > 0 && (
            <div className="de-section">
              <div className="de-seam">
                <div className="de-seam__line" />
                <div className="de-seam__icon">⬡ ⬡ ⬡</div>
                <div className="de-seam__line" />
              </div>
              <button
                className="de-collapse-header"
                onClick={() => setShowAccums(s => !s)}
              >
                <div className="de-section-header__dot" style={{ background: 'var(--gold)' }} />
                Accumulators
                <span className="de-collapse-header__count">{accums.length}</span>
                <span className="de-collapse-header__chev">{showAccums ? '▲' : '▼'}</span>
              </button>
              <AnimatePresence initial={false}>
                {showAccums && (
                  <motion.div
                    initial={{ height: 0, opacity: 0 }}
                    animate={{ height: 'auto', opacity: 1 }}
                    exit={{ height: 0, opacity: 0 }}
                    transition={{ duration: 0.25, ease: [0.22, 1, 0.36, 1] }}
                    style={{ overflow: 'hidden' }}
                  >
                    {accums.map((a, i) => (
                      <AccumulatorCard key={i} accum={a} index={i} />
                    ))}
                  </motion.div>
                )}
              </AnimatePresence>
            </div>
          )}

          <AccumulatorStatsCard />

          {placedBets.length === 0 && (
            <div className="de-empty">
              <div className="de-empty__icon">⌕</div>
              <div className="de-empty__title">No bets on this slate</div>
              <div className="de-empty__sub">
                The model found no qualifying edges for {formatDisplayDate(date)}.
              </div>
            </div>
          )}
        </>
      )}
    </div>
  )
}

function buildRlDiagnostics(predictions) {
  const rows = predictions ?? []
  if (!rows.length) return null
  const withModel = rows.filter(g => g.spreadModelLoaded)
  const evaluated = rows.filter(g => g.spreadBestEdge != null || g.spreadEdge != null)
  const candidates = rows.filter(g => (toFiniteNumber(g.spreadEdge) || 0) >= 0.03)
  const selected = rows.filter(g => g.useRl)
  const blocked = candidates.filter(g => !g.useRl)
  const first = rows.find(g => g.spreadModelStatus || g.spreadModelLoaded)
  const reasonCounts = rows.reduce((acc, g) => {
    const reason = g.spreadRejectionReason || 'not_evaluated'
    acc[reason] = (acc[reason] || 0) + 1
    return acc
  }, {})
  const bestEdge = rows.reduce((best, g) => {
    const edge = toFiniteNumber(g.spreadBestEdge ?? g.spreadEdge)
    return edge == null || (best != null && edge <= best) ? best : edge
  }, null)

  return {
    loaded: withModel.length > 0,
    status: first?.spreadModelStatus || 'missing',
    validationPassed: first?.spreadModelValidationPassed,
    validationReasons: first?.spreadModelValidationReasons || [],
    evaluated: evaluated.length,
    candidates: candidates.length,
    selected: selected.length,
    blocked: blocked.length,
    bestEdge,
    reasonCounts,
  }
}

function RlDiagnosticPanel({ diag }) {
  const validationLabel = diag.validationPassed === true
    ? 'Passed'
    : diag.validationPassed === false ? 'Failed' : 'Unknown'
  const topReasons = Object.entries(diag.reasonCounts)
    .sort((a, b) => b[1] - a[1])
    .slice(0, 3)

  return (
    <div className="rl-diagnostic">
      <div className="rl-diagnostic__header">
        <div>
          <div className="rl-diagnostic__label">Run Line Diagnostic</div>
          <div className="rl-diagnostic__status">
            {diag.loaded ? `Model ${diag.status}` : 'Model missing'} · Validation {validationLabel}
          </div>
        </div>
        <div className={`rl-diagnostic__pill${diag.selected > 0 ? ' active' : ''}`}>
          {diag.selected} RL selected
        </div>
      </div>
      <div className="rl-diagnostic__grid">
        <RlMetric label="Evaluated" value={diag.evaluated} />
        <RlMetric label="+EV >= 3%" value={diag.candidates} />
        <RlMetric label="Blocked" value={diag.blocked} />
        <RlMetric label="Best Edge" value={diag.bestEdge != null ? fmtPercent(diag.bestEdge) : '-'} />
      </div>
      {topReasons.length > 0 && (
        <div className="rl-diagnostic__reasons">
          {topReasons.map(([reason, count]) => (
            <span key={reason}>{formatRlReason(reason)}: {count}</span>
          ))}
        </div>
      )}
      {diag.validationReasons.length > 0 && (
        <div className="rl-diagnostic__note">{diag.validationReasons[0]}</div>
      )}
    </div>
  )
}

function RlMetric({ label, value }) {
  return (
    <div className="rl-diagnostic__metric">
      <span>{label}</span>
      <strong>{value}</strong>
    </div>
  )
}

function formatRlReason(reason) {
  return String(reason || 'unknown')
    .replace(/_/g, ' ')
    .replace(/\b\w/g, c => c.toUpperCase())
}
