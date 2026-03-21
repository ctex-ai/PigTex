import { useState, useEffect, useCallback, forwardRef, useImperativeHandle, useRef, MouseEvent as ReactMouseEvent } from 'react'
import { motion, AnimatePresence } from 'framer-motion'
import {
    Search,
    FolderOpen,
    FileText,
    Plus,
    ChevronRight,
    HardDrive,
    FilePlus2,
    FolderPlus,
    Pencil,
    Trash2,
    RefreshCw
} from 'lucide-react'
import { EditorTarget } from '../../../types/editor'
import { useI18n } from '../../../contexts/I18nContext'
import { showError, showInfo, showSuccess } from '../../Shared/Toast'
import MemoryManager from '../../Memory/MemoryManager'
import './ExplorerPanel.css'

interface ExplorerPanelProps {
    onOpenFile: (target: EditorTarget) => void
    workspaceId: string | null
    activeTarget?: EditorTarget | null
    onLocalRootChange?: (rootPath: string | null) => void
    onLocalPathRenamed?: (payload: { oldPath: string; newPath: string }) => void
    onLocalPathDeleted?: (payload: { targetPath: string; isDirectory: boolean }) => void
}

export interface ExplorerPanelHandle {
    openLocalFolder: () => Promise<void>
    createLocalFile: () => Promise<void>
    createLocalFolder: () => Promise<void>
    refreshLocalFolder: () => Promise<void>
}

interface LocalFsEntry {
    name: string
    path: string
    type: 'file' | 'directory'
    size: number
    mtimeMs: number
}

interface NameDialogOptions {
    title: string
    subtitle?: string
    placeholder: string
    defaultValue: string
    confirmLabel: string
    variant?: 'rename' | 'create'
}

interface ConfirmDialogOptions {
    title: string
    subtitle?: string
    message: string
    confirmLabel: string
}

type LocalContextTargetType = 'root' | 'directory' | 'file'

interface LocalContextMenuState {
    isOpen: boolean
    x: number
    y: number
    targetPath: string
    targetType: LocalContextTargetType
    targetName: string
}

const getBaseName = (filePath: string) => {
    const parts = filePath.split(/[\\/]+/).filter(Boolean)
    return parts[parts.length - 1] || filePath
}

