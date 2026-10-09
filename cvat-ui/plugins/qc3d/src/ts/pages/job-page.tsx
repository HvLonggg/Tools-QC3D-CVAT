// Copyright (C) CVAT.ai Corporation
//
// SPDX-License-Identifier: MIT

import React from 'react';
import { Link, useParams } from 'react-router-dom';
import Alert from 'antd/lib/alert';
import Button from 'antd/lib/button';
import Card from 'antd/lib/card';
import Descriptions from 'antd/lib/descriptions';
import Spin from 'antd/lib/spin';

import useSummary from '../use-summary';

export default function JobPage(): JSX.Element {
    const { jobId } = useParams<{ jobId: string }>();
    const { summary, error, loading } = useSummary();

    if (loading) return <Spin size='large' />;
    if (error || !summary) {
        return <Alert type='error' message='Không tải được dữ liệu QC 3D' description={error} />;
    }

    // Sprint 1: job details come from the summary; a dedicated job endpoint arrives with the QC engine
    const job = summary.recent_jobs.find((item) => item.id === Number(jobId));
    if (!job) {
        return <Alert type='warning' message={`Không tìm thấy job 3D #${jobId} hoặc bạn không có quyền xem`} />;
    }

    return (
        <Card
            title={`QC 3D · Job #${job.id}`}
            extra={<Link to={`/tasks/${job.task_id}/jobs/${job.id}`}>Mở job trong CVAT</Link>}
        >
            <Descriptions size='small' column={2}>
                <Descriptions.Item label='Task'>{job.task_name}</Descriptions.Item>
                <Descriptions.Item label='Stage'>{job.stage}</Descriptions.Item>
                <Descriptions.Item label='State'>{job.state}</Descriptions.Item>
                <Descriptions.Item label='Người làm'>{job.assignee ?? '—'}</Descriptions.Item>
            </Descriptions>
            <Button type='primary' disabled style={{ marginTop: 16 }}>
                Chạy QC (Sprint 2)
            </Button>
        </Card>
    );
}
