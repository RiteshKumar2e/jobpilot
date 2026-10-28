import React, { useEffect, useState } from 'react'
import { api } from '../api.js'
import JobCoach from './JobCoach.jsx'

export default function JobDrawer({ jobId, onClose }) {
  const [job, setJob] = useState(null)
  const [mats, setMats] = useState(null)
  const [busy, setBusy] = useState(false)
  const [msg, setMsg] = useState('')

  const load = async () => {
    setJob(await api.job(jobId))
    try { setMats(await api.materials(jobId)) } catch { /* none yet */ }
  }
  useEffect(() => { load() }, [jobId])

  const approve = async () => {
    if (!confirm('Approve and attempt submission? A browser will open to apply.')) return
    setBusy(true); setMsg('')
    try {
      const r = await api.approve(jobId)
      setMsg(`Result: ${r.status}${r.error ? ' — ' + r.error : ''}`)
      await load()
    } catch (e) { setMsg(String(e.message || e)) }
    finally { setBusy(false) }
  }
  const reject = async () => {
    setBusy(true)
    try { await api.reject(jobId); await load() }
    finally { setBusy(false) }
  }

  if (!job) return null
  const flags = mats?.resume_versions?.[0]?.flags || []

  return (
    <>
      <div className="overlay" onClick={onClose} />
      <div className="drawer">
        <div className="row" style={{ justifyContent: 'space-between' }}>
          <h2>{job.title}</h2>
          <button className="btn ghost" onClick={onClose}>Close</button>
        </div>
        <div className="muted">{job.company} · {job.location || 'location n/a'}</div>

        {job.sources?.length > 1 && (
          <div className="flag" style={{ marginTop: 10 }}>
            Listed on {job.sources.length} sources — it's one job and will only be applied to once.
          </div>
        )}

        <div className="kv">
          <div className="k">State</div><div><span className={`chip ${job.lifecycle}`}>{job.lifecycle}</span></div>
          <div className="k">How to apply</div><div>{
            job.apply_mode === 'auto' ? 'Auto-submit (allowlisted source)'
              : job.apply_mode === 'assist' ? 'Browser fills the form when you approve'
                : 'You apply on the site (handoff) — materials below'}</div>
          <div className="k">Found on</div><div>{(job.sources?.length ? job.sources : [{ source: job.source, url: job.job_url }])
            .map((s, i) => <div key={i}><a href={s.url} target="_blank" rel="noreferrer">{s.source}</a></div>)}</div>
          <div className="k">Match score</div><div><b>{job.match_score ?? '—'}</b></div>
          <div className="k">Eligibility</div><div><span className={`chip ${job.eligibility_status}`}>{job.eligibility_status}</span></div>
          <div className="k">App status</div><div><span className={`chip ${job.application_status}`}>{job.application_status}</span></div>
          <div className="k">Resume version</div><div>{job.recommended_resume_version || '—'}</div>
          <div className="k">Matching skills</div><div>{(job.matching_skills || []).join(', ') || '—'}</div>
          <div className="k">Missing skills</div><div className="muted">{(job.missing_skills || []).join(', ') || '—'}</div>
          <div className="k">Why it fits</div><div>{job.score_explanation || '—'}</div>
          <div className="k">Next action</div><div>{job.next_action || '—'}</div>
        </div>

        {job.eligibility_reasons?.length > 0 && (
          <div>
            <div className="l muted">Eligibility reasons</div>
            {job.eligibility_reasons.map((r, i) => <div key={i} className="muted">· {r}</div>)}
          </div>
        )}

        {flags.length > 0 && (
          <div style={{ marginTop: 12 }}>
            <div className="l muted">Truthfulness flags (confirm before sending)</div>
            {flags.map((f, i) => <div key={i} className="flag">⚠ {f}</div>)}
          </div>
        )}

        <div className="row" style={{ marginTop: 14 }}>
          {job.tailored_resume_path &&
            <a className="btn ghost" href={api.resumeUrl(jobId)} target="_blank" rel="noreferrer">Download resume</a>}
          <a className="btn ghost" href={job.job_url} target="_blank" rel="noreferrer">View posting</a>
          {job.application_url && job.application_url !== job.job_url &&
            <a className="btn ghost" href={job.application_url} target="_blank" rel="noreferrer">Application page</a>}
        </div>

        {mats?.cover_letter && (
          <div style={{ marginTop: 16 }}>
            <div className="l muted">Cover letter</div>
            <div className="pre">{mats.cover_letter}</div>
          </div>
        )}

        <div className="row" style={{ marginTop: 18 }}>
          {job.apply_mode === 'handoff'
            ? <a className="btn good" href={job.application_url || job.job_url} target="_blank" rel="noreferrer">
                Open & apply yourself</a>
            : <button className="btn good"
                disabled={busy || ['SUBMITTED_CONFIRMED', 'SUBMISSION_UNVERIFIED'].includes(job.application_status)}
                onClick={approve}>Approve & submit</button>}
          <button className="btn bad" disabled={busy} onClick={reject}>Reject</button>
        </div>
        {msg && <div className="banner auto" style={{ marginTop: 12 }}>{msg}</div>}

        <JobCoach job={job} reload={load} />
      </div>
    </>
  )
}
