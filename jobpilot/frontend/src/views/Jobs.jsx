import React, { useEffect, useState, useCallback } from 'react'
import { api } from '../api.js'
import JobDrawer from './JobDrawer.jsx'

function scoreClass(s) {
  if (s == null) return 'lo'
  if (s >= 70) return 'hi'
  if (s >= 50) return 'mid'
  return 'lo'
}

export default function Jobs() {
  const [jobs, setJobs] = useState([])
  const [filters, setFilters] = useState({ q: '', eligibility: '', status: '', sort: 'score', min_score: '' })
  const [selected, setSelected] = useState(null)
  const [err, setErr] = useState('')

  const load = useCallback(async () => {
    try { setJobs(await api.jobs(filters)); setErr('') }
    catch (e) { setErr(String(e.message || e)) }
  }, [filters])

  useEffect(() => { load() }, [load])

  const upd = (k) => (e) => setFilters((f) => ({ ...f, [k]: e.target.value }))

  return (
    <div>
      <div className="topbar"><h1>Jobs</h1>
        <button className="btn ghost" onClick={load}>Refresh</button>
      </div>

      <div className="filters">
        <input placeholder="Search title / company" value={filters.q} onChange={upd('q')} />
        <select value={filters.eligibility} onChange={upd('eligibility')}>
          <option value="">All eligibility</option>
          <option>VERIFIED</option><option>NEEDS_REVIEW</option>
          <option>REJECTED</option><option>EXPIRED</option>
        </select>
        <select value={filters.status} onChange={upd('status')}>
          <option value="">All app status</option>
          <option>READY_FOR_REVIEW</option><option>DRAFTED</option>
          <option>SUBMITTED_CONFIRMED</option><option>ACTION_REQUIRED</option>
          <option>FAILED</option><option>REJECTED_BY_USER</option>
        </select>
        <input placeholder="min score" style={{ width: 90 }} value={filters.min_score} onChange={upd('min_score')} />
        <select value={filters.sort} onChange={upd('sort')}>
          <option value="score">Sort: score</option>
          <option value="date">Sort: date</option>
          <option value="company">Sort: company</option>
        </select>
      </div>

      {err && <div className="panel">Error: {err}</div>}

      <div className="panel" style={{ padding: 0 }}>
        <table>
          <thead><tr>
            <th>Score</th><th>Title</th><th>Company</th><th>Location</th>
            <th>Eligibility</th><th>App status</th><th>Resume</th>
          </tr></thead>
          <tbody>
            {jobs.map((j) => (
              <tr key={j.job_id} style={{ cursor: 'pointer' }} onClick={() => setSelected(j.job_id)}>
                <td><span className={`score ${scoreClass(j.match_score)}`}>
                  {j.match_score == null ? '—' : j.match_score}
                </span></td>
                <td>{j.title}</td>
                <td>{j.company}</td>
                <td className="muted">{j.location || '—'}</td>
                <td><span className={`chip ${j.eligibility_status}`}>{j.eligibility_status}</span></td>
                <td><span className={`chip ${j.application_status}`}>{j.application_status}</span></td>
                <td>{j.tailored_resume_path ? '✓' : '—'}</td>
              </tr>
            ))}
            {jobs.length === 0 && (
              <tr><td colSpan={7} className="muted" style={{ padding: 24, textAlign: 'center' }}>
                No jobs yet. Add Greenhouse/Lever sources in Settings and run a discovery cycle.
              </td></tr>
            )}
          </tbody>
        </table>
      </div>

      {selected && <JobDrawer jobId={selected} onClose={() => { setSelected(null); load() }} />}
    </div>
  )
}
