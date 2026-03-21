import { StreamingActionParser, streamingActionToParsed } from './streamingActionParser'

type RawAiFileAction = {
    type?: string
    path?: string
    new_path?: string
    new_name?: string
    content?: string
    overwrite?: boolean
}

export type ParsedAiFileAction = {
    type: string
    path: string
    newPath?: string
    content?: string
    overwrite?: boolean
}

type DirectorySnapshotEntry = {
    name: string
    type: 'file' | 'directory'
}

type DirectorySnapshot = {
    entries: DirectorySnapshotEntry[]
    capturedAt: number
}

export interface AiFileExecutionContext {
    directorySnapshots: Record<string, DirectorySnapshot>
}

export interface AiFileActionParseResult {
    actions: ParsedAiFileAction[]
    errors: string[]
}

type ElectronFsAPI = NonNullable<Window['electronAPI']>

export interface AiFileActionResult {
    applied: number
    logs: string[]
    errors: string[]
    renamed: Array<{ oldPath: string; newPath: string }>
    deleted: Array<{ targetPath: string; isDirectory: boolean }>
    read: Array<{
        targetPath: string
        size: number
        mtimeMs: number
        preview: string
        truncated: boolean
    }>
    list?: Array<{
        targetPath: string
        totalEntries: number
        preview: string
        truncated: boolean
        exactEntries?: string[]
    }>
}

export interface AiFileActionProgressEvent {
    index: number
    total: number
    action: ParsedAiFileAction
    stage: 'start' | 'progress' | 'success' | 'error'
    message: string
}

export type AiFileExecutionMode = 'single_step' | 'multi_step'

const ACTION_ALIASES: Record<string, string> = {
    create_file: 'create_file',
    new_file: 'create_file',
    write_file: 'write_file',
    update_file: 'write_file',
    edit_file: 'write_file',
    apply_diff: 'apply_diff',
    patch_file: 'apply_diff',
    patch: 'apply_diff',
    create_folder: 'create_folder',
    create_directory: 'create_folder',
    new_folder: 'create_folder',
    delete_file: 'delete_file',
    remove_file: 'delete_file',
    delete_folder: 'delete_folder',
    remove_folder: 'delete_folder',
    delete_directory: 'delete_folder',
    delete_path: 'delete_path',
    rename: 'rename_path',
    rename_file: 'rename_path',
    rename_folder: 'rename_path',
    rename_path: 'rename_path',
    read_file: 'read_file',
    read: 'read_file',
    read_text_file: 'read_file',
    cat: 'read_file',
    open_file: 'read_file',
    view_file: 'read_file',
    inspect_file: 'read_file',
    list_directory: 'list_directory',
    list_dir: 'list_directory',
    ls: 'list_directory',
    list_files: 'list_directory',
    list_folder: 'list_directory',
    tree: 'list_directory'
}

const MAX_READ_PREVIEW_CHARS = 1200
const MAX_LIST_PREVIEW_ITEMS = 80
const ACTION_PROGRESS_HEARTBEAT_MS = 1200
const SEARCH_REPLACE_BLOCK_RE = /<<<<<<<\s*SEARCH\s*\r?\n([\s\S]*?)\r?\n=======\r?\n([\s\S]*?)\r?\n>>>>>>>\s*REPLACE/g

const extractPigtexFsCodeBlocks = (content: string): string[] => {
    const matches = Array.from(content.matchAll(/```pigtex_fs\s*([\s\S]*?)```/gi))
    return matches
        .map((match) => match[1]?.trim() || '')
        .filter((block) => block.length > 0)
}

const extractLegacyXmlActions = (content: string): RawAiFileAction[] => {
    const actions: RawAiFileAction[] = []

    const readMatches = Array.from(content.matchAll(/<read_code>\s*<path>\s*([\s\S]*?)\s*<\/path>\s*<\/read_code>/gi))
    for (const match of readMatches) {
        const path = (match[1] || '').trim()
        if (!path) continue
        actions.push({
            type: 'read_file',
            path
        })
    }

    const writeMatches = Array.from(content.matchAll(
        /<write_code>\s*<path>\s*([\s\S]*?)\s*<\/path>\s*<content>\s*([\s\S]*?)\s*<\/content>\s*<\/write_code>/gi
    ))
    for (const match of writeMatches) {
        const path = (match[1] || '').trim()
        if (!path) continue
        actions.push({
            type: 'write_file',
            path,
            content: match[2] || ''
        })
    }

    return actions
}

