import { useState, useRef, useEffect, useCallback, useLayoutEffect } from 'react'
import { motion, AnimatePresence } from 'framer-motion'
import { useMemo } from 'react'
import {
    Send,
    Sparkles,
    Copy,
    ThumbsUp,
    ThumbsDown,
    FileText,
    ArrowRight,
    Plus,
    ChevronDown,
    Image,
    Paperclip,
    Code,
    Globe,
    RotateCcw,
    Square,
    Check,
    Save,
    Wrench,
    Loader2,
    AlertCircle,
    FilePlus2,
    FileEdit,
    FolderPlus,
    Trash2,
    ArrowRightLeft
} from 'lucide-react'
import {
    getModels,
    getModelsWithCredentials,
    AIModel,
    AIModelProviderFlag,
    ApiConnectivityIssue,
    diagnoseApiConnectivityIssue,
    streamSmartChat,
    SmartChatRequest,
    StreamUsageMetadata,
    WebCitation,
    WebSearchClaimVerification,
    WebSearchMetadata,
    MemoryContextMetadata,
    getLocalConversations,
    getLocalConversation,
    getLearningLiveState,
    createConversation,
    addConversationMessage,
    updateConversationMessage,
    fileToBase64,
    uploadFiles,
    generateImages,
    editImage,
    synthesizeSpeech,
    generateVideo,
    getVideoGenerationTask,
    ImageAttachment,
    DocumentAttachment,
    resolveImageUrl,
    resolveProtectedMediaSrc,
    filterModelsByCapability,
    modelSupportsCapability,
    transportSupportsCapability
} from '../../../services/api'
import type { LearningChatMetadata, LearningLiveState, LearningState } from '../../../services/api'
import { PigTexSettings, resolveApiProviderForRequest } from '../../../services/settings'
import { useI18n } from '../../../contexts/I18nContext'
import MessageRenderer from '../../Shared/MessageRenderer'
import ProtectedImage from '../../Shared/ProtectedImage'
import { copyToClipboard, showError, showInfo, showSuccess } from '../../Shared/Toast'
import pigtexAvatarUrl from '../../../../assets/avata_pigtex.png'
import {
    createAiFileExecutionContext,
    executeAiFileActionsFromParsed,
    invalidateAiFileExecutionContextForAction,
    parseAiFileActions,
    ParsedAiFileAction,
    AiFileActionProgressEvent
} from '../../../utils/aiFileActions'
import { StreamingAction, StreamingActionParser } from '../../../utils/streamingActionParser'
import {
    buildFileAgentPlannerInstruction,
    parseFileAgentPlannerActions,
    buildFileAgentToolContextMessage,
    isFileAgentContextPayload,
    parseFileAgentPlannerEnvelope
} from '../../../features/fileAgent/plannerProtocol'
import {
    createFileAgentActionTracker,
    filterExecutableFileAgentActions,
    noteFailedFileAgentAction,
    noteSuccessfulFileAgentAction,
    resolveActionFocusRelativePath,
    serializeAiActionBatch,
    shouldContinueWithToolResult
} from '../../../features/fileAgent/controller'
import {
    buildWorkspaceReviewMessage,
    collectWorkspaceReviewContext,
    shouldUseWorkspaceReviewController
} from '../../../features/fileAgent/reviewContext'
import {
    useMention,
    flattenFileTree,
    filterMentionItems,
    MentionItem
} from '../../../hooks/useMention'
import MentionPopup from '../../Shared/MentionPopup/MentionPopup'
import './ChatPanel.css'

const ALLOWED_IMAGE_TYPES = ['image/jpeg', 'image/jpg', 'image/png', 'image/gif', 'image/webp', 'image/bmp']
const MAX_IMAGE_SIZE = 10 * 1024 * 1024 // 10MB
const MAX_IMAGES = 5
const ALLOWED_FILE_TYPES = [
    'text/plain',
    'text/markdown',
    'application/pdf',
    'application/vnd.openxmlformats-officedocument.wordprocessingml.document'
]
const ALLOWED_FILE_EXTENSIONS = ['.txt', '.md', '.markdown', '.pdf', '.docx']
const MAX_FILE_SIZE = 20 * 1024 * 1024 // 20MB
const MAX_FILES = 5
const MAX_PASTED_CODE_CHARS = 120000
const MODEL_SHORTLIST_LIMIT = 5

// Regex to match ![alt](url) markdown image references
const IMAGE_REF_REGEX = /!\[([^\]]*)\]\(([^)]+)\)/g
const MEDIA_REF_REGEX = /<!--PIGTEX_MEDIA\s+({[\s\S]*?})\s*-->/g
const VIDEO_TASK_REF_REGEX = /<!--PIGTEX_VIDEO_TASK\s+({[\s\S]*?})\s*-->/g
const REFERENCE_METADATA_REGEX = /<!--PIGTEX_REFERENCES\s+({[\s\S]*?})\s*-->/g
const OPENAI_VOICE_PRESETS = ['alloy', 'ash', 'coral', 'echo', 'sage', 'shimmer']
const GEMINI_VOICE_PRESETS = ['Kore', 'Aoede', 'Puck', 'Charon', 'Fenrir']
const ALIBABA_VOICE_PRESETS = ['Cherry', 'Serena', 'Ethan', 'Chelsie']
const VIDEO_STYLE_PRESETS = ['cinematic', 'product', 'ugc', 'anime', 'realistic']
const VOICE_FORMAT_OPTIONS = ['mp3', 'wav', 'ogg', 'aac', 'flac'] as const
const VIDEO_ASPECT_RATIO_OPTIONS = ['16:9', '9:16', '1:1', '4:5'] as const
const VIDEO_DURATION_OPTIONS = ['5', '8', '10'] as const
const VIDEO_QUALITY_OPTIONS = ['standard', 'high'] as const
const PENDING_VIDEO_TASK_STATUSES = new Set(['PENDING', 'RUNNING', 'QUEUED', 'SUBMITTED', 'IN_PROGRESS', 'PROCESSING'])
const TERMINAL_VIDEO_TASK_STATUSES = new Set(['SUCCEEDED', 'SUCCESS', 'COMPLETED', 'DONE', 'FAILED', 'ERROR', 'CANCELED', 'CANCELLED'])

type GeneratedMediaKind = 'audio' | 'video'

interface GeneratedMedia {
    id: string
    kind: GeneratedMediaKind
    src: string
    filename: string
    mimeType?: string
    model?: string | null
    voice?: string
    format?: string
    speed?: number
    aspectRatio?: string
    duration?: string
    quality?: string
    style?: string
    thumbnailUrl?: string
    revisedPrompt?: string
}

type GeneratedVideoTask = {
    taskId: string
    status?: string
    model?: string | null
    aspectRatio?: string
    duration?: string
    quality?: string
    style?: string
}

type MessageRequestKind = 'image_generate' | 'image_edit' | 'image_attachment' | 'voice' | 'video'

type VoiceStudioState = {
    model: string
    voice: string
    responseFormat: (typeof VOICE_FORMAT_OPTIONS)[number]
    speed: string
}

type VideoStudioState = {
    model: string
    aspectRatio: (typeof VIDEO_ASPECT_RATIO_OPTIONS)[number]
    duration: (typeof VIDEO_DURATION_OPTIONS)[number]
    quality: (typeof VIDEO_QUALITY_OPTIONS)[number]
    style: string
}

type StudioAccent = 'voice' | 'video'
type StudioDropdownId =
    | 'voiceModel'
    | 'voiceVoice'
    | 'voiceFormat'
    | 'videoModel'
    | 'videoAspectRatio'
    | 'videoDuration'
    | 'videoQuality'
    | 'videoStyle'
    | null

type StudioOption = {
    value: string
    label?: string
    description?: string | null
}

function resolveStoredAssetUrl(path: string): string {
    return resolveImageUrl(path)
}

function inferExtensionFromMimeType(mimeType?: string, fallback?: string): string {
    const normalized = (mimeType || '').trim().toLowerCase()
    if (normalized.includes('mpeg')) return 'mp3'
    if (normalized.includes('wav')) return 'wav'
    if (normalized.includes('ogg')) return 'ogg'
    if (normalized.includes('aac')) return 'aac'
    if (normalized.includes('flac')) return 'flac'
    if (normalized.includes('webm')) return 'webm'
    if (normalized.includes('mp4')) return 'mp4'
    return (fallback || 'bin').replace(/^\./, '')
}

function normalizeVideoTaskStatus(taskStatus?: string): string {
    return (taskStatus || '').trim().toUpperCase()
}

function isPendingVideoTaskStatus(taskStatus?: string): boolean {
    return PENDING_VIDEO_TASK_STATUSES.has(normalizeVideoTaskStatus(taskStatus))
}

function isTerminalVideoTaskStatus(taskStatus?: string): boolean {
    return TERMINAL_VIDEO_TASK_STATUSES.has(normalizeVideoTaskStatus(taskStatus))
}

function getVoicePresetsForProvider(providerId?: string): string[] {
    if (providerId === 'alibaba') {
        return ALIBABA_VOICE_PRESETS
    }
    if (providerId === 'gemini') {
        return GEMINI_VOICE_PRESETS
    }
    return OPENAI_VOICE_PRESETS
}

function formatAudioTime(seconds: number): string {
    if (!Number.isFinite(seconds) || seconds < 0) return '0:00'
    const totalSeconds = Math.floor(seconds)
    const minutes = Math.floor(totalSeconds / 60)
    const remainingSeconds = totalSeconds % 60
    return `${minutes}:${remainingSeconds.toString().padStart(2, '0')}`
}

function AudioPreviewPlayer({
    src,
    playLabel,
    pauseLabel
}: {
    src: string
    playLabel: string
    pauseLabel: string
}) {
    const audioRef = useRef<HTMLAudioElement | null>(null)
    const [isPlaying, setIsPlaying] = useState(false)
    const [currentTime, setCurrentTime] = useState(0)
    const [duration, setDuration] = useState(0)

    useEffect(() => {
        const audio = audioRef.current
        if (!audio) return

        const syncState = () => {
            setCurrentTime(audio.currentTime || 0)
            setDuration(Number.isFinite(audio.duration) ? audio.duration : 0)
            setIsPlaying(!audio.paused && !audio.ended)
        }

        const handleEnded = () => {
            setIsPlaying(false)
            setCurrentTime(Number.isFinite(audio.duration) ? audio.duration : 0)
        }

        syncState()
        audio.addEventListener('loadedmetadata', syncState)
        audio.addEventListener('durationchange', syncState)
        audio.addEventListener('timeupdate', syncState)
        audio.addEventListener('play', syncState)
        audio.addEventListener('pause', syncState)
        audio.addEventListener('ended', handleEnded)

        return () => {
            audio.removeEventListener('loadedmetadata', syncState)
            audio.removeEventListener('durationchange', syncState)
            audio.removeEventListener('timeupdate', syncState)
            audio.removeEventListener('play', syncState)
            audio.removeEventListener('pause', syncState)
            audio.removeEventListener('ended', handleEnded)
        }
    }, [src])

    useEffect(() => {
        const audio = audioRef.current
        if (!audio) return
        audio.pause()
        audio.currentTime = 0
        setIsPlaying(false)
        setCurrentTime(0)
        setDuration(Number.isFinite(audio.duration) ? audio.duration : 0)
    }, [src])

    const togglePlayback = useCallback(async () => {
        const audio = audioRef.current
        if (!audio) return

        if (audio.paused) {
            if (duration > 0 && audio.currentTime >= duration - 0.1) {
                audio.currentTime = 0
            }
            try {
                await audio.play()
            } catch (error) {
                console.error('Failed to play generated audio preview:', error)
            }
            return
        }

        audio.pause()
    }, [duration])

    const handleSeek = useCallback((event: React.ChangeEvent<HTMLInputElement>) => {
        const audio = audioRef.current
        if (!audio) return
        const nextTime = Number(event.target.value)
        audio.currentTime = nextTime
        setCurrentTime(nextTime)
    }, [])

    const safeDuration = duration > 0 ? duration : 1
    const progressPercent = Math.max(0, Math.min(100, (currentTime / safeDuration) * 100))

    return (
        <div className="message-generated-audio-shell">
            <audio ref={audioRef} preload="metadata" src={src} />
            <button
                type="button"
                className={`message-generated-audio-toggle ${isPlaying ? 'is-playing' : ''}`}
                onClick={() => void togglePlayback()}
                aria-label={isPlaying ? pauseLabel : playLabel}
                title={isPlaying ? pauseLabel : playLabel}
            >
                <span className="message-generated-audio-toggle-icon" aria-hidden="true" />
            </button>
            <div className="message-generated-audio-body">
                <div className="message-generated-audio-track">
                    <div
                        className="message-generated-audio-track-fill"
                        style={{ width: `${progressPercent}%` }}
                    />
                    <input
                        className="message-generated-audio-scrubber"
                        type="range"
                        min={0}
                        max={safeDuration}
                        step={0.1}
                        value={Math.min(currentTime, safeDuration)}
                        onChange={handleSeek}
                        aria-label="Audio progress"
                    />
                </div>
                <div className="message-generated-audio-time">
                    <span>{formatAudioTime(currentTime)}</span>
                    <span>{duration > 0 ? formatAudioTime(duration) : '--:--'}</span>
                </div>
            </div>
        </div>
    )
}

function VideoPreviewPlayer({
    src,
    poster,
    playLabel,
    pauseLabel,
    muteLabel,
    unmuteLabel
}: {
    src: string
    poster?: string
    playLabel: string
    pauseLabel: string
    muteLabel: string
    unmuteLabel: string
}) {
    const videoRef = useRef<HTMLVideoElement | null>(null)
    const [isPlaying, setIsPlaying] = useState(false)
    const [currentTime, setCurrentTime] = useState(0)
    const [duration, setDuration] = useState(0)
    const [isMuted, setIsMuted] = useState(false)

    useEffect(() => {
        const video = videoRef.current
        if (!video) return

        const syncState = () => {
            setCurrentTime(video.currentTime || 0)
            setDuration(Number.isFinite(video.duration) ? video.duration : 0)
            setIsPlaying(!video.paused && !video.ended)
            setIsMuted(video.muted)
        }

        const handleEnded = () => {
            setIsPlaying(false)
            setCurrentTime(Number.isFinite(video.duration) ? video.duration : 0)
        }

        syncState()
        video.addEventListener('loadedmetadata', syncState)
        video.addEventListener('durationchange', syncState)
        video.addEventListener('timeupdate', syncState)
        video.addEventListener('play', syncState)
        video.addEventListener('pause', syncState)
        video.addEventListener('volumechange', syncState)
        video.addEventListener('ended', handleEnded)

        return () => {
            video.removeEventListener('loadedmetadata', syncState)
            video.removeEventListener('durationchange', syncState)
            video.removeEventListener('timeupdate', syncState)
            video.removeEventListener('play', syncState)
            video.removeEventListener('pause', syncState)
            video.removeEventListener('volumechange', syncState)
            video.removeEventListener('ended', handleEnded)
        }
    }, [src])

    useEffect(() => {
        const video = videoRef.current
        if (!video) return
        video.pause()
        video.currentTime = 0
        video.muted = false
        setIsPlaying(false)
        setCurrentTime(0)
        setDuration(Number.isFinite(video.duration) ? video.duration : 0)
        setIsMuted(false)
    }, [src])

    const togglePlayback = useCallback(async () => {
        const video = videoRef.current
        if (!video) return

        if (video.paused) {
            if (duration > 0 && video.currentTime >= duration - 0.1) {
                video.currentTime = 0
            }
            try {
                await video.play()
            } catch (error) {
                console.error('Failed to play generated video preview:', error)
            }
            return
        }

        video.pause()
    }, [duration])

    const handleSeek = useCallback((event: React.ChangeEvent<HTMLInputElement>) => {
        const video = videoRef.current
        if (!video) return
        const nextTime = Number(event.target.value)
        video.currentTime = nextTime
        setCurrentTime(nextTime)
    }, [])

    const toggleMute = useCallback(() => {
        const video = videoRef.current
        if (!video) return
        video.muted = !video.muted
        setIsMuted(video.muted)
    }, [])

    const safeDuration = duration > 0 ? duration : 1
    const progressPercent = Math.max(0, Math.min(100, (currentTime / safeDuration) * 100))

    return (
        <div className="message-generated-video-shell">
            <div className="message-generated-video-frame-wrap">
                <video
                    ref={videoRef}
                    className="message-generated-video-frame"
                    preload="metadata"
                    playsInline
                    poster={poster}
                    src={src}
                    onClick={() => void togglePlayback()}
                />
                {!isPlaying && (
                    <button
                        type="button"
                        className="message-generated-video-overlay-toggle"
                        onClick={() => void togglePlayback()}
                        aria-label={playLabel}
                        title={playLabel}
                    >
                        <span className="message-generated-audio-toggle-icon" aria-hidden="true" />
                    </button>
                )}
            </div>
            <div className="message-generated-video-controls">
                <button
                    type="button"
                    className={`message-generated-audio-toggle ${isPlaying ? 'is-playing' : ''}`}
                    onClick={() => void togglePlayback()}
                    aria-label={isPlaying ? pauseLabel : playLabel}
                    title={isPlaying ? pauseLabel : playLabel}
                >
                    <span className="message-generated-audio-toggle-icon" aria-hidden="true" />
                </button>
                <div className="message-generated-video-controls-body">
                    <div className="message-generated-audio-track message-generated-video-track">
                        <div
                            className="message-generated-audio-track-fill message-generated-video-track-fill"
                            style={{ width: `${progressPercent}%` }}
                        />
                        <input
                            className="message-generated-audio-scrubber"
                            type="range"
                            min={0}
                            max={safeDuration}
                            step={0.1}
                            value={Math.min(currentTime, safeDuration)}
                            onChange={handleSeek}
                            aria-label="Video progress"
                        />
                    </div>
                    <div className="message-generated-video-meta">
                        <div className="message-generated-audio-time">
                            <span>{formatAudioTime(currentTime)}</span>
                            <span>{duration > 0 ? formatAudioTime(duration) : '--:--'}</span>
                        </div>
                        <button
                            type="button"
                            className={`message-generated-video-secondary ${isMuted ? 'is-muted' : ''}`}
                            onClick={toggleMute}
                        >
                            {isMuted ? unmuteLabel : muteLabel}
                        </button>
                    </div>
                </div>
            </div>
        </div>
    )
}

function GeneratedMediaCard({
    item,
    previewLabel,
    playLabel,
    pauseLabel,
    muteLabel,
    unmuteLabel,
    downloadLabel
}: {
    item: GeneratedMedia
    previewLabel: string
    playLabel: string
    pauseLabel: string
    muteLabel: string
    unmuteLabel: string
    downloadLabel: string
}) {
    const downloadFilename = (item.filename || '').trim() || `${item.kind}-${item.id}.${inferExtensionFromMimeType(item.mimeType, item.kind === 'audio' ? 'mp3' : 'mp4')}`
    const [resolvedSrc, setResolvedSrc] = useState(() => {
        if (!item.src) return ''
        return item.src.startsWith('data:') || item.src.startsWith('blob:') ? item.src : ''
    })
    const [resolvedPoster, setResolvedPoster] = useState<string | undefined>(() => {
        if (!item.thumbnailUrl) return undefined
        return item.thumbnailUrl.startsWith('data:') || item.thumbnailUrl.startsWith('blob:')
            ? item.thumbnailUrl
            : undefined
    })

    useEffect(() => {
        let disposed = false
        const cleanup: Array<() => void> = []

        setResolvedSrc(item.src.startsWith('data:') || item.src.startsWith('blob:') ? item.src : '')
        setResolvedPoster(
            item.thumbnailUrl && (item.thumbnailUrl.startsWith('data:') || item.thumbnailUrl.startsWith('blob:'))
                ? item.thumbnailUrl
                : undefined
        )

        void (async () => {
            try {
                const nextSource = await resolveProtectedMediaSrc(item.src)
                if (disposed) {
                    nextSource.revoke?.()
                    return
                }
                if (nextSource.revoke) {
                    cleanup.push(nextSource.revoke)
                }
                setResolvedSrc(nextSource.src)
            } catch (error) {
                console.error('Failed to resolve generated media source:', error)
                if (!disposed) {
                    setResolvedSrc(item.src)
                }
            }

            if (!item.thumbnailUrl) {
                return
            }

            try {
                const nextPoster = await resolveProtectedMediaSrc(item.thumbnailUrl)
                if (disposed) {
                    nextPoster.revoke?.()
                    return
                }
                if (nextPoster.revoke) {
                    cleanup.push(nextPoster.revoke)
                }
                setResolvedPoster(nextPoster.src)
            } catch (error) {
                console.error('Failed to resolve generated media poster:', error)
                if (!disposed) {
                    setResolvedPoster(item.thumbnailUrl)
                }
            }
        })()

        return () => {
            disposed = true
            cleanup.forEach(revoke => revoke())
        }
    }, [item.src, item.thumbnailUrl])

    return (
        <div
            className={`message-generated-media-card message-generated-media-card-${item.kind}`}
        >
            <div className="message-generated-media-header">
                <div className="message-generated-media-title-wrap">
                    <span className="message-generated-media-label">
                        {previewLabel}
                    </span>
                    <span className="message-generated-media-name">{item.filename}</span>
                </div>
                <div className="message-generated-media-meta">
                    {item.model && (
                        <span className="message-generated-media-chip">{item.model}</span>
                    )}
                    {item.kind === 'audio' && item.voice && (
                        <span className="message-generated-media-chip">{item.voice}</span>
                    )}
                    {item.kind === 'video' && item.aspectRatio && (
                        <span className="message-generated-media-chip">{item.aspectRatio}</span>
                    )}
                    {item.kind === 'video' && item.duration && (
                        <span className="message-generated-media-chip">{item.duration}s</span>
                    )}
                    <a
                        className="message-generated-media-download"
                        href={resolvedSrc || item.src}
                        download={downloadFilename}
                        title={`${downloadLabel} ${downloadFilename}`}
                    >
                        <Save size={12} />
                        <span>{downloadLabel}</span>
                    </a>
                </div>
            </div>
            {item.kind === 'audio' ? (
                <AudioPreviewPlayer
                    src={resolvedSrc || item.src}
                    playLabel={playLabel}
                    pauseLabel={pauseLabel}
                />
            ) : (
                <VideoPreviewPlayer
                    src={resolvedSrc || item.src}
                    poster={resolvedPoster}
                    playLabel={playLabel}
                    pauseLabel={pauseLabel}
                    muteLabel={muteLabel}
                    unmuteLabel={unmuteLabel}
                />
            )}
            {item.revisedPrompt && (
                <div className="message-generated-media-caption">
                    {item.revisedPrompt}
                </div>
            )}
        </div>
    )
}

function StudioComboboxField({
    accent,
    wide = false,
    label,
    value,
    onChange,
    placeholder,
    options,
    isOpen,
    onToggle,
    onOpen,
    onClose,
    emptyText
}: {
    accent: StudioAccent
    wide?: boolean
    label: string
    value: string
    onChange: (value: string) => void
    placeholder: string
    options: StudioOption[]
    isOpen: boolean
    onToggle: () => void
    onOpen: () => void
    onClose: () => void
    emptyText: string
}) {
    const normalizedValue = value.trim().toLowerCase()
    const filteredOptions = options.filter((option) => {
        const optionValue = option.value.toLowerCase()
        const optionLabel = (option.label || option.value).toLowerCase()
        return !normalizedValue || optionValue.includes(normalizedValue) || optionLabel.includes(normalizedValue)
    })
    const hasExactMatch = options.some(option => option.value.toLowerCase() === normalizedValue)
    const shouldShowCustomOption = Boolean(normalizedValue) && !hasExactMatch
    const shouldRenderMenu = isOpen && (filteredOptions.length > 0 || shouldShowCustomOption)

    return (
        <div className={`media-studio-field ${wide ? 'media-studio-field-wide' : ''}`}>
            <span>{label}</span>
            <div
                className={`media-studio-control media-studio-control-${accent} ${isOpen ? 'is-open' : ''}`}
                onClick={(event) => event.stopPropagation()}
            >
                <div className="media-studio-combobox-row">
                    <input
                        className="media-studio-input media-studio-combobox-input"
                        value={value}
                        onChange={(event) => {
                            onChange(event.target.value)
                            onOpen()
                        }}
                        onFocus={() => {
                            if (options.length > 0) onOpen()
                        }}
                        onKeyDown={(event) => {
                            if (event.key === 'Escape') {
                                event.preventDefault()
                                onClose()
                            }
                        }}
                        placeholder={placeholder}
                        autoComplete="off"
                        role="combobox"
                        aria-expanded={shouldRenderMenu}
                        aria-autocomplete="list"
                    />
                    <button
                        type="button"
                        className="media-studio-combobox-toggle"
                        onClick={onToggle}
                        aria-label={label}
                        aria-expanded={shouldRenderMenu}
                    >
                        <ChevronDown size={14} />
                    </button>
                </div>
                {shouldRenderMenu && (
                    <div className="media-studio-menu">
                        {shouldShowCustomOption && (
                            <button
                                type="button"
                                className="media-studio-menu-item media-studio-menu-item-active"
                                onClick={() => {
                                    onChange(value.trim())
                                    onClose()
                                }}
                            >
                                <div className="media-studio-menu-item-copy">
                                    <span className="media-studio-menu-item-main">{value.trim()}</span>
                                    <span className="media-studio-menu-item-sub">{emptyText}</span>
                                </div>
                            </button>
                        )}
                        {filteredOptions.slice(0, 8).map(option => {
                            const isActive = option.value === value
                            return (
                                <button
                                    key={option.value}
                                    type="button"
                                    className={`media-studio-menu-item ${isActive ? 'media-studio-menu-item-active' : ''}`}
                                    onClick={() => {
                                        onChange(option.value)
                                        onClose()
                                    }}
                                >
                                    <div className="media-studio-menu-item-copy">
                                        <span className="media-studio-menu-item-main">
                                            {option.label || option.value}
                                        </span>
                                        {option.description && (
                                            <span className="media-studio-menu-item-sub">{option.description}</span>
                                        )}
                                    </div>
                                    {isActive && <Check size={14} />}
                                </button>
                            )
                        })}
                    </div>
                )}
            </div>
        </div>
    )
}

function StudioSelectField({
    accent,
    wide = false,
    label,
    value,
    options,
    isOpen,
    onToggle,
    onSelect
}: {
    accent: StudioAccent
    wide?: boolean
    label: string
    value: string
    options: StudioOption[]
    isOpen: boolean
    onToggle: () => void
    onSelect: (value: string) => void
}) {
    const selectedOption = options.find(option => option.value === value)

    return (
        <div className={`media-studio-field ${wide ? 'media-studio-field-wide' : ''}`}>
            <span>{label}</span>
            <div
                className={`media-studio-control media-studio-control-${accent} ${isOpen ? 'is-open' : ''}`}
                onClick={(event) => event.stopPropagation()}
            >
                <button
                    type="button"
                    className="media-studio-select-trigger"
                    onClick={onToggle}
                    aria-label={label}
                    aria-expanded={isOpen}
                >
                    <span>{selectedOption?.label || value}</span>
                    <ChevronDown size={14} />
                </button>
                {isOpen && (
                    <div className="media-studio-menu">
                        {options.map(option => {
                            const isActive = option.value === value
                            return (
                                <button
                                    key={option.value}
                                    type="button"
                                    className={`media-studio-menu-item ${isActive ? 'media-studio-menu-item-active' : ''}`}
                                    onClick={() => onSelect(option.value)}
                                >
                                    <div className="media-studio-menu-item-copy">
                                        <span className="media-studio-menu-item-main">
                                            {option.label || option.value}
                                        </span>
                                        {option.description && (
                                            <span className="media-studio-menu-item-sub">{option.description}</span>
                                        )}
                                    </div>
                                    {isActive && <Check size={14} />}
                                </button>
                            )
                        })}
                    </div>
                )}
            </div>
        </div>
    )
}

function parseGeneratedMediaRefsFromContent(content: string): { text: string; media: GeneratedMedia[] } {
    const media: GeneratedMedia[] = []

    const text = content.replace(MEDIA_REF_REGEX, (_match, payload) => {
        try {
            const parsed = JSON.parse(payload) as Record<string, unknown>
            const kind = parsed.kind === 'audio' || parsed.kind === 'video'
                ? parsed.kind
                : null
            const rawSrc = typeof parsed.src === 'string' ? parsed.src.trim() : ''
            if (!kind || !rawSrc) return ''

            media.push({
                id: typeof parsed.id === 'string' && parsed.id.trim()
                    ? parsed.id.trim()
                    : `${kind}_${Date.now()}_${media.length + 1}`,
                kind,
                src: resolveStoredAssetUrl(rawSrc),
                filename: typeof parsed.filename === 'string' && parsed.filename.trim()
                    ? parsed.filename.trim()
                    : `${kind}_${media.length + 1}.${inferExtensionFromMimeType(typeof parsed.mimeType === 'string' ? parsed.mimeType : undefined, kind === 'audio' ? 'mp3' : 'mp4')}`,
                mimeType: typeof parsed.mimeType === 'string' ? parsed.mimeType : undefined,
                model: typeof parsed.model === 'string' ? parsed.model : undefined,
                voice: typeof parsed.voice === 'string' ? parsed.voice : undefined,
                format: typeof parsed.format === 'string' ? parsed.format : undefined,
                speed: typeof parsed.speed === 'number' ? parsed.speed : undefined,
                aspectRatio: typeof parsed.aspectRatio === 'string' ? parsed.aspectRatio : undefined,
                duration: typeof parsed.duration === 'string' ? parsed.duration : undefined,
                quality: typeof parsed.quality === 'string' ? parsed.quality : undefined,
                style: typeof parsed.style === 'string' ? parsed.style : undefined,
                thumbnailUrl: typeof parsed.thumbnailUrl === 'string' && parsed.thumbnailUrl.trim()
                    ? resolveStoredAssetUrl(parsed.thumbnailUrl.trim())
                    : undefined,
                revisedPrompt: typeof parsed.revisedPrompt === 'string' ? parsed.revisedPrompt : undefined,
            })
        } catch {
            return ''
        }

        return ''
    })

    return {
        text: text.replace(/\n{3,}/g, '\n\n').trim(),
        media
    }
}

function parseVideoTaskRefFromContent(content: string): { text: string; videoTask?: GeneratedVideoTask } {
    let videoTask: GeneratedVideoTask | undefined

    const text = content.replace(VIDEO_TASK_REF_REGEX, (_match, payload) => {
        try {
            const parsed = JSON.parse(payload) as Record<string, unknown>
            const taskId = typeof parsed.taskId === 'string' && parsed.taskId.trim()
                ? parsed.taskId.trim()
                : typeof parsed.task_id === 'string' && parsed.task_id.trim()
                    ? parsed.task_id.trim()
                    : ''
            if (!taskId) return ''

            videoTask = {
                taskId,
                status: typeof parsed.status === 'string' && parsed.status.trim()
                    ? parsed.status.trim()
                    : typeof parsed.task_status === 'string' && parsed.task_status.trim()
                        ? parsed.task_status.trim()
                        : undefined,
                model: typeof parsed.model === 'string' ? parsed.model : undefined,
                aspectRatio: typeof parsed.aspectRatio === 'string' ? parsed.aspectRatio : undefined,
                duration: typeof parsed.duration === 'string' ? parsed.duration : undefined,
                quality: typeof parsed.quality === 'string' ? parsed.quality : undefined,
                style: typeof parsed.style === 'string' ? parsed.style : undefined,
            }
        } catch {
            return ''
        }

        return ''
    })

    return {
        text: text.replace(/\n{3,}/g, '\n\n').trim(),
        videoTask
    }
}

function parseStoredReferenceMetadata(content: string): { text: string; mentions: MentionItem[] } {
    const mentions: MentionItem[] = []
    const seen = new Set<string>()

    const text = content.replace(REFERENCE_METADATA_REGEX, (_match, payload) => {
        try {
            const parsed = JSON.parse(payload) as { items?: unknown }
            const items = Array.isArray(parsed.items) ? parsed.items : []

            for (const rawItem of items) {
                if (!rawItem || typeof rawItem !== 'object') continue

                const candidate = rawItem as Record<string, unknown>
                const type = candidate.type === 'file' || candidate.type === 'folder' || candidate.type === 'conversation'
                    ? candidate.type
                    : null
                const relativePath = typeof candidate.relativePath === 'string'
                    ? candidate.relativePath.trim()
                    : ''
                const name = typeof candidate.name === 'string' && candidate.name.trim()
                    ? candidate.name.trim()
                    : relativePath.split(/[/\\]/).pop() || relativePath
                const referenceId = typeof candidate.referenceId === 'string' && candidate.referenceId.trim()
                    ? candidate.referenceId.trim()
                    : undefined
                const subtitle = typeof candidate.subtitle === 'string' && candidate.subtitle.trim()
                    ? candidate.subtitle.trim()
                    : undefined

                if (!type || (!relativePath && !referenceId)) continue

                const mention: MentionItem = {
                    type,
                    relativePath: relativePath || name,
                    name: name || relativePath || referenceId || '',
                    absolutePath: '',
                    referenceId,
                    subtitle
                }
                const key = `${mention.type}:${mention.referenceId || mention.relativePath}`
                if (seen.has(key)) continue
                seen.add(key)
                mentions.push(mention)
            }
        } catch {
            return ''
        }

        return ''
    })

    return {
        text: text.replace(/\n{3,}/g, '\n\n').trim(),
        mentions
    }
}

async function blobToDataUrl(blob: Blob): Promise<string> {
    return await new Promise((resolve, reject) => {
        const reader = new FileReader()
        reader.onload = () => {
            if (typeof reader.result === 'string') {
                resolve(reader.result)
            } else {
                reject(new Error('Failed to convert blob to data URL'))
            }
        }
        reader.onerror = () => reject(new Error('Failed to read blob'))
        reader.readAsDataURL(blob)
    })
}

/**
 * Parse ![image](url) references from stored message content.
 * Returns extracted images and cleaned text.
 */
function parseImageRefsFromContent(content: string): { text: string; images: ImageAttachment[] } {
    const images: ImageAttachment[] = []
    let match: RegExpExecArray | null
    const regex = new RegExp(IMAGE_REF_REGEX)

    while ((match = regex.exec(content)) !== null) {
        const [, alt, url] = match
        const resolvedUrl = resolveImageUrl(url)
        images.push({
            id: `saved_${images.length}_${Date.now()}`,
            filename: alt || 'image',
            mime_type: 'image/png',
            size: 0,
            base64_data: resolvedUrl, // Use serve URL instead of base64
        })
    }

    // Remove image references from display text
    const text = content.replace(IMAGE_REF_REGEX, '').trim()
    return { text, images }
}

function getFileExtension(filename: string): string {
    const lastDot = filename.lastIndexOf('.')
    if (lastDot < 0) return ''
    return filename.slice(lastDot).toLowerCase()
}

function isSupportedDocumentFile(file: File): boolean {
    const mimeType = (file.type || '').toLowerCase()
    const ext = getFileExtension(file.name)
    return ALLOWED_FILE_TYPES.includes(mimeType) || ALLOWED_FILE_EXTENSIONS.includes(ext)
}

function buildPastedCodeAttachment(rawCode: string): DocumentAttachment {
    const normalized = rawCode.replace(/\r\n/g, '\n')
    const trimmed = normalized.trim()
    const finalCode = trimmed.length > MAX_PASTED_CODE_CHARS
        ? `${trimmed.slice(0, MAX_PASTED_CODE_CHARS)}\n\n/* truncated */`
        : trimmed
    const timestamp = Date.now()
    const filename = `snippet-${timestamp}.txt`
    const size = new Blob([finalCode]).size

    return {
        id: `snippet_${timestamp}`,
        filename,
        mime_type: 'text/plain',
        size,
        extracted_text: finalCode,
        text_chars: finalCode.length,
        truncated: trimmed.length > MAX_PASTED_CODE_CHARS,
        chunks: [{
            index: 1,
            label: 'Snippet',
            text: finalCode,
            char_count: finalCode.length,
            truncated: trimmed.length > MAX_PASTED_CODE_CHARS
        }]
    }
}

interface ChatPanelProps {
    variant: 'centered' | 'sidebar'
    onFileOpen?: (fileId: string) => void
    conversationId?: string | null
    workspaceId?: string | null
    learningProgramId?: string | null
    learningProgramTitle?: string | null
    newChatToken?: number
    onConversationCreated?: (conversationId: string) => void
    onConversationInvalidated?: () => void
    localRootPath?: string | null
    settings: PigTexSettings
    onSettingsChange: (patch: Partial<PigTexSettings>) => void
}


