import { describe, it, expect, vi, beforeEach } from 'vitest'
import { fireEvent, render, screen, waitFor } from '@testing-library/react'

import MemoryManager from './MemoryManager'

const getRules = vi.fn()
const updateRules = vi.fn()

vi.mock('../../services/api', () => ({
    getRules: (...args: unknown[]) => getRules(...args),
    updateRules: (...args: unknown[]) => updateRules(...args)
}))

describe('MemoryManager', () => {
    beforeEach(() => {
        getRules.mockReset()
        updateRules.mockReset()
        localStorage.clear()
    })

    it('loads global rules for standalone chat by default', async () => {
        getRules.mockResolvedValue({
            rules: '- Always answer in Vietnamese',
            path: 'brain/rules/PIGTEX.md',
            tokens: 6
        })

        render(<MemoryManager />)

        await waitFor(() => {
            expect(getRules).toHaveBeenCalledWith(undefined)
        })

        expect(screen.getByText('Hệ thống Rules')).toBeInTheDocument()
        expect(screen.getByText('- Always answer in Vietnamese')).toBeInTheDocument()
    })

    it('loads workspace rules when a workspace is active', async () => {
        getRules.mockResolvedValue({
            rules: '- Keep tests green',
            path: 'brain/rules/workspaces/ws-1/PIGTEX.md',
            tokens: 4
        })

        render(<MemoryManager workspaceId="ws-1" />)

        await waitFor(() => {
            expect(getRules).toHaveBeenCalledWith('ws-1')
        })

        expect(screen.getByText('Workspace Rules')).toBeInTheDocument()
        expect(screen.getByText('- Keep tests green')).toBeInTheDocument()
    })

    it('saves standalone rules without a workspace id', async () => {
        getRules.mockResolvedValue({
            rules: '',
            path: 'brain/rules/PIGTEX.md',
            tokens: 0
        })
        updateRules.mockResolvedValue({
            ok: true,
            path: 'brain/rules/PIGTEX.md'
        })

        render(<MemoryManager />)

        await waitFor(() => {
            expect(getRules).toHaveBeenCalledWith(undefined)
        })

        fireEvent.click(screen.getByRole('button', { name: /thêm rules/i }))

        const textarea = screen.getByRole('textbox')
        fireEvent.change(textarea, {
            target: { value: '- Prefer concise responses' }
        })

        fireEvent.click(screen.getByRole('button', { name: /^lưu$/i }))

        await waitFor(() => {
            expect(updateRules).toHaveBeenCalledWith('- Prefer concise responses', undefined)
        })

        expect(screen.getByText('- Prefer concise responses')).toBeInTheDocument()
    })
})
