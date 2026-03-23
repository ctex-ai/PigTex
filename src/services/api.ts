// PigTex API Client - Python Backend Integration
// ===============================================
import {
    type ApiProviderCatalogEntry,
    getPigTexSettings,
    getProviderDefaultBaseUrl,
    resolveApiProviderForRequest,
    setRuntimeApiProviderCatalog
} from './settings';
import type { ApiEndpointProviderId, ApiProviderId } from './settings';
import { applyDeviceScopeHeaders } from '../utils/deviceScope';

// ===== Configuration =====
// Hosted-hybrid production standard:
// - production builds should set VITE_PIGTEX_API_BASE to the hosted backend root URL
// - localhost remains a development/QA fallback
const DEFAULT_PIGTEX_API_BASE = 'http://localhost:3001/api';
const ENV_PIGTEX_API_BASE =
    typeof import.meta !== 'undefined'
        ? (import.meta.env.VITE_PIGTEX_API_BASE as string | undefined)
        : undefined;
const IS_PRODUCTION_BUILD =
    typeof import.meta !== 'undefined'
        ? Boolean(import.meta.env.PROD)
        : false;
const ALLOW_LOCALHOST_API_BASE =
    typeof import.meta !== 'undefined'
        ? (import.meta.env.VITE_PIGTEX_ALLOW_LOCALHOST_API_BASE === '1' || import.meta.env.VITE_PIGTEX_ALLOW_LOCALHOST_API_BASE === 'true')
        : false;

function normalizeApiBase(base: string): string {
    const trimmed = (base || '').trim();
    if (!trimmed) return DEFAULT_PIGTEX_API_BASE;

    const withoutTrailingSlash = trimmed.replace(/\/+$/, '');
    return withoutTrailingSlash.endsWith('/api')
        ? withoutTrailingSlash
        : `${withoutTrailingSlash}/api`;
}

function isLoopbackHostname(hostname: string): boolean {
    const normalized = (hostname || '').trim().toLowerCase();
    return normalized === 'localhost'
        || normalized === '127.0.0.1'
        || normalized === '::1'
        || normalized === '[::1]';
}

function isLoopbackApiBase(apiBaseUrl: string): boolean {
    try {
        const parsed = new URL(apiBaseUrl);
        return isLoopbackHostname(parsed.hostname);
    } catch {
        return false;
    }
}

function isLikelyNetworkConnectivityError(error: unknown): boolean {
    if (!(error instanceof Error)) {
        return false;
    }

    const message = error.message.trim().toLowerCase();
    if (!message) {
        return false;
    }

    return (
        message.includes('failed to fetch')
        || message.includes('fetch failed')
        || message.includes('load failed')
        || message.includes('networkerror')
        || message.includes('network error')
        || message.includes('the network connection was lost')
    );
}

export function resolvePigTexApiBaseForEnvironment(
    envApiBase: string | undefined,
    isProductionBuild: boolean,
    allowLoopbackOverride = ALLOW_LOCALHOST_API_BASE
): string {
    const trimmed = (envApiBase || '').trim();
    if (!trimmed) {
        if (isProductionBuild) {
            throw new Error('Production desktop build requires VITE_PIGTEX_API_BASE to point to the hosted backend.');
        }
        return DEFAULT_PIGTEX_API_BASE;
    }

    const normalized = normalizeApiBase(trimmed);
    if (!isProductionBuild) {
        return normalized;
    }

    let parsed: URL;
    try {
        parsed = new URL(normalized);
    } catch {
        throw new Error('Production desktop build requires VITE_PIGTEX_API_BASE to be an absolute http(s) URL.');
    }

    if (parsed.protocol !== 'http:' && parsed.protocol !== 'https:') {
        throw new Error('Production desktop build requires VITE_PIGTEX_API_BASE to use http:// or https://.');
    }
    if (isLoopbackHostname(parsed.hostname) && !allowLoopbackOverride) {
        throw new Error('Production desktop build cannot use localhost or loopback for VITE_PIGTEX_API_BASE.');
    }

    return normalized;
}

export function resolveUpstreamBaseUrlForEnvironment(
    configuredBaseUrl: string | undefined,
    isProductionBuild: boolean,
    allowLoopbackOverride = ALLOW_LOCALHOST_API_BASE
): string {
    const normalized = normalizeBaseUrl(configuredBaseUrl || '');
    if (!normalized || !isProductionBuild) {
        return normalized;
    }

    let parsed: URL;
    try {
        parsed = new URL(normalized);
    } catch {
        throw new Error('Production desktop build requires AI provider base URL to be an absolute http(s) URL.');
    }

    if (parsed.protocol !== 'http:' && parsed.protocol !== 'https:') {
        throw new Error('Production desktop build requires AI provider base URL to use http:// or https://.');
    }
    if (isLoopbackHostname(parsed.hostname) && !allowLoopbackOverride) {
        throw new Error('Production desktop build cannot use localhost or loopback for AI provider base URL.');
    }

    return normalized;
}

const PIGTEX_API_BASE = resolvePigTexApiBaseForEnvironment(ENV_PIGTEX_API_BASE, IS_PRODUCTION_BUILD);
const PIGTEX_API_ROOT = PIGTEX_API_BASE.endsWith('/api')
    ? PIGTEX_API_BASE.slice(0, -4)
    : PIGTEX_API_BASE;
const PROTECTED_IMAGE_PATH_SEGMENT = '/api/images/serve/';

export type ApiConnectivityIssueKind =
    | 'backend_unreachable'
    | 'backend_unhealthy';

export interface ApiConnectivityIssue {
    kind: ApiConnectivityIssueKind;
    apiBaseUrl: string;
    isLoopback: boolean;
    statusCode?: number;
}

export interface ProviderCatalogResponse {
    data: ApiProviderCatalogEntry[];
}

// ===== Dynamic Model Type Detection =====
// Models are fetched from upstream /v1/models — no hardcoded catalog.

/**
 * Check if a model is a PAYG image model by looking at cached models.
 * Falls back to pattern matching if cache is empty.
 */
export function isPaygImageModel(modelId: string): boolean {
    const normalized = (modelId || '').trim();
    if (!normalized) return false;
    if (latestModelsCache) {
        const model = latestModelsCache.find(m => m.id === normalized);
        if (model) return modelSupportsCapability(model, 'image_generation', inferCapabilityTransport(model));
    }
    return matchesPigTexImageModelFamily(normalized);
}

export function isPaygModel(modelId: string): boolean {
    const normalized = (modelId || '').trim();
    if (!normalized) return false;
    if (latestModelsCache) {
        const model = latestModelsCache.find(m => m.id === normalized);
        if (model) {
            return modelSupportsCapability(model, 'image_generation', inferCapabilityTransport(model))
                || modelSupportsCapability(model, 'moderation', inferCapabilityTransport(model));
        }
    }
    const lower = normalized.toLowerCase();
    return matchesPigTexImageModelFamily(normalized) || lower.includes('moderation');
}

const PIGTEX_IMAGE_MODEL_HINTS = [
    'qwen-image',
    'z-image',
    'wanx',
    'seedream',
    'doubao-seedream',
    'gpt-image',
    'dall-e',
    'imagen',
    'imagegen',
    'stable-diffusion',
    'sdxl',
    'flux',
    'ideogram',
    'recraft'
] as const;

function matchesPigTexImageModelFamily(modelId: string): boolean {
    const lower = (modelId || '').trim().toLowerCase();
    if (!lower) return false;
    if (lower.includes('-image-') || lower.endsWith('-image') || lower.includes('image')) {
        return true;
    }
    return PIGTEX_IMAGE_MODEL_HINTS.some(hint => lower.includes(hint));
}

/**
 * Resolve a relative image serve path to a full URL.
 * e.g. /api/images/serve/abc.png → http://localhost:3001/api/images/serve/abc.png
 */
export function resolveImageUrl(path: string): string {
    if (path.startsWith('http://') || path.startsWith('https://') || path.startsWith('data:')) {
        return path;
    }
    // path starts with /api/...
    if (path.startsWith('/api/')) {
        return `${PIGTEX_API_ROOT}${path}`;
    }
    return `${PIGTEX_API_BASE}${path}`;
}

export function isProtectedImageUrl(path: string): boolean {
    return resolveImageUrl(path).includes(PROTECTED_IMAGE_PATH_SEGMENT);
}

export async function resolveProtectedImageSrc(path: string): Promise<{
    src: string;
    revoke?: () => void;
}> {
    const resolvedUrl = resolveImageUrl(path);
    if (!isProtectedImageUrl(resolvedUrl)) {
        return { src: resolvedUrl };
    }

    const headers: Record<string, string> = {
        'Authorization': getBearerToken()
    };
    applyDeviceScopeHeaders(headers);
    const response = await fetch(resolvedUrl, { headers });

    if (!response.ok) {
        const error = await response.json().catch(() => null);
        handle401(response, error);
        throwApiResponseError(response, error);
    }

    const blob = await response.blob();
    const objectUrl = URL.createObjectURL(blob);
    return {
        src: objectUrl,
        revoke: () => URL.revokeObjectURL(objectUrl)
    };
}

async function fetchAuthenticatedAssetSrc(
    resolvedUrl: string,
    headers: Record<string, string>
): Promise<{
    src: string;
    revoke?: () => void;
}> {
    applyDeviceScopeHeaders(headers);
    const response = await fetch(resolvedUrl, { headers });

    if (!response.ok) {
        const error = await response.json().catch(() => null);
        handle401(response, error);
        throwApiResponseError(response, error);
    }

    const blob = await response.blob();
    const objectUrl = URL.createObjectURL(blob);
    return {
        src: objectUrl,
        revoke: () => URL.revokeObjectURL(objectUrl)
    };
}

function isLocalApiAssetUrl(path: string): boolean {
    return path.startsWith(PIGTEX_API_BASE) || path.startsWith(PIGTEX_API_ROOT);
}

export async function resolveProtectedMediaSrc(path: string): Promise<{
    src: string;
    revoke?: () => void;
}> {
    const trimmed = path.trim();
    if (!trimmed || trimmed.startsWith('data:') || trimmed.startsWith('blob:')) {
        return { src: trimmed };
    }

    const resolvedUrl = resolveImageUrl(trimmed);
    if (isLocalApiAssetUrl(resolvedUrl)) {
        return await fetchAuthenticatedAssetSrc(resolvedUrl, {
            'Authorization': getBearerToken()
        });
    }

    const proxyHeaders: Record<string, string> = {
        'Authorization': getBearerToken()
    };
    applyProviderHeaders(proxyHeaders);
    const proxyUrl = `${PIGTEX_API_BASE}/v1/media/fetch?url=${encodeURIComponent(resolvedUrl)}`;
    return await fetchAuthenticatedAssetSrc(proxyUrl, proxyHeaders);
}

// ===== Auth Types =====
export interface User {
    id: string;
    email: string;
    username: string;
    plan: string;
    role?: string;
    is_admin?: boolean;
    permissions?: string[];
    is_active: boolean;
    created_at: string;
    last_login?: string | null;
    has_password?: boolean;
    oauth_provider?: string | null;
    avatar_url?: string | null;
}

export interface AuthResponse {
    access_token: string;
    token_type: string;
}

export type OAuthProvider = 'google' | 'github';

export interface OAuthProvidersResponse {
    google: boolean;
    github: boolean;
}

interface OAuthStartResponse {
    provider: OAuthProvider;
    auth_url: string;
    state: string;
    expires_in: number;
}

interface OAuthStatusPendingResponse {
    status: 'pending';
}

interface OAuthStatusSuccessResponse extends AuthResponse {
    status: 'success';
}

interface OAuthStatusErrorResponse {
    status: 'error';
    error: string;
}

type OAuthStatusResponse =
    | OAuthStatusPendingResponse
    | OAuthStatusSuccessResponse
    | OAuthStatusErrorResponse;

export interface UsageStats {
    total_requests: number;
    total_tokens: number;
    total_cost: number;
    period: string;
}

export interface UsageSummary {
    today: UsageStats;
    this_month: UsageStats;
}

export interface ChangePasswordPayload {
    currentPassword?: string;
    newPassword: string;
}

export interface ChangePasswordResponse {
    ok: boolean;
    message: string;
    has_password: boolean;
}

export interface DeleteAccountPayload {
    confirmation: string;
    password?: string;
}

export interface CloudQuota {
    plan_code: string;
    quota_bytes: number;
    retention_days: number;
    max_devices: number;
    max_snapshots: number;
    sync_enabled?: boolean;
    device_transfer_enabled?: boolean;
}

export interface SyncPlanOffer {
    plan_code: string;
    name: string;
    quota_bytes: number;
    retention_days: number;
    max_devices: number;
    max_snapshots: number;
    monthly_price_vnd: number;
    annual_price_vnd: number;
    sync_enabled: boolean;
    device_transfer_enabled: boolean;
    priority_level: number;
}

export interface SyncEntitlement {
    plan_code: string;
    plan_name: string;
    status: 'free' | 'active' | 'grace_period' | string;
    subscription_status: string;
    billing_cycle?: 'monthly' | 'annual' | string | null;
    quota_bytes: number;
    usage_bytes: number;
    retention_days: number;
    max_devices: number;
    max_snapshots: number;
    can_use_cloud_backup: boolean;
    can_use_device_transfer: boolean;
    can_use_sync: boolean;
    can_write_snapshots: boolean;
    can_restore_snapshots: boolean;
    priority_level: number;
    quota_source: string;
    cancel_at_period_end: boolean;
    current_period_start?: string | null;
    current_period_end?: string | null;
    grace_ends_at?: string | null;
    plans: SyncPlanOffer[];
}

export interface SkillFoundrySkill {
    skill_id: string;
    title?: string;
    domain?: string;
    score_total?: number;
    competition_status?: string;
    trigger_patterns?: string[];
    output_contract?: string[];
    instruction_core?: string | string[];
    [key: string]: unknown;
}

export interface SkillFoundryPublishGate {
    ready: boolean;
    blockers: string[];
    warnings: string[];
    runtime_empty?: boolean;
    draft_skill_count?: number;
    challenger_count?: number;
    rejected_count?: number;
    average_score?: number | null;
    active_threshold?: number;
    challenger_threshold?: number;
}

export interface SkillFoundryArtifactMove {
    source_path: string;
    destination_path: string;
    status: 'accepted' | 'rejected' | string;
}

export interface SkillFoundryArtifactRetentionSummary {
    enabled: boolean;
    moved_count: number;
    accepted_artifact_count: number;
    rejected_artifact_count: number;
    sample_moved_items?: SkillFoundryArtifactMove[];
}

export interface SkillFoundryCatalogReport {
    generated_at?: string | null;
    summary?: Record<string, unknown>;
    source_path?: string;
    report_path?: string;
    report_id?: string;
    artifact_retention?: SkillFoundryArtifactRetentionSummary;
}

export interface SkillFoundryRegistryPayload {
    schema_version: string;
    generated_at?: string | null;
    active_skills: SkillFoundrySkill[];
    summary?: Record<string, unknown>;
    report_id?: string;
    release_id?: string;
    released_at?: string;
    released_by?: string;
    note?: string | null;
    state?: string;
    publish_gate?: SkillFoundryPublishGate;
}

export interface SkillFoundryCatalogPayload {
    schema_version: string;
    generated_at?: string | null;
    challengers: SkillFoundrySkill[];
    rejected: SkillFoundrySkill[];
    reports: SkillFoundryCatalogReport[];
    draft_summary?: Record<string, unknown>;
    draft_report_id?: string;
}

export interface SkillFoundryRelease {
    release_id: string;
    released_at?: string | null;
    released_by?: string | null;
    note?: string | null;
    active_skill_count: number;
    path?: string;
}

export interface SkillFoundryAuditEvent {
    id: string;
    action: string;
    resource_id?: string | null;
    status: string;
    summary?: string | null;
    created_at?: string | null;
    actor_user_id: string;
    before_json?: string | null;
    after_json?: string | null;
    metadata_json?: string | null;
}

export interface SkillFoundrySummary {
    active_skill_count: number;
    draft_skill_count: number;
    challenger_count: number;
    rejected_count: number;
    generated_at?: string | null;
    draft_generated_at?: string | null;
    registry_path: string;
    draft_registry_path: string;
    incoming_path: string;
    release_count: number;
    publish_gate?: SkillFoundryPublishGate;
}

export interface SkillFoundryOverview {
    summary: SkillFoundrySummary;
    active_registry: SkillFoundryRegistryPayload;
    draft_registry: SkillFoundryRegistryPayload;
    catalog: SkillFoundryCatalogPayload;
    releases: SkillFoundryRelease[];
    publish_gate?: SkillFoundryPublishGate;
}

export interface RegisterCloudDevicePayload {
    deviceKey: string;
    deviceName: string;
    platform: string;
    appVersion?: string | null;
}

export interface RegisterCloudDeviceResponse {
    device_id: string;
    quota: CloudQuota;
}

export interface CloudUsageSummary {
    plan_code: string;
    quota_bytes: number;
    usage_bytes: number;
    snapshot_count: number;
    retention_days: number;
    max_devices: number;
    max_snapshots: number;
    sync_enabled?: boolean;
    device_transfer_enabled?: boolean;
}

export interface CloudBackupListItem {
    snapshot_id: string;
    device_id: string;
    device_name: string;
    scope_type: string;
    snapshot_kind: string;
    status: string;
    payload_size_bytes: number;
    created_at?: string | null;
}

export interface CloudBackupListResponse {
    items: CloudBackupListItem[];
}

export interface CreateLocalCloudBackupPayload {
    deviceId: string;
    scopeType?: string;
    scopeId?: string | null;
    snapshotKind?: string;
}

export interface CreateLocalCloudBackupResponse {
    ok: boolean;
    snapshot_id: string;
    status: string;
    counts: Record<string, number>;
}

