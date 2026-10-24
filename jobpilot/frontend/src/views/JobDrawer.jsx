import React, { useEffect, useState } from 'react'
import { api } from '../api.js'

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

        <div className="kv">
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
        </div>

        {mats?.cover_letter && (
          <div style={{ marginTop: 16 }}>
            <div className="l muted">Cover letter</div>
            <div className="pre">{mats.cover_letter}</div>
          </div>
        )}

        <div className="row" style={{ marginTop: 18 }}>
          <button className="btn good" disabled={busy || job.application_status === 'SUBMITTED_CONFIRMED'}
            onClick={approve}>Approve & submit</button>
          <button className="btn bad" disabled={busy} onClick={reject}>Reject</button>
        </div>
        {msg && <div className="banner auto" style={{ marginTop: 12 }}>{msg}</div>}
      </div>
    </>
  )
}
