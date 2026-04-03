import { useState, useEffect, useCallback, forwardRef, useImperativeHandle, type SyntheticEvent } from 'react'
import { motion, AnimatePresence } from 'framer-motion'
import {
    ChevronLeft,
    ChevronRight,
    Search,
    Rocket,
    Lightbulb,
    Sparkles,
    BookOpen,
    Plus,
    FolderOpen,
    FolderPlus,
    MessageSquare,
    Settings,
    ExternalLink,
    Pencil,
    Trash2,
    X,
    Shield,
    LucideIcon
} from 'lucide-react'
import {
    getWorkspaces,
    createWorkspace,
    updateWorkspace,
    deleteWorkspace as deleteWorkspaceApi,
    getConversations,
    getLearningPrograms,
    getLearningReviews,
    deleteConversation,
    LearningProgramSummary,
    LearningReviewItem,
    Workspace,
    Conversation
} from '../../../services/api'
import { useAuth } from '../../../contexts/AuthContext'
import { useI18n } from '../../../contexts/I18nContext'
import type { DesktopUpdateState } from '../../../services/desktopUpdate'
import type { SidebarHandle } from '../Dashboard'
import './Sidebar.css'
import pigtexLogoUrl from '../../../../assets/pigtex_logo.png'

interface SidebarProps {
    collapsed: boolean
    onToggle: () => void
    onOpenFile?: (fileId: string) => void
    selectedWorkspaceId: string | null
    onWorkspaceSelect: (workspaceId: string | null) => void
    selectedConversationId: string | null
    onConversationSelect: (conversationId: string | null) => void
    onNewChat: (workspaceId: string | null) => void
    selectedLearningProgramId?: string | null
    onLearningProgramSelect?: (program: { id: string; title: string; workspaceId: string | null }) => void
    isAdmin?: boolean
    onOpenAdmin?: () => void
    onOpenSettings?: () => void
    desktopUpdate?: DesktopUpdateState
    isInstallingDesktopUpdate?: boolean
    onInstallDesktopUpdate?: () => void
}

const iconMap: Record<string, LucideIcon> = {
    '🚀': Rocket,
    '💡': Lightbulb,
    '✨': Sparkles,
    '📚': BookOpen,
    '📁': FolderOpen
}

const workspaceColors = [
    '#6366f1', '#8b5cf6', '#ec4899', '#f43f5e',
    '#f97316', '#eab308', '#22c55e', '#14b8a6', '#06b6d4'
]

const workspaceIcons = ['📁', '🚀', '💡', '✨', '📚', '🎯', '🔬', '📊', '🎨']

const toErrorMessage = (error: unknown): string => {
    if (error instanceof Error && error.message.trim()) return error.message
    if (typeof error === 'string' && error.trim()) return error
    return 'Unknown error'
}

