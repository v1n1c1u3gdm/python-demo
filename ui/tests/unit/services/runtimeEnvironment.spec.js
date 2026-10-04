import { afterEach, describe, expect, it, vi } from 'vitest'

describe('runtime environment configuration', () => {
  afterEach(() => {
    vi.unstubAllEnvs()
    vi.resetModules()
  })

  it('uses the public Vite API URL and normalizes trailing slashes', async () => {
    // Arrange
    vi.stubEnv('VITE_API_BASE_URL', 'https://api.example.test///')
    vi.resetModules()

    // Act
    const { getApiBaseUrl } = await import('../../../src/services/authService.js')

    // Assert
    expect(getApiBaseUrl()).toBe('https://api.example.test')
  })

  it('uses custom Vite endpoints for the content services', async () => {
    // Arrange
    vi.stubEnv('VITE_ARTICLES_URL', 'https://api.example.test/articles///')
    vi.stubEnv('VITE_AUTHORS_URL', 'https://api.example.test/authors///')
    vi.stubEnv('VITE_ARTICLES_COUNT_URL', 'https://api.example.test/counts///')
    vi.stubEnv('VITE_SOCIALS_URL', 'https://api.example.test/socials///')
    vi.resetModules()
    global.fetch.mockResolvedValue({ ok: true, json: async () => [] })

    // Act
    const articles = await import('@/services/articlesService')
    const authors = await import('@/services/authorsService')
    const socials = await import('@/services/socialsService')
    await articles.fetchArticles()
    await authors.fetchAuthors()
    await authors.fetchArticlesCountByAuthor()
    await socials.fetchSocials()

    // Assert
    expect(global.fetch).toHaveBeenNthCalledWith(1, 'https://api.example.test/articles', expect.any(Object))
    expect(global.fetch).toHaveBeenNthCalledWith(2, 'https://api.example.test/authors', expect.any(Object))
    expect(global.fetch).toHaveBeenNthCalledWith(3, 'https://api.example.test/counts', expect.any(Object))
    expect(global.fetch).toHaveBeenNthCalledWith(4, 'https://api.example.test/socials', expect.any(Object))
  })
})
