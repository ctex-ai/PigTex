/**
 * Animated Empty State illustrations - Premium v2
 * Beautiful gradient mesh illustrations with smooth animations
 */

interface EmptyStateProps {
    type: 'no-conversations' | 'no-workspace' | 'no-results' | 'offline'
    title?: string
    description?: string
}

const NoConversations = () => {
    const id = `nc-${Math.random().toString(36).slice(2, 6)}`
    return (
        <svg width="140" height="140" viewBox="0 0 140 140" fill="none" className="empty-illustration">
            <defs>
                <linearGradient id={`${id}-bubble`} x1="0%" y1="0%" x2="100%" y2="100%">
                    <stop offset="0%" stopColor="#6366f1" stopOpacity="0.12" />
                    <stop offset="100%" stopColor="#06b6d4" stopOpacity="0.08" />
                </linearGradient>
                <linearGradient id={`${id}-accent`} x1="0%" y1="0%" x2="100%" y2="100%">
                    <stop offset="0%" stopColor="#6366f1" />
                    <stop offset="100%" stopColor="#06b6d4" />
                </linearGradient>
                <filter id={`${id}-glow`}>
                    <feGaussianBlur stdDeviation="3" result="blur" />
                    <feMerge><feMergeNode in="blur" /><feMergeNode in="SourceGraphic" /></feMerge>
                </filter>
            </defs>

            {/* Background circle */}
            <circle cx="70" cy="70" r="58" fill={`url(#${id}-bubble)`} className="float-slow" />

            {/* Main chat bubble */}
            <g className="float-slow">
                <rect x="30" y="38" width="72" height="44" rx="14"
                    fill="var(--color-bg-elevated, #fff)"
                    stroke="var(--color-border, #e5e5e5)" strokeWidth="1.5" />

                {/* Typing dots */}
                <circle cx="52" cy="60" r="4" fill={`url(#${id}-accent)`} className="dot-pulse-1" opacity="0.7" />
                <circle cx="66" cy="60" r="4" fill={`url(#${id}-accent)`} className="dot-pulse-2" opacity="0.7" />
                <circle cx="80" cy="60" r="4" fill={`url(#${id}-accent)`} className="dot-pulse-3" opacity="0.7" />

                {/* Tail */}
                <path d="M42 82 L38 96 L55 82" fill="var(--color-bg-elevated, #fff)" stroke="var(--color-border, #e5e5e5)" strokeWidth="1.5" />
                <line x1="42" y1="82" x2="55" y2="82" stroke="var(--color-bg-elevated, #fff)" strokeWidth="3" />
            </g>

            {/* Small secondary bubble */}
            <g opacity="0.5">
                <rect x="78" y="22" width="38" height="20" rx="10"
                    fill="var(--color-bg-tertiary, #f3f4f6)"
                    stroke="var(--color-border, #e5e5e5)" strokeWidth="1" />
                <line x1="86" y1="30" x2="108" y2="30" stroke="var(--color-border, #e5e5e5)" strokeWidth="2" strokeLinecap="round" />
                <line x1="86" y1="36" x2="100" y2="36" stroke="var(--color-border, #e5e5e5)" strokeWidth="2" strokeLinecap="round" />
            </g>

            {/* Sparkle decorations */}
            <g filter={`url(#${id}-glow)`}>
                <path d="M18 28 L20 22 L22 28 L28 30 L22 32 L20 38 L18 32 L12 30 Z" fill="#6366f1" opacity="0.5" className="sparkle-1" />
                <path d="M115 42 L116.5 38 L118 42 L122 43.5 L118 45 L116.5 49 L115 45 L111 43.5 Z" fill="#FFD700" opacity="0.45" className="sparkle-2" />
            </g>

            {/* Floating plus icon */}
            <g className="sparkle-3" opacity="0.4">
                <circle cx="120" cy="95" r="10" fill={`url(#${id}-accent)`} opacity="0.15" />
                <line x1="116" y1="95" x2="124" y2="95" stroke="#6366f1" strokeWidth="1.5" strokeLinecap="round" />
                <line x1="120" y1="91" x2="120" y2="99" stroke="#6366f1" strokeWidth="1.5" strokeLinecap="round" />
            </g>
        </svg>
    )
}

