import { app, BrowserWindow, dialog, ipcMain, safeStorage, shell } from 'electron'
import { cpSync, existsSync, mkdirSync, promises as fs, readFileSync, readdirSync, renameSync, rmSync, writeFileSync } from 'fs'
import os from 'os'
import path from 'path'
import { checkDesktopUpdate, downloadAndInstallDesktopUpdate } from './desktopUpdater'

type FsEntry = {
    name: string
    path: string
    type: 'file' | 'directory'
    size: number
    mtimeMs: number
}

const allowedRoots = new Set<string>()
const MAX_TEXT_FILE_SIZE = 2 * 1024 * 1024 // 2MB
const FS_TRASH_DIR_NAME = '.pigtex-trash'
const FS_HISTORY_DIR_NAME = '__history__'
const MAX_UNDO_HISTORY = 200
const LOCAL_SESSION_STATE_FILE_NAME = 'local-session-state.json'
const SECURE_API_KEYS_FILE_NAME = 'secure-api-keys.bin'
const SECURE_AUTH_TOKEN_FILE_NAME = 'secure-auth-token.bin'
const SECURE_API_PROVIDER_IDS = ['auto', 'openai', 'anthropic', 'gemini', 'alibaba'] as const
const STABLE_USER_DATA_DIR_NAME = 'PigTex'
const LEGACY_USER_DATA_MARKER_FILE_NAMES = [
    LOCAL_SESSION_STATE_FILE_NAME,
    SECURE_API_KEYS_FILE_NAME,
    SECURE_AUTH_TOKEN_FILE_NAME
] as const

type SecureApiProviderId = typeof SECURE_API_PROVIDER_IDS[number]
type SecureApiKeyMap = Partial<Record<SecureApiProviderId, string>>

type FsUndoEntry =
    | {
        kind: 'delete-path'
        targetPath: string
        description: string
    }
    | {
        kind: 'restore-file-content'
        targetPath: string
        backupPath: string
        description: string
    }
    | {
        kind: 'rename'
        fromPath: string
        toPath: string
        description: string
    }
    | {
        kind: 'restore-from-trash'
        trashPath: string
        originalPath: string
        description: string
    }

type FsUndoEffect =
    | {
        type: 'deleted'
        targetPath: string
    }
    | {
        type: 'content_restored'
        targetPath: string
    }
    | {
        type: 'rename'
        oldPath: string
        newPath: string
    }
    | {
        type: 'restored'
        targetPath: string
    }

const undoStack: FsUndoEntry[] = []

type LocalSessionState = {
    rootPath: string | null
    filePath: string | null
    fileName: string | null
}

const DEFAULT_LOCAL_SESSION_STATE: LocalSessionState = {
    rootPath: null,
    filePath: null,
    fileName: null
}

let mainWindow: BrowserWindow | null = null
let localSessionStateCache: LocalSessionState | null = null

function normalizePathForComparison(inputPath: string): string {
    return path.resolve(inputPath).replace(/[\\/]+$/, '').toLowerCase()
}

function hasDirectoryContentsSync(targetPath: string): boolean {
    if (!existsSync(targetPath)) {
        return false
    }
    try {
        return readdirSync(targetPath).length > 0
    } catch {
        return false
    }
}

function hasLegacyUserDataMarkersSync(targetPath: string): boolean {
    return LEGACY_USER_DATA_MARKER_FILE_NAMES.some((fileName) => (
        existsSync(path.join(targetPath, fileName))
    ))
}

function collectCandidateLegacyUserDataPaths(baseDir: string): string[] {
    if (!baseDir || !existsSync(baseDir)) {
        return []
    }

    try {
        return readdirSync(baseDir, { withFileTypes: true })
            .filter((entry) => entry.isDirectory())
            .map((entry) => path.resolve(path.join(baseDir, entry.name)))
    } catch {
        return []
    }
}

function configureStableUserDataPath(): void {
    try {
        const appDataPath = app.getPath('appData')
        const defaultUserDataPath = path.resolve(app.getPath('userData'))
        const stableUserDataPath = path.resolve(path.join(appDataPath, STABLE_USER_DATA_DIR_NAME))
        const normalizedDefaultPath = normalizePathForComparison(defaultUserDataPath)
        const normalizedStablePath = normalizePathForComparison(stableUserDataPath)
        const candidateBaseDirs = [appDataPath]
        const localAppDataPath = (process.env.LOCALAPPDATA || '').trim()
        if (localAppDataPath) {
            candidateBaseDirs.push(localAppDataPath)
        }

        if (normalizePathForComparison(defaultUserDataPath) !== normalizedStablePath) {
            app.setPath('userData', stableUserDataPath)
        }

        if (hasDirectoryContentsSync(stableUserDataPath)) {
            return
        }

        const candidateSourcePaths = Array.from(new Set([
            defaultUserDataPath,
            ...candidateBaseDirs.flatMap((baseDir) => collectCandidateLegacyUserDataPaths(baseDir)),
        ]))
        const sourcePath = candidateSourcePaths.find((candidatePath) => {
            const normalizedCandidatePath = normalizePathForComparison(candidatePath)
            return normalizedCandidatePath !== normalizedStablePath
                && hasDirectoryContentsSync(candidatePath)
                && (
                    hasLegacyUserDataMarkersSync(candidatePath)
                    || normalizedCandidatePath === normalizedDefaultPath
                )
        })
        if (!sourcePath) {
            return
        }

        mkdirSync(stableUserDataPath, { recursive: true })
        cpSync(sourcePath, stableUserDataPath, {
            recursive: true,
            errorOnExist: false,
            force: false,
        })
        console.log(`Migrated desktop user data from "${sourcePath}" to "${stableUserDataPath}"`)
    } catch (error) {
        console.error('Failed to configure stable user data path:', error)
    }
}

