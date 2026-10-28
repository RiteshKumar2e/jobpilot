import React, { useEffect, useState, useCallback } from 'react'
import { api } from './api.js'
import Overview from './views/Overview.jsx'
import Jobs from './views/Jobs.jsx'
import Activity from './views/Activity.jsx'
import Settings from './views/Settings.jsx'
import Sources from './views/Sources.jsx'
import Companies from './views/Companies.jsx'
import Monitoring from './views/Monitoring.jsx'

const NAV = [
  ['overview', 'Overview'],
  ['jobs', 'Jobs'],
  ['sources', 'Sources'],
  ['companies', 'Companies'],
  ['monitoring', 'Monitoring'],
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
              : `⚡ Auto-apply ON — ${(system.auto_apply_sources || []).join(' & ') || 'allowlisted'} jobs, up to ${system.daily_autosubmit_limit}/day; other sources are prepared for you to apply, and anything that needs you is emailed.`}
            {system.paused && ' · ⏸ Automation paused.'}
            {!system.sheets_enabled && ' · Google Sheets tracker off (optional).'}
            {system.sheet_url && <> · <a href={system.sheet_url} target="_blank" rel="noreferrer">📊 Open Google Sheet</a></>}
          </div>
        )}

        {view === 'overview' && <Overview />}
        {view === 'jobs' && <Jobs />}
        {view === 'sources' && <Sources />}
        {view === 'companies' && <Companies />}
        {view === 'monitoring' && <Monitoring />}
        {view === 'activity' && <Activity />}
        {view === 'settings' && <Settings system={system} reload={loadSystem} />}
      </main>
    </div>
  )
}
