import { mount as vueMount } from '@vue/test-utils'

export function mount(component, options = {}) {
  const { mocks = {}, stubs = {}, ...mountOptions } = options
  return vueMount(component, {
    ...mountOptions,
    global: {
      ...mountOptions.global,
      mocks: { ...mountOptions.global?.mocks, ...mocks },
      stubs: { ...mountOptions.global?.stubs, ...stubs }
    }
  })
}
