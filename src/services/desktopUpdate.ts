export type DesktopUpdateStatus = 'idle' | 'checking' | 'up_to_date' | 'update_available' | 'error'

export interface DesktopUpdateManifest {
    product?: string
    channel?: string
    platform?: string
    version: string
    downloadPageUrl: string
    installerUrl: string | null
    publishedAt: string | null
    releaseNotes: string | null
    requiresManualInstall: boolean
    upgradeBehavior: string | null
}

export interface DesktopUpdateCheckResponse {
    currentVersion: string
    checkedAt: string
    manifest: DesktopUpdateManifest | null
}

export interface DesktopUpdateState {
    status: DesktopUpdateStatus
    currentVersion: string
    latestVersion: string | null
    updateAvailable: boolean
    downloadPageUrl: string | null
    installerUrl: string | null
    publishedAt: string | null
    releaseNotes: string | null
    checkedAt: string | null
    errorMessage: string | null
    requiresManualInstall: boolean
    upgradeBehavior: string | null
}

type ParsedVersion = {
    core: [number, number, number]
    prerelease: string[]
}

export const IDLE_DESKTOP_UPDATE_STATE: DesktopUpdateState = {
    status: 'idle',
    currentVersion: '',
    latestVersion: null,
    updateAvailable: false,
    downloadPageUrl: null,
    installerUrl: null,
    publishedAt: null,
    releaseNotes: null,
    checkedAt: null,
    errorMessage: null,
    requiresManualInstall: true,
    upgradeBehavior: null
}

const ENV_PIGTEX_API_BASE =
    typeof import.meta !== 'undefined'
        ? (import.meta.env.VITE_PIGTEX_API_BASE as string | undefined)
        : undefined

function isNonEmptyString(value: unknown): value is string {
    return typeof value === 'string' && value.trim().length > 0
}

function normalizeOptionalString(value: unknown): string | null {
    return isNonEmptyString(value) ? value.trim() : null
}

function normalizeHttpUrl(value: unknown): string | null {
    if (!isNonEmptyString(value)) {
        return null
    }

    try {
        const parsed = new URL(value.trim())
        if (parsed.protocol !== 'http:' && parsed.protocol !== 'https:') {
            return null
        }
        return parsed.toString()
    } catch {
        return null
    }
}

export function resolveDesktopUpdateManifestUrl(apiBaseUrl: string | undefined): string | null {
    if (!isNonEmptyString(apiBaseUrl)) {
        return null
    }

    try {
        const parsed = new URL(apiBaseUrl.trim())
        if (parsed.protocol !== 'http:' && parsed.protocol !== 'https:') {
            return null
        }

        const normalizedPath = parsed.pathname.replace(/\/+$/, '')
        const rootPath = normalizedPath.endsWith('/api')
            ? normalizedPath.slice(0, -4)
            : normalizedPath
        const basePath = rootPath && rootPath !== '/' ? rootPath : ''
        return `${parsed.origin}${basePath}/api/desktop/latest`
    } catch {
        return null
    }
}

export const DEFAULT_RENDERER_DESKTOP_UPDATE_MANIFEST_URL = resolveDesktopUpdateManifestUrl(ENV_PIGTEX_API_BASE)

function hasOnlyAsciiLettersDigitsOrHyphen(value: string): boolean {
    if (!value) {
        return false
    }

    for (const char of value) {
        const code = char.charCodeAt(0)
        const isDigit = code >= 48 && code <= 57
        const isUpper = code >= 65 && code <= 90
        const isLower = code >= 97 && code <= 122
        if (!isDigit && !isUpper && !isLower && char !== '-') {
            return false
        }
    }

    return true
}

function hasOnlyDigits(value: string): boolean {
    if (!value) {
        return false
    }

    for (const char of value) {
        const code = char.charCodeAt(0)
        if (code < 48 || code > 57) {
            return false
        }
    }

    return true
}

function parseVersion(value: string): ParsedVersion | null {
    const trimmed = value.trim()
    if (!trimmed) {
        return null
    }

    const withoutPrefix = trimmed.startsWith('v') ? trimmed.slice(1) : trimmed
    const [mainAndPrerelease, buildMetadata = ''] = withoutPrefix.split('+', 2)
    if (!mainAndPrerelease) {
        return null
    }

    if (buildMetadata) {
        const buildParts = buildMetadata.split('.')
        if (buildParts.some((part) => !hasOnlyAsciiLettersDigitsOrHyphen(part))) {
            return null
        }
    }

    const [corePart, prereleasePart = ''] = mainAndPrerelease.split('-', 2)
    const coreParts = corePart.split('.')
    if (coreParts.length === 0 || coreParts.length > 3) {
        return null
    }
    if (coreParts.some((part) => !hasOnlyDigits(part))) {
        return null
    }

    const prerelease = prereleasePart
        ? prereleasePart
            .split('.')
            .map((part) => part.trim())
            .filter(Boolean)
        : []

    if (prereleasePart && prerelease.length === 0) {
        return null
    }
    if (prerelease.some((part) => !hasOnlyAsciiLettersDigitsOrHyphen(part))) {
        return null
    }

    return {
        core: [
            Number(coreParts[0] || '0'),
            Number(coreParts[1] || '0'),
            Number(coreParts[2] || '0')
        ],
        prerelease
    }
}

