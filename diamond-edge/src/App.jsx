import { useState, useCallback, useEffect } from 'react'
import AppShell from './components/AppShell'
import TodayView    from './views/TodayView'
import ResultsView  from './views/ResultsView'
import MovementView from './views/MovementView'
import ArchiveView  from './views/ArchiveView'
import AboutView    from './views/AboutView'
import RunCenterView from './views/RunCenterView'
import PageErrorBoundary from './components/PageErrorBoundary'
import { useResults } from './hooks/useResults'

// Keeps a tab mounted once visited, hidden via display:none when inactive.
// Eliminates blank screens from AnimatePresence mode="wait" race conditions
// during rapid tab switching.
function Panel({ active, children }) {
  const [mounted, setMounted] = useState(active)
  useEffect(() => { if (active) setMounted(true) }, [active])
  if (!mounted) return null
  return (
    <div style={{ display: active ? 'block' : 'none', minHeight: '100%' }}>
      {children}
    </div>
  )
}

export default function App() {
  const [tab, setTab]               = useState('tracker')
  const [moreSubTab, setMoreSubTab] = useState('archive')
  const [bankroll, setBankroll]     = useState(null)
  const [archiveDate, setArchiveDate] = useState(null)
  const { stats } = useResults()

  useEffect(() => {
    if (stats?.currentBankroll != null) {
      setBankroll(prev => prev === stats.currentBankroll ? prev : stats.currentBankroll)
    }
  }, [stats?.currentBankroll])

  function handleTab(newTab) {
    if (newTab !== 'picks') setArchiveDate(null)
    setTab(newTab)
  }

  function handleArchiveDate(date) {
    setArchiveDate(date)
    handleTab('picks')
  }

  const onBankrollLoaded = useCallback((b) => {
    setBankroll(prev => prev === b ? prev : b)
  }, [])

  return (
    <AppShell tab={tab} onTab={handleTab} bankroll={bankroll}>

      <Panel active={tab === 'tracker'}>
        <PageErrorBoundary resetKey="tracker">
          <ResultsView />
        </PageErrorBoundary>
      </Panel>

      <Panel active={tab === 'picks'}>
        <PageErrorBoundary resetKey="picks">
          <TodayView onBankrollLoaded={onBankrollLoaded} initialDate={archiveDate} />
        </PageErrorBoundary>
      </Panel>

      <Panel active={tab === 'movement'}>
        <PageErrorBoundary resetKey="movement">
          <MovementView />
        </PageErrorBoundary>
      </Panel>

      <Panel active={tab === 'more'}>
        <PageErrorBoundary resetKey={`more-${moreSubTab}`}>
          <div>
            <div className="de-subtabs">
              {[
                { id: 'archive', label: 'Archive' },
                { id: 'run',     label: 'Run'     },
                { id: 'about',   label: 'About'   },
              ].map(st => (
                <button
                  key={st.id}
                  onClick={() => setMoreSubTab(st.id)}
                  className={`de-subtabs__btn${moreSubTab === st.id ? ' active' : ''}`}
                >
                  {st.label}
                </button>
              ))}
            </div>
            {moreSubTab === 'archive' && <ArchiveView onSelectDate={handleArchiveDate} />}
            {moreSubTab === 'run'     && <RunCenterView />}
            {moreSubTab === 'about'   && <AboutView />}
          </div>
        </PageErrorBoundary>
      </Panel>

    </AppShell>
  )
}
