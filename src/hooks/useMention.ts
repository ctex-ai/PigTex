/**
 * useMention Hook
 * ================
 * Manages @file / @folder mention popup state for the chat textarea.
 * 
 * When user types "@" -> opens popup.
 * Filters workspace file tree in real-time as user continues typing.
 * On selection -> inserts a mention token.
 */

import { useState, useCallback, useRef, useEffect } from 'react'

// ===== Types =====

export interface MentionItem {
    /** 'file' or 'folder' */
    type: 'file' | 'folder'
    /** Relative path from workspace root */
    relativePath: string
    /** Display name (file/folder name) */
    name: string
    /** Full absolute path */
    absolutePath: string
}

export interface MentionToken {
    type: 'file' | 'folder'
    relativePath: string
    absolutePath: string
    name: string
}

interface MentionPopupState {
    isOpen: boolean
    query: string
    /** The index within the textarea where '@' was typed */
    triggerIndex: number
    /** Position on screen for popup placement */
    position: { top: number; left: number }
    /** Currently highlighted item index */
    activeIndex: number
}

const INITIAL_STATE: MentionPopupState = {
    isOpen: false,
    query: '',
    triggerIndex: -1,
    position: { top: 0, left: 0 },
    activeIndex: 0
}

// Mention token format: [[@type:relativePath]]
const MENTION_REGEX = /\[\[@(file|folder):([^\]]+)\]\]/g

// ===== Parse mentions from text =====

export function parseMentions(text: string): MentionToken[] {
    const mentions: MentionToken[] = []
    let match: RegExpExecArray | null

    const regex = new RegExp(MENTION_REGEX.source, 'g')
    while ((match = regex.exec(text)) !== null) {
        mentions.push({
            type: match[1] as 'file' | 'folder',
            relativePath: match[2],
            absolutePath: '', // will be resolved at send time
            name: match[2].split(/[/\\]/).pop() || match[2]
        })
    }

    return mentions
}

/**
 * Strip mention tokens from text, keeping display-friendly version.
 * [[@file:src/App.tsx]] -> @src/App.tsx
 */
export function stripMentionTokens(text: string): string {
    return text.replace(MENTION_REGEX, '@$2')
}

/**
 * Get clean message text (no mention tokens).
 */
export function getCleanMessage(text: string): string {
    return text.replace(MENTION_REGEX, '').trim()
}

/**
 * Build display text from mention.
 * Used to render mention chips in the UI.
 */
export function getMentionDisplayText(mention: MentionToken): string {
    const icon = mention.type === 'file' ? '📄' : '📁'
    return `${icon} ${mention.name}`
}

export function buildMentionAwareMessageText(
    promptText: string,
    mentions: Array<Pick<MentionItem, 'type' | 'relativePath'>>,
    fallbackAttachmentText: string = ''
): string {
    const normalizedPrompt = promptText.trim()
    const mentionSummary = mentions
        .map((mention) => `@${mention.type}:${mention.relativePath}`)
        .join(', ')
    const normalizedFallback = fallbackAttachmentText.trim()

    if (normalizedPrompt && mentionSummary) {
        return `${normalizedPrompt}\n\nReferenced items: ${mentionSummary}`
    }
    if (normalizedPrompt) return normalizedPrompt
    if (mentionSummary) return mentionSummary
    return normalizedFallback
}

// ===== Flatten file tree for search =====

interface FlattenInput {
    name: string
    path: string
    type: 'file' | 'directory'
}

export function flattenFileTree(
    tree: Record<string, FlattenInput[]>,
    rootPath: string
): MentionItem[] {
    const items: MentionItem[] = []
    const visited = new Set<string>()

    const walk = (dirPath: string) => {
        if (visited.has(dirPath)) return
        visited.add(dirPath)

        const entries = tree[dirPath] || []
        for (const entry of entries) {
            // Compute relative path
            let relativePath = entry.path
            if (relativePath.startsWith(rootPath)) {
                relativePath = relativePath.slice(rootPath.length)
                    .replace(/^[\\/]+/, '')
            }
            // Normalize separators
            relativePath = relativePath.replace(/\\/g, '/')

            items.push({
                type: entry.type === 'directory' ? 'folder' : 'file',
                relativePath,
                name: entry.name,
                absolutePath: entry.path
            })

            if (entry.type === 'directory') {
                walk(entry.path)
            }
        }
    }

    walk(rootPath)
    return items
}

