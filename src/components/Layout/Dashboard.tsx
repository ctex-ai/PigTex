import { useState, useRef, useCallback, useEffect, useMemo } from 'react'
import { motion, AnimatePresence } from 'framer-motion'
import Sidebar from './Sidebar/Sidebar'
import MainPanel from './MainPanel/MainPanel'
import ChatPanel from './ChatPanel/ChatPanel'
import ExplorerPanel, { ExplorerPanelHandle } from './ExplorerPanel/ExplorerPanel'
import TitleBar from './TitleBar'
import AdminConsole from '../Admin/AdminConsole'
import { EditorTarget } from '../../types/editor'
import { showError, showInfo, showSuccess } from '../Shared/Toast'
import SettingsModal from '../Settings/SettingsModal'
import { useI18n } from '../../contexts/I18nContext'
import { useAuth } from '../../contexts/AuthContext'
import {
    getPigTexSettings,
    savePigTexSettings,
    updatePigTexSettings,
    PIGTEX_SETTINGS_CHANGED_EVENT,
    PigTexSettings
} from '../../services/settings'
import {
    DEFAULT_RENDERER_DESKTOP_UPDATE_MANIFEST_URL,
    IDLE_DESKTOP_UPDATE_STATE,
    createDesktopUpdateErrorState,
    createDesktopUpdateStateFromResponse
} from '../../services/desktopUpdate'
import type { DesktopUpdateState } from '../../services/desktopUpdate'
import {
    persistStoredConversationSelection,
    persistStoredWorkspaceSelection,
    readStoredConversationSelection,
    readStoredWorkspaceSelection
} from '../../utils/accountScopedSelection'
import './Dashboard.css'

export type ViewMode = 'chat' | 'editor' | 'admin'

type LearningProgramLaunchContext = {
    id: string
    title: string
    workspaceId: string | null
}

/** Imperative handle exposed by Sidebar for external triggers */
export interface SidebarHandle {
    openCreateWorkspace: () => void
    createNewChat: () => void
}

const SIDEBAR_STORAGE_KEY = 'pigtex:layout:sidebar-width'
const EXPLORER_STORAGE_KEY = 'pigtex:layout:explorer-width'
const SIDEBAR_COLLAPSED_WIDTH = 56
const SIDEBAR_EXPANDED_DEFAULT_WIDTH = 260
const SIDEBAR_EXPANDED_MIN_WIDTH = 180
const SIDEBAR_EXPANDED_MAX_WIDTH = 420
const EXPLORER_DEFAULT_WIDTH = 320
const EXPLORER_MIN_WIDTH = 240
const EXPLORER_MAX_WIDTH = 520
const MAIN_MIN_WIDTH = 320
const RESIZER_HIT_WIDTH = 10
const DESKTOP_UPDATE_NOTICE_STORAGE_KEY = 'pigtex:desktop-update-notice-version'
const DESKTOP_UPDATE_POLL_INTERVAL_MS = 6 * 60 * 60 * 1000

const clamp = (value: number, min: number, max: number) => Math.min(Math.max(value, min), max)

const getStoredWidth = (storageKey: string, fallback: number, min: number, max: number) => {
    if (typeof window === 'undefined') return fallback

    const stored = window.localStorage.getItem(storageKey)
    if (!stored) return fallback

    const parsed = Number(stored)
    return Number.isFinite(parsed) ? clamp(parsed, min, max) : fallback
}

const getSidebarBounds = (containerWidth: number, explorerWidth: number) => {
    const maxWidth = Math.min(
        SIDEBAR_EXPANDED_MAX_WIDTH,
        Math.max(120, containerWidth - explorerWidth - MAIN_MIN_WIDTH - RESIZER_HIT_WIDTH * 2)
    )

    return {
        minWidth: Math.min(SIDEBAR_EXPANDED_MIN_WIDTH, maxWidth),
        maxWidth
    }
}

const getExplorerBounds = (containerWidth: number, sidebarWidth: number) => {
    const maxWidth = Math.min(
        EXPLORER_MAX_WIDTH,
        Math.max(180, containerWidth - sidebarWidth - MAIN_MIN_WIDTH - RESIZER_HIT_WIDTH * 2)
    )

    return {
        minWidth: Math.min(EXPLORER_MIN_WIDTH, maxWidth),
        maxWidth
    }
}

const getBaseName = (inputPath: string) => {
    const parts = inputPath.split(/[\\/]+/).filter(Boolean)
    return parts[parts.length - 1] || inputPath
}

const isSameOrChildPath = (candidatePath: string, parentPath: string) => {
    return (
        candidatePath === parentPath ||
        candidatePath.startsWith(`${parentPath}\\`) ||
        candidatePath.startsWith(`${parentPath}/`)
    )
}

const replacePathPrefix = (inputPath: string, oldPrefix: string, newPrefix: string) => {
    if (!isSameOrChildPath(inputPath, oldPrefix)) return inputPath
    return `${newPrefix}${inputPath.slice(oldPrefix.length)}`
}

const isLikelyMissingPathError = (error: unknown) => {
    const message = error instanceof Error ? error.message.toLowerCase() : String(error).toLowerCase()
    return message.includes('enoent') || message.includes('not found') || message.includes('no such file')
}

