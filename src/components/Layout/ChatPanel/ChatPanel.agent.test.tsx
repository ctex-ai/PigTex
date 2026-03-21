import React from 'react'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { fireEvent, render, screen, waitFor, cleanup } from '@testing-library/react'

import ChatPanel from './ChatPanel'
import { I18nProvider } from '../../../contexts/I18nContext'
import { DEFAULT_PIGTEX_SETTINGS, savePigTexSettings } from '../../../services/settings'

const EN_SETTINGS = {
    ...DEFAULT_PIGTEX_SETTINGS,
    language: 'en' as const
}

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
const getLocalConversation = vi.fn()
const createConversation = vi.fn()
const addConversationMessage = vi.fn()
const fileToBase64 = vi.fn()
const uploadFiles = vi.fn()
const generateImages = vi.fn()
const editImage = vi.fn()
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
    getLocalConversation: (...args: unknown[]) => getLocalConversation(...args),
    createConversation: (...args: unknown[]) => createConversation(...args),
    addConversationMessage: (...args: unknown[]) => addConversationMessage(...args),
    fileToBase64: (...args: unknown[]) => fileToBase64(...args),
    uploadFiles: (...args: unknown[]) => uploadFiles(...args),
    generateImages: (...args: unknown[]) => generateImages(...args),
    editImage: (...args: unknown[]) => editImage(...args),
    transportSupportsCapability: (...args: unknown[]) => transportSupportsCapability(...args),
    filterModelsByCapability: (...args: unknown[]) => filterModelsByCapability(...args),
    getTransportDefaultModelId: (...args: unknown[]) => getTransportDefaultModelId(...args),
    modelSupportsCapability: (...args: unknown[]) => modelSupportsCapability(...args),
    pickModelForCapability: (...args: unknown[]) => pickModelForCapability(...args),
    isPaygImageModel: () => false,
    resolveImageUrl: (url: string) => url,
    resolveProtectedImageSrc: async (url: string) => ({ src: url }),
    resolveProtectedMediaSrc: (...args: unknown[]) => resolveProtectedMediaSrc(...args)
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

type FsEntry = {
    name: string
    path: string
    type: 'file' | 'directory'
    size: number
    mtimeMs: number
}

type ElectronApiMock = {
    listDirectory?: (payload: { rootPath: string; dirPath: string }) => Promise<FsEntry[]>
    readFile?: (path: string) => Promise<{ content: string; size: number; mtimeMs: number }>
    writeFile?: (payload: { filePath: string; content: string }) => Promise<unknown>
    createFolder?: (payload: { parentPath: string; folderName: string }) => Promise<void>
    createFile?: (payload: { parentPath: string; fileName: string; content: string }) => Promise<void>
    deletePath?: (payload: { targetPath: string }) => Promise<void>
    renamePath?: (payload: { targetPath: string; newName: string }) => Promise<{ path: string }>
}

const LOCAL_ROOT = 'D:\\Workspace'

const renderAgentPanel = (modelId: string = 'gpt-5.1-codex-mini') => {
    const settings = {
        ...EN_SETTINGS,
        model: modelId,
        defaultAiFileMode: true,
        autoApproveAiFileActions: true
    }
    savePigTexSettings(settings)
    render(
        <I18nProvider>
            <ChatPanel
                variant="centered"
                settings={settings}
                localRootPath={LOCAL_ROOT}
                onSettingsChange={vi.fn()}
            />
        </I18nProvider>
    )
}

const sendPrompt = async (text: string) => {
    fireEvent.change(screen.getByRole('textbox'), {
        target: { value: text }
    })
    fireEvent.click(screen.getByTitle('Send'))
    await waitFor(() => expect(streamSmartChat).toHaveBeenCalled())
}

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

