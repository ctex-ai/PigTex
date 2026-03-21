import { describe, expect, it } from 'vitest'
import {
    compareSemanticVersions,
    createDesktopUpdateErrorState,
    createDesktopUpdateStateFromResponse,
    normalizeDesktopUpdateManifest,
    resolveDesktopUpdateManifestUrl
} from './desktopUpdate'

describe('desktopUpdate', () => {
    it('compares semantic versions in ascending order', () => {
        expect(compareSemanticVersions('1.0.0', '1.0.1')).toBeLessThan(0)
        expect(compareSemanticVersions('1.2.0', '1.1.9')).toBeGreaterThan(0)
        expect(compareSemanticVersions('1.2', '1.2.0')).toBe(0)
    })

    it('treats stable releases as newer than prereleases', () => {
        expect(compareSemanticVersions('1.0.0', '1.0.0-beta.2')).toBeGreaterThan(0)
        expect(compareSemanticVersions('1.0.0-beta.2', '1.0.0-beta.11')).toBeLessThan(0)
    })

    it('normalizes a valid update manifest', () => {
        expect(normalizeDesktopUpdateManifest({
            version: '1.0.1',
            downloadPageUrl: 'https://example.com/download/windows',
            installerUrl: 'https://example.com/downloads/PigTex-1.0.1.exe',
            releaseNotes: 'Improved update flow'
        })).toEqual({
            product: undefined,
            channel: undefined,
            platform: undefined,
            version: '1.0.1',
            downloadPageUrl: 'https://example.com/download/windows',
            installerUrl: 'https://example.com/downloads/PigTex-1.0.1.exe',
            publishedAt: null,
            releaseNotes: 'Improved update flow',
            requiresManualInstall: true,
            upgradeBehavior: null
        })
    })

    it('derives the desktop update manifest from the hosted API base', () => {
        expect(resolveDesktopUpdateManifestUrl('https://pigtex.id.vn')).toBe('https://pigtex.id.vn/api/desktop/latest')
        expect(resolveDesktopUpdateManifestUrl('https://pigtex.id.vn/api')).toBe('https://pigtex.id.vn/api/desktop/latest')
        expect(resolveDesktopUpdateManifestUrl('https://example.com/proxy/api/')).toBe('https://example.com/proxy/api/desktop/latest')
        expect(resolveDesktopUpdateManifestUrl('')).toBeNull()
        expect(resolveDesktopUpdateManifestUrl('not-a-url')).toBeNull()
    })

    it('marks update as available when latest version is newer', () => {
        const result = createDesktopUpdateStateFromResponse({
            currentVersion: '1.0.0',
            checkedAt: '2026-03-15T10:00:00.000Z',
            manifest: {
                version: '1.0.1',
                downloadPageUrl: 'https://example.com/download/windows',
                installerUrl: 'https://example.com/downloads/PigTex-1.0.1.exe',
                publishedAt: '2026-03-15T09:30:00.000Z',
                releaseNotes: 'Improved update flow',
                requiresManualInstall: true,
                upgradeBehavior: 'nsis-overwrite'
            }
        })

        expect(result.status).toBe('update_available')
        expect(result.updateAvailable).toBe(true)
        expect(result.latestVersion).toBe('1.0.1')
        expect(result.downloadPageUrl).toBe('https://example.com/download/windows')
    })

    it('creates an error state when update check fails', () => {
        const state = createDesktopUpdateErrorState('1.0.0', new Error('network error'))

        expect(state.status).toBe('error')
        expect(state.currentVersion).toBe('1.0.0')
        expect(state.errorMessage).toBe('network error')
    })
})
