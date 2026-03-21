import React from 'react'
import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest'
import { render, screen, fireEvent, waitFor, cleanup } from '@testing-library/react'
import SettingsModal from './SettingsModal'
import {
    DEFAULT_PIGTEX_SETTINGS,
    type ApiProviderCatalogEntry,
    PigTexSettings,
    getProviderDefaultBaseUrl,
    setRuntimeApiProviderCatalog
} from '../../services/settings'

vi.mock('framer-motion', () => {
    const MotionDiv = ({ children, ...props }: React.HTMLAttributes<HTMLDivElement>) => (
        <div {...props}>{children}</div>
    )
    return {
        AnimatePresence: ({ children }: { children: React.ReactNode }) => <>{children}</>,
        motion: new Proxy({}, { get: () => MotionDiv })
    }
})

const getModelsWithCredentials = vi.fn()
const getModels = vi.fn()
const fetchProviderCatalog = vi.fn()
const validateApiConnection = vi.fn()
const changePassword = vi.fn()
const deleteAccount = vi.fn()
const registerCloudDevice = vi.fn()
const getCloudUsage = vi.fn()
const listCloudBackups = vi.fn()
const createLocalCloudBackup = vi.fn()
const applyLocalCloudRestore = vi.fn()
const getSyncEntitlement = vi.fn()
const createSyncCheckoutSession = vi.fn()
const createSyncPortalSession = vi.fn()
const cancelSyncSubscription = vi.fn()
const getCloudSyncState = vi.fn()
const pushCloudSync = vi.fn()
const pullCloudSync = vi.fn()

vi.mock('../../services/api', () => ({
    getModels: (...args: unknown[]) => getModels(...args),
    getModelsWithCredentials: (...args: unknown[]) => getModelsWithCredentials(...args),
    fetchProviderCatalog: (...args: unknown[]) => fetchProviderCatalog(...args),
    validateApiConnection: (...args: unknown[]) => validateApiConnection(...args),
    changePassword: (...args: unknown[]) => changePassword(...args),
    deleteAccount: (...args: unknown[]) => deleteAccount(...args),
    registerCloudDevice: (...args: unknown[]) => registerCloudDevice(...args),
    getCloudUsage: (...args: unknown[]) => getCloudUsage(...args),
    listCloudBackups: (...args: unknown[]) => listCloudBackups(...args),
    createLocalCloudBackup: (...args: unknown[]) => createLocalCloudBackup(...args),
    applyLocalCloudRestore: (...args: unknown[]) => applyLocalCloudRestore(...args),
    getSyncEntitlement: (...args: unknown[]) => getSyncEntitlement(...args),
    createSyncCheckoutSession: (...args: unknown[]) => createSyncCheckoutSession(...args),
    createSyncPortalSession: (...args: unknown[]) => createSyncPortalSession(...args),
    cancelSyncSubscription: (...args: unknown[]) => cancelSyncSubscription(...args),
    getCloudSyncState: (...args: unknown[]) => getCloudSyncState(...args),
    pushCloudSync: (...args: unknown[]) => pushCloudSync(...args),
    pullCloudSync: (...args: unknown[]) => pullCloudSync(...args)
}))

const showError = vi.fn()
const showInfo = vi.fn()
const showSuccess = vi.fn()
const logout = vi.fn()
const refreshUser = vi.fn()
const mockUser = {
    id: 'user-1',
    email: 'tester@pigtex.io',
    username: 'tester',
    plan: 'sync',
    is_active: true,
    created_at: '2026-03-01T08:00:00Z',
    last_login: '2026-03-08T09:30:00Z',
    has_password: true,
    oauth_provider: 'google',
    avatar_url: null
}

vi.mock('../../contexts/AuthContext', () => ({
    useAuth: () => ({
        user: mockUser,
        logout,
        refreshUser
    })
}))

vi.mock('../Shared/Toast', () => ({
    showError: (...args: unknown[]) => showError(...args),
    showInfo: (...args: unknown[]) => showInfo(...args),
    showSuccess: (...args: unknown[]) => showSuccess(...args)
}))

const baseSettings: PigTexSettings = {
    ...DEFAULT_PIGTEX_SETTINGS,
    apiKey: '',
    baseUrl: '',
    model: 'gpt-4o-mini'
}