describe('ChatPanel AI file agent integration', () => {
    beforeEach(() => {
        window.localStorage.clear()
        window.sessionStorage.clear()
        getModels.mockReset()
        streamSmartChat.mockReset()
        getLocalConversation.mockReset()
        createConversation.mockReset()
        addConversationMessage.mockReset()
        fileToBase64.mockReset()
        uploadFiles.mockReset()
        generateImages.mockReset()
        editImage.mockReset()
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
        createConversation.mockResolvedValue({
            id: 'conv_agent',
            title: 'Agent Test',
            summary: null,
            total_messages: 0,
            workspace_id: null
        })
        addConversationMessage.mockResolvedValue({
            id: 'msg_agent',
            role: 'user',
            content: 'saved',
            model: null,
            token_count: 0
        })
        uploadFiles.mockResolvedValue([])
        generateImages.mockResolvedValue({ images: [], revisedPrompts: [] })
        editImage.mockResolvedValue({ images: [], revisedPrompts: [] })
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

    it('blocks guessed index.html after listing and feeds structured error back to the next step', async () => {
        const listDirectory = vi.fn().mockResolvedValue([
            {
                name: 'landing.html',
                path: `${LOCAL_ROOT}\\landing.html`,
                type: 'file',
                size: 321,
                mtimeMs: 100
            },
            {
                name: 'assets',
                path: `${LOCAL_ROOT}\\assets`,
                type: 'directory',
                size: 0,
                mtimeMs: 101
            }
        ] satisfies FsEntry[])
        const readFile = vi.fn()
        const windowWithElectron = window as unknown as { electronAPI?: ElectronApiMock }
        windowWithElectron.electronAPI = {
            listDirectory,
            readFile,
            writeFile: vi.fn().mockResolvedValue(undefined),
            createFolder: vi.fn().mockResolvedValue(undefined),
            createFile: vi.fn().mockResolvedValue(undefined),
            deletePath: vi.fn().mockResolvedValue(undefined),
            renamePath: vi.fn().mockResolvedValue({ path: `${LOCAL_ROOT}\\renamed.txt` })
        }

        let agentTurn = 0
        streamSmartChat.mockImplementation(async function* () {
            if (agentTurn === 0) {
                agentTurn += 1
                yield { content: buildPlannerToolRequest([{ type: 'list_directory', path: '.' }]) }
                return
            }
            if (agentTurn === 1) {
                agentTurn += 1
                yield { content: buildPlannerToolRequest([{ type: 'read_file', path: 'index.html' }]) }
                return
            }

            yield { content: buildPlannerFinalAnswer('Stopped because the file was not listed.') }
        })

        renderAgentPanel()
        await sendPrompt('List thư mục hiện tại trước rồi đọc index.html nếu có.')

        await waitFor(() => expect(streamSmartChat).toHaveBeenCalledTimes(3))
        expect(readFile).not.toHaveBeenCalled()
        await waitFor(() => expect(screen.getByTestId('message-renderer').textContent).toContain('Stopped because the file was not listed.'))

        const secondRequest = streamSmartChat.mock.calls[1][0] as { message?: string; use_web_search?: boolean }
        expect(secondRequest.message).toContain('"exactEntries"')
        expect(secondRequest.message).toContain('landing.html')
        expect(secondRequest.message).toContain('assets/')
        expect(secondRequest.use_web_search).toBe(false)

        const thirdRequest = streamSmartChat.mock.calls[2][0] as { message?: string; use_web_search?: boolean }
        expect(thirdRequest.message).toContain('Path is not present in the latest directory listing')
        expect(thirdRequest.message).toContain('landing.html')
        expect(thirdRequest.message).toContain('assets/')
        expect(thirdRequest.use_web_search).toBe(false)
    })

    it('blocks guessed subfolder listings after a parent folder was already listed', async () => {
        const listDirectory = vi.fn().mockResolvedValue([
            {
                name: 'bauer-cuo',
                path: `${LOCAL_ROOT}\\bauer-cuo`,
                type: 'directory',
                size: 0,
                mtimeMs: 101
            }
        ] satisfies FsEntry[])
        const windowWithElectron = window as unknown as { electronAPI?: ElectronApiMock }
        windowWithElectron.electronAPI = {
            listDirectory,
            readFile: vi.fn().mockResolvedValue({
                content: '',
                size: 0,
                mtimeMs: 102
            }),
            writeFile: vi.fn().mockResolvedValue(undefined),
            createFolder: vi.fn().mockResolvedValue(undefined),
            createFile: vi.fn().mockResolvedValue(undefined),
            deletePath: vi.fn().mockResolvedValue(undefined),
            renamePath: vi.fn().mockResolvedValue({ path: `${LOCAL_ROOT}\\renamed.txt` })
        }

        let agentTurn = 0
        streamSmartChat.mockImplementation(async function* () {
            if (agentTurn === 0) {
                agentTurn += 1
                yield { content: buildPlannerToolRequest([{ type: 'list_directory', path: '.' }]) }
                return
            }
            if (agentTurn === 1) {
                agentTurn += 1
                yield { content: buildPlannerToolRequest([{ type: 'list_directory', path: 'bau_cu' }]) }
                return
            }

            yield { content: buildPlannerFinalAnswer('Stopped because the guessed folder was not listed.') }
        })

        renderAgentPanel()
        await sendPrompt('List thư mục gốc rồi chỉ được mở subfolder có thật.')

        await waitFor(() => expect(streamSmartChat).toHaveBeenCalledTimes(3))
        expect(listDirectory.mock.calls.some(
            ([payload]) => payload?.dirPath === `${LOCAL_ROOT}\\bau_cu`
        )).toBe(false)
        await waitFor(() => expect(screen.getByTestId('message-renderer').textContent).toContain('Stopped because the guessed folder was not listed.'))

        const thirdRequest = streamSmartChat.mock.calls[2][0] as { message?: string; use_web_search?: boolean }
        expect(thirdRequest.message).toContain('Path is not present in the latest directory listing')
        expect(thirdRequest.message).toContain('bauer-cuo/')
        expect(thirdRequest.use_web_search).toBe(false)
    })

    it('skips retrying the same missing folder action on the next agent step', async () => {
        const listDirectory = vi.fn().mockImplementation(async ({ dirPath }: { dirPath: string }) => {
            if (dirPath === LOCAL_ROOT || dirPath === `${LOCAL_ROOT}\\.`) {
                return [
                    {
                        name: 'capcut_export',
                        path: `${LOCAL_ROOT}\\capcut_export`,
                        type: 'directory',
                        size: 0,
                        mtimeMs: 101
                    },
                    {
                        name: 'script_master.txt',
                        path: `${LOCAL_ROOT}\\script_master.txt`,
                        type: 'file',
                        size: 0,
                        mtimeMs: 102
                    }
                ] satisfies FsEntry[]
            }
            throw new Error(`Folder not found: ${dirPath}`)
        })
        let agentTurn = 0
        const windowWithElectron = window as unknown as { electronAPI?: ElectronApiMock }
        windowWithElectron.electronAPI = {
            listDirectory,
            readFile: vi.fn().mockResolvedValue({
                content: '',
                size: 0,
                mtimeMs: 103
            }),
            writeFile: vi.fn().mockResolvedValue(undefined),
            createFolder: vi.fn().mockResolvedValue(undefined),
            createFile: vi.fn().mockResolvedValue(undefined),
            deletePath: vi.fn().mockResolvedValue(undefined),
            renamePath: vi.fn().mockResolvedValue({ path: `${LOCAL_ROOT}\\renamed.txt` })
        }

        streamSmartChat.mockImplementation(async function* () {
            if (agentTurn === 0) {
                agentTurn += 1
                yield { content: buildPlannerToolRequest([{ type: 'list_directory', path: '.' }]) }
                return
            }
            if (agentTurn === 1) {
                agentTurn += 1
                yield { content: buildPlannerToolRequest([{ type: 'list_directory', path: 'bau_cu' }]) }
                return
            }
            if (agentTurn === 2) {
                agentTurn += 1
                yield { content: buildPlannerToolRequest([{ type: 'list_directory', path: 'bau_cu' }, { type: 'read_file', path: 'script_master.txt' }]) }
                return
            }

            yield { content: buildPlannerFinalAnswer('Recovered by using the valid root listing only.') }
        })

        renderAgentPanel()
        await sendPrompt(
            'Phân tích kỹ workspace này, nếu đoán sai folder con thì phải tự phục hồi bằng root listing hợp lệ, không được lặp lại đúng path đã fail, và chỉ tiếp tục khi còn action mới thật sự cần thiết.'
        )

        await waitFor(() => expect(streamSmartChat).toHaveBeenCalledTimes(4))
        expect(listDirectory.mock.calls.filter(
            ([payload]) => payload?.dirPath === `${LOCAL_ROOT}\\bau_cu`
        )).toHaveLength(0)
        expect(listDirectory.mock.calls.filter(
            ([payload]) => payload?.dirPath === LOCAL_ROOT
        ).length).toBeGreaterThanOrEqual(2)
        expect(windowWithElectron.electronAPI.readFile).toHaveBeenCalledWith(`${LOCAL_ROOT}\\script_master.txt`)
        await waitFor(() => expect(screen.getByTestId('message-renderer').textContent).toContain('Recovered by using the valid root listing only.'))

        const secondInternalRequest = streamSmartChat.mock.calls[2][0] as { message?: string; use_web_search?: boolean }
        expect(secondInternalRequest.use_web_search).toBe(false)
    })

    it('reads the exact HTML file returned by the directory listing', async () => {
        const listDirectory = vi.fn().mockResolvedValue([
            {
                name: 'landing.html',
                path: `${LOCAL_ROOT}\\landing.html`,
                type: 'file',
                size: 321,
                mtimeMs: 100
            }
        ] satisfies FsEntry[])
        const readFile = vi.fn().mockResolvedValue({
            content: '<html><body>Landing</body></html>',
            size: 34,
            mtimeMs: 102
        })
        const windowWithElectron = window as unknown as { electronAPI?: ElectronApiMock }
        windowWithElectron.electronAPI = {
            listDirectory,
            readFile,
            writeFile: vi.fn().mockResolvedValue(undefined),
            createFolder: vi.fn().mockResolvedValue(undefined),
            createFile: vi.fn().mockResolvedValue(undefined),
            deletePath: vi.fn().mockResolvedValue(undefined),
            renamePath: vi.fn().mockResolvedValue({ path: `${LOCAL_ROOT}\\renamed.txt` })
        }

        let agentTurn = 0
        streamSmartChat.mockImplementation(async function* () {
            if (agentTurn === 0) {
                agentTurn += 1
                yield { content: buildPlannerToolRequest([{ type: 'list_directory', path: '.' }]) }
                return
            }
            if (agentTurn === 1) {
                agentTurn += 1
                yield { content: buildPlannerToolRequest([{ type: 'read_file', path: 'landing.html' }]) }
                return
            }

            yield { content: buildPlannerFinalAnswer('Read the listed HTML file successfully.') }
        })

        renderAgentPanel()
        await sendPrompt('List thư mục rồi đọc đúng file HTML có thật.')

        await waitFor(() => expect(streamSmartChat).toHaveBeenCalledTimes(3))
        expect(readFile).toHaveBeenCalledWith(`${LOCAL_ROOT}\\landing.html`)
        const thirdRequest = streamSmartChat.mock.calls[2][0] as { message?: string }
        expect(thirdRequest.message).toContain(`"targetPath": "${LOCAL_ROOT.replace(/\\/g, '\\\\')}\\\\landing.html"`)
        expect(thirdRequest.message).toContain('<html><body>Landing</body></html>')
    })

    it('uses deterministic workspace review context for whole-folder review requests', async () => {
        const listDirectory = vi.fn().mockImplementation(async ({ dirPath }: { dirPath: string }) => {
            if (dirPath === LOCAL_ROOT) {
                return [
                    {
                        name: 'src',
                        path: `${LOCAL_ROOT}\\src`,
                        type: 'directory',
                        size: 0,
                        mtimeMs: 100
                    },
                    {
                        name: 'package.json',
                        path: `${LOCAL_ROOT}\\package.json`,
                        type: 'file',
                        size: 120,
                        mtimeMs: 101
                    },
                    {
                        name: 'image.png',
                        path: `${LOCAL_ROOT}\\image.png`,
                        type: 'file',
                        size: 1024,
                        mtimeMs: 102
                    }
                ] satisfies FsEntry[]
            }

            if (dirPath === `${LOCAL_ROOT}\\src`) {
                return [
                    {
                        name: 'main.js',
                        path: `${LOCAL_ROOT}\\src\\main.js`,
                        type: 'file',
                        size: 80,
                        mtimeMs: 103
                    }
                ] satisfies FsEntry[]
            }

            throw new Error(`Unexpected path: ${dirPath}`)
        })
        const readFile = vi.fn().mockImplementation(async (filePath: string) => {
            if (filePath === `${LOCAL_ROOT}\\package.json`) {
                return {
                    content: '{"name":"demo"}',
                    size: 15,
                    mtimeMs: 104
                }
            }

            if (filePath === `${LOCAL_ROOT}\\src\\main.js`) {
                return {
                    content: 'console.log("ok")',
                    size: 17,
                    mtimeMs: 105
                }
            }

            throw new Error(`Unexpected file read: ${filePath}`)
        })
        const windowWithElectron = window as unknown as { electronAPI?: ElectronApiMock }
        windowWithElectron.electronAPI = {
            listDirectory,
            readFile,
            writeFile: vi.fn().mockResolvedValue(undefined),
            createFolder: vi.fn().mockResolvedValue(undefined),
            createFile: vi.fn().mockResolvedValue(undefined),
            deletePath: vi.fn().mockResolvedValue(undefined),
            renamePath: vi.fn().mockResolvedValue({ path: `${LOCAL_ROOT}\\renamed.txt` })
        }

        streamSmartChat.mockImplementation(async function* (request: { message?: string }) {
            expect(request.message).toContain('[WORKSPACE_REVIEW_CONTEXT]')
            expect(request.message).toContain('Workspace root folder name: Workspace')
            expect(request.message).toContain('Workspace tree:')
            expect(request.message).toContain('package.json')
            expect(request.message).toContain('src/main.js')
            expect(request.message).not.toContain('[PIGTEX_TOOL_RESULT]')
            yield { content: 'Reviewed whole workspace successfully.' }
        })

        renderAgentPanel('qwen3-max')
        await sendPrompt('đọc toàn bộ folder luôn và review giúp tôi')

        await waitFor(() => expect(streamSmartChat).toHaveBeenCalledTimes(1))
        expect(listDirectory.mock.calls.length).toBeGreaterThanOrEqual(2)
        expect(readFile).toHaveBeenCalledWith(`${LOCAL_ROOT}\\package.json`)
        expect(readFile).toHaveBeenCalledWith(`${LOCAL_ROOT}\\src\\main.js`)
        expect(screen.getByTestId('message-renderer').textContent).toContain('Reviewed whole workspace successfully.')
    })

    it('tells the model to treat the workspace root name as the opened root during whole-folder review', async () => {
        const windowWithElectron = window as unknown as { electronAPI?: ElectronApiMock }
        windowWithElectron.electronAPI = {
            listDirectory: vi.fn().mockResolvedValue([]),
            readFile: vi.fn().mockResolvedValue({
                content: '',
                size: 0,
                mtimeMs: 100
            }),
            writeFile: vi.fn().mockResolvedValue(undefined),
            createFolder: vi.fn().mockResolvedValue(undefined),
            createFile: vi.fn().mockResolvedValue(undefined),
            deletePath: vi.fn().mockResolvedValue(undefined),
            renamePath: vi.fn().mockResolvedValue({ path: `${LOCAL_ROOT}\\renamed.txt` })
        }

        streamSmartChat.mockImplementation(async function* (request: { message?: string }) {
            expect(request.message).toContain('Workspace root folder name: Workspace')
            expect(request.message).toContain('treat that as the opened root folder')
            expect(request.message).toContain('hãy đọc toàn bộ folder Workspace')
            yield { content: 'Reviewed the opened workspace root.' }
        })

        renderAgentPanel('qwen3-max')
        await sendPrompt('hãy đọc toàn bộ folder Workspace')

        await waitFor(() => expect(streamSmartChat).toHaveBeenCalledTimes(1))
        expect(screen.getByTestId('message-renderer').textContent).toContain('Reviewed the opened workspace root.')
    })

    it('supports the new file_agent planner protocol with hidden internal turns', async () => {
        const listDirectory = vi.fn().mockResolvedValue([
            {
                name: 'README.md',
                path: `${LOCAL_ROOT}\\README.md`,
                type: 'file',
                size: 120,
                mtimeMs: 101
            }
        ] satisfies FsEntry[])
        const windowWithElectron = window as unknown as { electronAPI?: ElectronApiMock }
        windowWithElectron.electronAPI = {
            listDirectory,
            readFile: vi.fn().mockResolvedValue({
                content: '# Demo',
                size: 6,
                mtimeMs: 102
            }),
            writeFile: vi.fn().mockResolvedValue(undefined),
            createFolder: vi.fn().mockResolvedValue(undefined),
            createFile: vi.fn().mockResolvedValue(undefined),
            deletePath: vi.fn().mockResolvedValue(undefined),
            renamePath: vi.fn().mockResolvedValue({ path: `${LOCAL_ROOT}\\renamed.txt` })
        }

        streamSmartChat.mockImplementation(async function* (request: { message?: string; runtime_instruction?: string; use_web_search?: boolean }) {
            const message = String(request.message || '')
            if (message.includes('[FILE_AGENT_CONTEXT]')) {
                expect(request.use_web_search).toBe(false)
                expect(message).not.toContain('[PIGTEX_TOOL_RESULT]')
                yield {
                    content: [
                        '```file_agent',
                        '{"kind":"final_answer","message":"Da list xong va tong hop ket qua."}',
                        '```'
                    ].join('\n')
                }
                return
            }

            expect(request.runtime_instruction).toContain('```file_agent')
            yield {
                content: [
                    '```file_agent',
                    '{"kind":"tool_request","actions":[{"type":"list_directory","path":"."}]}',
                    '```'
                ].join('\n')
            }
        })

        renderAgentPanel('qwen3-max')
        await sendPrompt('hãy xem workspace này')

        await waitFor(() => expect(streamSmartChat).toHaveBeenCalledTimes(2))
        expect(listDirectory.mock.calls.length).toBeGreaterThanOrEqual(1)
        await waitFor(() => expect(screen.getByTestId('message-renderer').textContent).toContain('Da list xong va tong hop ket qua.'))
    })

    it('continues after list_directory in chat-model mode', async () => {
        const listDirectory = vi.fn().mockResolvedValue([
            {
                name: 'main.js',
                path: `${LOCAL_ROOT}\\main.js`,
                type: 'file',
                size: 321,
                mtimeMs: 100
            },
            {
                name: 'package.json',
                path: `${LOCAL_ROOT}\\package.json`,
                type: 'file',
                size: 120,
                mtimeMs: 101
            }
        ] satisfies FsEntry[])
        const windowWithElectron = window as unknown as { electronAPI?: ElectronApiMock }
        windowWithElectron.electronAPI = {
            listDirectory,
            readFile: vi.fn().mockResolvedValue({
                content: '',
                size: 0,
                mtimeMs: 102
            }),
            writeFile: vi.fn().mockResolvedValue(undefined),
            createFolder: vi.fn().mockResolvedValue(undefined),
            createFile: vi.fn().mockResolvedValue(undefined),
            deletePath: vi.fn().mockResolvedValue(undefined),
            renamePath: vi.fn().mockResolvedValue({ path: `${LOCAL_ROOT}\\renamed.txt` })
        }

        let agentTurn = 0
        streamSmartChat.mockImplementation(async function* () {
            if (agentTurn === 0) {
                agentTurn += 1
                yield { content: buildPlannerToolRequest([{ type: 'list_directory', path: '.' }]) }
                return
            }

            yield { content: buildPlannerFinalAnswer('Root listed successfully.') }
        })

        renderAgentPanel('qwen3.5-flash')
        await sendPrompt('List thư mục hiện tại giúp tôi.')

        await waitFor(() => expect(streamSmartChat).toHaveBeenCalledTimes(2))
        expect(listDirectory).toHaveBeenCalled()
        await waitFor(() => expect(screen.getByTestId('message-renderer').textContent).toContain('Root listed successfully.'))

        const secondRequest = streamSmartChat.mock.calls[1][0] as { message?: string; use_web_search?: boolean }
        expect(secondRequest.use_web_search).toBe(false)
        expect(secondRequest.message).toContain('"exactEntries"')
        expect(secondRequest.message).toContain('main.js')
        expect(secondRequest.message).toContain('package.json')
    })

    it('continues after write_file in chat-model mode to finish the response', async () => {
        const writeFile = vi.fn().mockResolvedValue(undefined)
        const updatedPaths: string[] = []
        const handleFileUpdated = (event: Event) => {
            const detail = (event as CustomEvent<{ targetPath?: string }>).detail
            if (detail?.targetPath) {
                updatedPaths.push(detail.targetPath)
            }
        }
        window.addEventListener('pigtex:file-content-updated', handleFileUpdated as EventListener)
        const windowWithElectron = window as unknown as { electronAPI?: ElectronApiMock }
        windowWithElectron.electronAPI = {
            writeFile,
            readFile: vi.fn().mockResolvedValue({
                content: '',
                size: 0,
                mtimeMs: 102
            }),
            listDirectory: vi.fn().mockResolvedValue([]),
            createFolder: vi.fn().mockResolvedValue(undefined),
            createFile: vi.fn().mockResolvedValue(undefined),
            deletePath: vi.fn().mockResolvedValue(undefined),
            renamePath: vi.fn().mockResolvedValue({ path: `${LOCAL_ROOT}\\renamed.txt` })
        }

        let agentTurn = 0
        streamSmartChat.mockImplementation(async function* () {
            if (agentTurn === 0) {
                agentTurn += 1
                yield { content: buildPlannerToolRequest([{ type: 'write_file', path: 'README.md', content: '# PigTex' }]) }
                return
            }

            yield { content: buildPlannerFinalAnswer('Da ghi file xong va tiep tuc hoan tat phan hoi.') }
        })

        renderAgentPanel('qwen3.5-flash')
        await sendPrompt('Tao README.md roi tiep tuc tra loi sau khi ghi xong.')

        await waitFor(() => expect(writeFile).toHaveBeenCalled())
        await waitFor(() => expect(streamSmartChat).toHaveBeenCalledTimes(2))
        await waitFor(() => expect(screen.getByTestId('message-renderer').textContent).toContain('Da ghi file xong va tiep tuc hoan tat phan hoi.'))
        expect(updatedPaths).toContain(`${LOCAL_ROOT}\\README.md`)

        const secondRequest = streamSmartChat.mock.calls[1][0] as { message?: string; use_web_search?: boolean }
        expect(secondRequest.use_web_search).toBe(false)
        expect(secondRequest.message).toContain('[FILE_AGENT_CONTEXT]')
        expect(secondRequest.message).toContain('README.md')
        window.removeEventListener('pigtex:file-content-updated', handleFileUpdated as EventListener)
    })

    it('allows create-then-read flow for a freshly written file in the same agent session', async () => {
        const writeFile = vi.fn().mockResolvedValue(undefined)
        const readFile = vi.fn().mockResolvedValue({
            content: '<html><body>ok</body></html>',
            size: 28,
            mtimeMs: 111
        })
        const windowWithElectron = window as unknown as { electronAPI?: ElectronApiMock }
        windowWithElectron.electronAPI = {
            writeFile,
            readFile,
            listDirectory: vi.fn().mockResolvedValue([]),
            createFolder: vi.fn().mockResolvedValue(undefined),
            createFile: vi.fn().mockResolvedValue(undefined),
            deletePath: vi.fn().mockResolvedValue(undefined),
            renamePath: vi.fn().mockResolvedValue({ path: `${LOCAL_ROOT}\\renamed.txt` })
        }

        let agentTurn = 0
        streamSmartChat.mockImplementation(async function* () {
            if (agentTurn === 0) {
                agentTurn += 1
                yield { content: buildPlannerToolRequest([{ type: 'write_file', path: 'test-pigtex-index.html', content: '<html><body>ok</body></html>' }]) }
                return
            }
            if (agentTurn === 1) {
                agentTurn += 1
                yield { content: buildPlannerToolRequest([{ type: 'read_file', path: 'test-pigtex-index.html' }]) }
                return
            }

            yield { content: buildPlannerFinalAnswer('Verified the file after creating it.') }
        })

        renderAgentPanel()
        await sendPrompt('Tạo file test-pigtex-index.html rồi đọc lại chính file đó.')

        await waitFor(() => expect(streamSmartChat).toHaveBeenCalledTimes(3))
        expect(writeFile).toHaveBeenCalled()
        expect(readFile).toHaveBeenCalledWith(`${LOCAL_ROOT}\\test-pigtex-index.html`)
    })

    it('uses exact entries from a subfolder listing before reading a nested file', async () => {
        const listDirectory = vi.fn().mockResolvedValue([
            {
                name: 'hero.html',
                path: `${LOCAL_ROOT}\\assets\\hero.html`,
                type: 'file',
                size: 210,
                mtimeMs: 112
            }
        ] satisfies FsEntry[])
        const readFile = vi.fn().mockResolvedValue({
            content: '<section>Hero</section>',
            size: 23,
            mtimeMs: 113
        })
        const windowWithElectron = window as unknown as { electronAPI?: ElectronApiMock }
        windowWithElectron.electronAPI = {
            listDirectory,
            readFile,
            writeFile: vi.fn().mockResolvedValue(undefined),
            createFolder: vi.fn().mockResolvedValue(undefined),
            createFile: vi.fn().mockResolvedValue(undefined),
            deletePath: vi.fn().mockResolvedValue(undefined),
            renamePath: vi.fn().mockResolvedValue({ path: `${LOCAL_ROOT}\\renamed.txt` })
        }

        let agentTurn = 0
        streamSmartChat.mockImplementation(async function* () {
            if (agentTurn === 0) {
                agentTurn += 1
                yield { content: buildPlannerToolRequest([{ type: 'list_directory', path: 'assets' }]) }
                return
            }
            if (agentTurn === 1) {
                agentTurn += 1
                yield { content: buildPlannerToolRequest([{ type: 'read_file', path: 'assets/hero.html' }]) }
                return
            }

            yield { content: buildPlannerFinalAnswer('Subfolder file read successfully.') }
        })

        renderAgentPanel()
        await sendPrompt('List thư mục assets rồi đọc đúng file có thật trong đó.')

        await waitFor(() => expect(streamSmartChat).toHaveBeenCalledTimes(3))
        expect(readFile).toHaveBeenCalledWith(`${LOCAL_ROOT}\\assets\\hero.html`)
    })

    it('completes the same list-then-read flow through file_agent planner blocks', async () => {
        const listDirectory = vi.fn().mockResolvedValue([
            {
                name: 'landing.html',
                path: `${LOCAL_ROOT}\\landing.html`,
                type: 'file',
                size: 222,
                mtimeMs: 120
            }
        ] satisfies FsEntry[])
        const readFile = vi.fn().mockResolvedValue({
            content: '<html><body>JSON</body></html>',
            size: 31,
            mtimeMs: 121
        })
        const windowWithElectron = window as unknown as { electronAPI?: ElectronApiMock }
        windowWithElectron.electronAPI = {
            listDirectory,
            readFile,
            writeFile: vi.fn().mockResolvedValue(undefined),
            createFolder: vi.fn().mockResolvedValue(undefined),
            createFile: vi.fn().mockResolvedValue(undefined),
            deletePath: vi.fn().mockResolvedValue(undefined),
            renamePath: vi.fn().mockResolvedValue({ path: `${LOCAL_ROOT}\\renamed.txt` })
        }

        let agentTurn = 0
        streamSmartChat.mockImplementation(async function* () {
            if (agentTurn === 0) {
                agentTurn += 1
                yield { content: buildPlannerToolRequest([{ type: 'list_directory', path: '.' }]) }
                return
            }
            if (agentTurn === 1) {
                agentTurn += 1
                yield { content: buildPlannerToolRequest([{ type: 'read_file', path: 'landing.html' }]) }
                return
            }

            yield { content: buildPlannerFinalAnswer('Planner flow completed.') }
        })

        renderAgentPanel()
        await sendPrompt('Dùng đúng flow JSON fallback để list rồi đọc file HTML có thật.')

        await waitFor(() => expect(streamSmartChat).toHaveBeenCalledTimes(3))
        expect(readFile).toHaveBeenCalledWith(`${LOCAL_ROOT}\\landing.html`)
        await waitFor(() => expect(screen.getByTestId('message-renderer').textContent).toContain('Planner flow completed.'))
    })

    it('stops extra tool actions when the automatic step budget is exhausted', async () => {
        const listDirectory = vi.fn().mockResolvedValue([
            {
                name: 'landing.html',
                path: `${LOCAL_ROOT}\\landing.html`,
                type: 'file',
                size: 321,
                mtimeMs: 130
            }
        ] satisfies FsEntry[])
        const readFile = vi.fn().mockResolvedValue({
            content: '<html><body>Budget</body></html>',
            size: 33,
            mtimeMs: 131
        })
        const windowWithElectron = window as unknown as { electronAPI?: ElectronApiMock }
        windowWithElectron.electronAPI = {
            listDirectory,
            readFile,
            writeFile: vi.fn().mockResolvedValue(undefined),
            createFolder: vi.fn().mockResolvedValue(undefined),
            createFile: vi.fn().mockResolvedValue(undefined),
            deletePath: vi.fn().mockResolvedValue(undefined),
            renamePath: vi.fn().mockResolvedValue({ path: `${LOCAL_ROOT}\\renamed.txt` })
        }

        let agentTurn = 0
        streamSmartChat.mockImplementation(async function* () {
            if (agentTurn === 0) {
                agentTurn += 1
                yield { content: buildPlannerToolRequest([{ type: 'list_directory', path: '.' }]) }
                return
            }
            if (agentTurn === 1) {
                agentTurn += 1
                yield { content: buildPlannerToolRequest([{ type: 'read_file', path: 'landing.html' }]) }
                return
            }

            yield { content: buildPlannerToolRequest([{ type: 'list_directory', path: '.' }]) }
        })

        renderAgentPanel()
        await sendPrompt('List rồi đọc file, nhưng không được vượt quá step budget.')

        await waitFor(() => expect(showInfo).toHaveBeenCalledWith('Reached max automatic tool steps'))
        expect(streamSmartChat).toHaveBeenCalledTimes(3)
        expect(readFile).toHaveBeenCalledWith(`${LOCAL_ROOT}\\landing.html`)
        expect(screen.getByTestId('message-renderer').textContent).toContain('Reached the automatic step limit')
    })
})