const sanitizeRelativePath = (inputPath: string): string => {
    const trimmed = inputPath.trim()
    const slashNormalized = trimmed.replace(/\\/g, '/')
    if (slashNormalized.startsWith('/')) {
        throw new Error('Absolute paths are not allowed')
    }
    const normalized = slashNormalized.replace(/^(?:\.\/)+/, '')

    if (!normalized) {
        throw new Error('Path is empty')
    }
    if (/^[a-zA-Z]:\//.test(normalized)) {
        throw new Error('Absolute paths are not allowed')
    }

    const segments = normalized.split('/').filter(Boolean)
    if (segments.length === 0) {
        throw new Error('Path is empty')
    }
    if (segments.some((segment) => segment === '.' || segment === '..')) {
        throw new Error('Path cannot contain "." or ".."')
    }

    return segments.join('/')
}

const splitRelativePath = (relativePath: string) => {
    const lastSlash = relativePath.lastIndexOf('/')
    if (lastSlash === -1) {
        return { parent: '', name: relativePath }
    }
    return {
        parent: relativePath.slice(0, lastSlash),
        name: relativePath.slice(lastSlash + 1)
    }
}

const getPathSeparator = (rootPath: string) => (rootPath.includes('\\') ? '\\' : '/')

const joinAbsolutePath = (rootPath: string, relativePath: string) => {
    const sep = getPathSeparator(rootPath)
    const rel = relativePath.split('/').join(sep)
    if (!rel) return rootPath
    const root = rootPath.endsWith(sep) ? rootPath.slice(0, -1) : rootPath
    return `${root}${sep}${rel}`
}

const formatExactEntryName = (entry: DirectorySnapshotEntry) =>
    entry.type === 'directory' ? `${entry.name}/` : entry.name

const getSnapshotParentAbsolutePath = (rootPath: string, relativePath: string) => {
    const { parent } = splitRelativePath(relativePath)
    return parent ? joinAbsolutePath(rootPath, parent) : rootPath
}

const listSnapshotEntries = (entries: DirectorySnapshotEntry[], maxItems: number = 20) => {
    if (entries.length === 0) return '(empty folder)'
    const rendered = entries.slice(0, maxItems).map(formatExactEntryName)
    if (entries.length > maxItems) {
        rendered.push(`... (+${entries.length - maxItems} more)`)
    }
    return rendered.join(', ')
}

const rememberDirectorySnapshot = (
    executionContext: AiFileExecutionContext | undefined,
    directoryPath: string,
    entries: DirectorySnapshotEntry[]
) => {
    if (!executionContext) return
    executionContext.directorySnapshots[directoryPath] = {
        entries,
        capturedAt: Date.now()
    }
}

const clearDirectorySnapshot = (
    executionContext: AiFileExecutionContext | undefined,
    directoryPath: string
) => {
    if (!executionContext) return
    delete executionContext.directorySnapshots[directoryPath]
}

const isMissingPathMessage = (message: string) => {
    const normalized = message.toLowerCase()
    return normalized.includes('enoent')
        || normalized.includes('not found')
        || normalized.includes('no such file or directory')
}

const buildSnapshotPresenceError = (
    relativePath: string,
    targetPath: string,
    parentPath: string,
    snapshot: DirectorySnapshot
) => {
    return [
        `Path is not present in the latest directory listing: ${relativePath} (${targetPath})`,
        `Listed folder: ${parentPath}`,
        `Exact entries: ${listSnapshotEntries(snapshot.entries)}`,
        'Use only exact entry names from the listing, or list the folder again instead of guessing.'
    ].join('. ')
}

const buildSnapshotTypeMismatchError = (
    relativePath: string,
    targetPath: string,
    parentPath: string,
    actualType: 'file' | 'directory',
    expectedType: 'file' | 'directory',
    recoveryHint: string
) => {
    return [
        `Target is listed as a ${actualType}, not a ${expectedType}: ${relativePath} (${targetPath})`,
        `Listed folder: ${parentPath}`,
        recoveryHint
    ].join('. ')
}

const buildPathNotFoundError = (
    kindLabel: 'File' | 'Folder' | 'Path',
    relativePath: string,
    targetPath: string,
    recoveryHint: string
) => {
    return [
        `${kindLabel} not found: ${relativePath || '.'} (${targetPath})`,
        recoveryHint
    ].join('. ')
}

const normalizeReadFileError = (
    relativePath: string,
    targetPath: string,
    error: unknown
) => {
    const rawMessage = error instanceof Error ? error.message : String(error)
    if (isMissingPathMessage(rawMessage)) {
        return buildPathNotFoundError(
            'File',
            relativePath,
            targetPath,
            'If the path is uncertain, list the folder first and use an exact entry name from the tool result.'
        )
    }
    return rawMessage
}

const isDoubledRootNamePath = (relativePath: string, targetPath: string): boolean => {
    if (!relativePath || relativePath.includes('/')) return false
    const fwdEnding = `/${relativePath}/${relativePath}`
    const bkwEnding = `\\${relativePath}\\${relativePath}`
    return targetPath.endsWith(fwdEnding) || targetPath.endsWith(bkwEnding)
}

