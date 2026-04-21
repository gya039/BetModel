import { useState } from 'react'
import { motion, AnimatePresence } from 'framer-motion'
import { decisionForRow, pickLabel, stakeLabel, skipReason, edgeTier } from '../utils/decisions'
import { deriveConfidence, deriveReason, betterPitcherSide, formSegments } from '../utils/deriveStats'
import { fmtMoney, fmtNumber, fmtPercent, fmtSignedMoney, safeText, toFiniteNumber } from '../utils/format'

export default function PickCard({ game, defaultExpanded }) {
  const decision = decisionForRow(game)
  const label = pickLabel(game)
  const stake = stakeLabel(game)
  const reason = deriveReason(game)
  const confidence = deriveConfidence(game)
  const edge = toFiniteNumber(game.edge) || 0
  const odds = toFiniteNumber(game.useRl ? game.rlPickOdds : game.pickOdds)
  const stakeEur = toFiniteNumber(game.stake?.eur) || 0
  const potentialReturn = odds != null && stakeEur > 0 ? odds * stakeEur : null
  const tier = edgeTier(edge)

  const status = game.gameStatus || 'NOT_STARTED'
  const isLive = status === 'LIVE'
  const isFinal = status === 'FINAL'
  const settledResult = game.settledResult
  const settledResultKey = settledResult ? String(settledResult).toLowerCase() : null
  const displayStatus = settledResult ? String(settledResult).toUpperCase() : isLive ? 'LIVE' : isFinal ? 'FINAL' : decision
  const pickedSide = game.pickSide

  const [expanded, setExpanded] = useState(defaultExpanded ?? false)

  const stateClass = settledResultKey && ['win', 'loss', 'push'].includes(settledResultKey)
    ? settledResultKey
    : isLive ? 'live' : isFinal ? 'final' : decision.toLowerCase()

  const cardClass = [
    'pick-card',
    expanded ? 'pick-card--expanded' : 'pick-card--collapsed',
    `pick-card--${stateClass}`,
  ].join(' ')

  return (
    <motion.div
      className={cardClass}
      layout
      initial={{ opacity: 0, y: 12 }}
      animate={{ opacity: 1, y: 0 }}
      transition={{ duration: 0.22, ease: [0.22, 1, 0.36, 1] }}
    >
      <button
        className="pick-card__summary"
        onClick={() => setExpanded(e => !e)}
        aria-expanded={expanded}
      >
        <div className="pick-card__summary-top">
          <div className="pick-card__identity">
            <div className="pick-card__teams">
              <span className={`pick-card__abbr${pickedSide === 'away' && !isFinal ? ` picked${isLive ? '-live' : ''}` : ''}`}>
                {safeText(game.awayAbbr)}
              </span>
              <span className="pick-card__at">@</span>
              <span className={`pick-card__abbr${pickedSide === 'home' && !isFinal ? ` picked${isLive ? '-live' : ''}` : ''}`}>
                {safeText(game.homeAbbr)}
              </span>
            </div>
            <div className="pick-card__series">
              {game.seriesGameNumber > 0
                ? `Game ${game.seriesGameNumber} of ${game.gamesInSeries || '—'} · ${safeText(game.homeTeam)} vs ${safeText(game.awayTeam)}`
                : `${safeText(game.awayTeam)} at ${safeText(game.homeTeam)}`}
            </div>
          </div>

          <div className="pick-card__badges">
            <span className={`badge badge--${settledResultKey || (isLive ? 'live' : isFinal ? 'final' : decision.toLowerCase())}`}>
              {isLive && <span className="badge--live-dot" />}
              {displayStatus}
            </span>
          </div>
        </div>

        <div className="pick-card__compact-grid">
          <Metric label="Pick" value={label} emphasis />
          <Metric label="Odds" value={fmtNumber(odds)} accent="accent" />
          <Metric label="Stake" value={stakeEur > 0 ? fmtMoney(stakeEur) : '—'} />
          <Metric label="Return" value={potentialReturn != null ? fmtMoney(potentialReturn) : '—'} accent="gold" />
          <Metric
            label={settledResult ? 'P&L' : 'Edge'}
            value={settledResult ? fmtSignedMoney(game.settledPnl) : decision === 'BET' && !isLive ? fmtPercent(edge) : '—'}
            accent={settledResultKey === 'win' ? 'bet' : settledResultKey === 'loss' ? 'loss' : decision === 'BET' ? 'bet' : undefined}
          />
        </div>

        {isFinal && game.finalScore && (
          <div className="pick-card__final-score">
            {safeText(game.awayAbbr)} {safeText(game.finalScore.away)} — {safeText(game.finalScore.home)} {safeText(game.homeAbbr)}
          </div>
        )}

        {isLive && (
          <div className="pick-card__live-note">
            {game.liveScore
              ? `${safeText(game.awayAbbr)} ${safeText(game.liveScore.away)} — ${safeText(game.liveScore.home)} ${safeText(game.homeAbbr)} · ${safeText(game.liveScore.half)} ${safeText(game.liveScore.inning)} · morning pick frozen`
              : 'Morning pick frozen · no live edge recalculation'}
          </div>
        )}

        {decision === 'SKIP' && !isFinal && !isLive && (
          <div className="pick-card__skip-reason">{skipReason(game)}</div>
        )}

        <span className={`pick-card__expand-cue${expanded ? ' expanded' : ''}`}>
          {expanded ? 'Less detail' : 'Analysis'}
          <svg width="14" height="14" viewBox="0 0 14 14" fill="none">
            <path d="M3 5l4 4 4-4" stroke="currentColor" strokeWidth="1.6" strokeLinecap="round" strokeLinejoin="round" />
          </svg>
        </span>
      </button>

      <AnimatePresence initial={false}>
        {expanded && (
          <motion.div
            className="pick-card__details"
            initial={{ height: 0, opacity: 0 }}
            animate={{ height: 'auto', opacity: 1 }}
            exit={{ height: 0, opacity: 0 }}
            transition={{ duration: 0.25, ease: [0.22, 1, 0.36, 1] }}
          >
            <div className="pick-card__details-inner">
              {decision === 'BET' && !isLive && (
                <>
                  <EdgeBar edge={edge} tier={tier} />
                  <div className="pick-card__stake-row">
                    <span className="pick-card__stake-label">Stake</span>
                    <span className="pick-card__stake-value">{stake}</span>
                  </div>
                </>
              )}

              {game.homeSpName && <PitcherRow game={game} />}

              {game.modelProb != null && game.marketImplied != null && !isLive && (
                <ProbBar modelProb={game.modelProb} marketImplied={game.marketImplied} />
              )}

              {!isLive && <RunLineDiagnostic game={game} />}

              {game.homeL10WP != null && <FormRow game={game} />}

              <div className="pick-card__reason">{reason}</div>
              <ConfidenceRow confidence={confidence} />
            </div>
          </motion.div>
        )}
      </AnimatePresence>
    </motion.div>
  )
}

