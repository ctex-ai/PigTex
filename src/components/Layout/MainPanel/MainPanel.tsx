import { useCallback, useEffect, useMemo, useRef, useState } from 'react'
import { motion } from 'framer-motion'
import {
    ArrowLeft,
    Bold,
    Check,
    ChevronDown,
    Code,
    Image,
    Italic,
    Link,
    List,
    ListOrdered,
    MoreHorizontal,
    Quote,
    Save,
    Share,
    Sparkles,
    Underline,
    X,
    LucideIcon
} from 'lucide-react'
import {
    getKnowledgeItem,
    KnowledgeItem,
    updateKnowledgeItem
} from '../../../services/api'
import { EditorTarget } from '../../../types/editor'
import { useI18n } from '../../../contexts/I18nContext'
import { copyToClipboard, showError, showInfo } from '../../Shared/Toast'
import './MainPanel.css'

interface MainPanelProps {
    onBackToChat?: () => void
    selectedTarget?: EditorTarget | null
    workspaceId?: string | null
}

type SaveState = 'idle' | 'saving' | 'saved' | 'error'

interface EditorTab {
    key: string
    target: EditorTarget
    knowledgeItem: KnowledgeItem | null
    title: string
    content: string
    baselineTitle: string
    baselineContent: string
    isLoading: boolean
    saveState: SaveState
    lastSavedAt: number | null
}

const AUTOSAVE_DELAY_MS = 800
const DEFAULT_IMAGE_PLACEHOLDER = 'https://example.com/image.png'

type ToolbarActionKey =
    | 'bold'
    | 'italic'
    | 'underline'
    | 'bullet_list'
    | 'numbered_list'
    | 'link'
    | 'image'
    | 'code'
    | 'quote'

type ToolbarItem =
    | {
        type: 'action'
        key: ToolbarActionKey
        icon: LucideIcon
        label: string
    }
    | {
        type: 'divider'
    }

const getTabKey = (target: EditorTarget) => {
    if (target.source === 'knowledge') {
        return `knowledge:${target.id}`
    }
    return `local:${target.path.toLowerCase()}`
}

const getBaseName = (inputPath: string) => {
    const parts = inputPath.split(/[\\/]+/).filter(Boolean)
    return parts[parts.length - 1] || inputPath
}

