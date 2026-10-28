import React, { useEffect, useState, useCallback } from 'react'
import { api } from '../api.js'

const TILES = [
  ['jobs', 'Unique jobs'],
  ['listings_seen', 'Listings seen'],
  ['eligible', 'Eligible'],
  ['prepared', 'Ready for review'],
  ['submitted_confirmed', 'Submitted (confirmed)'],
  ['submitted_unverified', 'Submitted (unverified)'],
  ['awaiting_action', 'Needs you'],
]

export default function Monitoring() {
  const [m, setM] = useState(null)
  const [err, setErr] = useState('')

  const load = useCallback(async () => {
    try { setM(await api.monitoring()); setErr('') }
    catch (e) { setErr(String(e.message || e)) }
  }, [])
  useEffect(() => {
    load()
    const t = setInterval(load, 15000)   // live view while a cycle runs
    return () => clearInterval(t)
  }, [load])

  if (err && !m) return <div className="panel">Could not load monitoring: {err}</div>
  if (!m) return <div className="panel muted">Loading…</div>

  const maxListings = Math.max(1, ...m.per_source.map((s) => s.listings))
  const prov = m.last_discovery?.providers || {}

  return (
    <div>
      <div className="topbar"><h1>Monitoring</h1>
        <button className="btn ghost" onClick={load}>Refresh</button>
      </div>

      <div className="cards">
        {TILES.map(([k, l]) => (
          <div className="card" key={k}><div className="n">{m[k] ?? 0}</div><div className="l">{l}</div></div>
        ))}
        <div className="card">
          <div className="n">{Math.round((m.duplicate_rate || 0) * 100)}%</div>
          <div className="l">Duplicate rate</div>
          <div className="muted small">{m.jobs_with_multiple_sources} jobs on 2+ sources</div>
        </div>
      </div>

      <div className="grid2">
        <div className="panel">
          <div className="l muted" style={{ marginBottom: 10 }}>Listings per source (all time)</div>
          {m.per_source.map((s) => (
            <div key={s.source} className="bar-row">
              <span className="bar-label">{s.source}</span>
              <span className="bar"><span style={{ width: `${(s.listings / maxListings) * 100}%` }} /></span>
              <span className="bar-n">{s.listings}</span>
            </div>
          ))}
          {m.per_source.length === 0 && <div className="muted">No data yet.</div>}
        </div>

        <div className="panel">
          <div className="l muted" style={{ marginBottom: 10 }}>
            Last discovery {m.last_discovery?.at ? `· ${m.last_discovery.at.slice(0, 16).replace('T', ' ')} UTC` : ''}
          </div>
          <div className="table-wrap">
            <table>
              <thead><tr><th>Source</th><th>OK</th><th>Failed</th><th>Cooling</th><th>Jobs</th></tr></thead>
              <tbody>
                {Object.entries(prov).map(([p, s]) => (
                  <tr key={p}><td>{p}</td><td>{s.ok}</td><td>{s.errors || ''}</td>
                    <td>{s.cooling_down || ''}</td><td>{s.jobs}</td></tr>
                ))}
                {Object.keys(prov).length === 0 && <tr><td colSpan={5} className="muted">No cycle yet.</td></tr>}
              </tbody>
            </table>
          </div>
        </div>
      </div>

      <div className="grid2">
        <div className="panel">
          <div className="l muted" style={{ marginBottom: 10 }}>Jobs by state</div>
          {Object.entries(m.by_lifecycle).sort((a, b) => b[1] - a[1]).map(([s, n]) => (
            <div className="row" key={s} style={{ justifyContent: 'space-between' }}>
              <span className={`chip ${s}`}>{s}</span><span>{n}</span>
            </div>
          ))}
        </div>
        <div className="panel">
          <div className="l muted" style={{ marginBottom: 10 }}>Source errors</div>
          {m.provider_errors.length === 0 && <div className="muted">None — every source answered last time.</div>}
          {m.provider_errors.map((e) => (
            <div key={e.key} className="log-line">
              <span className="lvl error">{e.key}</span> ×{e.consecutive_failures}{' '}
              <span className="muted">{e.last_error}</span>
            </div>
          ))}
        </div>
      </div>

      <div className="panel">
        <div className="l muted" style={{ marginBottom: 10 }}>Recent discovery activity</div>
        {m.recent.map((e, i) => (
          <div key={i} className="log-line">
            <span className="muted">{e.ts.slice(5, 16)}</span> <span className="lvl info">{e.agent}</span> {e.message}
          </div>
        ))}
      </div>
    </div>
  )
}
