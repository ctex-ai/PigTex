import React from 'react'
import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest'
import { render, screen, fireEvent, waitFor, cleanup } from '@testing-library/react'
import ChatPanel from './ChatPanel'
import { I18nProvider } from '../../../contexts/I18nContext'
import { DEFAULT_PIGTEX_SETTINGS, savePigTexSettings } from '../../../services/settings'

const EN_SETTINGS = {
    ...DEFAULT_PIGTEX_SETTINGS,
    language: 'en' as const
}

const STUDIO_MODEL_PLACEHOLDER = 'Enter the provider model id'

vi.mock('framer-motion', () => ({
    AnimatePresence: ({ children }: { children: React.ReactNode }) => <>{children}</>,
    motion: new Proxy({}, {
        get: (_target, tagName: string) => {
            return ({ children, ...props }: { children?: React.ReactNode } & Record<string, unknown>) => {
                const {
                    initial,
                    animate,
                    exit,
                    transition,
                    whileHover,
                    whileTap,
                    layout,
                    ...safeProps
                } = props
                void initial
                void animate
                void exit
                void transition
                void whileHover
                void whileTap
                void layout
                return React.createElement(tagName, safeProps, children)
            }
        }
    })
}))

vi.mock('../../Shared/MessageRenderer', () => ({
    default: ({ content }: { content: string }) => <div data-testid="message-renderer">{content}</div>
}))

const getModels = vi.fn()
const streamSmartChat = vi.fn()
const diagnoseApiConnectivityIssue = vi.fn()
const getLocalConversation = vi.fn()
const createConversation = vi.fn()
const addConversationMessage = vi.fn()
const updateConversationMessage = vi.fn()
const fileToBase64 = vi.fn()
const uploadFiles = vi.fn()
const generateImages = vi.fn()
const editImage = vi.fn()
const synthesizeSpeech = vi.fn()
const generateVideo = vi.fn()
const getVideoGenerationTask = vi.fn()
const resolveProtectedMediaSrc = vi.fn(async (url: string) => ({ src: url }))
const transportSupportsCapability = vi.fn(() => true)
const filterModelsByCapability = vi.fn((models: unknown[]) => models)
const modelSupportsCapability = vi.fn(() => true)
const getTransportDefaultModelId = vi.fn()
const pickModelForCapability = vi.fn()

const modelHasCapability = (model: Record<string, unknown>, capability: string): boolean => {
    const capabilities = Array.isArray(model.capabilities) ? model.capabilities : []
    if (capabilities.includes(capability)) {
        return true
    }
    const type = typeof model.type === 'string' ? model.type : 'chat'
    if (capability === 'chat') return type === 'chat'
    if (capability === 'image_generation' || capability === 'image_edit') return type === 'image'
    if (capability === 'audio_speech') return type === 'audio'
    if (capability === 'video_generation') return type === 'video'
    return false
}

const defaultModelIdByCapability: Record<string, Record<string, string>> = {
    chat: {
        openai: 'gpt-4o',
        anthropic: 'claude-sonnet-4-20250514',
        gemini: 'gemini-2.5-flash',
        alibaba: 'qwen-plus-latest'
    },
    image_generation: {
        openai: 'gpt-image-1',
        anthropic: '',
        gemini: 'gemini-2.5-flash-image',
        alibaba: 'qwen-image-2.0'
    },
    image_edit: {
        openai: 'gpt-image-1',
        anthropic: '',
        gemini: 'gemini-2.5-flash-image',
        alibaba: 'qwen-image-2.0'
    },
    audio_speech: {
        openai: 'gpt-4o-mini-tts',
        anthropic: '',
        gemini: 'gemini-2.5-flash-preview-tts',
        alibaba: 'qwen3-tts-flash'
    },
    video_generation: {
        openai: 'sora-2',
        anthropic: '',
        gemini: '',
        alibaba: ''
    }
}

vi.mock('../../../services/api', () => ({
    getModels: (...args: unknown[]) => getModels(...args),
    streamSmartChat: (...args: unknown[]) => streamSmartChat(...args),
    diagnoseApiConnectivityIssue: (...args: unknown[]) => diagnoseApiConnectivityIssue(...args),
    getLocalConversation: (...args: unknown[]) => getLocalConversation(...args),
    createConversation: (...args: unknown[]) => createConversation(...args),
    addConversationMessage: (...args: unknown[]) => addConversationMessage(...args),
    updateConversationMessage: (...args: unknown[]) => updateConversationMessage(...args),
    fileToBase64: (...args: unknown[]) => fileToBase64(...args),
    uploadFiles: (...args: unknown[]) => uploadFiles(...args),
    generateImages: (...args: unknown[]) => generateImages(...args),
    editImage: (...args: unknown[]) => editImage(...args),
    synthesizeSpeech: (...args: unknown[]) => synthesizeSpeech(...args),
    generateVideo: (...args: unknown[]) => generateVideo(...args),
    getVideoGenerationTask: (...args: unknown[]) => getVideoGenerationTask(...args),
    resolveProtectedMediaSrc: (...args: unknown[]) => resolveProtectedMediaSrc(...args),
    transportSupportsCapability: (...args: unknown[]) => transportSupportsCapability(...args),
    filterModelsByCapability: (...args: unknown[]) => filterModelsByCapability(...args),
    getTransportDefaultModelId: (...args: unknown[]) => getTransportDefaultModelId(...args),
    modelSupportsCapability: (...args: unknown[]) => modelSupportsCapability(...args),
    pickModelForCapability: (...args: unknown[]) => pickModelForCapability(...args),
    isPaygImageModel: () => false,
    resolveImageUrl: (url: string) => url,
    resolveProtectedImageSrc: async (url: string) => ({ src: url })
}))

