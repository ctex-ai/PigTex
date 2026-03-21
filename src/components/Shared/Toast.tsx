import toast, { Toaster, ToastPosition } from 'react-hot-toast'
import './Toast.css'

/** Toast configuration */
const TOAST_CONFIG = {
    position: 'bottom-right' as ToastPosition,
    duration: 3000,
}

/** Show success toast */
export const showSuccess = (message: string) => {
    toast.success(message, {
        duration: TOAST_CONFIG.duration,
        icon: null,
        className: 'pigtex-toast pigtex-toast-success',
    })
}

/** Show error toast */
export const showError = (message: string) => {
    toast.error(message, {
        duration: 4000,
        icon: null,
        className: 'pigtex-toast pigtex-toast-error',
    })
}

/** Show info toast */
export const showInfo = (message: string) => {
    toast(message, {
        duration: TOAST_CONFIG.duration,
        icon: null,
        className: 'pigtex-toast pigtex-toast-info',
    })
}

/** Copy text to clipboard with toast */
export const copyToClipboard = async (text: string, label: string = 'Copied to clipboard') => {
    try {
        await navigator.clipboard.writeText(text)
        showSuccess(label)
    } catch {
        // Fallback
        const textarea = document.createElement('textarea')
        textarea.value = text
        document.body.appendChild(textarea)
        textarea.select()
        document.execCommand('copy')
        document.body.removeChild(textarea)
        showSuccess(label)
    }
}

/** Toast Provider - Place in App root */
export const ToastProvider = () => (
    <Toaster
        position={TOAST_CONFIG.position}
        toastOptions={{
            className: 'pigtex-toast',
            style: {
                background: 'transparent',
                boxShadow: 'none',
                padding: 0,
            },
        }}
        containerStyle={{
            zIndex: 99999,
        }}
    />
)

export default { showSuccess, showError, showInfo, copyToClipboard, ToastProvider }
