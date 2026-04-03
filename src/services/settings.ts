// ─────────────────────────────────────────────────────────────────────────────
// PigTex Settings — Multi-Provider API Configuration
// ─────────────────────────────────────────────────────────────────────────────

// ===== API Provider Types =====

export type ApiProviderId = 'auto' | 'openai' | 'anthropic' | 'gemini' | 'alibaba'
export type ApiEndpointProviderId = Exclude<ApiProviderId, 'auto'>
export type PublicApiProviderId = 'texapi' | 'openai' | 'google' | 'anthropic' | 'alibaba'
export type AppLanguage = 'vi' | 'en'
const API_PROVIDER_MODE_IDS: ApiProviderId[] = ['auto', 'openai', 'anthropic', 'gemini', 'alibaba']
export const APP_LANGUAGE_IDS: AppLanguage[] = ['vi', 'en']

export interface ApiProviderConfig {
    id: ApiProviderId
    label: string
    defaultBaseUrl: string
    /** Placeholder shown in the API Key input */
    keyPlaceholder: string
    /** Docs URL for getting API keys */
    docsUrl: string
    /** How authentication works for this provider */
    authStyle: 'auto' | 'bearer' | 'x-api-key' | 'query-param'
    /** Extra headers required by this provider */
    extraHeaders?: Record<string, string>
}

export interface CustomEndpointOption {
    id: ApiEndpointProviderId
    label: string
    endpointPath: string
}

export interface ApiProviderCatalogEntry {
    id: PublicApiProviderId
    label: string
    kind: 'gateway' | 'direct'
    upstream_mode: ApiEndpointProviderId
    request_api_provider: ApiEndpointProviderId
    default_base_url: string
    docs_url: string
    auth_style: string
    supports_byok: boolean
    managed_by_server: boolean
    aliases: string[]
}

export const API_PROVIDERS: Record<ApiProviderId, ApiProviderConfig> = {
    auto: {
        id: 'auto',
        label: 'Custom',
        defaultBaseUrl: 'https://api.openai.com',
        keyPlaceholder: 'Nhập key theo endpoint đã chọn',
        docsUrl: 'https://platform.openai.com/docs/api-reference',
        authStyle: 'auto',
    },
    openai: {
        id: 'openai',
        label: 'OpenAI',
        defaultBaseUrl: 'https://api.openai.com',
        keyPlaceholder: 'sk-...',
        docsUrl: 'https://platform.openai.com/api-keys',
        authStyle: 'bearer',
    },
    anthropic: {
        id: 'anthropic',
        label: 'Anthropic',
        defaultBaseUrl: 'https://api.anthropic.com',
        keyPlaceholder: 'sk-ant-...',
        docsUrl: 'https://console.anthropic.com/settings/keys',
        authStyle: 'x-api-key',
        extraHeaders: {
            'anthropic-version': '2023-06-01',
        },
    },
    gemini: {
        id: 'gemini',
        label: 'Google Gemini',
        defaultBaseUrl: 'https://generativelanguage.googleapis.com',
        keyPlaceholder: 'AIza...',
        docsUrl: 'https://aistudio.google.com/apikey',
        authStyle: 'query-param',
    },
    alibaba: {
        id: 'alibaba',
        label: 'Alibaba',
        defaultBaseUrl: 'https://dashscope-intl.aliyuncs.com/compatible-mode/v1',
        keyPlaceholder: 'sk-...',
        docsUrl: 'https://www.alibabacloud.com/help/en/model-studio/',
        authStyle: 'bearer',
    },
}

export const API_PROVIDER_LIST: ApiProviderConfig[] = Object.values(API_PROVIDERS)
export const API_PROVIDER_IDS: ApiProviderId[] = API_PROVIDER_LIST.map(p => p.id)
export const CUSTOM_ENDPOINT_OPTIONS: CustomEndpointOption[] = [
    {
        id: 'openai',
        label: 'OpenAI',
        endpointPath: '/v1/chat/completions',
    },
    {
        id: 'anthropic',
        label: 'Anthropic',
        endpointPath: '/v1/messages',
    },
    {
        id: 'gemini',
        label: 'Google Gemini',
        endpointPath: '/v1beta/models/...'
    },
    {
        id: 'alibaba',
        label: 'Alibaba DashScope',
        endpointPath: '/compatible-mode/v1/chat/completions',
    },
]
const CUSTOM_ENDPOINT_PROVIDER_IDS: ApiEndpointProviderId[] = CUSTOM_ENDPOINT_OPTIONS.map(item => item.id)
const PUBLIC_PROVIDER_IDS: PublicApiProviderId[] = ['texapi', 'openai', 'google', 'anthropic', 'alibaba']

const FALLBACK_API_PROVIDER_CATALOG: ApiProviderCatalogEntry[] = [
    {
        id: 'texapi',
        label: 'TexAPI',
        kind: 'gateway',
        upstream_mode: 'openai',
        request_api_provider: 'openai',
        default_base_url: 'https://api.texapi.dev/v1/partner/gateway',
        docs_url: '',
        auth_style: 'bearer',
        supports_byok: true,
        managed_by_server: false,
        aliases: ['texapi', 'tex-api'],
    },
    {
        id: 'openai',
        label: API_PROVIDERS.openai.label,
        kind: 'direct',
        upstream_mode: 'openai',
        request_api_provider: 'openai',
        default_base_url: API_PROVIDERS.openai.defaultBaseUrl,
        docs_url: API_PROVIDERS.openai.docsUrl,
        auth_style: API_PROVIDERS.openai.authStyle,
        supports_byok: true,
        managed_by_server: false,
        aliases: ['openai'],
    },
    {
        id: 'google',
        label: 'Google',
        kind: 'direct',
        upstream_mode: 'gemini',
        request_api_provider: 'gemini',
        default_base_url: API_PROVIDERS.gemini.defaultBaseUrl,
        docs_url: API_PROVIDERS.gemini.docsUrl,
        auth_style: 'x-goog-api-key',
        supports_byok: true,
        managed_by_server: false,
        aliases: ['google', 'gemini'],
    },
    {
        id: 'anthropic',
        label: API_PROVIDERS.anthropic.label,
        kind: 'direct',
        upstream_mode: 'anthropic',
        request_api_provider: 'anthropic',
        default_base_url: API_PROVIDERS.anthropic.defaultBaseUrl,
        docs_url: API_PROVIDERS.anthropic.docsUrl,
        auth_style: API_PROVIDERS.anthropic.authStyle,
        supports_byok: true,
        managed_by_server: false,
        aliases: ['anthropic'],
    },
    {
        id: 'alibaba',
        label: API_PROVIDERS.alibaba.label,
        kind: 'direct',
        upstream_mode: 'alibaba',
        request_api_provider: 'alibaba',
        default_base_url: API_PROVIDERS.alibaba.defaultBaseUrl,
        docs_url: API_PROVIDERS.alibaba.docsUrl,
        auth_style: API_PROVIDERS.alibaba.authStyle,
        supports_byok: true,
        managed_by_server: false,
        aliases: ['alibaba', 'dashscope'],
    },
]