const providerCatalog: ApiProviderCatalogEntry[] = [
    {
        id: 'texapi',
        label: 'TexAPI',
        kind: 'gateway',
        upstream_mode: 'openai',
        request_api_provider: 'openai',
        default_base_url: '',
        docs_url: '',
        auth_style: 'bearer',
        supports_byok: true,
        managed_by_server: false,
        aliases: ['texapi', 'tex-api']
    },
    {
        id: 'openai',
        label: 'OpenAI',
        kind: 'direct',
        upstream_mode: 'openai',
        request_api_provider: 'openai',
        default_base_url: 'https://api.openai.com',
        docs_url: 'https://platform.openai.com/api-keys',
        auth_style: 'bearer',
        supports_byok: true,
        managed_by_server: false,
        aliases: ['openai']
    },
    {
        id: 'google',
        label: 'Google',
        kind: 'direct',
        upstream_mode: 'gemini',
        request_api_provider: 'gemini',
        default_base_url: 'https://generativelanguage.googleapis.com',
        docs_url: 'https://aistudio.google.com/apikey',
        auth_style: 'x-goog-api-key',
        supports_byok: true,
        managed_by_server: false,
        aliases: ['google', 'gemini']
    },
    {
        id: 'anthropic',
        label: 'Anthropic',
        kind: 'direct',
        upstream_mode: 'anthropic',
        request_api_provider: 'anthropic',
        default_base_url: 'https://api.anthropic.com',
        docs_url: 'https://console.anthropic.com/settings/keys',
        auth_style: 'x-api-key',
        supports_byok: true,
        managed_by_server: false,
        aliases: ['anthropic']
    },
    {
        id: 'alibaba',
        label: 'Alibaba',
        kind: 'direct',
        upstream_mode: 'alibaba',
        request_api_provider: 'alibaba',
        default_base_url: 'https://dashscope-intl.aliyuncs.com/compatible-mode/v1',
        docs_url: 'https://www.alibabacloud.com/help/en/model-studio/',
        auth_style: 'bearer',
        supports_byok: true,
        managed_by_server: false,
        aliases: ['alibaba', 'dashscope']
    }
]