export interface ApplyLocalCloudRestorePayload {
    snapshotId: string;
    merge?: boolean;
}

export interface ApplyLocalCloudRestoreResponse {
    ok: boolean;
    snapshot_id: string;
    stats: Record<string, number>;
}

export interface CreateSyncCheckoutSessionPayload {
    planCode: string;
    billingCycle: 'monthly' | 'annual';
    successUrl?: string | null;
    cancelUrl?: string | null;
}

export interface CreateSyncCheckoutSessionResponse {
    session_id: string;
    checkout_url?: string | null;
    mode: string;
}

export interface CreateSyncPortalSessionPayload {
    returnUrl?: string | null;
}

export interface CreateSyncPortalSessionResponse {
    session_url?: string | null;
    mode: string;
}

export interface CancelSyncSubscriptionPayload {
    immediately?: boolean;
}

export interface CancelSyncSubscriptionResponse {
    ok: boolean;
    status: string;
    cancel_at_period_end: boolean;
    current_period_end?: string | null;
    grace_ends_at?: string | null;
}

export interface CloudSyncState {
    device_id: string;
    auto_sync_enabled: boolean;
    sync_enabled: boolean;
    status: string;
    can_push: boolean;
    can_pull: boolean;
    local_updated_at?: string | null;
    last_sync_push_at?: string | null;
    last_sync_pull_at?: string | null;
    latest_device_snapshot_id?: string | null;
    latest_device_snapshot_at?: string | null;
    latest_remote_snapshot_id?: string | null;
    latest_remote_snapshot_at?: string | null;
}

export interface CloudSyncActionPayload {
    deviceId: string;
}

export interface CloudSyncPushResponse {
    ok: boolean;
    snapshot_id: string;
    status: string;
    counts: Record<string, number>;
}

export interface CloudSyncPullResponse {
    ok: boolean;
    snapshot_id: string;
    stats: Record<string, number>;
}

// ===== Chat Types =====
export interface ChatMessage {
    role: 'user' | 'assistant' | 'system';
    content: string;
}

export interface ChatCompletionResponse {
    id: string;
    object: string;
    created: number;
    model: string;
    choices: {
        index: number;
        message: ChatMessage;
        finish_reason: string;
    }[];
    usage?: {
        prompt_tokens: number;
        completion_tokens: number;
        total_tokens: number;
    };
}

export interface StreamDelta {
    id: string;
    object: string;
    created: number;
    model: string;
    choices: {
        index: number;
        delta: { role?: string; content?: string };
        finish_reason: string | null;
    }[];
}

// ===== Token Management =====
const TOKEN_KEY = 'pigtex_auth_token';
const LEGACY_TOKEN_KEY = 'access_token';

function getSessionStorage(): Storage | null {
    if (typeof window === 'undefined') return null;
    try {
        return window.sessionStorage;
    } catch {
        return null;
    }
}

function getLocalStorage(): Storage | null {
    if (typeof window === 'undefined') return null;
    try {
        return window.localStorage;
    } catch {
        return null;
    }
}

function clearLegacyTokenStorage(): void {
    const local = getLocalStorage();
    if (!local) return;
    local.removeItem(TOKEN_KEY);
    local.removeItem(LEGACY_TOKEN_KEY);
}

function hasSecureAuthTokenStorage(): boolean {
    if (typeof window === 'undefined') return false;
    try {
        const electronApi = window.electronAPI;
        return Boolean(
            electronApi
            && typeof electronApi.isSecureStorageAvailable === 'function'
            && electronApi.isSecureStorageAvailable()
            && typeof electronApi.getSecureAuthToken === 'function'
            && typeof electronApi.setSecureAuthToken === 'function'
            && typeof electronApi.clearSecureAuthToken === 'function'
        );
    } catch {
        return false;
    }
}

function getSecureAuthToken(): string | null {
    if (!hasSecureAuthTokenStorage()) return null;
    try {
        const token = window.electronAPI?.getSecureAuthToken?.();
        return typeof token === 'string' && token.trim() ? token.trim() : null;
    } catch {
        return null;
    }
}

function setSecureAuthToken(token: string): void {
    if (!hasSecureAuthTokenStorage()) return;
    try {
        window.electronAPI?.setSecureAuthToken?.(token);
    } catch {
        // Keep the in-memory/session auth alive even if secure persistence fails.
    }
}

function clearSecureAuthToken(): void {
    if (!hasSecureAuthTokenStorage()) return;
    try {
        window.electronAPI?.clearSecureAuthToken?.();
    } catch {
        // Session cleanup still proceeds below.
    }
}

function setSessionAuthToken(token: string): void {
    const session = getSessionStorage();
    if (!session) return;
    session.setItem(TOKEN_KEY, token);
    session.setItem(LEGACY_TOKEN_KEY, token);
}

export function getAuthToken(): string | null {
    const session = getSessionStorage();
    const sessionToken = session?.getItem(TOKEN_KEY) || session?.getItem(LEGACY_TOKEN_KEY);
    if (sessionToken) {
        return sessionToken;
    }

    const secureToken = getSecureAuthToken();
    if (secureToken) {
        setSessionAuthToken(secureToken);
        clearLegacyTokenStorage();
        return secureToken;
    }

    // One-time migration for older builds that persisted auth tokens in localStorage.
    const local = getLocalStorage();
    const legacyToken = local?.getItem(TOKEN_KEY) || local?.getItem(LEGACY_TOKEN_KEY);
    if (legacyToken) {
        setSessionAuthToken(legacyToken);
        setSecureAuthToken(legacyToken);
    }
    if (legacyToken) {
        clearLegacyTokenStorage();
    }
    return legacyToken || null;
}

export function setAuthToken(token: string): void {
    const normalizedToken = token.trim();
    if (!normalizedToken) {
        removeAuthToken();
        return;
    }
    setSessionAuthToken(normalizedToken);
    setSecureAuthToken(normalizedToken);
    clearLegacyTokenStorage();
}

export function removeAuthToken(): void {
    const session = getSessionStorage();
    session?.removeItem(TOKEN_KEY);
    session?.removeItem(LEGACY_TOKEN_KEY);
    clearSecureAuthToken();
    clearLegacyTokenStorage();
}

export function isAuthenticated(): boolean {
    return !!getAuthToken();
}

// ===== 401 Auto-Logout Helper =====
function isSessionAuthFailure(response: Response, errorBody: unknown): boolean {
    if (response.status !== 401) return false;

    const collectText = (value: unknown): string => {
        if (typeof value === 'string') return value;
        if (typeof value === 'number' || typeof value === 'boolean') return String(value);
        if (Array.isArray(value)) return value.map(collectText).join(' ');
        if (isJsonRecord(value)) {
            return Object.values(value).map(collectText).join(' ');
        }
        return '';
    };

    const detail = isJsonRecord(errorBody) ? errorBody.detail : undefined;
    const errorCode = isJsonRecord(detail) && typeof detail.error === 'string'
        ? detail.error.trim().toLowerCase()
        : '';
    const message = collectText(errorBody).toLowerCase();
    const detailMessage = (() => {
        if (typeof detail === 'string') return detail.toLowerCase();
        if (isJsonRecord(detail) && typeof detail.message === 'string') {
            return detail.message.toLowerCase();
        }
        return '';
    })();
    const wwwAuthenticate = (response.headers.get('WWW-Authenticate') || '').toLowerCase();

    if (
        errorCode === 'texapi_error' ||
        errorCode === 'upstream_api_error' ||
        errorCode === 'api_credentials_required' ||
        errorCode === 'api_connection_error' ||
        errorCode === 'provider_key_mismatch'
    ) {
        return false;
    }

    // Upstream credential failures must not force user logout.
    if (
        message.includes('incorrect api key') ||
        message.includes('invalid_api_key') ||
        message.includes('api key') ||
        message.includes('x-api-key') ||
        message.includes('x-api-provider')
    ) {
        return false;
    }

    if (
        detailMessage.includes('could not validate credentials') ||
        detailMessage.includes('not authenticated') ||
        detailMessage.includes('invalid authentication credentials')
    ) {
        return true;
    }

    if (
        message.includes('could not validate credentials') ||
        message.includes('not authenticated') ||
        message.includes('invalid authentication credentials')
    ) {
        return true;
    }

    // FastAPI auth dependency typically returns this on JWT/session failures.
    if (wwwAuthenticate.includes('bearer')) {
        return true;
    }

    return false;
}

function handle401(response: Response, errorBody?: unknown): void {
    if (!isSessionAuthFailure(response, errorBody)) {
        return;
    }
    removeAuthToken();
    window.location.reload();
}

function getBearerToken(): string {
    return `Bearer ${getAuthToken() || ''}`;
}

function normalizeBaseUrl(baseUrl: string): string {
    return baseUrl.trim().replace(/\/+$/, '');
}

function resolveUpstreamProvider(
    providerMode: ApiProviderId,
    customEndpoint: ApiEndpointProviderId,
    baseUrl: string,
    apiKey: string
): ApiEndpointProviderId {
    void baseUrl;
    void apiKey;
    // Endpoint selection is authoritative: only one active protocol at a time.
    return resolveApiProviderForRequest(providerMode, customEndpoint);
}

function inferProviderFromApiKeyPrefix(apiKey: string): ApiEndpointProviderId | null {
    const trimmed = apiKey.trim();
    if (!trimmed) return null;
    if (trimmed.startsWith('sk-ant-')) return 'anthropic';
    if (trimmed.startsWith('AIza')) return 'gemini';
    if (trimmed.startsWith('dashscope_') || trimmed.startsWith('ali-')) return 'alibaba';
    return null;
}

function buildProviderKeyMismatchMessage(provider: ApiEndpointProviderId, apiKey: string): string | null {
    const hintedProvider = inferProviderFromApiKeyPrefix(apiKey);
    if (!hintedProvider || hintedProvider === provider) {
        return null;
    }
    return `API key hiện tại thuộc ${hintedProvider.toUpperCase()} nhưng Endpoint protocol đang là ${provider.toUpperCase()}.`;
}

function applyProviderHeaders(
    headers: Record<string, string>,
    overrides?: { apiKey?: string; baseUrl?: string; provider?: ApiProviderId; customEndpoint?: ApiEndpointProviderId }
): void {
    const resolved = resolveProviderCredentials(overrides);
    headers['X-API-Provider'] = resolved.provider;
    if (resolved.baseUrl) {
        headers['X-API-Base-URL'] = resolved.baseUrl;
    }
    if (!resolved.apiKey) {
        // No BYOK key: still forward the selected base URL so the backend can
        // resolve managed providers without polluting the direct-provider flow.
        return;
    }
    headers['X-API-Key'] = resolved.apiKey;
}

function resolveProviderCredentials(
    overrides?: { apiKey?: string; baseUrl?: string; provider?: ApiProviderId; customEndpoint?: ApiEndpointProviderId }
): { apiKey: string; baseUrl: string; provider: ApiEndpointProviderId } {
    const settings = getPigTexSettings();
    const providerMode = overrides?.provider ?? settings.apiProvider;
    const customEndpoint = overrides?.customEndpoint ?? settings.customEndpoint;
    let apiKey = (overrides?.apiKey ?? settings.apiKey).trim();
    if (!apiKey) {
        const profiles = settings.providerCredentialProfiles;
        const endpointProfileKey = (profiles?.[customEndpoint]?.apiKey || '').trim();
        const autoProfileKey = (profiles?.auto?.apiKey || '').trim();
        apiKey = endpointProfileKey || autoProfileKey;
    }
    const baseUrl = resolveUpstreamBaseUrlForEnvironment(overrides?.baseUrl ?? settings.baseUrl, IS_PRODUCTION_BUILD);
    const provider = resolveUpstreamProvider(providerMode, customEndpoint, baseUrl, apiKey);

    const autoDefaultBaseUrl = normalizeBaseUrl(getProviderDefaultBaseUrl('auto'));
    let resolvedBaseUrl = baseUrl;
    if (!resolvedBaseUrl) {
        resolvedBaseUrl = normalizeBaseUrl(getProviderDefaultBaseUrl(provider));
    }
    if (provider !== 'openai' && resolvedBaseUrl === autoDefaultBaseUrl) {
        resolvedBaseUrl = normalizeBaseUrl(getProviderDefaultBaseUrl(provider));
    }

    if (!apiKey) {
        return {
            apiKey: '',
            baseUrl: resolvedBaseUrl,
            provider,
        };
    }

    return {
        apiKey,
        baseUrl: resolvedBaseUrl,
        provider,
    };
}

function extractErrorDetail(errorBody: unknown, statusCode: number): string {
    if (typeof errorBody === 'object' && errorBody !== null) {
        const maybeError = errorBody as {
            detail?: string | { message?: string };
            message?: string;
            error?: { message?: string };
        };

        if (typeof maybeError.detail === 'string' && maybeError.detail.trim()) {
            return maybeError.detail;
        }
        if (typeof maybeError.detail === 'object' && maybeError.detail !== null) {
            if (typeof maybeError.detail.message === 'string' && maybeError.detail.message.trim()) {
                return maybeError.detail.message;
            }
        }
        if (typeof maybeError.message === 'string' && maybeError.message.trim()) {
            return maybeError.message;
        }
        if (typeof maybeError.error === 'object' && maybeError.error !== null) {
            if (typeof maybeError.error.message === 'string' && maybeError.error.message.trim()) {
                return maybeError.error.message;
            }
        }
    }

    return `HTTP ${statusCode}`;
}

type JsonRecord = Record<string, unknown>;

function isJsonRecord(value: unknown): value is JsonRecord {
    return typeof value === 'object' && value !== null;
}

function extractErrorCode(errorBody: unknown): string | undefined {
    if (!isJsonRecord(errorBody)) return undefined;

    const detail = isJsonRecord(errorBody.detail) ? errorBody.detail : null;
    if (detail && typeof detail.error === 'string' && detail.error.trim()) {
        return detail.error.trim();
    }

    const error = isJsonRecord(errorBody.error) ? errorBody.error : null;
    if (error) {
        if (typeof error.code === 'string' && error.code.trim()) {
            return error.code.trim();
        }
        if (typeof error.type === 'string' && error.type.trim()) {
            return error.type.trim();
        }
    }

    return undefined;
}

function extractRequestId(errorBody: unknown, response?: Response): string | undefined {
    const headerRequestId = response?.headers.get('X-Request-ID') || response?.headers.get('X-Request-Id');
    if (headerRequestId && headerRequestId.trim()) {
        return headerRequestId.trim();
    }

    if (!isJsonRecord(errorBody)) return undefined;

    if (typeof errorBody.request_id === 'string' && errorBody.request_id.trim()) {
        return errorBody.request_id.trim();
    }

    const detail = isJsonRecord(errorBody.detail) ? errorBody.detail : null;
    if (detail && typeof detail.request_id === 'string' && detail.request_id.trim()) {
        return detail.request_id.trim();
    }

    const error = isJsonRecord(errorBody.error) ? errorBody.error : null;
    if (error && typeof error.request_id === 'string' && error.request_id.trim()) {
        return error.request_id.trim();
    }

    return undefined;
}

function formatApiErrorMessage(
    errorBody: unknown,
    statusCode: number,
    response?: Response
): string {
    const message = extractErrorDetail(errorBody, statusCode);
    const errorCode = extractErrorCode(errorBody);
    const requestId = extractRequestId(errorBody, response);
    const diagnostics: string[] = [];

    if (errorCode) {
        diagnostics.push(`code: ${errorCode}`);
    }
    if (requestId && (Boolean(errorCode) || statusCode >= 500)) {
        diagnostics.push(`request_id: ${requestId}`);
    }

    if (diagnostics.length === 0) {
        return message;
    }

    return `${message} [${diagnostics.join(', ')}]`;
}

function throwApiResponseError(response: Response, errorBody: unknown): never {
    throw new Error(formatApiErrorMessage(errorBody, response.status, response));
}

function extractTextFromContentPayload(content: unknown): string {
    if (typeof content === 'string') {
        return content;
    }

    if (Array.isArray(content)) {
        return content.map(item => extractTextFromContentPayload(item)).join('');
    }

    if (isJsonRecord(content)) {
        if (typeof content.text === 'string') {
            return content.text;
        }
        if (isJsonRecord(content.text) && typeof content.text.value === 'string') {
            return content.text.value;
        }
        if (typeof content.delta === 'string') {
            return content.delta;
        }
        if (typeof content.output_text === 'string') {
            return content.output_text;
        }
        if (typeof content.value === 'string') {
            return content.value;
        }

        for (const key of ['content', 'parts', 'part', 'item', 'message', 'output']) {
            const nested = extractTextFromContentPayload(content[key]);
            if (nested) return nested;
        }

        const candidates = content.candidates;
        if (Array.isArray(candidates)) {
            for (const candidate of candidates) {
                const nested = extractTextFromContentPayload(candidate);
                if (nested) return nested;
            }
        }

        const responsePayload = content.response;
        if (isJsonRecord(responsePayload)) {
            const nested = extractTextFromContentPayload(responsePayload);
            if (nested) return nested;
        }
    }

    return '';
}

