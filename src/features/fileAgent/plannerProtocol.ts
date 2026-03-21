import {
    parseAiFileActionEntries,
    type AiFileActionParseResult,
    type AiFileActionResult,
    type ParsedAiFileAction
} from '../../utils/aiFileActions'

export type FileAgentPlannerKind = 'tool_request' | 'final_answer' | 'need_user_input'

export type FileAgentPlannerEnvelope = {
    kind: FileAgentPlannerKind
    actions?: unknown[]
    message?: string
}

export type FileAgentPlannerParseResult = {
    envelope: FileAgentPlannerEnvelope | null
    errors: string[]
}

const FILE_AGENT_BLOCK_RE = /```(?:\s*)file_agent\s*([\s\S]*?)```/i

const tryParseJsonObject = (text: string): unknown => {
    return JSON.parse(text)
}

const isPlainObject = (value: unknown): value is Record<string, unknown> =>
    Boolean(value) && typeof value === 'object' && !Array.isArray(value)

const coerceKind = (value: unknown): FileAgentPlannerKind | null => {
    if (typeof value !== 'string') return null
    const normalized = value.trim().toLowerCase()
    if (normalized === 'tool_request') return 'tool_request'
    if (normalized === 'final_answer') return 'final_answer'
    if (normalized === 'need_user_input') return 'need_user_input'
    return null
}

const getAbsPathBasename = (absPath: string): string => {
    const parts = absPath.split(/[\\/]/).filter(Boolean)
    return parts[parts.length - 1] || absPath
}

export const buildFileAgentPlannerInstruction = (
    rootPath: string,
    options?: { requireUserApproval?: boolean }
) => {
    const requireUserApproval = options?.requireUserApproval ?? true
    const rootBaseName = getAbsPathBasename(rootPath)

    return [
        requireUserApproval
            ? 'You can perform file/folder operations for the opened local workspace only after user approval.'
            : 'You can perform file/folder operations for the opened local workspace. Actions are auto-approved and executed immediately.',
        `Workspace root: ${rootPath}`,
        '',
        'Return exactly one fenced ```file_agent``` JSON block and nothing else.',
        '',
        'Allowed envelope shapes:',
        '```file_agent',
        '{"kind":"tool_request","actions":[{"type":"list_directory","path":"."}]}',
        '```',
        '```file_agent',
        '{"kind":"final_answer","message":"User-facing answer here."}',
        '```',
        '```file_agent',
        '{"kind":"need_user_input","message":"Ask one concrete clarifying question."}',
        '```',
        '',
        'Allowed action types inside "tool_request":',
        '- list_directory',
        '- read_file',
        '- write_file',
        '- apply_diff',
        '- create_file',
        '- create_folder',
        '- rename_path',
        '- delete_path',
        '',
        'Rules:',
        '- Use only relative paths from the workspace root.',
        `- The root folder itself is named "${rootBaseName}" — do NOT use "${rootBaseName}" as a path; it is the root, not a subfolder.`,
        '- To list or access the root folder, always use path "." (e.g. {"type":"list_directory","path":"."}).',
        '- Do not use XML tags.',
        '- Do not use pigtex_fs.',
        '- Return exactly one file_agent block per turn.',
        '- If path is uncertain, inspect first instead of guessing.',
        '- Never guess conventional filenames or subfolders that were not explicitly listed.',
        '- Prefer read/list actions before mutate actions when inspection is required.',
        '- Prefer apply_diff for targeted edits to existing files.',
        '- Use final_answer when the task is complete.',
        '- Use need_user_input only when required information is genuinely missing.'
    ].join('\n')
}

export const parseFileAgentPlannerEnvelope = (content: string): FileAgentPlannerParseResult => {
    const trimmed = content.trim()
    if (!trimmed) {
        return { envelope: null, errors: [] }
    }

    let candidate = ''
    const match = trimmed.match(FILE_AGENT_BLOCK_RE)
    if (match?.[1]) {
        candidate = match[1].trim()
    } else if (trimmed.startsWith('{') && trimmed.endsWith('}')) {
        candidate = trimmed
    } else {
        return { envelope: null, errors: [] }
    }

    let parsed: unknown
    try {
        parsed = tryParseJsonObject(candidate)
    } catch (error) {
        return {
            envelope: null,
            errors: [error instanceof Error ? error.message : 'Invalid JSON in file_agent block']
        }
    }

    if (!isPlainObject(parsed)) {
        return {
            envelope: null,
            errors: ['file_agent block must contain a JSON object']
        }
    }

    const kind = coerceKind(parsed.kind)
    if (!kind) {
        return {
            envelope: null,
            errors: ['file_agent.kind must be one of: tool_request, final_answer, need_user_input']
        }
    }

    if (kind === 'tool_request') {
        if (!Array.isArray(parsed.actions)) {
            return {
                envelope: null,
                errors: ['tool_request must include an actions array']
            }
        }
        if (parsed.actions.length === 0) {
            return {
                envelope: null,
                errors: ['tool_request.actions must not be empty']
            }
        }
        return {
            envelope: {
                kind,
                actions: parsed.actions
            },
            errors: []
        }
    }

    const message = typeof parsed.message === 'string' ? parsed.message.trim() : ''
    if (!message) {
        return {
            envelope: null,
            errors: [`${kind} must include a non-empty message`]
        }
    }

    return {
        envelope: {
            kind,
            message
        },
        errors: []
    }
}

export const buildFileAgentToolContextMessage = (result: AiFileActionResult) => {
    const payload = {
        kind: 'tool_result',
        applied: result.applied,
        logs: result.logs,
        errors: result.errors,
        renamed: result.renamed,
        deleted: result.deleted,
        read: result.read,
        list: result.list ?? []
    }

    const hasErrors = result.errors.length > 0
    const hasRootNameError = hasErrors && result.errors.some(e =>
        e.includes('is the workspace root folder name') || e.includes('To list the root folder, use path: "."')
    )

    const lines = [
        '[FILE_AGENT_CONTEXT]',
        'The previous filesystem actions were executed on the opened workspace.',
        'Return exactly one ```file_agent``` JSON block for the next step.',
        'If the task is complete, return kind=final_answer.',
        'If more filesystem work is required, return kind=tool_request.',
        'Never repeat actions that already succeeded.'
    ]

    if (hasRootNameError) {
        lines.push('REMINDER: All paths are relative to the workspace root. To list or access the root folder itself, use path "." — never the root folder name.')
    } else if (hasErrors) {
        lines.push('REMINDER: Use exact entry names from previous list_directory results. Use "." to list the root folder.')
    }

    lines.push('```json', JSON.stringify(payload, null, 2), '```', '[/FILE_AGENT_CONTEXT]')

    return lines.join('\n')
}

export const isFileAgentContextPayload = (content: string) =>
    /^\s*\[FILE_AGENT_CONTEXT]/i.test(content)

export const parseFileAgentPlannerActions = (
    envelope: FileAgentPlannerEnvelope | null
): AiFileActionParseResult & { actions: ParsedAiFileAction[] } => {
    if (!envelope || envelope.kind !== 'tool_request') {
        return {
            actions: [],
            errors: ['Expected file_agent kind=tool_request']
        }
    }

    return parseAiFileActionEntries(envelope.actions ?? [])
}
