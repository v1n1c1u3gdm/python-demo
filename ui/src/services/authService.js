const DEFAULT_API_BASE_URL = 'http://localhost:3000'
const API_BASE_URL = normalizeBaseUrl(process.env.VUE_APP_API_BASE_URL || DEFAULT_API_BASE_URL)
const SESSION_STORAGE_KEY = 'python-demo-admin-session'

export const runtimeGuards = {
  hasWindow: () => typeof window !== 'undefined'
}

function normalizeBaseUrl(url) {
  if (!url) return DEFAULT_API_BASE_URL
  return url.replace(/\/+$/, '')
}

async function httpRequest(path, options = {}) {
  const response = await fetch(`${API_BASE_URL}${path}`, {
    headers: {
      Accept: 'application/json',
      'Content-Type': 'application/json',
      ...(options.headers || {})
    },
    ...options
  })

  if (!response.ok) {
    let detail = `${response.status} ${response.statusText}`
    try {
      const payload = await response.clone().json()
      if (payload?.errors?.length) {
        detail = payload.errors.join(', ')
      } else if (payload?.message) {
        detail = payload.message
      }
    } catch (error) {
      // ignore json parsing errors, fall back to status text
    }
    throw new Error(detail)
  }

  return response.json()
}

export async function login(username, password) {
  return httpRequest('/login', {
    method: 'POST',
    body: JSON.stringify({ username, password })
  })
}

export async function fetchAdminProfile(token) {
  if (!token) {
    throw new Error('Token não informado.')
  }

  return httpRequest('/admin/profile', {
    headers: {
      Authorization: `Bearer ${token}`
    }
  })
}

export function persistSession(session) {
  if (!session || !runtimeGuards.hasWindow()) return
  window.sessionStorage.setItem(SESSION_STORAGE_KEY, JSON.stringify(session))
}

export function getStoredSession() {
  if (!runtimeGuards.hasWindow()) return null
  const stored = window.sessionStorage.getItem(SESSION_STORAGE_KEY)
  if (!stored) return null
  try {
    return JSON.parse(stored)
  } catch (error) {
    clearSession()
    return null
  }
}

export function clearSession() {
  if (!runtimeGuards.hasWindow()) return
  window.sessionStorage.removeItem(SESSION_STORAGE_KEY)
}

export function getApiBaseUrl() {
  return API_BASE_URL
}