function extractToolCallsFromPayload(payload: unknown): Array<{ name: string; args?: string }> {
    if (!isJsonRecord(payload)) return [];
    const payloadType = typeof payload.type === 'string' ? payload.type.trim().toLowerCase() : '';

    if (payloadType === 'content_block_start') {
        const block = isJsonRecord(payload.content_block) ? payload.content_block : null;
        const blockType = typeof block?.type === 'string' ? block.type.trim().toLowerCase() : '';
        if (blockType === 'tool_use') {
            const name = typeof block?.name === 'string' ? block.name : '<unknown_tool>';
            const rawInput = block?.input;
            let args: string | undefined;
            if (typeof rawInput === 'string') {
                args = rawInput;
            } else if (rawInput !== undefined) {
                try {
                    args = JSON.stringify(rawInput);
                } catch {
                    args = undefined;
                }
            }
            return [{ name, args }];
        }
    }

    if (payloadType === 'response.output_item.added' || payloadType === 'response.output_item.done') {
        const item = isJsonRecord(payload.item) ? payload.item : null;
        const itemType = typeof item?.type === 'string' ? item.type.trim().toLowerCase() : '';
        if (itemType === 'function_call' || itemType === 'tool_call') {
            const name = typeof item?.name === 'string' ? item.name : '<unknown_tool>';
            const rawArgs = item?.arguments;
            let args: string | undefined;
            if (typeof rawArgs === 'string') {
                args = rawArgs;
            } else if (rawArgs !== undefined) {
                try {
                    args = JSON.stringify(rawArgs);
                } catch {
                    args = undefined;
                }
            }
            return [{ name, args }];
        }
    }

    if (payloadType === 'response.function_call_arguments.delta' || payloadType === 'response.tool_call_arguments.delta') {
        const name = typeof payload.name === 'string' ? payload.name : '<unknown_tool>';
        const args = typeof payload.delta === 'string' ? payload.delta : undefined;
        return [{ name, args }];
    }

    const choices = Array.isArray(payload.choices) ? payload.choices : [];
    const firstChoice = choices.length > 0 && isJsonRecord(choices[0]) ? choices[0] : null;
    if (!firstChoice) return [];

    const collect = (container: unknown): Array<{ name: string; args?: string }> => {
        if (!isJsonRecord(container) || !Array.isArray(container.tool_calls)) {
            return [];
        }
        return container.tool_calls
            .filter(isJsonRecord)
            .map(tc => {
                const fn = isJsonRecord(tc.function) ? tc.function : null;
                const name = typeof fn?.name === 'string' ? fn.name : '<unknown_tool>';
                const args = typeof fn?.arguments === 'string' ? fn.arguments : undefined;
                return { name, args };
            });
    };

    const deltaCalls = collect(firstChoice.delta);
    if (deltaCalls.length > 0) return deltaCalls;
    return collect(firstChoice.message);
}

function toolCallsToText(payload: unknown): string {
    const toolCalls = extractToolCallsFromPayload(payload);
    if (toolCalls.length === 0) return '';
    const lines = ['Model requested tool call(s):'];
    for (const tc of toolCalls.slice(0, 6)) {
        const args = (tc.args || '').trim();
        const argPreview = args.length > 220 ? `${args.slice(0, 220)} ...` : args;
        lines.push(argPreview ? `- ${tc.name}(${argPreview})` : `- ${tc.name}()`);
    }
    return lines.join('\n');
}

function extractCompletionText(payload: unknown): string {
    if (!isJsonRecord(payload)) return '';
    const payloadType = typeof payload.type === 'string' ? payload.type.trim().toLowerCase() : '';

    if (payloadType === 'content_block_delta') {
        const delta = isJsonRecord(payload.delta) ? payload.delta : null;
        if (delta) {
            if (typeof delta.text === 'string') return delta.text;
        }
        if (typeof payload.delta === 'string') return payload.delta;
    }

    if (payloadType === 'content_block_start') {
        const blockText = extractTextFromContentPayload(payload.content_block);
        if (blockText) return blockText;
    }

    if (payloadType === 'response.text.delta' || payloadType === 'response.output_text.delta') {
        if (typeof payload.delta === 'string') return payload.delta;
    }

    if (payloadType === 'response.text.done' || payloadType === 'response.output_text.done') {
        if (typeof payload.text === 'string') return payload.text;
        if (typeof payload.output_text === 'string') return payload.output_text;
        if (typeof payload.delta === 'string') return payload.delta;
    }

    if (payloadType === 'response.refusal.delta') {
        if (typeof payload.delta === 'string') return payload.delta;
    }

    if (
        payloadType === 'response.content_part.added' ||
        payloadType === 'response.content_part.done' ||
        payloadType === 'response.output_item.added' ||
        payloadType === 'response.output_item.done'
    ) {
        const partText = extractTextFromContentPayload(payload.part);
        if (partText) return partText;
        const itemText = extractTextFromContentPayload(payload.item);
        if (itemText) return itemText;
    }

    const choices = Array.isArray(payload.choices) ? payload.choices : [];
    const firstChoice = choices.length > 0 && isJsonRecord(choices[0]) ? choices[0] : null;

    if (firstChoice) {
        const delta = isJsonRecord(firstChoice.delta) ? firstChoice.delta : null;
        if (delta) {
            const deltaText = extractTextFromContentPayload(delta.content);
            if (deltaText) return deltaText;
            if (typeof delta.text === 'string') return delta.text;
        }

        const message = isJsonRecord(firstChoice.message) ? firstChoice.message : null;
        if (message) {
            const msgText = extractTextFromContentPayload(message.content);
            if (msgText) return msgText;
        }

        if (typeof firstChoice.text === 'string') return firstChoice.text;
    }

    const rootMessage = isJsonRecord(payload.message) ? payload.message : null;
    if (rootMessage) {
        const rootText = extractTextFromContentPayload(rootMessage.content);
        if (rootText) return rootText;
    }

    const directContent = extractTextFromContentPayload(payload.content);
    if (directContent) return directContent;
    const candidatesText = extractTextFromContentPayload(payload.candidates);
    if (candidatesText) return candidatesText;
    if (typeof payload.output_text === 'string') return payload.output_text;
    if (typeof payload.response === 'string') return payload.response;
    if (typeof payload.token === 'string') return payload.token;
    if (typeof payload.delta === 'string') return payload.delta;

    const toolText = toolCallsToText(payload);
    if (toolText) return toolText;

    return '';
}

function parseStreamPayloadFromEvent(eventBlock: string): JsonRecord | null {
    const trimmedEvent = eventBlock.trim();
    if (!trimmedEvent || trimmedEvent.startsWith(':')) return null;

    const dataLines = trimmedEvent
        .split('\n')
        .map(line => line.trim())
        .filter(line => line.startsWith('data:'))
        .map(line => line.slice('data:'.length).trim());

    if (dataLines.length === 0) return null;
    const payloadText = dataLines.join('\n').trim();

    if (
        !payloadText ||
        payloadText === '[DONE]' ||
        (!payloadText.startsWith('{') && !payloadText.startsWith('['))
    ) {
        return null;
    }

    try {
        const payload = JSON.parse(payloadText);
        if (isJsonRecord(payload)) {
            return payload;
        }

        // Some upstreams emit JSON arrays per SSE event (e.g. Gemini-compatible proxies).
        // Normalize into one synthetic payload so the existing extractor pipeline can run.
        if (Array.isArray(payload)) {
            const records = payload.filter(isJsonRecord);
            if (records.length === 1) {
                return records[0];
            }
            if (records.length > 1) {
                const combinedContent = records
                    .map(item => extractCompletionText(item))
                    .join('');
                const firstConversationId = records
                    .map(item => item.conversation_id)
                    .find((value): value is string => typeof value === 'string' && value.trim().length > 0);
                const firstError = records
                    .map(item => item.error)
                    .find(value => value !== undefined);
                const anyDone = records.some(item => isStreamFinishedPayload(item));
                const synthetic: JsonRecord = {};
                if (combinedContent) synthetic.content = combinedContent;
                if (firstConversationId) synthetic.conversation_id = firstConversationId;
                if (firstError !== undefined) synthetic.error = firstError;
                if (anyDone) synthetic.done = true;
                return synthetic;
            }
        }
        return null;
    } catch {
        return null;
    }
}

function isDoneSseEvent(eventBlock: string): boolean {
    const trimmedEvent = eventBlock.trim();
    if (!trimmedEvent || trimmedEvent.startsWith(':')) return false;

    const dataLines = trimmedEvent
        .split('\n')
        .map(line => line.trim())
        .filter(line => line.startsWith('data:'))
        .map(line => line.slice('data:'.length).trim());

    if (dataLines.length === 0) return false;
    return dataLines.join('\n').trim() === '[DONE]';
}

function splitSseEvents(buffer: string): { events: string[]; rest: string } {
    const normalized = buffer.replace(/\r\n/g, '\n');
    const parts = normalized.split('\n\n');
    const rest = parts.pop() ?? '';
    return { events: parts, rest };
}

function extractConversationIdFromPayload(payload: JsonRecord): string | undefined {
    const direct = payload.conversation_id;
    if (typeof direct === 'string' && direct.trim()) {
        return direct.trim();
    }
    return undefined;
}

function extractCitationsFromPayload(payload: JsonRecord): WebCitation[] | undefined {
    const rawCitations = payload.citations;
    if (!Array.isArray(rawCitations)) return undefined;

    const citations = rawCitations
        .map((item, index) => {
            if (!isJsonRecord(item)) return null;
            const url = typeof item.url === 'string' ? item.url.trim() : '';
            const title = typeof item.title === 'string' ? item.title.trim() : '';
            if (!url || !title) return null;
            const rawIndex = typeof item.index === 'number' ? item.index : index + 1;
            return {
                index: Number.isFinite(rawIndex) ? rawIndex : index + 1,
                title,
                url,
                domain: typeof item.domain === 'string' ? item.domain : undefined,
                published_at: typeof item.published_at === 'string' ? item.published_at : undefined,
                snippet: typeof item.snippet === 'string' ? item.snippet : undefined,
                source_provider: typeof item.source_provider === 'string' ? item.source_provider : undefined,
                relevance_score: typeof item.relevance_score === 'number' ? item.relevance_score : undefined,
                credibility_score: typeof item.credibility_score === 'number' ? item.credibility_score : undefined,
                recency_score: typeof item.recency_score === 'number' ? item.recency_score : undefined,
            } as WebCitation;
        })
        .filter((item): item is WebCitation => item !== null);

    return citations.length > 0 ? citations : undefined;
}

function extractWebSearchMetadataFromPayload(payload: JsonRecord): WebSearchMetadata | undefined {
    const raw = payload.web_search;
    if (!isJsonRecord(raw)) return undefined;

    const statusRaw = typeof raw.status === 'string' ? raw.status.trim().toLowerCase() : '';
    const normalizedStatus: WebSearchMetadata['status'] =
        statusRaw === 'running'
            ? 'running'
            : statusRaw === 'timeout'
                ? 'timeout'
            : statusRaw === 'complete'
                ? 'complete'
                : statusRaw === 'disabled'
                    ? 'disabled'
                    : statusRaw === 'error'
                        ? 'error'
                        : statusRaw === 'skipped'
                            ? 'skipped'
                            : 'skipped';

    const searchQueries = Array.isArray(raw.search_queries)
        ? raw.search_queries.filter((query): query is string => typeof query === 'string' && query.trim().length > 0)
        : undefined;

    const modeRaw = typeof raw.mode === 'string' ? raw.mode.trim().toLowerCase() : '';
    const normalizedMode: WebSearchMetadata['mode'] | undefined =
        modeRaw === 'realtime' || modeRaw === 'fast'
            ? 'fast'
            : modeRaw === 'deep_verify' || modeRaw === 'verify' || modeRaw === 'deep' || modeRaw === 'url_read'
                ? 'deep'
                : modeRaw === 'auto'
                    ? 'auto'
                    : undefined;

    const warnings = Array.isArray(raw.warnings)
        ? raw.warnings.filter((item): item is string => typeof item === 'string' && item.trim().length > 0)
        : undefined;

    const claimVerification = Array.isArray(raw.claim_verification)
        ? raw.claim_verification
            .filter(isJsonRecord)
            .map((item) => {
                const verdictRaw = typeof item.verdict === 'string' ? item.verdict.trim().toLowerCase() : '';
                const verdict: WebSearchClaimVerification['verdict'] =
                    verdictRaw === 'supported'
                        ? 'supported'
                        : verdictRaw === 'contradicted'
                            ? 'contradicted'
                            : verdictRaw === 'mixed'
                                ? 'mixed'
                                : 'insufficient';
                return {
                    claim: typeof item.claim === 'string' ? item.claim : '',
                    verdict,
                    confidence: typeof item.confidence === 'number' ? item.confidence : 0,
                    evidence_count: typeof item.evidence_count === 'number' ? item.evidence_count : undefined,
                    supporting_sources: Array.isArray(item.supporting_sources)
                        ? item.supporting_sources.filter((index): index is number => typeof index === 'number')
                        : undefined,
                    contradicting_sources: Array.isArray(item.contradicting_sources)
                        ? item.contradicting_sources.filter((index): index is number => typeof index === 'number')
                        : undefined,
                    summary: typeof item.summary === 'string' ? item.summary : undefined,
                } as WebSearchClaimVerification;
            })
            .filter((item) => item.claim.trim().length > 0)
        : undefined;

    return {
        enabled: Boolean(raw.enabled),
        status: normalizedStatus,
        mode: normalizedMode,
        search_intent: typeof raw.search_intent === 'string' ? raw.search_intent : undefined,
        search_queries: searchQueries,
        total_search_time_ms: typeof raw.total_search_time_ms === 'number' ? raw.total_search_time_ms : undefined,
        raw_results_count: typeof raw.raw_results_count === 'number' ? raw.raw_results_count : undefined,
        checked_at_utc: typeof raw.checked_at_utc === 'string' ? raw.checked_at_utc : undefined,
        confidence_score: typeof raw.confidence_score === 'number' ? raw.confidence_score : undefined,
        conflicts_count: typeof raw.conflicts_count === 'number' ? raw.conflicts_count : undefined,
        claims_verified_count: typeof raw.claims_verified_count === 'number' ? raw.claims_verified_count : undefined,
        warnings,
        claim_verification: claimVerification,
    };
}

function extractMemoryContextMetadataFromPayload(payload: JsonRecord): MemoryContextMetadata | undefined {
    const raw = payload.memory;
    if (!isJsonRecord(raw)) return undefined;

    const rawSources = Array.isArray(raw.sources)
        ? raw.sources.filter(isJsonRecord)
        : [];

    const sources = rawSources
        .map((item, idx) => {
            const id = typeof item.id === 'string' ? item.id.trim() : '';
            if (!id) return null;
            const rawIndex = typeof item.index === 'number' ? item.index : idx + 1;
            return {
                index: Number.isFinite(rawIndex) ? rawIndex : idx + 1,
                id,
                title: typeof item.title === 'string' ? item.title : undefined,
                type: typeof item.type === 'string' ? item.type : undefined,
            } as MemoryContextSource;
        })
        .filter((item): item is MemoryContextSource => item !== null);

    return {
        enabled: Boolean(raw.enabled),
        use_knowledge: typeof raw.use_knowledge === 'boolean' ? raw.use_knowledge : undefined,
        use_facts: typeof raw.use_facts === 'boolean' ? raw.use_facts : undefined,
        use_history: typeof raw.use_history === 'boolean' ? raw.use_history : undefined,
        context_tokens: typeof raw.context_tokens === 'number' ? raw.context_tokens : undefined,
        knowledge_hits: typeof raw.knowledge_hits === 'number' ? raw.knowledge_hits : undefined,
        history_messages_used: typeof raw.history_messages_used === 'number' ? raw.history_messages_used : undefined,
        facts_used: typeof raw.facts_used === 'number' ? raw.facts_used : undefined,
        preference_facts_used: typeof raw.preference_facts_used === 'number' ? raw.preference_facts_used : undefined,
        system_facts_used: typeof raw.system_facts_used === 'number' ? raw.system_facts_used : undefined,
        workspace_facts_used: typeof raw.workspace_facts_used === 'number' ? raw.workspace_facts_used : undefined,
        sources: sources.length > 0 ? sources : undefined,
    };
}

function extractUsageFromPayload(payload: JsonRecord): StreamUsageMetadata | undefined {
    const rawUsage = isJsonRecord(payload.usage) ? payload.usage : null;
    if (!rawUsage) return undefined;

    const parseNumber = (value: unknown): number | undefined =>
        typeof value === 'number' && Number.isFinite(value) ? value : undefined;

    const promptTokens = parseNumber(rawUsage.prompt_tokens) ?? parseNumber(rawUsage.input_tokens) ?? 0;
    const completionTokens = parseNumber(rawUsage.completion_tokens) ?? parseNumber(rawUsage.output_tokens) ?? 0;
    const totalTokens = parseNumber(rawUsage.total_tokens)
        ?? parseNumber(rawUsage.tokens)
        ?? Math.max(0, promptTokens + completionTokens);
    const costUsd = parseNumber(rawUsage.cost_usd)
        ?? parseNumber(rawUsage.cost)
        ?? parseNumber(rawUsage.total_cost);
    const estimated = typeof rawUsage.estimated === 'boolean'
        ? rawUsage.estimated
        : undefined;

    if (promptTokens <= 0 && completionTokens <= 0 && totalTokens <= 0 && costUsd === undefined) {
        return undefined;
    }

    return {
        prompt_tokens: promptTokens,
        completion_tokens: completionTokens,
        total_tokens: totalTokens,
        cost_usd: costUsd,
        estimated,
    };
}

function extractStreamError(payload: JsonRecord): string | null {
    let message = '';

    const payloadType = typeof payload.type === 'string' ? payload.type.trim().toLowerCase() : '';
    if (payloadType === 'error') {
        if (typeof payload.message === 'string' && payload.message.trim()) {
            message = payload.message.trim();
        }
    }

    const error = payload.error;
    if (!message && typeof error === 'string' && error.trim()) {
        message = error.trim();
    }

    if (!message && isJsonRecord(error)) {
        if (typeof error.message === 'string' && error.message.trim()) {
            message = error.message.trim();
        }
    }

    if (!message) {
        return null;
    }

    const diagnostics: string[] = [];
    const errorCode = extractErrorCode(payload);
    const requestId = extractRequestId(payload);
    if (errorCode) {
        diagnostics.push(`code: ${errorCode}`);
    }
    if (requestId) {
        diagnostics.push(`request_id: ${requestId}`);
    }

    if (diagnostics.length === 0) {
        return message;
    }

    return `${message} [${diagnostics.join(', ')}]`;
}

