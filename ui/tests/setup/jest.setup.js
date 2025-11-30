import { clearArticlesCache } from '@/services/articlesService'
import { clearAuthorsCache, clearCountsCache } from '@/services/authorsService'
import { clearSocialsCache } from '@/services/socialsService'

function ensureMatchMedia() {
  if (typeof window.matchMedia === 'function') {
    return
  }

  window.matchMedia = query => ({
    matches: false,
    media: query,
    onchange: null,
    addListener: jest.fn(),
    removeListener: jest.fn(),
    addEventListener: jest.fn(),
    removeEventListener: jest.fn(),
    dispatchEvent: jest.fn()
  })
}

function ensureIntersectionObserver() {
  if (typeof window.IntersectionObserver === 'function') {
    return
  }

  window.IntersectionObserver = class {
    constructor() {}
    observe() {}
    unobserve() {}
    disconnect() {}
  }
}

beforeEach(() => {
  global.fetch = jest.fn()
  ensureMatchMedia()
  ensureIntersectionObserver()
  jest.clearAllMocks()

  clearArticlesCache()
  clearAuthorsCache()
  clearCountsCache()
  clearSocialsCache()
})

