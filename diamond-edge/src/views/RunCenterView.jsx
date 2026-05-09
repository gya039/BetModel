import { useState, useEffect, useRef } from 'react'
import { motion, AnimatePresence } from 'framer-motion'
import { clearResultsCache } from '../hooks/useResults'

const TOKEN = 'de-local-run'

const ACTIONS = [
  {
    id:          'settle',
    label:       'Settle Results',
    icon:        '✓',
    description: "Mark yesterday's bets Win / Loss / Push and update the bankroll.",
    warning:     null,
    color:       'var(--bet)',
    dimColor:    'var(--bet-dim)',
  },
  {
    id:          'generate',
    label:       'Generate Today\'s Picks',
    icon:        '⬡',
    description: 'Settle yesterday, run the model, fetch morning odds, write prediction JSON.',
    warning:     'Spends Odds API requests',
    color:       'var(--accent)',
    dimColor:    'var(--accent-dim)',
  },
  {
    id:          'movement',
    label:       'Check Line Movement',
    icon:        '↕',
    description: 'Fetch updated afternoon odds and write the (Updated) JSON for comparison.',
    warning:     'Spends Odds API requests',
    color:       'var(--gold)',
    dimColor:    'var(--gold-dim)',
  },
]

export default function RunCenterView() {
  const [status,   setStatus]   = useState(null)
  const [logs,     setLogs]     = useState([])
  const [running,  setRunning]  = useState(false)
  const [exitCode, setExitCode] = useState(null)
  const [confirm,  setConfirm]  = useState(null) // action id awaiting confirm
  const logRef = useRef(null)
  const esRef  = useRef(null)

  function refreshStatus() {
    fetch('/api/status')
      .then(r => r.json())
      .then(setStatus)
      .catch(() => setStatus(null))
  }

  useEffect(() => {
    refreshStatus()
    const iv = setInterval(refreshStatus, 10_000)
    return () => clearInterval(iv)
  }, [])

  useEffect(() => {
    if (logRef.current) {
      logRef.current.scrollTop = logRef.current.scrollHeight
    }
  }, [logs])

  async function startRun(actionId) {
    setConfirm(null)
    setLogs([])
    setExitCode(null)
    setRunning(true)

    let runId
    try {
      const res = await fetch(`/api/run/${actionId}`, {
        method: 'POST',
        headers: { 'x-run-token': TOKEN },
      })
      const data = await res.json()
      if (!res.ok) {
        setLogs([`✗ ${data.error || 'Server error'}`])
        setRunning(false)
        return
      }
      runId = data.runId
    } catch (err) {
      setLogs([`✗ Could not reach server: ${err.message}`])
      setRunning(false)
      return
    }

    // SSE stream
    if (esRef.current) esRef.current.close()
    const es = new EventSource(`/api/run/stream/${runId}`)
    esRef.current = es

    es.onmessage = e => {
      const data = JSON.parse(e.data)
      if (data.line !== undefined) {
        setLogs(prev => [...prev, data.line])
      }
      if (data.done) {
        setExitCode(data.exitCode)
        setRunning(false)
        es.close()
        refreshStatus()
        if (data.exitCode === 0) clearResultsCache()
      }
    }
    es.onerror = () => {
      setLogs(prev => [...prev, '✗ Stream disconnected'])
      setRunning(false)
      es.close()
    }
  }

  function handleClick(action) {
    if (action.warning) {
      setConfirm(action.id)
    } else {
      startRun(action.id)
    }
  }

  const busy = running || status?.busy

  return (
    <div>
      <div className="de-hero">
        <div className="de-hero__date-label">Pipeline Control</div>
        <div className="de-hero__date">Run Center</div>
        {status && (
          <div style={{ fontSize: 12, color: 'var(--muted)', marginTop: -8 }}>
            {status.today} · {busy ? 'Running…' : 'Ready'}
          </div>
        )}
      </div>

      <div className="de-section">
        {/* Action cards */}
        {ACTIONS.map(action => {
          const doneKey = action.id === 'settle'   ? null
                        : action.id === 'generate' ? 'generated'
                        : 'movement'
          const isDone  = doneKey ? status?.[doneKey] : false

          return (
            <motion.div
              key={action.id}
              layout
              style={{
                background: 'var(--panel)',
                border: `1px solid ${isDone ? 'var(--line)' : action.color}33`,
                borderRadius: 'var(--r-xl)',
                padding: '16px',
                marginBottom: '12px',
                boxShadow: isDone ? 'none' : `0 0 20px ${action.color}0d`,
              }}
            >
              <div style={{ display: 'flex', alignItems: 'flex-start', gap: 12 }}>
                <div style={{
                  width: 36, height: 36, borderRadius: 'var(--r-md)',
                  background: isDone ? 'var(--line)' : action.dimColor,
                  display: 'flex', alignItems: 'center', justifyContent: 'center',
                  fontSize: 16, flexShrink: 0,
                  color: isDone ? 'var(--muted)' : action.color,
                }}>
                  {isDone ? '✓' : action.icon}
                </div>

                <div style={{ flex: 1, minWidth: 0 }}>
                  <div style={{
                    fontFamily: 'var(--font-display)',
                    fontWeight: 600, fontSize: 15,
                    color: isDone ? 'var(--muted)' : 'var(--text)',
                    marginBottom: 4,
                  }}>
                    {action.label}
                    {isDone && (
                      <span style={{
                        marginLeft: 8, fontSize: 10, fontWeight: 700,
                        color: 'var(--bet)', background: 'var(--bet-dim)',
                        padding: '2px 7px', borderRadius: 999,
                        letterSpacing: '0.06em',
                      }}>DONE</span>
                    )}
                  </div>
                  <div style={{ fontSize: 12, color: 'var(--muted)', lineHeight: 1.5 }}>
                    {action.description}
                  </div>
                  {action.warning && (
                    <div style={{
                      marginTop: 6, fontSize: 11, fontWeight: 600,
                      color: 'var(--skip)', display: 'flex', alignItems: 'center', gap: 4,
                    }}>
                      ⚠ {action.warning}
                    </div>
                  )}
                </div>

                <button
                  onClick={() => handleClick(action)}
                  disabled={busy || isDone}
                  style={{
                    flexShrink: 0,
                    padding: '8px 16px',
                    borderRadius: 'var(--r-md)',
                    background: busy || isDone ? 'var(--panel-alt)' : action.dimColor,
                    color: busy || isDone ? 'var(--muted-dim)' : action.color,
                    border: `1px solid ${busy || isDone ? 'var(--line)' : action.color + '44'}`,
                    fontSize: 12, fontWeight: 700,
                    cursor: busy || isDone ? 'not-allowed' : 'pointer',
                    transition: 'all 0.15s',
                    whiteSpace: 'nowrap',
                  }}
                >
                  {busy && running ? '…' : isDone ? 'Done' : 'Run'}
                </button>
              </div>

              {/* Confirmation prompt for Odds API actions */}
              <AnimatePresence>
                {confirm === action.id && (
                  <motion.div
                    initial={{ height: 0, opacity: 0 }}
                    animate={{ height: 'auto', opacity: 1 }}
                    exit={{ height: 0, opacity: 0 }}
                    transition={{ duration: 0.18 }}
                    style={{ overflow: 'hidden' }}
                  >
                    <div style={{
                      marginTop: 12,
                      padding: '10px 12px',
                      background: 'rgba(255,138,101,0.08)',
                      border: '1px solid rgba(255,138,101,0.2)',
                      borderRadius: 'var(--r-md)',
                      display: 'flex',
                      alignItems: 'center',
                      justifyContent: 'space-between',
                      gap: 12,
                    }}>
                      <span style={{ fontSize: 12, color: 'var(--skip)' }}>
                        This will spend Odds API requests. Continue?
                      </span>
                      <div style={{ display: 'flex', gap: 8, flexShrink: 0 }}>
                        <button
                          onClick={() => setConfirm(null)}
                          style={{
                            padding: '5px 12px', borderRadius: 'var(--r-md)',
                            background: 'var(--panel-alt)', border: '1px solid var(--line)',
                            color: 'var(--muted)', fontSize: 12, fontWeight: 600, cursor: 'pointer',
                          }}
                        >Cancel</button>
                        <button
                          onClick={() => startRun(action.id)}
                          style={{
                            padding: '5px 12px', borderRadius: 'var(--r-md)',
                            background: 'var(--skip-dim)', border: '1px solid var(--skip-border)',
                            color: 'var(--skip)', fontSize: 12, fontWeight: 700, cursor: 'pointer',
                          }}
                        >Confirm</button>
                      </div>
                    </div>
                  </motion.div>
                )}
              </AnimatePresence>
            </motion.div>
          )
        })}

        {/* Live log panel */}
        <AnimatePresence>
          {(logs.length > 0 || running) && (
            <motion.div
              initial={{ opacity: 0, y: 12 }}
              animate={{ opacity: 1, y: 0 }}
              exit={{ opacity: 0 }}
              transition={{ duration: 0.2 }}
            >
              <div style={{
                display: 'flex', alignItems: 'center', justifyContent: 'space-between',
                marginBottom: 8,
              }}>
                <div className="de-section-header" style={{ marginBottom: 0 }}>
                  <div
                    className="de-section-header__dot"
                    style={{
                      background: running ? 'var(--live)' : exitCode === 0 ? 'var(--bet)' : 'var(--skip)',
                      animation: running ? 'dotPulse 1.2s ease-in-out infinite' : 'none',
                    }}
                  />
                  <span className="de-section-header__title">
                    {running ? 'Running…' : exitCode === 0 ? 'Completed' : 'Failed'}
                  </span>
                </div>
                <button
                  onClick={() => { setLogs([]); setExitCode(null) }}
                  style={{ fontSize: 11, color: 'var(--muted-dim)', cursor: 'pointer' }}
                >
                  Clear
                </button>
              </div>

              <div
                ref={logRef}
                style={{
                  background: 'var(--bg-deeper)',
                  border: '1px solid var(--line)',
                  borderRadius: 'var(--r-lg)',
                  padding: '12px 14px',
                  fontFamily: 'var(--font-mono)',
                  fontSize: 11,
                  lineHeight: 1.7,
                  color: 'var(--text-secondary)',
                  maxHeight: 280,
                  overflowY: 'auto',
                  whiteSpace: 'pre-wrap',
                  wordBreak: 'break-all',
                }}
              >
                {logs.map((line, i) => (
                  <div
                    key={i}
                    style={{
                      color: line.startsWith('✗') ? 'var(--skip)'
                           : line.startsWith('▶') ? 'var(--accent)'
                           : 'var(--text-secondary)',
                    }}
                  >
                    {line}
                  </div>
                ))}
                {running && (
                  <div style={{ color: 'var(--muted-dim)' }}>▌</div>
                )}
              </div>

              {exitCode === 0 && (
                <div style={{
                  marginTop: 8, fontSize: 12, color: 'var(--bet)', fontWeight: 600,
                  display: 'flex', alignItems: 'center', gap: 6,
                }}>
                  ✓ Pipeline completed successfully — refresh Picks or Tracker to see updates
                </div>
              )}
            </motion.div>
          )}
        </AnimatePresence>
      </div>
    </div>
  )
}
