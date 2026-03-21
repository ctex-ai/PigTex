import {
    Minus,
    Square,
    X,
    MessageSquare,
    FolderOpen,
    FilePlus2,
    FolderPlus,
    RefreshCw,
    HardDrive,
    Undo2,
    Shield
} from 'lucide-react'
import { useI18n } from '../../contexts/I18nContext'
import { ViewMode } from './Dashboard'
import './TitleBar.css'
import pigtexLogoUrl from '../../../assets/pigtex_logo.png'

interface TitleBarProps {
    viewMode: ViewMode
    onModeChange: (mode: ViewMode) => void
    showAdminMode?: boolean
    localRootPath?: string | null
    undoCount?: number
    undoDescription?: string | null
    isUndoing?: boolean
    onOpenLocalFolder?: () => Promise<void> | void
    onCreateLocalFile?: () => Promise<void> | void
    onCreateLocalFolder?: () => Promise<void> | void
    onRefreshLocalFolder?: () => Promise<void> | void
    onUndoLastChange?: () => Promise<void> | void
}

const getBaseName = (inputPath: string) => {
    const parts = inputPath.split(/[\\/]+/).filter(Boolean)
    return parts[parts.length - 1] || inputPath
}

const TitleBar = ({
    viewMode,
    onModeChange,
    showAdminMode = false,
    localRootPath,
    undoCount = 0,
    undoDescription,
    isUndoing = false,
    onOpenLocalFolder,
    onCreateLocalFile,
    onCreateLocalFolder,
    onRefreshLocalFolder,
    onUndoLastChange
}: TitleBarProps) => {
    const { isVietnamese } = useI18n()
    const handleMinimize = () => window.electronAPI?.minimize?.()
    const handleMaximize = () => window.electronAPI?.maximize?.()
    const handleClose = () => window.electronAPI?.close?.()
    const hasLocalFolder = !!localRootPath
    const canUndo = hasLocalFolder && undoCount > 0 && !isUndoing
    const copy = isVietnamese ? {
        chat: 'Chat',
        files: 'Tệp',
        admin: 'Admin',
        noFolder: 'Chưa mở thư mục',
        noFolderSelected: 'Chưa chọn thư mục',
        openFolder: 'Mở thư mục',
        openFolderTitle: 'Mở thư mục cục bộ (Ctrl+O)',
        newFileTitle: 'Tệp mới (Ctrl+N)',
        newFolderTitle: 'Thư mục mới (Ctrl+Shift+N)',
        refreshTitle: 'Làm mới thư mục (F5)',
        openLocalFolderFirst: 'Hãy mở thư mục cục bộ trước',
        noLocalChanges: 'Không có thay đổi cục bộ để hoàn tác',
        undoing: 'Đang hoàn tác…',
        undo: 'Hoàn tác',
        aiAssistant: 'Trợ lý AI',
    } : {
        chat: 'Chat',
        files: 'Files',
        admin: 'Admin',
        noFolder: 'No Folder',
        noFolderSelected: 'No folder selected',
        openFolder: 'Open Folder',
        openFolderTitle: 'Open local folder (Ctrl+O)',
        newFileTitle: 'New file (Ctrl+N)',
        newFolderTitle: 'New folder (Ctrl+Shift+N)',
        refreshTitle: 'Refresh folder (F5)',
        openLocalFolderFirst: 'Open a local folder first',
        noLocalChanges: 'No local changes to undo',
        undoing: 'Undoing…',
        undo: 'Undo',
        aiAssistant: 'AI Assistant',
    }
    const localFolderName = localRootPath ? getBaseName(localRootPath) : copy.noFolder

    return (
        <div className="titlebar drag-region">
            <div className="titlebar-left no-drag">
                <div className="titlebar-logo">
                    <img src={pigtexLogoUrl} alt="PigTex logo" className="titlebar-logo-image" />
                    <span className="titlebar-logo-text">PigTex</span>
                </div>

                {/* Mode Switcher */}
                <div className="titlebar-mode-switcher">
                    <button
                        className={`mode-btn ${viewMode === 'chat' ? 'active' : ''}`}
                        onClick={() => onModeChange('chat')}
                    >
                        <MessageSquare size={14} />
                        <span>{copy.chat}</span>
                    </button>
                    <button
                        className={`mode-btn ${viewMode === 'editor' ? 'active' : ''}`}
                        onClick={() => onModeChange('editor')}
                    >
                        <FolderOpen size={14} />
                        <span>{copy.files}</span>
                    </button>
                    {showAdminMode && (
                        <button
                            className={`mode-btn ${viewMode === 'admin' ? 'active' : ''}`}
                            onClick={() => onModeChange('admin')}
                        >
                            <Shield size={14} />
                            <span>{copy.admin}</span>
                        </button>
                    )}
                </div>

                <div className="titlebar-local-tools">
                    <span className="local-folder-badge" title={localRootPath || copy.noFolderSelected}>
                        <HardDrive size={12} />
                        <span>{localFolderName}</span>
                    </span>
                    <button
                        className="local-tool-btn local-tool-open"
                        onClick={() => void onOpenLocalFolder?.()}
                        title={copy.openFolderTitle}
                    >
                        <FolderOpen size={12} />
                        <span>{copy.openFolder}</span>
                    </button>
                    <button
                        className="local-tool-icon-btn"
                        onClick={() => void onCreateLocalFile?.()}
                        disabled={!hasLocalFolder}
                        title={copy.newFileTitle}
                    >
                        <FilePlus2 size={12} />
                    </button>
                    <button
                        className="local-tool-icon-btn"
                        onClick={() => void onCreateLocalFolder?.()}
                        disabled={!hasLocalFolder}
                        title={copy.newFolderTitle}
                    >
                        <FolderPlus size={12} />
                    </button>
                    <button
                        className="local-tool-icon-btn"
                        onClick={() => void onRefreshLocalFolder?.()}
                        disabled={!hasLocalFolder}
                        title={copy.refreshTitle}
                    >
                        <RefreshCw size={12} />
                    </button>
                    <div className="titlebar-tools-sep" />
                    <button
                        className={`local-tool-btn local-tool-undo ${canUndo ? 'active' : ''} ${isUndoing ? 'undoing' : ''}`}
                        onClick={() => void onUndoLastChange?.()}
                        disabled={!canUndo}
                        title={
                            !hasLocalFolder
                                ? copy.openLocalFolderFirst
                                : undoDescription
                                    ? `Undo: ${undoDescription}`
                                    : copy.noLocalChanges
                        }
                    >
                        <Undo2 size={12} />
                        <span>{isUndoing ? copy.undoing : copy.undo}</span>
                        {undoCount > 0 && <span className="local-undo-count">{undoCount}</span>}
                    </button>
                </div>
            </div>

            <div className="titlebar-center">
                <span className="titlebar-workspace">
                    {viewMode === 'chat'
                        ? copy.aiAssistant
                        : viewMode === 'admin'
                            ? copy.admin
                            : localRootPath
                                ? getBaseName(localRootPath)
                                : copy.files}
                </span>
            </div>

            <div className="titlebar-right no-drag">
                <button className="titlebar-button" onClick={handleMinimize}>
                    <Minus size={14} />
                </button>
                <button className="titlebar-button" onClick={handleMaximize}>
                    <Square size={12} />
                </button>
                <button className="titlebar-button titlebar-button-close" onClick={handleClose}>
                    <X size={14} />
                </button>
            </div>
        </div>
    )
}

export default TitleBar