const normalizeListDirectoryError = (
    relativePath: string,
    targetPath: string,
    error: unknown
) => {
    const rawMessage = error instanceof Error ? error.message : String(error)
    if (isMissingPathMessage(rawMessage)) {
        if (isDoubledRootNamePath(relativePath, targetPath)) {
            return `"${relativePath}" is the workspace root folder name — it is not a subfolder of itself. To list the root folder, use path: "."`
        }
        return buildPathNotFoundError(
            'Folder',
            relativePath,
            targetPath,
            'If the path is uncertain, list its parent folder first and use an exact entry name from the tool result.'
        )
    }
    return rawMessage
}

const normalizePathMutationError = (
    kindLabel: 'File' | 'Folder' | 'Path',
    relativePath: string,
    targetPath: string,
    error: unknown
) => {
    const rawMessage = error instanceof Error ? error.message : String(error)
    if (isMissingPathMessage(rawMessage)) {
        return buildPathNotFoundError(
            kindLabel,
            relativePath,
            targetPath,
            'List the parent folder again before retrying, and use an exact entry name from the tool result.'
        )
    }
    return rawMessage
}

export const createAiFileExecutionContext = (): AiFileExecutionContext => ({
    directorySnapshots: {}
})

export const invalidateAiFileExecutionContextForAction = (
    executionContext: AiFileExecutionContext | undefined,
    rootPath: string,
    action: Pick<ParsedAiFileAction, 'type' | 'path' | 'newPath'>
) => {
    if (!executionContext) return

    const actionPath = (action.path || '').trim()
    const newActionPath = (action.newPath || '').trim()
    const parentPath = actionPath ? getSnapshotParentAbsolutePath(rootPath, actionPath) : rootPath
    const targetPath = actionPath ? joinAbsolutePath(rootPath, actionPath) : rootPath

    switch (action.type) {
        case 'create_file':
        case 'write_file':
        case 'delete_file':
        case 'delete_path':
            clearDirectorySnapshot(executionContext, parentPath)
            return
        case 'create_folder':
        case 'delete_folder':
            clearDirectorySnapshot(executionContext, parentPath)
            clearDirectorySnapshot(executionContext, targetPath)
            return
        case 'rename_path': {
            clearDirectorySnapshot(executionContext, parentPath)
            if (newActionPath) {
                clearDirectorySnapshot(executionContext, getSnapshotParentAbsolutePath(rootPath, newActionPath))
            }
            clearDirectorySnapshot(executionContext, targetPath)
            return
        }
        default:
            return
    }
}

const validateActionAgainstSnapshots = (
    action: ParsedAiFileAction,
    rootPath: string,
    executionContext: AiFileExecutionContext | undefined
) => {
    if (!executionContext) return
    if (!action.path) return

    const parentPath = getSnapshotParentAbsolutePath(rootPath, action.path)
    const snapshot = executionContext.directorySnapshots[parentPath]
    if (!snapshot) return

    const { name } = splitRelativePath(action.path)
    const entry = snapshot.entries.find(candidate => candidate.name === name)
    const targetPath = joinAbsolutePath(rootPath, action.path)

    if (!entry) {
        throw new Error(buildSnapshotPresenceError(action.path, targetPath, parentPath, snapshot))
    }

    switch (action.type) {
        case 'read_file':
        case 'apply_diff':
        case 'delete_file':
            if (entry.type !== 'file') {
                throw new Error(buildSnapshotTypeMismatchError(
                    action.path,
                    targetPath,
                    parentPath,
                    entry.type,
                    'file',
                    'Use the correct file path or list that directory first.'
                ))
            }
            return
        case 'list_directory':
        case 'delete_folder':
            if (entry.type !== 'directory') {
                throw new Error(buildSnapshotTypeMismatchError(
                    action.path,
                    targetPath,
                    parentPath,
                    entry.type,
                    'directory',
                    'Use the correct folder path or list the parent folder again first.'
                ))
            }
            return
        case 'delete_path':
        case 'rename_path':
            return
        default:
            return
    }
}

const ensureDirectoryChain = async (rootPath: string, relativeDirPath: string) => {
    if (!relativeDirPath) return
    if (!window.electronAPI?.createFolder) {
        throw new Error('Filesystem API is unavailable')
    }

    const segments = relativeDirPath.split('/').filter(Boolean)
    let currentParent = rootPath

    for (let index = 0; index < segments.length; index += 1) {
        const segment = segments[index]
        try {
            await window.electronAPI.createFolder({
                parentPath: currentParent,
                folderName: segment
            })
        } catch (error) {
            const message =
                error instanceof Error ? error.message.toLowerCase() : String(error).toLowerCase()
            if (!message.includes('exist')) {
                throw error
            }
        }

        currentParent = joinAbsolutePath(rootPath, segments.slice(0, index + 1).join('/'))
    }
}

