import { describe, expect, it } from 'vitest'
import { loadEnv } from 'vite'

describe('production build environment', () => {
  it('uses a browser-accessible API default', () => {
    // Arrange
    const productionEnvironment = loadEnv('production', process.cwd(), 'VITE_')

    // Act
    const apiBaseUrl = productionEnvironment.VITE_API_BASE_URL

    // Assert
    expect(apiBaseUrl).toBe('http://localhost:3000')
  })
})
