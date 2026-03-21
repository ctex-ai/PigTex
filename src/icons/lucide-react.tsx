import React from 'react'
import type { Icon, IconProps } from '@phosphor-icons/react'
import {
    WarningCircle as PhWarningCircle,
    ArrowLeft as PhArrowLeft,
    ArrowRight as PhArrowRight,
    ArrowsLeftRight as PhArrowsLeftRight,
    Briefcase as PhBriefcase,
    TextB as PhTextB,
    BookOpen as PhBookOpen,
    Brain as PhBrain,
    Check as PhCheck,
    CaretDown as PhCaretDown,
    CaretLeft as PhCaretLeft,
    CaretRight as PhCaretRight,
    Clock as PhClock,
    Code as PhCode,
    Copy as PhCopy,
    Eye as PhEye,
    EyeSlash as PhEyeSlash,
    ArrowSquareOut as PhArrowSquareOut,
    NotePencil as PhNotePencil,
    FilePlus as PhFilePlus,
    FileText as PhFileText,
    Folder as PhFolder,
    FolderOpen as PhFolderOpen,
    FolderPlus as PhFolderPlus,
    GithubLogo as PhGithubLogo,
    Globe as PhGlobe,
    HardDrive as PhHardDrive,
    Hash as PhHash,
    Image as PhImage,
    TextItalic as PhTextItalic,
    Lightbulb as PhLightbulb,
    Link as PhLink,
    List as PhList,
    ListNumbers as PhListNumbers,
    Spinner as PhSpinner,
    Lock as PhLock,
    SignOut as PhSignOut,
    EnvelopeSimple as PhEnvelopeSimple,
    ChatCircleText as PhChatCircleText,
    Minus as PhMinus,
    Moon as PhMoon,
    DotsThree as PhDotsThree,
    Pencil as PhPencil,
    Paperclip as PhPaperclip,
    Plus as PhPlus,
    Plug as PhPlug,
    Quotes as PhQuotes,
    ArrowsClockwise as PhArrowsClockwise,
    Rocket as PhRocket,
    ArrowsCounterClockwise as PhArrowsCounterClockwise,
    FloppyDisk as PhFloppyDisk,
    MagnifyingGlass as PhMagnifyingGlass,
    PaperPlaneRight as PhPaperPlaneRight,
    GearSix as PhGearSix,
    Share as PhShare,
    Shield as PhShield,
    Sliders as PhSliders,
    Sparkle as PhSparkle,
    Square as PhSquare,
    Star as PhStar,
    Sun as PhSun,
    Terminal as PhTerminal,
    ThumbsDown as PhThumbsDown,
    ThumbsUp as PhThumbsUp,
    Trash as PhTrash,
    TextUnderline as PhTextUnderline,
    ArrowUUpLeft as PhArrowUUpLeft,
    User as PhUser,
    Wrench as PhWrench,
    X as PhX
} from '@phosphor-icons/react'

type LucideProps = IconProps & {
    strokeWidth?: number
    absoluteStrokeWidth?: boolean
}

export type LucideIcon = React.ForwardRefExoticComponent<
    React.PropsWithoutRef<LucideProps> & React.RefAttributes<SVGSVGElement>
>

const asLucideIcon = (IconComponent: Icon): LucideIcon => {
    const WrappedIcon = React.forwardRef<SVGSVGElement, LucideProps>(
        ({ strokeWidth: _strokeWidth, absoluteStrokeWidth: _absoluteStrokeWidth, ...props }, ref) => (
            <IconComponent ref={ref} {...props} />
        )
    )
    WrappedIcon.displayName = `LucideCompat(${IconComponent.displayName || 'Icon'})`
    return WrappedIcon
}

