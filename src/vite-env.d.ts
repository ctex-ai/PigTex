/// <reference types="vite/client" />

interface ElectronAPI {
    minimize: () => void
    maximize: () => void
    close: () => void
    openExternal: (url: string) => Promise<{ ok: boolean }>
    getSystemInfo: () => Promise<{
        hostname: string
        platform: string
        arch: string
        appVersion: string
    }>
    checkDesktopUpdate: (payload?: {
        manifestUrl?: string | null
    }) => Promise<{
        currentVersion: string
        checkedAt: string
        manifest: {
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
        } | null
    }>
    downloadAndInstallDesktopUpdate: (payload?: {
        manifestUrl?: string | null
    }) => Promise<
        | {
            status: 'up_to_date'
            currentVersion: string
        }
        | {
            status: 'opened'
            currentVersion: string
            version: string
            downloadPageUrl: string
        }
    >
    resetLocalData: () => Promise<{
        ok: true
        removedPaths: string[]
    }>
    isSecureStorageAvailable: () => boolean
    getSecureApiKeys: () => Partial<Record<'auto' | 'openai' | 'anthropic' | 'gemini' | 'alibaba', string>>
    setSecureApiKeys: (payload: Partial<Record<'auto' | 'openai' | 'anthropic' | 'gemini' | 'alibaba', string>>) => Partial<Record<'auto' | 'openai' | 'anthropic' | 'gemini' | 'alibaba', string>>
    getSecureAuthToken: () => string | null
    setSecureAuthToken: (token: string) => string | null
    clearSecureAuthToken: () => void
    pickFolder: () => Promise<{ canceled: boolean; path?: string }>
    openFolder: (payload: { path: string }) => Promise<{
        opened: boolean
        path: string
        missing?: boolean
        message?: string
    }>
    getLocalSessionState: () => Promise<{
        rootPath: string | null
        filePath: string | null
        fileName: string | null
    }>
    updateLocalSessionState: (payload: {
        rootPath?: string | null
        filePath?: string | null
        fileName?: string | null
    }) => Promise<{
        rootPath: string | null
        filePath: string | null
        fileName: string | null
    }>
    listDirectory: (payload: {
        rootPath: string
        dirPath: string
    }) => Promise<Array<{
        name: string
        path: string
        type: 'file' | 'directory'
        size: number
        mtimeMs: number
    }>>
    readFile: (filePath: string) => Promise<{
        content: string
        size: number
        mtimeMs: number
    }>
    writeFile: (payload: {
        filePath: string
        content: string
    }) => Promise<{
        ok: boolean
        size: number
        mtimeMs: number
    }>
    createFile: (payload: {
        parentPath: string
        fileName: string
        content?: string
    }) => Promise<{ path: string }>
    createFolder: (payload: {
        parentPath: string
        folderName: string
    }) => Promise<{ path: string }>
    renamePath: (payload: {
        targetPath: string
        newName: string
    }) => Promise<{ path: string }>
    deletePath: (payload: {
        targetPath: string
    }) => Promise<{ ok: boolean }>
    getUndoState: () => Promise<{
        count: number
        lastDescription: string | null
    }>
    undoLastChange: () => Promise<{
        ok: boolean
        undone: boolean
        description?: string
        remaining: number
        effect?: {
            type: 'deleted' | 'content_restored' | 'rename' | 'restored'
            targetPath?: string
            oldPath?: string
            newPath?: string
        }
    }>
}

declare global {
    interface Window {
        electronAPI?: ElectronAPI
    }
}

export { }
