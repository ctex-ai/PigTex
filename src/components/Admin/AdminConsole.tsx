import { useEffect, useMemo, useState } from 'react'
import {
    SkillFoundryAuditEvent,
    SkillFoundryArtifactMove,
    SkillFoundryArtifactRetentionSummary,
    SkillFoundryOverview,
    SkillFoundryPublishGate,
    SkillFoundryRelease,
    SkillFoundrySkill,
    compileSkillFoundryDraft,
    getSkillFoundryAudit,
    getSkillFoundryOverview,
    publishSkillFoundryDraft,
    resolveSkillFoundryMatches,
    rollbackSkillFoundryRelease,
} from '../../services/api'
import { showError, showSuccess } from '../Shared/Toast'
import { useI18n } from '../../contexts/I18nContext'
import './AdminConsole.css'

interface ResolveState {
    intent: string
    keywords: string[]
    matches: SkillFoundrySkill[]
    formatted: string
}

interface ActivityItem {
    id: string
    title: string
    summary: string
    timestamp: string
    status: string
}

interface AdminCopy {
    title: string
    subtitle: string
    refresh: string
    loading: string
    importTitle: string
    importHint: string
    compilePath: string
    compilePathHint: string
    compileMaxFiles: string
    dryRun: string
    compile: string
    compileDone: string
    publishTitle: string
    publishHint: string
    publishNote: string
    publish: string
    rollbackTitle: string
    rollbackHint: string
    rollbackReleaseId: string
    rollbackNote: string
    rollback: string
    currentLive: string
    currentDraft: string
    activeSkills: string
    draftSkills: string
    challengers: string
    rejected: string
    releases: string
    activity: string
    advanced: string
    showAdvanced: string
    hideAdvanced: string
    advancedHint: string
    resolveTitle: string
    resolveMessage: string
    resolve: string
    recentSummary: string
    liveRelease: string
    availableRollback: string
    lastUpdated: string
    readyToPublish: string
    emptyRelease: string
    selectedRelease: string
    noSelectedRelease: string
    noData: string
    noAudit: string
    noReleases: string
    summary: string
    draftGeneratedAt: string
    activeGeneratedAt: string
    report: string
    formattedPrompt: string
    moreSkills: string
    importOptions: string
    matchedSkills: string
    activityFailed: string
    activitySuccess: string
    activityPending: string
    noMatches: string
    dryRunNote: string
    viewRawPrompt: string
    reportUnavailable: string
    detailSummary: string
    activityCompile: string
    activityPublish: string
    activityRollback: string
    activityDefault: string
    skillCount: string
    reportId: string
    topSkills: string
    releaseLabel: string
    noteOptional: string
    publishGate: string
    publishGateReady: string
    publishGateBlocked: string
    publishWarnings: string
    publishBlockers: string
    artifactIntake: string
    artifactMoved: string
    artifactAccepted: string
    artifactRejected: string
    artifactDetails: string
    noArtifactData: string
    artifactKept: string
    artifactDiscarded: string
    technicalCodes: string
}

const PUBLISH_GATE_BLOCKER_LABELS = {
    vi: {
        no_active_skills_in_draft: 'Bản nháp chưa có skill nào để đưa vào chạy.',
        missing_report_id: 'Bản nháp chưa có mã báo cáo compile.',
        missing_generated_at: 'Bản nháp chưa có thời điểm tạo.',
        missing_average_score: 'Bản nháp chưa có điểm trung bình để đánh giá.',
        average_score_below_active_threshold: 'Điểm trung bình của bản nháp chưa đạt ngưỡng publish.',
        draft_contains_low_score_active_skills: 'Bản nháp còn chứa skill active có điểm quá thấp.',
        monetization_skills_missing_effective_output_contract: 'Một số skill kiếm tiền chưa có output contract hiệu lực.',
    },
    en: {
        no_active_skills_in_draft: 'The draft does not contain any skills ready to go live.',
        missing_report_id: 'The draft is missing a compile report ID.',
        missing_generated_at: 'The draft is missing its generated timestamp.',
        missing_average_score: 'The draft is missing an average score.',
        average_score_below_active_threshold: 'The draft average score is below the publish threshold.',
        draft_contains_low_score_active_skills: 'The draft still contains active skills with low scores.',
        monetization_skills_missing_effective_output_contract: 'Some monetization skills do not have an effective output contract.',
    },
} as const