configureStableUserDataPath()

function isSecureStorageAvailable(): boolean {
    try {
        return safeStorage.isEncryptionAvailable()
    } catch {
        return false
    }
}

function sanitizeSecureApiKeyMap(raw: unknown): SecureApiKeyMap {
    if (!raw || typeof raw !== 'object') {
        return {}
    }

    const normalized: SecureApiKeyMap = {}
    const value = raw as Record<string, unknown>
    for (const providerId of SECURE_API_PROVIDER_IDS) {
        const candidate = value[providerId]
        if (typeof candidate === 'string' && candidate.trim()) {
            normalized[providerId] = candidate.trim()
        }
    }
    return normalized
}

function sanitizeSecureAuthToken(raw: unknown): string | null {
    if (typeof raw !== 'string') {
        return null
    }

    const normalized = raw.trim()
    return normalized || null
}

function getSecureApiKeysFilePath(): string {
    return path.join(app.getPath('userData'), SECURE_API_KEYS_FILE_NAME)
}

function getSecureAuthTokenFilePath(): string {
    return path.join(app.getPath('userData'), SECURE_AUTH_TOKEN_FILE_NAME)
}

function readSecureApiKeyMapSync(): SecureApiKeyMap {
    if (!isSecureStorageAvailable()) {
        return {}
    }

    const secureApiKeysFilePath = getSecureApiKeysFilePath()
    if (!existsSync(secureApiKeysFilePath)) {
        return {}
    }

    try {
        const encodedPayload = readFileSync(secureApiKeysFilePath, 'utf8').trim()
        if (!encodedPayload) {
            return {}
        }
        const encryptedPayload = Buffer.from(encodedPayload, 'base64')
        const decryptedPayload = safeStorage.decryptString(encryptedPayload)
        const parsedPayload = JSON.parse(decryptedPayload)
        return sanitizeSecureApiKeyMap(parsedPayload)
    } catch (error) {
        console.error('Failed to read secure API keys:', error)
        return {}
    }
}

function readSecureAuthTokenSync(): string | null {
    if (!isSecureStorageAvailable()) {
        return null
    }

    const secureAuthTokenFilePath = getSecureAuthTokenFilePath()
    if (!existsSync(secureAuthTokenFilePath)) {
        return null
    }

    try {
        const encodedPayload = readFileSync(secureAuthTokenFilePath, 'utf8').trim()
        if (!encodedPayload) {
            return null
        }
        const encryptedPayload = Buffer.from(encodedPayload, 'base64')
        const decryptedPayload = safeStorage.decryptString(encryptedPayload)
        return sanitizeSecureAuthToken(decryptedPayload)
    } catch (error) {
        console.error('Failed to read secure auth token:', error)
        return null
    }
}

function writeSecureApiKeyMapSync(raw: unknown): SecureApiKeyMap {
    const normalized = sanitizeSecureApiKeyMap(raw)
    const secureApiKeysFilePath = getSecureApiKeysFilePath()
    const tempFilePath = `${secureApiKeysFilePath}.tmp`

    if (Object.keys(normalized).length === 0) {
        rmSync(tempFilePath, { force: true })
        rmSync(secureApiKeysFilePath, { force: true })
        return {}
    }

    if (!isSecureStorageAvailable()) {
        throw new Error('Secure credential storage is unavailable on this device')
    }

    try {
        mkdirSync(path.dirname(secureApiKeysFilePath), { recursive: true })
        const encryptedPayload = safeStorage.encryptString(JSON.stringify(normalized))
        writeFileSync(tempFilePath, encryptedPayload.toString('base64'), { encoding: 'utf8' })
        rmSync(secureApiKeysFilePath, { force: true })
        renameSync(tempFilePath, secureApiKeysFilePath)
        return normalized
    } finally {
        rmSync(tempFilePath, { force: true })
    }
}

