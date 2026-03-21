import { describe, expect, it } from 'vitest'

import {
    filterModelsByCapability,
    getTransportDefaultModelId,
    modelSupportsCapability,
    pickModelForCapability,
    transportSupportsCapability,
    type AIModel
} from './api'

describe('model capability routing', () => {
    it('does not infer image-generation capability from model naming alone', () => {
        const model: AIModel = {
            id: 'gemini-3.1-flash-image-preview',
            name: 'gemini-3.1-flash-image-preview',
            provider: 'gateway',
            provider_id: 'gateway',
            transport: 'openai',
            tier: 'plus',
            type: 'chat',
            supports_streaming: false,
            supports_vision: true,
            max_tokens: 8192,
            description: null,
            priority: 100,
            is_active: true,
        }

        expect(modelSupportsCapability(model, 'image_generation', 'openai')).toBe(false)
        expect(modelSupportsCapability(model, 'image_generation', 'gemini')).toBe(false)
    })

    it('blocks voice capability when transport-level support is disabled', () => {
        const models: AIModel[] = [
            {
                id: 'gpt-5-low',
                name: 'gpt-5-low',
                provider: 'openai',
                provider_id: 'openai',
                transport: 'openai',
                tier: 'plus',
                type: 'chat',
                supports_streaming: true,
                supports_vision: true,
                max_tokens: 8192,
                description: null,
                priority: 100,
                is_active: true,
            },
            {
                id: 'gemini-2.5-flash-preview-tts',
                name: 'gemini-2.5-flash-preview-tts',
                provider: 'gemini',
                provider_id: 'gemini',
                transport: 'gemini',
                tier: 'plus',
                type: 'audio',
                supports_streaming: false,
                supports_vision: false,
                max_tokens: 8192,
                description: null,
                priority: 100,
                is_active: true,
            },
        ]

        expect(filterModelsByCapability(models, 'audio_speech', 'gemini').map(model => model.id)).toEqual([])
    })

    it('exposes provider-level transport support gates for media tools', () => {
        expect(transportSupportsCapability('anthropic', 'audio_speech')).toBe(false)
        expect(transportSupportsCapability('anthropic', 'video_generation')).toBe(false)
        expect(transportSupportsCapability('gemini', 'audio_speech')).toBe(false)
        expect(transportSupportsCapability('gemini', 'video_generation')).toBe(false)
    })

    it('uses explicit backend capabilities when they are present', () => {
        const model: AIModel = {
            id: 'vendor-special-media',
            name: 'vendor-special-media',
            provider: 'gateway',
            provider_id: 'gateway',
            transport: 'openai',
            tier: 'plus',
            type: 'chat',
            capabilities: ['audio_speech'],
            supports_streaming: true,
            supports_vision: false,
            max_tokens: 8192,
            description: null,
            priority: 100,
            is_active: true,
        }

        expect(modelSupportsCapability(model, 'audio_speech', 'openai')).toBe(false)
        expect(modelSupportsCapability(model, 'image_generation', 'openai')).toBe(false)
    })

    it('picks the first remaining chat-capable model from the backend catalog order', () => {
        const models: AIModel[] = [
            {
                id: 'gemini-2.5-flash-image',
                name: 'gemini-2.5-flash-image',
                provider: 'gemini',
                provider_id: 'gemini',
                transport: 'gemini',
                tier: 'plus',
                type: 'image',
                supports_streaming: false,
                supports_vision: true,
                max_tokens: 8192,
                description: null,
                priority: 100,
                is_active: true,
            },
            {
                id: 'gpt-4.1-mini',
                name: 'gpt-4.1-mini',
                provider: 'openai',
                provider_id: 'openai',
                transport: 'openai',
                tier: 'plus',
                type: 'chat',
                supports_streaming: true,
                supports_vision: true,
                max_tokens: 8192,
                description: null,
                priority: 100,
                is_active: true,
            },
            {
                id: 'gpt-5-low',
                name: 'gpt-5-low',
                provider: 'openai',
                provider_id: 'openai',
                transport: 'openai',
                tier: 'plus',
                type: 'chat',
                supports_streaming: true,
                supports_vision: true,
                max_tokens: 8192,
                description: null,
                priority: 100,
                is_active: true,
            }
        ]

        const picked = pickModelForCapability(models, 'chat', 'openai', {
            excludeModelIds: ['gpt-4.1-mini']
        })

        expect(picked?.id).toBe('gpt-5-low')
    })

    it('exposes provider-owned default model hints for media tools', () => {
        expect(getTransportDefaultModelId('openai', 'audio_speech')).toBe('')
        expect(getTransportDefaultModelId('gemini', 'audio_speech')).toBe('')
        expect(getTransportDefaultModelId('openai', 'video_generation')).toBe('sora-2')
    })
})
