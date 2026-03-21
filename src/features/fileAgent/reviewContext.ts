type MentionTarget = {
    type: 'file' | 'folder'
    relativePath: string
    absolutePath: string
    name: string
}

type FsEntry = {
    name: string
    path: string
    type: 'file' | 'directory'
    size: number
    mtimeMs: number
}

type ElectronFsAPI = NonNullable<Window['electronAPI']>

export interface WorkspaceReviewContext {
    rootName: string
    targetLabel: string
    visitedDirectories: number
    discoveredEntries: number
    filesRead: number
    totalReadChars: number
    treeText: string
    fileSections: string[]
    truncated: boolean
    errors: string[]
}

export interface WorkspaceReviewCollectionOptions {
    maxDepth?: number
    maxEntries?: number
    maxFilesToRead?: number
    maxCharsPerFile?: number
    maxTotalReadChars?: number
}

const REVIEW_HINT_RE = /(read|review|audit|inspect|scan|analyze|analyse|summarize|summary|qu[eé]t|đọc|rà soát|phân tích|kiểm tra|tóm tắt)/i
const WHOLE_SCOPE_RE = /(toàn bộ|cả thư mục|cả folder|nguyên folder|\bwhole\b|\bentire\b|\ball\b|\bfull\b)/i
const TEXT_EXTENSIONS = new Set([
    '.txt', '.md', '.markdown', '.json', '.jsonc', '.yaml', '.yml', '.toml', '.ini',
    '.js', '.jsx', '.ts', '.tsx', '.mjs', '.cjs', '.py', '.rb', '.php', '.java',
    '.c', '.cc', '.cpp', '.h', '.hpp', '.cs', '.go', '.rs', '.swift', '.kt', '.kts',
    '.html', '.htm', '.css', '.scss', '.sass', '.less', '.svg', '.xml',
    '.sh', '.bash', '.zsh', '.ps1', '.bat', '.cmd', '.sql', '.prisma'
])
const TEXT_BASENAMES = new Set([
    'readme', 'license', 'dockerfile', 'makefile', '.gitignore', '.env', '.env.example',
    'package.json', 'package-lock.json', 'tsconfig.json', 'vite.config.ts', 'requirements.txt'
])

const DEFAULT_OPTIONS: Required<WorkspaceReviewCollectionOptions> = {
    maxDepth: 4,
    maxEntries: 400,
    maxFilesToRead: 40,
    maxCharsPerFile: 2400,
    maxTotalReadChars: 48000
}

const normalizePrompt = (text: string) => text.trim().toLowerCase()

const toPosixPath = (inputPath: string) => inputPath.replace(/\\/g, '/')

const getWorkspaceRootName = (rootPath: string) => {
    const normalized = toPosixPath(rootPath).replace(/\/+$/, '')
    const segments = normalized.split('/').filter(Boolean)
    return segments[segments.length - 1] || '.'
}

const getDisplayPath = (relativePath: string) => {
    const normalized = toPosixPath(relativePath || '.').trim()
    return normalized === '' ? '.' : normalized
}

const joinRelativePath = (basePath: string, name: string) => {
    const normalizedBase = getDisplayPath(basePath)
    if (normalizedBase === '.') return name
    return `${normalizedBase}/${name}`
}

const getIndent = (depth: number) => '  '.repeat(Math.max(0, depth))

const sortEntries = (entries: FsEntry[]) =>
    [...entries].sort((left, right) => {
        if (left.type !== right.type) {
            return left.type === 'directory' ? -1 : 1
        }
        return left.name.localeCompare(right.name)
    })

const isLikelyTextFile = (fileName: string) => {
    const normalized = fileName.trim().toLowerCase()
    if (!normalized) return false
    if (TEXT_BASENAMES.has(normalized)) return true

    const lastDot = normalized.lastIndexOf('.')
    const ext = lastDot >= 0 ? normalized.slice(lastDot) : ''
    if (TEXT_EXTENSIONS.has(ext)) return true

    return normalized.startsWith('.') && !normalized.endsWith('.png') && !normalized.endsWith('.jpg') && !normalized.endsWith('.jpeg')
}