function comparePrereleaseIdentifiers(left: string[], right: string[]): number {
    if (left.length === 0 && right.length === 0) return 0
    if (left.length === 0) return 1
    if (right.length === 0) return -1

    const maxLength = Math.max(left.length, right.length)
    for (let index = 0; index < maxLength; index += 1) {
        const leftPart = left[index]
        const rightPart = right[index]

        if (leftPart === undefined) return -1
        if (rightPart === undefined) return 1

        const leftNumber = Number(leftPart)
        const rightNumber = Number(rightPart)
        const leftIsNumber = /^\d+$/.test(leftPart)
        const rightIsNumber = /^\d+$/.test(rightPart)

        if (leftIsNumber && rightIsNumber) {
            if (leftNumber !== rightNumber) {
                return leftNumber > rightNumber ? 1 : -1
            }
            continue
        }

        if (leftIsNumber !== rightIsNumber) {
            return leftIsNumber ? -1 : 1
        }

        const lexical = leftPart.localeCompare(rightPart)
        if (lexical !== 0) {
            return lexical > 0 ? 1 : -1
        }
    }

    return 0
}

export function compareSemanticVersions(leftVersion: string, rightVersion: string): number {
    const left = parseVersion(leftVersion)
    const right = parseVersion(rightVersion)

    if (!left || !right) {
        const normalizedLeft = leftVersion.trim()
        const normalizedRight = rightVersion.trim()
        if (normalizedLeft === normalizedRight) return 0
        return normalizedLeft.localeCompare(normalizedRight, undefined, { numeric: true }) > 0 ? 1 : -1
    }

    for (let index = 0; index < left.core.length; index += 1) {
        if (left.core[index] !== right.core[index]) {
            return left.core[index] > right.core[index] ? 1 : -1
        }
    }

    return comparePrereleaseIdentifiers(left.prerelease, right.prerelease)
}

export function normalizeDesktopUpdateManifest(raw: unknown): DesktopUpdateManifest | null {
    if (!raw || typeof raw !== 'object') {
        return null
    }

    const value = raw as Record<string, unknown>
    const version = normalizeOptionalString(value.version)
    const downloadPageUrl = normalizeHttpUrl(value.downloadPageUrl)
    const installerUrl = normalizeHttpUrl(value.installerUrl)

    if (!version || (!downloadPageUrl && !installerUrl)) {
        return null
    }

    return {
        product: normalizeOptionalString(value.product) || undefined,
        channel: normalizeOptionalString(value.channel) || undefined,
        platform: normalizeOptionalString(value.platform) || undefined,
        version,
        downloadPageUrl: downloadPageUrl || installerUrl!,
        installerUrl,
        publishedAt: normalizeOptionalString(value.publishedAt),
        releaseNotes: normalizeOptionalString(value.releaseNotes),
        requiresManualInstall: value.requiresManualInstall !== false,
        upgradeBehavior: normalizeOptionalString(value.upgradeBehavior)
    }
}

export function createDesktopUpdateErrorState(currentVersion: string, error: unknown): DesktopUpdateState {
    const message = error instanceof Error && error.message.trim()
        ? error.message
        : 'Unable to check for PigTex updates'

    return {
        ...IDLE_DESKTOP_UPDATE_STATE,
        status: 'error',
        currentVersion: currentVersion.trim(),
        checkedAt: new Date().toISOString(),
        errorMessage: message
    }
}

export function createDesktopUpdateStateFromResponse(response: DesktopUpdateCheckResponse): DesktopUpdateState {
    const currentVersion = response.currentVersion.trim()
    const checkedAt = normalizeOptionalString(response.checkedAt) || new Date().toISOString()
    const manifest = normalizeDesktopUpdateManifest(response.manifest)

    if (!manifest) {
        return {
            ...IDLE_DESKTOP_UPDATE_STATE,
            status: response.manifest ? 'error' : 'up_to_date',
            currentVersion,
            latestVersion: currentVersion || null,
            checkedAt,
            errorMessage: response.manifest ? 'Update metadata is invalid' : null
        }
    }

    const updateAvailable = compareSemanticVersions(currentVersion, manifest.version) < 0

    return {
        status: updateAvailable ? 'update_available' : 'up_to_date',
        currentVersion,
        latestVersion: manifest.version,
        updateAvailable,
        downloadPageUrl: manifest.downloadPageUrl,
        installerUrl: manifest.installerUrl,
        publishedAt: manifest.publishedAt,
        releaseNotes: manifest.releaseNotes,
        checkedAt,
        errorMessage: null,
        requiresManualInstall: manifest.requiresManualInstall,
        upgradeBehavior: manifest.upgradeBehavior
    }
}