export const AlertCircle = asLucideIcon(PhWarningCircle)
export const ArrowLeft = asLucideIcon(PhArrowLeft)
export const ArrowRight = asLucideIcon(PhArrowRight)
export const ArrowRightLeft = asLucideIcon(PhArrowsLeftRight)
export const Briefcase = asLucideIcon(PhBriefcase)
export const Bold = asLucideIcon(PhTextB)
export const BookOpen = asLucideIcon(PhBookOpen)
export const Brain = asLucideIcon(PhBrain)
export const Check = asLucideIcon(PhCheck)
export const ChevronDown = asLucideIcon(PhCaretDown)
export const ChevronLeft = asLucideIcon(PhCaretLeft)
export const ChevronRight = asLucideIcon(PhCaretRight)
export const Clock = asLucideIcon(PhClock)
export const Code = asLucideIcon(PhCode)
export const Copy = asLucideIcon(PhCopy)
export const Eye = asLucideIcon(PhEye)
export const EyeOff = asLucideIcon(PhEyeSlash)
export const ExternalLink = asLucideIcon(PhArrowSquareOut)
export const FileEdit = asLucideIcon(PhNotePencil)
export const FilePlus2 = asLucideIcon(PhFilePlus)
export const FileText = asLucideIcon(PhFileText)
export const Folder = asLucideIcon(PhFolder)
export const FolderOpen = asLucideIcon(PhFolderOpen)
export const FolderPlus = asLucideIcon(PhFolderPlus)
export const Github = asLucideIcon(PhGithubLogo)
export const Globe = asLucideIcon(PhGlobe)
export const HardDrive = asLucideIcon(PhHardDrive)
export const Hash = asLucideIcon(PhHash)
export const Image = asLucideIcon(PhImage)
export const Italic = asLucideIcon(PhTextItalic)
export const Lightbulb = asLucideIcon(PhLightbulb)
export const Link = asLucideIcon(PhLink)
export const List = asLucideIcon(PhList)
export const ListOrdered = asLucideIcon(PhListNumbers)
export const Loader2 = asLucideIcon(PhSpinner)
export const Lock = asLucideIcon(PhLock)
export const LogOut = asLucideIcon(PhSignOut)
export const Mail = asLucideIcon(PhEnvelopeSimple)
export const MessageSquare = asLucideIcon(PhChatCircleText)
export const Minus = asLucideIcon(PhMinus)
export const Moon = asLucideIcon(PhMoon)
export const MoreHorizontal = asLucideIcon(PhDotsThree)
export const Pencil = asLucideIcon(PhPencil)
export const Paperclip = asLucideIcon(PhPaperclip)
export const Plus = asLucideIcon(PhPlus)
export const Plug = asLucideIcon(PhPlug)
export const Quote = asLucideIcon(PhQuotes)
export const RefreshCw = asLucideIcon(PhArrowsClockwise)
export const Rocket = asLucideIcon(PhRocket)
export const RotateCcw = asLucideIcon(PhArrowsCounterClockwise)
export const Save = asLucideIcon(PhFloppyDisk)
export const Search = asLucideIcon(PhMagnifyingGlass)
export const Send = asLucideIcon(PhPaperPlaneRight)
export const Settings = asLucideIcon(PhGearSix)
export const Share = asLucideIcon(PhShare)
export const Shield = asLucideIcon(PhShield)
export const Sliders = asLucideIcon(PhSliders)
export const Sparkles = asLucideIcon(PhSparkle)
export const Square = asLucideIcon(PhSquare)
export const Star = asLucideIcon(PhStar)
export const Sun = asLucideIcon(PhSun)
export const Terminal = asLucideIcon(PhTerminal)
export const ThumbsDown = asLucideIcon(PhThumbsDown)
export const ThumbsUp = asLucideIcon(PhThumbsUp)
export const Trash2 = asLucideIcon(PhTrash)
export const Underline = asLucideIcon(PhTextUnderline)
export const Undo2 = asLucideIcon(PhArrowUUpLeft)
export const User = asLucideIcon(PhUser)
export const Wrench = asLucideIcon(PhWrench)
export const X = asLucideIcon(PhX)