function writeSecureAuthTokenSync(raw: unknown): string | null {
    const normalized = sanitizeSecureAuthToken(raw)
    const secureAuthTokenFilePath = getSecureAuthTokenFilePath()
    const tempFilePath = `${secureAuthTokenFilePath}.tmp`

    if (!normalized) {
        rmSync(tempFilePath, { force: true })
        rmSync(secureAuthTokenFilePath, { force: true })
        return null
    }

    if (!isSecureStorageAvailable()) {
        throw new Error('Secure auth token storage is unavailable on this device')
    }

    try {
        mkdirSync(path.dirname(secureAuthTokenFilePath), { recursive: true })
        const encryptedPayload = safeStorage.encryptString(normalized)
        writeFileSync(tempFilePath, encryptedPayload.toString('base64'), { encoding: 'utf8' })
        rmSync(secureAuthTokenFilePath, { force: true })
        renameSync(tempFilePath, secureAuthTokenFilePath)
        return normalized
    } finally {
        rmSync(tempFilePath, { force: true })
    }
}

function findBundledLogoInDistAssets(basePath: string): string | undefined {
    const distAssetsPath = path.join(basePath, 'dist', 'assets')
    if (!existsSync(distAssetsPath)) {
        return undefined
    }

    try {
        const logoFileName = readdirSync(distAssetsPath).find((fileName) =>
            /^pigtex_logo-.*\.png$/i.test(fileName)
        )
        return logoFileName ? path.join(distAssetsPath, logoFileName) : undefined
    } catch {
        return undefined
    }
}

function resolveWindowIconPath(): string | undefined {
    const appPath = app.getAppPath()
    const bundledLogoCandidates = [
        findBundledLogoInDistAssets(appPath),
        findBundledLogoInDistAssets(path.join(__dirname, '..'))
    ]

    const candidatePaths = [
        path.join(appPath, 'assets', 'pigtex_logo.png'),
        path.join(__dirname, '../../assets/pigtex_logo.png'),
        ...bundledLogoCandidates
    ]

    for (const candidatePath of candidatePaths) {
        if (candidatePath && existsSync(candidatePath)) {
            return candidatePath
        }
    }

    return undefined
}

function normalizePath(inputPath: string): string {
    return path.resolve(inputPath)
}

function getErrorCode(error: unknown): string | undefined {
    if (!error || typeof error !== 'object' || !('code' in error)) {
        return undefined
    }

    const code = (error as { code?: unknown }).code
    return typeof code === 'string' ? code : undefined
}

function requireNonEmptyString(value: unknown, fieldLabel: string): string {
    if (typeof value !== 'string') {
        throw new Error(`${fieldLabel} is required`)
    }

    const trimmed = value.trim()
    if (!trimmed) {
        throw new Error(`${fieldLabel} is required`)
    }

    return trimmed
}

async function statPathOrThrow(
    targetPath: string,
    missingLabel: 'File' | 'Folder' | 'Path'
) {
    try {
        return await fs.stat(targetPath)
    } catch (error) {
        if (getErrorCode(error) === 'ENOENT') {
            throw new Error(`${missingLabel} not found: ${targetPath}`)
        }
        throw error
    }
}

function cloneLocalSessionState(state: LocalSessionState): LocalSessionState {
    return {
        rootPath: state.rootPath,
        filePath: state.filePath,
        fileName: state.fileName
    }
}

function normalizePersistedAbsolutePath(value: unknown): string | null {
    if (typeof value !== 'string') return null
    const trimmed = value.trim()
    if (!trimmed || !path.isAbsolute(trimmed)) return null
    return normalizePath(trimmed)
}

function sanitizeLocalSessionState(raw: unknown): LocalSessionState {
    if (!raw || typeof raw !== 'object') {
        return cloneLocalSessionState(DEFAULT_LOCAL_SESSION_STATE)
    }

    const value = raw as Record<string, unknown>
    const rootPath = normalizePersistedAbsolutePath(value.rootPath)
    let filePath = normalizePersistedAbsolutePath(value.filePath)
    let fileName = typeof value.fileName === 'string' ? value.fileName.trim() : null

    if (fileName === '') {
        fileName = null
    }

    if (!rootPath) {
        filePath = null
        fileName = null
    } else if (filePath && !isWithinRoot(rootPath, filePath)) {
        filePath = null
        fileName = null
    }

    return {
        rootPath,
        filePath,
        fileName
    }
}

function getLocalSessionStateFilePath(): string {
    return path.join(app.getPath('userData'), LOCAL_SESSION_STATE_FILE_NAME)
}

