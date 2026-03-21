import { useState, useCallback, useMemo, memo, type ReactNode } from 'react'
import ReactMarkdown from 'react-markdown'
import remarkGfm from 'remark-gfm'
import remarkMath from 'remark-math'
import remarkBreaks from 'remark-breaks'
import rehypeHighlight from 'rehype-highlight'
import rehypeKatex from 'rehype-katex'
import { Copy, Check, Terminal, FileText } from 'lucide-react'
import type { Components } from 'react-markdown'
import { useI18n } from '../../contexts/I18nContext'
import ProtectedImage from './ProtectedImage'
import './MessageRenderer.css'

interface MessageRendererProps {
    content: string
    isStreaming?: boolean
    className?: string
}

interface MessageRendererCopy {
    copied: string
    copy: string
    copyCode: string
    code: string
    lines: (count: number) => string
}

interface StreamingMarkdownParts {
    stableMarkdown: string
    unstableTail: string
}

const FULL_REMARK_PLUGINS = [remarkGfm, remarkMath, remarkBreaks]
const STREAMING_REMARK_PLUGINS = [remarkGfm, remarkBreaks]
const FULL_REHYPE_PLUGINS = [rehypeHighlight, rehypeKatex]
const STREAMING_INLINE_MARKDOWN_LIMIT_CHARS = 240

/* ═══════════════════════════════════════════════════════════════
   LANGUAGE METADATA
   ═══════════════════════════════════════════════════════════════ */

const LANG_META: Record<string, { icon: string; label: string; color: string }> = {
    js: { icon: '⬡', label: 'JavaScript', color: '#f7df1e' },
    javascript: { icon: '⬡', label: 'JavaScript', color: '#f7df1e' },
    ts: { icon: '⬡', label: 'TypeScript', color: '#3178c6' },
    typescript: { icon: '⬡', label: 'TypeScript', color: '#3178c6' },
    tsx: { icon: '⬡', label: 'TSX', color: '#3178c6' },
    jsx: { icon: '⬡', label: 'JSX', color: '#f7df1e' },
    py: { icon: '🐍', label: 'Python', color: '#3776ab' },
    python: { icon: '🐍', label: 'Python', color: '#3776ab' },
    css: { icon: '🎨', label: 'CSS', color: '#1572b6' },
    html: { icon: '🌐', label: 'HTML', color: '#e34f26' },
    json: { icon: '{ }', label: 'JSON', color: '#6d8086' },
    bash: { icon: '$', label: 'Bash', color: '#89e051' },
    sh: { icon: '$', label: 'Shell', color: '#89e051' },
    shell: { icon: '$', label: 'Shell', color: '#89e051' },
    powershell: { icon: 'PS', label: 'PowerShell', color: '#5391fe' },
    sql: { icon: '🗃', label: 'SQL', color: '#e38c00' },
    rust: { icon: '🦀', label: 'Rust', color: '#dea584' },
    go: { icon: '🐿', label: 'Go', color: '#00add8' },
    java: { icon: '☕', label: 'Java', color: '#ed8b00' },
    cpp: { icon: 'C+', label: 'C++', color: '#00599c' },
    c: { icon: 'C', label: 'C', color: '#555555' },
    yaml: { icon: '📋', label: 'YAML', color: '#cb171e' },
    yml: { icon: '📋', label: 'YAML', color: '#cb171e' },
    md: { icon: '📝', label: 'Markdown', color: '#083fa1' },
    markdown: { icon: '📝', label: 'Markdown', color: '#083fa1' },
    dockerfile: { icon: '🐳', label: 'Dockerfile', color: '#0db7ed' },
    docker: { icon: '🐳', label: 'Docker', color: '#0db7ed' },
    xml: { icon: '📄', label: 'XML', color: '#f16529' },
    graphql: { icon: '◈', label: 'GraphQL', color: '#e535ab' },
    r: { icon: 'R', label: 'R', color: '#276dc3' },
    swift: { icon: '🐦', label: 'Swift', color: '#f05138' },
    kotlin: { icon: 'K', label: 'Kotlin', color: '#7f52ff' },
    ruby: { icon: '💎', label: 'Ruby', color: '#cc342d' },
    php: { icon: '🐘', label: 'PHP', color: '#777bb4' },
    dart: { icon: '🎯', label: 'Dart', color: '#0175c2' },
    text: { icon: '📄', label: 'Text', color: '#6d8086' },
    plaintext: { icon: '📄', label: 'Text', color: '#6d8086' },
    diff: { icon: '±', label: 'Diff', color: '#41b883' },
    ini: { icon: '⚙', label: 'INI', color: '#6d8086' },
    toml: { icon: '⚙', label: 'TOML', color: '#6d8086' },
    env: { icon: '🔐', label: 'ENV', color: '#ecd53f' },
    pigtex_fs: { icon: '🔧', label: 'PigTex Actions', color: '#6366f1' },
}