let runtimeApiProviderCatalog: ApiProviderCatalogEntry[] = FALLBACK_API_PROVIDER_CATALOG.map((entry) => ({
    ...entry,
    aliases: [...entry.aliases],
}))

function isValidProviderId(value: unknown): value is ApiProviderId {
    return typeof value === 'string' && API_PROVIDER_IDS.includes(value as ApiProviderId)
}

function isValidCustomEndpointProviderId(value: unknown): value is ApiEndpointProviderId {
    return typeof value === 'string' && CUSTOM_ENDPOINT_PROVIDER_IDS.includes(value as ApiEndpointProviderId)
}

function isValidPublicProviderId(value: unknown): value is PublicApiProviderId {
    return typeof value === 'string' && PUBLIC_PROVIDER_IDS.includes(value as PublicApiProviderId)
}

function cloneCatalogEntry(entry: ApiProviderCatalogEntry): ApiProviderCatalogEntry {
    return {
        ...entry,
        aliases: [...entry.aliases],
    }
}

function normalizeCatalogEntry(
    value: unknown,
    fallback: ApiProviderCatalogEntry
): ApiProviderCatalogEntry {
    const record = value && typeof value === 'object' ? value as Partial<ApiProviderCatalogEntry> : {}
    return {
        id: isValidPublicProviderId(record.id) ? record.id : fallback.id,
        label: typeof record.label === 'string' && record.label.trim() ? record.label.trim() : fallback.label,
        kind: record.kind === 'gateway' ? 'gateway' : 'direct',
        upstream_mode: isValidCustomEndpointProviderId(record.upstream_mode) ? record.upstream_mode : fallback.upstream_mode,
        request_api_provider: isValidCustomEndpointProviderId(record.request_api_provider)
            ? record.request_api_provider
            : fallback.request_api_provider,
        default_base_url: typeof record.default_base_url === 'string' ? record.default_base_url.trim() : fallback.default_base_url,
        docs_url: typeof record.docs_url === 'string' ? record.docs_url.trim() : fallback.docs_url,
        auth_style: typeof record.auth_style === 'string' && record.auth_style.trim() ? record.auth_style.trim() : fallback.auth_style,
        supports_byok: typeof record.supports_byok === 'boolean' ? record.supports_byok : fallback.supports_byok,
        managed_by_server: typeof record.managed_by_server === 'boolean' ? record.managed_by_server : fallback.managed_by_server,
        aliases: Array.isArray(record.aliases)
            ? record.aliases.filter((item): item is string => typeof item === 'string' && item.trim().length > 0)
            : [...fallback.aliases],
    }
}

export function setRuntimeApiProviderCatalog(catalog: ApiProviderCatalogEntry[]): void {
    if (!Array.isArray(catalog) || catalog.length === 0) {
        runtimeApiProviderCatalog = FALLBACK_API_PROVIDER_CATALOG.map(cloneCatalogEntry)
        return
    }

    const nextCatalog: ApiProviderCatalogEntry[] = []
    for (const providerId of PUBLIC_PROVIDER_IDS) {
        const fallback = FALLBACK_API_PROVIDER_CATALOG.find(entry => entry.id === providerId)
        if (!fallback) continue
        const runtime = catalog.find(entry => entry?.id === providerId)
        nextCatalog.push(normalizeCatalogEntry(runtime, fallback))
    }
    runtimeApiProviderCatalog = nextCatalog
}

export function getApiProviderCatalog(): ApiProviderCatalogEntry[] {
    return runtimeApiProviderCatalog.map(cloneCatalogEntry)
}

export function mapCatalogProviderIdToSettingsSelection(
    providerId: PublicApiProviderId
): { apiProvider: ApiProviderId; customEndpoint: ApiEndpointProviderId } {
    switch (providerId) {
        case 'texapi':
            return { apiProvider: 'auto', customEndpoint: 'openai' }
        case 'google':
            return { apiProvider: 'gemini', customEndpoint: 'gemini' }
        case 'anthropic':
            return { apiProvider: 'anthropic', customEndpoint: 'anthropic' }
        case 'alibaba':
            return { apiProvider: 'alibaba', customEndpoint: 'alibaba' }
        case 'openai':
        default:
            return { apiProvider: 'openai', customEndpoint: 'openai' }
    }
}

export function mapSettingsSelectionToCatalogProviderId(
    apiProvider: ApiProviderId,
    customEndpoint: ApiEndpointProviderId
): PublicApiProviderId {
    const resolved = apiProvider === 'auto' ? normalizeCustomEndpoint(customEndpoint) : apiProvider
    if (apiProvider === 'auto' && resolved === 'openai') {
        return 'texapi'
    }
    if (resolved === 'gemini') {
        return 'google'
    }
    return resolved
}

