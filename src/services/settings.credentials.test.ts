import { beforeEach, describe, expect, it } from 'vitest'
import {
    DEFAULT_PIGTEX_SETTINGS,
    getPigTexSettings,
    getProviderDefaultBaseUrl,
    savePigTexSettings,
    type PigTexSettings,
} from './settings'

function buildSettings(overrides: Partial<PigTexSettings> = {}): PigTexSettings {
    return {
        ...DEFAULT_PIGTEX_SETTINGS,
        providerCredentialProfiles: {
            ...DEFAULT_PIGTEX_SETTINGS.providerCredentialProfiles,
            auto: { ...DEFAULT_PIGTEX_SETTINGS.providerCredentialProfiles.auto },
            openai: { ...DEFAULT_PIGTEX_SETTINGS.providerCredentialProfiles.openai },
            anthropic: { ...DEFAULT_PIGTEX_SETTINGS.providerCredentialProfiles.anthropic },
            gemini: { ...DEFAULT_PIGTEX_SETTINGS.providerCredentialProfiles.gemini },
            alibaba: { ...DEFAULT_PIGTEX_SETTINGS.providerCredentialProfiles.alibaba },
        },
        ...overrides,
    }
}

describe('settings credential profiles', () => {
    beforeEach(() => {
        localStorage.clear()
        sessionStorage.clear()
        window.electronAPI = undefined
    })

    it('keeps api key in session only when local save is disabled', () => {
        savePigTexSettings(
            buildSettings({
                apiProvider: 'openai',
                customEndpoint: 'openai',
                apiKey: 'sk-openai-session',
                baseUrl: getProviderDefaultBaseUrl('openai'),
                saveApiKeyLocally: false,
            })
        )

        const raw = localStorage.getItem('pigtex_settings_v2')
        expect(raw).toBeTruthy()
        const parsed = JSON.parse(raw as string) as {
            apiKey: string
            providerCredentialProfiles: { openai: { apiKey: string } }
        }

        expect(parsed.apiKey).toBe('')
        expect(parsed.providerCredentialProfiles.openai.apiKey).toBe('')

        const inSession = getPigTexSettings()
        expect(inSession.apiKey).toBe('sk-openai-session')

        sessionStorage.clear()
        const afterNewSession = getPigTexSettings()
        expect(afterNewSession.apiKey).toBe('')
    })

    it('returns credentials of currently selected provider', () => {
        const secureApiKeys: Partial<Record<'auto' | 'openai' | 'anthropic' | 'gemini' | 'alibaba', string>> = {}
        window.electronAPI = {
            isSecureStorageAvailable: () => true,
            getSecureApiKeys: () => secureApiKeys,
            setSecureApiKeys: (payload: typeof secureApiKeys) => {
                Object.keys(secureApiKeys).forEach((key) => {
                    delete secureApiKeys[key as keyof typeof secureApiKeys]
                })
                Object.assign(secureApiKeys, payload)
                return secureApiKeys
            },
        } as unknown as NonNullable<Window['electronAPI']>

        const profiles = {
            ...DEFAULT_PIGTEX_SETTINGS.providerCredentialProfiles,
            auto: { apiKey: '', baseUrl: '' },
            openai: { apiKey: 'sk-openai-abc', baseUrl: getProviderDefaultBaseUrl('openai') },
            anthropic: { apiKey: 'sk-ant-xyz', baseUrl: getProviderDefaultBaseUrl('anthropic') },
            gemini: { apiKey: '', baseUrl: getProviderDefaultBaseUrl('gemini') },
        }

        savePigTexSettings(
            buildSettings({
                apiProvider: 'openai',
                customEndpoint: 'openai',
                apiKey: profiles.openai.apiKey,
                baseUrl: profiles.openai.baseUrl,
                providerCredentialProfiles: profiles,
                saveApiKeyLocally: true,
            })
        )

        let current = getPigTexSettings()
        expect(current.apiProvider).toBe('openai')
        expect(current.apiKey).toBe('sk-openai-abc')

        savePigTexSettings({
            ...current,
            apiProvider: 'anthropic',
            customEndpoint: 'anthropic',
            apiKey: current.providerCredentialProfiles.anthropic.apiKey,
            baseUrl: getProviderDefaultBaseUrl('anthropic'),
        })

        current = getPigTexSettings()
        expect(current.apiProvider).toBe('anthropic')
        expect(current.apiKey).toBe('sk-ant-xyz')
    })

    it('stores persisted desktop api keys outside localStorage when secure storage is available', () => {
        const secureApiKeys: Partial<Record<'auto' | 'openai' | 'anthropic' | 'gemini' | 'alibaba', string>> = {}
        window.electronAPI = {
            isSecureStorageAvailable: () => true,
            getSecureApiKeys: () => secureApiKeys,
            setSecureApiKeys: (payload: typeof secureApiKeys) => {
                Object.keys(secureApiKeys).forEach((key) => {
                    delete secureApiKeys[key as keyof typeof secureApiKeys]
                })
                Object.assign(secureApiKeys, payload)
                return secureApiKeys
            },
        } as unknown as NonNullable<Window['electronAPI']>

        savePigTexSettings(
            buildSettings({
                apiProvider: 'openai',
                customEndpoint: 'openai',
                apiKey: 'sk-openai-secure',
                baseUrl: getProviderDefaultBaseUrl('openai'),
                saveApiKeyLocally: true,
            })
        )

        const raw = localStorage.getItem('pigtex_settings_v2')
        expect(raw).toBeTruthy()
        const parsed = JSON.parse(raw as string) as {
            apiKey: string
            providerCredentialProfiles: { openai: { apiKey: string } }
            saveApiKeyLocally: boolean
        }

        expect(parsed.apiKey).toBe('')
        expect(parsed.providerCredentialProfiles.openai.apiKey).toBe('')
        expect(parsed.saveApiKeyLocally).toBe(true)
        expect(secureApiKeys.openai).toBe('sk-openai-secure')

        const reloaded = getPigTexSettings()
        expect(reloaded.apiKey).toBe('sk-openai-secure')
    })

    it('falls back to session-only api key storage when secure storage is unavailable', () => {
        window.electronAPI = {
            isSecureStorageAvailable: () => false,
        } as unknown as NonNullable<Window['electronAPI']>

        savePigTexSettings(
            buildSettings({
                apiProvider: 'openai',
                customEndpoint: 'openai',
                apiKey: 'sk-openai-session-fallback',
                baseUrl: getProviderDefaultBaseUrl('openai'),
                saveApiKeyLocally: true,
            })
        )

        const raw = localStorage.getItem('pigtex_settings_v2')
        expect(raw).toBeTruthy()
        const parsed = JSON.parse(raw as string) as {
            apiKey: string
            providerCredentialProfiles: { openai: { apiKey: string } }
            saveApiKeyLocally: boolean
        }

        expect(parsed.apiKey).toBe('')
        expect(parsed.providerCredentialProfiles.openai.apiKey).toBe('')
        expect(parsed.saveApiKeyLocally).toBe(false)

        const reloaded = getPigTexSettings()
        expect(reloaded.apiKey).toBe('sk-openai-session-fallback')
        expect(reloaded.saveApiKeyLocally).toBe(false)
    })

    it('sanitizes previously persisted plaintext api keys when secure storage is unavailable', () => {
        window.electronAPI = {
            isSecureStorageAvailable: () => false,
        } as unknown as NonNullable<Window['electronAPI']>

        localStorage.setItem(
            'pigtex_settings_v2',
            JSON.stringify(
                buildSettings({
                    apiProvider: 'openai',
                    customEndpoint: 'openai',
                    apiKey: 'sk-openai-legacy',
                    baseUrl: getProviderDefaultBaseUrl('openai'),
                    providerCredentialProfiles: {
                        ...DEFAULT_PIGTEX_SETTINGS.providerCredentialProfiles,
                        openai: {
                            apiKey: 'sk-openai-legacy',
                            baseUrl: getProviderDefaultBaseUrl('openai'),
                        },
                    },
                    saveApiKeyLocally: true,
                })
            )
        )

        const reloaded = getPigTexSettings()
        expect(reloaded.apiKey).toBe('sk-openai-legacy')
        expect(reloaded.saveApiKeyLocally).toBe(false)

        const raw = localStorage.getItem('pigtex_settings_v2')
        expect(raw).toBeTruthy()
        const parsed = JSON.parse(raw as string) as {
            apiKey: string
            providerCredentialProfiles: { openai: { apiKey: string } }
            saveApiKeyLocally: boolean
        }

        expect(parsed.apiKey).toBe('')
        expect(parsed.providerCredentialProfiles.openai.apiKey).toBe('')
        expect(parsed.saveApiKeyLocally).toBe(false)
    })
})