const NoWorkspace = () => {
    const id = `nw-${Math.random().toString(36).slice(2, 6)}`
    return (
        <svg width="140" height="140" viewBox="0 0 140 140" fill="none" className="empty-illustration">
            <defs>
                <linearGradient id={`${id}-folder`} x1="0%" y1="0%" x2="100%" y2="100%">
                    <stop offset="0%" stopColor="#6366f1" stopOpacity="0.15" />
                    <stop offset="100%" stopColor="#8b5cf6" stopOpacity="0.08" />
                </linearGradient>
                <linearGradient id={`${id}-folderFace`} x1="50%" y1="0%" x2="50%" y2="100%">
                    <stop offset="0%" stopColor="var(--color-bg-primary, #fff)" />
                    <stop offset="100%" stopColor="var(--color-bg-secondary, #f9fafb)" />
                </linearGradient>
                <linearGradient id={`${id}-tab`} x1="0%" y1="0%" x2="100%" y2="0%">
                    <stop offset="0%" stopColor="#6366f1" />
                    <stop offset="100%" stopColor="#0d9668" />
                </linearGradient>
                <filter id={`${id}-shadow`}>
                    <feDropShadow dx="0" dy="4" stdDeviation="6" floodColor="#6366f1" floodOpacity="0.12" />
                </filter>
            </defs>

            {/* Background radial */}
            <circle cx="70" cy="70" r="58" fill={`url(#${id}-folder)`} className="float-slow" />

            {/* Folder back */}
            <g className="float-slow" filter={`url(#${id}-shadow)`}>
                <path
                    d="M28 52 L28 100 Q28 106 34 106 L106 106 Q112 106 112 100 L112 60 Q112 54 106 54 L68 54 L62 46 Q60 42 56 42 L34 42 Q28 42 28 48 Z"
                    fill={`url(#${id}-folderFace)`}
                    stroke="var(--color-border, #e5e5e5)"
                    strokeWidth="1.2"
                />
                {/* Folder tab accent */}
                <path d="M28 48 Q28 42 34 42 L56 42 Q60 42 62 46 L68 54 L28 54 Z" fill={`url(#${id}-tab)`} opacity="0.8" />
            </g>

            {/* Folder content lines */}
            <g opacity="0.3">
                <line x1="42" y1="68" x2="98" y2="68" stroke="var(--color-text-muted, #a3a3a3)" strokeWidth="2" strokeLinecap="round" />
                <line x1="42" y1="78" x2="85" y2="78" stroke="var(--color-text-muted, #a3a3a3)" strokeWidth="2" strokeLinecap="round" />
                <line x1="42" y1="88" x2="72" y2="88" stroke="var(--color-text-muted, #a3a3a3)" strokeWidth="2" strokeLinecap="round" />
            </g>

            {/* Plus icon */}
            <g className="plus-anim">
                <circle cx="70" cy="78" r="16" fill="#6366f1" opacity="0.1" />
                <line x1="70" y1="70" x2="70" y2="86" stroke="#6366f1" strokeWidth="2.5" strokeLinecap="round" />
                <line x1="62" y1="78" x2="78" y2="78" stroke="#6366f1" strokeWidth="2.5" strokeLinecap="round" />
            </g>

            {/* Sparkles */}
            <path d="M22 32 L23.5 27 L25 32 L30 33.5 L25 35 L23.5 40 L22 35 L17 33.5 Z" fill="#FFD700" opacity="0.5" className="sparkle-1" />
            <path d="M112 30 L113 27 L114 30 L117 31 L114 32 L113 35 L112 32 L109 31 Z" fill="#6366f1" opacity="0.5" className="sparkle-2" />
        </svg>
    )
}

