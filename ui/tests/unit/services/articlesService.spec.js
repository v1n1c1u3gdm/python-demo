import {
  fetchArticles,
  fetchArticleBySlug,
  buildArticleUrl,
  clearArticlesCache
} from '@/services/articlesService'
import { makeArticles } from '../factories/articles'
import { createFetchResponse, mockFetchError, mockFetchResponse, createDeferred } from '../mocks/fetchMock'

describe('articlesService', () => {
  afterEach(() => {
    clearArticlesCache()
  })

  it('caches results between calls', async () => {
    const articles = makeArticles(2)
    mockFetchResponse(articles)

    const first = await fetchArticles()
    const second = await fetchArticles()

    expect(first).toEqual(articles)
    expect(second).toBe(first)
    expect(global.fetch).toHaveBeenCalledTimes(1)
  })

  it('forces reload when requested', async () => {
    const initial = makeArticles(1)
    mockFetchResponse(initial)
    await fetchArticles()

    const forced = makeArticles(1, index => ({ slug: `forced-${index}` }))
    mockFetchResponse(forced)
    const result = await fetchArticles({ force: true })

    expect(result).toEqual(forced)
    expect(global.fetch).toHaveBeenCalledTimes(2)
  })

  it('reuses inflight request while fetching', async () => {
    const deferred = createDeferred()
    global.fetch.mockReturnValueOnce(deferred.promise)

    const firstPromise = fetchArticles()
    const secondPromise = fetchArticles()

    expect(global.fetch).toHaveBeenCalledTimes(1)

    const payload = makeArticles(1)
    deferred.resolve(createFetchResponse({ jsonData: payload }))

    const [first, second] = await Promise.all([firstPromise, secondPromise])
    expect(first).toEqual(payload)
    expect(second).toEqual(payload)
  })

  it('propagates fetch errors with detail', async () => {
    mockFetchError({ status: 502, statusText: 'Bad Gateway', body: { message: 'Falha' } })

    await expect(fetchArticles()).rejects.toThrow('Falha')
  })

  it('requires slug to fetch article by slug', async () => {
    await expect(fetchArticleBySlug()).rejects.toThrow('Slug é obrigatório')
  })

  it('returns matched article by slug', async () => {
    const [first] = makeArticles(1, () => ({ slug: 'unique-slug' }))
    mockFetchResponse([first])

    const article = await fetchArticleBySlug('unique-slug')
    expect(article).toEqual(first)
  })

  it('builds sanitized article URLs', () => {
    expect(buildArticleUrl(' /example/ ')).toMatch(/example/)
    expect(buildArticleUrl('')).toBeNull()
    expect(buildArticleUrl(null)).toBeNull()
  })

  it('normalizes public base URL coming from env', () => {
    const prev = process.env.VUE_APP_ARTICLE_PUBLIC_BASE_URL
    process.env.VUE_APP_ARTICLE_PUBLIC_BASE_URL = 'https://example.com/blog///'

    jest.isolateModules(() => {
      const { buildArticleUrl: isolatedBuildArticleUrl } = require('@/services/articlesService')
      expect(isolatedBuildArticleUrl('slug')).toBe('https://example.com/blog/slug/')
    })

    process.env.VUE_APP_ARTICLE_PUBLIC_BASE_URL = prev
  })
})

