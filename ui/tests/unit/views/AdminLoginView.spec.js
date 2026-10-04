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

const RouterLinkStub = {
  name: 'RouterLinkStub',
  props: ['to'],
  template: '<a class="router-link-stub"><slot /></a>'
}

describe('AdminLoginView', () => {
  beforeEach(() => {
    vi.clearAllMocks()
    getStoredSession.mockReturnValue(null)
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
})
