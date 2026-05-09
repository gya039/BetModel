import { useState, useMemo } from 'react'
import { motion, AnimatePresence } from 'framer-motion'
import { pnlColor, fmtEur } from '../utils/deriveStats'
import { fmtMoney, fmtNumber } from '../utils/format'

const FILTERS = ['All', 'Pending', 'Win', 'Loss']

export default function AccumulatorsTable({ accas, stats }) {
  const [filter, setFilter] = useState('All')

  const settled = (accas ?? []).filter(r => r.result && r.result !== '')

  const filtered = useMemo(() => {
    let rows = settled
    if (filter !== 'All') rows = rows.filter(r => r.result === filter)
    return [...rows].sort((a, b) => (a.date > b.date ? -1 : 1))
  }, [settled, filter])

  return (
    <div>
      {/* Stats cards */}
      {stats && (stats.total > 0 || stats.pending > 0) && (
        <div className="de-stats-grid" style={{ marginBottom: 'var(--sp-4)' }}>
          <div className="de-stat-card">
            <div className="de-stat-card__label">Win Rate</div>
            <div className="de-stat-card__value" style={{ color: stats.total === 0 ? 'var(--muted)' : stats.wins >= stats.losses ? 'var(--bet)' : 'var(--skip)' }}>
              {stats.total > 0 ? ((stats.wins / stats.total) * 100).toFixed(1) : '—'}%
            </div>
            <div className="de-stat-card__sub">{stats.wins}W / {stats.losses}L</div>
          </div>
          <div className="de-stat-card">
            <div className="de-stat-card__label">Total Staked</div>
            <div className="de-stat-card__value">{stats.staked > 0 ? fmtMoney(stats.staked, 0) : '—'}</div>
            <div className="de-stat-card__sub">{stats.total} settled · {stats.pending} pending</div>
          </div>
          <div className="de-stat-card">
            <div className="de-stat-card__label">Net P&L</div>
            <div className="de-stat-card__value" style={{ color: stats.total > 0 ? pnlColor(stats.pnl) : 'var(--muted)' }}>
              {stats.total > 0 ? fmtEur(stats.pnl) : '—'}
            </div>
            <div className="de-stat-card__sub">Excluded from main bankroll</div>
          </div>
          <div className="de-stat-card">
            <div className="de-stat-card__label">ROI</div>
            <div className="de-stat-card__value" style={{ color: stats.total > 0 ? pnlColor(stats.roi) : 'var(--muted)' }}>
              {stats.total > 0 ? `${stats.roi >= 0 ? '+' : ''}${(stats.roi * 100).toFixed(1)}%` : '—'}
            </div>
            <div className="de-stat-card__sub">Return on staked</div>
          </div>
        </div>
      )}

      {/* Filter pills */}
      <div className="de-results-filters">
        {FILTERS.map(f => (
          <button
            key={f}
            className={`de-filter-pill${filter === f ? ' active' : ''}`}
            onClick={() => setFilter(f)}
          >
            {f}
            {f !== 'All' && (
              <span style={{ marginLeft: 5, opacity: 0.6 }}>
                {settled.filter(r => r.result === f).length}
              </span>
            )}
          </button>
        ))}
      </div>

      {/* Acca list */}
      <div style={{ display: 'flex', flexDirection: 'column', gap: 8, marginTop: 12 }}>
        {filtered.length === 0 ? (
          <div style={{ textAlign: 'center', color: 'var(--muted)', padding: '32px 0', fontSize: 13 }}>
            No accumulators for this filter
          </div>
        ) : (
          filtered.map((row, i) => (
            <AccaRow key={i} row={row} />
          ))
        )}
      </div>
    </div>
  )
}

