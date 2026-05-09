import { motion } from 'framer-motion'
import { fmtMoney, fmtNumber, fmtPercent, safeText } from '../utils/format'

export default function AccumulatorCard({ accum, index, liveStatuses }) {
  const returnMult = fmtNumber(accum.combined_odds)
  const potential = fmtMoney(accum.potential_return)
  const stake = fmtMoney(accum.stake)
  const bucket = accum.bucket === 'fun' || accum.non_core ? 'fun' : 'core'
  const safety = accum.safety_rating
  const score = Number.isFinite(Number(accum.score)) ? Number(accum.score) : null
  const typeLabel = accum.type ?? `${accum.legs?.length ?? '-'}-Leg Acca`
  const legCount = accum.legs?.length ?? 0

  return (
    <motion.div
      className={`accum-card accum-card--${bucket}`}
      initial={{ opacity: 0, y: 16 }}
      animate={{ opacity: 1, y: 0 }}
      transition={{ duration: 0.22, delay: index * 0.05, ease: [0.22, 1, 0.36, 1] }}
    >
      <div className="accum-card__header">
        <div className="accum-card__header-main">
          <div className="accum-card__eyebrow">Accumulator Slip</div>
          <div className="accum-card__type-row">
            <div className="accum-card__type">{typeLabel}</div>
            <div className="accum-card__count">{legCount} legs</div>
          </div>
          <div className="accum-card__meta">
            <AccaBadge kind={bucket}>{bucket === 'fun' ? 'Fun' : 'Core'}</AccaBadge>
            {safety && <AccaBadge kind={`risk-${String(safety).toLowerCase()}`}>{safety}</AccaBadge>}
            {score != null && <span className="accum-card__score">Score {score.toFixed(2)}</span>}
          </div>
        </div>
        <div className="accum-card__hero-metric">
          <span>Combined</span>
          <strong className="accum-card__odds">{returnMult}x</strong>
        </div>
      </div>

      {accum.reason && <div className="accum-card__reason">{safeText(accum.reason)}</div>}

      <div className="accum-card__legs">
        {accum.legs?.map((leg, i) => {
          const live = liveStatuses?.games?.[String(leg.gamePk)]
          const isLive = live?.gameStatus === 'LIVE'
          const isFinal = live?.gameStatus === 'FINAL'
          const hasScore = live != null && live.awayScore != null && live.homeScore != null
          const [awayAbbr, homeAbbr] = (leg.game ?? '').split(/\s*@\s*/).map(s => s.trim())

          return (
            <div key={i} className="accum-card__leg">
              <div className="accum-card__leg-index">{i + 1}</div>
              <div className="accum-card__leg-main">
                <div className="accum-card__leg-label">{safeText(leg.label)}</div>
                <div className="accum-card__leg-game">{safeText(leg.game)}</div>
                <div className="accum-card__leg-badges">
                  {leg.market && <AccaBadge kind="market">{safeText(leg.market)}</AccaBadge>}
                  {leg.tier && <AccaBadge kind={`tier-${String(leg.tier).toLowerCase()}`}>Tier {safeText(leg.tier)}</AccaBadge>}
                  {leg.risk_class && <AccaBadge kind={`risk-${String(leg.risk_class).toLowerCase()}`}>{safeText(leg.risk_class)}</AccaBadge>}
                </div>
              </div>
              <div className="accum-card__leg-side">
                <div className="accum-card__leg-odds">{fmtNumber(leg.odds)}</div>
                {hasScore && (
                  <div className={`accum-leg-score${isLive ? ' live' : isFinal ? ' final' : ''}`}>
                    {isLive && <span className="accum-leg-score__dot" />}
                    <span>{awayAbbr} {live.awayScore}</span>
                    <span className="accum-leg-score__sep">-</span>
                    <span>{live.homeScore} {homeAbbr}</span>
                    {isLive && live.currentInning && (
                      <span className="accum-leg-score__inning">
                        {live.inningHalf?.slice(0, 3) ?? ''} {live.currentInning}
                      </span>
                    )}
                  </div>
                )}
                {!hasScore && leg.edge != null && (
                  <div className="accum-card__leg-edge">{fmtPercent(leg.edge)} edge</div>
                )}
              </div>
            </div>
          )
        })}
      </div>

      <div className="accum-card__footer">
        <div className="accum-card__payout">
          <div className="accum-card__payout-cell">
            <span>Stake</span>
            <strong>{stake}</strong>
          </div>
          <div className="accum-card__payout-cell accum-card__payout-cell--return">
            <span>Potential return</span>
            <strong className="accum-card__return">{potential}</strong>
          </div>
        </div>
      </div>
    </motion.div>
  )
}

function AccaBadge({ kind, children }) {
  return <span className={`accum-badge accum-badge--${kind}`}>{children}</span>
}