/**
 * Filter mention items by query string.
 * Matches against name and path (case-insensitive).
 */
export function filterMentionItems(
    items: MentionItem[],
    query: string,
    limit: number = 12
): MentionItem[] {
    if (!query.trim()) {
        return items.slice(0, limit)
    }

    const q = query.toLowerCase().trim()
    const scored: { item: MentionItem; score: number }[] = []

    for (const item of items) {
        const nameLower = item.name.toLowerCase()
        const pathLower = item.relativePath.toLowerCase()

        let score = 0
        // Exact name match
        if (nameLower === q) score = 100
        // Name starts with query
        else if (nameLower.startsWith(q)) score = 80
        // Name contains query
        else if (nameLower.includes(q)) score = 60
        // Path contains query
        else if (pathLower.includes(q)) score = 40
        else continue

        // Boost folders slightly for short queries
        if (item.type === 'folder' && q.length <= 3) score += 5

        scored.push({ item, score })
    }

    scored.sort((a, b) => {
        if (b.score !== a.score) return b.score - a.score
        // Sort by type (folders first), then name
        if (a.item.type !== b.item.type) return a.item.type === 'folder' ? -1 : 1
        return a.item.name.localeCompare(b.item.name)
    })

    return scored.slice(0, limit).map(s => s.item)
}

// ===== Hook =====