function AccaRow({ row }) {
  const [open, setOpen] = useState(false)
  const pnl = Number(row.pnl) || 0
  const res = (row.result || '').toLowerCase()

  let legs = []
  try { legs = JSON.parse(row.legs) } catch { legs = [] }

  // Per-leg results are stored in the CSV as leg.legResult (set at settlement time).
  // Older entries (pre-fix) won't have it — those show "?" in the UI.
  const legsWithResults = legs

  return (
    <div
      style={{
        background: 'var(--panel)',
        border: `1px solid ${
          res === 'win'     ? 'var(--bet-border)' :
          res === 'loss'    ? 'rgba(255,90,60,0.25)' :
          res === 'pending' ? 'rgba(245,200,66,0.30)' :
          'var(--line)'
        }`,
        borderRadius: 'var(--r-lg)',
        overflow: 'hidden',
        boxShadow: 'var(--shadow-panel)',
      }}
    >
      {/* Row header — click to expand */}
      <button
        onClick={() => setOpen(o => !o)}
        style={{
          width: '100%',
          display: 'flex',
          alignItems: 'center',
          gap: 10,
          padding: '12px 16px',
          textAlign: 'left',
          background: 'transparent',
        }}
      >
        {/* Date */}
        <span style={{ fontSize: 11, color: 'var(--muted)', fontFamily: 'var(--font-mono)', minWidth: 42 }}>
          {fmtDate(row.date)}
        </span>

        {/* Type badge */}
        <span style={{
          fontSize: 10, fontWeight: 700, letterSpacing: '0.08em', textTransform: 'uppercase',
          background: 'var(--gold-dim)', color: 'var(--gold)',
          padding: '2px 7px', borderRadius: 4, flexShrink: 0,
        }}>
          {row.type}
        </span>

        {/* Legs summary */}
        <span style={{ flex: 1, fontSize: 12, color: 'var(--text-secondary)', overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>
          {legs.map(l => l.label).join(' + ')}
        </span>

        {/* Combined odds */}
        <span style={{ fontSize: 12, fontFamily: 'var(--font-mono)', color: 'var(--accent)', flexShrink: 0 }}>
          {fmtNumber(Number(row.combined_odds))}x
        </span>

        {/* Result badge */}
        <span className={`result-badge ${res}`} style={{ flexShrink: 0 }}>
          {row.result}
        </span>

        {/* P&L */}
        <span style={{ fontSize: 12, fontFamily: 'var(--font-mono)', fontWeight: 600, color: pnlColor(pnl), flexShrink: 0, minWidth: 52, textAlign: 'right' }}>
          {pnl !== 0 ? fmtEur(pnl) : '—'}
        </span>

        {/* Chevron */}
        <span style={{ color: 'var(--muted)', fontSize: 10, flexShrink: 0, transition: 'transform 0.2s', transform: open ? 'rotate(180deg)' : 'rotate(0deg)' }}>
          ▼
        </span>
      </button>

      {/* Expandable legs detail */}
      <AnimatePresence initial={false}>
        {open && (
          <motion.div
            initial={{ height: 0, opacity: 0 }}
            animate={{ height: 'auto', opacity: 1 }}
            exit={{ height: 0, opacity: 0 }}
            transition={{ duration: 0.2, ease: [0.22, 1, 0.36, 1] }}
            style={{ overflow: 'hidden' }}
          >
            <div style={{ borderTop: '1px solid var(--line)', padding: '12px 16px', display: 'flex', flexDirection: 'column', gap: 8 }}>
              {legsWithResults.map((leg, i) => {
                const legRes = leg.legResult?.toLowerCase()
                return (
                  <div key={i} style={{
                    display: 'flex', alignItems: 'center', gap: 10,
                    padding: '8px 12px',
                    background: 'var(--panel-alt)',
                    borderRadius: 'var(--r-md)',
                    border: `1px solid ${legRes === 'win' ? 'var(--bet-border)' : legRes === 'loss' ? 'rgba(255,90,60,0.2)' : 'var(--line)'}`,
                  }}>
                    <span style={{ fontSize: 14 }}>
                      {legRes === 'win' ? '✅' : legRes === 'loss' ? '❌' : '⏳'}
                    </span>
                    <span style={{ flex: 1, fontSize: 13, fontWeight: 600 }}>{leg.label}</span>
                    <span style={{ fontSize: 12, fontFamily: 'var(--font-mono)', color: 'var(--accent)' }}>
                      {fmtNumber(leg.odds)}
                    </span>
                    {leg.legResult ? (
                      <span className={`result-badge ${legRes}`} style={{ fontSize: 10 }}>
                        {leg.legResult}
                      </span>
                    ) : (
                      <span style={{ fontSize: 10, color: 'var(--muted)', fontStyle: 'italic' }}>—</span>
                    )}
                  </div>
                )
              })}
              <div style={{ fontSize: 11, color: 'var(--muted)', marginTop: 4, display: 'flex', justifyContent: 'space-between' }}>
                <span>Stake: {fmtMoney(Number(row.stake))}</span>
                <span>
                  {row.result === 'Win'
                    ? `Return: ${fmtMoney(Number(row.stake) * Number(row.combined_odds))}`
                    : row.result === 'Pending'
                    ? <span style={{ color: 'var(--gold)' }}>Potential: {fmtMoney(Number(row.stake) * Number(row.combined_odds))}</span>
                    : `Lost: ${fmtMoney(Number(row.stake))}`}
                </span>
              </div>
            </div>
          </motion.div>
        )}
      </AnimatePresence>
    </div>
  )
}

function fmtDate(d) {
  if (!d) return '—'
  const dt = new Date(String(d) + 'T12:00:00')
  return dt.toLocaleDateString('en-US', { month: 'short', day: 'numeric' })
}
