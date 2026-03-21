import type {
    AiFileActionProgressEvent,
    AiFileActionResult,
    ParsedAiFileAction
} from '../../utils/aiFileActions'

export interface FileAgentActionTracker {
    executedActionBatchSignatures: Set<string>
    completedRepeatSensitiveActionKeys: Set<string>
    blockedRetryActionKeys: Set<string>
}

export interface FilterExecutableFileAgentActionsResult {
    filteredActions: ParsedAiFileAction[]
    skippedCompleted: number
    skippedBlocked: number
}

const resolveRenameTargetPath = (action: ParsedAiFileAction): string | null => {
    if (action.type !== 'rename_path' || !action.newPath) return null
    if (action.newPath.includes('/')) return action.newPath

    const lastSlash = action.path.lastIndexOf('/')
    const parent = lastSlash >= 0 ? action.path.slice(0, lastSlash) : ''
    return parent ? `${parent}/${action.newPath}` : action.newPath
}

export const createFileAgentActionTracker = (): FileAgentActionTracker => ({
    executedActionBatchSignatures: new Set<string>(),
    completedRepeatSensitiveActionKeys: new Set<string>(),
    blockedRetryActionKeys: new Set<string>()
})

export const serializeAiActionBatch = (actions: ParsedAiFileAction[]) =>
    actions
        .map(action => JSON.stringify({
            type: action.type,
            path: action.path,
            newPath: action.newPath || '',
            overwrite: Boolean(action.overwrite),
            content: action.content ?? ''
        }))
        .join('\n')

export const resolveActionFocusRelativePath = (
    action: ParsedAiFileAction,
    stage: AiFileActionProgressEvent['stage']
): string | null => {
    switch (action.type) {
        case 'read_file':
            return action.path
        case 'write_file':
            return stage === 'success' ? action.path : null
        case 'create_file':
            return stage === 'success' ? action.path : null
        case 'apply_diff':
            return stage === 'success' ? action.path : null
        case 'rename_path':
            return stage === 'success'
                ? (resolveRenameTargetPath(action) || action.path)
                : null
        default:
            return null
    }
}

export const mergeAiFileActionResults = (
    base: AiFileActionResult,
    extra: AiFileActionResult
): AiFileActionResult => ({
    applied: base.applied + extra.applied,
    logs: [...base.logs, ...extra.logs],
    errors: [...base.errors, ...extra.errors],
    renamed: [...base.renamed, ...extra.renamed],
    deleted: [...base.deleted, ...extra.deleted],
    read: [...base.read, ...extra.read],
    list: [...(base.list ?? []), ...(extra.list ?? [])]
})

export const shouldContinueWithToolResult = (result: AiFileActionResult) => (
    result.errors.length > 0
    || result.read.length > 0
    || Boolean(result.list && result.list.length > 0)
    || result.applied > 0
)

export const getRepeatSensitiveActionKey = (action: ParsedAiFileAction): string | null => {
    switch (action.type) {
        case 'create_file':
        case 'create_folder':
        case 'delete_file':
        case 'delete_folder':
        case 'delete_path':
        case 'rename_path':
        case 'apply_diff':
            return `${action.type}:${action.path}:${action.newPath || ''}`
        default:
            return null
    }
}

export const getRetryBlockedActionKey = (action: ParsedAiFileAction): string | null => {
    switch (action.type) {
        case 'read_file':
        case 'list_directory':
        case 'delete_file':
        case 'delete_folder':
        case 'delete_path':
        case 'rename_path':
        case 'apply_diff':
            return `${action.type}:${action.path}:${action.newPath || ''}`
        default:
            return null
    }
}

export const shouldBlockFutureActionRetry = (
    action: ParsedAiFileAction,
    message: string
) => {
    const normalized = message.toLowerCase()
    if (action.type === 'list_directory' || action.type === 'read_file') {
        return (
            normalized.includes('path is not present in the latest directory listing')
            || normalized.includes('not found:')
            || normalized.includes('no such file or directory')
            || normalized.includes('listed as a directory, not a file')
            || normalized.includes('listed as a file, not a directory')
        )
    }

    if (action.type === 'apply_diff' || action.type === 'delete_file' || action.type === 'delete_folder' || action.type === 'delete_path') {
        return (
            normalized.includes('path is not present in the latest directory listing')
            || normalized.includes('not found:')
            || normalized.includes('no such file or directory')
            || normalized.includes('listed as a directory, not a file')
            || normalized.includes('listed as a file, not a directory')
        )
    }

    if (action.type === 'rename_path') {
        return (
            normalized.includes('path is not present in the latest directory listing')
            || normalized.includes('not found:')
            || normalized.includes('no such file or directory')
        )
    }

    return false
}

export const filterExecutableFileAgentActions = (
    tracker: FileAgentActionTracker,
    actions: ParsedAiFileAction[]
): FilterExecutableFileAgentActionsResult => {
    let skippedCompleted = 0
    let skippedBlocked = 0

    const filteredActions = actions.filter(action => {
        const repeatSensitiveKey = getRepeatSensitiveActionKey(action)
        if (repeatSensitiveKey && tracker.completedRepeatSensitiveActionKeys.has(repeatSensitiveKey)) {
            skippedCompleted += 1
            return false
        }

        const blockedRetryKey = getRetryBlockedActionKey(action)
        if (blockedRetryKey && tracker.blockedRetryActionKeys.has(blockedRetryKey)) {
            skippedBlocked += 1
            return false
        }

        return true
    })

    return {
        filteredActions,
        skippedCompleted,
        skippedBlocked
    }
}

export const noteSuccessfulFileAgentAction = (
    tracker: FileAgentActionTracker,
    action: ParsedAiFileAction
) => {
    const repeatSensitiveKey = getRepeatSensitiveActionKey(action)
    if (repeatSensitiveKey) {
        tracker.completedRepeatSensitiveActionKeys.add(repeatSensitiveKey)
    }
}

export const noteFailedFileAgentAction = (
    tracker: FileAgentActionTracker,
    action: ParsedAiFileAction,
    message: string
) => {
    const blockedRetryKey = getRetryBlockedActionKey(action)
    if (blockedRetryKey && shouldBlockFutureActionRetry(action, message)) {
        tracker.blockedRetryActionKeys.add(blockedRetryKey)
    }
}