export function getApiProviderCatalogEntry(providerId: PublicApiProviderId): ApiProviderCatalogEntry {
    return getApiProviderCatalog().find(entry => entry.id === providerId)
        || FALLBACK_API_PROVIDER_CATALOG.find(entry => entry.id === providerId)!
}

export function getApiProviderCatalogEntryForSelection(
    apiProvider: ApiProviderId,
    customEndpoint: ApiEndpointProviderId
): ApiProviderCatalogEntry {
    return getApiProviderCatalogEntry(mapSettingsSelectionToCatalogProviderId(apiProvider, customEndpoint))
}

export function getPublicProviderDefaultBaseUrl(providerId: PublicApiProviderId): string {
    return getApiProviderCatalogEntry(providerId).default_base_url
}

// ===== Settings Interface =====

export interface PigTexSettings {
    language: AppLanguage
    apiProvider: ApiProviderId
    customEndpoint: ApiEndpointProviderId
    apiKey: string
    baseUrl: string
    providerCredentialProfiles: ProviderCredentialProfiles
    model: string
    memoryEnabled: boolean
    useKnowledge: boolean
    useFacts: boolean
    useHistory: boolean
    temperature: number
    maxTokens: number
    customInstruction: string
    defaultAiFileMode: boolean
    autoApproveAiFileActions: boolean
    enableQwenImagePromptEnhancer: boolean
    saveApiKeyLocally: boolean
}

export interface ProviderCredentialProfile {
    apiKey: string
    baseUrl: string
}

export type ProviderCredentialProfiles = Record<ApiProviderId, ProviderCredentialProfile>
type ProviderApiKeyMap = Partial<Record<ApiProviderId, string>>

// ===== Constants =====

const SETTINGS_STORAGE_KEY = 'pigtex_settings_v2'
const LEGACY_SETTINGS_STORAGE_KEY = 'pigtex_settings_v1'
const SESSION_API_KEYS_STORAGE_KEY = 'pigtex_session_api_keys_v2'
const LEGACY_SESSION_API_KEY_STORAGE_KEY = 'pigtex_session_api_key'
export const PIGTEX_SETTINGS_CHANGED_EVENT = 'pigtex:settings-changed'

const DEFAULT_PROVIDER: ApiProviderId = 'auto'

function buildDefaultProviderCredentialProfiles(): ProviderCredentialProfiles {
    return {
        auto: {
            apiKey: '',
            baseUrl: getPublicProviderDefaultBaseUrl('texapi'),
        },
        openai: {
            apiKey: '',
            baseUrl: API_PROVIDERS.openai.defaultBaseUrl,
        },
        anthropic: {
            apiKey: '',
            baseUrl: API_PROVIDERS.anthropic.defaultBaseUrl,
        },
        gemini: {
            apiKey: '',
            baseUrl: API_PROVIDERS.gemini.defaultBaseUrl,
        },
        alibaba: {
            apiKey: '',
            baseUrl: API_PROVIDERS.alibaba.defaultBaseUrl,
        },
    }
}

export const DEFAULT_PIGTEX_SETTINGS: PigTexSettings = {
    language: 'vi',
    apiProvider: DEFAULT_PROVIDER,
    customEndpoint: 'openai',
    apiKey: '',
    baseUrl: getPublicProviderDefaultBaseUrl('texapi'),
    providerCredentialProfiles: buildDefaultProviderCredentialProfiles(),
    model: 'gpt-4o',
    memoryEnabled: true,
    useKnowledge: true,
    useFacts: true,
    useHistory: true,
    temperature: 0.7,
    maxTokens: 0,
    customInstruction: '',
    defaultAiFileMode: true,
    autoApproveAiFileActions: false,
    enableQwenImagePromptEnhancer: true,
    saveApiKeyLocally: false,
}

// ===== Helpers =====

type StoredPigTexSettings = Omit<PigTexSettings, 'apiKey'> & {
    apiKey?: string
}

function isBrowser(): boolean {
    return typeof window !== 'undefined'
}

function isValidAppLanguage(value: unknown): value is AppLanguage {
    return typeof value === 'string' && APP_LANGUAGE_IDS.includes(value as AppLanguage)
}

function normalizeBaseUrl(baseUrl: string, provider?: ApiProviderId): string {
    const mode = provider && isValidProviderId(provider) ? provider : DEFAULT_PIGTEX_SETTINGS.apiProvider
    if (mode !== 'auto') {
        return API_PROVIDERS[mode].defaultBaseUrl
    }

    const trimmed = (baseUrl || '').trim()
    const fallback = API_PROVIDERS.auto.defaultBaseUrl
    if (!trimmed) return fallback
    try {
        const parsed = new URL(trimmed)
        if (parsed.protocol !== 'http:' && parsed.protocol !== 'https:') {
            return fallback
        }
        return `${parsed.protocol}//${parsed.host}${parsed.pathname}`.replace(/\/+$/, '')
    } catch {
        return fallback
    }
}

function normalizeCustomBaseUrl(baseUrl: unknown): string {
    if (typeof baseUrl !== 'string') return ''
    const trimmed = baseUrl.trim()
    if (!trimmed) return ''
    try {
        const parsed = new URL(trimmed)
        if (parsed.protocol !== 'http:' && parsed.protocol !== 'https:') {
            return ''
        }
        return `${parsed.protocol}//${parsed.host}${parsed.pathname}`.replace(/\/+$/, '')
    } catch {
        return ''
    }
}

function normalizeProviderCredentialProfile(
    provider: ApiProviderId,
    raw: unknown
): ProviderCredentialProfile {
    const asRecord = (raw && typeof raw === 'object') ? (raw as Record<string, unknown>) : {}
    const apiKey = typeof asRecord.apiKey === 'string' ? asRecord.apiKey.trim() : ''
    if (provider === 'auto') {
        return {
            apiKey,
            baseUrl: normalizeCustomBaseUrl(asRecord.baseUrl),
        }
    }
    return {
        apiKey,
        baseUrl: API_PROVIDERS[provider].defaultBaseUrl,
    }
}