const getLangMeta = (lang: string, fallbackLabel: string = 'Code') => {
    const key = lang.toLowerCase().trim()
    return LANG_META[key] || { icon: '📄', label: lang || fallbackLabel, color: '#6d8086' }
}

/* ═══════════════════════════════════════════════════════════════
   CODE FORMATTERS — operate on raw strings BEFORE markdown parsing
   ═══════════════════════════════════════════════════════════════ */

/** Character-by-character parser for brace-based languages (CSS, JS, Java, etc.) */
const formatBraceCode = (code: string): string => {
    let out = ''
    let depth = 0
    const ind = () => '  '.repeat(depth)
    let inStr = false
    let strCh = ''
    let i = 0

    while (i < code.length) {
        const ch = code[i]

        // Track quoted strings — don't format inside them
        if (!inStr && (ch === '"' || ch === "'" || ch === '`')) {
            inStr = true; strCh = ch; out += ch; i++; continue
        }
        if (inStr) {
            out += ch
            if (ch === strCh && code[i - 1] !== '\\') inStr = false
            i++; continue
        }

        // Block comments /* ... */
        if (ch === '/' && code[i + 1] === '*') {
            const end = code.indexOf('*/', i + 2)
            const comment = end === -1 ? code.slice(i) : code.slice(i, end + 2)
            out += '\n' + ind() + comment.trim()
            i = end === -1 ? code.length : end + 2
            continue
        }
        // Line comments //
        if (ch === '/' && code[i + 1] === '/') {
            const end = code.indexOf('\n', i + 2)
            const comment = end === -1 ? code.slice(i) : code.slice(i, end)
            out += ' ' + comment.trim()
            i = end === -1 ? code.length : end + 1
            continue
        }

        if (ch === '{') {
            out = out.trimEnd() + ' {\n'
            depth++
            out += ind()
            i++
            while (i < code.length && code[i] === ' ') i++
            continue
        }
        if (ch === '}') {
            depth = Math.max(0, depth - 1)
            out = out.trimEnd() + '\n' + ind() + '}\n'
            i++
            while (i < code.length && code[i] === ' ') i++
            if (i < code.length && code[i] !== '}') out += ind()
            continue
        }
        if (ch === ';') {
            out += ';\n' + ind()
            i++
            while (i < code.length && code[i] === ' ') i++
            continue
        }

        out += ch
        i++
    }
    return out.replace(/\n{3,}/g, '\n\n').trim()
}