const normalizeAction = (action: RawAiFileAction): ParsedAiFileAction => {
    const normalizedType = ACTION_ALIASES[(action.type || '').trim().toLowerCase()]
    if (!normalizedType) {
        throw new Error(`Unsupported action type "${action.type || ''}"`)
    }
    if (!action.path && normalizedType !== 'list_directory') {
        throw new Error(`Action "${normalizedType}" is missing "path"`)
    }

    const rawPath = (action.path || '').trim()
    const normalizedPath =
        normalizedType === 'list_directory' && (!rawPath || rawPath === '.' || rawPath === '/' || rawPath === './')
            ? ''
            : sanitizeRelativePath(rawPath)
    const normalized: ParsedAiFileAction = {
        type: normalizedType,
        path: normalizedPath
    }

    if (typeof action.content === 'string') {
        normalized.content = action.content
    }
    if (typeof action.overwrite === 'boolean') {
        normalized.overwrite = action.overwrite
    }
    if (typeof action.new_path === 'string' && action.new_path.trim()) {
        normalized.newPath = sanitizeRelativePath(action.new_path)
    } else if (typeof action.new_name === 'string' && action.new_name.trim()) {
        normalized.newPath = action.new_name.trim()
    }

    return normalized
}

const normalizeActionForExecution = (action: ParsedAiFileAction): ParsedAiFileAction =>
    normalizeAction({
        type: action.type,
        path: action.path,
        new_path: action.newPath,
        content: action.content,
        overwrite: action.overwrite
    })

const parseActionList = (content: string): ParsedAiFileAction[] => {
    // 1. Preferred: parse XML action tags with the streaming parser for
    // consistency between live-stream execution and post-stream fallback.
    const streamParser = new StreamingActionParser()
    const streamEvents = [...streamParser.feed(content), ...streamParser.flush()]
    const streamActions = streamEvents
        .filter((event): event is
            | { type: 'action_end'; action: import('./streamingActionParser').StreamingAction }
            | { type: 'self_closing_action'; action: import('./streamingActionParser').StreamingAction } =>
            event.type === 'action_end' || event.type === 'self_closing_action'
        )
        .map(event => streamingActionToParsed(event.action))
    if (streamActions.length > 0) {
        return streamActions.map(action => normalizeAction({
            type: action.type,
            path: action.path,
            new_path: action.newPath,
            content: action.content,
            overwrite: action.overwrite
        }))
    }

    // 2. Fallback: pigtex_fs JSON code blocks
    const pigtexBlocks = extractPigtexFsCodeBlocks(content)
    if (pigtexBlocks.length > 0) {
        const selectedBlock = pigtexBlocks.find(block => block.trim().length > 0) || pigtexBlocks[0]

        let payload: unknown
        try {
            payload = JSON.parse(selectedBlock)
        } catch {
            throw new Error('Invalid JSON in pigtex_fs block')
        }

        if (!payload || typeof payload !== 'object') {
            throw new Error('pigtex_fs block must be a JSON object')
        }

        const actions = (payload as { actions?: unknown }).actions
        if (!Array.isArray(actions)) {
            throw new Error('pigtex_fs block must contain an "actions" array')
        }

        return actions.map((entry) => normalizeAction(entry as RawAiFileAction))
    }

    // 3. Legacy: <read_code>/<write_code> XML tags
    const legacyActions = extractLegacyXmlActions(content)
    if (legacyActions.length > 0) {
        return legacyActions.map((entry) => normalizeAction(entry))
    }

    return []
}

const safeActionLabel = (action: ParsedAiFileAction) => `${action.type}(${action.path})`

const formatPathLog = (label: string, relativePath: string, absolutePath: string) =>
    `${label}: ${relativePath} (${absolutePath})`

const describeAction = (action: ParsedAiFileAction): string => {
    switch (action.type) {
        case 'read_file':
            return `Read ${action.path}`
        case 'list_directory':
            return `List directory ${action.path || '.'}`
        case 'apply_diff':
            return `Patch file ${action.path}`
        case 'create_file':
            return `Create file ${action.path}`
        case 'write_file':
            return `Update file ${action.path}`
        case 'create_folder':
            return `Create folder ${action.path}`
        case 'delete_file':
            return `Delete file ${action.path}`
        case 'delete_folder':
            return `Delete folder ${action.path}`
        case 'delete_path':
            return `Delete path ${action.path}`
        case 'rename_path':
            return `Rename ${action.path} -> ${action.newPath || '(missing new_path)'}`
        default:
            return `${action.type} ${action.path}`
    }
}

const buildReadPreview = (content: string, maxChars: number = MAX_READ_PREVIEW_CHARS) => {
    if (content.length <= maxChars) {
        return {
            preview: content,
            truncated: false
        }
    }

    return {
        preview: `${content.slice(0, maxChars)}\n... (truncated)`,
        truncated: true
    }
}