const Dashboard = () => {
    const { isVietnamese } = useI18n()
    const { user } = useAuth()
    const isAdmin = Boolean(user?.is_admin)
    const [sidebarCollapsed, setSidebarCollapsed] = useState(false)
    const [sidebarWidth, setSidebarWidth] = useState(() =>
        getStoredWidth(
            SIDEBAR_STORAGE_KEY,
            SIDEBAR_EXPANDED_DEFAULT_WIDTH,
            SIDEBAR_EXPANDED_MIN_WIDTH,
            SIDEBAR_EXPANDED_MAX_WIDTH
        )
    )
    const [explorerWidth, setExplorerWidth] = useState(() =>
        getStoredWidth(
            EXPLORER_STORAGE_KEY,
            EXPLORER_DEFAULT_WIDTH,
            EXPLORER_MIN_WIDTH,
            EXPLORER_MAX_WIDTH
        )
    )
    const [activeResizeHandle, setActiveResizeHandle] = useState<'sidebar' | 'explorer' | null>(null)
    const [viewMode, setViewMode] = useState<ViewMode>('chat')
    const [selectedWorkspaceId, setSelectedWorkspaceId] = useState<string | null>(() => readStoredWorkspaceSelection(user?.id))
    const [selectedConversationId, setSelectedConversationId] = useState<string | null>(() => readStoredConversationSelection(user?.id))
    const [selectedLearningProgram, setSelectedLearningProgram] = useState<LearningProgramLaunchContext | null>(null)
    const [chatResetToken, setChatResetToken] = useState(0)
    const [selectedEditorTarget, setSelectedEditorTarget] = useState<EditorTarget | null>(null)
    const [showSettingsModal, setShowSettingsModal] = useState(false)
    const [pigtexSettings, setPigtexSettings] = useState<PigTexSettings>(() => getPigTexSettings())
    const [localRootPath, setLocalRootPath] = useState<string | null>(null)
    const [undoState, setUndoState] = useState<{ count: number; lastDescription: string | null }>({
        count: 0,
        lastDescription: null
    })
    const [isUndoing, setIsUndoing] = useState(false)
    const [desktopUpdate, setDesktopUpdate] = useState<DesktopUpdateState>(() => ({ ...IDLE_DESKTOP_UPDATE_STATE }))
    const [isCheckingDesktopUpdate, setIsCheckingDesktopUpdate] = useState(false)
    const [isInstallingDesktopUpdate, setIsInstallingDesktopUpdate] = useState(false)

    const sidebarRef = useRef<SidebarHandle>(null)
    const explorerRef = useRef<ExplorerPanelHandle>(null)
    const dashboardContentRef = useRef<HTMLDivElement>(null)
    const didRestoreLocalFileRef = useRef(false)
    const sidebarCollapsedRef = useRef(sidebarCollapsed)
    const sidebarWidthRef = useRef(sidebarWidth)
    const explorerWidthRef = useRef(explorerWidth)
    const copy = isVietnamese ? {
        openFolderFirst: 'Hãy mở thư mục trước',
        filesystemUnavailable: 'API hệ thống tệp hiện không khả dụng',
        noLocalChangesToUndo: 'Không có thay đổi cục bộ để hoàn tác',
        undoCompleted: 'Hoàn tác xong',
        undoPrefix: 'Hoàn tác',
        undoFailed: 'Không thể hoàn tác thay đổi cục bộ',
        lastOpenedFileMissing: (path: string | null) => `Tệp đã mở gần nhất không còn tồn tại${path ? `: ${path}` : ''}`,
        resizeSidebar: 'Kéo để đổi kích thước Sidebar',
        resizeExplorer: 'Kéo để đổi kích thước Explorer',
    } : {
        openFolderFirst: 'Open a folder first',
        filesystemUnavailable: 'Filesystem API is unavailable',
        noLocalChangesToUndo: 'No local changes to undo',
        undoCompleted: 'Undo completed',
        undoPrefix: 'Undo',
        undoFailed: 'Failed to undo local change',
        lastOpenedFileMissing: (path: string | null) => `Last opened file is missing${path ? `: ${path}` : ''}`,
        resizeSidebar: 'Drag to resize sidebar',
        resizeExplorer: 'Drag to resize explorer panel',
    }
    const desktopUpdateCopy = useMemo(() => (
        isVietnamese ? {
            toast: (version: string) => `Có PigTex ${version} mới. Bấm Cập nhật ngay để mở website tải bản mới nhất.`,
            updateAvailable: (version: string) => `PigTex ${version} đã sẵn sàng để cập nhật qua website.`,
            upToDate: 'PigTex đang ở bản mới nhất',
            openingUpdateWebsite: 'Đang mở website cập nhật...',
            updateWebsiteOpened: 'Website cập nhật đã mở. Hãy tải installer mới và chạy setup.',
            installFailed: 'Không thể mở website cập nhật',
            openFailed: 'Không thể mở trang cập nhật',
            checkFailed: 'Không thể kiểm tra bản cập nhật PigTex',
            missingUrl: 'Chưa có liên kết cập nhật'
        } : {
            toast: (version: string) => `PigTex ${version} is available. Use Update to open the website and download the latest installer.`,
            updateAvailable: (version: string) => `PigTex ${version} is ready to update from the website.`,
            upToDate: 'PigTex is already on the latest version',
            openingUpdateWebsite: 'Opening the update website...',
            updateWebsiteOpened: 'Update website opened. Download the latest installer and run setup.',
            installFailed: 'Unable to open the update website',
            openFailed: 'Unable to open the update page',
            checkFailed: 'Unable to check for PigTex updates',
            missingUrl: 'Update link is unavailable'
        }
    ), [isVietnamese])

    const getContainerWidth = useCallback(() => {
        return dashboardContentRef.current?.getBoundingClientRect().width || window.innerWidth
    }, [])

    const reconcilePanelWidths = useCallback((containerWidthOverride?: number) => {
        const containerWidth = containerWidthOverride || getContainerWidth()
        if (containerWidth <= 0) return

        const effectiveSidebarWidth = sidebarCollapsedRef.current
            ? SIDEBAR_COLLAPSED_WIDTH
            : sidebarWidthRef.current
        const initialExplorerBounds = getExplorerBounds(containerWidth, effectiveSidebarWidth)
        const nextExplorerWidth = clamp(
            explorerWidthRef.current,
            initialExplorerBounds.minWidth,
            initialExplorerBounds.maxWidth
        )

        const sidebarBounds = getSidebarBounds(containerWidth, nextExplorerWidth)
        const nextSidebarWidth = clamp(
            sidebarWidthRef.current,
            sidebarBounds.minWidth,
            sidebarBounds.maxWidth
        )

        const stableSidebarWidth = sidebarCollapsedRef.current
            ? SIDEBAR_COLLAPSED_WIDTH
            : nextSidebarWidth
        const stableExplorerBounds = getExplorerBounds(containerWidth, stableSidebarWidth)
        const stableExplorerWidth = clamp(
            nextExplorerWidth,
            stableExplorerBounds.minWidth,
            stableExplorerBounds.maxWidth
        )

        if (nextSidebarWidth !== sidebarWidthRef.current) {
            setSidebarWidth(nextSidebarWidth)
        }

        if (stableExplorerWidth !== explorerWidthRef.current) {
            setExplorerWidth(stableExplorerWidth)
        }
    }, [getContainerWidth])

    const handleOpenFile = useCallback((target: EditorTarget) => {
        didRestoreLocalFileRef.current = true
        setSelectedEditorTarget(target)
        setViewMode('editor')
    }, [])

    const handleBackToChat = useCallback(() => {
        setViewMode('chat')
    }, [])

    const handleOpenAdmin = useCallback(() => {
        if (!isAdmin) return
        setViewMode('admin')
    }, [isAdmin])

    const handleNewChat = useCallback((_workspaceId: string | null) => {
        // Force reset to a fresh thread even if caller forgot to clear selection.
        setSelectedLearningProgram(null)
        setSelectedConversationId(null)
        if (viewMode !== 'chat') {
            setViewMode('chat')
        }
        setChatResetToken(prev => prev + 1)
    }, [viewMode])

    const handleConversationSelect = useCallback((conversationId: string | null) => {
        setSelectedLearningProgram(null)
        setSelectedConversationId(conversationId)
        if (conversationId && viewMode !== 'chat') {
            setViewMode('chat')
        }
    }, [viewMode])

    const handleWorkspaceSelect = useCallback((workspaceId: string | null) => {
        setSelectedLearningProgram(null)
        setSelectedWorkspaceId(workspaceId)
        setSelectedConversationId(null)
        setSelectedEditorTarget(null)
    }, [])

    const handleLearningProgramSelect = useCallback((program: LearningProgramLaunchContext) => {
        setSelectedLearningProgram(program)
        setSelectedWorkspaceId(program.workspaceId)
        setSelectedConversationId(null)
        setSelectedEditorTarget(null)
        if (viewMode !== 'chat') {
            setViewMode('chat')
        }
        setChatResetToken(prev => prev + 1)
    }, [viewMode])

    const handleSaveSettings = useCallback((nextSettings: PigTexSettings) => {
        const saved = savePigTexSettings(nextSettings)
        setPigtexSettings(saved)
    }, [])

    const handleSettingsChange = useCallback((patch: Partial<PigTexSettings>) => {
        const updated = updatePigTexSettings(patch)
        setPigtexSettings(updated)
    }, [])

    const handleOpenLocalFolder = useCallback(async () => {
        await explorerRef.current?.openLocalFolder()
    }, [])

    const handleCheckDesktopUpdate = useCallback(async (options?: { manual?: boolean }) => {
        if (!window.electronAPI?.checkDesktopUpdate) {
            return
        }

        setIsCheckingDesktopUpdate(true)
        try {
            const response = await window.electronAPI.checkDesktopUpdate(
                DEFAULT_RENDERER_DESKTOP_UPDATE_MANIFEST_URL
                    ? { manifestUrl: DEFAULT_RENDERER_DESKTOP_UPDATE_MANIFEST_URL }
                    : undefined
            )
            const nextState = createDesktopUpdateStateFromResponse(response)
            setDesktopUpdate(nextState)

            if (nextState.updateAvailable && nextState.latestVersion) {
                const seenVersion = window.localStorage.getItem(DESKTOP_UPDATE_NOTICE_STORAGE_KEY)
                if (options?.manual || seenVersion !== nextState.latestVersion) {
                    window.localStorage.setItem(DESKTOP_UPDATE_NOTICE_STORAGE_KEY, nextState.latestVersion)
                    showInfo(options?.manual
                        ? desktopUpdateCopy.updateAvailable(nextState.latestVersion)
                        : desktopUpdateCopy.toast(nextState.latestVersion)
                    )
                }
                return
            }

            if (options?.manual) {
                showSuccess(desktopUpdateCopy.upToDate)
            }
        } catch (error) {
            console.error('Failed to check desktop update:', error)
            setDesktopUpdate(prev => createDesktopUpdateErrorState(prev.currentVersion, error))
            if (options?.manual) {
                showError(desktopUpdateCopy.checkFailed)
            }
        } finally {
            setIsCheckingDesktopUpdate(false)
        }
    }, [desktopUpdateCopy])

    const handleOpenDesktopUpdatePage = useCallback(async () => {
        const targetUrl = desktopUpdate.downloadPageUrl
        if (!targetUrl) {
            showError(desktopUpdateCopy.missingUrl)
            return
        }

        try {
            if (window.electronAPI?.openExternal) {
                await window.electronAPI.openExternal(targetUrl)
            } else {
                window.open(targetUrl, '_blank', 'noopener,noreferrer')
            }
        } catch (error) {
            console.error('Failed to open desktop update page:', error)
            showError(desktopUpdateCopy.openFailed)
        }
    }, [desktopUpdate.downloadPageUrl, desktopUpdateCopy])

    const handleInstallDesktopUpdate = useCallback(async () => {
        if (isInstallingDesktopUpdate) {
            return
        }

        if (!window.electronAPI?.downloadAndInstallDesktopUpdate) {
            await handleOpenDesktopUpdatePage()
            return
        }

        setIsInstallingDesktopUpdate(true)
        showInfo(desktopUpdateCopy.openingUpdateWebsite)

        try {
            const result = await window.electronAPI.downloadAndInstallDesktopUpdate(
                DEFAULT_RENDERER_DESKTOP_UPDATE_MANIFEST_URL
                    ? { manifestUrl: DEFAULT_RENDERER_DESKTOP_UPDATE_MANIFEST_URL }
                    : undefined
            )
            if (result.status === 'up_to_date') {
                setDesktopUpdate(prev => ({
                    ...prev,
                    currentVersion: result.currentVersion,
                    status: 'up_to_date',
                    updateAvailable: false
                }))
                showSuccess(desktopUpdateCopy.upToDate)
                return
            }

            showSuccess(desktopUpdateCopy.updateWebsiteOpened)
        } catch (error) {
            console.error('Failed to install desktop update:', error)
            showError(desktopUpdateCopy.installFailed)
        } finally {
            setIsInstallingDesktopUpdate(false)
        }
    }, [desktopUpdateCopy, handleOpenDesktopUpdatePage, isInstallingDesktopUpdate])

    const handleCreateLocalFile = useCallback(async () => {
        await explorerRef.current?.createLocalFile()
    }, [])

    const handleCreateLocalFolder = useCallback(async () => {
        await explorerRef.current?.createLocalFolder()
    }, [])

    const handleRefreshLocalFolder = useCallback(async () => {
        await explorerRef.current?.refreshLocalFolder()
    }, [])

    const refreshUndoState = useCallback(async () => {
        if (!window.electronAPI?.getUndoState) {
            setUndoState({ count: 0, lastDescription: null })
            return
        }

        try {
            const nextState = await window.electronAPI.getUndoState()
            setUndoState(nextState)
        } catch (error) {
            console.error('Failed to read undo state:', error)
        }
    }, [])

    const applyUndoEffect = useCallback((effect?: {
        type: 'deleted' | 'content_restored' | 'rename' | 'restored'
        targetPath?: string
        oldPath?: string
        newPath?: string
    }) => {
        if (!effect) return

        if (effect.type === 'rename' && effect.oldPath && effect.newPath) {
            window.dispatchEvent(new CustomEvent('pigtex:fs-path-renamed', {
                detail: {
                    oldPath: effect.oldPath,
                    newPath: effect.newPath
                }
            }))
        }

        if (effect.type === 'deleted' && effect.targetPath) {
            window.dispatchEvent(new CustomEvent('pigtex:fs-path-deleted', {
                detail: {
                    targetPath: effect.targetPath,
                    isDirectory: false
                }
            }))
        }

        if (effect.type === 'content_restored' && effect.targetPath) {
            window.dispatchEvent(new CustomEvent('pigtex:fs-content-restored', {
                detail: {
                    targetPath: effect.targetPath
                }
            }))
        }

        window.dispatchEvent(new CustomEvent('pigtex:local-fs-refresh'))
    }, [])

    const handleUndoLastChange = useCallback(async () => {
        if (!localRootPath) {
            showError(copy.openFolderFirst)
            return
        }
        if (!window.electronAPI?.undoLastChange) {
            showError(copy.filesystemUnavailable)
            return
        }
        if (isUndoing) return

        setIsUndoing(true)
        try {
            const result = await window.electronAPI.undoLastChange()
            if (!result.undone) {
                showInfo(copy.noLocalChangesToUndo)
                return
            }

            applyUndoEffect(result.effect)
            showSuccess(result.description ? `${copy.undoPrefix}: ${result.description}` : copy.undoCompleted)
        } catch (error) {
            console.error('Failed to undo local change:', error)
            showError(copy.undoFailed)
        } finally {
            setIsUndoing(false)
            void refreshUndoState()
        }
    }, [localRootPath, isUndoing, applyUndoEffect, refreshUndoState])

    const handleLocalPathRenamed = useCallback((payload: { oldPath: string; newPath: string }) => {
        setSelectedEditorTarget(prev => {
            if (!prev || prev.source !== 'local') return prev
            if (!isSameOrChildPath(prev.path, payload.oldPath)) return prev

            const nextPath = replacePathPrefix(prev.path, payload.oldPath, payload.newPath)
            const nextRootPath = replacePathPrefix(prev.rootPath, payload.oldPath, payload.newPath)
            return {
                ...prev,
                path: nextPath,
                rootPath: nextRootPath,
                name: getBaseName(nextPath)
            }
        })
    }, [])

    const handleLocalPathDeleted = useCallback((payload: { targetPath: string }) => {
        setSelectedEditorTarget(prev => {
            if (!prev || prev.source !== 'local') return prev
            if (!isSameOrChildPath(prev.path, payload.targetPath)) return prev
            return null
        })
    }, [])

    const handleSidebarResizeStart = useCallback((event: React.PointerEvent<HTMLDivElement>) => {
        if (sidebarCollapsed) return
        event.preventDefault()
        setActiveResizeHandle('sidebar')
    }, [sidebarCollapsed])

    const handleExplorerResizeStart = useCallback((event: React.PointerEvent<HTMLDivElement>) => {
        event.preventDefault()
        setActiveResizeHandle('explorer')
    }, [])

    const handleSidebarResizeReset = useCallback(() => {
        const containerWidth = getContainerWidth()
        const sidebarBounds = getSidebarBounds(containerWidth, explorerWidthRef.current)
        setSidebarWidth(
            clamp(
                SIDEBAR_EXPANDED_DEFAULT_WIDTH,
                sidebarBounds.minWidth,
                sidebarBounds.maxWidth
            )
        )
    }, [getContainerWidth])

    const handleExplorerResizeReset = useCallback(() => {
        const containerWidth = getContainerWidth()
        const effectiveSidebarWidth = sidebarCollapsed
            ? SIDEBAR_COLLAPSED_WIDTH
            : sidebarWidthRef.current
        const explorerBounds = getExplorerBounds(containerWidth, effectiveSidebarWidth)
        setExplorerWidth(
            clamp(EXPLORER_DEFAULT_WIDTH, explorerBounds.minWidth, explorerBounds.maxWidth)
        )
    }, [getContainerWidth, sidebarCollapsed])

    useEffect(() => {
        const handleRenamedEvent = (event: Event) => {
            const detail = (event as CustomEvent<{ oldPath: string; newPath: string }>).detail
            if (!detail?.oldPath || !detail?.newPath) return
            handleLocalPathRenamed(detail)
        }

        const handleDeletedEvent = (event: Event) => {
            const detail = (event as CustomEvent<{ targetPath: string }>).detail
            if (!detail?.targetPath) return
            handleLocalPathDeleted(detail)
        }

        window.addEventListener('pigtex:fs-path-renamed', handleRenamedEvent as EventListener)
        window.addEventListener('pigtex:fs-path-deleted', handleDeletedEvent as EventListener)

        return () => {
            window.removeEventListener('pigtex:fs-path-renamed', handleRenamedEvent as EventListener)
            window.removeEventListener('pigtex:fs-path-deleted', handleDeletedEvent as EventListener)
        }
    }, [handleLocalPathRenamed, handleLocalPathDeleted])

    useEffect(() => {
        const handleAgentFocusFile = (event: Event) => {
            const detail = (event as CustomEvent<{ path?: string }>).detail
            const targetPath = detail?.path?.trim()
            if (!targetPath || !localRootPath) return
            if (!isSameOrChildPath(targetPath, localRootPath)) return

            didRestoreLocalFileRef.current = true
            setSelectedEditorTarget(prev => {
                if (prev?.source === 'local' && prev.path === targetPath) {
                    return prev
                }
                return {
                    source: 'local',
                    path: targetPath,
                    rootPath: localRootPath,
                    name: getBaseName(targetPath)
                }
            })
        }

        window.addEventListener('pigtex:agent-focus-file', handleAgentFocusFile as EventListener)
        return () => {
            window.removeEventListener('pigtex:agent-focus-file', handleAgentFocusFile as EventListener)
        }
    }, [localRootPath])

    useEffect(() => {
        const handleSettingsChanged = (event: Event) => {
            const detail = (event as CustomEvent<PigTexSettings>).detail
            if (detail) {
                setPigtexSettings(detail)
            } else {
                setPigtexSettings(getPigTexSettings())
            }
        }

        window.addEventListener(PIGTEX_SETTINGS_CHANGED_EVENT, handleSettingsChanged as EventListener)
        return () => {
            window.removeEventListener(PIGTEX_SETTINGS_CHANGED_EVENT, handleSettingsChanged as EventListener)
        }
    }, [])

    useEffect(() => {
        setSelectedEditorTarget((prev) => {
            if (!prev || prev.source !== 'local') return prev
            if (!localRootPath) return null
            if (!isSameOrChildPath(prev.path, localRootPath)) return null
            return prev
        })
    }, [localRootPath])

    useEffect(() => {
        persistStoredWorkspaceSelection(user?.id, selectedWorkspaceId)
    }, [selectedWorkspaceId, user?.id])

    useEffect(() => {
        persistStoredConversationSelection(user?.id, selectedConversationId)
    }, [selectedConversationId, user?.id])

    useEffect(() => {
        if (didRestoreLocalFileRef.current) return
        if (!localRootPath) return
        if (selectedEditorTarget) {
            didRestoreLocalFileRef.current = true
            return
        }
        if (!window.electronAPI?.getLocalSessionState || !window.electronAPI?.readFile) {
            didRestoreLocalFileRef.current = true
            return
        }

        didRestoreLocalFileRef.current = true

        let cancelled = false

        const restoreLastOpenedFile = async () => {
            let lastFilePath: string | null = null
            try {
                const sessionState = await window.electronAPI!.getLocalSessionState()
                if (cancelled) return

                const storedFilePath = sessionState.filePath?.trim()
                if (!storedFilePath) return
                if (!isSameOrChildPath(storedFilePath, localRootPath)) return
                lastFilePath = storedFilePath

                const storedFileName = sessionState.fileName?.trim() || getBaseName(storedFilePath)
                await window.electronAPI!.readFile(storedFilePath)
                if (cancelled) return

                setSelectedEditorTarget({
                    source: 'local',
                    path: storedFilePath,
                    rootPath: localRootPath,
                    name: storedFileName
                })
                setViewMode('editor')
            } catch (error) {
                if (cancelled) return

                if (isLikelyMissingPathError(error)) {
                    try {
                        await window.electronAPI!.updateLocalSessionState({
                            filePath: null,
                            fileName: null
                        })
                    } catch (persistError) {
                        console.error('Failed to clear missing file from local session:', persistError)
                    }
                    showInfo(copy.lastOpenedFileMissing(lastFilePath))
                    return
                }

                console.error('Failed to restore last opened file:', error)
            }
        }

        void restoreLastOpenedFile()

        return () => {
            cancelled = true
        }
    }, [localRootPath, selectedEditorTarget])

    useEffect(() => {
        if (!didRestoreLocalFileRef.current && !selectedEditorTarget) {
            return
        }
        if (!window.electronAPI?.updateLocalSessionState) {
            return
        }

        if (selectedEditorTarget?.source === 'local') {
            void window.electronAPI.updateLocalSessionState({
                filePath: selectedEditorTarget.path,
                fileName: selectedEditorTarget.name
            }).catch((error) => {
                console.error('Failed to persist last opened file:', error)
            })
        } else {
            void window.electronAPI.updateLocalSessionState({
                filePath: null,
                fileName: null
            }).catch((error) => {
                console.error('Failed to clear last opened file:', error)
            })
        }
    }, [selectedEditorTarget])

    useEffect(() => {
        if (!localRootPath) {
            setUndoState({ count: 0, lastDescription: null })
            return
        }
        void refreshUndoState()
    }, [localRootPath, refreshUndoState])

    useEffect(() => {
        const handleMutation = () => {
            if (!localRootPath) return
            void refreshUndoState()
        }

        window.addEventListener('pigtex:local-fs-refresh', handleMutation as EventListener)
        window.addEventListener('pigtex:fs-path-renamed', handleMutation as EventListener)
        window.addEventListener('pigtex:fs-path-deleted', handleMutation as EventListener)
        window.addEventListener('pigtex:fs-content-restored', handleMutation as EventListener)

        return () => {
            window.removeEventListener('pigtex:local-fs-refresh', handleMutation as EventListener)
            window.removeEventListener('pigtex:fs-path-renamed', handleMutation as EventListener)
            window.removeEventListener('pigtex:fs-path-deleted', handleMutation as EventListener)
            window.removeEventListener('pigtex:fs-content-restored', handleMutation as EventListener)
        }
    }, [localRootPath, refreshUndoState])

    useEffect(() => {
        const onKeyDown = (event: KeyboardEvent) => {
            const target = event.target as HTMLElement | null
            const tagName = target?.tagName || ''
            const isTypingTarget = tagName === 'INPUT' || tagName === 'TEXTAREA' || target?.isContentEditable

            // Ctrl+S — save active editor tab (works even inside textarea)
            if ((event.ctrlKey || event.metaKey) && !event.altKey && event.key.toLowerCase() === 's') {
                event.preventDefault()
                window.dispatchEvent(new CustomEvent('pigtex:editor-save'))
                return
            }

            if (isTypingTarget) return

            if (event.key === 'F5' && localRootPath) {
                event.preventDefault()
                void handleRefreshLocalFolder()
                return
            }

            if (!(event.ctrlKey || event.metaKey) || event.altKey) return

            const lowerKey = event.key.toLowerCase()

            // Ctrl+Z — undo last local file change
            if (lowerKey === 'z' && !event.shiftKey) {
                event.preventDefault()
                void handleUndoLastChange()
                return
            }

            if (lowerKey === 'o') {
                event.preventDefault()
                void handleOpenLocalFolder()
                return
            }

            if (lowerKey === 'n' && event.shiftKey) {
                event.preventDefault()
                void handleCreateLocalFolder()
                return
            }

            if (lowerKey === 'n') {
                event.preventDefault()
                void handleCreateLocalFile()
            }
        }

        window.addEventListener('keydown', onKeyDown)
        return () => window.removeEventListener('keydown', onKeyDown)
    }, [
        localRootPath,
        handleOpenLocalFolder,
        handleCreateLocalFile,
        handleCreateLocalFolder,
        handleRefreshLocalFolder,
        handleUndoLastChange
    ])

    useEffect(() => {
        sidebarCollapsedRef.current = sidebarCollapsed
    }, [sidebarCollapsed])

    useEffect(() => {
        sidebarWidthRef.current = sidebarWidth
    }, [sidebarWidth])

    useEffect(() => {
        explorerWidthRef.current = explorerWidth
    }, [explorerWidth])

    useEffect(() => {
        window.localStorage.setItem(SIDEBAR_STORAGE_KEY, `${sidebarWidth}`)
    }, [sidebarWidth])

    useEffect(() => {
        window.localStorage.setItem(EXPLORER_STORAGE_KEY, `${explorerWidth}`)
    }, [explorerWidth])

    useEffect(() => {
        reconcilePanelWidths()
    }, [sidebarCollapsed, reconcilePanelWidths])

    useEffect(() => {
        const handleWindowResize = () => {
            reconcilePanelWidths()
        }

        window.addEventListener('resize', handleWindowResize)
        return () => window.removeEventListener('resize', handleWindowResize)
    }, [reconcilePanelWidths])

    useEffect(() => {
        if (!window.electronAPI?.checkDesktopUpdate) {
            return
        }

        void handleCheckDesktopUpdate()
        const intervalId = window.setInterval(() => {
            void handleCheckDesktopUpdate()
        }, DESKTOP_UPDATE_POLL_INTERVAL_MS)

        return () => {
            window.clearInterval(intervalId)
        }
    }, [handleCheckDesktopUpdate])

    useEffect(() => {
        if (!activeResizeHandle) return

        const handlePointerMove = (event: PointerEvent) => {
            const containerRect = dashboardContentRef.current?.getBoundingClientRect()
            if (!containerRect) return

            if (activeResizeHandle === 'sidebar') {
                const bounds = getSidebarBounds(containerRect.width, explorerWidthRef.current)
                const nextWidth = clamp(
                    event.clientX - containerRect.left,
                    bounds.minWidth,
                    bounds.maxWidth
                )
                setSidebarWidth(prev => (prev === nextWidth ? prev : nextWidth))
                return
            }

            const effectiveSidebarWidth = sidebarCollapsedRef.current
                ? SIDEBAR_COLLAPSED_WIDTH
                : sidebarWidthRef.current
            const bounds = getExplorerBounds(containerRect.width, effectiveSidebarWidth)
            const nextWidth = clamp(
                containerRect.right - event.clientX,
                bounds.minWidth,
                bounds.maxWidth
            )
            setExplorerWidth(prev => (prev === nextWidth ? prev : nextWidth))
        }

        const stopResize = () => {
            setActiveResizeHandle(null)
        }

        document.body.classList.add('dashboard-is-resizing')
        window.addEventListener('pointermove', handlePointerMove)
        window.addEventListener('pointerup', stopResize)
        window.addEventListener('pointercancel', stopResize)
        window.addEventListener('blur', stopResize)

        return () => {
            document.body.classList.remove('dashboard-is-resizing')
            window.removeEventListener('pointermove', handlePointerMove)
            window.removeEventListener('pointerup', stopResize)
            window.removeEventListener('pointercancel', stopResize)
            window.removeEventListener('blur', stopResize)
        }
    }, [activeResizeHandle])

    const effectiveSidebarWidth = sidebarCollapsed ? SIDEBAR_COLLAPSED_WIDTH : sidebarWidth

    return (
        <div className="dashboard">
            <TitleBar
                viewMode={viewMode}
                onModeChange={setViewMode}
                showAdminMode={isAdmin}
                localRootPath={localRootPath}
                undoCount={undoState.count}
                undoDescription={undoState.lastDescription}
                isUndoing={isUndoing}
                onOpenLocalFolder={handleOpenLocalFolder}
                onCreateLocalFile={handleCreateLocalFile}
                onCreateLocalFolder={handleCreateLocalFolder}
                onRefreshLocalFolder={handleRefreshLocalFolder}
                onUndoLastChange={handleUndoLastChange}
            />

            <div className="dashboard-content" ref={dashboardContentRef}>
                {/* Left Sidebar - Always visible */}
                <div
                    className={`dashboard-sidebar ${sidebarCollapsed ? 'is-collapsed' : ''} ${activeResizeHandle === 'sidebar' ? 'is-resizing' : ''}`}
                    style={{ width: effectiveSidebarWidth }}
                >
                    <Sidebar
                        ref={sidebarRef}
                        collapsed={sidebarCollapsed}
                        onToggle={() => setSidebarCollapsed(!sidebarCollapsed)}
                        selectedWorkspaceId={selectedWorkspaceId}
                        onWorkspaceSelect={handleWorkspaceSelect}
                        selectedConversationId={selectedConversationId}
                        onConversationSelect={handleConversationSelect}
                        onNewChat={handleNewChat}
                        selectedLearningProgramId={selectedLearningProgram?.id ?? null}
                        onLearningProgramSelect={handleLearningProgramSelect}
                        isAdmin={isAdmin}
                        onOpenAdmin={handleOpenAdmin}
                        onOpenSettings={() => setShowSettingsModal(true)}
                        desktopUpdate={desktopUpdate}
                        isInstallingDesktopUpdate={isInstallingDesktopUpdate}
                        onInstallDesktopUpdate={() => {
                            void handleInstallDesktopUpdate()
                        }}
                    />
                </div>

                {!sidebarCollapsed && (
                    <div
                        className={`dashboard-resizer ${activeResizeHandle === 'sidebar' ? 'is-active' : ''}`}
                        role="separator"
                        aria-orientation="vertical"
                        aria-label={copy.resizeSidebar}
                        onPointerDown={handleSidebarResizeStart}
                        onDoubleClick={handleSidebarResizeReset}
                    >
                        <div className="dashboard-resizer-grip" />
                    </div>
                )}

                {/* Main Content Area */}
                <div className="dashboard-main">
                    <div className={`dashboard-main-view ${viewMode === 'chat' ? 'is-active' : 'is-hidden'}`}>
                        <div className="chat-centered-view">
                            <ChatPanel
                                variant="centered"
                                conversationId={selectedConversationId}
                                workspaceId={selectedWorkspaceId}
                                learningProgramId={selectedLearningProgram?.id ?? null}
                                learningProgramTitle={selectedLearningProgram?.title ?? null}
                                newChatToken={chatResetToken}
                                onConversationCreated={(id) => setSelectedConversationId(id)}
                                onConversationInvalidated={() => setSelectedConversationId(null)}
                                localRootPath={localRootPath}
                                settings={pigtexSettings}
                                onSettingsChange={handleSettingsChange}
                            />
                        </div>
                    </div>
                    <div className={`dashboard-main-view ${viewMode === 'editor' ? 'is-active' : 'is-hidden'}`}>
                        <div className="editor-view">
                            <MainPanel
                                onBackToChat={handleBackToChat}
                                selectedTarget={selectedEditorTarget}
                                workspaceId={selectedWorkspaceId}
                            />
                        </div>
                    </div>
                    <div className={`dashboard-main-view ${viewMode === 'admin' ? 'is-active' : 'is-hidden'}`}>
                        <AdminConsole />
                    </div>
                </div>

                <div
                    className={`dashboard-resizer ${activeResizeHandle === 'explorer' ? 'is-active' : ''}`}
                    role="separator"
                    aria-orientation="vertical"
                    aria-label={copy.resizeExplorer}
                    onPointerDown={handleExplorerResizeStart}
                    onDoubleClick={handleExplorerResizeReset}
                >
                    <div className="dashboard-resizer-grip" />
                </div>

                {/* Right Panel */}
                <div
                    className={`dashboard-right-panel ${activeResizeHandle === 'explorer' ? 'is-resizing' : ''}`}
                    style={{ width: explorerWidth, flexBasis: explorerWidth, maxWidth: explorerWidth }}
                >
                    <AnimatePresence mode="wait">
                        <motion.div
                            key="explorer-right"
                            initial={{ opacity: 0 }}
                            animate={{ opacity: 1 }}
                            exit={{ opacity: 0 }}
                            transition={{ duration: 0.15 }}
                            style={{ height: '100%' }}
                        >
                            <ExplorerPanel
                                ref={explorerRef}
                                onOpenFile={handleOpenFile}
                                workspaceId={selectedWorkspaceId}
                                activeTarget={selectedEditorTarget}
                                onLocalRootChange={setLocalRootPath}
                                onLocalPathRenamed={handleLocalPathRenamed}
                                onLocalPathDeleted={handleLocalPathDeleted}
                            />
                        </motion.div>
                    </AnimatePresence>
                </div>
            </div>

            <SettingsModal
                isOpen={showSettingsModal}
                settings={pigtexSettings}
                onClose={() => setShowSettingsModal(false)}
                onSave={handleSaveSettings}
                desktopUpdate={desktopUpdate}
                isCheckingDesktopUpdate={isCheckingDesktopUpdate}
                isInstallingDesktopUpdate={isInstallingDesktopUpdate}
                onCheckDesktopUpdate={() => handleCheckDesktopUpdate({ manual: true })}
                onInstallDesktopUpdate={handleInstallDesktopUpdate}
                onOpenDesktopUpdatePage={handleOpenDesktopUpdatePage}
            />
        </div>
    )
}

export default Dashboard