const PUBLISH_GATE_WARNING_LABELS = {
    vi: {
        many_active_skills_missing_output_contract: 'Nhiều skill active chưa có output contract rõ ràng.',
        monetization_skills_using_fallback_contracts: 'Một số skill kiếm tiền vẫn đang dùng output contract dự phòng.',
        challenger_pool_is_large: 'Số skill challenger hiện đang khá lớn.',
        rejected_pool_is_large: 'Số skill bị loại hiện đang khá lớn.',
        draft_registry_is_large: 'Bản nháp hiện đang có quá nhiều skill.',
        automatic_redundancy_pruning_applied: 'Hệ thống đã tự dọn bớt skill trùng hoặc gần trùng.',
    },
    en: {
        many_active_skills_missing_output_contract: 'Many active skills still lack an explicit output contract.',
        monetization_skills_using_fallback_contracts: 'Some monetization skills are still relying on fallback output contracts.',
        challenger_pool_is_large: 'The challenger pool is getting large.',
        rejected_pool_is_large: 'The rejected pool is getting large.',
        draft_registry_is_large: 'The draft registry currently contains many skills.',
        automatic_redundancy_pruning_applied: 'Automatic redundancy pruning removed overlapping skills in this draft.',
    },
} as const

const AdminConsole = () => {
    const { isVietnamese } = useI18n()
    const [overview, setOverview] = useState<SkillFoundryOverview | null>(null)
    const [auditItems, setAuditItems] = useState<SkillFoundryAuditEvent[]>([])
    const [isLoading, setIsLoading] = useState(true)
    const [isBusy, setIsBusy] = useState(false)
    const [compilePath, setCompilePath] = useState('')
    const [compileDryRun, setCompileDryRun] = useState(false)
    const [compileMaxFiles, setCompileMaxFiles] = useState('200')
    const [publishNote, setPublishNote] = useState('')
    const [rollbackReleaseId, setRollbackReleaseId] = useState('')
    const [rollbackNote, setRollbackNote] = useState('')
    const [resolveMessage, setResolveMessage] = useState('')
    const [resolveState, setResolveState] = useState<ResolveState | null>(null)
    const [lastReport, setLastReport] = useState<Record<string, unknown> | null>(null)
    const [showAdvanced, setShowAdvanced] = useState(false)

    const copy = useMemo<AdminCopy>(() => (isVietnamese ? {
        title: 'Admin Console',
        subtitle: 'Màn hình vận hành skill: nhập kho, kiểm tra bản nháp, đưa vào chạy và khôi phục khi cần.',
        refresh: 'Làm mới',
        loading: 'Đang tải admin console...',
        importTitle: '1. Nhập skill mới',
        importHint: 'Chọn thư mục cần nhập. Hệ thống sẽ chấm điểm và tạo bản nháp mới.',
        compilePath: 'Thư mục đầu vào',
        compilePathHint: 'Để trống = quét toàn bộ thư mục incoming đã cấu hình cho Skill Foundry',
        compileMaxFiles: 'Số file tối đa',
        dryRun: 'Dry run',
        compile: 'Tạo bản nháp',
        compileDone: 'Lần nhập gần nhất trong phiên này',
        publishTitle: '2. Đưa bản nháp vào chạy',
        publishHint: 'Chỉ publish khi bản nháp đã đúng và sẵn sàng dùng cho khách.',
        publishNote: 'Ghi chú publish',
        publish: 'Publish bản nháp',
        rollbackTitle: '3. Khôi phục bản cũ',
        rollbackHint: 'Nếu bản mới có vấn đề, chọn một release cũ để quay lại nhanh.',
        rollbackReleaseId: 'Chọn release để khôi phục',
        rollbackNote: 'Ghi chú rollback',
        rollback: 'Khôi phục release',
        currentLive: 'Bản đang chạy',
        currentDraft: 'Bản nháp hiện tại',
        activeSkills: 'Skill đang chạy',
        draftSkills: 'Skill trong bản nháp',
        challengers: 'Skill đang thách đấu',
        rejected: 'Skill bị loại',
        releases: 'Lịch sử release',
        activity: 'Hoạt động gần đây',
        advanced: 'Nâng cao',
        showAdvanced: 'Mở chi tiết kỹ thuật',
        hideAdvanced: 'Ẩn chi tiết kỹ thuật',
        advancedHint: 'Chỉ mở khi cần debug hoặc kiểm tra định tuyến skill.',
        resolveTitle: 'Kiểm tra định tuyến skill',
        resolveMessage: 'Tin nhắn kiểm thử',
        resolve: 'Chạy kiểm tra',
        recentSummary: 'Tóm tắt nhanh',
        liveRelease: 'Release đang chạy',
        availableRollback: 'Release có thể khôi phục',
        lastUpdated: 'Cập nhật lúc',
        readyToPublish: 'Sẵn sàng publish',
        emptyRelease: 'Chọn một release',
        selectedRelease: 'Release đang chọn',
        noSelectedRelease: 'Chưa chọn release nào',
        noData: 'Chưa có dữ liệu',
        noAudit: 'Chưa có hoạt động gần đây',
        noReleases: 'Chưa có release',
        summary: 'Tổng quan',
        draftGeneratedAt: 'Tạo bản nháp',
        activeGeneratedAt: 'Tạo bản chạy',
        report: 'Dữ liệu compile kỹ thuật',
        formattedPrompt: 'Khối skill runtime',
        moreSkills: 'skill khác',
        importOptions: 'Tùy chọn nhập nâng cao',
        matchedSkills: 'Skill khớp',
        activityFailed: 'Thất bại',
        activitySuccess: 'Thành công',
        activityPending: 'Đang xử lý',
        noMatches: 'Chưa thấy skill khớp',
        dryRunNote: 'Dry run chỉ chấm và báo cáo, không ghi đè bản nháp.',
        viewRawPrompt: 'Xem khối runtime chi tiết',
        reportUnavailable: 'Chưa có báo cáo compile trong phiên này',
        detailSummary: 'Xem danh sách chi tiết',
        activityCompile: 'Đã nhập và chấm skill mới',
        activityPublish: 'Đã đưa bản nháp vào chạy',
        activityRollback: 'Đã khôi phục release',
        activityDefault: 'Đã cập nhật Skill Foundry',
        skillCount: 'Số lượng skill',
        reportId: 'Mã báo cáo',
        topSkills: 'Một số skill tiêu biểu',
        releaseLabel: 'Release',
        noteOptional: 'Ghi chú là tùy chọn',
        publishGate: 'Publish gate',
        publishGateReady: 'Đạt',
        publishGateBlocked: 'Bị chặn',
        publishWarnings: 'Cảnh báo',
        publishBlockers: 'Lý do chặn',
        artifactIntake: 'Kết quả nhập file',
        artifactMoved: 'File đã xử lý',
        artifactAccepted: 'File được giữ lại',
        artifactRejected: 'File bị loại',
        artifactDetails: 'Chi tiết file đã phân loại',
        noArtifactData: 'Chưa có dữ liệu file gần đây',
        artifactKept: 'Giữ lại',
        artifactDiscarded: 'Loại',
        technicalCodes: 'Mã kỹ thuật',
    } : {
        title: 'Admin Console',
        subtitle: 'Operate skills with a simple flow: import, review draft, publish, and roll back if needed.',
        refresh: 'Refresh',
        loading: 'Loading admin console...',
        importTitle: '1. Import new skills',
        importHint: 'Pick a folder to ingest. PigTex will score the files and produce a new draft.',
        compilePath: 'Input folder',
        compilePathHint: 'Leave empty to scan the configured Skill Foundry incoming folder',
        compileMaxFiles: 'Max files',
        dryRun: 'Dry run',
        compile: 'Build draft',
        compileDone: 'Latest import in this session',
        publishTitle: '2. Publish draft',
        publishHint: 'Publish only after the draft looks correct and ready for customers.',
        publishNote: 'Publish note',
        publish: 'Publish draft',
        rollbackTitle: '3. Roll back to a previous release',
        rollbackHint: 'If the new release causes issues, pick an earlier release and restore it quickly.',
        rollbackReleaseId: 'Release to restore',
        rollbackNote: 'Rollback note',
        rollback: 'Rollback release',
        currentLive: 'Current live registry',
        currentDraft: 'Current draft',
        activeSkills: 'Live skills',
        draftSkills: 'Draft skills',
        challengers: 'Challenger skills',
        rejected: 'Rejected skills',
        releases: 'Release history',
        activity: 'Recent activity',
        advanced: 'Advanced',
        showAdvanced: 'Show technical details',
        hideAdvanced: 'Hide technical details',
        advancedHint: 'Open this only when you need debugging or routing checks.',
        resolveTitle: 'Check skill routing',
        resolveMessage: 'Test message',
        resolve: 'Run resolve',
        recentSummary: 'Quick summary',
        liveRelease: 'Live release',
        availableRollback: 'Rollback options',
        lastUpdated: 'Last updated',
        readyToPublish: 'Ready to publish',
        emptyRelease: 'Select a release',
        selectedRelease: 'Selected release',
        noSelectedRelease: 'No release selected',
        noData: 'No data yet',
        noAudit: 'No recent activity yet',
        noReleases: 'No releases yet',
        summary: 'Overview',
        draftGeneratedAt: 'Draft generated',
        activeGeneratedAt: 'Active generated',
        report: 'Technical compile data',
        formattedPrompt: 'Formatted runtime block',
        moreSkills: 'more skills',
        importOptions: 'Advanced import options',
        matchedSkills: 'Matched skills',
        activityFailed: 'Failed',
        activitySuccess: 'Completed',
        activityPending: 'In progress',
        noMatches: 'No matching skills found',
        dryRunNote: 'Dry run only scores and reports. It does not overwrite the draft registry.',
        viewRawPrompt: 'View full runtime block',
        reportUnavailable: 'No compile report captured in this session',
        detailSummary: 'View detailed lists',
        activityCompile: 'Imported and scored new skills',
        activityPublish: 'Published the draft registry',
        activityRollback: 'Restored a previous release',
        activityDefault: 'Updated Skill Foundry',
        skillCount: 'Skill count',
        reportId: 'Report ID',
        topSkills: 'Sample skills',
        releaseLabel: 'Release',
        noteOptional: 'Note is optional',
        publishGate: 'Publish gate',
        publishGateReady: 'Ready',
        publishGateBlocked: 'Blocked',
        publishWarnings: 'Warnings',
        publishBlockers: 'Blockers',
        artifactIntake: 'Artifact intake',
        artifactMoved: 'Processed files',
        artifactAccepted: 'Accepted files',
        artifactRejected: 'Rejected files',
        artifactDetails: 'Recent file classification',
        noArtifactData: 'No recent artifact data',
        artifactKept: 'Accepted',
        artifactDiscarded: 'Rejected',
        technicalCodes: 'Technical codes',
    }), [isVietnamese])

    const refresh = async () => {
        setIsLoading(true)
        try {
            const [nextOverview, nextAudit] = await Promise.all([
                getSkillFoundryOverview(),
                getSkillFoundryAudit(20),
            ])
            setOverview(nextOverview)
            setAuditItems(nextAudit.items)
        } catch (error) {
            showError(error instanceof Error ? error.message : 'Failed to load admin console')
        } finally {
            setIsLoading(false)
        }
    }

    useEffect(() => {
        void refresh()
    }, [])

    useEffect(() => {
        const releases = overview?.releases || []
        if (!releases.length) {
            if (rollbackReleaseId) {
                setRollbackReleaseId('')
            }
            return
        }
        if (!rollbackReleaseId || !releases.some((release) => release.release_id === rollbackReleaseId)) {
            setRollbackReleaseId(releases[0].release_id)
        }
    }, [overview, rollbackReleaseId])

    const handleCompile = async () => {
        setIsBusy(true)
        try {
            const report = await compileSkillFoundryDraft({
                inputPath: compilePath.trim() || undefined,
                dryRun: compileDryRun,
                maxFiles: Number.isFinite(Number(compileMaxFiles)) ? Number(compileMaxFiles) : undefined,
            })
            setLastReport(report)
            showSuccess(compileDryRun ? 'Dry-run compile completed' : 'Draft registry compiled')
            await refresh()
        } catch (error) {
            showError(error instanceof Error ? error.message : 'Compile failed')
        } finally {
            setIsBusy(false)
        }
    }

    const handlePublish = async () => {
        setIsBusy(true)
        try {
            const result = await publishSkillFoundryDraft(publishNote.trim())
            showSuccess(`Published release ${result.release.release_id}`)
            setPublishNote('')
            await refresh()
        } catch (error) {
            showError(error instanceof Error ? error.message : 'Publish failed')
        } finally {
            setIsBusy(false)
        }
    }

    const handleRollback = async () => {
        setIsBusy(true)
        try {
            const result = await rollbackSkillFoundryRelease(rollbackReleaseId.trim(), rollbackNote.trim())
            showSuccess(`Rolled back to ${String(result.rollback.release_id || rollbackReleaseId.trim())}`)
            setRollbackNote('')
            await refresh()
        } catch (error) {
            showError(error instanceof Error ? error.message : 'Rollback failed')
        } finally {
            setIsBusy(false)
        }
    }

    const handleResolve = async () => {
        if (!resolveMessage.trim()) {
            showError(isVietnamese ? 'Hãy nhập message kiểm thử' : 'Enter a test message')
            return
        }
        setIsBusy(true)
        try {
            const result = await resolveSkillFoundryMatches({ message: resolveMessage.trim() })
            setResolveState(result)
        } catch (error) {
            showError(error instanceof Error ? error.message : 'Resolve failed')
        } finally {
            setIsBusy(false)
        }
    }

    if (isLoading) {
        return <div className="admin-console admin-console-loading">{copy.loading}</div>
    }

    const summary = overview?.summary
    const activeSkills = overview?.active_registry.active_skills || []
    const draftSkills = overview?.draft_registry.active_skills || []
    const challengers = overview?.catalog.challengers || []
    const rejected = overview?.catalog.rejected || []
    const releases = overview?.releases || []
    const liveReleaseId = overview?.active_registry.release_id || releases[0]?.release_id || '-'
    const selectedRelease = releases.find((release) => release.release_id === rollbackReleaseId) || null
    const activityItems = auditItems.map((item) => toActivityItem(item, copy, isVietnamese))
    const lastReportId = typeof lastReport?.report_id === 'string' ? lastReport.report_id : null
    const publishGate = resolvePublishGate(overview, lastReport)
    const publishGateBlockerLabels = publishGate
        ? publishGate.blockers.map((code) => describePublishGateCode(code, 'blocker', isVietnamese))
        : []
    const publishGateWarningLabels = publishGate
        ? publishGate.warnings.map((code) => describePublishGateCode(code, 'warning', isVietnamese))
        : []
    const artifactRetention = resolveArtifactRetention(overview, lastReport)

    return (
        <div className="admin-console">
            <div className="admin-console-header">
                <div>
                    <h1>{copy.title}</h1>
                    <p>{copy.subtitle}</p>
                </div>
                <button className="admin-btn admin-btn-secondary" onClick={() => void refresh()} disabled={isBusy}>
                    {copy.refresh}
                </button>
            </div>

            <section className="admin-card admin-card-summary">
                <div className="admin-section-header">
                    <div>
                        <h2>{copy.summary}</h2>
                        <p>{copy.recentSummary}</p>
                    </div>
                </div>
                <div className="admin-metric-grid">
                    <MetricCard label={copy.liveRelease} value={liveReleaseId} tone="neutral" />
                    <MetricCard label={copy.activeSkills} value={String(summary?.active_skill_count ?? 0)} tone="good" />
                    <MetricCard label={copy.draftSkills} value={String(summary?.draft_skill_count ?? 0)} tone="neutral" />
                    <MetricCard label={copy.availableRollback} value={String(summary?.release_count ?? 0)} tone="neutral" />
                </div>
                <div className="admin-summary-meta">
                    <div><strong>{copy.activeGeneratedAt}:</strong> {formatTimestamp(summary?.generated_at, isVietnamese)}</div>
                    <div><strong>{copy.draftGeneratedAt}:</strong> {formatTimestamp(summary?.draft_generated_at, isVietnamese)}</div>
                </div>
            </section>

            <div className="admin-grid admin-grid-main">
                <section className="admin-card">
                    <h2>{copy.importTitle}</h2>
                    <p className="admin-card-hint">{copy.importHint}</p>
                    <label className="admin-field">
                        <span>{copy.compilePath}</span>
                        <input value={compilePath} onChange={(e) => setCompilePath(e.target.value)} placeholder={copy.compilePathHint} />
                    </label>
                    <details className="admin-details">
                        <summary>{copy.importOptions}</summary>
                        <div className="admin-details-body">
                            <div className="admin-inline-fields">
                                <label className="admin-field">
                                    <span>{copy.compileMaxFiles}</span>
                                    <input value={compileMaxFiles} onChange={(e) => setCompileMaxFiles(e.target.value)} />
                                </label>
                                <label className="admin-checkbox">
                                    <input type="checkbox" checked={compileDryRun} onChange={(e) => setCompileDryRun(e.target.checked)} />
                                    <span>{copy.dryRun}</span>
                                </label>
                            </div>
                            <div className="admin-helper-text">{copy.dryRunNote}</div>
                        </div>
                    </details>
                    <button className="admin-btn" onClick={() => void handleCompile()} disabled={isBusy}>
                        {copy.compile}
                    </button>
                    <div className="admin-inline-note">
                        <strong>{copy.compileDone}:</strong> {lastReportId || copy.reportUnavailable}
                    </div>
                    {artifactRetention && (
                        <div className="admin-intake-block">
                            <div><strong>{copy.artifactIntake}:</strong></div>
                            <div>{copy.artifactMoved}: {artifactRetention.moved_count}</div>
                            <div>{copy.artifactAccepted}: {artifactRetention.accepted_artifact_count}</div>
                            <div>{copy.artifactRejected}: {artifactRetention.rejected_artifact_count}</div>
                        </div>
                    )}
                </section>

                <section className="admin-card">
                    <h2>{copy.currentLive}</h2>
                    <div className="admin-status-block">
                        <div><strong>{copy.releaseLabel}:</strong> {liveReleaseId}</div>
                        <div><strong>{copy.skillCount}:</strong> {summary?.active_skill_count ?? 0}</div>
                        <div><strong>{copy.lastUpdated}:</strong> {formatTimestamp(summary?.generated_at, isVietnamese)}</div>
                    </div>
                    <h3>{copy.topSkills}</h3>
                    <SkillList skills={activeSkills} emptyText={copy.noData} limit={5} moreLabel={copy.moreSkills} />
                </section>

                <section className="admin-card">
                    <h2>{copy.publishTitle}</h2>
                    <p className="admin-card-hint">{copy.publishHint}</p>
                    <div className="admin-status-block">
                        <div><strong>{copy.readyToPublish}:</strong> {draftSkills.length > 0 ? copy.activitySuccess : copy.noData}</div>
                        <div><strong>{copy.skillCount}:</strong> {summary?.draft_skill_count ?? 0}</div>
                        <div><strong>{copy.draftGeneratedAt}:</strong> {formatTimestamp(summary?.draft_generated_at, isVietnamese)}</div>
                    </div>
                    {publishGate && (
                        <div className={`admin-gate-block ${publishGate.ready ? 'is-ready' : 'is-blocked'}`}>
                            <div><strong>{copy.publishGate}:</strong> {publishGate.ready ? copy.publishGateReady : copy.publishGateBlocked}</div>
                            {publishGateBlockerLabels.length > 0 && (
                                <div><strong>{copy.publishBlockers}:</strong> {publishGateBlockerLabels.join(' ')}</div>
                            )}
                            {publishGateWarningLabels.length > 0 && (
                                <div><strong>{copy.publishWarnings}:</strong> {publishGateWarningLabels.join(' ')}</div>
                            )}
                            {showAdvanced && publishGate && (publishGate.blockers.length > 0 || publishGate.warnings.length > 0) && (
                                <div><strong>{copy.technicalCodes}:</strong> {[...publishGate.blockers, ...publishGate.warnings].join(', ')}</div>
                            )}
                        </div>
                    )}
                    <label className="admin-field">
                        <span>{copy.publishNote}</span>
                        <textarea value={publishNote} onChange={(e) => setPublishNote(e.target.value)} rows={3} placeholder={copy.noteOptional} />
                    </label>
                    <button className="admin-btn" onClick={() => void handlePublish()} disabled={isBusy || draftSkills.length === 0}>
                        {copy.publish}
                    </button>
                </section>

                <section className="admin-card">
                    <h2>{copy.currentDraft}</h2>
                    <div className="admin-status-block">
                        <div><strong>{copy.skillCount}:</strong> {summary?.draft_skill_count ?? 0}</div>
                        <div><strong>{copy.challengers}:</strong> {summary?.challenger_count ?? 0}</div>
                        <div><strong>{copy.rejected}:</strong> {summary?.rejected_count ?? 0}</div>
                    </div>
                    <h3>{copy.topSkills}</h3>
                    <SkillList skills={draftSkills} emptyText={copy.noData} limit={5} moreLabel={copy.moreSkills} />
                </section>

                <section className="admin-card">
                    <h2>{copy.rollbackTitle}</h2>
                    <p className="admin-card-hint">{copy.rollbackHint}</p>
                    <label className="admin-field">
                        <span>{copy.rollbackReleaseId}</span>
                        <select value={rollbackReleaseId} onChange={(e) => setRollbackReleaseId(e.target.value)}>
                            {!releases.length && <option value="">{copy.emptyRelease}</option>}
                            {releases.map((release: SkillFoundryRelease) => (
                                <option key={release.release_id} value={release.release_id}>
                                    {release.release_id}
                                </option>
                            ))}
                        </select>
                    </label>
                    <div className="admin-status-block">
                        <div><strong>{copy.selectedRelease}:</strong> {selectedRelease?.release_id || copy.noSelectedRelease}</div>
                        <div><strong>{copy.skillCount}:</strong> {selectedRelease?.active_skill_count ?? 0}</div>
                        <div><strong>{copy.lastUpdated}:</strong> {formatTimestamp(selectedRelease?.released_at, isVietnamese)}</div>
                    </div>
                    <label className="admin-field">
                        <span>{copy.rollbackNote}</span>
                        <textarea value={rollbackNote} onChange={(e) => setRollbackNote(e.target.value)} rows={3} placeholder={copy.noteOptional} />
                    </label>
                    <button className="admin-btn admin-btn-danger" onClick={() => void handleRollback()} disabled={isBusy || !rollbackReleaseId.trim()}>
                        {copy.rollback}
                    </button>
                </section>

                <section className="admin-card">
                    <h2>{copy.activity}</h2>
                    {activityItems.length === 0 ? <div className="admin-empty">{copy.noAudit}</div> : (
                        <div className="admin-activity-list">
                            {activityItems.map((item) => (
                                <div key={item.id} className={`admin-activity-item status-${item.status}`}>
                                    <div className="admin-activity-top">
                                        <strong>{item.title}</strong>
                                        <span>{item.timestamp}</span>
                                    </div>
                                    <div>{item.summary}</div>
                                </div>
                            ))}
                        </div>
                    )}
                </section>
            </div>

            <section className="admin-card admin-card-advanced">
                <div className="admin-section-header">
                    <div>
                        <h2>{copy.advanced}</h2>
                        <p>{copy.advancedHint}</p>
                    </div>
                    <button className="admin-btn admin-btn-secondary" onClick={() => setShowAdvanced((value) => !value)}>
                        {showAdvanced ? copy.hideAdvanced : copy.showAdvanced}
                    </button>
                </div>

                {showAdvanced && (
                    <div className="admin-grid admin-grid-advanced">
                        <section className="admin-subpanel">
                            <h3>{copy.resolveTitle}</h3>
                            <label className="admin-field">
                                <span>{copy.resolveMessage}</span>
                                <textarea value={resolveMessage} onChange={(e) => setResolveMessage(e.target.value)} rows={4} />
                            </label>
                            <button className="admin-btn admin-btn-secondary" onClick={() => void handleResolve()} disabled={isBusy}>
                                {copy.resolve}
                            </button>
                            {resolveState && (
                                <div className="admin-result-block">
                                    <div><strong>Intent:</strong> {resolveState.intent}</div>
                                    <div><strong>Keywords:</strong> {resolveState.keywords.join(', ') || '-'}</div>
                                    <div><strong>{copy.matchedSkills}:</strong> {resolveState.matches.length || copy.noMatches}</div>
                                    <SkillList skills={resolveState.matches} emptyText={copy.noMatches} limit={4} moreLabel={copy.moreSkills} />
                                    <details className="admin-details">
                                        <summary>{copy.viewRawPrompt}</summary>
                                        <div className="admin-details-body">
                                            <pre>{resolveState.formatted || '-'}</pre>
                                        </div>
                                    </details>
                                </div>
                            )}
                        </section>

                        <section className="admin-subpanel">
                            <h3>{copy.detailSummary}</h3>
                            <div className="admin-detail-group">
                                <div>
                                    <h4>{copy.challengers}</h4>
                                    <SkillList skills={challengers} emptyText={copy.noData} limit={8} moreLabel={copy.moreSkills} />
                                </div>
                                <div>
                                    <h4>{copy.rejected}</h4>
                                    <SkillList skills={rejected} emptyText={copy.noData} limit={8} moreLabel={copy.moreSkills} />
                                </div>
                            </div>
                        </section>

                        <section className="admin-subpanel">
                            <h3>{copy.releases}</h3>
                            {releases.length === 0 ? <div className="admin-empty">{copy.noReleases}</div> : (
                                <div className="admin-list">
                                    {releases.map((release: SkillFoundryRelease) => (
                                        <div key={release.release_id} className="admin-list-item admin-list-item-static">
                                            <strong>{release.release_id}</strong>
                                            <span>{formatTimestamp(release.released_at, isVietnamese)}</span>
                                            <span>{release.active_skill_count} skills</span>
                                        </div>
                                    ))}
                                </div>
                            )}
                        </section>

                        <section className="admin-subpanel">
                            <h3>{copy.artifactDetails}</h3>
                            {artifactRetention?.sample_moved_items?.length ? (
                                <ArtifactMoveList
                                    items={artifactRetention.sample_moved_items}
                                    acceptedLabel={copy.artifactKept}
                                    rejectedLabel={copy.artifactDiscarded}
                                />
                            ) : (
                                <div className="admin-empty">{copy.noArtifactData}</div>
                            )}
                        </section>

                        <section className="admin-subpanel">
                            <h3>{copy.report}</h3>
                            {!lastReport ? <div className="admin-empty">{copy.reportUnavailable}</div> : (
                                <details className="admin-details" open>
                                    <summary>{copy.reportId}: {lastReportId || '-'}</summary>
                                    <div className="admin-details-body">
                                        <pre>{JSON.stringify(lastReport, null, 2)}</pre>
                                    </div>
                                </details>
                            )}
                        </section>
                    </div>
                )}
            </section>
        </div>
    )
}

