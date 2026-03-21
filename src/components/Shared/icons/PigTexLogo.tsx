/**
 * PigTex Brand Logo SVG
 * Scalable vector logo replacing emoji
 */

interface PigTexLogoProps {
    size?: number
    className?: string
}

const PigTexLogo = ({ size = 20, className = '' }: PigTexLogoProps) => {
    const id = `logo-${Math.random().toString(36).slice(2, 6)}`
    return (
        <svg
            width={size}
            height={size}
            viewBox="0 0 32 32"
            fill="none"
            xmlns="http://www.w3.org/2000/svg"
            className={`pigtex-logo ${className}`}
        >
            <defs>
                <linearGradient id={`${id}-bg`} x1="0%" y1="0%" x2="100%" y2="100%">
                    <stop offset="0%" stopColor="#12C28F" />
                    <stop offset="100%" stopColor="#0B8A6A" />
                </linearGradient>
                <linearGradient id={`${id}-pig`} x1="50%" y1="10%" x2="50%" y2="90%">
                    <stop offset="0%" stopColor="#FFD4DD" />
                    <stop offset="100%" stopColor="#FFB6C1" />
                </linearGradient>
                <radialGradient id={`${id}-shine`} cx="35%" cy="30%" r="50%">
                    <stop offset="0%" stopColor="#fff" stopOpacity="0.25" />
                    <stop offset="100%" stopColor="#fff" stopOpacity="0" />
                </radialGradient>
            </defs>

            {/* Background rounded square */}
            <rect width="32" height="32" rx="8" fill={`url(#${id}-bg)`} />
            {/* Shine overlay */}
            <rect width="32" height="32" rx="8" fill={`url(#${id}-shine)`} />

            {/* Pig face - simplified for small size */}
            {/* Ears */}
            <ellipse cx="10" cy="9" rx="4" ry="5" fill={`url(#${id}-pig)`} />
            <ellipse cx="22" cy="9" rx="4" ry="5" fill={`url(#${id}-pig)`} />
            <ellipse cx="10" cy="9" rx="2.5" ry="3" fill="#FFE0E8" />
            <ellipse cx="22" cy="9" rx="2.5" ry="3" fill="#FFE0E8" />

            {/* Head */}
            <circle cx="16" cy="16" r="10" fill={`url(#${id}-pig)`} />

            {/* Snout */}
            <ellipse cx="16" cy="18" rx="5" ry="3.5" fill="#FFA8B8" />
            <ellipse cx="16" cy="18" rx="4" ry="2.5" fill="#FFD0D8" />
            {/* Nostrils */}
            <circle cx="14" cy="18" r="1.2" fill="#E87090" />
            <circle cx="18" cy="18" r="1.2" fill="#E87090" />

            {/* Eyes */}
            <circle cx="12" cy="14" r="2" fill="white" />
            <circle cx="20" cy="14" r="2" fill="white" />
            <circle cx="12.5" cy="13.8" r="1.2" fill="#2D2D2D" />
            <circle cx="20.5" cy="13.8" r="1.2" fill="#2D2D2D" />
            <circle cx="13" cy="13.2" r="0.5" fill="white" />
            <circle cx="21" cy="13.2" r="0.5" fill="white" />

            {/* Tiny glasses */}
            <g stroke="white" strokeWidth="0.8" fill="none" strokeOpacity="0.7">
                <circle cx="12" cy="14" r="2.8" />
                <circle cx="20" cy="14" r="2.8" />
                <path d="M14.8 13.8 Q16 12.5 17.2 13.8" />
            </g>

            {/* Cheeks */}
            <circle cx="9" cy="17" r="1.8" fill="#FF8FA3" opacity="0.3" />
            <circle cx="23" cy="17" r="1.8" fill="#FF8FA3" opacity="0.3" />
        </svg>
    )
}

export default PigTexLogo
