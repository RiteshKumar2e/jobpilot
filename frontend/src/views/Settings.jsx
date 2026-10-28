import React, { useEffect, useState } from 'react'
import { api } from '../api.js'

const AGENTS = ['discovery', 'verification', 'matching', 'resume_intel',
  'materials', 'browser_apply', 'sheets', 'followup']

export default function Settings({ system, reload }) {
  const [key, setKey] = useState(api.getKey())
  const [config, setConfig] = useState(null)
  const [cron, setCron] = useState(system?.discovery_cron || '0 */6 * * *')
  const [msg, setMsg] = useState('')

  useEffect(() => { api.config().then(setConfig).catch(() => {}) }, [])
  useEffect(() => { if (system?.discovery_cron) setCron(system.discovery_cron) }, [system])

  const saveKey = async () => {
    api.setKey(key)
    setKey(api.getKey())
    try { await api.checkKey(); setMsg('✓ API key saved and accepted by the backend.') }
    catch (e) {
      setMsg(String(e.message || e).startsWith('401')
        ? '✗ Saved, but the backend rejected it. It must exactly match JOBPILOT_API_KEY in backend/.env (restart the backend after editing .env).'
        : `Saved, but the backend could not be reached: ${e.message || e}`)
    }
    reload()
  }
  const toggle = async (a, on) => { await api.toggleAgent(a, on); reload() }
  const pause = async () => { await api.pause(); reload() }
  const resume = async () => { await api.resumeAutomation(); reload() }
  const saveCron = async () => {
    try { await api.setSchedule(cron); setMsg('Schedule updated.'); reload() }
    catch (e) { setMsg(String(e.message || e)) }
  }

  const enabled = new Set(system?.enabled_agents || [])

  return (
    <div>
      <div className="topbar"><h1>Settings</h1></div>

      <div className="panel">
        <div className="l muted">API key</div>
        <p className="muted" style={{ fontSize: 12 }}>
          Paste the value you set as JOBPILOT_API_KEY in backend/.env. Stored only in this browser.
        </p>
        <div className="row">
          <input type="password" value={key} onChange={(e) => setKey(e.target.value)} style={{ width: 320 }} />
          <button className="btn" onClick={saveKey}>Save</button>
        </div>
      </div>

      <div className="panel">
        <div className="l muted">Automation</div>
        <div className="row" style={{ marginTop: 8 }}>
          <span>Approval mode: <b>{system?.approval_mode}</b></span>
          <span className="muted">(change in .env: JOBPILOT_APPROVAL_MODE)</span>
        </div>
        <div className="row" style={{ marginTop: 10 }}>
          {system?.paused
            ? <button className="btn good" onClick={resume}>Resume automation</button>
            : <button className="btn ghost" onClick={pause}>Pause automation</button>}
          <span className="muted">Daily cap: {system?.daily_application_limit} prepared · {system?.daily_autosubmit_limit} auto-submit</span>
        </div>
      </div>

      <div className="panel">
        <div className="l muted">Discovery schedule (cron, UTC)</div>
        <div className="row" style={{ marginTop: 8 }}>
          <input value={cron} onChange={(e) => setCron(e.target.value)} style={{ width: 180 }} />
          <button className="btn" onClick={saveCron}>Update</button>
          <span className="muted">Next run: {system?.next_run || '—'}</span>
        </div>
      </div>

      <div className="panel">
        <div className="l muted">Agents (enable / disable)</div>
        <div style={{ marginTop: 8 }}>
          {AGENTS.map((a) => (
            <span key={a} className={`toggle ${enabled.has(a) ? 'on' : ''}`}
              onClick={() => toggle(a, !enabled.has(a))}>
              {enabled.has(a) ? '● ' : '○ '}{a}
            </span>
          ))}
        </div>
      </div>

      <div className="panel">
        <div className="l muted">Resume variants detected</div>
        {(system?.resume_variants || []).length === 0
          ? <div className="muted" style={{ marginTop: 6 }}>None. Drop resumes into data/resumes/ (pdf/docx/tex) and reload.</div>
          : (system.resume_variants).map((v, i) => (
            <div key={i} className="row" style={{ justifyContent: 'space-between', marginTop: 6 }}>
              <span>{v.filename}</span><span className="chip">{v.label}</span>
            </div>
          ))}
      </div>

      {config && (
        <div className="panel">
          <div className="l muted">Profile check</div>
          <div style={{ marginTop: 8 }}>
            <div>Verified items: {config.verified_items?.verified?.length || 0}</div>
            {(config.verified_items?.unverified || []).map((u, i) =>
              <div key={i} className="flag">⚠ Unverified — confirm in candidate_profile.yaml: {u}</div>)}
          </div>
          <div className="muted" style={{ marginTop: 10, fontSize: 12 }}>
            Sources: {(config.sources?.greenhouse_boards || []).length} Greenhouse boards,
            {' '}{(config.sources?.lever_accounts || []).length} Lever accounts in your profile — every
            source, company and its health is on the Sources and Companies pages.
          </div>
        </div>
      )}

      {msg && <div className={`banner ${/^(✗|Saved, but|\d{3}:)/.test(msg) ? 'auto' : 'safe'}`}>{msg}</div>}
    </div>
  )
}
