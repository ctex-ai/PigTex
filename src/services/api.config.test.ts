import { afterEach, describe, expect, it, vi } from 'vitest'

import {
    diagnoseApiConnectivityIssue,
    resolvePigTexApiBaseForEnvironment,
    resolveUpstreamBaseUrlForEnvironment,
    sendSmartChat
} from './api'

describe('resolvePigTexApiBaseForEnvironment', () => {
    it('keeps localhost fallback for non-production builds when unset', () => {
        expect(resolvePigTexApiBaseForEnvironment(undefined, false)).toBe('http://localhost:3001/api')
    })

    it('normalizes hosted backend urls for production builds', () => {
        expect(resolvePigTexApiBaseForEnvironment('https://api.pigtex.app', true)).toBe('https://api.pigtex.app/api')
    })

    it('rejects missing production api base configuration', () => {
        expect(() => resolvePigTexApiBaseForEnvironment('', true)).toThrow(
            'Production desktop build requires VITE_PIGTEX_API_BASE to point to the hosted backend.'
        )
    })

    it('rejects localhost targets for production builds', () => {
        expect(() => resolvePigTexApiBaseForEnvironment('http://localhost:3001', true)).toThrow(
            'Production desktop build cannot use localhost or loopback for VITE_PIGTEX_API_BASE.'
        )
    })

    it('allows localhost targets for production QA builds when explicitly overridden', () => {
        expect(resolvePigTexApiBaseForEnvironment('http://localhost:3001', true, true)).toBe(
            'http://localhost:3001/api'
        )
    })

    it('rejects non-absolute production api base values', () => {
        expect(() => resolvePigTexApiBaseForEnvironment('/api', true)).toThrow(
            'Production desktop build requires VITE_PIGTEX_API_BASE to be an absolute http(s) URL.'
        )
    })

    it('rejects loopback upstream ai base urls for production builds', () => {
        expect(() => resolveUpstreamBaseUrlForEnvironment('http://127.0.0.1:8045/v1', true)).toThrow(
            'Production desktop build cannot use localhost or loopback for AI provider base URL.'
        )
    })

    it('allows loopback upstream ai base urls for production QA builds when explicitly overridden', () => {
        expect(resolveUpstreamBaseUrlForEnvironment('http://127.0.0.1:8045/v1', true, true)).toBe(
            'http://127.0.0.1:8045/v1'
        )
    })

    it('allows loopback upstream ai base urls for non-production builds', () => {
        expect(resolveUpstreamBaseUrlForEnvironment('http://127.0.0.1:8045/v1', false)).toBe(
            'http://127.0.0.1:8045/v1'
        )
    })
})

describe('diagnoseApiConnectivityIssue', () => {
    afterEach(() => {
        vi.unstubAllGlobals()
    })

    it('detects an unreachable local backend from network failures', async () => {
        vi.stubGlobal('fetch', vi.fn().mockRejectedValue(new TypeError('Failed to fetch')))

        const issue = await diagnoseApiConnectivityIssue(new TypeError('Failed to fetch'))

        expect(issue).toMatchObject({
            kind: 'backend_unreachable'
        })
        expect(issue?.apiBaseUrl).toMatch(/\/api$/)
        expect(typeof issue?.isLoopback).toBe('boolean')
    })

    it('detects an unhealthy backend when health check responds with 503', async () => {
        vi.stubGlobal('fetch', vi.fn().mockResolvedValue(new Response(null, { status: 503 })))

        const issue = await diagnoseApiConnectivityIssue(new TypeError('Network error'))

        expect(issue).toMatchObject({
            kind: 'backend_unhealthy',
            statusCode: 503
        })
        expect(issue?.apiBaseUrl).toMatch(/\/api$/)
        expect(typeof issue?.isLoopback).toBe('boolean')
    })
})

describe('sendSmartChat error diagnostics', () => {
    afterEach(() => {
        vi.unstubAllGlobals()
    })

    it('includes backend error code and request id for upstream chat failures', async () => {
        vi.stubGlobal(
            'fetch',
            vi.fn().mockResolvedValue(
                new Response(
                    JSON.stringify({
                        detail: {
                            error: 'upstream_api_error',
                            message: 'Upstream API request failed (status 502)'
                        }
                    }),
                    {
                        status: 502,
                        headers: {
                            'Content-Type': 'application/json',
                            'X-Request-ID': 'req_prod_123'
                        }
                    }
                )
            )
        )

        await expect(sendSmartChat({ message: 'hello production' })).rejects.toThrow(
            'Upstream API request failed (status 502) [code: upstream_api_error, request_id: req_prod_123]'
        )
    })
})