function extractStreamContent(payload: JsonRecord): string {
    return extractCompletionText(payload);
}

function isStreamFinishedPayload(payload: JsonRecord): boolean {
    if (payload.done === true) {
        return true;
    }

    if (typeof payload.type === 'string') {
        const normalizedType = payload.type.trim().toLowerCase();
        if (
            normalizedType === 'response.completed' ||
            normalizedType === 'response.done' ||
            normalizedType === 'response.failed' ||
            normalizedType === 'response.cancelled' ||
            normalizedType === 'response.canceled' ||
            normalizedType === 'message.completed' ||
            normalizedType === 'message_stop' ||
            normalizedType === 'chat.completion.completed'
        ) {
            return true;
        }
        if (normalizedType === 'message_delta' && isJsonRecord(payload.delta)) {
            const stopReason = payload.delta.stop_reason;
            if (typeof stopReason === 'string' && stopReason.trim()) {
                return true;
            }
        }
    }

    if (typeof payload.status === 'string') {
        const normalizedStatus = payload.status.trim().toLowerCase();
        if (
            normalizedStatus === 'done' ||
            normalizedStatus === 'completed' ||
            normalizedStatus === 'complete' ||
            normalizedStatus === 'finished' ||
            normalizedStatus === 'stop'
        ) {
            return true;
        }
    }

    const choices = Array.isArray(payload.choices) ? payload.choices : [];
    for (const choice of choices) {
        if (!isJsonRecord(choice)) continue;
        const finishReason = choice.finish_reason;
        if (typeof finishReason === 'string' && finishReason.trim()) {
            return true;
        }
    }

    const candidates = Array.isArray(payload.candidates) ? payload.candidates : [];
    for (const candidate of candidates) {
        if (!isJsonRecord(candidate)) continue;
        const finishReason = candidate.finishReason ?? candidate.finish_reason;
        if (typeof finishReason === 'string' && finishReason.trim()) {
            const normalizedFinish = finishReason.trim().toLowerCase();
            if (normalizedFinish !== 'finish_reason_unspecified' && normalizedFinish !== 'unspecified') {
                return true;
            }
        }
    }

    return false;
}

async function fetchV1WithFallback(
    v1Path: string,
    init: RequestInit
): Promise<Response> {
    const headers = {
        ...((init.headers as Record<string, string> | undefined) || {})
    };
    applyDeviceScopeHeaders(headers);
    const scopedInit: RequestInit = {
        ...init,
        headers
    };
    const normalizedPath = v1Path.startsWith('/') ? v1Path : `/${v1Path}`;
    const primaryUrl = `${PIGTEX_API_BASE}${normalizedPath}`;
    const primaryResponse = await fetch(primaryUrl, scopedInit);

    if (primaryResponse.status !== 404) {
        return primaryResponse;
    }

    let shouldFallback = false;
    try {
        const payload = await primaryResponse.clone().json() as { detail?: unknown };
        const detail = payload?.detail;
        shouldFallback = typeof detail === 'string' && detail.trim().toLowerCase() === 'not found';
    } catch {
        try {
            const text = await primaryResponse.clone().text();
            shouldFallback = text.toLowerCase().includes('not found');
        } catch {
            shouldFallback = false;
        }
    }

    // 404 from upstream should be returned as-is (do not fallback route).
    if (!shouldFallback) {
        return primaryResponse;
    }

    const fallbackUrl = `${PIGTEX_API_ROOT}${normalizedPath}`;
    if (fallbackUrl === primaryUrl) {
        return primaryResponse;
    }

    return fetch(fallbackUrl, scopedInit);
}

// ===== API Helper =====
async function apiRequest<T>(
    endpoint: string,
    options: RequestInit = {},
    useAuth: boolean = true
): Promise<T> {
    const headers: HeadersInit = {
        'Content-Type': 'application/json',
        ...(options.headers || {})
    };

    if (useAuth) {
        const token = getAuthToken();
        if (token) {
            (headers as Record<string, string>)['Authorization'] = `Bearer ${token}`;
        }
    }
    applyDeviceScopeHeaders(headers as Record<string, string>);

    const response = await fetch(`${PIGTEX_API_BASE}${endpoint}`, {
        ...options,
        headers
    });

    if (!response.ok) {
        const error = await response.json().catch(() => null);
        if (useAuth) handle401(response, error);
        throwApiResponseError(response, error);
    }

    return response.json();
}

// ===== Auth API =====
export async function register(email: string, username: string, password: string): Promise<User> {
    return apiRequest<User>('/auth/register', {
        method: 'POST',
        body: JSON.stringify({ email, username, password })
    }, false);
}

export async function login(email: string, password: string): Promise<AuthResponse> {
    const response = await apiRequest<AuthResponse>('/auth/login', {
        method: 'POST',
        body: JSON.stringify({ email, password })
    }, false);

    if (response.access_token) {
        setAuthToken(response.access_token);
    }

    return response;
}

function delay(ms: number): Promise<void> {
    return new Promise(resolve => setTimeout(resolve, ms));
}

export async function getOAuthProviders(): Promise<OAuthProvidersResponse> {
    return apiRequest<OAuthProvidersResponse>('/auth/oauth/providers', {}, false);
}

export async function loginWithOAuth(provider: OAuthProvider): Promise<AuthResponse> {
    const startResponse = await apiRequest<OAuthStartResponse>(
        `/auth/oauth/${provider}/start`,
        { method: 'POST' },
        false
    );

    if (typeof window !== 'undefined' && window.electronAPI?.openExternal) {
        await window.electronAPI.openExternal(startResponse.auth_url);
    } else if (typeof window !== 'undefined') {
        window.open(startResponse.auth_url, '_blank', 'noopener,noreferrer');
    } else {
        throw new Error('OAuth login requires a browser environment');
    }

    const pollIntervalMs = 1500;
    const timeoutMs = Math.max(45_000, startResponse.expires_in * 1000);
    const deadline = Date.now() + timeoutMs;
    let latestError = 'OAuth login failed';

    while (Date.now() < deadline) {
        const statusResponse = await apiRequest<OAuthStatusResponse>(
            `/auth/oauth/${provider}/status?state=${encodeURIComponent(startResponse.state)}`,
            {},
            false
        );

        if (statusResponse.status === 'pending') {
            await delay(pollIntervalMs);
            continue;
        }

        if (statusResponse.status === 'error') {
            latestError = statusResponse.error || latestError;
            throw new Error(latestError);
        }

        if (statusResponse.access_token) {
            setAuthToken(statusResponse.access_token);
            return {
                access_token: statusResponse.access_token,
                token_type: statusResponse.token_type || 'bearer'
            };
        }
    }

    throw new Error(`${provider.toUpperCase()} login timed out. Please try again.`);
}

export async function getCurrentUser(): Promise<User> {
    return apiRequest<User>('/auth/me');
}

export function logout(): void {
    removeAuthToken();
}

// ===== Admin Skill Foundry API =====
export async function getSkillFoundryOverview(): Promise<SkillFoundryOverview> {
    return apiRequest<SkillFoundryOverview>('/skill-foundry/overview');
}

export async function getSkillFoundryAudit(limit: number = 50): Promise<{ items: SkillFoundryAuditEvent[] }> {
    const normalizedLimit = Math.max(1, Math.min(200, Math.trunc(limit || 50)));
    return apiRequest<{ items: SkillFoundryAuditEvent[] }>(`/skill-foundry/audit?limit=${normalizedLimit}`);
}

export async function compileSkillFoundryDraft(payload: {
    inputPath?: string;
    dryRun?: boolean;
    maxFiles?: number;
    judgeModel?: string;
    judgeApiKey?: string;
    judgeApiBaseUrl?: string;
}): Promise<Record<string, unknown>> {
    return apiRequest<Record<string, unknown>>('/skill-foundry/compile', {
        method: 'POST',
        body: JSON.stringify({
            input_path: payload.inputPath || null,
            dry_run: payload.dryRun ?? false,
            max_files: payload.maxFiles,
            judge_model: payload.judgeModel || null,
            judge_api_key: payload.judgeApiKey || null,
            judge_api_base_url: payload.judgeApiBaseUrl || null,
        })
    });
}

export async function publishSkillFoundryDraft(note: string): Promise<{
    release: SkillFoundryRelease;
    registry: SkillFoundryRegistryPayload;
}> {
    return apiRequest<{
        release: SkillFoundryRelease;
        registry: SkillFoundryRegistryPayload;
    }>('/skill-foundry/publish', {
        method: 'POST',
        body: JSON.stringify({ note })
    });
}

export async function rollbackSkillFoundryRelease(
    releaseId: string,
    note: string
): Promise<{
    rollback: Record<string, unknown>;
    registry: SkillFoundryRegistryPayload;
}> {
    return apiRequest<{
        rollback: Record<string, unknown>;
        registry: SkillFoundryRegistryPayload;
    }>('/skill-foundry/rollback', {
        method: 'POST',
        body: JSON.stringify({
            release_id: releaseId,
            note,
        })
    });
}

export async function resolveSkillFoundryMatches(payload: {
    message: string;
    intent?: string;
    keywords?: string[];
}): Promise<{
    intent: string;
    keywords: string[];
    matches: SkillFoundrySkill[];
    formatted: string;
}> {
    return apiRequest<{
        intent: string;
        keywords: string[];
        matches: SkillFoundrySkill[];
        formatted: string;
    }>('/skill-foundry/resolve', {
        method: 'POST',
        body: JSON.stringify({
            message: payload.message,
            intent: payload.intent || null,
            keywords: payload.keywords || null,
        })
    });
}

// ===== User API =====
export async function getUsage(): Promise<UsageSummary> {
    return apiRequest<UsageSummary>('/user/usage');
}

export async function getUserProfile(): Promise<User> {
    return apiRequest<User>('/user/profile');
}

export async function getSyncEntitlement(): Promise<SyncEntitlement> {
    return apiRequest<SyncEntitlement>('/billing/sync/entitlement');
}

export async function createSyncCheckoutSession(
    payload: CreateSyncCheckoutSessionPayload
): Promise<CreateSyncCheckoutSessionResponse> {
    return apiRequest<CreateSyncCheckoutSessionResponse>('/billing/sync/checkout-session', {
        method: 'POST',
        body: JSON.stringify({
            plan_code: payload.planCode,
            billing_cycle: payload.billingCycle,
            success_url: payload.successUrl || null,
            cancel_url: payload.cancelUrl || null,
        })
    });
}

export async function createSyncPortalSession(
    payload: CreateSyncPortalSessionPayload = {}
): Promise<CreateSyncPortalSessionResponse> {
    return apiRequest<CreateSyncPortalSessionResponse>('/billing/sync/portal-session', {
        method: 'POST',
        body: JSON.stringify({
            return_url: payload.returnUrl || null,
        })
    });
}

export async function cancelSyncSubscription(
    payload: CancelSyncSubscriptionPayload = {}
): Promise<CancelSyncSubscriptionResponse> {
    return apiRequest<CancelSyncSubscriptionResponse>('/billing/sync/cancel', {
        method: 'POST',
        body: JSON.stringify({
            immediately: payload.immediately ?? false,
        })
    });
}

export async function changePassword(payload: ChangePasswordPayload): Promise<ChangePasswordResponse> {
    return apiRequest<ChangePasswordResponse>('/user/password', {
        method: 'POST',
        body: JSON.stringify({
            current_password: payload.currentPassword || '',
            new_password: payload.newPassword
        })
    });
}

export async function deleteAccount(payload: DeleteAccountPayload): Promise<void> {
    const headers: HeadersInit = {
        'Content-Type': 'application/json'
    };

    const token = getAuthToken();
    if (token) {
        (headers as Record<string, string>)['Authorization'] = `Bearer ${token}`;
    }
    applyDeviceScopeHeaders(headers as Record<string, string>);

    const response = await fetch(`${PIGTEX_API_BASE}/user/account`, {
        method: 'DELETE',
        headers,
        body: JSON.stringify({
            confirmation: payload.confirmation,
            password: payload.password || null
        })
    });

    if (!response.ok) {
        const error = await response.json().catch(() => null);
        handle401(response, error);
        throwApiResponseError(response, error);
    }
}

export async function registerCloudDevice(
    payload: RegisterCloudDevicePayload
): Promise<RegisterCloudDeviceResponse> {
    return apiRequest<RegisterCloudDeviceResponse>('/cloud/devices/register', {
        method: 'POST',
        body: JSON.stringify({
            device_key: payload.deviceKey,
            device_name: payload.deviceName,
            platform: payload.platform,
            app_version: payload.appVersion || null
        })
    });
}

export async function getCloudUsage(): Promise<CloudUsageSummary> {
    return apiRequest<CloudUsageSummary>('/cloud/usage');
}

export async function listCloudBackups(limit: number = 20): Promise<CloudBackupListResponse> {
    const normalizedLimit = Math.max(1, Math.min(100, Math.trunc(limit || 20)));
    return apiRequest<CloudBackupListResponse>(`/cloud/backups?limit=${normalizedLimit}`);
}

export async function createLocalCloudBackup(
    payload: CreateLocalCloudBackupPayload
): Promise<CreateLocalCloudBackupResponse> {
    return apiRequest<CreateLocalCloudBackupResponse>('/cloud/backups/create-local', {
        method: 'POST',
        body: JSON.stringify({
            device_id: payload.deviceId,
            scope_type: payload.scopeType || 'account',
            scope_id: payload.scopeId || null,
            snapshot_kind: payload.snapshotKind || 'full'
        })
    });
}

export async function applyLocalCloudRestore(
    payload: ApplyLocalCloudRestorePayload
): Promise<ApplyLocalCloudRestoreResponse> {
    return apiRequest<ApplyLocalCloudRestoreResponse>('/cloud/restores/apply-local', {
        method: 'POST',
        body: JSON.stringify({
            snapshot_id: payload.snapshotId,
            merge: payload.merge ?? false
        })
    });
}

export async function getCloudSyncState(deviceId: string): Promise<CloudSyncState> {
    return apiRequest<CloudSyncState>(`/cloud/sync/state?device_id=${encodeURIComponent(deviceId)}`);
}

export async function pushCloudSync(
    payload: CloudSyncActionPayload
): Promise<CloudSyncPushResponse> {
    return apiRequest<CloudSyncPushResponse>('/cloud/sync/push', {
        method: 'POST',
        body: JSON.stringify({
            device_id: payload.deviceId,
        })
    });
}

export async function pullCloudSync(
    payload: CloudSyncActionPayload
): Promise<CloudSyncPullResponse> {
    return apiRequest<CloudSyncPullResponse>('/cloud/sync/pull', {
        method: 'POST',
        body: JSON.stringify({
            device_id: payload.deviceId,
        })
    });
}

// ===== Health Check =====
async function getApiHealthStatus(): Promise<{
    reachable: boolean;
    ok: boolean;
    statusCode?: number;
}> {
    try {
        const response = await fetch(`${PIGTEX_API_BASE}/health`);
        return {
            reachable: true,
            ok: response.ok,
            statusCode: response.status
        };
    } catch {
        return {
            reachable: false,
            ok: false
        };
    }
}

export async function checkApiHealth(): Promise<boolean> {
    const status = await getApiHealthStatus();
    return status.ok;
}

export async function diagnoseApiConnectivityIssue(
    error: unknown
): Promise<ApiConnectivityIssue | null> {
    if (!isLikelyNetworkConnectivityError(error)) {
        return null;
    }

    const health = await getApiHealthStatus();
    const isLoopback = isLoopbackApiBase(PIGTEX_API_BASE);

    if (!health.reachable) {
        return {
            kind: 'backend_unreachable',
            apiBaseUrl: PIGTEX_API_BASE,
            isLoopback
        };
    }

    if (!health.ok) {
        return {
            kind: 'backend_unhealthy',
            apiBaseUrl: PIGTEX_API_BASE,
            isLoopback,
            statusCode: health.statusCode
        };
    }

    return null;
}

// ===== Model Types & API =====
export type AIModelType = 'chat' | 'image' | 'audio' | 'video' | 'moderation';
export type ModelCapability =
    | 'chat'
    | 'vision'
    | 'image_generation'
    | 'image_edit'
    | 'audio_speech'
    | 'video_generation'
    | 'moderation';
export type AIModelFlagTone = 'neutral' | 'accent' | 'success' | 'warning' | 'danger';

export interface AIModelProviderFlag {
    label: string;
    code?: string | null;
    tone?: AIModelFlagTone;
    disabled?: boolean;
}

export interface AIModel {
    id: string;
    name: string;
    provider: string;
    provider_id?: string;
    transport?: ApiEndpointProviderId;
    tier: 'free' | 'plus' | 'pro' | 'premium';
    type?: AIModelType;
    capabilities?: ModelCapability[];
    supports_streaming: boolean;
    supports_vision: boolean;
    max_tokens: number;
    description: string | null;
    priority: number;
    is_active: boolean;
    recommendation_flag?: AIModelProviderFlag | null;
    status_flag?: AIModelProviderFlag | null;
}

// Keep only the latest successfully fetched catalog for capability helpers.
let latestModelsCache: AIModel[] | null = null;

interface V1ModelListResponse {
    data?: {
        id: string;
        owned_by?: string;
        provider_id?: string;
        transport?: string;
        type?: string;
        capabilities?: string[];
        name?: string;
        description?: string;
        supports_streaming?: boolean;
        supports_vision?: boolean;
        max_output?: number;
        tier?: string;
        recommendation_flag?: unknown;
        status_flag?: unknown;
    }[];
}