const MetricCard = ({ label, value, tone }: { label: string; value: string; tone: 'neutral' | 'good' }) => (
    <div className={`admin-metric-card tone-${tone}`}>
        <span>{label}</span>
        <strong>{value}</strong>
    </div>
)

const SkillList = ({
    skills,
    emptyText,
    limit = 5,
    moreLabel,
}: {
    skills: SkillFoundrySkill[]
    emptyText: string
    limit?: number
    moreLabel: string
}) => {
    if (!skills.length) {
        return <div className="admin-empty">{emptyText}</div>
    }

    const visibleSkills = skills.slice(0, limit)
    const hiddenCount = Math.max(skills.length - visibleSkills.length, 0)

    return (
        <div className="admin-list admin-list-compact">
            {visibleSkills.map((skill) => (
                <div key={skill.skill_id} className="admin-list-item admin-list-item-static">
                    <strong>{skill.title || skill.skill_id}</strong>
                    <span>{skill.domain || '-'}</span>
                    <span>score: {typeof skill.score_total === 'number' ? skill.score_total : '-'}</span>
                </div>
            ))}
            {hiddenCount > 0 && <div className="admin-more-count">+ {hiddenCount} {moreLabel}</div>}
        </div>
    )
}

const ArtifactMoveList = ({
    items,
    acceptedLabel,
    rejectedLabel,
}: {
    items: SkillFoundryArtifactMove[]
    acceptedLabel: string
    rejectedLabel: string
}) => (
    <div className="admin-list admin-list-compact">
        {items.map((item, index) => (
            <div key={`${item.source_path}-${index}`} className="admin-list-item admin-list-item-static">
                <strong>{item.status === 'accepted' ? acceptedLabel : rejectedLabel}</strong>
                <span>{shortArtifactPath(item.source_path)}</span>
                <span>{shortArtifactPath(item.destination_path)}</span>
            </div>
        ))}
    </div>
)

