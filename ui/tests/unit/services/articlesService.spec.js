import { afterEach, describe, expect, it, vi } from 'vitest'
import {
  fetchArticles,
  fetchArticleBySlug,
  buildArticleUrl,
  clearArticlesCache,
  updateArticleSanitizationBypass
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

  it('normalizes the public base URL from Vite environment settings', async () => {
    // Arrange
    vi.stubEnv('VITE_ARTICLE_PUBLIC_BASE_URL', 'https://example.com/blog///')
    vi.resetModules()

    // Act
    const { buildArticleUrl: buildUrlWithCustomEnvironment } = await import('@/services/articlesService')

    // Assert
    expect(buildUrlWithCustomEnvironment('slug')).toBe('https://example.com/blog/slug/')
  })

  it('patches only the sanitization bypass with the admin token and refreshes cached articles', async () => {
    // Arrange
    mockFetchResponse(makeArticles(1, () => ({ id: 14, bypass_sanitization: false })))
    await fetchArticles()
    const updatedArticle = makeArticles(1, () => ({ id: 14, bypass_sanitization: true }))[0]
    mockFetchResponse(updatedArticle)

    // Act
    const result = await updateArticleSanitizationBypass(14, true, 'admin-token')

    // Assert
    expect(global.fetch).toHaveBeenNthCalledWith(2, 'http://localhost:3000/articles/14', {
      headers: {
        Accept: 'application/json',
        'Content-Type': 'application/json',
        Authorization: 'Bearer admin-token'
      },
      method: 'PATCH',
      body: JSON.stringify({ article: { bypass_sanitization: true } })
    })
    expect(result).toEqual(updatedArticle)

    const refreshedArticles = makeArticles(1, () => ({ id: 14, bypass_sanitization: true }))
    mockFetchResponse(refreshedArticles)
    await expect(fetchArticles()).resolves.toEqual(refreshedArticles)
    expect(global.fetch).toHaveBeenCalledTimes(3)
  })

  it('keeps cached articles when bypass update fails', async () => {
    // Arrange
    const cachedArticles = makeArticles(1, () => ({ id: 14, bypass_sanitization: false }))
    mockFetchResponse(cachedArticles)
    await fetchArticles()
    mockFetchError({ status: 403, body: { message: 'Forbidden' } })

    // Act
    await expect(updateArticleSanitizationBypass(14, true, 'author-token')).rejects.toThrow('Forbidden')

    // Assert
    await expect(fetchArticles()).resolves.toBe(cachedArticles)
    expect(global.fetch).toHaveBeenCalledTimes(2)
  })

  it('does not let an older in-flight article read overwrite the cache after a bypass update', async () => {
    // Arrange
    const staleRead = createDeferred()
    global.fetch.mockReturnValueOnce(staleRead.promise)
    const oldRequest = fetchArticles()
    const updatedArticle = makeArticles(1, () => ({ id: 14, bypass_sanitization: true }))[0]
    mockFetchResponse(updatedArticle)
    await updateArticleSanitizationBypass(14, true, 'admin-token')
    const freshArticles = makeArticles(1, () => ({ id: 14, bypass_sanitization: true }))
    mockFetchResponse(freshArticles)
    await fetchArticles()

    // Act
    staleRead.resolve(createFetchResponse({
      jsonData: makeArticles(1, () => ({ id: 14, bypass_sanitization: false }))
    }))
    await oldRequest
    const cachedArticles = await fetchArticles()

    // Assert
    expect(cachedArticles).toEqual(freshArticles)
    expect(global.fetch).toHaveBeenCalledTimes(3)
  })
})