const buildTargetLabel = (mentions: MentionTarget[], rootName: string) => {
    if (mentions.length === 0) return `workspace root (${rootName})`
    return mentions
        .map((target) => `${target.type === 'folder' ? 'folder' : 'file'}:${getDisplayPath(target.relativePath)}`)
        .join(', ')
}

export const shouldUseWorkspaceReviewController = (params: {
    promptText: string
    mentions: MentionTarget[]
    localRootPath: string | null
    aiFileModeEnabled: boolean
}) => {
    const { promptText, mentions, localRootPath, aiFileModeEnabled } = params
    if (!aiFileModeEnabled || !localRootPath) return false

    const normalizedPrompt = normalizePrompt(promptText)
    if (!normalizedPrompt && mentions.length === 0) return false

    if (mentions.some((mention) => mention.type === 'folder')) {
        return !normalizedPrompt || REVIEW_HINT_RE.test(normalizedPrompt)
    }

    if (mentions.some((mention) => mention.type === 'file')) return false

    return REVIEW_HINT_RE.test(normalizedPrompt) && WHOLE_SCOPE_RE.test(normalizedPrompt)
}

const buildReviewTargets = (
    rootPath: string,
    mentions: MentionTarget[]
) => {
    if (mentions.length === 0) {
        return [{
            type: 'folder' as const,
            relativePath: '.',
            absolutePath: rootPath,
            name: '.'
        }]
    }

    return mentions.map((mention) => ({
        type: mention.type,
        relativePath: getDisplayPath(mention.relativePath),
        absolutePath: mention.absolutePath,
        name: mention.name
    }))
}

export const collectWorkspaceReviewContext = async (
    rootPath: string,
    mentions: MentionTarget[],
    options?: WorkspaceReviewCollectionOptions
): Promise<WorkspaceReviewContext> => {
    const electronAPI = window.electronAPI as ElectronFsAPI | undefined
    if (!electronAPI?.listDirectory || !electronAPI?.readFile) {
        throw new Error('Local filesystem review is unavailable')
    }

    const config = {
        ...DEFAULT_OPTIONS,
        ...options
    }
    const rootName = getWorkspaceRootName(rootPath)

    const targets = buildReviewTargets(rootPath, mentions)
    const treeLines: string[] = []
    const fileSections: string[] = []
    const errors: string[] = []
    const visitedDirectories = new Set<string>()
    const visitedFiles = new Set<string>()
    let discoveredEntries = 0
    let filesRead = 0
    let totalReadChars = 0
    let truncated = false

    const tryReadFile = async (absolutePath: string, relativePath: string) => {
        if (visitedFiles.has(absolutePath)) return
        if (filesRead >= config.maxFilesToRead || totalReadChars >= config.maxTotalReadChars) {
            truncated = true
            return
        }

        visitedFiles.add(absolutePath)
        try {
            const fileData = await electronAPI.readFile!(absolutePath)
            const remainingBudget = Math.max(0, config.maxTotalReadChars - totalReadChars)
            const previewLimit = Math.max(0, Math.min(config.maxCharsPerFile, remainingBudget))
            if (previewLimit <= 0) {
                truncated = true
                return
            }

            const preview = fileData.content.slice(0, previewLimit)
            const previewWasTruncated = fileData.content.length > preview.length
            totalReadChars += preview.length
            filesRead += 1

            fileSections.push(
                [
                    `Path: ${getDisplayPath(relativePath)}`,
                    `Size: ${fileData.size} bytes${previewWasTruncated ? ' (truncated preview)' : ''}`,
                    '```text',
                    preview || '(empty file)',
                    '```'
                ].join('\n')
            )
            if (previewWasTruncated) {
                truncated = true
            }
        } catch (error) {
            const message = error instanceof Error ? error.message : String(error)
            errors.push(`Could not read ${getDisplayPath(relativePath)}: ${message}`)
        }
    }

    const walkDirectory = async (absolutePath: string, relativePath: string, depth: number) => {
        if (visitedDirectories.has(absolutePath)) return
        if (depth > config.maxDepth || discoveredEntries >= config.maxEntries) {
            truncated = true
            return
        }

        visitedDirectories.add(absolutePath)
        let entries: FsEntry[] = []
        try {
            entries = sortEntries(await electronAPI.listDirectory!({
                rootPath,
                dirPath: absolutePath
            }) as FsEntry[])
        } catch (error) {
            const message = error instanceof Error ? error.message : String(error)
            errors.push(`Could not list ${getDisplayPath(relativePath)}: ${message}`)
            return
        }

        treeLines.push(`${getIndent(depth)}[DIR] ${getDisplayPath(relativePath)}/`)

        for (const entry of entries) {
            if (discoveredEntries >= config.maxEntries) {
                truncated = true
                break
            }

            const entryRelativePath = joinRelativePath(relativePath, entry.name)
            discoveredEntries += 1
            if (entry.type === 'directory') {
                if (depth < config.maxDepth) {
                    await walkDirectory(entry.path, entryRelativePath, depth + 1)
                } else {
                    truncated = true
                }
                continue
            }

            treeLines.push(`${getIndent(depth + 1)}[FILE] ${entry.name} (${entry.size} bytes)`)
            if (isLikelyTextFile(entry.name)) {
                await tryReadFile(entry.path, entryRelativePath)
            }
        }
    }

    for (const target of targets) {
        if (target.type === 'file') {
            treeLines.push(`[FILE] ${getDisplayPath(target.relativePath)}`)
            await tryReadFile(target.absolutePath, target.relativePath)
            continue
        }

        await walkDirectory(target.absolutePath, target.relativePath, 0)
    }

    if (treeLines.length === 0) {
        treeLines.push('[DIR] ./')
    }

    return {
        rootName,
        targetLabel: buildTargetLabel(mentions, rootName),
        visitedDirectories: visitedDirectories.size,
        discoveredEntries,
        filesRead,
        totalReadChars,
        treeText: treeLines.join('\n'),
        fileSections,
        truncated,
        errors
    }
}