function formatTimestamp(value: string | null | undefined, isVietnamese: boolean): string {
    if (!value) {
        return '-'
    }
    const parsed = new Date(value)
    if (Number.isNaN(parsed.getTime())) {
        return value
    }
    return new Intl.DateTimeFormat(isVietnamese ? 'vi-VN' : 'en-US', {
        dateStyle: 'short',
        timeStyle: 'short',
    }).format(parsed)
}

function toActivityItem(
    item: SkillFoundryAuditEvent,
    copy: AdminCopy,
    isVietnamese: boolean,
): ActivityItem {
    const normalizedAction = (item.action || '').trim().toLowerCase()
    let title = copy.activityDefault
    if (normalizedAction === 'compile') {
        title = copy.activityCompile
    } else if (normalizedAction === 'publish') {
        title = copy.activityPublish
    } else if (normalizedAction === 'rollback') {
        title = copy.activityRollback
    }

    const normalizedStatus = normalizeStatus(item.status)
    const statusLabel = normalizedStatus === 'failed'
        ? copy.activityFailed
        : normalizedStatus === 'pending'
            ? copy.activityPending
            : copy.activitySuccess

    const summary = [
        item.summary,
        item.resource_id && item.resource_id !== 'skill_foundry' ? item.resource_id : null,
        statusLabel,
    ].filter(Boolean).join(' • ')

    return {
        id: item.id,
        title,
        summary: summary || title,
        timestamp: formatTimestamp(item.created_at, isVietnamese),
        status: normalizedStatus,
    }
}

