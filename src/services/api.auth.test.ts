import { beforeEach, describe, expect, it } from 'vitest'

import { getAuthToken, removeAuthToken, setAuthToken } from './api'

function installSecureAuthElectronApi() {
    let secureToken: string | null = null

    window.electronAPI = {
        isSecureStorageAvailable: () => true,
        getSecureAuthToken: () => secureToken,
        setSecureAuthToken: (token: string) => {
            secureToken = token.trim() || null
            return secureToken
        },
        clearSecureAuthToken: () => {
            secureToken = null
        },
    } as unknown as NonNullable<Window['electronAPI']>

    return {
        getSecureToken: () => secureToken,
    }
}

describe('auth token persistence', () => {
    beforeEach(() => {
        localStorage.clear()
        sessionStorage.clear()
        window.electronAPI = undefined
    })

    it('persists desktop auth tokens in secure storage and restores them after a new session', () => {
        const secureAuth = installSecureAuthElectronApi()

        setAuthToken('jwt-desktop-session')

        expect(sessionStorage.getItem('pigtex_auth_token')).toBe('jwt-desktop-session')
        expect(secureAuth.getSecureToken()).toBe('jwt-desktop-session')

        sessionStorage.clear()

        expect(getAuthToken()).toBe('jwt-desktop-session')
        expect(sessionStorage.getItem('pigtex_auth_token')).toBe('jwt-desktop-session')
    })

    it('clears secure auth tokens on logout', () => {
        const secureAuth = installSecureAuthElectronApi()

        setAuthToken('jwt-to-remove')
        removeAuthToken()

        expect(sessionStorage.getItem('pigtex_auth_token')).toBeNull()
        expect(sessionStorage.getItem('access_token')).toBeNull()
        expect(secureAuth.getSecureToken()).toBeNull()
    })

    it('migrates legacy localStorage auth tokens into secure desktop storage', () => {
        const secureAuth = installSecureAuthElectronApi()
        localStorage.setItem('pigtex_auth_token', 'legacy-jwt')

        expect(getAuthToken()).toBe('legacy-jwt')
        expect(sessionStorage.getItem('pigtex_auth_token')).toBe('legacy-jwt')
        expect(localStorage.getItem('pigtex_auth_token')).toBeNull()
        expect(secureAuth.getSecureToken()).toBe('legacy-jwt')
    })
})
