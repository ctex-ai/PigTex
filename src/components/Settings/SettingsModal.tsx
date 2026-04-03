import { useEffect, useMemo, useState } from 'react'
import { AnimatePresence, motion } from 'framer-motion'
import {
    X, Eye, EyeOff, RefreshCw, Plug, Sliders,
    Sparkles, Brain, Shield, ChevronRight, ExternalLink,
    User, HardDrive, Lock, AlertCircle, LogOut
} from 'lucide-react'
import {
    applyLocalCloudRestore,
    cancelSyncSubscription,
    changePassword,
    createSyncCheckoutSession,
    createSyncPortalSession,
    createLocalCloudBackup,
    deleteAccount,
    fetchProviderCatalog,
    getCloudSyncState,
    getModels,
    getModelsWithCredentials,
    getCloudUsage,
    getTexApiPartnerUsage,
    getSyncEntitlement,
    listCloudBackups,
    pullCloudSync,
    pushCloudSync,
    registerCloudDevice,
    validateApiConnection
} from '../../services/api'
import type {
    AIModel,
    CloudBackupListItem,
    CloudSyncState,
    CloudQuota,
    TexApiPartnerUsageSummary,
    CloudUsageSummary,
    SyncEntitlement
} from '../../services/api'
import {
    DEFAULT_PIGTEX_SETTINGS,
    PigTexSettings,
    getApiProviderCatalog,
    getApiProviderCatalogEntryForSelection,
    getProviderDefaultBaseUrl,
    getPublicProviderDefaultBaseUrl,
    mapCatalogProviderIdToSettingsSelection,
    mapSettingsSelectionToCatalogProviderId,
    resolveApiProviderForRequest
} from '../../services/settings'
import type {
    ApiEndpointProviderId,
    ApiProviderCatalogEntry,
    ApiProviderId,
    PublicApiProviderId,
} from '../../services/settings'
import {
    IDLE_DESKTOP_UPDATE_STATE
} from '../../services/desktopUpdate'
import type { DesktopUpdateState } from '../../services/desktopUpdate'
import { useAuth } from '../../contexts/AuthContext'
import { showError, showInfo, showSuccess } from '../Shared/Toast'
import './SettingsModal.css'

type SettingsTabId = 'profile' | 'connection' | 'behavior'

type ValidationFeedback = {
    kind: 'success' | 'error'
    message: string
} | null

type DeviceSnapshot = {
    hostname: string
    platform: string
    arch: string
    appVersion: string
    language: string
    timeZone: string
}

interface SettingsModalProps {
    isOpen: boolean
    settings: PigTexSettings
    onClose: () => void
    onSave: (settings: PigTexSettings) => void
    desktopUpdate?: DesktopUpdateState
    isCheckingDesktopUpdate?: boolean
    isInstallingDesktopUpdate?: boolean
    onCheckDesktopUpdate?: () => Promise<void> | void
    onInstallDesktopUpdate?: () => Promise<void> | void
    onOpenDesktopUpdatePage?: () => Promise<void> | void
}

const buildSettingsTabs = (isVietnamese: boolean): Array<{
    id: SettingsTabId
    label: string
    icon: typeof Plug
    description: string
}> => [
        {
            id: 'profile',
            label: isVietnamese ? 'Hồ sơ' : 'Profile',
            icon: User,
            description: isVietnamese
                ? 'Gói, thiết bị và bảo mật tài khoản'
                : 'Plan, device, and account security'
        },
        {
            id: 'connection',
            label: isVietnamese ? 'Kết nối' : 'Connection',
            icon: Plug,
            description: isVietnamese
                ? 'API, model và endpoint'
                : 'API, model, and endpoint'
        },
        {
            id: 'behavior',
            label: isVietnamese ? 'Hành vi' : 'Behavior',
            icon: Sliders,
            description: isVietnamese
                ? 'Phản hồi, memory và cá nhân hóa'
                : 'Responses, memory, and personalization'
        }
    ]

const isValidHttpUrl = (value: string): boolean => /^https?:\/\/\S+$/i.test(value.trim())

const normalizeBaseUrlInput = (value: string): string => value.trim().replace(/\/+$/, '')

const normalizeTemperature = (value: unknown): number => {
    const parsed = typeof value === 'number' ? value : Number(value)
    if (!Number.isFinite(parsed)) return DEFAULT_PIGTEX_SETTINGS.temperature
    const clamped = Math.min(2, Math.max(0, parsed))
    return Math.round(clamped * 100) / 100
}

const normalizeMaxTokens = (value: unknown): number => {
    const parsed = typeof value === 'number' ? value : Number(value)
    if (!Number.isFinite(parsed)) return DEFAULT_PIGTEX_SETTINGS.maxTokens
    const normalized = Math.trunc(parsed)
    if (normalized <= 0) return 0
    return Math.min(normalized, 32768)
}

const formatPlanLabel = (plan?: string | null, isVietnamese: boolean = true): string => {
    const normalized = (plan || 'free').trim().toLowerCase()
    if (!normalized) return isVietnamese ? 'Miễn phí' : 'Free'
    if (normalized === 'free') return isVietnamese ? 'Miễn phí' : 'Free'
    if (normalized === 'sync') return 'PigTex Sync'
    if (normalized === 'sync_plus') return 'PigTex Sync Plus'
    return normalized.charAt(0).toUpperCase() + normalized.slice(1)
}

const formatSubscriptionStatusLabel = (status?: string | null, isVietnamese: boolean = true): string => {
    const normalized = (status || '').trim().toLowerCase()
    if (normalized === 'active') return isVietnamese ? 'Đang hoạt động' : 'Active'
    if (normalized === 'grace_period') return isVietnamese ? 'Gia hạn chờ thanh toán' : 'Grace period'
    if (normalized === 'free') return isVietnamese ? 'Miễn phí' : 'Free'
    return normalized || (isVietnamese ? 'Không rõ' : 'Unknown')
}

const formatBillingCycleLabel = (cycle?: string | null, isVietnamese: boolean = true): string => {
    const normalized = (cycle || '').trim().toLowerCase()
    if (normalized === 'monthly') return isVietnamese ? 'Tháng' : 'Monthly'
    if (normalized === 'annual') return isVietnamese ? 'Năm' : 'Annual'
    return normalized || (isVietnamese ? 'Chưa có' : 'Not set')
}

const formatVndPrice = (value?: number | null): string => {
    const amount = typeof value === 'number' && Number.isFinite(value) ? Math.max(0, value) : 0
    return new Intl.NumberFormat('vi-VN').format(amount)
}

const formatProviderLabel = (provider?: string | null, isVietnamese: boolean = true): string => {
    const normalized = (provider || '').trim().toLowerCase()
    if (normalized === 'google') return 'Google'
    if (normalized === 'github') return 'GitHub'
    return isVietnamese ? 'Email' : 'Email'
}

const formatPlatformLabel = (platform?: string | null, isVietnamese: boolean = true): string => {
    const normalized = (platform || '').trim().toLowerCase()
    if (normalized === 'win32') return 'Windows'
    if (normalized === 'darwin') return 'macOS'
    if (normalized === 'linux') return 'Linux'
    return normalized || (isVietnamese ? 'Không rõ' : 'Unknown')
}

const formatDateTimeLabel = (
    value?: string | null,
    locale: string = 'vi-VN',
    emptyLabel: string = 'Chưa có dữ liệu'
): string => {
    if (!value) return emptyLabel
    const date = new Date(value)
    if (Number.isNaN(date.getTime())) return emptyLabel
    return new Intl.DateTimeFormat(locale, {
        day: '2-digit',
        month: '2-digit',
        year: 'numeric',
        hour: '2-digit',
        minute: '2-digit'
    }).format(date)
}

const getProfileInitial = (value?: string | null): string => {
    const normalized = (value || '').trim()
    return normalized ? normalized.charAt(0).toUpperCase() : 'P'
}

const buildFallbackDeviceSnapshot = (isVietnamese: boolean = true): DeviceSnapshot => ({
    hostname: isVietnamese ? 'Thiết bị hiện tại' : 'Current device',
    platform: typeof navigator !== 'undefined' ? navigator.platform || 'unknown' : 'unknown',
    arch: 'unknown',
    appVersion: 'desktop',
    language: typeof navigator !== 'undefined' ? navigator.language || 'unknown' : 'unknown',
    timeZone: Intl.DateTimeFormat().resolvedOptions().timeZone || 'UTC'
})

const CLOUD_DEVICE_KEY_STORAGE = 'pigtex_cloud_device_key_v1'

const generateCloudDeviceKey = (): string => {
    if (typeof globalThis.crypto !== 'undefined' && typeof globalThis.crypto.randomUUID === 'function') {
        return globalThis.crypto.randomUUID()
    }
    return `device-${Math.random().toString(36).slice(2, 10)}-${Date.now().toString(36)}`
}

const getOrCreateCloudDeviceKey = (): string => {
    if (typeof window === 'undefined' || !window.localStorage) {
        return generateCloudDeviceKey()
    }

    const existing = window.localStorage.getItem(CLOUD_DEVICE_KEY_STORAGE)?.trim()
    if (existing) return existing

    const nextValue = generateCloudDeviceKey()
    window.localStorage.setItem(CLOUD_DEVICE_KEY_STORAGE, nextValue)
    return nextValue
}

const formatByteSize = (value?: number | null): string => {
    const size = typeof value === 'number' && Number.isFinite(value) ? Math.max(0, value) : 0
    if (size < 1024) return `${size} B`

    const units = ['KB', 'MB', 'GB', 'TB']
    let normalized = size / 1024
    let unitIndex = 0
    while (normalized >= 1024 && unitIndex < units.length - 1) {
        normalized /= 1024
        unitIndex += 1
    }

    const digits = normalized >= 100 ? 0 : normalized >= 10 ? 1 : 2
    return `${normalized.toFixed(digits)} ${units[unitIndex]}`
}

const formatCurrencyAmount = (
    value: number | null | undefined,
    locale: string = 'vi-VN',
    currency: string = 'USD'
): string => {
    if (typeof value !== 'number' || !Number.isFinite(value)) return '—'
    const normalizedCurrency = (currency || 'USD').trim().toUpperCase() || 'USD'
    try {
        return new Intl.NumberFormat(locale, {
            style: 'currency',
            currency: normalizedCurrency,
            minimumFractionDigits: value < 10 ? 2 : 0,
            maximumFractionDigits: 2
        }).format(value)
    } catch {
        return `${new Intl.NumberFormat(locale, {
            minimumFractionDigits: 2,
            maximumFractionDigits: 2
        }).format(value)} ${normalizedCurrency}`
    }
}

const formatCountValue = (value: number | null | undefined, locale: string = 'vi-VN'): string => {
    if (typeof value !== 'number' || !Number.isFinite(value)) return '—'
    return new Intl.NumberFormat(locale, { maximumFractionDigits: 0 }).format(value)
}

const formatDateRangeLabel = (
    start: string | null | undefined,
    end: string | null | undefined,
    locale: string,
    emptyLabel: string
): string => {
    if (!start && !end) return emptyLabel
    const startLabel = start ? formatDateTimeLabel(start, locale, emptyLabel) : emptyLabel
    const endLabel = end ? formatDateTimeLabel(end, locale, emptyLabel) : emptyLabel
    if (start && end) {
        return `${startLabel} → ${endLabel}`
    }
    return start ? startLabel : endLabel
}

const formatCloudStatusLabel = (status?: string | null, isVietnamese: boolean = true): string => {
    const normalized = (status || '').trim().toLowerCase()
    if (normalized === 'ready') return isVietnamese ? 'Sẵn sàng' : 'Ready'
    if (normalized === 'upload_requested') return isVietnamese ? 'Chờ tải lên' : 'Upload requested'
    if (normalized === 'uploading') return isVietnamese ? 'Đang tải lên' : 'Uploading'
    if (normalized === 'failed') return isVietnamese ? 'Lỗi' : 'Failed'
    if (normalized === 'deleted') return isVietnamese ? 'Đã xóa' : 'Deleted'
    return normalized || (isVietnamese ? 'Không rõ' : 'Unknown')
}

const formatCloudScopeLabel = (scopeType?: string | null, isVietnamese: boolean = true): string => {
    const normalized = (scopeType || '').trim().toLowerCase()
    if (normalized === 'account') return isVietnamese ? 'Toàn bộ tài khoản' : 'Full account'
    return normalized || (isVietnamese ? 'Không rõ phạm vi' : 'Unknown scope')
}

const formatSyncStateLabel = (value?: string | null, isVietnamese: boolean = true): string => {
    const normalized = (value || '').trim().toLowerCase()
    if (normalized === 'idle') return isVietnamese ? 'Đồng bộ' : 'In sync'
    if (normalized === 'push_needed') return isVietnamese ? 'Có thay đổi local' : 'Local changes pending'
    if (normalized === 'pull_needed') return isVietnamese ? 'Có thay đổi từ cloud' : 'Remote changes pending'
    if (normalized === 'bidirectional') return isVietnamese ? 'Hai chiều' : 'Bi-directional'
    return normalized || (isVietnamese ? 'Không rõ' : 'Unknown')
}

const formatCloudStatsSummary = (stats?: Record<string, number> | null): string => {
    if (!stats) return ''

    const parts = Object.entries(stats)
        .filter(([, value]) => typeof value === 'number' && value > 0)
        .sort((left, right) => right[1] - left[1])
        .slice(0, 3)
        .map(([key, value]) => `${value} ${key}`)

    return parts.join(' • ')
}

const MODEL_PLACEHOLDER_BY_PROVIDER: Record<ApiEndpointProviderId, string> = {
    openai: 'gpt-4o',
    anthropic: 'claude-sonnet-4-20250514',
    gemini: 'gemini-2.5-flash',
    alibaba: 'qwen-plus-latest',
}

const createEmptyCredentialProfiles = (): PigTexSettings['providerCredentialProfiles'] => ({
    auto: { apiKey: '', baseUrl: getProviderDefaultBaseUrl('auto') },
    openai: { apiKey: '', baseUrl: getProviderDefaultBaseUrl('openai') },
    anthropic: { apiKey: '', baseUrl: getProviderDefaultBaseUrl('anthropic') },
    gemini: { apiKey: '', baseUrl: getProviderDefaultBaseUrl('gemini') },
    alibaba: { apiKey: '', baseUrl: getProviderDefaultBaseUrl('alibaba') },
})

const normalizeProfileBaseUrl = (provider: ApiProviderId, baseUrl: string): string => {
    if (provider !== 'auto') {
        return getProviderDefaultBaseUrl(provider)
    }
    return normalizeBaseUrlInput(baseUrl)
}

const ensureCredentialProfiles = (
    profiles?: PigTexSettings['providerCredentialProfiles']
): PigTexSettings['providerCredentialProfiles'] => {
    const defaults = createEmptyCredentialProfiles()
    if (!profiles) return defaults
    return {
        auto: {
            apiKey: profiles.auto?.apiKey || '',
            baseUrl: normalizeProfileBaseUrl('auto', profiles.auto?.baseUrl || ''),
        },
        openai: {
            apiKey: profiles.openai?.apiKey || '',
            baseUrl: normalizeProfileBaseUrl('openai', profiles.openai?.baseUrl || ''),
        },
        anthropic: {
            apiKey: profiles.anthropic?.apiKey || '',
            baseUrl: normalizeProfileBaseUrl('anthropic', profiles.anthropic?.baseUrl || ''),
        },
        gemini: {
            apiKey: profiles.gemini?.apiKey || '',
            baseUrl: normalizeProfileBaseUrl('gemini', profiles.gemini?.baseUrl || ''),
        },
        alibaba: {
            apiKey: profiles.alibaba?.apiKey || '',
            baseUrl: normalizeProfileBaseUrl('alibaba', profiles.alibaba?.baseUrl || ''),
        },
    }
}

