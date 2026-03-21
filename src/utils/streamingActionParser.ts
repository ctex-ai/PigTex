/**
 * Streaming Action Parser — Cline-inspired realtime tool detection.
 *
 * Detects file-operation tags DURING streaming so that:
 *   1. File writes can happen in real-time (content appended as it streams)
 *   2. Other actions (read, delete, rename, folder) fire immediately on tag close
 *   3. Backward-compat: pigtex_fs JSON blocks still work for batch actions
 *
 * ─── Supported streaming tags ────────────────────────────
 *
 *   Write / Create:
 *     <pigtex_write path="relative/path.ts">
 *     ...raw content (no escaping needed)...
 *     </pigtex_write>
 *
 *   Read:
 *     <pigtex_read path="relative/path.ts" />
 *
 *   Delete:
 *     <pigtex_delete path="relative/path.ts" />
 *
 *   Create folder:
 *     <pigtex_mkdir path="relative/folder" />
 *
 *   Rename:
 *     <pigtex_rename path="old/path" new_path="new/path" />
 *
 * These tags can appear anywhere in the AI's streamed response.
 * Text outside tags is treated as normal chat content.
 */

// ─── Event types ────────────────────────────────────────

export type StreamingParserEvent =
    | { type: 'text'; content: string }
    | { type: 'action_start'; action: StreamingAction }
    | { type: 'content_chunk'; content: string }
    | { type: 'action_end'; action: StreamingAction }
    | { type: 'self_closing_action'; action: StreamingAction }

export interface StreamingAction {
    actionType:
        | 'write_file'
        | 'create_file'
        | 'apply_diff'
        | 'read_file'
        | 'delete_file'
        | 'create_folder'
        | 'rename_path'
        | 'delete_path'
        | 'list_directory'
    path: string
    newPath?: string
    content: string
}

// ─── Tag mapping ────────────────────────────────────────

const TAG_TO_ACTION: Record<string, StreamingAction['actionType']> = {
    pigtex_write: 'write_file',
    'pigtex.write': 'write_file',
    pigtex_create: 'create_file',
    'pigtex.create': 'create_file',
    pigtex_patch: 'apply_diff',
    'pigtex.patch': 'apply_diff',
    pigtex_read: 'read_file',
    'pigtex.read': 'read_file',
    pigtex_delete: 'delete_file',
    'pigtex.delete': 'delete_file',
    pigtex_mkdir: 'create_folder',
    'pigtex.mkdir': 'create_folder',
    pigtex_rename: 'rename_path',
    'pigtex.rename': 'rename_path',
    pigtex_rm: 'delete_path',
    'pigtex.rm': 'delete_path',
    pigtex_ls: 'list_directory',
    'pigtex.ls': 'list_directory',
    pigtex_list: 'list_directory',
    'pigtex.list': 'list_directory',
}

// Tags that have content between open/close
const CONTENT_TAGS = new Set([
    'pigtex_write',
    'pigtex.write',
    'pigtex_create',
    'pigtex.create',
    'pigtex_patch',
    'pigtex.patch'
])

// Regex patterns
const ACTION_TAG_PATTERN = 'pigtex(?:_|\\.)(?:write|create|patch|read|delete|mkdir|rename|rm|ls|list)'
const SELF_CLOSING_TAG_PATTERN = 'pigtex(?:_|\\.)(?:read|delete|mkdir|rename|rm|ls|list)'
const OPENING_TAG_RE = new RegExp(`<(${ACTION_TAG_PATTERN})\\b([^>]*?)>`, 'i')
const SELF_CLOSING_TAG_RE = new RegExp(`<(${SELF_CLOSING_TAG_PATTERN})\\b([^>]*?)\\/\\s*>`, 'i')
const ATTR_RE = /([:@\w-]+)\s*=\s*(?:"([^"]*)"|'([^']*)'|([^\s"'=<>`]+))/g

// ─── Parser class ───────────────────────────────────────

export class StreamingActionParser {
    private buffer = ''
    private state: 'text' | 'content' = 'text'
    private currentAction: StreamingAction | null = null
    private currentTagName = ''
    private accumulatedContent = ''
    private readonly holdBackChars = 220

