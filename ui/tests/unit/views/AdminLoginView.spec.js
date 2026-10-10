import { beforeEach, describe, expect, it, vi } from 'vitest'
import { mount } from '../../support/mount.js'
import flushPromises from 'flush-promises'

import AdminLoginView from '@/views/AdminLoginView.vue'
import {
  login,
  persistSession,
  getStoredSession,
  fetchAdminProfile,
  clearSession
} from '@/services/authService'
import { fetchArticles, updateArticleSanitizationBypass } from '@/services/articlesService'
import {
  initializeSso,
  isSsoEnabled,
  loginWithSso,
  logoutFromSso,
  refreshSsoSession,
  subscribeSsoSession
} from '@/services/ssoService'
import { makeArticles } from '../factories/articles'

vi.mock('@/components/SiteLayout.vue', () => ({
  default: {
    name: 'SiteLayout',
    template: '<div class="site-layout-stub"><slot name="main-left"/><slot name="main-right"/></div>'
  }
}))

vi.mock('@/services/authService', () => ({
  login: vi.fn(),
  persistSession: vi.fn(),
  getStoredSession: vi.fn(),
  fetchAdminProfile: vi.fn(),
  clearSession: vi.fn()
}))

vi.mock('@/services/articlesService', () => ({
  fetchArticles: vi.fn(),
  updateArticleSanitizationBypass: vi.fn()
}))

vi.mock('@/services/ssoService', () => ({
  initializeSso: vi.fn(),
  isSsoEnabled: vi.fn(() => false),
  loginWithSso: vi.fn(),
  logoutFromSso: vi.fn(),
  refreshSsoSession: vi.fn(),
  subscribeSsoSession: vi.fn()
}))

const RouterLinkStub = {
  name: 'RouterLinkStub',
  props: ['to'],
  template: '<a class="router-link-stub"><slot /></a>'
}