function normalizeProviderCredentialProfiles(raw: unknown): ProviderCredentialProfiles {
    const asRecord = (raw && typeof raw === 'object') ? (raw as Record<string, unknown>) : {}
    const normalized = buildDefaultProviderCredentialProfiles()
    for (const providerId of API_PROVIDER_MODE_IDS) {
        normalized[providerId] = normalizeProviderCredentialProfile(providerId, asRecord[providerId])
    }
    return normalized
}

function cloneProviderCredentialProfiles(
    input: ProviderCredentialProfiles
): ProviderCredentialProfiles {
    return {
        auto: { ...input.auto },
        openai: { ...input.openai },
        anthropic: { ...input.anthropic },
        gemini: { ...input.gemini },
        alibaba: { ...input.alibaba },
    }
}

function clearProviderApiKeys(
    input: ProviderCredentialProfiles
): ProviderCredentialProfiles {
    const next = cloneProviderCredentialProfiles(input)
    for (const providerId of API_PROVIDER_MODE_IDS) {
        next[providerId].apiKey = ''
    }
    return next
}

function extractProviderApiKeyMap(
    input: ProviderCredentialProfiles
): ProviderApiKeyMap {
    const extracted: ProviderApiKeyMap = {}
    for (const providerId of API_PROVIDER_MODE_IDS) {
        const apiKey = input[providerId].apiKey.trim()
        if (apiKey) {
            extracted[providerId] = apiKey
        }
    }
    return extracted
}

function canUseSecureCredentialStorage(): boolean {
    if (!isBrowser()) return false
    try {
        return Boolean(window.electronAPI?.isSecureStorageAvailable?.())
    } catch {
        return false
    }
}

function getSecureApiKeyMap(): ProviderApiKeyMap {
    if (!canUseSecureCredentialStorage()) return {}
    try {
        return window.electronAPI?.getSecureApiKeys?.() || {}
    } catch {
        return {}
    }
}

function setSecureApiKeyMap(map: ProviderApiKeyMap): boolean {
    if (!canUseSecureCredentialStorage()) return false
    try {
        window.electronAPI?.setSecureApiKeys?.(map)
        return true
    } catch {
        return false
    }
}

function promoteStoredApiKeysToSession(
    providerCredentialProfiles: ProviderCredentialProfiles
): ProviderCredentialProfiles {
    const storedApiKeys = extractProviderApiKeyMap(providerCredentialProfiles)
    if (Object.keys(storedApiKeys).length > 0) {
        setSessionApiKeyMap({
            ...getSessionApiKeyMap(),
            ...storedApiKeys,
        })
    }
    return clearProviderApiKeys(providerCredentialProfiles)
}

function applyLegacySingleCredentialToProfiles(
    profiles: ProviderCredentialProfiles,
    provider: ApiProviderId,
    baseUrl: unknown,
    apiKey: unknown
): ProviderCredentialProfiles {
    const next = cloneProviderCredentialProfiles(profiles)
    const legacyApiKey = typeof apiKey === 'string' ? apiKey.trim() : ''
    const legacyBaseUrl = provider === 'auto'
        ? normalizeCustomBaseUrl(baseUrl)
        : API_PROVIDERS[provider].defaultBaseUrl

    const target = next[provider]
    if (!target.apiKey && legacyApiKey) {
        target.apiKey = legacyApiKey
    }
    if (provider === 'auto' && !target.baseUrl && legacyBaseUrl) {
        target.baseUrl = legacyBaseUrl
    }
    return next
}

function inferEndpointFromBaseUrl(baseUrl: unknown): ApiEndpointProviderId | null {
    if (typeof baseUrl !== 'string') return null
    const trimmed = baseUrl.trim()
    if (!trimmed) return null
    try {
        const parsed = new URL(trimmed)
        const host = parsed.host.toLowerCase()
        const path = parsed.pathname.toLowerCase()
        if (host === 'api.anthropic.com') return 'anthropic'
        if (host === 'generativelanguage.googleapis.com') return 'gemini'
        if (
            host === 'dashscope-intl.aliyuncs.com'
            || host === 'dashscope.aliyuncs.com'
            || path.includes('/compatible-mode/')
        ) {
            return 'alibaba'
        }
        return 'openai'
    } catch {
        return null
    }
}

function inferEndpointFromApiKey(apiKey: unknown): ApiEndpointProviderId | null {
    if (typeof apiKey !== 'string') return null
    const trimmed = apiKey.trim()
    if (!trimmed) return null
    if (trimmed.startsWith('sk-ant-')) return 'anthropic'
    if (trimmed.startsWith('AIza')) return 'gemini'
    return 'openai'
}

export function inferEndpointProviderFromCredentials(
    baseUrl?: string,
    apiKey?: string
): ApiEndpointProviderId {
    const fromBaseUrl = inferEndpointFromBaseUrl(baseUrl)
    if (fromBaseUrl && fromBaseUrl !== 'openai') {
        return fromBaseUrl
    }
    const fromApiKey = inferEndpointFromApiKey(apiKey)
    if (fromApiKey) {
        return fromApiKey
    }
    return DEFAULT_PIGTEX_SETTINGS.customEndpoint
}

function normalizeTemperature(value: unknown): number {
    const parsed = typeof value === 'number' ? value : Number(value)
    if (!Number.isFinite(parsed)) {
        return DEFAULT_PIGTEX_SETTINGS.temperature
    }
    const clamped = Math.min(2, Math.max(0, parsed))
    return Math.round(clamped * 100) / 100
}

function normalizeMaxTokens(value: unknown): number {
    const parsed = typeof value === 'number' ? value : Number(value)
    if (!Number.isFinite(parsed)) {
        return DEFAULT_PIGTEX_SETTINGS.maxTokens
    }
    const normalized = Math.trunc(parsed)
    if (normalized <= 0) return 0
    return Math.min(normalized, 32768)
}