function RunLineDiagnostic({ game }) {
  if (!game.spreadModelLoaded && !game.spreadModelStatus) return null

  const bestEdge = toFiniteNumber(game.spreadBestEdge ?? game.spreadEdge)
  const coverProb = toFiniteNumber(game.spreadBestCoverProb ?? game.spreadCoverProb)
  const line = game.spreadBestPoint ?? game.spreadPoint
  const odds = toFiniteNumber(game.spreadBestOdds ?? game.spreadOdds)
  const positive = toFiniteNumber(game.spreadPositiveLineCount) || 0
  const options = toFiniteNumber(game.spreadOptionCount) || 0
  const validation = game.spreadModelValidationPassed === true
    ? 'Passed'
    : game.spreadModelValidationPassed === false ? 'Failed' : 'Unknown'

  return (
    <div className="rl-card-diag">
      <div className="rl-card-diag__header">
        <span>Run Line</span>
        <strong>{game.useRl ? 'Selected' : formatRlReason(game.spreadRejectionReason || game.spreadModelStatus)}</strong>
      </div>
      <div className="rl-card-diag__grid">
        <MiniMetric label="Line" value={line != null ? `${line > 0 ? '+' : ''}${line}` : '-'} />
        <MiniMetric label="Edge" value={bestEdge != null ? fmtPercent(bestEdge) : '-'} />
        <MiniMetric label="Cover" value={coverProb != null ? fmtPercent(coverProb) : '-'} />
        <MiniMetric label="+EV Lines" value={`${positive}/${options}`} />
        <MiniMetric label="Odds" value={odds != null ? fmtNumber(odds) : '-'} />
        <MiniMetric label="Validation" value={validation} />
      </div>
    </div>
  )
}

