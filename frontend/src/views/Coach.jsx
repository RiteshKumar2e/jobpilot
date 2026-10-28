import React, { useEffect, useState } from 'react'
import { api, copy } from '../api.js'

function LinkedInKit() {
  const [k, setK] = useState(null)
  const [err, setErr] = useState('')
  useEffect(() => { api.linkedinKit().then(setK).catch((e) => setErr(String(e.message || e))) }, [])
  if (err) return <div className="panel">Error: {err}</div>
  if (!k) return <div className="panel muted">Writing your LinkedIn kit…</div>
  return (
    <div>
      <div className="banner auto">{k.note}</div>
      <div className="panel">
        <div className="row" style={{ justifyContent: 'space-between' }}>
          <div className="l muted">Headline ({k.headline_chars}/220)</div>
          <button className="btn ghost" onClick={() => copy(k.headline)}>Copy</button>
        </div>
        <div className="pre">{k.headline}</div>
      </div>
      <div className="panel">
        <div className="row" style={{ justifyContent: 'space-between' }}>
          <div className="l muted">About ({k.about_chars}/2600)</div>
          <button className="btn ghost" onClick={() => copy(k.about)}>Copy</button>
        </div>
        <div className="pre" style={{ maxHeight: 360 }}>{k.about}</div>
      </div>
      <div className="grid2">
        <div className="panel">
          <div className="l muted">Top 10 skills (ordered by how often your target jobs ask)</div>
          <ol>{k.top_skills.map((s) => <li key={s}>{s}</li>)}</ol>
        </div>
        <div className="panel">
          <div className="l muted">Open to Work</div>
          <div><b>Titles:</b> {k.open_to_work.job_titles.join(', ')}</div>
          <div><b>Locations:</b> {k.open_to_work.locations.join(', ')}</div>
          <div><b>Visible to:</b> {k.open_to_work.visibility}</div>
        </div>
      </div>
      {k.check.length > 0 && <div className="panel">
        <div className="l muted">Confirm before posting (marked [CHECK] in your profile)</div>
        {k.check.map((c, i) => <div key={i} className="flag">⚠ {c}</div>)}
      </div>}
    </div>
  )
}

function Followups() {
  const [rows, setRows] = useState(null)
  useEffect(() => { api.followups().then(setRows).catch(() => setRows([])) }, [])
  if (!rows) return <div className="panel muted">Loading…</div>
  return (
    <div>
      <p className="muted">Applied 7+ days ago with no reply — your top 5 by fit. Find the recruiter or
        hiring manager and send the note <b>yourself</b> (keeps you inside LinkedIn's rules).</p>
      {rows.length === 0 && <div className="panel muted">Nothing to follow up on yet.</div>}
      {rows.map((r) => (
        <div className="panel" key={r.job_id}>
          <b>{r.title} @ {r.company}</b> <span className="muted small">applied {r.applied} · score {r.score}</span>
          <div className="pre" style={{ marginTop: 6 }}>{r.note}</div>
          <div className="row" style={{ marginTop: 6 }}>
            <button className="btn ghost" onClick={() => copy(r.note)}>Copy note ({r.note.length} chars)</button>
            <a className="btn ghost" href={r.find_recruiter_url} target="_blank" rel="noreferrer">Find recruiter on LinkedIn</a>
            <a className="btn ghost" href={r.job_url} target="_blank" rel="noreferrer">Job</a>
          </div>
        </div>
      ))}
    </div>
  )
}

function Weekly() {
  const [r, setR] = useState(null)
  const [msg, setMsg] = useState('')
  useEffect(() => { api.weekly().then(setR).catch((e) => setMsg(String(e.message || e))) }, [])
  if (!r) return <div className="panel muted">{msg || 'Loading…'}</div>
  const Tbl = ({ title, data }) => (
    <div className="panel">
      <div className="l muted">{title}</div>
      <div className="table-wrap"><table>
        <thead><tr><th></th><th>Applied</th><th>Replies</th><th>Interviews</th><th>Reply rate</th></tr></thead>
        <tbody>{Object.entries(data).map(([k, v]) => (
          <tr key={k}><td>{k}</td><td>{v.applied}</td><td>{v.replies}</td><td>{v.interviews}</td>
            <td>{Math.round(v.reply_rate * 100)}%</td></tr>))}
          {Object.keys(data).length === 0 && <tr><td colSpan={5} className="muted">No applications this week.</td></tr>}
        </tbody></table></div>
    </div>
  )
  return (
    <div>
      <div className="cards">
        <div className="card"><div className="n">{r.applied}</div><div className="l">Applied</div></div>
        <div className="card"><div className="n">{r.replies}</div><div className="l">Replies</div></div>
        <div className="card"><div className="n">{r.interviews}</div><div className="l">Interviews</div></div>
        <div className="card"><div className="n">{r.waiting_on_you}</div><div className="l">Waiting on you</div></div>
      </div>
      <div className="panel">
        <div className="l muted">Three changes for next week</div>
        <ol>{r.changes.map((c, i) => <li key={i}>{c}</li>)}</ol>
        <button className="btn ghost" onClick={async () => {
          try { const x = await api.emailWeekly(); setMsg(x.sent ? 'Emailed to you.' : 'Email not configured.') }
          catch (e) { setMsg(String(e.message || e)) }
        }}>Email me this review</button> <span className="muted small">{msg} (also sent every Sunday 9 AM)</span>
      </div>
      <div className="grid2">
        <Tbl title="By source" data={r.by_source} />
        <Tbl title="By resume version" data={r.by_resume} />
      </div>
      <div className="panel">
        <div className="l muted">Why jobs were rejected this week</div>
        {r.rejection_reasons.map(([why, n]) => <div key={why} className="log-line">{n}× {why}</div>)}
      </div>
    </div>
  )
}

const TABS = [['weekly', 'Weekly review'], ['followups', 'Follow-ups'], ['linkedin', 'LinkedIn kit']]

export default function Coach() {
  const [tab, setTab] = useState('weekly')
  return (
    <div>
      <div className="topbar"><h1>Coach</h1></div>
      <p className="muted" style={{ marginTop: -8 }}>Interview prep, mock interviews and debriefs are on
        each job (Jobs → open a job).</p>
      <div className="tabs">
        {TABS.map(([k, l]) => <button key={k} className={tab === k ? 'active' : ''} onClick={() => setTab(k)}>{l}</button>)}
      </div>
      {tab === 'weekly' && <Weekly />}
      {tab === 'followups' && <Followups />}
      {tab === 'linkedin' && <LinkedInKit />}
    </div>
  )
}