function normalizeStatus(status: string | null | undefined): string {
    const value = (status || '').trim().toLowerCase()
    if (value === 'failed' || value === 'error') {
        return 'failed'
    }
    if (value === 'pending' || value === 'running') {
        return 'pending'
    }
    return 'success'
}

function describePublishGateCode(
    code: string,
    type: 'blocker' | 'warning',
    isVietnamese: boolean,
): string {
    const normalized = String(code || '').trim()
    if (!normalized) {
        return ''
    }

    const dictionary = type === 'blocker'
        ? (isVietnamese ? PUBLISH_GATE_BLOCKER_LABELS.vi : PUBLISH_GATE_BLOCKER_LABELS.en)
        : (isVietnamese ? PUBLISH_GATE_WARNING_LABELS.vi : PUBLISH_GATE_WARNING_LABELS.en)

    return dictionary[normalized as keyof typeof dictionary]
        || (
            isVietnamese
                ? `${type === 'blocker' ? 'Lý do nội bộ' : 'Cảnh báo nội bộ'}: ${normalized}`
                : `${type === 'blocker' ? 'Internal blocker' : 'Internal warning'}: ${normalized}`
        )
}

function resolvePublishGate(
    overview: SkillFoundryOverview | null,
    lastReport: Record<string, unknown> | null,
): SkillFoundryPublishGate | null {
    const reportGate = lastReport?.publish_gate
    if (isPublishGate(reportGate)) {
        return reportGate
    }
    if (isPublishGate(overview?.publish_gate)) {
        return overview?.publish_gate ?? null
    }
    if (isPublishGate(overview?.summary?.publish_gate)) {
        return overview?.summary?.publish_gate ?? null
    }
    if (isPublishGate(overview?.draft_registry?.publish_gate)) {
        return overview?.draft_registry?.publish_gate ?? null
    }
    return null
}

