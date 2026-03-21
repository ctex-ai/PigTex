import { useCallback, useEffect, useMemo, useRef, useState } from 'react'
import {
    Check,
    FolderOpen,
    Globe,
    Pencil,
    RefreshCw,
    Shield,
    X
} from 'lucide-react'

import { getRules, updateRules } from '../../services/api'
import { useI18n } from '../../contexts/I18nContext'
import './MemoryManager.css'

type RuleScope = 'system' | 'workspace'

interface MemoryManagerProps {
    workspaceId?: string | null
}

type RuleScopeMeta = {
    label: string
    emptyMessage: string
    placeholder: string
}

const getErrorMessage = (err: unknown, fallback: string) => {
    if (err instanceof Error && err.message.trim()) return err.message
    return fallback
}

const normalizeWorkspaceId = (value?: string | null) => {
    if (typeof value !== 'string') return null
    const normalized = value.trim()
    return normalized || null
}

const MemoryManager = ({ workspaceId }: MemoryManagerProps) => {
    const { isVietnamese } = useI18n()
    const resolvedWorkspaceId = useMemo(() => {
        if (workspaceId !== undefined) {
            return normalizeWorkspaceId(workspaceId)
        }
        return normalizeWorkspaceId(localStorage.getItem('active_workspace_id'))
    }, [workspaceId])
    const copy = isVietnamese ? {
        system: 'Hệ thống',
        workspace: 'Workspace',
        systemEmpty: 'Chưa có global rules.',
        workspaceEmpty: 'Chưa có workspace rules.',
        systemPlaceholder:
            'Ví dụ:\n' +
            '- Luôn trả lời bằng tiếng Việt\n' +
            '- Ưu tiên câu trả lời ngắn, trực tiếp\n' +
            '- Khi sửa code, luôn nêu rõ file đã thay đổi',
        workspacePlaceholder:
            'Ví dụ:\n' +
            '- Dùng TypeScript strict mode\n' +
            '- Không tạo file mới nếu chưa cần thiết\n' +
            '- Ưu tiên sửa theo convention hiện có của repo',
        loadError: 'Không thể tải rules.',
        rulesScopes: 'Phạm vi rules',
        active: 'Đang bật',
        empty: 'Trống',
        reloadRules: 'Tải lại rules',
        edit: 'Sửa',
        addRules: 'Thêm Rules',
        workspaceNotOpen: 'Chưa mở workspace',
        workspaceUnavailable: 'Rules ở tab này chỉ dùng khi đang có workspace hoạt động.',
        loadingRules: 'Đang tải rules...',
        cancel: 'Hủy',
        saving: 'Đang lưu...',
        save: 'Lưu',
        systemRulesHelp: 'Bạn có thể đặt rules chung cho standalone chat và mọi phiên không gắn workspace.',
        workspaceRulesHelp: 'Workspace rules sẽ được ghép thêm sau global rules mỗi khi chat trong workspace này.',
        footerConfirm: 'xác nhận',
        footerCancel: 'hủy',
        footerNavigate: 'di chuyển',
    } : {
        system: 'System',
        workspace: 'Workspace',
        systemEmpty: 'No global rules yet.',
        workspaceEmpty: 'No workspace rules yet.',
        systemPlaceholder:
            'Example:\n' +
            '- Always answer in English\n' +
            '- Prefer concise, direct responses\n' +
            '- When editing code, always mention changed files',
        workspacePlaceholder:
            'Example:\n' +
            '- Use TypeScript strict mode\n' +
            '- Do not create new files unless necessary\n' +
            '- Prefer the repo’s existing conventions',
        loadError: 'Unable to load rules.',
        rulesScopes: 'Rules scopes',
        active: 'Active',
        empty: 'Empty',
        reloadRules: 'Reload rules',
        edit: 'Edit',
        addRules: 'Add Rules',
        workspaceNotOpen: 'No workspace open',
        workspaceUnavailable: 'Rules in this tab are only used when a workspace is active.',
        loadingRules: 'Loading rules...',
        cancel: 'Cancel',
        saving: 'Saving...',
        save: 'Save',
        systemRulesHelp: 'You can define shared rules for standalone chats and any session without a workspace.',
        workspaceRulesHelp: 'Workspace rules are appended after global rules for chats in this workspace.',
        footerConfirm: 'confirm',
        footerCancel: 'cancel',
        footerNavigate: 'navigate',
    }
    const ruleScopeMeta: Record<RuleScope, RuleScopeMeta> = {
        system: {
            label: copy.system,
            emptyMessage: copy.systemEmpty,
            placeholder: copy.systemPlaceholder
        },
        workspace: {
            label: copy.workspace,
            emptyMessage: copy.workspaceEmpty,
            placeholder: copy.workspacePlaceholder
        }
    }

    const [activeScope, setActiveScope] = useState<RuleScope>(
        resolvedWorkspaceId ? 'workspace' : 'system'
    )
    const [rulesContent, setRulesContent] = useState('')
    const [editedRules, setEditedRules] = useState('')
    const [isLoading, setIsLoading] = useState(false)
    const [isSaving, setIsSaving] = useState(false)
    const [isEditing, setIsEditing] = useState(false)
    const [loadError, setLoadError] = useState<string | null>(null)

    const rulesTextareaRef = useRef<HTMLTextAreaElement>(null)
    const latestLoadIdRef = useRef(0)

    const scopeMeta = ruleScopeMeta[activeScope]
    const isWorkspaceScope = activeScope === 'workspace'
    const isWorkspaceUnavailable = isWorkspaceScope && !resolvedWorkspaceId
    const hasRules = rulesContent.trim().length > 0

    const getTargetWorkspaceId = useCallback(
        (scope: RuleScope) => (scope === 'workspace' ? resolvedWorkspaceId : undefined),
        [resolvedWorkspaceId]
    )

    const loadRules = useCallback(async (scope: RuleScope) => {
        const loadId = latestLoadIdRef.current + 1
        latestLoadIdRef.current = loadId

        if (scope === 'workspace' && !resolvedWorkspaceId) {
            setRulesContent('')
            setEditedRules('')
            setLoadError(null)
            setIsLoading(false)
            return
        }

        setIsLoading(true)
        setLoadError(null)

        try {
            const data = await getRules(getTargetWorkspaceId(scope))
            if (latestLoadIdRef.current !== loadId) return

            const nextRules = data.rules || ''
            setRulesContent(nextRules)
            setEditedRules(nextRules)
        } catch (err) {
            if (latestLoadIdRef.current !== loadId) return

            setRulesContent('')
            setEditedRules('')
            setLoadError(getErrorMessage(err, copy.loadError))
        } finally {
            if (latestLoadIdRef.current === loadId) {
                setIsLoading(false)
            }
        }
    }, [copy.loadError, getTargetWorkspaceId, resolvedWorkspaceId])

    const handleSaveRules = useCallback(async () => {
        if (isWorkspaceUnavailable) return

        setIsSaving(true)
        setLoadError(null)

        try {
            await updateRules(editedRules, getTargetWorkspaceId(activeScope))
            setRulesContent(editedRules)
            setIsEditing(false)
        } catch (err) {
            setLoadError(getErrorMessage(err, copy.loadError))
        } finally {
            setIsSaving(false)
        }
    }, [activeScope, copy.loadError, editedRules, getTargetWorkspaceId, isWorkspaceUnavailable])

    const handleStartEdit = useCallback(() => {
        setEditedRules(rulesContent)
        setLoadError(null)
        setIsEditing(true)
    }, [rulesContent])

    const handleCancelEdit = useCallback(() => {
        setEditedRules(rulesContent)
        setLoadError(null)
        setIsEditing(false)
    }, [rulesContent])

    useEffect(() => {
        setIsEditing(false)
        void loadRules(activeScope)
    }, [activeScope, loadRules])

    useEffect(() => {
        if (isEditing && rulesTextareaRef.current) {
            rulesTextareaRef.current.focus()
            rulesTextareaRef.current.setSelectionRange(
                rulesTextareaRef.current.value.length,
                rulesTextareaRef.current.value.length
            )
        }
    }, [isEditing])

    return (
        <div className="mm-container">
            <div className="mm-tabs" role="tablist" aria-label={copy.rulesScopes}>
                <button
                    type="button"
                    role="tab"
                    aria-selected={activeScope === 'system'}
                    className={`mm-tab ${activeScope === 'system' ? 'active' : ''}`}
                    onClick={() => setActiveScope('system')}
                >
                    <Globe size={14} />
                    <span className="mm-tab-label">{copy.system}</span>
                </button>
                <button
                    type="button"
                    role="tab"
                    aria-selected={activeScope === 'workspace'}
                    className={`mm-tab ${activeScope === 'workspace' ? 'active' : ''}`}
                    onClick={() => setActiveScope('workspace')}
                >
                    <FolderOpen size={14} />
                    <span className="mm-tab-label">{copy.workspace}</span>
                </button>
            </div>

            <div className="mm-panel">
                <div className="mm-panel-header">
                    <div className="mm-scope-meta">
                        <div className="mm-scope-icon">
                            <Shield size={14} />
                        </div>
                        <div className="mm-scope-copy">
                            <div className="mm-scope-topline">
                                <span className="mm-scope-title">{scopeMeta.label} Rules</span>
                                <span className={`mm-badge ${hasRules ? 'mm-badge-active' : 'mm-badge-empty'}`}>
                                    {hasRules ? copy.active : copy.empty}
                                </span>
                            </div>
                        </div>
                    </div>

                    <div className="mm-header-actions">
                        <button
                            type="button"
                            className="mm-icon-btn"
                            onClick={() => void loadRules(activeScope)}
                            disabled={isLoading || isSaving}
                            title={copy.reloadRules}
                        >
                            <RefreshCw size={13} className={isLoading ? 'mm-spin' : ''} />
                        </button>
                        {!isEditing && (
                            <button
                                type="button"
                                className="mm-btn mm-btn-primary"
                                onClick={handleStartEdit}
                                disabled={isWorkspaceUnavailable || isLoading}
                            >
                                <Pencil size={13} />
                                {hasRules ? copy.edit : copy.addRules}
                            </button>
                        )}
                    </div>
                </div>

                {isWorkspaceUnavailable ? (
                    <div className="mm-state mm-state-empty">
                        <FolderOpen size={18} />
                        <div className="mm-state-title">{copy.workspaceNotOpen}</div>
                        <div className="mm-state-copy">
                            {copy.workspaceUnavailable}
                        </div>
                    </div>
                ) : isLoading ? (
                    <div className="mm-state mm-state-loading">
                        <RefreshCw size={16} className="mm-spin" />
                        <span>{copy.loadingRules}</span>
                    </div>
                ) : (
                    <>
                        {loadError && (
                            <div className="mm-alert mm-alert-error">{loadError}</div>
                        )}

                        {isEditing ? (
                            <div className="mm-editor">
                                <textarea
                                    ref={rulesTextareaRef}
                                    className="mm-textarea"
                                    value={editedRules}
                                    onChange={(event) => setEditedRules(event.target.value)}
                                    onKeyDown={(event) => {
                                        if ((event.ctrlKey || event.metaKey) && event.key.toLowerCase() === 's') {
                                            event.preventDefault()
                                            void handleSaveRules()
                                        }
                                        if (event.key === 'Escape') {
                                            event.preventDefault()
                                            handleCancelEdit()
                                        }
                                    }}
                                    placeholder={scopeMeta.placeholder}
                                    rows={12}
                                />
                                <div className="mm-editor-actions">
                                    <button
                                        type="button"
                                        className="mm-btn mm-btn-secondary"
                                        onClick={handleCancelEdit}
                                        disabled={isSaving}
                                    >
                                        <X size={13} />
                                        {copy.cancel}
                                    </button>
                                    <button
                                        type="button"
                                        className="mm-btn mm-btn-primary"
                                        onClick={() => void handleSaveRules()}
                                        disabled={isSaving}
                                    >
                                        <Check size={13} />
                                        {isSaving ? copy.saving : copy.save}
                                    </button>
                                </div>
                            </div>
                        ) : hasRules ? (
                            <div className="mm-display">
                                <pre className="mm-rules-content">{rulesContent}</pre>
                            </div>
                        ) : (
                            <div className="mm-state mm-state-empty">
                                <Shield size={18} />
                                <div className="mm-state-title">{scopeMeta.emptyMessage}</div>
                                <div className="mm-state-copy">
                                    {activeScope === 'system'
                                        ? copy.systemRulesHelp
                                        : copy.workspaceRulesHelp}
                                </div>
                            </div>
                        )}
                    </>
                )}
            </div>
        </div>
    )
}

export default MemoryManager