function normalizeCustomInstruction(value: unknown): string {
    if (typeof value !== 'string') return DEFAULT_PIGTEX_SETTINGS.customInstruction
    return value.trim()
}

function normalizeLanguage(value: unknown): AppLanguage {
    if (isValidAppLanguage(value)) return value
    return DEFAULT_PIGTEX_SETTINGS.language
}

function normalizeCustomEndpoint(value: unknown): ApiEndpointProviderId {
    if (isValidCustomEndpointProviderId(value)) return value
    return DEFAULT_PIGTEX_SETTINGS.customEndpoint
}

function resolveCustomEndpointForProvider(
    provider: ApiProviderId,
    rawCustomEndpoint: unknown,
    baseUrl: unknown,
    apiKey: unknown
): ApiEndpointProviderId {
    const hasExplicitEndpoint = isValidCustomEndpointProviderId(rawCustomEndpoint)
    const currentEndpoint = normalizeCustomEndpoint(rawCustomEndpoint)
    const hintedEndpoint = inferEndpointProviderFromCredentials(
        typeof baseUrl === 'string' ? baseUrl : undefined,
        typeof apiKey === 'string' ? apiKey : undefined
    )

    if (!hasExplicitEndpoint) {
        return hintedEndpoint
    }

    if (provider !== 'auto') {
        return currentEndpoint
    }

    // Auto-correct legacy defaults: customEndpoint=openai but credentials clearly indicate another protocol.
    if (currentEndpoint === 'openai' && hintedEndpoint !== 'openai') {
        const baseUrlHint = inferEndpointFromBaseUrl(baseUrl)
        const normalizedAutoBaseUrl = normalizeBaseUrl(typeof baseUrl === 'string' ? baseUrl : '', 'auto')
        const isDefaultAutoBaseUrl = normalizedAutoBaseUrl === API_PROVIDERS.auto.defaultBaseUrl
        if (baseUrlHint !== 'openai' || isDefaultAutoBaseUrl) {
            return hintedEndpoint
        }
    }

    return currentEndpoint
}

function resolveBaseUrlForProvider(
    provider: ApiProviderId,
    rawBaseUrl: unknown,
    customEndpoint: ApiEndpointProviderId
): string {
    if (provider !== 'auto') {
        return API_PROVIDERS[provider].defaultBaseUrl
    }

    void customEndpoint
    return normalizeCustomBaseUrl(rawBaseUrl) || getPublicProviderDefaultBaseUrl('texapi')
}

// ===== Migration from v1 =====

function migrateLegacySettings(): Partial<StoredPigTexSettings> | null {
    if (!isBrowser()) return null
    const raw = localStorage.getItem(LEGACY_SETTINGS_STORAGE_KEY)
    if (!raw) return null
    try {
        const legacy = JSON.parse(raw) as Record<string, unknown>
        const provider = isValidProviderId(legacy.apiProvider)
            ? legacy.apiProvider
            : ('auto' as ApiProviderId)
        const legacyApiKey = typeof legacy.apiKey === 'string' ? legacy.apiKey.trim() : ''
        const legacyBaseUrl = provider === 'auto'
            ? normalizeCustomBaseUrl(legacy.baseUrl)
            : API_PROVIDERS[provider].defaultBaseUrl
        const providerCredentialProfiles = buildDefaultProviderCredentialProfiles()
        providerCredentialProfiles[provider] = {
            apiKey: legacyApiKey,
            baseUrl: legacyBaseUrl,
        }
        // Remove old key after reading
        localStorage.removeItem(LEGACY_SETTINGS_STORAGE_KEY)
        // Map legacy provider settings -> Auto (Custom) provider mode
        return {
            apiProvider: provider,
            customEndpoint: resolveCustomEndpointForProvider(
                provider,
                legacy.customEndpoint,
                legacyBaseUrl,
                legacyApiKey
            ),
            providerCredentialProfiles,
            model: typeof legacy.model === 'string' ? legacy.model : DEFAULT_PIGTEX_SETTINGS.model,
            baseUrl: legacyBaseUrl,
            memoryEnabled: typeof legacy.memoryEnabled === 'boolean' ? legacy.memoryEnabled : DEFAULT_PIGTEX_SETTINGS.memoryEnabled,
            useKnowledge: typeof legacy.useKnowledge === 'boolean' ? legacy.useKnowledge : DEFAULT_PIGTEX_SETTINGS.useKnowledge,
            useFacts: typeof legacy.useFacts === 'boolean' ? legacy.useFacts : DEFAULT_PIGTEX_SETTINGS.useFacts,
            useHistory: typeof legacy.useHistory === 'boolean' ? legacy.useHistory : DEFAULT_PIGTEX_SETTINGS.useHistory,
            temperature: normalizeTemperature(legacy.temperature),
            maxTokens: normalizeMaxTokens(legacy.maxTokens),
            customInstruction: normalizeCustomInstruction(legacy.customInstruction),
            defaultAiFileMode: typeof legacy.defaultAiFileMode === 'boolean' ? legacy.defaultAiFileMode : DEFAULT_PIGTEX_SETTINGS.defaultAiFileMode,
            autoApproveAiFileActions: typeof legacy.autoApproveAiFileActions === 'boolean' ? legacy.autoApproveAiFileActions : DEFAULT_PIGTEX_SETTINGS.autoApproveAiFileActions,
            enableQwenImagePromptEnhancer: typeof legacy.enableQwenImagePromptEnhancer === 'boolean'
                ? legacy.enableQwenImagePromptEnhancer
                : DEFAULT_PIGTEX_SETTINGS.enableQwenImagePromptEnhancer,
            saveApiKeyLocally: typeof legacy.saveApiKeyLocally === 'boolean' ? legacy.saveApiKeyLocally : DEFAULT_PIGTEX_SETTINGS.saveApiKeyLocally,
            apiKey: legacyApiKey,
        }
    } catch {
        return null
    }
}