const NoResults = () => {
    const id = `nr-${Math.random().toString(36).slice(2, 6)}`
    return (
        <svg width="140" height="140" viewBox="0 0 140 140" fill="none" className="empty-illustration">
            <defs>
                <linearGradient id={`${id}-bg`} x1="0%" y1="0%" x2="100%" y2="100%">
                    <stop offset="0%" stopColor="#f59e0b" stopOpacity="0.08" />
                    <stop offset="100%" stopColor="#6366f1" stopOpacity="0.05" />
                </linearGradient>
                <linearGradient id={`${id}-glass`} x1="30%" y1="0%" x2="70%" y2="100%">
                    <stop offset="0%" stopColor="var(--color-bg-primary, #fff)" />
                    <stop offset="100%" stopColor="var(--color-bg-tertiary, #f3f4f6)" />
                </linearGradient>
                <linearGradient id={`${id}-handle`} x1="0%" y1="0%" x2="100%" y2="100%">
                    <stop offset="0%" stopColor="#888" />
                    <stop offset="100%" stopColor="#555" />
                </linearGradient>
                <filter id={`${id}-shadowSearch`}>
                    <feDropShadow dx="0" dy="3" stdDeviation="5" floodColor="#000" floodOpacity="0.08" />
                </filter>
            </defs>

            {/* Background */}
            <circle cx="70" cy="70" r="58" fill={`url(#${id}-bg)`} />

            {/* Magnifying glass */}
            <g className="float-slow" filter={`url(#${id}-shadowSearch)`}>
                {/* Glass body */}
                <circle cx="58" cy="58" r="28" fill={`url(#${id}-glass)`} stroke="var(--color-border, #e5e5e5)" strokeWidth="2.5" />

                {/* Glass shine */}
                <path d="M42 48 Q48 38 62 42" stroke="white" strokeWidth="2" fill="none" strokeLinecap="round" opacity="0.5" />

                {/* Handle */}
                <line x1="78" y1="78" x2="105" y2="105" stroke={`url(#${id}-handle)`} strokeWidth="6" strokeLinecap="round" />
                <line x1="78" y1="78" x2="105" y2="105" stroke="white" strokeWidth="1.5" strokeLinecap="round" opacity="0.15" />
            </g>

            {/* Question mark inside glass */}
            <g className="question-bounce">
                <text x="50" y="66" fontSize="26" fontWeight="700" fill="#6366f1" opacity="0.6" fontFamily="Inter, sans-serif">?</text>
            </g>

            {/* Mini document icons */}
            <g opacity="0.25">
                <rect x="95" y="28" width="16" height="20" rx="3" fill="var(--color-text-muted, #a3a3a3)" />
                <line x1="99" y1="34" x2="107" y2="34" stroke="white" strokeWidth="1.5" strokeLinecap="round" />
                <line x1="99" y1="39" x2="105" y2="39" stroke="white" strokeWidth="1.5" strokeLinecap="round" />
            </g>

            {/* Sparkles */}
            <path d="M95 22 L96.5 17 L98 22 L103 23.5 L98 25 L96.5 30 L95 25 L90 23.5 Z" fill="#6366f1" opacity="0.5" className="sparkle-1" />
            <path d="M20 95 L21.5 91 L23 95 L27 96.5 L23 98 L21.5 102 L20 98 L16 96.5 Z" fill="#f59e0b" opacity="0.45" className="sparkle-2" />
        </svg>
    )
}