function isKnownEndpointProvider(value: unknown): value is ApiEndpointProviderId {
    return value === 'openai' || value === 'anthropic' || value === 'gemini' || value === 'alibaba';
}

function normalizeModelCapability(value: unknown): ModelCapability | null {
    if (typeof value !== 'string') return null;
    const normalized = value.trim().toLowerCase();
    return normalized === 'chat'
        || normalized === 'vision'
        || normalized === 'image_generation'
        || normalized === 'image_edit'
        || normalized === 'audio_speech'
        || normalized === 'video_generation'
        || normalized === 'moderation'
        ? normalized
        : null;
}

function normalizeAIModelFlagTone(value: unknown): AIModelFlagTone | undefined {
    if (typeof value !== 'string') return undefined;
    const normalized = value.trim().toLowerCase();
    return normalized === 'neutral'
        || normalized === 'accent'
        || normalized === 'success'
        || normalized === 'warning'
        || normalized === 'danger'
        ? normalized
        : undefined;
}

function mapRawModelProviderFlag(value: unknown): AIModelProviderFlag | null {
    if (!value || typeof value !== 'object') {
        return null;
    }

    const raw = value as {
        label?: unknown;
        code?: unknown;
        tone?: unknown;
        disabled?: unknown;
    };
    const label = typeof raw.label === 'string' ? raw.label.trim() : '';
    if (!label) {
        return null;
    }

    const code = typeof raw.code === 'string' && raw.code.trim()
        ? raw.code.trim()
        : null;
    const tone = normalizeAIModelFlagTone(raw.tone);
    const disabled = typeof raw.disabled === 'boolean' ? raw.disabled : undefined;
    return {
        label,
        code,
        tone,
        disabled,
    };
}

const TRANSPORT_STANDARD_CAPABILITIES: Record<ApiEndpointProviderId, ModelCapability[]> = {
    openai: ['chat', 'vision', 'image_generation', 'image_edit', 'video_generation'],
    anthropic: ['chat', 'vision'],
    gemini: ['chat', 'vision', 'image_generation', 'image_edit'],
    alibaba: ['chat', 'vision', 'image_generation', 'image_edit', 'video_generation']
};

type CapabilitySubject = {
    id: string;
    provider?: string;
    provider_id?: string;
    transport?: ApiEndpointProviderId;
    type?: AIModelType;
    supports_vision?: boolean;
    capabilities?: ModelCapability[];
};

function inferCapabilityTransport(subject: CapabilitySubject, endpointProvider?: ApiEndpointProviderId): ApiEndpointProviderId {
    if (endpointProvider) return endpointProvider;
    if (isKnownEndpointProvider(subject.transport)) return subject.transport;
    if (isKnownEndpointProvider(subject.provider_id)) return subject.provider_id;
    if (isKnownEndpointProvider(subject.provider)) return subject.provider as ApiEndpointProviderId;
    return 'openai';
}

function inferModelCapabilities(subject: CapabilitySubject): ModelCapability[] {
    const explicitCapabilities = Array.isArray(subject.capabilities)
        ? subject.capabilities
            .map(capability => normalizeModelCapability(capability))
            .filter((capability): capability is ModelCapability => capability !== null)
        : [];
    if (explicitCapabilities.length > 0) {
        return explicitCapabilities;
    }

    const normalized = new Set<ModelCapability>(explicitCapabilities);
    const modelId = (subject.id || '').trim().toLowerCase();
    const modelType = subject.type || 'chat';

    if (modelType === 'moderation' || modelId.includes('moderation')) {
        normalized.add('moderation');
        return Array.from(normalized);
    }

    if (modelType === 'chat') {
        normalized.add('chat');
    }
    if (modelType === 'chat' && subject.supports_vision) {
        normalized.add('vision');
    }
    if (modelType === 'image') {
        normalized.add('image_generation');
        normalized.add('image_edit');
    }
    if (modelType === 'audio') {
        normalized.add('audio_speech');
    }
    if (modelType === 'video') {
        normalized.add('video_generation');
    }

    return Array.from(normalized);
}

export function modelSupportsCapability(
    subject: CapabilitySubject,
    capability: ModelCapability,
    endpointProvider?: ApiEndpointProviderId
): boolean {
    const transport = inferCapabilityTransport(subject, endpointProvider);
    if (capability !== 'moderation' && !TRANSPORT_STANDARD_CAPABILITIES[transport].includes(capability)) {
        return false;
    }
    return inferModelCapabilities(subject).includes(capability);
}

export function filterModelsByCapability<T extends CapabilitySubject>(
    models: T[],
    capability: ModelCapability,
    endpointProvider?: ApiEndpointProviderId
): T[] {
    return models.filter(model => modelSupportsCapability(model, capability, endpointProvider));
}

function mapV1ModelToAIModel(model: {
    id: string;
    owned_by?: string;
    provider_id?: string;
    transport?: string;
    type?: string;
    capabilities?: string[];
    name?: string;
    description?: string;
    supports_streaming?: boolean;
    supports_vision?: boolean;
    max_output?: number;
    tier?: string;
    recommendation_flag?: unknown;
    status_flag?: unknown;
}): AIModel {
    const transport = isKnownEndpointProvider(model.transport) ? model.transport : undefined;
    const explicitCapabilities = Array.isArray(model.capabilities)
        ? model.capabilities
            .map(capability => normalizeModelCapability(capability))
            .filter((capability): capability is ModelCapability => capability !== null)
        : [];
    const mapped: AIModel = {
        id: model.id,
        name: model.name || model.id,
        provider: model.owned_by || 'unknown',
        provider_id: model.provider_id || model.owned_by || 'unknown',
        transport,
        tier: (model.tier as AIModel['tier']) || 'plus',
        type: (model.type as AIModelType) || 'chat',
        capabilities: explicitCapabilities.length > 0 ? explicitCapabilities : undefined,
        supports_streaming: model.supports_streaming ?? true,
        supports_vision: model.supports_vision ?? false,
        max_tokens: model.max_output ?? 8192,
        description: model.description || null,
        priority: 100,
        is_active: true,
        recommendation_flag: mapRawModelProviderFlag(model.recommendation_flag),
        status_flag: mapRawModelProviderFlag(model.status_flag)
    };
    mapped.capabilities = explicitCapabilities.length > 0
        ? explicitCapabilities
        : inferModelCapabilities(mapped);
    return mapped;
}

function mapV1ModelListToAIModels(payload: V1ModelListResponse): AIModel[] {
    return (payload.data || []).map(mapV1ModelToAIModel);
}

function filterModelsBySelectedEndpoint(
    models: AIModel[],
    endpoint: ApiEndpointProviderId
): AIModel[] {
    if (!models.length) return models;
    const filtered = models.filter(model => !model.transport || model.transport === endpoint);
    return filtered.length > 0 ? filtered : models;
}

async function fetchModelsFromV1(
    headers: Record<string, string>,
    endpoint: ApiEndpointProviderId,
    options?: { includeAllReturnedModels?: boolean; simpleHttpError?: boolean }
): Promise<AIModel[]> {
    const response = await fetchV1WithFallback('/v1/models', { headers });
    if (!response.ok) {
        const error = await response.json().catch(() => null);
        if (options?.simpleHttpError) {
            throw new Error(`HTTP ${response.status}`);
        }
        handle401(response, error);
        throwApiResponseError(response, error);
    }

    const data = await response.json() as V1ModelListResponse;
    const mapped = mapV1ModelListToAIModels(data);
    if (options?.includeAllReturnedModels) {
        latestModelsCache = mapped;
        return mapped;
    }

    const filtered = filterModelsBySelectedEndpoint(mapped, endpoint);
    latestModelsCache = filtered;
    return filtered;
}

export async function getModelsWithCredentials(
    apiKey: string,
    baseUrl: string,
    provider?: ApiProviderId,
    options?: { includeAllReturnedModels?: boolean }
): Promise<AIModel[]> {
    const settings = getPigTexSettings();
    const providerMode = provider ?? settings.apiProvider;
    const customEndpoint = settings.customEndpoint;
    const resolvedProvider = resolveUpstreamProvider(
        providerMode,
        customEndpoint,
        normalizeBaseUrl(baseUrl),
        apiKey
    );
    const mismatchMessage = buildProviderKeyMismatchMessage(resolvedProvider, apiKey);
    if (mismatchMessage) {
        throw new Error(mismatchMessage);
    }

    const headers: Record<string, string> = {
        'Authorization': getBearerToken()
    };
    applyProviderHeaders(headers, { apiKey, baseUrl, provider: providerMode, customEndpoint });
    return fetchModelsFromV1(headers, resolvedProvider, options);
}

/**
 * Fetch available models from upstream provider/gateway
 * Always revalidates against the current upstream catalog.
 */
export async function getModels(forceRefresh: boolean = false): Promise<AIModel[]> {
    void forceRefresh;
    const settings = getPigTexSettings();

    try {
        const token = getAuthToken();
        const hasByok = Boolean(settings.apiKey.trim());

        if (hasByok) {
            const models = await getModelsWithCredentials(
                settings.apiKey,
                settings.baseUrl,
                settings.apiProvider
            );
            latestModelsCache = models;
            return models;
        }

        const headers: Record<string, string> = {};
        if (token) {
            headers.Authorization = getBearerToken();
        }
        applyDeviceScopeHeaders(headers);
        applyProviderHeaders(headers);
        const endpoint = resolveApiProviderForRequest(settings.apiProvider, settings.customEndpoint);
        return fetchModelsFromV1(headers, endpoint, { simpleHttpError: true });
    } catch (error) {
        console.error('Failed to fetch models:', error);
        latestModelsCache = null;
        throw (error instanceof Error ? error : new Error('Failed to fetch models.'));
    }
}

export async function fetchProviderCatalog(): Promise<ApiProviderCatalogEntry[]> {
    const headers: Record<string, string> = {}
    const token = getAuthToken()
    if (token) {
        headers.Authorization = getBearerToken()
    }
    applyDeviceScopeHeaders(headers)

    const response = await fetchV1WithFallback('/v1/providers', { headers })
    const payload = await response.json().catch(() => null)
    if (!response.ok) {
        handle401(response, payload)
        throwApiResponseError(response, payload)
    }

    const catalog = Array.isArray((payload as ProviderCatalogResponse | null)?.data)
        ? (payload as ProviderCatalogResponse).data
        : []
    setRuntimeApiProviderCatalog(catalog)
    return catalog
}

/**
 * Get models by tier
 */
export async function getModelsByTier(tier: string): Promise<AIModel[]> {
    const models = await getModels();
    return models.filter(m => m.tier === tier);
}

/**
 * Get a specific model by ID
 */
export async function getModel(modelId: string): Promise<AIModel | undefined> {
    const models = await getModels();
    return models.find(m => m.id === modelId);
}

export type ModelId = string;

function requireExplicitClientModelId(model: string | null | undefined, operation: string): string {
    const normalized = (model || '').trim();
    if (!normalized) {
        throw new Error(`Model is required for ${operation}. PigTex does not auto-select or remap models for this request.`);
    }
    return normalized;
}

// ===== Chat Completions API =====

/**
 * Send a chat message and get a response (non-streaming)
 * Routes through PigTex backend which forwards to chat2api
 */
export async function sendChatMessage(
    messages: ChatMessage[],
    model: ModelId
): Promise<ChatCompletionResponse> {
    const explicitModel = requireExplicitClientModelId(model, 'chat');
    const headers: Record<string, string> = {
        'Content-Type': 'application/json',
        'Authorization': getBearerToken()
    };
    applyProviderHeaders(headers);

    const response = await fetchV1WithFallback('/v1/chat/completions', {
        method: 'POST',
        headers,
        body: JSON.stringify({
            model: explicitModel,
            messages,
            stream: false
        })
    });

    if (!response.ok) {
        const error = await response.json().catch(() => ({ detail: 'Unknown error' }));
        handle401(response, error);
        throwApiResponseError(response, error);
    }

    return response.json();
}

/**
 * Send a chat message with streaming response
 * Returns an async generator that yields content chunks
 */
export async function* streamChatMessage(
    messages: ChatMessage[],
    model: ModelId,
    signal?: AbortSignal
): AsyncGenerator<string, void, unknown> {
    const explicitModel = requireExplicitClientModelId(model, 'chat');
    const headers: Record<string, string> = {
        'Content-Type': 'application/json',
        'Authorization': getBearerToken()
    };
    applyProviderHeaders(headers);

    const response = await fetchV1WithFallback('/v1/chat/completions', {
        method: 'POST',
        headers,
        body: JSON.stringify({
            model: explicitModel,
            messages,
            stream: true
        }),
        signal
    });

    if (!response.ok) {
        const error = await response.json().catch(() => ({ detail: 'Unknown error' }));
        handle401(response, error);
        throwApiResponseError(response, error);
    }

    const reader = response.body?.getReader();
    if (!reader) {
        throw new Error('No response body');
    }

    const decoder = new TextDecoder();
    let buffer = '';
    let doneReceived = false;

    const processEvent = (eventBlock: string): { content: string | null; finished: boolean } => {
        const payload = parseStreamPayloadFromEvent(eventBlock);
        if (!payload) {
            return { content: null, finished: false };
        }

        const streamError = extractStreamError(payload);
        if (streamError) {
            throw new Error(streamError);
        }

        return {
            content: extractStreamContent(payload) || null,
            finished: isStreamFinishedPayload(payload)
        };
    };

    try {
        while (true) {
            const { done, value } = await reader.read();
            if (done) {
                break;
            }

            buffer += decoder.decode(value, { stream: true });
            const { events, rest } = splitSseEvents(buffer);
            buffer = rest;

            for (const eventBlock of events) {
                if (isDoneSseEvent(eventBlock)) {
                    doneReceived = true;
                    break;
                }
                const processed = processEvent(eventBlock);
                if (processed.content) {
                    yield processed.content;
                }
                if (processed.finished) {
                    doneReceived = true;
                    break;
                }
            }

            if (doneReceived) {
                break;
            }
        }

        if (!doneReceived && buffer.trim()) {
            if (isDoneSseEvent(buffer)) {
                doneReceived = true;
            }
            const processed = processEvent(buffer);
            if (processed.content) {
                yield processed.content;
            }
            if (processed.finished) {
                doneReceived = true;
            }
        }
    } finally {
        reader.releaseLock();
    }
}

// ===== API Provider Connection Validation =====

export interface ApiValidationResult {
    valid: boolean;
    message: string;
    provider?: string;
    base_url?: string;
    models_count?: number;
    source?: string;
    status_code?: number;
}


export async function validateApiConnection(
    apiKey: string,
    baseUrl: string,
    provider?: ApiProviderId
): Promise<ApiValidationResult> {
    const settings = getPigTexSettings();
    const providerMode = provider ?? settings.apiProvider;
    const customEndpoint = settings.customEndpoint;
    const resolvedProvider = resolveUpstreamProvider(
        providerMode,
        customEndpoint,
        normalizeBaseUrl(baseUrl),
        apiKey
    );
    const mismatchMessage = buildProviderKeyMismatchMessage(resolvedProvider, apiKey);
    if (mismatchMessage) {
        throw new Error(mismatchMessage);
    }

    const headers: Record<string, string> = {
        'Authorization': getBearerToken()
    };
    applyProviderHeaders(headers, { apiKey, baseUrl, provider: providerMode, customEndpoint });

    const response = await fetchV1WithFallback('/v1/keys/validate', {
        method: 'POST',
        headers
    });

    const result = await response.json().catch(() => null);
    if (!response.ok) {
        handle401(response, result);
        throwApiResponseError(response, result);
    }

    return result as ApiValidationResult;
}


// ===== Audio & Realtime API =====

export interface AudioTranscriptionOptions {
    model?: string;
    language?: string;
    prompt?: string;
    response_format?: 'json' | 'text' | 'verbose_json' | 'srt' | 'vtt';
    temperature?: number;
}

export interface AudioTranscriptionResult {
    text: string;
}

export interface AudioSpeechOptions {
    model: string;
    input: string;
    voice?: string;
    response_format?: 'mp3' | 'wav' | 'ogg' | 'aac' | 'flac';
    speed?: number;
    prompt_enhance?: boolean;
    prompt_profile?: string;
    purpose?: string;
    audience?: string;
    language?: string;
    voice_character?: string;
    emotion_arc?: string;
    accent?: string;
    speaking_rate?: string;
    pronunciation_dictionary?: string;
    brand_terms?: string[];
}

export interface RealtimeSessionOptions {
    model: string;
    voice?: string;
    modalities?: string[];
    instructions?: string;
}

export interface RealtimeSessionResult {
    id: string;
    object?: string;
    model?: string;
    expires_at?: number;
    ws_url?: string;
    provider?: string;
    client_secret?: { value?: string; expires_at?: number };
    auth?: { type?: string; header?: string; scheme?: string };
}

export async function transcribeAudio(
    file: File,
    options: AudioTranscriptionOptions = {}
): Promise<AudioTranscriptionResult | string> {
    const settings = getPigTexSettings();
    const endpointProvider = resolveApiProviderForRequest(settings.apiProvider, settings.customEndpoint);
    if (!transportSupportsCapability(endpointProvider, 'audio_speech')) {
        throw new Error('Voice features are disabled on this PigTex build.');
    }
    const explicitModel = requireExplicitClientModelId(options.model, 'audio transcription');
    const headers: Record<string, string> = {
        'Authorization': getBearerToken()
    };
    applyProviderHeaders(headers);

    const form = new FormData();
    form.append('file', file);
    form.append('model', explicitModel);
    if (options.language) form.append('language', options.language);
    if (options.prompt) form.append('prompt', options.prompt);
    if (options.response_format) form.append('response_format', options.response_format);
    if (typeof options.temperature === 'number') form.append('temperature', `${options.temperature}`);
    const credentials = resolveProviderCredentials();
    if (credentials.apiKey) {
        form.append('api_key', credentials.apiKey);
        form.append('api_provider', credentials.provider);
        if (credentials.baseUrl) {
            form.append('api_base_url', credentials.baseUrl);
        }
    }

    const response = await fetchV1WithFallback('/v1/audio/transcriptions', {
        method: 'POST',
        headers,
        body: form,
    });
    if (!response.ok) {
        const error = await response.json().catch(() => null);
        handle401(response, error);
        throwApiResponseError(response, error);
    }

    const contentType = (response.headers.get('content-type') || '').toLowerCase();
    if (contentType.includes('application/json')) {
        return await response.json() as AudioTranscriptionResult;
    }
    return await response.text();
}

