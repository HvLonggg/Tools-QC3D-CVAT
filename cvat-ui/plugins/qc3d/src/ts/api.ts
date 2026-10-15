// Copyright (C) CVAT.ai Corporation
//
// SPDX-License-Identifier: MIT

import { CVATCore } from 'cvat-core-wrapper';

export type QcRole = 'lead' | 'reviewer' | 'qa' | 'ml_engineer';
export type IssueStatus = 'open' | 'confirmed' | 'waived';
export type Severity = 'blocker' | 'major' | 'minor';

export interface Qc3dJob {
    id: number;
    task_id: number;
    task_name: string;
    stage: string;
    state: string;
    assignee: string | null;
    updated_date: string;
    last_qc_date: string | null;
    open_issues: number | null;
}

export interface Qc3dSummary {
    is_admin: boolean;
    roles: QcRole[];
    tasks_3d: number;
    jobs_3d: number;
    jobs_3d_by_stage: Record<string, number>;
    recent_jobs: Qc3dJob[];
}

export interface QcIssue {
    id: number;
    rule: string;
    severity: Severity;
    frame: number;
    uid: string;
    label: string;
    message: string;
    evidence: Record<string, unknown>;
    status: IssueStatus;
    decided_by: string | null;
    decided_date: string | null;
    decision_reason: string;
    proposal: {
        kind: 'update' | 'delete' | 'create';
        summary: string;
        changes?: Record<string, unknown>;
        // create: the new boxes; method: how the correction was built (LiDAR points, model, class only)
        shapes?: { label: string; frame: number; points: number[]; score: number }[];
        method?: 'points' | 'model' | 'class' | 'rotation';
        verified?: boolean;
        checks?: string[];
    } | null;
    fix_status: 'none' | 'applied' | 'reverted';
    fixed_by: string | null;
    fixed_date: string | null;
}

export interface TaxonomyClass {
    name: string;
    name_vi: string;
    description: string;
    size_min: [number, number, number];
    size_max: [number, number, number];
    size_mean: [number, number, number];
    points_at_10m: number;
    max_speed: number;
}

export interface GuidelineClass {
    name: string;
    name_vi: string;
    description: string;
    size_min: [number, number, number];
    size_max: [number, number, number];
    size_mean?: [number, number, number];
    size_std?: [number, number, number];
    points_at_10m?: number;
    max_speed?: number;
}

export interface GuidelineContent {
    format: number;
    classes: GuidelineClass[];
    thresholds: Record<string, number>;
    rules: Record<string, boolean>;
    // what autofix may change under this guideline (absent: everything, no class excluded)
    autofix?: GuidelineAutofix;
    notes: string;
}

export interface GuidelineAutofix {
    exclude_classes: string[];
    fix: Record<string, boolean>;
}

export interface GuidelineTarget {
    id: number;
    name: string;
}

export interface GuidelineSummary {
    id: number;
    name: string;
    description: string;
    revision: number;
    source_name: string;
    is_default: boolean;
    classes: string[];
    projects: GuidelineTarget[];
    tasks: GuidelineTarget[];
    hidden_bindings: number;
    created_by: string | null;
    updated_by: string | null;
    updated_date: string;
}

export interface Guideline extends GuidelineSummary {
    content: GuidelineContent;
}

export interface GuidelineList {
    can_manage: boolean;
    results: GuidelineSummary[];
    targets: { projects: GuidelineTarget[]; tasks: GuidelineTarget[] };
    template: GuidelineContent;
    thresholds: { key: string; label: string; min: number; max: number; default: number }[];
    rules: { code: string; title: string }[];
    autofix_kinds: { key: string; title: string }[];
}

export interface GuidelineInput {
    name: string;
    description: string;
    is_default: boolean;
    source_name: string;
    content: GuidelineContent;
    projects: number[];
    tasks: number[];
}

export interface ParsedGuideline {
    source_name: string;
    content: GuidelineContent;
    warnings: string[];
    // sentences of the guide that were turned into settings, with what they set
    compiled?: string[];
}

