import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

import { getModels, getModelsWithCredentials } from './api'
import { DEFAULT_PIGTEX_SETTINGS, savePigTexSettings } from './settings'

function buildJsonResponse(payload: unknown, status = 200): Response {
    return new Response(JSON.stringify(payload), {
        status,
        headers: {
            'Content-Type': 'application/json'
        }
    })
}

describe('model list freshness', () => {
    const fetchMock = vi.fn()

    beforeEach(() => {
        vi.stubGlobal('fetch', fetchMock)
        localStorage.clear()
        sessionStorage.clear()

        savePigTexSettings({
            ...DEFAULT_PIGTEX_SETTINGS,
            apiProvider: 'auto',
            customEndpoint: 'openai',
            apiKey: '',
            baseUrl: '',
        })
    })

    afterEach(() => {
        vi.unstubAllGlobals()
        fetchMock.mockReset()
        localStorage.clear()
        sessionStorage.clear()
    })

    it('refetches models on every call instead of returning a stale cached list', async () => {
        fetchMock
            .mockResolvedValueOnce(buildJsonResponse({
                data: [{ id: 'provider-model-a', owned_by: 'gateway' }]
            }))
            .mockResolvedValueOnce(buildJsonResponse({
                data: [{ id: 'provider-model-b', owned_by: 'gateway' }]
            }))

        await expect(getModels()).resolves.toMatchObject([
            { id: 'provider-model-a' }
        ])
        await expect(getModels()).resolves.toMatchObject([
            { id: 'provider-model-b' }
        ])

        expect(fetchMock).toHaveBeenCalledTimes(2)
    })

    it('throws on later model list failures instead of silently returning the previous list', async () => {
        fetchMock
            .mockResolvedValueOnce(buildJsonResponse({
                data: [{ id: 'provider-model-a', owned_by: 'gateway' }]
            }))
            .mockResolvedValueOnce(buildJsonResponse({
                detail: {
                    error: 'upstream_api_error',
                    message: 'Provider unavailable'
                }
            }, 503))

        await expect(getModels()).resolves.toMatchObject([
            { id: 'provider-model-a' }
        ])
        await expect(getModels()).rejects.toThrow('HTTP 503')

        expect(fetchMock).toHaveBeenCalledTimes(2)
    })

    it('sends TexAPI credentials through the standard BYOK headers', async () => {
        fetchMock.mockResolvedValueOnce(buildJsonResponse({
            data: [{ id: 'provider-model-direct', owned_by: 'texapi' }]
        }))

        await expect(
            getModelsWithCredentials(
                'texapi-key-123',
                'https://api.texapi.dev/v1',
                'auto',
                { includeAllReturnedModels: true }
            )
        ).resolves.toMatchObject([
            { id: 'provider-model-direct' }
        ])

        expect(fetchMock).toHaveBeenCalledTimes(1)
        const [, init] = fetchMock.mock.calls[0] as [string, RequestInit]
        const headers = new Headers(init.headers)
        expect(headers.get('X-API-Provider')).toBe('openai')
        expect(headers.get('X-API-Key')).toBe('texapi-key-123')
        expect(headers.get('X-API-Base-URL')).toBe('https://api.texapi.dev/v1')
    })
})
