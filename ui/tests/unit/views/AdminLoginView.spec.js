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

const RouterLinkStub = {
  name: 'RouterLinkStub',
  props: ['to'],
  template: '<a class="router-link-stub"><slot /></a>'
}

describe('AdminLoginView', () => {
  beforeEach(() => {
    vi.clearAllMocks()
    getStoredSession.mockReturnValue(null)
    fetchArticles.mockResolvedValue([])
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