const showError = vi.fn()
const showInfo = vi.fn()
const showSuccess = vi.fn()
const copyToClipboard = vi.fn()

vi.mock('../../Shared/Toast', () => ({
    showError: (...args: unknown[]) => showError(...args),
    showInfo: (...args: unknown[]) => showInfo(...args),
    showSuccess: (...args: unknown[]) => showSuccess(...args),
    copyToClipboard: (...args: unknown[]) => copyToClipboard(...args)
}))

const buildPlannerToolRequest = (actions: unknown[]) => [
    '```file_agent',
    JSON.stringify({ kind: 'tool_request', actions }),
    '```'
].join('\n')

const buildPlannerFinalAnswer = (message: string) => [
    '```file_agent',
    JSON.stringify({ kind: 'final_answer', message }),
    '```'
].join('\n')

const renderChatPanel = (
    settings = EN_SETTINGS,
    props: Partial<React.ComponentProps<typeof ChatPanel>> = {}
) => {
    savePigTexSettings(settings)
    return render(
        <I18nProvider>
            <ChatPanel
                variant="centered"
                settings={settings}
                onSettingsChange={vi.fn()}
                {...props}
            />
        </I18nProvider>
    )
}

describe('ChatPanel settings integration', () => {
    beforeEach(() => {
        window.localStorage.clear()
        window.sessionStorage.clear()
        getModels.mockReset()
        streamSmartChat.mockReset()
        diagnoseApiConnectivityIssue.mockReset()
        getLocalConversation.mockReset()
        createConversation.mockReset()
        addConversationMessage.mockReset()
        updateConversationMessage.mockReset()
        fileToBase64.mockReset()
        uploadFiles.mockReset()
        generateImages.mockReset()
        editImage.mockReset()
        synthesizeSpeech.mockReset()
        generateVideo.mockReset()
        getVideoGenerationTask.mockReset()
        resolveProtectedMediaSrc.mockClear()
        transportSupportsCapability.mockReset()
        filterModelsByCapability.mockReset()
        getTransportDefaultModelId.mockReset()
        modelSupportsCapability.mockReset()
        pickModelForCapability.mockReset()
        showError.mockReset()
        showInfo.mockReset()
        showSuccess.mockReset()
        copyToClipboard.mockReset()

        getModels.mockResolvedValue([])
        streamSmartChat.mockImplementation(async function* () {
            yield { content: 'Done' }
        })
        diagnoseApiConnectivityIssue.mockResolvedValue(null)
        createConversation.mockResolvedValue({
            id: 'conv_test',
            title: 'Test',
            summary: null,
            total_messages: 0,
            workspace_id: null
        })
        addConversationMessage.mockResolvedValue({
            id: 'msg_test',
            role: 'user',
            content: 'saved',
            model: null,
            token_count: 0
        })
        updateConversationMessage.mockResolvedValue({
            id: 'msg_test',
            role: 'assistant',
            content: 'updated',
            model: null,
            token_count: 0
        })
        uploadFiles.mockResolvedValue([])
        generateImages.mockResolvedValue({ images: [], revisedPrompts: [] })
        editImage.mockResolvedValue({ images: [], revisedPrompts: [] })
        synthesizeSpeech.mockResolvedValue(new Blob(['voice'], { type: 'audio/mpeg' }))
        generateVideo.mockResolvedValue({
            data: [{
                url: 'https://example.com/generated.mp4',
                mime_type: 'video/mp4'
            }]
        })
        getVideoGenerationTask.mockResolvedValue({
            data: [{
                url: 'https://example.com/generated.mp4',
                mime_type: 'video/mp4'
            }]
        })
        resolveProtectedMediaSrc.mockImplementation(async (url: string) => ({ src: url }))
        transportSupportsCapability.mockReturnValue(true)
        filterModelsByCapability.mockImplementation((models: unknown[]) => models)
        getTransportDefaultModelId.mockImplementation((endpointProvider: string, capability: string) => {
            return defaultModelIdByCapability[capability]?.[endpointProvider] ?? ''
        })
        modelSupportsCapability.mockReturnValue(true)
        pickModelForCapability.mockImplementation((
            models: unknown[],
            capability: string,
            _endpointProvider?: string,
            options?: { preferredModelId?: string; fallbackModelId?: string; excludeModelIds?: string[] }
        ) => {
            const list = Array.isArray(models)
                ? models.filter((model): model is Record<string, unknown> => Boolean(model && typeof model === 'object'))
                : []
            const candidates = list.filter(model => modelHasCapability(model, capability))
            const excluded = new Set((options?.excludeModelIds || []).map(value => value.trim().toLowerCase()))
            const pickById = (modelId?: string) => {
                const normalizedId = (modelId || '').trim().toLowerCase()
                if (!normalizedId) return null
                return candidates.find(model => {
                    const modelIdValue = typeof model.id === 'string' ? model.id.trim().toLowerCase() : ''
                    return modelIdValue === normalizedId && !excluded.has(modelIdValue)
                }) || null
            }
            return pickById(options?.preferredModelId)
                || pickById(options?.fallbackModelId)
                || candidates.find(model => {
                    const modelIdValue = typeof model.id === 'string' ? model.id.trim().toLowerCase() : ''
                    return !excluded.has(modelIdValue)
                })
                || null
        })
    })

    afterEach(() => {
        const windowWithElectron = window as unknown as { electronAPI?: unknown }
        windowWithElectron.electronAPI = undefined
        cleanup()
    })

    it('uses generation and memory settings in smart chat request payload', async () => {
        const settings = {
            ...EN_SETTINGS,
            model: 'gpt-4.1-mini',
            temperature: 0.55,
            maxTokens: 2048,
            customInstruction: '  Please answer short  ',
            memoryEnabled: true,
            useKnowledge: false,
            useFacts: true,
            useHistory: false,
            defaultAiFileMode: true
        }

        renderChatPanel(settings)

        fireEvent.change(screen.getByPlaceholderText('Ask me anything...'), {
            target: { value: 'test settings payload' }
        })
        fireEvent.click(screen.getByTitle('Send'))

        await waitFor(() => expect(streamSmartChat).toHaveBeenCalledTimes(1))

        const request = streamSmartChat.mock.calls[0][0] as Record<string, unknown>
        expect(request.message).toBe('test settings payload')
        expect(request.model).toBe('gpt-4.1-mini')
        expect(request.temperature).toBe(0.55)
        expect(request.max_tokens).toBe(2048)
        expect(request.use_memory).toBe(true)
        expect(request.use_knowledge).toBe(false)
        expect(request.use_facts).toBe(true)
        expect(request.use_history).toBe(false)
        const runtimeInstruction = request.runtime_instruction as string
        expect(runtimeInstruction).toContain('Please answer short')
        expect(runtimeInstruction).toContain('Response Mode: FAST')
        expect(request.stream).toBe(true)
    })

    it('drops a stale conversation selection after a 404 and starts a fresh chat', async () => {
        const onConversationInvalidated = vi.fn()

        getLocalConversation.mockRejectedValue(new Error('Conversation not found'))
        streamSmartChat.mockImplementation(async function* () {
            yield { content: 'Recovered', conversationId: 'conv_new' }
        })

        renderChatPanel(EN_SETTINGS, {
            conversationId: 'conv_stale',
            onConversationInvalidated
        })

        await waitFor(() => expect(onConversationInvalidated).toHaveBeenCalledTimes(1))

        fireEvent.change(screen.getByPlaceholderText('Ask me anything...'), {
            target: { value: 'recover from stale conversation' }
        })
        fireEvent.click(screen.getByTitle('Send'))

        await waitFor(() => expect(streamSmartChat).toHaveBeenCalledTimes(1))

        const request = streamSmartChat.mock.calls[0][0] as Record<string, unknown>
        expect(request.conversation_id).toBeUndefined()
        expect(getLocalConversation).toHaveBeenCalledTimes(1)
    })

    it('keeps the configured chat model when it is unavailable in the fetched catalog', async () => {
        const onSettingsChange = vi.fn()
        getModels.mockResolvedValue([
            {
                id: 'gemini-2.5-flash-image',
                name: 'Gemini Image',
                provider: 'gemini',
                tier: 'pro',
                type: 'image',
                supports_streaming: false,
                supports_vision: true,
                max_tokens: 0,
                description: null,
                priority: 100,
                is_active: true
            },
            {
                id: 'gpt-5-low',
                name: 'GPT-5 Low',
                provider: 'cxtocc',
                tier: 'plus',
                type: 'chat',
                supports_streaming: true,
                supports_vision: false,
                max_tokens: 4096,
                description: null,
                priority: 100,
                is_active: true
            }
        ])

        renderChatPanel(
            {
                ...EN_SETTINGS,
                model: 'gpt-4.1-mini'
            },
            { onSettingsChange }
        )

        await waitFor(() => {
            expect(screen.getByText('gpt-4.1-mini')).toBeInTheDocument()
        })
        expect(onSettingsChange).not.toHaveBeenCalled()

        fireEvent.change(screen.getByPlaceholderText('Ask me anything...'), {
            target: { value: 'model fallback check' }
        })
        fireEvent.click(screen.getByTitle('Send'))

        await waitFor(() => expect(streamSmartChat).toHaveBeenCalledTimes(1))
        const request = streamSmartChat.mock.calls[0][0] as Record<string, unknown>
        expect(request.model).toBe('gpt-4.1-mini')
    })

    it('renders provider-supplied model flags and disables stopped models in the dropdown', async () => {
        getModels.mockResolvedValue([
            {
                id: 'gpt-5',
                name: 'GPT-5',
                provider: 'openai',
                tier: 'plus',
                type: 'chat',
                supports_streaming: true,
                supports_vision: true,
                max_tokens: 8192,
                description: null,
                priority: 100,
                is_active: true,
                recommendation_flag: {
                    label: 'Best',
                    tone: 'accent',
                },
                status_flag: {
                    label: 'Active',
                    tone: 'success',
                },
            },
            {
                id: 'legacy-gpt',
                name: 'Legacy GPT',
                provider: 'openai',
                tier: 'plus',
                type: 'chat',
                supports_streaming: true,
                supports_vision: false,
                max_tokens: 4096,
                description: null,
                priority: 100,
                is_active: true,
                status_flag: {
                    label: 'Stopped',
                    tone: 'danger',
                    disabled: true,
                },
            },
        ])

        renderChatPanel({
            ...EN_SETTINGS,
            model: 'gpt-5',
        })

        await waitFor(() => {
            expect(screen.getByText('Best')).toBeInTheDocument()
            expect(screen.getByText('Active')).toBeInTheDocument()
        })

        const modelTrigger = screen.getByText('GPT-5').closest('button')
        expect(modelTrigger).not.toBeNull()
        fireEvent.click(modelTrigger!)

        await waitFor(() => {
            const legacyButton = screen.getByText('Legacy GPT').closest('button')
            expect(legacyButton).not.toBeNull()
            expect(legacyButton).toBeDisabled()
        })
        expect(screen.getByText('Stopped')).toBeInTheDocument()
    })

    it('shows a short model list first and expands on demand', async () => {
        getModels.mockResolvedValue(
            Array.from({ length: 6 }, (_, index) => ({
                id: `model-${index + 1}`,
                name: `Model ${index + 1}`,
                provider: 'openai',
                tier: 'plus',
                type: 'chat',
                supports_streaming: true,
                supports_vision: false,
                max_tokens: 4096,
                description: null,
                priority: 100,
                is_active: true,
            }))
        )

        renderChatPanel({
            ...EN_SETTINGS,
            model: 'model-1',
        })

        await waitFor(() => {
            expect(screen.getByText('Model 1')).toBeInTheDocument()
        })

        const modelTrigger = screen.getByText('Model 1').closest('button')
        expect(modelTrigger).not.toBeNull()
        fireEvent.click(modelTrigger!)

        await waitFor(() => {
            expect(screen.getByText('Suggested models')).toBeInTheDocument()
            expect(screen.getByText('View all models')).toBeInTheDocument()
        })
        expect(screen.queryByText('Model 6')).not.toBeInTheDocument()

        fireEvent.click(screen.getByText('View all models'))

        await waitFor(() => {
            expect(screen.getByText('Model 6')).toBeInTheDocument()
            expect(screen.getByText('Show fewer models')).toBeInTheDocument()
        })
    })

    it('disables chat send when no chat model is selected', async () => {
        renderChatPanel({
            ...EN_SETTINGS,
            model: ''
        })

        fireEvent.change(screen.getByPlaceholderText('Ask me anything...'), {
            target: { value: 'missing model check' }
        })

        await waitFor(() => {
            expect((screen.getByTitle('Send') as HTMLButtonElement).disabled).toBe(true)
        })
        expect(streamSmartChat).not.toHaveBeenCalled()
    })

    it('keeps only one composer dropdown open at a time', async () => {
        const settings = {
            ...EN_SETTINGS,
            model: 'gpt-4o'
        }

        renderChatPanel(settings)

        fireEvent.click(screen.getByTitle('Choose creation mode'))
        expect(screen.getByText('Image')).toBeInTheDocument()

        const modelButton = screen.getByText('gpt-4o').closest('button')
        expect(modelButton).not.toBeNull()
        fireEvent.click(modelButton!)
        expect(screen.queryByText('Image')).not.toBeInTheDocument()

        const modeButton = screen.getByText('Fast').closest('button')
        expect(modeButton).not.toBeNull()
        fireEvent.click(modeButton!)
        expect(screen.getByText('Prioritize speed with minimal tool use')).toBeInTheDocument()

        fireEvent.click(modelButton!)
        expect(screen.queryByText('Ưu tiên tốc độ, tự chọn tool tối thiểu')).not.toBeInTheDocument()
    })

    it('keeps media studio dropdowns open when clicking their triggers', async () => {
        renderChatPanel({
            ...EN_SETTINGS,
            model: 'gpt-4o'
        })

        fireEvent.click(screen.getByTitle('Choose creation mode'))
        fireEvent.click(screen.getByText('Video'))

        const aspectRatioTrigger = screen.getByRole('button', { name: 'Aspect Ratio' })
        fireEvent.click(aspectRatioTrigger)
        expect(screen.getByText('9:16')).toBeInTheDocument()

        const durationTrigger = screen.getByRole('button', { name: 'Duration' })
        fireEvent.click(durationTrigger)

        expect(screen.queryByText('9:16')).not.toBeInTheDocument()
        expect(screen.getByText('10s')).toBeInTheDocument()
    })

    it('forces memory sources off when global memory is disabled', async () => {
        streamSmartChat.mockImplementation(async function* () {
            yield { content: 'Done' }
        })

        const settings = {
            ...EN_SETTINGS,
            model: 'gpt-4.1-mini',
            memoryEnabled: false,
            useKnowledge: true,
            useFacts: true,
            useHistory: true
        }

        renderChatPanel(settings)

        fireEvent.change(screen.getByPlaceholderText('Ask me anything...'), {
            target: { value: 'memory off check' }
        })
        fireEvent.click(screen.getByTitle('Send'))

        await waitFor(() => expect(streamSmartChat).toHaveBeenCalledTimes(1))

        const request = streamSmartChat.mock.calls[0][0] as Record<string, unknown>
        expect(request.use_memory).toBe(true)
        expect(request.use_knowledge).toBe(false)
        expect(request.use_facts).toBe(false)
        expect(request.use_history).toBe(false)
    })

    it('keeps memory sources on in AI file mode when memory settings are enabled', async () => {
        const settings = {
            ...EN_SETTINGS,
            memoryEnabled: true,
            useKnowledge: true,
            useFacts: true,
            useHistory: true,
            defaultAiFileMode: true
        }

        renderChatPanel(settings, { localRootPath: 'D:\\PigTex' })

        fireEvent.change(screen.getByRole('textbox'), {
            target: { value: 'ai file mode memory persistence check' }
        })
        fireEvent.click(screen.getByTitle('Send'))

        await waitFor(() => expect(streamSmartChat).toHaveBeenCalledTimes(1))
        const request = streamSmartChat.mock.calls[0][0] as Record<string, unknown>
        expect(request.use_memory).toBe(true)
        expect(request.use_knowledge).toBe(true)
        expect(request.use_facts).toBe(true)
        expect(request.use_history).toBe(true)
    })

    it('auto-approves AI file actions when auto mode is enabled', async () => {
        const writeFile = vi.fn().mockResolvedValue(undefined)
        const windowWithElectron = window as unknown as {
            electronAPI?: { writeFile: (payload: { filePath: string; content: string }) => Promise<void> }
        }
        windowWithElectron.electronAPI = { writeFile }

        streamSmartChat.mockImplementation(async function* (request: { message?: string }) {
            if (typeof request.message === 'string' && request.message.includes('[FILE_AGENT_CONTEXT]')) {
                yield { content: buildPlannerFinalAnswer('Done after tool execution') }
                return
            }

            yield { content: buildPlannerToolRequest([{ type: 'write_file', path: 'README.md', content: 'Hello from AI' }]) }
        })

        const settings = {
            ...EN_SETTINGS,
            defaultAiFileMode: true,
            autoApproveAiFileActions: true
        }

        renderChatPanel(settings, { localRootPath: 'D:\\PigTex' })

        fireEvent.change(screen.getByPlaceholderText(/Ask me anything/i), {
            target: { value: 'hãy sửa README' }
        })
        fireEvent.click(screen.getByTitle('Send'))

        await waitFor(() => expect(writeFile).toHaveBeenCalledTimes(1))
        await waitFor(() => expect(streamSmartChat).toHaveBeenCalledTimes(2))

        expect(screen.queryByText('Confirm AI Actions')).not.toBeInTheDocument()
        const firstRequest = streamSmartChat.mock.calls[0][0] as { runtime_instruction?: string }
        expect(firstRequest.runtime_instruction).toContain('Actions are auto-approved')
        const secondRequest = streamSmartChat.mock.calls[1][0] as { message?: string; use_web_search?: boolean }
        expect(secondRequest.message).toContain('[FILE_AGENT_CONTEXT]')
        expect(secondRequest.use_web_search).toBe(false)
    })

    it('auto-repairs malformed file_agent block and continues tool flow', async () => {
        const writeFile = vi.fn().mockResolvedValue(undefined)
        const windowWithElectron = window as unknown as {
            electronAPI?: { writeFile: (payload: { filePath: string; content: string }) => Promise<void> }
        }
        windowWithElectron.electronAPI = { writeFile }

        streamSmartChat.mockImplementation(async function* (request: { message?: string }) {
            if (typeof request.message === 'string' && request.message.includes('[FILE_AGENT_CONTEXT]')) {
                yield { content: buildPlannerFinalAnswer('Done after repaired tool execution') }
                return
            }
            if (typeof request.message === 'string' && request.message.includes('previous file_agent block was invalid')) {
                yield { content: buildPlannerToolRequest([{ type: 'write_file', path: 'README.md', content: 'Recovered from malformed JSON' }]) }
                return
            }

            yield {
                content: '```file_agent\n{"kind":"tool_request","actions":[{"type":"write_file","path":"README.md","content":"broken-json"}]\n```'
            }
        })

        const settings = {
            ...EN_SETTINGS,
            defaultAiFileMode: true,
            autoApproveAiFileActions: true
        }

        renderChatPanel(settings, { localRootPath: 'D:\\PigTex' })

        fireEvent.change(screen.getByPlaceholderText(/Ask me anything/i), {
            target: { value: 'hãy sửa README với tool mode' }
        })
        fireEvent.click(screen.getByTitle('Send'))

        await waitFor(() => expect(writeFile).toHaveBeenCalledTimes(1))
        await waitFor(() => expect(streamSmartChat).toHaveBeenCalledTimes(3))

        const secondRequest = streamSmartChat.mock.calls[1][0] as { message?: string }
        expect(secondRequest.message).toContain('previous file_agent block was invalid')
        const thirdRequest = streamSmartChat.mock.calls[2][0] as { message?: string; use_web_search?: boolean }
        expect(thirdRequest.message).toContain('[FILE_AGENT_CONTEXT]')
        expect(thirdRequest.use_web_search).toBe(false)
    })

    it('keeps multi-step tool loop for codex-like models', async () => {
        const writeFile = vi.fn().mockResolvedValue(undefined)
        const windowWithElectron = window as unknown as {
            electronAPI?: { writeFile: (payload: { filePath: string; content: string }) => Promise<void> }
        }
        windowWithElectron.electronAPI = { writeFile }

        streamSmartChat.mockImplementation(async function* (request: { message?: string }) {
            if (typeof request.message === 'string' && request.message.includes('[FILE_AGENT_CONTEXT]')) {
                yield { content: buildPlannerFinalAnswer('Final answer from codex follow-up step') }
                return
            }

            yield { content: buildPlannerToolRequest([{ type: 'write_file', path: 'README.md', content: 'Codex step 1' }]) }
        })

        renderChatPanel(
            {
                ...EN_SETTINGS,
                model: 'gpt-5.1-codex-mini',
                defaultAiFileMode: true,
                autoApproveAiFileActions: true
            },
            { localRootPath: 'D:\\PigTex' }
        )

        fireEvent.change(screen.getByPlaceholderText(/Ask me anything/i), {
            target: { value: 'hãy sửa README bằng codex mode' }
        })
        fireEvent.click(screen.getByTitle('Send'))

        await waitFor(() => expect(writeFile).toHaveBeenCalledTimes(1))
        await waitFor(() => expect(streamSmartChat).toHaveBeenCalledTimes(2))
        const secondRequest = streamSmartChat.mock.calls[1][0] as { message?: string }
        expect(secondRequest.message).toContain('[FILE_AGENT_CONTEXT]')
    })

    it('routes to image generation API when image tool mode is Image without attachments', async () => {
        generateImages.mockResolvedValue({
            images: [{
                id: 'gen_1',
                filename: 'generated_1.png',
                mime_type: 'image/png',
                size: 0,
                base64_data: 'data:image/png;base64,AAAA'
            }],
            revisedPrompts: []
        })

        renderChatPanel(EN_SETTINGS)

        fireEvent.click(screen.getByTitle('Choose creation mode'))
        fireEvent.click(screen.getByText('Image'))

        fireEvent.change(screen.getByPlaceholderText('Describe the image you want to generate...'), {
            target: { value: 'a pig astronaut in watercolor style' }
        })
        fireEvent.click(screen.getByTitle('Send'))

        await waitFor(() => expect(generateImages).toHaveBeenCalledTimes(1))
        await waitFor(() => expect(createConversation).toHaveBeenCalledTimes(1))
        await waitFor(() => expect(addConversationMessage).toHaveBeenCalledTimes(2))
        expect(streamSmartChat).not.toHaveBeenCalled()
    })

    it('routes to image edit API when image tool mode is Image with an attachment', async () => {
        fileToBase64.mockResolvedValue({
            id: 'img_1',
            filename: 'source.png',
            mime_type: 'image/png',
            size: 4,
            base64_data: 'data:image/png;base64,AAAA'
        })
        editImage.mockResolvedValue({
            images: [{
                id: 'edit_1',
                filename: 'edited_1.png',
                mime_type: 'image/png',
                size: 0,
                base64_data: 'data:image/png;base64,BBBB'
            }],
            revisedPrompts: []
        })

        const { container } = renderChatPanel(EN_SETTINGS)

        fireEvent.click(screen.getByTitle('Choose creation mode'))
        fireEvent.click(screen.getByText('Image'))

        const imageInput = container.querySelector('input[type="file"][accept*="image/jpeg"]')
        expect(imageInput).not.toBeNull()

        const imageFile = new File(['img'], 'source.png', { type: 'image/png' })
        fireEvent.change(imageInput!, { target: { files: [imageFile] } })

        await waitFor(() => expect(fileToBase64).toHaveBeenCalledTimes(1))

        fireEvent.change(screen.getByPlaceholderText('Describe how to edit the first attached image...'), {
            target: { value: 'remove the background and brighten it' }
        })
        fireEvent.click(screen.getByTitle('Send'))

        await waitFor(() => expect(editImage).toHaveBeenCalledTimes(1))
        await waitFor(() => expect(createConversation).toHaveBeenCalledTimes(1))
        await waitFor(() => expect(addConversationMessage).toHaveBeenCalledTimes(2))
        expect(generateImages).not.toHaveBeenCalled()
        expect(editImage.mock.calls[0]?.[0]).toBe('remove the background and brighten it')
        expect(editImage.mock.calls[0]?.[1]).toMatchObject({
            filename: 'source.png',
            mime_type: 'image/png'
        })
    })

    it('routes to voice generation API when media tool mode is Voice', async () => {
        synthesizeSpeech.mockResolvedValue(new Blob(['voice'], { type: 'audio/mpeg' }))

        renderChatPanel(EN_SETTINGS)

        fireEvent.click(screen.getByTitle('Choose creation mode'))
        fireEvent.click(screen.getByText('Voice'))

        fireEvent.change(screen.getByPlaceholderText(STUDIO_MODEL_PLACEHOLDER), {
            target: { value: 'gpt-4o-mini-tts' }
        })
        fireEvent.change(screen.getByPlaceholderText('Write the script or voiceover you want PigTex to read...'), {
            target: { value: 'Read this as a calm product trailer voiceover.' }
        })
        fireEvent.click(screen.getByTitle('Send'))

        await waitFor(() => expect(synthesizeSpeech).toHaveBeenCalledTimes(1))
        await waitFor(() => expect(createConversation).toHaveBeenCalledTimes(1))
        await waitFor(() => expect(addConversationMessage).toHaveBeenCalledTimes(2))
        expect(generateVideo).not.toHaveBeenCalled()
        expect(streamSmartChat).not.toHaveBeenCalled()
        expect(synthesizeSpeech.mock.calls[0]?.[0]).toMatchObject({
            model: 'gpt-4o-mini-tts',
            input: 'Read this as a calm product trailer voiceover.'
        })
    })

    it('switches to an Alibaba-safe default voice preset when the provider is Alibaba', async () => {
        renderChatPanel({
            ...EN_SETTINGS,
            apiProvider: 'alibaba',
            customEndpoint: 'alibaba'
        })

        fireEvent.click(screen.getByTitle('Choose creation mode'))
        fireEvent.click(screen.getByText('Voice'))

        await waitFor(() => expect(screen.getByDisplayValue('Cherry')).toBeInTheDocument())
    })

    it('routes to video generation API when media tool mode is Video', async () => {
        generateVideo.mockResolvedValue({
            data: [{
                url: 'https://example.com/generated.mp4',
                mime_type: 'video/mp4'
            }]
        })

        renderChatPanel(EN_SETTINGS)

        fireEvent.click(screen.getByTitle('Choose creation mode'))
        fireEvent.click(screen.getByText('Video'))

        fireEvent.change(screen.getByPlaceholderText(STUDIO_MODEL_PLACEHOLDER), {
            target: { value: 'sora-2' }
        })
        fireEvent.change(screen.getByPlaceholderText('Describe the video you want to generate...'), {
            target: { value: 'A product hero shot slowly rotating on a reflective pedestal.' }
        })
        fireEvent.click(screen.getByTitle('Send'))

        await waitFor(() => expect(generateVideo).toHaveBeenCalledTimes(1))
        await waitFor(() => expect(createConversation).toHaveBeenCalledTimes(1))
        await waitFor(() => expect(addConversationMessage).toHaveBeenCalledTimes(2))
        await waitFor(() => expect(resolveProtectedMediaSrc).toHaveBeenCalledWith('https://example.com/generated.mp4'))
        expect(synthesizeSpeech).not.toHaveBeenCalled()
        expect(streamSmartChat).not.toHaveBeenCalled()
        expect(generateVideo.mock.calls[0]?.[0]).toBe('A product hero shot slowly rotating on a reflective pedestal.')
        expect(generateVideo.mock.calls[0]?.[1]).toMatchObject({
            model: 'sora-2',
            aspect_ratio: '16:9',
            duration: '5'
        })
    })

    it('polls queued video tasks until a playable result is available', async () => {
        generateVideo.mockResolvedValue({
            task_id: 'task_video_123',
            task_status: 'QUEUED',
            data: []
        })
        let resolveTaskResult: ((value: {
            task_id: string
            task_status: string
            data: Array<{ url: string; mime_type: string }>
        }) => void) | null = null
        getVideoGenerationTask.mockImplementation(() => new Promise(resolve => {
            resolveTaskResult = resolve
        }))

        renderChatPanel(EN_SETTINGS)

        fireEvent.click(screen.getByTitle('Choose creation mode'))
        fireEvent.click(screen.getByText('Video'))

        fireEvent.change(screen.getByPlaceholderText(STUDIO_MODEL_PLACEHOLDER), {
            target: { value: 'sora-2' }
        })
        fireEvent.change(screen.getByPlaceholderText('Describe the video you want to generate...'), {
            target: { value: 'Launch reveal with slow dolly-in camera motion.' }
        })
        fireEvent.click(screen.getByTitle('Send'))

        await waitFor(() => expect(generateVideo).toHaveBeenCalledTimes(1))
        await waitFor(() => expect(addConversationMessage).toHaveBeenCalledTimes(2))
        await waitFor(() => expect(getVideoGenerationTask).toHaveBeenCalledTimes(1))
        await waitFor(() => {
            expect(screen.getByRole('status', { name: 'Assistant is responding' })).toBeInTheDocument()
        })

        expect(screen.queryByText(/Video task is QUEUED\./i)).not.toBeInTheDocument()
        expect(updateConversationMessage).not.toHaveBeenCalled()

        if (!resolveTaskResult) {
            throw new Error('Expected queued video poll to be waiting for a result.')
        }

        resolveTaskResult({
            task_id: 'task_video_123',
            task_status: 'SUCCEEDED',
            data: [{
                url: 'https://example.com/final.mp4',
                mime_type: 'video/mp4'
            }]
        })

        await waitFor(() => expect(updateConversationMessage).toHaveBeenCalledTimes(1))
        await waitFor(() => expect(document.querySelector('video')).not.toBeNull())

        expect(addConversationMessage.mock.calls[1]?.[2]).toContain('PIGTEX_VIDEO_TASK')
        expect(updateConversationMessage.mock.calls[0]?.[2]).toContain('PIGTEX_MEDIA')
        expect(updateConversationMessage.mock.calls[0]?.[1]).toBe('msg_test')
    })

    it('keeps polling when the initial video response is only a preview clip for a pending task', async () => {
        generateVideo.mockResolvedValue({
            task_id: 'task_video_preview',
            task_status: 'RUNNING',
            data: [{
                url: 'https://example.com/preview.mp4',
                mime_type: 'video/mp4'
            }]
        })
        let resolveTaskResult: ((value: {
            task_id: string
            task_status: string
            data: Array<{ url: string; mime_type: string }>
        }) => void) | null = null
        getVideoGenerationTask.mockImplementation(() => new Promise(resolve => {
            resolveTaskResult = resolve
        }))

        renderChatPanel(EN_SETTINGS)

        fireEvent.click(screen.getByTitle('Choose creation mode'))
        fireEvent.click(screen.getByText('Video'))

        fireEvent.change(screen.getByPlaceholderText(STUDIO_MODEL_PLACEHOLDER), {
            target: { value: 'sora-2' }
        })
        fireEvent.change(screen.getByPlaceholderText('Describe the video you want to generate...'), {
            target: { value: 'Show a low-res preview first, then replace it with the final render.' }
        })
        fireEvent.click(screen.getByTitle('Send'))

        await waitFor(() => expect(generateVideo).toHaveBeenCalledTimes(1))
        await waitFor(() => expect(getVideoGenerationTask).toHaveBeenCalledTimes(1))
        await waitFor(() => expect(screen.getByTitle('Stop generating')).toBeInTheDocument())

        expect(addConversationMessage.mock.calls[1]?.[2]).toContain('PIGTEX_MEDIA')
        expect(addConversationMessage.mock.calls[1]?.[2]).toContain('PIGTEX_VIDEO_TASK')
        expect(addConversationMessage.mock.calls[1]?.[2]).toContain('preview.mp4')

        if (!resolveTaskResult) {
            throw new Error('Expected preview video poll to be waiting for a final result.')
        }

        resolveTaskResult({
            task_id: 'task_video_preview',
            task_status: 'SUCCEEDED',
            data: [{
                url: 'https://example.com/final-preview-upgraded.mp4',
                mime_type: 'video/mp4'
            }]
        })

        await waitFor(() => expect(updateConversationMessage).toHaveBeenCalledTimes(1))
        expect(updateConversationMessage.mock.calls[0]?.[2]).toContain('final-preview-upgraded.mp4')
        expect(updateConversationMessage.mock.calls[0]?.[2]).not.toContain('PIGTEX_VIDEO_TASK')
    })

    it('keeps input locked and hides send while queued video generation is still pending', async () => {
        generateVideo.mockResolvedValue({
            task_id: 'task_video_123',
            task_status: 'QUEUED',
            data: []
        })
        let resolveTaskResult: ((value: {
            task_id: string
            task_status: string
            data: Array<{ url: string; mime_type: string }>
        }) => void) | null = null
        getVideoGenerationTask.mockImplementation(() => new Promise(resolve => {
            resolveTaskResult = resolve
        }))

        renderChatPanel(EN_SETTINGS)

        fireEvent.click(screen.getByTitle('Choose creation mode'))
        fireEvent.click(screen.getByText('Video'))

        fireEvent.change(screen.getByPlaceholderText(STUDIO_MODEL_PLACEHOLDER), {
            target: { value: 'sora-2' }
        })
        fireEvent.change(screen.getByPlaceholderText('Describe the video you want to generate...'), {
            target: { value: 'Keep the input locked until video polling finishes.' }
        })
        fireEvent.click(screen.getByTitle('Send'))

        await waitFor(() => expect(generateVideo).toHaveBeenCalledTimes(1))
        await waitFor(() => expect(getVideoGenerationTask).toHaveBeenCalledTimes(1))
        await waitFor(() => expect(screen.getByTitle('Stop generating')).toBeInTheDocument())

        expect(screen.queryByTitle('Send')).toBeNull()
        expect((screen.getByPlaceholderText('Describe the video you want to generate...') as HTMLTextAreaElement).disabled).toBe(true)

        if (!resolveTaskResult) {
            throw new Error('Expected queued video poll to be waiting for a result.')
        }

        resolveTaskResult({
            task_id: 'task_video_123',
            task_status: 'SUCCEEDED',
            data: [{
                url: 'https://example.com/final-lock-check.mp4',
                mime_type: 'video/mp4'
            }]
        })

        await waitFor(() => expect(screen.getByTitle('Send')).toBeInTheDocument())
        expect(screen.queryByTitle('Stop generating')).toBeNull()
    })

    it('shows the provider failure reason for a terminal video task without polling again', async () => {
        generateVideo.mockResolvedValue({
            task_id: 'task_video_failed',
            task_status: 'FAILED',
            error_message: 'Prompt violates the provider safety policy.',
            data: []
        })

        renderChatPanel(EN_SETTINGS)

        fireEvent.click(screen.getByTitle('Choose creation mode'))
        fireEvent.click(screen.getByText('Video'))

        fireEvent.change(screen.getByPlaceholderText(STUDIO_MODEL_PLACEHOLDER), {
            target: { value: 'sora-2' }
        })
        fireEvent.change(screen.getByPlaceholderText('Describe the video you want to generate...'), {
            target: { value: 'Generate a restricted brand promo.' }
        })
        fireEvent.click(screen.getByTitle('Send'))

        await waitFor(() => expect(generateVideo).toHaveBeenCalledTimes(1))
        await waitFor(() => {
            expect(screen.getByText('Video task ended with status FAILED: Prompt violates the provider safety policy.')).toBeTruthy()
        })

        expect(getVideoGenerationTask).not.toHaveBeenCalled()
    })

    it('surfaces the provider failure reason when a queued video task later fails', async () => {
        generateVideo.mockResolvedValue({
            task_id: 'task_video_123',
            task_status: 'QUEUED',
            data: []
        })
        getVideoGenerationTask.mockResolvedValue({
            task_id: 'task_video_123',
            task_status: 'FAILED',
            error_message: 'Prompt violates the provider safety policy.',
            data: []
        })

        renderChatPanel(EN_SETTINGS)

        fireEvent.click(screen.getByTitle('Choose creation mode'))
        fireEvent.click(screen.getByText('Video'))

        fireEvent.change(screen.getByPlaceholderText(STUDIO_MODEL_PLACEHOLDER), {
            target: { value: 'sora-2' }
        })
        fireEvent.change(screen.getByPlaceholderText('Describe the video you want to generate...'), {
            target: { value: 'Launch video with blocked content.' }
        })
        fireEvent.click(screen.getByTitle('Send'))

        await waitFor(() => expect(generateVideo).toHaveBeenCalledTimes(1))
        await waitFor(() => expect(getVideoGenerationTask).toHaveBeenCalledTimes(1))
        await waitFor(() => expect(updateConversationMessage).toHaveBeenCalledTimes(1))

        expect(updateConversationMessage.mock.calls[0]?.[2]).toContain('Prompt violates the provider safety policy.')
        await waitFor(() => {
            expect(screen.getByText('Video task ended with status FAILED: Prompt violates the provider safety policy.')).toBeTruthy()
        })
    })

    it('shows an actionable backend message when the local desktop backend is unreachable', async () => {
        streamSmartChat.mockImplementation(async function* () {
            throw new TypeError('Failed to fetch')
        })
        diagnoseApiConnectivityIssue.mockResolvedValue({
            kind: 'backend_unreachable',
            apiBaseUrl: 'http://localhost:3001/api',
            isLoopback: true
        })

        renderChatPanel(EN_SETTINGS)

        fireEvent.change(screen.getByPlaceholderText('Ask me anything...'), {
            target: { value: 'backend offline check' }
        })
        fireEvent.click(screen.getByTitle('Send'))

        const expectedMessage = 'Cannot connect to PigTex backend at http://localhost:3001/api. Start the local backend and try again.'

        await waitFor(() => expect(diagnoseApiConnectivityIssue).toHaveBeenCalledTimes(1))
        await waitFor(() => expect(showError).toHaveBeenCalledWith(expectedMessage))
        await waitFor(() => expect(screen.getByText(expectedMessage)).toBeInTheDocument())
    })
})
