import { useEffect, useMemo, useState } from 'react'
import { pnlColor, fmtEur } from '../utils/deriveStats'
import { fmtMoney, fmtNumber, fmtSignedMoney, safeText, toFiniteNumber } from '../utils/format'
import { todayStr } from '../utils/paths'

const FILTERS = ['All', 'Win', 'Loss', 'Push', 'Pending']

export default function ResultsTable({ rows }) {
  const [filter, setFilter] = useState('All')

  const filtered = useMemo(() => {
    let data = (rows ?? []).filter(row => row.decision !== 'SKIP')
    if (filter !== 'All') data = data.filter(row => row.result === filter)

    return [...data].sort((a, b) => {
      if (a.date !== b.date) return a.date > b.date ? -1 : 1
      return (a.rowIndex ?? 0) - (b.rowIndex ?? 0)
    })
  }, [rows, filter])

  return (
    <div>
      <div className="de-results-filters">
        {FILTERS.map(f => (
          <button
            key={f}
            className={`de-filter-pill${filter === f ? ' active' : ''}`}
            onClick={() => setFilter(f)}
          >
            {f}
            {f !== 'All' && rows && (
              <span style={{ marginLeft: 5, opacity: 0.6 }}>
                {rows.filter(row => row.decision !== 'SKIP' && row.result === f).length}
              </span>
            )}
          </button>
        ))}
      </div>

      <GroupedBetHistory rows={filtered} />
    </div>
  )
}

function GroupedBetHistory({ rows }) {
  const groups = useMemo(() => groupRowsByDay(rows), [rows])
  const [expandedDates, setExpandedDates] = useState(() => new Set(groups[0] ? [groups[0].date] : []))

  useEffect(() => {
    setExpandedDates(current => {
      const visibleDates = new Set(groups.map(group => group.date))
      const kept = new Set([...current].filter(date => visibleDates.has(date)))
      if (kept.size === 0 && groups[0]) kept.add(groups[0].date)
      return kept
    })
  }, [groups])

  if (groups.length === 0) {
    return <div className="de-results-empty">No results for this filter</div>
  }

  function toggleDate(date) {
    setExpandedDates(current => {
      const next = new Set(current)
      if (next.has(date)) next.delete(date)
      else next.add(date)
      return next
    })
  }

  return (
    <div className="bet-history-groups">
      {groups.map(group => (
        <DayBetGroup
          key={group.date}
          group={group}
          expanded={expandedDates.has(group.date)}
          onToggle={() => toggleDate(group.date)}
        />
      ))}
    </div>
  )
}

function DayBetGroup({ group, expanded, onToggle }) {
  const isProfit = group.dailyProfit > 0
  const isLoss = group.dailyProfit < 0
  const profitClass = group.displayPending ? 'pending' : isProfit ? 'win' : isLoss ? 'loss' : 'flat'

  return (
    <section className={`bet-day-group${expanded ? ' expanded' : ''}`}>
      <button
        type="button"
        className="bet-day-group__header"
        onClick={onToggle}
        aria-expanded={expanded}
      >
        <span className={`bet-day-group__chev${expanded ? ' expanded' : ''}`} aria-hidden="true">&rsaquo;</span>
        <span className="bet-day-group__date">{fmtDate(group.date)}</span>
        <span className="bet-day-group__metric">Bets: <strong>{group.betCount}</strong></span>
        <span className="bet-day-group__metric">Stake: <strong>{fmtMoney(group.totalStake)}</strong></span>
        <span className={`bet-day-group__profit ${profitClass}`}>
          Profit: <strong>{group.displayPending ? 'Pending' : fmtSignedMoney(group.dailyProfit)}</strong>
        </span>
      </button>

      {expanded && (
        <div className="bet-day-group__body">
          <div className="de-results-table-wrap">
            <table className="de-results-table bet-day-group__table">
              <thead>
                <tr>
                  <th>Game</th>
                  <th>Pick</th>
                  <th>Odds</th>
                  <th>Stake</th>
                  <th>Result</th>
                  <th className="text-right">P&L</th>
                  <th className="text-right">Bankroll</th>
                </tr>
              </thead>
              <tbody>
                {group.rows.map((row, i) => (
                  <ResultRow key={`${row.date}-${row.game}-${i}`} row={row} />
                ))}
              </tbody>
            </table>
          </div>
        </div>
      )}
    </section>
  )
}

function ResultRow({ row }) {
  const pnl = toFiniteNumber(row.profit_loss) || 0
  const res = row.result?.toLowerCase() || 'pending'

  return (
    <tr>
      <td data-label="Game" style={{ fontSize: 12, color: 'var(--text-secondary)' }}>{safeText(row.game)}</td>
      <td data-label="Pick" style={{ fontWeight: 600, whiteSpace: 'nowrap' }}>{safeText(row.pick)}</td>
      <td data-label="Odds" style={{ fontFamily: 'var(--font-mono)', color: 'var(--accent)' }}>
        {fmtNumber(row.odds)}
      </td>
      <td data-label="Stake" style={{ fontFamily: 'var(--font-mono)', fontSize: 12, color: 'var(--muted)' }}>
        {fmtMoney(row.stake_eur)}
      </td>
      <td data-label="Result">
        <span className={`result-badge ${res}`}>{safeText(row.result)}</span>
      </td>
      <td data-label="P&L" className="text-right" style={{ fontFamily: 'var(--font-mono)', fontWeight: 600, color: pnlColor(pnl) }}>
        {pnl !== 0 ? fmtEur(pnl) : '—'}
      </td>
      <td data-label="Bankroll" className="text-right" style={{ fontFamily: 'var(--font-mono)', color: 'var(--text-secondary)' }}>
        {fmtMoney(row.bankroll_after)}
      </td>
    </tr>
  )
}

function groupRowsByDay(rows) {
  const today = todayStr()
  const byDate = new Map()

  for (const row of rows ?? []) {
    const date = row.date || 'Unknown'
    if (!byDate.has(date)) byDate.set(date, [])
    byDate.get(date).push(row)
  }

  return [...byDate.entries()]
    .sort(([a], [b]) => (a > b ? -1 : 1))
    .map(([date, dayRows]) => {
      const betRows = dayRows.filter(row => row.decision !== 'SKIP')
      const hasPending = betRows.some(row => row.result === 'Pending')
      const dailyProfit = betRows.reduce((sum, row) => sum + (toFiniteNumber(row.profit_loss) || 0), 0)
      const totalStake = betRows.reduce((sum, row) => sum + (toFiniteNumber(row.stake_eur) || 0), 0)

      return {
        date,
        rows: betRows,
        betCount: betRows.length,
        totalStake,
        dailyProfit,
        hasPending,
        displayPending: date === today && hasPending,
      }
    })
}

function fmtDate(d) {
  if (!d) return '—'
  const dt = new Date(String(d) + 'T12:00:00')
  return dt.toLocaleDateString('en-US', { month: 'short', day: 'numeric' })
}