const buildDirectoryPreview = (
    entries: Array<{ name: string; type: 'file' | 'directory'; size: number }>,
    maxItems: number = MAX_LIST_PREVIEW_ITEMS
) => {
    const sorted = [...entries].sort((a, b) => {
        if (a.type !== b.type) return a.type === 'directory' ? -1 : 1
        return a.name.localeCompare(b.name)
    })
    const truncated = sorted.length > maxItems
    const selected = truncated ? sorted.slice(0, maxItems) : sorted
    const lines = selected.map(entry =>
        entry.type === 'directory'
            ? `[DIR] ${entry.name}`
            : `[FILE] ${entry.name} (${entry.size} bytes)`
    )
    if (truncated) {
        lines.push(`... (${sorted.length - maxItems} more entries omitted)`)
    }
    return {
        preview: lines.join('\n'),
        truncated
    }
}

const parseSearchReplaceBlocks = (patchText: string) => {
    const blocks: Array<{ search: string; replace: string }> = []
    let match: RegExpExecArray | null
    const regex = new RegExp(SEARCH_REPLACE_BLOCK_RE.source, 'g')
    while ((match = regex.exec(patchText)) !== null) {
        blocks.push({
            search: match[1] ?? '',
            replace: match[2] ?? ''
        })
    }
    return blocks
}

const applySearchReplacePatch = (originalContent: string, patchText: string) => {
    const blocks = parseSearchReplaceBlocks(patchText)
    if (blocks.length === 0) {
        throw new Error('apply_diff requires at least one SEARCH/REPLACE block')
    }

    let nextContent = originalContent
    for (const block of blocks) {
        if (!block.search) {
            throw new Error('SEARCH block cannot be empty')
        }

        const index = nextContent.indexOf(block.search)
        if (index < 0) {
            throw new Error('SEARCH block not found in target file')
        }

        nextContent = `${nextContent.slice(0, index)}${block.replace}${nextContent.slice(index + block.search.length)}`
    }

    return {
        nextContent,
        blockCount: blocks.length
    }
}

export const parseAiFileActions = (assistantContent: string): AiFileActionParseResult => {
    try {
        const actions = parseActionList(assistantContent)
        return { actions, errors: [] }
    } catch (error) {
        return {
            actions: [],
            errors: [error instanceof Error ? error.message : 'Invalid AI action JSON']
        }
    }
}

export const parseAiFileActionEntries = (entries: unknown[]): AiFileActionParseResult => {
    try {
        return {
            actions: entries.map((entry) => normalizeAction(entry as RawAiFileAction)),
            errors: []
        }
    } catch (error) {
        return {
            actions: [],
            errors: [error instanceof Error ? error.message : 'Invalid AI action payload']
        }
    }
}

