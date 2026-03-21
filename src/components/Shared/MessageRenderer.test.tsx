import { describe, expect, it, vi } from 'vitest'
import { render, screen } from '@testing-library/react'
import MessageRenderer from './MessageRenderer'

vi.mock('../../contexts/I18nContext', () => ({
    useI18n: () => ({ isVietnamese: true })
}))

vi.mock('./ProtectedImage', () => ({
    default: ({ source, alt, className }: { source: string; alt: string; className?: string }) => (
        <img src={source} alt={alt} className={className} />
    )
}))

describe('MessageRenderer', () => {
    it('renders short markdown immediately while streaming', () => {
        const { container } = render(
            <MessageRenderer content="### Tiêu đề" isStreaming />
        )

        expect(container.querySelector('.md-h3')?.textContent).toBe('Tiêu đề')
        expect(container.querySelector('.streaming-plain-text')).toBeNull()
    })

    it('renders stable markdown head and keeps trailing unfinished text plain while streaming', () => {
        const { container } = render(
            <MessageRenderer
                content={'### Kế hoạch\n\n**Mục 1**\nĐang viết dở'}
                isStreaming
            />
        )

        expect(container.querySelector('.md-h3')?.textContent).toBe('Kế hoạch')
        expect(container.querySelector('.md-strong')?.textContent).toBe('Mục 1')

        const tail = container.querySelector('.streaming-plain-text-tail')
        expect(tail?.textContent).toBe('Đang viết dở')
        expect(screen.getByText('Đang viết dở')).not.toBeNull()
    })
})
