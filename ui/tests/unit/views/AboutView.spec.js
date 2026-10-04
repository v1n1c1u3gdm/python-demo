import { describe, expect, it, vi } from 'vitest'
import { mount } from '../../support/mount.js'
import flushPromises from 'flush-promises'
import AboutView from '@/views/AboutView.vue'
import { fetchAuthors, fetchArticlesCountByAuthor } from '@/services/authorsService'
import { makeAuthor } from '../factories/authors'

vi.mock('@/components/SiteLayout.vue', () => ({
  default: {
    name: 'SiteLayout',
    template: '<div class="site-layout-stub"><slot name="main-left"/><slot name="main-right"/></div>'
  }
}))

vi.mock('@/services/authorsService', () => ({
  fetchAuthors: vi.fn(),
  fetchArticlesCountByAuthor: vi.fn()
}))

const RouterLinkStub = {
  name: 'RouterLinkStub',
  props: ['to'],
  template: '<a class="router-link-stub"><slot /></a>'
}

function mountView() {
  return mount(AboutView, {
    mocks: {
      $route: { name: 'about' }
    },
    stubs: {
      RouterLink: RouterLinkStub
    }
  })
}

describe('AboutView', () => {
  it('renders author data when request succeeds', async () => {
    const author = makeAuthor({ id: 1, birthdate: '1995-08-10', articlesCount: undefined })
    fetchAuthors.mockResolvedValue([author])
    fetchArticlesCountByAuthor.mockResolvedValue([{ author_id: 1, articles_count: 2 }])

    const wrapper = mountView()
    await flushPromises()

    expect(fetchAuthors).toHaveBeenCalled()
    expect(wrapper.text()).toContain('2 artigos publicados')
    expect(wrapper.find('.post__entry p').exists()).toBe(true)
    expect(wrapper.text()).toContain('Chave pública')
  })

  it('shows empty state when no authors exist', async () => {
    fetchAuthors.mockResolvedValue([])
    fetchArticlesCountByAuthor.mockResolvedValue([])

    const wrapper = mountView()
    await flushPromises()

    expect(wrapper.text()).toContain('Nenhum autor disponível')
  })

  it('shows error message when request fails', async () => {
    fetchAuthors.mockRejectedValue(new Error('fail'))
    const wrapper = mountView()
    await flushPromises()

    expect(wrapper.text()).toContain('Não foi possível carregar os dados do autor')
  })
})
