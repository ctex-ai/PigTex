import { cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import AdminConsole from './AdminConsole'

const apiMocks = vi.hoisted(() => ({
    getSkillFoundryOverview: vi.fn(),
    getSkillFoundryAudit: vi.fn(),
    compileSkillFoundryDraft: vi.fn(),
    publishSkillFoundryDraft: vi.fn(),
    rollbackSkillFoundryRelease: vi.fn(),
    resolveSkillFoundryMatches: vi.fn(),
}))

const localeMocks = vi.hoisted(() => ({
    isVietnamese: false,
}))

vi.mock('../../services/api', () => ({
    getSkillFoundryOverview: apiMocks.getSkillFoundryOverview,
    getSkillFoundryAudit: apiMocks.getSkillFoundryAudit,
    compileSkillFoundryDraft: apiMocks.compileSkillFoundryDraft,
    publishSkillFoundryDraft: apiMocks.publishSkillFoundryDraft,
    rollbackSkillFoundryRelease: apiMocks.rollbackSkillFoundryRelease,
    resolveSkillFoundryMatches: apiMocks.resolveSkillFoundryMatches,
}))

vi.mock('../../contexts/I18nContext', () => ({
    useI18n: () => ({ isVietnamese: localeMocks.isVietnamese }),
}))

vi.mock('../Shared/Toast', () => ({
    showError: vi.fn(),
    showSuccess: vi.fn(),
}))

const overviewPayload = {
    summary: {
        active_skill_count: 1,
        draft_skill_count: 1,
        challenger_count: 1,
        rejected_count: 0,
        generated_at: '2026-03-13T00:00:00',
        draft_generated_at: '2026-03-13T01:00:00',
        registry_path: 'runtime_registry.json',
        draft_registry_path: 'draft_registry.json',
        incoming_path: 'incoming',
        release_count: 1,
    },
    active_registry: {
        schema_version: '1.0',
        active_skills: [{ skill_id: 'active-1', title: 'Active Skill', domain: 'marketing.ads.facebook', score_total: 91 }],
    },
    draft_registry: {
        schema_version: '1.0',
        active_skills: [{ skill_id: 'draft-1', title: 'Draft Skill', domain: 'marketing.ads.tiktok', score_total: 89 }],
    },
    catalog: {
        schema_version: '1.0',
        challengers: [{ skill_id: 'challenger-1', title: 'Challenger Skill', domain: 'support.triage', score_total: 70 }],
        rejected: [],
        reports: [{
            report_id: 'report-catalog-1',
            generated_at: '2026-03-13T01:30:00',
            artifact_retention: {
                enabled: true,
                moved_count: 3,
                accepted_artifact_count: 2,
                rejected_artifact_count: 1,
                sample_moved_items: [
                    {
                        source_path: 'data/skill_foundry/incoming/marketing/facebook_hook.md',
                        destination_path: 'data/skill_foundry/processed/accepted/marketing/facebook_hook.md',
                        status: 'accepted',
                    },
                ],
            },
        }],
    },
    releases: [{ release_id: '20260313-demo', active_skill_count: 1, released_at: '2026-03-13T02:00:00', released_by: 'admin@example.com' }],
    publish_gate: {
        ready: false,
        blockers: ['average_score_below_active_threshold'],
        warnings: ['many_active_skills_missing_output_contract'],
        runtime_empty: false,
        draft_skill_count: 1,
        challenger_count: 1,
        rejected_count: 0,
        average_score: 58,
        active_threshold: 62,
        challenger_threshold: 55,
        redundancy_pruned_count: 0,
    },
}

describe('AdminConsole', () => {
    beforeEach(() => {
        vi.clearAllMocks()
        localeMocks.isVietnamese = false
        apiMocks.getSkillFoundryOverview.mockResolvedValue(overviewPayload)
        apiMocks.getSkillFoundryAudit.mockResolvedValue({
            items: [{ id: 'audit-1', action: 'publish', status: 'success', actor_user_id: 'admin-1', created_at: '2026-03-13T02:00:00', summary: 'Published draft' }],
        })
        apiMocks.compileSkillFoundryDraft.mockResolvedValue({ report_id: 'report-1' })
        apiMocks.publishSkillFoundryDraft.mockResolvedValue({
            release: { release_id: '20260313-demo-2', active_skill_count: 1 },
            registry: overviewPayload.active_registry,
        })
        apiMocks.rollbackSkillFoundryRelease.mockResolvedValue({
            rollback: { release_id: '20260313-demo' },
            registry: overviewPayload.active_registry,
        })
        apiMocks.resolveSkillFoundryMatches.mockResolvedValue({
            intent: 'creative',
            keywords: ['facebook', 'hook'],
            matches: [],
            formatted: '### Active Skill',
        })
    })

    afterEach(() => {
        cleanup()
    })

    it('loads overview and publishes draft successfully', async () => {
        render(<AdminConsole />)

        await waitFor(() => expect(screen.getByText('Admin Console')).toBeInTheDocument())
        expect(screen.getAllByText(/Active Skill|Draft Skill/).length).toBeGreaterThan(0)
        expect(screen.getByText('Accepted files: 2')).toBeInTheDocument()
        expect(screen.getByText('Rejected files: 1')).toBeInTheDocument()

        fireEvent.change(screen.getByLabelText('Publish note'), {
            target: { value: 'Ship tested draft' },
        })
        fireEvent.click(screen.getByRole('button', { name: 'Publish draft' }))

        await waitFor(() => expect(apiMocks.publishSkillFoundryDraft).toHaveBeenCalledWith('Ship tested draft'))
        expect(apiMocks.getSkillFoundryOverview).toHaveBeenCalledTimes(2)
    })

    it('keeps technical tools hidden until advanced mode is opened', async () => {
        render(<AdminConsole />)

        await waitFor(() => expect(screen.getByText('Admin Console')).toBeInTheDocument())
        expect(screen.queryByText('Check skill routing')).not.toBeInTheDocument()

        fireEvent.click(screen.getAllByRole('button', { name: 'Show technical details' })[0])

        expect(screen.getByText('Check skill routing')).toBeInTheDocument()
    })

    it('shows translated publish gate labels in Vietnamese and keeps raw codes in advanced mode', async () => {
        localeMocks.isVietnamese = true

        render(<AdminConsole />)

        await waitFor(() => expect(screen.getAllByText('Admin Console').length).toBeGreaterThan(0))
        expect(screen.getByText('Điểm trung bình của bản nháp chưa đạt ngưỡng publish.')).toBeInTheDocument()
        expect(screen.getByText('Nhiều skill active chưa có output contract rõ ràng.')).toBeInTheDocument()
        expect(screen.queryByText(/average_score_below_active_threshold/)).not.toBeInTheDocument()

        fireEvent.click(screen.getAllByRole('button', { name: 'Mở chi tiết kỹ thuật' })[0])

        expect(screen.getByText(/average_score_below_active_threshold/)).toBeInTheDocument()
    })
})