export async function translateAudio(
    file: File,
    options: Omit<AudioTranscriptionOptions, 'language'> = {}
): Promise<AudioTranscriptionResult | string> {
    const settings = getPigTexSettings();
    const endpointProvider = resolveApiProviderForRequest(settings.apiProvider, settings.customEndpoint);
    if (!transportSupportsCapability(endpointProvider, 'audio_speech')) {
        throw new Error('Voice features are disabled on this PigTex build.');
    }
    const explicitModel = requireExplicitClientModelId(options.model, 'audio translation');
    const headers: Record<string, string> = {
        'Authorization': getBearerToken()
    };
    applyProviderHeaders(headers);

    const form = new FormData();
    form.append('file', file);
    form.append('model', explicitModel);
    if (options.prompt) form.append('prompt', options.prompt);
    if (options.response_format) form.append('response_format', options.response_format);
    if (typeof options.temperature === 'number') form.append('temperature', `${options.temperature}`);
    const credentials = resolveProviderCredentials();
    if (credentials.apiKey) {
        form.append('api_key', credentials.apiKey);
        form.append('api_provider', credentials.provider);
        if (credentials.baseUrl) {
            form.append('api_base_url', credentials.baseUrl);
        }
    }

    const response = await fetchV1WithFallback('/v1/audio/translations', {
        method: 'POST',
        headers,
        body: form,
    });
    if (!response.ok) {
        const error = await response.json().catch(() => null);
        handle401(response, error);
        throwApiResponseError(response, error);
    }

    const contentType = (response.headers.get('content-type') || '').toLowerCase();
    if (contentType.includes('application/json')) {
        return await response.json() as AudioTranscriptionResult;
    }
    return await response.text();
}

export async function synthesizeSpeech(options: AudioSpeechOptions): Promise<Blob | unknown> {
    const settings = getPigTexSettings();
    const endpointProvider = resolveApiProviderForRequest(settings.apiProvider, settings.customEndpoint);
    if (!transportSupportsCapability(endpointProvider, 'audio_speech')) {
        throw new Error('Voice features are disabled on this PigTex build.');
    }
    const explicitModel = requireExplicitClientModelId(options.model, 'speech synthesis');

    const headers: Record<string, string> = {
        'Authorization': getBearerToken(),
        'Content-Type': 'application/json'
    };
    applyProviderHeaders(headers);

    const credentials = resolveProviderCredentials();
    const payload: Record<string, unknown> = {
        ...options,
        model: explicitModel,
    };
    if (credentials.apiKey) {
        payload.api_key = credentials.apiKey;
        payload.api_base_url = credentials.baseUrl;
    }
    if (payload.prompt_enhance === undefined) {
        payload.prompt_enhance = true;
    }
    if (typeof payload.prompt_profile !== 'string' || !payload.prompt_profile.trim()) {
        payload.prompt_profile = 'world_class';
    }

    const response = await fetchV1WithFallback('/v1/audio/speech', {
        method: 'POST',
        headers,
        body: JSON.stringify(payload),
    });
    if (!response.ok) {
        const error = await response.json().catch(() => null);
        handle401(response, error);
        throwApiResponseError(response, error);
    }

    const contentType = (response.headers.get('content-type') || '').toLowerCase();
    if (contentType.includes('application/json')) {
        return await response.json();
    }
    return await response.blob();
}

export interface VideoGenerationOptions {
    model?: string;
    n?: number;
    size?: string;
    duration?: string;
    quality?: string;
    response_format?: string;
    style?: string;
    aspect_ratio?: string;
    user?: string;
    prompt_enhance?: boolean;
    prompt_profile?: string;
    objective?: string;
    audience?: string;
    offer?: string;
    tone?: string;
    reference_style?: string;
    brand_palette?: string;
    cta?: string;
}

export interface VideoGenerationDataItem {
    url?: string;
    video_url?: string;
    download_url?: string;
    thumbnail_url?: string;
    revised_prompt?: string;
    b64_json?: string;
    mime_type?: string;
    id?: string;
}

export interface VideoGenerationResult {
    created?: number;
    data?: VideoGenerationDataItem[];
    task_id?: string;
    task_status?: string;
    error_message?: string;
    [key: string]: unknown;
}

export async function generateVideo(
    prompt: string,
    options: VideoGenerationOptions = {},
    signal?: AbortSignal
): Promise<VideoGenerationResult | Blob> {
    const settings = getPigTexSettings();
    const endpointProvider = resolveApiProviderForRequest(settings.apiProvider, settings.customEndpoint);
    if (!transportSupportsCapability(endpointProvider, 'video_generation')) {
        throw new Error(`The selected ${endpointProvider.toUpperCase()} transport does not support video generation.`);
    }
    const explicitModel = requireExplicitClientModelId(options.model, 'video generation');

    const headers: Record<string, string> = {
        'Authorization': getBearerToken(),
        'Content-Type': 'application/json'
    };
    applyProviderHeaders(headers);

    const credentials = resolveProviderCredentials();
    const payload: Record<string, unknown> = {
        prompt,
        ...options,
        model: explicitModel,
    };
    if (credentials.apiKey) {
        payload.api_key = credentials.apiKey;
        payload.api_base_url = credentials.baseUrl;
    }
    if (payload.prompt_enhance === undefined) {
        payload.prompt_enhance = true;
    }
    if (typeof payload.prompt_profile !== 'string' || !payload.prompt_profile.trim()) {
        payload.prompt_profile = 'world_class';
    }

    const response = await fetchV1WithFallback('/v1/videos/generations', {
        method: 'POST',
        headers,
        body: JSON.stringify(payload),
        signal,
    });

    if (!response.ok) {
        const error = await response.json().catch(() => null);
        handle401(response, error);
        throwApiResponseError(response, error);
    }

    const contentType = (response.headers.get('content-type') || '').toLowerCase();
    if (contentType.includes('application/json')) {
        return await response.json() as VideoGenerationResult;
    }
    return await response.blob();
}

export async function getVideoGenerationTask(
    taskId: string,
    signal?: AbortSignal
): Promise<VideoGenerationResult | Blob> {
    const settings = getPigTexSettings();
    const endpointProvider = resolveApiProviderForRequest(settings.apiProvider, settings.customEndpoint);
    if (!transportSupportsCapability(endpointProvider, 'video_generation')) {
        throw new Error(`The selected ${endpointProvider.toUpperCase()} transport does not support video generation.`);
    }

    const headers: Record<string, string> = {
        'Authorization': getBearerToken(),
    };
    applyProviderHeaders(headers);

    const credentials = resolveProviderCredentials();
    const route = `/v1/videos/generations/${encodeURIComponent(taskId)}`;
    const query = new URLSearchParams();
    if (credentials.apiKey) {
        query.set('api_key', credentials.apiKey);
        query.set('api_provider', credentials.provider);
        if (credentials.baseUrl) {
            query.set('api_base_url', credentials.baseUrl);
        }
    }
    const endpoint = query.toString() ? `${route}?${query.toString()}` : route;

    const response = await fetchV1WithFallback(endpoint, {
        method: 'GET',
        headers,
        signal,
    });
    const contentType = (response.headers.get('content-type') || '').toLowerCase();
    if (!response.ok) {
        const result = await response.json().catch(() => null);
        handle401(response, result);
        throwApiResponseError(response, result);
    }
    if (contentType.includes('application/json')) {
        const result = await response.json().catch(() => null);
        return result as VideoGenerationResult;
    }
    return await response.blob();
}

export async function createRealtimeSession(
    options: RealtimeSessionOptions
): Promise<RealtimeSessionResult> {
    const explicitModel = requireExplicitClientModelId(options.model, 'realtime session');
    const headers: Record<string, string> = {
        'Authorization': getBearerToken(),
        'Content-Type': 'application/json'
    };
    applyProviderHeaders(headers);

    const response = await fetchV1WithFallback('/v1/realtime/sessions', {
        method: 'POST',
        headers,
        body: JSON.stringify({
            ...options,
            model: explicitModel,
        }),
    });
    const result = await response.json().catch(() => null);
    if (!response.ok) {
        handle401(response, result);
        throwApiResponseError(response, result);
    }
    return result as RealtimeSessionResult;
}


// ===== Image Upload API =====

export interface ImageAttachment {
    id: string;
    filename: string;
    mime_type: string;
    size: number;
    base64_data: string;  // data:image/png;base64,...
    serve_url?: string;
    width?: number;
    height?: number;
}

export interface ImageUploadResult {
    images: ImageAttachment[];
    count: number;
}

export interface DocumentAttachment {
    id: string;
    filename: string;
    mime_type: string;
    size: number;
    extracted_text: string;
    text_chars: number;
    truncated: boolean;
    chunks?: DocumentAttachmentChunk[];
}

export interface DocumentAttachmentChunk {
    index: number;
    label?: string | null;
    text: string;
    char_count: number;
    truncated?: boolean;
}

export interface FileUploadResult {
    files: DocumentAttachment[];
    count: number;
}

/**
 * Upload images for multimodal chat.
 * Returns base64-encoded image data for including in chat messages.
 */
export async function uploadImages(files: File[]): Promise<ImageAttachment[]> {
    const token = getAuthToken();
    const formData = new FormData();
    for (const file of files) {
        formData.append('files', file);
    }
    const headers: Record<string, string> = {
        'Authorization': `Bearer ${token || ''}`
    };
    applyDeviceScopeHeaders(headers);

    const response = await fetch(`${PIGTEX_API_BASE}/images/upload`, {
        method: 'POST',
        headers,
        body: formData
    });

    if (!response.ok) {
        const error = await response.json().catch(() => null);
        handle401(response, error);
        throwApiResponseError(response, error);
    }

    const result = await response.json() as ImageUploadResult;
    return result.images;
}

/**
 * Upload files and extract text for chat context.
 * Server returns normalized extracted content for each file.
 */
export async function uploadFiles(files: File[]): Promise<DocumentAttachment[]> {
    const token = getAuthToken();
    const formData = new FormData();
    for (const file of files) {
        formData.append('files', file);
    }
    const headers: Record<string, string> = {
        'Authorization': `Bearer ${token || ''}`
    };
    applyDeviceScopeHeaders(headers);

    const response = await fetch(`${PIGTEX_API_BASE}/files/upload`, {
        method: 'POST',
        headers,
        body: formData
    });

    if (!response.ok) {
        const error = await response.json().catch(() => null);
        handle401(response, error);
        throwApiResponseError(response, error);
    }

    const result = await response.json() as FileUploadResult;
    return result.files;
}

/**
 * Convert a File to base64 data URL client-side (no server upload needed).
 * Preferred for speed — avoids round-trip to server.
 */
export function fileToBase64(file: File): Promise<ImageAttachment> {
    return new Promise((resolve, reject) => {
        const reader = new FileReader();
        reader.onload = () => {
            const base64Data = reader.result as string;
            resolve({
                id: `img_${Date.now()}_${Math.random().toString(36).slice(2, 8)}`,
                filename: file.name,
                mime_type: file.type || 'image/png',
                size: file.size,
                base64_data: base64Data,
            });
        };
        reader.onerror = () => reject(new Error('Failed to read file'));
        reader.readAsDataURL(file);
    });
}

export interface ImageOperationResult {
    images: ImageAttachment[];
    revisedPrompts: string[];
}

export interface ImageGenerationOptions {
    model?: string;
    n?: number;
    size?: string;
    quality?: string;
    response_format?: 'b64_json' | 'url';
    style?: string;
    background?: string;
    user?: string;
}

export interface ImageEditOptions {
    model?: string;
    n?: number;
    size?: string;
    quality?: string;
    response_format?: 'b64_json' | 'url';
    user?: string;
    mask?: ImageAttachment | null;
}

interface V1ImageResultItem {
    b64_json?: string;
    url?: string;
    serve_url?: string;
    revised_prompt?: string;
    mime_type?: string;
}

interface V1ImageResponsePayload {
    data?: V1ImageResultItem[];
}

type DefaultModelCapability = Exclude<ModelCapability, 'vision' | 'moderation'>;

const DEFAULT_MODEL_IDS_BY_CAPABILITY: Record<DefaultModelCapability, Record<ApiEndpointProviderId, string>> = {
    chat: {
        openai: 'gpt-4o',
        anthropic: 'claude-sonnet-4-20250514',
        gemini: 'gemini-2.5-flash',
        alibaba: 'qwen-plus-latest'
    },
    image_generation: {
        openai: '',
        anthropic: '',
        gemini: '',
        alibaba: ''
    },
    image_edit: {
        openai: '',
        anthropic: '',
        gemini: '',
        alibaba: ''
    },
    audio_speech: {
        openai: '',
        anthropic: '',
        gemini: '',
        alibaba: ''
    },
    video_generation: {
        openai: 'sora-2',
        anthropic: '',
        gemini: '',
        alibaba: ''
    }
};

export function getTransportDefaultModelId(
    endpointProvider: ApiEndpointProviderId,
    capability: DefaultModelCapability
): string {
    return DEFAULT_MODEL_IDS_BY_CAPABILITY[capability][endpointProvider] || '';
}

type PickModelForCapabilityOptions = {
    preferredModelId?: string | null;
    fallbackModelId?: string | null;
    excludeModelIds?: string[];
};

function normalizeModelIdMatch(value?: string | null): string {
    return (value || '').trim().toLowerCase();
}

export function pickModelForCapability<T extends CapabilitySubject>(
    models: T[],
    capability: ModelCapability,
    endpointProvider?: ApiEndpointProviderId,
    options: PickModelForCapabilityOptions = {}
): T | null {
    const candidates = filterModelsByCapability(models, capability, endpointProvider);
    if (candidates.length === 0) return null;

    const excludedIds = new Set(
        (options.excludeModelIds || [])
            .map(value => normalizeModelIdMatch(value))
            .filter(Boolean)
    );
    const pickById = (modelId?: string | null): T | null => {
        const normalizedTarget = normalizeModelIdMatch(modelId);
        if (!normalizedTarget) return null;
        return candidates.find(model => {
            const normalizedId = normalizeModelIdMatch(model.id);
            return normalizedId === normalizedTarget && !excludedIds.has(normalizedId);
        }) || null;
    };

    return pickById(options.preferredModelId)
        || pickById(options.fallbackModelId)
        || candidates.find(model => !excludedIds.has(normalizeModelIdMatch(model.id)))
        || null;
}

function normalizeImageApiResponse(
    payload: V1ImageResponsePayload,
    prefix: 'generated' | 'edited'
): ImageOperationResult {
    const now = Date.now();
    const rows = Array.isArray(payload.data) ? payload.data : [];
    const images: ImageAttachment[] = [];
    const revisedPrompts: string[] = [];

    rows.forEach((row, index) => {
        if (typeof row.revised_prompt === 'string' && row.revised_prompt.trim()) {
            revisedPrompts.push(row.revised_prompt.trim());
        }

        const mimeType = (typeof row.mime_type === 'string' && row.mime_type.trim())
            ? row.mime_type.trim()
            : 'image/png';

        let resolvedSource = '';
        let serveUrl: string | undefined;

        if (typeof row.b64_json === 'string' && row.b64_json.trim()) {
            resolvedSource = `data:${mimeType};base64,${row.b64_json.trim()}`;
        } else if (typeof row.serve_url === 'string' && row.serve_url.trim()) {
            serveUrl = row.serve_url.trim();
            resolvedSource = resolveImageUrl(serveUrl);
        } else if (typeof row.url === 'string' && row.url.trim()) {
            resolvedSource = resolveImageUrl(row.url.trim());
            if (row.url.includes('/api/images/serve/')) {
                serveUrl = row.url.trim();
            }
        }

        if (!resolvedSource) return;

        const ext = mimeType.split('/')[1] || 'png';
        images.push({
            id: `${prefix}_${now}_${index + 1}`,
            filename: `${prefix}_${index + 1}.${ext}`,
            mime_type: mimeType,
            size: 0,
            base64_data: resolvedSource,
            serve_url: serveUrl
        });
    });

    return { images, revisedPrompts };
}

export function transportSupportsCapability(
    endpointProvider: ApiEndpointProviderId,
    capability: ModelCapability
): boolean {
    return TRANSPORT_STANDARD_CAPABILITIES[endpointProvider].includes(capability);
}

function shouldUsePaygProxyForImageRequest(model: string | undefined): boolean {
    void model;
    const settings = getPigTexSettings();
    const hasByok = Boolean(settings.apiKey.trim());
    return !hasByok;
}

function shouldApplyQwenImagePromptEnhancer(model: string | undefined): boolean {
    const settings = getPigTexSettings();
    if (!settings.enableQwenImagePromptEnhancer) {
        return false;
    }
    const resolvedProvider = resolveApiProviderForRequest(settings.apiProvider, settings.customEndpoint);
    if (resolvedProvider !== 'alibaba') {
        return false;
    }
    if (!model) {
        return true;
    }
    const lowered = model.trim().toLowerCase();
    return lowered.startsWith('qwen-image') || lowered.startsWith('wanx') || lowered.includes('image');
}