// ===== Read / Write =====

function readStoredSettings(): StoredPigTexSettings {
    if (!isBrowser()) {
        return { ...DEFAULT_PIGTEX_SETTINGS }
    }

    let raw = localStorage.getItem(SETTINGS_STORAGE_KEY)

    // Try v1 migration
    if (!raw) {
        const migrated = migrateLegacySettings()
        if (migrated) {
            const merged = { ...DEFAULT_PIGTEX_SETTINGS, ...migrated } as StoredPigTexSettings
            const provider = isValidProviderId(merged.apiProvider)
                ? merged.apiProvider
                : DEFAULT_PIGTEX_SETTINGS.apiProvider
            let providerCredentialProfiles = normalizeProviderCredentialProfiles(merged.providerCredentialProfiles)
            providerCredentialProfiles = applyLegacySingleCredentialToProfiles(
                providerCredentialProfiles,
                provider,
                merged.baseUrl,
                merged.apiKey
            )
            merged.providerCredentialProfiles = providerCredentialProfiles

            const secureStorageEnabled = canUseSecureCredentialStorage()
            const storedApiKeys = extractProviderApiKeyMap(providerCredentialProfiles)
            const hasStoredApiKeys = Object.keys(storedApiKeys).length > 0

            if (hasStoredApiKeys) {
                if (secureStorageEnabled && merged.saveApiKeyLocally) {
                    const existingSecureApiKeys = getSecureApiKeyMap()
                    const persisted =
                        Object.keys(existingSecureApiKeys).length > 0
                        || setSecureApiKeyMap({
                            ...storedApiKeys,
                            ...existingSecureApiKeys
                        })
                    if (persisted) {
                        merged.providerCredentialProfiles = clearProviderApiKeys(providerCredentialProfiles)
                    } else {
                        merged.providerCredentialProfiles = promoteStoredApiKeysToSession(providerCredentialProfiles)
                        merged.saveApiKeyLocally = false
                    }
                } else {
                    merged.providerCredentialProfiles = promoteStoredApiKeysToSession(providerCredentialProfiles)
                    merged.saveApiKeyLocally = false
                }
                merged.apiKey = ''
            } else if (merged.saveApiKeyLocally && !secureStorageEnabled) {
                merged.saveApiKeyLocally = false
            }
            localStorage.setItem(SETTINGS_STORAGE_KEY, JSON.stringify(merged))
            return merged
        }
        return { ...DEFAULT_PIGTEX_SETTINGS }
    }

    try {
        const parsed = JSON.parse(raw) as Partial<StoredPigTexSettings>
        const provider = isValidProviderId(parsed.apiProvider) ? parsed.apiProvider : DEFAULT_PIGTEX_SETTINGS.apiProvider
        let providerCredentialProfiles = normalizeProviderCredentialProfiles(parsed.providerCredentialProfiles)
        providerCredentialProfiles = applyLegacySingleCredentialToProfiles(
            providerCredentialProfiles,
            provider,
            parsed.baseUrl,
            parsed.apiKey
        )
        const secureStorageEnabled = canUseSecureCredentialStorage()
        let saveApiKeyLocally = parsed.saveApiKeyLocally ?? DEFAULT_PIGTEX_SETTINGS.saveApiKeyLocally
        const storedApiKeys = extractProviderApiKeyMap(providerCredentialProfiles)
        const hasStoredApiKeys = Object.keys(storedApiKeys).length > 0
        let shouldRewriteStoredSettings = false

        if (saveApiKeyLocally && !secureStorageEnabled) {
            saveApiKeyLocally = false
            shouldRewriteStoredSettings = true
        }

        if (hasStoredApiKeys) {
            if (secureStorageEnabled && saveApiKeyLocally) {
                const existingSecureApiKeys = getSecureApiKeyMap()
                const persisted =
                    Object.keys(existingSecureApiKeys).length > 0
                    || setSecureApiKeyMap({
                        ...storedApiKeys,
                        ...existingSecureApiKeys
                    })
                if (persisted) {
                    providerCredentialProfiles = clearProviderApiKeys(providerCredentialProfiles)
                } else {
                    providerCredentialProfiles = promoteStoredApiKeysToSession(providerCredentialProfiles)
                    saveApiKeyLocally = false
                }
            } else {
                providerCredentialProfiles = promoteStoredApiKeysToSession(providerCredentialProfiles)
            }
            shouldRewriteStoredSettings = true
        }

        if (shouldRewriteStoredSettings) {
            localStorage.setItem(
                SETTINGS_STORAGE_KEY,
                JSON.stringify({
                    ...parsed,
                    providerCredentialProfiles,
                    apiKey: '',
                    saveApiKeyLocally
                })
            )
        }
        const activeProfile = providerCredentialProfiles[provider]
        const customEndpoint = resolveCustomEndpointForProvider(
            provider,
            parsed.customEndpoint,
            activeProfile.baseUrl,
            activeProfile.apiKey
        )
        return {
            language: normalizeLanguage(parsed.language),
            apiProvider: provider,
            customEndpoint,
            providerCredentialProfiles,
            model: parsed.model || DEFAULT_PIGTEX_SETTINGS.model,
            baseUrl: resolveBaseUrlForProvider(provider, activeProfile.baseUrl, customEndpoint),
            memoryEnabled: parsed.memoryEnabled ?? DEFAULT_PIGTEX_SETTINGS.memoryEnabled,
            useKnowledge: parsed.useKnowledge ?? DEFAULT_PIGTEX_SETTINGS.useKnowledge,
            useFacts: parsed.useFacts ?? DEFAULT_PIGTEX_SETTINGS.useFacts,
            useHistory: parsed.useHistory ?? DEFAULT_PIGTEX_SETTINGS.useHistory,
            temperature: normalizeTemperature(parsed.temperature),
            maxTokens: normalizeMaxTokens(parsed.maxTokens),
            customInstruction: normalizeCustomInstruction(parsed.customInstruction),
            defaultAiFileMode: parsed.defaultAiFileMode ?? DEFAULT_PIGTEX_SETTINGS.defaultAiFileMode,
            autoApproveAiFileActions:
                parsed.autoApproveAiFileActions ?? DEFAULT_PIGTEX_SETTINGS.autoApproveAiFileActions,
            enableQwenImagePromptEnhancer:
                parsed.enableQwenImagePromptEnhancer ?? DEFAULT_PIGTEX_SETTINGS.enableQwenImagePromptEnhancer,
            saveApiKeyLocally,
            apiKey: activeProfile.apiKey
        }
    } catch {
        return { ...DEFAULT_PIGTEX_SETTINGS }
    }
}

