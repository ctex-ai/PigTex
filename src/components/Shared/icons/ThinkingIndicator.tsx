/**
 * Animated AI Thinking Indicator - Premium v2
 * Neural network-inspired pulsing animation with orbiting particles
 */

interface ThinkingIndicatorProps {
    size?: 'sm' | 'md' | 'lg'
    label?: string
}

const ThinkingIndicator = ({ size = 'md', label }: ThinkingIndicatorProps) => {
    const dotSize = size === 'sm' ? 4 : size === 'md' ? 6 : 8
    const iconSize = size === 'sm' ? 18 : size === 'md' ? 22 : 30
    const id = `think-${Math.random().toString(36).slice(2, 6)}`

    return (
        <div className={`thinking-indicator thinking-${size}`}>
            <div className="thinking-icon">
                <svg width={iconSize} height={iconSize} viewBox="0 0 28 28" fill="none">
                    <defs>
                        <linearGradient id={`${id}-grad`} x1="0%" y1="0%" x2="100%" y2="100%">
                            <stop offset="0%" stopColor="#6366f1" />
                            <stop offset="100%" stopColor="#06b6d4" />
                        </linearGradient>
                        <filter id={`${id}-glow`}>
                            <feGaussianBlur stdDeviation="1.5" result="blur" />
                            <feMerge>
                                <feMergeNode in="blur" />
                                <feMergeNode in="SourceGraphic" />
                            </feMerge>
                        </filter>
                    </defs>

                    {/* Outer orbit ring */}
                    <circle cx="14" cy="14" r="12" stroke={`url(#${id}-grad)`} strokeWidth="1.5" fill="none" strokeDasharray="4 3" className="thinking-orbit" />

                    {/* Inner ring */}
                    <circle cx="14" cy="14" r="8" stroke="#6366f1" strokeWidth="1" fill="none" opacity="0.3" strokeDasharray="2 4" className="thinking-orbit-reverse" />

                    {/* Center brain/sparkle icon */}
                    <g filter={`url(#${id}-glow)`}>
                        {/* Neural spark */}
                        <path
                            d="M14 6 L15.2 11.5 L20 10 L16 13 L20 16 L15.2 14.5 L14 20 L12.8 14.5 L8 16 L12 13 L8 10 L12.8 11.5 Z"
                            fill={`url(#${id}-grad)`}
                            opacity="0.8"
                            className="thinking-core"
                        />
                    </g>

                    {/* Orbiting particles */}
                    <circle r="1.5" fill="#6366f1" className="thinking-particle-1">
                        <animateMotion dur="2.5s" repeatCount="indefinite" path="M14,2 A12,12 0 1,1 13.99,2" />
                    </circle>
                    <circle r="1" fill="#06b6d4" className="thinking-particle-2">
                        <animateMotion dur="3s" repeatCount="indefinite" begin="0.8s" path="M14,2 A12,12 0 1,1 13.99,2" />
                    </circle>
                    <circle r="1.2" fill="#FFD700" className="thinking-particle-3">
                        <animateMotion dur="3.5s" repeatCount="indefinite" begin="1.6s" path="M14,2 A12,12 0 1,1 13.99,2" />
                    </circle>
                </svg>
            </div>
            <div className="thinking-dots">
                <span style={{ width: dotSize, height: dotSize }} />
                <span style={{ width: dotSize, height: dotSize }} />
                <span style={{ width: dotSize, height: dotSize }} />
            </div>
            {label && <span className="thinking-label">{label}</span>}
        </div>
    )
}

export default ThinkingIndicator
