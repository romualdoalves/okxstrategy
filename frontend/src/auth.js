// Token da API (backend: API_AUTH_TOKEN). Guardado só no localStorage deste browser.
const KEY = 'okx_api_token'

export const getToken = () => {
  try { return localStorage.getItem(KEY) || '' } catch { return '' }
}

export const setToken = (t) => {
  try { localStorage.setItem(KEY, (t || '').trim()) } catch {}
}

function askToken() {
  const t = window.prompt('Token da API (API_AUTH_TOKEN do servidor):')
  if (t == null) return false
  setToken(t)
  return true
}

// fetch com Authorization; em 401 pede o token uma vez e repete a chamada.
export async function authFetch(url, options = {}, retry = true) {
  const token = getToken()
  const headers = { ...(options.headers || {}), ...(token ? { Authorization: `Bearer ${token}` } : {}) }
  const res = await fetch(url, { ...options, headers })
  if (res.status === 401 && retry && askToken()) return authFetch(url, options, false)
  return res
}

// WebSocket não aceita headers no browser: token vai na query string.
export function wsUrl(path = '/ws') {
  const protocol = location.protocol === 'https:' ? 'wss' : 'ws'
  const token = getToken()
  return `${protocol}://${location.host}${path}${token ? `?token=${encodeURIComponent(token)}` : ''}`
}