const executeParsedAiFileActions = async (
    actions: ParsedAiFileAction[],
    rootPath: string,
    electronAPI: ElectronFsAPI,
    onProgress?: (event: AiFileActionProgressEvent) => void,
    executionContext?: AiFileExecutionContext
): Promise<AiFileActionResult | null> => {
    if (actions.length === 0) return null

    const result: AiFileActionResult = {
        applied: 0,
        logs: [],
        errors: [],
        renamed: [],
        deleted: [],
        read: [],
        list: []
    }

    for (let index = 0; index < actions.length; index += 1) {
        const action = actions[index]
        const actionStartedAt = Date.now()
        let progressHeartbeat: ReturnType<typeof setInterval> | null = null
        onProgress?.({
            index,
            total: actions.length,
            action,
            stage: 'start',
            message: describeAction(action)
        })
        if (onProgress) {
            progressHeartbeat = setInterval(() => {
                const elapsedSeconds = Math.max(1, Math.floor((Date.now() - actionStartedAt) / 1000))
                onProgress({
                    index,
                    total: actions.length,
                    action,
                    stage: 'progress',
                    message: `${describeAction(action)} (${elapsedSeconds}s)`
                })
            }, ACTION_PROGRESS_HEARTBEAT_MS)
        }
        try {
            switch (action.type) {
                case 'create_folder': {
                    await ensureDirectoryChain(rootPath, action.path)
                    const targetPath = joinAbsolutePath(rootPath, action.path)
                    invalidateAiFileExecutionContextForAction(executionContext, rootPath, action)
                    result.applied += 1
                    result.logs.push(formatPathLog('Created folder', action.path, targetPath))
                    break
                }
                case 'create_file': {
                    const { parent, name } = splitRelativePath(action.path)
                    await ensureDirectoryChain(rootPath, parent)
                    const parentAbsolute = parent ? joinAbsolutePath(rootPath, parent) : rootPath
                    const targetPath = joinAbsolutePath(rootPath, action.path)
                    const content = action.content ?? ''
                    let createOutcome: 'created' | 'overwritten' | 'already_exists_same_content' = 'created'

                    try {
                        await electronAPI.createFile({
                            parentPath: parentAbsolute,
                            fileName: name,
                            content
                        })
                    } catch (error) {
                        const message =
                            error instanceof Error ? error.message.toLowerCase() : String(error).toLowerCase()
                        if (message.includes('exist') && action.overwrite) {
                            await electronAPI.writeFile({
                                filePath: joinAbsolutePath(rootPath, action.path),
                                content
                            })
                            createOutcome = 'overwritten'
                        } else if (message.includes('exist') && electronAPI.readFile) {
                            const existing = await electronAPI.readFile(targetPath)
                            if (existing.content === content) {
                                createOutcome = 'already_exists_same_content'
                            } else {
                                throw error
                            }
                        } else {
                            throw error
                        }
                    }

                    invalidateAiFileExecutionContextForAction(executionContext, rootPath, action)
                    result.applied += 1
                    if (createOutcome === 'overwritten') {
                        result.logs.push(formatPathLog('Updated file (overwrite)', action.path, targetPath))
                    } else if (createOutcome === 'already_exists_same_content') {
                        result.logs.push(formatPathLog('Skipped create (already exists, same content)', action.path, targetPath))
                    } else {
                        result.logs.push(formatPathLog('Created file', action.path, targetPath))
                    }
                    break
                }
                case 'write_file': {
                    const { parent } = splitRelativePath(action.path)
                    await ensureDirectoryChain(rootPath, parent)
                    const targetPath = joinAbsolutePath(rootPath, action.path)
                    await electronAPI.writeFile({
                        filePath: targetPath,
                        content: action.content ?? ''
                    })
                    invalidateAiFileExecutionContextForAction(executionContext, rootPath, action)
                    result.applied += 1
                    result.logs.push(formatPathLog('Updated file', action.path, targetPath))
                    break
                }
                case 'apply_diff': {
                    if (!electronAPI.readFile || !electronAPI.writeFile) {
                        throw new Error('Patch APIs are unavailable')
                    }
                    validateActionAgainstSnapshots(action, rootPath, executionContext)
                    const targetPath = joinAbsolutePath(rootPath, action.path)
                    const currentFile = await electronAPI.readFile(targetPath)
                    const patchText = action.content ?? ''
                    const patched = applySearchReplacePatch(currentFile.content, patchText)
                    await electronAPI.writeFile({
                        filePath: targetPath,
                        content: patched.nextContent
                    })
                    result.applied += 1
                    result.logs.push(
                        `${formatPathLog('Patched file', action.path, targetPath)} (${patched.blockCount} block${patched.blockCount > 1 ? 's' : ''})`
                    )
                    break
                }
                case 'delete_file': {
                    const targetPath = joinAbsolutePath(rootPath, action.path)
                    validateActionAgainstSnapshots(action, rootPath, executionContext)
                    try {
                        await electronAPI.deletePath({ targetPath })
                    } catch (error) {
                        throw new Error(normalizePathMutationError('File', action.path, targetPath, error))
                    }
                    invalidateAiFileExecutionContextForAction(executionContext, rootPath, action)
                    result.applied += 1
                    result.deleted.push({ targetPath, isDirectory: false })
                    result.logs.push(formatPathLog('Deleted file', action.path, targetPath))
                    break
                }
                case 'delete_folder': {
                    const targetPath = joinAbsolutePath(rootPath, action.path)
                    validateActionAgainstSnapshots(action, rootPath, executionContext)
                    try {
                        await electronAPI.deletePath({ targetPath })
                    } catch (error) {
                        throw new Error(normalizePathMutationError('Folder', action.path, targetPath, error))
                    }
                    invalidateAiFileExecutionContextForAction(executionContext, rootPath, action)
                    result.applied += 1
                    result.deleted.push({ targetPath, isDirectory: true })
                    result.logs.push(formatPathLog('Deleted folder', action.path, targetPath))
                    break
                }
                case 'delete_path': {
                    const targetPath = joinAbsolutePath(rootPath, action.path)
                    validateActionAgainstSnapshots(action, rootPath, executionContext)
                    try {
                        await electronAPI.deletePath({ targetPath })
                    } catch (error) {
                        throw new Error(normalizePathMutationError('Path', action.path, targetPath, error))
                    }
                    invalidateAiFileExecutionContextForAction(executionContext, rootPath, action)
                    result.applied += 1
                    result.deleted.push({ targetPath, isDirectory: false })
                    result.logs.push(formatPathLog('Deleted path', action.path, targetPath))
                    break
                }
                case 'rename_path': {
                    if (!electronAPI.renamePath) {
                        throw new Error('Rename API is unavailable')
                    }
                    if (!action.newPath) {
                        throw new Error('rename_path requires "new_path" or "new_name"')
                    }

                    const oldParts = splitRelativePath(action.path)
                    const renamedTarget = action.newPath.includes('/')
                        ? action.newPath
                        : `${oldParts.parent ? `${oldParts.parent}/` : ''}${action.newPath}`
                    const newParts = splitRelativePath(renamedTarget)

                    if (oldParts.parent !== newParts.parent) {
                        throw new Error('Renaming across folders is not supported yet')
                    }
                    if (!newParts.name || newParts.name.includes('/')) {
                        throw new Error('Invalid new name')
                    }

                    const oldAbsolutePath = joinAbsolutePath(rootPath, action.path)
                    validateActionAgainstSnapshots(action, rootPath, executionContext)
                    let renamed: Awaited<ReturnType<ElectronFsAPI['renamePath']>>
                    try {
                        renamed = await electronAPI.renamePath({
                            targetPath: oldAbsolutePath,
                            newName: newParts.name
                        })
                    } catch (error) {
                        throw new Error(normalizePathMutationError('Path', action.path, oldAbsolutePath, error))
                    }
                    invalidateAiFileExecutionContextForAction(executionContext, rootPath, {
                        type: action.type,
                        path: action.path,
                        newPath: renamedTarget
                    })
                    result.applied += 1
                    result.renamed.push({ oldPath: oldAbsolutePath, newPath: renamed.path })
                    result.logs.push(`Renamed: ${action.path} -> ${renamedTarget} (${oldAbsolutePath} -> ${renamed.path})`)
                    break
                }
                case 'read_file': {
                    if (!electronAPI.readFile) {
                        throw new Error('Read file API is unavailable')
                    }
                    validateActionAgainstSnapshots(action, rootPath, executionContext)
                    const targetPath = joinAbsolutePath(rootPath, action.path)
                    let readResult: Awaited<ReturnType<ElectronFsAPI['readFile']>>
                    try {
                        readResult = await electronAPI.readFile(targetPath)
                    } catch (error) {
                        throw new Error(normalizeReadFileError(action.path, targetPath, error))
                    }
                    const preview = buildReadPreview(readResult.content)

                    result.applied += 1
                    result.read.push({
                        targetPath,
                        size: readResult.size,
                        mtimeMs: readResult.mtimeMs,
                        preview: preview.preview,
                        truncated: preview.truncated
                    })
                    result.logs.push(formatPathLog('Read file', action.path, targetPath))
                    break
                }
                case 'list_directory': {
                    if (!electronAPI.listDirectory) {
                        throw new Error('List directory API is unavailable')
                    }
                    if (action.path) {
                        validateActionAgainstSnapshots(action, rootPath, executionContext)
                    }
                    const targetPath = action.path
                        ? joinAbsolutePath(rootPath, action.path)
                        : rootPath
                    let entries: Awaited<ReturnType<ElectronFsAPI['listDirectory']>>
                    try {
                        entries = await electronAPI.listDirectory({
                            rootPath,
                            dirPath: targetPath
                        })
                    } catch (error) {
                        throw new Error(normalizeListDirectoryError(action.path || '.', targetPath, error))
                    }
                    const exactEntries = entries.map(entry => ({
                        name: entry.name,
                        type: entry.type
                    }))
                    rememberDirectorySnapshot(executionContext, targetPath, exactEntries)
                    const listing = buildDirectoryPreview(entries)
                    result.applied += 1
                    result.list = result.list || []
                    result.list.push({
                        targetPath,
                        totalEntries: entries.length,
                        preview: listing.preview || '(empty folder)',
                        truncated: listing.truncated,
                        exactEntries: exactEntries.map(formatExactEntryName)
                    })
                    result.logs.push(formatPathLog('Listed directory', action.path || '.', targetPath))
                    break
                }
                default:
                    throw new Error(`Unsupported action type "${action.type}"`)
            }
            onProgress?.({
                index,
                total: actions.length,
                action,
                stage: 'success',
                message: result.logs[result.logs.length - 1] || describeAction(action)
            })
        } catch (error) {
            const message = error instanceof Error ? error.message : String(error)
            const errorLine = `${safeActionLabel(action)}: ${message}`
            result.errors.push(errorLine)
            onProgress?.({
                index,
                total: actions.length,
                action,
                stage: 'error',
                message: errorLine
            })
        } finally {
            if (progressHeartbeat) {
                clearInterval(progressHeartbeat)
            }
        }
    }

    return result
}