async function readLocalSessionState(): Promise<LocalSessionState> {
    if (localSessionStateCache) {
        return cloneLocalSessionState(localSessionStateCache)
    }

    const sessionStateFilePath = getLocalSessionStateFilePath()
    try {
        const rawContent = await fs.readFile(sessionStateFilePath, 'utf8')
        const parsedContent = JSON.parse(rawContent)
        const sanitizedState = sanitizeLocalSessionState(parsedContent)
        localSessionStateCache = sanitizedState
        return cloneLocalSessionState(sanitizedState)
    } catch (error) {
        const code = (error as NodeJS.ErrnoException).code
        if (code !== 'ENOENT') {
            console.error('Failed to read local session state:', error)
        }
        localSessionStateCache = cloneLocalSessionState(DEFAULT_LOCAL_SESSION_STATE)
        return cloneLocalSessionState(DEFAULT_LOCAL_SESSION_STATE)
    }
}

async function writeLocalSessionState(state: LocalSessionState): Promise<LocalSessionState> {
    const sanitizedState = sanitizeLocalSessionState(state)
    const sessionStateFilePath = getLocalSessionStateFilePath()
    const tempStateFilePath = `${sessionStateFilePath}.tmp`
    const payload = JSON.stringify(
        {
            ...sanitizedState,
            updatedAt: new Date().toISOString()
        },
        null,
        2
    )

    try {
        await fs.mkdir(path.dirname(sessionStateFilePath), { recursive: true })
        await fs.writeFile(tempStateFilePath, payload, { encoding: 'utf8' })
        try {
            await fs.rename(tempStateFilePath, sessionStateFilePath)
        } catch (error) {
            const code = (error as NodeJS.ErrnoException).code
            if (code === 'EEXIST' || code === 'EPERM') {
                await fs.rm(sessionStateFilePath, { force: true })
                await fs.rename(tempStateFilePath, sessionStateFilePath)
            } else {
                throw error
            }
        }
    } finally {
        await fs.rm(tempStateFilePath, { force: true }).catch(() => undefined)
    }

    localSessionStateCache = sanitizedState
    return cloneLocalSessionState(sanitizedState)
}

async function updateLocalSessionState(
    patch: {
        rootPath?: string | null
        filePath?: string | null
        fileName?: string | null
    }
): Promise<LocalSessionState> {
    const currentState = await readLocalSessionState()

    const nextState: LocalSessionState = {
        rootPath: patch.rootPath !== undefined ? patch.rootPath : currentState.rootPath,
        filePath: patch.filePath !== undefined ? patch.filePath : currentState.filePath,
        fileName: patch.fileName !== undefined ? patch.fileName : currentState.fileName
    }

    if (patch.rootPath === null) {
        nextState.filePath = null
        nextState.fileName = null
    }
    if (patch.filePath === null) {
        nextState.fileName = null
    }

    return writeLocalSessionState(nextState)
}

function isWithinRoot(rootPath: string, targetPath: string): boolean {
    const relative = path.relative(rootPath, targetPath)
    return relative === '' || (!relative.startsWith('..') && !path.isAbsolute(relative))
}

function ensureAllowedByAnyRoot(targetPath: string): string {
    const normalizedTarget = normalizePath(targetPath)

    for (const root of allowedRoots) {
        if (isWithinRoot(root, normalizedTarget)) {
            return normalizedTarget
        }
    }

    throw new Error('Access denied outside opened folders')
}

function ensureAllowedWithRoot(rootPath: string, targetPath: string): { root: string; target: string } {
    const normalizedRoot = normalizePath(rootPath)
    if (!allowedRoots.has(normalizedRoot)) {
        throw new Error('Folder is not opened in this session')
    }

    const normalizedTarget = normalizePath(targetPath)
    if (!isWithinRoot(normalizedRoot, normalizedTarget)) {
        throw new Error('Target path is outside opened folder')
    }

    return { root: normalizedRoot, target: normalizedTarget }
}

function ensureSafeName(value: string): string {
    const normalized = value.trim()
    if (!normalized) {
        throw new Error('Name is required')
    }
    if (normalized.includes('/') || normalized.includes('\\')) {
        throw new Error('Name cannot contain path separators')
    }
    if (normalized.toLowerCase() === FS_TRASH_DIR_NAME.toLowerCase()) {
        throw new Error(`Name "${FS_TRASH_DIR_NAME}" is reserved`)
    }
    return normalized
}

function getAllowedRootForTarget(targetPath: string): string | null {
    for (const root of allowedRoots) {
        if (isWithinRoot(root, targetPath)) {
            return root
        }
    }
    return null
}

function pushUndoEntry(entry: FsUndoEntry) {
    undoStack.push(entry)
    if (undoStack.length > MAX_UNDO_HISTORY) {
        undoStack.shift()
    }
}

function buildUniqueName(prefix: string): string {
    const randomPart = Math.random().toString(36).slice(2, 8)
    return `${Date.now()}-${randomPart}-${prefix}`
}

async function pathExists(targetPath: string): Promise<boolean> {
    try {
        await fs.access(targetPath)
        return true
    } catch {
        return false
    }
}