export interface CompiledGuideline {
    content: GuidelineContent;
    warnings: string[];
    compiled: string[];
}

export interface JobGuideline {
    source: 'task' | 'cvat_guide_task' | 'project' | 'cvat_guide_project' | 'default' | 'builtin';
    source_text: string;
    id: number | null;
    name: string;
    revision: number | string;
    key: string;
    content: GuidelineContent;
    warnings: string[];
    skipped: string[];
    labels_without_class: string[];
    classes_without_label: string[];
    last_run_key: string | null;
}

export interface QcCoverage {
    rule: string;
    status: 'checked' | 'not_checked';
    reason: string;
    issues: number;
}

export interface QcReport {
    job: {
        id: number;
        task_id: number;
        task_name: string;
        stage: string;
        state: string;
        start_frame: number;
        stop_frame: number;
    };
    run: {
        id: number;
        created_date: string;
        created_by: string | null;
        rules_version: string;
        objects_count: number;
        duration_ms: number;
        coverage: QcCoverage[];
        guideline: { key?: string; name?: string; revision?: number | string; source?: string };
        frames?: number[] | null;  // checked frames (None: whole job)
    } | null;
    issues: QcIssue[];
}

let core: CVATCore | null = null;

export function setCore(instance: CVATCore | null): void {
    core = instance;
}

type Method = 'GET' | 'POST' | 'PUT' | 'DELETE';

async function request<T>(path: string, method: Method = 'GET', data?: object): Promise<T> {
    if (!core) {
        throw new Error('QC 3D plugin is not initialized');
    }

    const response = await core.server.request(`${core.config.backendAPI}/qc3d/${path}`, { method, data });
    return (response as { data: T }).data;
}

export const getSummary = (): Promise<Qc3dSummary> => request('summary');

export interface Person {
    id: number;
    username: string;
    full_name: string;
}

export interface QualityRating {
    key: 'excellent' | 'good' | 'pass' | 'improve';
    text: string;
}

export interface QualityCounts {
    objects: number;
    error_objects: number;
    confirmed: number;
    fixed: number;
    waived: number;
    open: number;
    score: number | null;
    rating: QualityRating | null;
}

export interface QualityJob extends QualityCounts {
    id: number;
    task_id: number;
    task_name: string;
    project_name: string | null;
    stage: string;
    state: string;
    review_status: string;
    review_status_text: string;
    updated_date: string;
    last_qc_date: string | null;
    qc_by: Person | null;
    current_assignee: Person | null;
    reviewers: (Person & { roles: string[] })[];
}

export interface QualityOverview {
    viewer: { id: number; username: string; can_view_team: boolean };
    subject: Person;
    people: Person[];
    summary: Omit<QualityCounts, 'rating'> & {
        jobs: number;
        checked_jobs: number;
        rating: QualityRating | null;
        provisional: boolean;
        confirm_rate: number | null;
    };
    status_counts: { key: string; text: string; count: number }[];
    by_rule: { rule: string; title: string; count: number }[];
    by_label: { label: string; count: number }[];
    by_severity: Record<string, number>;
    trend: { week: string; jobs: number; objects: number; error_objects: number; score: number | null }[];
    jobs: QualityJob[];
}

export interface FeedbackIssue {
    id: number;
    rule: string;
    rule_title: string;
    severity: Severity;
    frame: number;
    label: string;
    message: string;
    outcome: 'confirmed' | 'fixed' | 'open' | 'waived';
    fix_summary: string | null;
    decided_by: Person | null;
    decided_date: string | null;
    decision_reason: string;
    fixed_by: Person | null;
    fixed_date: string | null;
    found_date: string;
}

export interface JobFeedback extends QualityCounts {
    job: {
        id: number;
        task_id: number;
        task_name: string;
        stage: string;
        state: string;
        review_status: string;
        review_status_text: string;
    };
    last_qc_date: string | null;
    qc_by: Person | null;
    guideline: string | null;
    reviewers: (Person & { roles: string[] })[];
    history: { user: Person; stage: string; started_date: string; ended_date: string | null }[];
    issues: FeedbackIssue[];
}