function getSessionApiKeyMap(): Partial<Record<ApiProviderId, string>> {
    if (!isBrowser()) return {}
    const raw = sessionStorage.getItem(SESSION_API_KEYS_STORAGE_KEY)
    if (raw) {
        try {
            const parsed = JSON.parse(raw) as Record<string, unknown>
            const normalized: Partial<Record<ApiProviderId, string>> = {}
            for (const providerId of API_PROVIDER_MODE_IDS) {
                const value = parsed[providerId]
                if (typeof value === 'string' && value.trim()) {
                    normalized[providerId] = value.trim()
                }
            }
            return normalized
        } catch {
            return {}
        }
    }

    // Backward compatibility with old single-key session storage.
    const legacy = (sessionStorage.getItem(LEGACY_SESSION_API_KEY_STORAGE_KEY) || '').trim()
    if (!legacy) return {}
    return { auto: legacy }
}

function setSessionApiKeyMap(map: Partial<Record<ApiProviderId, string>>): void {
    if (!isBrowser()) return
    const normalized: Partial<Record<ApiProviderId, string>> = {}
    for (const providerId of API_PROVIDER_MODE_IDS) {
        const value = map[providerId]
        if (typeof value === 'string' && value.trim()) {
            normalized[providerId] = value.trim()
        }
    }
    if (Object.keys(normalized).length === 0) {
        sessionStorage.removeItem(SESSION_API_KEYS_STORAGE_KEY)
    } else {
        sessionStorage.setItem(SESSION_API_KEYS_STORAGE_KEY, JSON.stringify(normalized))
    }
    sessionStorage.removeItem(LEGACY_SESSION_API_KEY_STORAGE_KEY)
}

export function getPigTexSettings(): PigTexSettings {
    const stored = readStoredSettings()
    const provider = isValidProviderId(stored.apiProvider) ? stored.apiProvider : DEFAULT_PIGTEX_SETTINGS.apiProvider
    const sessionApiKeys = getSessionApiKeyMap()
    const providerCredentialProfiles = normalizeProviderCredentialProfiles(stored.providerCredentialProfiles)
    const secureStorageEnabled = canUseSecureCredentialStorage()
    if (stored.saveApiKeyLocally && secureStorageEnabled) {
        const secureApiKeys = getSecureApiKeyMap()
        for (const providerId of API_PROVIDER_MODE_IDS) {
            providerCredentialProfiles[providerId].apiKey = (secureApiKeys[providerId] || '').trim()
        }
    } else {
        for (const providerId of API_PROVIDER_MODE_IDS) {
            providerCredentialProfiles[providerId].apiKey = (sessionApiKeys[providerId] || '').trim()
        }
    }
    const savedApiKey = providerCredentialProfiles[provider].apiKey.trim()
    const apiKey = savedApiKey
    const activeBaseUrl = resolveBaseUrlForProvider(
        provider,
        providerCredentialProfiles[provider].baseUrl,
        stored.customEndpoint
    )
    const customEndpoint = resolveCustomEndpointForProvider(
        provider,
        stored.customEndpoint,
        activeBaseUrl,
        apiKey
    )

    return {
        language: normalizeLanguage(stored.language),
        apiProvider: provider,
        customEndpoint,
        providerCredentialProfiles,
        apiKey,
        baseUrl: resolveBaseUrlForProvider(provider, activeBaseUrl, customEndpoint),
        model: (stored.model || DEFAULT_PIGTEX_SETTINGS.model).trim() || DEFAULT_PIGTEX_SETTINGS.model,
        memoryEnabled: stored.memoryEnabled ?? DEFAULT_PIGTEX_SETTINGS.memoryEnabled,
        useKnowledge: stored.useKnowledge ?? DEFAULT_PIGTEX_SETTINGS.useKnowledge,
        useFacts: stored.useFacts ?? DEFAULT_PIGTEX_SETTINGS.useFacts,
        useHistory: stored.useHistory ?? DEFAULT_PIGTEX_SETTINGS.useHistory,
        temperature: normalizeTemperature(stored.temperature),
        maxTokens: normalizeMaxTokens(stored.maxTokens),
        customInstruction: normalizeCustomInstruction(stored.customInstruction),
        defaultAiFileMode: stored.defaultAiFileMode ?? DEFAULT_PIGTEX_SETTINGS.defaultAiFileMode,
        autoApproveAiFileActions:
            stored.autoApproveAiFileActions ?? DEFAULT_PIGTEX_SETTINGS.autoApproveAiFileActions,
        enableQwenImagePromptEnhancer:
            stored.enableQwenImagePromptEnhancer ?? DEFAULT_PIGTEX_SETTINGS.enableQwenImagePromptEnhancer,
        saveApiKeyLocally: stored.saveApiKeyLocally ?? DEFAULT_PIGTEX_SETTINGS.saveApiKeyLocally
    }
}

function dispatchSettingsChanged(settings: PigTexSettings): void {
    if (!isBrowser()) return
    window.dispatchEvent(
        new CustomEvent(PIGTEX_SETTINGS_CHANGED_EVENT, {
            detail: settings
        })
    )
}