export function useMention(
    textareaRef: React.RefObject<HTMLTextAreaElement | null>
) {
    const [popup, setPopup] = useState<MentionPopupState>(INITIAL_STATE)
    const mentionQueryRef = useRef('')

    const openPopup = useCallback((triggerIndex: number, position: { top: number; left: number }) => {
        setPopup({
            isOpen: true,
            query: '',
            triggerIndex,
            position,
            activeIndex: 0
        })
        mentionQueryRef.current = ''
    }, [])

    const closePopup = useCallback(() => {
        setPopup(INITIAL_STATE)
        mentionQueryRef.current = ''
    }, [])

    const updateQuery = useCallback((query: string) => {
        mentionQueryRef.current = query
        setPopup(prev => ({
            ...prev,
            query,
            activeIndex: 0
        }))
    }, [])

    const setActiveIndex = useCallback((index: number) => {
        setPopup(prev => ({ ...prev, activeIndex: index }))
    }, [])

    /**
     * Insert a mention token into the textarea value.
     * Returns the new textarea value.
     */
    const insertMention = useCallback((
        currentValue: string,
        item: MentionItem
    ): string => {
        const { triggerIndex } = popup
        if (triggerIndex < 0) return currentValue

        // Find the end of current mention query 
        // @ + query text (no spaces in query typically)
        const beforeAt = currentValue.slice(0, triggerIndex)
        const afterAt = currentValue.slice(triggerIndex)
        // Find how far the query extends
        const queryEndMatch = afterAt.match(/^@[^\s]*/)
        const queryLength = queryEndMatch ? queryEndMatch[0].length : 1

        const mentionToken = `[[@${item.type}:${item.relativePath}]] `
        const newValue = beforeAt + mentionToken + currentValue.slice(triggerIndex + queryLength)

        closePopup()

        // Restore focus to textarea and move cursor
        setTimeout(() => {
            const textarea = textareaRef.current
            if (textarea) {
                textarea.focus()
                const cursorPos = beforeAt.length + mentionToken.length
                textarea.selectionStart = cursorPos
                textarea.selectionEnd = cursorPos
            }
        }, 0)

        return newValue
    }, [popup, closePopup, textareaRef])

    /**
     * Handle textarea onChange to detect @ triggers and update query.
     */
    const handleInputChange = useCallback((
        value: string,
        cursorPosition: number
    ) => {
        // Find the last unmatched @ before cursor
        const textBeforeCursor = value.slice(0, cursorPosition)

        // Check if we're inside a completed mention token [[@...]]
        const lastOpenBracket = textBeforeCursor.lastIndexOf('[[@')
        const lastCloseBracket = textBeforeCursor.lastIndexOf(']]')
        if (lastOpenBracket >= 0 && lastCloseBracket < lastOpenBracket) {
            // We're inside a mention token - don't trigger popup
            return
        }

        // Find last @ that is not part of a mention token
        let atIndex = -1
        for (let i = textBeforeCursor.length - 1; i >= 0; i--) {
            if (textBeforeCursor[i] === '@') {
                // Check if this @ is part of [[@
                if (i >= 2 && textBeforeCursor.slice(i - 2, i) === '[[') {
                    continue
                }
                // Check if there's a space before the query (don't trigger mid-word)
                if (i > 0 && textBeforeCursor[i - 1] !== ' ' && textBeforeCursor[i - 1] !== '\n') {
                    continue
                }
                atIndex = i
                break
            }
            // Stop looking if we hit a space (query broke)
            if (textBeforeCursor[i] === ' ' || textBeforeCursor[i] === '\n') {
                break
            }
        }

        if (atIndex === -1) {
            // Special case: @ at position 0
            if (textBeforeCursor.length > 0 && textBeforeCursor[0] === '@' && cursorPosition <= textBeforeCursor.length) {
                const queryAfterAt = textBeforeCursor.slice(1)
                if (!queryAfterAt.includes(' ') && !queryAfterAt.includes('\n')) {
                    atIndex = 0
                }
            }
        }

        if (atIndex >= 0) {
            const queryAfterAt = textBeforeCursor.slice(atIndex + 1)

            // Don't open if query has space (user moved on)
            if (queryAfterAt.includes(' ') || queryAfterAt.includes('\n')) {
                if (popup.isOpen) closePopup()
                return
            }

            if (!popup.isOpen) {
                // Calculate popup position
                const textarea = textareaRef.current
                if (textarea) {
                    const rect = textarea.getBoundingClientRect()
                    const position = {
                        top: rect.top - 8, // Above the textarea
                        left: rect.left + 12
                    }
                    openPopup(atIndex, position)
                }
            }
            updateQuery(queryAfterAt)
        } else {
            if (popup.isOpen) closePopup()
        }
    }, [popup.isOpen, openPopup, closePopup, updateQuery, textareaRef])

    /**
     * Handle keyboard navigation within the popup.
     * Returns true if the key was handled (prevent default).
     */
    const handleKeyDown = useCallback((
        e: React.KeyboardEvent,
        filteredItems: MentionItem[],
        onSelect: (item: MentionItem) => void
    ): boolean => {
        if (!popup.isOpen) return false

        switch (e.key) {
            case 'ArrowDown':
                e.preventDefault()
                setActiveIndex(
                    popup.activeIndex < filteredItems.length - 1
                        ? popup.activeIndex + 1
                        : 0
                )
                return true

            case 'ArrowUp':
                e.preventDefault()
                setActiveIndex(
                    popup.activeIndex > 0
                        ? popup.activeIndex - 1
                        : filteredItems.length - 1
                )
                return true

            case 'Enter':
            case 'Tab':
                if (filteredItems.length > 0) {
                    e.preventDefault()
                    onSelect(filteredItems[popup.activeIndex] || filteredItems[0])
                    return true
                }
                return false

            case 'Escape':
                e.preventDefault()
                closePopup()
                return true

            default:
                return false
        }
    }, [popup.isOpen, popup.activeIndex, setActiveIndex, closePopup])

    // Close popup when clicking outside
    useEffect(() => {
        if (!popup.isOpen) return

        const handleClick = (e: MouseEvent) => {
            const target = e.target as HTMLElement
            if (target.closest('.mention-popup')) return
            if (textareaRef.current?.contains(target)) return
            closePopup()
        }

        document.addEventListener('mousedown', handleClick)
        return () => document.removeEventListener('mousedown', handleClick)
    }, [popup.isOpen, closePopup, textareaRef])

    return {
        popup,
        openPopup,
        closePopup,
        updateQuery,
        setActiveIndex,
        insertMention,
        handleInputChange,
        handleKeyDown
    }
}
