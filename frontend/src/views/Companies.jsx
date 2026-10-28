import React, { useEffect, useState, useCallback } from 'react'
import { api } from '../api.js'

export default function Companies() {
  const [rows, setRows] = useState([])
  const [err, setErr] = useState('')
  const [filter, setFilter] = useState('')
  const [onlyHiring, setOnlyHiring] = useState(false)
  const [add, setAdd] = useState({ url: '', name: '' })
  const [msg, setMsg] = useState('')
  const [busy, setBusy] = useState(false)

  const load = useCallback(async () => {
    try { setRows(await api.companies()); setErr('') }
    catch (e) { setErr(String(e.message || e)) }
  }, [])
  useEffect(() => { load() }, [load])

  const addCompany = async () => {
    setBusy(true); setMsg('')
    try {
      const r = await api.addCompany(add)
      setMsg(r.existing ? `Already registered (${r.provider}: ${r.board}) — now watched.`
        : `Added ${r.provider}: ${r.board}. It's polled from the next discovery cycle.`)
      setAdd({ url: '', name: '' })
      await load()
    } catch (e) { setMsg(String(e.message || e)) }
    finally { setBusy(false) }
  }
  const toggle = async (c) => { await api.watchCompany(c.id, !c.watch); load() }
  const remove = async (c) => {
    if (!confirm(`Remove ${c.name} from the registry? (Its stored jobs stay.)`)) return
    await api.deleteCompany(c.id); load()
  }

  const f = filter.toLowerCase()
  const shown = rows.filter((c) =>
    (!f || `${c.name} ${c.board} ${c.provider}`.toLowerCase().includes(f)) &&
    (!onlyHiring || c.eligible_jobs > 0))

  return (
    <div>
      <div className="topbar"><h1>Companies</h1>
        <button className="btn ghost" onClick={load}>Refresh</button>
      </div>
      <p className="muted" style={{ marginTop: -8 }}>
        Employers whose boards are polled every cycle — from your profile, added here, or discovered
        through links in other listings. Watched companies are fetched; unwatched ones are kept but skipped.
      </p>

      <div className="panel">
        <div className="l muted">Add a company</div>
        <div className="filters" style={{ marginTop: 8 }}>
          <input style={{ flex: 2, minWidth: 200 }}
            placeholder="Careers page or any job link (Greenhouse, Lever, Ashby, Workday, …)"
            value={add.url} onChange={(e) => setAdd({ ...add, url: e.target.value })} />
          <input style={{ flex: 1, minWidth: 120 }} placeholder="Name (optional)" value={add.name}
            onChange={(e) => setAdd({ ...add, name: e.target.value })} />
          <button className="btn" disabled={busy || !add.url} onClick={addCompany}>Add</button>
        </div>
        <div className="muted small">The ATS is detected from the link. Other careers pages are read
          for schema.org job data and for links to supported boards.</div>
        {msg && <div className="banner auto" style={{ marginTop: 10 }}>{msg}</div>}
      </div>

      {err && <div className="panel">Error: {err}</div>}

      <div className="filters">
        <input placeholder="Filter companies" value={filter} onChange={(e) => setFilter(e.target.value)} />
        <label className="toggle">
          <input type="checkbox" checked={onlyHiring} onChange={(e) => setOnlyHiring(e.target.checked)} />
          With eligible jobs
        </label>
        <span className="muted" style={{ alignSelf: 'center' }}>{shown.length} of {rows.length}</span>
      </div>

      <div className="panel" style={{ padding: 0 }}>
        <div className="table-wrap">
          <table>
            <thead><tr>
              <th>Company</th><th>Platform</th><th>Relevant open</th><th>Eligible</th>
              <th>Status</th><th>Found via</th><th></th>
            </tr></thead>
            <tbody>
              {shown.map((c) => (
                <tr key={c.id}>
                  <td><b>{c.name}</b>
                    {c.careers_url && <div className="small"><a href={c.careers_url} target="_blank" rel="noreferrer">careers page</a></div>}</td>
                  <td>{c.provider}{c.auto_submit && <div className="muted small">auto-submit</div>}</td>
                  <td>{c.open_jobs}</td>
                  <td>{c.eligible_jobs}</td>
                  <td><span className={`chip ${c.status === 'ok' ? 'VERIFIED' : c.status === 'error' ? 'FAILED' : ''}`}>{c.status}</span>
                    {c.last_error && <div className="muted small" title={c.last_error}>{c.last_error.slice(0, 60)}</div>}</td>
                  <td className="muted small">{c.discovered_by}</td>
                  <td className="nowrap">
                    <button className={`toggle ${c.watch ? 'on' : ''}`} onClick={() => toggle(c)}>
                      {c.watch ? '● Watching' : '○ Watch'}
                    </button>
                    <button className="toggle" onClick={() => remove(c)} title="Remove">✕</button>
                  </td>
                </tr>
              ))}
              {shown.length === 0 && (
                <tr><td colSpan={7} className="muted" style={{ padding: 24, textAlign: 'center' }}>
                  No companies yet — run a discovery cycle (profile boards register themselves) or add one above.
                </td></tr>
              )}
            </tbody>
          </table>
        </div>
      </div>
    </div>
  )
}
