import { mount } from '@vue/test-utils'
import flushPromises from 'flush-promises'
import HomeView from '@/views/HomeView.vue'
import { fetchArticles, buildArticleUrl } from '@/services/articlesService'
import { makeArticles } from '../factories/articles'

jest.mock('@/components/SiteLayout.vue', () => ({
  name: 'SiteLayout',
  render(h) {
    const left = this.$scopedSlots['main-left'] ? this.$scopedSlots['main-left']() : null
    const right = this.$scopedSlots['main-right'] ? this.$scopedSlots['main-right']() : null
    return h('div', { class: 'site-layout-stub' }, [left, right])
  }
}))

jest.mock('@/services/articlesService', () => ({
  fetchArticles: jest.fn(),
  buildArticleUrl: jest.fn(slug => `https://example.com/${slug}`)
}))

const RouterLinkStub = {
  name: 'RouterLinkStub',
  props: ['to'],
  template: '<a class="router-link-stub"><slot /></a>'
}

function mountView() {
  return mount(HomeView, {
    mocks: {
      $route: { name: 'home' }
    },
    stubs: {
      RouterLink: RouterLinkStub
    }
  })
}

describe('HomeView', () => {
  it('renders fetched posts and paginates results', async () => {
    fetchArticles.mockResolvedValue(
      makeArticles(15, index => ({
        slug: `post-${index}`,
        post_entry: `Conteúdo ${index}`
      }))
    )

    const wrapper = mountView()
    await flushPromises()

    expect(wrapper.findAll('.feed__item').length).toBe(wrapper.vm.perPage)
    expect(wrapper.find('.pagination__info').text()).toContain('Página 1 de 2')

    const nextButton = wrapper.findAll('.btn--icon').at(1)
    await nextButton.trigger('click')
    await wrapper.vm.$nextTick()

    expect(wrapper.vm.currentPage).toBe(2)
    expect(wrapper.findAll('.feed__item').length).toBe(5)
  })

  it('shows empty state when there are no articles', async () => {
    fetchArticles.mockResolvedValue([])
    const wrapper = mountView()
    await flushPromises()

    expect(wrapper.text()).toContain('Nenhum artigo encontrado.')
  })

  it('shows error state when fetch fails', async () => {
    fetchArticles.mockRejectedValue(new Error('boom'))
    const wrapper = mountView()
    await flushPromises()

    expect(wrapper.text()).toContain('Não foi possível carregar os artigos')
  })

  it('truncates long excerpts and builds URLs', async () => {
    const [article] = makeArticles(1, () => ({
      slug: 'custom',
      post_entry: 'A'.repeat(400),
      published_label: null
    }))
    fetchArticles.mockResolvedValue([article])

    const wrapper = mountView()
    await flushPromises()

    expect(buildArticleUrl).toHaveBeenCalledWith('custom')
    expect(wrapper.find('.feed__item p').text().endsWith('…')).toBe(true)
    expect(wrapper.find('.feed__item time').exists()).toBe(true)
  })
})

