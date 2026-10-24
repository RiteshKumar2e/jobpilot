import React, { useEffect, useState } from 'react'
import { api } from '../api.js'

export default function Activity() {
  const [events, setEvents] = useState([])
  const [history, setHistory] = useState([])
  const [tab, setTab] = useState('events')

  const load = async () => {
    try { setEvents(await api.events(100)) } catch { /* */ }
    try { setHistory(await api.history()) } catch { /* */ }
  }
  useEffect(() => {
    load()
    const t = setInterval(load, 5000) // live refresh
    return () => clearInterval(t)
  }, [])

  return (
    <div>
      <div className="topbar"><h1>Activity & Logs</h1>
        <div className="row">
          <button className={`btn ${tab === 'events' ? '' : 'ghost'}`} onClick={() => setTab('events')}>Agent events</button>
          <button className={`btn ${tab === 'history' ? '' : 'ghost'}`} onClick={() => setTab('history')}>Submission history</button>
        </div>
      </div>

      {tab === 'events' && (
        <div className="panel">
          {events.length === 0 && <div className="muted">No events yet.</div>}
          {events.map((e, i) => (
            <div className="log-line" key={i}>
              <span className={`lvl ${e.level}`}>[{e.level.toUpperCase()}]</span>{' '}
              <span className="muted">{e.ts?.slice(0, 19)}</span>{' '}
              <b>{e.agent}</b> — {e.message}
              {e.job_id && <span className="muted"> ({e.job_id})</span>}
            </div>
          ))}
        </div>
      )}

      {tab === 'history' && (
        <div className="panel" style={{ padding: 0 }}>
          <table>
            <thead><tr><th>Job</th><th>Attempt</th><th>Result</th><th>Error</th><th>Started</th></tr></thead>
            <tbody>
              {history.map((h, i) => (
                <tr key={i}>
                  <td className="muted">{h.job_id}</td><td>{h.attempt}</td>
                  <td><span className={`chip ${h.result}`}>{h.result}</span></td>
                  <td className="muted">{h.error?.slice(0, 60)}</td>
                  <td className="muted">{h.started_at?.slice(0, 19)}</td>
                </tr>
              ))}
              {history.length === 0 && <tr><td colSpan={5} className="muted" style={{ padding: 20 }}>No submission attempts yet.</td></tr>}
            </tbody>
          </table>
        </div>
      )}
    </div>
  )
}
