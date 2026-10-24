import React, { useEffect, useState, useCallback } from 'react'
import { api } from './api.js'
import Overview from './views/Overview.jsx'
import Jobs from './views/Jobs.jsx'
import Activity from './views/Activity.jsx'
import Settings from './views/Settings.jsx'

const NAV = [
  ['overview', 'Overview'],
  ['jobs', 'Jobs'],
  ['activity', 'Activity & Logs'],
  ['settings', 'Settings'],
]

export default function App() {
  const [view, setView] = useState('overview')
  const [system, setSystem] = useState(null)
  const [err, setErr] = useState('')
  const [busy, setBusy] = useState(false)

  const loadSystem = useCallback(async () => {
    try { setSystem(await api.system()); setErr('') }
    catch (e) { setErr(String(e.message || e)) }
  }, [])

  useEffect(() => { loadSystem() }, [loadSystem])

  const runCycle = async () => {
    setBusy(true)
    try { await api.runCycle(); await loadSystem() }
    catch (e) { setErr(String(e.message || e)) }
    finally { setBusy(false) }
  }

  return (
    <div className="app">
      <aside className="sidebar">
        <div className="logo">Job<span>Pilot</span></div>
        <nav className="nav">
          {NAV.map(([k, label]) => (
            <button key={k} className={view === k ? 'active' : ''} onClick={() => setView(k)}>
              {label}
            </button>
          ))}
        </nav>
        <div style={{ marginTop: 24 }}>
          <button className="btn" style={{ width: '100%' }} disabled={busy} onClick={runCycle}>
            {busy ? 'Running…' : 'Run discovery cycle'}
          </button>
        </div>
      </aside>

      <main className="main">
        {err && <div className="banner auto">⚠ {err} {err.includes('401') && '— set your API key in Settings.'}</div>}
        {system && (
          <div className={`banner ${system.approval_mode === 'SAFE' ? 'safe' : 'auto'}`}>
            {system.approval_mode === 'SAFE'
              ? '🛡 SAFE mode — nothing is submitted without your explicit approval.'
              : `⚡ Auto-apply ON — Greenhouse & Lever jobs, up to ${system.daily_autosubmit_limit}/day; anything that needs you is emailed.`}
            {system.paused && ' · ⏸ Automation paused.'}
            {!system.sheets_enabled && ' · Google Sheets tracker off (optional).'}
            {system.sheet_url && <> · <a href={system.sheet_url} target="_blank" rel="noreferrer">📊 Open Google Sheet</a></>}
          </div>
        )}

        {view === 'overview' && <Overview />}
        {view === 'jobs' && <Jobs />}
        {view === 'activity' && <Activity />}
        {view === 'settings' && <Settings system={system} reload={loadSystem} />}
      </main>
    </div>
  )
}