export function savePigTexSettings(settings: PigTexSettings): PigTexSettings {
    const provider = isValidProviderId(settings.apiProvider) ? settings.apiProvider : DEFAULT_PIGTEX_SETTINGS.apiProvider
    const customEndpoint = normalizeCustomEndpoint(settings.customEndpoint)
    const existingProfiles = normalizeProviderCredentialProfiles(settings.providerCredentialProfiles)
    const providerCredentialProfiles = cloneProviderCredentialProfiles(existingProfiles)
    providerCredentialProfiles[provider] = {
        apiKey: settings.apiKey.trim(),
        baseUrl: resolveBaseUrlForProvider(provider, settings.baseUrl, customEndpoint),
    }

    const normalized: PigTexSettings = {
        language: normalizeLanguage(settings.language),
        apiProvider: provider,
        customEndpoint,
        apiKey: providerCredentialProfiles[provider].apiKey,
        baseUrl: providerCredentialProfiles[provider].baseUrl,
        providerCredentialProfiles,
        model: settings.model.trim() || DEFAULT_PIGTEX_SETTINGS.model,
        memoryEnabled: settings.memoryEnabled,
        useKnowledge: settings.useKnowledge,
        useFacts: settings.useFacts,
        useHistory: settings.useHistory,
        temperature: normalizeTemperature(settings.temperature),
        maxTokens: normalizeMaxTokens(settings.maxTokens),
        customInstruction: normalizeCustomInstruction(settings.customInstruction),
        defaultAiFileMode: settings.defaultAiFileMode,
        autoApproveAiFileActions: settings.autoApproveAiFileActions,
        enableQwenImagePromptEnhancer: settings.enableQwenImagePromptEnhancer,
        saveApiKeyLocally: settings.saveApiKeyLocally,
    }

    if (isBrowser()) {
        const secureStorageEnabled = canUseSecureCredentialStorage()
        if (normalized.saveApiKeyLocally && !secureStorageEnabled) {
            normalized.saveApiKeyLocally = false
        }
        const storedProfiles = cloneProviderCredentialProfiles(normalized.providerCredentialProfiles)
        if (secureStorageEnabled) {
            if (normalized.saveApiKeyLocally) {
                const persisted = setSecureApiKeyMap(extractProviderApiKeyMap(storedProfiles))
                normalized.saveApiKeyLocally = persisted
            } else {
                setSecureApiKeyMap({})
            }
        }
        if (secureStorageEnabled || !normalized.saveApiKeyLocally) {
            for (const providerId of API_PROVIDER_MODE_IDS) {
                storedProfiles[providerId].apiKey = ''
            }
        }

        const stored: StoredPigTexSettings = {
            language: normalized.language,
            apiProvider: normalized.apiProvider,
            customEndpoint: normalized.customEndpoint,
            providerCredentialProfiles: storedProfiles,
            model: normalized.model,
            baseUrl: normalized.baseUrl,
            memoryEnabled: normalized.memoryEnabled,
            useKnowledge: normalized.useKnowledge,
            useFacts: normalized.useFacts,
            useHistory: normalized.useHistory,
            temperature: normalized.temperature,
            maxTokens: normalized.maxTokens,
            customInstruction: normalized.customInstruction,
            defaultAiFileMode: normalized.defaultAiFileMode,
            autoApproveAiFileActions: normalized.autoApproveAiFileActions,
            enableQwenImagePromptEnhancer: normalized.enableQwenImagePromptEnhancer,
            saveApiKeyLocally: normalized.saveApiKeyLocally,
            // Legacy fallback field (v1 shape)
            apiKey: normalized.saveApiKeyLocally ? storedProfiles[provider].apiKey : ''
        }
        localStorage.setItem(SETTINGS_STORAGE_KEY, JSON.stringify(stored))
        if (normalized.saveApiKeyLocally) {
            setSessionApiKeyMap({})
        } else {
            const currentSessionMap = getSessionApiKeyMap()
            const nextSessionMap: Partial<Record<ApiProviderId, string>> = { ...currentSessionMap }
            const activeApiKey = normalized.apiKey.trim()
            if (activeApiKey) {
                nextSessionMap[provider] = activeApiKey
            } else {
                delete nextSessionMap[provider]
            }
            setSessionApiKeyMap(nextSessionMap)
        }
    }

    dispatchSettingsChanged(normalized)
    return normalized
}

export function updatePigTexSettings(patch: Partial<PigTexSettings>): PigTexSettings {
    const current = getPigTexSettings()
    return savePigTexSettings({
        ...current,
        ...patch
    })
}

// ===== Provider Helpers (exported for api.ts and UI) =====

/** Get the provider config for current settings */
export function getProviderConfig(providerId?: ApiProviderId): ApiProviderConfig {
    const id = providerId && isValidProviderId(providerId) ? providerId : DEFAULT_PROVIDER
    return API_PROVIDERS[id]
}

/** Get default base URL for a given provider */
export function getProviderDefaultBaseUrl(providerId: ApiProviderId): string {
    if (providerId === 'gemini') {
        return getPublicProviderDefaultBaseUrl('google') || API_PROVIDERS.gemini.defaultBaseUrl
    }
    if (providerId === 'openai') {
        return getPublicProviderDefaultBaseUrl('openai') || API_PROVIDERS.openai.defaultBaseUrl
    }
    if (providerId === 'anthropic') {
        return getPublicProviderDefaultBaseUrl('anthropic') || API_PROVIDERS.anthropic.defaultBaseUrl
    }
    if (providerId === 'alibaba') {
        return getPublicProviderDefaultBaseUrl('alibaba') || API_PROVIDERS.alibaba.defaultBaseUrl
    }
    return getPublicProviderDefaultBaseUrl('texapi') || API_PROVIDERS[DEFAULT_PROVIDER].defaultBaseUrl
}

export function resolveApiProviderForRequest(
    apiProvider: ApiProviderId,
    customEndpoint: ApiEndpointProviderId
): ApiEndpointProviderId {
    return apiProvider === 'auto' ? normalizeCustomEndpoint(customEndpoint) : apiProvider
}
