import { beforeEach, describe, expect, it } from 'vitest'

import {
    applyDeviceScopeHeaders,
    DEVICE_SCOPE_ID_STORAGE_KEY,
    getKnownAccountIds,
    getOrCreateDeviceScopeId,
    KNOWN_ACCOUNT_IDS_STORAGE_KEY,
    rememberKnownAccountId,
} from './deviceScope'

describe('deviceScope', () => {
    beforeEach(() => {
        window.localStorage.clear()
    })

    it('creates and reuses a stable device scope id', () => {
        const first = getOrCreateDeviceScopeId()
        const second = getOrCreateDeviceScopeId()

        expect(first).toBeTruthy()
        expect(first).toBe(second)
        expect(window.localStorage.getItem(DEVICE_SCOPE_ID_STORAGE_KEY)).toBe(first)
    })

    it('stores known account ids without duplicates', () => {
        rememberKnownAccountId('user-a')
        rememberKnownAccountId('user-b')
        rememberKnownAccountId('user-a')

        expect(getKnownAccountIds()).toEqual(['user-a', 'user-b'])
        expect(window.localStorage.getItem(KNOWN_ACCOUNT_IDS_STORAGE_KEY)).toBe(JSON.stringify(['user-a', 'user-b']))
    })

    it('applies device scope headers for backend local scoping', () => {
        rememberKnownAccountId('user-a')
        rememberKnownAccountId('user-b')

        const headers: Record<string, string> = {}
        applyDeviceScopeHeaders(headers)

        expect(headers['X-PigTex-Device-Scope']).toBeTruthy()
        expect(headers['X-PigTex-Legacy-Accounts']).toBe('user-a,user-b')
    })
})