export interface QcAnalytics {
    days: number;
    since: string;
    totals: {
        jobs: number;
        checked_jobs: number;
        runs: number;
        objects: number;
        error_objects: number;
        score: number | null;
        rating: QualityRating | null;
        found: number;
        confirmed: number;
        waived: number;
        open: number;
        fixed: number;
        reverted: number;
        precision: number | null;
        median_decision_hours: number | null;
    };
    daily: { day: string; runs: number; found: number; confirmed: number; waived: number; fixed: number }[];
    by_rule: { rule: string; title: string; confirmed: number; waived: number; open: number }[];
    by_label: { label: string; count: number }[];
    open_by_severity: Record<'blocker' | 'major' | 'minor', number>;
    status_counts: { key: string; text: string; count: number }[];
    attention: {
        id: number;
        task_id: number;
        task_name: string;
        stage: string;
        open: number;
        checked: boolean;
        assignee: string | null;
        last_qc_date: string | null;
    }[];
    // only for lead / QA (annotators) and QC roles (reviewers)
    annotators: (Person & {
        jobs: number;
        objects: number;
        error_objects: number;
        confirmed: number;
        open: number;
        score: number | null;
        rating: QualityRating | null;
    })[] | null;
    reviewers: (Person & { runs: number; decisions: number; confirmed: number; waived: number; fixes: number })[] | null;
    filters: {
        projects: { id: number; name: string }[];
        tasks: { id: number; name: string; project_id: number | null }[];
    };
}

export const getAnalytics = (days: number, projectId: number | null, taskId: number | null): Promise<QcAnalytics> => {
    const query = [`days=${days}`];
    if (projectId !== null) query.push(`project_id=${projectId}`);
    if (taskId !== null) query.push(`task_id=${taskId}`);
    return request(`analytics?${query.join('&')}`);
};

export interface DetectStatus {
    model: { key: string; name: string; trained_on: string; trusted: string; available: boolean };
    frames_total: number;
    frames_done: number;
    running: boolean;
    last_message: string;
}

export interface MarkedFixResult {
    run: number;
    applied: { uid: string; frame: number; summary: string }[];
    failed: { uid: string; frame: number | null; reason: string }[];
}

export interface CheckStatus {
    stage: 'idle' | 'model' | 'qc' | 'fix' | 'done' | 'error';
    done?: number;
    total?: number;
    frame?: number | null;
    run?: number;
    issues?: number;
    error?: string;
    // boxes marked by the reviewer: their number, and what auto-fix did with them
    marked?: number;
    fixed?: MarkedFixResult | null;
}

export const getCheckStatus = (jobId: number): Promise<CheckStatus> => request(`jobs/${jobId}/check`);
export const startCheck = (jobId: number, frame: number | null, marked?: string[]): Promise<CheckStatus> => (
    request(`jobs/${jobId}/check`, 'POST', {
        ...(frame === null ? {} : { frame }),
        ...(marked?.length ? { marked } : {}),
    })
);

export const getDetectStatus = (jobId: number): Promise<DetectStatus> => request(`jobs/${jobId}/detect`);
export const startDetect = (jobId: number): Promise<DetectStatus> => request(`jobs/${jobId}/detect`, 'POST');

const forUser = (userId: number | null): string => (userId === null ? '' : `?user=${encodeURIComponent(userId)}`);
export const getQuality = (userId: number | null): Promise<QualityOverview> => request(`quality${forUser(userId)}`);
export const getQualityJob = (jobId: number, userId: number | null): Promise<JobFeedback> => (
    request(`quality/jobs/${jobId}${forUser(userId)}`)
);
export const getReport = (jobId: number): Promise<QcReport> => request(`jobs/${jobId}/report`);
export const runQc = (jobId: number): Promise<QcReport> => request(`jobs/${jobId}/run`, 'POST');
export const decideIssue = (issueId: number, status: IssueStatus, reason = ''): Promise<QcIssue> => (
    request(`issues/${issueId}/decision`, 'POST', { status, reason })
);

