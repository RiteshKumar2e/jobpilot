import React, { useEffect, useState } from 'react'
import { api } from '../api.js'

const TILES = [
  ['discovered', 'Discovered'],
  ['verified_eligible', 'Verified'],
  ['shortlisted', 'Shortlisted'],
  ['prepared', 'Prepared'],
  ['submitted_confirmed', 'Submitted'],
  ['submission_failed_or_unverified', 'Failed/Unverified'],
  ['interviews', 'Interviews'],
  ['rejected', 'Rejected'],
  ['pending_followups', 'Follow-ups'],
]

export default function Overview() {
  const [stats, setStats] = useState(null)
  const [err, setErr] = useState('')

  useEffect(() => {
    api.stats().then(setStats).catch((e) => setErr(String(e.message || e)))
  }, [])

  if (err) return <div className="panel">Could not load stats: {err}</div>
  if (!stats) return <div className="panel muted">Loading…</div>

  return (
    <div>
      <div className="topbar"><h1>Overview</h1></div>
      <div className="cards">
        {TILES.map(([k, l]) => (
          <div className="card" key={k}>
            <div className="n">{stats[k] ?? 0}</div>
            <div className="l">{l}</div>
          </div>
        ))}
      </div>

      <div className="row" style={{ alignItems: 'flex-start' }}>
        <div className="panel" style={{ flex: 1 }}>
          <div className="l muted" style={{ marginBottom: 10 }}>By source</div>
          {Object.keys(stats.by_source || {}).length === 0 && <div className="muted">No data yet.</div>}
          {Object.entries(stats.by_source || {}).map(([s, n]) => (
            <div className="row" key={s} style={{ justifyContent: 'space-between' }}>
              <span>{s}</span><span className="chip">{n}</span>
            </div>
          ))}
        </div>
        <div className="panel" style={{ flex: 1 }}>
          <div className="l muted" style={{ marginBottom: 10 }}>By application status</div>
          {Object.entries(stats.by_status || {}).map(([s, n]) => (
            <div className="row" key={s} style={{ justifyContent: 'space-between' }}>
              <span className={`chip ${s}`}>{s}</span><span>{n}</span>
            </div>
          ))}
        </div>
      </div>
    </div>
  )
}