const Sidebar = forwardRef<SidebarHandle, SidebarProps>(({
    collapsed,
    onToggle,
    selectedWorkspaceId,
    onWorkspaceSelect,
    selectedConversationId,
    onConversationSelect,
    onNewChat,
    selectedLearningProgramId = null,
    onLearningProgramSelect,
    isAdmin = false,
    onOpenAdmin,
    onOpenSettings,
    desktopUpdate,
    isInstallingDesktopUpdate = false,
    onInstallDesktopUpdate
}, ref) => {
    const { isVietnamese } = useI18n()
    const { user } = useAuth()
    const copy = isVietnamese ? {
        user: 'Người dùng',
        avatar: 'ảnh đại diện',
        newStandaloneChat: 'Chat độc lập mới',
        settings: 'Cài đặt',
        admin: 'Admin',
        collapseSidebar: 'Thu gọn thanh bên',
        search: 'Tìm kiếm...',
        chats: 'Chat',
        learn: 'Học',
        reviews: 'Ôn lại',
        reviewDueBadge: (count: number) => `${count} đến hạn`,
        noLearningPrograms: 'Chưa có lộ trình học',
        noReviewsDue: 'Chưa có mục ôn lại',
        continueProgram: 'Tiếp tục lộ trình',
        reviewNode: 'Ôn mục này',
        reviewDueAt: 'Đến hạn ôn',
        nextNode: 'Mục tiếp theo',
        workspaceScope: 'Workspace',
        standaloneScope: 'Độc lập',
        progressCompact: (done: number, total: number) => `${done}/${total} mục`,
        noWorkspace: 'Không workspace',
        newLabel: 'Mới',
        createStandaloneChat: 'Tạo chat độc lập',
        noStandaloneChats: 'Chưa có chat độc lập',
        newConversation: 'Hội thoại mới',
        workspace: 'Workspace',
        newWorkspace: 'Workspace mới',
        loading: 'Đang tải...',
        renameWorkspace: 'Đổi tên workspace',
        deleteWorkspace: 'Xóa workspace',
        newChat: 'Chat mới',
        noChatsYet: 'Chưa có chat',
        createWorkspace: 'Tạo workspace',
        name: 'Tên',
        myWorkspace: 'Workspace của tôi',
        icon: 'Biểu tượng',
        color: 'Màu',
        cancel: 'Hủy',
        create: 'Tạo',
        save: 'Lưu',
        workspaceName: 'Tên workspace',
        renameFailed: 'Đổi tên workspace thất bại',
        deleteFailed: 'Xóa workspace thất bại',
        deleteWorkspaceConfirm: (name: string) => `Xóa workspace "${name}"?\nMọi chat trong workspace này sẽ được chuyển về phạm vi độc lập.`,
    } : {
        user: 'User',
        avatar: 'avatar',
        newStandaloneChat: 'New Standalone Chat',
        settings: 'Settings',
        admin: 'Admin',
        collapseSidebar: 'Collapse sidebar',
        search: 'Search...',
        chats: 'Chats',
        learn: 'Learn',
        reviews: 'Reviews',
        reviewDueBadge: (count: number) => `${count} due`,
        noLearningPrograms: 'No learning programs yet',
        noReviewsDue: 'No reviews due',
        continueProgram: 'Continue program',
        reviewNode: 'Review this node',
        reviewDueAt: 'Review due',
        nextNode: 'Next node',
        workspaceScope: 'Workspace',
        standaloneScope: 'Standalone',
        progressCompact: (done: number, total: number) => `${done}/${total} nodes`,
        noWorkspace: 'No workspace',
        newLabel: 'New',
        createStandaloneChat: 'Create standalone chat',
        noStandaloneChats: 'No standalone chats yet',
        newConversation: 'New Conversation',
        workspace: 'Workspace',
        newWorkspace: 'New Workspace',
        loading: 'Loading...',
        renameWorkspace: 'Rename workspace',
        deleteWorkspace: 'Delete workspace',
        newChat: 'New chat',
        noChatsYet: 'No chats yet',
        createWorkspace: 'Create Workspace',
        name: 'Name',
        myWorkspace: 'My Workspace',
        icon: 'Icon',
        color: 'Color',
        cancel: 'Cancel',
        create: 'Create',
        save: 'Save',
        workspaceName: 'Workspace name',
        renameFailed: 'Rename workspace failed',
        deleteFailed: 'Delete workspace failed',
        deleteWorkspaceConfirm: (name: string) => `Delete workspace "${name}"?\nItems and chats in this workspace will be moved to standalone scope.`,
    }
    const updateCopy = isVietnamese ? {
        updateReady: (version?: string | null) => version ? `Cập nhật ${version}` : 'Có bản cập nhật',
        installNow: 'Cập nhật ngay',
        installing: 'Đang mở web...',
    } : {
        updateReady: (version?: string | null) => version ? `Update ${version}` : 'Update available',
        installNow: 'Update now',
        installing: 'Opening web...',
    }
    const userName = user?.username || user?.email?.split('@')[0] || copy.user
    const userEmail = user?.email || 'user@pigtex.io'
    const oauthProvider = (user?.oauth_provider || '').trim().toLowerCase()
    const oauthAvatarUrl = (user?.avatar_url || '').trim()
    const useProviderAvatar =
        (oauthProvider === 'google' || oauthProvider === 'github') && oauthAvatarUrl.length > 0
    const userAvatarUrl = useProviderAvatar ? oauthAvatarUrl : pigtexLogoUrl
    const userAvatarAlt = useProviderAvatar ? `${userName} ${copy.avatar}` : 'PigTex logo'
    const hasDesktopUpdate = Boolean(desktopUpdate?.updateAvailable)
    const desktopUpdateLabel = updateCopy.updateReady(desktopUpdate?.latestVersion)
    const desktopUpdateActionLabel = isInstallingDesktopUpdate ? updateCopy.installing : updateCopy.installNow

    const handleUserAvatarError = (event: SyntheticEvent<HTMLImageElement>) => {
        const target = event.currentTarget
        if (target.dataset.fallbackApplied === 'true') return
        target.dataset.fallbackApplied = 'true'
        target.src = pigtexLogoUrl
        target.classList.add('is-default-avatar')
    }

    const [workspaces, setWorkspaces] = useState<Workspace[]>([])
    const [conversations, setConversations] = useState<Record<string, Conversation[]>>({})
    const [standaloneChats, setStandaloneChats] = useState<Conversation[]>([])
    const [learningPrograms, setLearningPrograms] = useState<LearningProgramSummary[]>([])
    const [learningReviews, setLearningReviews] = useState<LearningReviewItem[]>([])
    const [expandedWorkspaces, setExpandedWorkspaces] = useState<Set<string>>(new Set())
    const [isLoading, setIsLoading] = useState(true)
    const [isLearningLoading, setIsLearningLoading] = useState(true)

    // Create workspace modal state
    const [showCreateModal, setShowCreateModal] = useState(false)
    const [newWorkspaceName, setNewWorkspaceName] = useState('')
    const [newWorkspaceIcon, setNewWorkspaceIcon] = useState('📁')
    const [newWorkspaceColor, setNewWorkspaceColor] = useState('#6366f1')
    const [showRenameModal, setShowRenameModal] = useState(false)
    const [renameWorkspaceId, setRenameWorkspaceId] = useState<string | null>(null)
    const [renameWorkspaceName, setRenameWorkspaceName] = useState('')

    // Expose imperative methods to parent via ref
    useImperativeHandle(ref, () => ({
        openCreateWorkspace: () => setShowCreateModal(true),
        createNewChat: () => handleCreateChat(null),
    }))

    const fetchWorkspaces = useCallback(async () => {
        try {
            const data = await getWorkspaces()
            setWorkspaces(data)
        } catch (error) {
            console.error('Failed to fetch workspaces:', error)
        } finally {
            setIsLoading(false)
        }
    }, [])

    const fetchStandaloneChats = useCallback(async () => {
        try {
            const data = await getConversations(null)
            setStandaloneChats(data)
        } catch (error) {
            console.error('Failed to fetch standalone chats:', error)
        }
    }, [])

    const fetchLearningPrograms = useCallback(async () => {
        try {
            const data = await getLearningPrograms()
            setLearningPrograms(data)
        } catch (error) {
            console.error('Failed to fetch learning programs:', error)
        } finally {
            setIsLearningLoading(false)
        }
    }, [])

    const fetchLearningReviews = useCallback(async () => {
        try {
            const data = await getLearningReviews()
            setLearningReviews(data)
        } catch (error) {
            console.error('Failed to fetch learning reviews:', error)
        }
    }, [])

    const fetchWorkspaceConversations = useCallback(async (workspaceId: string) => {
        try {
            const data = await getConversations(workspaceId)
            setConversations(prev => ({ ...prev, [workspaceId]: data }))
        } catch (error) {
            console.error('Failed to fetch conversations:', error)
        }
    }, [])

    useEffect(() => {
        setWorkspaces([])
        setConversations({})
        setStandaloneChats([])
        setLearningPrograms([])
        setLearningReviews([])
        setExpandedWorkspaces(new Set())
        setIsLoading(true)
        setIsLearningLoading(true)

        void fetchWorkspaces()
        void fetchStandaloneChats()
        void fetchLearningPrograms()
        void fetchLearningReviews()
    }, [fetchLearningPrograms, fetchLearningReviews, fetchStandaloneChats, fetchWorkspaces, user?.id])

    useEffect(() => {
        const handleConversationUpdated = (event: Event) => {
            const detail = (event as CustomEvent<{ workspaceId?: string | null }>).detail
            const workspaceScope = detail?.workspaceId

            void fetchLearningPrograms()
            void fetchLearningReviews()

            if (workspaceScope === null || workspaceScope === '') {
                void fetchStandaloneChats()
                return
            }

            if (typeof workspaceScope === 'string' && workspaceScope.trim()) {
                void fetchWorkspaceConversations(workspaceScope)
                return
            }

            void fetchStandaloneChats()
            expandedWorkspaces.forEach((workspaceId) => {
                void fetchWorkspaceConversations(workspaceId)
            })
        }

        const handleCloudRestoreApplied = () => {
            void fetchWorkspaces()
            void fetchStandaloneChats()
            void fetchLearningPrograms()
            void fetchLearningReviews()
            expandedWorkspaces.forEach((workspaceId) => {
                void fetchWorkspaceConversations(workspaceId)
            })
        }

        window.addEventListener('pigtex:conversation-updated', handleConversationUpdated as EventListener)
        window.addEventListener('pigtex:cloud-restore-applied', handleCloudRestoreApplied)
        return () => {
            window.removeEventListener('pigtex:conversation-updated', handleConversationUpdated as EventListener)
            window.removeEventListener('pigtex:cloud-restore-applied', handleCloudRestoreApplied)
        }
    }, [expandedWorkspaces, fetchLearningPrograms, fetchLearningReviews, fetchStandaloneChats, fetchWorkspaceConversations, fetchWorkspaces])

    useEffect(() => {
        if (!selectedWorkspaceId) return

        setExpandedWorkspaces(prev => {
            if (prev.has(selectedWorkspaceId)) return prev
            const next = new Set(prev)
            next.add(selectedWorkspaceId)
            return next
        })

        if (!conversations[selectedWorkspaceId]) {
            void fetchWorkspaceConversations(selectedWorkspaceId)
        }
    }, [conversations, fetchWorkspaceConversations, selectedWorkspaceId])

    useEffect(() => {
        if (!selectedWorkspaceId || isLoading) return
        if (workspaces.some(workspace => workspace.id === selectedWorkspaceId)) return
        onWorkspaceSelect(null)
    }, [isLoading, onWorkspaceSelect, selectedWorkspaceId, workspaces])

    const handleCreateWorkspace = async () => {
        if (!newWorkspaceName.trim()) return
        try {
            const workspace = await createWorkspace(
                newWorkspaceName.trim(),
                newWorkspaceIcon,
                newWorkspaceColor
            )
            setWorkspaces(prev => [...prev, workspace])
            setShowCreateModal(false)
            setNewWorkspaceName('')
            setNewWorkspaceIcon('📁')
            setNewWorkspaceColor('#6366f1')
        } catch (error) {
            console.error('Failed to create workspace:', error)
        }
    }

    const handleCreateChat = (workspaceId: string | null) => {
        onWorkspaceSelect(workspaceId)
        onConversationSelect(null)
        onNewChat(workspaceId)

        if (workspaceId) {
            setExpandedWorkspaces(prev => new Set(prev).add(workspaceId))
            void fetchWorkspaceConversations(workspaceId)
        } else {
            void fetchStandaloneChats()
        }
    }

    const handleOpenLearningProgram = useCallback((program: LearningProgramSummary) => {
        onWorkspaceSelect(program.workspace_id ?? null)
        onConversationSelect(null)
        onLearningProgramSelect?.({
            id: program.id,
            title: program.title,
            workspaceId: program.workspace_id ?? null
        })
    }, [onConversationSelect, onLearningProgramSelect, onWorkspaceSelect])

    const handleOpenLearningReview = useCallback((review: LearningReviewItem) => {
        const matchedProgram = learningPrograms.find(program => program.id === review.program_id)
        onWorkspaceSelect(matchedProgram?.workspace_id ?? null)
        onConversationSelect(null)
        onLearningProgramSelect?.({
            id: review.program_id,
            title: review.program_title,
            workspaceId: matchedProgram?.workspace_id ?? null
        })
    }, [learningPrograms, onConversationSelect, onLearningProgramSelect, onWorkspaceSelect])

    const closeRenameModal = () => {
        setShowRenameModal(false)
        setRenameWorkspaceId(null)
        setRenameWorkspaceName('')
    }

    const openRenameWorkspaceModal = (workspace: Workspace) => {
        setRenameWorkspaceId(workspace.id)
        setRenameWorkspaceName(workspace.name)
        setShowRenameModal(true)
    }

    const handleRenameWorkspace = async () => {
        const nextName = renameWorkspaceName.trim()
        if (!renameWorkspaceId || !nextName) return

        try {
            const updated = await updateWorkspace(renameWorkspaceId, { name: nextName })
            setWorkspaces(prev =>
                prev.map(workspace => workspace.id === updated.id ? updated : workspace)
            )
            closeRenameModal()
        } catch (error) {
            console.error('Failed to rename workspace:', error)
            window.alert(`${copy.renameFailed}: ${toErrorMessage(error)}`)
        }
    }

    const handleDeleteWorkspace = async (workspace: Workspace, workspaceChats: Conversation[]) => {
        const confirmed = window.confirm(copy.deleteWorkspaceConfirm(workspace.name))
        if (!confirmed) return

        try {
            await deleteWorkspaceApi(workspace.id)
            setWorkspaces(prev => prev.filter(item => item.id !== workspace.id))
            setConversations(prev => {
                const next = { ...prev }
                delete next[workspace.id]
                return next
            })
            setExpandedWorkspaces(prev => {
                const next = new Set(prev)
                next.delete(workspace.id)
                return next
            })
            if (selectedWorkspaceId === workspace.id) {
                onWorkspaceSelect(null)
            }
            if (
                selectedConversationId &&
                workspaceChats.some(chat => chat.id === selectedConversationId)
            ) {
                onConversationSelect(null)
            }
            void fetchStandaloneChats()
        } catch (error) {
            console.error('Failed to delete workspace:', error)
            window.alert(`${copy.deleteFailed}: ${toErrorMessage(error)}`)
        }
    }

    const handleDeleteChat = async (e: React.MouseEvent, convId: string, workspaceId: string | null) => {
        e.stopPropagation()
        try {
            await deleteConversation(convId)
            if (workspaceId) {
                setConversations(prev => ({
                    ...prev,
                    [workspaceId]: prev[workspaceId]?.filter(c => c.id !== convId) || []
                }))
            } else {
                setStandaloneChats(prev => prev.filter(c => c.id !== convId))
            }
            if (selectedConversationId === convId) {
                onConversationSelect(null)
            }
        } catch (error) {
            console.error('Failed to delete chat:', error)
        }
    }

    const toggleWorkspaceExpand = (workspaceId: string) => {
        setExpandedWorkspaces(prev => {
            const next = new Set(prev)
            if (next.has(workspaceId)) {
                next.delete(workspaceId)
            } else {
                next.add(workspaceId)
                if (!conversations[workspaceId]) {
                    fetchWorkspaceConversations(workspaceId)
                }
            }
            return next
        })
    }

    const getIconComponent = (icon: string) => {
        return iconMap[icon] || FolderOpen
    }

    /* ===== Collapsed View ===== */
    if (collapsed) {
        return (
            <div className="sidebar sidebar-collapsed">
                {/* New Chat */}
                <button
                    className="sidebar-action-mini"
                    onClick={() => handleCreateChat(null)}
                    title={copy.newStandaloneChat}
                >
                    <Plus size={16} />
                </button>

                {/* Expand */}
                <button className="sidebar-toggle collapsed-toggle" onClick={onToggle}>
                    <ChevronRight size={18} />
                </button>

                {/* Spacer */}
                <div style={{ flex: 1 }} />

                {/* Footer icons */}
                <div className="sidebar-footer-collapsed">
                    {isAdmin && (
                        <button
                            className="sidebar-action-mini"
                            onClick={() => onOpenAdmin?.()}
                            title={copy.admin}
                        >
                            <Shield size={16} />
                        </button>
                    )}
                    {hasDesktopUpdate && (
                        <button
                            className="sidebar-action-mini sidebar-action-mini--update"
                            onClick={() => onInstallDesktopUpdate?.()}
                            title={desktopUpdateActionLabel}
                            disabled={isInstallingDesktopUpdate}
                        >
                            <ExternalLink size={16} />
                            <span className="sidebar-update-dot" />
                        </button>
                    )}
                    <button
                        className="sidebar-action-mini"
                        onClick={() => onOpenSettings?.()}
                        title={copy.settings}
                    >
                        <Settings size={16} />
                    </button>
                    <div className="user-avatar">
                        <img
                            src={userAvatarUrl}
                            alt={userAvatarAlt}
                            className={`user-avatar-image ${useProviderAvatar ? '' : 'is-default-avatar'}`.trim()}
                            onError={handleUserAvatarError}
                            referrerPolicy={useProviderAvatar ? 'no-referrer' : undefined}
                        />
                    </div>
                </div>
            </div>
        )
    }

    /* ===== Expanded View ===== */
    return (
        <div className="sidebar">
            {/* Header — Collapse */}
            <div className="sidebar-header">
                <button className="sidebar-toggle" onClick={onToggle} title={copy.collapseSidebar}>
                    <ChevronLeft size={16} />
                </button>
            </div>

            {/* Search */}
            <div className="sidebar-search">
                <Search size={16} className="sidebar-search-icon" />
                <input
                    type="text"
                    placeholder={copy.search}
                    className="sidebar-search-input"
                />
            </div>

            {/* Scrollable Content */}
            <div className="sidebar-content">
                {/* Standalone Chats */}
                <div className="sidebar-section sidebar-split-pane">
                    <div className="sidebar-section-header">
                        <span className="sidebar-section-title">{copy.chats}</span>
                        <div className="sidebar-section-header-actions">
                            <span className="sidebar-section-badge">{copy.noWorkspace}</span>
                            <button
                                className="sidebar-section-create-btn"
                                onClick={() => handleCreateChat(null)}
                                title={copy.createStandaloneChat}
                            >
                                <Plus size={12} />
                                <span>{copy.newLabel}</span>
                            </button>
                        </div>
                    </div>
                    <div className="sidebar-items">
                        {standaloneChats.length === 0 ? (
                            <div className="sidebar-empty-hint">{copy.noStandaloneChats}</div>
                        ) : (
                            standaloneChats.map((chat) => (
                                <button
                                    key={chat.id}
                                    className={`chat-item ${chat.id === selectedConversationId ? 'active' : ''}`}
                                    onClick={() => {
                                        onWorkspaceSelect(null)
                                        onConversationSelect(chat.id)
                                    }}
                                >
                                    <MessageSquare size={14} />
                                    <span className="chat-title">{chat.title || copy.newConversation}</span>
                                    <button
                                        className="chat-delete"
                                        onClick={(e) => handleDeleteChat(e, chat.id, null)}
                                    >
                                        <Trash2 size={12} />
                                    </button>
                                </button>
                            ))
                        )}
                    </div>
                </div>

                <div className="sidebar-section sidebar-learn-section">
                    <div className="sidebar-section-header">
                        <span className="sidebar-section-title">{copy.learn}</span>
                        {learningReviews.length > 0 && (
                            <span className="sidebar-section-badge sidebar-section-badge-accent">
                                {copy.reviewDueBadge(learningReviews.length)}
                            </span>
                        )}
                    </div>
                    <div className="sidebar-items learning-sidebar-items sidebar-learn-items">
                        {isLearningLoading ? (
                            <div className="sidebar-loading">{copy.loading}</div>
                        ) : (
                            <>
                                {learningReviews.length > 0 ? (
                                    <div className="learning-review-list">
                                        {learningReviews.slice(0, 3).map((review) => (
                                            <button
                                                key={`${review.program_id}-${review.node?.id ?? 'review'}`}
                                                className={`chat-item learning-sidebar-item ${review.program_id === selectedLearningProgramId ? 'active' : ''}`}
                                                onClick={() => handleOpenLearningReview(review)}
                                                title={copy.reviewNode}
                                            >
                                                <BookOpen size={14} className="learning-sidebar-item-icon" />
                                                <span className="chat-title">
                                                    {review.node?.title || review.program_title}
                                                </span>
                                            </button>
                                        ))}
                                    </div>
                                ) : null}

                                {learningPrograms.length === 0 ? (
                                    <div className="sidebar-empty-hint">{copy.noLearningPrograms}</div>
                                ) : (
                                    <div className="learning-program-list">
                                        {learningPrograms.slice(0, 4).map((program) => (
                                            <button
                                                key={program.id}
                                                className={`chat-item learning-sidebar-item ${program.id === selectedLearningProgramId ? 'active' : ''}`}
                                                onClick={() => handleOpenLearningProgram(program)}
                                                title={copy.continueProgram}
                                            >
                                                <BookOpen size={14} className="learning-sidebar-item-icon" />
                                                <span className="chat-title">{program.title}</span>
                                            </button>
                                        ))}
                                    </div>
                                )}
                            </>
                        )}
                    </div>
                </div>

                {/* Workspaces */}
                <div className="sidebar-section sidebar-split-pane">
                    <div className="sidebar-section-header">
                        <span className="sidebar-section-title">{copy.workspace}</span>
                        <div className="sidebar-section-header-actions">
                            <button
                                className="sidebar-section-action"
                                onClick={() => setShowCreateModal(true)}
                                title={copy.newWorkspace}
                            >
                                <FolderPlus size={14} />
                            </button>
                        </div>
                    </div>
                    <div className="sidebar-items">
                        {isLoading ? (
                            <div className="sidebar-loading">{copy.loading}</div>
                        ) : (
                            workspaces.map((workspace) => {
                                const IconComponent = getIconComponent(workspace.icon)
                                const isActive = workspace.id === selectedWorkspaceId
                                const isExpanded = expandedWorkspaces.has(workspace.id)
                                const workspaceChats = conversations[workspace.id] || []

                                return (
                                    <div key={workspace.id} className="workspace-group">
                                        <div className={`workspace-item ${isActive ? 'active' : ''}`}>
                                            <button
                                                className="workspace-toggle"
                                                onClick={() => {
                                                    toggleWorkspaceExpand(workspace.id)
                                                }}
                                            >
                                                <ChevronRight
                                                    size={12}
                                                    className={`toggle-icon ${isExpanded ? 'expanded' : ''}`}
                                                />
                                            </button>
                                            <button
                                                className="workspace-info"
                                                onClick={() => {
                                                    onWorkspaceSelect(workspace.id)
                                                    if (!isExpanded) toggleWorkspaceExpand(workspace.id)
                                                }}
                                            >
                                                <div className="workspace-icon" style={{ color: workspace.color }}>
                                                    <IconComponent size={16} />
                                                </div>
                                                <div className="workspace-details">
                                                    <span className="workspace-name">{workspace.name}</span>
                                                </div>
                                            </button>
                                            <div className="workspace-actions">
                                                <button
                                                    className="workspace-action-btn workspace-edit"
                                                    onClick={(e) => {
                                                        e.stopPropagation()
                                                        openRenameWorkspaceModal(workspace)
                                                    }}
                                                    title={copy.renameWorkspace}
                                                >
                                                    <Pencil size={11} />
                                                </button>
                                                <button
                                                    className="workspace-action-btn workspace-delete"
                                                    onClick={(e) => {
                                                        e.stopPropagation()
                                                        void handleDeleteWorkspace(workspace, workspaceChats)
                                                    }}
                                                    title={copy.deleteWorkspace}
                                                >
                                                    <Trash2 size={11} />
                                                </button>
                                            </div>
                                            <button
                                                className="workspace-add"
                                                onClick={(e) => {
                                                    e.stopPropagation()
                                                    handleCreateChat(workspace.id)
                                                }}
                                                title={copy.newChat}
                                            >
                                                <Plus size={12} />
                                            </button>
                                        </div>

                                        <AnimatePresence>
                                            {isExpanded && (
                                                <motion.div
                                                    className="workspace-chats"
                                                    initial={{ height: 0, opacity: 0 }}
                                                    animate={{ height: 'auto', opacity: 1 }}
                                                    exit={{ height: 0, opacity: 0 }}
                                                    transition={{ duration: 0.15 }}
                                                >
                                                    {workspaceChats.length === 0 ? (
                                                        <div className="no-chats">{copy.noChatsYet}</div>
                                                    ) : (
                                                        workspaceChats.map((chat) => (
                                                            <button
                                                                key={chat.id}
                                                                className={`nested-chat ${chat.id === selectedConversationId ? 'active' : ''}`}
                                                                onClick={() => {
                                                                    onWorkspaceSelect(workspace.id)
                                                                    onConversationSelect(chat.id)
                                                                }}
                                                            >
                                                                <MessageSquare size={12} />
                                                                <span>{chat.title || copy.newConversation}</span>
                                                                <button
                                                                    className="chat-delete"
                                                                    onClick={(e) => handleDeleteChat(e, chat.id, workspace.id)}
                                                                >
                                                                    <Trash2 size={10} />
                                                                </button>
                                                            </button>
                                                        ))
                                                    )}
                                                </motion.div>
                                            )}
                                        </AnimatePresence>
                                    </div>
                                )
                            })
                        )}
                    </div>
                </div>
            </div>

            {/* Footer — User info + Settings */}
            <div className="sidebar-footer">
                <div className="sidebar-user">
                    <div className="user-avatar">
                        <img
                            src={userAvatarUrl}
                            alt={userAvatarAlt}
                            className={`user-avatar-image ${useProviderAvatar ? '' : 'is-default-avatar'}`.trim()}
                            onError={handleUserAvatarError}
                            referrerPolicy={useProviderAvatar ? 'no-referrer' : undefined}
                        />
                    </div>
                    <div className="user-info">
                        <span className="user-name">{userName}</span>
                        <span className="user-email">{userEmail}</span>
                        {hasDesktopUpdate && (
                            <button
                                className="sidebar-update-link"
                                onClick={() => onInstallDesktopUpdate?.()}
                                title={desktopUpdateActionLabel}
                                disabled={isInstallingDesktopUpdate}
                            >
                                <span className="sidebar-update-pulse" />
                                <span>{isInstallingDesktopUpdate ? updateCopy.installing : desktopUpdateLabel}</span>
                            </button>
                        )}
                    </div>
                </div>
                <div className="sidebar-footer-actions">
                    {isAdmin && (
                        <button
                            className="sidebar-footer-btn"
                            onClick={() => onOpenAdmin?.()}
                            title={copy.admin}
                        >
                            <Shield size={16} />
                        </button>
                    )}
                    <button
                        className="sidebar-footer-btn"
                        onClick={() => onOpenSettings?.()}
                        title={copy.settings}
                    >
                        <Settings size={16} />
                    </button>
                </div>
            </div>

            {/* Create Workspace Modal */}
            <AnimatePresence>
                {showCreateModal && (
                    <motion.div
                        className="modal-overlay"
                        initial={{ opacity: 0 }}
                        animate={{ opacity: 1 }}
                        exit={{ opacity: 0 }}
                        onClick={() => setShowCreateModal(false)}
                    >
                        <motion.div
                            className="modal"
                            initial={{ scale: 0.95, opacity: 0 }}
                            animate={{ scale: 1, opacity: 1 }}
                            exit={{ scale: 0.95, opacity: 0 }}
                            onClick={(e) => e.stopPropagation()}
                        >
                            <div className="modal-header">
                                <h3>{copy.createWorkspace}</h3>
                                <button className="modal-close" onClick={() => setShowCreateModal(false)}>
                                    <X size={18} />
                                </button>
                            </div>

                            <div className="modal-body">
                                <div className="form-field">
                                    <label>{copy.name}</label>
                                    <input
                                        type="text"
                                        value={newWorkspaceName}
                                        onChange={(e) => setNewWorkspaceName(e.target.value)}
                                        placeholder={copy.myWorkspace}
                                        autoFocus
                                    />
                                </div>

                                <div className="form-field">
                                    <label>{copy.icon}</label>
                                    <div className="icon-grid">
                                        {workspaceIcons.map((icon) => (
                                            <button
                                                key={icon}
                                                className={`icon-btn ${newWorkspaceIcon === icon ? 'selected' : ''}`}
                                                onClick={() => setNewWorkspaceIcon(icon)}
                                            >
                                                {icon}
                                            </button>
                                        ))}
                                    </div>
                                </div>

                                <div className="form-field">
                                    <label>{copy.color}</label>
                                    <div className="color-grid">
                                        {workspaceColors.map((color) => (
                                            <button
                                                key={color}
                                                className={`color-btn ${newWorkspaceColor === color ? 'selected' : ''}`}
                                                style={{ backgroundColor: color }}
                                                onClick={() => setNewWorkspaceColor(color)}
                                            />
                                        ))}
                                    </div>
                                </div>
                            </div>

                            <div className="modal-footer">
                                <button className="btn-cancel" onClick={() => setShowCreateModal(false)}>
                                    {copy.cancel}
                                </button>
                                <button
                                    className="btn-create"
                                    onClick={handleCreateWorkspace}
                                    disabled={!newWorkspaceName.trim()}
                                >
                                    {copy.create}
                                </button>
                            </div>
                        </motion.div>
                    </motion.div>
                )}
            </AnimatePresence>

            {/* Rename Workspace Modal */}
            <AnimatePresence>
                {showRenameModal && (
                    <motion.div
                        className="modal-overlay"
                        initial={{ opacity: 0 }}
                        animate={{ opacity: 1 }}
                        exit={{ opacity: 0 }}
                        onClick={closeRenameModal}
                    >
                        <motion.div
                            className="modal"
                            initial={{ scale: 0.95, opacity: 0 }}
                            animate={{ scale: 1, opacity: 1 }}
                            exit={{ scale: 0.95, opacity: 0 }}
                            onClick={(e) => e.stopPropagation()}
                        >
                            <div className="modal-header">
                                <h3>{copy.renameWorkspace}</h3>
                                <button className="modal-close" onClick={closeRenameModal}>
                                    <X size={18} />
                                </button>
                            </div>
                            <div className="modal-body">
                                <div className="form-field">
                                    <label>{copy.name}</label>
                                    <input
                                        type="text"
                                        value={renameWorkspaceName}
                                        onChange={(e) => setRenameWorkspaceName(e.target.value)}
                                        placeholder={copy.workspaceName}
                                        autoFocus
                                        onKeyDown={(e) => {
                                            if (e.key === 'Enter') {
                                                void handleRenameWorkspace()
                                            }
                                        }}
                                    />
                                </div>
                            </div>
                            <div className="modal-footer">
                                <button className="btn-cancel" onClick={closeRenameModal}>
                                    {copy.cancel}
                                </button>
                                <button
                                    className="btn-create"
                                    onClick={handleRenameWorkspace}
                                    disabled={!renameWorkspaceName.trim()}
                                >
                                    {copy.save}
                                </button>
                            </div>
                        </motion.div>
                    </motion.div>
                )}
            </AnimatePresence>
        </div>
    )
})

Sidebar.displayName = 'Sidebar'

export default Sidebar
