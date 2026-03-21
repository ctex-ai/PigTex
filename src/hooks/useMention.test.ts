import { describe, expect, it } from 'vitest'

import { buildMentionAwareMessageText } from './useMention'

describe('useMention helpers', () => {
    it('keeps referenced items visible when prompt text and mentions are both present', () => {
        const text = buildMentionAwareMessageText(
            'Review this carefully',
            [
                { type: 'file', relativePath: 'src/main.ts' },
                { type: 'folder', relativePath: 'docs' }
            ]
        )

        expect(text).toContain('Review this carefully')
        expect(text).toContain('Referenced items:')
        expect(text).toContain('@file:src/main.ts')
        expect(text).toContain('@folder:docs')
    })

    it('falls back to mention-only text when prompt text is empty', () => {
        const text = buildMentionAwareMessageText(
            '',
            [{ type: 'file', relativePath: 'README.md' }]
        )

        expect(text).toBe('@file:README.md')
    })
})
