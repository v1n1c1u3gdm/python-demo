export function createFetchResponse({ ok = true, status = 200, statusText = 'OK', jsonData = null } = {}) {
  const json = jest.fn(() => Promise.resolve(jsonData))
  return {
    ok,
    status,
    statusText,
    json,
    clone() {
      return { json }
    }
  }
}

export function mockFetchResponse(data, overrides = {}) {
  global.fetch.mockResolvedValue(createFetchResponse({ jsonData: data, ...overrides }))
}

export function mockFetchError({ status = 500, statusText = 'Internal Server Error', body } = {}) {
  global.fetch.mockResolvedValue(
    createFetchResponse({
      ok: false,
      status,
      statusText,
      jsonData: body || { message: statusText }
    })
  )
}

export function createDeferred() {
  const deferred = {}
  deferred.promise = new Promise((resolve, reject) => {
    deferred.resolve = resolve
    deferred.reject = reject
  })
  return deferred
}