const OfflineState = () => {
    const id = `off-${Math.random().toString(36).slice(2, 6)}`
    return (
        <svg width="140" height="140" viewBox="0 0 140 140" fill="none" className="empty-illustration">
            <defs>
                <linearGradient id={`${id}-bg`} x1="0%" y1="0%" x2="100%" y2="100%">
                    <stop offset="0%" stopColor="#ef4444" stopOpacity="0.08" />
                    <stop offset="100%" stopColor="#6366f1" stopOpacity="0.05" />
                </linearGradient>
                <linearGradient id={`${id}-cloud`} x1="50%" y1="0%" x2="50%" y2="100%">
                    <stop offset="0%" stopColor="var(--color-bg-elevated, #fff)" />
                    <stop offset="100%" stopColor="var(--color-bg-tertiary, #f3f4f6)" />
                </linearGradient>
                <filter id={`${id}-cloudShadow`}>
                    <feDropShadow dx="0" dy="3" stdDeviation="5" floodColor="#ef4444" floodOpacity="0.1" />
                </filter>
            </defs>

            {/* Background */}
            <circle cx="70" cy="70" r="58" fill={`url(#${id}-bg)`} />

            {/* Cloud */}
            <g className="float-slow" filter={`url(#${id}-cloudShadow)`}>
                <path
                    d="M38 80 Q20 80 20 64 Q20 48 38 46 Q42 28 60 28 Q76 28 82 42 Q100 42 100 58 Q100 80 82 80 Z"
                    fill={`url(#${id}-cloud)`}
                    stroke="var(--color-border, #e5e5e5)"
                    strokeWidth="1.5"
                />
                {/* Cloud highlight */}
                <path d="M40 48 Q48 30 62 32 Q70 30 76 38" stroke="white" strokeWidth="1.5" fill="none" strokeLinecap="round" opacity="0.4" />
            </g>

            {/* X mark - disconnected */}
            <g>
                <circle cx="60" cy="58" r="14" fill="#ef4444" opacity="0.1" />
                <line x1="53" y1="51" x2="67" y2="65" stroke="#ef4444" strokeWidth="3" strokeLinecap="round" />
                <line x1="67" y1="51" x2="53" y2="65" stroke="#ef4444" strokeWidth="3" strokeLinecap="round" />
            </g>

            {/* Lightning bolt */}
            <g className="lightning-flash">
                <path
                    d="M62 88 L56 100 L64 98 L58 112"
                    stroke="#FFD700"
                    strokeWidth="2.5"
                    strokeLinecap="round"
                    strokeLinejoin="round"
                    fill="none"
                />
            </g>

            {/* Wifi icon with slash */}
            <g opacity="0.3" transform="translate(90, 75)">
                <path d="M0 12 Q8 4 16 12" stroke="var(--color-text-muted, #a3a3a3)" strokeWidth="2" fill="none" strokeLinecap="round" />
                <path d="M-5 7 Q8 -3 21 7" stroke="var(--color-text-muted, #a3a3a3)" strokeWidth="2" fill="none" strokeLinecap="round" />
                <circle cx="8" cy="16" r="2" fill="var(--color-text-muted, #a3a3a3)" />
                {/* Slash */}
                <line x1="-2" y1="18" x2="18" y2="2" stroke="#ef4444" strokeWidth="2" strokeLinecap="round" />
            </g>

            {/* Sparkle */}
            <path d="M108 32 L109.5 27 L111 32 L116 33.5 L111 35 L109.5 40 L108 35 L103 33.5 Z" fill="#ef4444" opacity="0.35" className="sparkle-1" />
        </svg>
    )
}

const illustrations: Record<string, JSX.Element> = {
    'no-conversations': <NoConversations />,
    'no-workspace': <NoWorkspace />,
    'no-results': <NoResults />,
    'offline': <OfflineState />,
}

const EmptyState = ({ type, title, description }: EmptyStateProps) => {
    const defaultTitles: Record<string, string> = {
        'no-conversations': 'No conversations yet',
        'no-workspace': 'Create your first workspace',
        'no-results': 'No results found',
        'offline': 'Connection lost',
    }

    const defaultDescriptions: Record<string, string> = {
        'no-conversations': 'Start a new conversation to get going',
        'no-workspace': 'Organize your work with workspaces',
        'no-results': 'Try a different search term',
        'offline': 'Check your connection and try again',
    }

    return (
        <div className="empty-state">
            <div className="empty-state-icon">
                {illustrations[type]}
            </div>
            <h3 className="empty-state-title">{title || defaultTitles[type]}</h3>
            <p className="empty-state-description">{description || defaultDescriptions[type]}</p>
        </div>
    )
}

export default EmptyState
