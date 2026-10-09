// Copyright (C) CVAT.ai Corporation
//
// SPDX-License-Identifier: MIT

import { CVATCore } from 'cvat-core-wrapper';

export type QcRole = 'lead' | 'reviewer' | 'qa' | 'ml_engineer';

export interface Qc3dJob {
    id: number;
    task_id: number;
    task_name: string;
    stage: string;
    state: string;
    assignee: string | null;
    updated_date: string;
}

export interface Qc3dSummary {
    is_admin: boolean;
    roles: QcRole[];
    tasks_3d: number;
    jobs_3d: number;
    jobs_3d_by_stage: Record<string, number>;
    recent_jobs: Qc3dJob[];
}

let core: CVATCore | null = null;

export function setCore(instance: CVATCore | null): void {
    core = instance;
}

export async function getSummary(): Promise<Qc3dSummary> {
    if (!core) {
        throw new Error('QC 3D plugin is not initialized');
    }

    const response = await core.server.request(`${core.config.backendAPI}/qc3d/summary`, { method: 'GET' });
    return response.data;
}
