import { mount } from '@vue/test-utils'
import flushPromises from 'flush-promises'
import SocialsView from '@/views/SocialsView.vue'
import { fetchSocials } from '@/services/socialsService'
import { makeSocial } from '../factories/socials'

jest.mock('@/services/socialsService', () => ({
  fetchSocials: jest.fn()
}))

describe('SocialsView', () => {
  it('shows loading state before data resolves', () => {
    fetchSocials.mockReturnValue(new Promise(() => {}))
    const wrapper = mount(SocialsView)
    expect(wrapper.text()).toContain('Carregando redes')
  })

  it('renders social icons with mapped variants', async () => {
    fetchSocials.mockResolvedValue([
      makeSocial({ slug: 'twitter', profile_link: 'https://twitter.com' }),
      makeSocial({ slug: 'custom', profile_link: 'https://example.com/custom' })
    ])

    const wrapper = mount(SocialsView)
    await flushPromises()

    const icons = wrapper.findAll('.social-icon')
    expect(icons.length).toBe(2)
    expect(wrapper.find('.social-icon.twitter').exists()).toBe(true)
    expect(wrapper.find('.social-icon.default').exists()).toBe(true)
  })

  it('shows error message when request fails', async () => {
    fetchSocials.mockRejectedValue(new Error('Falha ao carregar'))
    const wrapper = mount(SocialsView)

    await flushPromises()
    expect(wrapper.text()).toContain('Falha ao carregar redes sociais')
  })

  it('shows empty state when no socials are available', async () => {
    fetchSocials.mockResolvedValue([])
    const wrapper = mount(SocialsView)
    await flushPromises()

    expect(wrapper.text()).toContain('Nenhuma rede social cadastrada')
  })
})

