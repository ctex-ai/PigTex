/**
 * MentionPopup Component
 * =======================
 * Dropdown popup that appears when user types @ in the chat textarea.
 * Shows files and folders from the workspace tree for selection.
 */

import { useRef, useEffect } from 'react'
import { motion, AnimatePresence } from 'framer-motion'
import { FileText, FolderOpen, Hash, MessageSquare, Search } from 'lucide-react'
import type { MentionItem } from '../../../hooks/useMention'
import { useI18n } from '../../../contexts/I18nContext'
import './MentionPopup.css'

interface MentionPopupProps {
    isOpen: boolean
    items: MentionItem[]
    query: string
    activeIndex: number
    position: { top: number; left: number }
    onSelect: (item: MentionItem) => void
    onHover: (index: number) => void
    onClose: () => void
}

function getFileIcon(name: string) {
    const ext = name.split('.').pop()?.toLowerCase() || ''
    const iconMap: Record<string, string> = {
        ts: '🟦', tsx: '⚛️', js: '🟨', jsx: '⚛️',
        py: '🐍', css: '🎨', html: '🌐', json: '📋',
        md: '📝', txt: '📄', svg: '🖼️', png: '🖼️',
        jpg: '🖼️', gif: '🖼️', yml: '⚙️', yaml: '⚙️',
        toml: '⚙️', env: '🔒', gitignore: '🔒',
    }
    return iconMap[ext] || null
}

export default function MentionPopup({
    isOpen,
    items,
    query,
    activeIndex,
    onSelect,
    onHover,
}: MentionPopupProps) {
    const { isVietnamese } = useI18n()
    const listRef = useRef<HTMLDivElement>(null)
    const activeItemRef = useRef<HTMLButtonElement>(null)
    const copy = isVietnamese ? {
        mention: 'Nhắc tới',
        noMatches: (value: string) => `Không có kết quả cho "${value}"`,
        noFiles: 'Không có tệp trong workspace',
        navigate: 'di chuyển',
        select: 'chọn',
        close: 'đóng'
    } : {
        mention: 'Mention',
        noMatches: (value: string) => `No matches for "${value}"`,
        noFiles: 'No files in workspace',
        navigate: 'navigate',
        select: 'select',
        close: 'close'
    }

    // Scroll active item into view
    useEffect(() => {
        if (activeItemRef.current && listRef.current) {
            const container = listRef.current
            const item = activeItemRef.current
            const itemTop = item.offsetTop
            const itemBottom = itemTop + item.offsetHeight
            const scrollTop = container.scrollTop
            const containerHeight = container.clientHeight

            if (itemTop < scrollTop) {
                container.scrollTop = itemTop - 4
            } else if (itemBottom > scrollTop + containerHeight) {
                container.scrollTop = itemBottom - containerHeight + 4
            }
        }
    }, [activeIndex])

    return (
        <AnimatePresence>
            {isOpen && (
                <motion.div
                    className="mention-popup"
                    style={{
                        bottom: `calc(100% + 8px)`,
                        left: 12,
                        right: 12
                    }}
                    initial={{ opacity: 0, y: 8, scale: 0.98 }}
                    animate={{ opacity: 1, y: 0, scale: 1 }}
                    exit={{ opacity: 0, y: 8, scale: 0.98 }}
                    transition={{ duration: 0.15, ease: [0.23, 1, 0.32, 1] }}
                >
                    {/* Header */}
                    <div className="mention-popup-header">
                        <div className="mention-popup-header-left">
                            <Hash size={13} />
                            <span>{copy.mention}</span>
                        </div>
                        {query && (
                            <span className="mention-popup-query">
                                <Search size={11} />
                                {query}
                            </span>
                        )}
                    </div>

                    {/* Items list */}
                    <div className="mention-popup-list" ref={listRef}>
                        {items.length === 0 ? (
                            <div className="mention-popup-empty">
                                {query
                                    ? copy.noMatches(query)
                                    : copy.noFiles
                                }
                            </div>
                        ) : (
                            items.map((item, index) => {
                                const isActive = index === activeIndex
                                const emojiIcon = item.type === 'file' ? getFileIcon(item.name) : null
                                const itemMeta = item.subtitle || item.relativePath

                                return (
                                    <button
                                        key={`${item.type}:${item.referenceId || item.relativePath}`}
                                        ref={isActive ? activeItemRef : null}
                                        className={`mention-popup-item ${isActive ? 'active' : ''} mention-type-${item.type}`}
                                        onMouseEnter={() => onHover(index)}
                                        onMouseDown={(e) => {
                                            e.preventDefault() // keep textarea focused
                                            onSelect(item)
                                        }}
                                    >
                                        <span className="mention-item-icon">
                                            {item.type === 'folder' ? (
                                                <FolderOpen size={14} />
                                            ) : item.type === 'conversation' ? (
                                                <MessageSquare size={14} />
                                            ) : emojiIcon ? (
                                                <span className="mention-item-emoji">{emojiIcon}</span>
                                            ) : (
                                                <FileText size={14} />
                                            )}
                                        </span>
                                        <span className="mention-item-name">{item.name}</span>
                                        <span className="mention-item-path">{itemMeta}</span>
                                    </button>
                                )
                            })
                        )}
                    </div>

                    {/* Footer hint */}
                    <div className="mention-popup-footer">
                        <span><kbd>↑↓</kbd> {copy.navigate}</span>
                        <span><kbd>↵</kbd> {copy.select}</span>
                        <span><kbd>esc</kbd> {copy.close}</span>
                    </div>
                </motion.div>
            )}
        </AnimatePresence>
    )
}