async function movePath(sourcePath: string, destinationPath: string) {
    await fs.mkdir(path.dirname(destinationPath), { recursive: true })
    try {
        await fs.rename(sourcePath, destinationPath)
    } catch (error) {
        const code = (error as NodeJS.ErrnoException).code
        if (code !== 'EXDEV') {
            throw error
        }

        const sourceStat = await fs.stat(sourcePath)
        if (sourceStat.isDirectory()) {
            await fs.cp(sourcePath, destinationPath, { recursive: true })
            await fs.rm(sourcePath, { recursive: true, force: true })
        } else {
            await fs.copyFile(sourcePath, destinationPath)
            await fs.unlink(sourcePath)
        }
    }
}

async function createBackupFileForUndo(filePath: string): Promise<string> {
    const root = getAllowedRootForTarget(filePath)
    if (!root) {
        throw new Error('Cannot determine root folder for backup')
    }

    const historyDir = path.join(root, FS_TRASH_DIR_NAME, FS_HISTORY_DIR_NAME)
    await fs.mkdir(historyDir, { recursive: true })
    const backupPath = path.join(historyDir, buildUniqueName('file.bak'))
    const originalContent = await fs.readFile(filePath)
    await fs.writeFile(backupPath, originalContent)
    return backupPath
}

async function movePathToTrashForUndo(targetPath: string): Promise<{ trashPath: string; originalPath: string }> {
    const root = getAllowedRootForTarget(targetPath)
    if (!root) {
        throw new Error('Cannot determine root folder for trash')
    }

    const trashDir = path.join(root, FS_TRASH_DIR_NAME)
    await fs.mkdir(trashDir, { recursive: true })

    const trashPath = path.join(trashDir, buildUniqueName(path.basename(targetPath)))
    await movePath(targetPath, trashPath)

    return {
        trashPath,
        originalPath: targetPath
    }
}

async function applyUndo(entry: FsUndoEntry): Promise<FsUndoEffect> {
    switch (entry.kind) {
        case 'delete-path': {
            if (await pathExists(entry.targetPath)) {
                const stat = await fs.stat(entry.targetPath)
                if (stat.isDirectory()) {
                    await fs.rm(entry.targetPath, { recursive: true, force: true })
                } else {
                    await fs.unlink(entry.targetPath)
                }
            }
            return {
                type: 'deleted',
                targetPath: entry.targetPath
            }
        }

        case 'restore-file-content': {
            const content = await fs.readFile(entry.backupPath)
            await fs.mkdir(path.dirname(entry.targetPath), { recursive: true })
            await fs.writeFile(entry.targetPath, content)
            return {
                type: 'content_restored',
                targetPath: entry.targetPath
            }
        }

        case 'rename': {
            if (!(await pathExists(entry.fromPath))) {
                throw new Error('Source path for undo rename no longer exists')
            }
            if (await pathExists(entry.toPath)) {
                throw new Error('Cannot undo rename because target path already exists')
            }
            await fs.rename(entry.fromPath, entry.toPath)
            return {
                type: 'rename',
                oldPath: entry.fromPath,
                newPath: entry.toPath
            }
        }

        case 'restore-from-trash': {
            if (!(await pathExists(entry.trashPath))) {
                throw new Error('Deleted item backup not found')
            }
            if (await pathExists(entry.originalPath)) {
                throw new Error('Cannot restore because original path already exists')
            }
            await movePath(entry.trashPath, entry.originalPath)
            return {
                type: 'restored',
                targetPath: entry.originalPath
            }
        }

        default:
            throw new Error('Unsupported undo operation')
    }
}

function looksBinary(content: Buffer): boolean {
    const sample = content.subarray(0, 1024)
    for (const byte of sample) {
        if (byte === 0) {
            return true
        }
    }
    return false
}

async function listDirectory(rootPath: string, dirPath: string): Promise<FsEntry[]> {
    const normalizedRoot = normalizePath(requireNonEmptyString(rootPath, 'Root path'))
    const requestedDir = typeof dirPath === 'string' ? dirPath.trim() : ''
    const requestedTargetPath = requestedDir.length === 0
        ? normalizedRoot
        : path.isAbsolute(requestedDir)
            ? requestedDir
            : path.join(normalizedRoot, requestedDir)
    const { target } = ensureAllowedWithRoot(normalizedRoot, requestedTargetPath)
    const targetStat = await statPathOrThrow(target, 'Folder')
    if (!targetStat.isDirectory()) {
        throw new Error(`Target is not a directory: ${target}`)
    }
    const dirents = (await fs.readdir(target, { withFileTypes: true }))
        .filter((entry) => entry.name !== FS_TRASH_DIR_NAME)

    const entries = await Promise.all(
        dirents.map(async (entry) => {
            const fullPath = path.join(target, entry.name)
            const stat = await fs.stat(fullPath)

            return {
                name: entry.name,
                path: fullPath,
                type: entry.isDirectory() ? 'directory' : 'file',
                size: stat.size,
                mtimeMs: stat.mtimeMs
            } as FsEntry
        })
    )

    entries.sort((a, b) => {
        if (a.type !== b.type) {
            return a.type === 'directory' ? -1 : 1
        }
        return a.name.localeCompare(b.name)
    })

    return entries
}

