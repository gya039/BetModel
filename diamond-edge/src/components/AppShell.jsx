import BaseballCursor from './BaseballCursor'
import BottomNav from './BottomNav'
import { fmtMoney, toFiniteNumber } from '../utils/format'

const TOP_TABS = [
  { id: 'tracker', label: 'Tracker' },
  { id: 'picks', label: 'Picks' },
  { id: 'movement', label: 'Movement' },
  { id: 'more', label: 'More' },
]

export default function AppShell({ tab, onTab, bankroll, children }) {
  const bank = toFiniteNumber(bankroll)
  const bankPositive = bank != null && bank > 500
  const todayLabel = new Intl.DateTimeFormat('en-US', {
    weekday: 'short',
    month: 'short',
    day: 'numeric',
  }).format(new Date())

  return (
    <>
      <BaseballCursor />

      <div className="de-app de-app--purple-trial">
        {/* Header */}
        <header className="de-header">
          <div className="de-header__logo">
            <svg width="28" height="28" viewBox="0 0 28 28" fill="none">
              <polygon points="14,2 26,14 14,26 2,14" stroke="var(--brand-accent)" strokeWidth="1.5" fill="var(--brand-mark-bg)"/>
              <polygon points="14,7 21,14 14,21 7,14" stroke="var(--brand-accent-2)" strokeWidth="1" fill="var(--brand-mark-core)"/>
              <circle cx="14" cy="14" r="2.5" fill="var(--brand-accent-soft)"/>
            </svg>
            <div className="de-header__wordmark">
              <span>Diamond </span>
              <span>Edge</span>
            </div>
          </div>

          <nav className="de-top-nav" aria-label="Primary navigation">
            {TOP_TABS.map(item => (
              <button
                key={item.id}
                className={`de-top-nav__btn${tab === item.id ? ' active' : ''}`}
                onClick={() => onTab(item.id)}
                aria-current={tab === item.id ? 'page' : undefined}
              >
                {item.label}
              </button>
            ))}
          </nav>

          <div className="de-header__right">
            <div className="de-date-pill">{todayLabel}</div>
            <div className="de-bankroll-pill">
              <span className="de-bankroll-pill__label">Bank</span>
              <span className={`de-bankroll-pill__value${bankPositive ? ' de-bankroll-pill__value--positive' : ''}`}>
                {bank != null ? fmtMoney(bank) : '...'}
              </span>
            </div>
          </div>
        </header>

        {/* Page content */}
        <main className="de-page" id="main-content" tabIndex={-1}>
          {children}
        </main>

        {/* Bottom navigation */}
        <BottomNav active={tab} onTab={onTab} />
      </div>
    </>
  )
}
