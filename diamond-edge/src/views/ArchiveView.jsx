import { useState, useEffect } from 'react'
import { predictionsPath, todayStr, datesInMonth, formatDisplayDate } from '../utils/paths'
import { useResults } from '../hooks/useResults'
import { fmtMoney, toFiniteNumber } from '../utils/format'

const SEASON_START = '2026-04-01'

export default function ArchiveView({ onSelectDate }) {
  const today = todayStr()
  const [availableDates, setAvailableDates] = useState(new Set())
  const [checking, setChecking] = useState(true)
  const [pnlView, setPnlView] = useState('normal')

  const { results, updated } = useResults()
  const hasUpdatedResults = updated?.length > 0
  const activeResults = pnlView === 'updated' && hasUpdatedResults ? updated : results
  const dailyPnl = buildDailyPnl(activeResults)
  const monthTotal = activeResults
    ? Array.from(dailyPnl.values()).reduce((s, v) => s + v, 0)
    : null
  const viewLabel = pnlView === 'updated' ? 'Updated odds' : 'Morning odds'

  const months = buildMonths(SEASON_START, today)
  const settledDays = Array.from(dailyPnl.entries())
  const availableCount = availableDates.size
  const winningDays = settledDays.filter(([, pnl]) => pnl > 0).length
  const losingDays = settledDays.filter(([, pnl]) => pnl < 0).length
  const lastAvailableDate = Array.from(availableDates).sort().at(-1)

  useEffect(() => {
    const allDates = months.flatMap(m => m.dates).filter(d => d <= today)
    let cancelled = false

    async function checkDates() {
      const found = new Set()
      for (let i = 0; i < allDates.length; i += 5) {
        const batch = allDates.slice(i, i + 5)
        await Promise.all(
          batch.map(async d => {
            try {
              const r = await fetch(predictionsPath(d), { method: 'HEAD' })
              if (r.ok) found.add(d)
            } catch {
              // no file
            }
          })
        )
        if (cancelled) return
        setAvailableDates(new Set(found))
      }
      setChecking(false)
    }

    checkDates()
    return () => { cancelled = true }
  }, [])

  return (
    <div>
      <div className="de-hero de-archive-hero">
        <div className="de-hero__date-label">Archive</div>
        <div className="de-hero__date">Browse Past Dates</div>
        <div className="de-archive-hero__meta">
          <span>Tap any date to view that day's picks</span>
          {monthTotal != null && (
            <span className={`de-archive__month-total${monthTotal >= 0 ? ' de-archive__month-total--win' : ' de-archive__month-total--loss'}`}>
              {monthTotal >= 0 ? '+' : '-'}{fmtMoney(Math.abs(monthTotal))}
            </span>
          )}
        </div>
      </div>

      <div className="de-archive">
        <aside className="de-archive__side">
          <div className="de-archive__panel">
            <div className="de-archive__panel-kicker">Season File Room</div>
            <div className="de-archive__panel-title">2026 archive</div>
            <div className="de-archive__panel-copy">
              Past prediction files, {viewLabel.toLowerCase()} settled daily P&L, and direct access back into the picks board.
            </div>

            <div className="de-archive__mode-toggle de-segmented">
              <button
                className={`de-segmented__btn${pnlView === 'normal' ? ' active' : ''}`}
                onClick={() => setPnlView('normal')}
              >
                Morning Odds
              </button>
              {hasUpdatedResults && (
                <button
                  className={`de-segmented__btn${pnlView === 'updated' ? ' active' : ''}`}
                  onClick={() => setPnlView('updated')}
                >
                  Updated Odds
                </button>
              )}
            </div>

            <div className="de-archive__summary-grid">
              <ArchiveStat label="Files" value={checking ? '...' : availableCount} />
              <ArchiveStat label="Won Days" value={winningDays} tone="win" />
              <ArchiveStat label="Lost Days" value={losingDays} tone="loss" />
              <ArchiveStat
                label="Last File"
                value={lastAvailableDate ? formatDisplayDate(lastAvailableDate).replace(',', '') : '—'}
              />
            </div>
          </div>

          <div className="de-archive__legend">
            <div className="de-archive__legend-heading">{viewLabel} P&L</div>
            <div className="de-archive__legend-row">
              <span className="de-archive__legend-dot win" />
              Profitable settled day
            </div>
            <div className="de-archive__legend-row">
              <span className="de-archive__legend-dot loss" />
              Losing settled day
            </div>
            <div className="de-archive__legend-row">
              <span className="de-archive__legend-dot neutral" />
              Predictions available
            </div>
            <div className="de-archive__legend-row">
              <span className="de-archive__legend-dot today" />
              Today
            </div>
          </div>
        </aside>

        <div className="de-archive__calendar-stack">
          <div className="de-archive__mobile-toggle de-segmented">
            <button
              className={`de-segmented__btn${pnlView === 'normal' ? ' active' : ''}`}
              onClick={() => setPnlView('normal')}
            >
              Morning Odds
            </button>
            {hasUpdatedResults && (
              <button
                className={`de-segmented__btn${pnlView === 'updated' ? ' active' : ''}`}
                onClick={() => setPnlView('updated')}
              >
                Updated Odds
              </button>
            )}
          </div>

          {months.map(month => (
            <div key={month.key} className="de-archive__month">
              <div className="de-archive__month-head">
                <div className="de-archive__month-rule" />
                <span className="de-archive__month-label">{month.label}</span>
                <div className="de-archive__month-rule de-archive__month-rule--right" />
              </div>

              <div className="de-archive__dow-row">
                {['S', 'M', 'T', 'W', 'T', 'F', 'S'].map((d, i) => (
                  <div key={i} className="de-archive__dow">{d}</div>
                ))}
              </div>

              <div className="de-archive__grid">
                {Array.from({ length: month.startDow }, (_, i) => (
                  <div key={`gap-${i}`} className="de-archive__day de-archive__day--gap" />
                ))}

                {month.dates.map(date => {
                  const isFuture = date > today
                  const isToday = date === today
                  const hasData = availableDates.has(date)
                  const pnl = dailyPnl.get(date)
                  const hasPnl = pnl != null
                  const isWin = hasPnl && pnl > 0
                  const isLoss = hasPnl && pnl < 0
                  const dayNum = getDayNum(date)
                  const pnlStr = hasPnl
                    ? (pnl >= 0 ? '+' : '-') + fmtMoney(Math.abs(pnl))
                    : null
                  const clickable = hasData || isToday

                  if (isFuture) {
                    return (
                      <div key={date} className="de-archive__day de-archive__day--future">
                        <span className="de-archive__day-num">{dayNum}</span>
                      </div>
                    )
                  }

                  const mod = isToday ? 'today'
                    : isWin ? 'win'
                    : isLoss ? 'loss'
                    : hasData ? 'neutral'
                    : 'empty'

                  return (
                    <button
                      key={date}
                      className={`de-archive__day de-archive__day--${mod}`}
                      onClick={() => clickable && onSelectDate(date)}
                      disabled={!clickable}
                      title={hasData ? `${date}${pnlStr ? ` · ${pnlStr}` : ''}` : undefined}
                    >
                      <span className="de-archive__day-num">{dayNum}</span>
                      {pnlStr ? (
                        <span className="de-archive__day-pnl">{pnlStr}</span>
                      ) : hasData && !isToday ? (
                        <span className="de-archive__day-dot" />
                      ) : null}
                    </button>
                  )
                })}
              </div>
            </div>
          ))}
        </div>
      </div>
    </div>
  )
}