function registerIpcHandlers() {
    ipcMain.on('window-minimize', () => {
        mainWindow?.minimize()
    })

    ipcMain.on('window-maximize', () => {
        if (!mainWindow) return
        if (mainWindow.isMaximized()) {
            mainWindow.unmaximize()
        } else {
            mainWindow.maximize()
        }
    })

    ipcMain.on('window-close', () => {
        mainWindow?.close()
    })

    ipcMain.handle('shell:open-external', async (_event, payload: { url: string }) => {
        const rawUrl = typeof payload?.url === 'string' ? payload.url.trim() : ''
        if (!rawUrl) {
            throw new Error('URL is required')
        }

        let parsedUrl: URL
        try {
            parsedUrl = new URL(rawUrl)
        } catch {
            throw new Error('Invalid URL')
        }

        const protocol = parsedUrl.protocol.toLowerCase()
        if (protocol !== 'https:' && protocol !== 'http:') {
            throw new Error('Only http/https URLs are allowed')
        }

        await shell.openExternal(parsedUrl.toString())
        return { ok: true as const }
    })

    ipcMain.handle('app:get-system-info', async () => ({
        hostname: os.hostname(),
        platform: process.platform,
        arch: process.arch,
        appVersion: app.getVersion(),
    }))

    ipcMain.handle('app:check-desktop-update', async (_event, payload?: { manifestUrl?: string | null }) => {
        return checkDesktopUpdate(payload?.manifestUrl)
    })

    ipcMain.handle('app:download-and-install-desktop-update', async (_event, payload?: { manifestUrl?: string | null }) => {
        return downloadAndInstallDesktopUpdate(payload?.manifestUrl)
    })

    ipcMain.on('settings:is-secure-storage-available', (event) => {
        event.returnValue = isSecureStorageAvailable()
    })

    ipcMain.on('settings:get-secure-api-keys', (event) => {
        event.returnValue = readSecureApiKeyMapSync()
    })

    ipcMain.on('settings:set-secure-api-keys', (event, payload: unknown) => {
        event.returnValue = writeSecureApiKeyMapSync(payload)
    })

    ipcMain.on('settings:get-secure-auth-token', (event) => {
        event.returnValue = readSecureAuthTokenSync()
    })

    ipcMain.on('settings:set-secure-auth-token', (event, payload: unknown) => {
        event.returnValue = writeSecureAuthTokenSync(payload)
    })

    ipcMain.handle('fs:pick-folder', async (event) => {
        const ownerWindow = BrowserWindow.fromWebContents(event.sender) || mainWindow
        const result = ownerWindow
            ? await dialog.showOpenDialog(ownerWindow, {
                properties: ['openDirectory']
            })
            : await dialog.showOpenDialog({
                properties: ['openDirectory']
            })

        if (result.canceled || result.filePaths.length === 0) {
            return { canceled: true as const }
        }

        const rootPath = normalizePath(result.filePaths[0])
        allowedRoots.clear()
        allowedRoots.add(rootPath)
        undoStack.length = 0

        return {
            canceled: false as const,
            path: rootPath
        }
    })

    ipcMain.handle('fs:open-folder', async (_event, payload: { path: string }) => {
        const requestedPath = typeof payload?.path === 'string' ? payload.path.trim() : ''
        if (!requestedPath) {
            throw new Error('Folder path is required')
        }

        if (!path.isAbsolute(requestedPath)) {
            return {
                opened: false as const,
                path: requestedPath,
                missing: false as const,
                message: 'Saved folder path is invalid'
            }
        }

        const rootPath = normalizePath(requestedPath)

        try {
            const stat = await fs.stat(rootPath)
            if (!stat.isDirectory()) {
                return {
                    opened: false as const,
                    path: rootPath,
                    missing: false as const,
                    message: 'Saved path is not a folder'
                }
            }
        } catch (error) {
            const code = (error as NodeJS.ErrnoException).code
            if (code === 'ENOENT') {
                return {
                    opened: false as const,
                    path: rootPath,
                    missing: true as const,
                    message: 'Folder no longer exists'
                }
            }
            throw error
        }

        allowedRoots.clear()
        allowedRoots.add(rootPath)
        undoStack.length = 0

        return {
            opened: true as const,
            path: rootPath
        }
    })

    ipcMain.handle('session:get-local-state', async () => {
        return readLocalSessionState()
    })

    ipcMain.handle(
        'session:update-local-state',
        async (
            _event,
            payload: {
                rootPath?: string | null
                filePath?: string | null
                fileName?: string | null
            }
        ) => {
            if (!payload || typeof payload !== 'object') {
                throw new Error('Invalid local session payload')
            }

            return updateLocalSessionState({
                rootPath: payload.rootPath,
                filePath: payload.filePath,
                fileName: payload.fileName
            })
        }
    )

    ipcMain.handle(
        'fs:list-directory',
        async (_event, payload: { rootPath: string; dirPath: string }) => {
            if (!payload || typeof payload !== 'object') {
                throw new Error('Invalid list directory payload')
            }

            return listDirectory(payload.rootPath, payload.dirPath ?? '')
        }
    )

    ipcMain.handle('fs:read-file', async (_event, filePath: string) => {
        const normalizedPath = ensureAllowedByAnyRoot(requireNonEmptyString(filePath, 'File path'))
        const stat = await statPathOrThrow(normalizedPath, 'File')

        if (!stat.isFile()) {
            throw new Error(`Target is not a file: ${normalizedPath}`)
        }
        const ext = path.extname(normalizedPath).toLowerCase()
        if (ext === '.docx' || ext === '.pdf') {
            throw new Error('DOCX/PDF are not editable in Local Editor. Use Chat -> + -> Upload file to analyze them.')
        }
        if (stat.size > MAX_TEXT_FILE_SIZE) {
            throw new Error('File is too large (max 2MB)')
        }

        const buffer = await fs.readFile(normalizedPath)
        if (looksBinary(buffer)) {
            throw new Error('Binary files are not supported in this editor')
        }

        return {
            content: buffer.toString('utf8'),
            size: stat.size,
            mtimeMs: stat.mtimeMs
        }
    })

    ipcMain.handle(
        'fs:write-file',
        async (_event, payload: { filePath: string; content: string }) => {
            if (!payload || typeof payload !== 'object') {
                throw new Error('Invalid write file payload')
            }

            const normalizedPath = ensureAllowedByAnyRoot(requireNonEmptyString(payload.filePath, 'File path'))
            const parentPath = path.dirname(normalizedPath)
            const parentStat = await statPathOrThrow(parentPath, 'Folder')
            if (!parentStat.isDirectory()) {
                throw new Error(`Parent path is not a directory: ${parentPath}`)
            }
            const existedBeforeWrite = await pathExists(normalizedPath)
            let backupPath: string | null = null

            if (existedBeforeWrite) {
                const stat = await statPathOrThrow(normalizedPath, 'File')
                if (!stat.isFile()) {
                    throw new Error(`Target path is not a file: ${normalizedPath}`)
                }
                backupPath = await createBackupFileForUndo(normalizedPath)
            }

            const content = typeof payload.content === 'string' ? payload.content : ''
            await fs.writeFile(normalizedPath, content, { encoding: 'utf8' })
            const stat = await fs.stat(normalizedPath)

             if (existedBeforeWrite && backupPath) {
                pushUndoEntry({
                    kind: 'restore-file-content',
                    targetPath: normalizedPath,
                    backupPath,
                    description: `Edit ${path.basename(normalizedPath)}`
                })
            } else {
                pushUndoEntry({
                    kind: 'delete-path',
                    targetPath: normalizedPath,
                    description: `Create ${path.basename(normalizedPath)}`
                })
            }

            return {
                ok: true,
                size: stat.size,
                mtimeMs: stat.mtimeMs
            }
        }
    )

    ipcMain.handle(
        'fs:create-file',
        async (_event, payload: { parentPath: string; fileName: string; content?: string }) => {
            if (!payload || typeof payload !== 'object') {
                throw new Error('Invalid create file payload')
            }

            const parentPath = ensureAllowedByAnyRoot(requireNonEmptyString(payload.parentPath, 'Parent path'))
            const parentStat = await statPathOrThrow(parentPath, 'Folder')

            if (!parentStat.isDirectory()) {
                throw new Error(`Parent path is not a directory: ${parentPath}`)
            }

            const fileName = ensureSafeName(requireNonEmptyString(payload.fileName, 'File name'))
            const filePath = path.join(parentPath, fileName)
            ensureAllowedByAnyRoot(filePath)

            try {
                const content = typeof payload.content === 'string' ? payload.content : ''
                await fs.writeFile(filePath, content, {
                    encoding: 'utf8',
                    flag: 'wx'
                })
            } catch (error) {
                const code = (error as NodeJS.ErrnoException).code
                if (code === 'EEXIST') {
                    throw new Error(`File already exists: ${filePath}`)
                }
                if (code === 'ENOENT') {
                    throw new Error(`Folder not found: ${parentPath}`)
                }
                throw error
            }

            pushUndoEntry({
                kind: 'delete-path',
                targetPath: filePath,
                description: `Create ${fileName}`
            })

            return { path: filePath }
        }
    )

    ipcMain.handle(
        'fs:create-folder',
        async (_event, payload: { parentPath: string; folderName: string }) => {
            if (!payload || typeof payload !== 'object') {
                throw new Error('Invalid create folder payload')
            }

            const parentPath = ensureAllowedByAnyRoot(requireNonEmptyString(payload.parentPath, 'Parent path'))
            const parentStat = await statPathOrThrow(parentPath, 'Folder')

            if (!parentStat.isDirectory()) {
                throw new Error(`Parent path is not a directory: ${parentPath}`)
            }

            const folderName = ensureSafeName(requireNonEmptyString(payload.folderName, 'Folder name'))
            const folderPath = path.join(parentPath, folderName)
            ensureAllowedByAnyRoot(folderPath)

            try {
                await fs.mkdir(folderPath, { recursive: false })
            } catch (error) {
                const code = getErrorCode(error)
                if (code === 'EEXIST') {
                    throw new Error(`Folder already exists: ${folderPath}`)
                }
                if (code === 'ENOENT') {
                    throw new Error(`Folder not found: ${parentPath}`)
                }
                throw error
            }
            pushUndoEntry({
                kind: 'delete-path',
                targetPath: folderPath,
                description: `Create folder ${folderName}`
            })
            return { path: folderPath }
        }
    )

    ipcMain.handle(
        'fs:rename-path',
        async (_event, payload: { targetPath: string; newName: string }) => {
            if (!payload || typeof payload !== 'object') {
                throw new Error('Invalid rename payload')
            }

            const targetPath = ensureAllowedByAnyRoot(requireNonEmptyString(payload.targetPath, 'Target path'))
            await statPathOrThrow(targetPath, 'Path')
            const parentPath = path.dirname(targetPath)
            const nextPath = path.join(parentPath, ensureSafeName(requireNonEmptyString(payload.newName, 'New name')))
            ensureAllowedByAnyRoot(nextPath)

            if (nextPath === targetPath) {
                return { path: targetPath }
            }
            if (await pathExists(nextPath)) {
                throw new Error(`Path already exists: ${nextPath}`)
            }

            await fs.rename(targetPath, nextPath)
            pushUndoEntry({
                kind: 'rename',
                fromPath: nextPath,
                toPath: targetPath,
                description: `Rename ${path.basename(targetPath)} to ${path.basename(nextPath)}`
            })
            return { path: nextPath }
        }
    )

    ipcMain.handle('fs:delete-path', async (_event, payload: { targetPath: string }) => {
        if (!payload || typeof payload !== 'object') {
            throw new Error('Invalid delete payload')
        }

        const targetPath = ensureAllowedByAnyRoot(requireNonEmptyString(payload.targetPath, 'Target path'))
        await statPathOrThrow(targetPath, 'Path')
        const moved = await movePathToTrashForUndo(targetPath)
        pushUndoEntry({
            kind: 'restore-from-trash',
            trashPath: moved.trashPath,
            originalPath: moved.originalPath,
            description: `Delete ${path.basename(targetPath)}`
        })

        return { ok: true }
    })

    ipcMain.handle('fs:get-undo-state', async () => {
        const lastEntry = undoStack[undoStack.length - 1]
        return {
            count: undoStack.length,
            lastDescription: lastEntry?.description || null
        }
    })

    ipcMain.handle('fs:undo-last-change', async () => {
        const latest = undoStack.pop()
        if (!latest) {
            return {
                ok: true,
                undone: false,
                remaining: 0
            }
        }

        try {
            const effect = await applyUndo(latest)
            return {
                ok: true,
                undone: true,
                description: latest.description,
                remaining: undoStack.length,
                effect
            }
        } catch (error) {
            undoStack.push(latest)
            throw error
        }
    })
}

function createWindow() {
    const windowIcon = resolveWindowIconPath()

    mainWindow = new BrowserWindow({
        width: 1400,
        height: 900,
        minWidth: 1000,
        minHeight: 700,
        frame: false,
        titleBarStyle: 'hidden',
        backgroundColor: '#0D0B1A',
        ...(windowIcon ? { icon: windowIcon } : {}),
        webPreferences: {
            nodeIntegration: false,
            contextIsolation: true,
            preload: path.join(__dirname, 'preload.js')
        }
    })

    if (process.env.NODE_ENV === 'development' || !app.isPackaged) {
        mainWindow.loadURL('http://localhost:5173')
        mainWindow.webContents.openDevTools()
    } else {
        mainWindow.loadFile(path.join(__dirname, '../dist/index.html'))
    }

    mainWindow.on('closed', () => {
        mainWindow = null
    })
}

app.whenReady().then(() => {
    registerIpcHandlers()
    createWindow()

    app.on('activate', () => {
        if (BrowserWindow.getAllWindows().length === 0) {
            createWindow()
        }
    })
})

app.on('window-all-closed', () => {
    if (process.platform !== 'darwin') {
        app.quit()
    }
})
