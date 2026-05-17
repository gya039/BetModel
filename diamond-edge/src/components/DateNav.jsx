import { formatDisplayDate, isFuture, todayStr } from '../utils/paths'

export default function DateNav({ date, onPrev, onNext, onToday }) {
  const isToday   = date === todayStr()
  const isFuture_ = isFuture(nextDate(date))

  function nextDate(d) {
    const dt = new Date(d + 'T12:00:00')
    dt.setDate(dt.getDate() + 1)
    return dt.toISOString().split('T')[0]
  }

  const display   = formatDisplayDate(date)
  const [weekday, rest] = display.split(', ')

  return (
    <div className="de-date-nav">
      <button className="de-date-nav__btn" onClick={onPrev} aria-label="Previous day">
        <svg width="16" height="16" viewBox="0 0 16 16" fill="none">
          <path d="M10 12L6 8l4-4" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round"/>
        </svg>
      </button>

      <div className="de-date-nav__center">
        <div className="de-date-nav__label">{display}</div>
        {isToday && <div className="de-date-nav__sub">Today's slate</div>}
      </div>

      {!isToday && (
        <button className="de-date-nav__today" onClick={onToday}>
          Today
        </button>
      )}

      <button
        className="de-date-nav__btn"
        onClick={onNext}
        disabled={isFuture_}
        aria-label="Next day"
      >
        <svg width="16" height="16" viewBox="0 0 16 16" fill="none">
          <path d="M6 4l4 4-4 4" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round"/>
        </svg>
      </button>
    </div>
  )
}
