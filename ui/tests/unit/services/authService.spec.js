import * as authServiceModule from '@/services/authService'

const { login, fetchAdminProfile, persistSession, getStoredSession, clearSession, runtimeGuards } =
  authServiceModule

const mockResponse = (ok, data, status = 200, statusText = 'OK') => ({
  ok,
  status,
  statusText,
  json: jest.fn().mockResolvedValue(data),
  clone() {
    return {
      ok,
      status,
      statusText,
      json: jest.fn().mockResolvedValue(data)
    }
  }
})

describe('authService', () => {
  beforeEach(() => {
    jest.clearAllMocks()
    global.fetch = jest.fn()
    window.sessionStorage.clear()
  })

  it('skips storage interactions when window is undefined', () => {
    const spy = jest.spyOn(runtimeGuards, 'hasWindow').mockReturnValue(false)

    expect(persistSession({ username: 'ghost' })).toBeUndefined()
    expect(getStoredSession()).toBeNull()
    expect(clearSession()).toBeUndefined()

    spy.mockRestore()
  })

  it('normalizes the API base URL from environment variables', () => {
    const originalValue = process.env.VUE_APP_API_BASE_URL

    process.env.VUE_APP_API_BASE_URL = ''
    jest.isolateModules(() => {
      const { getApiBaseUrl } = require('@/services/authService')
      expect(getApiBaseUrl()).toBe('http://localhost:3000')
    })

    process.env.VUE_APP_API_BASE_URL = 'http://localhost:4100///'
    jest.isolateModules(() => {
      const { getApiBaseUrl } = require('@/services/authService')
      expect(getApiBaseUrl()).toBe('http://localhost:4100')
    })

    process.env.VUE_APP_API_BASE_URL = originalValue
  })

  it('performs login via POST and returns payload', async () => {
    const payload = { access_token: 'token', roles: ['admin'] }
    global.fetch.mockResolvedValue(mockResponse(true, payload))

    const response = await login('admin', 'secret')

    expect(global.fetch).toHaveBeenCalledWith(
      'http://localhost:3000/login',
      expect.objectContaining({
        method: 'POST',
        body: JSON.stringify({ username: 'admin', password: 'secret' })
      })
    )
    expect(response).toEqual(payload)
  })

  it('translates failed login responses into errors', async () => {
    global.fetch.mockResolvedValue(mockResponse(false, { errors: ['Invalid'] }, 401, 'Unauthorized'))

    await expect(login('admin', 'bad')).rejects.toThrow('Invalid')
  })

  it('fetches the protected admin profile with bearer token', async () => {
    const profile = { username: 'admin' }
    global.fetch.mockResolvedValue(mockResponse(true, profile))

    const response = await fetchAdminProfile('token')

    expect(global.fetch).toHaveBeenCalledWith(
      'http://localhost:3000/admin/profile',
      expect.objectContaining({
        headers: expect.objectContaining({
          Authorization: 'Bearer token'
        })
      })
    )
    expect(response).toEqual(profile)
  })

  it('surfaces message errors from the API', async () => {
    global.fetch.mockResolvedValue(mockResponse(false, { message: 'Ops' }, 500, 'Server Error'))

    await expect(fetchAdminProfile('token')).rejects.toThrow('Ops')
  })

  it('persists and clears the session in storage', () => {
    const session = { username: 'admin', access_token: 'token' }

    persistSession(session)

    expect(getStoredSession()).toEqual(session)

    clearSession()
    expect(getStoredSession()).toBeNull()
  })

  it('clears corrupted session payloads before returning null', () => {
    window.sessionStorage.setItem('python-demo-admin-session', '{invalid')

    expect(getStoredSession()).toBeNull()
    expect(window.sessionStorage.getItem('python-demo-admin-session')).toBeNull()
  })

  it('returns null when no session is stored', () => {
    expect(getStoredSession()).toBeNull()
  })

  it('ignores profile fetches without a token', async () => {
    await expect(fetchAdminProfile()).rejects.toThrow('Token não informado.')
  })
})

