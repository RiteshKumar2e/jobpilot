import React, { useEffect, useState } from 'react'
import { api, copy } from '../api.js'

const OUTCOMES = ['', 'RECRUITER_REPLY', 'ASSESSMENT', 'INTERVIEW_INVITE', 'INTERVIEW_BOOKED',
  'INTERVIEW_DONE', 'OFFER', 'REJECTED_BY_COMPANY']

function Section({ title, children, open: o = false }) {
  const [open, setOpen] = useState(o)
  return (
    <div className="section">
      <button className="section-h" onClick={() => setOpen((v) => !v)}>{open ? '▾' : '▸'} {title}</button>
      {open && <div style={{ marginTop: 8 }}>{children}</div>}
    </div>
  )
}

export default function JobCoach({ job, reload }) {
  const id = job.job_id
  const [outcome, setOutcome] = useState(job.outcome || '')
  const [recruiter, setRecruiter] = useState(job.recruiter || '')
  const [when, setWhen] = useState('')
  const [kit, setKit] = useState(null)
  const [kind, setKind] = useState('recruiter screen')
  const [brief, setBrief] = useState(null)
  const [q, setQ] = useState('')
  const [ans, setAns] = useState('')
  const [score, setScore] = useState(null)
  const [notes, setNotes] = useState('')
  const [deb, setDeb] = useState(null)
  const [attempts, setAttempts] = useState([])
  const [busy, setBusy] = useState('')
  const [msg, setMsg] = useState('')

  useEffect(() => {
    api.kit(id).then(setKit).catch(() => {})
    api.attempts(id).then(setAttempts).catch(() => {})
  }, [id])

  const run = async (label, fn) => {
    setBusy(label); setMsg('')
    try { await fn() } catch (e) { setMsg(String(e.message || e)) } finally { setBusy('') }
  }

  return (
    <div style={{ marginTop: 16 }}>
      <Section title="Outcome (after you applied)" open>
        <div className="filters" style={{ marginBottom: 0 }}>
          <select value={outcome} onChange={(e) => setOutcome(e.target.value)}>
            {OUTCOMES.map((o) => <option key={o} value={o}>{o ? o.replace(/_/g, ' ').toLowerCase() : '— none —'}</option>)}
          </select>
          <input placeholder="Recruiter name / email" value={recruiter} onChange={(e) => setRecruiter(e.target.value)} />
          {outcome === 'INTERVIEW_BOOKED' &&
            <input type="datetime-local" value={when} onChange={(e) => setWhen(e.target.value)} />}
          <button className="btn" disabled={!!busy} onClick={() => run('outcome', async () => {
            await api.setOutcome(id, { outcome, recruiter, interview_at: when }); setMsg('Saved.'); reload()
          })}>Save</button>
        </div>
      </Section>

      {kit && (
        <Section title={`Dream-job kit · ${kit.keyword_match_pct ?? '—'}% keyword match`}>
          <div className="small"><b>You have:</b> {kit.you_have.join(', ') || '—'}</div>
          <div className="small muted"><b>You lack:</b> {kit.you_lack.join(', ') || 'nothing obvious'}</div>
          <div className="muted small" style={{ margin: '4px 0' }}>{kit.advice}</div>
          <div className="pre">{kit.recruiter_note}</div>
          <div className="row" style={{ marginTop: 6 }}>
            <button className="btn ghost" onClick={() => copy(kit.recruiter_note)}>Copy note ({kit.recruiter_note_chars}/200)</button>
            <a className="btn ghost" href={kit.find_recruiter_url} target="_blank" rel="noreferrer">Find recruiter</a>
          </div>
        </Section>
      )}

      <Section title="Interview prep brief">
        <div className="filters" style={{ marginBottom: 6 }}>
          <select value={kind} onChange={(e) => setKind(e.target.value)}>
            <option>recruiter screen</option><option>technical round</option>
            <option>hiring manager</option><option>hr round</option>
          </select>
          <button className="btn" disabled={!!busy} onClick={() => run('prep', async () => {
            const r = await api.prep(id, kind)
            setQ(r.questions[0] || '')
            setBrief({ text: r.brief, questions: r.questions })
          })}>{busy === 'prep' ? 'Writing…' : 'Make brief'}</button>
        </div>
        {brief && <><div className="pre" style={{ maxHeight: 420 }}>{brief.text}</div>
          <button className="btn ghost" style={{ marginTop: 6 }} onClick={() => copy(brief.text)}>Copy brief</button></>}
      </Section>

      <Section title="Mock interview">
        {!brief?.questions && <div className="muted small">Make a prep brief first — it gives you the questions.</div>}
        {brief?.questions && (<>
          <select style={{ width: '100%' }} value={q} onChange={(e) => { setQ(e.target.value); setScore(null); setAns('') }}>
            {brief.questions.map((x) => <option key={x}>{x}</option>)}
          </select>
          <textarea rows={5} style={{ width: '100%', marginTop: 6 }} placeholder="Answer as you would on the call (use your real experience)"
            value={ans} onChange={(e) => setAns(e.target.value)} />
          <button className="btn" disabled={!ans.trim() || !!busy} onClick={() => run('mock', async () => setScore(await api.mock(id, q, ans)))}>
            {busy === 'mock' ? 'Scoring…' : 'Score my answer'}</button>
          {score && <div className="pre" style={{ marginTop: 8 }}><b>{score.score}/10</b>{'\n'}{score.feedback}</div>}
        </>)}
      </Section>

      <Section title="After the call">
        <textarea rows={4} style={{ width: '100%' }} placeholder="Your notes from the call: who you spoke to, what they asked, next steps"
          value={notes} onChange={(e) => setNotes(e.target.value)} />
        <button className="btn" disabled={!notes.trim() || !!busy} onClick={() => run('deb', async () => {
          setDeb(await api.debrief(id, notes, recruiter)); setOutcome('INTERVIEW_DONE'); reload()
        })}>{busy === 'deb' ? 'Writing…' : 'Thank-you email + next steps'}</button>
        {deb && <><div className="pre" style={{ marginTop: 8 }}>{deb.text}</div>
          <button className="btn ghost" style={{ marginTop: 6 }} onClick={() => copy(deb.thank_you)}>Copy thank-you email</button></>}
      </Section>

      {attempts.length > 0 && (
        <Section title={`What was sent (${attempts.length} attempt${attempts.length > 1 ? 's' : ''})`}>
          {attempts.map((a) => (
            <div key={a.id} className="log-line">
              <span className={`chip ${a.result}`}>{a.result}</span> <span className="muted">{a.started_at.slice(0, 16)}</span>
              {a.filled.length > 0 && <div className="small">Filled: {a.filled.join(', ')}</div>}
              {a.missing.length > 0 && <div className="small muted">Missing: {a.missing.join(', ')}</div>}
              {a.error && <div className="small muted">{a.error}</div>}
              {a.has_screenshot && <a className="small" href={api.screenshotUrl(a.id)} target="_blank" rel="noreferrer">Screenshot of the form</a>}
            </div>
          ))}
        </Section>
      )}
      {msg && <div className="banner auto" style={{ marginTop: 10 }}>{msg}</div>}
    </div>
  )
}
