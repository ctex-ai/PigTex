import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

import { streamSmartChat } from './api'
import { DEFAULT_PIGTEX_SETTINGS, savePigTexSettings } from './settings'

function buildSseResponse(events: Array<Record<string, unknown>>): Response {
    const encoder = new TextEncoder()
    const stream = new ReadableStream<Uint8Array>({
        start(controller) {
            for (const payload of events) {
                controller.enqueue(encoder.encode(`data: ${JSON.stringify(payload)}\n\n`))
            }
            controller.enqueue(encoder.encode('data: [DONE]\n\n'))
            controller.close()
        },
    })

    return new Response(stream, {
        status: 200,
        headers: {
            'Content-Type': 'text/event-stream',
            'X-Conversation-ID': 'conv-timeout',
        },
    })
}

describe('web search metadata parsing', () => {
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

    it('preserves timeout web search status from SSE payloads', async () => {
        fetchMock.mockResolvedValueOnce(buildSseResponse([
            {
                delta: 'Partial answer',
                web_search: {
                    enabled: true,
                    status: 'timeout',
                    warnings: ['Web search reached its time budget.'],
                },
                done: true,
            },
        ]))

        const chunks: Array<Awaited<ReturnType<typeof streamSmartChat>> extends AsyncGenerator<infer T, void, unknown> ? T : never> = []
        for await (const chunk of streamSmartChat({
            message: 'Gia vang hom nay bao nhieu?',
            model: 'gpt-4o-mini',
            use_web_search: true,
        })) {
            chunks.push(chunk)
        }

        expect(chunks).toHaveLength(1)
        expect(chunks[0].conversationId).toBe('conv-timeout')
        expect(chunks[0].webSearch?.status).toBe('timeout')
        expect(chunks[0].webSearch?.warnings?.[0]).toContain('time budget')
    })
})
