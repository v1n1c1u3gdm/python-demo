import { h } from 'vue'
import { describe, expect, it, vi } from 'vitest'
import flushPromises from 'flush-promises'
import { createApplication } from '@/main'

describe('Vue application startup', () => {
  it('mounts a Vue 3 component and installs the router plugin', () => {
    // Arrange
    const routerPlugin = { install: vi.fn() }
    const component = { render: () => h('p', 'Mounted') }
    const app = createApplication(component, routerPlugin)
    const target = document.createElement('div')

    // Act
    app.mount(target)

    // Assert
    expect(routerPlugin.install).toHaveBeenCalledWith(app)
    expect(target.textContent).toBe('Mounted')
    app.unmount()
  })

  it('mounts the application entry when the HTML root exists', async () => {
    // Arrange
    document.body.innerHTML = '<div id="app"></div>'
    global.fetch.mockResolvedValue({ ok: true, json: async () => [] })
    vi.resetModules()

    // Act
    await import('@/main')
    await flushPromises()

    // Assert
    expect(document.querySelector('#app .layout-wrapper')).not.toBeNull()
  })
})
