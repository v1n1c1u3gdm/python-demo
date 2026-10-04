const DEFAULT_ARTICLES_URL = 'http://localhost:3000/articles'
const DEFAULT_ARTICLE_PUBLIC_BASE_URL = 'https://viniciusmenezes.com'

const ARTICLES_ENDPOINT = normalizeArticlesUrl(import.meta.env.VITE_ARTICLES_URL || DEFAULT_ARTICLES_URL)
const ARTICLE_PUBLIC_BASE_URL = normalizePublicBaseUrl(
  import.meta.env.VITE_ARTICLE_PUBLIC_BASE_URL || DEFAULT_ARTICLE_PUBLIC_BASE_URL
)

let articlesCache = null
let inflightRequest = null
let articlesCacheGeneration = 0

function normalizeArticlesUrl(url) {
  if (!url) return DEFAULT_ARTICLES_URL
  return url.replace(/\/+$/, '')
}

function normalizePublicBaseUrl(url) {
  if (!url) return DEFAULT_ARTICLE_PUBLIC_BASE_URL
  return url.replace(/\/+$/, '')
}

async function request(url, fetchOptions = {}) {
  const response = await fetch(url, {
    ...fetchOptions,
    headers: {
      Accept: 'application/json',
      ...(fetchOptions.headers || {})
    }
  })

  if (!response.ok) {
    let errorDetail = `${response.status} ${response.statusText}`
    try {
      const payload = await response.clone().json()
      if (payload?.errors) {
        errorDetail = payload.errors.join(', ')
      } else if (payload?.message) {
        errorDetail = payload.message
      }
    } catch {
      // noop: fallback to status text
    }

    throw new Error(`Erro ao consultar artigos: ${errorDetail}`)
  }

  return response.json()
}

export async function fetchArticles(options = { force: false }) {
  if (!options.force && articlesCache) {
    return articlesCache
  }

  if (inflightRequest) {
    return inflightRequest
  }

  const requestGeneration = articlesCacheGeneration
  const currentRequest = request(ARTICLES_ENDPOINT)
  inflightRequest = currentRequest
  try {
    const articles = await currentRequest
    if (requestGeneration === articlesCacheGeneration) {
      articlesCache = articles
    }
    return articles
  } finally {
    if (inflightRequest === currentRequest) {
      inflightRequest = null
    }
  }
}

export async function fetchArticleBySlug(slug) {
  if (!slug) {
    throw new Error('Slug é obrigatório para buscar um artigo específico.')
  }

  const articles = await fetchArticles()
  return articles.find(article => article.slug === slug)
}

export async function updateArticleSanitizationBypass(articleId, enabled, token) {
  const payload = await request(`${ARTICLES_ENDPOINT}/${articleId}`, {
    method: 'PATCH',
    headers: {
      'Content-Type': 'application/json',
      Authorization: `Bearer ${token}`
    },
    body: JSON.stringify({ article: { bypass_sanitization: enabled } })
  })

  clearArticlesCache()
  return payload
}

export function clearArticlesCache() {
  articlesCacheGeneration += 1
  articlesCache = null
  inflightRequest = null
}

export function buildArticleUrl(slug) {
  if (!slug) return null
  const sanitizedSlug = String(slug).trim().replace(/^\/+|\/+$/g, '')
  if (!sanitizedSlug) return null
  return `${ARTICLE_PUBLIC_BASE_URL}/${sanitizedSlug}/`
}
