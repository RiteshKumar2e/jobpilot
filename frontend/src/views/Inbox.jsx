import React, { useEffect, useState, useCallback } from 'react'
import { api, copy } from '../api.js'

const CATS = [
  ['', 'All'], ['interview', '📅 Interviews'], ['assessment', '📝 Assessments'],
  ['verification_code', '🔑 Codes'], ['offer', '🎉 Offers'], ['recruiter_reply', '✉ Replies'],
  ['application_received', '✓ Received'], ['rejection', '✗ Rejections'],
]
const CHIP = { interview: 'VERIFIED', assessment: 'NEEDS_REVIEW', verification_code: 'ACTION_REQUIRED',
  offer: 'SUBMITTED_CONFIRMED', rejection: 'REJECTED', application_received: 'ELIGIBLE', recruiter_reply: '' }

export default function Inbox() {
  const [data, setData] = useState(null)
  const [cat, setCat] = useState('')
  const [showHandled, setShowHandled] = useState(false)
  const [msg, setMsg] = useState('')
  const [open, setOpen] = useState(null)

  const load = useCallback(async () => {
    try { setData(await api.inbox(cat, showHandled)) } catch (e) { setMsg(String(e.message || e)) }
  }, [cat, showHandled])
  useEffect(() => { load() }, [load])

  const check = async () => {
    try { setMsg((await api.checkInbox()).message); setTimeout(load, 20000) }
    catch (e) { setMsg(String(e.message || e)) }
  }
  const done = async (m) => { await api.inboxHandled(m.id, !m.handled); load() }

  return (
    <div>
      <div className="topbar"><h1>Inbox</h1>
        <div className="row">
          <button className="btn ghost" onClick={load}>Refresh</button>
          <button className="btn" onClick={check}>Check Gmail now</button>
        </div>
      </div>
      <p className="muted" style={{ marginTop: -8 }}>
        Recruiter emails about your applications, read <b>read-only</b> every hour. Replies are drafted
        and calendar links prepared — nothing is ever sent for you. Copy the draft, edit, send it from Gmail.
      </p>
      {data && !data.enabled && <div className="banner auto">Inbox needs your Gmail App Password
        (JOBPILOT_SMTP_USER / JOBPILOT_SMTP_PASSWORD in backend/.env).</div>}
      {msg && <div className="banner safe">{msg}</div>}

      <div className="tabs">
        {CATS.map(([v, l]) => <button key={l} className={cat === v ? 'active' : ''} onClick={() => setCat(v)}>{l}</button>)}
        <label className="toggle"><input type="checkbox" checked={showHandled}
          onChange={(e) => setShowHandled(e.target.checked)} /> show done</label>
      </div>

      {data?.messages?.length === 0 && <div className="panel muted">No job emails here yet.</div>}
      {data?.messages?.map((m) => (
        <div className="panel" key={m.id} style={{ opacity: m.handled ? 0.6 : 1 }}>
          <div className="row" style={{ justifyContent: 'space-between', alignItems: 'flex-start' }}>
            <div style={{ minWidth: 0, flex: 1 }}>
              <span className={`chip ${CHIP[m.category] || ''}`}>{m.category.replace('_', ' ')}</span>{' '}
              <b>{m.subject}</b>
              <div className="muted small">{m.sender} · {m.received_at.slice(0, 16)}
                {m.job && <> · <b>{m.job.title} @ {m.job.company}</b></>}</div>
            </div>
            <div className="row">
              <a className="btn ghost" href={m.gmail_url} target="_blank" rel="noreferrer">Open in Gmail</a>
              <button className="btn ghost" onClick={() => done(m)}>{m.handled ? 'Undo' : 'Done'}</button>
            </div>
          </div>
          <div className="muted small" style={{ marginTop: 6 }}>{m.snippet.slice(0, 220)}…</div>
          {(m.links.length > 0 || m.calendar_url) && (
            <div className="linkrow" style={{ marginTop: 6 }}>
              {m.calendar_url && <a className="toggle" href={m.calendar_url} target="_blank" rel="noreferrer">
                📅 Add to Google Calendar</a>}
              {m.links.map((l) => <a key={l} className="toggle" href={l} target="_blank" rel="noreferrer">
                {new URL(l).hostname}</a>)}
            </div>
          )}
          {m.draft_reply && (
            <div style={{ marginTop: 8 }}>
              <button className="btn ghost" onClick={() => setOpen(open === m.id ? null : m.id)}>
                {open === m.id ? 'Hide draft reply' : 'Show draft reply'}</button>
              {open === m.id && (<>
                <div className="pre" style={{ marginTop: 8 }}>{m.draft_reply}</div>
                <button className="btn" style={{ marginTop: 6 }} onClick={() => copy(m.draft_reply)}>Copy draft</button>
                <span className="muted small"> Slots are suggestions — check your calendar before sending.</span>
              </>)}
            </div>
          )}
        </div>
      ))}
    </div>
  )
}