function ArchiveStat({ label, value, tone }) {
  return (
    <div className={`de-archive__summary-stat${tone ? ` ${tone}` : ''}`}>
      <span>{label}</span>
      <strong>{value}</strong>
    </div>
  )
}

function buildDailyPnl(results) {
  const map = new Map()
  if (!results) return map
  for (const row of results) {
    if (!row.date) continue
    const pnl = toFiniteNumber(row.profit_loss)
    if (pnl == null) continue
    if (['Win', 'Loss', 'Push'].includes(row.result)) {
      map.set(row.date, (map.get(row.date) ?? 0) + pnl)
    }
  }
  return map
}

function buildMonths(startDate, endDate) {
  const months = []
  let cur = new Date(startDate + 'T12:00:00')
  const end = new Date(endDate + 'T12:00:00')

  while (cur <= end) {
    const year = cur.getFullYear()
    const month = cur.getMonth()
    const key = `${year}-${month}`
    const label = cur.toLocaleDateString('en-US', { month: 'long', year: 'numeric' })
    const dates = datesInMonth(year, month).filter(d => d >= startDate)
    const startDow = new Date(`${year}-${String(month + 1).padStart(2, '0')}-01T12:00:00`).getDay()

    if (dates.length > 0) months.push({ key, label, dates, startDow })

    cur.setMonth(cur.getMonth() + 1)
    cur.setDate(1)
  }

  return months.reverse()
}

function getDayNum(dateStr) {
  return parseInt(dateStr.split('-')[2], 10)
}