export const executeAiFileActionsFromParsed = async (
    actions: ParsedAiFileAction[],
    rootPath: string | null,
    onProgress?: (event: AiFileActionProgressEvent) => void,
    executionContext?: AiFileExecutionContext
): Promise<AiFileActionResult | null> => {
    if (!rootPath) return null
    let normalizedActions: ParsedAiFileAction[]
    try {
        normalizedActions = actions.map(action => normalizeActionForExecution(action))
    } catch (error) {
        return {
            applied: 0,
            logs: [],
            errors: [error instanceof Error ? error.message : 'Invalid file action payload'],
            renamed: [],
            deleted: [],
            read: [],
            list: []
        }
    }
    const electronAPI = window.electronAPI
    if (!electronAPI) {
        return {
            applied: 0,
            logs: [],
            errors: ['Filesystem API is unavailable in this runtime'],
            renamed: [],
            deleted: [],
            read: [],
            list: []
        }
    }
    return executeParsedAiFileActions(normalizedActions, rootPath, electronAPI, onProgress, executionContext)
}

export const executeAiFileActions = async (
    assistantContent: string,
    rootPath: string | null,
    executionContext?: AiFileExecutionContext
): Promise<AiFileActionResult | null> => {
    const parsed = parseAiFileActions(assistantContent)
    if (parsed.errors.length > 0) {
        return {
            applied: 0,
            logs: [],
            errors: parsed.errors,
            renamed: [],
            deleted: [],
            read: [],
            list: []
        }
    }

    return executeAiFileActionsFromParsed(parsed.actions, rootPath, undefined, executionContext)
}

