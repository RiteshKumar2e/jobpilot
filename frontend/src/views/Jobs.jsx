import React, { useEffect, useState, useCallback } from 'react'
import { api } from '../api.js'
import JobDrawer from './JobDrawer.jsx'

function scoreClass(s) {
  if (s == null) return 'lo'
  if (s >= 70) return 'hi'
  if (s >= 50) return 'mid'
  return 'lo'
}

// Application center tabs = lifecycle presets
const TABS = [
  ['', 'All'],
  ['SHORTLISTED,ELIGIBLE,VERIFIED', 'Eligible'],
  ['READY_FOR_REVIEW', 'Ready / handoff'],
  ['ACTION_REQUIRED', 'Needs you'],
  ['SUBMITTED_CONFIRMED', 'Submitted ✓'],
  ['SUBMISSION_UNVERIFIED', 'Unverified'],
  ['REJECTED,CLOSED', 'Rejected / closed'],
]
const SOURCES = ['greenhouse', 'lever', 'ashby', 'workable', 'smartrecruiters', 'recruitee',
  'personio', 'breezy', 'workday', 'careers', 'remotive', 'remoteok', 'arbeitnow', 'himalayas',
  'weworkremotely', 'adzuna', 'linkedin', 'naukri', 'indeed', 'manual']
const EMPTY = { q: '', source: '', work_mode: '', location: '', posted_within_days: '',
  salary_disclosed: '', min_score: '', sort: 'score' }

export default function Jobs() {
  const [jobs, setJobs] = useState([])
  const [tab, setTab] = useState('')
  const [filters, setFilters] = useState(EMPTY)
  const [selected, setSelected] = useState(null)
  const [err, setErr] = useState('')
  const [loading, setLoading] = useState(false)

  const load = useCallback(async () => {
    setLoading(true)
    try { setJobs(await api.jobs({ ...filters, lifecycle: tab, limit: 300 })); setErr('') }
    catch (e) { setErr(String(e.message || e)) }
    finally { setLoading(false) }
  }, [filters, tab])

  useEffect(() => {
    const t = setTimeout(load, 250)   // debounce typing
    return () => clearTimeout(t)
  }, [load])

  const upd = (k) => (e) => setFilters((f) => ({ ...f, [k]: e.target.value }))

  return (
    <div>
      <div className="topbar"><h1>Jobs</h1>
        <button className="btn ghost" onClick={load}>{loading ? 'Loading…' : 'Refresh'}</button>
      </div>

      <div className="tabs">
        {TABS.map(([v, l]) => (
          <button key={l} className={tab === v ? 'active' : ''} onClick={() => setTab(v)}>{l}</button>
        ))}
      </div>

      <div className="filters">
        <input style={{ flex: 2, minWidth: 180 }} placeholder="Search title, company, description"
          value={filters.q} onChange={upd('q')} />
        <select value={filters.source} onChange={upd('source')}>
          <option value="">All sources</option>
          {SOURCES.map((s) => <option key={s}>{s}</option>)}
        </select>
        <select value={filters.work_mode} onChange={upd('work_mode')}>
          <option value="">Any work mode</option>
          <option>remote</option><option>hybrid</option><option>onsite</option>
        </select>
        <input style={{ width: 120 }} placeholder="Location" value={filters.location} onChange={upd('location')} />
        <select value={filters.posted_within_days} onChange={upd('posted_within_days')}>
          <option value="">Posted any time</option>
          <option value="1">Last 24h</option><option value="7">Last 7 days</option>
          <option value="30">Last 30 days</option>
        </select>
        <select value={filters.salary_disclosed} onChange={upd('salary_disclosed')}>
          <option value="">Salary: any</option><option value="true">Salary disclosed</option>
        </select>
        <input style={{ width: 90 }} placeholder="min score" value={filters.min_score} onChange={upd('min_score')} />
        <select value={filters.sort} onChange={upd('sort')}>
          <option value="score">Sort: relevance</option>
          <option value="posted">Sort: newest posting</option>
          <option value="date">Sort: newest found</option>
          <option value="salary">Sort: salary disclosed first</option>
          <option value="company">Sort: company</option>
        </select>
        <button className="btn ghost" onClick={() => setFilters(EMPTY)}>Clear</button>
      </div>

      {err && <div className="panel">Error: {err}</div>}

      <div className="panel" style={{ padding: 0 }}>
        <div className="table-wrap">
          <table>
            <thead><tr>
              <th>Score</th><th>Title</th><th>Company</th><th>Location</th>
              <th>State</th><th>Sources</th><th>Apply</th>
            </tr></thead>
            <tbody>
              {jobs.map((j) => (
                <tr key={j.job_id} style={{ cursor: 'pointer' }} onClick={() => setSelected(j.job_id)}>
                  <td><span className={`score ${scoreClass(j.match_score)}`}>
                    {j.match_score == null ? '—' : j.match_score}
                  </span></td>
                  <td>{j.title}{j.salary_range && <div className="muted small">{j.salary_range}</div>}</td>
                  <td>{j.company}</td>
                  <td className="muted">{j.location || '—'}{j.work_mode && <div className="small">{j.work_mode}</div>}</td>
                  <td><span className={`chip ${j.lifecycle}`}>{j.lifecycle}</span></td>
                  <td className="small">{(j.sources?.length ? j.sources.map((s) => s.source) : [j.source])
                    .filter((v, i, a) => a.indexOf(v) === i).join(', ')}</td>
                  <td className="small">{j.apply_mode === 'auto' ? 'Auto' : j.apply_mode === 'assist' ? 'On approve' : 'You apply'}</td>
                </tr>
              ))}
              {jobs.length === 0 && (
                <tr><td colSpan={7} className="muted" style={{ padding: 24, textAlign: 'center' }}>
                  No jobs match. Change the filters, or add sources/companies and run a discovery cycle.
                </td></tr>
              )}
            </tbody>
          </table>
        </div>
      </div>

      {selected && <JobDrawer jobId={selected} onClose={() => { setSelected(null); load() }} />}
    </div>
  )
}