const withCurrentCredentialSynced = (value: PigTexSettings): PigTexSettings => {
    const profiles = ensureCredentialProfiles(value.providerCredentialProfiles)
    const currentProvider = value.apiProvider
    profiles[currentProvider] = {
        apiKey: value.apiKey,
        baseUrl: normalizeProfileBaseUrl(currentProvider, value.baseUrl),
    }

    return {
        ...value,
        providerCredentialProfiles: profiles,
        apiKey: value.apiKey.trim(),
        baseUrl: normalizeProfileBaseUrl(currentProvider, value.baseUrl),
    }
}

const sanitizeForSave = (value: PigTexSettings): PigTexSettings => ({
    ...withCurrentCredentialSynced(value),
    model: value.model.trim(),
    temperature: normalizeTemperature(value.temperature),
    maxTokens: normalizeMaxTokens(value.maxTokens),
    customInstruction: value.customInstruction.trim()
})

const serializeForCompare = (value: PigTexSettings): string => JSON.stringify(sanitizeForSave(value))

/* ─────────────────────────── Reusable section wrapper ─────────────────────────── */

interface SettingsSectionProps {
    icon: typeof Plug
    title: string
    description?: string
    children: React.ReactNode
    accent?: boolean
}

const SettingsSection = ({ icon: Icon, title, description, children, accent }: SettingsSectionProps) => (
    <div className={`stg-section ${accent ? 'stg-section--accent' : ''}`}>
        <div className="stg-section-header">
            <div className="stg-section-icon">
                <Icon size={15} />
            </div>
            <div className="stg-section-text">
                <span className="stg-section-title">{title}</span>
                {description && <span className="stg-section-desc">{description}</span>}
            </div>
        </div>
        <div className="stg-section-body">
            {children}
        </div>
    </div>
)

/* ─────────────────────────── SettingsModal ─────────────────────────── */

