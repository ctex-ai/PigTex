import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

import { generateImages, editImage, type ImageAttachment } from './api'
import { DEFAULT_PIGTEX_SETTINGS, savePigTexSettings } from './settings'

function buildJsonResponse(payload: unknown): Response {
    return new Response(JSON.stringify(payload), {
        status: 200,
        headers: {
            'Content-Type': 'application/json'
        }
    })
}

describe('image request model passthrough', () => {
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
            enableQwenImagePromptEnhancer: false,
        })

        fetchMock.mockResolvedValue(buildJsonResponse({ data: [] }))
    })

    afterEach(() => {
        vi.unstubAllGlobals()
        fetchMock.mockReset()
        localStorage.clear()
        sessionStorage.clear()
    })

    it('keeps the exact model on PAYG image generation requests', async () => {
        await generateImages('Render a product hero image', {
            model: 'vendor-exact-image-model'
        })

        expect(fetchMock).toHaveBeenCalledTimes(1)
        const [url, init] = fetchMock.mock.calls[0] as [string, RequestInit]
        expect(url).toBe('http://localhost:3001/api/proxy/v1/images/generations')

        const payload = JSON.parse(String(init.body)) as Record<string, unknown>
        expect(payload.model).toBe('vendor-exact-image-model')
    })

    it('does not inject a fallback model on PAYG image generation requests', async () => {
        await generateImages('Render a product hero image')

        expect(fetchMock).toHaveBeenCalledTimes(1)
        const [, init] = fetchMock.mock.calls[0] as [string, RequestInit]
        const payload = JSON.parse(String(init.body)) as Record<string, unknown>
        expect(payload).not.toHaveProperty('model')
    })

    it('keeps the exact model on PAYG image edit requests', async () => {
        const image: ImageAttachment = {
            id: 'img-1',
            filename: 'source.png',
            mime_type: 'image/png',
            size: 4,
            base64_data: 'data:image/png;base64,aGVsbG8='
        }

        await editImage('Remove the background', image, {
            model: 'vendor-exact-image-edit-model'
        })

        expect(fetchMock).toHaveBeenCalledTimes(1)
        const [url, init] = fetchMock.mock.calls[0] as [string, RequestInit]
        expect(url).toBe('http://localhost:3001/api/proxy/v1/images/edits')

        const payload = JSON.parse(String(init.body)) as Record<string, unknown>
        expect(payload.model).toBe('vendor-exact-image-edit-model')
    })
})
