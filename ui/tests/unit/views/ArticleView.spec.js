import { mount } from '@vue/test-utils'
import flushPromises from 'flush-promises'
import ArticleView from '@/views/ArticleView.vue'
import { fetchArticles, fetchArticleBySlug, buildArticleUrl } from '@/services/articlesService'
import { makeArticle } from '../factories/articles'

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
  fetchArticleBySlug: jest.fn(),
  buildArticleUrl: jest.fn(() => 'https://example.com/article')
}))

const RouterLinkStub = {
  name: 'RouterLinkStub',
  props: ['to'],
  template: '<a class="router-link-stub"><slot /></a>'
}

function mountView(slug) {
  return mount(ArticleView, {
    mocks: {
      $route: {
        params: { slug }
      }
    },
    stubs: {
      RouterLink: RouterLinkStub
    }
  })
}

describe('ArticleView', () => {
  it('loads article by slug and renders content', async () => {
    const article = makeArticle({ slug: 'my-article' })
    fetchArticleBySlug.mockResolvedValue(article)

    const wrapper = mountView('my-article')
    await flushPromises()

    expect(fetchArticleBySlug).toHaveBeenCalledWith('my-article')
    expect(wrapper.find('.post__entry').exists()).toBe(true)
    expect(buildArticleUrl).toHaveBeenCalledWith('my-article')
    expect(wrapper.vm.shareLinks.length).toBe(3)
  })

  it('falls back to first article when slug is missing', async () => {
    const articleList = [makeArticle({ slug: 'first' })]
    fetchArticleBySlug.mockResolvedValue(null)
    fetchArticles.mockResolvedValue(articleList)

    const wrapper = mountView(undefined)
    await flushPromises()

    expect(fetchArticles).toHaveBeenCalled()
    expect(wrapper.vm.article.title).toBe(articleList[0].title)
  })

  it('shows not found state when no article exists', async () => {
    fetchArticleBySlug.mockResolvedValue(undefined)
    const wrapper = mountView('missing')
    await flushPromises()

    expect(wrapper.text()).toContain('Artigo não encontrado')
  })

  it('shows error state when request fails', async () => {
    fetchArticleBySlug.mockRejectedValue(new Error('boom'))
    const wrapper = mountView('error-slug')
    await flushPromises()

    expect(wrapper.text()).toContain('Não foi possível carregar o artigo')
  })

  it('reloads article when slug changes', async () => {
    fetchArticleBySlug.mockResolvedValue(makeArticle({ slug: 'initial' }))
    const wrapper = mountView('initial')
    await flushPromises()

    fetchArticleBySlug.mockResolvedValue(makeArticle({ slug: 'second' }))
    wrapper.vm.$route.params.slug = 'second'
    await wrapper.vm.$options.watch['$route.params.slug'].call(wrapper.vm)
    await flushPromises()

    expect(fetchArticleBySlug).toHaveBeenCalledWith('second')
  })
})