export const buildAiFileRuntimeInstruction = (
    rootPath: string,
    options?: { requireUserApproval?: boolean; executionMode?: AiFileExecutionMode }
) => {
    const requireUserApproval = options?.requireUserApproval ?? true
    const executionMode = options?.executionMode || 'single_step'

    return [
        requireUserApproval
            ? 'You can perform file/folder operations for the opened local workspace only after user approval.'
            : 'You can perform file/folder operations for the opened local workspace. Actions are auto-approved and executed immediately.',
        `Workspace root: ${rootPath}`,
        '',
        '## File Operations — XML Tag Format (PREFERRED)',
        'Use XML tags to write/create files. Content is output as raw text (no JSON escaping needed):',
        '',
        'Write or create a file (content streams in real-time):',
        '<pigtex_write path="relative/path.ts">',
        'file content here — raw text, no escaping needed',
        '</pigtex_write>',
        '',
        'Patch an existing file (preferred for targeted edits):',
        '<pigtex_patch path="relative/path.ts">',
        '<<<<<<< SEARCH',
        'exact old text to replace',
        '=======',
        'new replacement text',
        '>>>>>>> REPLACE',
        '</pigtex_patch>',
        '',
        'Read a file:',
        '<pigtex_read path="relative/path.ts" />',
        '',
        'List a folder:',
        '<pigtex_ls path="relative/folder" />',
        '',
        'Delete a file or folder:',
        '<pigtex_delete path="relative/path" />',
        '',
        'Create a folder:',
        '<pigtex_mkdir path="relative/folder" />',
        '',
        'Rename:',
        '<pigtex_rename path="old/path" new_path="new/path" />',
        '',
        '## Fallback — JSON Format',
        'If you prefer batch operations, use a pigtex_fs code block:',
        '```pigtex_fs',
        '{"actions":[{"type":"read_file|list_directory|write_file|apply_diff|create_folder|delete_file|rename_path","path":"relative/path","content":"optional","new_path":"optional"}]}',
        '```',
        '',
        '## Rules',
        '- ALWAYS use paths relative to workspace root.',
        '- Never use absolute paths.',
        '- IMPORTANT for streaming UX: when writing/patching, emit the opening XML tag immediately (first output tokens), then stream content progressively, then close the tag at the end.',
        '- Do not output any explanation/prose before the first XML action tag when a filesystem action is required.',
        '- For writing/creating files, PREFER the XML tag format (<pigtex_write>) over pigtex_fs JSON — it avoids JSON escaping issues and enables real-time streaming.',
        '- For small/targeted edits, PREFER <pigtex_patch> with SEARCH/REPLACE blocks instead of rewriting whole files.',
        '- Write the COMPLETE file content inside <pigtex_write> tags. Do not use partial edits or diffs.',
        requireUserApproval
            ? '- Tool-call protocol: if you need to inspect files first, output a <pigtex_read> tag and wait for user approval + tool result.'
            : '- Tool-call protocol: if you need to inspect files first, output a <pigtex_read> tag and wait for tool result.',
        executionMode === 'multi_step'
            ? '- Multi-step mode: after tool results, request another file operation only if additional changes are strictly necessary.'
            : '- Single-step mode: after one successful file action batch, provide final user-facing text and do not emit more file operations.',
        '- Never repeat a successful create/delete/rename action from previous tool results.',
        '- If a file already exists and you need to change content, use <pigtex_write> (it handles both create and overwrite).',
        '- Use <pigtex_read> when the user asks to view/check a file before editing.',
        '- Use <pigtex_ls> to inspect folders before choosing exact file paths.',
        '- After a directory listing, use only the exact file or folder names returned by the tool result. This rule also applies to follow-up folder listings inside that directory.',
        '- Never guess conventional names such as index.html, main.py, app.js, or guessed subfolders unless they were explicitly listed.',
        '- If read_file fails because a path does not exist, do not retry another guessed filename in the same folder. Use the exact listing entries or list the folder again.',
        '- Always provide exact paths. If path is ambiguous, ask the user instead of guessing.',
        '- If no filesystem action is requested, do not output any file operation tags.'
    ].join('\n')
}