export const applyFix = (issueId: number): Promise<QcIssue> => request(`issues/${issueId}/fix`, 'POST');
export const revertFix = (issueId: number): Promise<QcIssue> => request(`issues/${issueId}/revert`, 'POST');
export const getTaxonomy = (): Promise<{ version: string; classes: TaxonomyClass[] }> => request('taxonomy');

export const getJobGuideline = (jobId: number): Promise<JobGuideline> => request(`jobs/${jobId}/guideline`);
export const getGuidelines = (): Promise<GuidelineList> => request('guidelines');
export const getGuideline = (id: number): Promise<Guideline> => request(`guidelines/${id}`);
export const createGuideline = (data: GuidelineInput): Promise<Guideline> => request('guidelines', 'POST', data);
export const updateGuideline = (id: number, data: GuidelineInput): Promise<Guideline> => (
    request(`guidelines/${id}`, 'PUT', data)
);
export const deleteGuideline = (id: number): Promise<void> => request(`guidelines/${id}`, 'DELETE');
export const parseGuidelineFile = (filename: string, contentBase64: string): Promise<ParsedGuideline> => (
    request('guidelines/parse', 'POST', { filename, content_base64: contentBase64 })
);
export const compileGuideline = (content: GuidelineContent, text: string): Promise<CompiledGuideline> => (
    request('guidelines/compile', 'POST', { content, text })
);
export const parseCvatGuide = (target: { task_id?: number; project_id?: number }): Promise<ParsedGuideline> => (
    request('guidelines/parse', 'POST', target)
);

export interface JobQcSettings {
    autofix_excluded_labels: string[];
    // auto-fix also adds boxes for objects the model sees without a label
    autofix_create: boolean;
    labels: string[];
    can_decide: boolean;
    can_fix: boolean;
    can_run: boolean;
}

export interface AutofixPlanned {
    issue: number;
    uid: string;
    frame: number;
    label: string;
    summary: string;
    kinds: string[];
    new_boxes: string[];
}

export interface AutofixSkipped {
    issue: number;
    uid: string;
    frame: number;
    reason: string;
    summary: string;
    checks: string[];
}

export interface AutofixResult {
    run: number;
    // set when the QC was redone first (labels, guideline or rules changed since the last run)
    // set when the labels, guideline or rules changed since the QC run: run QC again first
    stale_reason: string | null;
    frames: number[] | null;
    guideline: {
        name: string;
        excluded_labels: string[];
        allowed_kinds: string[];
        blocked_kinds: string[];
    };
    planned: AutofixPlanned[];
    skipped: AutofixSkipped[];
    applied: number[];
    failed: { issue: number; uid: string; frame: number; reason: string }[];
    excluded_labels: string[];
}

export const getJobSettings = (jobId: number): Promise<JobQcSettings> => request(`jobs/${jobId}/settings`);
export const setExcludedLabels = (jobId: number, labels: string[]): Promise<JobQcSettings> => (
    request(`jobs/${jobId}/settings`, 'PUT', { autofix_excluded_labels: labels })
);
export const setAutofixCreate = (jobId: number, create: boolean): Promise<JobQcSettings> => (
    request(`jobs/${jobId}/settings`, 'PUT', { autofix_create: create })
);
export const autofix = (jobId: number, dryRun: boolean): Promise<AutofixResult> => (
    request(`jobs/${jobId}/autofix`, 'POST', { dry_run: dryRun })
);

export function errorText(err: any): string {
    const data = err?.response?.data;
    if (data && typeof data === 'object') {
        return Object.values(data).flat().map(String).join('; ');
    }
    return err?.message ?? String(err);
}

export function cvatObjectLink(report: QcReport, issue: QcIssue): string {
    // uid: "s:<shape id>" or "t:<track id>:<frame>"
    const { task_id: taskId, id: jobId } = report.job;
    return `/tasks/${taskId}/jobs/${jobId}?frame=${issue.frame}&qcFrame=${issue.frame}` +
        `&qcFocus=${encodeURIComponent(issue.uid)}`;
}
