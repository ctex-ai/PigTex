/**
 * AI Bot Avatar SVG - premium animated avatar
 * Used in chat messages and headers
 */

interface BotAvatarProps {
    size?: number
    className?: string
    animated?: boolean
}

const BotAvatar = ({ size = 24, className = '', animated = false }: BotAvatarProps) => {
    const id = `bot-${Math.random().toString(36).slice(2, 6)}`

    return (
        <svg
            width={size}
            height={size}
            viewBox="0 0 28 28"
            fill="none"
            xmlns="http://www.w3.org/2000/svg"
            className={`bot-avatar-svg ${animated ? 'bot-animated' : ''} ${className}`}
        >
            <defs>
                <linearGradient id={`${id}-bg`} x1="0%" y1="0%" x2="100%" y2="100%">
                    <stop offset="0%" stopColor="#818cf8" />
                    <stop offset="100%" stopColor="#6366f1" />
                </linearGradient>
                <radialGradient id={`${id}-shine`} cx="30%" cy="25%" r="50%">
                    <stop offset="0%" stopColor="#fff" stopOpacity="0.3" />
                    <stop offset="100%" stopColor="#fff" stopOpacity="0" />
                </radialGradient>
                <filter id={`${id}-glow`}>
                    <feDropShadow dx="0" dy="1" stdDeviation="1" floodColor="#6366f1" floodOpacity="0.3" />
                </filter>
            </defs>

            {/* Background */}
            <rect width="28" height="28" rx="8" fill={`url(#${id}-bg)`} />
            <rect width="28" height="28" rx="8" fill={`url(#${id}-shine)`} />

            {/* Neural spark icon */}
            <g transform="translate(14, 14)" filter={`url(#${id}-glow)`}>
                {/* Diamond core */}
                <path d="M0,-7 L2,-2 L7,0 L2,2 L0,7 L-2,2 L-7,0 L-2,-2 Z" fill="white" opacity="0.9" />

                {/* Orbital dots */}
                <circle cx="0" cy="-9" r="1" fill="white" opacity="0.6">
                    {animated && <animateTransform attributeName="transform" type="rotate" values="0;360" dur="4s" repeatCount="indefinite" />}
                </circle>
                <circle cx="0" cy="9" r="0.8" fill="white" opacity="0.4">
                    {animated && <animateTransform attributeName="transform" type="rotate" values="360;0" dur="3s" repeatCount="indefinite" />}
                </circle>
            </g>
        </svg>
    )
}

export default BotAvatar
