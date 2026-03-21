import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

import { generateVideo, sendSmartChat, synthesizeSpeech } from './api'
import { DEFAULT_PIGTEX_SETTINGS, savePigTexSettings } from './settings'

function buildJsonResponse(payload: unknown, status = 200): Response {
    return new Response(JSON.stringify(payload), {
        status,
        headers: {
            'Content-Type': 'application/json'
        }
    })
}

describe('runtime model passthrough', () => {
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
    })

    afterEach(() => {
        vi.unstubAllGlobals()
        fetchMock.mockReset()
        localStorage.clear()
        sessionStorage.clear()
    })

    it('keeps the exact model on smart chat requests and does not retry to a fallback model', async () => {
        fetchMock.mockResolvedValueOnce(buildJsonResponse({
            detail: {
                error: 'upstream_api_error',
                message: 'Model unavailable'
            }
        }, 404))

        await expect(sendSmartChat({
            message: 'hello',
            model: 'vendor-chat-model'
        })).rejects.toThrow('Model unavailable')

        expect(fetchMock).toHaveBeenCalledTimes(1)
        const [, init] = fetchMock.mock.calls[0] as [string, RequestInit]
        const payload = JSON.parse(String(init.body)) as Record<string, unknown>
        expect(payload.model).toBe('vendor-chat-model')
    })

    it('blocks speech synthesis requests when voice is disabled', async () => {
        fetchMock.mockResolvedValueOnce(buildJsonResponse({ ok: true }))

        await expect(synthesizeSpeech({
            model: 'vendor-tts-model',
            input: 'hello world'
        })).rejects.toThrow('Voice features are disabled on this PigTex build.')

        expect(fetchMock).not.toHaveBeenCalled()
    })

    it('requires an explicit video model instead of injecting a default', async () => {
        await expect(generateVideo('make a trailer')).rejects.toThrow(
            'Model is required for video generation.'
        )
        expect(fetchMock).not.toHaveBeenCalled()
    })
})
