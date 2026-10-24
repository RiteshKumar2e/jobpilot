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
    throw new Error(`${res.status}: ${text}`)
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
}
