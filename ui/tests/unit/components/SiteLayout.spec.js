import { describe, expect, it, vi } from 'vitest'
import { mount } from '../../support/mount.js'
import SiteLayout from '@/components/SiteLayout.vue'

vi.mock('@/views/SocialsView.vue', () => ({
  default: {
    name: 'SocialsView',
    template: '<div class="socials-stub">Socials</div>'
  }
}))

const RouterLinkStub = {
  name: 'RouterLinkStub',
  props: ['to'],
  template: '<a class="router-link" @click="$emit(\'click\', $event)"><slot /></a>'
}

function mountComponent(options = {}) {
  return mount(SiteLayout, {
    stubs: {
      RouterLink: RouterLinkStub
    },
    mocks: {
      $route: { name: 'home' }
    },
    slots: {
      'main-left': '<div class="left-slot">left</div>',
      'main-right': '<div class="right-slot">right</div>'
    },
    ...options
  })
}

describe('SiteLayout', () => {
  it('renders navigation links and marks active route', () => {
    const wrapper = mountComponent()
    const navItems = wrapper.findAll('.navbar__menu li')

    expect(navItems.length).toBe(wrapper.vm.navLinks.length)
    expect(navItems[0].classes()).toContain('active')

    wrapper.unmount()
  })

  it('toggles menu visibility and body scroll state', async () => {
    const wrapper = mountComponent()

    const toggle = wrapper.find('.menu-toggle')
    await toggle.trigger('click')
    expect(wrapper.classes()).toContain('is-menu')
    expect(document.body.classList.contains('no-scroll')).toBe(true)

    await wrapper.find('.router-link').trigger('click')
    expect(wrapper.classes()).not.toContain('is-menu')
    expect(document.body.classList.contains('no-scroll')).toBe(false)

    wrapper.unmount()
  })
})
