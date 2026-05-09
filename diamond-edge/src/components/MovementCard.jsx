import { motion } from 'framer-motion'
import { fmtNumber, fmtPercent, fmtMoney, safeText, toFiniteNumber } from '../utils/format'

const IP_WARN = 30

function SpLine({ name, era, ip, picked }) {
  if (!name || name === 'TBD') return null
  const smallSample = ip > 0 && ip < IP_WARN
  return (
    <div style={{
      display: 'flex', alignItems: 'center', gap: 6,
      color: picked ? 'var(--text)' : 'var(--muted)',
      fontWeight: picked ? 600 : 400,
    }}>
      <span style={{ fontSize: 11 }}>{picked ? '▶' : '·'}</span>
      <span style={{ fontSize: 12 }}>{name}</span>
      {era != null && (
        <span style={{ fontSize: 11, color: 'var(--muted)', fontFamily: 'var(--font-mono)' }}>
          ERA {Number(era).toFixed(2)}
          {smallSample && (
            <span style={{ color: 'var(--gold)', marginLeft: 4 }}>⚠ {Number(ip).toFixed(1)} IP</span>
          )}
        </span>
      )}
    </div>
  )
}

export default function MovementCard({ mov, index }) {
  const pickOddsDelta  = toFiniteNumber(mov.pickOddsDelta)
  const edgeDelta      = toFiniteNumber(mov.edgeDelta)
  const { decisionChanged, isNewBet } = mov

  const oddsDeltaClass = pickOddsDelta == null ? 'delta-neu' : pickOddsDelta > 0 ? 'delta-pos' : 'delta-neg'
  const edgeDeltaClass = edgeDelta == null     ? 'delta-neu' : edgeDelta > 0     ? 'delta-pos' : 'delta-neg'

  const aDecision = safeText(mov.afternoonDecision, 'skip').toLowerCase()
  const mDecision = safeText(mov.morningDecision,   'skip').toLowerCase()

  const homeIsPicked = mov.pickSide === 'home'
  const awayIsPicked = mov.pickSide === 'away'

  return (
    <motion.div
      className={`movement-card${decisionChanged ? ' changed' : ''}`}
      initial={{ opacity: 0, y: 12 }}
      animate={{ opacity: 1, y: 0 }}
      transition={{ duration: 0.2, delay: index * 0.04, ease: [0.22, 1, 0.36, 1] }}
    >
      {/* Header */}
      <div className="movement-card__matchup">
        {safeText(mov.awayAbbr)} @ {safeText(mov.homeAbbr)}
        {mov.pickLabel && (
          <span style={{ marginLeft: 8, fontSize: 13, color: isNewBet ? 'var(--bet)' : 'var(--muted)', fontWeight: isNewBet ? 600 : 400 }}>
            · {mov.pickLabel}
            {isNewBet && <span style={{ marginLeft: 6, fontSize: 10, background: 'rgba(96,165,250,0.16)', color: 'var(--accent)', borderRadius: 4, padding: '1px 5px' }}>NEW</span>}
          </span>
        )}
      </div>

      <div className="movement-card__rows">

        {/* New afternoon bet — show full pick details prominently */}
        {isNewBet && mov.afternoonPickOdds != null && (
          <div style={{
            display: 'grid', gridTemplateColumns: 'repeat(4, 1fr)',
            gap: 8, padding: '8px 0 4px',
          }}>
            {[
              { label: 'Odds',   value: fmtNumber(mov.afternoonPickOdds) },
              { label: 'Edge',   value: fmtPercent(mov.afternoonEdge) },
              { label: 'Stake',  value: mov.afternoonStakeEur > 0 ? fmtMoney(mov.afternoonStakeEur) : '—' },
              { label: 'Return', value: mov.afternoonReturn  != null ? fmtMoney(mov.afternoonReturn) : '—' },
            ].map(({ label, value }) => (
              <div key={label} style={{ background: 'rgba(96,165,250,0.07)', border: '1px solid rgba(96,165,250,0.18)', borderRadius: 8, padding: '6px 8px' }}>
                <div style={{ fontSize: 10, color: 'var(--muted)', textTransform: 'uppercase', letterSpacing: '0.06em', marginBottom: 2 }}>{label}</div>
                <div style={{ fontSize: 13, fontWeight: 700, color: 'var(--bet)', fontFamily: 'var(--font-mono)' }}>{value}</div>
              </div>
            ))}
          </div>
        )}

        {/* Existing pick — odds movement row */}
        {!isNewBet && mov.morningPickOdds != null && (
          <div className="movement-card__row">
            <span className="movement-card__row-label">Pick odds</span>
            <div className="movement-card__values">
              <span className="movement-card__morning">{fmtNumber(mov.morningPickOdds)}</span>
              <span className="movement-card__arrow" style={{ color: 'var(--muted-dim)' }}>→</span>
              <span className="movement-card__afternoon" style={{ color: 'var(--text)' }}>
                {fmtNumber(mov.afternoonPickOdds)}
              </span>
              {pickOddsDelta != null && (
                <span className={`movement-card__delta ${oddsDeltaClass}`}>
                  {pickOddsDelta > 0 ? '+' : ''}{fmtNumber(pickOddsDelta)}
                </span>
              )}
            </div>
          </div>
        )}

        {/* Edge row */}
        {mov.morningEdge != null && (
          <div className="movement-card__row">
            <span className="movement-card__row-label">Edge</span>
            <div className="movement-card__values">
              <span className="movement-card__morning">{fmtPercent(mov.morningEdge)}</span>
              <span className="movement-card__arrow" style={{ color: 'var(--muted-dim)' }}>→</span>
              <span className="movement-card__afternoon" style={{ color: 'var(--text)' }}>
                {fmtPercent(mov.afternoonEdge)}
              </span>
              {edgeDelta != null && (
                <span className={`movement-card__delta ${edgeDeltaClass}`}>
                  {edgeDelta > 0 ? '+' : ''}{fmtPercent(edgeDelta)}
                </span>
              )}
            </div>
          </div>
        )}

        {/* Stake + return for stable BETs */}
        {!isNewBet && aDecision === 'bet' && mov.afternoonStakeEur > 0 && (
          <div className="movement-card__row">
            <span className="movement-card__row-label">Stake / Return</span>
            <div className="movement-card__values" style={{ gap: 6 }}>
              <span style={{ fontFamily: 'var(--font-mono)', fontSize: 13 }}>{fmtMoney(mov.afternoonStakeEur)}</span>
              <span style={{ color: 'var(--muted-dim)', fontSize: 11 }}>→</span>
              <span style={{ fontFamily: 'var(--font-mono)', fontSize: 13, color: 'var(--accent)' }}>
                {mov.afternoonReturn != null ? fmtMoney(mov.afternoonReturn) : '—'}
              </span>
            </div>
          </div>
        )}

        {/* Decision row */}
        <div className="movement-card__row">
          <span className="movement-card__row-label">Decision</span>
          <div className="movement-card__values">
            <span className={`badge badge--${mDecision}`} style={{ fontSize: 10, padding: '2px 7px' }}>
              {safeText(mov.morningDecision)}
            </span>
            <span style={{ color: 'var(--muted-dim)', fontSize: 12 }}>→</span>
            <span className={`badge badge--${aDecision}`} style={{ fontSize: 10, padding: '2px 7px' }}>
              {safeText(mov.afternoonDecision)}
            </span>
          </div>
        </div>

        {/* SP info */}
        {(mov.homeSpName || mov.awaySpName) && (
          <div style={{ display: 'flex', flexDirection: 'column', gap: 3, paddingTop: 6, borderTop: '1px solid var(--line)', marginTop: 4 }}>
            <SpLine name={mov.awaySpName} era={mov.awaySpEra} ip={mov.awaySpIp} picked={awayIsPicked} />
            <SpLine name={mov.homeSpName} era={mov.homeSpEra} ip={mov.homeSpIp} picked={homeIsPicked} />
          </div>
        )}

      </div>
    </motion.div>
  )
}