describe('SettingsModal', () => {
    beforeEach(() => {
        mockUser.plan = 'sync'
        setRuntimeApiProviderCatalog(providerCatalog)
        getModels.mockReset()
        getModelsWithCredentials.mockReset()
        fetchProviderCatalog.mockReset()
        validateApiConnection.mockReset()
        changePassword.mockReset()
        deleteAccount.mockReset()
        registerCloudDevice.mockReset()
        getCloudUsage.mockReset()
        listCloudBackups.mockReset()
        createLocalCloudBackup.mockReset()
        applyLocalCloudRestore.mockReset()
        getSyncEntitlement.mockReset()
        createSyncCheckoutSession.mockReset()
        createSyncPortalSession.mockReset()
        cancelSyncSubscription.mockReset()
        getCloudSyncState.mockReset()
        pushCloudSync.mockReset()
        pullCloudSync.mockReset()
        showError.mockReset()
        showInfo.mockReset()
        showSuccess.mockReset()
        logout.mockReset()
        refreshUser.mockReset()
        window.localStorage.clear()
        window.electronAPI = undefined
        getModels.mockResolvedValue([])
        getModelsWithCredentials.mockResolvedValue([])
        fetchProviderCatalog.mockResolvedValue(providerCatalog)
        changePassword.mockResolvedValue({
            ok: true,
            message: 'Đã cập nhật mật khẩu',
            has_password: true
        })
        deleteAccount.mockResolvedValue(undefined)
        getSyncEntitlement.mockResolvedValue({
            plan_code: 'sync',
            plan_name: 'PigTex Sync',
            status: 'active',
            subscription_status: 'active',
            billing_cycle: 'monthly',
            quota_bytes: 1024 * 1024,
            usage_bytes: 1024,
            retention_days: 30,
            max_devices: 3,
            max_snapshots: 20,
            can_use_cloud_backup: true,
            can_use_device_transfer: true,
            can_use_sync: true,
            can_write_snapshots: true,
            can_restore_snapshots: true,
            priority_level: 1,
            quota_source: 'subscription',
            cancel_at_period_end: false,
            current_period_start: '2026-03-01T00:00:00Z',
            current_period_end: '2026-04-01T00:00:00Z',
            grace_ends_at: '2026-04-15T00:00:00Z',
            plans: [
                {
                    plan_code: 'sync',
                    name: 'PigTex Sync',
                    quota_bytes: 1024 * 1024,
                    retention_days: 30,
                    max_devices: 3,
                    max_snapshots: 20,
                    monthly_price_vnd: 79000,
                    annual_price_vnd: 790000,
                    sync_enabled: true,
                    device_transfer_enabled: true,
                    priority_level: 1,
                },
                {
                    plan_code: 'sync_plus',
                    name: 'PigTex Sync Plus',
                    quota_bytes: 100 * 1024 * 1024,
                    retention_days: 180,
                    max_devices: 10,
                    max_snapshots: 256,
                    monthly_price_vnd: 149000,
                    annual_price_vnd: 1490000,
                    sync_enabled: true,
                    device_transfer_enabled: true,
                    priority_level: 2,
                }
            ]
        })
        registerCloudDevice.mockResolvedValue({
            device_id: 'cloud-device-1',
            quota: {
                plan_code: 'sync',
                quota_bytes: 1024 * 1024,
                retention_days: 30,
                max_devices: 3,
                max_snapshots: 20,
                sync_enabled: true,
                device_transfer_enabled: true,
            }
        })
        getCloudUsage.mockResolvedValue({
            plan_code: 'sync',
            quota_bytes: 1024 * 1024,
            usage_bytes: 1024,
            snapshot_count: 1,
            retention_days: 30,
            max_devices: 3,
            max_snapshots: 20,
            sync_enabled: true,
            device_transfer_enabled: true,
        })
        listCloudBackups.mockResolvedValue({
            items: [
                {
                    snapshot_id: 'snapshot-1',
                    device_id: 'cloud-device-1',
                    device_name: 'DESKTOP-PIGTEX',
                    scope_type: 'account',
                    snapshot_kind: 'full',
                    status: 'ready',
                    payload_size_bytes: 1024,
                    created_at: '2026-03-08T09:30:00Z'
                }
            ]
        })
        createLocalCloudBackup.mockResolvedValue({
            ok: true,
            snapshot_id: 'snapshot-2',
            status: 'ready',
            counts: {
                conversations: 12,
                knowledge_items: 3
            }
        })
        applyLocalCloudRestore.mockResolvedValue({
            ok: true,
            snapshot_id: 'snapshot-1',
            stats: {
                conversations: 12
            }
        })
        createSyncCheckoutSession.mockResolvedValue({
            session_id: 'checkout-1',
            checkout_url: 'https://billing.example/checkout-1',
            mode: 'mock_activated',
        })
        createSyncPortalSession.mockResolvedValue({
            session_url: null,
            mode: 'unsupported',
        })
        cancelSyncSubscription.mockResolvedValue({
            ok: true,
            status: 'active',
            cancel_at_period_end: true,
            current_period_end: '2026-04-01T00:00:00Z',
            grace_ends_at: '2026-04-15T00:00:00Z',
        })
        getCloudSyncState.mockResolvedValue({
            device_id: 'cloud-device-1',
            auto_sync_enabled: true,
            sync_enabled: true,
            status: 'idle',
            can_push: true,
            can_pull: true,
            local_updated_at: '2026-03-08T09:30:00Z',
            last_sync_push_at: '2026-03-08T09:30:00Z',
            last_sync_pull_at: '2026-03-08T09:30:00Z',
            latest_device_snapshot_id: 'snapshot-1',
            latest_device_snapshot_at: '2026-03-08T09:30:00Z',
            latest_remote_snapshot_id: null,
            latest_remote_snapshot_at: null,
        })
        pushCloudSync.mockResolvedValue({
            ok: true,
            snapshot_id: 'snapshot-sync-1',
            status: 'ready',
            counts: { conversations: 12 }
        })
        pullCloudSync.mockResolvedValue({
            ok: true,
            snapshot_id: 'snapshot-sync-remote',
            stats: { conversations: 12 }
        })
        vi.spyOn(window, 'confirm').mockReturnValue(true)
        window.electronAPI = {
            getSystemInfo: vi.fn().mockResolvedValue({
                hostname: 'DESKTOP-PIGTEX',
                platform: 'win32',
                arch: 'x64',
                appVersion: '1.0.0'
            }),
            openExternal: vi.fn().mockResolvedValue({ ok: true }),
        } as unknown as NonNullable<Window['electronAPI']>
    })

    afterEach(() => {
        cleanup()
        vi.restoreAllMocks()
    })

    it('saves all settings with normalized values', async () => {
        const onSave = vi.fn()

        render(
            <SettingsModal
                isOpen={true}
                settings={baseSettings}
                onClose={vi.fn()}
                onSave={onSave}
            />
        )

        fireEvent.click(screen.getByRole('button', { name: 'OpenAI' }))
        fireEvent.change(screen.getByLabelText('API Key', { selector: 'input' }), {
            target: { value: '  my-key  ' }
        })
        fireEvent.change(screen.getByLabelText(/Model/i), {
            target: { value: '  gpt-4.1-mini  ' }
        })
        // Toggle local API key storage (merged from privacy tab)
        fireEvent.click(screen.getByRole('button', { name: /Toggle API key local storage|Bật\/tắt lưu API key cục bộ/i }))

        // Switch to Behavior tab
        fireEvent.click(screen.getByRole('tab', { name: /Hành vi/i }))

        fireEvent.change(screen.getByLabelText('Temperature'), {
            target: { value: '1.25' }
        })
        fireEvent.change(screen.getByLabelText('Max tokens'), {
            target: { value: '1500' }
        })
        fireEvent.change(screen.getByLabelText('Custom instruction'), {
            target: { value: '  always answer with bullet points  ' }
        })

        // Toggle memory sub-options
        fireEvent.click(screen.getByRole('button', { name: /Toggle Knowledge|Bật\/tắt Tri thức/i }))
        fireEvent.click(screen.getByRole('button', { name: /Toggle History|Bật\/tắt Lịch sử/i }))
        fireEvent.click(screen.getByRole('button', { name: /Toggle Auto duyệt AI Files|Bật\/tắt Auto duyệt AI Files/i }))

        // Save
        fireEvent.click(screen.getByRole('button', { name: 'Lưu thay đổi' }))

        await waitFor(() => expect(onSave).toHaveBeenCalledTimes(1))
        const saved = onSave.mock.calls[0][0] as PigTexSettings

        expect(saved.apiProvider).toBe('openai')
        expect(saved.apiKey).toBe('my-key')
        expect(saved.baseUrl).toBe(getProviderDefaultBaseUrl('openai'))
        expect(saved.model).toBe('gpt-4.1-mini')
        expect(saved.temperature).toBe(1.25)
        expect(saved.maxTokens).toBe(1500)
        expect(saved.customInstruction).toBe('always answer with bullet points')
        expect(saved.memoryEnabled).toBe(true)
        expect(saved.useKnowledge).toBe(false)
        expect(saved.useFacts).toBe(true)
        expect(saved.useHistory).toBe(false)
        expect(saved.autoApproveAiFileActions).toBe(true)
        expect(saved.enableQwenImagePromptEnhancer).toBe(true)
        expect(saved.saveApiKeyLocally).toBe(true)
        expect(showSuccess).toHaveBeenCalledWith('Đã lưu Settings')
    }, 15000)

    it('resets to defaults while keeping API key and local-save flag', async () => {
        const onSave = vi.fn()

        render(
            <SettingsModal
                isOpen={true}
                settings={{
                    ...baseSettings,
                    apiProvider: 'openai',
                    customEndpoint: 'openai',
                    apiKey: 'persist-key',
                    baseUrl: getProviderDefaultBaseUrl('openai'),
                    providerCredentialProfiles: {
                        ...baseSettings.providerCredentialProfiles,
                        auto: { apiKey: '', baseUrl: 'https://api.texapi.dev/v1' },
                        openai: { apiKey: 'persist-key', baseUrl: getProviderDefaultBaseUrl('openai') },
                        anthropic: { apiKey: '', baseUrl: getProviderDefaultBaseUrl('anthropic') },
                        gemini: { apiKey: '', baseUrl: getProviderDefaultBaseUrl('gemini') },
                        alibaba: { apiKey: '', baseUrl: getProviderDefaultBaseUrl('alibaba') },
                    },
                    saveApiKeyLocally: true,
                    temperature: 1.4,
                    maxTokens: 2048,
                    customInstruction: 'custom',
                    useKnowledge: false,
                    useFacts: false,
                    useHistory: false,
                    defaultAiFileMode: false,
                    autoApproveAiFileActions: true
                }}
                onClose={vi.fn()}
                onSave={onSave}
            />
        )

        fireEvent.click(screen.getByRole('button', { name: 'Khôi phục mặc định' }))
        fireEvent.click(screen.getByRole('button', { name: 'Lưu thay đổi' }))

        await waitFor(() => expect(onSave).toHaveBeenCalledTimes(1))
        const saved = onSave.mock.calls[0][0] as PigTexSettings

        expect(saved.apiProvider).toBe(DEFAULT_PIGTEX_SETTINGS.apiProvider)
        expect(saved.apiKey).toBe('')
        expect(saved.baseUrl).toBe('https://api.texapi.dev/v1')
        expect(saved.providerCredentialProfiles.openai.apiKey).toBe('persist-key')
        expect(saved.saveApiKeyLocally).toBe(true)
        expect(saved.temperature).toBe(DEFAULT_PIGTEX_SETTINGS.temperature)
        expect(saved.maxTokens).toBe(DEFAULT_PIGTEX_SETTINGS.maxTokens)
        expect(saved.customInstruction).toBe(DEFAULT_PIGTEX_SETTINGS.customInstruction)
        expect(saved.useKnowledge).toBe(DEFAULT_PIGTEX_SETTINGS.useKnowledge)
        expect(saved.useFacts).toBe(DEFAULT_PIGTEX_SETTINGS.useFacts)
        expect(saved.useHistory).toBe(DEFAULT_PIGTEX_SETTINGS.useHistory)
        expect(saved.defaultAiFileMode).toBe(DEFAULT_PIGTEX_SETTINGS.defaultAiFileMode)
        expect(saved.autoApproveAiFileActions).toBe(DEFAULT_PIGTEX_SETTINGS.autoApproveAiFileActions)
        expect(saved.enableQwenImagePromptEnhancer).toBe(DEFAULT_PIGTEX_SETTINGS.enableQwenImagePromptEnhancer)
    })

    it('disables local api key persistence when secure storage is explicitly unavailable', async () => {
        window.electronAPI = {
            isSecureStorageAvailable: () => false,
        } as unknown as NonNullable<Window['electronAPI']>

        const onSave = vi.fn()

        render(
            <SettingsModal
                isOpen={true}
                settings={{
                    ...baseSettings,
                    saveApiKeyLocally: true,
                }}
                onClose={vi.fn()}
                onSave={onSave}
            />
        )

        const storageToggle = screen.getByRole('button', { name: /Toggle local API key storage|Bật\/tắt lưu API key cục bộ/i })
        expect(storageToggle).toBeDisabled()
        expect(
            screen.getByText(/không hỗ trợ secure storage|does not support OS secure storage/i)
        ).toBeInTheDocument()

        fireEvent.change(screen.getByLabelText('Model', { selector: 'input' }), {
            target: { value: 'gpt-4.1-mini' }
        })
        fireEvent.click(screen.getByRole('button', { name: 'Lưu thay đổi' }))

        await waitFor(() => expect(onSave).toHaveBeenCalledTimes(1))
        const saved = onSave.mock.calls[0][0] as PigTexSettings
        expect(saved.saveApiKeyLocally).toBe(false)
    })

    it('validates connection with trimmed credentials', async () => {
        validateApiConnection.mockResolvedValue({
            valid: true,
            message: 'Kết nối thành công'
        })

        render(
            <SettingsModal
                isOpen={true}
                settings={baseSettings}
                onClose={vi.fn()}
                onSave={vi.fn()}
            />
        )

        fireEvent.click(screen.getByRole('button', { name: 'OpenAI' }))
        fireEvent.change(screen.getByLabelText('API Key', { selector: 'input' }), {
            target: { value: ' key-123 ' }
        })

        fireEvent.click(screen.getByRole('button', { name: 'Kiểm tra kết nối' }))

        await waitFor(() => {
            expect(validateApiConnection).toHaveBeenCalledWith(
                'key-123',
                getProviderDefaultBaseUrl('openai'),
                'openai'
            )
        })
        expect(showSuccess).toHaveBeenCalledWith('Kết nối thành công')
    })

    it('locks base URL for strict provider modes', () => {
        render(
            <SettingsModal
                isOpen={true}
                settings={baseSettings}
                onClose={vi.fn()}
                onSave={vi.fn()}
            />
        )

        fireEvent.click(screen.getByRole('button', { name: 'OpenAI' }))

        const baseUrlInput = screen.getByLabelText('Base URL') as HTMLInputElement
        expect(baseUrlInput.disabled).toBe(true)
        expect(baseUrlInput.value).toBe(getProviderDefaultBaseUrl('openai'))
    })

    it('routes validation through the selected direct provider', async () => {
        validateApiConnection.mockResolvedValue({
            valid: true,
            message: 'Kết nối thành công'
        })

        render(
            <SettingsModal
                isOpen={true}
                settings={baseSettings}
                onClose={vi.fn()}
                onSave={vi.fn()}
            />
        )

        fireEvent.click(screen.getByRole('button', { name: 'Anthropic' }))
        fireEvent.change(screen.getByLabelText('API Key', { selector: 'input' }), {
            target: { value: ' key-123 ' }
        })

        fireEvent.click(screen.getByRole('button', { name: 'Kiểm tra kết nối' }))

        await waitFor(() => {
            expect(validateApiConnection).toHaveBeenCalledWith(
                'key-123',
                getProviderDefaultBaseUrl('anthropic'),
                'anthropic'
            )
        })
    })

    it('isolates credentials between TexAPI and direct providers', () => {
        const settingsWithProfiles: PigTexSettings = {
            ...baseSettings,
            apiProvider: 'openai',
            apiKey: 'sk-openai-123',
            baseUrl: getProviderDefaultBaseUrl('openai'),
            providerCredentialProfiles: {
                ...baseSettings.providerCredentialProfiles,
                auto: { apiKey: 'texapi-key-1', baseUrl: 'https://api.texapi.dev/v1' },
                openai: { apiKey: 'sk-openai-123', baseUrl: getProviderDefaultBaseUrl('openai') },
                anthropic: { apiKey: 'sk-ant-999', baseUrl: getProviderDefaultBaseUrl('anthropic') },
                gemini: { apiKey: '', baseUrl: getProviderDefaultBaseUrl('gemini') },
                alibaba: { apiKey: 'sk-alibaba-001', baseUrl: getProviderDefaultBaseUrl('alibaba') },
            },
        }

        render(
            <SettingsModal
                isOpen={true}
                settings={settingsWithProfiles}
                onClose={vi.fn()}
                onSave={vi.fn()}
            />
        )

        const apiKeyInput = screen.getByLabelText('API Key', { selector: 'input' }) as HTMLInputElement
        const baseUrlInput = screen.getByLabelText('Base URL') as HTMLInputElement

        expect(apiKeyInput.value).toBe('sk-openai-123')
        expect(baseUrlInput.value).toBe(getProviderDefaultBaseUrl('openai'))

        fireEvent.click(screen.getByRole('button', { name: 'Anthropic' }))
        expect(apiKeyInput.value).toBe('sk-ant-999')
        expect(baseUrlInput.value).toBe(getProviderDefaultBaseUrl('anthropic'))

        fireEvent.click(screen.getByRole('button', { name: 'TexAPI' }))
        expect((screen.getByLabelText('API Key', { selector: 'input' }) as HTMLInputElement).value).toBe('texapi-key-1')
        expect((screen.getByLabelText('Base URL') as HTMLInputElement).value).toBe('https://api.texapi.dev/v1')

        fireEvent.click(screen.getByRole('button', { name: 'OpenAI' }))
        expect((screen.getByLabelText('API Key', { selector: 'input' }) as HTMLInputElement).value).toBe('sk-openai-123')
    })

    it('remembers direct-provider credentials when switching modes', () => {
        render(
            <SettingsModal
                isOpen={true}
                settings={baseSettings}
                onClose={vi.fn()}
                onSave={vi.fn()}
            />
        )

        fireEvent.click(screen.getByRole('button', { name: 'OpenAI' }))
        fireEvent.change(screen.getByLabelText('API Key', { selector: 'input' }), {
            target: { value: 'openai-key-1' }
        })
        fireEvent.click(screen.getByRole('button', { name: 'Anthropic' }))
        fireEvent.change(screen.getByLabelText('API Key', { selector: 'input' }), {
            target: { value: 'anthropic-key-1' }
        })
        fireEvent.click(screen.getByRole('button', { name: 'TexAPI' }))
        fireEvent.click(screen.getByRole('button', { name: 'OpenAI' }))

        expect((screen.getByLabelText('API Key', { selector: 'input' }) as HTMLInputElement).value).toBe('openai-key-1')
        fireEvent.click(screen.getByRole('button', { name: 'Anthropic' }))
        expect((screen.getByLabelText('API Key', { selector: 'input' }) as HTMLInputElement).value).toBe('anthropic-key-1')
    })

    it('allows editing TexAPI base URL because the endpoint is user-managed', () => {
        render(
            <SettingsModal
                isOpen={true}
                settings={baseSettings}
                onClose={vi.fn()}
                onSave={vi.fn()}
            />
        )

        fireEvent.click(screen.getByRole('button', { name: 'TexAPI' }))

        const baseUrlInput = screen.getByLabelText('Base URL') as HTMLInputElement
        expect(baseUrlInput.disabled).toBe(false)

        fireEvent.change(baseUrlInput, {
            target: { value: 'https://api.texapi.dev/v1' }
        })

        expect(baseUrlInput.value).toBe('https://api.texapi.dev/v1')
    })

    it('supports Alibaba mode with locked default base URL', () => {
        render(
            <SettingsModal
                isOpen={true}
                settings={baseSettings}
                onClose={vi.fn()}
                onSave={vi.fn()}
            />
        )

        fireEvent.click(screen.getByRole('button', { name: 'Alibaba' }))

        const baseUrlInput = screen.getByLabelText('Base URL') as HTMLInputElement
        expect(baseUrlInput.disabled).toBe(true)
        expect(baseUrlInput.value).toBe(getProviderDefaultBaseUrl('alibaba'))
    })

    it('shows profile information and current device details', async () => {
        render(
            <SettingsModal
                isOpen={true}
                settings={baseSettings}
                onClose={vi.fn()}
                onSave={vi.fn()}
            />
        )

        fireEvent.click(screen.getByRole('tab', { name: /Profile|Hồ sơ/i }))

        expect(screen.getAllByText('PigTex Sync').length).toBeGreaterThan(0)
        expect(screen.getByText('Google')).toBeInTheDocument()
        await waitFor(() => {
            expect(screen.getAllByText('DESKTOP-PIGTEX').length).toBeGreaterThan(0)
        })
        expect(screen.getByText('1.0.0')).toBeInTheDocument()
    })

    it('loads cloud backup status and creates a cloud backup from profile tab', async () => {
        render(
            <SettingsModal
                isOpen={true}
                settings={baseSettings}
                onClose={vi.fn()}
                onSave={vi.fn()}
            />
        )

        fireEvent.click(screen.getByRole('tab', { name: /Profile|Hồ sơ/i }))

        await waitFor(() => {
            expect(getSyncEntitlement).toHaveBeenCalled()
        })
        expect(refreshUser).not.toHaveBeenCalled()
        await waitFor(() => {
            expect(registerCloudDevice).toHaveBeenCalled()
        })
        await waitFor(() => {
            expect(screen.getByText('Đã kết nối cloud backup')).toBeInTheDocument()
        })
        expect(screen.getAllByText('DESKTOP-PIGTEX').length).toBeGreaterThan(0)
        expect(screen.getByText('1.00 KB / 1.00 MB')).toBeInTheDocument()

        fireEvent.click(screen.getByRole('button', { name: 'Backup ngay' }))

        await waitFor(() => {
            expect(createLocalCloudBackup).toHaveBeenCalledWith({
                deviceId: 'cloud-device-1',
                scopeType: 'account',
                snapshotKind: 'full'
            })
        })
        await waitFor(() => {
            expect(showSuccess).toHaveBeenCalledWith('Đã tạo cloud backup. 12 conversations • 3 knowledge_items')
        })
    })

    it('opens sync checkout for free users from profile tab', async () => {
        mockUser.plan = 'free'
        getSyncEntitlement.mockResolvedValue({
            plan_code: 'free',
            plan_name: 'Miễn phí',
            status: 'free',
            subscription_status: 'free',
            billing_cycle: null,
            quota_bytes: 0,
            usage_bytes: 0,
            retention_days: 0,
            max_devices: 1,
            max_snapshots: 0,
            can_use_cloud_backup: false,
            can_use_device_transfer: false,
            can_use_sync: false,
            can_write_snapshots: false,
            can_restore_snapshots: false,
            priority_level: 0,
            quota_source: 'free',
            cancel_at_period_end: false,
            current_period_start: null,
            current_period_end: null,
            grace_ends_at: null,
            plans: [
                {
                    plan_code: 'sync',
                    name: 'PigTex Sync',
                    quota_bytes: 1024 * 1024,
                    retention_days: 30,
                    max_devices: 3,
                    max_snapshots: 20,
                    monthly_price_vnd: 79000,
                    annual_price_vnd: 790000,
                    sync_enabled: true,
                    device_transfer_enabled: true,
                    priority_level: 1,
                },
                {
                    plan_code: 'sync_plus',
                    name: 'PigTex Sync Plus',
                    quota_bytes: 100 * 1024 * 1024,
                    retention_days: 180,
                    max_devices: 10,
                    max_snapshots: 256,
                    monthly_price_vnd: 149000,
                    annual_price_vnd: 1490000,
                    sync_enabled: true,
                    device_transfer_enabled: true,
                    priority_level: 2,
                }
            ]
        })

        render(
            <SettingsModal
                isOpen={true}
                settings={baseSettings}
                onClose={vi.fn()}
                onSave={vi.fn()}
            />
        )

        fireEvent.click(screen.getByRole('tab', { name: /Profile|Hồ sơ/i }))

        const upgradeButtons = await screen.findAllByRole('button')
        const upgradeButton = upgradeButtons.find((button) => {
            const label = button.textContent?.trim() || ''
            return label.startsWith('Nâng cấp Sync •') || label.startsWith('Upgrade to Sync •')
        })
        if (!upgradeButton) {
            throw new Error('Missing Sync upgrade button')
        }
        await waitFor(() => {
            expect(upgradeButton).toBeEnabled()
        })

        fireEvent.click(upgradeButton)

        await waitFor(() => {
            expect(createSyncCheckoutSession).toHaveBeenCalledWith({
                planCode: 'sync',
                billingCycle: 'monthly',
            })
        })
        await waitFor(() => {
            expect(window.electronAPI?.openExternal).toHaveBeenCalledWith('https://billing.example/checkout-1')
        })
    })

    it('signs out from profile tab', async () => {
        const onClose = vi.fn()

        render(
            <SettingsModal
                isOpen={true}
                settings={baseSettings}
                onClose={onClose}
                onSave={vi.fn()}
            />
        )

        fireEvent.click(screen.getByRole('tab', { name: /Profile|Hồ sơ/i }))
        const signOutButton = screen.getByRole('button', { name: /Đăng xuất|Sign out/i })
        expect(signOutButton).toHaveClass('stg-btn', 'stg-btn--danger')
        fireEvent.click(signOutButton)

        expect(onClose).toHaveBeenCalledTimes(1)
        expect(logout).toHaveBeenCalledTimes(1)
        expect(showSuccess).toHaveBeenCalledWith(expect.stringMatching(/Đã đăng xuất|Signed out/))
    })

    it('changes password and deletes account from profile tab', async () => {
        render(
            <SettingsModal
                isOpen={true}
                settings={baseSettings}
                onClose={vi.fn()}
                onSave={vi.fn()}
            />
        )

        fireEvent.click(screen.getByRole('tab', { name: /Profile|Hồ sơ/i }))

        fireEvent.change(screen.getByLabelText('Mật khẩu hiện tại'), {
            target: { value: 'old-secret' }
        })
        fireEvent.change(screen.getByLabelText('Mật khẩu mới'), {
            target: { value: 'new-secret-123' }
        })
        fireEvent.change(screen.getByLabelText('Xác nhận mật khẩu mới'), {
            target: { value: 'new-secret-123' }
        })

        fireEvent.click(screen.getByRole('button', { name: 'Cập nhật mật khẩu' }))

        await waitFor(() => {
            expect(changePassword).toHaveBeenCalledWith({
                currentPassword: 'old-secret',
                newPassword: 'new-secret-123'
            })
        })

        fireEvent.change(screen.getByLabelText('Nhập email để xác nhận'), {
            target: { value: 'tester@pigtex.io' }
        })
        fireEvent.change(screen.getByLabelText(/^Mật khẩu$/), {
            target: { value: 'old-secret' }
        })
        fireEvent.click(screen.getByRole('button', { name: 'Xóa tài khoản' }))

        await waitFor(() => {
            expect(deleteAccount).toHaveBeenCalledWith({
                confirmation: 'tester@pigtex.io',
                password: 'old-secret'
            })
        })
        expect(logout).toHaveBeenCalled()
    })
})
