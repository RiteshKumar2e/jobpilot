// Thin API client. The key is read from localStorage so you can paste the same
// value you put in JOBPILOT_API_KEY. All calls hit the backend via the Vite proxy.
const KEY = () => localStorage.getItem('jobpilot_api_key') || ''

async function req(path, opts = {}) {
  const res = await fetch(path, {
    ...opts,
    headers: {
      'Content-Type': 'application/json',
      'X-API-Key': KEY(),
      ...(opts.headers || {}),
    },
  })
  if (!res.ok) {
    const text = await res.text()
    let detail = text
    try { detail = JSON.parse(text).detail || text } catch { /* not JSON */ }
    throw new Error(`${res.status}: ${typeof detail === 'string' ? detail : JSON.stringify(detail)}`)
  }
  const ct = res.headers.get('content-type') || ''
  return ct.includes('application/json') ? res.json() : res.text()
}

export const api = {
  setKey: (k) => localStorage.setItem('jobpilot_api_key', k.trim()),
  getKey: KEY,
  checkKey: () => req('/api/auth/check'),
  system: () => req('/api/system'),
  stats: () => req('/api/stats'),
  config: () => req('/api/config'),
  jobs: (params = {}) => {
    const qs = new URLSearchParams(
      Object.entries(params).filter(([, v]) => v !== '' && v != null)
    ).toString()
    return req('/api/jobs' + (qs ? `?${qs}` : ''))
  },
  job: (id) => req(`/api/jobs/${id}`),
  materials: (id) => req(`/api/jobs/${id}/materials`),
  approve: (id) => req(`/api/jobs/${id}/approve`, { method: 'POST' }),
  reject: (id) => req(`/api/jobs/${id}/reject`, { method: 'POST' }),
  runCycle: () => req('/api/cycle/run', { method: 'POST' }),
  resumePending: () => req('/api/cycle/resume', { method: 'POST' }),
  pause: () => req('/api/automation/pause', { method: 'POST' }),
  resumeAutomation: () => req('/api/automation/resume', { method: 'POST' }),
  toggleAgent: (a, enable) =>
    req(`/api/agents/${a}/toggle?enable=${enable}`, { method: 'POST' }),
  events: (limit = 80) => req(`/api/events?limit=${limit}`),
  history: () => req('/api/history'),
  setSchedule: (cron) =>
    req(`/api/schedule?cron=${encodeURIComponent(cron)}`, { method: 'POST' }),
  resumeUrl: (id) => `/api/jobs/${id}/resume`,
  // multi-source discovery
  sources: () => req('/api/sources'),
  testSource: (provider, target) =>
    req('/api/sources/test', { method: 'POST', body: JSON.stringify({ provider, target }) }),
  companies: () => req('/api/companies'),
  addCompany: (body) => req('/api/companies', { method: 'POST', body: JSON.stringify(body) }),
  watchCompany: (id, watch) => req(`/api/companies/${id}/watch?watch=${watch}`, { method: 'POST' }),
  deleteCompany: (id) => req(`/api/companies/${id}`, { method: 'DELETE' }),
  importJob: (body) => req('/api/jobs/import', { method: 'POST', body: JSON.stringify(body) }),
  portalLinks: (q) => req('/api/portals/links' + (q ? `?q=${encodeURIComponent(q)}` : '')),
  monitoring: () => req('/api/monitoring'),
  // answer bank / inbox / coach
  answers: () => req('/api/answers'),
  saveAnswer: (question, answer, pattern = '') =>
    req('/api/answers', { method: 'POST', body: JSON.stringify({ question, answer, pattern }) }),
  inbox: (category = '', includeHandled = false) =>
    req(`/api/inbox?include_handled=${includeHandled}` + (category ? `&category=${category}` : '')),
  checkInbox: () => req('/api/inbox/check', { method: 'POST' }),
  inboxHandled: (id, handled = true) => req(`/api/inbox/${id}/handled?handled=${handled}`, { method: 'POST' }),
  setOutcome: (id, body) => req(`/api/jobs/${id}/outcome`, { method: 'POST', body: JSON.stringify(body) }),
  kit: (id) => req(`/api/jobs/${id}/kit`),
  prep: (id, kind) => req(`/api/jobs/${id}/prep`, { method: 'POST', body: JSON.stringify({ kind }) }),
  mock: (id, question, answer) =>
    req(`/api/jobs/${id}/mock`, { method: 'POST', body: JSON.stringify({ question, answer }) }),
  debrief: (id, notes, interviewer) =>
    req(`/api/jobs/${id}/debrief`, { method: 'POST', body: JSON.stringify({ notes, interviewer }) }),
  attempts: (id) => req(`/api/jobs/${id}/attempts`),
  screenshotUrl: (aid) => `/api/attempts/${aid}/screenshot`,
  linkedinKit: () => req('/api/coach/linkedin'),
  followups: () => req('/api/coach/followups'),
  weekly: () => req('/api/coach/weekly'),
  emailWeekly: () => req('/api/coach/weekly/email', { method: 'POST' }),
}

export function copy(text) {
  try { navigator.clipboard.writeText(text) } catch { /* older browsers */ }
}