export async function generateImages(
    prompt: string,
    options: ImageGenerationOptions = {},
    signal?: AbortSignal
): Promise<ImageOperationResult> {
    const settings = getPigTexSettings();
    const endpointProvider = resolveApiProviderForRequest(settings.apiProvider, settings.customEndpoint);
    if (!transportSupportsCapability(endpointProvider, 'image_generation')) {
        throw new Error(`The selected ${endpointProvider.toUpperCase()} transport does not support image generation.`);
    }

    const payload: Record<string, unknown> = {
        prompt,
        response_format: options.response_format || 'b64_json'
    };

    const optionalKeys: Array<keyof ImageGenerationOptions> = [
        'model',
        'n',
        'size',
        'quality',
        'style',
        'background',
        'user'
    ];
    for (const key of optionalKeys) {
        const value = options[key];
        if (value !== undefined && value !== null && `${value}`.trim() !== '') {
            payload[key] = value;
        }
    }

    const requestedModel = typeof payload.model === 'string' ? payload.model : undefined;
    const usePaygProxy = shouldUsePaygProxyForImageRequest(requestedModel);
    if (!usePaygProxy && shouldApplyQwenImagePromptEnhancer(requestedModel)) {
        payload.prompt_enhance = true;
        payload.prompt_profile = 'qwen_vip';
    }

    let response: Response;
    if (usePaygProxy) {
        const proxyHeaders: Record<string, string> = {
            'Authorization': getBearerToken(),
            'Content-Type': 'application/json'
        };
        applyDeviceScopeHeaders(proxyHeaders);
        applyProviderHeaders(proxyHeaders);
        response = await fetch(`${PIGTEX_API_BASE}/proxy/v1/images/generations`, {
            method: 'POST',
            headers: proxyHeaders,
            body: JSON.stringify(payload),
            signal
        });
    } else {
        const headers: Record<string, string> = {
            'Authorization': getBearerToken(),
            'Content-Type': 'application/json'
        };
        applyProviderHeaders(headers);
        response = await fetchV1WithFallback('/v1/images/generations', {
            method: 'POST',
            headers,
            body: JSON.stringify(payload),
            signal
        });
    }

    const result = await response.json().catch(() => null) as V1ImageResponsePayload | null;
    if (!response.ok || !result) {
        handle401(response, result);
        throwApiResponseError(response, result);
    }

    return normalizeImageApiResponse(result, 'generated');
}

export async function editImage(
    prompt: string,
    image: ImageAttachment,
    options: ImageEditOptions = {},
    signal?: AbortSignal
): Promise<ImageOperationResult> {
    const settings = getPigTexSettings();
    const endpointProvider = resolveApiProviderForRequest(settings.apiProvider, settings.customEndpoint);
    if (!transportSupportsCapability(endpointProvider, 'image_edit')) {
        throw new Error(`The selected ${endpointProvider.toUpperCase()} transport does not support image editing.`);
    }

    const payload: Record<string, unknown> = {
        prompt,
        image: image.base64_data,
        response_format: options.response_format || 'b64_json'
    };
    if (options.mask?.base64_data) {
        payload.mask = options.mask.base64_data;
    }

    const optionalKeys: Array<keyof ImageEditOptions> = [
        'model',
        'n',
        'size',
        'quality',
        'user'
    ];
    for (const key of optionalKeys) {
        const value = options[key];
        if (value !== undefined && value !== null && `${value}`.trim() !== '') {
            payload[key] = value;
        }
    }

    const requestedModel = typeof payload.model === 'string' ? payload.model : undefined;
    const usePaygProxy = shouldUsePaygProxyForImageRequest(requestedModel);
    if (!usePaygProxy && shouldApplyQwenImagePromptEnhancer(requestedModel)) {
        payload.prompt_enhance = true;
        payload.prompt_profile = 'qwen_vip';
    }

    let response: Response;
    if (usePaygProxy) {
        const proxyHeaders: Record<string, string> = {
            'Authorization': getBearerToken(),
            'Content-Type': 'application/json'
        };
        applyDeviceScopeHeaders(proxyHeaders);
        applyProviderHeaders(proxyHeaders);
        response = await fetch(`${PIGTEX_API_BASE}/proxy/v1/images/edits`, {
            method: 'POST',
            headers: proxyHeaders,
            body: JSON.stringify(payload),
            signal
        });
    } else {
        const headers: Record<string, string> = {
            'Authorization': getBearerToken(),
            'Content-Type': 'application/json'
        };
        applyProviderHeaders(headers);
        response = await fetchV1WithFallback('/v1/images/edits', {
            method: 'POST',
            headers,
            body: JSON.stringify(payload),
            signal
        });
    }

    const result = await response.json().catch(() => null) as V1ImageResponsePayload | null;
    if (!response.ok || !result) {
        handle401(response, result);
        throwApiResponseError(response, result);
    }

    return normalizeImageApiResponse(result, 'edited');
}

// ===== Smart Chat API (Full Memory System) =====

export interface SmartChatRequest {
    message: string;
    model?: string;
    mode?: 'fast' | 'deep';
    conversation_id?: string;
    workspace_id?: string;
    runtime_instruction?: string;
    temperature?: number;
    max_tokens?: number;
    stream?: boolean;
    use_memory?: boolean;
    use_knowledge?: boolean;
    use_facts?: boolean;
    use_history?: boolean;
    use_web_search?: boolean;
    web_search_mode?: 'auto' | 'fast' | 'deep' | 'realtime' | 'verify' | 'deep_verify';
    web_search_max_results?: number;
    web_search_deep_read?: boolean;
    web_search_deep_verify?: boolean;
    image_attachments?: ImageAttachment[];  // Attached images for multimodal
    file_attachments?: DocumentAttachment[];  // Extracted documents for context injection
}

export interface SmartChatSource {
    index: number;
    id: string;
    title: string;
    type: string;
}

export interface MemoryContextSource {
    index: number;
    id: string;
    title?: string;
    type?: string;
}

export interface MemoryContextMetadata {
    enabled: boolean;
    use_knowledge?: boolean;
    use_facts?: boolean;
    use_history?: boolean;
    context_tokens?: number;
    knowledge_hits?: number;
    history_messages_used?: number;
    facts_used?: number;
    preference_facts_used?: number;
    system_facts_used?: number;
    workspace_facts_used?: number;
    sources?: MemoryContextSource[];
}

export interface WebCitation {
    index: number;
    title: string;
    url: string;
    domain?: string;
    published_at?: string;
    snippet?: string;
    source_provider?: string;
    relevance_score?: number;
    credibility_score?: number;
    recency_score?: number;
}

export interface WebSearchClaimVerification {
    claim: string;
    verdict: 'supported' | 'contradicted' | 'mixed' | 'insufficient';
    confidence: number;
    evidence_count?: number;
    supporting_sources?: number[];
    contradicting_sources?: number[];
    summary?: string;
}

export interface WebSearchMetadata {
    enabled: boolean;
    status: 'running' | 'complete' | 'timeout' | 'skipped' | 'disabled' | 'error';
    mode?: 'auto' | 'fast' | 'deep';
    search_intent?: string;
    search_queries?: string[];
    total_search_time_ms?: number;
    raw_results_count?: number;
    checked_at_utc?: string;
    confidence_score?: number;
    conflicts_count?: number;
    claims_verified_count?: number;
    warnings?: string[];
    claim_verification?: WebSearchClaimVerification[];
}

export interface StreamUsageMetadata {
    prompt_tokens: number;
    completion_tokens: number;
    total_tokens: number;
    cost_usd?: number;
    estimated?: boolean;
}

export interface SmartChatResponse {
    id: string;
    conversation_id: string;
    message: {
        role: 'assistant';
        content: string;
    };
    sources?: SmartChatSource[];
    memory?: MemoryContextMetadata;
    citations?: WebCitation[];
    web_search?: WebSearchMetadata;
    usage?: {
        prompt_tokens: number;
        completion_tokens: number;
        total_tokens: number;
        cost_usd?: number;
        estimated?: boolean;
    };
    created_at: string;
}

interface V1SmartChatCompletionResponse extends ChatCompletionResponse {
    conversation_id?: string;
    sources?: SmartChatSource[];
    memory?: MemoryContextMetadata;
    citations?: WebCitation[];
    web_search?: WebSearchMetadata;
}

function buildSmartChatPayload(
    request: SmartChatRequest,
    forceStream?: boolean
): Record<string, unknown> {
    const settings = getPigTexSettings();
    const explicitModel = requireExplicitClientModelId(
        request.model ?? settings.model,
        'chat completion'
    );
    const useKnowledge = request.use_knowledge !== false;
    const useFacts = request.use_facts !== false;
    const useHistory = request.use_history !== false;
    const useMemory = request.use_memory ?? (useKnowledge || useFacts || useHistory);

    // Build multimodal content if images are attached
    let userContent: unknown;
    if (request.image_attachments && request.image_attachments.length > 0) {
        const contentParts: unknown[] = [];
        // Add text part first
        if (request.message.trim()) {
            contentParts.push({ type: 'text', text: request.message });
        }
        // Add image parts
        for (const img of request.image_attachments) {
            contentParts.push({
                type: 'image_url',
                image_url: { url: img.base64_data }
            });
        }
        userContent = contentParts;
    } else {
        userContent = request.message;
    }

    const payload: Record<string, unknown> = {
        model: explicitModel,
        messages: [{ role: 'user', content: userContent }],
        temperature: request.temperature ?? 0.7,
        stream: forceStream ?? Boolean(request.stream),
        mode: request.mode,
        conversation_id: request.conversation_id,
        workspace_id: request.workspace_id,
        runtime_instruction: request.runtime_instruction,
        use_memory: useMemory,
        use_knowledge: useKnowledge,
        use_facts: useFacts,
        use_history: useHistory
    };

    if (request.max_tokens !== undefined) {
        payload.max_tokens = request.max_tokens;
    }
    if (request.use_web_search !== undefined) {
        payload.use_web_search = request.use_web_search;
    }
    if (request.web_search_mode) {
        payload.web_search_mode = request.web_search_mode;
    }
    if (request.web_search_max_results !== undefined) {
        payload.web_search_max_results = request.web_search_max_results;
    }
    if (request.web_search_deep_read !== undefined) {
        payload.web_search_deep_read = request.web_search_deep_read;
    }
    if (request.web_search_deep_verify !== undefined) {
        payload.web_search_deep_verify = request.web_search_deep_verify;
    }
    if (request.file_attachments && request.file_attachments.length > 0) {
        payload.file_attachments = request.file_attachments;
    }

    return payload;
}

async function postSmartChat(
    request: SmartChatRequest,
    forceStream: boolean,
    headers: Record<string, string>,
    signal?: AbortSignal
): Promise<Response> {
    const payload = buildSmartChatPayload(request, forceStream);
    return await fetchV1WithFallback('/v1/chat/completions', {
        method: 'POST',
        headers,
        body: JSON.stringify(payload),
        signal
    });
}

/**
 * Smart Chat - Send message with full memory system
 * Features:
 * - "Bơm Ngầm": Injects hidden system prompts + runtime instructions
 * - Local Memory: Uses conversation history, knowledge, facts
 * - Auto conversation tracking
 * - Semantic search enabled
 */
export async function sendSmartChat(
    request: SmartChatRequest
): Promise<SmartChatResponse> {
    const headers: Record<string, string> = {
        'Content-Type': 'application/json',
        'Authorization': getBearerToken()
    };
    applyProviderHeaders(headers);

    const response = await postSmartChat(request, false, headers);

    if (!response.ok) {
        const error = await response.json().catch(() => null);
        handle401(response, error);
        throwApiResponseError(response, error);
    }

    const data = await response.json() as V1SmartChatCompletionResponse;
    const assistantContent = extractCompletionText(data);
    const createdAt = typeof data.created === 'number'
        ? new Date(data.created * 1000).toISOString()
        : new Date().toISOString();

    return {
        id: data.id || `assistant-${Date.now()}`,
        conversation_id: data.conversation_id || request.conversation_id || '',
        message: {
            role: 'assistant',
            content: assistantContent
        },
        sources: data.sources,
        memory: data.memory,
        citations: data.citations,
        web_search: data.web_search,
        usage: data.usage,
        created_at: createdAt
    };
}

/**
 * Smart Chat with streaming - Full memory system
 * Returns conversation_id in header for tracking
 */
export async function* streamSmartChat(
    request: SmartChatRequest,
    signal?: AbortSignal
): AsyncGenerator<{
    content: string;
    conversationId?: string;
    citations?: WebCitation[];
    webSearch?: WebSearchMetadata;
    memory?: MemoryContextMetadata;
    usage?: StreamUsageMetadata;
}, void, unknown> {
    const headers: Record<string, string> = {
        'Content-Type': 'application/json',
        'Authorization': getBearerToken()
    };
    applyProviderHeaders(headers);

    const response = await postSmartChat(request, true, headers, signal);

    if (!response.ok) {
        const error = await response.json().catch(() => null);
        handle401(response, error);
        throwApiResponseError(response, error);
    }

    // Primary source: response header. Fallback source: SSE payload metadata.
    let resolvedConversationId = response.headers.get('X-Conversation-ID') || undefined;

    const reader = response.body?.getReader();
    if (!reader) {
        throw new Error('No response body');
    }

    const decoder = new TextDecoder();
    let buffer = '';
    let conversationIdEmitted = false;
    let didEmitAnyContent = false;

    const emitChunk = (
        content: string,
        citations?: WebCitation[],
        webSearch?: WebSearchMetadata,
        memory?: MemoryContextMetadata,
        usage?: StreamUsageMetadata
    ): {
        content: string;
        conversationId?: string;
        citations?: WebCitation[];
        webSearch?: WebSearchMetadata;
        memory?: MemoryContextMetadata;
        usage?: StreamUsageMetadata;
    } => {
        if (content) {
            didEmitAnyContent = true;
        }
        const chunk: {
            content: string;
            conversationId?: string;
            citations?: WebCitation[];
            webSearch?: WebSearchMetadata;
            memory?: MemoryContextMetadata;
            usage?: StreamUsageMetadata;
        } = { content };
        if (citations && citations.length > 0) {
            chunk.citations = citations;
        }
        if (webSearch) {
            chunk.webSearch = webSearch;
        }
        if (memory) {
            chunk.memory = memory;
        }
        if (usage) {
            chunk.usage = usage;
        }
        if (!conversationIdEmitted && resolvedConversationId) {
            chunk.conversationId = resolvedConversationId;
            conversationIdEmitted = true;
            return chunk;
        }
        return chunk;
    };

    const processEvent = (
        eventBlock: string
    ): {
        content: string | null;
        finished: boolean;
        citations?: WebCitation[];
        webSearch?: WebSearchMetadata;
        memory?: MemoryContextMetadata;
        usage?: StreamUsageMetadata;
    } => {
        const payload = parseStreamPayloadFromEvent(eventBlock);
        if (!payload) {
            return { content: null, finished: false };
        }

        if (!resolvedConversationId) {
            resolvedConversationId = extractConversationIdFromPayload(payload);
        }

        const streamError = extractStreamError(payload);
        if (streamError) {
            throw new Error(streamError);
        }

        return {
            content: extractStreamContent(payload) || null,
            finished: isStreamFinishedPayload(payload),
            citations: extractCitationsFromPayload(payload),
            webSearch: extractWebSearchMetadataFromPayload(payload),
            memory: extractMemoryContextMetadataFromPayload(payload),
            usage: extractUsageFromPayload(payload),
        };
    };

    try {
        while (true) {
            const { done, value } = await reader.read();
            if (done) {
                break;
            }

            buffer += decoder.decode(value, { stream: true });
            const { events, rest } = splitSseEvents(buffer);
            buffer = rest;

            for (const eventBlock of events) {
                if (isDoneSseEvent(eventBlock)) {
                    continue;
                }
                const processed = processEvent(eventBlock);
                if (processed.content || processed.citations || processed.webSearch || processed.memory || processed.usage) {
                    yield emitChunk(
                        processed.content || '',
                        processed.citations,
                        processed.webSearch,
                        processed.memory,
                        processed.usage
                    );
                }
                if (processed.finished) {
                    continue;
                }
            }
        }

        if (buffer.trim()) {
            if (isDoneSseEvent(buffer)) {
            } else {
                const processed = processEvent(buffer);
                if (processed.content || processed.citations || processed.webSearch || processed.memory || processed.usage) {
                    yield emitChunk(
                        processed.content || '',
                        processed.citations,
                        processed.webSearch,
                        processed.memory,
                        processed.usage
                    );
                }
            }
        }

        // Fallback: if upstream stream produced no chunks, retry once in non-stream mode.
        if (!didEmitAnyContent) {
            const fallbackResponse = await fetchV1WithFallback('/v1/chat/completions', {
                method: 'POST',
                headers,
                body: JSON.stringify(buildSmartChatPayload(request, false)),
                signal
            });

            if (!fallbackResponse.ok) {
                const error = await fallbackResponse.json().catch(() => null);
                handle401(fallbackResponse, error);
                throwApiResponseError(fallbackResponse, error);
            }

            const fallbackData = await fallbackResponse.json() as V1SmartChatCompletionResponse;
            const fallbackContent = extractCompletionText(fallbackData);
            const fallbackConversationId = fallbackData.conversation_id || resolvedConversationId;
            const fallbackPayload = isJsonRecord(fallbackData) ? fallbackData : null;
            const fallbackCitations = fallbackPayload ? extractCitationsFromPayload(fallbackPayload) : undefined;
            const fallbackWebSearch = fallbackPayload ? extractWebSearchMetadataFromPayload(fallbackPayload) : undefined;
            const fallbackMemory = fallbackPayload ? extractMemoryContextMetadataFromPayload(fallbackPayload) : undefined;
            const fallbackUsage = fallbackPayload ? extractUsageFromPayload(fallbackPayload) : undefined;
            if (!resolvedConversationId && fallbackConversationId) {
                resolvedConversationId = fallbackConversationId;
            }

            if (fallbackContent || fallbackCitations || fallbackWebSearch || fallbackMemory || fallbackUsage) {
                const chunk = emitChunk(
                    fallbackContent || '',
                    fallbackCitations,
                    fallbackWebSearch,
                    fallbackMemory,
                    fallbackUsage
                );
                if (fallbackConversationId) {
                    chunk.conversationId = fallbackConversationId;
                }
                yield chunk;
            }
        }
    } finally {
        reader.releaseLock();
    }
}