export const buildWorkspaceReviewMessage = (
    userRequest: string,
    reviewContext: WorkspaceReviewContext
) => {
    const lines: string[] = [
        '[WORKSPACE_REVIEW_CONTEXT]',
        `Workspace root folder name: ${reviewContext.rootName}`,
        `Target: ${reviewContext.targetLabel}`,
        `Visited directories: ${reviewContext.visitedDirectories}`,
        `Discovered entries: ${reviewContext.discoveredEntries}`,
        `Read text files: ${reviewContext.filesRead}`,
        `Collected text chars: ${reviewContext.totalReadChars}`,
        `Context truncated: ${reviewContext.truncated ? 'yes' : 'no'}`,
        '',
        'Workspace tree:',
        '```text',
        reviewContext.treeText,
        '```'
    ]

    if (reviewContext.fileSections.length > 0) {
        lines.push('', 'File previews:')
        lines.push(...reviewContext.fileSections)
    }

    if (reviewContext.errors.length > 0) {
        lines.push('', 'Collection warnings:')
        lines.push(...reviewContext.errors.map((error) => `- ${error}`))
    }

    lines.push(
        '[/WORKSPACE_REVIEW_CONTEXT]',
        '',
        'User request:',
        userRequest || 'Read and review the opened workspace thoroughly.',
        '',
        'Instructions:',
        '- Use the workspace context above as the source of truth.',
        `- If the user references the workspace root by name (${reviewContext.rootName}), treat that as the opened root folder, not a nested child folder.`,
        '- Do not ask the user to choose a file or folder unless the collected context is clearly insufficient.',
        '- Do not output any XML tool tags or pigtex_fs blocks.',
        '- If the context is truncated, explain what you could review and what remains uncertain.',
        '- Provide a direct user-facing review answer.'
    )

    return lines.join('\n')
}
