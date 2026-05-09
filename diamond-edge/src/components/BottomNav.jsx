const TABS = [
  {
    id: 'tracker',
    label: 'Tracker',
    icon: (
      <svg width="22" height="22" viewBox="0 0 22 22" fill="none">
        <polyline points="3,16 8,10 12,13 19,5" stroke="currentColor" strokeWidth="1.6" strokeLinecap="round" strokeLinejoin="round"/>
        <rect x="3" y="17" width="16" height="1.5" rx="0.75" fill="currentColor" opacity="0.3"/>
      </svg>
    ),
  },
  {
    id: 'picks',
    label: 'Picks',
    icon: (
      <svg width="22" height="22" viewBox="0 0 22 22" fill="none">
        <polygon points="11,2 20,11 11,20 2,11" stroke="currentColor" strokeWidth="1.6" fill="none"/>
        <circle cx="11" cy="11" r="2.5" fill="currentColor"/>
      </svg>
    ),
  },
  {
    id: 'movement',
    label: 'Movement',
    icon: (
      <svg width="22" height="22" viewBox="0 0 22 22" fill="none">
        <path d="M11 4v14M6 9l5-5 5 5M6 13l5 5 5-5" stroke="currentColor" strokeWidth="1.6" strokeLinecap="round" strokeLinejoin="round"/>
      </svg>
    ),
  },
  {
    id: 'more',
    label: 'More',
    icon: (
      <svg width="22" height="22" viewBox="0 0 22 22" fill="none">
        <circle cx="11" cy="5"  r="1.5" fill="currentColor"/>
        <circle cx="11" cy="11" r="1.5" fill="currentColor"/>
        <circle cx="11" cy="17" r="1.5" fill="currentColor"/>
      </svg>
    ),
  },
]

export default function BottomNav({ active, onTab }) {
  return (
    <nav className="de-nav" role="navigation" aria-label="Main navigation">
      {TABS.map(tab => (
        <button
          key={tab.id}
          className={`de-nav__tab${active === tab.id ? ' active' : ''}`}
          onClick={() => onTab(tab.id)}
          aria-label={tab.label}
          aria-current={active === tab.id ? 'page' : undefined}
        >
          <span className="de-nav__icon">{tab.icon}</span>
          <span className="de-nav__label">{tab.label}</span>
        </button>
      ))}
    </nav>
  )
}