    /**
     * Feed a new chunk of streamed text. Returns events that occurred.
     * Call this for every SSE text chunk received.
     */
    feed(chunk: string): StreamingParserEvent[] {
        this.buffer += chunk
        const events: StreamingParserEvent[] = []
        let safety = 0
        const MAX_ITERATIONS = 100

        while (this.buffer.length > 0 && safety++ < MAX_ITERATIONS) {
            if (this.state === 'text') {
                const consumed = this.processTextState(events)
                if (!consumed) break
            } else if (this.state === 'content') {
                const consumed = this.processContentState(events)
                if (!consumed) break
            }
        }

        return events
    }

    /**
     * Flush any remaining buffer content (call when stream ends).
     */
    flush(): StreamingParserEvent[] {
        const events: StreamingParserEvent[] = []

        if (this.state === 'content' && this.currentAction) {
            // Stream ended mid-tag — flush accumulated content as the final content
            if (this.buffer.length > 0) {
                this.accumulatedContent += this.buffer
                events.push({ type: 'content_chunk', content: this.buffer })
                this.buffer = ''
            }
            this.currentAction.content = this.accumulatedContent
            events.push({ type: 'action_end', action: { ...this.currentAction } })
            this.currentAction = null
            this.state = 'text'
        }

        if (this.buffer.length > 0) {
            events.push({ type: 'text', content: this.buffer })
            this.buffer = ''
        }

        return events
    }

    /** Reset parser state for a new message. */
    reset(): void {
        this.buffer = ''
        this.state = 'text'
        this.currentAction = null
        this.currentTagName = ''
        this.accumulatedContent = ''
    }

    /** Check if parser is currently inside a content tag. */
    isInsideContentTag(): boolean {
        return this.state === 'content'
    }

    /** Get current action being parsed (if any). */
    getCurrentAction(): StreamingAction | null {
        return this.currentAction ? { ...this.currentAction } : null
    }

    // ─── Internal state handlers ────────────────────────

    private processTextState(events: StreamingParserEvent[]): boolean {
        // Try self-closing tags first (they won't match opening tags)
        const selfCloseMatch = this.buffer.match(SELF_CLOSING_TAG_RE)
        if (selfCloseMatch && selfCloseMatch.index !== undefined) {
            // Emit text before the tag
            if (selfCloseMatch.index > 0) {
                events.push({ type: 'text', content: this.buffer.slice(0, selfCloseMatch.index) })
            }

            const tagName = selfCloseMatch[1].toLowerCase()
            const attrs = this.parseAttributes(selfCloseMatch[2])
            const actionType = TAG_TO_ACTION[tagName]

            const action = this.buildAction(actionType, attrs)
            if (action) {
                events.push({ type: 'self_closing_action', action })
            }

            this.buffer = this.buffer.slice(selfCloseMatch.index + selfCloseMatch[0].length)
            return true
        }

        // Try opening tags
        const openMatch = this.buffer.match(OPENING_TAG_RE)
        if (openMatch && openMatch.index !== undefined) {
            // Emit text before the tag
            if (openMatch.index > 0) {
                events.push({ type: 'text', content: this.buffer.slice(0, openMatch.index) })
            }

            const tagName = openMatch[1].toLowerCase()
            const attrs = this.parseAttributes(openMatch[2])
            const actionType = TAG_TO_ACTION[tagName]
            const action = this.buildAction(actionType, attrs)

            if (action) {
                this.currentTagName = tagName
                this.accumulatedContent = ''
                this.currentAction = action

                if (CONTENT_TAGS.has(tagName)) {
                    // Content tag — switch to content state
                    this.state = 'content'
                    events.push({ type: 'action_start', action: { ...this.currentAction } })
                } else {
                    // Non-content tags behave as self-closing actions.
                    events.push({ type: 'self_closing_action', action: { ...this.currentAction } })
                    this.currentAction = null
                }
            }

            this.buffer = this.buffer.slice(openMatch.index + openMatch[0].length)

            // If model emitted paired style for non-content tags:
            // <pigtex_read ...></pigtex_read>, consume the immediate closing tag.
            if (!CONTENT_TAGS.has(tagName)) {
                const closeTagRe = new RegExp(`^\\s*<\\/${tagName}\\s*>`, 'i')
                const closeTagMatch = this.buffer.match(closeTagRe)
                if (closeTagMatch) {
                    this.buffer = this.buffer.slice(closeTagMatch[0].length)
                }
            }
            return true
        }

        // No tag found — but buffer might contain a partial tag
        // Hold back tail in case a tag is being split across chunks.
        if (this.buffer.length > this.holdBackChars) {
            const safeText = this.buffer.slice(0, this.buffer.length - this.holdBackChars)
            // Only emit if there's no '<' in the safe portion that could be a tag start
            const lastAngle = safeText.lastIndexOf('<')
            if (lastAngle === -1) {
                events.push({ type: 'text', content: safeText })
                this.buffer = this.buffer.slice(safeText.length)
                return true
            }
            // There's a '<' — hold everything from it onward
            if (lastAngle > 0) {
                events.push({ type: 'text', content: safeText.slice(0, lastAngle) })
                this.buffer = this.buffer.slice(lastAngle)
                return true
            }
        }

        return false // Need more data
    }

