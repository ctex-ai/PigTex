import { useEffect, useState, type ImgHTMLAttributes } from 'react'
import { resolveProtectedImageSrc } from '../../services/api'

interface ProtectedImageProps extends Omit<ImgHTMLAttributes<HTMLImageElement>, 'src' | 'onClick'> {
    source: string
    onImageClick?: (resolvedSrc: string) => void
}

function ProtectedImage({ source, onImageClick, ...imgProps }: ProtectedImageProps) {
    const [resolvedSrc, setResolvedSrc] = useState('')
    const [hasError, setHasError] = useState(false)

    useEffect(() => {
        let isActive = true
        let revokeObjectUrl: (() => void) | undefined

        setResolvedSrc('')
        setHasError(false)

        void resolveProtectedImageSrc(source)
            .then(({ src, revoke }) => {
                if (!isActive) {
                    revoke?.()
                    return
                }
                revokeObjectUrl = revoke
                setResolvedSrc(src)
            })
            .catch(() => {
                if (isActive) {
                    setHasError(true)
                }
            })

        return () => {
            isActive = false
            revokeObjectUrl?.()
        }
    }, [source])

    if (!source || hasError || !resolvedSrc) {
        return null
    }

    return (
        <img
            {...imgProps}
            src={resolvedSrc}
            onClick={onImageClick ? () => onImageClick(resolvedSrc) : undefined}
        />
    )
}

export default ProtectedImage
