import { describe, expect, it } from 'vitest'
import { routes } from '@/router'

describe('application routes', () => {
  it('keeps the public and admin route contracts', () => {
    // Arrange
    const configuredPaths = routes.map(route => route.path)

    // Act
    const hasOptionalArticleSlug = configuredPaths.includes('/articles/:slug?')

    // Assert
    expect(configuredPaths).toEqual(['/', '/articles/:slug?', '/about', '/admin'])
    expect(hasOptionalArticleSlug).toBe(true)
  })
})
