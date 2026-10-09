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
    } | null;
    issues: QcIssue[];
}

let core: CVATCore | null = null;

export function setCore(instance: CVATCore | null): void {
    core = instance;
}

async function request<T>(path: string, method: 'GET' | 'POST' = 'GET', data?: object): Promise<T> {
    if (!core) {
        throw new Error('QC 3D plugin is not initialized');
    }

    const response = await core.server.request(`${core.config.backendAPI}/qc3d/${path}`, { method, data });
    return (response as { data: T }).data;
}

export const getSummary = (): Promise<Qc3dSummary> => request('summary');
export const getReport = (jobId: number): Promise<QcReport> => request(`jobs/${jobId}/report`);
export const runQc = (jobId: number): Promise<QcReport> => request(`jobs/${jobId}/run`, 'POST');
export const decideIssue = (issueId: number, status: IssueStatus, reason = ''): Promise<QcIssue> => (
    request(`issues/${issueId}/decision`, 'POST', { status, reason })
);

export function cvatObjectLink(report: QcReport, issue: QcIssue): string {
    // uid: "s:<shape id>" or "t:<track id>:<frame>"
    const [kind, id] = issue.uid.split(':');
    const type = kind === 't' ? 'track' : 'shape';
    const { task_id: taskId, id: jobId } = report.job;
    return `/tasks/${taskId}/jobs/${jobId}?frame=${issue.frame}&serverID=${id}&type=${type}`;
}
