import { fetchSocials, clearSocialsCache } from '@/services/socialsService'
import { makeSocials } from '../factories/socials'
import { createDeferred, createFetchResponse, mockFetchError, mockFetchResponse } from '../mocks/fetchMock'

describe('socialsService', () => {
  afterEach(() => {
    clearSocialsCache()
  })

  it('caches social links between calls', async () => {
    const socials = makeSocials(3)
    mockFetchResponse(socials)

    const first = await fetchSocials()
    const second = await fetchSocials()

    expect(second).toBe(first)
    expect(global.fetch).toHaveBeenCalledTimes(1)
  })

  it('forces reload when requested', async () => {
    mockFetchResponse(makeSocials(1))
    await fetchSocials()

    mockFetchResponse(makeSocials(2))
    const result = await fetchSocials({ force: true })

    expect(result).toHaveLength(2)
    expect(global.fetch).toHaveBeenCalledTimes(2)
  })

  it('shares inflight requests', async () => {
    const deferred = createDeferred()
    global.fetch.mockReturnValueOnce(deferred.promise)

    const first = fetchSocials()
    const second = fetchSocials()

    expect(global.fetch).toHaveBeenCalledTimes(1)

    const payload = makeSocials(2)
    deferred.resolve(createFetchResponse({ jsonData: payload }))
    const [a, b] = await Promise.all([first, second])
    expect(a).toEqual(payload)
    expect(b).toEqual(payload)
  })

  it('throws when API responds with error status', async () => {
    mockFetchError({ status: 404, statusText: 'Not Found' })
    await expect(fetchSocials()).rejects.toThrow('Erro ao consultar redes sociais')
  })
})

