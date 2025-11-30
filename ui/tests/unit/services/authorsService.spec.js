import {
  fetchAuthors,
  fetchArticlesCountByAuthor,
  clearAuthorsCache,
  clearCountsCache
} from '@/services/authorsService'
import { makeAuthors } from '../factories/authors'
import { createDeferred, createFetchResponse, mockFetchError, mockFetchResponse } from '../mocks/fetchMock'

describe('authorsService', () => {
  afterEach(() => {
    clearAuthorsCache()
    clearCountsCache()
  })

  it('caches authors between calls', async () => {
    const authors = makeAuthors(2)
    mockFetchResponse(authors)

    const first = await fetchAuthors()
    const second = await fetchAuthors()

    expect(first).toBe(second)
    expect(global.fetch).toHaveBeenCalledTimes(1)
  })

  it('caches counts between calls', async () => {
    const counts = [{ author_id: 1, articles_count: 3 }]
    mockFetchResponse(counts)

    const first = await fetchArticlesCountByAuthor()
    const second = await fetchArticlesCountByAuthor()

    expect(first).toBe(second)
    expect(global.fetch).toHaveBeenCalledTimes(1)
  })

  it('allows forcing fetch even with cache', async () => {
    const authors = makeAuthors(1)
    mockFetchResponse(authors)
    await fetchAuthors()

    const updated = makeAuthors(1, index => ({ name: `Novo ${index}` }))
    mockFetchResponse(updated)
    const result = await fetchAuthors({ force: true })

    expect(result).toEqual(updated)
    expect(global.fetch).toHaveBeenCalledTimes(2)
  })

  it('shares inflight requests for counts', async () => {
    const deferred = createDeferred()
    global.fetch.mockReturnValueOnce(deferred.promise)

    const promiseA = fetchArticlesCountByAuthor()
    const promiseB = fetchArticlesCountByAuthor()
    expect(global.fetch).toHaveBeenCalledTimes(1)

    const payload = [{ author_id: 1, articles_count: 5 }]
    deferred.resolve(createFetchResponse({ jsonData: payload }))

    const [a, b] = await Promise.all([promiseA, promiseB])
    expect(a).toEqual(payload)
    expect(b).toEqual(payload)
  })

  it('propagates API errors with detail', async () => {
    mockFetchError({ status: 500, statusText: 'Server exploded' })
    await expect(fetchAuthors()).rejects.toThrow('Erro ao consultar autores')
  })
})