const getParentPath = (targetPath: string) => {
    const normalized = targetPath.replace(/[\\/]+$/, '')
    const slashIndex = Math.max(normalized.lastIndexOf('/'), normalized.lastIndexOf('\\'))
    if (slashIndex <= 0) return normalized
    return normalized.slice(0, slashIndex)
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

const getErrorMessage = (error: unknown, fallback: string) => {
    if (error instanceof Error && error.message) {
        return error.message
            .replace(/^Error invoking remote method '[^']+':\s*/i, '')
            .replace(/^Error:\s*/i, '')
            .trim()
    }
    return fallback
}

const ExplorerPanel = forwardRef<ExplorerPanelHandle, ExplorerPanelProps>(({
    onOpenFile,
    workspaceId,
    activeTarget,
    onLocalRootChange,
    onLocalPathRenamed,
    onLocalPathDeleted
}, ref) => {
    const { isVietnamese } = useI18n()
    const [searchQuery, setSearchQuery] = useState('')
    const copy = isVietnamese ? {
        desktopOnly: 'Tính năng hệ thống tệp chỉ hoạt động trong ứng dụng desktop Electron',
        openedFolderMissing: (path: string) => `Thư mục đã mở không còn tồn tại: ${path}`,
        openedFolderSuccess: (name: string) => `Đã mở thư mục: ${name}`,
        lastOpenedFolderMissing: (path: string) => `Thư mục mở gần nhất không còn tồn tại: ${path}`,
        reopenFolderFailed: 'Không thể mở lại thư mục gần nhất',
        openFolderFailed: 'Không thể mở thư mục',
        openFolderFirst: 'Hãy mở thư mục trước',
        filesystemUnavailable: 'API hệ thống tệp hiện không khả dụng',
        newFile: 'Tệp mới',
        newFolder: 'Thư mục mới',
        inFolder: (name: string) => `trong ${name}`,
        create: 'Tạo',
        fileNameEmpty: 'Tên tệp không được để trống',
        folderNameEmpty: 'Tên thư mục không được để trống',
        fileNameInvalid: 'Tên tệp không được chứa / hoặc \\',
        folderNameInvalid: 'Tên thư mục không được chứa / hoặc \\',
        fileCreated: (name: string) => `Đã tạo tệp: ${name}`,
        folderCreated: (name: string) => `Đã tạo thư mục: ${name}`,
        createFileFailed: 'Không thể tạo tệp',
        createFolderFailed: 'Không thể tạo thư mục',
        rootRenameBlocked: 'Không thể đổi tên thư mục gốc đang mở',
        rename: 'Đổi tên',
        renameConfirm: 'Đổi tên',
        nameEmpty: 'Tên không được để trống',
        nameInvalid: 'Tên không được chứa / hoặc \\',
        renamedTo: (name: string) => `Đã đổi tên thành ${name}`,
        renameFailed: 'Không thể đổi tên',
        rootDeleteBlocked: 'Không thể xóa thư mục gốc đang mở',
        deleteFolder: 'Xóa thư mục',
        deleteFile: 'Xóa tệp',
        deleteConfirm: 'Xóa',
        deleted: (isDirectory: boolean) => isDirectory ? 'Đã xóa thư mục' : 'Đã xóa tệp',
        deleteFailed: 'Không thể xóa',
        emptyFolder: 'Thư mục trống',
        loading: 'Đang tải...',
        noFolder: 'Chưa mở thư mục',
        searchLocalFiles: 'Tìm kiếm tệp cục bộ...',
        localFolder: 'Thư mục cục bộ',
        inLabel: 'Trong',
        openFolderToEdit: 'Mở một thư mục cục bộ để chỉnh sửa tệp như Cursor',
        contextNewFile: 'Tệp mới',
        contextNewFolder: 'Thư mục mới',
        contextRefresh: 'Làm mới',
        contextRename: 'Đổi tên',
        contextDelete: 'Xóa',
        confirmHint: 'xác nhận',
        cancel: 'Hủy',
        cancelHint: 'hủy',
    } : {
        desktopOnly: 'Filesystem feature only works in desktop Electron app',
        openedFolderMissing: (path: string) => `Opened folder is no longer available: ${path}`,
        openedFolderSuccess: (name: string) => `Opened folder: ${name}`,
        lastOpenedFolderMissing: (path: string) => `Last opened folder is missing: ${path}`,
        reopenFolderFailed: 'Failed to reopen last folder',
        openFolderFailed: 'Failed to open folder',
        openFolderFirst: 'Open a folder first',
        filesystemUnavailable: 'Filesystem API is unavailable',
        newFile: 'New File',
        newFolder: 'New Folder',
        inFolder: (name: string) => `in ${name}`,
        create: 'Create',
        fileNameEmpty: 'File name cannot be empty',
        folderNameEmpty: 'Folder name cannot be empty',
        fileNameInvalid: 'File name cannot contain / or \\',
        folderNameInvalid: 'Folder name cannot contain / or \\',
        fileCreated: (name: string) => `File created: ${name}`,
        folderCreated: (name: string) => `Folder created: ${name}`,
        createFileFailed: 'Failed to create file',
        createFolderFailed: 'Failed to create folder',
        rootRenameBlocked: 'Cannot rename the opened root folder',
        rename: 'Rename',
        renameConfirm: 'Rename',
        nameEmpty: 'Name cannot be empty',
        nameInvalid: 'Name cannot contain / or \\',
        renamedTo: (name: string) => `Renamed to ${name}`,
        renameFailed: 'Failed to rename',
        rootDeleteBlocked: 'Cannot delete the opened root folder',
        deleteFolder: 'Delete Folder',
        deleteFile: 'Delete File',
        deleteConfirm: 'Delete',
        deleted: (isDirectory: boolean) => isDirectory ? 'Folder deleted' : 'File deleted',
        deleteFailed: 'Failed to delete',
        emptyFolder: 'Empty folder',
        loading: 'Loading...',
        noFolder: 'No folder',
        searchLocalFiles: 'Search local files...',
        localFolder: 'Local Folder',
        inLabel: 'In',
        openFolderToEdit: 'Open a local folder to edit files like Cursor',
        contextNewFile: 'New File',
        contextNewFolder: 'New Folder',
        contextRefresh: 'Refresh',
        contextRename: 'Rename',
        contextDelete: 'Delete',
        confirmHint: 'confirm',
        cancel: 'Cancel',
        cancelHint: 'cancel',
    }

    const [localRootPath, setLocalRootPath] = useState<string | null>(null)
    const [selectedLocalDirPath, setSelectedLocalDirPath] = useState<string | null>(null)
    const [localTree, setLocalTree] = useState<Record<string, LocalFsEntry[]>>({})
    const [expandedDirs, setExpandedDirs] = useState<Set<string>>(new Set())
    const [loadingDirs, setLoadingDirs] = useState<Set<string>>(new Set())
    const [, setIsPickingFolder] = useState(false)
    const [nameDialog, setNameDialog] = useState<{
        isOpen: boolean
        title: string
        subtitle?: string
        placeholder: string
        confirmLabel: string
        variant: 'rename' | 'create'
    }>({
        isOpen: false,
        title: '',
        subtitle: '',
        placeholder: '',
        confirmLabel: copy.create,
        variant: 'create'
    })
    const [nameInputValue, setNameInputValue] = useState('')
    const nameDialogResolverRef = useRef<((value: string | null) => void) | null>(null)
    const nameInputRef = useRef<HTMLInputElement>(null)
    const [confirmDialog, setConfirmDialog] = useState<{
        isOpen: boolean
        title: string
        subtitle?: string
        message: string
        confirmLabel: string
    }>({
        isOpen: false,
        title: '',
        subtitle: '',
        message: '',
        confirmLabel: copy.deleteConfirm
    })
    const confirmDialogResolverRef = useRef<((value: boolean) => void) | null>(null)
    const [contextMenu, setContextMenu] = useState<LocalContextMenuState>({
        isOpen: false,
        x: 0,
        y: 0,
        targetPath: '',
        targetType: 'root',
        targetName: ''
    })
    const contextMenuRef = useRef<HTMLDivElement>(null)
    const didRestoreLocalRootRef = useRef(false)

    const search = searchQuery.trim().toLowerCase()

    const closeNameDialog = useCallback((value: string | null) => {
        setNameDialog(prev => ({ ...prev, isOpen: false }))
        const resolve = nameDialogResolverRef.current
        nameDialogResolverRef.current = null
        resolve?.(value)
    }, [])

    const closeConfirmDialog = useCallback((value: boolean) => {
        setConfirmDialog(prev => ({ ...prev, isOpen: false }))
        const resolve = confirmDialogResolverRef.current
        confirmDialogResolverRef.current = null
        resolve?.(value)
    }, [])

    const closeContextMenu = useCallback(() => {
        setContextMenu(prev => ({ ...prev, isOpen: false }))
    }, [])

    const requestName = useCallback((options: NameDialogOptions) => {
        return new Promise<string | null>((resolve) => {
            nameDialogResolverRef.current = resolve
            setNameInputValue(options.defaultValue)
            setNameDialog({
                isOpen: true,
                title: options.title,
                subtitle: options.subtitle || '',
                placeholder: options.placeholder,
                confirmLabel: options.confirmLabel,
                variant: options.variant || 'create'
            })
        })
    }, [])

    const requestConfirm = useCallback((options: ConfirmDialogOptions) => {
        return new Promise<boolean>((resolve) => {
            confirmDialogResolverRef.current = resolve
            setConfirmDialog({
                isOpen: true,
                title: options.title,
                subtitle: options.subtitle || '',
                message: options.message,
                confirmLabel: options.confirmLabel
            })
        })
    }, [])

    const openLocalContextMenu = useCallback(
        (
            event: ReactMouseEvent<HTMLElement>,
            payload: { path: string; type: LocalContextTargetType; name: string }
        ) => {
            event.preventDefault()
            event.stopPropagation()
            const menuWidth = 196
            const menuHeight = 220
            const x = Math.min(event.clientX, window.innerWidth - menuWidth)
            const y = Math.min(event.clientY, window.innerHeight - menuHeight)
            setContextMenu({
                isOpen: true,
                x: Math.max(8, x),
                y: Math.max(8, y),
                targetPath: payload.path,
                targetType: payload.type,
                targetName: payload.name
            })
        },
        []
    )

    useEffect(() => {
        if (!nameDialog.isOpen) return
        const id = window.setTimeout(() => {
            nameInputRef.current?.focus()
            nameInputRef.current?.select()
        }, 0)
        return () => window.clearTimeout(id)
    }, [nameDialog.isOpen])

    useEffect(() => {
        return () => {
            if (nameDialogResolverRef.current) {
                nameDialogResolverRef.current(null)
                nameDialogResolverRef.current = null
            }
            if (confirmDialogResolverRef.current) {
                confirmDialogResolverRef.current(false)
                confirmDialogResolverRef.current = null
            }
        }
    }, [])

    useEffect(() => {
        if (!contextMenu.isOpen) return

        const handleGlobalMouseDown = (event: MouseEvent) => {
            const target = event.target as Node | null
            if (contextMenuRef.current?.contains(target)) return
            closeContextMenu()
        }

        const handleEscape = (event: KeyboardEvent) => {
            if (event.key === 'Escape') {
                closeContextMenu()
            }
        }

        window.addEventListener('mousedown', handleGlobalMouseDown)
        window.addEventListener('keydown', handleEscape)
        window.addEventListener('blur', closeContextMenu)
        window.addEventListener('resize', closeContextMenu)

        return () => {
            window.removeEventListener('mousedown', handleGlobalMouseDown)
            window.removeEventListener('keydown', handleEscape)
            window.removeEventListener('blur', closeContextMenu)
            window.removeEventListener('resize', closeContextMenu)
        }
    }, [contextMenu.isOpen, closeContextMenu])

    const loadLocalDirectory = useCallback(async (dirPath: string, rootOverride?: string) => {
        const rootPath = rootOverride || localRootPath
        if (!rootPath) return
        if (!window.electronAPI?.listDirectory) {
            showError(copy.desktopOnly)
            return
        }

        setLoadingDirs(prev => {
            const next = new Set(prev)
            next.add(dirPath)
            return next
        })

        try {
            const entries = await window.electronAPI.listDirectory({ rootPath, dirPath })
            setLocalTree(prev => ({ ...prev, [dirPath]: entries }))
        } catch (error) {
            console.error('Failed to list directory:', error)
            const message = getErrorMessage(error, 'Failed to read folder')

            if (dirPath === rootPath && /folder not found:/i.test(message)) {
                setLocalRootPath(null)
                setSelectedLocalDirPath(null)
                setLocalTree({})
                setExpandedDirs(new Set())
                showError(copy.openedFolderMissing(rootPath))
            } else {
                showError(message)
            }
        } finally {
            setLoadingDirs(prev => {
                const next = new Set(prev)
                next.delete(dirPath)
                return next
            })
        }
    }, [copy, localRootPath])

    const refreshLocalTree = useCallback(async (rootOverride?: string, expandedOverride?: Set<string>) => {
        const rootPath = rootOverride || localRootPath
        if (!rootPath) return
        if (!window.electronAPI?.listDirectory) {
            showError(copy.desktopOnly)
            return
        }

        const expandedSnapshot = new Set(expandedOverride ?? expandedDirs)
        expandedSnapshot.add(rootPath)
        const nextTree: Record<string, LocalFsEntry[]> = {}
        const nextExpanded = new Set<string>([rootPath])
        let rootMissing = false

        const refreshDirectory = async (dirPath: string) => {
            setLoadingDirs(prev => {
                const next = new Set(prev)
                next.add(dirPath)
                return next
            })

            try {
                const entries = await window.electronAPI!.listDirectory({ rootPath, dirPath })
                nextTree[dirPath] = entries

                for (const entry of entries) {
                    if (entry.type !== 'directory') continue
                    if (!expandedSnapshot.has(entry.path)) continue
                    nextExpanded.add(entry.path)
                    await refreshDirectory(entry.path)
                }
            } catch (error) {
                console.error('Failed to refresh directory:', error)
                const message = getErrorMessage(error, 'Failed to read folder')

                if (dirPath === rootPath && /folder not found:/i.test(message)) {
                    rootMissing = true
                    setLocalRootPath(null)
                    setSelectedLocalDirPath(null)
                    setLocalTree({})
                    setExpandedDirs(new Set())
                    showError(copy.openedFolderMissing(rootPath))
                    return
                }

                // Expanded nested folders can disappear after AI mutations.
                // Skip them quietly and let the refreshed parent listing become the source of truth.
            } finally {
                setLoadingDirs(prev => {
                    const next = new Set(prev)
                    next.delete(dirPath)
                    return next
                })
            }
        }

        await refreshDirectory(rootPath)
        if (rootMissing) return

        setLocalTree(nextTree)
        setExpandedDirs(nextExpanded)
    }, [copy, expandedDirs, localRootPath])

    useEffect(() => {
        onLocalRootChange?.(localRootPath)
        if (!didRestoreLocalRootRef.current && !localRootPath) {
            return
        }
        if (!window.electronAPI?.updateLocalSessionState) {
            return
        }
        void window.electronAPI.updateLocalSessionState({ rootPath: localRootPath }).catch((error) => {
            console.error('Failed to persist local root path:', error)
        })
    }, [localRootPath, onLocalRootChange])

    useEffect(() => {
        if (!localRootPath) {
            setSelectedLocalDirPath(null)
            return
        }

        if (!selectedLocalDirPath) {
            setSelectedLocalDirPath(localRootPath)
            return
        }

        if (!isSameOrChildPath(selectedLocalDirPath, localRootPath)) {
            setSelectedLocalDirPath(localRootPath)
        }
    }, [localRootPath, selectedLocalDirPath])

    const getCreateParentPath = useCallback(() => {
        if (!localRootPath) return null
        if (selectedLocalDirPath) return selectedLocalDirPath
        if (activeTarget?.source === 'local') return getParentPath(activeTarget.path)
        return localRootPath
    }, [localRootPath, selectedLocalDirPath, activeTarget])

    const applyOpenedLocalRoot = useCallback(async (rootPath: string, announce: boolean) => {
        setLocalRootPath(rootPath)
        setSelectedLocalDirPath(rootPath)
        setLocalTree({})
        setExpandedDirs(new Set([rootPath]))
        await refreshLocalTree(rootPath, new Set([rootPath]))

        if (announce) {
            showSuccess(copy.openedFolderSuccess(getBaseName(rootPath)))
        }
    }, [copy, refreshLocalTree])

    useEffect(() => {
        if (didRestoreLocalRootRef.current) return
        didRestoreLocalRootRef.current = true

        if (!window.electronAPI?.getLocalSessionState || !window.electronAPI?.openFolder) return

        let cancelled = false

        const restoreLastFolder = async () => {
            try {
                const sessionState = await window.electronAPI!.getLocalSessionState()
                if (cancelled) return
                const savedRootPath = sessionState.rootPath?.trim()
                if (!savedRootPath) return

                const result = await window.electronAPI!.openFolder({ path: savedRootPath })
                if (cancelled) return

                if (result.opened && result.path) {
                    await applyOpenedLocalRoot(result.path, false)
                    return
                }

                await window.electronAPI!.updateLocalSessionState({ rootPath: null })
                if (result.missing) {
                    showInfo(copy.lastOpenedFolderMissing(savedRootPath))
                } else {
                    showError(result.message || copy.reopenFolderFailed)
                }
            } catch (error) {
                if (cancelled) return
                console.error('Failed to restore last folder:', error)
                showError(copy.reopenFolderFailed)
            }
        }

        void restoreLastFolder()

        return () => {
            cancelled = true
        }
    }, [applyOpenedLocalRoot, copy])

    const handlePickFolder = async () => {
        if (!window.electronAPI?.pickFolder) {
            showError(copy.desktopOnly)
            return
        }

        setIsPickingFolder(true)
        try {
            const result = await window.electronAPI.pickFolder()
            if (result.canceled || !result.path) return

            await applyOpenedLocalRoot(result.path, true)
        } catch (error) {
            console.error('Failed to pick folder:', error)
            showError(copy.openFolderFailed)
        } finally {
            setIsPickingFolder(false)
        }
    }

    const handleRefreshLocal = useCallback(async () => {
        if (!localRootPath) return
        await refreshLocalTree(localRootPath)
    }, [localRootPath, refreshLocalTree])

    useEffect(() => {
        const handleExternalRefresh = () => {
            void handleRefreshLocal()
        }

        window.addEventListener('pigtex:local-fs-refresh', handleExternalRefresh as EventListener)
        return () => {
            window.removeEventListener('pigtex:local-fs-refresh', handleExternalRefresh as EventListener)
        }
    }, [handleRefreshLocal])

    const handleCreateLocalFile = async (parentPathOverride?: string) => {
        const parentPath = parentPathOverride || getCreateParentPath()
        if (!parentPath || !localRootPath) {
            showError(copy.openFolderFirst)
            return
        }
        if (!window.electronAPI?.createFile) {
            showError(copy.filesystemUnavailable)
            return
        }

        try {
            const fileNameInput = await requestName({
                title: copy.newFile,
                subtitle: copy.inFolder(getBaseName(parentPath)),
                placeholder: 'untitled.txt',
                defaultValue: 'untitled.txt',
                confirmLabel: copy.create,
                variant: 'create'
            })
            if (fileNameInput === null) return

            const fileName = fileNameInput.trim()
            if (!fileName) {
                showError(copy.fileNameEmpty)
                return
            }
            if (/[\\/]/.test(fileName)) {
                showError(copy.fileNameInvalid)
                return
            }

            const result = await window.electronAPI.createFile({
                parentPath,
                fileName,
                content: ''
            })
            const createdPath = result.path

            setExpandedDirs(prev => {
                const next = new Set(prev)
                next.add(parentPath)
                return next
            })
            setSelectedLocalDirPath(parentPath)
            await loadLocalDirectory(parentPath, localRootPath)
            showSuccess(copy.fileCreated(getBaseName(createdPath)))
            onOpenFile({
                source: 'local',
                path: createdPath,
                rootPath: localRootPath,
                name: getBaseName(createdPath)
            })
        } catch (error) {
            console.error('Failed to create file:', error)
            showError(getErrorMessage(error, copy.createFileFailed))
        }
    }

    const handleCreateLocalFolder = async (parentPathOverride?: string) => {
        const parentPath = parentPathOverride || getCreateParentPath()
        if (!parentPath || !localRootPath) {
            showError(copy.openFolderFirst)
            return
        }
        if (!window.electronAPI?.createFolder) {
            showError(copy.filesystemUnavailable)
            return
        }

        try {
            const folderNameInput = await requestName({
                title: copy.newFolder,
                subtitle: copy.inFolder(getBaseName(parentPath)),
                placeholder: 'new-folder',
                defaultValue: 'new-folder',
                confirmLabel: copy.create,
                variant: 'create'
            })
            if (folderNameInput === null) return

            const folderName = folderNameInput.trim()
            if (!folderName) {
                showError(copy.folderNameEmpty)
                return
            }
            if (/[\\/]/.test(folderName)) {
                showError(copy.folderNameInvalid)
                return
            }

            const result = await window.electronAPI.createFolder({
                parentPath,
                folderName
            })
            const createdPath = result.path

            await loadLocalDirectory(parentPath, localRootPath)
            setExpandedDirs(prev => {
                const next = new Set(prev)
                next.add(parentPath)
                next.add(createdPath)
                return next
            })
            setSelectedLocalDirPath(createdPath)
            await loadLocalDirectory(createdPath, localRootPath)
            showSuccess(copy.folderCreated(getBaseName(createdPath)))
        } catch (error) {
            console.error('Failed to create folder:', error)
            showError(getErrorMessage(error, copy.createFolderFailed))
        }
    }

    const handleRenameLocalPath = async (targetPath: string, targetName: string) => {
        if (!localRootPath) {
            showError(copy.openFolderFirst)
            return
        }
        if (!window.electronAPI?.renamePath) {
            showError(copy.filesystemUnavailable)
            return
        }
        if (targetPath === localRootPath) {
            showError(copy.rootRenameBlocked)
            return
        }

        const renamedInput = await requestName({
            title: copy.rename,
            subtitle: targetName,
            placeholder: targetName,
            defaultValue: targetName,
            confirmLabel: copy.renameConfirm,
            variant: 'rename'
        })
        if (renamedInput === null) return

        const newName = renamedInput.trim()
        if (!newName) {
            showError(copy.nameEmpty)
            return
        }
        if (/[\\/]/.test(newName)) {
            showError(copy.nameInvalid)
            return
        }
        if (newName === targetName) return

        try {
            const result = await window.electronAPI.renamePath({
                targetPath,
                newName
            })
            const nextPath = result.path

            setLocalTree(prev => {
                const next: Record<string, LocalFsEntry[]> = {}
                Object.entries(prev).forEach(([dirPath, entries]) => {
                    const mappedDirPath = replacePathPrefix(dirPath, targetPath, nextPath)
                    next[mappedDirPath] = entries.map((entry) => {
                        const mappedEntryPath = replacePathPrefix(entry.path, targetPath, nextPath)
                        return {
                            ...entry,
                            path: mappedEntryPath,
                            name: entry.path === targetPath ? getBaseName(nextPath) : entry.name
                        }
                    })
                })
                return next
            })

            setExpandedDirs(prev => new Set(Array.from(prev).map(path => replacePathPrefix(path, targetPath, nextPath))))
            setSelectedLocalDirPath(prev => {
                if (!prev) return prev
                return replacePathPrefix(prev, targetPath, nextPath)
            })

            if (activeTarget?.source === 'local' && isSameOrChildPath(activeTarget.path, targetPath)) {
                onLocalPathRenamed?.({ oldPath: targetPath, newPath: nextPath })
            }

            window.dispatchEvent(new CustomEvent('pigtex:fs-path-renamed', {
                detail: { oldPath: targetPath, newPath: nextPath }
            }))

            await loadLocalDirectory(getParentPath(nextPath), localRootPath)
            showSuccess(copy.renamedTo(getBaseName(nextPath)))
        } catch (error) {
            console.error('Failed to rename path:', error)
            showError(getErrorMessage(error, copy.renameFailed))
        }
    }

    const handleDeleteLocalPath = async (
        targetPath: string,
        targetType: LocalContextTargetType,
        targetName: string
    ) => {
        if (!localRootPath) {
            showError(copy.openFolderFirst)
            return
        }
        if (!window.electronAPI?.deletePath) {
            showError(copy.filesystemUnavailable)
            return
        }
        if (targetPath === localRootPath) {
            showError(copy.rootDeleteBlocked)
            return
        }

        const confirmed = await requestConfirm({
            title: targetType === 'directory' ? copy.deleteFolder : copy.deleteFile,
            subtitle: targetName,
            message: isVietnamese
                ? `Bạn có chắc muốn xóa "${targetName}"? Bạn có thể hoàn tác thao tác này từ thanh công cụ.`
                : `Are you sure you want to delete "${targetName}"? You can undo this action from the toolbar.`,
            confirmLabel: copy.deleteConfirm
        })
        if (!confirmed) return

        const isDirectory = targetType === 'directory'
        const parentPath = getParentPath(targetPath)

        try {
            await window.electronAPI.deletePath({ targetPath })

            setLocalTree(prev => {
                const next: Record<string, LocalFsEntry[]> = {}
                Object.entries(prev).forEach(([dirPath, entries]) => {
                    if (isSameOrChildPath(dirPath, targetPath)) return
                    next[dirPath] = entries.filter(entry => !isSameOrChildPath(entry.path, targetPath))
                })
                return next
            })

            setExpandedDirs(prev => new Set(Array.from(prev).filter(path => !isSameOrChildPath(path, targetPath))))
            setSelectedLocalDirPath(prev => {
                if (!prev) return prev
                if (isSameOrChildPath(prev, targetPath)) return parentPath
                return prev
            })

            if (activeTarget?.source === 'local' && isSameOrChildPath(activeTarget.path, targetPath)) {
                onLocalPathDeleted?.({
                    targetPath,
                    isDirectory
                })
            }

            window.dispatchEvent(new CustomEvent('pigtex:fs-path-deleted', {
                detail: {
                    targetPath,
                    isDirectory
                }
            }))

            await loadLocalDirectory(parentPath, localRootPath)
            showSuccess(copy.deleted(isDirectory))
        } catch (error) {
            console.error('Failed to delete path:', error)
            showError(getErrorMessage(error, copy.deleteFailed))
        }
    }

    useImperativeHandle(ref, () => ({
        openLocalFolder: handlePickFolder,
        createLocalFile: handleCreateLocalFile,
        createLocalFolder: handleCreateLocalFolder,
        refreshLocalFolder: handleRefreshLocal
    }), [
        handlePickFolder,
        handleCreateLocalFile,
        handleCreateLocalFolder,
        handleRefreshLocal
    ])

    const toggleDirectory = async (dirPath: string) => {
        const shouldExpand = !expandedDirs.has(dirPath)
        setExpandedDirs(prev => {
            const next = new Set(prev)
            if (next.has(dirPath)) {
                next.delete(dirPath)
            } else {
                next.add(dirPath)
            }
            return next
        })

        if (shouldExpand && !localTree[dirPath]) {
            await loadLocalDirectory(dirPath)
        }
    }

    const matchesSearch = (name: string) => !search || name.toLowerCase().includes(search)

    const hasMatchInSubtree = useCallback((dirPath: string): boolean => {
        const entries = localTree[dirPath] || []
        return entries.some((entry) => {
            if (matchesSearch(entry.name)) return true
            if (entry.type === 'directory') return hasMatchInSubtree(entry.path)
            return false
        })
    }, [localTree, search])

    const renderLocalDirectory = (dirPath: string, depth: number = 0): JSX.Element => {
        const entries = localTree[dirPath] || []

        const visibleEntries = entries.filter((entry) => {
            if (!search) return true
            if (matchesSearch(entry.name)) return true
            if (entry.type === 'directory') return hasMatchInSubtree(entry.path)
            return false
        })

        if (visibleEntries.length === 0 && !loadingDirs.has(dirPath)) {
            return <div className="explorer-empty local-empty">{copy.emptyFolder}</div>
        }

        return (
            <>
                {visibleEntries.map((entry) => {
                    if (entry.type === 'directory') {
                        const isExpanded = expandedDirs.has(entry.path)
                        const isLoadingDir = loadingDirs.has(entry.path)
                        const isSelectedDir = selectedLocalDirPath === entry.path
                        return (
                            <div key={entry.path} className="tree-item-wrapper">
                                <button
                                    className={`tree-item local-tree-item ${isSelectedDir ? 'active' : ''}`}
                                    style={{ paddingLeft: 12 + depth * 14 }}
                                    onClick={() => {
                                        closeContextMenu()
                                        setSelectedLocalDirPath(entry.path)
                                        void toggleDirectory(entry.path)
                                    }}
                                    onContextMenu={(event) =>
                                        openLocalContextMenu(event, {
                                            path: entry.path,
                                            type: 'directory',
                                            name: entry.name
                                        })
                                    }
                                >
                                    <span className={`tree-chevron ${isExpanded ? 'expanded' : ''}`}>
                                        <ChevronRight size={12} />
                                    </span>
                                    <span className="tree-icon">
                                        <FolderOpen size={14} />
                                    </span>
                                    <span className="tree-name">{entry.name}</span>
                                </button>
                                {isExpanded && (
                                    <div className="tree-children">
                                        {isLoadingDir ? (
                                            <div className="explorer-loading local-loading">{copy.loading}</div>
                                        ) : (
                                            renderLocalDirectory(entry.path, depth + 1)
                                        )}
                                    </div>
                                )}
                            </div>
                        )
                    }

                    const isActive = activeTarget?.source === 'local' && activeTarget.path === entry.path

                    return (
                        <motion.button
                            key={entry.path}
                            className={`tree-item local-tree-item ${isActive ? 'active' : ''}`}
                            style={{ paddingLeft: 28 + depth * 14 }}
                            onClick={() => {
                                closeContextMenu()
                                if (!localRootPath) return
                                onOpenFile({
                                    source: 'local',
                                    path: entry.path,
                                    rootPath: localRootPath,
                                    name: entry.name
                                })
                            }}
                            onContextMenu={(event) =>
                                openLocalContextMenu(event, {
                                    path: entry.path,
                                    type: 'file',
                                    name: entry.name
                                })
                            }
                            whileHover={{ backgroundColor: 'var(--color-bg-hover)' }}
                        >
                            <span className="tree-icon">
                                <FileText size={14} />
                            </span>
                            <span className="tree-name">{entry.name}</span>
                        </motion.button>
                    )
                })}
            </>
        )
    }

    const localRootName = localRootPath ? getBaseName(localRootPath) : copy.noFolder
    const localCreateTargetName = selectedLocalDirPath
        ? getBaseName(selectedLocalDirPath)
        : localRootName
    const isContextDirectory = contextMenu.targetType === 'directory' || contextMenu.targetType === 'root'
    const canContextRename = contextMenu.targetType !== 'root'
    const canContextDelete = contextMenu.targetType !== 'root'

    return (
        <div className="explorer-panel">
            <div className="explorer-memory-section">
                <MemoryManager workspaceId={workspaceId} />
            </div>

            <div className="explorer-search">
                <Search size={14} className="search-icon" />
                <input
                    type="text"
                    placeholder={copy.searchLocalFiles}
                    className="search-input"
                    value={searchQuery}
                    onChange={(e) => setSearchQuery(e.target.value)}
                />
            </div>

            <div className="explorer-section explorer-tree">
                <div className="section-header">
                    <HardDrive size={12} />
                    <span>{copy.localFolder}</span>
                    <span className="item-count" title={selectedLocalDirPath || localRootPath || ''}>
                        {copy.inLabel}: {localCreateTargetName}
                    </span>
                </div>

                <div
                    className="file-tree local-file-tree"
                    onContextMenu={(event) => {
                        if (!localRootPath) return
                        const target = event.target as HTMLElement
                        if (target.closest('.local-tree-item')) return
                        openLocalContextMenu(event, {
                            path: localRootPath,
                            type: 'root',
                            name: localRootName
                        })
                    }}
                >
                    {!localRootPath ? (
                        <div className="explorer-empty">
                            {copy.openFolderToEdit}
                        </div>
                    ) : loadingDirs.has(localRootPath) && !localTree[localRootPath] ? (
                        <div className="explorer-loading">{copy.loading}</div>
                    ) : (
                        renderLocalDirectory(localRootPath, 0)
                    )}
                </div>
            </div>

            <AnimatePresence>
                {contextMenu.isOpen && (
                    <motion.div
                        ref={contextMenuRef}
                        className="explorer-context-menu"
                        style={{ left: contextMenu.x, top: contextMenu.y }}
                        initial={{ opacity: 0, scale: 0.96, y: 6 }}
                        animate={{ opacity: 1, scale: 1, y: 0 }}
                        exit={{ opacity: 0, scale: 0.96, y: 4 }}
                        transition={{ duration: 0.12, ease: [0.23, 1, 0.32, 1] }}
                        onContextMenu={(event) => event.preventDefault()}
                    >
                        {isContextDirectory && (
                            <>
                                <button
                                    className="explorer-context-item"
                                    onClick={() => {
                                        closeContextMenu()
                                        void handleCreateLocalFile(contextMenu.targetPath)
                                    }}
                                >
                                    <FilePlus2 size={14} />
                                    <span className="context-label">{copy.contextNewFile}</span>
                                </button>
                                <button
                                    className="explorer-context-item"
                                    onClick={() => {
                                        closeContextMenu()
                                        void handleCreateLocalFolder(contextMenu.targetPath)
                                    }}
                                >
                                    <FolderPlus size={14} />
                                    <span className="context-label">{copy.contextNewFolder}</span>
                                </button>
                                <button
                                    className="explorer-context-item"
                                    onClick={() => {
                                        closeContextMenu()
                                        void loadLocalDirectory(contextMenu.targetPath, localRootPath || undefined)
                                    }}
                                >
                                    <RefreshCw size={14} />
                                    <span className="context-label">{copy.contextRefresh}</span>
                                </button>
                            </>
                        )}
                        {(isContextDirectory && (canContextRename || canContextDelete)) && (
                            <div className="explorer-context-divider" />
                        )}
                        {canContextRename && (
                            <button
                                className="explorer-context-item"
                                onClick={() => {
                                    closeContextMenu()
                                    void handleRenameLocalPath(contextMenu.targetPath, contextMenu.targetName)
                                }}
                            >
                                <Pencil size={14} />
                                <span className="context-label">{copy.contextRename}</span>
                                <span className="context-shortcut">F2</span>
                            </button>
                        )}
                        {canContextDelete && (
                            <button
                                className="explorer-context-item explorer-context-item-danger"
                                onClick={() => {
                                    closeContextMenu()
                                    void handleDeleteLocalPath(
                                        contextMenu.targetPath,
                                        contextMenu.targetType,
                                        contextMenu.targetName
                                    )
                                }}
                            >
                                <Trash2 size={14} />
                                <span className="context-label">{copy.contextDelete}</span>
                                <span className="context-shortcut">Del</span>
                            </button>
                        )}
                    </motion.div>
                )}
            </AnimatePresence>

            <AnimatePresence>
                {nameDialog.isOpen && (
                    <motion.div
                        className="explorer-modal-overlay"
                        initial={{ opacity: 0 }}
                        animate={{ opacity: 1 }}
                        exit={{ opacity: 0 }}
                        transition={{ duration: 0.15 }}
                        onClick={() => closeNameDialog(null)}
                    >
                        <motion.div
                            className="explorer-modal"
                            initial={{ opacity: 0, scale: 0.96, y: -12 }}
                            animate={{ opacity: 1, scale: 1, y: 0 }}
                            exit={{ opacity: 0, scale: 0.96, y: -8 }}
                            transition={{ duration: 0.18, ease: [0.23, 1, 0.32, 1] }}
                            onClick={(e) => e.stopPropagation()}
                        >
                            <div className="explorer-modal-header">
                                <div className={`explorer-modal-icon ${nameDialog.variant === 'rename' ? 'icon-rename' : 'icon-create'}`}>
                                    {nameDialog.variant === 'rename'
                                        ? <Pencil size={16} />
                                        : <Plus size={16} />
                                    }
                                </div>
                                <div className="explorer-modal-header-text">
                                    <div className="explorer-modal-title">{nameDialog.title}</div>
                                    {nameDialog.subtitle && (
                                        <div className="explorer-modal-subtitle">{nameDialog.subtitle}</div>
                                    )}
                                </div>
                            </div>
                            <div className="explorer-modal-input-wrapper">
                                <input
                                    ref={nameInputRef}
                                    className="explorer-modal-input"
                                    value={nameInputValue}
                                    onChange={(e) => setNameInputValue(e.target.value)}
                                    placeholder={nameDialog.placeholder}
                                    onKeyDown={(e) => {
                                        if (e.key === 'Escape') {
                                            e.preventDefault()
                                            closeNameDialog(null)
                                        }
                                        if (e.key === 'Enter') {
                                            e.preventDefault()
                                            closeNameDialog(nameInputValue)
                                        }
                                    }}
                                />
                            </div>
                            <div className="explorer-modal-actions">
                                <div className="explorer-modal-hint">
                                    <kbd>↵</kbd> <span>{copy.confirmHint}</span>
                                    <kbd>Esc</kbd> <span>{copy.cancelHint}</span>
                                </div>
                                <button
                                    className="explorer-modal-btn"
                                    onClick={() => closeNameDialog(null)}
                                >
                                    {copy.cancel}
                                </button>
                                <button
                                    className="explorer-modal-btn explorer-modal-btn-primary"
                                    onClick={() => closeNameDialog(nameInputValue)}
                                >
                                    {nameDialog.confirmLabel}
                                </button>
                            </div>
                        </motion.div>
                    </motion.div>
                )}
            </AnimatePresence>

            <AnimatePresence>
                {confirmDialog.isOpen && (
                    <motion.div
                        className="explorer-modal-overlay"
                        initial={{ opacity: 0 }}
                        animate={{ opacity: 1 }}
                        exit={{ opacity: 0 }}
                        transition={{ duration: 0.15 }}
                        onClick={() => closeConfirmDialog(false)}
                    >
                        <motion.div
                            className="explorer-modal"
                            initial={{ opacity: 0, scale: 0.96, y: -12 }}
                            animate={{ opacity: 1, scale: 1, y: 0 }}
                            exit={{ opacity: 0, scale: 0.96, y: -8 }}
                            transition={{ duration: 0.18, ease: [0.23, 1, 0.32, 1] }}
                            onClick={(e) => e.stopPropagation()}
                        >
                            <div className="explorer-modal-header">
                                <div className="explorer-modal-icon icon-delete">
                                    <Trash2 size={16} />
                                </div>
                                <div className="explorer-modal-header-text">
                                    <div className="explorer-modal-title">{confirmDialog.title}</div>
                                    {confirmDialog.subtitle && (
                                        <div className="explorer-modal-subtitle">{confirmDialog.subtitle}</div>
                                    )}
                                </div>
                            </div>
                            <p className="explorer-modal-message">{confirmDialog.message}</p>
                            <div className="explorer-modal-actions">
                                <div className="explorer-modal-hint">
                                    <kbd>Esc</kbd> <span>{copy.cancelHint}</span>
                                </div>
                                <button
                                    className="explorer-modal-btn"
                                    onClick={() => closeConfirmDialog(false)}
                                >
                                    {copy.cancel}
                                </button>
                                <button
                                    className="explorer-modal-btn explorer-modal-btn-danger"
                                    onClick={() => closeConfirmDialog(true)}
                                >
                                    {confirmDialog.confirmLabel}
                                </button>
                            </div>
                        </motion.div>
                    </motion.div>
                )}
            </AnimatePresence>
        </div>
    )
})

ExplorerPanel.displayName = 'ExplorerPanel'

export default ExplorerPanel