describe('AdminLoginView', () => {
  let sessionListener
  let unsubscribeSession

  beforeEach(() => {
    vi.clearAllMocks()
    getStoredSession.mockReturnValue(null)
    fetchArticles.mockResolvedValue([])
    isSsoEnabled.mockReturnValue(false)
    initializeSso.mockResolvedValue(null)
    loginWithSso.mockResolvedValue(undefined)
    logoutFromSso.mockResolvedValue(undefined)
    refreshSsoSession.mockResolvedValue(null)
    sessionListener = null
    unsubscribeSession = vi.fn()
    subscribeSsoSession.mockImplementation(listener => {
      sessionListener = listener
      return unsubscribeSession
    })
  })

  function mountView() {
    return mount(AdminLoginView, {
      stubs: {
        RouterLink: RouterLinkStub
      },
      mocks: {
        $route: { name: 'admin' }
      }
    })
  }

  it('shows Keycloak login without reading or persisting a legacy session in SSO mode', async () => {
    // Arrange
    isSsoEnabled.mockReturnValue(true)
    initializeSso.mockResolvedValue(null)

    // Act
    const wrapper = mountView()
    await flushPromises()

    // Assert
    expect(wrapper.text()).toContain('Entrar com Keycloak')
    expect(wrapper.find('form').exists()).toBe(false)
    expect(getStoredSession).not.toHaveBeenCalled()
    expect(persistSession).not.toHaveBeenCalled()
    await wrapper.find('button').trigger('click')
    expect(loginWithSso).toHaveBeenCalledOnce()
  })

  it('shows a rejected SSO callback error without reading a legacy session', async () => {
    // Arrange
    isSsoEnabled.mockReturnValue(true)
    initializeSso.mockRejectedValue(new Error('callback inválido'))

    // Act
    const wrapper = mountView()
    await flushPromises()

    // Assert
    expect(wrapper.find('.admin-login__error').text()).toContain('callback inválido')
    expect(getStoredSession).not.toHaveBeenCalled()
  })

  it('shows the Keycloak logout error after clearing local admin state', async () => {
    // Arrange
    const session = { username: 'admin', access_token: 'sso-token', roles: ['admin'], sso: true }
    isSsoEnabled.mockReturnValue(true)
    initializeSso.mockResolvedValue(session)
    logoutFromSso.mockRejectedValue(new Error('logout indisponível'))
    fetchArticles.mockResolvedValue(makeArticles(1))
    const wrapper = mountView()
    await flushPromises()

    // Act
    await wrapper.vm.handleLogout()
    await flushPromises()

    // Assert
    expect(wrapper.vm.session).toBeNull()
    expect(wrapper.find('.admin-login__session').exists()).toBe(false)
    expect(wrapper.find('.admin-login__error').text()).toContain('logout indisponível')
  })

  it('reuses an authenticated callback session and loads its admin content', async () => {
    // Arrange
    const session = { username: 'admin', access_token: 'sso-token', roles: ['admin'], sso: true }
    isSsoEnabled.mockReturnValue(true)
    initializeSso.mockResolvedValue(session)
    const [article] = makeArticles(1, () => ({ id: 60, bypass_sanitization: false }))
    fetchArticles.mockResolvedValue([article])

    // Act
    const wrapper = mountView()
    await flushPromises()

    // Assert
    expect(wrapper.text()).toContain('Autenticado como admin')
    expect(wrapper.find('form').exists()).toBe(false)
    expect(getStoredSession).not.toHaveBeenCalled()
    expect(fetchArticles).toHaveBeenCalledOnce()
    expect(wrapper.find('select[aria-label="Artigo existente"]').exists()).toBe(true)
  })

  it('uses the refreshed SSO token when saving article bypass', async () => {
    // Arrange
    const [article] = makeArticles(1, () => ({ id: 61, bypass_sanitization: false }))
    const initialSession = { username: 'admin', access_token: 'old-token', roles: ['admin'], sso: true }
    const refreshedSession = { ...initialSession, access_token: 'new-token' }
    isSsoEnabled.mockReturnValue(true)
    initializeSso.mockResolvedValue(initialSession)
    refreshSsoSession.mockResolvedValue(refreshedSession)
    fetchArticles.mockResolvedValue([article])
    updateArticleSanitizationBypass.mockResolvedValue({ ...article, bypass_sanitization: true })

    // Act
    const wrapper = mountView()
    await flushPromises()
    await wrapper.find('select[aria-label="Artigo existente"]').setValue('61')
    await wrapper.find('input[type="checkbox"]').setValue(true)
    await wrapper.find('button[aria-label="Salvar bypass de sanitização"]').trigger('click')
    await flushPromises()

    // Assert
    expect(refreshSsoSession).toHaveBeenCalledOnce()
    expect(updateArticleSanitizationBypass).toHaveBeenCalledWith(61, true, 'new-token')
    expect(wrapper.text()).toContain('Bypass atualizado.')
  })

  it('clears the view session when SSO refresh fails before an article update', async () => {
    // Arrange
    const [article] = makeArticles(1, () => ({ id: 62, bypass_sanitization: false }))
    isSsoEnabled.mockReturnValue(true)
    initializeSso.mockResolvedValue({ username: 'admin', access_token: 'old-token', roles: ['admin'], sso: true })
    refreshSsoSession.mockRejectedValue(new Error('sessão expirada'))
    fetchArticles.mockResolvedValue([article])

    // Act
    const wrapper = mountView()
    await flushPromises()
    await wrapper.find('select[aria-label="Artigo existente"]').setValue('62')
    await wrapper.find('input[type="checkbox"]').setValue(true)
    await wrapper.find('button[aria-label="Salvar bypass de sanitização"]').trigger('click')
    await flushPromises()

    // Assert
    expect(updateArticleSanitizationBypass).not.toHaveBeenCalled()
    expect(wrapper.find('.admin-login__session').exists()).toBe(false)
    expect(wrapper.find('select[aria-label="Artigo existente"]').exists()).toBe(false)
    expect(wrapper.find('.admin-login__error').text()).toContain('sessão expirada')
  })

  it('invalidates an SSO callback that resolves after logout', async () => {
    // Arrange
    let resolveInitialization
    isSsoEnabled.mockReturnValue(true)
    initializeSso.mockReturnValue(new Promise(resolve => {
      resolveInitialization = resolve
    }))
    const wrapper = mountView()

    // Act
    await wrapper.vm.handleLogout()
    resolveInitialization({ username: 'admin', access_token: 'late-token', roles: ['admin'], sso: true })
    await flushPromises()

    // Assert
    expect(logoutFromSso).toHaveBeenCalledOnce()
    expect(wrapper.find('.admin-login__session').exists()).toBe(false)
    expect(fetchArticles).not.toHaveBeenCalled()
  })

  it('clears an idle SSO view when renewal fails and unsubscribes on unmount', async () => {
    // Arrange
    isSsoEnabled.mockReturnValue(true)
    initializeSso.mockResolvedValue({ username: 'admin', access_token: 'expiring-token', roles: ['admin'], sso: true })
    const wrapper = mountView()
    await flushPromises()

    // Act
    expect(subscribeSsoSession).toHaveBeenCalledOnce()
    expect(wrapper.vm.session?.sso).toBe(true)
    sessionListener({ session: null, error: new Error('sessão expirada') })
    await flushPromises()

    // Assert
    expect(wrapper.vm.session).toBeNull()
    expect(wrapper.find('.admin-login__session').exists()).toBe(false)
    expect(wrapper.find('select[aria-label="Artigo existente"]').exists()).toBe(false)
    expect(wrapper.find('.admin-login__error').text()).toContain('sessão expirada')
    wrapper.unmount()
    expect(unsubscribeSession).toHaveBeenCalledOnce()
  })

  it.each(['profile refresh first', 'article write first'])(
    'keeps profile and article state coherent when %s resolves first',
    async order => {
      // Arrange
      const [article] = makeArticles(1, () => ({ id: 63, bypass_sanitization: false }))
      const currentSession = { username: 'admin', access_token: 'current-token', roles: ['admin'], sso: true }
      const renewedSession = { ...currentSession, access_token: 'renewed-token' }
      isSsoEnabled.mockReturnValue(true)
      let resolveProfileRefresh
      let resolveWriteRefresh
      refreshSsoSession
        .mockReturnValueOnce(new Promise(resolve => { resolveProfileRefresh = resolve }))
        .mockReturnValueOnce(new Promise(resolve => { resolveWriteRefresh = resolve }))
      initializeSso.mockResolvedValue(currentSession)
      fetchArticles.mockResolvedValue([article])
      let resolveWrite
      updateArticleSanitizationBypass.mockReturnValue(new Promise(resolve => { resolveWrite = resolve }))

      // Act
      const wrapper = mountView()
      await flushPromises()
      const profileButton = wrapper.findAll('button').find(button => button.text().includes('Atualizar perfil'))
      await profileButton.trigger('click')
      await wrapper.find('select[aria-label="Artigo existente"]').setValue('63')
      await wrapper.find('input[type="checkbox"]').setValue(true)
      await wrapper.find('button[aria-label="Salvar bypass de sanitização"]').trigger('click')
      if (order === 'profile refresh first') resolveProfileRefresh(renewedSession)
      else resolveWriteRefresh(renewedSession)
      await flushPromises()

      const pendingRefresh = order === 'profile refresh first' ? resolveWriteRefresh : resolveProfileRefresh
      if (order === 'profile refresh first') {
        expect(wrapper.vm.isProfileLoading).toBe(false)
        expect(wrapper.vm.isBypassSaving).toBe(true)
      } else {
        expect(wrapper.vm.isProfileLoading).toBe(true)
        expect(wrapper.vm.isBypassSaving).toBe(true)
      }
      pendingRefresh(renewedSession)
      await flushPromises()
      expect(updateArticleSanitizationBypass).toHaveBeenCalledWith(63, true, 'renewed-token')
      resolveWrite({ ...article, bypass_sanitization: true })
      await flushPromises()

      // Assert
      expect(wrapper.vm.isProfileLoading).toBe(false)
      expect(wrapper.vm.isBypassSaving).toBe(false)
      expect(wrapper.vm.session.access_token).toBe('renewed-token')
      expect(wrapper.text()).toContain('Bypass atualizado.')
    }
  )

  it('submits credentials and persists the session', async () => {
    login.mockResolvedValue({ username: 'admin', access_token: 'token', roles: ['admin'] })
    fetchAdminProfile.mockResolvedValue({ username: 'admin', roles: ['admin'] })

    const wrapper = mountView()

    await wrapper.find('#admin-user').setValue('admin')
    await wrapper.find('#admin-password').setValue('secret')
    await wrapper.find('form').trigger('submit.prevent')
    await flushPromises()

    expect(login).toHaveBeenCalledWith('admin', 'secret')
    expect(persistSession).toHaveBeenCalled()
    expect(fetchAdminProfile).toHaveBeenCalledWith('token')
    expect(wrapper.find('.admin-login__session').exists()).toBe(true)
  })

  it('loads stored session and profile on mount', async () => {
    getStoredSession.mockReturnValue({ username: 'stored', access_token: 'token', roles: ['admin'] })
    fetchAdminProfile.mockResolvedValue({ username: 'stored', roles: ['admin'] })

    const wrapper = mountView()
    await flushPromises()

    expect(fetchAdminProfile).toHaveBeenCalledWith('token')
    expect(wrapper.text()).toContain('stored')
  })

  it('shows error feedback when login fails', async () => {
    login.mockRejectedValue(new Error('boom'))

    const wrapper = mountView()
    await wrapper.find('#admin-user').setValue('admin')
    await wrapper.find('#admin-password').setValue('bad')
    await wrapper.find('form').trigger('submit.prevent')
    await flushPromises()

    expect(wrapper.find('.admin-login__error').text()).toContain('boom')
    expect(clearSession).not.toHaveBeenCalled()
  })

  it('clears the session when the user logs out', async () => {
    getStoredSession.mockReturnValue({ username: 'stored', access_token: 'token', roles: ['admin'] })
    fetchAdminProfile.mockResolvedValue({ username: 'stored', roles: ['admin'] })

    const wrapper = mountView()
    await flushPromises()

    const logoutButton = wrapper.find('.btn--link')
    await logoutButton.trigger('click')

    expect(clearSession).toHaveBeenCalled()
    expect(wrapper.find('form').exists()).toBe(true)
  })

  it('shows API errors when profile fetch fails', async () => {
    getStoredSession.mockReturnValue({ username: 'stored', access_token: 'token', roles: ['admin'] })
    fetchAdminProfile.mockRejectedValue(new Error('perfil indisponível'))

    const wrapper = mountView()
    await flushPromises()

    expect(wrapper.find('.admin-login__error').text()).toContain('perfil indisponível')
  })

  it('lets an admin load an article, toggle bypass, and save the server update', async () => {
    // Arrange
    const [article] = makeArticles(1, () => ({ id: 21, title: 'Raw HTML', bypass_sanitization: false }))
    fetchArticles.mockResolvedValue([article])
    fetchAdminProfile.mockResolvedValue({ username: 'stored', roles: ['admin'] })
    getStoredSession.mockReturnValue({ username: 'stored', access_token: 'admin-token', roles: ['admin'] })
    const updatedArticle = { ...article, bypass_sanitization: true }
    updateArticleSanitizationBypass.mockResolvedValue(updatedArticle)

    // Act
    const wrapper = mountView()
    await flushPromises()
    const articleSelect = wrapper.find('select[aria-label="Artigo existente"]')
    expect(articleSelect.exists()).toBe(true)
    await articleSelect.setValue('21')
    const checkbox = wrapper.find('input[type="checkbox"]')
    await checkbox.setValue(true)
    await wrapper.find('button[aria-label="Salvar bypass de sanitização"]').trigger('click')
    await flushPromises()

    // Assert
    expect(fetchArticles).toHaveBeenCalledOnce()
    expect(updateArticleSanitizationBypass).toHaveBeenCalledWith(21, true, 'admin-token')
    expect(wrapper.find('input[type="checkbox"]').element.checked).toBe(true)
    expect(wrapper.text()).toContain('Bypass atualizado.')
  })

  it('does not show bypass controls to an authenticated non-admin', async () => {
    // Arrange
    getStoredSession.mockReturnValue({ username: 'author', access_token: 'author-token', roles: ['author'] })
    fetchAdminProfile.mockResolvedValue({ username: 'author', roles: ['author'] })

    // Act
    const wrapper = mountView()
    await flushPromises()

    // Assert
    expect(wrapper.find('input[type="checkbox"]').exists()).toBe(false)
    expect(wrapper.find('select[aria-label="Artigo existente"]').exists()).toBe(false)
    expect(fetchArticles).not.toHaveBeenCalled()
  })

  it('restores the selected server value and shows an error when bypass save fails', async () => {
    // Arrange
    const [article] = makeArticles(1, () => ({ id: 22, bypass_sanitization: false }))
    getStoredSession.mockReturnValue({ username: 'stored', access_token: 'admin-token', roles: ['admin'] })
    fetchAdminProfile.mockResolvedValue({ username: 'stored', roles: ['admin'] })
    fetchArticles.mockResolvedValue([article])
    updateArticleSanitizationBypass.mockRejectedValue(new Error('Sem autorização'))

    // Act
    const wrapper = mountView()
    await flushPromises()
    const articleSelect = wrapper.find('select[aria-label="Artigo existente"]')
    expect(articleSelect.exists()).toBe(true)
    await articleSelect.setValue('22')
    await wrapper.find('input[type="checkbox"]').setValue(true)
    await wrapper.find('button[aria-label="Salvar bypass de sanitização"]').trigger('click')
    await flushPromises()

    // Assert
    expect(wrapper.find('input[type="checkbox"]').element.checked).toBe(false)
    expect(wrapper.find('.admin-login__error').text()).toContain('Sem autorização')
    expect(updateArticleSanitizationBypass).toHaveBeenCalledWith(22, true, 'admin-token')
  })

  it('reflects the server bypass value when another article is selected', async () => {
    // Arrange
    const articles = makeArticles(2, index => ({
      id: index + 30,
      title: `Article ${index}`,
      bypass_sanitization: index === 1
    }))
    getStoredSession.mockReturnValue({ username: 'stored', access_token: 'admin-token', roles: ['admin'] })
    fetchAdminProfile.mockResolvedValue({ username: 'stored', roles: ['admin'] })
    fetchArticles.mockResolvedValue(articles)

    // Act
    const wrapper = mountView()
    await flushPromises()
    const articleSelect = wrapper.find('select[aria-label="Artigo existente"]')
    expect(articleSelect.exists()).toBe(true)
    await articleSelect.setValue('31')

    // Assert
    expect(wrapper.find('input[type="checkbox"]').element.checked).toBe(true)
  })

  it('clears bypass controls and article state on logout', async () => {
    // Arrange
    const [article] = makeArticles(1, () => ({ id: 41, bypass_sanitization: true }))
    getStoredSession.mockReturnValue({ username: 'stored', access_token: 'admin-token', roles: ['admin'] })
    fetchAdminProfile.mockResolvedValue({ username: 'stored', roles: ['admin'] })
    fetchArticles.mockResolvedValue([article])
    const wrapper = mountView()
    await flushPromises()
    const articleSelect = wrapper.find('select[aria-label="Artigo existente"]')
    expect(articleSelect.exists()).toBe(true)
    await articleSelect.setValue('41')
    expect(wrapper.find('input[type="checkbox"]').element.checked).toBe(true)

    // Act
    await wrapper.find('.btn--link').trigger('click')

    // Assert
    expect(wrapper.find('select[aria-label="Artigo existente"]').exists()).toBe(false)
    expect(wrapper.find('input[type="checkbox"]').exists()).toBe(false)
  })

  it('locks article controls during a save and ignores a response after logout', async () => {
    // Arrange
    const [article] = makeArticles(1, () => ({ id: 51, bypass_sanitization: false }))
    getStoredSession.mockReturnValue({ username: 'stored', access_token: 'admin-token', roles: ['admin'] })
    fetchAdminProfile.mockResolvedValue({ username: 'stored', roles: ['admin'] })
    fetchArticles.mockResolvedValue([article])
    let resolveSave
    updateArticleSanitizationBypass.mockReturnValue(new Promise(resolve => {
      resolveSave = resolve
    }))

    // Act
    const wrapper = mountView()
    await flushPromises()
    await wrapper.find('select[aria-label="Artigo existente"]').setValue('51')
    await wrapper.find('input[type="checkbox"]').setValue(true)
    await wrapper.find('button[aria-label="Salvar bypass de sanitização"]').trigger('click')
    await flushPromises()

    // Assert
    expect(wrapper.find('select[aria-label="Artigo existente"]').element.disabled).toBe(true)
    expect(wrapper.find('input[type="checkbox"]').element.disabled).toBe(true)
    expect(wrapper.find('button[aria-label="Salvar bypass de sanitização"]').element.disabled).toBe(true)

    // Act
    await wrapper.find('.btn--link').trigger('click')
    resolveSave({ ...article, bypass_sanitization: true })
    await flushPromises()

    // Assert
    expect(wrapper.find('form').exists()).toBe(true)
    expect(wrapper.find('input[type="checkbox"]').exists()).toBe(false)
  })
})
