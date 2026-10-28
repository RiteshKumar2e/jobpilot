import React, { useEffect, useState, useCallback } from 'react'
import { api } from '../api.js'

const KIND = { ats: 'Company ATS', feed: 'Job feed', careers: 'Careers page', portal: 'Portal' }
const APPLY = {
  auto: 'Auto-submit',
  assist: 'Fill on approve',
  handoff: 'You apply (handoff)',
}
const STATUS_CHIP = {
  active: 'VERIFIED', configured: 'DRAFTED', degraded: 'NEEDS_REVIEW', failing: 'FAILED',
  needs_key: 'NEEDS_REVIEW', disabled: 'REJECTED', handoff: '',
}

function ago(s) {
  if (!s) return '—'
  const d = new Date(s.replace(' ', 'T') + (s.includes('+') || s.endsWith('Z') ? '' : 'Z'))
  const m = Math.round((Date.now() - d.getTime()) / 60000)
  if (Number.isNaN(m)) return s
  if (m < 60) return `${m}m ago`
  if (m < 48 * 60) return `${Math.round(m / 60)}h ago`
  return `${Math.round(m / 1440)}d ago`
}

export default function Sources() {
  const [data, setData] = useState(null)
  const [err, setErr] = useState('')
  const [showTargets, setShowTargets] = useState(false)
  const [test, setTest] = useState({ provider: 'ashby', target: '' })
  const [testRes, setTestRes] = useState(null)
  const [busy, setBusy] = useState(false)
  const [imp, setImp] = useState({ url: '', title: '', company: '', location: '', description: '' })
  const [impMsg, setImpMsg] = useState('')
  const [links, setLinks] = useState([])
  const [q, setQ] = useState('')

  const load = useCallback(async () => {
    try { setData(await api.sources()); setErr('') }
    catch (e) { setErr(String(e.message || e)) }
  }, [])
  useEffect(() => { load(); api.portalLinks().then(setLinks).catch(() => {}) }, [load])

  const runTest = async () => {
    setBusy(true); setTestRes(null)
    try { setTestRes(await api.testSource(test.provider, test.target)) }
    catch (e) { setTestRes({ ok: false, error: String(e.message || e) }) }
    finally { setBusy(false) }
  }
  const doImport = async () => {
    setBusy(true); setImpMsg('')
    try {
      const r = await api.importJob(imp)
      setImpMsg(`✓ ${r.title} @ ${r.company} — ${r.message}`)
      setImp({ url: '', title: '', company: '', location: '', description: '' })
    } catch (e) { setImpMsg(String(e.message || e)) }
    finally { setBusy(false) }
  }
  const searchLinks = async () => {
    try { setLinks(await api.portalLinks(q)) } catch (e) { setErr(String(e.message || e)) }
  }

  if (err && !data) return <div className="panel">Could not load sources: {err}</div>
  if (!data) return <div className="panel muted">Loading…</div>

  const fetchable = data.providers.filter((p) => p.kind !== 'portal')
  const portals = data.providers.filter((p) => p.kind === 'portal')

  return (
    <div>
      <div className="topbar"><h1>Sources</h1>
        <button className="btn ghost" onClick={load}>Refresh</button>
      </div>
      <p className="muted" style={{ marginTop: -8 }}>
        Every source JobPilot can read, what it can do, and how it did on the last runs.
        Only sources on your auto-apply allowlist are ever submitted automatically.
      </p>

      <div className="panel" style={{ padding: 0 }}>
        <div className="table-wrap">
          <table>
            <thead><tr>
              <th>Source</th><th>Type</th><th>Status</th><th>Applying</th>
              <th>Targets</th><th>Last OK</th><th>Last run</th><th>Stored</th><th>Eligible</th>
            </tr></thead>
            <tbody>
              {fetchable.map((p) => (
                <tr key={p.name} title={p.notes}>
                  <td><b>{p.name}</b><div className="muted small">{p.notes}</div></td>
                  <td>{KIND[p.kind]}</td>
                  <td><span className={`chip ${STATUS_CHIP[p.status] || ''}`}>{p.status.replace('_', ' ')}</span>
                    {p.failing_targets > 0 && <div className="muted small">{p.failing_targets} failing</div>}</td>
                  <td>{p.auto_submit ? APPLY.auto : APPLY[p.apply_mode === 'auto' ? 'assist' : p.apply_mode]}</td>
                  <td>{p.targets}</td>
                  <td className="muted">{ago(p.last_ok)}</td>
                  <td>{p.jobs_last}</td>
                  <td>{p.jobs_stored}</td>
                  <td>{p.eligible}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </div>

      <div className="panel">
        <div className="row" style={{ justifyContent: 'space-between' }}>
          <div className="l muted">Per-target health ({data.targets.length})</div>
          <button className="btn ghost" onClick={() => setShowTargets((v) => !v)}>
            {showTargets ? 'Hide' : 'Show'}
          </button>
        </div>
        {showTargets && (
          <div className="table-wrap" style={{ marginTop: 10 }}>
            <table>
              <thead><tr><th>Target</th><th>Last OK</th><th>Jobs</th><th>Fails</th><th>Error / cool-down</th></tr></thead>
              <tbody>
                {data.targets.map((t) => (
                  <tr key={t.key}>
                    <td>{t.provider}: <span className="muted">{t.target}</span></td>
                    <td className="muted">{ago(t.last_ok)}</td>
                    <td>{t.jobs_last}</td>
                    <td>{t.consecutive_failures || ''}</td>
                    <td className="muted small">
                      {t.cooling_down_until && <>retry after {t.cooling_down_until.slice(0, 16)} · </>}
                      {t.last_error}
                    </td>
                  </tr>
                ))}
                {data.targets.length === 0 &&
                  <tr><td colSpan={5} className="muted">No runs yet — run a discovery cycle.</td></tr>}
              </tbody>
            </table>
          </div>
        )}
      </div>

      <div className="grid2">
        <div className="panel">
          <div className="l muted">Test a source</div>
          <p className="muted small">Fetches once and shows a sample. Nothing is stored.</p>
          <div className="filters">
            <select value={test.provider} onChange={(e) => setTest({ ...test, provider: e.target.value })}>
              {fetchable.map((p) => <option key={p.name}>{p.name}</option>)}
            </select>
            <input style={{ flex: 1, minWidth: 160 }} placeholder="board id, query or careers URL"
              value={test.target} onChange={(e) => setTest({ ...test, target: e.target.value })} />
            <button className="btn" disabled={busy || !test.target} onClick={runTest}>Test</button>
          </div>
          {testRes && (testRes.ok ? (
            <div>
              <div className="muted">{testRes.count} relevant listing(s){testRes.complete ? ' · full board' : ''}</div>
              {testRes.sample.map((s, i) => (
                <div key={i} className="small">· <a href={s.url} target="_blank" rel="noreferrer">{s.title}</a>
                  <span className="muted"> — {s.company} · {s.location || 'n/a'}</span></div>
              ))}
              {testRes.discovered_boards?.length > 0 &&
                <div className="muted small">Linked boards: {testRes.discovered_boards.map((b) => `${b[0]}:${b[1]}`).join(', ')}</div>}
            </div>
          ) : <div className="flag">{testRes.error}</div>)}
        </div>

        <div className="panel">
          <div className="l muted">Import a job URL</div>
          <p className="muted small">
            Company ATS links are fetched (and the company is watched from then on). For LinkedIn,
            Naukri, Indeed, … add title + company and paste the description — you apply there yourself.
          </p>
          <div className="stack">
            <input placeholder="https://… job link" value={imp.url} onChange={(e) => setImp({ ...imp, url: e.target.value })} />
            <div className="filters" style={{ marginBottom: 0 }}>
              <input style={{ flex: 1, minWidth: 120 }} placeholder="Title (portals)" value={imp.title}
                onChange={(e) => setImp({ ...imp, title: e.target.value })} />
              <input style={{ flex: 1, minWidth: 120 }} placeholder="Company (portals)" value={imp.company}
                onChange={(e) => setImp({ ...imp, company: e.target.value })} />
              <input style={{ flex: 1, minWidth: 100 }} placeholder="Location" value={imp.location}
                onChange={(e) => setImp({ ...imp, location: e.target.value })} />
            </div>
            <textarea rows={3} placeholder="Description (optional, improves matching)" value={imp.description}
              onChange={(e) => setImp({ ...imp, description: e.target.value })} />
            <div><button className="btn" disabled={busy || !imp.url} onClick={doImport}>Import</button></div>
          </div>
          {impMsg && <div className="banner auto" style={{ marginTop: 10 }}>{impMsg}</div>}
        </div>
      </div>

      <div className="panel">
        <div className="l muted">Search the portals yourself</div>
        <p className="muted small">
          {portals.map((p) => p.name).join(', ')} have no permitted API, so JobPilot doesn't scrape
          or log in to them. These links open the right search; import anything good above.
          “web:” links search the web for postings hosted on company ATS boards.
        </p>
        <div className="filters">
          <input style={{ flex: 1, minWidth: 160 }} placeholder="Custom query (default: your search_queries)"
            value={q} onChange={(e) => setQ(e.target.value)} />
          <button className="btn ghost" onClick={searchLinks}>Build links</button>
        </div>
        {links.map((g) => (
          <div key={g.query} style={{ marginBottom: 10 }}>
            <b>{g.query}</b>
            <div className="linkrow">
              {Object.entries(g.links).map(([name, url]) => (
                <a key={name} className="toggle" href={url} target="_blank" rel="noreferrer">{name}</a>
              ))}
            </div>
          </div>
        ))}
      </div>
    </div>
  )
}