const PIGTEX_SUGGESTIONS: { id: string; text: string; icon: typeof FileText; gradient: string }[] = [
    { id: 'p1', text: 'Giúp mình viết một đề xuất dự án', icon: FileText, gradient: 'linear-gradient(135deg, #818cf8 0%, #6366f1 100%)' },
    { id: 'p2', text: 'Giải thích đoạn code này cho mình', icon: Code, gradient: 'linear-gradient(135deg, #a78bfa 0%, #7c3aed 100%)' },
    { id: 'p3', text: 'Tóm tắt tài liệu này giúp mình', icon: FileText, gradient: 'linear-gradient(135deg, #c084fc 0%, #9333ea 100%)' },
    { id: 'p4', text: 'Debug code giúp mình nha', icon: Sparkles, gradient: 'linear-gradient(135deg, #e879f9 0%, #c026d3 100%)' },
]

const PIGTEX_GREETING = 'Chào bạn! Mình là PigTex'
const PIGTEX_SUBTITLE = 'Trợ lý AI tập trung hiệu năng, trả lời nhanh và bám sát công việc.'

const isConversationNotFoundError = (error: unknown) => {
    if (!(error instanceof Error)) return false
    const normalized = error.message.trim().toLowerCase()
    return normalized.includes('conversation not found')
}
const PIGTEX_ORB_COLORS = ['rgba(129, 140, 248, 0.12)', 'rgba(192, 132, 252, 0.10)', 'rgba(99, 102, 241, 0.08)']

const conversationModes = [
    {
        id: 'fast',
        label: 'Fast',
        description: 'Ưu tiên tốc độ, tự chọn tool tối thiểu'
    },
    {
        id: 'deep',
        label: 'Deep',
        description: 'Plan/checklist kỹ hơn, loop tool chặt hơn'
    },
    {
        id: 'learn',
        label: 'Learn',
        description: 'Tutor mode with goal, checklist, evidence, and memory'
    },
]
type ConversationModeId = 'fast' | 'deep' | 'learn'

type ConversationMode = {
    id: ConversationModeId
    label: string
    description: string
}

type AttachmentOption = {
    id: 'image' | 'file' | 'code' | 'web'
    label: string
    icon: typeof Image
}

const imageToolModes = [
    { id: 'chat', label: 'Chat' },
    { id: 'image', label: 'Image' },
    { id: 'voice', label: 'Voice' },
    { id: 'video', label: 'Video' },
] as const

type ImageToolMode = {
    id: 'chat' | 'image' | 'voice' | 'video'
    label: string
}

const attachOptions = [
    { id: 'image', label: 'Add image', icon: Image },
    { id: 'file', label: 'Upload file', icon: Paperclip },
    { id: 'code', label: 'Paste code', icon: Code },
    { id: 'web', label: 'Web search', icon: Globe },
]

const MAX_AI_AGENT_STEPS_BY_MODE: Record<ConversationModeId, { base: number; complex: number; max: number }> = {
    fast: { base: 2, complex: 3, max: 4 },
    deep: { base: 4, complex: 5, max: 6 },
    learn: { base: 3, complex: 4, max: 5 }
}
const MAX_AI_AGENT_RUNTIME_MS = 120000
const MAX_DIFF_INPUT_LINES = 260
const MAX_DIFF_OUTPUT_LINES = 360
const MAX_DIFF_LINE_LENGTH = 220
const STREAM_SMOOTH_FRAME_MS = 16
const STREAM_SMOOTH_MIN_CHARS = 2
const STREAM_SMOOTH_MAX_CHARS = 48
const STREAM_SMOOTH_BACKLOG_DIVISOR = 8
const STREAM_DIRECT_RENDER_BACKLOG = 24
const STREAM_ARTIFACT_SCAN_WINDOW_CHARS = 1200
const MAX_MENTION_CONTEXT_ITEMS = 6
const MAX_MENTION_FILE_CHARS = 4000
const MAX_CONVERSATION_MENTION_ITEMS = 3
const MAX_CONVERSATION_MENTION_MESSAGES = 12
const MAX_CONVERSATION_MENTION_MESSAGE_CHARS = 1400
const MAX_CONVERSATION_MENTION_TOTAL_CHARS = 12000
const COMPLEXITY_HINT_RE = /(compare|analysis|research|debug|root cause|refactor|architecture|plan|roadmap|trade-off|audit|benchmark|migrate|phân tích|so sánh|đánh giá|kế hoạch|kiến trúc|tối ưu|kiểm tra|điều tra|xử lý lỗi)/i
const VERIFICATION_HINT_RE = /(verify|fact check|citation|source|evidence|legal|medical|finance|security|latest|today|news|price|xác minh|kiểm chứng|nguồn|bằng chứng|pháp lý|y tế|tài chính|bảo mật|mới nhất|hôm nay|tin tức|giá)/i
const LIST_HINT_RE = /(^|\n)\s*(?:[-*]|\d+[.)])\s+/m
const AGENT_PREFIX_RE = /^\[(?:agent|Agent)\]\s*/i
const AGENT_BULLET_RE = /^[•\-]\s*/

const isFilesystemMention = (mention: MentionItem): mention is MentionItem & { type: 'file' | 'folder' } => (
    mention.type === 'file' || mention.type === 'folder'
)

const isConversationMention = (mention: MentionItem): mention is MentionItem & { type: 'conversation'; referenceId: string } => (
    mention.type === 'conversation'
    && typeof mention.referenceId === 'string'
    && mention.referenceId.trim().length > 0
)

const truncatePromptSnippet = (value: string, maxChars: number): string => {
    const normalized = value.trim()
    if (!normalized) return ''
    if (normalized.length <= maxChars) return normalized
    return `${normalized.slice(0, maxChars)}\n... (truncated)`
}

const normalizeAgentStatusLine = (line: string): string => (
    line
        .replace(AGENT_PREFIX_RE, '')
        .replace(AGENT_BULLET_RE, '')
        .replace(/\s+/g, ' ')
        .trim()
)

const detectAgentStatusTone = (line: string): NonNullable<UIMessage['agentStatus']>['tone'] => {
    const normalized = line.toLowerCase()

    if (
        /(?:\blỗi\b|\berror\b|\bfailed?\b|\btimeout\b|không hợp lệ|invalid|not found|không tồn tại)/i.test(normalized)
    ) {
        return 'error'
    }

    if (
        /(?:\bxong\b|\bdone\b|\bcompleted?\b|\bsuccess(?:ful|fully)?\b|đã hoàn tất|đã xong)/i.test(normalized)
    ) {
        return 'success'
    }

    if (
        /(?:skip|bỏ qua|loop|lặp|giới hạn|limit|max steps|no new actions|không còn thao tác|stopped?|đã dừng)/i.test(normalized)
    ) {
        return 'warning'
    }

    if (
        /(?:đang|scanning|reviewing|writing|patching|executing|summarizing|processing|running|checking|analyzing)/i.test(normalized)
    ) {
        return 'running'
    }

    return 'info'
}

const MODE_PROMPT_FAST = `
## Response Mode: FAST
You are in fast mode. Prioritize speed and practical usefulness.
- Decide tools adaptively per request. Only call tools when they materially improve answer quality.
- Keep response concise and actionable.
- Skip long planning unless user explicitly asks.
- If uncertainty remains, state it clearly instead of guessing.
`.trim()

const MODE_PROMPT_LEARN = `
## Response Mode: LEARN
You are in learn mode. Operate as PigTex Learn inside the normal chat UI.
- Treat the turn as guided learning, not generic assistance.
- Keep one instructional purpose per turn: diagnose, teach, guided practice, independent practice, review, evaluate, or summarize progress.
- Track a real target, checklist movement, learner evidence, and memory updates.
- Do not mark mastery from confidence or agreement alone.
- If the user provides files, prefer teaching from those materials before general background knowledge.
- End with a concrete next step that helps verify progress.
`.trim()

const MODE_PROMPT_DEEP = `
## Response Mode: DEEP
You are in deep mode. Prioritize rigor and completeness.
- Decide tools adaptively per request. Do not force tool usage if unnecessary.
- For non-trivial tasks, start with a short plan/checklist and keep it updated.
- Analyze multiple options/tradeoffs before concluding.
- Verify critical claims when relevant and call out source conflicts.
- End with: final answer + checklist status + confidence note.
`.trim()

const MODE_PENDING_TEXT: Record<ConversationModeId, string> = {
    fast: 'Fast mode đang xử lý nhanh...',
    deep: 'Deep mode đang lập kế hoạch và kiểm tra kỹ...',
    learn: 'Learn mode đang dẫn bạn theo lộ trình học...'
}

const isTransientPendingMessage = (message: string) => {
    const normalized = message.trim()
    return (
        normalized === 'Đang xử lý yêu cầu...'
        || normalized === MODE_PENDING_TEXT.fast
        || normalized === MODE_PENDING_TEXT.deep
        || normalized === MODE_PENDING_TEXT.learn
    )
}

const StreamingWaitLoader = ({ label }: { label: string }) => (
    <div className="message-wait-loader-wrap" aria-label={label} role="status">
        <div className="message-wait-loader">
            <span>
                <span />
                <span />
                <span />
                <span />
            </span>
            <div className="message-wait-loader-base">
                <span />
                <div className="message-wait-loader-face" />
            </div>
        </div>
        <div className="message-wait-loader-lines">
            <span />
            <span />
            <span />
            <span />
        </div>
    </div>
)

const estimateTurnComplexity = (
    text: string,
    mentionsCount: number,
    imageCount: number,
    fileCount: number
) => {
    const normalized = text.trim()
    if (!normalized) return 0

    let score = 0
    if (normalized.length >= 180) score += 1
    if (normalized.length >= 420) score += 1
    if (COMPLEXITY_HINT_RE.test(normalized)) score += 1
    if (VERIFICATION_HINT_RE.test(normalized)) score += 1
    if (LIST_HINT_RE.test(normalized)) score += 1
    if (mentionsCount + imageCount + fileCount > 0) score += 1

    return Math.min(score, 6)
}

const resolveAiAgentStepBudget = (modeId: ConversationModeId, complexityScore: number) => {
    const budget = MAX_AI_AGENT_STEPS_BY_MODE[modeId]
    if (complexityScore >= 4) return budget.max
    if (complexityScore >= 2) return budget.complex
    return budget.base
}

type DiffPreviewBuildResult = {
    text: string
    truncatedInput: boolean
    truncatedOutput: boolean
}

type DiffPreviewCopy = {
    diffBefore: (count: number) => string
    diffAfter: (count: number) => string
    diffInputTruncatedNotice: (count: number) => string
    diffOutputTruncatedNotice: (count: number) => string
}

const getAiActionKey = (action: ParsedAiFileAction, index: number) =>
    `${action.type}:${action.path}:${action.newPath || ''}:${index}`

const joinAbsolutePathForPreview = (rootPath: string, relativePath: string) => {
    const separator = rootPath.includes('\\') ? '\\' : '/'
    const normalizedRoot = rootPath.endsWith(separator) ? rootPath.slice(0, -1) : rootPath
    const normalizedRelativePath = relativePath.split('/').join(separator)
    if (!normalizedRelativePath) {
        return normalizedRoot
    }
    return `${normalizedRoot}${separator}${normalizedRelativePath}`
}

const resolveRenameTargetPath = (action: ParsedAiFileAction): string | null => {
    if (action.type !== 'rename_path' || !action.newPath) return null
    if (action.newPath.includes('/')) return action.newPath

    const lastSlash = action.path.lastIndexOf('/')
    const parent = lastSlash >= 0 ? action.path.slice(0, lastSlash) : ''
    return parent ? `${parent}/${action.newPath}` : action.newPath
}

const clipLineLength = (line: string) =>
    line.length > MAX_DIFF_LINE_LENGTH ? `${line.slice(0, MAX_DIFF_LINE_LENGTH)} ...` : line

const getSmoothStreamChunkSize = (backlog: number) => {
    const adaptiveStep = Math.ceil(backlog / STREAM_SMOOTH_BACKLOG_DIVISOR)
    return Math.max(
        STREAM_SMOOTH_MIN_CHARS,
        Math.min(STREAM_SMOOTH_MAX_CHARS, adaptiveStep)
    )
}

const getWebSearchStatusLabel = (status: WebSearchMetadata['status']) => {
    switch (status) {
        case 'running':
            return 'Web search in progress...'
        case 'timeout':
            return 'Web search timed out'
        case 'complete':
            return 'Web search completed'
        case 'error':
            return 'Web search failed'
        case 'disabled':
            return 'Web search disabled'
        case 'skipped':
        default:
            return 'Web search skipped'
    }
}

const getWebSearchModeLabel = (mode?: WebSearchMetadata['mode']) => {
    switch (mode) {
        case 'deep':
            return 'Deep'
        case 'fast':
            return 'Fast'
        case 'auto':
            return 'Auto'
        default:
            return ''
    }
}

const getClaimVerdictLabel = (verdict: WebSearchClaimVerification['verdict']) => {
    switch (verdict) {
        case 'supported':
            return 'Supported'
        case 'contradicted':
            return 'Contradicted'
        case 'mixed':
            return 'Conflicting'
        case 'insufficient':
        default:
            return 'Insufficient'
    }
}

void PIGTEX_SUGGESTIONS
void PIGTEX_GREETING
void PIGTEX_SUBTITLE
void conversationModes
void imageToolModes
void attachOptions
void MODE_PENDING_TEXT
void isTransientPendingMessage
void getWebSearchStatusLabel
void getWebSearchModeLabel
void getClaimVerdictLabel

const buildMemoryMetadataFromStoredSources = (sources?: string[] | null): MemoryContextMetadata | undefined => {
    if (!sources || sources.length === 0) return undefined
    const structured = sources
        .map(item => item.trim())
        .filter(Boolean)
        .filter(item => !/^https?:\/\//i.test(item))
        .slice(0, 6)
        .map((id, index) => ({
            index: index + 1,
            id,
            title: id,
            type: 'knowledge'
        }))
    if (structured.length === 0) return undefined
    return {
        enabled: true,
        use_knowledge: true,
        knowledge_hits: structured.length,
        sources: structured
    }
}

interface MessageUsageMeta {
    promptTokens: number
    completionTokens: number
    totalTokens: number
    costUsd?: number
    estimated?: boolean
}

type UsageModelRateHint = {
    hint: string
    inputUsdPer1M: number
    outputUsdPer1M: number
}

const USAGE_MODEL_RATE_HINTS: UsageModelRateHint[] = [
    { hint: 'gpt-5-minimal', inputUsdPer1M: 0.25, outputUsdPer1M: 2.0 },
    { hint: 'gpt-5-low', inputUsdPer1M: 0.25, outputUsdPer1M: 2.0 },
    { hint: 'gpt-5.1-codex-mini', inputUsdPer1M: 0.3, outputUsdPer1M: 1.5 },
    { hint: 'gpt-5-mini', inputUsdPer1M: 0.3, outputUsdPer1M: 2.4 },
    { hint: 'gpt-5', inputUsdPer1M: 1.25, outputUsdPer1M: 10.0 },
    { hint: 'gpt-4.1-mini', inputUsdPer1M: 0.4, outputUsdPer1M: 1.6 },
    { hint: 'gpt-4.1', inputUsdPer1M: 2.0, outputUsdPer1M: 8.0 },
    { hint: 'gpt-4o-mini', inputUsdPer1M: 0.15, outputUsdPer1M: 0.6 },
    { hint: 'gpt-4o', inputUsdPer1M: 2.5, outputUsdPer1M: 10.0 },
    { hint: 'o3-mini', inputUsdPer1M: 1.1, outputUsdPer1M: 4.4 },
    { hint: 'o3', inputUsdPer1M: 2.0, outputUsdPer1M: 8.0 },
    { hint: 'o1-mini', inputUsdPer1M: 1.1, outputUsdPer1M: 4.4 },
    { hint: 'o1', inputUsdPer1M: 2.0, outputUsdPer1M: 8.0 },
    { hint: 'claude-3.5-haiku', inputUsdPer1M: 1.0, outputUsdPer1M: 5.0 },
    { hint: 'claude-3.7-sonnet', inputUsdPer1M: 3.0, outputUsdPer1M: 15.0 },
    { hint: 'claude-3.5-sonnet', inputUsdPer1M: 3.0, outputUsdPer1M: 15.0 },
    { hint: 'gemini-2.0-flash', inputUsdPer1M: 0.3, outputUsdPer1M: 0.6 },
    { hint: 'gemini-1.5-flash', inputUsdPer1M: 0.3, outputUsdPer1M: 0.6 },
    { hint: 'gemini-1.5-pro', inputUsdPer1M: 3.5, outputUsdPer1M: 10.5 },
]

const DEFAULT_USAGE_INPUT_USD_PER_1M = 0.15
const DEFAULT_USAGE_OUTPUT_USD_PER_1M = 0.6
const resolveModelUsageRates = (modelId?: string | null) => {
    const normalized = (modelId || '').trim().toLowerCase()
    const matched = USAGE_MODEL_RATE_HINTS.find(rate => normalized.includes(rate.hint))
    if (matched) {
        return {
            inputUsdPer1M: matched.inputUsdPer1M,
            outputUsdPer1M: matched.outputUsdPer1M,
            knownRate: true
        }
    }
    return {
        inputUsdPer1M: DEFAULT_USAGE_INPUT_USD_PER_1M,
        outputUsdPer1M: DEFAULT_USAGE_OUTPUT_USD_PER_1M,
        knownRate: false
    }
}

const estimateUsageCostUsd = (
    modelId: string | null | undefined,
    promptTokens: number,
    completionTokens: number
) => {
    const safePrompt = Math.max(0, Math.floor(promptTokens))
    const safeCompletion = Math.max(0, Math.floor(completionTokens))
    const rates = resolveModelUsageRates(modelId)
    const cost =
        (safePrompt / 1_000_000) * rates.inputUsdPer1M
        + (safeCompletion / 1_000_000) * rates.outputUsdPer1M
    return {
        costUsd: Number(cost.toFixed(10)),
        knownRate: rates.knownRate
    }
}

const normalizeStreamUsage = (
    usage?: StreamUsageMetadata,
    modelId?: string | null
): MessageUsageMeta | undefined => {
    if (!usage) return undefined

    const promptTokens = Math.max(0, Math.floor(usage.prompt_tokens || 0))
    const completionTokens = Math.max(0, Math.floor(usage.completion_tokens || 0))
    const totalTokens = Math.max(
        0,
        Math.floor(
            usage.total_tokens
            || (promptTokens + completionTokens)
        )
    )

    const estimatedCost = estimateUsageCostUsd(modelId, promptTokens, completionTokens)
    const normalizedCost = typeof usage.cost_usd === 'number' && Number.isFinite(usage.cost_usd)
        ? Math.max(0, usage.cost_usd)
        : estimatedCost.costUsd

    return {
        promptTokens,
        completionTokens,
        totalTokens,
        costUsd: normalizedCost,
        estimated: usage.estimated ?? !estimatedCost.knownRate
    }
}

const buildStoredAssistantUsage = (
    tokenCount: number | null | undefined,
    modelId?: string | null
): MessageUsageMeta | undefined => {
    const completionTokens = typeof tokenCount === 'number'
        ? Math.max(0, Math.floor(tokenCount))
        : 0
    if (completionTokens <= 0) return undefined

    const estimatedCost = estimateUsageCostUsd(modelId, 0, completionTokens)
    return {
        promptTokens: 0,
        completionTokens,
        totalTokens: completionTokens,
        costUsd: estimatedCost.costUsd,
        estimated: true
    }
}

const buildModeRuntimeInstruction = (
    modeId: ConversationModeId,
    webSearchPreference: 'auto' | 'force_on'
): string => {
    const basePrompt = modeId === 'deep'
        ? MODE_PROMPT_DEEP
        : modeId === 'learn'
            ? MODE_PROMPT_LEARN
            : MODE_PROMPT_FAST
    const searchPrompt = webSearchPreference === 'force_on'
        ? 'Web search is forced ON for this turn. Use fresh evidence and cite sources when web data is used.'
        : 'Web search and tools are AUTO. Decide if they are needed from user intent and risk level.'

    return `${basePrompt}\n\n${searchPrompt}`.trim()
}

const buildUnifiedDiffPreview = (
    beforeContent: string,
    afterContent: string,
    copy: DiffPreviewCopy
): DiffPreviewBuildResult => {
    const beforeLinesRaw = beforeContent.replace(/\r\n/g, '\n').split('\n')
    const afterLinesRaw = afterContent.replace(/\r\n/g, '\n').split('\n')

    const truncatedInput =
        beforeLinesRaw.length > MAX_DIFF_INPUT_LINES || afterLinesRaw.length > MAX_DIFF_INPUT_LINES
    const beforeLines = beforeLinesRaw.slice(0, MAX_DIFF_INPUT_LINES)
    const afterLines = afterLinesRaw.slice(0, MAX_DIFF_INPUT_LINES)

    const rows = beforeLines.length
    const cols = afterLines.length
    const lcs: number[][] = Array.from({ length: rows + 1 }, () => Array<number>(cols + 1).fill(0))

    for (let i = rows - 1; i >= 0; i -= 1) {
        for (let j = cols - 1; j >= 0; j -= 1) {
            if (beforeLines[i] === afterLines[j]) {
                lcs[i][j] = lcs[i + 1][j + 1] + 1
            } else {
                lcs[i][j] = Math.max(lcs[i + 1][j], lcs[i][j + 1])
            }
        }
    }

    const diffBody: string[] = []
    let i = 0
    let j = 0
    while (i < rows && j < cols) {
        if (beforeLines[i] === afterLines[j]) {
            diffBody.push(` ${clipLineLength(beforeLines[i])}`)
            i += 1
            j += 1
            continue
        }

        if (lcs[i + 1][j] >= lcs[i][j + 1]) {
            diffBody.push(`-${clipLineLength(beforeLines[i])}`)
            i += 1
        } else {
            diffBody.push(`+${clipLineLength(afterLines[j])}`)
            j += 1
        }
    }

    while (i < rows) {
        diffBody.push(`-${clipLineLength(beforeLines[i])}`)
        i += 1
    }
    while (j < cols) {
        diffBody.push(`+${clipLineLength(afterLines[j])}`)
        j += 1
    }

    const truncatedOutput = diffBody.length > MAX_DIFF_OUTPUT_LINES
    const visibleBody = truncatedOutput ? diffBody.slice(0, MAX_DIFF_OUTPUT_LINES) : diffBody

    const lines: string[] = [
        copy.diffBefore(beforeLinesRaw.length),
        copy.diffAfter(afterLinesRaw.length)
    ]
    if (truncatedInput) {
        lines.push(copy.diffInputTruncatedNotice(MAX_DIFF_INPUT_LINES))
    }
    lines.push(...visibleBody)
    if (truncatedOutput) {
        lines.push(copy.diffOutputTruncatedNotice(MAX_DIFF_OUTPUT_LINES))
    }

    return {
        text: lines.join('\n'),
        truncatedInput,
        truncatedOutput
    }
}

const isLikelyMissingFileError = (error: unknown) => {
    const message = error instanceof Error ? error.message.toLowerCase() : String(error).toLowerCase()
    return message.includes('enoent') || message.includes('not found') || message.includes('no such file')
}

const containsAiToolArtifacts = (content: string) =>
    /```(?:\s*)(?:pigtex_fs|file_agent)|<pigtex(?:_|\.)(?:write|create|patch|read|delete|mkdir|rename|rm|ls|list)\s|<read_code>|<write_code>|\[(?:PIGTEX_TOOL_RESULT|FILE_AGENT_CONTEXT)]/i.test(content)

/** Detect hallucinated tool-call patterns that some models emit (bash commands, JSON tool objects, etc.) */
const containsHallucinatedToolCalls = (content: string) =>
    /\{"command"\s*:\s*\[|"recipient"\s*:\s*"shell"|assistant\s+to=\w+\s+code:|<tool_call>|<function_calls>|<invoke\s|^\s*\{"command":/im.test(content)

const isInternalToolResultPayload = (content: string) =>
    /^\s*\[PIGTEX_TOOL_RESULT]/i.test(content) || isFileAgentContextPayload(content)

/**
 * Strip hallucinated tool-call artifacts that some AI models emit.
 * These include JSON command objects, `assistant to=shell` blocks,
 * `<tool_call>` XML, and repetitive internal reasoning about tool usage.
 */
const stripHallucinatedToolCalls = (text: string): string => {
    return text
        // JSON command objects on their own line(s): {"command":["bash",...], "workdir":"..."}
        .replace(/^[ \t]*\{[^{}]*"command"\s*:\s*\[.*?\].*?\}[ \t]*$/gm, '')
        // JSON recipient/shell objects: {"recipient":"shell",...}
        .replace(/^[ \t]*\{[^{}]*"recipient"\s*:\s*"[^"]*".*?\}[ \t]*$/gm, '')
        // `assistant to=shell code:` style blocks (tool call preamble + following JSON)
        .replace(/^[ \t]*assistant\s+to=\w+\s+code:\s*$/gm, '')
        // <tool_call>...</tool_call> complete
        .replace(/<tool_call>[\s\S]*?<\/tool_call>/gi, '')
        // <tool_call> incomplete (still streaming)
        .replace(/<tool_call>[\s\S]*$/gi, '')
        // <function_calls>...</function_calls> complete
        .replace(/<function_calls>[\s\S]*?<\/function_calls>/gi, '')
        // <function_calls> incomplete (still streaming)
        .replace(/<function_calls>[\s\S]*$/gi, '')
        // <invoke ...>...</invoke> complete
        .replace(/<invoke[\s\S]*?<\/invoke>/gi, '')
        // <invoke incomplete
        .replace(/<invoke[\s\S]*$/gi, '')
        // Lines that are just `...` or `` `...` `` wrapping JSON-like tool calls
        .replace(/^[ \t]*`+\s*$/gm, '')
}

const stripAiToolArtifacts = (content: string) => {
    const withoutCompleteToolBlocks = content
        // pigtex_fs JSON blocks
        .replace(/```(?:\s*)pigtex_fs[\s\S]*?```/gi, '')
        .replace(/```(?:\s*)file_agent[\s\S]*?```/gi, '')
        // New XML tags (complete)
        .replace(/<pigtex(?:_|\.)(?:write|create|patch)\s+[^>]*>[\s\S]*?<\/pigtex(?:_|\.)(?:write|create|patch)>/gi, '')
        .replace(/<pigtex(?:_|\.)(?:read|delete|mkdir|rename|rm|ls|list)\s+[^>]*?\/>/gi, '')
        .replace(/<pigtex(?:_|\.)(?:read|delete|mkdir|rename|rm|ls|list)\s+[^>]*?>[\s\S]*?<\/pigtex(?:_|\.)(?:read|delete|mkdir|rename|rm|ls|list)>/gi, '')
        // Tool result blocks
        .replace(/\[PIGTEX_TOOL_RESULT][\s\S]*?\[\/PIGTEX_TOOL_RESULT]/gi, '')
        .replace(/\[FILE_AGENT_CONTEXT][\s\S]*?\[\/FILE_AGENT_CONTEXT]/gi, '')
        // Legacy XML
        .replace(/<read_code>[\s\S]*?<\/read_code>/gi, '')
        .replace(/<write_code>[\s\S]*?<\/write_code>/gi, '')

    const withoutOpenToolBlocks = withoutCompleteToolBlocks
        // Incomplete pigtex_fs
        .replace(/```(?:\s*)pigtex_fs[\s\S]*$/gi, '')
        .replace(/```(?:\s*)file_agent[\s\S]*$/gi, '')
        // Incomplete new XML tags (still streaming)
        .replace(/<pigtex(?:_|\.)(?:write|create|patch)\s+[^>]*>[\s\S]*$/gi, '')
        .replace(/<pigtex(?:_|\.)(?:read|delete|mkdir|rename|rm|ls|list)\s+[^>]*$/gi, '')
        // Tool result
        .replace(/\[PIGTEX_TOOL_RESULT][\s\S]*$/gi, '')
        .replace(/\[\/PIGTEX_TOOL_RESULT]/gi, '')
        .replace(/\[FILE_AGENT_CONTEXT][\s\S]*$/gi, '')
        .replace(/\[\/FILE_AGENT_CONTEXT]/gi, '')
        // Legacy
        .replace(/<read_code>[\s\S]*$/gi, '')
        .replace(/<write_code>[\s\S]*$/gi, '')

    // Also strip hallucinated tool calls from non-PigTex patterns
    const withoutHallucinations = stripHallucinatedToolCalls(withoutOpenToolBlocks)

    return withoutHallucinations
        .replace(/[ \t]+\n/g, '\n')
        .replace(/\n{3,}/g, '\n\n')
        .trim()
}

const isImageMetadataOnlyMessage = (message: UIMessage) => {
    if (!message.images || message.images.length === 0) return false
    const normalizedContent = message.content.trim()
    if (!normalizedContent) return false
    return /^(Generated \d+ image\(s\)\.|Edited image successfully\.|Revised prompt:)/i.test(normalizedContent)
}

const buildStoredMessageContent = (
    text: string,
    images?: ImageAttachment[],
    media?: GeneratedMedia[],
    videoTask?: GeneratedVideoTask,
    mentions?: MentionItem[]
) => {
    const normalizedText = text.trim()
    const mentionList = mentions || []
    const referenceRef = mentionList.length > 0
        ? `<!--PIGTEX_REFERENCES ${JSON.stringify({
            items: mentionList.map((mention) => ({
                type: mention.type,
                relativePath: mention.relativePath,
                name: mention.name,
                referenceId: mention.referenceId,
                subtitle: mention.subtitle
            }))
        })} -->`
        : ''
    const imageRefs = (images || [])
        .map((img, index) => {
            const source = (img.serve_url || img.base64_data || '').trim()
            if (!source) return ''
            const alt = (img.filename || `image_${index + 1}`).trim() || `image_${index + 1}`
            return `![${alt}](${source})`
        })
        .filter(Boolean)
    const mediaRefs = (media || [])
        .map(item => {
            const source = (item.src || '').trim()
            if (!source || source.startsWith('blob:')) return ''
            return `<!--PIGTEX_MEDIA ${JSON.stringify({
                id: item.id,
                kind: item.kind,
                src: source,
                filename: item.filename,
                mimeType: item.mimeType,
                model: item.model,
                voice: item.voice,
                format: item.format,
                speed: item.speed,
                aspectRatio: item.aspectRatio,
                duration: item.duration,
                quality: item.quality,
                style: item.style,
                thumbnailUrl: item.thumbnailUrl,
                revisedPrompt: item.revisedPrompt,
            })} -->`
        })
        .filter(Boolean)
    const videoTaskRef = videoTask?.taskId
        ? `<!--PIGTEX_VIDEO_TASK ${JSON.stringify({
            taskId: videoTask.taskId,
            status: videoTask.status,
            model: videoTask.model,
            aspectRatio: videoTask.aspectRatio,
            duration: videoTask.duration,
            quality: videoTask.quality,
            style: videoTask.style,
        })} -->`
        : ''

    if (imageRefs.length === 0 && mediaRefs.length === 0 && !videoTaskRef && !referenceRef) {
        return normalizedText
    }

    const sections = [normalizedText, referenceRef, imageRefs.join('\n'), mediaRefs.join('\n'), videoTaskRef]
        .map(section => section.trim())
        .filter(Boolean)

    if (sections.length === 0) {
        return ''
    }

    return sections.join('\n\n')
}

interface DisplayModel {
    id: string
    label: string
    badges: DisplayModelFlag[]
    disabled: boolean
    type?: AIModel['type']
    transport?: AIModel['transport']
    provider?: AIModel['provider']
    provider_id?: AIModel['provider_id']
    supports_vision?: AIModel['supports_vision']
    capabilities?: AIModel['capabilities']
}

interface DisplayModelFlag extends AIModelProviderFlag {
    kind: 'recommendation' | 'status'
}

const buildFallbackDisplayModel = (modelId: string): DisplayModel => ({
    id: modelId,
    label: modelId,
    badges: [],
    disabled: false
})

const normalizeDisplayModelFlag = (
    kind: DisplayModelFlag['kind'],
    flag?: AIModelProviderFlag | null
): DisplayModelFlag | null => {
    const label = (flag?.label || '').trim()
    if (!label) return null

    const code = typeof flag?.code === 'string' && flag.code.trim()
        ? flag.code.trim()
        : undefined
    return {
        kind,
        label,
        code,
        tone: flag?.tone ?? (kind === 'recommendation' ? 'accent' : undefined),
        disabled: flag?.disabled === true
    }
}

const getDisplayModelFlags = (
    model: Pick<AIModel, 'recommendation_flag' | 'status_flag'>
): DisplayModelFlag[] => {
    const flags = [
        normalizeDisplayModelFlag('recommendation', model.recommendation_flag),
        normalizeDisplayModelFlag('status', model.status_flag)
    ].filter((flag): flag is DisplayModelFlag => flag !== null)
    return flags.slice(0, 2)
}

const getDisplayModelFlagSummary = (model: Pick<DisplayModel, 'badges'>): string | null => {
    if (!model.badges.length) return null
    return model.badges.map(flag => flag.label).join(' • ')
}

const hasRecommendationDisplayFlag = (model: Pick<DisplayModel, 'badges'>): boolean =>
    model.badges.some(flag => flag.kind === 'recommendation')

const buildModelShortlist = (models: DisplayModel[], selectedModelId: string): DisplayModel[] => {
    if (models.length <= MODEL_SHORTLIST_LIMIT) {
        return models
    }

    const shortlist: DisplayModel[] = []
    const seen = new Set<string>()
    const pushModel = (model: DisplayModel) => {
        if (seen.has(model.id)) return
        shortlist.push(model)
        seen.add(model.id)
    }

    models
        .filter(model => !model.disabled && hasRecommendationDisplayFlag(model))
        .forEach(model => {
            if (shortlist.length < MODEL_SHORTLIST_LIMIT) {
                pushModel(model)
            }
        })

    models
        .filter(model => !model.disabled)
        .forEach(model => {
            if (shortlist.length < MODEL_SHORTLIST_LIMIT) {
                pushModel(model)
            }
        })

    models.forEach(model => {
        if (shortlist.length < MODEL_SHORTLIST_LIMIT) {
            pushModel(model)
        }
    })

    if (selectedModelId.trim()) {
        const selected = models.find(model => model.id === selectedModelId)
        if (selected && !seen.has(selected.id)) {
            pushModel(selected)
        }
    }

    return shortlist
}

const mapAiModelToDisplayModel = (model: AIModel): DisplayModel => ({
    id: model.id,
    label: model.name || model.id,
    badges: getDisplayModelFlags(model),
    disabled: model.status_flag?.disabled === true,
    type: model.type,
    transport: model.transport,
    provider: model.provider,
    provider_id: model.provider_id,
    supports_vision: model.supports_vision,
    capabilities: model.capabilities
})

interface UIMessage {
    id: string
    storedMessageId?: string | null
    role: 'user' | 'assistant'
    content: string
    timestamp: string
    model?: string | null
    isStreaming?: boolean
    images?: ImageAttachment[]
    media?: GeneratedMedia[]
    videoTask?: GeneratedVideoTask
    files?: DocumentAttachment[]
    mentions?: MentionItem[]
    requestKind?: MessageRequestKind
    usage?: MessageUsageMeta
    memory?: MemoryContextMetadata
    citations?: WebCitation[]
    webSearch?: WebSearchMetadata
    learning?: LearningChatMetadata
    agentStatus?: {
        text: string
        tone: 'running' | 'success' | 'error' | 'warning' | 'info'
        sequence: number
    }
}

const hasPendingAssistantWork = (message: UIMessage): boolean => {
    if (message.role !== 'assistant') {
        return false
    }
    if (message.isStreaming) {
        return true
    }
    return Boolean(
        message.videoTask?.taskId
        && !message.media?.length
        && !isTerminalVideoTaskStatus(message.videoTask.status)
    )
}

interface AiActionConfirmDialog {
    isOpen: boolean
    actions: ParsedAiFileAction[]
}

interface AiActionDiffPreview {
    status: 'loading' | 'ready' | 'error'
    absolutePath: string
    diffText?: string
    message?: string
}

type ComposerMenuId = 'mode' | 'model' | 'add' | 'imageTool' | null