/**
 * Get conversations from local storage (via backend)
 */
export async function getLocalConversations(
    workspaceId?: string | null,
    limit: number = 50
): Promise<Conversation[]> {
    const params = new URLSearchParams();
    if (workspaceId !== undefined) {
        params.append('workspace_id', workspaceId ?? '');
    }
    params.append('limit', limit.toString());

    const response = await apiRequest<{ conversations: Conversation[] }>(
        `/v1/conversations?${params.toString()}`
    );
    return response.conversations;
}

/**
 * Get a conversation with messages from local storage
 */
export async function getLocalConversation(conversationId: string): Promise<{
    id: string;
    title: string;
    summary?: string | null;
    workspace_id: string | null;
    total_messages: number;
    total_tokens: number;
    created_at: string;
    updated_at: string;
    is_archived?: boolean;
    messages: ConversationMessage[];
}> {
    return apiRequest(`/v1/conversations/${conversationId}`);
}

// ===== Conversation & Workspace Types =====
export interface Conversation {
    id: string;
    title: string | null;
    summary: string | null;
    total_messages: number;
    workspace_id: string | null;
}

export interface ConversationMessage {
    id: string;
    role: 'user' | 'assistant' | 'system';
    content: string;
    model: string | null;
    token_count: number;
    sources?: string[] | null;
    citations?: WebCitation[] | null;
}

export interface Workspace {
    id: string;
    name: string;
    icon: string;
    color: string;
    parent_id: string | null;
    item_count: number;
}

export type KnowledgeContentType = 'note' | 'code' | 'doc' | 'link' | 'file';

export interface KnowledgeItem {
    id: string;
    workspace_id: string | null;
    title: string;
    content: string | null;
    content_type: KnowledgeContentType | string;
    is_favorite: boolean;
    is_pinned: boolean;
}

// ===== Conversation API =====

/**
 * Create a new conversation
 * @param workspaceId - Optional workspace ID. If null, creates standalone chat
 * @param title - Optional title for the conversation
 */
export async function createConversation(workspaceId?: string | null, title?: string): Promise<Conversation> {
    return apiRequest<Conversation>('/v1/conversations', {
        method: 'POST',
        body: JSON.stringify({
            workspace_id: workspaceId || null,
            title: title || null
        })
    });
}

/**
 * Get list of conversations
 * @param workspaceId - Optional filter by workspace. Pass null for standalone chats, undefined for all
 */
export async function getConversations(workspaceId?: string | null, limit: number = 50): Promise<Conversation[]> {
    const params = new URLSearchParams();
    if (workspaceId !== undefined) {
        params.append('workspace_id', workspaceId ?? '');
    }
    params.append('limit', limit.toString());

    const response = await apiRequest<{ conversations: Conversation[] }>(`/v1/conversations?${params.toString()}`);
    return response.conversations;
}

/**
 * Get a single conversation
 */
export async function getConversation(conversationId: string): Promise<Conversation> {
    const response = await apiRequest<Conversation & { messages?: ConversationMessage[] }>(`/v1/conversations/${conversationId}`);
    return {
        id: response.id,
        title: response.title,
        summary: response.summary ?? null,
        total_messages: response.total_messages,
        workspace_id: response.workspace_id
    };
}

/**
 * Delete a conversation
 */
export async function deleteConversation(conversationId: string): Promise<void> {
    await apiRequest<{ ok: boolean }>(`/v1/conversations/${conversationId}`, {
        method: 'DELETE'
    });
}

/**
 * Get messages for a conversation
 */
export async function getConversationMessages(conversationId: string, limit?: number): Promise<ConversationMessage[]> {
    const params = limit ? `?limit=${limit}` : '';
    const response = await apiRequest<{ messages: ConversationMessage[] }>(`/v1/conversations/${conversationId}/messages${params}`);
    return response.messages;
}

/**
 * Add a message to a conversation
 */
export async function addConversationMessage(
    conversationId: string,
    role: 'user' | 'assistant' | 'system',
    content: string,
    model?: string
): Promise<ConversationMessage> {
    return apiRequest<ConversationMessage>(`/v1/conversations/${conversationId}/messages`, {
        method: 'POST',
        body: JSON.stringify({ role, content, model })
    });
}

export async function updateConversationMessage(
    conversationId: string,
    messageId: string,
    content: string,
    model?: string
): Promise<ConversationMessage> {
    return apiRequest<ConversationMessage>(`/v1/conversations/${conversationId}/messages/${messageId}`, {
        method: 'PATCH',
        body: JSON.stringify({ content, model })
    });
}

// ===== Workspace API =====

/**
 * Create a new workspace
 */
export async function createWorkspace(name: string, icon: string = '📁', color: string = '#6366f1'): Promise<Workspace> {
    return apiRequest<Workspace>('/memory/workspaces', {
        method: 'POST',
        body: JSON.stringify({ name, icon, color })
    });
}

/**
 * Get list of workspaces
 */
export async function getWorkspaces(): Promise<Workspace[]> {
    return apiRequest<Workspace[]>('/memory/workspaces');
}

/**
 * Update a workspace
 */
export async function updateWorkspace(
    workspaceId: string,
    payload: {
        name?: string;
        icon?: string;
        color?: string;
    }
): Promise<Workspace> {
    return apiRequest<Workspace>(`/memory/workspaces/${workspaceId}`, {
        method: 'PATCH',
        body: JSON.stringify(payload)
    });
}

/**
 * Delete a workspace
 */
export async function deleteWorkspace(workspaceId: string): Promise<void> {
    await apiRequest<{ ok: boolean }>(`/memory/workspaces/${workspaceId}`, {
        method: 'DELETE'
    });
}

// ===== Knowledge API =====

export async function getKnowledgeItems(
    options: {
        workspaceId?: string | null;
        contentType?: string;
        favoritesOnly?: boolean;
    } = {}
): Promise<KnowledgeItem[]> {
    const params = new URLSearchParams();

    if (options.workspaceId) {
        params.append('workspace_id', options.workspaceId);
    }
    if (options.contentType) {
        params.append('content_type', options.contentType);
    }
    if (options.favoritesOnly) {
        params.append('favorites_only', 'true');
    }

    const query = params.toString();
    return apiRequest<KnowledgeItem[]>(`/memory/knowledge${query ? `?${query}` : ''}`);
}

export async function getKnowledgeItem(itemId: string): Promise<KnowledgeItem> {
    return apiRequest<KnowledgeItem>(`/memory/knowledge/${itemId}`);
}

export async function createKnowledgeItem(payload: {
    workspace_id: string;
    title: string;
    content?: string;
    content_type?: KnowledgeContentType | string;
}): Promise<KnowledgeItem> {
    return apiRequest<KnowledgeItem>('/memory/knowledge', {
        method: 'POST',
        body: JSON.stringify({
            workspace_id: payload.workspace_id,
            title: payload.title,
            content: payload.content ?? '',
            content_type: payload.content_type ?? 'note'
        })
    });
}

export async function updateKnowledgeItem(
    itemId: string,
    payload: {
        title?: string;
        content?: string;
        is_favorite?: boolean;
        is_pinned?: boolean;
    }
): Promise<KnowledgeItem> {
    return apiRequest<KnowledgeItem>(`/memory/knowledge/${itemId}`, {
        method: 'PATCH',
        body: JSON.stringify(payload)
    });
}

export async function deleteKnowledgeItem(itemId: string): Promise<void> {
    await apiRequest<{ ok: boolean }>(`/memory/knowledge/${itemId}`, {
        method: 'DELETE'
    });
}

// ===== Semantic Search API =====

export interface SemanticKnowledgeResult {
    id: string;
    title: string;
    content: string | null;
    content_type: string;
    similarity: number;
}

export async function searchKnowledgeSemantic(
    query: string,
    options: {
        workspaceId?: string | null;
        limit?: number;
        minSimilarity?: number;
    } = {}
): Promise<SemanticKnowledgeResult[]> {
    const params = new URLSearchParams();
    params.append('q', query);

    if (options.workspaceId) {
        params.append('workspace_id', options.workspaceId);
    }
    if (options.limit !== undefined) {
        params.append('limit', options.limit.toString());
    }
    if (options.minSimilarity !== undefined) {
        params.append('min_similarity', options.minSimilarity.toString());
    }

    return apiRequest<SemanticKnowledgeResult[]>(`/memory/search/semantic?${params.toString()}`);
}

// ===== V1 Memory Facts + Rules API =====

export interface MemoryFact {
    id: string;
    content: string;
    subject: string;
    predicate: string;
    object: string;
    category: string;
    confidence: number;
    source: string;
    source_conversation_id: string | null;
    workspace_id: string | null;
    scope: 'system' | 'workspace';
    access_count: number;
    created_at: string | null;
    updated_at: string | null;
    confirmed_at: string | null;
}

export interface MemoryListResponse {
    memories: MemoryFact[];
    total: number;
}

export interface MemoryStatsResponse {
    architecture: string;
    schema_version: number | null;
    storage: {
        db_size_bytes: number;
        brain_size_bytes: number;
        total_size_human?: string;
        fact_count: number;
        preference_count: number;
        conversation_count: number;
        message_count: number;
        knowledge_item_count?: number;
        workspace_count?: number;
        [key: string]: unknown;
    };
}

export interface MemoryReindexResponse {
    ok: boolean;
    scanned_conversations: number;
    processed_user_messages: number;
    skipped_messages: number;
    fact_count: number;
    preference_count: number;
}

export async function getMemoryFacts(
    options: {
        scope?: 'system' | 'workspace' | 'all';
        workspaceId?: string | null;
        subject?: string;
        category?: string;
        limit?: number;
    } = {}
): Promise<MemoryListResponse> {
    const params = new URLSearchParams();
    params.append('scope', options.scope ?? 'all');

    if (options.workspaceId !== undefined) {
        params.append('workspace_id', options.workspaceId ?? '');
    }
    if (options.subject) {
        params.append('subject', options.subject);
    }
    if (options.category) {
        params.append('category', options.category);
    }
    if (options.limit !== undefined) {
        params.append('limit', options.limit.toString());
    }

    return apiRequest<MemoryListResponse>(`/v1/memory/memories?${params.toString()}`);
}

export async function getMemoryStats(): Promise<MemoryStatsResponse> {
    return apiRequest<MemoryStatsResponse>('/v1/memory/stats');
}

export async function reindexMemoryFromConversations(payload?: {
    workspaceId?: string | null;
    maxConversations?: number;
    maxMessagesPerConversation?: number;
}): Promise<MemoryReindexResponse> {
    const params = new URLSearchParams();
    if (payload?.workspaceId !== undefined) {
        params.append('workspace_id', payload.workspaceId ?? '');
    }
    if (payload?.maxConversations !== undefined) {
        params.append('max_conversations', String(payload.maxConversations));
    }
    if (payload?.maxMessagesPerConversation !== undefined) {
        params.append('max_messages_per_conversation', String(payload.maxMessagesPerConversation));
    }
    const query = params.toString();
    return apiRequest<MemoryReindexResponse>(`/v1/memory/reindex${query ? `?${query}` : ''}`, {
        method: 'POST',
    });
}

export async function rememberMemoryFact(payload: {
    content: string;
    category?: string;
    subject?: string;
    predicate?: string;
    workspaceId?: string | null;
    conversationId?: string;
}): Promise<{ ok: boolean; memory: MemoryFact }> {
    const params = new URLSearchParams();
    params.append('content', payload.content);
    params.append('category', payload.category ?? 'explicit_memory');
    params.append('subject', payload.subject ?? 'User');
    params.append('predicate', payload.predicate ?? 'remembers');

    if (payload.workspaceId !== undefined) {
        params.append('workspace_id', payload.workspaceId ?? '');
    }
    if (payload.conversationId) {
        params.append('conversation_id', payload.conversationId);
    }

    return apiRequest<{ ok: boolean; memory: MemoryFact }>(
        `/v1/memory/remember?${params.toString()}`,
        { method: 'POST' }
    );
}

export async function updateMemoryFact(
    memoryId: string,
    payload: {
        subject?: string;
        predicate?: string;
        object?: string;
        category?: string;
        confidence?: number;
    }
): Promise<{ ok: boolean; memory: MemoryFact }> {
    const params = new URLSearchParams();
    if (payload.subject !== undefined) params.append('subject', payload.subject);
    if (payload.predicate !== undefined) params.append('predicate', payload.predicate);
    if (payload.object !== undefined) params.append('object', payload.object);
    if (payload.category !== undefined) params.append('category', payload.category);
    if (payload.confidence !== undefined) params.append('confidence', payload.confidence.toString());

    return apiRequest<{ ok: boolean; memory: MemoryFact }>(
        `/v1/memory/memories/${memoryId}${params.toString() ? `?${params.toString()}` : ''}`,
        { method: 'PATCH' }
    );
}

export async function deleteMemoryFact(memoryId: string): Promise<{ ok: boolean }> {
    return apiRequest<{ ok: boolean }>(`/v1/memory/memories/${memoryId}`, {
        method: 'DELETE'
    });
}

// ===== Fact & Preference Endpoints (Extracted Knowledge) =====

export interface ExtractedFact {
    id: string;
    content: string;
    subject: string;
    predicate: string;
    object: string;
    category: string;
    confidence: number;
    source: string;
    source_conversation_id: string | null;
    workspace_id: string | null;
    scope: string;
    access_count: number;
    created_at: string | null;
    updated_at: string | null;
    confirmed_at: string | null;
}

export interface ExtractedPreference {
    id: string;
    category: string;
    key: string;
    value: string;
    confidence: number;
    source_conversation_id: string | null;
    created_at: string | null;
    updated_at: string | null;
}

export async function getExtractedFacts(workspaceId?: string | null): Promise<ExtractedFact[]> {
    const params = new URLSearchParams();
    if (workspaceId !== undefined) {
        params.append('workspace_id', workspaceId ?? '');
    }
    const query = params.toString();
    try {
        return await apiRequest<ExtractedFact[]>(`/memory/facts${query ? `?${query}` : ''}`);
    } catch {
        const fallback = await getMemoryFacts({
            scope: workspaceId !== undefined && workspaceId !== null ? 'workspace' : 'all',
            workspaceId,
            limit: 500
        });
        return (fallback.memories || [])
            .filter(item => (item.source || '').toLowerCase() !== 'user_input')
            .map(item => ({
                id: item.id,
                content: item.content,
                subject: item.subject,
                predicate: item.predicate,
                object: item.object,
                category: item.category,
                confidence: item.confidence,
                source: item.source,
                source_conversation_id: item.source_conversation_id,
                workspace_id: item.workspace_id,
                scope: item.scope,
                access_count: item.access_count,
                created_at: item.created_at,
                updated_at: item.updated_at,
                confirmed_at: item.confirmed_at,
            }));
    }
}

export async function deleteExtractedFact(factId: string): Promise<void> {
    await apiRequest<{ ok: boolean }>(`/memory/facts/${factId}`, { method: 'DELETE' });
}

export async function getExtractedPreferences(category?: string): Promise<ExtractedPreference[]> {
    const params = new URLSearchParams();
    if (category) {
        params.append('category', category);
    }
    const query = params.toString();
    try {
        return await apiRequest<ExtractedPreference[]>(`/memory/preferences${query ? `?${query}` : ''}`);
    } catch {
        const fallback = await getMemoryFacts({ scope: 'all', category: category || undefined, limit: 500 });
        return (fallback.memories || [])
            .filter(item => (item.source || '').toLowerCase() !== 'user_input')
            .filter(item => {
                const normalizedCategory = (item.category || '').toLowerCase();
                const normalizedPredicate = (item.predicate || '').toLowerCase();
                return normalizedCategory.includes('preference') || normalizedPredicate.includes('prefer');
            })
            .map(item => ({
                id: item.id,
                category: item.category || 'preference',
                key: (item.predicate || 'preference').trim() || 'preference',
                value: (item.object || item.content || '').trim(),
                confidence: item.confidence,
                source_conversation_id: item.source_conversation_id,
                created_at: item.created_at,
                updated_at: item.updated_at,
            }));
    }
}

export async function deleteExtractedPreference(prefId: string): Promise<void> {
    await apiRequest<{ ok: boolean }>(`/memory/preferences/${prefId}`, { method: 'DELETE' });
}

export interface RulesResponse {
    rules: string;
    path: string;
    tokens: number;
}

export async function getRules(workspaceId?: string | null): Promise<RulesResponse> {
    const params = new URLSearchParams();
    if (workspaceId !== undefined) {
        params.append('workspace_id', workspaceId ?? '');
    }
    const query = params.toString();
    return apiRequest<RulesResponse>(`/v1/rules${query ? `?${query}` : ''}`);
}

export async function updateRules(
    content: string,
    workspaceId?: string | null
): Promise<{ ok: boolean; path: string }> {
    const params = new URLSearchParams();
    params.append('content', content);
    if (workspaceId !== undefined) {
        params.append('workspace_id', workspaceId ?? '');
    }
    return apiRequest<{ ok: boolean; path: string }>(`/v1/rules?${params.toString()}`, {
        method: 'PUT'
    });
}