function resolveArtifactRetention(
    overview: SkillFoundryOverview | null,
    lastReport: Record<string, unknown> | null,
): SkillFoundryArtifactRetentionSummary | null {
    const reportRetention = lastReport?.artifact_retention
    if (isArtifactRetentionSummary(reportRetention)) {
        return reportRetention
    }
    const reports = overview?.catalog?.reports || []
    const latestCatalogReport = reports[reports.length - 1]
    if (isArtifactRetentionSummary(latestCatalogReport?.artifact_retention)) {
        return latestCatalogReport?.artifact_retention ?? null
    }
    return null
}

function isPublishGate(value: unknown): value is SkillFoundryPublishGate {
    return Boolean(
        value
        && typeof value === 'object'
        && Array.isArray((value as SkillFoundryPublishGate).blockers)
        && Array.isArray((value as SkillFoundryPublishGate).warnings)
        && typeof (value as SkillFoundryPublishGate).ready === 'boolean',
    )
}

function isArtifactRetentionSummary(value: unknown): value is SkillFoundryArtifactRetentionSummary {
    return Boolean(
        value
        && typeof value === 'object'
        && typeof (value as SkillFoundryArtifactRetentionSummary).enabled === 'boolean'
        && typeof (value as SkillFoundryArtifactRetentionSummary).moved_count === 'number'
        && typeof (value as SkillFoundryArtifactRetentionSummary).accepted_artifact_count === 'number'
        && typeof (value as SkillFoundryArtifactRetentionSummary).rejected_artifact_count === 'number',
    )
}

function shortArtifactPath(path: string): string {
    const normalized = String(path || '').replace(/\\/g, '/').trim()
    if (!normalized) {
        return '-'
    }
    const parts = normalized.split('/').filter(Boolean)
    return parts.slice(-4).join('/')
}

export default AdminConsole