    private processContentState(events: StreamingParserEvent[]): boolean {
        if (!this.currentTagName) {
            this.state = 'text'
            return true
        }

        const closeTagRe = new RegExp(`<\\/${this.currentTagName}\\s*>`, 'i')
        const closeTagMatch = this.buffer.match(closeTagRe)
        const closeIdx = closeTagMatch?.index ?? -1

        if (closeIdx >= 0 && closeTagMatch) {
            // Found closing tag — emit remaining content and action_end
            const finalContent = this.buffer.slice(0, closeIdx)
            if (finalContent.length > 0) {
                this.accumulatedContent += finalContent
                events.push({ type: 'content_chunk', content: finalContent })
            }

            if (this.currentAction) {
                this.currentAction.content = this.accumulatedContent
                events.push({ type: 'action_end', action: { ...this.currentAction } })
            }

            this.buffer = this.buffer.slice(closeIdx + closeTagMatch[0].length)
            this.state = 'text'
            this.currentAction = null
            this.currentTagName = ''
            this.accumulatedContent = ''
            return true
        }

        // No closing tag yet — stream buffered content (keep holdback for tag detection)
        const CLOSE_TAG_MAX_LEN = `</${this.currentTagName}>`.length + 8
        const safeLen = Math.max(0, this.buffer.length - CLOSE_TAG_MAX_LEN)
        if (safeLen > 0) {
            const contentChunk = this.buffer.slice(0, safeLen)
            this.accumulatedContent += contentChunk
            events.push({ type: 'content_chunk', content: contentChunk })
            this.buffer = this.buffer.slice(safeLen)
            return true
        }

        return false // Need more data
    }

    private parseAttributes(attrString: string): Record<string, string> {
        const attrs: Record<string, string> = {}
        let match: RegExpExecArray | null
        const re = new RegExp(ATTR_RE.source, 'g')
        while ((match = re.exec(attrString)) !== null) {
            const key = (match[1] || '').trim().toLowerCase()
            const value = (match[2] ?? match[3] ?? match[4] ?? '').trim()
            if (key && value) {
                attrs[key] = value
            }
        }
        return attrs
    }

    private pickAttr(attrs: Record<string, string>, candidates: string[]): string {
        for (const candidate of candidates) {
            const value = attrs[candidate.toLowerCase()]
            if (typeof value === 'string' && value.trim()) {
                return value.trim()
            }
        }
        return ''
    }

    private buildAction(
        actionType: StreamingAction['actionType'] | undefined,
        attrs: Record<string, string>
    ): StreamingAction | null {
        if (!actionType) return null

        const pathCandidates =
            actionType === 'list_directory'
                ? ['path', 'dir', 'dir_path', 'folder', 'folder_path']
                : actionType === 'rename_path'
                    ? ['path', 'source', 'source_path', 'from', 'old_path']
                    : ['path', 'file', 'file_path', 'target', 'target_path']
        const newPathCandidates = ['new_path', 'newpath', 'new_name', 'newname', 'to', 'destination', 'dest']
        const resolvedPath = this.pickAttr(attrs, pathCandidates)
        const resolvedNewPath = actionType === 'rename_path'
            ? this.pickAttr(attrs, newPathCandidates)
            : ''

        // Allow listing workspace root when path omitted.
        if (actionType !== 'list_directory' && !resolvedPath) return null
        if (actionType === 'rename_path' && !resolvedNewPath) return null

        return {
            actionType,
            path: resolvedPath || '',
            newPath: resolvedNewPath || undefined,
            content: '',
        }
    }
}

// ─── Helper: Convert streaming actions to ParsedAiFileAction ──

import type { ParsedAiFileAction } from './aiFileActions'

export function streamingActionToParsed(action: StreamingAction): ParsedAiFileAction {
    return {
        type: action.actionType,
        path: action.path,
        newPath: action.newPath,
        content: action.content,
        overwrite: action.actionType === 'write_file',
    }
}