const getRendererErrorMessage = (error: unknown, fallback: string) => {
    if (error instanceof Error && error.message) {
        return error.message
            .replace(/^Error invoking remote method '[^']+':\s*/i, '')
            .replace(/^Error:\s*/i, '')
            .trim()
    }
    return fallback
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

const isTabDirty = (tab: EditorTab) => {
    if (tab.target.source === 'knowledge') {
        return tab.title !== tab.baselineTitle || tab.content !== tab.baselineContent
    }
    return tab.content !== tab.baselineContent
}

const buildInitialTab = (target: EditorTarget): EditorTab => {
    const initialTitle = target.source === 'local' ? target.name : 'Loading...'
    return {
        key: getTabKey(target),
        target,
        knowledgeItem: null,
        title: initialTitle,
        content: '',
        baselineTitle: initialTitle,
        baselineContent: '',
        isLoading: true,
        saveState: 'idle',
        lastSavedAt: null
    }
}

const wrapSelection = (
    input: string,
    start: number,
    end: number,
    prefix: string,
    suffix: string,
    placeholder: string
) => {
    const safeStart = Math.max(0, Math.min(start, input.length))
    const safeEnd = Math.max(safeStart, Math.min(end, input.length))
    const selected = input.slice(safeStart, safeEnd)
    const inserted = selected || placeholder
    const next = `${input.slice(0, safeStart)}${prefix}${inserted}${suffix}${input.slice(safeEnd)}`
    const selectionStart = safeStart + prefix.length
    const selectionEnd = selectionStart + inserted.length
    return { next, selectionStart, selectionEnd }
}

const prefixSelectionLines = (
    input: string,
    start: number,
    end: number,
    prefixFactory: (index: number) => string
) => {
    const safeStart = Math.max(0, Math.min(start, input.length))
    const safeEnd = Math.max(safeStart, Math.min(end, input.length))
    const lineStart = input.lastIndexOf('\n', safeStart - 1) + 1
    const lineEndIndex = input.indexOf('\n', safeEnd)
    const lineEnd = lineEndIndex === -1 ? input.length : lineEndIndex
    const selectedBlock = input.slice(lineStart, lineEnd)
    const lines = selectedBlock.split('\n')
    const prefixed = lines.map((line, index) => `${prefixFactory(index)}${line}`).join('\n')
    const next = `${input.slice(0, lineStart)}${prefixed}${input.slice(lineEnd)}`
    const selectionStart = lineStart
    const selectionEnd = lineStart + prefixed.length
    return { next, selectionStart, selectionEnd }
}

const MainPanel = ({ onBackToChat, selectedTarget, workspaceId }: MainPanelProps) => {
    const { isVietnamese, locale } = useI18n()
    const [tabs, setTabs] = useState<EditorTab[]>([])
    const [activeTabKey, setActiveTabKey] = useState<string | null>(null)
    const editorTextareaRef = useRef<HTMLTextAreaElement | null>(null)
    const copy = isVietnamese ? {
        loading: 'Đang tải...',
        untitled: 'Chưa đặt tên',
        filesystemUnavailable: 'API hệ thống tệp không khả dụng trong môi trường hiện tại',
        openFileFailed: 'Không thể mở tệp',
        saveDocumentFailed: 'Không thể lưu tài liệu',
        unsavedChangesConfirm: (title: string) => `Tab "${title || 'Chưa đặt tên'}" có thay đổi chưa lưu. Vẫn đóng?`,
        aiEditHint: 'Hãy dùng Chat với "AI Files On" để đọc/tạo/sửa/xóa tệp và thư mục tự động.',
        documentCopied: 'Đã sao chép tài liệu',
        noFileSelected: 'Chưa chọn tệp',
        saving: 'Đang lưu...',
        saveFailed: 'Lưu thất bại',
        unsavedChanges: 'Có thay đổi chưa lưu',
        saved: 'Đã lưu',
        notSavedYet: 'Chưa lưu',
        updatedAt: (time: string) => `Cập nhật lúc ${time}`,
        backToChat: 'Quay lại Chat',
        noDocument: 'Chưa có tài liệu',
        closeTab: 'Đóng tab',
        aiFileActions: 'AI thao tác tệp',
        documentEditor: 'Trình soạn thảo tài liệu',
        aiEdit: 'AI Edit',
        save: 'Lưu',
        share: 'Chia sẻ',
        loadingDocument: 'Đang tải tài liệu...',
        noDocumentSelected: 'Chưa chọn tài liệu',
        openFolderToEdit: 'Mở thư mục cục bộ từ panel bên phải để chỉnh sửa nhiều tệp.',
        selectWorkspaceOrFolder: 'Chọn workspace để dùng memory, hoặc mở thư mục cục bộ để sửa tệp.',
        startWriting: 'Bắt đầu viết...',
        words: (count: number) => `${count} từ`,
        tabs: (count: number) => `${count} tab`,
        toolbar: {
            bold: 'Đậm',
            italic: 'Nghiêng',
            underline: 'Gạch chân',
            bulletList: 'Danh sách chấm',
            numberedList: 'Danh sách số',
            link: 'Liên kết',
            image: 'Ảnh',
            code: 'Mã',
            quote: 'Trích dẫn',
        },
    } : {
        loading: 'Loading...',
        untitled: 'Untitled',
        filesystemUnavailable: 'Filesystem API is not available in current runtime',
        openFileFailed: 'Failed to open file',
        saveDocumentFailed: 'Failed to save document',
        unsavedChangesConfirm: (title: string) => `Tab "${title || 'Untitled'}" has unsaved changes. Close anyway?`,
        aiEditHint: 'Use Chat with "AI Files On" to read/create/edit/delete files and folders automatically.',
        documentCopied: 'Document copied to clipboard',
        noFileSelected: 'No file selected',
        saving: 'Saving...',
        saveFailed: 'Save failed',
        unsavedChanges: 'Unsaved changes',
        saved: 'Saved',
        notSavedYet: 'Not saved yet',
        updatedAt: (time: string) => `Updated ${time}`,
        backToChat: 'Back to Chat',
        noDocument: 'No document',
        closeTab: 'Close tab',
        aiFileActions: 'AI file actions',
        documentEditor: 'Document Editor',
        aiEdit: 'AI Edit',
        save: 'Save',
        share: 'Share',
        loadingDocument: 'Loading document...',
        noDocumentSelected: 'No document selected',
        openFolderToEdit: 'Open a local folder from the right panel to edit multiple files.',
        selectWorkspaceOrFolder: 'Select a workspace for memory, or open a local folder to edit files.',
        startWriting: 'Start writing...',
        words: (count: number) => `${count} words`,
        tabs: (count: number) => `${count} tab(s)`,
        toolbar: {
            bold: 'Bold',
            italic: 'Italic',
            underline: 'Underline',
            bulletList: 'Bullet List',
            numberedList: 'Numbered List',
            link: 'Link',
            image: 'Image',
            code: 'Code',
            quote: 'Quote',
        },
    }

    const tabsRef = useRef<EditorTab[]>([])
    useEffect(() => {
        tabsRef.current = tabs
    }, [tabs])

    const toolbarItems: ToolbarItem[] = [
        { type: 'action', key: 'bold', icon: Bold, label: copy.toolbar.bold },
        { type: 'action', key: 'italic', icon: Italic, label: copy.toolbar.italic },
        { type: 'action', key: 'underline', icon: Underline, label: copy.toolbar.underline },
        { type: 'divider' },
        { type: 'action', key: 'bullet_list', icon: List, label: copy.toolbar.bulletList },
        { type: 'action', key: 'numbered_list', icon: ListOrdered, label: copy.toolbar.numberedList },
        { type: 'divider' },
        { type: 'action', key: 'link', icon: Link, label: copy.toolbar.link },
        { type: 'action', key: 'image', icon: Image, label: copy.toolbar.image },
        { type: 'action', key: 'code', icon: Code, label: copy.toolbar.code },
        { type: 'action', key: 'quote', icon: Quote, label: copy.toolbar.quote }
    ]

    const activeTab = useMemo(
        () => tabs.find((tab) => tab.key === activeTabKey) || null,
        [tabs, activeTabKey]
    )

    const patchTab = useCallback((tabKey: string, updater: (tab: EditorTab) => EditorTab) => {
        setTabs((prev) => prev.map((tab) => (tab.key === tabKey ? updater(tab) : tab)))
    }, [])

    const loadTabData = useCallback(async (target: EditorTarget, tabKey: string) => {
        patchTab(tabKey, (tab) => ({ ...tab, isLoading: true, saveState: 'idle' }))
        try {
            if (target.source === 'knowledge') {
                const data = await getKnowledgeItem(target.id)
                const nextTitle = data.title || copy.untitled
                const nextContent = data.content ?? ''
                patchTab(tabKey, (tab) => ({
                    ...tab,
                    key: getTabKey(target),
                    target,
                    knowledgeItem: data,
                    title: nextTitle,
                    content: nextContent,
                    baselineTitle: nextTitle,
                    baselineContent: nextContent,
                    saveState: 'saved',
                    isLoading: false,
                    lastSavedAt: Date.now()
                }))
            } else {
                if (!window.electronAPI?.readFile) {
                    throw new Error(copy.filesystemUnavailable)
                }
                const result = await window.electronAPI.readFile(target.path)
                const nextTitle = target.name || getBaseName(target.path)
                patchTab(tabKey, (tab) => ({
                    ...tab,
                    key: getTabKey(target),
                    target: {
                        ...target,
                        name: nextTitle
                    },
                    knowledgeItem: null,
                    title: nextTitle,
                    content: result.content,
                    baselineTitle: nextTitle,
                    baselineContent: result.content,
                    saveState: 'saved',
                    isLoading: false,
                    lastSavedAt: result.mtimeMs
                }))
            }
        } catch (error) {
            console.error('Failed to load target:', error)
            patchTab(tabKey, (tab) => ({
                ...tab,
                isLoading: false,
                saveState: 'error'
            }))
            showError(getRendererErrorMessage(error, copy.openFileFailed))
        }
    }, [copy.filesystemUnavailable, copy.openFileFailed, copy.untitled, patchTab])

    const openTargetTab = useCallback(async (target: EditorTarget) => {
        const tabKey = getTabKey(target)
        const existing = tabsRef.current.find((tab) => tab.key === tabKey)

        if (existing) {
            patchTab(tabKey, (tab) => ({
                ...tab,
                target: target.source === 'local'
                    ? { ...target, name: target.name || tab.title }
                    : target
            }))
        } else {
            setTabs((prev) => [...prev, buildInitialTab(target)])
            await loadTabData(target, tabKey)
        }

        setActiveTabKey(tabKey)
    }, [loadTabData, patchTab])

    const saveTab = useCallback(async (tabKey: string) => {
        const tab = tabsRef.current.find((candidate) => candidate.key === tabKey)
        if (!tab || tab.isLoading || !isTabDirty(tab)) return

        patchTab(tabKey, (current) => ({ ...current, saveState: 'saving' }))

        try {
            if (tab.target.source === 'knowledge') {
                if (!tab.knowledgeItem) return
                const normalizedTitle = tab.title.trim() || copy.untitled
                const updated = await updateKnowledgeItem(tab.knowledgeItem.id, {
                    title: normalizedTitle,
                    content: tab.content
                })

                const nextTitle = updated.title || normalizedTitle
                const nextContent = updated.content ?? ''
                patchTab(tabKey, (current) => ({
                    ...current,
                    knowledgeItem: updated,
                    title: nextTitle,
                    content: nextContent,
                    baselineTitle: nextTitle,
                    baselineContent: nextContent,
                    saveState: 'saved',
                    lastSavedAt: Date.now()
                }))
            } else {
                if (!window.electronAPI?.writeFile) {
                    throw new Error(copy.filesystemUnavailable)
                }
                const result = await window.electronAPI.writeFile({
                    filePath: tab.target.path,
                    content: tab.content
                })
                patchTab(tabKey, (current) => ({
                    ...current,
                    baselineContent: current.content,
                    saveState: 'saved',
                    lastSavedAt: result.mtimeMs
                }))
                window.dispatchEvent(new CustomEvent('pigtex:local-fs-refresh'))
            }
        } catch (error) {
            console.error('Failed to save target:', error)
            patchTab(tabKey, (current) => ({ ...current, saveState: 'error' }))
            showError(getRendererErrorMessage(error, copy.saveDocumentFailed))
        }
    }, [copy.filesystemUnavailable, copy.saveDocumentFailed, copy.untitled, patchTab])

    const closeTab = useCallback((tabKey: string) => {
        const currentTabs = tabsRef.current
        const tabIndex = currentTabs.findIndex((tab) => tab.key === tabKey)
        if (tabIndex === -1) return

        const tab = currentTabs[tabIndex]
        if (isTabDirty(tab)) {
            const confirmed = window.confirm(copy.unsavedChangesConfirm(tab.title || copy.untitled))
            if (!confirmed) return
        }

        const nextTabs = currentTabs.filter((candidate) => candidate.key !== tabKey)
        let nextActiveKey = activeTabKey
        if (activeTabKey === tabKey) {
            nextActiveKey =
                nextTabs[tabIndex]?.key ||
                nextTabs[tabIndex - 1]?.key ||
                nextTabs[0]?.key ||
                null
        } else if (nextActiveKey && !nextTabs.some((candidate) => candidate.key === nextActiveKey)) {
            nextActiveKey = nextTabs[0]?.key || null
        }

        setTabs(nextTabs)
        setActiveTabKey(nextActiveKey)
    }, [activeTabKey, copy])

    useEffect(() => {
        if (!selectedTarget) return
        void openTargetTab(selectedTarget)
    }, [selectedTarget, openTargetTab])

    useEffect(() => {
        if (!activeTab) return
        if (activeTab.isLoading || !isTabDirty(activeTab)) return

        const timer = window.setTimeout(() => {
            void saveTab(activeTab.key)
        }, AUTOSAVE_DELAY_MS)

        return () => window.clearTimeout(timer)
    }, [
        activeTab?.key,
        activeTab?.title,
        activeTab?.content,
        activeTab?.baselineTitle,
        activeTab?.baselineContent,
        activeTab?.isLoading,
        saveTab
    ])

    const hasDirtyTabs = useMemo(() => tabs.some((tab) => isTabDirty(tab)), [tabs])
    useEffect(() => {
        const beforeUnload = (event: BeforeUnloadEvent) => {
            if (!hasDirtyTabs) return
            event.preventDefault()
            event.returnValue = ''
        }

        window.addEventListener('beforeunload', beforeUnload)
        return () => window.removeEventListener('beforeunload', beforeUnload)
    }, [hasDirtyTabs])

    // Ctrl+S global event → save active tab
    useEffect(() => {
        const handleGlobalSave = () => {
            const currentActive = tabsRef.current.find((tab) => tab.key === activeTabKey)
            if (!currentActive || currentActive.isLoading || !isTabDirty(currentActive)) return
            void saveTab(currentActive.key)
        }

        window.addEventListener('pigtex:editor-save', handleGlobalSave as EventListener)
        return () => window.removeEventListener('pigtex:editor-save', handleGlobalSave as EventListener)
    }, [activeTabKey, saveTab])

    useEffect(() => {
        const handleRenamed = (event: Event) => {
            const detail = (event as CustomEvent<{ oldPath: string; newPath: string }>).detail
            if (!detail?.oldPath || !detail?.newPath) return

            const currentTabs = tabsRef.current
            let activeCandidate = activeTabKey
            let changed = false

            const renamedTabs = currentTabs.map((tab) => {
                if (tab.target.source !== 'local') return tab
                if (!isSameOrChildPath(tab.target.path, detail.oldPath)) return tab

                changed = true
                const nextPath = replacePathPrefix(tab.target.path, detail.oldPath, detail.newPath)
                const nextRootPath = replacePathPrefix(tab.target.rootPath, detail.oldPath, detail.newPath)
                const nextName = getBaseName(nextPath)
                const nextTarget: EditorTarget = {
                    source: 'local',
                    path: nextPath,
                    rootPath: nextRootPath,
                    name: nextName
                }
                const nextKey = getTabKey(nextTarget)

                if (activeCandidate === tab.key) {
                    activeCandidate = nextKey
                }

                return {
                    ...tab,
                    key: nextKey,
                    target: nextTarget,
                    title: nextName,
                    baselineTitle: nextName
                }
            })

            if (!changed) return

            const dedupedTabs: EditorTab[] = []
            const seen = new Set<string>()
            for (const tab of renamedTabs) {
                if (seen.has(tab.key)) continue
                seen.add(tab.key)
                dedupedTabs.push(tab)
            }

            const safeActive =
                activeCandidate && dedupedTabs.some((tab) => tab.key === activeCandidate)
                    ? activeCandidate
                    : dedupedTabs[0]?.key || null

            setTabs(dedupedTabs)
            setActiveTabKey(safeActive)
        }

        const handleDeleted = (event: Event) => {
            const detail = (event as CustomEvent<{ targetPath: string }>).detail
            if (!detail?.targetPath) return

            const currentTabs = tabsRef.current
            const filteredTabs = currentTabs.filter((tab) => {
                if (tab.target.source !== 'local') return true
                return !isSameOrChildPath(tab.target.path, detail.targetPath)
            })

            if (filteredTabs.length === currentTabs.length) return

            const safeActive =
                activeTabKey && filteredTabs.some((tab) => tab.key === activeTabKey)
                    ? activeTabKey
                    : filteredTabs[0]?.key || null

            setTabs(filteredTabs)
            setActiveTabKey(safeActive)
        }

        const handleContentRestored = (event: Event) => {
            const detail = (event as CustomEvent<{ targetPath: string }>).detail
            if (!detail?.targetPath || !window.electronAPI?.readFile) return

            const tasks: Promise<void>[] = []
            for (const tab of tabsRef.current) {
                if (tab.target.source !== 'local' || tab.target.path !== detail.targetPath) {
                    continue
                }

                const localPath = tab.target.path
                tasks.push((async () => {
                    try {
                        const refreshed = await window.electronAPI!.readFile(localPath)
                        patchTab(tab.key, (current) => ({
                            ...current,
                            content: refreshed.content,
                            baselineContent: refreshed.content,
                            saveState: 'saved',
                            lastSavedAt: refreshed.mtimeMs
                        }))
                    } catch (error) {
                        console.error(`Failed to refresh restored content for ${localPath}:`, error)
                    }
                })())
            }

            if (tasks.length > 0) {
                void Promise.all(tasks)
            }
        }

        window.addEventListener('pigtex:fs-path-renamed', handleRenamed as EventListener)
        window.addEventListener('pigtex:fs-path-deleted', handleDeleted as EventListener)
        window.addEventListener('pigtex:fs-content-restored', handleContentRestored as EventListener)
        window.addEventListener('pigtex:file-content-updated', handleContentRestored as EventListener)

        return () => {
            window.removeEventListener('pigtex:fs-path-renamed', handleRenamed as EventListener)
            window.removeEventListener('pigtex:fs-path-deleted', handleDeleted as EventListener)
            window.removeEventListener('pigtex:fs-content-restored', handleContentRestored as EventListener)
            window.removeEventListener('pigtex:file-content-updated', handleContentRestored as EventListener)
        }
    }, [activeTabKey, patchTab])

    const handleManualSave = () => {
        if (!activeTab) return
        void saveTab(activeTab.key)
    }

    const handleAiEdit = () => {
        showInfo(copy.aiEditHint)
    }

    const applyToolbarAction = useCallback((updater: (content: string, start: number, end: number) => {
        next: string
        selectionStart: number
        selectionEnd: number
    }) => {
        if (!activeTabKey) return

        const currentTab = tabsRef.current.find((tab) => tab.key === activeTabKey)
        if (!currentTab || currentTab.isLoading) return

        const textarea = editorTextareaRef.current
        const fallbackPos = currentTab.content.length
        const start = textarea?.selectionStart ?? fallbackPos
        const end = textarea?.selectionEnd ?? fallbackPos
        const result = updater(currentTab.content, start, end)

        patchTab(activeTabKey, (tab) => ({ ...tab, content: result.next }))

        requestAnimationFrame(() => {
            const currentArea = editorTextareaRef.current
            if (!currentArea) return
            currentArea.focus()
            currentArea.setSelectionRange(result.selectionStart, result.selectionEnd)
        })
    }, [activeTabKey, patchTab])

    const handleToolbarAction = useCallback((action: ToolbarActionKey) => {
        switch (action) {
            case 'bold':
                applyToolbarAction((content, start, end) => wrapSelection(content, start, end, '**', '**', 'bold text'))
                return
            case 'italic':
                applyToolbarAction((content, start, end) => wrapSelection(content, start, end, '_', '_', 'italic text'))
                return
            case 'underline':
                applyToolbarAction((content, start, end) => wrapSelection(content, start, end, '<u>', '</u>', 'underlined text'))
                return
            case 'bullet_list':
                applyToolbarAction((content, start, end) => prefixSelectionLines(content, start, end, () => '- '))
                return
            case 'numbered_list':
                applyToolbarAction((content, start, end) => prefixSelectionLines(content, start, end, (index) => `${index + 1}. `))
                return
            case 'link':
                applyToolbarAction((content, start, end) => wrapSelection(content, start, end, '[', '](https://)', 'link text'))
                return
            case 'image':
                applyToolbarAction((content, start, end) => wrapSelection(content, start, end, '![alt text](', ')', DEFAULT_IMAGE_PLACEHOLDER))
                return
            case 'code':
                applyToolbarAction((content, start, end) => wrapSelection(content, start, end, '```\n', '\n```', 'code'))
                return
            case 'quote':
                applyToolbarAction((content, start, end) => prefixSelectionLines(content, start, end, () => '> '))
                return
            default:
                return
        }
    }, [applyToolbarAction])

    const handleShare = useCallback(async () => {
        if (!activeTab || activeTab.isLoading) return

        const sourceLabel = activeTab.target.source === 'local'
            ? `Local file: ${activeTab.target.path}`
            : `Knowledge: ${activeTab.title || 'Untitled'}`
        const payload = [
            sourceLabel,
            '',
            activeTab.content
        ].join('\n')

        await copyToClipboard(payload, copy.documentCopied)
    }, [activeTab, copy.documentCopied])

    const statusText = (() => {
        if (!activeTab) return copy.noFileSelected
        if (activeTab.saveState === 'saving') return copy.saving
        if (activeTab.saveState === 'error') return copy.saveFailed
        if (isTabDirty(activeTab)) return copy.unsavedChanges
        return copy.saved
    })()

    const updatedLabel = activeTab?.lastSavedAt
        ? copy.updatedAt(new Date(activeTab.lastSavedAt).toLocaleTimeString(locale, { hour: '2-digit', minute: '2-digit' }))
        : copy.notSavedYet

    const canEditTitle = activeTab?.target.source === 'knowledge'
    const isLocalTarget = activeTab?.target.source === 'local'
    const isDirty = activeTab ? isTabDirty(activeTab) : false
    const wordCount = useMemo(() => {
        const text = activeTab?.content?.trim() || ''
        if (!text) return 0
        return text.split(/\s+/).length
    }, [activeTab?.content])
    const showEmptyState = !activeTab

    return (
        <div className="main-panel">
            <div className="main-panel-tabs">
                {onBackToChat && (
                    <button className="back-button" onClick={onBackToChat}>
                        <ArrowLeft size={16} />
                        <span>{copy.backToChat}</span>
                    </button>
                )}
                <div className="tabs-list">
                    {tabs.length === 0 ? (
                        <div className="tab tab-placeholder active">
                            <span className="tab-icon">📄</span>
                            <span className="tab-title">{copy.noDocument}</span>
                        </div>
                    ) : (
                        tabs.map((tab) => {
                            const tabDirty = isTabDirty(tab)
                            const isActive = tab.key === activeTabKey
                            const tabIcon = tab.target.source === 'local' ? '🗂️' : '📝'
                            return (
                                <motion.div
                                    key={tab.key}
                                    className={`tab ${isActive ? 'active' : ''}`}
                                    initial={{ opacity: 0, y: -6 }}
                                    animate={{ opacity: 1, y: 0 }}
                                    onClick={() => setActiveTabKey(tab.key)}
                                >
                                    <span className="tab-icon">{tabIcon}</span>
                                    <span className="tab-title">{tab.title || 'Untitled'}</span>
                                    {tabDirty && <span className="tab-dirty-dot" />}
                                    <button
                                    className="tab-close"
                                    onClick={(event) => {
                                        event.stopPropagation()
                                        closeTab(tab.key)
                                    }}
                                        title={copy.closeTab}
                                    >
                                        <X size={12} />
                                    </button>
                                </motion.div>
                            )
                        })
                    )}
                </div>
                <button className="tab-new" onClick={handleAiEdit} title={copy.aiFileActions}>
                    <Sparkles size={14} />
                </button>
            </div>

            <div className="main-panel-header">
                <div className="document-title-wrapper">
                    <span className="document-emoji">{isLocalTarget ? '🗂️' : '📝'}</span>
                    {activeTab ? (
                        <input
                            className="document-title-input"
                            value={activeTab.title}
                            onChange={(event) => {
                                const value = event.target.value
                                if (!activeTabKey) return
                                patchTab(activeTabKey, (tab) => ({ ...tab, title: value }))
                            }}
                            placeholder={copy.untitled}
                            readOnly={!canEditTitle}
                        />
                    ) : (
                        <h1 className="document-title">{copy.documentEditor}</h1>
                    )}
                </div>
                <div className="document-meta">
                    <span className="document-status">
                        <Check size={14} /> {statusText}
                    </span>
                    <span className="document-updated">{updatedLabel}</span>
                </div>
                <div className="document-actions">
                    <motion.button
                        className="action-button action-button-ai"
                        whileHover={{ scale: 1.02 }}
                        whileTap={{ scale: 0.98 }}
                        onClick={handleAiEdit}
                    >
                        <Sparkles size={14} />
                        <span>{copy.aiEdit}</span>
                    </motion.button>
                    <button
                        className="action-button"
                        onClick={handleManualSave}
                        disabled={!activeTab || !isDirty || activeTab.isLoading}
                    >
                        <Save size={14} />
                        <span>{copy.save}</span>
                    </button>
                    <button
                        className="action-button"
                        onClick={() => void handleShare()}
                        disabled={!activeTab || activeTab.isLoading}
                    >
                        <Share size={14} />
                        <span>{copy.share}</span>
                    </button>
                    <button className="action-button-icon" disabled>
                        <MoreHorizontal size={16} />
                    </button>
                </div>
            </div>

            <motion.div
                className="floating-toolbar"
                initial={{ opacity: 0, y: 10 }}
                animate={{ opacity: 1, y: 0 }}
                transition={{ delay: 0.2 }}
            >
                {toolbarItems.map((itemConfig, index) =>
                    itemConfig.type === 'divider' ? (
                        <div key={index} className="toolbar-divider" />
                    ) : (
                        <motion.button
                            key={index}
                            className="toolbar-button"
                            whileHover={{ scale: 1.1 }}
                            whileTap={{ scale: 0.95 }}
                            title={itemConfig.label}
                            onClick={() => handleToolbarAction(itemConfig.key)}
                            disabled={!activeTab || activeTab.isLoading}
                        >
                            <itemConfig.icon size={14} />
                        </motion.button>
                    )
                )}
            </motion.div>

            <div className="main-panel-content">
                <div className="document-editor">
                    {activeTab?.isLoading ? (
                        <div className="editor-placeholder">
                            <p className="editor-placeholder-title">{copy.loadingDocument}</p>
                        </div>
                    ) : showEmptyState ? (
                        <div className="editor-placeholder">
                            <p className="editor-placeholder-title">{copy.noDocumentSelected}</p>
                            <p className="editor-placeholder-description">
                                {workspaceId
                                    ? copy.openFolderToEdit
                                    : copy.selectWorkspaceOrFolder}
                            </p>
                        </div>
                    ) : (
                        <textarea
                            ref={editorTextareaRef}
                            className="document-textarea"
                            value={activeTab.content}
                            onChange={(event) => {
                                if (!activeTabKey) return
                                patchTab(activeTabKey, (tab) => ({ ...tab, content: event.target.value }))
                            }}
                            placeholder={copy.startWriting}
                            spellCheck={false}
                        />
                    )}
                </div>
            </div>

            <div className="main-panel-statusbar">
                <div className="statusbar-left">
                    <span className="statusbar-item">
                        <span className={`statusbar-dot ${activeTab?.saveState === 'error' ? 'statusbar-dot-error' : 'statusbar-dot-online'}`} />
                        {statusText}
                    </span>
                    <span className="statusbar-item">{copy.words(wordCount)}</span>
                    <span className="statusbar-item">{copy.tabs(tabs.length)}</span>
                </div>
                <div className="statusbar-right">
                    <button className="statusbar-item statusbar-button">
                        100% <ChevronDown size={12} />
                    </button>
                </div>
            </div>
        </div>
    )
}

export default MainPanel