function MiniMetric({ label, value }) {
  return (
    <div className="rl-card-diag__metric">
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

function Metric({ label, value, emphasis, accent }) {
  return (
    <div className={`pick-card__metric${emphasis ? ' emphasis' : ''}${accent ? ` ${accent}` : ''}`}>
      <span>{label}</span>
      <strong>{value}</strong>
    </div>
  )
}

function EdgeBar({ edge, tier }) {
  const safeEdge = toFiniteNumber(edge) || 0
  const pct = Math.min((safeEdge / 0.20) * 100, 100)
  const color = tier.color

  return (
    <div className="de-edge-bar">
      <div className="de-edge-bar__header">
        <span className="de-edge-bar__label">Edge</span>
        <span className="de-edge-bar__value" style={{ color }}>
          {fmtPercent(safeEdge)} · {tier.label}
        </span>
      </div>
      <div className="de-edge-bar__track">
        <div
          className="de-edge-bar__fill"
          style={{ width: `${pct}%`, background: color, color }}
        />
      </div>
    </div>
  )
}

function PitcherRow({ game }) {
  const betterSide = betterPitcherSide(game)
  const homeIsBetter = betterSide === 'home'

  return (
    <div className="pitcher-row">
      <div className="pitcher-row__side away">
        <div className="pitcher-row__label">Away SP</div>
        <div className="pitcher-row__name">{formatName(game.awaySpName)}</div>
        <div className={`pitcher-row__stat${!homeIsBetter ? ' better' : ''}`}>
          {fmtNumber(game.awaySpEra)} ERA · {fmtNumber(game.awaySpWhip)} WHIP
        </div>
        <div className="pitcher-row__stat" style={{ color: 'var(--muted-dim)', marginTop: 2 }}>
          {game.awaySpW ?? '?'}-{game.awaySpL ?? '?'}
        </div>
      </div>

      <div className="pitcher-row__vs">VS</div>

      <div className="pitcher-row__side home">
        <div className="pitcher-row__label" style={{ textAlign: 'right' }}>Home SP</div>
        <div className="pitcher-row__name">{formatName(game.homeSpName)}</div>
        <div className={`pitcher-row__stat${homeIsBetter ? ' better' : ''}`}>
          {fmtNumber(game.homeSpEra)} ERA · {fmtNumber(game.homeSpWhip)} WHIP
        </div>
        <div className="pitcher-row__stat" style={{ color: 'var(--muted-dim)', marginTop: 2 }}>
          {game.homeSpW ?? '?'}-{game.homeSpL ?? '?'}
        </div>
      </div>
    </div>
  )
}

function ProbBar({ modelProb, marketImplied }) {
  const model = toFiniteNumber(modelProb)
  const market = toFiniteNumber(marketImplied)
  if (model == null || market == null) return null

  const modelPct = Math.round(model * 100)
  const marketPct = Math.round(market * 100)
  const edge = ((model - market) * 100).toFixed(1)
  const positive = model > market

  return (
    <div className="prob-bar">
      <div className="prob-bar__header">
        <span className="prob-bar__label">Model vs Market</span>
        <span style={{ fontSize: 11, fontFamily: 'var(--font-mono)', color: positive ? 'var(--bet)' : 'var(--skip)' }}>
          {positive ? '+' : ''}{edge}% edge
        </span>
      </div>
      <div className="prob-bar__track">
        <div className="prob-bar__model" style={{ width: `${modelPct}%` }} />
        <div className="prob-bar__market-tick" style={{ left: `${marketPct}%` }} />
      </div>
      <div className="prob-bar__values">
        <span className="prob-bar__val model">Model {modelPct}%</span>
        <span className="prob-bar__val market">Market {marketPct}%</span>
      </div>
    </div>
  )
}

function FormRow({ game }) {
  const homeSegs = formSegments(game.homeL10WP)
  const awaySegs = formSegments(game.awayL10WP)

  return (
    <div className="form-row">
      <div className="form-row__side">
        <div className="form-row__label">{safeText(game.awayAbbr)} L10</div>
        <div className="form-row__dots">
          {Array.from({ length: 10 }, (_, i) => (
            <div key={i} className={`form-row__dot ${i < awaySegs ? 'on' : 'off'}`} />
          ))}
        </div>
        <div className="form-row__pct">{fmtPercent(game.awayL10WP, 0)}</div>
      </div>

      <div className="form-row__side" style={{ textAlign: 'right' }}>
        <div className="form-row__label">{safeText(game.homeAbbr)} L10</div>
        <div className="form-row__dots" style={{ justifyContent: 'flex-end' }}>
          {Array.from({ length: 10 }, (_, i) => (
            <div key={i} className={`form-row__dot ${i < homeSegs ? 'on' : 'off'}`} />
          ))}
        </div>
        <div className="form-row__pct">{fmtPercent(game.homeL10WP, 0)}</div>
      </div>
    </div>
  )
}

function ConfidenceRow({ confidence }) {
  return (
    <div style={{ display: 'flex', alignItems: 'center', gap: 8, marginTop: 4 }}>
      <span style={{ fontSize: 10, fontWeight: 600, letterSpacing: '0.08em', textTransform: 'uppercase', color: 'var(--muted)' }}>
        Confidence
      </span>
      <div style={{ display: 'flex', gap: 3, flex: 1 }}>
        {Array.from({ length: 10 }, (_, i) => {
          const filled = i < confidence
          const color = confidence >= 8 ? 'var(--bet)' : confidence >= 5 ? 'var(--accent)' : 'var(--skip)'
          return (
            <div
              key={i}
              style={{
                flex: 1,
                height: 4,
                borderRadius: 99,
                background: filled ? color : 'var(--line)',
                boxShadow: filled ? `0 0 4px ${color}` : 'none',
                transition: 'background 0.3s',
              }}
            />
          )
        })}
      </div>
      <span style={{ fontFamily: 'var(--font-mono)', fontSize: 12, color: 'var(--muted)', minWidth: 28 }}>
        {confidence}/10
      </span>
    </div>
  )
}

function formatName(name) {
  if (!name) return 'TBD'
  const parts = name.split(' ')
  if (parts.length <= 1) return name
  return `${parts[0][0]}. ${parts.slice(1).join(' ')}`
}
