import Keycloak from 'keycloak-js'
import { clearSession, fetchAdminProfile } from './authService'

const redirectUri = () => `${window.location.origin}/admin`
let adapter
let adapterInitialized = false
let session = null
let operation = 0
let initialization
let refreshPromise
const sessionListeners = new Set()

export function isSsoEnabled() {
  return import.meta.env.VITE_SSO_ENABLED === 'true'
}

export function getSsoSession() {
  return session
}

export function subscribeSsoSession(listener) {
  sessionListeners.add(listener)
  return () => sessionListeners.delete(listener)
}

function publishSsoSession(error = null) {
  const state = { session, error }
  sessionListeners.forEach(listener => listener(state))
}

function getAdapter() {
  if (!adapter) {
    const { VITE_KEYCLOAK_URL: url, VITE_KEYCLOAK_REALM: realm, VITE_KEYCLOAK_CLIENT_ID: clientId } = import.meta.env
    if (!url || !realm || !clientId) throw new Error('A configuração do Keycloak está incompleta.')
    adapter = new Keycloak({ url, realm, clientId })
    adapter.onTokenExpired = () => {
      void refreshSsoSession().catch(() => {})
    }
  }
  return adapter
}

function clearSsoSession(error = null) {
  const hadSession = Boolean(session)
  session = null
  clearSession()
  if (hadSession || error) publishSsoSession(error)
}

async function loadAdminProfile(token) {
  try {
    return await fetchAdminProfile(token)
  } catch (error) {
    if (error?.status === 403) {
      throw new Error('A conta não possui o papel admin.', { cause: error })
    }
    throw error
  }
}

async function loadValidatedSession(client, currentOperation) {
  if (currentOperation !== operation) return null
  if (!client.authenticated || !client.token) {
    clearSsoSession(session ? new Error('A sessão Keycloak expirou.') : null)
    return null
  }
  const profile = await loadAdminProfile(client.token)
  if (currentOperation !== operation) return null
  if (!profile?.roles?.includes('admin')) throw new Error('A conta não possui o papel admin.')
  session = {
    username: profile.username,
    ...(profile.email ? { email: profile.email } : {}),
    roles: profile.roles,
    access_token: client.token,
    sso: true
  }
  publishSsoSession()
  return session
}

export async function initializeSso() {
  if (!isSsoEnabled()) return null
  if (initialization) return initialization
  if (adapterInitialized) return session ? refreshSsoSession() : null
  const currentOperation = ++operation
  clearSsoSession()
  const client = getAdapter()
  initialization = (async () => {
    try {
      const authenticated = await client.init({
        onLoad: 'check-sso',
        checkLoginIframe: false,
        pkceMethod: 'S256',
        redirectUri: redirectUri()
      })
      adapterInitialized = true
      if (currentOperation !== operation || !authenticated) return null
      return await loadValidatedSession(client, currentOperation)
    } catch (error) {
      if (currentOperation === operation) clearSsoSession(error)
      throw error
    } finally {
      if (currentOperation === operation) initialization = null
    }
  })()
  return initialization
}

export async function loginWithSso() {
  if (!isSsoEnabled()) throw new Error('O login SSO está desativado.')
  await getAdapter().login({ redirectUri: redirectUri() })
}

export async function logoutFromSso() {
  const client = adapter
  operation += 1
  initialization = null
  clearSsoSession()
  if (client) await client.logout({ redirectUri: redirectUri() })
}

export function refreshSsoSession() {
  if (!isSsoEnabled() || !adapter || !session) return Promise.resolve(null)
  if (refreshPromise) return refreshPromise
  const currentOperation = operation
  const pendingRefresh = (async () => {
    try {
      await adapter.updateToken(30)
      return await loadValidatedSession(adapter, currentOperation)
    } catch (error) {
      if (currentOperation === operation) clearSsoSession(error)
      throw error
    } finally {
      if (refreshPromise === pendingRefresh) refreshPromise = null
    }
  })()
  refreshPromise = pendingRefresh
  return pendingRefresh
}
