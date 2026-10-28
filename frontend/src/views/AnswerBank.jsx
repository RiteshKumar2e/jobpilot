import React, { useEffect, useState, useCallback } from 'react'
import { api } from '../api.js'

function AnswerRow({ question, onSaved, children }) {
  const [answer, setAnswer] = useState('')
  const [pattern, setPattern] = useState('')
  const [adv, setAdv] = useState(false)
  const [msg, setMsg] = useState('')
  const save = async () => {
    setMsg('')
    try { await api.saveAnswer(question, answer, pattern); setAnswer(''); onSaved() }
    catch (e) { setMsg(String(e.message || e)) }
  }
  return (
    <div className="qa">
      <div><b>{question}</b> {children}</div>
      <div className="filters" style={{ margin: '6px 0 0' }}>
        <input style={{ flex: 2, minWidth: 160 }} placeholder="Your true answer" value={answer}
          onChange={(e) => setAnswer(e.target.value)} onKeyDown={(e) => e.key === 'Enter' && answer && save()} />
        {adv && <input style={{ flex: 1, minWidth: 140 }} placeholder="Match pattern (regex, optional)"
          value={pattern} onChange={(e) => setPattern(e.target.value)} />}
        <button className="btn" disabled={!answer.trim()} onClick={save}>Save</button>
        <button className="btn ghost" onClick={() => setAdv((v) => !v)} title="Match more wordings">
          {adv ? 'Simple' : 'Pattern'}</button>
      </div>
      {msg && <div className="flag">{msg}</div>}
    </div>
  )
}

export default function AnswerBank() {
  const [data, setData] = useState(null)
  const [err, setErr] = useState('')
  const [saved, setSaved] = useState('')

  const load = useCallback(async () => {
    try { setData(await api.answers()); setErr('') }
    catch (e) { setErr(String(e.message || e)) }
  }, [])
  useEffect(() => { load() }, [load])
  const onSaved = () => { setSaved('Saved to your profile — waiting jobs retry on the next run.'); load() }

  if (err && !data) return <div className="panel">Could not load: {err}</div>
  if (!data) return <div className="panel muted">Loading…</div>

  return (
    <div>
      <div className="topbar"><h1>Answer Bank</h1>
        <button className="btn ghost" onClick={load}>Refresh</button>
      </div>
      <p className="muted" style={{ marginTop: -8 }}>
        Job forms ask the same questions again and again. Answer each one once — truthfully — and
        every application that stopped on it goes through by itself. Answers are saved to
        <code> candidate_profile.yaml</code>.
      </p>
      {saved && <div className="banner safe">{saved}</div>}

      <div className="panel">
        <div className="l muted">Questions that stopped your applications ({data.pending.length})</div>
        {data.pending.length === 0 && <div className="muted" style={{ marginTop: 8 }}>
          Nothing waiting — every required question so far has an answer.</div>}
        {data.pending.map((p) => (
          <AnswerRow key={p.question} question={p.question} onSaved={onSaved}>
            <span className="chip ACTION_REQUIRED">{p.count} job{p.count > 1 ? 's' : ''}</span>
            <div className="muted small">{p.jobs.slice(0, 4).map((j) => `${j.title} @ ${j.company}`).join(' · ')}
              {p.jobs.length > 4 && ` · +${p.jobs.length - 4} more`}</div>
          </AnswerRow>
        ))}
      </div>

      <div className="panel">
        <div className="l muted">The questions every form asks</div>
        <div className="table-wrap">
          <table>
            <thead><tr><th>Topic</th><th>Your answer</th></tr></thead>
            <tbody>
              {data.coverage.map((c) => (
                <tr key={c.topic}>
                  <td>{c.topic}<div className="muted small">“{c.question}”</div></td>
                  <td>{c.answer
                    ? <span className="chip VERIFIED">{c.answer.length > 60 ? c.answer.slice(0, 60) + '…' : c.answer}</span>
                    : <AnswerRow question={c.question} onSaved={onSaved}><span className="chip NEEDS_REVIEW">missing</span></AnswerRow>}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </div>

      <div className="panel">
        <div className="l muted">All saved answers ({data.answers.length})</div>
        {data.answers.map((a, i) => (
          <div key={i} className="log-line"><span className="muted">{a.match}</span> → <b>{String(a.answer)}</b></div>
        ))}
      </div>
    </div>
  )
}
