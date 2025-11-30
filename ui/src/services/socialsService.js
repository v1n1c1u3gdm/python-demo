const DEFAULT_SOCIALS_URL = 'http://localhost:3000/socials'

const SOCIALS_ENDPOINT = normalizeUrl(process.env.VUE_APP_SOCIALS_URL || DEFAULT_SOCIALS_URL)

let socialsCache = null
let inflightRequest = null

function normalizeUrl(url) {
  if (!url) return DEFAULT_SOCIALS_URL
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
      // noop - mantém detalhe padrão
    }

    throw new Error(`Erro ao consultar redes sociais: ${errorDetail}`)
  }

  return response.json()
}

export async function fetchSocials(options = { force: false }) {
  if (!options.force && socialsCache) {
    return socialsCache
  }

  if (inflightRequest) {
    return inflightRequest
  }

  inflightRequest = request(SOCIALS_ENDPOINT)

  try {
    socialsCache = await inflightRequest
    return socialsCache
  } finally {
    inflightRequest = null
  }
}

export function clearSocialsCache() {
  socialsCache = null
  inflightRequest = null
}

