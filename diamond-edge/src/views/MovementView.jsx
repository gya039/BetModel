import { useState, useEffect } from 'react'
import { useMovement } from '../hooks/useMovement'
import MovementCard from '../components/MovementCard'
import DateNav from '../components/DateNav'
import { PickCardSkeleton } from '../components/Skeleton'
import { todayStr, prevDate, nextDate, isFuture, formatDisplayDate, findLatestDate } from '../utils/paths'

export default function MovementView() {
  const [date, setDate] = useState(todayStr)

  useEffect(() => {
    let cancelled = false
    findLatestDate().then(d => {
      if (!cancelled) setDate(d)
    })
    return () => { cancelled = true }
  }, [])

  const { movements, hasUpdated, loading, error, morning } = useMovement(date)

  const changed = movements.filter(m => m.decisionChanged)
  const unchanged = movements.filter(m => !m.decisionChanged)

  function goNext() {
    const n = nextDate(date)
    if (!isFuture(n)) setDate(n)
  }

  return (
    <div className="de-page-skin de-page-skin--movement">
      <DateNav
        date={date}
        onPrev={() => setDate(prevDate(date))}
        onNext={goNext}
        onToday={() => setDate(todayStr())}
      />

      <div className="de-hero de-hero--movement">
        <div className="de-hero__copy">
          <div className="de-hero__date-label">Line Movement</div>
          <div className="de-hero__date">Morning vs Afternoon</div>
          <div className="de-hero__lede">
            Watch where conviction strengthened, faded, or stayed disciplined.
          </div>
        </div>

        {!loading && hasUpdated && movements.length > 0 && (
          <div className="de-hero__stats">
            <div className="de-hero__stat">
              <div className="de-hero__stat-label">Games</div>
              <div className="de-hero__stat-value blue">{movements.length}</div>
            </div>
            <div className="de-hero__stat">
              <div className="de-hero__stat-label">Changed</div>
              <div className="de-hero__stat-value accent">{changed.length}</div>
            </div>
            <div className="de-hero__stat">
              <div className="de-hero__stat-label">Stable</div>
              <div className="de-hero__stat-value">{unchanged.length}</div>
            </div>
          </div>
        )}
      </div>

      <div className="de-section" style={{ paddingBottom: 0 }}>
        <div
          style={{
            background: 'var(--panel-alt)',
            border: '1px solid var(--line)',
            borderRadius: 'var(--r-lg)',
            padding: '10px 14px',
            fontSize: 12,
            color: 'var(--muted)',
            lineHeight: 1.5,
            display: 'flex',
            gap: 8,
          }}
        >
          <span style={{ flexShrink: 0 }}>ℹ️</span>
          <span>
            Live games are excluded from this comparison — odds changes after game start
            are not market signals and are never used for edge recalculation.
          </span>
        </div>
      </div>

      <div className="de-section">
        {loading && [1, 2, 3].map(i => <PickCardSkeleton key={i} />)}

        {!loading && error && (
          <div className="de-empty">
            <div className="de-empty__icon">📡</div>
            <div className="de-empty__title">No movement data</div>
            <div className="de-empty__sub">
              No predictions file found for {formatDisplayDate(date)}.
            </div>
          </div>
        )}

        {!loading && !error && !hasUpdated && morning && (
          <div className="de-empty">
            <div className="de-empty__icon">⏳</div>
            <div className="de-empty__title">No afternoon update yet</div>
            <div className="de-empty__sub">
              The (Updated).json file has not been generated for {formatDisplayDate(date)} yet.
              Run `check_movement.py` after the morning lock.
            </div>
          </div>
        )}

        {!loading && !error && !morning && (
          <div className="de-empty">
            <div className="de-empty__icon">⚾</div>
            <div className="de-empty__title">No file for this date</div>
            <div className="de-empty__sub">
              Navigate to a date that has prediction data.
            </div>
          </div>
        )}

        {!loading && movements.length > 0 && (
          <>
            {changed.length > 0 && (
              <>
                <div className="de-section-header de-section-header--featured">
                  <div className="de-section-header__dot" style={{ background: 'var(--accent)' }} />
                  <span className="de-section-header__title">Decision Changed</span>
                  <span className="de-section-header__count">{changed.length}</span>
                </div>
                <div className="de-movement-lane de-movement-lane--featured">
                  {changed.map((m, i) => (
                    <MovementCard key={m.gamePk} mov={m} index={i} />
                  ))}
                </div>

                <div className="de-seam">
                  <div className="de-seam__line" />
                  <div className="de-seam__icon">· · ·</div>
                  <div className="de-seam__line" />
                </div>
              </>
            )}

            <div className="de-section-header">
              <div className="de-section-header__dot" style={{ background: 'var(--muted)' }} />
              <span className="de-section-header__title">No Change</span>
              <span className="de-section-header__count">{unchanged.length}</span>
            </div>
            <div className="de-movement-lane">
              {unchanged.map((m, i) => (
                <MovementCard key={m.gamePk} mov={m} index={i + changed.length} />
              ))}
            </div>
          </>
        )}
      </div>
    </div>
  )
}