const ChatPanel = ({
    variant,
    conversationId,
    workspaceId,
    learningProgramId,
    learningProgramTitle,
    newChatToken,
    onConversationCreated,
    onConversationInvalidated,
    localRootPath,
    settings,
    onSettingsChange
}: ChatPanelProps) => {
    const { isVietnamese, locale } = useI18n()
    const copy = isVietnamese ? {
        messageCopied: 'Đã sao chép tin nhắn',
        unsupportedFormat: (name: string) => `Định dạng không hỗ trợ: ${name}`,
        tooLargeImage: (name: string) => `Quá lớn: ${name} (tối đa 10MB)`,
        maximumImages: `Tối đa ${MAX_IMAGES} ảnh`,
        onlyMoreImages: (remaining: number) => `Chỉ có thể thêm ${remaining} ảnh nữa`,
        failedProcessImage: 'Không thể xử lý ảnh',
        unsupportedFileType: (name: string) => `Loại tệp không hỗ trợ: ${name}`,
        tooLargeFile: (name: string) => `Quá lớn: ${name} (tối đa 20MB)`,
        maximumFiles: `Tối đa ${MAX_FILES} tệp`,
        onlyMoreFiles: (remaining: number) => `Chỉ có thể thêm ${remaining} tệp nữa`,
        attachedFiles: (count: number) => `Đã đính kèm ${count} tệp`,
        failedProcessFiles: 'Không thể xử lý tệp',
        pasteCodeSnippet: 'Dán đoạn mã:',
        noCodeSnippet: 'Không có đoạn mã nào được đính kèm',
        codeSnippetTruncated: `Đã đính kèm đoạn mã (cắt tại ${MAX_PASTED_CODE_CHARS} ký tự)`,
        codeSnippetAttached: 'Đã đính kèm đoạn mã',
        imageOnlyModel: 'Model này chỉ dành cho tạo ảnh. Hãy chuyển sang chế độ Ảnh hoặc chọn model chat.',
        chatModelRequired: 'Hãy chọn model chat trong dropdown trước khi gửi.',
        noImageModelAvailable: 'Không có model tạo ảnh nào. Endpoint hiện tại chưa hỗ trợ image generation.',
        imageModelRequired: 'Hãy chọn model image generation trong dropdown trước khi tạo ảnh.',
        promptRequiredImage: 'Cần nhập prompt để tạo/chỉnh sửa ảnh',
        promptRequiredVoice: 'Cần nhập nội dung để tạo voice',
        promptRequiredVideo: 'Cần nhập prompt để tạo video',
        voiceModelRequired: 'Hãy nhập model cho voice',
        videoModelRequired: 'Hãy nhập model cho video',
        voiceTransportUnavailable: 'Endpoint hiện tại chưa hỗ trợ voice generation theo chuẩn PigTex.',
        videoTransportUnavailable: 'Endpoint hiện tại chưa hỗ trợ video generation theo chuẩn PigTex.',
        attachImageFirst: 'Hãy đính kèm ít nhất một ảnh trước khi chỉnh ảnh',
        justNow: 'Vừa xong',
        savedTime: 'Đã lưu',
        imagePlaceholder: '[Ảnh]',
        editUsesFirstImage: 'Chế độ Ảnh hiện chỉ chỉnh ảnh đính kèm đầu tiên',
        imageHistoryFailed: 'Đã tạo ảnh nhưng chưa lưu được lịch sử chat',
        voiceHistoryFailed: 'Đã tạo voice nhưng chưa lưu được lịch sử chat',
        videoHistoryFailed: 'Đã tạo video nhưng chưa lưu được lịch sử chat',
        imageRequest: 'Yêu cầu ảnh',
        voiceRequest: 'Yêu cầu voice',
        videoRequest: 'Yêu cầu video',
        webSearchRunning: 'Đang tìm trên web...',
        webSearchTimeout: 'Web search quá thời gian',
        webSearchComplete: 'Đã tìm web xong',
        webSearchFailed: 'Web search thất bại',
        webSearchDisabled: 'Web search đang tắt',
        webSearchSkipped: 'Đã bỏ qua web search',
        supported: 'Được xác nhận',
        contradicted: 'Bị mâu thuẫn',
        conflicting: 'Xung đột',
        insufficient: 'Chưa đủ dữ liệu',
        online: 'Trực tuyến',
        imageGenerationRequest: 'Yêu cầu tạo ảnh',
        imageEditRequest: 'Yêu cầu chỉnh ảnh',
        messageWithImage: 'Tin nhắn có ảnh đính kèm',
        confidence: 'Độ tin cậy',
        claimsChecked: 'Số claim đã kiểm',
        conflicts: 'Xung đột',
        sources: 'Nguồn',
        aiSearchDetails: 'Thông tin tìm kiếm AI',
        learningDetails: 'Chi tiet hoc tap',
        learningGoal: 'Muc tieu',
        learningMode: 'Che do',
        learningNextStep: 'Buoc tiep theo',
        learningChecklist: 'Checklist',
        learningEvidence: 'Evidence',
        learningMemory: 'Memory update',
        learningSources: 'Nguon',
        suggestedModels: 'Model đề xuất',
        viewAllModels: 'Xem tất cả model',
        showLessModels: 'Thu gọn model',
        unknownDomain: 'không-rõ-miền',
        totalTokensTitle: (total: number, prompt: number, completion: number) => `Tổng token: ${total} • Prompt: ${prompt} • Completion: ${completion}`,
        copy: 'Sao chép',
        helpful: 'Hữu ích',
        notHelpful: 'Không hữu ích',
        regenerate: 'Tạo lại',
        dropImagesHere: 'Thả ảnh vào đây',
        removeMention: 'Xóa nhắc tới',
        removeImage: 'Xóa ảnh',
        removeFile: 'Xóa tệp',
        addAttachments: 'Thêm tệp đính kèm và thao tác nhanh',
        webSearchForcedTurn: 'Web search: Bật cưỡng bức cho lượt này',
        webSearchAutoTurn: 'Web search: Tự chọn theo tool',
        webSearchForced: 'Web search: Bật',
        webSearchAuto: 'Web search: Tự động',
        chooseImageWorkflow: 'Chọn chế độ tạo nội dung',
        toggleAiFiles: 'Bật/tắt AI thao tác tệp/thư mục trong workspace cục bộ',
        openFolderEnableAiFiles: 'Mở một thư mục cục bộ để bật AI thao tác tệp',
        aiFilesOn: 'AI Files Bật',
        aiFilesOff: 'AI Files Tắt',
        stopGenerating: 'Dừng tạo phản hồi',
        stop: 'Dừng',
        send: 'Gửi',
        inputHint: 'Nhấn Enter để gửi • Shift + Enter để xuống dòng',
        confirmAiActions: 'Xác nhận thao tác AI',
        workspaceOperationsProposed: (count: number) => `${count} thao tác workspace được đề xuất`,
        preparingDiffPreview: 'Đang chuẩn bị xem trước diff...',
        diffUnavailable: (message: string) => `Không thể tạo preview diff: ${message}`,
        showDiff: 'Hiện diff',
        hideDiff: 'Ẩn diff',
        cancelHint: 'hủy',
        cancel: 'Hủy',
        preparingDiff: 'Đang chuẩn bị diff...',
        applyAction: 'Áp dụng thao tác',
        applyActions: (count: number) => `Áp dụng ${count} thao tác`,
        closeEsc: 'Đóng (Esc)',
        fullSizePreview: 'Xem ảnh kích thước đầy đủ',
        diffBefore: (count: number) => `--- trước (${count} dòng)`,
        diffAfter: (count: number) => `+++ sau (${count} dòng)`,
        diffInputTruncatedNotice: (count: number) => `@@ Dữ liệu diff đầu vào đã bị cắt ở ${count} dòng đầu @@`,
        diffOutputTruncatedNotice: (count: number) => `@@ Kết quả diff đã bị cắt ở ${count} dòng đầu @@`,
        unsupportedResponse: 'AI trả về phản hồi file agent không hợp lệ',
        legacyProtocol: 'AI trả về giao thức thao tác tệp cũ',
        reachedMaxSteps: 'Đã chạm giới hạn bước tự động',
        stoppedRepeatedActions: 'Đã dừng các thao tác AI lặp lại',
        actionsCancelled: 'Đã hủy thao tác AI trên tệp',
        appliedActions: (count: number) => `Đã áp dụng ${count} thao tác AI trên tệp`,
        actionErrors: (count: number) => `Thao tác AI trên tệp có ${count} lỗi`,
        failedAiResponse: 'Không thể lấy phản hồi AI',
        failedApplyActionsTimeout: 'AI agent đã timeout khi áp dụng thao tác tệp',
        invalidFileActions: 'AI trả về thao tác tệp không hợp lệ',
        failedToolResponse: 'Không thể xử lý phản hồi tool',
        askAnything: 'Hỏi tôi bất cứ điều gì...',
        askAnythingMentions: 'Hỏi tôi bất cứ điều gì... (gõ @ để nhắc tới file, folder, conversation)',
        attachmentPrompt: 'Mô tả điều bạn muốn làm với tệp đính kèm...',
        describeImageGenerate: 'Mô tả ảnh bạn muốn tạo...',
        describeImageEdit: 'Mô tả cách chỉnh sửa ảnh đính kèm đầu tiên...',
        describeVoiceGenerate: 'Viết lời thoại hoặc script bạn muốn PigTex đọc...',
        describeVideoGenerate: 'Mô tả video bạn muốn tạo...',
        attachImageThenEdit: 'Hãy đính kèm ảnh trước rồi mô tả chỉnh sửa...',
        assistantResponding: 'Trợ lý đang phản hồi',
        imagePending: 'Đang tạo ảnh...',
        voicePending: 'Đang dựng voice...',
        videoPending: 'Đang tạo video...',
        generatedImagesText: (count: number) => `Đã tạo ${count} ảnh.`,
        editedImageText: 'Đã chỉnh ảnh thành công.',
        generatedVoiceText: 'Đã tạo voice thành công.',
        generatedVideoText: (count: number) => `Đã tạo ${count} video.`,
        queuedVideoText: (status: string) => `Video đang ở trạng thái ${status}. PigTex vẫn đang tự động kiểm tra và sẽ cập nhật ngay khi video hoặc preview sẵn sàng.`,
        videoTaskTerminalText: (status: string, detail?: string) => detail
            ? `Video kết thúc với trạng thái ${status}: ${detail}`
            : `Video kết thúc với trạng thái ${status}.`,
        revisedPrompt: 'Prompt đã chỉnh:',
        voiceStudioTitle: 'Voice Studio',
        voiceStudioSubtitle: 'Tạo voiceover ngay trong ô chat, vẫn dùng chung lịch sử hội thoại.',
        videoStudioTitle: 'Video Studio',
        videoStudioSubtitle: 'Tạo video ngắn với preset gọn, không cần rời composer.',
        selectModel: 'Chọn model',
        studioFieldModel: 'Model',
        studioModelPlaceholder: 'Nhập model do provider cung cấp',
        studioFieldVoice: 'Voice',
        studioFieldFormat: 'Định dạng',
        studioFieldSpeed: 'Tốc độ',
        studioFieldAspectRatio: 'Tỷ lệ khung',
        studioFieldDuration: 'Độ dài',
        studioFieldQuality: 'Chất lượng',
        studioFieldStyle: 'Phong cách',
        studioPill: 'Media Studio',
        audioPreviewLabel: 'Kết quả voice',
        videoPreviewLabel: 'Kết quả video',
        actionReadFile: (path: string) => `Đọc tệp ${path}`,
        actionCreateFile: (path: string) => `Tạo tệp ${path}`,
        actionUpdateFile: (path: string) => `Cập nhật tệp ${path}`,
        actionCreateFolder: (path: string) => `Tạo thư mục ${path}`,
        actionDeleteFile: (path: string) => `Xóa tệp ${path}`,
        actionDeleteFolder: (path: string) => `Xóa thư mục ${path}`,
        actionDeletePath: (path: string) => `Xóa đường dẫn ${path}`,
        actionRename: (from: string, to: string) => `Đổi tên ${from} → ${to}`,
        actionMissingNewPath: '(thiếu đường dẫn mới)',
        workspaceActionFallback: (type: string, path: string) => `${type} ${path}`,
        chatRequestFailedMessage: 'Xin lỗi, đã có lỗi khi kết nối tới AI. Vui lòng thử lại.',
        backendUnavailableLocal: (apiBaseUrl: string) =>
            `Không thể kết nối tới PigTex backend tại ${apiBaseUrl}. Backend local chưa chạy. Hãy mở backend rồi thử lại.`,
        backendUnavailableRemote: (apiBaseUrl: string) =>
            `Không thể kết nối tới PigTex backend tại ${apiBaseUrl}. Hãy kiểm tra URL backend hoặc kết nối mạng rồi thử lại.`,
        backendUnhealthy: (apiBaseUrl: string, statusCode?: number) =>
            `PigTex backend tại ${apiBaseUrl} đang phản hồi nhưng health check lỗi${statusCode ? ` HTTP ${statusCode}` : ''}. Hãy kiểm tra backend logs và dịch vụ phụ trợ rồi thử lại.`,
        imageRequestFailedMessage: 'Xin lỗi, việc tạo hoặc chỉnh sửa ảnh đã thất bại. Vui lòng thử prompt hoặc model khác.',
        voiceRequestFailedMessage: 'Xin lỗi, việc tạo voice đã thất bại. Hãy thử model, voice hoặc nội dung khác.',
        videoRequestFailedMessage: 'Xin lỗi, việc tạo video đã thất bại. Hãy thử model hoặc prompt khác.',
        fileCharCount: (count: number) => `${count} ký tự`,
        filePreviewChars: (count: number) => `${count}k ký tự`,
        agentReviewingWorkspace: '[Agent] Đang quét workspace để đọc/review...',
        agentReviewedWorkspace: (dirs: number, entries: number, files: number) =>
            `[Agent] Đã quét ${dirs} thư mục, thấy ${entries} mục, đọc ${files} tệp văn bản.`,
        agentTrimmedWorkspaceContext: '[Agent] Context workspace đã được cắt gọn theo budget an toàn trước khi gửi cho model.',
        agentPatchingFile: (path: string) => `[Agent] Đang patch tệp: ${path}...`,
        agentWritingFile: (path: string) => `[Agent] Đang ghi tệp: ${path}...`,
        agentCompletedPath: (path: string, count: number) => `• Xong: ${path} (${count} ký tự)`,
        agentWriteFileError: (path: string, message: string) => `• Lỗi ghi tệp: ${path} - ${message}`,
        agentPatchError: (message: string) => `• Lỗi patch: ${message}`,
        agentTimeout: '[Agent] Timeout khi chạy tự động. Đã dừng để tránh treo phiên.',
        agentInvalidPlannerBlock: '[Agent] Planner trả về file_agent block chưa hợp lệ. Đang yêu cầu sửa lại...',
        agentLegacyProtocol: '[Agent] AI trả về protocol file action cũ. Đang yêu cầu chuyển sang file_agent...',
        agentReachedMaxSteps: '[Agent] Đã chạm giới hạn bước tự động. Bỏ qua thao tác bổ sung để tránh lặp.',
        agentInvalidActionList: '[Agent] Planner trả về danh sách action chưa hợp lệ. Đang yêu cầu sửa lại...',
        agentSkippedActions: (count: number) => `[Agent] Bỏ qua ${count} thao tác đã hoàn thành hoặc vừa lỗi trước đó.`,
        agentNoNewActions: '[Agent] Không còn thao tác mới cần thực thi. Đã dừng để tránh lặp.',
        agentRepeatedLoop: '[Agent] Phát hiện vòng lặp thao tác trùng lặp, đã dừng tự động.',
        agentExecutingActions: (count: number) => `[Agent] Đang thực thi ${count} thao tác tệp...`,
        agentActionStart: (index: number, total: number, message: string) => `[Agent] (${index}/${total}) ${message}`,
        agentActionProgress: (message: string) => `[Agent] ... ${message}`,
        agentActionSuccess: (message: string) => `• Xong: ${message}`,
        agentActionError: (message: string) => `• Lỗi: ${message}`,
        agentBatchFinished: (applied: number, total: number, errors: number) =>
            errors > 0
                ? `[Agent] Hoàn tất ${applied}/${total} thao tác, có ${errors} lỗi.`
                : `[Agent] Hoàn tất ${applied}/${total} thao tác.`,
        agentSummarizingChanges: '[Agent] Đang tổng hợp kết quả thay đổi...',
        agentAppliedChangesFinal: (count: number) =>
            count <= 1
                ? 'Đã áp dụng xong 1 thao tác trên tệp.'
                : `Đã áp dụng xong ${count} thao tác trên tệp.`,
    } : {
        messageCopied: 'Message copied',
        unsupportedFormat: (name: string) => `Unsupported format: ${name}`,
        tooLargeImage: (name: string) => `Too large: ${name} (max 10MB)`,
        maximumImages: `Maximum ${MAX_IMAGES} images allowed`,
        onlyMoreImages: (remaining: number) => `Only ${remaining} more image(s) can be added`,
        failedProcessImage: 'Failed to process image',
        unsupportedFileType: (name: string) => `Unsupported file type: ${name}`,
        tooLargeFile: (name: string) => `Too large: ${name} (max 20MB)`,
        maximumFiles: `Maximum ${MAX_FILES} files allowed`,
        onlyMoreFiles: (remaining: number) => `Only ${remaining} more file(s) can be added`,
        attachedFiles: (count: number) => `Attached ${count} file(s)`,
        failedProcessFiles: 'Failed to process files',
        pasteCodeSnippet: 'Paste code snippet:',
        noCodeSnippet: 'No code snippet attached',
        codeSnippetTruncated: `Code snippet attached (truncated to ${MAX_PASTED_CODE_CHARS} chars)`,
        codeSnippetAttached: 'Code snippet attached',
        imageOnlyModel: 'This model is image-only. Switch to Image mode or choose a chat model.',
        chatModelRequired: 'Please select a chat model from the dropdown before sending.',
        noImageModelAvailable: 'No image generation model available. The current endpoint does not support image generation.',
        imageModelRequired: 'Please select an image generation model from the dropdown before generating.',
        promptRequiredImage: 'Prompt is required for image generation/edit',
        promptRequiredVoice: 'Prompt is required for voice generation',
        promptRequiredVideo: 'Prompt is required for video generation',
        voiceModelRequired: 'Please enter a voice model',
        videoModelRequired: 'Please enter a video model',
        voiceTransportUnavailable: 'The current endpoint does not support voice generation in PigTex standard routing.',
        videoTransportUnavailable: 'The current endpoint does not support video generation in PigTex standard routing.',
        attachImageFirst: 'Attach at least one image before editing',
        justNow: 'Just now',
        savedTime: 'Saved',
        imagePlaceholder: '[Image]',
        editUsesFirstImage: 'Image mode currently edits the first attached image only',
        imageHistoryFailed: 'Image was created but chat history could not be saved',
        voiceHistoryFailed: 'Voice was created but chat history could not be saved',
        videoHistoryFailed: 'Video was created but chat history could not be saved',
        imageRequest: 'Image request',
        voiceRequest: 'Voice request',
        videoRequest: 'Video request',
        webSearchRunning: 'Web search in progress...',
        webSearchTimeout: 'Web search timed out',
        webSearchComplete: 'Web search completed',
        webSearchFailed: 'Web search failed',
        webSearchDisabled: 'Web search disabled',
        webSearchSkipped: 'Web search skipped',
        supported: 'Supported',
        contradicted: 'Contradicted',
        conflicting: 'Conflicting',
        insufficient: 'Insufficient',
        online: 'Online',
        imageGenerationRequest: 'Image generation request',
        imageEditRequest: 'Image edit request',
        messageWithImage: 'Message with image attachment',
        confidence: 'Confidence',
        claimsChecked: 'Claims checked',
        conflicts: 'Conflicts',
        sources: 'Sources',
        aiSearchDetails: 'AI search details',
        learningDetails: 'Learning details',
        learningGoal: 'Goal',
        learningMode: 'Mode',
        learningNextStep: 'Next step',
        learningChecklist: 'Checklist',
        learningEvidence: 'Evidence',
        learningMemory: 'Memory update',
        learningSources: 'Sources',
        suggestedModels: 'Suggested models',
        viewAllModels: 'View all models',
        showLessModels: 'Show fewer models',
        unknownDomain: 'unknown-domain',
        totalTokensTitle: (total: number, prompt: number, completion: number) => `Total tokens: ${total} • Prompt: ${prompt} • Completion: ${completion}`,
        copy: 'Copy',
        helpful: 'Helpful',
        notHelpful: 'Not helpful',
        regenerate: 'Regenerate',
        dropImagesHere: 'Drop images here',
        removeMention: 'Remove mention',
        removeImage: 'Remove image',
        removeFile: 'Remove file',
        addAttachments: 'Add attachments and quick actions',
        webSearchForcedTurn: 'Web search: Forced ON for this turn',
        webSearchAutoTurn: 'Web search: Auto tool selection',
        webSearchForced: 'Web search: Forced ON',
        webSearchAuto: 'Web search: Auto',
        chooseImageWorkflow: 'Choose creation mode',
        toggleAiFiles: 'Toggle AI file/folder actions in local workspace',
        openFolderEnableAiFiles: 'Open a local folder to enable AI file actions',
        aiFilesOn: 'AI Files On',
        aiFilesOff: 'AI Files Off',
        stopGenerating: 'Stop generating',
        stop: 'Stop',
        send: 'Send',
        inputHint: 'Press Enter to send • Shift + Enter for new line',
        confirmAiActions: 'Confirm AI Actions',
        workspaceOperationsProposed: (count: number) => `${count} workspace operation${count !== 1 ? 's' : ''} proposed`,
        preparingDiffPreview: 'Preparing diff preview...',
        diffUnavailable: (message: string) => `Diff preview unavailable: ${message}`,
        showDiff: 'Show Diff',
        hideDiff: 'Hide Diff',
        cancelHint: 'to cancel',
        cancel: 'Cancel',
        preparingDiff: 'Preparing Diff...',
        applyAction: 'Apply Action',
        applyActions: (count: number) => `Apply ${count} Actions`,
        closeEsc: 'Close (Esc)',
        fullSizePreview: 'Full size preview',
        diffBefore: (count: number) => `--- before (${count} lines)`,
        diffAfter: (count: number) => `+++ after (${count} lines)`,
        diffInputTruncatedNotice: (count: number) => `@@ Diff input truncated to first ${count} lines @@`,
        diffOutputTruncatedNotice: (count: number) => `@@ Diff output truncated to first ${count} lines @@`,
        unsupportedResponse: 'AI returned invalid file agent response',
        legacyProtocol: 'AI returned legacy file action protocol',
        reachedMaxSteps: 'Reached max automatic tool steps',
        stoppedRepeatedActions: 'Stopped repeated AI file actions',
        actionsCancelled: 'AI file actions cancelled',
        appliedActions: (count: number) => `Applied ${count} AI file action(s)`,
        actionErrors: (count: number) => `AI file actions had ${count} error(s)`,
        failedAiResponse: 'Failed to get AI response',
        failedApplyActionsTimeout: 'AI agent timed out while applying file actions',
        invalidFileActions: 'AI returned invalid file actions',
        failedToolResponse: 'Failed to process tool response',
        askAnything: 'Ask me anything...',
        askAnythingMentions: 'Ask me anything... (type @ to mention files, folders, or conversations)',
        attachmentPrompt: 'Describe what you want to do with the attachment(s)...',
        describeImageGenerate: 'Describe the image you want to generate...',
        describeImageEdit: 'Describe how to edit the first attached image...',
        describeVoiceGenerate: 'Write the script or voiceover you want PigTex to read...',
        describeVideoGenerate: 'Describe the video you want to generate...',
        attachImageThenEdit: 'Attach an image first, then describe edits...',
        assistantResponding: 'Assistant is responding',
        imagePending: 'Generating image...',
        voicePending: 'Generating voice...',
        videoPending: 'Generating video...',
        generatedImagesText: (count: number) => `Generated ${count} image(s).`,
        editedImageText: 'Edited image successfully.',
        generatedVoiceText: 'Generated voice successfully.',
        generatedVideoText: (count: number) => `Generated ${count} video(s).`,
        queuedVideoText: (status: string) => `Video task is ${status}. PigTex is still polling and will update as soon as the video or preview is ready.`,
        videoTaskTerminalText: (status: string, detail?: string) => detail
            ? `Video task ended with status ${status}: ${detail}`
            : `Video task ended with status ${status}.`,
        revisedPrompt: 'Revised prompt:',
        voiceStudioTitle: 'Voice Studio',
        voiceStudioSubtitle: 'Turn the prompt into a voiceover clip without leaving the chat composer.',
        videoStudioTitle: 'Video Studio',
        videoStudioSubtitle: 'Build short-form video prompts with compact presets inside the composer.',
        selectModel: 'Select model',
        studioFieldModel: 'Model',
        studioModelPlaceholder: 'Enter the provider model id',
        studioFieldVoice: 'Voice',
        studioFieldFormat: 'Format',
        studioFieldSpeed: 'Speed',
        studioFieldAspectRatio: 'Aspect Ratio',
        studioFieldDuration: 'Duration',
        studioFieldQuality: 'Quality',
        studioFieldStyle: 'Style',
        studioPill: 'Media Studio',
        audioPreviewLabel: 'Voice result',
        videoPreviewLabel: 'Video result',
        actionReadFile: (path: string) => `Read file ${path}`,
        actionCreateFile: (path: string) => `Create file ${path}`,
        actionUpdateFile: (path: string) => `Update file ${path}`,
        actionCreateFolder: (path: string) => `Create folder ${path}`,
        actionDeleteFile: (path: string) => `Delete file ${path}`,
        actionDeleteFolder: (path: string) => `Delete folder ${path}`,
        actionDeletePath: (path: string) => `Delete path ${path}`,
        actionRename: (from: string, to: string) => `Rename ${from} → ${to}`,
        actionMissingNewPath: '(missing new path)',
        workspaceActionFallback: (type: string, path: string) => `${type} ${path}`,
        chatRequestFailedMessage: 'Sorry, an error occurred while connecting to AI. Please try again.',
        backendUnavailableLocal: (apiBaseUrl: string) =>
            `Cannot connect to PigTex backend at ${apiBaseUrl}. Start the local backend and try again.`,
        backendUnavailableRemote: (apiBaseUrl: string) =>
            `Cannot connect to PigTex backend at ${apiBaseUrl}. Check the backend URL or your network and try again.`,
        backendUnhealthy: (apiBaseUrl: string, statusCode?: number) =>
            `PigTex backend at ${apiBaseUrl} is responding, but the health check failed${statusCode ? ` with HTTP ${statusCode}` : ''}. Check backend logs and dependent services, then try again.`,
        imageRequestFailedMessage: 'Sorry, image generation or editing failed. Please try another prompt or model.',
        voiceRequestFailedMessage: 'Sorry, voice generation failed. Try another model, voice, or script.',
        videoRequestFailedMessage: 'Sorry, video generation failed. Try another model or prompt.',
        fileCharCount: (count: number) => `${count} chars`,
        filePreviewChars: (count: number) => `${count}k chars`,
        agentReviewingWorkspace: '[Agent] Scanning workspace for reading and review...',
        agentReviewedWorkspace: (dirs: number, entries: number, files: number) =>
            `[Agent] Scanned ${dirs} folders, found ${entries} entries, read ${files} text file(s).`,
        agentTrimmedWorkspaceContext: '[Agent] Workspace context was trimmed to stay within the safe budget before sending to the model.',
        agentPatchingFile: (path: string) => `[Agent] Patching file: ${path}...`,
        agentWritingFile: (path: string) => `[Agent] Writing file: ${path}...`,
        agentCompletedPath: (path: string, count: number) => `• Done: ${path} (${count} chars)`,
        agentWriteFileError: (path: string, message: string) => `• File write error: ${path} - ${message}`,
        agentPatchError: (message: string) => `• Patch error: ${message}`,
        agentTimeout: '[Agent] Automatic execution timed out and was stopped to avoid hanging the session.',
        agentInvalidPlannerBlock: '[Agent] Planner returned an invalid file_agent block. Requesting a corrected block...',
        agentLegacyProtocol: '[Agent] AI returned the legacy file action protocol. Requesting file_agent format...',
        agentReachedMaxSteps: '[Agent] Reached the automatic step limit. Skipping additional actions to avoid loops.',
        agentInvalidActionList: '[Agent] Planner returned an invalid action list. Requesting a corrected block...',
        agentSkippedActions: (count: number) => `[Agent] Skipped ${count} action(s) that were already completed or just failed.`,
        agentNoNewActions: '[Agent] No new actions remain to execute. Stopping to avoid loops.',
        agentRepeatedLoop: '[Agent] Repeated action loop detected. Automatic execution stopped.',
        agentExecutingActions: (count: number) => `[Agent] Executing ${count} file action(s)...`,
        agentActionStart: (index: number, total: number, message: string) => `[Agent] (${index}/${total}) ${message}`,
        agentActionProgress: (message: string) => `[Agent] ... ${message}`,
        agentActionSuccess: (message: string) => `• Done: ${message}`,
        agentActionError: (message: string) => `• Error: ${message}`,
        agentBatchFinished: (applied: number, total: number, errors: number) =>
            errors > 0
                ? `[Agent] Finished ${applied}/${total} actions with ${errors} error(s).`
                : `[Agent] Finished ${applied}/${total} actions.`,
        agentSummarizingChanges: '[Agent] Summarizing applied changes...',
        agentAppliedChangesFinal: (count: number) =>
            count <= 1
                ? 'Applied 1 file action successfully.'
                : `Applied ${count} file actions successfully.`,
    }
    const suggestions = isVietnamese ? [
        { id: 'p1', text: 'Giúp mình viết một đề xuất dự án', icon: FileText, gradient: 'linear-gradient(135deg, #818cf8 0%, #6366f1 100%)' },
        { id: 'p2', text: 'Giải thích đoạn code này cho mình', icon: Code, gradient: 'linear-gradient(135deg, #a78bfa 0%, #7c3aed 100%)' },
        { id: 'p3', text: 'Tóm tắt tài liệu này giúp mình', icon: FileText, gradient: 'linear-gradient(135deg, #c084fc 0%, #9333ea 100%)' },
        { id: 'p4', text: 'Debug code giúp mình nha', icon: Sparkles, gradient: 'linear-gradient(135deg, #e879f9 0%, #c026d3 100%)' },
    ] : [
        { id: 'p1', text: 'Help me draft a project proposal', icon: FileText, gradient: 'linear-gradient(135deg, #818cf8 0%, #6366f1 100%)' },
        { id: 'p2', text: 'Explain this code to me', icon: Code, gradient: 'linear-gradient(135deg, #a78bfa 0%, #7c3aed 100%)' },
        { id: 'p3', text: 'Summarize this document for me', icon: FileText, gradient: 'linear-gradient(135deg, #c084fc 0%, #9333ea 100%)' },
        { id: 'p4', text: 'Help me debug this code', icon: Sparkles, gradient: 'linear-gradient(135deg, #e879f9 0%, #c026d3 100%)' },
    ]
    const greeting = isVietnamese ? 'Chào bạn! Mình là PigTex' : 'Hi there! I’m PigTex'
    const subtitle = isVietnamese
        ? 'Trợ lý AI tập trung hiệu năng. Chọn Nhanh, Sâu, hoặc Học ngay ở khung nhập.'
        : 'A high-performance AI assistant. Pick Fast, Deep, or Learn right from the input bar.'
    const conversationModesLocal: ConversationMode[] = isVietnamese ? [
        { id: 'fast', label: 'Nhanh', description: 'Ưu tiên tốc độ, tự chọn tool tối thiểu' },
        { id: 'deep', label: 'Sâu', description: 'Lập plan/checklist kỹ hơn, lặp tool chặt hơn' },
        { id: 'learn', label: 'Học', description: 'Tutor mode: bám mục tiêu, checklist, evidence, memory' },
    ] : [
        { id: 'fast', label: 'Fast', description: 'Prioritize speed with minimal tool use' },
        { id: 'deep', label: 'Deep', description: 'Use tighter planning, checklists, and tool loops' },
        { id: 'learn', label: 'Learn', description: 'Tutor mode with goal, checklist, evidence, and memory' },
    ]
    const imageToolModesLocal: ImageToolMode[] = isVietnamese ? [
        { id: 'chat', label: 'Chat' },
        { id: 'image', label: 'Ảnh' },
        { id: 'voice', label: 'Voice' },
        { id: 'video', label: 'Video' },
    ] : [
        { id: 'chat', label: 'Chat' },
        { id: 'image', label: 'Image' },
        { id: 'voice', label: 'Voice' },
        { id: 'video', label: 'Video' },
    ]
    const attachOptionsLocal: AttachmentOption[] = isVietnamese ? [
        { id: 'image', label: 'Thêm ảnh', icon: Image },
        { id: 'file', label: 'Tải tệp', icon: Paperclip },
        { id: 'code', label: 'Dán code', icon: Code },
        { id: 'web', label: 'Web search', icon: Globe },
    ] : [
        { id: 'image', label: 'Add image', icon: Image },
        { id: 'file', label: 'Upload file', icon: Paperclip },
        { id: 'code', label: 'Paste code', icon: Code },
        { id: 'web', label: 'Web search', icon: Globe },
    ]
    const modePendingText: Record<ConversationModeId, string> = {
        fast: isVietnamese ? 'Chế độ nhanh đang xử lý...' : 'Fast mode is working...',
        deep: isVietnamese ? 'Chế độ sâu đang lập kế hoạch và kiểm tra...' : 'Deep mode is planning and checking...',
        learn: isVietnamese ? 'Chế độ học đang theo dõi tiến trình học...' : 'Learn mode is tracking the learning flow...'
    }
    const formatConnectivityIssueMessage = (issue: ApiConnectivityIssue): string => {
        switch (issue.kind) {
            case 'backend_unreachable':
                return issue.isLoopback
                    ? copy.backendUnavailableLocal(issue.apiBaseUrl)
                    : copy.backendUnavailableRemote(issue.apiBaseUrl)
            case 'backend_unhealthy':
                return copy.backendUnhealthy(issue.apiBaseUrl, issue.statusCode)
            default:
                return copy.chatRequestFailedMessage
        }
    }
    const resolvedEndpointProvider: 'openai' | 'anthropic' | 'gemini' | 'alibaba' = resolveApiProviderForRequest(
        settings.apiProvider,
        settings.customEndpoint
    )
    const initialModel = settings.model.trim()
    const [inputValue, setInputValue] = useState('')
    const [isTyping, setIsTyping] = useState(false)
    const [messages, setMessages] = useState<UIMessage[]>([])
    const [selectedMode, setSelectedMode] = useState<ConversationMode>(conversationModesLocal[0])
    const [models, setModels] = useState<DisplayModel[]>([])
    const [selectedModel, setSelectedModel] = useState<DisplayModel>(buildFallbackDisplayModel(initialModel))
    const [openComposerMenu, setOpenComposerMenu] = useState<ComposerMenuId>(null)
    const [showAllModelsInMenu, setShowAllModelsInMenu] = useState(false)
    const [openStudioMenu, setOpenStudioMenu] = useState<StudioDropdownId>(null)
    const [webSearchEnabled, setWebSearchEnabled] = useState(false)
    const [selectedImageTool, setSelectedImageTool] = useState<ImageToolMode>(imageToolModesLocal[0])
    const [voiceStudio, setVoiceStudio] = useState<VoiceStudioState>({
        model: '',
        voice: OPENAI_VOICE_PRESETS[0],
        responseFormat: 'mp3',
        speed: '1.0'
    })
    const [videoStudio, setVideoStudio] = useState<VideoStudioState>({
        model: '',
        aspectRatio: '16:9',
        duration: '5',
        quality: 'standard',
        style: VIDEO_STYLE_PRESETS[0]
    })
    const [aiFileModeEnabled, setAiFileModeEnabled] = useState(settings.defaultAiFileMode)
    const [aiActionConfirmDialog, setAiActionConfirmDialog] = useState<AiActionConfirmDialog>({
        isOpen: false,
        actions: []
    })
    const [aiActionDiffPreviews, setAiActionDiffPreviews] = useState<Record<string, AiActionDiffPreview>>({})
    const [expandedActionDiffs, setExpandedActionDiffs] = useState<Record<string, boolean>>({})
    const [isPreparingActionDiffs, setIsPreparingActionDiffs] = useState(false)
    const [currentConversationId, setCurrentConversationId] = useState<string | null>(conversationId || null)
    const [conversationWorkspaceId, setConversationWorkspaceId] = useState<string | null>(workspaceId || null)
    const [learningLiveState, setLearningLiveState] = useState<LearningLiveState | null>(null)
    const [isLearningLiveLoading, setIsLearningLiveLoading] = useState(false)
    const [copiedMessageId, setCopiedMessageId] = useState<string | null>(null)

    const textareaRef = useRef<HTMLTextAreaElement>(null)
    const messagesEndRef = useRef<HTMLDivElement>(null)
    const abortControllerRef = useRef<AbortController | null>(null)
    const sendInFlightRef = useRef(false)
    const justCreatedConversation = useRef(false)
    const conversationLoadTokenRef = useRef(0)
    const currentConversationIdRef = useRef<string | null>(conversationId || null)
    const conversationWorkspaceIdRef = useRef<string | null>(workspaceId || null)
    const ignoredConversationIdRef = useRef<string | null>(null)
    const lastHandledNewChatTokenRef = useRef<number | undefined>(newChatToken)
    const messagesContainerRef = useRef<HTMLDivElement>(null)
    const aiActionConfirmResolverRef = useRef<((value: boolean) => void) | null>(null)
    const aiActionDiffRequestIdRef = useRef(0)
    const transientObjectUrlsRef = useRef<string[]>([])
    const videoPollingControllersRef = useRef(new Map<string, AbortController>())
    const videoPollingContextRef = useRef(new Map<string, {
        storedMessageId?: string | null
        conversationId?: string | null
        modelId: string
    }>())
    const videoPollingStartedRef = useRef(new Set<string>())

    function abortAllVideoPolling() {
        for (const controller of videoPollingControllersRef.current.values()) {
            controller.abort()
        }
        videoPollingControllersRef.current.clear()
        videoPollingContextRef.current.clear()
        videoPollingStartedRef.current.clear()
    }

    // ===== Image attachments =====
    const [imageAttachments, setImageAttachments] = useState<ImageAttachment[]>([])
    const [fileAttachments, setFileAttachments] = useState<DocumentAttachment[]>([])
    const [isDraggingImage, setIsDraggingImage] = useState(false)
    const [lightboxImage, setLightboxImage] = useState<string | null>(null)
    const imageInputRef = useRef<HTMLInputElement>(null)
    const fileInputRef = useRef<HTMLInputElement>(null)
    const dragCounterRef = useRef(0)

    // ===== @Mention system =====
    const [fileMentionItems, setFileMentionItems] = useState<MentionItem[]>([])
    const [conversationMentionItems, setConversationMentionItems] = useState<MentionItem[]>([])
    const [currentMentions, setCurrentMentions] = useState<MentionItem[]>([])
    const mention = useMention(textareaRef)
    const mentionItems = [...conversationMentionItems, ...fileMentionItems]
    const filteredMentionItems = filterMentionItems(mentionItems, mention.popup.query)

    const isCentered = variant === 'centered'
    const assistantProfile = { name: 'PigTex' } as const
    const assistantAvatarUrl = pigtexAvatarUrl
    const showModeMenu = openComposerMenu === 'mode'
    const showModelMenu = openComposerMenu === 'model'
    const showAddMenu = openComposerMenu === 'add'
    const showImageToolMenu = openComposerMenu === 'imageTool'
    const selectedModeOption = conversationModesLocal.find(mode => mode.id === selectedMode.id) || conversationModesLocal[0]
    const selectedModeButtonLabel = isVietnamese
        ? `Chế độ: ${selectedModeOption.label}`
        : `Mode: ${selectedModeOption.label}`
    const availableImageToolModes = imageToolModesLocal.filter(mode => {
        if (mode.id === 'chat') return true
        if (mode.id === 'image') return transportSupportsCapability(resolvedEndpointProvider, 'image_generation')
        if (mode.id === 'voice') return transportSupportsCapability(resolvedEndpointProvider, 'audio_speech')
        if (mode.id === 'video') return transportSupportsCapability(resolvedEndpointProvider, 'video_generation')
        return false
    })
    const selectedImageToolOption = availableImageToolModes.find(mode => mode.id === selectedImageTool.id) || availableImageToolModes[0] || imageToolModesLocal[0]
    const imageModels = filterModelsByCapability(models, 'image_generation', resolvedEndpointProvider)
    const voicePresetOptions = getVoicePresetsForProvider(resolvedEndpointProvider)
    const defaultVoiceModelHint = copy.studioModelPlaceholder
    const defaultVideoModelHint = copy.studioModelPlaceholder
    const voiceModelSuggestions = filterModelsByCapability(models, 'audio_speech', resolvedEndpointProvider).slice(0, 8)
    const videoModelSuggestions = filterModelsByCapability(models, 'video_generation', resolvedEndpointProvider).slice(0, 8)
    const voiceModelOptions: StudioOption[] = voiceModelSuggestions.map(model => ({
        value: model.id,
        label: model.id,
        description: model.label !== model.id
            ? model.label
            : getDisplayModelFlagSummary(model)
    }))
    const voicePresetStudioOptions: StudioOption[] = voicePresetOptions.map(voice => ({
        value: voice,
        label: voice
    }))
    const voiceFormatStudioOptions: StudioOption[] = VOICE_FORMAT_OPTIONS.map(format => ({
        value: format,
        label: format.toUpperCase()
    }))
    const videoAspectRatioStudioOptions: StudioOption[] = VIDEO_ASPECT_RATIO_OPTIONS.map(option => ({
        value: option,
        label: option
    }))
    const videoModelStudioOptions: StudioOption[] = videoModelSuggestions.map(model => ({
        value: model.id,
        label: model.id,
        description: model.label !== model.id
            ? model.label
            : getDisplayModelFlagSummary(model)
    }))
    const videoDurationStudioOptions: StudioOption[] = VIDEO_DURATION_OPTIONS.map(option => ({
        value: option,
        label: `${option}s`
    }))
    const videoQualityStudioOptions: StudioOption[] = VIDEO_QUALITY_OPTIONS.map(option => ({
        value: option,
        label: option
    }))
    const videoStyleStudioOptions: StudioOption[] = VIDEO_STYLE_PRESETS.map(style => ({
        value: style,
        label: style
    }))
    const renderModelBadges = (badges: DisplayModelFlag[]) => {
        if (badges.length === 0) return null
        return (
            <span className="model-badge-list">
                {badges.map(flag => (
                    <span
                        key={`${flag.kind}-${flag.code || flag.label}`}
                        className={`model-badge model-badge-tone-${flag.tone || 'neutral'}`}
                    >
                        {flag.label}
                    </span>
                ))}
            </span>
        )
    }
    const formatUiDateTime = useCallback((value?: string | null) => {
        if (!value) return ''
        const date = new Date(value)
        if (Number.isNaN(date.getTime())) return value
        return new Intl.DateTimeFormat(locale, {
            day: '2-digit',
            month: 'short',
            hour: '2-digit',
            minute: '2-digit'
        }).format(date)
    }, [locale])
    const learningCockpitCopy = isVietnamese ? {
        cockpitTitle: 'Learn cockpit',
        focus: 'Focus',
        goal: 'Target',
        nextAction: 'Next evidence',
        deadline: 'Deadline',
        reviewLoad: 'Review load',
        remaining: 'Con lai',
        dueNow: 'Den han',
        stalled: 'Bi ket',
        sources: 'Nguon dang uu tien',
        misconceptions: 'Watch-outs',
        successSignals: 'Dau hieu dat',
        minutesPerSession: 'Phut/buoi',
        sessionsPerWeek: 'Buoi/tuan',
        loading: 'Dang cap nhat learn state...',
        noData: 'Gui them muc tieu, bai lam hoac tai lieu de PigTex Learn khoi dong cockpit.',
    } : {
        cockpitTitle: 'Learn cockpit',
        focus: 'Focus',
        goal: 'Target',
        nextAction: 'Next evidence',
        deadline: 'Deadline',
        reviewLoad: 'Review load',
        remaining: 'Remaining',
        dueNow: 'Due now',
        stalled: 'Stalled',
        sources: 'Preferred sources',
        misconceptions: 'Watch-outs',
        successSignals: 'Success signals',
        minutesPerSession: 'Mins/session',
        sessionsPerWeek: 'Sessions/week',
        loading: 'Refreshing learning state...',
        noData: 'Send a goal, response, or material to let PigTex Learn initialize the cockpit.',
    }
    const getLearningDeadlineStatusLabelLocal = (status?: string | null) => {
        const normalized = (status || '').trim().toLowerCase()
        const lookup: Record<string, string> = isVietnamese ? {
            none: 'Chua co',
            on_track: 'On track',
            at_risk: 'Can day pace',
            urgent: 'Gap'
        } : {
            none: 'No deadline',
            on_track: 'On track',
            at_risk: 'At risk',
            urgent: 'Urgent'
        }
        return lookup[normalized] || normalized || (isVietnamese ? 'Chua co' : 'No deadline')
    }
    const getLearningReviewPressureLabelLocal = (pressure?: string | null) => {
        const normalized = (pressure || '').trim().toLowerCase()
        const lookup: Record<string, string> = isVietnamese ? {
            low: 'Nhe',
            medium: 'Vua',
            high: 'Cao'
        } : {
            low: 'Low',
            medium: 'Medium',
            high: 'High'
        }
        return lookup[normalized] || normalized || (isVietnamese ? 'Nhe' : 'Low')
    }
    const isTransientPendingMessageLocal = (message: string) => {
        const normalized = message.trim()
        return normalized === (isVietnamese ? 'Đang xử lý yêu cầu...' : 'Processing your request...')
            || normalized === modePendingText.fast
            || normalized === modePendingText.deep
            || normalized === modePendingText.learn
    }
    const getWebSearchStatusLabelLocal = (status: WebSearchMetadata['status']) => {
        switch (status) {
            case 'running':
                return copy.webSearchRunning
            case 'timeout':
                return copy.webSearchTimeout
            case 'complete':
                return copy.webSearchComplete
            case 'error':
                return copy.webSearchFailed
            case 'disabled':
                return copy.webSearchDisabled
            case 'skipped':
            default:
                return copy.webSearchSkipped
        }
    }
    const getWebSearchModeLabelLocal = (mode?: WebSearchMetadata['mode']) => {
        switch (mode) {
            case 'deep':
                return conversationModesLocal[1].label
            case 'fast':
                return conversationModesLocal[0].label
            case 'auto':
                return isVietnamese ? 'Tự động' : 'Auto'
            default:
                return ''
        }
    }
    const getLearningModeLabelLocal = (mode?: string | null) => {
        const normalized = (mode || '').trim().toLowerCase()
        if (!normalized) return ''

        const lookup: Record<string, string> = isVietnamese ? {
            teach: 'Day hoc',
            teacher: 'Day hoc',
            explain: 'Giai thich',
            lecture: 'Giai thich',
            guided: 'Co huong dan',
            guided_practice: 'Luyen co huong dan',
            practice: 'Luyen tap',
            independent_practice: 'Lam doc lap',
            retrieval: 'Goi nho',
            assess: 'Danh gia',
            assessment: 'Danh gia',
            review: 'On tap',
            remediate: 'Cuong co',
            summarize_progress: 'Tong ket',
            transfer: 'Van dung'
        } : {
            teach: 'Teach',
            teacher: 'Teach',
            explain: 'Explain',
            lecture: 'Explain',
            guided: 'Guided',
            guided_practice: 'Guided practice',
            practice: 'Practice',
            independent_practice: 'Independent practice',
            retrieval: 'Retrieval',
            assess: 'Assessment',
            assessment: 'Assessment',
            review: 'Review',
            remediate: 'Remediate',
            summarize_progress: 'Summary',
            transfer: 'Transfer'
        }

        return lookup[normalized]
            || normalized
                .replace(/[_-]+/g, ' ')
                .replace(/\b\w/g, (char) => char.toUpperCase())
    }
    const getLearningChecklistTone = (status?: string | null) => {
        const normalized = (status || '').trim().toLowerCase()
        if (['done', 'complete', 'completed', 'passed', 'mastered', 'verified'].includes(normalized)) {
            return 'success'
        }
        if (['review_due', 'retry', 'needs_retry', 'blocked', 'partial'].includes(normalized)) {
            return 'warning'
        }
        if (['failed', 'incorrect', 'downgraded'].includes(normalized)) {
            return 'danger'
        }
        return 'neutral'
    }
    const getLearningChecklistStatusLabel = (status?: string | null) => {
        const normalized = (status || '').trim().toLowerCase()
        if (!normalized) return isVietnamese ? 'Dang theo doi' : 'Tracking'

        const lookup: Record<string, string> = isVietnamese ? {
            not_started: 'Chua bat dau',
            pending: 'Cho lam',
            diagnosing: 'Dang chan doan',
            active: 'Dang hoc',
            partial: 'Chua vung',
            in_progress: 'Dang lam',
            done: 'Hoan tat',
            complete: 'Hoan tat',
            completed: 'Hoan tat',
            passed: 'Dat',
            mastered: 'Vung',
            verified: 'Da xac nhan',
            review_due: 'Can on',
            retry: 'Lam lai',
            needs_retry: 'Lam lai',
            blocked: 'Tac'
        } : {
            not_started: 'Not started',
            pending: 'Pending',
            diagnosing: 'Diagnosing',
            active: 'Active',
            partial: 'Partial',
            in_progress: 'In progress',
            done: 'Done',
            complete: 'Done',
            completed: 'Done',
            passed: 'Passed',
            mastered: 'Mastered',
            verified: 'Verified',
            review_due: 'Review due',
            retry: 'Retry',
            needs_retry: 'Retry',
            blocked: 'Blocked'
        }

        return lookup[normalized]
            || normalized
                .replace(/[_-]+/g, ' ')
                .replace(/\b\w/g, (char) => char.toUpperCase())
    }
    const buildLearningDetailState = useCallback((learning?: LearningChatMetadata) => {
        if (!learning) return null

        const checklist = (
            learning.turn_output?.progress_checklist
            || learning.progress_checklist
            || learning.assessment?.progress_checklist
            || learning.learning_state?.progress_checklist
            || []
        ).filter(item => item && item.label).slice(0, 4)

        const evidence = (
            learning.turn_output?.evidence_collected
            || learning.assessment?.evidence_collected
            || []
        ).slice(0, 3)
        const sourceRefs = (
            learning.turn_output?.selected_source_refs
            || []
        ).slice(0, 3)

        const memorySummary =
            learning.turn_output?.memory_update_summary
            || learning.memory_update_summary
            || learning.assessment?.memory_update_summary
            || learning.learning_state?.last_memory_update_summary
            || null

        const goal =
            learning.learning_state?.current_goal?.operational_goal
            || learning.learning_state?.current_goal?.raw_goal
            || learning.coach_brief
            || null

        const mode =
            learning.turn_output?.instructional_mode
            || learning.assessment?.instructional_mode
            || null

        const nextStep =
            learning.turn_output?.next_step
            || learning.assessment?.next_step
            || learning.next_action
            || null

        const focusTitle =
            learning.turn_output?.focus_node_title
            || learning.focus_node?.title
            || learning.program_title
            || null

        const hasMemory = Boolean(
            memorySummary
            && (memorySummary.added.length || memorySummary.revised.length || memorySummary.downgraded.length || memorySummary.confidence)
        )

        if (!goal && !mode && !nextStep && !focusTitle && checklist.length === 0 && evidence.length === 0 && sourceRefs.length === 0 && !hasMemory) {
            return null
        }

        return {
            goal,
            mode,
            nextStep,
            focusTitle,
            checklist,
            evidence,
            sourceRefs,
            memorySummary: hasMemory ? memorySummary : null
        }
    }, [])
    const getClaimVerdictLabelLocal = (verdict: WebSearchClaimVerification['verdict']) => {
        switch (verdict) {
            case 'supported':
                return copy.supported
            case 'contradicted':
                return copy.contradicted
            case 'mixed':
                return copy.conflicting
            case 'insufficient':
            default:
                return copy.insufficient
        }
    }

    const updateConversationSelection = useCallback((nextConversationId: string | null, nextWorkspaceId: string | null) => {
        if (nextConversationId && ignoredConversationIdRef.current === nextConversationId) {
            ignoredConversationIdRef.current = null
        }
        currentConversationIdRef.current = nextConversationId
        conversationWorkspaceIdRef.current = nextWorkspaceId
        setCurrentConversationId(nextConversationId)
        setConversationWorkspaceId(nextWorkspaceId)
    }, [])

    const invalidateConversationSelection = useCallback((
        invalidConversationId?: string | null,
        options?: { clearMessages?: boolean }
    ) => {
        if (invalidConversationId) {
            ignoredConversationIdRef.current = invalidConversationId
        }
        currentConversationIdRef.current = null
        conversationWorkspaceIdRef.current = workspaceId || null
        justCreatedConversation.current = false
        setCurrentConversationId(null)
        setConversationWorkspaceId(workspaceId || null)
        if (options?.clearMessages) {
            setMessages([])
        }
        onConversationInvalidated?.()
    }, [onConversationInvalidated, workspaceId])

    const toggleComposerMenu = useCallback((menu: Exclude<ComposerMenuId, null>) => {
        setOpenStudioMenu(null)
        setOpenComposerMenu(prev => (prev === menu ? null : menu))
    }, [])

    const toggleStudioMenu = useCallback((menu: Exclude<StudioDropdownId, null>) => {
        setOpenComposerMenu(null)
        setOpenStudioMenu(prev => (prev === menu ? null : menu))
    }, [])

    const openStudioMenuById = useCallback((menu: Exclude<StudioDropdownId, null>) => {
        setOpenStudioMenu(menu)
    }, [])

    const closeComposerMenus = useCallback(() => {
        setOpenComposerMenu(null)
    }, [])

    const closeStudioMenus = useCallback(() => {
        setOpenStudioMenu(null)
    }, [])

    const closeAllMenus = useCallback(() => {
        closeComposerMenus()
        closeStudioMenus()
    }, [closeComposerMenus, closeStudioMenus])

    useEffect(() => {
        if (!showModelMenu) {
            setShowAllModelsInMenu(false)
        }
    }, [showModelMenu])

    // Auto-scroll to bottom
    const scrollToBottom = useCallback((smooth: boolean = true) => {
        messagesEndRef.current?.scrollIntoView({
            behavior: smooth ? 'smooth' : 'instant'
        })
    }, [])

    // Scroll on new messages
    useEffect(() => {
        scrollToBottom()
    }, [messages, scrollToBottom])

    useEffect(() => {
        currentConversationIdRef.current = currentConversationId
    }, [currentConversationId])

    useEffect(() => {
        conversationWorkspaceIdRef.current = conversationWorkspaceId
    }, [conversationWorkspaceId])

    useEffect(() => {
        setOpenStudioMenu(null)
    }, [resolvedEndpointProvider, selectedImageTool.id])

    useEffect(() => {
        if (!availableImageToolModes.some(mode => mode.id === selectedImageTool.id)) {
            setSelectedImageTool(availableImageToolModes[0])
        }
    }, [availableImageToolModes, selectedImageTool.id])

    // Sync conversationId prop before paint so a cleared parent selection cannot reuse a stale id.
    useLayoutEffect(() => {
        const nextConversationId = conversationId || null
        if (nextConversationId && ignoredConversationIdRef.current === nextConversationId) {
            return
        }
        if (ignoredConversationIdRef.current && ignoredConversationIdRef.current !== nextConversationId) {
            ignoredConversationIdRef.current = null
        }
        currentConversationIdRef.current = nextConversationId
        if (!nextConversationId) {
            conversationWorkspaceIdRef.current = workspaceId || null
        }
        setCurrentConversationId(nextConversationId)
    }, [conversationId])

    // Scope for a brand-new chat follows currently selected workspace.
    useEffect(() => {
        if (!currentConversationId) {
            conversationWorkspaceIdRef.current = workspaceId || null
            setConversationWorkspaceId(workspaceId || null)
        }
    }, [workspaceId, currentConversationId])

    // Explicit "new chat" reset from parent.
    useEffect(() => {
        if (newChatToken === undefined) return
        if (lastHandledNewChatTokenRef.current === undefined) {
            // Initial mount: do not force-reset just because token exists.
            lastHandledNewChatTokenRef.current = newChatToken
            return
        }
        if (newChatToken === lastHandledNewChatTokenRef.current) {
            return
        }
        lastHandledNewChatTokenRef.current = newChatToken

        // Cancel any in-flight generation and invalidate pending conversation loads.
        if (abortControllerRef.current) {
            abortControllerRef.current.abort()
            abortControllerRef.current = null
        }
        abortAllVideoPolling()
        conversationLoadTokenRef.current += 1
        justCreatedConversation.current = false

        setIsTyping(false)
        updateConversationSelection(null, workspaceId || null)
        setMessages([])
        setInputValue('')
        setCurrentMentions([])
        setFileAttachments([])
        setImageAttachments([])
    }, [newChatToken, updateConversationSelection, workspaceId])

    // Load models from currently selected endpoint settings.
    useEffect(() => {
        let isMounted = true
        setModels([])

        const byokApiKey = settings.apiKey.trim()
        const byokBaseUrl = settings.baseUrl.trim()
        const loadModelsPromise = byokApiKey && byokBaseUrl
            ? getModelsWithCredentials(
                byokApiKey,
                byokBaseUrl,
                resolvedEndpointProvider,
                { includeAllReturnedModels: true }
            )
            : getModels(true)

        loadModelsPromise
            .then((apiModels: AIModel[]) => {
                if (!isMounted) return

                const dedupedModels = Array.from(
                    new Map(apiModels.map((model) => [model.id, model])).values()
                )
                const displayModels: DisplayModel[] = dedupedModels.map(mapAiModelToDisplayModel)
                setModels(displayModels)

                const configuredModelId = settings.model.trim()
                if (configuredModelId) {
                    const matched = displayModels.find(model => model.id === configuredModelId)
                    if (matched) {
                        setSelectedModel(matched)
                        return
                    }
                }

                if (configuredModelId) {
                    setSelectedModel(buildFallbackDisplayModel(configuredModelId))
                    return
                }

                setSelectedModel(buildFallbackDisplayModel(''))
            })
            .catch(() => {
                if (!isMounted) return
                setModels([])
                const configuredModelId = settings.model.trim()
                if (configuredModelId) {
                    setSelectedModel(buildFallbackDisplayModel(configuredModelId))
                    return
                }

                setSelectedModel(buildFallbackDisplayModel(''))
            })

        return () => {
            isMounted = false
        }
    }, [
        settings.model,
        settings.apiProvider,
        settings.customEndpoint,
        settings.baseUrl,
        settings.apiKey,
        onSettingsChange,
        resolvedEndpointProvider
    ])

    useEffect(() => {
        const configuredModelId = settings.model.trim()
        if (configuredModelId && selectedModel.id === configuredModelId) return

        if (configuredModelId) {
            const matched = models.find(model => model.id === configuredModelId)
            if (matched) {
                setSelectedModel(matched)
                return
            }
            setSelectedModel(buildFallbackDisplayModel(configuredModelId))
            return
        }

        setSelectedModel(buildFallbackDisplayModel(''))
    }, [settings.model, models, selectedModel.id])

    useEffect(() => {
        const currentVoicePresets = getVoicePresetsForProvider(resolvedEndpointProvider)
        setVoiceStudio(prev => {
            const currentModel = prev.model.trim()
            const nextModel = currentModel && models.some(model =>
                model.id === currentModel && modelSupportsCapability(model, 'audio_speech', resolvedEndpointProvider)
            )
                ? currentModel
                : ''
            const nextVoice = (() => {
                const currentVoice = prev.voice.trim()
                if (!currentVoice) {
                    return currentVoicePresets[0]
                }
                if (!currentVoicePresets.includes(currentVoice)) {
                    return currentVoicePresets[0]
                }
                return currentVoice
            })()
            if (prev.model === nextModel && prev.voice === nextVoice) {
                return prev
            }
            return {
                ...prev,
                model: nextModel,
                voice: nextVoice
            }
        })
        setVideoStudio(prev => {
            if (
                prev.model.trim()
                && models.some(model =>
                    model.id === prev.model.trim()
                    && modelSupportsCapability(model, 'video_generation', resolvedEndpointProvider)
                )
            ) {
                return prev
            }
            return {
                ...prev,
                model: ''
            }
        })
    }, [models, resolvedEndpointProvider])

    useEffect(() => {
        const toolId = selectedImageTool.id
        if (toolId !== 'voice' && toolId !== 'video') {
            return
        }
        if (imageAttachments.length > 0) {
            setImageAttachments([])
        }
        if (fileAttachments.length > 0) {
            setFileAttachments([])
        }
        if (currentMentions.length > 0) {
            setCurrentMentions([])
        }
        if (webSearchEnabled) {
            setWebSearchEnabled(false)
        }
        mention.closePopup()
    }, [
        selectedImageTool.id,
        imageAttachments.length,
        fileAttachments.length,
        currentMentions.length,
        webSearchEnabled,
        mention
    ])

    useEffect(() => {
        if (!learningProgramId) return

        if (selectedMode.id !== 'learn') {
            const learnMode = conversationModesLocal.find(mode => mode.id === 'learn') || conversationModesLocal[0]
            setSelectedMode(learnMode)
        }

        if (selectedImageTool.id !== 'chat') {
            const chatTool = imageToolModesLocal.find(mode => mode.id === 'chat') || imageToolModesLocal[0]
            setSelectedImageTool(chatTool)
        }
    }, [
        conversationModesLocal,
        imageToolModesLocal,
        learningProgramId,
        selectedImageTool.id,
        selectedMode.id
    ])

    useEffect(() => {
        const activeWorkspaceId = conversationWorkspaceId || workspaceId || null
        const hasEmbeddedLearning = messages.some((message) => (
            Boolean(message.learning?.program_id)
            || Boolean(message.learning?.learning_state)
        ))
        const shouldLoadLearning =
            selectedMode.id === 'learn'
            || Boolean(learningProgramId)
            || hasEmbeddedLearning

        if (!shouldLoadLearning) {
            setLearningLiveState(null)
            setIsLearningLiveLoading(false)
            return
        }

        let cancelled = false
        setIsLearningLiveLoading(true)

        void getLearningLiveState({
            conversationId: currentConversationId || undefined,
            workspaceId: activeWorkspaceId,
            programId: learningProgramId || undefined
        })
            .then((data) => {
                if (cancelled) return
                setLearningLiveState(data.enabled ? data : null)
            })
            .catch(() => {
                if (!cancelled) {
                    setLearningLiveState(null)
                }
            })
            .finally(() => {
                if (!cancelled) {
                    setIsLearningLiveLoading(false)
                }
            })

        return () => {
            cancelled = true
        }
    }, [
        selectedMode.id,
        learningProgramId,
        currentConversationId,
        conversationWorkspaceId,
        workspaceId,
        messages.length
    ])

    useEffect(() => {
        setAiFileModeEnabled(settings.defaultAiFileMode)
    }, [settings.defaultAiFileMode])

    useEffect(() => {
        return () => {
            abortAllVideoPolling()
            for (const url of transientObjectUrlsRef.current) {
                URL.revokeObjectURL(url)
            }
            transientObjectUrlsRef.current = []
        }
    }, [])

    // ===== Load file tree for @mention =====
    useEffect(() => {
        if (!localRootPath || !window.electronAPI?.listDirectory) {
            setFileMentionItems([])
            return
        }

        let cancelled = false
        const tree: Record<string, { name: string; path: string; type: 'file' | 'directory' }[]> = {}

        const loadRecursive = async (dirPath: string, depth: number) => {
            if (cancelled || depth > 2) return
            try {
                const entries = await window.electronAPI!.listDirectory({
                    rootPath: localRootPath!,
                    dirPath
                })
                if (cancelled) return
                tree[dirPath] = entries
                // Load subdirectories (max depth 2)
                for (const entry of entries) {
                    if (entry.type === 'directory') {
                        await loadRecursive(entry.path, depth + 1)
                    }
                }
            } catch {
                // Silently skip unreadable directories
            }
        }

        loadRecursive(localRootPath, 0).then(() => {
            if (!cancelled) {
                setFileMentionItems(flattenFileTree(tree, localRootPath!))
            }
        })

        // Also refresh when filesystem changes
        const handleFsRefresh = () => {
            const freshTree: Record<string, { name: string; path: string; type: 'file' | 'directory' }[]> = {}
            const reload = async (dirPath: string, depth: number) => {
                if (cancelled || depth > 2) return
                try {
                    const entries = await window.electronAPI!.listDirectory({
                        rootPath: localRootPath!,
                        dirPath
                    })
                    if (cancelled) return
                    freshTree[dirPath] = entries
                    for (const entry of entries) {
                        if (entry.type === 'directory') {
                            await reload(entry.path, depth + 1)
                        }
                    }
                } catch {
                    // skip
                }
            }
            reload(localRootPath!, 0).then(() => {
                if (!cancelled) {
                    setFileMentionItems(flattenFileTree(freshTree, localRootPath!))
                }
            })
        }

        window.addEventListener('pigtex:local-fs-refresh', handleFsRefresh)
        return () => {
            cancelled = true
            window.removeEventListener('pigtex:local-fs-refresh', handleFsRefresh)
        }
    }, [localRootPath])

    useEffect(() => {
        let cancelled = false
        const preferredWorkspaceId = conversationWorkspaceId || workspaceId || null

        const buildConversationMentions = async () => {
            try {
                const conversations = await getLocalConversations(undefined, 120)
                if (cancelled) return

                const sorted = conversations
                    .filter((conversation) => conversation.id !== currentConversationId)
                    .sort((left, right) => {
                        const leftPreferred = (left.workspace_id || null) === preferredWorkspaceId ? 1 : 0
                        const rightPreferred = (right.workspace_id || null) === preferredWorkspaceId ? 1 : 0
                        if (leftPreferred !== rightPreferred) return rightPreferred - leftPreferred
                        return (right.total_messages || 0) - (left.total_messages || 0)
                    })

                setConversationMentionItems(
                    sorted.map((conversation) => {
                        const title = (conversation.title || '').trim()
                            || (isVietnamese ? 'Đoạn chat chưa đặt tên' : 'Untitled conversation')
                        const summary = (conversation.summary || '').trim()
                        const subtitle = summary
                            || (
                                isVietnamese
                                    ? `${conversation.total_messages || 0} tin nhắn`
                                    : `${conversation.total_messages || 0} messages`
                            )
                        return {
                            type: 'conversation',
                            name: title,
                            relativePath: subtitle,
                            absolutePath: '',
                            referenceId: conversation.id,
                            subtitle
                        } as MentionItem
                    })
                )
            } catch {
                if (!cancelled) {
                    setConversationMentionItems([])
                }
            }
        }

        void buildConversationMentions()

        const handleConversationRefresh = () => {
            void buildConversationMentions()
        }

        window.addEventListener('pigtex:conversation-updated', handleConversationRefresh)
        return () => {
            cancelled = true
            window.removeEventListener('pigtex:conversation-updated', handleConversationRefresh)
        }
    }, [currentConversationId, conversationWorkspaceId, workspaceId, isVietnamese])

    // Load messages when conversationId changes
    useEffect(() => {
        abortAllVideoPolling()
        if (justCreatedConversation.current && currentConversationId) {
            justCreatedConversation.current = false
            return
        }

        if (currentConversationId) {
            const requestToken = conversationLoadTokenRef.current + 1
            conversationLoadTokenRef.current = requestToken
            void loadConversationMessages(currentConversationId, requestToken)
        } else {
            conversationLoadTokenRef.current += 1
            justCreatedConversation.current = false
            setMessages([])
            conversationWorkspaceIdRef.current = workspaceId || null
            setConversationWorkspaceId(workspaceId || null)
        }
    }, [currentConversationId, workspaceId])

    const loadConversationMessages = async (convId: string, requestToken: number) => {
        try {
            const data = await getLocalConversation(convId)
            if (requestToken !== conversationLoadTokenRef.current) return
            conversationWorkspaceIdRef.current = data.workspace_id || null
            setConversationWorkspaceId(data.workspace_id || null)
            const uiMessages: UIMessage[] = data.messages
                .map((message) => {
                    const role: UIMessage['role'] = message.role === 'user' ? 'user' : 'assistant'
                    let content = role === 'assistant'
                        ? stripAiToolArtifacts(message.content)
                        : message.content

                    const shouldHideMessage =
                        (message.role === 'user' && isInternalToolResultPayload(message.content)) ||
                        (role === 'assistant' && !content.trim() && (containsAiToolArtifacts(message.content) || containsHallucinatedToolCalls(message.content)))

                    if (shouldHideMessage) {
                        return null
                    }

                    // Parse generated media and image references from stored messages
                    const parsedReferences = parseStoredReferenceMetadata(content)
                    let mentions = parsedReferences.mentions.length > 0
                        ? parsedReferences.mentions
                        : undefined
                    content = parsedReferences.text

                    let videoTask: GeneratedVideoTask | undefined
                    const parsedVideoTask = parseVideoTaskRefFromContent(content)
                    if (parsedVideoTask.videoTask) {
                        videoTask = parsedVideoTask.videoTask
                        content = parsedVideoTask.text
                    }

                    let media: GeneratedMedia[] | undefined
                    const parsedMedia = parseGeneratedMediaRefsFromContent(content)
                    if (parsedMedia.media.length > 0) {
                        media = parsedMedia.media
                        content = parsedMedia.text
                        videoTask = undefined
                    }

                    let images: ImageAttachment[] | undefined
                    const parsed = parseImageRefsFromContent(content)
                    if (parsed.images.length > 0) {
                        images = parsed.images
                        if (parsed.text.trim()) {
                            content = parsed.text
                        } else {
                            content = role === 'user' ? copy.imagePlaceholder : ''
                        }
                    }

                    const shouldResumePendingVideoTask = role === 'assistant'
                        && !!videoTask?.taskId
                        && !isTerminalVideoTaskStatus(videoTask.status)

                    if (shouldResumePendingVideoTask) {
                        content = ''
                    }

                    return {
                        id: message.id,
                        storedMessageId: message.id,
                        role,
                        content,
                        timestamp: copy.savedTime,
                        model: message.model ?? null,
                        images,
                        media,
                        videoTask,
                        mentions,
                        isStreaming: shouldResumePendingVideoTask,
                        requestKind: role === 'user' && images?.length ? 'image_attachment' : undefined,
                        usage: role === 'assistant'
                            ? buildStoredAssistantUsage(message.token_count, message.model ?? null)
                            : undefined,
                        memory: role === 'assistant'
                            ? buildMemoryMetadataFromStoredSources(message.sources)
                            : undefined,
                        citations: message.citations ?? undefined
                    } as UIMessage
                })
                .filter((message): message is UIMessage => message !== null)
            setMessages(uiMessages)
            // Scroll to bottom after loading
            setTimeout(() => scrollToBottom(false), 50)
        } catch (error) {
            if (requestToken !== conversationLoadTokenRef.current) return
            if (isConversationNotFoundError(error)) {
                conversationLoadTokenRef.current += 1
                invalidateConversationSelection(convId, { clearMessages: true })
                return
            }
            console.error('Failed to load messages:', error)
        }
    }

    // Auto-resize textarea
    useEffect(() => {
        const textarea = textareaRef.current
        if (textarea) {
            textarea.style.height = 'auto'
            const newHeight = Math.min(textarea.scrollHeight, 200)
            textarea.style.height = `${newHeight}px`
        }
    }, [inputValue])

    // Close menus when clicking outside
    useEffect(() => {
        const handleClick = (event: MouseEvent) => {
            const target = event.target
            if (
                target instanceof Element
                && target.closest('.dropdown-container, .media-studio-control')
            ) {
                return
            }
            closeAllMenus()
        }
        document.addEventListener('click', handleClick)
        return () => document.removeEventListener('click', handleClick)
    }, [closeAllMenus])

    useEffect(() => {
        return () => {
            aiActionDiffRequestIdRef.current += 1
            if (aiActionConfirmResolverRef.current) {
                aiActionConfirmResolverRef.current(false)
                aiActionConfirmResolverRef.current = null
            }
        }
    }, [])

    // Handle copy message
    const handleCopyMessage = useCallback(async (messageId: string, content: string) => {
        await copyToClipboard(content, copy.messageCopied)
        setCopiedMessageId(messageId)
        setTimeout(() => setCopiedMessageId(null), 2000)
    }, [copy.messageCopied])

    const emitConversationUpdated = useCallback((conversationId: string, workspace: string | null) => {
        window.dispatchEvent(new CustomEvent('pigtex:conversation-updated', {
            detail: {
                conversationId,
                workspaceId: workspace
            }
        }))
    }, [])

    const prepareAiActionDiffPreviews = useCallback(async (actions: ParsedAiFileAction[]) => {
        const requestId = aiActionDiffRequestIdRef.current + 1
        aiActionDiffRequestIdRef.current = requestId

        const diffTargets = actions
            .map((action, index) => ({
                action,
                key: getAiActionKey(action, index)
            }))
            .filter(({ action }) => action.type === 'write_file')

        if (!localRootPath || !window.electronAPI?.readFile || diffTargets.length === 0) {
            if (aiActionDiffRequestIdRef.current === requestId) {
                setAiActionDiffPreviews({})
                setIsPreparingActionDiffs(false)
            }
            return
        }

        const loadingState: Record<string, AiActionDiffPreview> = {}
        for (const target of diffTargets) {
            loadingState[target.key] = {
                status: 'loading',
                absolutePath: joinAbsolutePathForPreview(localRootPath, target.action.path)
            }
        }
        setAiActionDiffPreviews(loadingState)
        setIsPreparingActionDiffs(true)

        await Promise.all(diffTargets.map(async ({ action, key }) => {
            const absolutePath = joinAbsolutePathForPreview(localRootPath, action.path)
            try {
                let currentContent = ''
                try {
                    const currentFile = await window.electronAPI!.readFile(absolutePath)
                    currentContent = currentFile.content
                } catch (error) {
                    if (!isLikelyMissingFileError(error)) {
                        throw error
                    }
                }

                const diffPreview = buildUnifiedDiffPreview(currentContent, action.content ?? '', copy)
                if (aiActionDiffRequestIdRef.current !== requestId) return

                setAiActionDiffPreviews(prev => ({
                    ...prev,
                    [key]: {
                        status: 'ready',
                        absolutePath,
                        diffText: diffPreview.text
                    }
                }))
            } catch (error) {
                if (aiActionDiffRequestIdRef.current !== requestId) return
                const message = error instanceof Error ? error.message : copy.failedToolResponse
                setAiActionDiffPreviews(prev => ({
                    ...prev,
                    [key]: {
                        status: 'error',
                        absolutePath,
                        message
                    }
                }))
            }
        }))

        if (aiActionDiffRequestIdRef.current === requestId) {
            setIsPreparingActionDiffs(false)
        }
    }, [copy.failedToolResponse, localRootPath])

    const closeAiActionConfirmDialog = useCallback((approved: boolean) => {
        aiActionDiffRequestIdRef.current += 1
        setAiActionDiffPreviews({})
        setExpandedActionDiffs({})
        setIsPreparingActionDiffs(false)
        setAiActionConfirmDialog({ isOpen: false, actions: [] })
        const resolve = aiActionConfirmResolverRef.current
        aiActionConfirmResolverRef.current = null
        resolve?.(approved)
    }, [])

    const requestAiActionApproval = useCallback((actions: ParsedAiFileAction[]) => {
        return new Promise<boolean>((resolve) => {
            setExpandedActionDiffs({})
            setAiActionDiffPreviews({})
            setIsPreparingActionDiffs(false)
            aiActionConfirmResolverRef.current = resolve
            setAiActionConfirmDialog({
                isOpen: true,
                actions
            })
            void prepareAiActionDiffPreviews(actions)
        })
    }, [prepareAiActionDiffPreviews])

    // Escape key handler for AI action confirm dialog
    useEffect(() => {
        if (!aiActionConfirmDialog.isOpen) return

        const handleEscape = (event: KeyboardEvent) => {
            if (event.key === 'Escape') {
                event.preventDefault()
                event.stopPropagation()
                closeAiActionConfirmDialog(false)
            }
        }

        window.addEventListener('keydown', handleEscape, true)
        return () => window.removeEventListener('keydown', handleEscape, true)
    }, [aiActionConfirmDialog.isOpen, closeAiActionConfirmDialog])

    // Handle stop generation
    const handleStopGeneration = useCallback(() => {
        if (abortControllerRef.current) {
            abortControllerRef.current.abort()
            abortControllerRef.current = null
        }
        abortAllVideoPolling()
        sendInFlightRef.current = false
        setIsTyping(false)
        setMessages(prev => prev.map(message => {
            const hasPendingVideoTask = Boolean(
                message.videoTask?.taskId
                && !isTerminalVideoTaskStatus(message.videoTask.status)
            )
            if (!message.isStreaming && !hasPendingVideoTask) {
                return message
            }
            return {
                ...message,
                isStreaming: false,
                videoTask: hasPendingVideoTask ? undefined : message.videoTask
            }
        }))
    }, [])

    const handleMentionSelect = useCallback((item: MentionItem) => {
        setCurrentMentions(prev => {
            const nextKey = item.referenceId || item.relativePath
            const exists = prev.some(existing =>
                existing.type === item.type
                && (existing.referenceId || existing.relativePath) === nextKey
            )
            return exists ? prev : [...prev, item]
        })

        const triggerIndex = mention.popup.triggerIndex
        let newValue = inputValue
        let cursorPos = inputValue.length

        if (triggerIndex >= 0) {
            const beforeAt = inputValue.slice(0, triggerIndex)
            const afterAt = inputValue.slice(triggerIndex)
            const queryEndMatch = afterAt.match(/^@[^\s]*/)
            const queryLength = queryEndMatch ? queryEndMatch[0].length : 1
            newValue = beforeAt + inputValue.slice(triggerIndex + queryLength)
            cursorPos = beforeAt.length
        }

        setInputValue(newValue.replace(/[ \t]{2,}/g, ' '))
        mention.closePopup()

        setTimeout(() => {
            const textarea = textareaRef.current
            if (!textarea) return
            textarea.focus()
            const safeCursorPos = Math.min(cursorPos, textarea.value.length)
            textarea.selectionStart = safeCursorPos
            textarea.selectionEnd = safeCursorPos
        }, 0)
    }, [inputValue, mention])

    const buildConversationMentionContext = useCallback(async (mentions: MentionItem[]) => {
        const referencedConversations = mentions
            .filter(isConversationMention)
            .filter((mention, index, allMentions) =>
                mention.referenceId !== currentConversationId
                && allMentions.findIndex((candidate) => candidate.referenceId === mention.referenceId) === index
            )

        if (referencedConversations.length === 0) {
            return ''
        }

        const limitedMentions = referencedConversations.slice(0, MAX_CONVERSATION_MENTION_ITEMS)
        const fetchedConversations = await Promise.all(
            limitedMentions.map(async (mention) => {
                try {
                    const conversation = await getLocalConversation(mention.referenceId)
                    return { mention, conversation, error: null as string | null }
                } catch (error) {
                    return {
                        mention,
                        conversation: null,
                        error: error instanceof Error ? error.message : String(error)
                    }
                }
            })
        )

        const contextParts: string[] = [
            '## Referenced Conversations',
            isVietnamese
                ? 'Nguoi dung da chu dong keo cac conversation sau vao lam ngữ cảnh tham chiếu cho lượt chat này.'
                : 'The user explicitly pulled the following conversations in as reference context for this turn.',
            isVietnamese
                ? 'Chi dung de lay thong tin cu the khi lien quan. Khong duoc tu dong tron muc tieu, task, memory, hay learner state cua conversation kia vao thread hien tai neu user chua yeu cau.'
                : 'Use them only for concrete reference when relevant. Do not automatically merge goals, tasks, memory, or learner state from those conversations into the current thread unless the user asks.',
            ''
        ]

        let totalChars = 0
        let reachedBudget = false

        for (const item of fetchedConversations) {
            const lines: string[] = []
            const mentionLabel = item.mention.name || item.mention.relativePath
            if (!item.conversation) {
                lines.push(`### @conversation:${mentionLabel}`)
                lines.push(
                    isVietnamese
                        ? `Khong the nap conversation nay luc gui: ${item.error || 'Unknown error'}`
                        : `Could not load this conversation at send time: ${item.error || 'Unknown error'}`
                )
                lines.push('')
                contextParts.push(...lines)
                continue
            }

            const conversationTitle = (item.conversation.title || '').trim() || mentionLabel
            const conversationSummary = (item.conversation.summary || '').trim()
            const visibleMessages = item.conversation.messages
                .filter((message) => message.role !== 'system')
                .slice(-MAX_CONVERSATION_MENTION_MESSAGES)

            lines.push(`### @conversation:${conversationTitle}`)
            if (conversationSummary) {
                const remainingBudget = MAX_CONVERSATION_MENTION_TOTAL_CHARS - totalChars
                if (remainingBudget <= 0) {
                    reachedBudget = true
                    break
                }
                const summaryText = truncatePromptSnippet(conversationSummary, Math.min(700, remainingBudget))
                totalChars += summaryText.length
                lines.push(`${isVietnamese ? 'Tom tat' : 'Summary'}: ${summaryText}`)
            }
            lines.push(isVietnamese ? 'Cac luot trao doi tham chieu:' : 'Referenced turns:')

            let includedMessages = 0
            for (const message of visibleMessages) {
                const remainingBudget = MAX_CONVERSATION_MENTION_TOTAL_CHARS - totalChars
                if (remainingBudget <= 0) {
                    reachedBudget = true
                    break
                }

                const content = truncatePromptSnippet(
                    stripAiToolArtifacts(message.content || ''),
                    Math.min(MAX_CONVERSATION_MENTION_MESSAGE_CHARS, remainingBudget)
                )
                if (!content) continue

                const roleLabel = message.role === 'assistant'
                    ? (isVietnamese ? 'Assistant' : 'Assistant')
                    : (isVietnamese ? 'User' : 'User')
                lines.push(`${roleLabel}: ${content}`)
                totalChars += content.length
                includedMessages += 1
            }

            if (item.conversation.messages.filter((message) => message.role !== 'system').length > includedMessages) {
                const omittedCount = Math.max(0, item.conversation.messages.filter((message) => message.role !== 'system').length - includedMessages)
                if (omittedCount > 0) {
                    lines.push(
                        isVietnamese
                            ? `... (${omittedCount} tin nhan khac duoc bo qua de giu context gon hon)`
                            : `... (${omittedCount} more messages omitted to keep the context compact)`
                    )
                }
            }

            lines.push('')
            contextParts.push(...lines)

            if (reachedBudget) {
                break
            }
        }

        if (referencedConversations.length > limitedMentions.length) {
            contextParts.push(
                isVietnamese
                    ? `... (${referencedConversations.length - limitedMentions.length} conversation mention khac duoc bo qua de giu do tre on dinh)`
                    : `... (${referencedConversations.length - limitedMentions.length} additional conversation mentions skipped to keep latency stable)`,
                ''
            )
        } else if (reachedBudget) {
            contextParts.push(
                isVietnamese
                    ? '... (Da cat bot noi dung conversation tham chieu de giu context trong gioi han)'
                    : '... (Referenced conversation content was truncated to stay within the context budget)',
                ''
            )
        }

        return contextParts.join('\n')
    }, [currentConversationId, isVietnamese])

    // ===== Image handling =====
    const processImageFiles = useCallback(async (files: File[]) => {
        const validFiles = files.filter(f => {
            if (!ALLOWED_IMAGE_TYPES.includes(f.type)) {
                showError(copy.unsupportedFormat(f.name))
                return false
            }
            if (f.size > MAX_IMAGE_SIZE) {
                showError(copy.tooLargeImage(f.name))
                return false
            }
            return true
        })

        if (validFiles.length === 0) return

        const remaining = MAX_IMAGES - imageAttachments.length
        if (remaining <= 0) {
            showError(copy.maximumImages)
            return
        }
        const filesToProcess = validFiles.slice(0, remaining)
        if (validFiles.length > remaining) {
            showInfo(copy.onlyMoreImages(remaining))
        }

        try {
            const results = await Promise.all(filesToProcess.map(f => fileToBase64(f)))
            setImageAttachments(prev => [...prev, ...results])
        } catch {
            showError(copy.failedProcessImage)
        }
    }, [copy, imageAttachments.length])

    const processDocumentFiles = useCallback(async (files: File[]) => {
        const validFiles = files.filter(f => {
            if (!isSupportedDocumentFile(f)) {
                showError(copy.unsupportedFileType(f.name))
                return false
            }
            if (f.size > MAX_FILE_SIZE) {
                showError(copy.tooLargeFile(f.name))
                return false
            }
            return true
        })

        if (validFiles.length === 0) return

        const remaining = MAX_FILES - fileAttachments.length
        if (remaining <= 0) {
            showError(copy.maximumFiles)
            return
        }

        const filesToProcess = validFiles.slice(0, remaining)
        if (validFiles.length > remaining) {
            showInfo(copy.onlyMoreFiles(remaining))
        }

        try {
            const uploaded = await uploadFiles(filesToProcess)
            setFileAttachments(prev => [...prev, ...uploaded])
            showSuccess(copy.attachedFiles(uploaded.length))
        } catch (error) {
            showError(error instanceof Error ? error.message : copy.failedProcessFiles)
        }
    }, [copy, fileAttachments.length])

    const removeImageAttachment = useCallback((id: string) => {
        setImageAttachments(prev => prev.filter(img => img.id !== id))
    }, [])

    const removeFileAttachment = useCallback((id: string) => {
        setFileAttachments(prev => prev.filter(file => file.id !== id))
    }, [])

    const handleImageFileSelect = useCallback(() => {
        imageInputRef.current?.click()
    }, [])

    const handleDocumentFileSelect = useCallback(() => {
        fileInputRef.current?.click()
    }, [])

    const handlePasteCodeSnippet = useCallback(async () => {
        const remaining = MAX_FILES - fileAttachments.length
        if (remaining <= 0) {
            showError(copy.maximumFiles)
            return
        }

        let code = ''
        try {
            if (navigator.clipboard?.readText) {
                code = await navigator.clipboard.readText()
            }
        } catch {
            // Clipboard read permission can be blocked in some environments.
        }

        if (!code.trim()) {
            const manualInput = window.prompt(copy.pasteCodeSnippet)
            if (!manualInput?.trim()) {
                showInfo(copy.noCodeSnippet)
                return
            }
            code = manualInput
        }

        const attachment = buildPastedCodeAttachment(code)
        setFileAttachments(prev => [...prev, attachment])
        if (attachment.truncated) {
            showInfo(copy.codeSnippetTruncated)
            return
        }
        showSuccess(copy.codeSnippetAttached)
    }, [copy, fileAttachments.length])

    const handleImageInputChange = useCallback((e: React.ChangeEvent<HTMLInputElement>) => {
        const files = Array.from(e.target.files || [])
        if (files.length > 0) {
            void processImageFiles(files)
        }
        // Reset input so same file can be selected again
        e.target.value = ''
    }, [processImageFiles])

    const handleDocumentInputChange = useCallback((e: React.ChangeEvent<HTMLInputElement>) => {
        const files = Array.from(e.target.files || [])
        if (files.length > 0) {
            void processDocumentFiles(files)
        }
        e.target.value = ''
    }, [processDocumentFiles])

    // Drag & drop handlers
    const handleDragEnter = useCallback((e: React.DragEvent) => {
        e.preventDefault()
        e.stopPropagation()
        dragCounterRef.current++
        if (e.dataTransfer.types.includes('Files')) {
            setIsDraggingImage(true)
        }
    }, [])

    const handleDragLeave = useCallback((e: React.DragEvent) => {
        e.preventDefault()
        e.stopPropagation()
        dragCounterRef.current--
        if (dragCounterRef.current <= 0) {
            dragCounterRef.current = 0
            setIsDraggingImage(false)
        }
    }, [])

    const handleDragOver = useCallback((e: React.DragEvent) => {
        e.preventDefault()
        e.stopPropagation()
    }, [])

    const handleDrop = useCallback((e: React.DragEvent) => {
        e.preventDefault()
        e.stopPropagation()
        dragCounterRef.current = 0
        setIsDraggingImage(false)

        const files = Array.from(e.dataTransfer.files).filter(f => f.type.startsWith('image/'))
        if (files.length > 0) {
            void processImageFiles(files)
        }
    }, [processImageFiles])

    // Clipboard paste handler for images
    const handlePaste = useCallback((e: React.ClipboardEvent) => {
        const items = Array.from(e.clipboardData.items)
        const imageFiles = items
            .filter(item => item.type.startsWith('image/'))
            .map(item => item.getAsFile())
            .filter((f): f is File => f !== null)

        if (imageFiles.length > 0) {
            e.preventDefault()
            void processImageFiles(imageFiles)
        }
    }, [processImageFiles])

    const trackTransientObjectUrl = (url: string) => {
        transientObjectUrlsRef.current.push(url)
    }

    const persistGeneratedTurn = async (payload: {
        titleSeed: string
        modelId: string
        userContent: string
        userImages?: ImageAttachment[]
        assistantContent?: string
        assistantImages?: ImageAttachment[]
        assistantMedia?: GeneratedMedia[]
        assistantVideoTask?: GeneratedVideoTask
        historyErrorMessage: string
    }): Promise<{ conversationId: string | null; assistantMessageId: string | null }> => {
        try {
            const requestWorkspaceId = currentConversationIdRef.current
                ? (conversationWorkspaceIdRef.current || null)
                : (workspaceId || null)
            let activeConversationId = currentConversationIdRef.current || null
            let activeWorkspaceId = requestWorkspaceId

            if (!activeConversationId) {
                const title = payload.titleSeed.length > 50
                    ? `${payload.titleSeed.slice(0, 50)}...`
                    : payload.titleSeed
                const createdConversation = await createConversation(requestWorkspaceId, title)
                activeConversationId = createdConversation.id
                activeWorkspaceId = createdConversation.workspace_id ?? requestWorkspaceId

                justCreatedConversation.current = true
                updateConversationSelection(activeConversationId, activeWorkspaceId || null)
                onConversationCreated?.(activeConversationId)
            }

            if (activeConversationId) {
                await addConversationMessage(
                    activeConversationId,
                    'user',
                    buildStoredMessageContent(payload.userContent, payload.userImages),
                    payload.modelId
                )
                const assistantMessage = await addConversationMessage(
                    activeConversationId,
                    'assistant',
                    buildStoredMessageContent(
                        payload.assistantContent || '',
                        payload.assistantImages,
                        payload.assistantMedia,
                        payload.assistantVideoTask
                    ),
                    payload.modelId
                )
                emitConversationUpdated(activeConversationId, activeWorkspaceId || null)
                return {
                    conversationId: activeConversationId,
                    assistantMessageId: assistantMessage.id
                }
            }
        } catch (persistError) {
            console.error('Failed to persist generated conversation messages:', persistError)
            showError(payload.historyErrorMessage)
        }

        return {
            conversationId: currentConversationIdRef.current || null,
            assistantMessageId: null
        }
    }

    const buildVoiceMediaFromResult = async (
        result: Blob | unknown,
        options: VoiceStudioState
    ): Promise<GeneratedMedia[]> => {
        const normalizedModel = options.model.trim()
        const normalizedVoice = options.voice.trim()
        const fallbackMimeType = (() => {
            switch (options.responseFormat) {
                case 'wav': return 'audio/wav'
                case 'ogg': return 'audio/ogg'
                case 'aac': return 'audio/aac'
                case 'flac': return 'audio/flac'
                case 'mp3':
                default:
                    return 'audio/mpeg'
            }
        })()
        const buildAudioItem = async (
            src: string,
            mimeType?: string,
            filename?: string
        ): Promise<GeneratedMedia> => {
            const extension = inferExtensionFromMimeType(mimeType || fallbackMimeType, options.responseFormat)
            const speed = Number.parseFloat(options.speed)
            return {
                id: `audio_${Date.now()}_${Math.random().toString(36).slice(2, 8)}`,
                kind: 'audio',
                src,
                filename: filename || `voice_${Date.now()}.${extension}`,
                mimeType: mimeType || fallbackMimeType,
                model: normalizedModel || undefined,
                voice: normalizedVoice || undefined,
                format: options.responseFormat,
                speed: Number.isFinite(speed) ? speed : undefined
            }
        }

        if (result instanceof Blob) {
            return [await buildAudioItem(await blobToDataUrl(result), result.type || fallbackMimeType)]
        }

        const record = (result && typeof result === 'object') ? (result as Record<string, unknown>) : null
        const rows = Array.isArray(record?.data) ? record.data : record ? [record] : []
        const mediaItems: GeneratedMedia[] = []

        for (const row of rows) {
            if (!row || typeof row !== 'object') continue
            const item = row as Record<string, unknown>
            const mimeType = typeof item.mime_type === 'string' && item.mime_type.trim()
                ? item.mime_type.trim()
                : fallbackMimeType
            const b64 = typeof item.b64_json === 'string' && item.b64_json.trim()
                ? item.b64_json.trim()
                : ''
            const rawUrl = typeof item.audio_url === 'string' && item.audio_url.trim()
                ? item.audio_url.trim()
                : typeof item.url === 'string' && item.url.trim()
                    ? item.url.trim()
                    : typeof item.download_url === 'string' && item.download_url.trim()
                        ? item.download_url.trim()
                        : ''
            const src = b64
                ? `data:${mimeType};base64,${b64}`
                : rawUrl
                    ? resolveStoredAssetUrl(rawUrl)
                    : ''
            if (!src) continue
            mediaItems.push(await buildAudioItem(src, mimeType))
        }

        return mediaItems
    }

    const buildVideoMediaFromResult = async (
        result: Blob | unknown,
        options: VideoStudioState
    ): Promise<{ items: GeneratedMedia[]; statusText?: string; task?: GeneratedVideoTask }> => {
        const buildVideoItem = (params: {
            src: string
            mimeType?: string
            thumbnailUrl?: string
            revisedPrompt?: string
        }): GeneratedMedia => ({
            id: `video_${Date.now()}_${Math.random().toString(36).slice(2, 8)}`,
            kind: 'video',
            src: params.src,
            filename: `video_${Date.now()}.${inferExtensionFromMimeType(params.mimeType, 'mp4')}`,
            mimeType: params.mimeType || 'video/mp4',
            model: options.model.trim() || undefined,
            aspectRatio: options.aspectRatio,
            duration: options.duration,
            quality: options.quality,
            style: options.style.trim() || undefined,
            thumbnailUrl: params.thumbnailUrl,
            revisedPrompt: params.revisedPrompt
        })

        if (result instanceof Blob) {
            const objectUrl = URL.createObjectURL(result)
            trackTransientObjectUrl(objectUrl)
            return {
                items: [
                    buildVideoItem({
                        src: objectUrl,
                        mimeType: result.type || 'video/mp4'
                    })
                ]
            }
        }

        const record = (result && typeof result === 'object') ? (result as Record<string, unknown>) : null
        const rows = Array.isArray(record?.data)
            ? record.data
            : record && typeof record === 'object'
                ? [record]
                : []
        const items: GeneratedMedia[] = []

        for (const row of rows) {
            if (!row || typeof row !== 'object') continue
            const item = row as Record<string, unknown>
            const mimeType = typeof item.mime_type === 'string' && item.mime_type.trim()
                ? item.mime_type.trim()
                : 'video/mp4'
            const b64 = typeof item.b64_json === 'string' && item.b64_json.trim()
                ? item.b64_json.trim()
                : ''
            const rawUrl = typeof item.video_url === 'string' && item.video_url.trim()
                ? item.video_url.trim()
                : typeof item.url === 'string' && item.url.trim()
                    ? item.url.trim()
                    : typeof item.download_url === 'string' && item.download_url.trim()
                        ? item.download_url.trim()
                        : ''
            const src = b64
                ? `data:${mimeType};base64,${b64}`
                : rawUrl
                    ? resolveStoredAssetUrl(rawUrl)
                    : ''
            if (!src) continue
            const thumbnailUrl = typeof item.thumbnail_url === 'string' && item.thumbnail_url.trim()
                ? resolveStoredAssetUrl(item.thumbnail_url.trim())
                : undefined
            const revisedPrompt = typeof item.revised_prompt === 'string' && item.revised_prompt.trim()
                ? item.revised_prompt.trim()
                : undefined
            items.push(buildVideoItem({ src, mimeType, thumbnailUrl, revisedPrompt }))
        }

        const taskStatus = typeof record?.task_status === 'string' && record.task_status.trim()
            ? record.task_status.trim()
            : undefined
        const errorMessage = typeof record?.error_message === 'string' && record.error_message.trim()
            ? record.error_message.trim()
            : undefined
        const taskId = typeof record?.task_id === 'string' && record.task_id.trim()
            ? record.task_id.trim()
            : undefined
        const task = taskId
            ? {
                taskId,
                status: taskStatus,
                model: options.model.trim() || undefined,
                aspectRatio: options.aspectRatio,
                duration: options.duration,
                quality: options.quality,
                style: options.style.trim() || undefined
            } satisfies GeneratedVideoTask
            : undefined
        const statusText = taskStatus
            ? isTerminalVideoTaskStatus(taskStatus)
                ? copy.videoTaskTerminalText(taskStatus, errorMessage)
                : (isPendingVideoTaskStatus(taskStatus) && !taskId)
                    ? (isVietnamese
                        ? 'Video đang tạo nhưng upstream không trả về task ID để PigTex tiếp tục theo dõi.'
                        : 'The video is still processing, but the upstream provider did not return a task ID for PigTex to continue polling.')
                    : undefined
            : undefined

        return { items, statusText, task }
    }

    const waitForVideoPollingDelay = useCallback((ms: number, signal?: AbortSignal) => {
        return new Promise<void>((resolve, reject) => {
            if (signal?.aborted) {
                reject(new DOMException('Aborted', 'AbortError'))
                return
            }

            const timeoutId = window.setTimeout(() => {
                signal?.removeEventListener('abort', handleAbort)
                resolve()
            }, ms)

            const handleAbort = () => {
                window.clearTimeout(timeoutId)
                signal?.removeEventListener('abort', handleAbort)
                reject(new DOMException('Aborted', 'AbortError'))
            }

            signal?.addEventListener('abort', handleAbort, { once: true })
        })
    }, [])

    const buildVideoStudioStateFromTask = useCallback((task: GeneratedVideoTask): VideoStudioState => ({
        model: task.model?.trim() || '',
        aspectRatio: (VIDEO_ASPECT_RATIO_OPTIONS.includes((task.aspectRatio || '') as typeof VIDEO_ASPECT_RATIO_OPTIONS[number])
            ? task.aspectRatio
            : '16:9') as VideoStudioState['aspectRatio'],
        duration: (VIDEO_DURATION_OPTIONS.includes((task.duration || '') as typeof VIDEO_DURATION_OPTIONS[number])
            ? task.duration
            : '5') as VideoStudioState['duration'],
        quality: (VIDEO_QUALITY_OPTIONS.includes((task.quality || '') as typeof VIDEO_QUALITY_OPTIONS[number])
            ? task.quality
            : 'standard') as VideoStudioState['quality'],
        style: task.style || ''
    }), [])

    const applyVideoTaskMessageUpdate = useCallback((params: {
        messageId: string
        content: string
        media?: GeneratedMedia[]
        videoTask?: GeneratedVideoTask
        storedMessageId?: string | null
        isStreaming?: boolean
    }) => {
        setMessages(prev => prev.map(message =>
            message.id === params.messageId
                ? {
                    ...message,
                    content: params.content,
                    isStreaming: params.isStreaming ?? false,
                    media: params.media && params.media.length > 0 ? params.media : undefined,
                    videoTask: params.videoTask,
                    storedMessageId: params.storedMessageId ?? message.storedMessageId
                }
                : message
        ))
    }, [])

    const persistAssistantMessageUpdate = useCallback(async (params: {
        conversationId: string
        messageId: string
        modelId: string
        content: string
        media?: GeneratedMedia[]
        videoTask?: GeneratedVideoTask
    }) => {
        try {
            await updateConversationMessage(
                params.conversationId,
                params.messageId,
                buildStoredMessageContent(params.content, undefined, params.media, params.videoTask),
                params.modelId
            )
            emitConversationUpdated(params.conversationId, conversationWorkspaceId || null)
        } catch (error) {
            console.error('Failed to update persisted video message:', error)
            showError(copy.videoHistoryFailed)
        }
    }, [conversationWorkspaceId, copy.videoHistoryFailed])

    const startVideoTaskPolling = useCallback((params: {
        messageId: string
        task: GeneratedVideoTask
        storedMessageId?: string | null
        conversationId?: string | null
        modelId: string
    }) => {
        if (!params.task.taskId) {
            return
        }

        videoPollingContextRef.current.set(params.messageId, {
            storedMessageId: params.storedMessageId,
            conversationId: params.conversationId,
            modelId: params.modelId
        })

        const pollingKey = `${params.messageId}:${params.task.taskId}`
        if (videoPollingStartedRef.current.has(pollingKey)) {
            return
        }
        const existingController = videoPollingControllersRef.current.get(params.messageId)
        if (existingController) {
            return
        }

        videoPollingStartedRef.current.add(pollingKey)
        const controller = new AbortController()
        videoPollingControllersRef.current.set(params.messageId, controller)

        void (async () => {
            let latestTask = params.task
            let lastPendingPreviewSignature = ''
            const options = buildVideoStudioStateFromTask(params.task)
            const getLatestContext = () => videoPollingContextRef.current.get(params.messageId) || {
                storedMessageId: params.storedMessageId,
                conversationId: params.conversationId,
                modelId: params.modelId
            }

            try {
                while (!controller.signal.aborted) {
                    const result = await getVideoGenerationTask(params.task.taskId, controller.signal)
                    const normalized = await buildVideoMediaFromResult(result, options)
                    const currentStatus = normalized.task?.status || latestTask.status || ''
                    const nextTask = normalized.task
                        ? { ...latestTask, ...normalized.task }
                        : { ...latestTask, status: currentStatus || latestTask.status }
                    latestTask = nextTask

                    if (
                        normalized.items.length > 0
                        && nextTask.taskId
                        && isPendingVideoTaskStatus(currentStatus)
                    ) {
                        const latestContext = getLatestContext()
                        const previewSignature = `${currentStatus}|${normalized.items.map(item => item.src).join('|')}`
                        applyVideoTaskMessageUpdate({
                            messageId: params.messageId,
                            content: '',
                            media: normalized.items,
                            videoTask: nextTask,
                            storedMessageId: latestContext.storedMessageId,
                            isStreaming: true
                        })
                        if (
                            latestContext.conversationId
                            && latestContext.storedMessageId
                            && previewSignature !== lastPendingPreviewSignature
                        ) {
                            await persistAssistantMessageUpdate({
                                conversationId: latestContext.conversationId,
                                messageId: latestContext.storedMessageId,
                                modelId: latestContext.modelId,
                                content: '',
                                media: normalized.items,
                                videoTask: nextTask
                            })
                            lastPendingPreviewSignature = previewSignature
                        }
                        await waitForVideoPollingDelay(4000, controller.signal)
                        continue
                    }

                    if (normalized.items.length > 0) {
                        const latestContext = getLatestContext()
                        applyVideoTaskMessageUpdate({
                            messageId: params.messageId,
                            content: '',
                            media: normalized.items,
                            videoTask: undefined,
                            storedMessageId: latestContext.storedMessageId,
                            isStreaming: false
                        })
                        if (latestContext.conversationId && latestContext.storedMessageId) {
                            await persistAssistantMessageUpdate({
                                conversationId: latestContext.conversationId,
                                messageId: latestContext.storedMessageId,
                                modelId: latestContext.modelId,
                                content: '',
                                media: normalized.items
                            })
                        }
                        return
                    }

                    if (isTerminalVideoTaskStatus(currentStatus)) {
                        const terminalText = normalized.statusText || copy.videoTaskTerminalText(currentStatus)
                        const latestContext = getLatestContext()
                        applyVideoTaskMessageUpdate({
                            messageId: params.messageId,
                            content: terminalText,
                            videoTask: undefined,
                            storedMessageId: latestContext.storedMessageId,
                            isStreaming: false
                        })
                        if (latestContext.conversationId && latestContext.storedMessageId) {
                            await persistAssistantMessageUpdate({
                                conversationId: latestContext.conversationId,
                                messageId: latestContext.storedMessageId,
                                modelId: latestContext.modelId,
                                content: terminalText
                            })
                        }
                        return
                    }

                    const latestContext = getLatestContext()
                    applyVideoTaskMessageUpdate({
                        messageId: params.messageId,
                        content: '',
                        videoTask: nextTask,
                        storedMessageId: latestContext.storedMessageId,
                        isStreaming: true
                    })

                    await waitForVideoPollingDelay(4000, controller.signal)
                }
            } catch (error) {
                if (error instanceof DOMException && error.name === 'AbortError') {
                    return
                }
                console.error('Failed while polling video task:', error)
                const message = error instanceof Error && error.message.trim()
                    ? error.message.trim()
                    : copy.videoTaskTerminalText('ERROR')
                const latestContext = getLatestContext()
                applyVideoTaskMessageUpdate({
                    messageId: params.messageId,
                    content: message,
                    videoTask: undefined,
                    storedMessageId: latestContext.storedMessageId,
                    isStreaming: false
                })
            } finally {
                videoPollingControllersRef.current.delete(params.messageId)
                videoPollingContextRef.current.delete(params.messageId)
            }
        })()
    }, [
        applyVideoTaskMessageUpdate,
        buildVideoMediaFromResult,
        buildVideoStudioStateFromTask,
        copy.videoTaskTerminalText,
        persistAssistantMessageUpdate,
        waitForVideoPollingDelay
    ])

    useEffect(() => {
        if (!currentConversationId) {
            return
        }

        for (const message of messages) {
            const persistedMessageId = message.storedMessageId
                || (message.id.startsWith('m') ? null : message.id)
            if (
                !persistedMessageId
                || message.role !== 'assistant'
                || !message.videoTask?.taskId
                || isTerminalVideoTaskStatus(message.videoTask.status)
            ) {
                continue
            }

            startVideoTaskPolling({
                messageId: message.id,
                task: message.videoTask,
                storedMessageId: persistedMessageId,
                conversationId: currentConversationId,
                modelId: message.model || message.videoTask.model || ''
            })
        }
    }, [currentConversationId, messages, startVideoTaskPolling])

    const handleSend = async () => {
        if (isTyping || sendInFlightRef.current || messages.some(hasPendingAssistantWork)) return

        const imageToolModeId = selectedImageTool.id
        const isImageToolRequest = imageToolModeId === 'image'
        const isVoiceToolRequest = imageToolModeId === 'voice'
        const isVideoToolRequest = imageToolModeId === 'video'
        const isMediaGenerationRequest = isImageToolRequest || isVoiceToolRequest || isVideoToolRequest
        const promptText = inputValue.trim()
        const isImageEditMode = isImageToolRequest && imageAttachments.length > 0
        const isImageGenerateMode = isImageToolRequest && !isImageEditMode
        const selectedModeId = selectedMode.id as ConversationModeId
        const requestModeId: NonNullable<SmartChatRequest['mode']> = selectedModeId === 'fast' ? 'fast' : 'deep'
        const selectedLearningMode: NonNullable<SmartChatRequest['learning_mode']> = selectedModeId === 'learn' ? 'teacher' : 'off'
        const activeToolModelId = isVoiceToolRequest
            ? voiceStudio.model.trim()
            : isVideoToolRequest
                ? videoStudio.model.trim()
                : selectedModel.id
        const shouldForceWebSearch = !isMediaGenerationRequest && webSearchEnabled
        const turnComplexityScore = !isMediaGenerationRequest
            ? estimateTurnComplexity(
                promptText,
                currentMentions.length,
                imageAttachments.length,
                fileAttachments.length
            )
            : 0
        const agentStepBudget = isMediaGenerationRequest
            ? 1
            : resolveAiAgentStepBudget(selectedModeId, turnComplexityScore)
        const shouldHintDeepRead = !isMediaGenerationRequest && selectedModeId === 'deep' && turnComplexityScore >= 2
        const shouldHintDeepVerify = !isMediaGenerationRequest && selectedModeId === 'deep' && turnComplexityScore >= 3

        if (!isMediaGenerationRequest && !selectedModel.id.trim()) {
            showError(copy.chatModelRequired)
            return
        }

        if (
            !isMediaGenerationRequest
            && modelSupportsCapability(selectedModel, 'image_generation', resolvedEndpointProvider)
            && !modelSupportsCapability(selectedModel, 'chat', resolvedEndpointProvider)
        ) {
            showError(copy.imageOnlyModel)
            return
        }

        if (isImageToolRequest) {
            if (!transportSupportsCapability(resolvedEndpointProvider, 'image_generation')) {
                showError(copy.noImageModelAvailable)
                return
            }
            if (!modelSupportsCapability(selectedModel, 'image_generation', resolvedEndpointProvider)) {
                if (imageModels.length === 0) {
                    showError(copy.noImageModelAvailable)
                } else {
                    showError(copy.imageModelRequired)
                }
                return
            }
            if (!promptText) {
                showError(copy.promptRequiredImage)
                return
            }
        } else if (isVoiceToolRequest) {
            if (!transportSupportsCapability(resolvedEndpointProvider, 'audio_speech')) {
                showError(copy.voiceTransportUnavailable)
                return
            }
            if (!activeToolModelId) {
                showError(copy.voiceModelRequired)
                return
            }
            const activeVoiceModel = models.find(model => model.id === activeToolModelId)
            if (activeVoiceModel && !modelSupportsCapability(activeVoiceModel, 'audio_speech', resolvedEndpointProvider)) {
                showError(copy.voiceModelRequired)
                return
            }
            if (!promptText) {
                showError(copy.promptRequiredVoice)
                return
            }
        } else if (isVideoToolRequest) {
            if (!transportSupportsCapability(resolvedEndpointProvider, 'video_generation')) {
                showError(copy.videoTransportUnavailable)
                return
            }
            if (!activeToolModelId) {
                showError(copy.videoModelRequired)
                return
            }
            const activeVideoModel = models.find(model => model.id === activeToolModelId)
            if (activeVideoModel && !modelSupportsCapability(activeVideoModel, 'video_generation', resolvedEndpointProvider)) {
                showError(copy.videoModelRequired)
                return
            }
            if (!promptText) {
                showError(copy.promptRequiredVideo)
                return
            }
        } else if (!promptText && currentMentions.length === 0 && imageAttachments.length === 0 && fileAttachments.length === 0) {
            return
        }

        let resolvedConversationId = currentConversationIdRef.current || null
        let resolvedConversationWorkspaceId = resolvedConversationId
            ? (conversationWorkspaceIdRef.current || null)
            : (workspaceId || null)

        if (resolvedConversationId && messages.length === 0) {
            try {
                const existingConversation = await getLocalConversation(resolvedConversationId)
                resolvedConversationWorkspaceId = existingConversation.workspace_id ?? resolvedConversationWorkspaceId
                conversationWorkspaceIdRef.current = resolvedConversationWorkspaceId
                setConversationWorkspaceId(resolvedConversationWorkspaceId)
            } catch (error) {
                if (!isConversationNotFoundError(error)) {
                    throw error
                }
                conversationLoadTokenRef.current += 1
                invalidateConversationSelection(resolvedConversationId, { clearMessages: true })
                resolvedConversationId = null
                resolvedConversationWorkspaceId = workspaceId || null
            }
        }

        const mentions = [...currentMentions]
        const filesystemMentions = mentions.filter(isFilesystemMention)
        const conversationMentions = mentions.filter(isConversationMention)
        const images = [...imageAttachments]
        const files = [...fileAttachments]
        const fallbackAttachmentText = files.length > 0
            ? `${isVietnamese ? 'Tệp đính kèm' : 'Attached files'}: ${files.map(file => file.filename).join(', ')}`
            : ''
        const visibleUserText = promptText
        const storedUserText = buildStoredMessageContent(
            promptText || fallbackAttachmentText,
            undefined,
            undefined,
            undefined,
            mentions
        )
        const requestKind: UIMessage['requestKind'] = isImageGenerateMode
            ? 'image_generate'
            : isImageEditMode
                ? 'image_edit'
                : isVoiceToolRequest
                    ? 'voice'
                    : isVideoToolRequest
                        ? 'video'
                        : images.length > 0
                            ? 'image_attachment'
                            : undefined

        const userMessage: UIMessage = {
            id: `m${Date.now()}`,
            role: 'user',
            content: visibleUserText,
            timestamp: copy.justNow,
            model: activeToolModelId || undefined,
            images: images.length > 0 ? images : undefined,
            files: files.length > 0 ? files : undefined,
            mentions: mentions.length > 0 ? mentions : undefined,
            requestKind
        }

        sendInFlightRef.current = true
        setMessages(prev => [...prev, userMessage])
        const messageText = storedUserText
        const useWorkspaceReviewControllerForTurn = shouldUseWorkspaceReviewController({
            promptText,
            mentions: filesystemMentions,
            localRootPath: localRootPath ?? null,
            aiFileModeEnabled
        })
        const interactiveAiFileModeEnabled = aiFileModeEnabled && !useWorkspaceReviewControllerForTurn
        const shouldUseWebSearch = shouldForceWebSearch && !useWorkspaceReviewControllerForTurn
        setInputValue('')
        setCurrentMentions([])
        setImageAttachments([])
        setFileAttachments([])
        mention.closePopup()
        setIsTyping(true)

        const assistantPendingText = isImageToolRequest
            ? copy.imagePending
            : isVoiceToolRequest
                ? copy.voicePending
                : isVideoToolRequest
                    ? ''
                    : modePendingText[selectedModeId]

        // Create assistant message placeholder
        const assistantId = `m${Date.now() + 1}`
        setMessages(prev => [...prev, {
            id: assistantId,
            role: 'assistant',
            content: assistantPendingText,
            timestamp: copy.justNow,
            model: activeToolModelId || selectedModel.id,
            isStreaming: true,
            webSearch: shouldUseWebSearch
                ? { enabled: true, status: 'running' }
                : undefined
        }])
        const aiFileExecutionContext =
            interactiveAiFileModeEnabled && localRootPath
                ? createAiFileExecutionContext()
                : undefined
        let finalFallbackAgentStatusText = ''

        try {
            abortControllerRef.current = new AbortController()

            if (isImageToolRequest) {
                if (isImageEditMode && images.length > 1) {
                    showInfo(copy.editUsesFirstImage)
                }

                const imageResult = isImageGenerateMode
                    ? await generateImages(
                        promptText,
                        { model: selectedModel.id },
                        abortControllerRef.current?.signal
                    )
                    : await editImage(
                        promptText,
                        images[0],
                        { model: selectedModel.id },
                        abortControllerRef.current?.signal
                    )

                if (!imageResult.images.length) {
                    throw new Error(isVietnamese ? 'Model không trả về ảnh nào' : 'No images returned from model')
                }

                setMessages(prev => prev.map(m =>
                    m.id === assistantId
                        ? {
                            ...m,
                            // Keep image responses visual-only to avoid noisy metadata text.
                            content: '',
                            isStreaming: false,
                            images: imageResult.images
                        }
                        : m
                ))

                await persistGeneratedTurn({
                    titleSeed: promptText.trim() || copy.imageRequest,
                    modelId: selectedModel.id,
                    userContent: messageText,
                    userImages: images,
                    assistantImages: imageResult.images,
                    historyErrorMessage: copy.imageHistoryFailed
                })
                return
            }

            if (isVoiceToolRequest) {
                const voiceResult = await synthesizeSpeech({
                    model: activeToolModelId,
                    input: promptText,
                    voice: voiceStudio.voice.trim() || undefined,
                    response_format: voiceStudio.responseFormat,
                    speed: Number.isFinite(Number.parseFloat(voiceStudio.speed))
                        ? Number.parseFloat(voiceStudio.speed)
                        : undefined
                })
                const mediaItems = await buildVoiceMediaFromResult(voiceResult, voiceStudio)
                if (!mediaItems.length) {
                    throw new Error(isVietnamese ? 'Model không trả về audio nào' : 'No audio returned from model')
                }

                setMessages(prev => prev.map(m =>
                    m.id === assistantId
                        ? {
                            ...m,
                            content: '',
                            isStreaming: false,
                            media: mediaItems
                        }
                        : m
                ))

                await persistGeneratedTurn({
                    titleSeed: promptText.trim() || copy.voiceRequest,
                    modelId: activeToolModelId,
                    userContent: messageText,
                    assistantMedia: mediaItems,
                    historyErrorMessage: copy.voiceHistoryFailed
                })
                return
            }

            if (isVideoToolRequest) {
                const videoResult = await generateVideo(
                    promptText,
                    {
                        model: activeToolModelId,
                        aspect_ratio: videoStudio.aspectRatio,
                        duration: videoStudio.duration,
                        quality: videoStudio.quality,
                        style: videoStudio.style.trim() || undefined
                    },
                    abortControllerRef.current?.signal
                )
                const normalizedVideo = await buildVideoMediaFromResult(videoResult, videoStudio)
                if (!normalizedVideo.items.length && !normalizedVideo.statusText && !normalizedVideo.task) {
                    throw new Error(isVietnamese ? 'Model không trả về video nào' : 'No video returned from model')
                }

                const shouldKeepWaitingForVideo = !!normalizedVideo.task
                    && !isTerminalVideoTaskStatus(normalizedVideo.task.status)

                setMessages(prev => prev.map(m =>
                    m.id === assistantId
                        ? {
                            ...m,
                            content: normalizedVideo.statusText || '',
                            isStreaming: shouldKeepWaitingForVideo,
                            media: normalizedVideo.items.length > 0 ? normalizedVideo.items : undefined,
                            videoTask: normalizedVideo.task
                        }
                        : m
                ))

                const persistedVideoTurn = await persistGeneratedTurn({
                    titleSeed: promptText.trim() || copy.videoRequest,
                    modelId: activeToolModelId,
                    userContent: messageText,
                    assistantContent: normalizedVideo.statusText,
                    assistantMedia: normalizedVideo.items,
                    assistantVideoTask: shouldKeepWaitingForVideo ? normalizedVideo.task : undefined,
                    historyErrorMessage: copy.videoHistoryFailed
                })
                if (persistedVideoTurn.assistantMessageId) {
                    setMessages(prev => prev.map(message =>
                        message.id === assistantId
                            ? {
                                ...message,
                                storedMessageId: persistedVideoTurn.assistantMessageId
                            }
                            : message
                    ))
                }
                if (
                    normalizedVideo.task
                    && !isTerminalVideoTaskStatus(normalizedVideo.task.status)
                ) {
                    startVideoTaskPolling({
                        messageId: assistantId,
                        task: normalizedVideo.task,
                        storedMessageId: persistedVideoTurn.assistantMessageId || null,
                        conversationId: persistedVideoTurn.conversationId || null,
                        modelId: activeToolModelId
                    })
                }
                return
            }

            const customInstruction = settings.customInstruction.trim()
            const modeRuntimeInstruction = buildModeRuntimeInstruction(
                selectedModeId,
                shouldUseWebSearch ? 'force_on' : 'auto'
            )
            const requiresAiActionApproval = !settings.autoApproveAiFileActions
            const aiFileInstruction = interactiveAiFileModeEnabled && localRootPath
                ? buildFileAgentPlannerInstruction(localRootPath, {
                    requireUserApproval: requiresAiActionApproval
                })
                : ''

            // ===== Build @mention context =====
            let mentionContext = ''
            if (!useWorkspaceReviewControllerForTurn && filesystemMentions.length > 0 && localRootPath && window.electronAPI?.readFile) {
                const contextParts: string[] = [
                    '## Referenced Files/Folders (user explicitly mentioned these with @)',
                    'The user has explicitly referenced the following items. Focus your response on these when relevant.',
                    ''
                ]
                const limitedMentions = filesystemMentions.slice(0, MAX_MENTION_CONTEXT_ITEMS)
                const sep = localRootPath.includes('/') ? '/' : '\\\\'

                const mentionBlocks = await Promise.all(
                    limitedMentions.map(async (m) => {
                        if (m.type === 'file') {
                            try {
                                const absPath = `${localRootPath}${sep}${m.relativePath.replace(/\//g, sep)}`
                                const fileData = await window.electronAPI!.readFile(absPath)
                                const lines = [
                                    `### 📄 @file:${m.relativePath}`,
                                    '```',
                                    fileData.content.slice(0, MAX_MENTION_FILE_CHARS),
                                ]
                                if (fileData.content.length > MAX_MENTION_FILE_CHARS) {
                                    lines.push('... (truncated)')
                                }
                                lines.push('```', '')
                                return lines
                            } catch {
                                return [`### 📄 @file:${m.relativePath} (could not read)`, '']
                            }
                        }

                        try {
                            const absPath = `${localRootPath}${sep}${m.relativePath.replace(/\//g, sep)}`
                            const entries = await window.electronAPI!.listDirectory({
                                rootPath: localRootPath,
                                dirPath: absPath
                            })
                            const listing = entries
                                .slice(0, 120)
                                .map(e => `${e.type === 'directory' ? '📁' : '📄'} ${e.name}`)
                                .join('\n')
                            return [
                                `### 📁 @folder:${m.relativePath}`,
                                'Contents:',
                                listing || '(empty folder)',
                                ''
                            ]
                        } catch {
                            return [`### 📁 @folder:${m.relativePath} (could not list)`, '']
                        }
                    })
                )

                for (const lines of mentionBlocks) {
                    contextParts.push(...lines)
                }

                if (filesystemMentions.length > limitedMentions.length) {
                    contextParts.push(
                        `... (${filesystemMentions.length - limitedMentions.length} additional @mentions skipped to keep response latency stable)`,
                        ''
                    )
                }

                mentionContext = contextParts.join('\n')
            }

            const conversationContext = conversationMentions.length > 0
                ? await buildConversationMentionContext(conversationMentions)
                : ''

            const runtimeInstructionParts = [
                modeRuntimeInstruction,
                customInstruction,
                aiFileInstruction,
                mentionContext,
                conversationContext
            ].filter(Boolean)
            const runtimeInstruction = runtimeInstructionParts.length > 0
                ? runtimeInstructionParts.join('\n\n')
                : undefined
            const requestWorkspaceId = resolvedConversationId
                ? (resolvedConversationWorkspaceId || null)
                : (workspaceId || null)
            const useKnowledge = settings.memoryEnabled && settings.useKnowledge
            const useFacts = settings.memoryEnabled && settings.useFacts
            const useHistory = settings.memoryEnabled && settings.useHistory
            let activeConversationId = resolvedConversationId
            let nextAgentMessage = messageText
            let prependGapBeforeAssistantStep = false
            let completedAgentSteps = 0
            let plannerRepairAttempts = 0
            let assistantVisibleContent = assistantPendingText
            let lastAgentStatusLine = ''
            let assistantAgentStatus: UIMessage['agentStatus'] = undefined
            let agentStatusSequence = 0
            let lastFocusedActionPath = ''
            let appliedActionsInTurn = 0
            const agentLoopStartedAt = Date.now()
            const fileAgentActionTracker =
                interactiveAiFileModeEnabled && localRootPath
                    ? createFileAgentActionTracker()
                    : null

            const setAssistantMessage = (
                content: string,
                isStreaming: boolean,
                agentStatus: UIMessage['agentStatus'] = assistantAgentStatus
            ) => {
                setMessages(prev => prev.map(m =>
                    m.id === assistantId
                        ? { ...m, content, isStreaming, agentStatus }
                        : m
                ))
            }

            const appendAgentStatus = (statusLine: string, isStreaming: boolean = true) => {
                const normalized = statusLine.trim()
                if (!normalized) return
                if (normalized === lastAgentStatusLine) return
                lastAgentStatusLine = normalized
                const displayText = normalizeAgentStatusLine(normalized)
                if (!displayText) return
                if (isTransientPendingMessageLocal(assistantVisibleContent)) {
                    assistantVisibleContent = ''
                }
                assistantAgentStatus = {
                    text: displayText,
                    tone: detectAgentStatusTone(normalized),
                    sequence: ++agentStatusSequence
                }
                finalFallbackAgentStatusText = displayText
                setAssistantMessage(assistantVisibleContent, isStreaming)
            }

            const focusAgentActionFile = (relativePath: string | null) => {
                if (!localRootPath || !relativePath) return
                const absolutePath = joinAbsolutePathForPreview(localRootPath, relativePath)
                if (!absolutePath || absolutePath === lastFocusedActionPath) return
                lastFocusedActionPath = absolutePath
                window.dispatchEvent(new CustomEvent('pigtex:agent-focus-file', {
                    detail: {
                        path: absolutePath
                    }
                }))
            }

            const rollbackAssistantStepContent = (stepContent: string) => {
                if (!stepContent) return
                if (assistantVisibleContent.endsWith(stepContent)) {
                    assistantVisibleContent = assistantVisibleContent.slice(
                        0,
                        assistantVisibleContent.length - stepContent.length
                    )
                } else {
                    const lastIndex = assistantVisibleContent.lastIndexOf(stepContent)
                    if (lastIndex >= 0) {
                        assistantVisibleContent = `${assistantVisibleContent.slice(0, lastIndex)}${assistantVisibleContent.slice(lastIndex + stepContent.length)}`
                    }
                }
                assistantVisibleContent = assistantVisibleContent
                    .replace(/[ \t]+\n/g, '\n')
                    .replace(/\n{3,}/g, '\n\n')
                    .trim()
                setAssistantMessage(assistantVisibleContent || assistantPendingText, true)
            }

            const appendFinalAssistantText = (finalText: string) => {
                const trimmed = finalText.trim()
                if (!trimmed) return
                assistantVisibleContent = assistantVisibleContent.trim()
                    ? `${assistantVisibleContent.trim()}\n\n${trimmed}`
                    : trimmed
                assistantAgentStatus = undefined
                finalFallbackAgentStatusText = ''
                setAssistantMessage(assistantVisibleContent, false, undefined)
            }

            if (useWorkspaceReviewControllerForTurn && localRootPath) {
                appendAgentStatus(copy.agentReviewingWorkspace)
                const reviewContext = await collectWorkspaceReviewContext(localRootPath, filesystemMentions)
                appendAgentStatus(copy.agentReviewedWorkspace(
                    reviewContext.visitedDirectories,
                    reviewContext.discoveredEntries,
                    reviewContext.filesRead
                ))
                if (reviewContext.truncated) {
                    appendAgentStatus(copy.agentTrimmedWorkspaceContext)
                }
                nextAgentMessage = buildWorkspaceReviewMessage(messageText, reviewContext)
            }

            const streamAssistantStep = async (message: string): Promise<{
                rawContent: string
                visibleContent: string
                hadToolArtifacts: boolean
                streamingActions: StreamingAction[]
            }> => {
                const isInternalToolTurn = isInternalToolResultPayload(message)
                const request: SmartChatRequest = {
                    message,
                    model: selectedModel.id,
                    conversation_id: activeConversationId || undefined,
                    workspace_id: requestWorkspaceId || undefined,
                    learning_program_id: (isInternalToolTurn || useWorkspaceReviewControllerForTurn || isImageToolRequest || isMediaGenerationRequest)
                        ? undefined
                        : (selectedModeId === 'learn' ? learningProgramId || undefined : undefined),
                    learning_mode: (isInternalToolTurn || useWorkspaceReviewControllerForTurn || isImageToolRequest || isMediaGenerationRequest)
                        ? 'off'
                        : selectedLearningMode,
                    runtime_instruction: runtimeInstruction,
                    stream: true,
                    temperature: settings.temperature,
                    max_tokens: settings.maxTokens > 0 ? settings.maxTokens : undefined,
                    image_attachments: images.length > 0 ? images : undefined,
                    file_attachments: files.length > 0 ? files : undefined,
                    // Keep conversation persistence on for chat history even when
                    // memory retrieval is disabled in settings.
                    use_memory: true,
                    use_knowledge: useKnowledge,
                    use_facts: useFacts,
                    use_history: useHistory,
                    mode: requestModeId,
                    use_web_search: isInternalToolTurn ? false : (shouldUseWebSearch ? true : undefined),
                    web_search_mode: (isImageToolRequest || isInternalToolTurn || useWorkspaceReviewControllerForTurn) ? undefined : 'auto',
                    web_search_deep_read: useWorkspaceReviewControllerForTurn
                        ? undefined
                        : (shouldHintDeepRead ? true : undefined),
                    web_search_deep_verify: useWorkspaceReviewControllerForTurn
                        ? undefined
                        : (shouldHintDeepVerify ? true : undefined),
                    web_search_max_results: useWorkspaceReviewControllerForTurn
                        ? undefined
                        : shouldHintDeepVerify
                        ? 8
                        : shouldHintDeepRead
                            ? 6
                            : undefined
                }

                if (isTransientPendingMessageLocal(assistantVisibleContent)) {
                    assistantVisibleContent = ''
                }

                if (prependGapBeforeAssistantStep && assistantVisibleContent.trim()) {
                    assistantVisibleContent = `${assistantVisibleContent}\n\n`
                }

                setAssistantMessage(assistantVisibleContent, true)

                let assistantRawContent = ''
                let targetVisibleStepContent = ''
                let renderedVisibleStepContent = ''
                let lastCommittedContent = assistantVisibleContent
                let keepSmoothing = true
                // Start in fast path: do not strip on every chunk unless artifacts are detected.
                let shouldStripToolArtifacts = false
                let hadToolArtifacts = false
                // ─── Streaming Action Parser integration ───
                const streamParser = new StreamingActionParser()
                const collectedStreamingActions: StreamingAction[] = []
                let activeStreamActionType: StreamingAction['actionType'] | null = null
                let activeStreamWritePath: string | null = null
                let activeStreamWriteContent = ''
                let streamWriteDebounceTimer: ReturnType<typeof setTimeout> | null = null
                const STREAM_WRITE_DEBOUNCE_MS = 120

                const flushStreamWrite = async () => {
                    if (activeStreamActionType !== 'write_file' && activeStreamActionType !== 'create_file') return
                    if (!activeStreamWritePath || !localRootPath || !window.electronAPI?.writeFile) return
                    const absPath = joinAbsolutePathForPreview(localRootPath, activeStreamWritePath)
                    try {
                        await window.electronAPI.writeFile({
                            filePath: absPath,
                            content: activeStreamWriteContent
                        })
                        // Push live file updates while content is still streaming.
                        window.dispatchEvent(new CustomEvent('pigtex:file-content-updated', {
                            detail: { targetPath: absPath }
                        }))
                    } catch (err) {
                        console.warn('[StreamWrite] Debounced write failed:', err)
                    }
                }

                const scheduleStreamWrite = () => {
                    if (streamWriteDebounceTimer) clearTimeout(streamWriteDebounceTimer)
                    streamWriteDebounceTimer = setTimeout(flushStreamWrite, STREAM_WRITE_DEBOUNCE_MS)
                }

                const commitRenderedContent = (isStreaming: boolean, force: boolean = false) => {
                    const nextContent = `${assistantVisibleContent}${renderedVisibleStepContent}`
                    if (!force && nextContent === lastCommittedContent) return
                    lastCommittedContent = nextContent
                    setAssistantMessage(nextContent, isStreaming)
                }

                const smoothingLoop = (async () => {
                    while (keepSmoothing || renderedVisibleStepContent !== targetVisibleStepContent) {
                        if (!targetVisibleStepContent.startsWith(renderedVisibleStepContent)) {
                            renderedVisibleStepContent = targetVisibleStepContent
                            commitRenderedContent(true, true)
                        } else {
                            const backlog = targetVisibleStepContent.length - renderedVisibleStepContent.length
                            if (backlog > 0) {
                                if (backlog <= STREAM_DIRECT_RENDER_BACKLOG) {
                                    renderedVisibleStepContent = targetVisibleStepContent
                                } else {
                                    const step = Math.min(backlog, getSmoothStreamChunkSize(backlog))
                                    renderedVisibleStepContent += targetVisibleStepContent.slice(
                                        renderedVisibleStepContent.length,
                                        renderedVisibleStepContent.length + step
                                    )
                                }
                                commitRenderedContent(true)
                            }
                        }

                        if (keepSmoothing || renderedVisibleStepContent !== targetVisibleStepContent) {
                            await new Promise<void>(resolve => setTimeout(resolve, STREAM_SMOOTH_FRAME_MS))
                        }
                    }
                })()

                try {
                    for await (const chunk of streamSmartChat(request, abortControllerRef.current?.signal)) {
                        assistantRawContent += chunk.content
                        const chunkUsage = normalizeStreamUsage(chunk.usage, selectedModel.id)

                        if (chunk.citations || chunk.webSearch || chunk.memory || chunk.learning || chunkUsage) {
                            setMessages(prev => prev.map(m =>
                                m.id === assistantId
                                    ? {
                                        ...m,
                                        memory: chunk.memory ?? m.memory,
                                        citations: chunk.citations ?? m.citations,
                                        webSearch: chunk.webSearch ?? m.webSearch,
                                        learning: chunk.learning ?? m.learning,
                                        usage: chunkUsage ?? m.usage
                                    }
                                    : m
                            ))
                        }

                        // ─── Feed chunk to streaming parser ───
                        if (interactiveAiFileModeEnabled && localRootPath) {
                            const parserEvents = streamParser.feed(chunk.content)
                            for (const evt of parserEvents) {
                                switch (evt.type) {
                                    case 'action_start': {
                                        hadToolArtifacts = true
                                        shouldStripToolArtifacts = true
                                        const action = evt.action
                                        activeStreamActionType = action.actionType
                                        activeStreamWritePath = action.path
                                        activeStreamWriteContent = ''

                                        if (action.actionType === 'write_file' || action.actionType === 'create_file') {
                                            // Ensure parent directory exists
                                            const lastSlashIdx = action.path.lastIndexOf('/')
                                            const parent = lastSlashIdx === -1 ? '' : action.path.slice(0, lastSlashIdx)
                                            if (parent && window.electronAPI?.createFolder) {
                                                const segments = parent.split('/').filter(Boolean)
                                                let currentDir = localRootPath
                                                for (const seg of segments) {
                                                    try {
                                                        await window.electronAPI.createFolder({ parentPath: currentDir, folderName: seg })
                                                    } catch { /* folder exists */ }
                                                    const sep = localRootPath.includes('\\') ? '\\' : '/'
                                                    currentDir = `${currentDir}${sep}${seg}`
                                                }
                                            }

                                            // Create empty file first
                                            const absPath = joinAbsolutePathForPreview(localRootPath, action.path)
                                            if (window.electronAPI?.writeFile) {
                                                try {
                                                    await window.electronAPI.writeFile({ filePath: absPath, content: '' })
                                                } catch { /* will retry with content */ }
                                            }
                                        }

                                        // Focus file in editor + show status
                                        focusAgentActionFile(action.path)
                                        window.dispatchEvent(new CustomEvent('pigtex:local-fs-refresh'))
                                        if (action.actionType === 'apply_diff') {
                                            appendAgentStatus(copy.agentPatchingFile(action.path))
                                        } else {
                                            appendAgentStatus(copy.agentWritingFile(action.path))
                                        }
                                        break
                                    }
                                    case 'content_chunk': {
                                        activeStreamWriteContent += evt.content
                                        // Debounced write to disk for full-write actions.
                                        if (activeStreamActionType === 'write_file' || activeStreamActionType === 'create_file') {
                                            scheduleStreamWrite()
                                        }
                                        break
                                    }
                                    case 'action_end': {
                                        const action = evt.action
                                        if (action.actionType === 'write_file' || action.actionType === 'create_file') {
                                            // Final write with complete content
                                            if (streamWriteDebounceTimer) {
                                                clearTimeout(streamWriteDebounceTimer)
                                                streamWriteDebounceTimer = null
                                            }
                                            if (localRootPath && window.electronAPI?.writeFile && activeStreamWritePath) {
                                                const absPath = joinAbsolutePathForPreview(localRootPath, activeStreamWritePath)
                                                try {
                                                    await window.electronAPI.writeFile({
                                                        filePath: absPath,
                                                        content: action.content
                                                    })
                                                    invalidateAiFileExecutionContextForAction(
                                                        aiFileExecutionContext,
                                                        localRootPath,
                                                        {
                                                            type: action.actionType,
                                                            path: action.path,
                                                            newPath: action.newPath
                                                        }
                                                    )
                                                    appendAgentStatus(copy.agentCompletedPath(action.path, action.content.length))
                                                    // Refresh file in editor
                                                    window.dispatchEvent(new CustomEvent('pigtex:file-content-updated', {
                                                        detail: { targetPath: absPath }
                                                    }))
                                                } catch (err) {
                                                    appendAgentStatus(copy.agentWriteFileError(
                                                        action.path,
                                                        err instanceof Error ? err.message : String(err)
                                                    ))
                                                }
                                            }
                                        } else if (action.actionType === 'apply_diff') {
                                            const patchResult = await executeAiFileActionsFromParsed(
                                                [{
                                                    type: 'apply_diff',
                                                    path: action.path,
                                                    content: action.content
                                                }],
                                                localRootPath,
                                                (progress: AiFileActionProgressEvent) => {
                                                    if (progress.stage === 'success') {
                                                        appendAgentStatus(copy.agentActionSuccess(progress.message))
                                                    } else if (progress.stage === 'error') {
                                                        appendAgentStatus(copy.agentPatchError(progress.message))
                                                    }
                                                },
                                                aiFileExecutionContext
                                            )
                                            if (patchResult?.applied) {
                                                const absPath = joinAbsolutePathForPreview(localRootPath, action.path)
                                                window.dispatchEvent(new CustomEvent('pigtex:file-content-updated', {
                                                    detail: { targetPath: absPath }
                                                }))
                                            }
                                        }
                                        collectedStreamingActions.push(action)
                                        activeStreamActionType = null
                                        activeStreamWritePath = null
                                        activeStreamWriteContent = ''
                                        break
                                    }
                                    case 'self_closing_action': {
                                        hadToolArtifacts = true
                                        shouldStripToolArtifacts = true
                                        collectedStreamingActions.push(evt.action)
                                        break
                                    }
                                }
                            }
                        }

                        // ─── Existing tool artifact detection (for pigtex_fs JSON fallback) ───
                        // Scan only a bounded tail to catch cross-chunk markers without
                        // repeatedly running heavy regex on the full accumulated response.
                        const artifactScanWindow = assistantRawContent.length > STREAM_ARTIFACT_SCAN_WINDOW_CHARS
                            ? assistantRawContent.slice(-STREAM_ARTIFACT_SCAN_WINDOW_CHARS)
                            : assistantRawContent
                        const chunkHasToolArtifacts = containsAiToolArtifacts(artifactScanWindow)
                        const chunkHasHallucinatedCalls = containsHallucinatedToolCalls(artifactScanWindow)
                        if (chunkHasToolArtifacts && interactiveAiFileModeEnabled && Boolean(localRootPath)) {
                            hadToolArtifacts = true
                            shouldStripToolArtifacts = true
                        }
                        if (!shouldStripToolArtifacts && (chunkHasToolArtifacts || chunkHasHallucinatedCalls)) {
                            shouldStripToolArtifacts = true
                        }
                        targetVisibleStepContent = shouldStripToolArtifacts
                            ? stripAiToolArtifacts(assistantRawContent)
                            : assistantRawContent

                        if (chunk.conversationId) {
                            if (!activeConversationId && !currentConversationIdRef.current) {
                                justCreatedConversation.current = true
                                updateConversationSelection(chunk.conversationId, requestWorkspaceId)
                                onConversationCreated?.(chunk.conversationId)
                                emitConversationUpdated(chunk.conversationId, requestWorkspaceId)
                            }
                            activeConversationId = chunk.conversationId
                        }
                    }
                } finally {
                    // Flush any remaining streaming parser state
                    if (interactiveAiFileModeEnabled && localRootPath) {
                        const flushEvents = streamParser.flush()
                        for (const evt of flushEvents) {
                            if (evt.type === 'action_end') {
                                if (localRootPath && window.electronAPI?.writeFile && activeStreamWritePath) {
                                    const absPath = joinAbsolutePathForPreview(localRootPath, activeStreamWritePath)
                                    try {
                                        await window.electronAPI.writeFile({
                                            filePath: absPath,
                                            content: evt.action.content
                                        })
                                        invalidateAiFileExecutionContextForAction(
                                            aiFileExecutionContext,
                                            localRootPath,
                                            {
                                                type: evt.action.actionType,
                                                path: evt.action.path,
                                                newPath: evt.action.newPath
                                            }
                                        )
                                        appendAgentStatus(copy.agentCompletedPath(evt.action.path, evt.action.content.length))
                                        window.dispatchEvent(new CustomEvent('pigtex:file-content-updated', {
                                            detail: { targetPath: absPath }
                                        }))
                                    } catch { /* non-fatal */ }
                                }
                                collectedStreamingActions.push(evt.action)
                            }
                            if (evt.type === 'self_closing_action') {
                                collectedStreamingActions.push(evt.action)
                            }
                        }
                    }
                    // Clear debounce timer 
                    if (streamWriteDebounceTimer) {
                        clearTimeout(streamWriteDebounceTimer)
                        streamWriteDebounceTimer = null
                    }
                    activeStreamActionType = null
                    // Refresh explorer if we wrote files
                    if (collectedStreamingActions.length > 0) {
                        window.dispatchEvent(new CustomEvent('pigtex:local-fs-refresh'))
                    }

                    // Flush full buffered content immediately when stream closes to avoid delayed stop feel.
                    renderedVisibleStepContent = targetVisibleStepContent
                    commitRenderedContent(true, true)
                    keepSmoothing = false
                    await smoothingLoop
                }

                // Compute the final visible text for this step.
                // stripAiToolArtifacts removes tool tags but keeps surrounding text.
                const strippedStepContent = shouldStripToolArtifacts
                    ? stripAiToolArtifacts(assistantRawContent)
                    : assistantRawContent

                const finalVisibleStepContent = strippedStepContent
                renderedVisibleStepContent = finalVisibleStepContent
                assistantVisibleContent = `${assistantVisibleContent}${finalVisibleStepContent}`
                setAssistantMessage(assistantVisibleContent, false)

                const fallbackCompletionTokens = Math.max(0, Math.floor(strippedStepContent.split(/\s+/).filter(Boolean).length * 1.3))
                if (fallbackCompletionTokens > 0) {
                    setMessages(prev => prev.map(m =>
                        m.id === assistantId && !m.usage
                            ? {
                                ...m,
                                usage: buildStoredAssistantUsage(fallbackCompletionTokens, m.model ?? selectedModel.id)
                            }
                            : m
                    ))
                }

                if (activeConversationId) {
                    emitConversationUpdated(activeConversationId, requestWorkspaceId)
                }

                return {
                    rawContent: assistantRawContent,
                    visibleContent: finalVisibleStepContent,
                    hadToolArtifacts,
                    streamingActions: collectedStreamingActions
                }
            }

            while (true) {
                if (Date.now() - agentLoopStartedAt > MAX_AI_AGENT_RUNTIME_MS) {
                    appendAgentStatus(copy.agentTimeout)
                    showError(copy.failedApplyActionsTimeout)
                    break
                }

                const stepResult = await streamAssistantStep(nextAgentMessage)
                const assistantStepContent = stepResult.rawContent
                prependGapBeforeAssistantStep = false

                if (!(interactiveAiFileModeEnabled && localRootPath)) {
                    break
                }

                const plannerParseResult = parseFileAgentPlannerEnvelope(assistantStepContent)
                let parsedActions = { actions: [] as ParsedAiFileAction[], errors: [] as string[] }
                if (plannerParseResult.envelope || plannerParseResult.errors.length > 0) {
                    rollbackAssistantStepContent(stepResult.visibleContent)

                    if (plannerParseResult.errors.length > 0) {
                        if (plannerRepairAttempts < 1) {
                            appendAgentStatus(copy.agentInvalidPlannerBlock)
                            plannerRepairAttempts += 1
                            nextAgentMessage = [
                                'Your previous file_agent block was invalid.',
                                `Parser errors: ${plannerParseResult.errors.join('; ')}`,
                                'Return exactly ONE corrected ```file_agent``` JSON block and nothing else.'
                            ].join('\n')
                            prependGapBeforeAssistantStep = true
                            continue
                        }
                        showError(copy.unsupportedResponse)
                        break
                    }

                    const plannerEnvelope = plannerParseResult.envelope
                    if (plannerEnvelope?.kind === 'final_answer') {
                        appendFinalAssistantText(plannerEnvelope.message || '')
                        break
                    }

                    if (plannerEnvelope?.kind === 'need_user_input') {
                        appendFinalAssistantText(plannerEnvelope.message || '')
                        break
                    }

                    parsedActions = parseFileAgentPlannerActions(plannerEnvelope)
                } else {
                    const legacyParsedActions = parseAiFileActions(assistantStepContent)
                    const hasLegacyFileActionArtifacts =
                        legacyParsedActions.actions.length > 0 || legacyParsedActions.errors.length > 0
                    if (hasLegacyFileActionArtifacts) {
                        rollbackAssistantStepContent(stepResult.visibleContent)
                        if (plannerRepairAttempts < 1) {
                            appendAgentStatus(copy.agentLegacyProtocol)
                            plannerRepairAttempts += 1
                            nextAgentMessage = [
                                'You used the legacy file action protocol.',
                                legacyParsedActions.errors.length > 0
                                    ? `Legacy parser errors: ${legacyParsedActions.errors.join('; ')}`
                                    : `Legacy actions detected: ${legacyParsedActions.actions.map(action => `${action.type}:${action.path}`).join(', ')}`,
                                'Return exactly ONE corrected ```file_agent``` JSON block and nothing else.'
                            ].join('\n')
                            prependGapBeforeAssistantStep = true
                            continue
                        }
                        showError(copy.legacyProtocol)
                        break
                    }
                }

                const hasCandidateToolActions =
                    parsedActions.actions.length > 0
                    || parsedActions.errors.length > 0

                if (completedAgentSteps >= agentStepBudget && hasCandidateToolActions) {
                    if (appliedActionsInTurn > 0) {
                        showInfo(copy.reachedMaxSteps)
                        appendFinalAssistantText(
                            normalizeAgentStatusLine(copy.agentReachedMaxSteps) || copy.reachedMaxSteps
                        )
                        break
                    }
                    appendAgentStatus(copy.agentReachedMaxSteps)
                    showInfo(copy.reachedMaxSteps)
                    break
                }
                if (parsedActions.errors.length > 0) {
                    if (plannerRepairAttempts < 1) {
                        appendAgentStatus(copy.agentInvalidActionList)
                        plannerRepairAttempts += 1
                        nextAgentMessage = [
                            'Your previous file_agent block contained invalid actions.',
                            `Parser errors: ${parsedActions.errors.join('; ')}`,
                            'Return exactly ONE corrected ```file_agent``` JSON block and nothing else.'
                        ].join('\n')
                        prependGapBeforeAssistantStep = true
                        continue
                    }
                    showError(copy.invalidFileActions)
                    break
                }

                if (parsedActions.actions.length === 0) {
                    break
                }

                const {
                    filteredActions: actionableActions,
                    skippedCompleted,
                    skippedBlocked
                } = filterExecutableFileAgentActions(fileAgentActionTracker!, parsedActions.actions)

                if (skippedCompleted > 0 || skippedBlocked > 0) {
                    appendAgentStatus(copy.agentSkippedActions(skippedCompleted + skippedBlocked))
                }
                if (actionableActions.length === 0) {
                    if (skippedCompleted > 0 && appliedActionsInTurn > 0) {
                        appendFinalAssistantText(copy.agentAppliedChangesFinal(appliedActionsInTurn))
                        break
                    }
                    appendAgentStatus(copy.agentNoNewActions)
                    break
                }

                const actionBatchSignature = serializeAiActionBatch(actionableActions)
                if (fileAgentActionTracker!.executedActionBatchSignatures.has(actionBatchSignature)) {
                    if (appliedActionsInTurn > 0) {
                        showInfo(copy.stoppedRepeatedActions)
                        appendFinalAssistantText(copy.agentAppliedChangesFinal(appliedActionsInTurn))
                        break
                    }
                    appendAgentStatus(copy.agentRepeatedLoop)
                    showInfo(copy.stoppedRepeatedActions)
                    break
                }

                rollbackAssistantStepContent(stepResult.visibleContent)

                const approved = requiresAiActionApproval
                    ? await requestAiActionApproval(actionableActions)
                    : true
                if (!approved) {
                    showInfo(copy.actionsCancelled)
                    break
                }
                fileAgentActionTracker!.executedActionBatchSignatures.add(actionBatchSignature)

                appendAgentStatus(copy.agentExecutingActions(actionableActions.length))
                const fileActionResult = await executeAiFileActionsFromParsed(
                    actionableActions,
                    localRootPath,
                    (progress: AiFileActionProgressEvent) => {
                        const focusRelativePath = resolveActionFocusRelativePath(progress.action, progress.stage)
                        if (focusRelativePath) {
                            focusAgentActionFile(focusRelativePath)
                        }
                        if (progress.stage === 'start') {
                            appendAgentStatus(copy.agentActionStart(progress.index + 1, progress.total, progress.message))
                            return
                        }
                        if (progress.stage === 'progress') {
                            appendAgentStatus(copy.agentActionProgress(progress.message))
                            return
                        }
                        if (progress.stage === 'success') {
                            noteSuccessfulFileAgentAction(fileAgentActionTracker!, progress.action)
                            if (
                                progress.action.type === 'write_file'
                                || progress.action.type === 'create_file'
                                || progress.action.type === 'apply_diff'
                            ) {
                                window.dispatchEvent(new CustomEvent('pigtex:file-content-updated', {
                                    detail: {
                                        targetPath: joinAbsolutePathForPreview(localRootPath, progress.action.path)
                                    }
                                }))
                            }
                            appendAgentStatus(copy.agentActionSuccess(progress.message))
                            return
                        }
                        if (progress.stage === 'error') {
                            noteFailedFileAgentAction(fileAgentActionTracker!, progress.action, progress.message)
                            appendAgentStatus(copy.agentActionError(progress.message))
                        }
                    },
                    aiFileExecutionContext
                )
                if (!fileActionResult) {
                    break
                }
                appliedActionsInTurn += fileActionResult.applied
                appendAgentStatus(copy.agentBatchFinished(
                    fileActionResult.applied,
                    actionableActions.length,
                    fileActionResult.errors.length
                ))

                for (const renamed of fileActionResult.renamed) {
                    window.dispatchEvent(new CustomEvent('pigtex:fs-path-renamed', { detail: renamed }))
                }
                for (const deleted of fileActionResult.deleted) {
                    window.dispatchEvent(new CustomEvent('pigtex:fs-path-deleted', { detail: deleted }))
                }
                if (fileActionResult.applied > 0) {
                    window.dispatchEvent(new CustomEvent('pigtex:local-fs-refresh'))
                    showSuccess(copy.appliedActions(fileActionResult.applied))
                }
                if (fileActionResult.errors.length > 0) {
                    showError(copy.actionErrors(fileActionResult.errors.length))
                }

                if (fileActionResult.applied <= 0) {
                    if (fileActionResult.errors.length > 0 && completedAgentSteps < agentStepBudget) {
                        completedAgentSteps += 1
                        nextAgentMessage = buildFileAgentToolContextMessage(fileActionResult)
                        prependGapBeforeAssistantStep = true
                        continue
                    }
                    break
                }

                if (completedAgentSteps < agentStepBudget && shouldContinueWithToolResult(fileActionResult)) {
                    completedAgentSteps += 1
                    appendAgentStatus(copy.agentSummarizingChanges)
                    nextAgentMessage = buildFileAgentToolContextMessage(fileActionResult)
                    prependGapBeforeAssistantStep = true
                    continue
                }

                if (completedAgentSteps >= agentStepBudget) {
                    showInfo(copy.reachedMaxSteps)
                }
                break
            }
        } catch (error: any) {
            if (error?.name !== 'AbortError') {
                console.error('Chat error:', error)
                const connectivityIssue = isMediaGenerationRequest
                    ? null
                    : await diagnoseApiConnectivityIssue(error)
                const actualErrorText = error instanceof Error && error.message.trim() ? error.message.trim() : ''
                const resolvedErrorMessage = isImageToolRequest
                    ? (actualErrorText || copy.imageRequestFailedMessage)
                    : isVoiceToolRequest
                        ? (actualErrorText || copy.voiceRequestFailedMessage)
                        : isVideoToolRequest
                            ? (actualErrorText || copy.videoRequestFailedMessage)
                    : connectivityIssue
                        ? formatConnectivityIssueMessage(connectivityIssue)
                        : (actualErrorText || copy.chatRequestFailedMessage)
                setMessages(prev => prev.map(m =>
                    m.id === assistantId
                        ? {
                            ...m,
                            content: resolvedErrorMessage,
                            isStreaming: false,
                            agentStatus: undefined,
                            webSearch: shouldUseWebSearch
                                ? { enabled: true, status: 'error' }
                                : m.webSearch
                        }
                        : m
                ))
                const fallbackSpecialistError = isImageToolRequest
                    ? (actualErrorText || copy.imageRequestFailedMessage)
                    : isVoiceToolRequest
                        ? (actualErrorText || copy.voiceRequestFailedMessage)
                        : isVideoToolRequest
                            ? (actualErrorText || copy.videoRequestFailedMessage)
                            : resolvedErrorMessage || copy.failedAiResponse
                showError(isImageToolRequest ? (actualErrorText || copy.failedProcessImage) : fallbackSpecialistError)
            } else {
                setMessages(prev => prev.map(m =>
                    m.id === assistantId
                        ? {
                            ...m,
                            isStreaming: false,
                            agentStatus: undefined,
                            webSearch: shouldUseWebSearch
                                ? { enabled: true, status: 'skipped' }
                                : m.webSearch
                        }
                        : m
                ))
            }
        } finally {
            const fallbackAgentStatusText = finalFallbackAgentStatusText.trim()
            setMessages(prev => prev.map(m =>
                m.id !== assistantId
                    ? m
                    : (() => {
                        const keepStreamingForPendingVideo =
                            Boolean(
                                m.videoTask?.taskId
                                && !isTerminalVideoTaskStatus(m.videoTask.status)
                            )
                        const hasRenderableContent = Boolean(
                            m.content?.trim()
                            && !isTransientPendingMessageLocal(m.content)
                        )
                        const resolvedContent =
                            !keepStreamingForPendingVideo && !hasRenderableContent && fallbackAgentStatusText
                                ? fallbackAgentStatusText
                                : m.content
                        return {
                            ...m,
                            content: resolvedContent,
                            isStreaming: keepStreamingForPendingVideo,
                            agentStatus: undefined
                        }
                    })()
            ))
            setIsTyping(false)
            abortControllerRef.current = null
            sendInFlightRef.current = false
        }
    }

    const getActionIcon = (type: string) => {
        switch (type) {
            case 'read_file': return <FileText size={13} />
            case 'create_file': return <FilePlus2 size={13} />
            case 'write_file': return <FileEdit size={13} />
            case 'create_folder': return <FolderPlus size={13} />
            case 'delete_file':
            case 'delete_folder':
            case 'delete_path': return <Trash2 size={13} />
            case 'rename_path': return <ArrowRightLeft size={13} />
            default: return <FileText size={13} />
        }
    }

    const getActionColor = (type: string) => {
        if (type.startsWith('read')) return 'action-read'
        if (type.startsWith('create')) return 'action-create'
        if (type.startsWith('write') || type === 'edit_file') return 'action-write'
        if (type.startsWith('delete')) return 'action-delete'
        if (type.startsWith('rename')) return 'action-rename'
        return ''
    }

    const getAgentStatusIcon = (tone: NonNullable<UIMessage['agentStatus']>['tone']) => {
        switch (tone) {
            case 'success':
                return <Check size={13} />
            case 'error':
                return <AlertCircle size={13} />
            case 'warning':
                return <Wrench size={13} />
            case 'running':
                return <Loader2 size={13} />
            case 'info':
            default:
                return <Sparkles size={13} />
        }
    }

    const isImageToolMode = selectedImageTool.id === 'image'
    const isVoiceToolMode = selectedImageTool.id === 'voice'
    const isVideoToolMode = selectedImageTool.id === 'video'
    const isImageEditMode = isImageToolMode && imageAttachments.length > 0
    const isChatMode = selectedImageTool.id === 'chat'
    const modelMenuSource = isImageToolMode ? imageModels : models
    const shortlistedModels = buildModelShortlist(modelMenuSource, selectedModel.id)
    const hasHiddenModels = modelMenuSource.length > shortlistedModels.length
    const visibleModelMenuOptions = showAllModelsInMenu ? modelMenuSource : shortlistedModels
    const showComposerAttachmentControls = isChatMode || isImageToolMode
    const hasPendingAssistantMessage = messages.some(hasPendingAssistantWork)
    const isInputLocked = isTyping || sendInFlightRef.current || hasPendingAssistantMessage
    const activeLearningProgramTitle = (learningProgramTitle || '').trim()
    const latestLearningMetadata = useMemo(() => {
        const lastLearningMessage = [...messages].reverse().find((message) => (
            Boolean(message.learning?.learning_state)
            || Boolean(message.learning?.focus_node)
            || Boolean(message.learning?.turn_output)
        ))
        return lastLearningMessage?.learning || null
    }, [messages])
    const learningCockpitState = learningLiveState?.learning_state || latestLearningMetadata?.learning_state || null
    const learningCockpitFocus = learningLiveState?.focus_node || latestLearningMetadata?.focus_node || null
    const learningCockpitAdaptive = learningCockpitState?.adaptive_plan || null
    const learningCockpitReview = learningCockpitState?.review_summary || null
    const learningCockpitSnapshot = learningCockpitState?.focus_snapshot || null
    const learningCockpitSources: LearningState['source_registry']['sources'] = (learningCockpitState?.source_registry?.sources || []).slice(0, 3)
    const learningCockpitFocusSkill = (learningCockpitState?.knowledge_map?.skills || []).find(
        (skill: LearningState['knowledge_map']['skills'][number]) => skill.node_id === learningCockpitFocus?.id
    ) || null
    const learningCockpitProgramTitle =
        learningLiveState?.program?.title
        || activeLearningProgramTitle
        || learningLiveState?.program?.topic
        || latestLearningMetadata?.program_title
        || null
    const learningCockpitFocusTitle =
        learningCockpitSnapshot?.title
        || learningCockpitFocus?.title
        || learningCockpitProgramTitle
        || null
    const learningCockpitGoal =
        learningCockpitState?.current_goal?.operational_goal
        || learningCockpitState?.current_goal?.raw_goal
        || null
    const learningCockpitNextAction =
        learningCockpitSnapshot?.next_verification_action
        || learningCockpitState?.last_turn_output?.next_step
        || learningLiveState?.next_action
        || null
    const learningCockpitMisconceptions: string[] = (
        learningCockpitSnapshot?.misconceptions
        || learningCockpitFocusSkill?.misconceptions
        || []
    ).slice(0, 3)
    const learningCockpitSuccessSignals: string[] = (learningCockpitSnapshot?.success_criteria || []).slice(0, 3)
    const showLearningCockpit = selectedMode.id === 'learn' && (
        Boolean(learningCockpitProgramTitle)
        || Boolean(learningCockpitGoal)
        || Boolean(learningCockpitFocusTitle)
        || learningCockpitSources.length > 0
        || Boolean(learningCockpitAdaptive)
        || Boolean(learningCockpitReview)
        || isLearningLiveLoading
    )
    const canSend = !isInputLocked && (
        isImageToolMode
            ? Boolean(inputValue.trim())
            : isVoiceToolMode
                ? Boolean(inputValue.trim() && voiceStudio.model.trim())
                : isVideoToolMode
                    ? Boolean(inputValue.trim() && videoStudio.model.trim())
                    : Boolean(
                        selectedModel.id.trim()
                        && (inputValue.trim() || currentMentions.length > 0 || imageAttachments.length > 0 || fileAttachments.length > 0)
                    )
    )
    const chatInputPlaceholder = isImageToolMode
        ? (isImageEditMode ? copy.describeImageEdit : copy.describeImageGenerate)
        : isVoiceToolMode
            ? copy.describeVoiceGenerate
            : isVideoToolMode
                ? copy.describeVideoGenerate
                : selectedMode.id === 'learn'
                    ? (
                        activeLearningProgramTitle
                            ? (isVietnamese
                                ? `Tiep tuc "${activeLearningProgramTitle}" hoac gui them bai/tai lieu cho PigTex Learn...`
                                : `Continue "${activeLearningProgramTitle}" or attach material for PigTex Learn...`)
                            : (isVietnamese
                                ? 'Noi muc tieu hoc hoac gui bai/tai lieu de PigTex day theo mode Learn...'
                                : 'Describe your learning goal or attach material for PigTex Learn...')
                    )
                : (imageAttachments.length > 0 || fileAttachments.length > 0
            ? copy.attachmentPrompt
            : (localRootPath ? copy.askAnythingMentions : copy.askAnything))

    const getRequestBadgeTitle = (requestKind?: MessageRequestKind) => {
        switch (requestKind) {
            case 'image_generate':
                return copy.imageGenerationRequest
            case 'image_edit':
                return copy.imageEditRequest
            case 'voice':
                return copy.voiceRequest
            case 'video':
                return copy.videoRequest
            case 'image_attachment':
            default:
                return copy.messageWithImage
        }
    }

    const getRequestBadgeIcon = (requestKind?: MessageRequestKind) => {
        switch (requestKind) {
            case 'voice':
                return <FileText size={12} />
            case 'video':
                return <Globe size={12} />
            case 'image_generate':
            case 'image_edit':
            case 'image_attachment':
            default:
                return <Image size={12} />
        }
    }

    const formatActionPreview = (action: ParsedAiFileAction) => {
        switch (action.type) {
            case 'read_file':
                return copy.actionReadFile(action.path)
            case 'create_file':
                return copy.actionCreateFile(action.path)
            case 'write_file':
                return copy.actionUpdateFile(action.path)
            case 'create_folder':
                return copy.actionCreateFolder(action.path)
            case 'delete_file':
                return copy.actionDeleteFile(action.path)
            case 'delete_folder':
                return copy.actionDeleteFolder(action.path)
            case 'delete_path':
                return copy.actionDeletePath(action.path)
            case 'rename_path':
                return copy.actionRename(action.path, action.newPath || copy.actionMissingNewPath)
            default:
                return copy.workspaceActionFallback(action.type, action.path)
        }
    }

    const formatActionAbsolutePath = (action: ParsedAiFileAction) => {
        if (!localRootPath) return null

        const absoluteSourcePath = joinAbsolutePathForPreview(localRootPath, action.path)
        if (action.type !== 'rename_path') {
            return absoluteSourcePath
        }

        const targetRelativePath = resolveRenameTargetPath(action)
        if (!targetRelativePath) {
            return absoluteSourcePath
        }

        const absoluteTargetPath = joinAbsolutePathForPreview(localRootPath, targetRelativePath)
        return `${absoluteSourcePath} -> ${absoluteTargetPath}`
    }

    const toggleActionDiff = useCallback((actionKey: string) => {
        setExpandedActionDiffs(prev => ({
            ...prev,
            [actionKey]: !prev[actionKey]
        }))
    }, [])

    const hasPendingDiffPreview = isPreparingActionDiffs ||
        Object.values(aiActionDiffPreviews).some(preview => preview.status === 'loading')

    return (
        <div className={`chat-panel ${variant}`}>
            {/* Header - Only for sidebar variant */}
            {!isCentered && (
                <div className="chat-header">
                    <div className="chat-header-left">
                        <div className="chat-avatar">
                            <img
                                src={assistantAvatarUrl}
                                alt={`${assistantProfile.name} avatar`}
                                className="assistant-avatar-image"
                            />
                        </div>
                        <div className="chat-header-title">
                            <span className="chat-name">{assistantProfile.name}</span>
                            <span className="chat-status">
                                <span className="status-dot" />
                                {copy.online}
                            </span>
                        </div>
                    </div>
                </div>
            )}

            {/* Messages / Welcome */}
            <div className="chat-messages" ref={messagesContainerRef}>
                {isCentered && messages.length === 0 ? (
                    <div className="chat-welcome">
                        {/* Animated background orbs */}
                        <div className="welcome-bg-orbs">
                            {PIGTEX_ORB_COLORS.map((color, i) => (
                                <motion.div
                                    key={`orb-${i}`}
                                    className={`welcome-orb welcome-orb-${i + 1}`}
                                    style={{ background: color }}
                                    animate={{
                                        x: [0, (i % 2 === 0 ? 30 : -30), 0],
                                        y: [0, (i % 2 === 0 ? -20 : 20), 0],
                                        scale: [1, 1.15, 1],
                                    }}
                                    transition={{
                                        duration: 6 + i * 2,
                                        repeat: Infinity,
                                        ease: 'easeInOut',
                                    }}
                                />
                            ))}
                        </div>

                        {/* Assistant Avatar */}
                        <motion.div
                            className="welcome-avatar-wrapper"
                            initial={{ scale: 0, rotate: -10 }}
                            animate={{ scale: 1, rotate: 0 }}
                            transition={{ delay: 0.1, type: 'spring', stiffness: 200, damping: 15 }}
                        >
                            <div className="welcome-avatar-ring">
                                <img
                                    src={assistantAvatarUrl}
                                    alt={`${assistantProfile.name} avatar`}
                                    className="welcome-avatar-img"
                                />
                            </div>
                            <motion.div
                                className="welcome-avatar-glow"
                                animate={{ opacity: [0.4, 0.8, 0.4], scale: [0.95, 1.05, 0.95] }}
                                transition={{ duration: 3, repeat: Infinity, ease: 'easeInOut' }}
                            />
                        </motion.div>

                        {/* Greeting */}
                        <motion.h1
                            className="welcome-title"
                            initial={{ opacity: 0, y: 16 }}
                            animate={{ opacity: 1, y: 0 }}
                            transition={{ delay: 0.25, duration: 0.5 }}
                        >
                            {greeting}
                        </motion.h1>
                        <motion.p
                            className="welcome-subtitle"
                            initial={{ opacity: 0, y: 10 }}
                            animate={{ opacity: 1, y: 0 }}
                            transition={{ delay: 0.35, duration: 0.5 }}
                        >
                            {subtitle}
                        </motion.p>

                        {/* Assistant Tag */}
                        <motion.div
                            className="welcome-assistant-tag"
                            initial={{ opacity: 0, scale: 0.9 }}
                            animate={{ opacity: 1, scale: 1 }}
                            transition={{ delay: 0.4, duration: 0.4 }}
                        >
                            <span className="welcome-assistant-dot" />
                            <span>{assistantProfile.name}</span>
                            <span className="welcome-assistant-separator">·</span>
                            <span>{copy.online}</span>
                        </motion.div>

                        {/* Suggestion Cards */}
                        <motion.div
                            className="welcome-suggestions"
                            initial={{ opacity: 0, y: 16 }}
                            animate={{ opacity: 1, y: 0 }}
                            transition={{ delay: 0.5 }}
                        >
                            {suggestions.map((suggestion, index) => (
                                <motion.button
                                    key={suggestion.id}
                                    className="welcome-suggestion-card"
                                    initial={{ opacity: 0, y: 12 }}
                                    animate={{ opacity: 1, y: 0 }}
                                    transition={{ delay: 0.55 + index * 0.08 }}
                                    whileHover={{ scale: 1.03, y: -2 }}
                                    whileTap={{ scale: 0.98 }}
                                    onClick={() => {
                                        setInputValue(suggestion.text)
                                        setCurrentMentions([])
                                    }}
                                >
                                    <span
                                        className="welcome-suggestion-icon"
                                        style={{ background: suggestion.gradient }}
                                    >
                                        <suggestion.icon size={14} />
                                    </span>
                                    <span className="welcome-suggestion-text">{suggestion.text}</span>
                                    <ArrowRight size={14} className="welcome-suggestion-arrow" />
                                </motion.button>
                            ))}
                        </motion.div>
                    </div>
                ) : (
                    <>
                        {messages.map((message, index) => (
                            <motion.div
                                key={message.id}
                                className={`message message-${message.role}`}
                                initial={{ opacity: 0, y: 10 }}
                                animate={{ opacity: 1, y: 0 }}
                                transition={{ delay: index < 5 ? index * 0.05 : 0 }}
                            >
                                {/* Assistant: avatar on left */}
                                {message.role === 'assistant' && (
                                    <div className={`message-avatar ${message.isStreaming ? 'streaming' : ''}`}>
                                        <img
                                            src={assistantAvatarUrl}
                                            alt={`${assistantProfile.name} avatar`}
                                            className="assistant-avatar-image"
                                        />
                                    </div>
                                )}

                                <div className="message-content">
                                    {/* Only show header for assistant */}
                                    {message.role === 'assistant' && (
                                        <div className="message-header">
                                            <span className="message-author">{assistantProfile.name}</span>
                                            <span className="message-time">{message.timestamp}</span>
                                        </div>
                                    )}

                                    {message.role === 'assistant' && message.agentStatus?.text && (
                                        <div
                                            key={`${message.id}-${message.agentStatus.sequence}`}
                                            className={`message-agent-status message-agent-status-${message.agentStatus.tone}`}
                                        >
                                            <span
                                                className={`message-agent-status-icon ${message.agentStatus.tone === 'running' ? 'is-spinning' : ''}`}
                                                aria-hidden="true"
                                            >
                                                {getAgentStatusIcon(message.agentStatus.tone)}
                                            </span>
                                            <span className="message-agent-status-text">{message.agentStatus.text}</span>
                                        </div>
                                    )}

                                    {/* Message body */}
                                    {message.role === 'user' ? (
                                        <div className="message-bubble">
                                            {message.requestKind && (
                                                <span
                                                    className={`message-image-flag message-image-flag-${message.requestKind}`}
                                                    title={getRequestBadgeTitle(message.requestKind)}
                                                >
                                                    {getRequestBadgeIcon(message.requestKind)}
                                                </span>
                                            )}
                                            {message.images && message.images.length > 0 && (
                                                <div className="message-images">
                                                    {message.images.map(img => (
                                                        <div key={img.id} className="message-image-item">
                                                            <ProtectedImage
                                                                source={img.base64_data}
                                                                alt={img.filename}
                                                                className="message-image-thumb message-image-thumb-user"
                                                                title={`${img.filename} (${img.size > 0 ? (img.size / 1024).toFixed(0) + 'KB' : copy.savedTime})`}
                                                                onImageClick={setLightboxImage}
                                                            />
                                                            <a
                                                                className="message-image-download"
                                                                href={resolveStoredAssetUrl(img.base64_data)}
                                                                download={(img.filename || 'image').trim() || 'image'}
                                                                title={`${isVietnamese ? 'Tải ảnh' : 'Download image'} ${(img.filename || '').trim()}`}
                                                            >
                                                                <Save size={12} />
                                                                <span>{isVietnamese ? 'Tải' : 'Download'}</span>
                                                            </a>
                                                        </div>
                                                    ))}
                                                </div>
                                            )}
                                            {message.media && message.media.length > 0 && (
                                                <div className="message-generated-media-list message-generated-media-list-user">
                                                    {message.media.map(item => (
                                                        <GeneratedMediaCard
                                                            key={item.id}
                                                            item={item}
                                                            previewLabel={item.kind === 'audio' ? copy.audioPreviewLabel : copy.videoPreviewLabel}
                                                            playLabel={item.kind === 'audio'
                                                                ? (isVietnamese ? 'Phát audio' : 'Play audio')
                                                                : (isVietnamese ? 'Phát video' : 'Play video')}
                                                            pauseLabel={item.kind === 'audio'
                                                                ? (isVietnamese ? 'Tạm dừng audio' : 'Pause audio')
                                                                : (isVietnamese ? 'Tạm dừng video' : 'Pause video')}
                                                            muteLabel={isVietnamese ? 'Tắt tiếng' : 'Mute'}
                                                            unmuteLabel={isVietnamese ? 'Bật tiếng' : 'Unmute'}
                                                            downloadLabel={isVietnamese ? 'Tải' : 'Download'}
                                                        />
                                                    ))}
                                                </div>
                                            )}
                                            {message.files && message.files.length > 0 && (
                                                <div className="message-files">
                                                    {message.files.map(file => (
                                                        <span key={file.id} className="message-file-chip" title={`${file.filename} (${file.mime_type})`}>
                                                            <Paperclip size={11} />
                                                            <span>{file.filename}</span>
                                                        </span>
                                                    ))}
                                                </div>
                                            )}
                                            {message.mentions && message.mentions.length > 0 && (
                                                <div className="message-reference-chips">
                                                    {message.mentions.map((mention) => (
                                                        <span
                                                            key={`${mention.type}:${mention.referenceId || mention.relativePath}`}
                                                            className={`message-reference-chip message-reference-chip-${mention.type}`}
                                                            title={mention.type === 'conversation'
                                                                ? mention.subtitle || mention.name
                                                                : mention.relativePath}
                                                        >
                                                            <span className="message-reference-chip-prefix">
                                                                {mention.type === 'conversation'
                                                                    ? '@conversation'
                                                                    : mention.type === 'folder'
                                                                        ? '@folder'
                                                                        : '@file'}
                                                            </span>
                                                            <span className="message-reference-chip-value">
                                                                {mention.type === 'conversation' ? mention.name : mention.relativePath}
                                                            </span>
                                                        </span>
                                                    ))}
                                                </div>
                                            )}
                                            {message.content && <span>{message.content}</span>}
                                        </div>
                                    ) : (
                                        <>
                                            {message.isStreaming
                                            && !message.agentStatus?.text
                                            && (!message.content.trim() || isTransientPendingMessageLocal(message.content)) ? (
                                                <StreamingWaitLoader label={copy.assistantResponding} />
                                            ) : (
                                                message.content
                                                && !(message.agentStatus?.text && isTransientPendingMessageLocal(message.content))
                                                && !isImageMetadataOnlyMessage(message) && (
                                                    <MessageRenderer
                                                        content={message.content}
                                                        isStreaming={message.isStreaming}
                                                    />
                                                )
                                            )}
                                            {message.images && message.images.length > 0 && (
                                                <div className={`message-images message-images-assistant ${(!message.content || isImageMetadataOnlyMessage(message)) ? 'message-images-assistant-only' : ''}`}>
                                                    {message.images.map(img => (
                                                        <div key={img.id} className="message-image-item">
                                                            <ProtectedImage
                                                                source={img.base64_data}
                                                                alt={img.filename}
                                                                className="message-image-thumb message-image-thumb-assistant"
                                                                title={img.filename}
                                                                onImageClick={setLightboxImage}
                                                            />
                                                            <a
                                                                className="message-image-download"
                                                                href={resolveStoredAssetUrl(img.base64_data)}
                                                                download={(img.filename || 'image').trim() || 'image'}
                                                                title={`${isVietnamese ? 'Tải ảnh' : 'Download image'} ${(img.filename || '').trim()}`}
                                                            >
                                                                <Save size={12} />
                                                                <span>{isVietnamese ? 'Tải' : 'Download'}</span>
                                                            </a>
                                                        </div>
                                                    ))}
                                                </div>
                                            )}
                                            {message.media && message.media.length > 0 && (
                                                <div className={`message-generated-media-list message-generated-media-list-assistant ${!message.content ? 'message-generated-media-list-assistant-only' : ''}`}>
                                                    {message.media.map(item => (
                                                        <GeneratedMediaCard
                                                            key={item.id}
                                                            item={item}
                                                            previewLabel={item.kind === 'audio' ? copy.audioPreviewLabel : copy.videoPreviewLabel}
                                                            playLabel={item.kind === 'audio'
                                                                ? (isVietnamese ? 'Phát audio' : 'Play audio')
                                                                : (isVietnamese ? 'Phát video' : 'Play video')}
                                                            pauseLabel={item.kind === 'audio'
                                                                ? (isVietnamese ? 'Tạm dừng audio' : 'Pause audio')
                                                                : (isVietnamese ? 'Tạm dừng video' : 'Pause video')}
                                                            muteLabel={isVietnamese ? 'Tắt tiếng' : 'Mute'}
                                                            unmuteLabel={isVietnamese ? 'Bật tiếng' : 'Unmute'}
                                                            downloadLabel={isVietnamese ? 'Tải' : 'Download'}
                                                        />
                                                    ))}
                                                </div>
                                            )}
                                            {(message.webSearch || (message.citations && message.citations.length > 0)) && (
                                                <details className="message-search-details">
                                                    <summary className="message-search-summary">
                                                        <span>{copy.aiSearchDetails}</span>
                                                        <ChevronDown size={14} className="message-search-summary-icon" />
                                                    </summary>
                                                    <div className="message-search-details-content">
                                                        {message.webSearch && (
                                                            <div className={`message-web-search-status message-web-search-status-${message.webSearch.status}`}>
                                                                <div className="message-web-search-line">
                                                                    <span>
                                                                        {getWebSearchStatusLabelLocal(message.webSearch.status)}
                                                                        {message.webSearch.total_search_time_ms
                                                                            ? ` (${message.webSearch.total_search_time_ms}ms)`
                                                                            : ''}
                                                                        {message.webSearch.checked_at_utc
                                                                            ? ` • ${formatUiDateTime(message.webSearch.checked_at_utc)}`
                                                                            : ''}
                                                                    </span>
                                                                    {getWebSearchModeLabelLocal(message.webSearch.mode) && (
                                                                        <span className="message-web-search-badge">
                                                                            {getWebSearchModeLabelLocal(message.webSearch.mode)}
                                                                        </span>
                                                                    )}
                                                                </div>

                                                                {(message.webSearch.confidence_score !== undefined
                                                                    || message.webSearch.claims_verified_count !== undefined
                                                                    || message.webSearch.conflicts_count !== undefined) && (
                                                                        <div className="message-web-search-meta">
                                                                            {message.webSearch.confidence_score !== undefined && (
                                                                                <span>{copy.confidence}: {Math.round(message.webSearch.confidence_score * 100)}%</span>
                                                                            )}
                                                                            {message.webSearch.claims_verified_count !== undefined && (
                                                                                <span>{copy.claimsChecked}: {message.webSearch.claims_verified_count}</span>
                                                                            )}
                                                                            {message.webSearch.conflicts_count !== undefined && (
                                                                                <span>{copy.conflicts}: {message.webSearch.conflicts_count}</span>
                                                                            )}
                                                                        </div>
                                                                    )}

                                                                {message.webSearch.warnings && message.webSearch.warnings.length > 0 && (
                                                                    <div className="message-web-search-warnings">
                                                                        {message.webSearch.warnings.slice(0, 3).map((warning, warningIndex) => (
                                                                            <div key={`${message.id}-warn-${warningIndex}`}>• {warning}</div>
                                                                        ))}
                                                                    </div>
                                                                )}

                                                                {message.webSearch.claim_verification && message.webSearch.claim_verification.length > 0 && (
                                                                    <div className="message-claim-checks">
                                                                        {message.webSearch.claim_verification.slice(0, 3).map((item, claimIndex) => (
                                                                            <div
                                                                                key={`${message.id}-claim-${claimIndex}-${item.claim}`}
                                                                                className={`message-claim-check message-claim-check-${item.verdict}`}
                                                                                title={item.summary || item.claim}
                                                                            >
                                                                                <span className="message-claim-check-verdict">
                                                                                    {getClaimVerdictLabelLocal(item.verdict)}
                                                                                </span>
                                                                                <span className="message-claim-check-text">{item.claim}</span>
                                                                            </div>
                                                                        ))}
                                                                    </div>
                                                                )}
                                                            </div>
                                                        )}
                                                        {message.citations && message.citations.length > 0 && (
                                                            <div className="message-citations">
                                                                <span className="message-citations-title">{copy.sources}</span>
                                                                <div className="message-citations-list">
                                                                    {message.citations.map(citation => (
                                                                        <a
                                                                            key={`${message.id}-cite-${citation.index}-${citation.url}`}
                                                                            className="message-citation-link"
                                                                            href={citation.url}
                                                                            target="_blank"
                                                                            rel="noopener noreferrer"
                                                                            title={citation.title}
                                                                        >
                                                                            <span className="message-citation-title">[{citation.index}] {citation.title}</span>
                                                                            {(citation.domain || citation.published_at) && (
                                                                                <span className="message-citation-meta">
                                                                                    {citation.domain || copy.unknownDomain}
                                                                                    {citation.published_at ? ` • ${formatUiDateTime(citation.published_at)}` : ''}
                                                                                </span>
                                                                            )}
                                                                        </a>
                                                                    ))}
                                                                </div>
                                                            </div>
                                                        )}
                                                    </div>
                                                </details>
                                            )}
                                            {(() => {
                                                const learningDetails = buildLearningDetailState(message.learning)
                                                if (!learningDetails) return null

                                                return (
                                                    <details className="message-learning-details">
                                                        <summary className="message-learning-summary">
                                                            <span>{copy.learningDetails}</span>
                                                            {learningDetails.focusTitle && (
                                                                <span className="message-learning-summary-tag">
                                                                    {learningDetails.focusTitle}
                                                                </span>
                                                            )}
                                                            <ChevronDown size={14} className="message-learning-summary-icon" />
                                                        </summary>
                                                        <div className="message-learning-details-content">
                                                            {(learningDetails.goal || learningDetails.mode || learningDetails.nextStep) && (
                                                                <div className="message-learning-grid">
                                                                    {learningDetails.goal && (
                                                                        <div className="message-learning-field">
                                                                            <span className="message-learning-label">{copy.learningGoal}</span>
                                                                            <span className="message-learning-value">{learningDetails.goal}</span>
                                                                        </div>
                                                                    )}
                                                                    {learningDetails.mode && (
                                                                        <div className="message-learning-field">
                                                                            <span className="message-learning-label">{copy.learningMode}</span>
                                                                            <span className="message-learning-value">
                                                                                {getLearningModeLabelLocal(learningDetails.mode)}
                                                                            </span>
                                                                        </div>
                                                                    )}
                                                                    {learningDetails.nextStep && (
                                                                        <div className="message-learning-field">
                                                                            <span className="message-learning-label">{copy.learningNextStep}</span>
                                                                            <span className="message-learning-value">{learningDetails.nextStep}</span>
                                                                        </div>
                                                                    )}
                                                                </div>
                                                            )}

                                                            {learningDetails.checklist.length > 0 && (
                                                                <div className="message-learning-section">
                                                                    <span className="message-learning-section-title">{copy.learningChecklist}</span>
                                                                    <div className="message-learning-list">
                                                                        {learningDetails.checklist.map((item) => (
                                                                            <div
                                                                                key={`${message.id}-learning-check-${item.item_id}`}
                                                                                className="message-learning-check-item"
                                                                            >
                                                                                <span className={`message-learning-status message-learning-status-${getLearningChecklistTone(item.status)}`}>
                                                                                    {getLearningChecklistStatusLabel(item.status)}
                                                                                </span>
                                                                                <div className="message-learning-body">
                                                                                    <span className="message-learning-item-title">{item.label}</span>
                                                                                    {item.reason && (
                                                                                        <span className="message-learning-item-meta">{item.reason}</span>
                                                                                    )}
                                                                                </div>
                                                                            </div>
                                                                        ))}
                                                                    </div>
                                                                </div>
                                                            )}

                                                            {learningDetails.evidence.length > 0 && (
                                                                <div className="message-learning-section">
                                                                    <span className="message-learning-section-title">{copy.learningEvidence}</span>
                                                                    <div className="message-learning-list">
                                                                        {learningDetails.evidence.map((item, evidenceIndex) => (
                                                                            <div
                                                                                key={`${message.id}-learning-evidence-${evidenceIndex}`}
                                                                                className="message-learning-evidence-item"
                                                                            >
                                                                                {typeof item === 'string' ? (
                                                                                    <span className="message-learning-value">{item}</span>
                                                                                ) : (
                                                                                    <div className="message-learning-body">
                                                                                        <div className="message-learning-evidence-header">
                                                                                            <span className="message-learning-item-title">{item.summary}</span>
                                                                                            <span className="message-learning-evidence-strength">
                                                                                                {(item.strength || '').trim() || (isVietnamese ? 'Bang chung' : 'Evidence')}
                                                                                            </span>
                                                                                        </div>
                                                                                        {(item.type || item.timestamp) && (
                                                                                            <span className="message-learning-item-meta">
                                                                                                {item.type || (isVietnamese ? 'Cap nhat' : 'Updated')}
                                                                                                {item.timestamp ? ` • ${formatUiDateTime(item.timestamp)}` : ''}
                                                                                            </span>
                                                                                        )}
                                                                                    </div>
                                                                                )}
                                                                            </div>
                                                                        ))}
                                                                    </div>
                                                                </div>
                                                            )}

                                                            {learningDetails.sourceRefs.length > 0 && (
                                                                <div className="message-learning-section">
                                                                    <span className="message-learning-section-title">{copy.learningSources}</span>
                                                                    <div className="message-learning-list">
                                                                        {learningDetails.sourceRefs.map((item: string, sourceIndex: number) => (
                                                                            <div
                                                                                key={`${message.id}-learning-source-${sourceIndex}`}
                                                                                className="message-learning-evidence-item"
                                                                            >
                                                                                <span className="message-learning-value">{item}</span>
                                                                            </div>
                                                                        ))}
                                                                    </div>
                                                                </div>
                                                            )}

                                                            {learningDetails.memorySummary && (
                                                                <div className="message-learning-section">
                                                                    <span className="message-learning-section-title">{copy.learningMemory}</span>
                                                                    <div className="message-learning-memory">
                                                                        {learningDetails.memorySummary.added.length > 0 && (
                                                                            <div className="message-learning-memory-block">
                                                                                <span className="message-learning-memory-label">
                                                                                    {isVietnamese ? 'Them vao nho' : 'Added'}
                                                                                </span>
                                                                                <span className="message-learning-memory-value">
                                                                                    {learningDetails.memorySummary.added.join(' • ')}
                                                                                </span>
                                                                            </div>
                                                                        )}
                                                                        {learningDetails.memorySummary.revised.length > 0 && (
                                                                            <div className="message-learning-memory-block">
                                                                                <span className="message-learning-memory-label">
                                                                                    {isVietnamese ? 'Dieu chinh' : 'Revised'}
                                                                                </span>
                                                                                <span className="message-learning-memory-value">
                                                                                    {learningDetails.memorySummary.revised.join(' • ')}
                                                                                </span>
                                                                            </div>
                                                                        )}
                                                                        {learningDetails.memorySummary.downgraded.length > 0 && (
                                                                            <div className="message-learning-memory-block">
                                                                                <span className="message-learning-memory-label">
                                                                                    {isVietnamese ? 'Can xem lai' : 'Downgraded'}
                                                                                </span>
                                                                                <span className="message-learning-memory-value">
                                                                                    {learningDetails.memorySummary.downgraded.join(' • ')}
                                                                                </span>
                                                                            </div>
                                                                        )}
                                                                        {learningDetails.memorySummary.confidence > 0 && (
                                                                            <span className="message-learning-memory-confidence">
                                                                                {copy.confidence}: {Math.round(learningDetails.memorySummary.confidence * 100)}%
                                                                            </span>
                                                                        )}
                                                                    </div>
                                                                </div>
                                                            )}
                                                        </div>
                                                    </details>
                                                )
                                            })()}
                                        </>
                                    )}

                                    {/* Message Actions - assistant only */}
                                    {message.role === 'assistant' && !message.isStreaming && message.content && !isImageMetadataOnlyMessage(message) && (
                                        <div className="message-actions">
                                            <button
                                                className={`message-action-btn ${copiedMessageId === message.id ? 'active' : ''}`}
                                                title={copy.copy}
                                                onClick={() => handleCopyMessage(message.id, message.content)}
                                            >
                                                {copiedMessageId === message.id ? <Check size={12} /> : <Copy size={12} />}
                                            </button>
                                            <button className="message-action-btn" title={copy.helpful}>
                                                <ThumbsUp size={12} />
                                            </button>
                                            <button className="message-action-btn" title={copy.notHelpful}>
                                                <ThumbsDown size={12} />
                                            </button>
                                            <button
                                                className="message-action-btn"
                                                title={copy.regenerate}
                                            >
                                                <RotateCcw size={12} />
                                            </button>
                                        </div>
                                    )}
                                </div>
                            </motion.div>
                        ))}

                        {/* Scroll anchor */}
                        <div ref={messagesEndRef} />
                    </>
                )}
            </div>

            {/* Input Area */}
            <div
                className={`chat-input-area ${isCentered ? 'centered' : ''}`}
                onDragEnter={handleDragEnter}
                onDragLeave={handleDragLeave}
                onDragOver={handleDragOver}
                onDrop={handleDrop}
            >
                {/* Drag overlay */}
                <AnimatePresence>
                    {isDraggingImage && (
                        <motion.div
                            className="image-drop-overlay"
                            initial={{ opacity: 0 }}
                            animate={{ opacity: 1 }}
                            exit={{ opacity: 0 }}
                            transition={{ duration: 0.15 }}
                        >
                            <div className="image-drop-content">
                                <Image size={32} />
                                <span>{copy.dropImagesHere}</span>
                            </div>
                        </motion.div>
                    )}
                </AnimatePresence>

                <div className="chat-input-box" style={{ position: 'relative' }}>
                    {/* Hidden file input for image picker */}
                    <input
                        ref={imageInputRef}
                        type="file"
                        accept={ALLOWED_IMAGE_TYPES.join(',')}
                        multiple
                        onChange={handleImageInputChange}
                        style={{ display: 'none' }}
                    />
                    {/* Hidden file input for document picker */}
                    <input
                        ref={fileInputRef}
                        type="file"
                        accept={ALLOWED_FILE_EXTENSIONS.join(',')}
                        multiple
                        onChange={handleDocumentInputChange}
                        style={{ display: 'none' }}
                    />
                    {/* @Mention chips preview */}
                    {currentMentions.length > 0 && (
                        <div className="chat-mention-chips">
                            {currentMentions.map((m) => (
                                <span
                                    key={`${m.type}:${m.referenceId || m.relativePath}`}
                                    className={`chat-mention-chip chat-mention-chip-${m.type}`}
                                >
                                    {m.type === 'folder' ? '📁' : m.type === 'conversation' ? '💬' : '📄'} {m.type === 'conversation' ? m.name : m.relativePath}
                                    <button
                                        className="chat-mention-chip-remove"
                                        title={copy.removeMention}
                                        onClick={() => {
                                            const mentionKey = m.referenceId || m.relativePath
                                            setCurrentMentions(prev =>
                                                prev.filter(existing =>
                                                    !(
                                                        existing.type === m.type
                                                        && (existing.referenceId || existing.relativePath) === mentionKey
                                                    )
                                                )
                                            )
                                        }}
                                    >
                                        ×
                                    </button>
                                </span>
                            ))}
                        </div>
                    )}

                    {/* MentionPopup */}
                    <MentionPopup
                        isOpen={mention.popup.isOpen}
                        items={filteredMentionItems}
                        query={mention.popup.query}
                        activeIndex={mention.popup.activeIndex}
                        position={mention.popup.position}
                        onSelect={handleMentionSelect}
                        onHover={mention.setActiveIndex}
                        onClose={mention.closePopup}
                    />

                    {/* Image preview strip */}
                    {imageAttachments.length > 0 && (
                        <div className="chat-image-preview-strip">
                            {imageAttachments.map(img => (
                                <div key={img.id} className="chat-image-preview-item">
                                    <ProtectedImage
                                        source={img.base64_data}
                                        alt={img.filename}
                                        className="chat-image-preview-thumb"
                                    />
                                    <button
                                        className="chat-image-preview-remove"
                                        onClick={() => removeImageAttachment(img.id)}
                                        title={copy.removeImage}
                                    >
                                        ×
                                    </button>
                                    <span className="chat-image-preview-name">{img.filename}</span>
                                </div>
                            ))}
                        </div>
                    )}

                    {/* Document preview strip */}
                    {fileAttachments.length > 0 && (
                        <div className="chat-file-preview-strip">
                            {fileAttachments.map(file => (
                                <div key={file.id} className="chat-file-preview-item">
                                    <button
                                        className="chat-file-preview-remove"
                                        onClick={() => removeFileAttachment(file.id)}
                                        title={copy.removeFile}
                                    >
                                        ×
                                    </button>
                                    <div className="chat-file-preview-icon">
                                        <Paperclip size={14} />
                                    </div>
                                    <span className="chat-file-preview-name">{file.filename}</span>
                                    <span className="chat-file-preview-meta">
                                        {copy.filePreviewChars(Math.max(1, Math.round((file.text_chars || 0) / 1000)))}
                                    </span>
                                </div>
                            ))}
                        </div>
                    )}

                    {(isVoiceToolMode || isVideoToolMode) && (
                        <>
                            <div className={`media-studio-panel media-studio-panel-${selectedImageTool.id}`}>
                                <div className="media-studio-header">
                                    <div className="media-studio-title-wrap">
                                        <span className="media-studio-pill">{copy.studioPill}</span>
                                        <span className="media-studio-title">
                                            {isVoiceToolMode ? copy.voiceStudioTitle : copy.videoStudioTitle}
                                        </span>
                                    </div>
                                    <span className="media-studio-subtitle">
                                        {isVoiceToolMode ? copy.voiceStudioSubtitle : copy.videoStudioSubtitle}
                                    </span>
                                </div>

                                {isVoiceToolMode ? (
                                    <div className="media-studio-grid">
                                        <StudioComboboxField
                                            accent="voice"
                                            label={copy.studioFieldModel}
                                            value={voiceStudio.model}
                                            onChange={(value) => setVoiceStudio(prev => ({ ...prev, model: value }))}
                                            placeholder={defaultVoiceModelHint}
                                            options={voiceModelOptions}
                                            isOpen={openStudioMenu === 'voiceModel'}
                                            onToggle={() => toggleStudioMenu('voiceModel')}
                                            onOpen={() => openStudioMenuById('voiceModel')}
                                            onClose={closeStudioMenus}
                                            emptyText={isVietnamese ? 'Dùng model tùy chỉnh' : 'Use custom model'}
                                        />
                                        <StudioComboboxField
                                            accent="voice"
                                            label={copy.studioFieldVoice}
                                            value={voiceStudio.voice}
                                            onChange={(value) => setVoiceStudio(prev => ({ ...prev, voice: value }))}
                                            placeholder={voicePresetOptions[0]}
                                            options={voicePresetStudioOptions}
                                            isOpen={openStudioMenu === 'voiceVoice'}
                                            onToggle={() => toggleStudioMenu('voiceVoice')}
                                            onOpen={() => openStudioMenuById('voiceVoice')}
                                            onClose={closeStudioMenus}
                                            emptyText={isVietnamese ? 'Dùng giọng tùy chỉnh' : 'Use custom voice'}
                                        />
                                        <StudioSelectField
                                            accent="voice"
                                            label={copy.studioFieldFormat}
                                            value={voiceStudio.responseFormat}
                                            options={voiceFormatStudioOptions}
                                            isOpen={openStudioMenu === 'voiceFormat'}
                                            onToggle={() => toggleStudioMenu('voiceFormat')}
                                            onSelect={(value) => {
                                                setVoiceStudio(prev => ({
                                                    ...prev,
                                                    responseFormat: value as VoiceStudioState['responseFormat']
                                                }))
                                                closeStudioMenus()
                                            }}
                                        />
                                        <label className="media-studio-field">
                                            <span>{copy.studioFieldSpeed}</span>
                                            <input
                                                className="media-studio-input"
                                                type="number"
                                                min="0.25"
                                                max="2"
                                                step="0.05"
                                                value={voiceStudio.speed}
                                                onChange={(event) => setVoiceStudio(prev => ({ ...prev, speed: event.target.value }))}
                                            />
                                        </label>
                                    </div>
                                ) : (
                                    <div className="media-studio-grid">
                                        <StudioComboboxField
                                            accent="video"
                                            label={copy.studioFieldModel}
                                            value={videoStudio.model}
                                            onChange={(value) => setVideoStudio(prev => ({ ...prev, model: value }))}
                                            placeholder={defaultVideoModelHint}
                                            options={videoModelStudioOptions}
                                            isOpen={openStudioMenu === 'videoModel'}
                                            onToggle={() => toggleStudioMenu('videoModel')}
                                            onOpen={() => openStudioMenuById('videoModel')}
                                            onClose={closeStudioMenus}
                                            emptyText={isVietnamese ? 'Dùng model tùy chỉnh' : 'Use custom model'}
                                        />
                                        <StudioSelectField
                                            accent="video"
                                            label={copy.studioFieldAspectRatio}
                                            value={videoStudio.aspectRatio}
                                            options={videoAspectRatioStudioOptions}
                                            isOpen={openStudioMenu === 'videoAspectRatio'}
                                            onToggle={() => toggleStudioMenu('videoAspectRatio')}
                                            onSelect={(value) => {
                                                setVideoStudio(prev => ({
                                                    ...prev,
                                                    aspectRatio: value as VideoStudioState['aspectRatio']
                                                }))
                                                closeStudioMenus()
                                            }}
                                        />
                                        <StudioSelectField
                                            accent="video"
                                            label={copy.studioFieldDuration}
                                            value={videoStudio.duration}
                                            options={videoDurationStudioOptions}
                                            isOpen={openStudioMenu === 'videoDuration'}
                                            onToggle={() => toggleStudioMenu('videoDuration')}
                                            onSelect={(value) => {
                                                setVideoStudio(prev => ({
                                                    ...prev,
                                                    duration: value as VideoStudioState['duration']
                                                }))
                                                closeStudioMenus()
                                            }}
                                        />
                                        <StudioSelectField
                                            accent="video"
                                            label={copy.studioFieldQuality}
                                            value={videoStudio.quality}
                                            options={videoQualityStudioOptions}
                                            isOpen={openStudioMenu === 'videoQuality'}
                                            onToggle={() => toggleStudioMenu('videoQuality')}
                                            onSelect={(value) => {
                                                setVideoStudio(prev => ({
                                                    ...prev,
                                                    quality: value as VideoStudioState['quality']
                                                }))
                                                closeStudioMenus()
                                            }}
                                        />
                                        <StudioComboboxField
                                            accent="video"
                                            wide
                                            label={copy.studioFieldStyle}
                                            value={videoStudio.style}
                                            onChange={(value) => setVideoStudio(prev => ({ ...prev, style: value }))}
                                            placeholder={VIDEO_STYLE_PRESETS[0]}
                                            options={videoStyleStudioOptions}
                                            isOpen={openStudioMenu === 'videoStyle'}
                                            onToggle={() => toggleStudioMenu('videoStyle')}
                                            onOpen={() => openStudioMenuById('videoStyle')}
                                            onClose={closeStudioMenus}
                                            emptyText={isVietnamese ? 'Dùng style tùy chỉnh' : 'Use custom style'}
                                        />
                                    </div>
                                )}
                            </div>
                        </>
                    )}

                    {selectedMode.id === 'learn' && (
                        showLearningCockpit ? (
                            <div className="chat-learning-cockpit">
                                <div className="chat-learning-cockpit-header">
                                    <div className="chat-learning-cockpit-title-wrap">
                                        <span className="chat-learning-cockpit-label">{learningCockpitCopy.cockpitTitle}</span>
                                        {learningCockpitProgramTitle && (
                                            <span className="chat-learning-cockpit-program">{learningCockpitProgramTitle}</span>
                                        )}
                                    </div>
                                    {learningCockpitFocusTitle && (
                                        <span className="chat-learning-cockpit-focus">{learningCockpitFocusTitle}</span>
                                    )}
                                </div>

                                {isLearningLiveLoading && (
                                    <div className="chat-learning-cockpit-note">{learningCockpitCopy.loading}</div>
                                )}

                                <div className="chat-learning-cockpit-stats">
                                    <div className="chat-learning-cockpit-stat">
                                        <span>{learningCockpitCopy.deadline}</span>
                                        <strong>
                                            {getLearningDeadlineStatusLabelLocal(learningCockpitAdaptive?.deadline_status)}
                                            {typeof learningCockpitAdaptive?.days_left === 'number' ? ` • ${learningCockpitAdaptive.days_left}d` : ''}
                                        </strong>
                                    </div>
                                    <div className="chat-learning-cockpit-stat">
                                        <span>{learningCockpitCopy.reviewLoad}</span>
                                        <strong>{getLearningReviewPressureLabelLocal(learningCockpitReview?.review_pressure || learningCockpitAdaptive?.review_pressure)}</strong>
                                    </div>
                                    <div className="chat-learning-cockpit-stat">
                                        <span>{learningCockpitCopy.remaining}</span>
                                        <strong>{learningCockpitAdaptive?.remaining_nodes ?? '—'}</strong>
                                    </div>
                                    <div className="chat-learning-cockpit-stat">
                                        <span>{learningCockpitCopy.dueNow}</span>
                                        <strong>{learningCockpitReview?.due_now ?? learningCockpitAdaptive?.due_now ?? 0}</strong>
                                    </div>
                                    <div className="chat-learning-cockpit-stat">
                                        <span>{learningCockpitCopy.sessionsPerWeek}</span>
                                        <strong>{learningCockpitAdaptive?.recommended_sessions_per_week ?? '—'}</strong>
                                    </div>
                                    <div className="chat-learning-cockpit-stat">
                                        <span>{learningCockpitCopy.minutesPerSession}</span>
                                        <strong>{learningCockpitAdaptive?.recommended_minutes_per_session ?? '—'}</strong>
                                    </div>
                                </div>

                                {(learningCockpitGoal || learningCockpitNextAction || learningCockpitSnapshot?.summary) && (
                                    <div className="chat-learning-cockpit-grid">
                                        {learningCockpitGoal && (
                                            <div className="chat-learning-cockpit-field">
                                                <span>{learningCockpitCopy.goal}</span>
                                                <strong>{learningCockpitGoal}</strong>
                                            </div>
                                        )}
                                        {learningCockpitSnapshot?.summary && (
                                            <div className="chat-learning-cockpit-field">
                                                <span>{learningCockpitCopy.focus}</span>
                                                <strong>{learningCockpitSnapshot.summary}</strong>
                                            </div>
                                        )}
                                        {learningCockpitNextAction && (
                                            <div className="chat-learning-cockpit-field">
                                                <span>{learningCockpitCopy.nextAction}</span>
                                                <strong>{learningCockpitNextAction}</strong>
                                            </div>
                                        )}
                                    </div>
                                )}

                                {(learningCockpitMisconceptions.length > 0 || learningCockpitSuccessSignals.length > 0 || learningCockpitSources.length > 0 || (learningCockpitAdaptive?.stalled_nodes?.length || 0) > 0) && (
                                    <div className="chat-learning-cockpit-columns">
                                        {learningCockpitMisconceptions.length > 0 && (
                                            <div className="chat-learning-cockpit-section">
                                                <span>{learningCockpitCopy.misconceptions}</span>
                                                <div className="chat-learning-cockpit-list">
                                                    {learningCockpitMisconceptions.map((item: string, index: number) => (
                                                        <div key={`learning-mis-${index}`} className="chat-learning-cockpit-chip">
                                                            {item}
                                                        </div>
                                                    ))}
                                                </div>
                                            </div>
                                        )}

                                        {learningCockpitSuccessSignals.length > 0 && (
                                            <div className="chat-learning-cockpit-section">
                                                <span>{learningCockpitCopy.successSignals}</span>
                                                <div className="chat-learning-cockpit-list">
                                                    {learningCockpitSuccessSignals.map((item: string, index: number) => (
                                                        <div key={`learning-success-${index}`} className="chat-learning-cockpit-chip chat-learning-cockpit-chip-success">
                                                            {item}
                                                        </div>
                                                    ))}
                                                </div>
                                            </div>
                                        )}

                                        {(learningCockpitAdaptive?.stalled_nodes?.length || 0) > 0 && (
                                            <div className="chat-learning-cockpit-section">
                                                <span>{learningCockpitCopy.stalled}</span>
                                                <div className="chat-learning-cockpit-list">
                                                    {(learningCockpitAdaptive?.stalled_nodes || []).slice(0, 2).map((item: NonNullable<NonNullable<LearningState['adaptive_plan']>['stalled_nodes']>[number]) => (
                                                        <div key={item.node_id} className="chat-learning-cockpit-chip chat-learning-cockpit-chip-warning">
                                                            {item.title} • {item.failures}
                                                        </div>
                                                    ))}
                                                </div>
                                            </div>
                                        )}

                                        {learningCockpitSources.length > 0 && (
                                            <div className="chat-learning-cockpit-section">
                                                <span>{learningCockpitCopy.sources}</span>
                                                <div className="chat-learning-cockpit-sources">
                                                    {learningCockpitSources.map((source: LearningState['source_registry']['sources'][number]) => (
                                                        <div key={source.source_id} className="chat-learning-cockpit-source">
                                                            <strong>{source.file_name}</strong>
                                                            {source.excerpt && (
                                                                <span>{source.excerpt}</span>
                                                            )}
                                                        </div>
                                                    ))}
                                                </div>
                                            </div>
                                        )}
                                    </div>
                                )}
                            </div>
                        ) : (
                            <div className="chat-learning-focus">
                                <span className="chat-learning-focus-label">
                                    {isVietnamese ? 'PigTex Learn' : 'PigTex Learn'}
                                </span>
                                <span className="chat-learning-cockpit-note">{learningCockpitCopy.noData}</span>
                            </div>
                        )
                    )}

                    {/* Textarea */}
                    <textarea
                        ref={textareaRef}
                        className="chat-textarea"
                        placeholder={chatInputPlaceholder}
                        value={inputValue}
                        onPaste={handlePaste}
                        onChange={(e) => {
                            setInputValue(e.target.value)
                            // Trigger mention detection
                            mention.handleInputChange(
                                e.target.value,
                                e.target.selectionStart || 0
                            )
                        }}
                        onKeyDown={(e) => {
                            // Let mention popup handle keys first
                            if (mention.popup.isOpen) {
                                const handled = mention.handleKeyDown(
                                    e,
                                    filteredMentionItems,
                                    handleMentionSelect
                                )
                                if (handled) return
                            }

                            if (e.key === 'Enter' && !e.shiftKey) {
                                e.preventDefault()
                                handleSend()
                            }
                        }}
                        rows={1}
                        disabled={isInputLocked}
                    />

                    {/* Controls row */}
                    <div className="input-controls">
                        {showComposerAttachmentControls && (
                            <div className="dropdown-container" onClick={(e) => e.stopPropagation()}>
                                <button
                                    className="input-btn add-btn"
                                    onClick={() => toggleComposerMenu('add')}
                                    title={copy.addAttachments}
                                >
                                    <Plus size={18} />
                                </button>
                                <AnimatePresence>
                                    {showAddMenu && (
                                        <motion.div
                                            className="dropdown-menu"
                                            initial={{ opacity: 0, y: 8 }}
                                            animate={{ opacity: 1, y: 0 }}
                                            exit={{ opacity: 0, y: 8 }}
                                        >
                                            {attachOptionsLocal.map(opt => (
                                                <button
                                                    key={opt.id}
                                                    className={`dropdown-item ${opt.id === 'web' && webSearchEnabled ? 'active' : ''}`}
                                                    onClick={() => {
                                                        closeComposerMenus()
                                                        if (opt.id === 'image') {
                                                            handleImageFileSelect()
                                                        } else if (opt.id === 'file') {
                                                            handleDocumentFileSelect()
                                                        } else if (opt.id === 'web') {
                                                            setWebSearchEnabled(prev => {
                                                                const next = !prev
                                                                showInfo(
                                                                    next
                                                                        ? copy.webSearchForcedTurn
                                                                        : copy.webSearchAutoTurn
                                                                )
                                                                return next
                                                            })
                                                        } else if (opt.id === 'code') {
                                                            void handlePasteCodeSnippet()
                                                        }
                                                    }}
                                                >
                                                    <opt.icon size={14} />
                                                    <span>
                                                        {opt.id === 'web'
                                                            ? (webSearchEnabled
                                                                ? copy.webSearchForced
                                                                : copy.webSearchAuto)
                                                            : opt.label}
                                                    </span>
                                                </button>
                                            ))}
                                        </motion.div>
                                    )}
                                </AnimatePresence>
                            </div>
                        )}

                        {/* Image tool selector */}
                        <div className="dropdown-container" onClick={(e) => e.stopPropagation()}>
                            <button
                                className={`selector-btn image-tool-btn ${selectedImageTool.id !== 'chat' ? 'active' : ''}`}
                                onClick={() => toggleComposerMenu('imageTool')}
                                title={copy.chooseImageWorkflow}
                            >
                                {selectedImageTool.id === 'chat'
                                    ? <Sparkles size={12} />
                                    : selectedImageTool.id === 'voice'
                                    ? <FileText size={12} />
                                    : selectedImageTool.id === 'video'
                                        ? <Globe size={12} />
                                        : <Image size={12} />}
                                <span>{selectedImageToolOption.label}</span>
                                <ChevronDown size={14} />
                            </button>
                            <AnimatePresence>
                                {showImageToolMenu && (
                                    <motion.div
                                        className="dropdown-menu"
                                        initial={{ opacity: 0, y: 8 }}
                                        animate={{ opacity: 1, y: 0 }}
                                        exit={{ opacity: 0, y: 8 }}
                                    >
                                        {availableImageToolModes.map(mode => (
                                            <button
                                                key={mode.id}
                                                className={`dropdown-item ${selectedImageTool.id === mode.id ? 'active' : ''}`}
                                                onClick={() => {
                                                    setSelectedImageTool(mode)
                                                    closeComposerMenus()
                                                }}
                                            >
                                                <span>{mode.label}</span>
                                            </button>
                                        ))}
                                    </motion.div>
                                )}
                            </AnimatePresence>
                        </div>

                        {isChatMode && (
                            <>
                                {/* Mode selector */}
                                <div className="dropdown-container" onClick={(e) => e.stopPropagation()}>
                                    <button
                                        className="selector-btn"
                                        title={isVietnamese ? 'Chọn chế độ chat hoặc học' : 'Choose chat or learning mode'}
                                        onClick={() => toggleComposerMenu('mode')}
                                    >
                                        <span>{selectedModeButtonLabel}</span>
                                        <ChevronDown size={14} />
                                    </button>
                                    <AnimatePresence>
                                        {showModeMenu && (
                                            <motion.div
                                                className="dropdown-menu"
                                                initial={{ opacity: 0, y: 8 }}
                                                animate={{ opacity: 1, y: 0 }}
                                                exit={{ opacity: 0, y: 8 }}
                                            >
                                                {conversationModesLocal.map(mode => (
                                                    <button
                                                        key={mode.id}
                                                        className={`dropdown-item ${selectedMode.id === mode.id ? 'active' : ''}`}
                                                        onClick={() => {
                                                            setSelectedMode(mode)
                                                            closeComposerMenus()
                                                        }}
                                                    >
                                                        <div className="dropdown-item-text">
                                                            <span className="dropdown-item-main">{mode.label}</span>
                                                            <span className="dropdown-item-sub">{mode.description}</span>
                                                        </div>
                                                    </button>
                                                ))}
                                            </motion.div>
                                        )}
                                    </AnimatePresence>
                                </div>

                                <button
                                    className={`selector-btn fs-mode-btn ${aiFileModeEnabled && localRootPath ? 'active' : ''}`}
                                    disabled={!localRootPath}
                                    title={
                                        localRootPath
                                            ? copy.toggleAiFiles
                                            : copy.openFolderEnableAiFiles
                                    }
                                    onClick={() => setAiFileModeEnabled(prev => !prev)}
                                >
                                    <Wrench size={12} />
                                    <span>{aiFileModeEnabled && localRootPath ? copy.aiFilesOn : copy.aiFilesOff}</span>
                                </button>
                            </>
                        )}

                        {(isChatMode || isImageToolMode) && (
                            <div className="dropdown-container" onClick={(e) => e.stopPropagation()}>
                                <button
                                    className="selector-btn model-selector"
                                    onClick={() => toggleComposerMenu('model')}
                                    title={getDisplayModelFlagSummary(selectedModel) || undefined}
                                >
                                    <span className="model-selector-label">{selectedModel.label || copy.selectModel}</span>
                                    {renderModelBadges(selectedModel.badges)}
                                    <ChevronDown size={14} />
                                </button>
                                <AnimatePresence>
                                    {showModelMenu && (
                                        <motion.div
                                            className="dropdown-menu model-menu"
                                            initial={{ opacity: 0, y: 8 }}
                                            animate={{ opacity: 1, y: 0 }}
                                            exit={{ opacity: 0, y: 8 }}
                                        >
                                            {!showAllModelsInMenu && hasHiddenModels && (
                                                <div className="model-menu-section-label">
                                                    {copy.suggestedModels}
                                                </div>
                                            )}
                                            {visibleModelMenuOptions.map(model => (
                                                <button
                                                    key={model.id}
                                                    className={`dropdown-item ${selectedModel.id === model.id ? 'active' : ''} ${model.disabled ? 'dropdown-item-disabled' : ''}`}
                                                    disabled={model.disabled}
                                                    title={getDisplayModelFlagSummary(model) || undefined}
                                                    onClick={() => {
                                                        if (model.disabled) return
                                                        setSelectedModel(model)
                                                        onSettingsChange({ model: model.id })
                                                        closeComposerMenus()
                                                    }}
                                                >
                                                    <span className="dropdown-item-main">{model.label}</span>
                                                    {renderModelBadges(model.badges)}
                                                </button>
                                            ))}
                                            {hasHiddenModels && (
                                                <>
                                                    <div className="model-menu-divider" />
                                                    <button
                                                        className="model-menu-toggle"
                                                        onClick={() => {
                                                            setShowAllModelsInMenu(prev => !prev)
                                                        }}
                                                    >
                                                        {showAllModelsInMenu ? copy.showLessModels : copy.viewAllModels}
                                                    </button>
                                                </>
                                            )}
                                        </motion.div>
                                    )}
                                </AnimatePresence>
                            </div>
                        )}

                        {!isChatMode && !isImageToolMode && (
                            <span className="composer-studio-hint">{copy.studioPill}</span>
                        )}

                        {/* Spacer */}
                        <div className="controls-spacer" />

                        {/* Stop / Send button */}
                        {isInputLocked ? (
                            <motion.button
                                className="stop-btn"
                                whileHover={{ scale: 1.05 }}
                                whileTap={{ scale: 0.95 }}
                                onClick={handleStopGeneration}
                                title={copy.stopGenerating}
                            >
                                <Square size={14} />
                                <span>{copy.stop}</span>
                            </motion.button>
                        ) : (
                            <motion.button
                                className={`send-btn ${canSend ? 'active' : ''}`}
                                whileHover={{ scale: 1.05 }}
                                whileTap={{ scale: 0.95 }}
                                disabled={!canSend}
                                onClick={handleSend}
                                title={copy.send}
                            >
                                <Send size={16} />
                            </motion.button>
                        )}
                    </div>
                </div>

                {isCentered && (
                    <div className="input-hint">
                        {copy.inputHint}
                    </div>
                )}
            </div>

            <AnimatePresence>
                {aiActionConfirmDialog.isOpen && (
                    <motion.div
                        className="chat-confirm-overlay"
                        initial={{ opacity: 0 }}
                        animate={{ opacity: 1 }}
                        exit={{ opacity: 0 }}
                        transition={{ duration: 0.15 }}
                        onClick={() => closeAiActionConfirmDialog(false)}
                    >
                        <motion.div
                            className="chat-confirm-modal"
                            initial={{ opacity: 0, scale: 0.96, y: -16 }}
                            animate={{ opacity: 1, scale: 1, y: 0 }}
                            exit={{ opacity: 0, scale: 0.96, y: -10 }}
                            transition={{ duration: 0.2, ease: [0.23, 1, 0.32, 1] }}
                            onClick={(event) => event.stopPropagation()}
                        >
                            <div className="chat-confirm-header">
                                <div className="chat-confirm-icon">
                                    <Wrench size={16} />
                                </div>
                                <div className="chat-confirm-header-text">
                                    <h3 className="chat-confirm-title">{copy.confirmAiActions}</h3>
                                    <p className="chat-confirm-subtitle">
                                        {copy.workspaceOperationsProposed(aiActionConfirmDialog.actions.length)}
                                    </p>
                                    {localRootPath && (
                                        <p className="chat-confirm-root">{localRootPath}</p>
                                    )}
                                </div>
                            </div>
                            <div className="chat-confirm-list">
                                {aiActionConfirmDialog.actions.map((action, index) => {
                                    const actionKey = getAiActionKey(action, index)
                                    const absolutePathPreview = formatActionAbsolutePath(action)
                                    const diffPreview = aiActionDiffPreviews[actionKey]
                                    const isWriteAction = action.type === 'write_file'
                                    const isDiffExpanded = !!expandedActionDiffs[actionKey]
                                    return (
                                        <div
                                            key={actionKey}
                                            className={`chat-confirm-item ${getActionColor(action.type)}`}
                                        >
                                            <span className="chat-confirm-item-icon">
                                                {getActionIcon(action.type)}
                                            </span>
                                            <span className="chat-confirm-item-text">
                                                <span className="chat-confirm-item-primary">{formatActionPreview(action)}</span>
                                                {absolutePathPreview && (
                                                    <span className="chat-confirm-item-secondary">
                                                        {absolutePathPreview}
                                                    </span>
                                                )}
                                                {isWriteAction && (
                                                    <div className="chat-confirm-diff">
                                                        {diffPreview?.status === 'loading' && (
                                                            <span className="chat-confirm-diff-meta">{copy.preparingDiffPreview}</span>
                                                        )}
                                                        {diffPreview?.status === 'error' && (
                                                            <span className="chat-confirm-diff-error">
                                                                {copy.diffUnavailable(diffPreview.message || 'Unknown error')}
                                                            </span>
                                                        )}
                                                        {diffPreview?.status === 'ready' && (
                                                            <>
                                                                <button
                                                                    className="chat-confirm-diff-toggle"
                                                                    onClick={() => toggleActionDiff(actionKey)}
                                                                >
                                                                    {isDiffExpanded ? copy.hideDiff : copy.showDiff}
                                                                </button>
                                                                {isDiffExpanded && (
                                                                    <pre className="chat-confirm-diff-code">
                                                                        {diffPreview.diffText}
                                                                    </pre>
                                                                )}
                                                            </>
                                                        )}
                                                    </div>
                                                )}
                                            </span>
                                        </div>
                                    )
                                })}
                            </div>
                            <div className="chat-confirm-actions">
                                <div className="chat-confirm-hint">
                                    <kbd>Esc</kbd> <span>{copy.cancelHint}</span>
                                </div>
                                <button
                                    className="chat-confirm-btn"
                                    onClick={() => closeAiActionConfirmDialog(false)}
                                >
                                    {copy.cancel}
                                </button>
                                <button
                                    className="chat-confirm-btn chat-confirm-btn-primary"
                                    disabled={hasPendingDiffPreview}
                                    onClick={() => closeAiActionConfirmDialog(true)}
                                >
                                    {hasPendingDiffPreview
                                        ? copy.preparingDiff
                                        : (aiActionConfirmDialog.actions.length > 1
                                            ? copy.applyActions(aiActionConfirmDialog.actions.length)
                                            : copy.applyAction)}
                                </button>
                            </div>
                        </motion.div>
                    </motion.div>
                )}
            </AnimatePresence>

            {/* Image Lightbox */}
            <AnimatePresence>
                {lightboxImage && (
                    <motion.div
                        className="image-lightbox-overlay"
                        initial={{ opacity: 0 }}
                        animate={{ opacity: 1 }}
                        exit={{ opacity: 0 }}
                        onClick={() => setLightboxImage(null)}
                        onKeyDown={(e) => { if (e.key === 'Escape') setLightboxImage(null) }}
                        tabIndex={0}
                        role="dialog"
                    >
                        <motion.img
                            src={lightboxImage}
                            className="image-lightbox-img"
                            initial={{ scale: 0.8, opacity: 0 }}
                            animate={{ scale: 1, opacity: 1 }}
                            exit={{ scale: 0.8, opacity: 0 }}
                            transition={{ type: 'spring', stiffness: 300, damping: 25 }}
                            onClick={(e) => e.stopPropagation()}
                            alt={copy.fullSizePreview}
                        />
                        <button
                            className="image-lightbox-close"
                            onClick={() => setLightboxImage(null)}
                            title={copy.closeEsc}
                        >
                            ×
                        </button>
                    </motion.div>
                )}
            </AnimatePresence>
        </div>
    )
}

export default ChatPanel

