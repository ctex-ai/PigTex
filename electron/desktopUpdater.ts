import { app, shell } from 'electron'
import http from 'http'
import https from 'https'
import electronUpdater from 'electron-updater'

const { autoUpdater } = electronUpdater

const DEFAULT_DESKTOP_UPDATE_MANIFEST_URL = 'https://example.com/api/desktop/latest'
const GITHUB_RELEASES_LATEST_API_URL = 'https://api.github.com/repos/ctex-ai/PigTex/releases/latest'
const GITHUB_RELEASES_LATEST_PAGE_URL = 'https://github.com/ctex-ai/PigTex/releases/latest'

export type RemoteDesktopUpdateManifest = {
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

export type DesktopUpdateCheckResult = {
    currentVersion: string
    checkedAt: string
    manifest: RemoteDesktopUpdateManifest | null
}

export type DesktopUpdateInstallResult =
    | {
        status: 'up_to_date'
        currentVersion: string
    }
    | {
        status: 'installing'
        currentVersion: string
        version: string
    }
    | {
        status: 'opened'
        currentVersion: string
        version: string
        downloadPageUrl: string
    }

let autoUpdaterConfigured = false

type GitHubReleaseAsset = {
    name: string
    browser_download_url: string
}

type GitHubLatestReleasePayload = {
    tag_name: string
    html_url: string | null
    published_at: string | null
    body: string | null
    draft: boolean
    prerelease: boolean
    assets: GitHubReleaseAsset[]
}

function normalizeOptionalNonEmptyString(value: unknown): string | null {
    if (typeof value !== 'string') {
        return null
    }

    const normalized = value.trim()
    return normalized || null
}

function normalizePublicHttpUrl(value: unknown): string | null {
    const normalized = normalizeOptionalNonEmptyString(value)
    if (!normalized) {
        return null
    }

    try {
        const parsedUrl = new URL(normalized)
        if (parsedUrl.protocol !== 'http:' && parsedUrl.protocol !== 'https:') {
            return null
        }
        return parsedUrl.toString()
    } catch {
        return null
    }
}

function envFlagEnabled(value: string | undefined): boolean {
    if (!value) {
        return false
    }

    const normalized = value.trim().toLowerCase()
    return normalized === '1' || normalized === 'true' || normalized === 'yes'
}

function getDesktopUpdateManifestUrl(overrideUrl?: unknown): string {
    const requestedUrl = normalizePublicHttpUrl(overrideUrl)
    const configuredUrl = normalizePublicHttpUrl(process.env.PIGTEX_DESKTOP_UPDATE_MANIFEST_URL)
    return requestedUrl || configuredUrl || DEFAULT_DESKTOP_UPDATE_MANIFEST_URL
}

function fetchRemoteText(urlString: string, acceptHeader: string, redirectCount = 0): Promise<string> {
    return new Promise((resolve, reject) => {
        let request: http.ClientRequest | null = null

        try {
            const requestUrl = new URL(urlString)
            const client = requestUrl.protocol === 'http:' ? http : https
            request = client.get(
                requestUrl,
                {
                    headers: {
                        Accept: acceptHeader,
                        'User-Agent': `PigTex/${app.getVersion()}`
                    }
                },
                (response) => {
                    const statusCode = response.statusCode ?? 0
                    const location = response.headers.location

                    if ([301, 302, 303, 307, 308].includes(statusCode) && location) {
                        response.resume()
                        if (redirectCount >= 3) {
                            reject(new Error('Update request redirected too many times'))
                            return
                        }
                        const nextUrl = new URL(location, requestUrl).toString()
                        resolve(fetchRemoteText(nextUrl, acceptHeader, redirectCount + 1))
                        return
                    }

                    if (statusCode === 404) {
                        response.resume()
                        reject(new Error('Update metadata was not found'))
                        return
                    }

                    if (statusCode < 200 || statusCode >= 300) {
                        response.resume()
                        reject(new Error(`Update request failed with status ${statusCode}`))
                        return
                    }

                    const chunks: Buffer[] = []
                    response.on('data', (chunk) => {
                        chunks.push(Buffer.isBuffer(chunk) ? chunk : Buffer.from(chunk))
                    })
                    response.on('end', () => {
                        resolve(Buffer.concat(chunks).toString('utf8'))
                    })
                }
            )

            request.setTimeout(15000, () => {
                request?.destroy(new Error('Update request timed out'))
            })
            request.on('error', reject)
        } catch (error) {
            request?.destroy()
            reject(error)
        }
    })
}

function sanitizeRemoteDesktopUpdateManifest(raw: unknown): RemoteDesktopUpdateManifest {
    if (!raw || typeof raw !== 'object') {
        throw new Error('Update manifest payload is invalid')
    }

    const value = raw as Record<string, unknown>
    const version = normalizeOptionalNonEmptyString(value.version)
    const downloadPageUrl = normalizePublicHttpUrl(value.downloadPageUrl)
    const installerUrl = normalizePublicHttpUrl(value.installerUrl)

    if (!version) {
        throw new Error('Update manifest is missing version')
    }
    if (!downloadPageUrl && !installerUrl) {
        throw new Error('Update manifest is missing download URL')
    }

    return {
        product: normalizeOptionalNonEmptyString(value.product) || undefined,
        channel: normalizeOptionalNonEmptyString(value.channel) || undefined,
        platform: normalizeOptionalNonEmptyString(value.platform) || undefined,
        version,
        downloadPageUrl: downloadPageUrl || installerUrl!,
        installerUrl,
        publishedAt: normalizeOptionalNonEmptyString(value.publishedAt),
        releaseNotes: normalizeOptionalNonEmptyString(value.releaseNotes),
        requiresManualInstall: value.requiresManualInstall !== false,
        upgradeBehavior: normalizeOptionalNonEmptyString(value.upgradeBehavior)
    }
}

async function fetchDesktopUpdateManifest(overrideUrl?: unknown): Promise<RemoteDesktopUpdateManifest> {
    const manifestUrl = getDesktopUpdateManifestUrl(overrideUrl)
    const payload = await fetchRemoteText(manifestUrl, 'application/json')
    const parsedPayload = JSON.parse(payload)
    return sanitizeRemoteDesktopUpdateManifest(parsedPayload)
}

function normalizeGitHubReleaseAsset(raw: unknown): GitHubReleaseAsset | null {
    if (!raw || typeof raw !== 'object') {
        return null
    }

    const value = raw as Record<string, unknown>
    const name = normalizeOptionalNonEmptyString(value.name)
    const browserDownloadUrl = normalizePublicHttpUrl(value.browser_download_url)
    if (!name || !browserDownloadUrl) {
        return null
    }

    return {
        name,
        browser_download_url: browserDownloadUrl
    }
}

function normalizeGitHubReleasePayload(raw: unknown): GitHubLatestReleasePayload | null {
    if (!raw || typeof raw !== 'object') {
        return null
    }

    const value = raw as Record<string, unknown>
    const tagName = normalizeOptionalNonEmptyString(value.tag_name)
    if (!tagName) {
        return null
    }

    const assetsRaw = Array.isArray(value.assets) ? value.assets : []
    const assets = assetsRaw
        .map(normalizeGitHubReleaseAsset)
        .filter((asset): asset is GitHubReleaseAsset => Boolean(asset))

    return {
        tag_name: tagName,
        html_url: normalizePublicHttpUrl(value.html_url),
        published_at: normalizeOptionalNonEmptyString(value.published_at),
        body: normalizeOptionalNonEmptyString(value.body),
        draft: value.draft === true,
        prerelease: value.prerelease === true,
        assets
    }
}

function parseVersionCore(value: string): [number, number, number] {
    const trimmed = value.trim()
    if (!trimmed) {
        return [0, 0, 0]
    }

    const withoutPrefix = trimmed.startsWith('v') ? trimmed.slice(1) : trimmed
    const mainPart = withoutPrefix.split('-', 2)[0]?.split('+', 2)[0] || ''
    const rawParts = mainPart.split('.').slice(0, 3)
    if (rawParts.length === 0) {
        return [0, 0, 0]
    }

    const parts = rawParts.map((part) => {
        if (!part) {
            return null
        }
        for (const char of part) {
            const code = char.charCodeAt(0)
            if (code < 48 || code > 57) {
                return null
            }
        }
        return Number(part)
    })

    if (parts.some((part) => part === null)) {
        return [0, 0, 0]
    }

    return [
        parts[0] ?? 0,
        parts[1] ?? 0,
        parts[2] ?? 0
    ]
}

function compareVersionCore(leftVersion: string, rightVersion: string): number {
    const left = parseVersionCore(leftVersion)
    const right = parseVersionCore(rightVersion)

    for (let index = 0; index < left.length; index += 1) {
        if (left[index] !== right[index]) {
            return left[index] > right[index] ? 1 : -1
        }
    }

    return 0
}

function createGitHubReleasePageUrl(version: string): string {
    return `https://github.com/ctex-ai/PigTex/releases/tag/v${version}`
}

function findStableInstallerAsset(assets: GitHubReleaseAsset[], version: string): GitHubReleaseAsset | null {
    const expectedName = `PigTex-${version}.exe`.toLowerCase()
    return assets.find((asset) => asset.name.trim().toLowerCase() === expectedName) || null
}

async function fetchLatestGitHubReleaseManifest(): Promise<RemoteDesktopUpdateManifest | null> {
    const payload = await fetchRemoteText(
        GITHUB_RELEASES_LATEST_API_URL,
        'application/vnd.github+json'
    )
    const parsedPayload = normalizeGitHubReleasePayload(JSON.parse(payload))
    if (!parsedPayload || parsedPayload.draft) {
        return null
    }

    const version = parsedPayload.tag_name.replace(/^v/i, '').trim()
    if (!version) {
        return null
    }

    const installerAsset = findStableInstallerAsset(parsedPayload.assets, version)
    const downloadPageUrl =
        parsedPayload.html_url
        || createGitHubReleasePageUrl(version)
        || GITHUB_RELEASES_LATEST_PAGE_URL

    return {
        product: 'PigTex',
        channel: parsedPayload.prerelease ? 'preview' : 'stable',
        platform: process.platform,
        version,
        downloadPageUrl,
        installerUrl: installerAsset?.browser_download_url || null,
        publishedAt: parsedPayload.published_at,
        releaseNotes: parsedPayload.body,
        requiresManualInstall: false,
        upgradeBehavior: 'github-releases-auto'
    }
}

function shouldUseGitHubReleaseUpdater(): boolean {
    if (envFlagEnabled(process.env.PIGTEX_FORCE_LEGACY_DESKTOP_UPDATER)) {
        return false
    }

    return process.platform === 'win32' && app.isPackaged
}

function ensureAutoUpdaterConfigured(): void {
    if (autoUpdaterConfigured) {
        return
    }

    autoUpdater.autoDownload = false
    autoUpdater.autoInstallOnAppQuit = false
    autoUpdaterConfigured = true
}

async function openDesktopUpdateWebsite(overrideUrl?: unknown): Promise<DesktopUpdateInstallResult> {
    const manifest = await fetchDesktopUpdateManifest(overrideUrl)
    const currentVersion = app.getVersion()
    if (compareVersionCore(currentVersion, manifest.version) >= 0) {
        return {
            status: 'up_to_date',
            currentVersion
        }
    }

    await shell.openExternal(manifest.downloadPageUrl)

    return {
        status: 'opened',
        currentVersion,
        version: manifest.version,
        downloadPageUrl: manifest.downloadPageUrl
    }
}

async function downloadAndInstallGitHubRelease(): Promise<DesktopUpdateInstallResult> {
    ensureAutoUpdaterConfigured()

    const currentVersion = app.getVersion()
    const result = await autoUpdater.checkForUpdates()
    const updateInfo = result?.updateInfo

    if (!updateInfo || compareVersionCore(currentVersion, updateInfo.version) >= 0) {
        return {
            status: 'up_to_date',
            currentVersion
        }
    }

    await autoUpdater.downloadUpdate()

    setTimeout(() => {
        try {
            autoUpdater.quitAndInstall(false, true)
        } catch (error) {
            console.error('Failed to finalize desktop update installation:', error)
        }
    }, 500)

    return {
        status: 'installing',
        currentVersion,
        version: updateInfo.version
    }
}

export async function checkDesktopUpdate(overrideUrl?: unknown): Promise<DesktopUpdateCheckResult> {
    const currentVersion = app.getVersion()
    const checkedAt = new Date().toISOString()

    if (shouldUseGitHubReleaseUpdater()) {
        try {
            const manifest = await fetchLatestGitHubReleaseManifest()
            return {
                currentVersion,
                checkedAt,
                manifest
            }
        } catch (error) {
            const hasLegacyOverride =
                Boolean(normalizePublicHttpUrl(overrideUrl))
                || Boolean(normalizePublicHttpUrl(process.env.PIGTEX_DESKTOP_UPDATE_MANIFEST_URL))

            if (!hasLegacyOverride) {
                throw error
            }
        }
    }

    return {
        currentVersion,
        checkedAt,
        manifest: await fetchDesktopUpdateManifest(overrideUrl)
    }
}

export async function downloadAndInstallDesktopUpdate(overrideUrl?: unknown): Promise<DesktopUpdateInstallResult> {
    if (shouldUseGitHubReleaseUpdater()) {
        try {
            return await downloadAndInstallGitHubRelease()
        } catch (error) {
            console.error('GitHub release auto-update failed, falling back to manual update flow:', error)

            const manifest = await fetchLatestGitHubReleaseManifest().catch(() => null)
            const currentVersion = app.getVersion()
            if (manifest && compareVersionCore(currentVersion, manifest.version) < 0) {
                await shell.openExternal(manifest.downloadPageUrl)
                return {
                    status: 'opened',
                    currentVersion,
                    version: manifest.version,
                    downloadPageUrl: manifest.downloadPageUrl
                }
            }
        }
    }

    return openDesktopUpdateWebsite(overrideUrl)
}