/** HTML/XML tag-aware formatter with indentation */
const formatHTML = (code: string): string => {
    const lines: string[] = []
    let depth = 0
    const ind = () => '  '.repeat(depth)
    const parts = code.replace(/></g, '>\n<').split('\n')

    for (const part of parts) {
        const t = part.trim()
        if (!t) continue
        if (/^<\//.test(t)) {
            depth = Math.max(0, depth - 1)
            lines.push(ind() + t)
        } else if (/\/>$/.test(t) || /^<!/.test(t) || /^<(meta|link|br|hr|img|input|source|area|base|col|embed|track|wbr)\b/i.test(t)) {
            lines.push(ind() + t)
        } else if (/^<[a-zA-Z]/.test(t)) {
            lines.push(ind() + t)
            if (!/<\//.test(t)) depth++
        } else {
            lines.push(ind() + t)
        }
    }
    return lines.join('\n')
}

/** Escape a string for dynamic RegExp construction */
const escapeRegExp = (value: string): string => value.replace(/[.*+?^${}()|[\]\\]/g, '\\$&')

/** Convert escaped newline/tab sequences when a code block arrives as one line */
const decodeEscapedWhitespace = (code: string): string => {
    if (code.includes('\n')) return code

    const escapedNewlineCount = (code.match(/\\n/g) || []).length + (code.match(/\\r\\n/g) || []).length
    if (escapedNewlineCount < 2) return code

    return code
        .replace(/\\r\\n/g, '\n')
        .replace(/\\n/g, '\n')
        .replace(/\\t/g, '\t')
        .replace(/\\"/g, '"')
}

/** Python keyword-based formatter */
const formatPython = (code: string): string => {
    let r = code.replace(/\r\n?/g, '\n').trim()
    if (r.includes('\n')) return r

    r = r.replace(/\s+/g, ' ')

    const blockBreakpoints = [
        'from ',
        'import ',
        'async def ',
        'def ',
        'class ',
        'if ',
        'elif ',
        'else:',
        'for ',
        'while ',
        'with ',
        'try:',
        'except ',
        'finally:'
    ]

    for (const bp of blockBreakpoints) {
        r = r.replace(new RegExp(`\\s+(?=${escapeRegExp(bp)})`, 'g'), '\n')
    }

    const indentedBreakpoints = ['return ', 'yield ', 'raise ', 'pass', 'break', 'continue']
    for (const bp of indentedBreakpoints) {
        r = r.replace(new RegExp(`\\s+(?=${escapeRegExp(bp)}\\b)`, 'g'), '\n    ')
    }

    // Basic block indentation when code was fully flattened.
    r = r.replace(/:\s+(?=[^\s#])/g, ':\n    ')
    r = r.replace(/\)\s+(?=(?:logging\.|print\(|await |[A-Za-z_][\w.]*\())/g, ')\n    ')

    return r.replace(/^\n+/, '').trim()
}

/** SQL keyword-based formatter */
const formatSQL = (code: string): string => {
    const kws = ['SELECT', 'FROM', 'WHERE', 'AND', 'OR', 'JOIN', 'LEFT JOIN', 'RIGHT JOIN',
        'INNER JOIN', 'ON', 'GROUP BY', 'ORDER BY', 'HAVING', 'LIMIT', 'OFFSET',
        'INSERT INTO', 'VALUES', 'UPDATE', 'SET', 'DELETE FROM', 'CREATE TABLE',
        'ALTER TABLE', 'DROP TABLE', 'UNION', 'UNION ALL']
    let r = code
    for (const kw of kws) {
        r = r.replace(new RegExp(' (' + kw + '\\b)', 'gi'), '\n$1')
    }
    return r.replace(/^\n+/, '').trim()
}

/** Detect language from code content when no language tag */
const detectLanguage = (code: string): string => {
    if (/^\s*<(!DOCTYPE|html|head|body|div|span|p\b|h[1-6]|a\b|img|ul|ol|li|table|form|input|button|link|meta|script|style)/i.test(code)) return 'html'
    if (/^\s*<\?xml|^\s*<svg/i.test(code)) return 'xml'
    if (/[{]\s*[\w-]+\s*:/.test(code) && /\b(margin|padding|display|color|font|background|border|width|height)\b/i.test(code)) return 'css'
    if (/\b(def |import |from |class |print\(|async def )/i.test(code) && !code.includes(';')) return 'python'
    if (/^\s*[{\[]/.test(code)) {
        try { JSON.parse(code); return 'json' } catch { /* nope */ }
    }
    if (/\b(SELECT|INSERT|UPDATE|DELETE|CREATE|FROM|WHERE)\b/.test(code)) return 'sql'
    if (/\b(const |let |var |function |=>|require\()/i.test(code)) return 'javascript'
    return ''
}

/** Generic formatter for compact single-line code with unknown language */
const formatUnknownCode = (code: string): string => {
    let r = code
    r = r.replace(/;\s*/g, ';\n')
    r = r.replace(/\)\s+(?=[A-Za-z_$][\w$]*(?:\(|\.))/g, ')\n')
    r = r.replace(/\{\s*/g, '{\n')
    r = r.replace(/\}\s*/g, '}\n')
    r = r.replace(/,\s+(?=[\w$]+\s*:)/g, ',\n')
    return r.replace(/\n{3,}/g, '\n\n').trim()
}

/** Format a single-line code string based on detected or given language */
const formatCodeString = (code: string, lang: string): string => {
    const normalizedLang = lang.toLowerCase()
    const normalizedSource = normalizedLang === 'pigtex_fs'
        ? code
        : decodeEscapedWhitespace(code)
    const normalized = normalizedSource.replace(/\r\n?/g, '\n').trim()
    const l = normalizedLang || detectLanguage(normalized)

    if (l === 'pigtex_fs') {
        return normalized
    }

    // If code is already multi-line and reasonably formatted, skip
    if (normalized.includes('\n') && normalized.split('\n').length > 2) return normalized

    // Skip short code
    if (normalized.length < 40) return normalized

    // Language-specific formatters
    if (['json', 'pigtex_fs'].includes(l)) {
        try { return JSON.stringify(JSON.parse(normalized), null, 2) }
        catch { return formatBraceCode(normalized) }
    }
    if (['css', 'scss', 'less'].includes(l)) return formatBraceCode(normalized)
    if (['html', 'xml', 'svg'].includes(l)) return formatHTML(normalized)
    if (['python', 'py'].includes(l)) return formatPython(normalized)
    if (['sql', 'mysql', 'postgresql', 'sqlite'].includes(l)) return formatSQL(normalized)
    if (['javascript', 'js', 'typescript', 'ts', 'tsx', 'jsx', 'java', 'c', 'cpp',
        'csharp', 'cs', 'rust', 'go', 'swift', 'kotlin', 'dart', 'php'].includes(l)) {
        return formatBraceCode(normalized)
    }
    if (['bash', 'sh', 'shell', 'powershell', 'zsh'].includes(l)) {
        return normalized.replace(/ && /g, ' &&\n').replace(/; (?=[a-zA-Z$])/g, ';\n').trim()
    }

    const genericFormatted = formatUnknownCode(normalized)
    if (genericFormatted.split('\n').length > 1) {
        return genericFormatted
    }

    // Universal fallback
    if (normalized.includes('{') && normalized.includes('}')) return formatBraceCode(normalized)
    if (normalized.includes('><')) return formatHTML(normalized)

    return normalized
}

/**
 * ★ KEY FUNCTION ★
 * Pre-process markdown text: find code blocks with single-line content
 * and format them BEFORE ReactMarkdown + rehype-highlight parses them.
 * This way formatting happens on raw text → syntax highlighting is preserved.
 */
const preprocessCodeBlocks = (markdown: string): string => {
    // Match fenced code blocks (supports full info string, not only \w language tags).
    return markdown.replace(/(^|\n)```([^\n`]*)\r?\n([\s\S]*?)```/g, (_match, lineStart: string, infoString: string, codeContent: string) => {
        const language = (infoString || '').trim().split(/\s+/)[0] || ''
        if (language.toLowerCase() === 'pigtex_fs') {
            return lineStart + '```' + infoString + '\n' + codeContent + '```'
        }
        const normalizedCodeContent = codeContent.replace(/\r\n?/g, '\n')
        const trimmed = normalizedCodeContent.trim()

        // Skip already well-formatted code (3+ lines)
        if (trimmed.split('\n').length > 3) {
            return lineStart + '```' + infoString + '\n' + codeContent + '```'
        }

        // Format the code
        const formatted = formatCodeString(trimmed, language)

        // If formatting produced more lines, use it
        if (formatted !== trimmed && formatted.split('\n').length > 1) {
            return lineStart + '```' + infoString + '\n' + formatted + '\n```'
        }

        return lineStart + '```' + infoString + '\n' + codeContent + '```'
    })
}

const findUnclosedCodeFenceStart = (text: string): number => {
    const normalized = text.replace(/\r\n?/g, '\n')
    const lines = normalized.split('\n')
    let openFenceStart = -1
    let offset = 0

    for (const line of lines) {
        const trimmedStart = line.trimStart()
        if (trimmedStart.startsWith('```')) {
            const fenceColumn = line.indexOf('```')
            const fenceOffset = fenceColumn >= 0 ? fenceColumn : 0
            openFenceStart = openFenceStart === -1 ? offset + fenceOffset : -1
        }
        offset += line.length + 1
    }

    return openFenceStart
}

const splitStreamingMarkdownContent = (content: string): StreamingMarkdownParts => {
    const normalized = content.replace(/\r\n?/g, '\n')
    if (!normalized) {
        return { stableMarkdown: '', unstableTail: '' }
    }

    const unclosedFenceStart = findUnclosedCodeFenceStart(normalized)
    if (unclosedFenceStart >= 0) {
        return {
            stableMarkdown: normalized.slice(0, unclosedFenceStart),
            unstableTail: normalized.slice(unclosedFenceStart)
        }
    }

    const lastNewline = normalized.lastIndexOf('\n')
    if (lastNewline >= 0) {
        if (lastNewline + 1 >= normalized.length) {
            return { stableMarkdown: normalized, unstableTail: '' }
        }
        return {
            stableMarkdown: normalized.slice(0, lastNewline + 1),
            unstableTail: normalized.slice(lastNewline + 1)
        }
    }

    if (normalized.length <= STREAMING_INLINE_MARKDOWN_LIMIT_CHARS) {
        return { stableMarkdown: normalized, unstableTail: '' }
    }

    return { stableMarkdown: '', unstableTail: normalized }
}

/* ═══════════════════════════════════════════════════════════════
   UTILITY FUNCTIONS
   ═══════════════════════════════════════════════════════════════ */

/** Recursively extract plain text from React element tree */
const extractText = (node: ReactNode): string => {
    if (typeof node === 'string') return node
    if (typeof node === 'number') return String(node)
    if (!node) return ''
    if (Array.isArray(node)) return node.map(extractText).join('')
    if (typeof node === 'object' && 'props' in node) {
        return extractText((node as React.ReactElement).props.children)
    }
    return ''
}

/** Detect if inline code looks like a file path, command, or identifier */
const getInlineCodeType = (text: string): 'path' | 'command' | 'code' => {
    if (/^[.~\/\\]|^[a-zA-Z]:[\\\/]|\.(?:ts|tsx|js|jsx|py|css|html|json|md|yaml|yml|toml|sql|rs|go|java|c|cpp|h|rb|php|sh|env|txt|cfg|ini|log|xml|svg|png|jpg|gif|ico|woff|woff2|ttf|eot|map)$/i.test(text)) {
        return 'path'
    }
    if (/^(?:npm|yarn|pnpm|bun|npx|pip|cargo|go|docker|git|cd|ls|mkdir|rm|cat|echo|curl|wget|python|node|deno)\s/i.test(text)) {
        return 'command'
    }
    return 'code'
}

/* ═══════════════════════════════════════════════════════════════
   REACT COMPONENTS
   ═══════════════════════════════════════════════════════════════ */

/** Copy button for code blocks */
const CopyButton = ({ text, copy }: { text: string; copy: MessageRendererCopy }) => {
    const [copied, setCopied] = useState(false)

    const handleCopy = useCallback(async () => {
        try {
            await navigator.clipboard.writeText(text)
            setCopied(true)
            setTimeout(() => setCopied(false), 2000)
        } catch {
            const textarea = document.createElement('textarea')
            textarea.value = text
            document.body.appendChild(textarea)
            textarea.select()
            document.execCommand('copy')
            document.body.removeChild(textarea)
            setCopied(true)
            setTimeout(() => setCopied(false), 2000)
        }
    }, [text])

    return (
        <button
            className={`code-copy-btn ${copied ? 'copied' : ''}`}
            onClick={handleCopy}
            title={copied ? copy.copied : copy.copyCode}
        >
            {copied ? (
                <>
                    <Check size={13} strokeWidth={2.5} />
                    <span>{copy.copied}</span>
                </>
            ) : (
                <>
                    <Copy size={13} />
                    <span>{copy.copy}</span>
                </>
            )}
        </button>
    )
}

/** Line number gutter */
const LineNumbers = ({ count }: { count: number }) => (
    <div className="code-line-numbers" aria-hidden="true">
        {Array.from({ length: count }, (_, i) => (
            <span key={i}>{i + 1}</span>
        ))}
    </div>
)

/* ═══════════════════════════════════════════════════════════════
   MARKDOWN CUSTOM COMPONENTS
   ═══════════════════════════════════════════════════════════════ */

const createMarkdownComponents = (copy: MessageRendererCopy): Components => ({
    pre({ children }) {
        return (
            <div className="code-block-wrapper">
                {children}
            </div>
        )
    },

    code({ className, children, ...props }) {
        const match = /language-(\w+)/.exec(className || '')
        const language = match ? match[1] : ''

        // Extract plain text from React tree (rehype-highlight creates spans)
        const plainText = extractText(children).replace(/\n$/, '')
        const plainTextNormalized = plainText.replace(/\r\n?/g, '\n')

        // Inline code: no language class + short + no newlines
        const isInline = !className && !plainText.includes('\n') && plainText.length < 100
        if (isInline) {
            const codeType = getInlineCodeType(plainText)
            return (
                <code className={`inline-code inline-code--${codeType}`} {...props}>
                    {codeType === 'path' && <FileText size={12} className="inline-code-icon" />}
                    {codeType === 'command' && <Terminal size={12} className="inline-code-icon" />}
                    {children}
                </code>
            )
        }

        // Block code — use extractText for accurate line count & copy
        const effectiveLang = language || detectLanguage(plainTextNormalized)
        const formattedFallback = formatCodeString(plainTextNormalized, effectiveLang)
        const shouldUseFormattedFallback =
            plainTextNormalized.split('\n').length <= 2 &&
            formattedFallback.split('\n').length > plainTextNormalized.split('\n').length

        // Final text used for line-count and copy action.
        const renderedText = shouldUseFormattedFallback ? formattedFallback : plainTextNormalized
        const meta = getLangMeta(effectiveLang, copy.code)
        const lineCount = renderedText.split('\n').length
        const showLineNumbers = lineCount > 1

        return (
            <>
                <div className="code-block-header">
                    <div className="code-lang-info">
                        <span className="code-lang-icon" style={{ color: meta.color }}>
                            {meta.icon}
                        </span>
                        <span className="code-language">{meta.label}</span>
                        {lineCount > 1 && <span className="code-line-count">{copy.lines(lineCount)}</span>}
                    </div>
                    <CopyButton text={renderedText} copy={copy} />
                </div>
                <div className={`code-body ${showLineNumbers ? '' : 'code-body--single'}`}>
                    {showLineNumbers && <LineNumbers count={lineCount} />}
                    <code className={className} {...props}>
                        {shouldUseFormattedFallback ? renderedText : children}
                    </code>
                </div>
            </>
        )
    },

    table({ children }) {
        return (
            <div className="table-wrapper">
                <table>{children}</table>
            </div>
        )
    },

    a({ href, children }) {
        return (
            <a href={href} target="_blank" rel="noopener noreferrer" className="md-link">
                {children}
                <svg width="11" height="11" viewBox="0 0 12 12" className="md-link-icon">
                    <path d="M3.5 3a.5.5 0 0 1 .5-.5h5a.5.5 0 0 1 .5.5v5a.5.5 0 0 1-1 0V4.207L3.854 8.854a.5.5 0 1 1-.708-.708L7.793 3.5H4a.5.5 0 0 1-.5-.5z" fill="currentColor" />
                </svg>
            </a>
        )
    },

    blockquote({ children }) {
        return <blockquote className="md-blockquote">{children}</blockquote>
    },

    hr() {
        return (
            <div className="md-hr-wrapper">
                <hr className="md-hr" />
            </div>
        )
    },

    img({ src, alt }) {
        return (
            <div className="md-image-wrapper">
                <ProtectedImage
                    source={src || ''}
                    alt={alt || ''}
                    className="md-image"
                    loading="lazy"
                />
                {alt && <span className="md-image-caption">{alt}</span>}
            </div>
        )
    },

    ul({ children }) {
        return <ul className="md-list md-ul">{children}</ul>
    },
    ol({ children }) {
        return <ol className="md-list md-ol">{children}</ol>
    },
    li({ children }) {
        return <li className="md-li">{children}</li>
    },

    h1({ children }) { return <h1 className="md-heading md-h1">{children}</h1> },
    h2({ children }) { return <h2 className="md-heading md-h2">{children}</h2> },
    h3({ children }) { return <h3 className="md-heading md-h3">{children}</h3> },
    h4({ children }) { return <h4 className="md-heading md-h4">{children}</h4> },

    p({ children }) {
        return <p className="md-paragraph">{children}</p>
    },

    strong({ children }) {
        return <strong className="md-strong">{children}</strong>
    },
    em({ children }) {
        return <em className="md-em">{children}</em>
    },
})

/* ═══════════════════════════════════════════════════════════════
   MAIN COMPONENT
   ═══════════════════════════════════════════════════════════════ */

const MessageRenderer = memo(({ content, isStreaming = false, className = '' }: MessageRendererProps) => {
    const { isVietnamese } = useI18n()
    const copy = useMemo<MessageRendererCopy>(() => (
        isVietnamese
            ? {
                copied: 'Đã chép',
                copy: 'Sao chép',
                copyCode: 'Sao chép mã',
                code: 'Mã',
                lines: (count: number) => `${count} dòng`
            }
            : {
                copied: 'Copied!',
                copy: 'Copy',
                copyCode: 'Copy code',
                code: 'Code',
                lines: (count: number) => `${count} line${count === 1 ? '' : 's'}`
            }
    ), [isVietnamese])
    const markdownComponents = useMemo(() => createMarkdownComponents(copy), [copy])
    const streamingParts = useMemo(
        () => (isStreaming ? splitStreamingMarkdownContent(content) : { stableMarkdown: '', unstableTail: '' }),
        [content, isStreaming]
    )
    const processedStreamingMarkdown = useMemo(
        () => (streamingParts.stableMarkdown ? preprocessCodeBlocks(streamingParts.stableMarkdown) : ''),
        [streamingParts.stableMarkdown]
    )
    const processedContent = useMemo(() => preprocessCodeBlocks(content), [content])

    if (!content) {
        return isStreaming ? (
            <div className="streaming-placeholder">
                <div className="streaming-dots">
                    <span />
                    <span />
                    <span />
                </div>
            </div>
        ) : null
    }

    // Render the stable head as markdown while the incomplete tail stays plain text.
    // This keeps streaming responsive without re-running highlight on every delta.
    if (isStreaming) {
        return (
            <div className={`message-renderer is-streaming ${className}`}>
                {processedStreamingMarkdown && (
                    <div className="streaming-markdown-live">
                        <ReactMarkdown
                            remarkPlugins={STREAMING_REMARK_PLUGINS}
                            components={markdownComponents}
                        >
                            {processedStreamingMarkdown}
                        </ReactMarkdown>
                    </div>
                )}
                {streamingParts.unstableTail && (
                    <pre className={`streaming-plain-text ${processedStreamingMarkdown ? 'streaming-plain-text-tail' : ''}`}>
                        {streamingParts.unstableTail}
                    </pre>
                )}
                <span className="streaming-cursor">▊</span>
            </div>
        )
    }

    return (
        <div className={`message-renderer ${className}`}>
            <ReactMarkdown
                remarkPlugins={FULL_REMARK_PLUGINS}
                rehypePlugins={FULL_REHYPE_PLUGINS}
                components={markdownComponents}
            >
                {processedContent}
            </ReactMarkdown>
        </div>
    )
})

MessageRenderer.displayName = 'MessageRenderer'

export default MessageRenderer
