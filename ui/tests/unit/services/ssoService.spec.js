import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import Keycloak from 'keycloak-js'
import {
  fetchAdminProfile,
  clearSession
} from '@/services/authService'

const { mockState } = vi.hoisted(() => ({ mockState: { adapter: null } }))

vi.mock('keycloak-js', () => ({
  default: vi.fn(function KeycloakMock() {
    return mockState.adapter
  })
}))
vi.mock('@/services/authService', () => ({
  fetchAdminProfile: vi.fn(),
  clearSession: vi.fn()
}))

describe('ssoService', () => {
  let adapter
  let sso

  beforeEach(async () => {
    vi.resetModules()
    vi.clearAllMocks()
    vi.stubEnv('VITE_SSO_ENABLED', 'true')
    vi.stubEnv('VITE_KEYCLOAK_URL', 'https://app.localhost/auth')
    vi.stubEnv('VITE_KEYCLOAK_REALM', 'python-demo')
    vi.stubEnv('VITE_KEYCLOAK_CLIENT_ID', 'python-demo-ui')
    sessionStorage.clear()
    adapter = {
      init: vi.fn().mockResolvedValue(false),
      login: vi.fn().mockResolvedValue(undefined),
      logout: vi.fn().mockResolvedValue(undefined),
      updateToken: vi.fn().mockResolvedValue(false),
      authenticated: false,
      token: undefined
    }
    mockState.adapter = adapter
    Keycloak.mockClear()
    fetchAdminProfile.mockResolvedValue({ username: 'admin', roles: ['admin'] })
    sso = await import('@/services/ssoService')
  })

  afterEach(() => vi.unstubAllEnvs())

  it('initializes without a session when Keycloak has no authenticated callback', async () => {
    // Arrange
    adapter.init.mockResolvedValue(false)

    // Act
    const session = await sso.initializeSso()

    // Assert
    expect(session).toBeNull()
    expect(fetchAdminProfile).not.toHaveBeenCalled()
    expect(sso.getSsoSession()).toBeNull()
    expect(clearSession).toHaveBeenCalled()
  })

  it('clears the session and rejects when Keycloak rejects an invalid callback', async () => {
    // Arrange
    adapter.init.mockRejectedValue(new Error('callback inválido'))

    // Act
    await expect(sso.initializeSso()).rejects.toThrow('callback inválido')

    // Assert
    expect(sso.getSsoSession()).toBeNull()
    expect(clearSession).toHaveBeenCalled()
  })

  it('retries adapter initialization after a first init failure instead of silently staying logged out', async () => {
    // Arrange
    adapter.init.mockRejectedValueOnce(new Error('keycloak indisponível'))
    adapter.init.mockResolvedValue(true)
    adapter.authenticated = true
    adapter.token = 'recovered-token'

    // Act
    await expect(sso.initializeSso()).rejects.toThrow('keycloak indisponível')
    const session = await sso.initializeSso()

    // Assert
    expect(adapter.init).toHaveBeenCalledTimes(2)
    expect(session.access_token).toBe('recovered-token')
    expect(sso.getSsoSession()).toEqual(session)
  })

  it('loads the API profile for an authenticated PKCE callback without storing its token', async () => {
    // Arrange
    adapter.init.mockResolvedValue(true)
    adapter.authenticated = true
    adapter.token = 'memory-access-token'

    // Act
    const session = await sso.initializeSso()

    // Assert
    expect(Keycloak).toHaveBeenCalledWith({
      url: 'https://app.localhost/auth',
      realm: 'python-demo',
      clientId: 'python-demo-ui'
    })
    expect(adapter.init).toHaveBeenCalledWith(expect.objectContaining({
      onLoad: 'check-sso',
      checkLoginIframe: false,
      pkceMethod: 'S256',
      redirectUri: `${window.location.origin}/admin`
    }))
    expect(fetchAdminProfile).toHaveBeenCalledWith('memory-access-token')
    expect(session).toEqual({ username: 'admin', roles: ['admin'], access_token: 'memory-access-token', sso: true })
    expect(sessionStorage.length).toBe(0)
    expect(sso.getSsoSession()).toEqual(session)
  })

  it('reuses the initialized Keycloak adapter when the admin view mounts again', async () => {
    // Arrange
    adapter.init.mockResolvedValue(true)
    adapter.authenticated = true
    adapter.token = 'first-token'
    await sso.initializeSso()
    adapter.token = 'refreshed-token'

    // Act
    const session = await sso.initializeSso()

    // Assert
    expect(adapter.init).toHaveBeenCalledOnce()
    expect(adapter.updateToken).toHaveBeenCalledWith(30)
    expect(fetchAdminProfile).toHaveBeenLastCalledWith('refreshed-token')
    expect(session.access_token).toBe('refreshed-token')
  })

  it('coalesces simultaneous refreshes into one validated profile request', async () => {
    // Arrange
    adapter.init.mockResolvedValue(true)
    adapter.authenticated = true
    adapter.token = 'initial-token'
    await sso.initializeSso()
    let resolveRefresh
    const pendingRefresh = new Promise(resolve => {
      resolveRefresh = resolve
    })
    adapter.updateToken.mockReturnValue(pendingRefresh)
    adapter.token = 'renewed-token'
    fetchAdminProfile.mockReset()
    fetchAdminProfile.mockResolvedValue({ username: 'admin', roles: ['admin'] })

    // Act
    const firstRefresh = sso.refreshSsoSession()
    const secondRefresh = sso.refreshSsoSession()
    resolveRefresh(true)
    const [firstSession, secondSession] = await Promise.all([firstRefresh, secondRefresh])

    // Assert
    expect(adapter.updateToken).toHaveBeenCalledOnce()
    expect(fetchAdminProfile).toHaveBeenCalledOnce()
    expect(firstSession).toEqual(secondSession)
    expect(firstSession.access_token).toBe('renewed-token')
  })

  it('registers an adapter expiry callback for idle session renewal', async () => {
    // Arrange
    adapter.init.mockResolvedValue(true)
    adapter.authenticated = true
    adapter.token = 'initial-token'

    // Act
    await sso.initializeSso()

    // Assert
    expect(adapter.onTokenExpired).toEqual(expect.any(Function))
  })

  it('publishes expiry and clears an idle session when automatic renewal fails', async () => {
    // Arrange
    adapter.init.mockResolvedValue(true)
    adapter.authenticated = true
    adapter.token = 'expiring-token'
    await sso.initializeSso()
    adapter.updateToken.mockRejectedValue(new Error('refresh indisponível'))
    const expiryState = new Promise(resolve => {
      sso.subscribeSsoSession(state => {
        if (state.error) resolve(state)
      })
    })

    // Act
    adapter.onTokenExpired()
    const state = await expiryState

    // Assert
    expect(state.session).toBeNull()
    expect(state.error.message).toBe('refresh indisponível')
    expect(sso.getSsoSession()).toBeNull()
  })

  it('does not publish a late expiry after logout cancels renewal', async () => {
    // Arrange
    adapter.init.mockResolvedValue(true)
    adapter.authenticated = true
    adapter.token = 'expiring-token'
    await sso.initializeSso()
    let resolveRefresh
    adapter.updateToken.mockReturnValue(new Promise(resolve => {
      resolveRefresh = resolve
    }))
    const states = []
    sso.subscribeSsoSession(state => states.push(state))

    // Act
    adapter.onTokenExpired()
    await sso.logoutFromSso()
    adapter.authenticated = false
    adapter.token = undefined
    resolveRefresh(false)
    await new Promise(resolve => setTimeout(resolve, 0))

    // Assert
    expect(sso.getSsoSession()).toBeNull()
    expect(states).toEqual([{ session: null, error: null }])
  })

  it('clears the SSO session when the validated API profile lacks admin role', async () => {
    // Arrange
    adapter.init.mockResolvedValue(true)
    adapter.authenticated = true
    adapter.token = 'author-token'
    fetchAdminProfile.mockResolvedValue({ username: 'author', roles: ['author'] })

    // Act
    await expect(sso.initializeSso()).rejects.toThrow('A conta não possui o papel admin.')

    // Assert
    expect(sso.getSsoSession()).toBeNull()
    expect(clearSession).toHaveBeenCalled()
  })

  it('rejects a non-admin identity whose admin profile is forbidden with a clear denial message', async () => {
    // Arrange
    adapter.init.mockResolvedValue(true)
    adapter.authenticated = true
    adapter.token = 'non-admin-token'
    fetchAdminProfile.mockRejectedValue(
      Object.assign(new Error('Insufficient permissions.'), { status: 403 })
    )

    // Act
    await expect(sso.initializeSso()).rejects.toThrow('A conta não possui o papel admin.')

    // Assert
    expect(sso.getSsoSession()).toBeNull()
    expect(clearSession).toHaveBeenCalled()
  })

  it('drops the current session when token refresh fails', async () => {
    // Arrange
    adapter.init.mockResolvedValue(true)
    adapter.authenticated = true
    adapter.token = 'fresh-token'
    await sso.initializeSso()
    adapter.updateToken.mockRejectedValue(new Error('refresh indisponível'))

    // Act
    await expect(sso.refreshSsoSession()).rejects.toThrow('refresh indisponível')

    // Assert
    expect(sso.getSsoSession()).toBeNull()
    expect(clearSession).toHaveBeenCalled()
  })

  it('clears the session when refresh completes after Keycloak expires it', async () => {
    // Arrange
    adapter.init.mockResolvedValue(true)
    adapter.authenticated = true
    adapter.token = 'fresh-token'
    await sso.initializeSso()
    adapter.updateToken.mockResolvedValue(false)
    adapter.authenticated = false
    adapter.token = undefined

    // Act
    const refreshedSession = await sso.refreshSsoSession()

    // Assert
    expect(refreshedSession).toBeNull()
    expect(sso.getSsoSession()).toBeNull()
    expect(clearSession).toHaveBeenCalled()
  })

  it('starts Keycloak logout and clears the in-memory session', async () => {
    // Arrange
    adapter.init.mockResolvedValue(true)
    adapter.authenticated = true
    adapter.token = 'fresh-token'
    await sso.initializeSso()

    // Act
    await sso.logoutFromSso()

    // Assert
    expect(adapter.logout).toHaveBeenCalledWith({ redirectUri: `${window.location.origin}/admin` })
    expect(sso.getSsoSession()).toBeNull()
  })

  it('starts the official adapter login with the exact admin redirect', async () => {
    // Arrange

    // Act
    await sso.loginWithSso()

    // Assert
    expect(adapter.login).toHaveBeenCalledWith({ redirectUri: `${window.location.origin}/admin` })
  })

  it('ignores a Keycloak callback that resolves after logout', async () => {
    // Arrange
    let resolveCallback
    adapter.init.mockReturnValue(new Promise(resolve => {
      resolveCallback = resolve
    }))
    adapter.authenticated = true
    adapter.token = 'late-token'
    const pendingInitialization = sso.initializeSso()

    // Act
    await sso.logoutFromSso()
    resolveCallback(true)

    // Assert
    await expect(pendingInitialization).resolves.toBeNull()
    expect(fetchAdminProfile).not.toHaveBeenCalled()
    expect(sso.getSsoSession()).toBeNull()
  })

  it('reports whether local SSO was enabled at build time', () => {
    // Arrange / Act
    const enabled = sso.isSsoEnabled()

    // Assert
    expect(enabled).toBe(true)
  })
})