const SettingsModal = ({
    isOpen,
    settings,
    onClose,
    onSave,
    desktopUpdate = IDLE_DESKTOP_UPDATE_STATE,
    isCheckingDesktopUpdate = false,
    isInstallingDesktopUpdate = false,
    onCheckDesktopUpdate = () => undefined,
    onInstallDesktopUpdate = () => undefined,
    onOpenDesktopUpdatePage = () => undefined
}: SettingsModalProps) => {
    const { user, logout, refreshUser } = useAuth()
    const initialIsVietnamese = settings.language !== 'en'
    const [draft, setDraft] = useState<PigTexSettings>(settings)
    const previewIsVietnamese = draft.language !== 'en'
    const previewLocale = previewIsVietnamese ? 'vi-VN' : 'en-US'
    const settingsTabs = buildSettingsTabs(previewIsVietnamese)
    const copy = previewIsVietnamese ? {
        settings: 'Cài đặt',
        unsavedChanges: 'Có thay đổi chưa lưu',
        close: 'Đóng',
        settingsSections: 'Các mục cài đặt',
        restoreDefaults: 'Khôi phục mặc định',
        noData: 'Chưa có dữ liệu',
        pigtexUser: 'Người dùng PigTex',
        noEmail: 'Không có email',
        account: 'Tài khoản',
        accountDescription: 'Gói hiện tại, phương thức đăng nhập và thời gian hoạt động',
        currentPlan: 'Gói hiện tại',
        syncSubscription: 'PigTex Sync',
        syncSubscriptionDescription: 'Cloud backup, chuyển máy và personal sync theo subscription',
        subscriptionStatus: 'Trạng thái subscription',
        billingCycle: 'Chu kỳ thanh toán',
        graceEnds: 'Hết grace',
        upgradeToSync: 'Nâng cấp Sync',
        upgradeToSyncPlus: 'Nâng cấp Sync Plus',
        manageSubscription: 'Quản lý gói',
        cancelSubscription: 'Hủy gói',
        cancelSubscriptionConfirm: 'Hủy PigTex Sync ở cuối chu kỳ hiện tại?',
        subscriptionUpdated: 'Đã cập nhật subscription PigTex Sync',
        subscriptionCanceled: 'Đã cập nhật trạng thái hủy subscription',
        subscriptionManageUnavailable: 'Billing portal chưa khả dụng cho cấu hình hiện tại',
        checkoutOpenedPending: 'Đã mở trang thanh toán. PigTex Sync sẽ được kích hoạt sau khi PayOS báo thanh toán thành công.',
        checkoutActivationPending: 'Nếu chưa thấy cập nhật ngay, quay lại app và nhấn làm mới sau vài giây.',
        signedInWith: 'Đăng nhập bằng',
        createdAt: 'Ngày tạo',
        lastLogin: 'Lần đăng nhập cuối',
        signOut: 'Đăng xuất',
        signOutDescription: 'Kết thúc phiên PigTex hiện tại trên thiết bị này',
        signOutHint: 'Bạn có thể đăng nhập lại bất cứ lúc nào bằng email hoặc OAuth.',
        signedOut: 'Đã đăng xuất',
        resetLocalData: 'Xóa toàn bộ data trên máy',
        resetLocalDataDescription: 'Xóa toàn bộ dữ liệu PigTex trên thiết bị này để app trở về trạng thái mới tinh',
        resetLocalDataWarning: 'Thao tác này xóa settings, phiên đăng nhập, cache, secure key và dữ liệu local của PigTex trên máy này.',
        resetLocalDataServerNote: 'Không xóa tài khoản PigTex, subscription hay dữ liệu cloud/server.',
        resetLocalDataConfirm: 'Xóa toàn bộ dữ liệu PigTex trên máy này? Ứng dụng sẽ tải lại về trạng thái mới tinh.',
        resetLocalDataSuccess: 'Đã xóa toàn bộ dữ liệu local của PigTex. Ứng dụng sẽ tải lại.',
        resetLocalDataFailed: 'Không thể xóa toàn bộ dữ liệu local của PigTex',
        resettingLocalData: 'Đang xóa dữ liệu...',
        currentDevice: 'Thiết bị hiện tại',
        loadingDeviceInfo: 'Đang tải thông tin thiết bị',
        currentDeviceDescription: 'Phiên PigTex đang mở trên máy này',
        device: 'Thiết bị',
        platform: 'Hệ điều hành',
        architecture: 'Kiến trúc',
        appVersion: 'Phiên bản app',
        language: 'Ngôn ngữ',
        timeZone: 'Múi giờ',
        deviceHint: 'Đây là thông tin của thiết bị hiện đang chạy ứng dụng desktop.',
        cloudBackup: 'Cloud Backup',
        cloudBackupDescription: 'Backup lên cloud để chuyển máy, khôi phục dữ liệu và làm nền cho sync/share sau này',
        cloudLockedDescription: 'Tính năng này chỉ mở cho PigTex Sync.',
        backupNow: 'Backup ngay',
        backingUp: 'Đang backup...',
        syncNow: 'Sync ngay',
        syncing: 'Đang sync...',
        syncState: 'Trạng thái sync',
        syncCompleted: 'Đã sync xong với cloud',
        syncFailed: 'Không thể sync với cloud',
        reload: 'Tải lại',
        cloudDeviceReady: 'Thiết bị này đã đăng ký sẵn sàng backup.',
        cloudRegisteringDevice: 'Đang đăng ký thiết bị với cloud backup.',
        cloudNotReady: 'Cloud backup chưa sẵn sàng.',
        status: 'Trạng thái',
        connected: 'Đã kết nối cloud backup',
        notConnected: 'Chưa kết nối',
        storage: 'Dung lượng',
        snapshots: 'Snapshot',
        retention: 'Retention',
        days: 'ngày',
        cloudUsageHint: (percent: number) => `Đã dùng ${percent}% quota cloud cho tài khoản này.`,
        loadingCloudBackups: 'Đang tải danh sách backup cloud...',
        noCloudSnapshots: 'Chưa có snapshot nào. Tạo backup đầu tiên để có thể chuyển dữ liệu sang máy khác.',
        restore: 'Khôi phục',
        restoring: 'Đang khôi phục...',
        appLanguageTitle: 'Ngôn ngữ ứng dụng',
        appLanguageDescription: 'Chọn ngôn ngữ giao diện cho PigTex desktop',
        appLanguageHint: 'Thay đổi sẽ áp dụng cho toàn bộ ứng dụng sau khi lưu.',
        vietnamese: 'Tiếng Việt',
        english: 'English',
        changePassword: 'Đổi mật khẩu',
        createPassword: 'Tạo mật khẩu',
        changePasswordDescription: 'Cập nhật mật khẩu đăng nhập bằng email',
        createPasswordDescription: 'Thêm mật khẩu để dùng song song với đăng nhập OAuth',
        oauthPasswordHint: (provider: string) => `Tài khoản đang đăng nhập bằng ${provider}. Bạn có thể đặt mật khẩu để đăng nhập bằng email sau này.`,
        currentPassword: 'Mật khẩu hiện tại',
        newPassword: 'Mật khẩu mới',
        confirmNewPassword: 'Xác nhận mật khẩu mới',
        minimumPasswordHint: 'Tối thiểu 8 ký tự.',
        updating: 'Đang cập nhật...',
        updatePassword: 'Cập nhật mật khẩu',
        deleteAccount: 'Xóa tài khoản',
        deleteAccountDescription: 'Hành động này sẽ xóa toàn bộ dữ liệu gắn với tài khoản',
        deleteAccountWarning: 'Toàn bộ workspace, đoạn chat, knowledge và lịch sử sử dụng sẽ bị xóa vĩnh viễn.',
        enterEmailToConfirm: 'Nhập email để xác nhận',
        password: 'Mật khẩu',
        deleting: 'Đang xóa...',
        cannotUndo: 'Không thể hoàn tác.',
        endpointMode: 'Endpoint Mode',
        endpointModeDescription: 'Chọn đúng 1 trong 5 provider công khai của PigTex',
        mode: 'Mode',
        getKey: 'Lấy key',
        hideSecret: 'Ẩn',
        showSecret: 'Hiện',
        defaultBaseUrl: 'Mặc định',
        baseUrlLockedHint: 'Provider hiện tại dùng URL gốc cố định để tránh endpoint chồng chéo.',
        baseUrlCustomHint: 'Custom cho phép dùng bất kỳ host/proxy tương thích nào.',
        endpointProtocol: 'Endpoint protocol',
        endpointProtocolHint: 'Chọn đúng giao thức để parse stream/request theo endpoint bạn dùng.',
        providerManagedConnectionHint: 'Provider này không hỗ trợ nhập credential ở màn hình này.',
        providerManagedBaseUrlHint: 'Base URL này được quản lý ở tầng cấu hình khác.',
        providerManagedTestHint: 'Provider này không hỗ trợ test kết nối từ client.',
        providerManagedTexapiConnectionHint: 'TexAPI ở đây được PigTex quản lý sẵn cho tài khoản của bạn.',
        providerManagedTexapiHint: 'TexAPI hoạt động như một cổng bình thường: bạn có thể nhập API key và Base URL riêng. Nếu để trống API key và giữ Base URL mặc định, PigTex sẽ tự dùng gateway partner cùng credits TexAPI đã cấp cho tài khoản này.',
        texapiByokHint: 'Bạn đang dùng cấu hình TexAPI riêng. PigTex sẽ không dùng credits TexAPI mặc định cho kết nối này.',
        texapiApiKeyPlaceholder: 'Để trống để PigTex tự dùng TexAPI mặc định, hoặc nhập API key TexAPI của bạn',
        texapiCreditsTitle: 'TexAPI credits',
        texapiCreditsDescription: 'Chỉ áp dụng khi bạn để trống API key và dùng Base URL mặc định của TexAPI trên PigTex.',
        texapiRemaining: 'Còn lại',
        texapiUsed: 'Đã dùng',
        texapiRequests: 'Lượt gọi',
        texapiPeriod: 'Chu kỳ',
        texapiUsageLoading: 'Đang tải usage TexAPI...',
        texapiUsageLoadFailed: 'Không tải được usage TexAPI lúc này.',
        texapiUsageEmpty: 'Tài khoản này chưa có usage TexAPI hiện ra.',
        texapiSignInHint: 'Đăng nhập tài khoản PigTex để dùng credits TexAPI được cấp sẵn.',
        model: 'Model',
        loadingModels: 'Đang tải danh sách Model...',
        availableModels: (count: number) => `${count} model khả dụng`,
        testingConnection: 'Đang kiểm tra...',
        testConnection: 'Kiểm tra kết nối',
        security: 'Bảo mật',
        securityDescription: 'Cách lưu trữ API key',
        storeApiKey: 'Lưu API key an toàn trên máy này',
        storeApiKeyOn: 'API key được lưu trong vùng bảo mật của hệ điều hành trên máy này.',
        storeApiKeyOff: 'API key chỉ tồn tại theo session, tự xóa khi đóng app.',
        storeApiKeyUnavailable: 'Thiết bị này không hỗ trợ secure storage của hệ điều hành, nên API key chỉ có thể giữ theo session.',
        responseBehavior: 'Hành vi trả lời',
        responseBehaviorDescription: 'Tùy chỉnh cách PigTex phản hồi',
        customInstruction: 'Custom instruction',
        customInstructionPlaceholder: 'Ví dụ: Trả lời ngắn gọn, ưu tiên bullet, luôn kèm ví dụ code khi cần.',
        customInstructionHint: (count: number) => `${count}/1200 ký tự — áp vào mỗi request`,
        memoryEnabled: (count: number) => `${count}/3 nguồn đang bật`,
        memoryOff: 'Đang tắt',
        globalMemory: 'Memory tổng',
        globalMemoryHint: 'Bật/tắt toàn bộ tầng memory trong Smart Chat.',
        knowledgeHint: 'Dùng tri thức đã lưu trong workspace.',
        factsHint: 'Truy xuất facts đã extract từ hội thoại.',
        historyHint: 'Dùng lịch sử hội thoại gần nhất.',
        responseParameters: 'Tham số phản hồi',
        responseParametersDescription: 'Temperature và max tokens',
        temperatureHint: 'Thấp → ổn định · Cao → sáng tạo',
        maxTokensHint: 'Đặt 0 để backend tự quyết định.',
        defaultAiFiles: 'AI Files mặc định',
        defaultAiFilesHint: 'Mặc định bật chế độ AI thao tác file khi mở chat.',
        autoApproveAiFiles: 'Auto duyệt AI Files',
        autoApproveAiFilesOn: 'AI sẽ tự chạy tác vụ đọc/sửa/tạo/xóa/đổi tên file mà không hỏi lại.',
        autoApproveAiFilesOff: 'Hiện hộp xác nhận trước khi AI thao tác file/folder.',
        qwenEnhancer: 'Qwen Image Prompt Enhancer',
        qwenEnhancerOn: 'Bật rewrite prompt production cho Alibaba/Qwen image (ưu tiên rõ chữ và layout).',
        qwenEnhancerOff: 'Tắt rewrite prompt tự động, gửi prompt gốc khi generate/edit ảnh Alibaba.',
        cancel: 'Hủy',
        saveChanges: 'Lưu thay đổi',
        saved: 'Đã lưu',
        baseUrlRequired: 'Base URL không được để trống',
        baseUrlInvalid: 'Base URL phải bắt đầu bằng http:// hoặc https://',
        modelRequired: 'Model không được để trống',
        settingsSaved: 'Đã lưu Settings',
        defaultsRestored: 'Đã khôi phục mặc định (chưa lưu)',
        enterApiKeyToTest: 'Nhập API key để kiểm tra kết nối',
        invalidBaseUrl: 'Base URL không hợp lệ',
        connectionSuccess: 'Kết nối thành công',
        connectionFailed: 'Kết nối thất bại',
        testConnectionFailed: 'Không thể kiểm tra kết nối',
        enterCurrentPassword: 'Nhập mật khẩu hiện tại',
        newPasswordMin: 'Mật khẩu mới phải có ít nhất 8 ký tự',
        confirmPasswordMismatch: 'Xác nhận mật khẩu không khớp',
        passwordUpdated: 'Đã cập nhật mật khẩu',
        passwordUpdateFailed: 'Không thể cập nhật mật khẩu',
        missingCurrentAccount: 'Không tìm thấy thông tin tài khoản hiện tại',
        emailConfirmMismatch: 'Nhập đúng email để xác nhận xóa tài khoản',
        enterPasswordToContinue: 'Nhập mật khẩu để tiếp tục',
        deleteAccountConfirm: (email: string) => `Xóa vĩnh viễn tài khoản ${email}? Toàn bộ workspace, chat và dữ liệu liên quan sẽ bị xóa.`,
        accountDeleted: 'Tài khoản đã được xóa',
        deleteAccountFailed: 'Không thể xóa tài khoản',
        modelLoadFailed: 'Không tải được danh sách Model',
        modelLoadBaseUrlHint: 'Không tải được Model. Kiểm tra Base URL.',
        cloudLoadFailed: 'Không thể tải dữ liệu cloud backup',
        openCheckoutFailed: 'Không thể mở checkout/subscription flow',
        deviceNotReadyForBackup: 'Thiết bị chưa sẵn sàng cho cloud backup',
        cloudBackupCreated: (stats: string) => stats ? `Đã tạo cloud backup. ${stats}` : 'Đã tạo cloud backup thành công',
        cloudBackupCreateFailed: 'Không thể tạo cloud backup',
        restoreCloudConfirm: 'Khôi phục snapshot này vào máy hiện tại? Dữ liệu local hiện tại có thể bị ghi đè.',
        cloudRestoreSuccess: (stats: string) => stats ? `Đã khôi phục dữ liệu cloud. ${stats}` : 'Đã khôi phục dữ liệu cloud vào máy hiện tại',
        reloadAfterRestore: 'Nên tải lại workspace hoặc khởi động lại app để giao diện phản ánh dữ liệu mới.',
        cloudRestoreFailed: 'Không thể khôi phục cloud backup',
        toggleApiKeyStorage: 'Bật/tắt lưu API key cục bộ',
        toggleQwenEnhancer: 'Bật/tắt Qwen Image Prompt Enhancer',
    } : {
        settings: 'Settings',
        unsavedChanges: 'Unsaved changes',
        close: 'Close',
        settingsSections: 'Settings sections',
        restoreDefaults: 'Restore defaults',
        noData: 'No data yet',
        pigtexUser: 'PigTex User',
        noEmail: 'No email',
        account: 'Account',
        accountDescription: 'Current plan, sign-in method, and activity timestamps',
        currentPlan: 'Current plan',
        syncSubscription: 'PigTex Sync',
        syncSubscriptionDescription: 'Subscription for cloud backup, device transfer, and personal sync',
        subscriptionStatus: 'Subscription status',
        billingCycle: 'Billing cycle',
        graceEnds: 'Grace ends',
        upgradeToSync: 'Upgrade to Sync',
        upgradeToSyncPlus: 'Upgrade to Sync Plus',
        manageSubscription: 'Manage plan',
        cancelSubscription: 'Cancel plan',
        cancelSubscriptionConfirm: 'Cancel PigTex Sync at the end of the current billing period?',
        subscriptionUpdated: 'PigTex Sync subscription updated',
        subscriptionCanceled: 'Subscription cancellation updated',
        subscriptionManageUnavailable: 'Billing portal is not available in the current configuration',
        checkoutOpenedPending: 'Checkout opened. PigTex Sync will activate after PayOS confirms the payment.',
        checkoutActivationPending: 'If the plan does not update immediately, return to the app and refresh after a few seconds.',
        signedInWith: 'Signed in with',
        createdAt: 'Created at',
        lastLogin: 'Last login',
        signOut: 'Sign out',
        signOutDescription: 'End the current PigTex session on this device',
        signOutHint: 'You can sign back in any time with email or OAuth.',
        signedOut: 'Signed out',
        resetLocalData: 'Wipe all local data',
        resetLocalDataDescription: 'Remove all PigTex data on this device and return the app to a clean first-run state',
        resetLocalDataWarning: 'This removes PigTex settings, sign-in session, cache, secure keys, and local data from this device.',
        resetLocalDataServerNote: 'It does not delete your PigTex account, subscription, or cloud/server data.',
        resetLocalDataConfirm: 'Delete all PigTex data on this device? The app will reload into a clean state.',
        resetLocalDataSuccess: 'All PigTex local data was removed. The app will reload.',
        resetLocalDataFailed: 'Failed to wipe PigTex local data',
        resettingLocalData: 'Clearing data...',
        currentDevice: 'Current device',
        loadingDeviceInfo: 'Loading device information',
        currentDeviceDescription: 'This is the PigTex session currently running on this machine',
        device: 'Device',
        platform: 'Platform',
        architecture: 'Architecture',
        appVersion: 'App version',
        language: 'Language',
        timeZone: 'Time zone',
        deviceHint: 'This is the device currently running the desktop application.',
        cloudBackup: 'Cloud Backup',
        cloudBackupDescription: 'Back up to the cloud so you can restore on another machine and prepare for future sync/share.',
        cloudLockedDescription: 'This feature is available with PigTex Sync only.',
        backupNow: 'Back up now',
        backingUp: 'Backing up...',
        syncNow: 'Sync now',
        syncing: 'Syncing...',
        syncState: 'Sync state',
        syncCompleted: 'Cloud sync completed',
        syncFailed: 'Failed to sync with the cloud',
        reload: 'Reload',
        cloudDeviceReady: 'This device is registered and ready for backup.',
        cloudRegisteringDevice: 'Registering this device with cloud backup.',
        cloudNotReady: 'Cloud backup is not ready yet.',
        status: 'Status',
        connected: 'Cloud backup connected',
        notConnected: 'Not connected',
        storage: 'Storage',
        snapshots: 'Snapshots',
        retention: 'Retention',
        days: 'days',
        cloudUsageHint: (percent: number) => `${percent}% of this account's cloud quota is currently used.`,
        loadingCloudBackups: 'Loading cloud backup list...',
        noCloudSnapshots: 'No snapshots yet. Create the first backup so you can move data to another machine.',
        restore: 'Restore',
        restoring: 'Restoring...',
        appLanguageTitle: 'App language',
        appLanguageDescription: 'Choose the interface language for PigTex desktop',
        appLanguageHint: 'The change will apply across the app after you save.',
        vietnamese: 'Tiếng Việt',
        english: 'English',
        changePassword: 'Change password',
        createPassword: 'Create password',
        changePasswordDescription: 'Update the password used for email sign-in',
        createPasswordDescription: 'Add a password so email sign-in can be used alongside OAuth',
        oauthPasswordHint: (provider: string) => `This account currently signs in with ${provider}. You can set a password for future email sign-in.`,
        currentPassword: 'Current password',
        newPassword: 'New password',
        confirmNewPassword: 'Confirm new password',
        minimumPasswordHint: 'Minimum 8 characters.',
        updating: 'Updating...',
        updatePassword: 'Update password',
        deleteAccount: 'Delete account',
        deleteAccountDescription: 'This action permanently removes all data linked to the account',
        deleteAccountWarning: 'All workspaces, chats, knowledge, and usage history will be permanently deleted.',
        enterEmailToConfirm: 'Enter email to confirm',
        password: 'Password',
        deleting: 'Deleting...',
        cannotUndo: 'This cannot be undone.',
        endpointMode: 'Endpoint mode',
        endpointModeDescription: 'Choose exactly one of PigTex’s 5 supported public providers',
        mode: 'Mode',
        getKey: 'Get key',
        hideSecret: 'Hide',
        showSecret: 'Show',
        defaultBaseUrl: 'Default',
        baseUrlLockedHint: 'The current provider uses a fixed base URL to avoid endpoint overlap.',
        baseUrlCustomHint: 'Custom mode lets you use any compatible host or proxy.',
        endpointProtocol: 'Endpoint protocol',
        endpointProtocolHint: 'Choose the correct protocol so requests and streaming are parsed correctly for your endpoint.',
        providerManagedConnectionHint: 'This provider does not accept credentials on this screen.',
        providerManagedBaseUrlHint: 'This Base URL is controlled by another configuration layer.',
        providerManagedTestHint: 'This provider does not support client-side connection testing.',
        providerManagedTexapiConnectionHint: 'TexAPI on this screen is managed by PigTex for your account.',
        providerManagedTexapiHint: 'TexAPI works like a normal provider here: you can enter your own API key and Base URL. If you leave the API key empty and keep the default Base URL, PigTex will automatically use the partner gateway and the TexAPI credits included for this account.',
        texapiByokHint: 'You are using your own TexAPI configuration. PigTex included credits are not used for this connection.',
        texapiApiKeyPlaceholder: 'Leave empty to use PigTex managed TexAPI, or enter your own TexAPI API key',
        texapiCreditsTitle: 'TexAPI credits',
        texapiCreditsDescription: 'Only applies when the API key is empty and the default PigTex TexAPI Base URL is used.',
        texapiRemaining: 'Remaining',
        texapiUsed: 'Used',
        texapiRequests: 'Requests',
        texapiPeriod: 'Period',
        texapiUsageLoading: 'Loading TexAPI usage...',
        texapiUsageLoadFailed: 'Unable to load TexAPI usage right now.',
        texapiUsageEmpty: 'No TexAPI usage details are available for this account yet.',
        texapiSignInHint: 'Sign in to your PigTex account to use the included TexAPI credits.',
        model: 'Model',
        loadingModels: 'Loading model list...',
        availableModels: (count: number) => `${count} model(s) available`,
        testingConnection: 'Testing...',
        testConnection: 'Test connection',
        security: 'Security',
        securityDescription: 'How API keys are stored',
        storeApiKey: 'Store API key securely on this machine',
        storeApiKeyOn: 'The API key is stored in this machine’s operating-system secure storage.',
        storeApiKeyOff: 'The API key only exists for the current session and is removed when the app closes.',
        storeApiKeyUnavailable: 'This device does not support OS secure storage, so API keys can only be kept for the current session.',
        responseBehavior: 'Response behavior',
        responseBehaviorDescription: 'Customize how PigTex responds',
        customInstruction: 'Custom instruction',
        customInstructionPlaceholder: 'Example: Keep answers concise, prefer bullets, and include code examples when useful.',
        customInstructionHint: (count: number) => `${count}/1200 characters — applied to every request`,
        memoryEnabled: (count: number) => `${count}/3 sources enabled`,
        memoryOff: 'Off',
        globalMemory: 'Global memory',
        globalMemoryHint: 'Enable or disable the full memory layer in Smart Chat.',
        knowledgeHint: 'Use saved knowledge from the workspace.',
        factsHint: 'Retrieve facts extracted from conversations.',
        historyHint: 'Use recent conversation history.',
        responseParameters: 'Response parameters',
        responseParametersDescription: 'Temperature and max tokens',
        temperatureHint: 'Lower → stable · Higher → creative',
        maxTokensHint: 'Set 0 to let the backend decide.',
        defaultAiFiles: 'Default AI Files',
        defaultAiFilesHint: 'Enable AI file actions by default when a chat opens.',
        autoApproveAiFiles: 'Auto-approve AI Files',
        autoApproveAiFilesOn: 'AI will run read/edit/create/delete/rename file actions without asking again.',
        autoApproveAiFilesOff: 'Show a confirmation dialog before AI touches files or folders.',
        qwenEnhancer: 'Qwen Image Prompt Enhancer',
        qwenEnhancerOn: 'Enable production-style prompt rewriting for Alibaba/Qwen image models with a focus on text clarity and layout.',
        qwenEnhancerOff: 'Disable automatic prompt rewriting and send the original prompt for Alibaba image generate/edit.',
        cancel: 'Cancel',
        saveChanges: 'Save changes',
        saved: 'Saved',
        baseUrlRequired: 'Base URL cannot be empty',
        baseUrlInvalid: 'Base URL must start with http:// or https://',
        modelRequired: 'Model cannot be empty',
        settingsSaved: 'Settings saved',
        defaultsRestored: 'Defaults restored (not saved yet)',
        enterApiKeyToTest: 'Enter an API key to test the connection',
        invalidBaseUrl: 'Invalid Base URL',
        connectionSuccess: 'Connection successful',
        connectionFailed: 'Connection failed',
        testConnectionFailed: 'Failed to test the connection',
        enterCurrentPassword: 'Enter the current password',
        newPasswordMin: 'The new password must be at least 8 characters',
        confirmPasswordMismatch: 'Password confirmation does not match',
        passwordUpdated: 'Password updated',
        passwordUpdateFailed: 'Failed to update password',
        missingCurrentAccount: 'Could not find the current account information',
        emailConfirmMismatch: 'Enter the correct email to confirm account deletion',
        enterPasswordToContinue: 'Enter your password to continue',
        deleteAccountConfirm: (email: string) => `Delete account ${email} permanently? All related workspaces, chats, and data will be removed.`,
        accountDeleted: 'Account deleted',
        deleteAccountFailed: 'Failed to delete account',
        modelLoadFailed: 'Failed to load model list',
        modelLoadBaseUrlHint: 'Failed to load models. Check the Base URL.',
        cloudLoadFailed: 'Failed to load cloud backup data',
        openCheckoutFailed: 'Failed to open the checkout or subscription flow',
        deviceNotReadyForBackup: 'This device is not ready for cloud backup',
        cloudBackupCreated: (stats: string) => stats ? `Cloud backup created. ${stats}` : 'Cloud backup created successfully',
        cloudBackupCreateFailed: 'Failed to create cloud backup',
        restoreCloudConfirm: 'Restore this snapshot to the current machine? Existing local data may be overwritten.',
        cloudRestoreSuccess: (stats: string) => stats ? `Cloud data restored. ${stats}` : 'Cloud data restored to this machine',
        reloadAfterRestore: 'Reload the workspace or restart the app so the UI reflects the restored data.',
        cloudRestoreFailed: 'Failed to restore cloud backup',
        toggleApiKeyStorage: 'Toggle local API key storage',
        toggleQwenEnhancer: 'Toggle Qwen Image Prompt Enhancer',
    }
    const updateCopy = previewIsVietnamese ? {
        title: 'Cập nhật desktop',
        description: 'PigTex kiểm tra bản mới và mở website cập nhật để bạn tải installer mới nhất.',
        availableTitle: (version: string) => `Có PigTex ${version} mới`,
        availableDescription: 'Bấm Cập nhật ngay để mở website, tải installer và chọn cách cài đặt.',
        upToDateTitle: 'Bạn đang dùng bản mới nhất',
        upToDateDescription: 'Khi có bản mới, PigTex sẽ hiện badge Update ở footer sidebar.',
        failedTitle: 'Không kiểm tra được bản cập nhật',
        failedDescription: 'Kiểm tra lại mạng hoặc endpoint manifest update.',
        status: 'Trạng thái',
        latestVersion: 'Bản mới nhất',
        lastChecked: 'Kiểm tra lần cuối',
        checkNow: 'Kiểm tra ngay',
        checking: 'Đang kiểm tra...',
        installNow: 'Cập nhật ngay',
        installing: 'Đang mở website...',
        openWebsite: 'Mở website cập nhật',
        updateHint: 'Trong setup sẽ có lựa chọn: gỡ bản cũ rồi cài mới, hoặc giữ bản cũ và cài song song.'
    } : {
        title: 'Desktop updates',
        description: 'PigTex checks for new releases and opens the update website so you can download the latest installer.',
        availableTitle: (version: string) => `PigTex ${version} is available`,
        availableDescription: 'Click Install update to open the website, download the installer, and choose install mode.',
        upToDateTitle: 'You are already on the latest version',
        upToDateDescription: 'When a newer build is published, PigTex will show an Update badge in the sidebar footer.',
        failedTitle: 'Unable to check for updates',
        failedDescription: 'Check your network connection or the update manifest endpoint.',
        status: 'Status',
        latestVersion: 'Latest version',
        lastChecked: 'Last checked',
        checkNow: 'Check now',
        checking: 'Checking...',
        installNow: 'Install update',
        installing: 'Opening website...',
        openWebsite: 'Open update website',
        updateHint: 'Setup now offers a choice: uninstall old version first, or keep old version and install side-by-side.'
    }
    const [activeTab, setActiveTab] = useState<SettingsTabId>('connection')
    const [showApiKey, setShowApiKey] = useState(false)
    const [isValidating, setIsValidating] = useState(false)
    const [validationFeedback, setValidationFeedback] = useState<ValidationFeedback>(null)
    const [isLoadingModels, setIsLoadingModels] = useState(false)
    const [modelsError, setModelsError] = useState<string | null>(null)
    const [modelOptions, setModelOptions] = useState<string[]>([])
    const [modelReloadTick, setModelReloadTick] = useState(0)
    const [texApiUsage, setTexApiUsage] = useState<TexApiPartnerUsageSummary | null>(null)
    const [texApiUsageError, setTexApiUsageError] = useState<string | null>(null)
    const [isLoadingTexApiUsage, setIsLoadingTexApiUsage] = useState(false)
    const [texApiUsageReloadTick, setTexApiUsageReloadTick] = useState(0)
    const [deviceInfo, setDeviceInfo] = useState<DeviceSnapshot>(() => buildFallbackDeviceSnapshot(initialIsVietnamese))
    const [isLoadingDeviceInfo, setIsLoadingDeviceInfo] = useState(false)
    const [hasPasswordOverride, setHasPasswordOverride] = useState<boolean | null>(null)
    const [providerCatalog, setProviderCatalog] = useState<ApiProviderCatalogEntry[]>(() => getApiProviderCatalog())
    const [passwordForm, setPasswordForm] = useState({
        currentPassword: '',
        newPassword: '',
        confirmPassword: ''
    })
    const [deleteForm, setDeleteForm] = useState({
        confirmation: '',
        password: ''
    })
    const [isChangingPassword, setIsChangingPassword] = useState(false)
    const [isDeletingAccount, setIsDeletingAccount] = useState(false)
    const [isResettingLocalData, setIsResettingLocalData] = useState(false)
    const [cloudDeviceId, setCloudDeviceId] = useState<string | null>(null)
    const [cloudQuota, setCloudQuota] = useState<CloudQuota | null>(null)
    const [cloudUsage, setCloudUsage] = useState<CloudUsageSummary | null>(null)
    const [cloudBackups, setCloudBackups] = useState<CloudBackupListItem[]>([])
    const [syncEntitlement, setSyncEntitlement] = useState<SyncEntitlement | null>(null)
    const [syncState, setSyncState] = useState<CloudSyncState | null>(null)
    const [cloudError, setCloudError] = useState<string | null>(null)
    const [isLoadingCloudData, setIsLoadingCloudData] = useState(false)
    const [isLoadingSyncEntitlement, setIsLoadingSyncEntitlement] = useState(false)
    const [isCreatingCloudBackup, setIsCreatingCloudBackup] = useState(false)
    const [isManagingSubscription, setIsManagingSubscription] = useState(false)
    const [isSyncingCloud, setIsSyncingCloud] = useState(false)
    const [restoreSnapshotId, setRestoreSnapshotId] = useState<string | null>(null)

    useEffect(() => {
        if (!isOpen) return
        setDraft(withCurrentCredentialSynced(settings))
        setActiveTab('connection')
        setShowApiKey(false)
        setValidationFeedback(null)
        setHasPasswordOverride(null)
        setTexApiUsage(null)
        setTexApiUsageError(null)
        setIsLoadingTexApiUsage(false)
        setTexApiUsageReloadTick(0)
        setPasswordForm({
            currentPassword: '',
            newPassword: '',
            confirmPassword: ''
        })
        setDeleteForm({
            confirmation: '',
            password: ''
        })
        setCloudDeviceId(null)
        setCloudQuota(null)
        setCloudUsage(null)
        setCloudBackups([])
        setSyncEntitlement(null)
        setSyncState(null)
        setCloudError(null)
        setIsLoadingCloudData(false)
        setIsLoadingSyncEntitlement(false)
        setIsCreatingCloudBackup(false)
        setIsManagingSubscription(false)
        setIsSyncingCloud(false)
        setRestoreSnapshotId(null)
    }, [isOpen, settings])

    useEffect(() => {
        if (!isOpen) return

        let disposed = false
        fetchProviderCatalog()
            .then((catalog) => {
                if (disposed) return
                if (catalog.length > 0) {
                    setProviderCatalog(catalog)
                }
            })
            .catch(() => {
                if (disposed) return
                setProviderCatalog(getApiProviderCatalog())
            })

        return () => {
            disposed = true
        }
    }, [isOpen])

    const selectedPublicProviderId = mapSettingsSelectionToCatalogProviderId(draft.apiProvider, draft.customEndpoint)
    const effectiveProvider = resolveApiProviderForRequest(draft.apiProvider, draft.customEndpoint)
    const currentProviderConfig = providerCatalog.find((provider) => provider.id === selectedPublicProviderId)
        || getApiProviderCatalogEntryForSelection(draft.apiProvider, draft.customEndpoint)
    const providerManagedByServer = Boolean(currentProviderConfig.managed_by_server)
    const providerAcceptsClientCredentials = Boolean(currentProviderConfig.supports_byok)
    const isTexApiManagedProvider = selectedPublicProviderId === 'texapi'
    const texApiDefaultBaseUrl = normalizeBaseUrlInput(
        currentProviderConfig.default_base_url || getPublicProviderDefaultBaseUrl('texapi')
    )
    const isTexApiManagedFallback = Boolean(
        isTexApiManagedProvider
        && !draft.apiKey.trim()
        && normalizeBaseUrlInput(draft.baseUrl || currentProviderConfig.default_base_url || '') === texApiDefaultBaseUrl
    )
    const providerManagedConnectionHint = isTexApiManagedProvider
        ? copy.providerManagedTexapiConnectionHint
        : copy.providerManagedConnectionHint
    const isBaseUrlLocked = draft.apiProvider !== 'auto' || providerManagedByServer
    const modelPlaceholder = MODEL_PLACEHOLDER_BY_PROVIDER[effectiveProvider]
    const hasPassword = hasPasswordOverride ?? (user?.has_password ?? true)
    const accountProviderLabel = formatProviderLabel(user?.oauth_provider, previewIsVietnamese)
    const accountPlanLabel = formatPlanLabel(user?.plan, previewIsVietnamese)
    const hasTexApiUsageData = Boolean(
        texApiUsage
        && (
            texApiUsage.remaining_credits_usd !== null
            || texApiUsage.total_credits_usd !== null
            || texApiUsage.used_credits_usd !== null
            || texApiUsage.total_requests !== null
            || texApiUsage.period_start
            || texApiUsage.period_end
        )
    )
    const secureStorageAvailability = useMemo<'available' | 'unavailable' | 'unknown'>(() => {
        if (typeof window === 'undefined' || !window.electronAPI?.isSecureStorageAvailable) {
            return 'unknown'
        }
        try {
            return window.electronAPI.isSecureStorageAvailable() ? 'available' : 'unavailable'
        } catch {
            return 'unknown'
        }
    }, [])
    const secureStorageUnavailable = secureStorageAvailability === 'unavailable'

    useEffect(() => {
        if (!isOpen) return

        const fallback = buildFallbackDeviceSnapshot(previewIsVietnamese)
        let disposed = false
        setDeviceInfo(fallback)

        if (!window.electronAPI?.getSystemInfo) {
            setIsLoadingDeviceInfo(false)
            return
        }

        setIsLoadingDeviceInfo(true)
        window.electronAPI.getSystemInfo()
            .then((systemInfo) => {
                if (disposed) return
                setDeviceInfo({
                    ...fallback,
                    ...systemInfo,
                    language: fallback.language,
                    timeZone: fallback.timeZone
                })
            })
            .catch(() => {
                if (disposed) return
                setDeviceInfo(fallback)
            })
            .finally(() => {
                if (disposed) return
                setIsLoadingDeviceInfo(false)
            })

        return () => {
            disposed = true
        }
    }, [isOpen, previewIsVietnamese])

    const handleProviderChange = (newProviderId: PublicApiProviderId) => {
        const providerCfg = providerCatalog.find((provider) => provider.id === newProviderId)
            || getApiProviderCatalog().find((provider) => provider.id === newProviderId)
        if (!providerCfg) return
        setDraft(prev => {
            const profiles = ensureCredentialProfiles(prev.providerCredentialProfiles)
            const currentProvider = prev.apiProvider
            profiles[currentProvider] = {
                apiKey: prev.apiKey,
                baseUrl: normalizeProfileBaseUrl(currentProvider, prev.baseUrl),
            }
            const nextSelection = mapCatalogProviderIdToSettingsSelection(newProviderId)
            const nextProvider = nextSelection.apiProvider
            const nextCustomEndpoint = nextSelection.customEndpoint
            const nextProfile = profiles[nextProvider]
            const acceptsClientCredentials = Boolean(providerCfg.supports_byok)
            const nextApiKey = acceptsClientCredentials ? (nextProfile?.apiKey || '') : ''
            const nextBaseUrl = nextProvider === 'auto'
                ? normalizeProfileBaseUrl(
                    nextProvider,
                    nextProfile?.baseUrl || providerCfg.default_base_url || ''
                )
                : normalizeProfileBaseUrl(
                    nextProvider,
                    providerCfg.default_base_url || nextProfile?.baseUrl || ''
                )

            profiles[nextProvider] = {
                apiKey: nextApiKey,
                baseUrl: nextBaseUrl,
            }

            return {
                ...prev,
                apiProvider: nextProvider,
                customEndpoint: nextCustomEndpoint,
                apiKey: nextApiKey,
                baseUrl: nextBaseUrl,
                providerCredentialProfiles: profiles,
                // Clear model when switching mode because model catalogs can differ.
                model: '',
            }
        })
        setModelOptions([])
        setModelsError(null)
        setValidationFeedback(null)
    }

    useEffect(() => {
        if (!isOpen) return

        let disposed = false
        const timeoutId = window.setTimeout(() => {
            setIsLoadingModels(true)
            setModelsError(null)

            const modelRequest = providerAcceptsClientCredentials && draft.apiKey.trim()
                ? (() => {
                    const apiKey = draft.apiKey.trim()
                    const baseUrl = normalizeBaseUrlInput(draft.baseUrl)
                    if (!apiKey) {
                        setModelOptions([])
                        setModelsError(null)
                        setIsLoadingModels(false)
                        return Promise.resolve<AIModel[]>([])
                    }
                    if (!baseUrl || !isValidHttpUrl(baseUrl)) {
                        setModelOptions([])
                        setModelsError(null)
                        setIsLoadingModels(false)
                        return Promise.resolve<AIModel[]>([])
                    }
                    return getModelsWithCredentials(apiKey, baseUrl, effectiveProvider, { includeAllReturnedModels: true })
                })()
                : isTexApiManagedFallback
                    ? getModels(modelReloadTick > 0)
                    : Promise.resolve<AIModel[]>([])

            modelRequest
                .then((models: AIModel[]) => {
                    if (disposed) return
                    const ids = Array.from(new Set(models.map((model) => model.id).filter(Boolean)))
                    setModelOptions(ids)
                })
                .catch((error) => {
                    if (disposed) return
                    setModelOptions([])
                    const message = error instanceof Error ? error.message : copy.modelLoadFailed
                    const normalized = message.toLowerCase()
                    if (
                        isTexApiManagedFallback
                        && (
                            normalized.includes('401')
                            || normalized.includes('403')
                            || normalized.includes('credentials')
                            || normalized.includes('unauthorized')
                            || normalized.includes('forbidden')
                        )
                    ) {
                        setModelsError(copy.texapiSignInHint)
                        return
                    }
                    if (normalized.includes('404')) {
                        setModelsError(copy.modelLoadBaseUrlHint)
                        return
                    }
                    setModelsError(message)
                })
                .finally(() => {
                    if (disposed) return
                    setIsLoadingModels(false)
                })
        }, 320)

        return () => {
            disposed = true
            window.clearTimeout(timeoutId)
        }
    }, [
        copy.modelLoadBaseUrlHint,
        copy.modelLoadFailed,
        providerAcceptsClientCredentials,
        draft.apiKey,
        draft.baseUrl,
        draft.apiProvider,
        draft.customEndpoint,
        effectiveProvider,
        isTexApiManagedFallback,
        isOpen,
        modelReloadTick
    ])

    useEffect(() => {
        if (!isOpen || activeTab !== 'connection') return
        if (!isTexApiManagedFallback) {
            setTexApiUsage(null)
            setTexApiUsageError(null)
            setIsLoadingTexApiUsage(false)
            return
        }
        if (!user) {
            setTexApiUsage(null)
            setTexApiUsageError(copy.texapiSignInHint)
            setIsLoadingTexApiUsage(false)
            return
        }

        let disposed = false
        setIsLoadingTexApiUsage(true)
        setTexApiUsageError(null)

        getTexApiPartnerUsage()
            .then((usage) => {
                if (disposed) return
                setTexApiUsage(usage)
            })
            .catch((error) => {
                if (disposed) return
                setTexApiUsage(null)
                setTexApiUsageError(error instanceof Error ? error.message : copy.texapiUsageLoadFailed)
            })
            .finally(() => {
                if (disposed) return
                setIsLoadingTexApiUsage(false)
            })

        return () => {
            disposed = true
        }
    }, [
        activeTab,
        copy.texapiSignInHint,
        copy.texapiUsageLoadFailed,
        isOpen,
        isTexApiManagedFallback,
        texApiUsageReloadTick,
        user?.id,
    ])

    useEffect(() => {
        if (!isOpen) return
        if (modelOptions.length === 0) return
        const current = draft.model.trim()
        if (!current || !modelOptions.includes(current)) {
            setDraft(prev => ({ ...prev, model: modelOptions[0] }))
        }
    }, [isOpen, modelOptions, draft.model])

    useEffect(() => {
        if (!isOpen) return
        const handleEscape = (event: KeyboardEvent) => {
            if (event.key === 'Escape') {
                event.preventDefault()
                onClose()
            }
        }

        window.addEventListener('keydown', handleEscape, true)
        return () => window.removeEventListener('keydown', handleEscape, true)
    }, [isOpen, onClose])

    const normalizedDraft = useMemo(() => {
        const sanitized = sanitizeForSave(draft)
        if (!secureStorageUnavailable) return sanitized
        return {
            ...sanitized,
            saveApiKeyLocally: false
        }
    }, [draft, secureStorageUnavailable])

    const canSave = useMemo(() => {
        const hasApiKey = Boolean(normalizedDraft.apiKey.trim())
        const requiresBaseUrl = hasApiKey
        const baseUrlIsValidOrEmpty = !normalizedDraft.baseUrl || isValidHttpUrl(normalizedDraft.baseUrl)
        return Boolean(
            normalizedDraft.model &&
            (requiresBaseUrl ? normalizedDraft.baseUrl && isValidHttpUrl(normalizedDraft.baseUrl) : baseUrlIsValidOrEmpty) &&
            normalizedDraft.temperature >= 0 &&
            normalizedDraft.temperature <= 2 &&
            normalizedDraft.maxTokens >= 0
        )
    }, [currentProviderConfig.supports_byok, normalizedDraft])

    const hasUnsavedChanges = useMemo(() => {
        return serializeForCompare(draft) !== serializeForCompare(settings)
    }, [draft, settings])

    const clearBrowserLocalData = () => {
        if (typeof window === 'undefined') return

        try {
            window.sessionStorage.clear()
        } catch {
            // Ignore browser storage cleanup failures; Electron still clears desktop storage below.
        }

        try {
            window.localStorage.clear()
        } catch {
            // Ignore browser storage cleanup failures; Electron still clears desktop storage below.
        }
    }

    const handleSave = () => {
        const hasApiKey = Boolean(normalizedDraft.apiKey.trim())
        const requiresBaseUrl = hasApiKey
        if (requiresBaseUrl) {
            if (!normalizedDraft.baseUrl) {
                showInfo(copy.baseUrlRequired)
                return
            }
            if (!isValidHttpUrl(normalizedDraft.baseUrl)) {
                showInfo(copy.baseUrlInvalid)
                return
            }
        } else if (normalizedDraft.baseUrl && !isValidHttpUrl(normalizedDraft.baseUrl)) {
            showInfo(copy.baseUrlInvalid)
            return
        }
        if (!normalizedDraft.model) {
            showInfo(copy.modelRequired)
            return
        }

        onSave(normalizedDraft)
        showSuccess(copy.settingsSaved)
        onClose()
    }

    const handleResetDefaults = () => {
        setDraft(prev => {
            const profiles = ensureCredentialProfiles(prev.providerCredentialProfiles)
            return {
                ...DEFAULT_PIGTEX_SETTINGS,
                saveApiKeyLocally: secureStorageUnavailable ? false : prev.saveApiKeyLocally,
                providerCredentialProfiles: profiles,
                apiKey: profiles.auto.apiKey,
                baseUrl: getPublicProviderDefaultBaseUrl('texapi') || profiles.auto.baseUrl,
            }
        })
        setValidationFeedback(null)
        setModelsError(null)
        setModelOptions([])
        showInfo(copy.defaultsRestored)
    }

    const handleTestConnection = async () => {
        if (!providerAcceptsClientCredentials && !isTexApiManagedFallback) {
            showInfo(copy.providerManagedTestHint)
            return
        }
        const apiKey = draft.apiKey.trim()
        const baseUrl = normalizeBaseUrlInput(draft.baseUrl)
        if (!apiKey && !isTexApiManagedFallback) {
            showInfo(copy.enterApiKeyToTest)
            return
        }
        if (!isValidHttpUrl(baseUrl)) {
            showInfo(copy.invalidBaseUrl)
            return
        }

        setIsValidating(true)
        setValidationFeedback(null)
        try {
            const result = await validateApiConnection(apiKey, baseUrl, effectiveProvider)
            if (result.valid) {
                const successMessage = result.message || copy.connectionSuccess
                setValidationFeedback({
                    kind: 'success',
                    message: successMessage
                })
                setModelsError(null)
                showSuccess(successMessage)
            } else {
                const errorMessage = result.message || copy.connectionFailed
                setValidationFeedback({
                    kind: 'error',
                    message: errorMessage
                })
                showError(errorMessage)
            }
        } catch (error) {
            const message = error instanceof Error ? error.message : copy.testConnectionFailed
            setValidationFeedback({
                kind: 'error',
                message
            })
            showError(message)
        } finally {
            setIsValidating(false)
        }
    }

    const handleChangePassword = async () => {
        const nextPassword = passwordForm.newPassword.trim()

        if (hasPassword && !passwordForm.currentPassword) {
            showInfo(copy.enterCurrentPassword)
            return
        }
        if (nextPassword.length < 8) {
            showInfo(copy.newPasswordMin)
            return
        }
        if (passwordForm.newPassword !== passwordForm.confirmPassword) {
            showInfo(copy.confirmPasswordMismatch)
            return
        }

        setIsChangingPassword(true)
        try {
            const result = await changePassword({
                currentPassword: passwordForm.currentPassword,
                newPassword: passwordForm.newPassword
            })
            setHasPasswordOverride(result.has_password)
            setPasswordForm({
                currentPassword: '',
                newPassword: '',
                confirmPassword: ''
            })
            showSuccess(result.message || copy.passwordUpdated)
        } catch (error) {
            const message = error instanceof Error ? error.message : copy.passwordUpdateFailed
            showError(message)
        } finally {
            setIsChangingPassword(false)
        }
    }

    const handleSignOut = () => {
        onClose()
        logout()
        showSuccess(copy.signedOut)
    }

    const handleResetLocalData = async () => {
        const confirmed = window.confirm(copy.resetLocalDataConfirm)
        if (!confirmed) return

        setIsResettingLocalData(true)
        try {
            await window.electronAPI?.resetLocalData?.()
            clearBrowserLocalData()
            onClose()
            showSuccess(copy.resetLocalDataSuccess)
            window.setTimeout(() => {
                window.location.reload()
            }, 150)
        } catch (error) {
            const message = error instanceof Error ? error.message : copy.resetLocalDataFailed
            showError(message)
        } finally {
            setIsResettingLocalData(false)
        }
    }

    const handleDeleteAccount = async () => {
        const expectedEmail = (user?.email || '').trim().toLowerCase()
        const confirmation = deleteForm.confirmation.trim().toLowerCase()

        if (!expectedEmail) {
            showError(copy.missingCurrentAccount)
            return
        }
        if (confirmation !== expectedEmail) {
            showInfo(copy.emailConfirmMismatch)
            return
        }
        if (hasPassword && !deleteForm.password.trim()) {
            showInfo(copy.enterPasswordToContinue)
            return
        }

        const confirmed = window.confirm(
            copy.deleteAccountConfirm(user?.email || '')
        )
        if (!confirmed) return

        setIsDeletingAccount(true)
        try {
            await deleteAccount({
                confirmation: deleteForm.confirmation,
                password: deleteForm.password
            })
            showSuccess(copy.accountDeleted)
            onClose()
            logout()
        } catch (error) {
            const message = error instanceof Error ? error.message : copy.deleteAccountFailed
            showError(message)
        } finally {
            setIsDeletingAccount(false)
        }
    }

    const refreshSyncEntitlement = async (): Promise<SyncEntitlement | null> => {
        if (!user) return null

        setIsLoadingSyncEntitlement(true)
        try {
            const entitlement = await getSyncEntitlement()
            setSyncEntitlement(entitlement)
            return entitlement
        } catch (error) {
            const message = error instanceof Error ? error.message : copy.cloudLoadFailed
            setCloudError(message)
            return null
        } finally {
            setIsLoadingSyncEntitlement(false)
        }
    }

    const loadCloudBackupData = async (options?: { showErrorToast?: boolean }) => {
        if (!user) return

        setIsLoadingCloudData(true)
        setCloudError(null)

        try {
            const entitlement = await refreshSyncEntitlement()
            if (!entitlement) return

            if (!entitlement.can_use_cloud_backup) {
                setCloudDeviceId(null)
                setCloudQuota(null)
                setCloudUsage(null)
                setCloudBackups([])
                setSyncState(null)
                return
            }

            const registration = await registerCloudDevice({
                deviceKey: getOrCreateCloudDeviceKey(),
                deviceName: deviceInfo.hostname || copy.currentDevice,
                platform: deviceInfo.platform || 'unknown',
                appVersion: deviceInfo.appVersion || 'desktop'
            })

            setCloudDeviceId(registration.device_id)
            setCloudQuota(registration.quota)

            const [usageResponse, backupsResponse, nextSyncState] = await Promise.all([
                getCloudUsage(),
                listCloudBackups(20),
                entitlement.can_use_sync
                    ? getCloudSyncState(registration.device_id).catch(() => null)
                    : Promise.resolve(null)
            ])

            setCloudUsage(usageResponse)
            setCloudBackups(backupsResponse.items)
            setSyncState(nextSyncState)
        } catch (error) {
            const message = error instanceof Error ? error.message : copy.cloudLoadFailed
            setCloudError(message)
            if (options?.showErrorToast) {
                showError(message)
            }
        } finally {
            setIsLoadingCloudData(false)
        }
    }

    const handleUpgradeSync = async (planCode: 'sync' | 'sync_plus', billingCycle: 'monthly' | 'annual') => {
        setIsManagingSubscription(true)
        setCloudError(null)
        try {
            const session = await createSyncCheckoutSession({
                planCode,
                billingCycle,
            })
            const targetUrl = (session.checkout_url || '').trim()
            if (!targetUrl) {
                throw new Error(copy.openCheckoutFailed)
            }
            if (targetUrl) {
                if (window.electronAPI?.openExternal) {
                    await window.electronAPI.openExternal(targetUrl)
                } else {
                    window.open(targetUrl, '_blank', 'noopener,noreferrer')
                }
            }
            if (session.mode === 'mock_activated') {
                try {
                    await refreshUser()
                } catch {
                    // Checkout flow should not fail just because a profile refresh is temporarily unavailable.
                }
                await loadCloudBackupData()
                showSuccess(copy.subscriptionUpdated)
                return
            }
            showInfo(`${copy.checkoutOpenedPending} ${copy.checkoutActivationPending}`)
        } catch (error) {
            const message = error instanceof Error ? error.message : copy.openCheckoutFailed
            setCloudError(message)
            showError(message)
        } finally {
            setIsManagingSubscription(false)
        }
    }

    const handleManageSubscription = async () => {
        setIsManagingSubscription(true)
        setCloudError(null)
        try {
            const session = await createSyncPortalSession()
            const targetUrl = (session.session_url || '').trim()
            if (session.mode !== 'redirect' || !targetUrl) {
                showInfo(copy.subscriptionManageUnavailable)
                return
            }
            if (window.electronAPI?.openExternal) {
                await window.electronAPI.openExternal(targetUrl)
            } else {
                window.open(targetUrl, '_blank', 'noopener,noreferrer')
            }
        } catch (error) {
            const message = error instanceof Error ? error.message : copy.subscriptionManageUnavailable
            setCloudError(message)
            showError(message)
        } finally {
            setIsManagingSubscription(false)
        }
    }

    const handleCancelSubscription = async () => {
        const confirmed = window.confirm(copy.cancelSubscriptionConfirm)
        if (!confirmed) return

        setIsManagingSubscription(true)
        setCloudError(null)
        try {
            await cancelSyncSubscription()
            try {
                await refreshUser()
            } catch {
                // Cancellation state can still be reconciled via entitlement reload below.
            }
            await loadCloudBackupData()
            showSuccess(copy.subscriptionCanceled)
        } catch (error) {
            const message = error instanceof Error ? error.message : copy.subscriptionManageUnavailable
            setCloudError(message)
            showError(message)
        } finally {
            setIsManagingSubscription(false)
        }
    }

    const handleCreateCloudBackup = async () => {
        if (!cloudDeviceId) {
            showInfo(copy.deviceNotReadyForBackup)
            return
        }

        setIsCreatingCloudBackup(true)
        setCloudError(null)
        try {
            const result = await createLocalCloudBackup({
                deviceId: cloudDeviceId,
                scopeType: 'account',
                snapshotKind: 'full'
            })
            await loadCloudBackupData()
            const statsSummary = formatCloudStatsSummary(result.counts)
            showSuccess(copy.cloudBackupCreated(statsSummary))
        } catch (error) {
            const message = error instanceof Error ? error.message : copy.cloudBackupCreateFailed
            setCloudError(message)
            showError(message)
        } finally {
            setIsCreatingCloudBackup(false)
        }
    }

    const handleRestoreCloudBackup = async (snapshotId: string) => {
        const confirmed = window.confirm(
            copy.restoreCloudConfirm
        )
        if (!confirmed) return

        setRestoreSnapshotId(snapshotId)
        setCloudError(null)
        try {
            const result = await applyLocalCloudRestore({
                snapshotId,
                merge: false
            })
            const statsSummary = formatCloudStatsSummary(result.stats)
            window.dispatchEvent(new CustomEvent('pigtex:cloud-restore-applied'))
            showSuccess(copy.cloudRestoreSuccess(statsSummary))
            showInfo(copy.reloadAfterRestore)
            await loadCloudBackupData()
        } catch (error) {
            const message = error instanceof Error ? error.message : copy.cloudRestoreFailed
            setCloudError(message)
            showError(message)
        } finally {
            setRestoreSnapshotId(null)
        }
    }

    const handleSyncNow = async () => {
        if (!cloudDeviceId) {
            showInfo(copy.deviceNotReadyForBackup)
            return
        }

        setIsSyncingCloud(true)
        setCloudError(null)
        try {
            let state = await getCloudSyncState(cloudDeviceId)
            setSyncState(state)

            if (state.can_pull && (state.status === 'pull_needed' || state.status === 'bidirectional')) {
                await pullCloudSync({ deviceId: cloudDeviceId })
                window.dispatchEvent(new CustomEvent('pigtex:cloud-restore-applied'))
            }

            state = await getCloudSyncState(cloudDeviceId)
            setSyncState(state)

            if (state.can_push && (state.status === 'push_needed' || state.status === 'bidirectional')) {
                await pushCloudSync({ deviceId: cloudDeviceId })
            }

            const nextState = await getCloudSyncState(cloudDeviceId)
            setSyncState(nextState)
            await loadCloudBackupData()
            showSuccess(copy.syncCompleted)
        } catch (error) {
            const message = error instanceof Error ? error.message : copy.syncFailed
            setCloudError(message)
            showError(message)
        } finally {
            setIsSyncingCloud(false)
        }
    }

    const renderToggle = (
        title: string,
        description: string,
        checked: boolean,
        onToggle: () => void,
        options?: { disabled?: boolean; ariaLabel?: string }
    ) => {
        const disabled = options?.disabled ?? false
        return (
            <div className={`stg-toggle ${disabled ? 'stg-toggle--disabled' : ''}`}>
                <div className="stg-toggle-info">
                    <span className="stg-toggle-label">{title}</span>
                    <span className="stg-toggle-hint">{description}</span>
                </div>
                <button
                    type="button"
                    className={`stg-switch ${checked ? 'stg-switch--on' : ''}`}
                    onClick={onToggle}
                    aria-label={options?.ariaLabel || (previewIsVietnamese ? `Bật/tắt ${title}` : `Toggle ${title}`)}
                    disabled={disabled}
                >
                    <span />
                </button>
            </div>
        )
    }

    const activeMemoryTools = [
        draft.useKnowledge ? 'Knowledge' : null,
        draft.useFacts ? 'Facts' : null,
        draft.useHistory ? 'History' : null
    ].filter(Boolean)
    const canSubmitPasswordChange = Boolean(
        passwordForm.newPassword.trim().length >= 8
        && passwordForm.newPassword === passwordForm.confirmPassword
        && (!hasPassword || passwordForm.currentPassword)
    )
    const canDeleteAccount = Boolean(
        user?.email
        && deleteForm.confirmation.trim().toLowerCase() === user.email.trim().toLowerCase()
        && (!hasPassword || deleteForm.password.trim())
    )
    const canUseCloudBackup = Boolean(syncEntitlement?.can_use_cloud_backup)
    const canWriteCloudSnapshots = Boolean(syncEntitlement?.can_write_snapshots)
    const canUseCloudSync = Boolean(syncEntitlement?.can_use_sync)
    const cloudQuotaSummary = cloudUsage
        ? {
            plan_code: cloudUsage.plan_code,
            quota_bytes: cloudUsage.quota_bytes,
            retention_days: cloudUsage.retention_days,
            max_devices: cloudUsage.max_devices,
            max_snapshots: cloudUsage.max_snapshots
        }
        : cloudQuota || (syncEntitlement
            ? {
                plan_code: syncEntitlement.plan_code,
                quota_bytes: syncEntitlement.quota_bytes,
                retention_days: syncEntitlement.retention_days,
                max_devices: syncEntitlement.max_devices,
                max_snapshots: syncEntitlement.max_snapshots,
                sync_enabled: syncEntitlement.can_use_sync,
                device_transfer_enabled: syncEntitlement.can_use_device_transfer,
            }
            : null)
    const cloudUsagePercent = cloudUsage?.quota_bytes
        ? Math.min(100, Math.round((cloudUsage.usage_bytes / cloudUsage.quota_bytes) * 100))
        : 0
    const desktopUpdateTitle = desktopUpdate.status === 'error'
        ? updateCopy.failedTitle
        : desktopUpdate.updateAvailable && desktopUpdate.latestVersion
            ? updateCopy.availableTitle(desktopUpdate.latestVersion)
            : updateCopy.upToDateTitle
    const desktopUpdateDescription = desktopUpdate.status === 'error'
        ? updateCopy.failedDescription
        : desktopUpdate.updateAvailable
            ? updateCopy.availableDescription
            : updateCopy.upToDateDescription
    const desktopUpdateStatusLabel = desktopUpdate.status === 'error'
        ? (previewIsVietnamese ? 'Lỗi kiểm tra' : 'Check failed')
        : desktopUpdate.updateAvailable
            ? (previewIsVietnamese ? 'Có bản mới' : 'Update available')
            : (previewIsVietnamese ? 'Đã mới nhất' : 'Up to date')

    useEffect(() => {
        if (!isOpen || activeTab !== 'profile' || isLoadingDeviceInfo || !user) {
            return
        }
        void loadCloudBackupData()
    }, [
        activeTab,
        deviceInfo.appVersion,
        deviceInfo.hostname,
        deviceInfo.platform,
        isLoadingDeviceInfo,
        isOpen,
        user?.id
    ])

    return (
        <AnimatePresence>
            {isOpen && (
                <motion.div
                    className="stg-overlay"
                    initial={{ opacity: 0 }}
                    animate={{ opacity: 1 }}
                    exit={{ opacity: 0 }}
                    onClick={onClose}
                >
                    <motion.div
                        className="stg-modal"
                        initial={{ opacity: 0, y: 12, scale: 0.97 }}
                        animate={{ opacity: 1, y: 0, scale: 1 }}
                        exit={{ opacity: 0, y: 8, scale: 0.97 }}
                        transition={{ duration: 0.18, ease: [0.16, 1, 0.3, 1] }}
                        onClick={(event) => event.stopPropagation()}
                    >
                        {/* ── Header ── */}
                        <div className="stg-header">
                            <div className="stg-header-left">
                                <h3>{copy.settings}</h3>
                                {hasUnsavedChanges && (
                                    <span className="stg-unsaved-dot" title={copy.unsavedChanges} />
                                )}
                            </div>
                            <button className="stg-close" onClick={onClose} title={copy.close}>
                                <X size={16} />
                            </button>
                        </div>

                        {/* ── Sidebar + Content layout ── */}
                        <div className="stg-layout">
                            {/* Sidebar navigation */}
                            <nav className="stg-nav" role="tablist" aria-label={copy.settingsSections}>
                                {settingsTabs.map(tab => {
                                    const TabIcon = tab.icon
                                    return (
                                        <button
                                            key={tab.id}
                                            type="button"
                                            role="tab"
                                            className={`stg-nav-item ${activeTab === tab.id ? 'stg-nav-item--active' : ''}`}
                                            aria-selected={activeTab === tab.id}
                                            onClick={() => setActiveTab(tab.id)}
                                        >
                                            <TabIcon size={16} className="stg-nav-icon" />
                                            <div className="stg-nav-text">
                                                <span className="stg-nav-label">{tab.label}</span>
                                                <span className="stg-nav-desc">{tab.description}</span>
                                            </div>
                                            <ChevronRight size={14} className="stg-nav-arrow" />
                                        </button>
                                    )
                                })}

                                <div className="stg-nav-footer">
                                    <button
                                        className="stg-reset-btn"
                                        onClick={handleResetDefaults}
                                        type="button"
                                    >
                                        {copy.restoreDefaults}
                                    </button>
                                </div>
                            </nav>

                            {/* Main content */}
                            <div className="stg-content">
                                <AnimatePresence mode="wait">
                                    {activeTab === 'profile' && (
                                        <motion.div
                                            key="profile"
                                            className="stg-pane"
                                            initial={{ opacity: 0, x: 6 }}
                                            animate={{ opacity: 1, x: 0 }}
                                            exit={{ opacity: 0, x: -6 }}
                                            transition={{ duration: 0.12 }}
                                        >
                                            <SettingsSection
                                                icon={User}
                                                title={copy.account}
                                                description={copy.accountDescription}
                                                accent
                                            >
                                                <div className="stg-profile-card">
                                                    <div className="stg-profile-avatar" aria-hidden="true">
                                                        {user?.avatar_url ? (
                                                            <img
                                                                src={user.avatar_url}
                                                                alt={user.username || user.email || copy.pigtexUser}
                                                                className="stg-profile-avatar-image"
                                                                referrerPolicy="no-referrer"
                                                            />
                                                        ) : (
                                                            <span>{getProfileInitial(user?.username || user?.email)}</span>
                                                        )}
                                                    </div>
                                                    <div className="stg-profile-main">
                                                        <div className="stg-profile-heading">
                                                            <div className="stg-profile-identity">
                                                                <span className="stg-profile-name">{user?.username || copy.pigtexUser}</span>
                                                                <span className="stg-profile-email">{user?.email || copy.noEmail}</span>
                                                            </div>
                                                            <span className="stg-plan-badge">{accountPlanLabel}</span>
                                                        </div>
                                                        <div className="stg-info-grid">
                                                            <div className="stg-info-card">
                                                                <span className="stg-info-label">{copy.currentPlan}</span>
                                                                <span className="stg-info-value">{accountPlanLabel}</span>
                                                            </div>
                                                            <div className="stg-info-card">
                                                                <span className="stg-info-label">{copy.signedInWith}</span>
                                                                <span className="stg-info-value">{accountProviderLabel}</span>
                                                            </div>
                                                            <div className="stg-info-card">
                                                                <span className="stg-info-label">{copy.createdAt}</span>
                                                                <span className="stg-info-value">{formatDateTimeLabel(user?.created_at, previewLocale, copy.noData)}</span>
                                                            </div>
                                                            <div className="stg-info-card">
                                                                <span className="stg-info-label">{copy.lastLogin}</span>
                                                                <span className="stg-info-value">{formatDateTimeLabel(user?.last_login, previewLocale, copy.noData)}</span>
                                                            </div>
                                                        </div>
                                                    </div>
                                                </div>
                                            </SettingsSection>

                                            <SettingsSection
                                                icon={LogOut}
                                                title={copy.signOut}
                                                description={copy.signOutDescription}
                                            >
                                                <div className="stg-action-row">
                                                    <button
                                                        type="button"
                                                        className="stg-btn stg-btn--danger"
                                                        onClick={handleSignOut}
                                                    >
                                                        <LogOut size={14} />
                                                        {copy.signOut}
                                                    </button>
                                                    <span className="stg-hint">{copy.signOutHint}</span>
                                                </div>
                                            </SettingsSection>

                                            <SettingsSection
                                                icon={ExternalLink}
                                                title={copy.syncSubscription}
                                                description={copy.syncSubscriptionDescription}
                                            >
                                                <div className="stg-info-grid">
                                                    <div className="stg-info-card">
                                                        <span className="stg-info-label">{copy.currentPlan}</span>
                                                        <span className="stg-info-value">
                                                            {syncEntitlement ? formatPlanLabel(syncEntitlement.plan_code, previewIsVietnamese) : accountPlanLabel}
                                                        </span>
                                                    </div>
                                                    <div className="stg-info-card">
                                                        <span className="stg-info-label">{copy.subscriptionStatus}</span>
                                                        <span className="stg-info-value">
                                                            {formatSubscriptionStatusLabel(syncEntitlement?.status, previewIsVietnamese)}
                                                        </span>
                                                    </div>
                                                    <div className="stg-info-card">
                                                        <span className="stg-info-label">{copy.billingCycle}</span>
                                                        <span className="stg-info-value">
                                                            {formatBillingCycleLabel(syncEntitlement?.billing_cycle, previewIsVietnamese)}
                                                        </span>
                                                    </div>
                                                    <div className="stg-info-card">
                                                        <span className="stg-info-label">{copy.graceEnds}</span>
                                                        <span className="stg-info-value">
                                                            {formatDateTimeLabel(syncEntitlement?.grace_ends_at, previewLocale, copy.noData)}
                                                        </span>
                                                    </div>
                                                </div>
                                                <div className="stg-action-row">
                                                    <button
                                                        type="button"
                                                        className="stg-btn stg-btn--primary"
                                                        onClick={() => void handleUpgradeSync('sync', 'monthly')}
                                                        disabled={
                                                            isManagingSubscription
                                                            || isLoadingSyncEntitlement
                                                            || syncEntitlement?.plan_code === 'sync'
                                                            || syncEntitlement?.plan_code === 'sync_plus'
                                                        }
                                                    >
                                                        {`${copy.upgradeToSync} • ${formatVndPrice(syncEntitlement?.plans.find(plan => plan.plan_code === 'sync')?.monthly_price_vnd)}đ/${previewIsVietnamese ? 'th' : 'mo'}`}
                                                    </button>
                                                    <button
                                                        type="button"
                                                        className="stg-mini-btn"
                                                        onClick={() => void handleUpgradeSync('sync_plus', 'monthly')}
                                                        disabled={isManagingSubscription || isLoadingSyncEntitlement || syncEntitlement?.plan_code === 'sync_plus'}
                                                    >
                                                        {`${copy.upgradeToSyncPlus} • ${formatVndPrice(syncEntitlement?.plans.find(plan => plan.plan_code === 'sync_plus')?.monthly_price_vnd)}đ/${previewIsVietnamese ? 'th' : 'mo'}`}
                                                    </button>
                                                    {syncEntitlement && syncEntitlement.plan_code !== 'free' && (
                                                        <>
                                                            <button
                                                                type="button"
                                                                className="stg-mini-btn"
                                                                onClick={() => void handleManageSubscription()}
                                                                disabled={isManagingSubscription}
                                                            >
                                                                {copy.manageSubscription}
                                                            </button>
                                                            <button
                                                                type="button"
                                                                className="stg-mini-btn"
                                                                onClick={() => void handleCancelSubscription()}
                                                                disabled={isManagingSubscription}
                                                            >
                                                                {copy.cancelSubscription}
                                                            </button>
                                                        </>
                                                    )}
                                                </div>
                                            </SettingsSection>

                                            <SettingsSection
                                                icon={Sliders}
                                                title={copy.appLanguageTitle}
                                                description={copy.appLanguageDescription}
                                            >
                                                <div className="stg-field">
                                                    <label className="stg-label" htmlFor="stg-language">
                                                        {copy.language}
                                                    </label>
                                                    <select
                                                        id="stg-language"
                                                        value={draft.language}
                                                        onChange={(event) => setDraft(prev => ({
                                                            ...prev,
                                                            language: event.target.value === 'en' ? 'en' : 'vi'
                                                        }))}
                                                        className="stg-select"
                                                    >
                                                        <option value="vi">{copy.vietnamese}</option>
                                                        <option value="en">{copy.english}</option>
                                                    </select>
                                                    <span className="stg-hint">{copy.appLanguageHint}</span>
                                                </div>
                                            </SettingsSection>

                                            <SettingsSection
                                                icon={HardDrive}
                                                title={copy.currentDevice}
                                                description={isLoadingDeviceInfo ? copy.loadingDeviceInfo : copy.currentDeviceDescription}
                                            >
                                                <div className="stg-info-grid">
                                                    <div className="stg-info-card">
                                                        <span className="stg-info-label">{copy.device}</span>
                                                        <span className="stg-info-value">{deviceInfo.hostname}</span>
                                                    </div>
                                                    <div className="stg-info-card">
                                                        <span className="stg-info-label">{copy.platform}</span>
                                                        <span className="stg-info-value">{formatPlatformLabel(deviceInfo.platform, previewIsVietnamese)}</span>
                                                    </div>
                                                    <div className="stg-info-card">
                                                        <span className="stg-info-label">{copy.architecture}</span>
                                                        <span className="stg-info-value">{deviceInfo.arch}</span>
                                                    </div>
                                                    <div className="stg-info-card">
                                                        <span className="stg-info-label">{copy.appVersion}</span>
                                                        <span className="stg-info-value">{deviceInfo.appVersion}</span>
                                                    </div>
                                                    <div className="stg-info-card">
                                                        <span className="stg-info-label">{copy.language}</span>
                                                        <span className="stg-info-value">{deviceInfo.language}</span>
                                                    </div>
                                                    <div className="stg-info-card">
                                                        <span className="stg-info-label">{copy.timeZone}</span>
                                                        <span className="stg-info-value">{deviceInfo.timeZone}</span>
                                                    </div>
                                                </div>
                                                <span className="stg-hint">
                                                    {copy.deviceHint}
                                                </span>
                                            </SettingsSection>

                                            <SettingsSection
                                                icon={RefreshCw}
                                                title={updateCopy.title}
                                                description={updateCopy.description}
                                            >
                                                <div className={`stg-update-banner ${desktopUpdate.updateAvailable ? 'stg-update-banner--ready' : desktopUpdate.status === 'error' ? 'stg-update-banner--error' : 'stg-update-banner--idle'}`}>
                                                    <div className="stg-update-banner-copy">
                                                        <span className="stg-update-banner-title">{desktopUpdateTitle}</span>
                                                        <span className="stg-update-banner-desc">{desktopUpdateDescription}</span>
                                                    </div>
                                                    {desktopUpdate.updateAvailable && (
                                                        <button
                                                            type="button"
                                                            className="stg-btn stg-btn--primary"
                                                            onClick={() => void onInstallDesktopUpdate()}
                                                            disabled={isInstallingDesktopUpdate}
                                                        >
                                                            {isInstallingDesktopUpdate ? updateCopy.installing : updateCopy.installNow}
                                                        </button>
                                                    )}
                                                </div>

                                                <div className="stg-info-grid">
                                                    <div className="stg-info-card">
                                                        <span className="stg-info-label">{copy.appVersion}</span>
                                                        <span className="stg-info-value">{desktopUpdate.currentVersion || copy.noData}</span>
                                                    </div>
                                                    <div className="stg-info-card">
                                                        <span className="stg-info-label">{updateCopy.latestVersion}</span>
                                                        <span className="stg-info-value">{desktopUpdate.latestVersion || copy.noData}</span>
                                                    </div>
                                                    <div className="stg-info-card">
                                                        <span className="stg-info-label">{updateCopy.status}</span>
                                                        <span className="stg-info-value">{desktopUpdateStatusLabel}</span>
                                                    </div>
                                                    <div className="stg-info-card">
                                                        <span className="stg-info-label">{updateCopy.lastChecked}</span>
                                                        <span className="stg-info-value">
                                                            {formatDateTimeLabel(desktopUpdate.checkedAt, previewLocale, copy.noData)}
                                                        </span>
                                                    </div>
                                                </div>

                                                {desktopUpdate.errorMessage && (
                                                    <div className="stg-danger-note">
                                                        {desktopUpdate.errorMessage}
                                                    </div>
                                                )}

                                                <div className="stg-action-row">
                                                    <button
                                                        type="button"
                                                        className="stg-mini-btn"
                                                        onClick={() => void onCheckDesktopUpdate()}
                                                        disabled={isCheckingDesktopUpdate || isInstallingDesktopUpdate}
                                                    >
                                                        <RefreshCw size={12} className={isCheckingDesktopUpdate ? 'spin' : ''} />
                                                        {isCheckingDesktopUpdate ? updateCopy.checking : updateCopy.checkNow}
                                                    </button>
                                                    {desktopUpdate.downloadPageUrl && (
                                                        <button
                                                            type="button"
                                                            className="stg-mini-btn"
                                                            onClick={() => void onOpenDesktopUpdatePage()}
                                                            disabled={isInstallingDesktopUpdate}
                                                        >
                                                            <ExternalLink size={12} />
                                                            {updateCopy.openWebsite}
                                                        </button>
                                                    )}
                                                </div>

                                                <span className="stg-hint">
                                                    {updateCopy.updateHint}
                                                </span>
                                            </SettingsSection>

                                            <SettingsSection
                                                icon={HardDrive}
                                                title={copy.cloudBackup}
                                                description={canUseCloudBackup ? copy.cloudBackupDescription : copy.cloudLockedDescription}
                                            >
                                                <div className="stg-action-row">
                                                    <button
                                                        type="button"
                                                        className="stg-btn stg-btn--primary"
                                                        onClick={handleCreateCloudBackup}
                                                        disabled={
                                                            !canWriteCloudSnapshots
                                                            || isLoadingCloudData
                                                            || isCreatingCloudBackup
                                                            || !cloudDeviceId
                                                            || Boolean(restoreSnapshotId)
                                                            || isSyncingCloud
                                                        }
                                                    >
                                                        {isCreatingCloudBackup ? copy.backingUp : copy.backupNow}
                                                    </button>
                                                    <button
                                                        type="button"
                                                        className="stg-mini-btn"
                                                        onClick={() => void handleSyncNow()}
                                                        disabled={
                                                            !canUseCloudSync
                                                            || !cloudDeviceId
                                                            || isLoadingCloudData
                                                            || isCreatingCloudBackup
                                                            || isSyncingCloud
                                                            || Boolean(restoreSnapshotId)
                                                        }
                                                    >
                                                        {isSyncingCloud ? copy.syncing : copy.syncNow}
                                                    </button>
                                                    <button
                                                        type="button"
                                                        className="stg-mini-btn"
                                                        onClick={() => void loadCloudBackupData({ showErrorToast: true })}
                                                        disabled={isLoadingCloudData || isCreatingCloudBackup || Boolean(restoreSnapshotId) || isSyncingCloud}
                                                    >
                                                        <RefreshCw size={12} className={isLoadingCloudData ? 'spin' : ''} />
                                                        {copy.reload}
                                                    </button>
                                                    <span className="stg-hint">
                                                        {!canUseCloudBackup
                                                            ? copy.cloudLockedDescription
                                                            : cloudDeviceId
                                                            ? copy.cloudDeviceReady
                                                            : isLoadingCloudData
                                                                ? copy.cloudRegisteringDevice
                                                                : copy.cloudNotReady}
                                                    </span>
                                                </div>

                                                {cloudError && (
                                                    <div className="stg-danger-note">
                                                        {cloudError}
                                                    </div>
                                                )}

                                                <div className="stg-info-grid">
                                                    <div className="stg-info-card">
                                                        <span className="stg-info-label">{copy.status}</span>
                                                        <span className="stg-info-value">
                                                            {canUseCloudBackup
                                                                ? (cloudDeviceId ? copy.connected : copy.notConnected)
                                                                : formatSubscriptionStatusLabel(syncEntitlement?.status, previewIsVietnamese)}
                                                        </span>
                                                    </div>
                                                    <div className="stg-info-card">
                                                        <span className="stg-info-label">{copy.syncState}</span>
                                                        <span className="stg-info-value">
                                                            {formatSyncStateLabel(syncState?.status, previewIsVietnamese)}
                                                        </span>
                                                    </div>
                                                    <div className="stg-info-card">
                                                        <span className="stg-info-label">{copy.storage}</span>
                                                        <span className="stg-info-value">
                                                            {cloudUsage
                                                                ? `${formatByteSize(cloudUsage.usage_bytes)} / ${formatByteSize(cloudUsage.quota_bytes)}`
                                                                : cloudQuotaSummary
                                                                    ? `0 B / ${formatByteSize(cloudQuotaSummary.quota_bytes)}`
                                                                    : copy.noData}
                                                        </span>
                                                    </div>
                                                    <div className="stg-info-card">
                                                        <span className="stg-info-label">{copy.snapshots}</span>
                                                        <span className="stg-info-value">
                                                            {cloudUsage?.snapshot_count ?? cloudBackups.length}
                                                            {cloudQuotaSummary ? ` / ${cloudQuotaSummary.max_snapshots}` : ''}
                                                        </span>
                                                    </div>
                                                    <div className="stg-info-card">
                                                        <span className="stg-info-label">{copy.retention}</span>
                                                        <span className="stg-info-value">
                                                            {cloudQuotaSummary ? `${cloudQuotaSummary.retention_days} ${copy.days}` : copy.noData}
                                                        </span>
                                                    </div>
                                                </div>

                                                {cloudUsage && (
                                                    <span className="stg-hint">
                                                        {copy.cloudUsageHint(cloudUsagePercent)}
                                                    </span>
                                                )}

                                                <div className="stg-cloud-list">
                                                    {isLoadingCloudData ? (
                                                        <div className="stg-cloud-empty">{copy.loadingCloudBackups}</div>
                                                    ) : cloudBackups.length === 0 ? (
                                                        <div className="stg-cloud-empty">
                                                            {copy.noCloudSnapshots}
                                                        </div>
                                                    ) : (
                                                        cloudBackups.map(snapshot => {
                                                            const statusTone =
                                                                snapshot.status === 'ready'
                                                                    ? 'ready'
                                                                    : snapshot.status === 'failed'
                                                                        ? 'failed'
                                                                        : 'progress'
                                                            return (
                                                                <div key={snapshot.snapshot_id} className="stg-cloud-item">
                                                                    <div className="stg-cloud-item-main">
                                                                        <div className="stg-cloud-item-top">
                                                                            <span className="stg-cloud-item-title">
                                                                                {formatDateTimeLabel(snapshot.created_at, previewLocale, copy.noData)}
                                                                            </span>
                                                                            <span className={`stg-status-chip stg-status-chip--${statusTone}`}>
                                                                                {formatCloudStatusLabel(snapshot.status, previewIsVietnamese)}
                                                                            </span>
                                                                        </div>
                                                                        <div className="stg-cloud-meta">
                                                                            <span>{snapshot.device_name}</span>
                                                                            <span>{formatByteSize(snapshot.payload_size_bytes)}</span>
                                                                            <span>{formatCloudScopeLabel(snapshot.scope_type, previewIsVietnamese)}</span>
                                                                        </div>
                                                                    </div>
                                                                    <button
                                                                        type="button"
                                                                        className="stg-mini-btn"
                                                                        onClick={() => void handleRestoreCloudBackup(snapshot.snapshot_id)}
                                                                        disabled={
                                                                            !syncEntitlement?.can_restore_snapshots
                                                                            || snapshot.status !== 'ready'
                                                                            || isCreatingCloudBackup
                                                                            || Boolean(restoreSnapshotId)
                                                                            || isSyncingCloud
                                                                        }
                                                                    >
                                                                        {restoreSnapshotId === snapshot.snapshot_id ? copy.restoring : copy.restore}
                                                                    </button>
                                                                </div>
                                                            )
                                                        })
                                                    )}
                                                </div>
                                            </SettingsSection>

                                            <SettingsSection
                                                icon={Lock}
                                                title={hasPassword ? copy.changePassword : copy.createPassword}
                                                description={
                                                    hasPassword
                                                        ? copy.changePasswordDescription
                                                        : copy.createPasswordDescription
                                                }
                                            >
                                                {!hasPassword && (
                                                    <div className="stg-inline-note">
                                                        {copy.oauthPasswordHint(accountProviderLabel)}
                                                    </div>
                                                )}
                                                {hasPassword && (
                                                    <div className="stg-field">
                                                        <label className="stg-label" htmlFor="stg-current-password">
                                                            {copy.currentPassword}
                                                        </label>
                                                        <input
                                                            id="stg-current-password"
                                                            type="password"
                                                            value={passwordForm.currentPassword}
                                                            onChange={(event) => setPasswordForm(prev => ({
                                                                ...prev,
                                                                currentPassword: event.target.value
                                                            }))}
                                                            className="stg-input"
                                                            autoComplete="current-password"
                                                        />
                                                    </div>
                                                )}
                                                <div className="stg-field">
                                                    <label className="stg-label" htmlFor="stg-new-password">
                                                        {copy.newPassword}
                                                    </label>
                                                    <input
                                                        id="stg-new-password"
                                                        type="password"
                                                        value={passwordForm.newPassword}
                                                        onChange={(event) => setPasswordForm(prev => ({
                                                            ...prev,
                                                            newPassword: event.target.value
                                                        }))}
                                                        className="stg-input"
                                                        autoComplete="new-password"
                                                    />
                                                </div>
                                                <div className="stg-field">
                                                    <label className="stg-label" htmlFor="stg-confirm-password">
                                                        {copy.confirmNewPassword}
                                                    </label>
                                                    <input
                                                        id="stg-confirm-password"
                                                        type="password"
                                                        value={passwordForm.confirmPassword}
                                                        onChange={(event) => setPasswordForm(prev => ({
                                                            ...prev,
                                                            confirmPassword: event.target.value
                                                        }))}
                                                        className="stg-input"
                                                        autoComplete="new-password"
                                                    />
                                                    <span className="stg-hint">{copy.minimumPasswordHint}</span>
                                                </div>
                                                <div className="stg-action-row">
                                                    <button
                                                        type="button"
                                                        className="stg-btn stg-btn--primary"
                                                        onClick={handleChangePassword}
                                                        disabled={!canSubmitPasswordChange || isChangingPassword}
                                                    >
                                                        {isChangingPassword ? copy.updating : hasPassword ? copy.updatePassword : copy.createPassword}
                                                    </button>
                                                </div>
                                            </SettingsSection>

                                            <SettingsSection
                                                icon={AlertCircle}
                                                title={copy.deleteAccount}
                                                description={copy.deleteAccountDescription}
                                            >
                                                <div className="stg-danger-note">
                                                    {copy.deleteAccountWarning}
                                                </div>
                                                <div className="stg-field">
                                                    <label className="stg-label" htmlFor="stg-delete-confirmation">
                                                        {copy.enterEmailToConfirm}
                                                    </label>
                                                    <input
                                                        id="stg-delete-confirmation"
                                                        type="text"
                                                        value={deleteForm.confirmation}
                                                        onChange={(event) => setDeleteForm(prev => ({
                                                            ...prev,
                                                            confirmation: event.target.value
                                                        }))}
                                                        placeholder={user?.email || 'you@example.com'}
                                                        className="stg-input"
                                                    />
                                                </div>
                                                {hasPassword && (
                                                    <div className="stg-field">
                                                        <label className="stg-label" htmlFor="stg-delete-password">
                                                            {copy.password}
                                                        </label>
                                                        <input
                                                            id="stg-delete-password"
                                                            type="password"
                                                            value={deleteForm.password}
                                                            onChange={(event) => setDeleteForm(prev => ({
                                                                ...prev,
                                                                password: event.target.value
                                                            }))}
                                                            className="stg-input"
                                                            autoComplete="current-password"
                                                        />
                                                    </div>
                                                )}
                                                <div className="stg-action-row">
                                                    <button
                                                        type="button"
                                                        className="stg-btn stg-btn--danger"
                                                        onClick={handleDeleteAccount}
                                                        disabled={!canDeleteAccount || isDeletingAccount}
                                                    >
                                                        {isDeletingAccount ? copy.deleting : copy.deleteAccount}
                                                    </button>
                                                    <span className="stg-hint">{copy.cannotUndo}</span>
                                                </div>
                                            </SettingsSection>

                                            <SettingsSection
                                                icon={HardDrive}
                                                title={copy.resetLocalData}
                                                description={copy.resetLocalDataDescription}
                                            >
                                                <div className="stg-danger-note">
                                                    {copy.resetLocalDataWarning}
                                                </div>
                                                <div className="stg-inline-note">
                                                    {copy.resetLocalDataServerNote}
                                                </div>
                                                <div className="stg-action-row">
                                                    <button
                                                        type="button"
                                                        className="stg-btn stg-btn--danger"
                                                        onClick={handleResetLocalData}
                                                        disabled={isResettingLocalData}
                                                    >
                                                        {isResettingLocalData ? copy.resettingLocalData : copy.resetLocalData}
                                                    </button>
                                                    <span className="stg-hint">{copy.cannotUndo}</span>
                                                </div>
                                            </SettingsSection>
                                        </motion.div>
                                    )}

                                    {activeTab === 'connection' && (
                                        <motion.div
                                            key="connection"
                                            className="stg-pane"
                                            initial={{ opacity: 0, x: 6 }}
                                            animate={{ opacity: 1, x: 0 }}
                                            exit={{ opacity: 0, x: -6 }}
                                            transition={{ duration: 0.12 }}
                                        >
                                            {/* API Provider Selection */}
                                            <SettingsSection
                                                icon={Plug}
                                                title={copy.endpointMode}
                                                description={copy.endpointModeDescription}
                                                accent
                                            >
                                                {/* Provider Selector */}
                                                <div className="stg-field">
                                                    <label className="stg-label">{copy.mode}</label>
                                                    <div className="stg-provider-grid">
                                                        {providerCatalog.map(provider => (
                                                            <button
                                                                key={provider.id}
                                                                type="button"
                                                                className={`stg-provider-btn ${selectedPublicProviderId === provider.id ? 'stg-provider-btn--active' : ''}`}
                                                                onClick={() => handleProviderChange(provider.id)}
                                                            >
                                                                <span className="stg-provider-name">{provider.label}</span>
                                                            </button>
                                                        ))}
                                                    </div>
                                                </div>

                                                {/* API Key */}
                                                <div className="stg-field">
                                                    <div className="stg-field-row">
                                                        <label className="stg-label" htmlFor="stg-api-key">API Key</label>
                                                        {currentProviderConfig.docs_url ? (
                                                            <a
                                                                href={currentProviderConfig.docs_url}
                                                                target="_blank"
                                                                rel="noopener noreferrer"
                                                                className="stg-docs-link"
                                                            >
                                                                <ExternalLink size={11} />
                                                                {copy.getKey}
                                                            </a>
                                                        ) : null}
                                                    </div>
                                                    <div className="stg-secret-wrap">
                                                        <input
                                                            id="stg-api-key"
                                                            type={showApiKey ? 'text' : 'password'}
                                                            value={draft.apiKey}
                                                            onChange={(e) => setDraft(prev => {
                                                                if (providerManagedByServer) return prev
                                                                const nextApiKey = e.target.value
                                                                const profiles = ensureCredentialProfiles(prev.providerCredentialProfiles)
                                                                profiles[prev.apiProvider] = {
                                                                    apiKey: nextApiKey,
                                                                    baseUrl: normalizeProfileBaseUrl(prev.apiProvider, prev.baseUrl),
                                                                }
                                                                return {
                                                                    ...prev,
                                                                    apiKey: nextApiKey,
                                                                    providerCredentialProfiles: profiles,
                                                                }
                                                            })}
                                                            placeholder={
                                                                providerManagedByServer
                                                                    ? providerManagedConnectionHint
                                                                    : isTexApiManagedProvider
                                                                        ? copy.texapiApiKeyPlaceholder
                                                                        : copy.getKey
                                                            }
                                                            disabled={providerManagedByServer}
                                                            className="stg-input"
                                                        />
                                                        <button
                                                            type="button"
                                                            className="stg-secret-btn"
                                                            onClick={() => setShowApiKey(prev => !prev)}
                                                            title={showApiKey ? copy.hideSecret : copy.showSecret}
                                                            disabled={providerManagedByServer}
                                                        >
                                                            {showApiKey ? <EyeOff size={14} /> : <Eye size={14} />}
                                                        </button>
                                                    </div>
                                                    {providerManagedByServer ? (
                                                        <span className="stg-hint">{providerManagedConnectionHint}</span>
                                                    ) : null}
                                                </div>

                                                {/* Base URL */}
                                                <div className="stg-field">
                                                    <label className="stg-label" htmlFor="stg-base-url">Base URL</label>
                                                    <input
                                                        id="stg-base-url"
                                                        type="text"
                                                        value={isBaseUrlLocked ? (currentProviderConfig.default_base_url || draft.baseUrl) : draft.baseUrl}
                                                        onChange={(e) => {
                                                            if (isBaseUrlLocked) return
                                                            const nextBaseUrl = e.target.value
                                                            setDraft(prev => {
                                                                const profiles = ensureCredentialProfiles(prev.providerCredentialProfiles)
                                                                profiles[prev.apiProvider] = {
                                                                    apiKey: prev.apiKey,
                                                                    baseUrl: normalizeProfileBaseUrl(prev.apiProvider, nextBaseUrl),
                                                                }
                                                                return {
                                                                    ...prev,
                                                                    baseUrl: nextBaseUrl,
                                                                    providerCredentialProfiles: profiles,
                                                                }
                                                            })
                                                        }}
                                                        placeholder={currentProviderConfig.default_base_url || 'https://your-openai-compatible-endpoint/v1'}
                                                        disabled={isBaseUrlLocked}
                                                        className="stg-input"
                                                    />
                                                    <span className="stg-hint">
                                                        {copy.defaultBaseUrl}: <code>{currentProviderConfig.default_base_url || '—'}</code>
                                                        {isBaseUrlLocked
                                                            ? ` · ${copy.baseUrlLockedHint}`
                                                            : ` · ${copy.baseUrlCustomHint}`}
                                                    </span>
                                                </div>

                                                {isTexApiManagedProvider && (
                                                    <div className="stg-field">
                                                        <div className="stg-inline-note">
                                                            {isTexApiManagedFallback ? copy.providerManagedTexapiHint : copy.texapiByokHint}
                                                        </div>
                                                    </div>
                                                )}

                                                {isTexApiManagedFallback && (
                                                    <div className="stg-field">
                                                        <div className="stg-field-row">
                                                            <label className="stg-label">{copy.texapiCreditsTitle}</label>
                                                            <button
                                                                type="button"
                                                                className="stg-mini-btn"
                                                                onClick={() => setTexApiUsageReloadTick(prev => prev + 1)}
                                                                disabled={isLoadingTexApiUsage}
                                                            >
                                                                <RefreshCw size={12} className={isLoadingTexApiUsage ? 'spin' : ''} />
                                                                {copy.reload}
                                                            </button>
                                                        </div>
                                                        <span className="stg-hint">{copy.texapiCreditsDescription}</span>
                                                        {texApiUsageError && (
                                                            <div className="stg-danger-note">
                                                                {texApiUsageError}
                                                            </div>
                                                        )}
                                                        {hasTexApiUsageData ? (
                                                            <div className="stg-info-grid">
                                                                <div className="stg-info-card">
                                                                    <span className="stg-info-label">{copy.texapiRemaining}</span>
                                                                    <span className="stg-info-value">
                                                                        {formatCurrencyAmount(texApiUsage?.remaining_credits_usd, previewLocale, texApiUsage?.currency || 'USD')}
                                                                    </span>
                                                                </div>
                                                                <div className="stg-info-card">
                                                                    <span className="stg-info-label">{copy.texapiUsed}</span>
                                                                    <span className="stg-info-value">
                                                                        {formatCurrencyAmount(texApiUsage?.used_credits_usd, previewLocale, texApiUsage?.currency || 'USD')}
                                                                    </span>
                                                                </div>
                                                                <div className="stg-info-card">
                                                                    <span className="stg-info-label">{copy.texapiRequests}</span>
                                                                    <span className="stg-info-value">
                                                                        {formatCountValue(texApiUsage?.total_requests, previewLocale)}
                                                                    </span>
                                                                </div>
                                                                <div className="stg-info-card">
                                                                    <span className="stg-info-label">{copy.texapiPeriod}</span>
                                                                    <span className="stg-info-value">
                                                                        {formatDateRangeLabel(
                                                                            texApiUsage?.period_start,
                                                                            texApiUsage?.period_end,
                                                                            previewLocale,
                                                                            copy.noData
                                                                        )}
                                                                    </span>
                                                                </div>
                                                            </div>
                                                        ) : !texApiUsageError ? (
                                                            <span className="stg-hint">
                                                                {isLoadingTexApiUsage ? copy.texapiUsageLoading : copy.texapiUsageEmpty}
                                                            </span>
                                                        ) : null}
                                                    </div>
                                                )}

                                                {/* Model */}
                                                <div className="stg-field">
                                                    <div className="stg-field-row">
                                                        <label className="stg-label" htmlFor="stg-model">{copy.model}</label>
                                                        <button
                                                            type="button"
                                                            className="stg-mini-btn"
                                                            onClick={() => setModelReloadTick(prev => prev + 1)}
                                                            disabled={isLoadingModels}
                                                        >
                                                            <RefreshCw size={12} className={isLoadingModels ? 'spin' : ''} />
                                                            {copy.reload}
                                                        </button>
                                                    </div>
                                                    {modelOptions.length > 0 ? (
                                                        <select
                                                            id="stg-model"
                                                            value={modelOptions.includes(draft.model) ? draft.model : ''}
                                                            onChange={(e) => setDraft(prev => ({ ...prev, model: e.target.value }))}
                                                            className="stg-select"
                                                        >
                                                            {modelOptions.map((model) => (
                                                                <option key={model} value={model}>{model}</option>
                                                            ))}
                                                        </select>
                                                    ) : (
                                                        <input
                                                            id="stg-model"
                                                            type="text"
                                                            value={draft.model}
                                                            onChange={(e) => setDraft(prev => ({ ...prev, model: e.target.value }))}
                                                            placeholder={modelPlaceholder}
                                                            className="stg-input"
                                                        />
                                                    )}
                                                    <span className="stg-hint">
                                                        {isLoadingModels && copy.loadingModels}
                                                        {!isLoadingModels && modelsError && modelsError}
                                                        {!isLoadingModels && !modelsError && modelOptions.length > 0 && copy.availableModels(modelOptions.length)}
                                                    </span>
                                                </div>

                                                {/* Test connection inline */}
                                                <div className="stg-connection-test">
                                                    <button
                                                        className="stg-test-btn"
                                                        onClick={handleTestConnection}
                                                        disabled={isValidating || (!providerAcceptsClientCredentials && !isTexApiManagedFallback)}
                                                        type="button"
                                                    >
                                                        <Plug size={14} />
                                                        {isValidating ? copy.testingConnection : copy.testConnection}
                                                    </button>
                                                    {validationFeedback && (
                                                        <span className={`stg-test-result stg-test-result--${validationFeedback.kind}`}>
                                                            {validationFeedback.message}
                                                        </span>
                                                    )}
                                                </div>
                                            </SettingsSection>

                                            {/* API Key storage (merged from Privacy tab) */}
                                            <SettingsSection
                                                icon={Shield}
                                                title={copy.security}
                                                description={copy.securityDescription}
                                            >
                                                {renderToggle(
                                                    copy.storeApiKey,
                                                    secureStorageUnavailable
                                                        ? copy.storeApiKeyUnavailable
                                                        : draft.saveApiKeyLocally
                                                        ? copy.storeApiKeyOn
                                                        : copy.storeApiKeyOff,
                                                    secureStorageUnavailable ? false : draft.saveApiKeyLocally,
                                                    () => setDraft(prev => ({ ...prev, saveApiKeyLocally: !prev.saveApiKeyLocally })),
                                                    {
                                                        ariaLabel: copy.toggleApiKeyStorage,
                                                        disabled: secureStorageUnavailable
                                                    }
                                                )}
                                            </SettingsSection>
                                        </motion.div>
                                    )}

                                    {activeTab === 'behavior' && (
                                        <motion.div
                                            key="behavior"
                                            className="stg-pane"
                                            initial={{ opacity: 0, x: 6 }}
                                            animate={{ opacity: 1, x: 0 }}
                                            exit={{ opacity: 0, x: -6 }}
                                            transition={{ duration: 0.12 }}
                                        >
                                            {/* Behavior */}
                                            <SettingsSection
                                                icon={Sparkles}
                                                title={copy.responseBehavior}
                                                description={copy.responseBehaviorDescription}
                                                accent
                                            >
                                                <div className="stg-field">
                                                    <label className="stg-label" htmlFor="stg-custom-instruction">
                                                        {copy.customInstruction}
                                                    </label>
                                                    <textarea
                                                        id="stg-custom-instruction"
                                                        value={draft.customInstruction}
                                                        onChange={(e) => setDraft(prev => ({
                                                            ...prev,
                                                            customInstruction: e.target.value
                                                        }))}
                                                        placeholder={copy.customInstructionPlaceholder}
                                                        rows={4}
                                                        maxLength={1200}
                                                        className="stg-textarea"
                                                    />
                                                    <span className="stg-hint">
                                                        {copy.customInstructionHint(draft.customInstruction.length)}
                                                    </span>
                                                </div>
                                            </SettingsSection>

                                            {/* Memory */}
                                            <SettingsSection
                                                icon={Brain}
                                                title="Memory"
                                                description={
                                                    draft.memoryEnabled
                                                        ? copy.memoryEnabled(activeMemoryTools.length)
                                                        : copy.memoryOff
                                                }
                                            >
                                                {renderToggle(
                                                    copy.globalMemory,
                                                    copy.globalMemoryHint,
                                                    draft.memoryEnabled,
                                                    () => setDraft(prev => ({ ...prev, memoryEnabled: !prev.memoryEnabled }))
                                                )}
                                                {draft.memoryEnabled && (
                                                    <div className="stg-sub-toggles">
                                                        {renderToggle(
                                                            previewIsVietnamese ? 'Tri thức' : 'Knowledge',
                                                            copy.knowledgeHint,
                                                            draft.useKnowledge,
                                                            () => setDraft(prev => ({ ...prev, useKnowledge: !prev.useKnowledge }))
                                                        )}
                                                        {renderToggle(
                                                            previewIsVietnamese ? 'Facts' : 'Facts',
                                                            copy.factsHint,
                                                            draft.useFacts,
                                                            () => setDraft(prev => ({ ...prev, useFacts: !prev.useFacts }))
                                                        )}
                                                        {renderToggle(
                                                            previewIsVietnamese ? 'Lịch sử' : 'History',
                                                            copy.historyHint,
                                                            draft.useHistory,
                                                            () => setDraft(prev => ({ ...prev, useHistory: !prev.useHistory }))
                                                        )}
                                                    </div>
                                                )}
                                            </SettingsSection>

                                            {/* Generation params */}
                                            <SettingsSection
                                                icon={Sliders}
                                                title={copy.responseParameters}
                                                description={copy.responseParametersDescription}
                                            >
                                                <div className="stg-field">
                                                    <div className="stg-field-row">
                                                        <label className="stg-label" htmlFor="stg-temperature">Temperature</label>
                                                        <span className="stg-chip">{draft.temperature.toFixed(2)}</span>
                                                    </div>
                                                    <div className="stg-slider-row">
                                                        <input
                                                            id="stg-temperature"
                                                            type="range"
                                                            min={0}
                                                            max={2}
                                                            step={0.05}
                                                            value={draft.temperature}
                                                            onChange={(e) => setDraft(prev => ({
                                                                ...prev,
                                                                temperature: normalizeTemperature(e.target.value)
                                                            }))}
                                                            className="stg-range"
                                                        />
                                                    </div>
                                                    <span className="stg-hint">{copy.temperatureHint}</span>
                                                </div>

                                                <div className="stg-field">
                                                    <label className="stg-label" htmlFor="stg-max-tokens">Max tokens</label>
                                                    <input
                                                        id="stg-max-tokens"
                                                        type="number"
                                                        min={0}
                                                        max={32768}
                                                        step={1}
                                                        value={draft.maxTokens}
                                                        onChange={(e) => setDraft(prev => ({
                                                            ...prev,
                                                            maxTokens: normalizeMaxTokens(e.target.value)
                                                        }))}
                                                        className="stg-input stg-input--narrow"
                                                    />
                                                    <span className="stg-hint">{copy.maxTokensHint}</span>
                                                </div>

                                                {renderToggle(
                                                    copy.defaultAiFiles,
                                                    copy.defaultAiFilesHint,
                                                    draft.defaultAiFileMode,
                                                    () => setDraft(prev => ({ ...prev, defaultAiFileMode: !prev.defaultAiFileMode }))
                                                )}

                                                {renderToggle(
                                                    copy.autoApproveAiFiles,
                                                    draft.autoApproveAiFileActions
                                                        ? copy.autoApproveAiFilesOn
                                                        : copy.autoApproveAiFilesOff,
                                                    draft.autoApproveAiFileActions,
                                                    () => setDraft(prev => ({
                                                        ...prev,
                                                        autoApproveAiFileActions: !prev.autoApproveAiFileActions
                                                    }))
                                                )}

                                                {renderToggle(
                                                    copy.qwenEnhancer,
                                                    draft.enableQwenImagePromptEnhancer
                                                        ? copy.qwenEnhancerOn
                                                        : copy.qwenEnhancerOff,
                                                    draft.enableQwenImagePromptEnhancer,
                                                    () => setDraft(prev => ({
                                                        ...prev,
                                                        enableQwenImagePromptEnhancer: !prev.enableQwenImagePromptEnhancer
                                                    })),
                                                    { ariaLabel: copy.toggleQwenEnhancer }
                                                )}
                                            </SettingsSection>
                                        </motion.div>
                                    )}
                                </AnimatePresence>
                            </div>
                        </div>

                        {/* ── Footer ── */}
                        <div className="stg-footer">
                            <button className="stg-btn stg-btn--ghost" onClick={onClose}>
                                {copy.cancel}
                            </button>
                            <button
                                className="stg-btn stg-btn--primary"
                                onClick={handleSave}
                                disabled={!canSave || !hasUnsavedChanges}
                            >
                                {hasUnsavedChanges ? copy.saveChanges : copy.saved}
                            </button>
                        </div>
                    </motion.div>
                </motion.div>
            )}
        </AnimatePresence>
    )
}

export default SettingsModal
