/**
 * PigTex Premium SVG Mascot - v2
 * A lovable pig with gradients, glass reflections, particle effects 
 * Fully animated with CSS + SVG SMIL animations
 */

interface PigMascotProps {
    size?: number
    className?: string
    animate?: boolean
    mood?: 'happy' | 'thinking' | 'greeting'
}

const PigMascot = ({ size = 120, className = '', animate = true, mood = 'greeting' }: PigMascotProps) => {
    const id = `pig-${Math.random().toString(36).slice(2, 8)}`

    return (
        <svg
            width={size}
            height={size}
            viewBox="0 0 200 200"
            fill="none"
            xmlns="http://www.w3.org/2000/svg"
            className={`pig-mascot ${animate ? 'pig-animated' : ''} ${className}`}
        >
            <defs>
                {/* Ambient glow */}
                <radialGradient id={`${id}-glow`} cx="50%" cy="50%" r="50%">
                    <stop offset="0%" stopColor="#6366f1" stopOpacity="0.18" />
                    <stop offset="60%" stopColor="#6366f1" stopOpacity="0.06" />
                    <stop offset="100%" stopColor="#6366f1" stopOpacity="0" />
                </radialGradient>

                {/* Body main gradient - warm pink */}
                <radialGradient id={`${id}-body`} cx="45%" cy="35%" r="65%">
                    <stop offset="0%" stopColor="#FFD0D8" />
                    <stop offset="50%" stopColor="#FFB6C1" />
                    <stop offset="100%" stopColor="#FF9DAE" />
                </radialGradient>

                {/* Body highlight - top shine */}
                <radialGradient id={`${id}-bodyShine`} cx="38%" cy="28%" r="40%">
                    <stop offset="0%" stopColor="#fff" stopOpacity="0.35" />
                    <stop offset="100%" stopColor="#fff" stopOpacity="0" />
                </radialGradient>

                {/* Ear gradient */}
                <linearGradient id={`${id}-ear`} x1="50%" y1="0%" x2="50%" y2="100%">
                    <stop offset="0%" stopColor="#FFA8B8" />
                    <stop offset="100%" stopColor="#E87090" />
                </linearGradient>

                {/* Inner ear */}
                <radialGradient id={`${id}-earInner`} cx="50%" cy="45%" r="50%">
                    <stop offset="0%" stopColor="#FFD4DD" />
                    <stop offset="100%" stopColor="#FFBBC8" />
                </radialGradient>

                {/* Scarf gradient */}
                <linearGradient id={`${id}-scarf`} x1="0%" y1="0%" x2="100%" y2="100%">
                    <stop offset="0%" stopColor="#12C28F" />
                    <stop offset="50%" stopColor="#6366f1" />
                    <stop offset="100%" stopColor="#0B8A6A" />
                </linearGradient>

                {/* Scarf shadow */}
                <linearGradient id={`${id}-scarfShadow`} x1="50%" y1="0%" x2="50%" y2="100%">
                    <stop offset="0%" stopColor="#0B8A6A" stopOpacity="0" />
                    <stop offset="100%" stopColor="#0B8A6A" stopOpacity="0.4" />
                </linearGradient>

                {/* Glasses lens tint */}
                <radialGradient id={`${id}-lens`} cx="35%" cy="30%" r="70%">
                    <stop offset="0%" stopColor="#E8F5FF" stopOpacity="0.15" />
                    <stop offset="100%" stopColor="#B0D4F1" stopOpacity="0.08" />
                </radialGradient>

                {/* Snout gradient */}
                <radialGradient id={`${id}-snout`} cx="50%" cy="40%" r="55%">
                    <stop offset="0%" stopColor="#FFCBD5" />
                    <stop offset="100%" stopColor="#FF9DAE" />
                </radialGradient>

                {/* Eye white gradient */}
                <radialGradient id={`${id}-eyeWhite`} cx="40%" cy="35%" r="60%">
                    <stop offset="0%" stopColor="#FFFFFF" />
                    <stop offset="100%" stopColor="#F0F0F5" />
                </radialGradient>

                {/* Soft shadow filter */}
                <filter id={`${id}-shadow`} x="-20%" y="-20%" width="140%" height="140%">
                    <feDropShadow dx="0" dy="3" stdDeviation="4" floodColor="#E87090" floodOpacity="0.2" />
                </filter>

                {/* Glasses frame shadow */}
                <filter id={`${id}-glassShadow`} x="-10%" y="-10%" width="120%" height="120%">
                    <feDropShadow dx="0" dy="1" stdDeviation="1.5" floodColor="#000" floodOpacity="0.12" />
                </filter>

                {/* Star shape clip */}
                <clipPath id={`${id}-starClip`}>
                    <path d="M0,-8 L2,-2 L8,-2 L3,2 L5,8 L0,4 L-5,8 L-3,2 L-8,-2 L-2,-2 Z" />
                </clipPath>
            </defs>

            {/* Background glow */}
            <circle cx="100" cy="100" r="96" fill={`url(#${id}-glow)`} className="pig-glow" />

            {/* Floating particles */}
            <g className="pig-particles">
                <circle cx="30" cy="35" r="1.5" fill="#6366f1" opacity="0.5">
                    {animate && <animate attributeName="opacity" values="0.2;0.7;0.2" dur="3s" repeatCount="indefinite" />}
                    {animate && <animate attributeName="cy" values="35;28;35" dur="4s" repeatCount="indefinite" />}
                </circle>
                <circle cx="170" cy="45" r="1" fill="#FFD700" opacity="0.4">
                    {animate && <animate attributeName="opacity" values="0.1;0.6;0.1" dur="2.5s" repeatCount="indefinite" />}
                    {animate && <animate attributeName="cy" values="45;38;45" dur="3.5s" repeatCount="indefinite" />}
                </circle>
                <circle cx="25" cy="120" r="1.2" fill="#ec4899" opacity="0.3">
                    {animate && <animate attributeName="opacity" values="0.15;0.5;0.15" dur="3.2s" repeatCount="indefinite" />}
                    {animate && <animate attributeName="cy" values="120;114;120" dur="4.2s" repeatCount="indefinite" />}
                </circle>
                <circle cx="175" cy="130" r="1.3" fill="#6366f1" opacity="0.35">
                    {animate && <animate attributeName="opacity" values="0.2;0.6;0.2" dur="2.8s" repeatCount="indefinite" />}
                    {animate && <animate attributeName="cx" values="175;170;175" dur="3.8s" repeatCount="indefinite" />}
                </circle>
            </g>

            {/* Left Ear */}
            <g className="pig-ear-left">
                <ellipse cx="62" cy="50" rx="23" ry="30" fill={`url(#${id}-ear)`} />
                <ellipse cx="62" cy="50" rx="15" ry="20" fill={`url(#${id}-earInner)`} />
                {/* Ear shine */}
                <ellipse cx="58" cy="42" rx="6" ry="8" fill="white" opacity="0.15" />
            </g>

            {/* Right Ear */}
            <g className="pig-ear-right">
                <ellipse cx="138" cy="50" rx="23" ry="30" fill={`url(#${id}-ear)`} />
                <ellipse cx="138" cy="50" rx="15" ry="20" fill={`url(#${id}-earInner)`} />
                <ellipse cx="134" cy="42" rx="6" ry="8" fill="white" opacity="0.15" />
            </g>

            {/* Body / Head */}
            <g filter={`url(#${id}-shadow)`}>
                <circle cx="100" cy="105" r="56" fill={`url(#${id}-body)`} />
            </g>
            {/* Body shine overlay */}
            <circle cx="100" cy="105" r="54" fill={`url(#${id}-bodyShine)`} />
            {/* Body rim light */}
            <circle cx="100" cy="105" r="55" fill="none" stroke="#E87090" strokeWidth="0.8" strokeOpacity="0.25" />

            {/* Scarf */}
            <g className="pig-scarf">
                <path
                    d="M58 128 Q68 140 100 145 Q132 140 142 128 L140 138 Q125 154 100 158 Q75 154 60 138 Z"
                    fill={`url(#${id}-scarf)`}
                />
                {/* Scarf fold shadow */}
                <path
                    d="M65 134 Q80 145 100 148 Q120 145 135 134"
                    fill="none"
                    stroke="#0B8A6A"
                    strokeWidth="1"
                    strokeOpacity="0.3"
                />
                {/* Scarf knot */}
                <circle cx="100" cy="150" r="7" fill="#0d9668" />
                <circle cx="100" cy="150" r="5" fill={`url(#${id}-scarf)`} />
                {/* Scarf tails */}
                <path d="M94 150 Q90 160 86 170 Q90 167 94 170" fill={`url(#${id}-scarf)`} opacity="0.9" />
                <path d="M106 150 Q110 160 114 170 Q110 167 106 170" fill={`url(#${id}-scarf)`} opacity="0.9" />
                {/* Scarf highlight */}
                <path
                    d="M70 132 Q85 141 100 143"
                    fill="none"
                    stroke="white"
                    strokeWidth="1.2"
                    strokeOpacity="0.2"
                    strokeLinecap="round"
                />
            </g>

            {/* Snout */}
            <ellipse cx="100" cy="112" rx="21" ry="15" fill={`url(#${id}-snout)`} />
            <ellipse cx="100" cy="111" rx="18" ry="12" fill="#FFD0D8" />
            {/* Snout highlight */}
            <ellipse cx="96" cy="107" rx="8" ry="4" fill="white" opacity="0.2" />
            {/* Nostrils */}
            <ellipse cx="92" cy="112" rx="4.5" ry="3.5" fill="#E87090" />
            <ellipse cx="108" cy="112" rx="4.5" ry="3.5" fill="#E87090" />
            {/* Nostril shine */}
            <ellipse cx="91" cy="111" rx="1.5" ry="1" fill="#F0A0B0" opacity="0.5" />
            <ellipse cx="107" cy="111" rx="1.5" ry="1" fill="#F0A0B0" opacity="0.5" />

            {/* Eyes */}
            <g className="pig-eyes">
                {/* Left eye */}
                <circle cx="80" cy="90" r="11" fill={`url(#${id}-eyeWhite)`} />
                <circle cx="82" cy="89" r="6.5" fill="#2D2D2D" />
                {/* Pupil gradient */}
                <circle cx="82" cy="89" r="4" fill="#1a1a1a" />
                {/* Eye catchlight */}
                <circle cx="84" cy="87" r="2.8" fill="white" />
                <circle cx="80" cy="91" r="1.3" fill="white" opacity="0.5" />

                {/* Right eye */}
                <circle cx="120" cy="90" r="11" fill={`url(#${id}-eyeWhite)`} />
                <circle cx="122" cy="89" r="6.5" fill="#2D2D2D" />
                <circle cx="122" cy="89" r="4" fill="#1a1a1a" />
                <circle cx="124" cy="87" r="2.8" fill="white" />
                <circle cx="120" cy="91" r="1.3" fill="white" opacity="0.5" />
            </g>

            {/* Glasses - premium with lens tint */}
            <g className="pig-glasses" filter={`url(#${id}-glassShadow)`}>
                {/* Left lens fill */}
                <circle cx="80" cy="90" r="14.5" fill={`url(#${id}-lens)`} />
                {/* Right lens fill */}
                <circle cx="120" cy="90" r="14.5" fill={`url(#${id}-lens)`} />

                {/* Frame */}
                <g stroke="#444" strokeWidth="2.2" fill="none" strokeLinecap="round">
                    <circle cx="80" cy="90" r="14.5" />
                    <circle cx="120" cy="90" r="14.5" />
                    {/* Bridge */}
                    <path d="M94.5 89 Q100 84 105.5 89" />
                    {/* Temple left */}
                    <path d="M65.5 88 L54 81" />
                    {/* Temple right */}
                    <path d="M134.5 88 L146 81" />
                </g>
                {/* Lens reflection */}
                <path d="M72 83 Q76 79 81 82" stroke="white" strokeWidth="1.2" fill="none" strokeLinecap="round" opacity="0.35" />
                <path d="M112 83 Q116 79 121 82" stroke="white" strokeWidth="1.2" fill="none" strokeLinecap="round" opacity="0.35" />
            </g>

            {/* Mouth / Smile */}
            <path
                d="M87 121 Q94 128 100 128 Q106 128 113 121"
                stroke="#E06880"
                strokeWidth="2.2"
                fill="none"
                strokeLinecap="round"
                className="pig-smile"
            />

            {/* Blush */}
            <ellipse cx="66" cy="106" rx="9" ry="6" fill="#FF8FA3" fillOpacity="0.3" />
            <ellipse cx="134" cy="106" rx="9" ry="6" fill="#FF8FA3" fillOpacity="0.3" />

            {/* Waving hand/hoof */}
            <g className="pig-wave">
                {/* Arm stub */}
                <ellipse cx="152" cy="88" rx="7" ry="14" fill="#FFB6C1" transform="rotate(-20 152 88)" />
                {/* Hoof */}
                <circle cx="155" cy="73" r="13" fill="#FFB6C1" />
                <circle cx="155" cy="73" r="11" fill="#FFD4DD" />
                {/* Hoof shine */}
                <ellipse cx="152" cy="68" rx="4" ry="3" fill="white" opacity="0.2" />
                {/* Little fingers */}
                <circle cx="148" cy="63" r="4.5" fill="#FFB6C1" />
                <circle cx="156" cy="61" r="4.5" fill="#FFB6C1" />
                <circle cx="164" cy="65" r="4.5" fill="#FFB6C1" />
                {/* Finger tips */}
                <circle cx="148" cy="63" r="3" fill="#FFD4DD" />
                <circle cx="156" cy="61" r="3" fill="#FFD4DD" />
                <circle cx="164" cy="65" r="3" fill="#FFD4DD" />
            </g>

            {/* Sparkle Stars - diamond style */}
            <g className="pig-sparkles">
                {/* 4-point star */}
                <path d="M38 38 L40 32 L42 38 L48 40 L42 42 L40 48 L38 42 L32 40 Z" fill="#6366f1" fillOpacity="0.6" />
                <path d="M160 35 L161.5 30 L163 35 L168 36.5 L163 38 L161.5 43 L160 38 L155 36.5 Z" fill="#FFD700" fillOpacity="0.55" />
                <path d="M33 100 L34.5 96 L36 100 L40 101.5 L36 103 L34.5 107 L33 103 L29 101.5 Z" fill="#ec4899" fillOpacity="0.4" />
                <path d="M170 115 L171 112 L172 115 L175 116 L172 117 L171 120 L170 117 L167 116 Z" fill="#6366f1" fillOpacity="0.45" />
            </g>

            {/* Small floating hearts (mood: happy) */}
            {mood === 'happy' && (
                <g className="pig-hearts">
                    <path d="M40 60 C40 56, 46 56, 46 60 C46 64, 40 68, 40 68 C40 68, 34 64, 34 60 C34 56, 40 56, 40 60 Z" fill="#FF6B8A" opacity="0.5">
                        {animate && <animate attributeName="opacity" values="0.2;0.6;0.2" dur="2s" repeatCount="indefinite" />}
                    </path>
                </g>
            )}

            {/* Thinking dots (mood: thinking) */}
            {mood === 'thinking' && (
                <g className="pig-think-bubbles">
                    <circle cx="50" cy="50" r="3" fill="var(--color-text-muted, #a3a3a3)" opacity="0.4">
                        {animate && <animate attributeName="opacity" values="0.2;0.6;0.2" dur="1.5s" repeatCount="indefinite" />}
                    </circle>
                    <circle cx="40" cy="38" r="5" fill="var(--color-text-muted, #a3a3a3)" opacity="0.3">
                        {animate && <animate attributeName="opacity" values="0.15;0.5;0.15" dur="1.5s" begin="0.3s" repeatCount="indefinite" />}
                    </circle>
                    <circle cx="28" cy="25" r="7" fill="var(--color-text-muted, #a3a3a3)" opacity="0.25">
                        {animate && <animate attributeName="opacity" values="0.1;0.4;0.1" dur="1.5s" begin="0.6s" repeatCount="indefinite" />}
                    </circle>
                </g>
            )}
        </svg>
    )
}

export default PigMascot
