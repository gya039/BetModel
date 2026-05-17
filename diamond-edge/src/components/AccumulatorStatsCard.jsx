import { useAccumulators } from '../hooks/useAccumulators'
import { fmtSignedMoney, fmtMoney } from '../utils/format'

export default function AccumulatorStatsCard() {
  const { stats, loading } = useAccumulators()

  if (loading || !stats || (stats.total === 0 && stats.pending === 0)) return null

  const pnlColor = stats.pnl >= 0 ? 'var(--bet)' : 'var(--skip)'
  const roiColor = stats.roi >= 0 ? 'var(--bet)' : 'var(--skip)'

  return (
    <div className="de-section">
      <div className="de-section-header" style={{ marginBottom: 'var(--sp-3)' }}>
        <div className="de-section-header__dot" style={{ background: 'var(--gold)' }} />
        <span className="de-section-header__title">Accumulator Stats</span>
        <span className="de-section-header__count">{stats.total}</span>
      </div>

      <div
        style={{
          background: 'var(--panel)',
          border: '1px solid var(--line)',
          borderRadius: 'var(--r-xl)',
          padding: 'var(--sp-4)',
          boxShadow: 'var(--shadow-panel)',
        }}
      >
        {/* Top row: big P&L + ROI */}
        <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'flex-start', marginBottom: 'var(--sp-4)' }}>
          <div>
            <div className="de-stat-card__label">Total P&L</div>
            <div className="de-stat-card__value" style={{ color: pnlColor, fontSize: 28 }}>
              {fmtSignedMoney(stats.pnl)}
            </div>
            <div className="de-stat-card__sub">from {fmtMoney(stats.staked)} staked</div>
          </div>
          <div style={{ textAlign: 'right' }}>
            <div className="de-stat-card__label">ROI</div>
            <div className="de-stat-card__value" style={{ color: roiColor, fontSize: 28 }}>
              {stats.roi >= 0 ? '+' : ''}{(stats.roi * 100).toFixed(1)}%
            </div>
            <div className="de-stat-card__sub">{stats.wins}W / {stats.losses}L</div>
          </div>
        </div>

        {/* Per-type breakdown */}
        {Object.entries(stats.byType).length > 0 && (
          <div
            style={{
              borderTop: '1px solid var(--line)',
              paddingTop: 'var(--sp-3)',
              display: 'flex',
              gap: 'var(--sp-3)',
              flexWrap: 'wrap',
            }}
          >
            {Object.entries(stats.byType).map(([type, t]) => {
              const typePnlColor = t.pnl >= 0 ? 'var(--bet)' : 'var(--skip)'
              return (
                <div key={type} style={{ flex: '1 1 80px', minWidth: 80 }}>
                  <div style={{ fontSize: 10, fontWeight: 600, letterSpacing: '0.08em', textTransform: 'uppercase', color: 'var(--muted)', marginBottom: 4 }}>
                    {type}
                  </div>
                  <div style={{ fontFamily: 'var(--font-mono)', fontSize: 15, fontWeight: 700, color: typePnlColor }}>
                    {t.pnl >= 0 ? '+' : ''}{t.pnl.toFixed(2)}
                  </div>
                  <div style={{ fontSize: 11, color: 'var(--muted)' }}>
                    {t.wins}W / {t.losses}L
                  </div>
                </div>
              )
            })}
          </div>
        )}
      </div>
    </div>
  )
}
