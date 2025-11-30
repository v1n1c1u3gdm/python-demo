const DEFAULT_AUTHORS_URL = 'http://localhost:3000/authors'
const DEFAULT_ARTICLES_COUNT_URL = 'http://localhost:3000/articles/count_by_author'

const AUTHORS_ENDPOINT = normalizeUrl(process.env.VUE_APP_AUTHORS_URL || DEFAULT_AUTHORS_URL)
const ARTICLES_COUNT_ENDPOINT = normalizeUrl(
  process.env.VUE_APP_ARTICLES_COUNT_URL || DEFAULT_ARTICLES_COUNT_URL
)

let authorsCache = null
let authorsInflight = null
let countsCache = null
let countsInflight = null

function normalizeUrl(url) {
  if (!url) return null
  return url.replace(/\/+$/, '')
}

async function requestJson(url, fetchOptions = {}) {
  if (!url) {
    throw new Error('URL não configurada para consulta.')
  }

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
      // noop - mantém detalhe padrão
    }

    throw new Error(`Erro ao consultar autores: ${errorDetail}`)
  }

  return response.json()
}

export async function fetchAuthors(options = { force: false }) {
  if (!options.force && authorsCache) {
    return authorsCache
  }

  if (authorsInflight) {
    return authorsInflight
  }

  authorsInflight = requestJson(AUTHORS_ENDPOINT)
  try {
    authorsCache = await authorsInflight
    return authorsCache
  } finally {
    authorsInflight = null
  }
}

export async function fetchArticlesCountByAuthor(options = { force: false }) {
  if (!options.force && countsCache) {
    return countsCache
  }

  if (countsInflight) {
    return countsInflight
  }

  countsInflight = requestJson(ARTICLES_COUNT_ENDPOINT)
  try {
    countsCache = await countsInflight
    return countsCache
  } finally {
    countsInflight = null
  }
}

export function clearAuthorsCache() {
  authorsCache = null
  authorsInflight = null
}

export function clearCountsCache() {
  countsCache = null
  countsInflight = null
}

