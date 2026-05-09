import { useEffect, useMemo, useState } from 'react'
import { useResults } from '../hooks/useResults'
import { useAccumulators } from '../hooks/useAccumulators'
import { usePredictions } from '../hooks/usePredictions'
import { useLiveScores } from '../hooks/useLiveScores'
import BankrollChart from '../components/BankrollChart'
import ResultsTable from '../components/ResultsTable'
import AccumulatorsTable from '../components/AccumulatorsTable'
import { StatCardSkeleton, ChartSkeleton } from '../components/Skeleton'
import { roiColor, pnlColor, fmtPct, fmtEur } from '../utils/deriveStats'
import { fmtMoney, safeText, toFiniteNumber } from '../utils/format'
import { findLatestDate, todayStr } from '../utils/paths'

export default function ResultsView() {
  const { results, updated, loading, error, stats, statsU } = useResults()
  const { accas, stats: accaStats } = useAccumulators()
  const [view, setView] = useState('normal') // 'normal' | 'updated' | 'accumulators'
  const [tickerDate, setTickerDate] = useState(todayStr())
  const { data: tickerData } = usePredictions(tickerDate)
  const liveStatuses = useLiveScores(tickerDate)

  const activeStats   = view === 'updated' && statsU ? statsU : stats
  const activeRows    = view === 'updated' && updated?.length ? updated : results
  const activeBetRows = activeRows?.filter(r => r.decision !== 'SKIP') ?? []
  const bankrollPositive = toFiniteNumber(activeStats?.currentBankroll) > toFiniteNumber(activeStats?.baseBankroll)
  const roiPositive = toFiniteNumber(activeStats?.roi) > 0
  const pnlPositive = toFiniteNumber(activeStats?.totalPnl) > 0

  const isAccaView = view === 'accumulators'
  const tickerGames = useMemo(
    () => buildScoreTickerGames(tickerData?.current?.predictions, liveStatuses),
    [tickerData?.current?.predictions, liveStatuses]
  )

  useEffect(() => {
    let cancelled = false
    findLatestDate().then(d => { if (!cancelled) setTickerDate(d) })
    return () => { cancelled = true }
  }, [])

  if (error) {
    return (
      <div className="de-empty">
        <div className="de-empty__icon">📊</div>
        <div className="de-empty__title">No results data</div>
        <div className="de-empty__sub">results_log.csv not found. Run the pipeline first.</div>
      </div>
    )
  }

  return (
    <div>
      <section className="tracker-landing-hero" aria-label="Diamond Edge season tracker">
        <div className="tracker-landing-hero__glow" aria-hidden="true" />
        <div className="tracker-landing-hero__copy">
          <div className="tracker-landing-hero__kicker">MLB Analytics Platform - Season 2026</div>
          <h1><span>Diamond</span><span>Edge</span></h1>
          <p>
            Institutional-grade MLB betting intelligence. Real-time line movement,
            model validation, accumulator history, and precision bankroll tracking.
          </p>
          <div className="tracker-landing-hero__actions">
            <button
              className="tracker-landing-hero__button"
              onClick={() => document.getElementById('tracker-dashboard')?.scrollIntoView({ behavior: 'smooth', block: 'start' })}
            >
              Open Dashboard
            </button>
          </div>
          {!loading && activeStats && (
            <div className="tracker-landing-hero__signals" aria-label="Season summary">
              <span>
                <strong className={bankrollPositive ? 'tracker-landing-hero__signal-value tracker-landing-hero__signal-value--positive' : 'tracker-landing-hero__signal-value'}>
                  {fmtMoney(activeStats.currentBankroll)}
                </strong>
                Bankroll
                <em className="tracker-landing-hero__signal-sub">Started at {fmtMoney(activeStats.baseBankroll, 0)}</em>
              </span>
              <span>
                <strong className={roiPositive ? 'tracker-landing-hero__signal-value tracker-landing-hero__signal-value--positive' : 'tracker-landing-hero__signal-value'}>
                  {fmtPct(activeStats.roi)}
                </strong>
                Season ROI
              </span>
              <span>
                <strong className={pnlPositive ? 'tracker-landing-hero__signal-value tracker-landing-hero__signal-value--positive' : 'tracker-landing-hero__signal-value'}>
                  {fmtEur(activeStats.totalPnl)}
                </strong>
                Net P&L
              </span>
              <span>
                <strong className="tracker-landing-hero__signal-value">{fmtPct(activeStats.winRate)}</strong>
                Win Rate
              </span>
            </div>
          )}
        </div>
      </section>

      {tickerGames.length > 0 && <ScoreTicker games={tickerGames} />}

      {/* View toggle */}
      <div className="de-section" id="tracker-dashboard" style={{ paddingBottom: 0 }}>
        <div className="de-segmented">
          <button
            className={`de-segmented__btn${view === 'normal' ? ' active' : ''}`}
            onClick={() => setView('normal')}
          >
            Morning
          </button>
          {updated?.length > 0 && (
            <button
              className={`de-segmented__btn${view === 'updated' ? ' active' : ''}`}
              onClick={() => setView('updated')}
            >
              Updated
            </button>
          )}
          <button
            className={`de-segmented__btn${view === 'accumulators' ? ' active' : ''}`}
            onClick={() => setView('accumulators')}
          >
            Accas {accaStats?.total > 0 && <span style={{ opacity: 0.65 }}>({accaStats.total})</span>}
          </button>
        </div>
      </div>

      <div className="de-section">
        {isAccaView ? (
          /* ── Accumulators tab ─────────────────────────────── */
          <>
            <div className="de-section-header" style={{ marginBottom: 12 }}>
              <div className="de-section-header__dot" style={{ background: 'var(--gold)' }} />
              <span className="de-section-header__title">Accumulator History</span>
              <span className="de-section-header__count">{accas?.length ?? 0}</span>
            </div>
            <div
              style={{
                background: 'var(--panel)',
                border: '1px solid var(--line)',
                borderRadius: 'var(--r-xl)',
                padding: '16px',
                boxShadow: 'var(--shadow-panel)',
              }}
            >
              <AccumulatorsTable accas={accas ?? []} stats={accaStats} />
            </div>
          </>
        ) : (
          /* ── Morning / Updated Odds tab ───────────────────── */
          <>
            {loading ? (
              <div className="de-stats-grid">
                {[1,2,3,4].map(i => <StatCardSkeleton key={i} />)}
              </div>
            ) : activeStats && (
              <div className="de-stats-grid">
                <div className="de-stat-card">
                  <div className="de-stat-card__label">Win Rate</div>
                  <div
                    className="de-stat-card__value"
                    style={{ color: activeStats.winRate >= 0.5 ? 'var(--bet)' : 'var(--skip)' }}
                  >
                    {fmtPct(activeStats.winRate)}
                  </div>
                  <div className="de-stat-card__sub">
                    {activeStats.wins}W {activeStats.losses}L {activeStats.pushes}P
                  </div>
                </div>

                <div className="de-stat-card">
                  <div className="de-stat-card__label">Total Staked</div>
                  <div className="de-stat-card__value">
                    {fmtMoney(activeStats.totalStaked, 0)}
                  </div>
                  <div className="de-stat-card__sub">{activeStats.settledBets} settled bets</div>
                </div>

                <div className="de-stat-card">
                  <div className="de-stat-card__label">Net P&L</div>
                  <div
                    className="de-stat-card__value"
                    style={{ color: pnlColor(activeStats.totalPnl) }}
                  >
                    {fmtEur(activeStats.totalPnl)}
                  </div>
                  <div className="de-stat-card__sub">From {fmtMoney(activeStats.baseBankroll, 0)} base</div>
                </div>

                <div className="de-stat-card">
                  <div className="de-stat-card__label">Pending</div>
                  <div className="de-stat-card__value" style={{ color: 'var(--gold)' }}>
                    {activeStats.pending}
                  </div>
                  <div className="de-stat-card__sub">unsettled bets</div>
                </div>
              </div>
            )}

            {loading ? (
              <ChartSkeleton />
            ) : activeStats?.bankrollHistory?.length > 0 && (
              <BankrollChart history={activeStats.bankrollHistory} startBankroll={activeStats.baseBankroll} />
            )}

            {loading ? null : (
              <>
                <div className="de-section-header" style={{ marginBottom: 12 }}>
                  <div className="de-section-header__dot" style={{ background: 'var(--accent)' }} />
                  <span className="de-section-header__title">Bet History</span>
                  <span className="de-section-header__count">{activeBetRows.length}</span>
                </div>
                <div
                  style={{
                    background: 'var(--panel)',
                    border: '1px solid var(--line)',
                    borderRadius: 'var(--r-xl)',
                    padding: '16px',
                    boxShadow: 'var(--shadow-panel)',
                  }}
                >
                  <ResultsTable rows={activeRows} />
                </div>
              </>
            )}
          </>
        )}
      </div>
    </div>
  )
}

