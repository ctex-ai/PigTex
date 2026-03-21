import { createContext, useCallback, useContext, useEffect, useMemo, useState } from 'react'

import {
    type AppLanguage,
    DEFAULT_PIGTEX_SETTINGS,
    getPigTexSettings,
    PIGTEX_SETTINGS_CHANGED_EVENT,
    type PigTexSettings,
    updatePigTexSettings,
} from '../services/settings'

interface I18nContextValue {
    language: AppLanguage
    locale: string
    isVietnamese: boolean
    setLanguage: (language: AppLanguage) => void
}

const getLocaleForLanguage = (language: AppLanguage) => (
    language === 'vi' ? 'vi-VN' : 'en-US'
)

const defaultContextValue: I18nContextValue = {
    language: DEFAULT_PIGTEX_SETTINGS.language,
    locale: getLocaleForLanguage(DEFAULT_PIGTEX_SETTINGS.language),
    isVietnamese: DEFAULT_PIGTEX_SETTINGS.language === 'vi',
    setLanguage: () => undefined
}

const I18nContext = createContext<I18nContextValue>(defaultContextValue)

export const I18nProvider = ({ children }: { children: React.ReactNode }) => {
    const [language, setLanguageState] = useState<AppLanguage>(() => getPigTexSettings().language)

    useEffect(() => {
        const handleSettingsChanged = (event: Event) => {
            const detail = (event as CustomEvent<PigTexSettings>).detail
            setLanguageState(detail?.language ?? getPigTexSettings().language)
        }

        window.addEventListener(PIGTEX_SETTINGS_CHANGED_EVENT, handleSettingsChanged as EventListener)
        return () => {
            window.removeEventListener(PIGTEX_SETTINGS_CHANGED_EVENT, handleSettingsChanged as EventListener)
        }
    }, [])

    useEffect(() => {
        document.documentElement.lang = language
    }, [language])

    const setLanguage = useCallback((nextLanguage: AppLanguage) => {
        updatePigTexSettings({ language: nextLanguage })
    }, [])

    const value = useMemo<I18nContextValue>(() => ({
        language,
        locale: getLocaleForLanguage(language),
        isVietnamese: language === 'vi',
        setLanguage,
    }), [language, setLanguage])

    return (
        <I18nContext.Provider value={value}>
            {children}
        </I18nContext.Provider>
    )
}

export const useI18n = () => useContext(I18nContext)

export default I18nContext
