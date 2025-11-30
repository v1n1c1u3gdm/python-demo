const DEFAULT_ARTICLES_URL = 'http://localhost:3000/articles'
const DEFAULT_ARTICLE_PUBLIC_BASE_URL = 'https://viniciusmenezes.com'

const ARTICLES_ENDPOINT = normalizeArticlesUrl(process.env.VUE_APP_ARTICLES_URL || DEFAULT_ARTICLES_URL)
const ARTICLE_PUBLIC_BASE_URL = normalizePublicBaseUrl(
  process.env.VUE_APP_ARTICLE_PUBLIC_BASE_URL || DEFAULT_ARTICLE_PUBLIC_BASE_URL
)

let articlesCache = null
let inflightRequest = null

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
    headers: {
      Accept: 'application/json',
      ...(fetchOptions.headers || {})
    },
    ...fetchOptions
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
    } catch (_) {
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

  inflightRequest = request(ARTICLES_ENDPOINT)
  try {
    articlesCache = await inflightRequest
    return articlesCache
  } finally {
    inflightRequest = null
  }
}

export async function fetchArticleBySlug(slug) {
  if (!slug) {
    throw new Error('Slug é obrigatório para buscar um artigo específico.')
  }

  const articles = await fetchArticles()
  return articles.find(article => article.slug === slug)
}

export function clearArticlesCache() {
  articlesCache = null
  inflightRequest = null
}

export function buildArticleUrl(slug) {
  if (!slug) return null
  const sanitizedSlug = String(slug).trim().replace(/^\/+|\/+$/g, '')
  if (!sanitizedSlug) return null
  return `${ARTICLE_PUBLIC_BASE_URL}/${sanitizedSlug}/`
}