function ScoreTicker({ games }) {
  const items = [...games, ...games]
  return (
    <div className="score-ticker" aria-label="Live score ticker">
      <div className="score-ticker__label">
        <span />
        Live
      </div>
      <div className="score-ticker__viewport">
        <div className="score-ticker__track">
          {items.map((game, index) => (
          <div className="score-ticker__item" key={`${game.key}-${index}`}>
            <strong>{game.away} @ {game.home}</strong>
            <span className={game.statusClass}>{game.detail}</span>
          </div>
          ))}
        </div>
      </div>
    </div>
  )
}

function buildScoreTickerGames(predictions, liveStatuses) {
  const games = predictions ?? []
  const statusMap = liveStatuses?.games ?? {}
  return games
    .map(game => {
      const live = statusMap[String(game.gamePk)]
      const status = live?.gameStatus || game.gameStatus || 'SCHEDULED'
      const awayScore = live?.awayScore ?? game.finalScore?.away
      const homeScore = live?.homeScore ?? game.finalScore?.home
      const hasScore = awayScore != null && homeScore != null
      const statusClass = status === 'LIVE' ? 'live' : status === 'FINAL' ? 'final' : 'scheduled'
      const detail = hasScore
        ? `${awayScore}-${homeScore} ${live?.currentInning ? formatTickerInning(live) : status === 'FINAL' ? 'Final' : ''}`.trim()
        : formatTickerTime(game.gameDate)

      return {
        key: String(game.gamePk),
        away: safeText(game.awayAbbr),
        home: safeText(game.homeAbbr),
        detail: detail || safeText(live?.detailedState || status),
        status,
        statusClass,
        timeValue: Date.parse(game.gameDate ?? '') || Number.MAX_SAFE_INTEGER,
      }
    })
    .sort((a, b) => {
      const rank = { LIVE: 0, SCHEDULED: 1, FINAL: 2 }
      const ar = rank[a.status] ?? 1
      const br = rank[b.status] ?? 1
      if (ar !== br) return ar - br
      return a.timeValue - b.timeValue
    })
    .slice(0, 16)
}

function formatTickerInning(live) {
  const half = live?.inningHalf ? String(live.inningHalf).slice(0, 3) : ''
  return `${half} ${live.currentInning}`.trim()
}

function formatTickerTime(value) {
  if (!value) return ''
  const dt = new Date(value)
  if (Number.isNaN(dt.getTime())) return ''
  return new Intl.DateTimeFormat('en-GB', {
    hour: '2-digit',
    minute: '2-digit',
    hour12: false,
    timeZone: 'Europe/Dublin',
  }).format(dt)
}
