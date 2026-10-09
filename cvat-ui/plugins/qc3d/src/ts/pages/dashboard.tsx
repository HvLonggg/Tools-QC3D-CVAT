// Copyright (C) CVAT.ai Corporation
//
// SPDX-License-Identifier: MIT

import React from 'react';
import { Link } from 'react-router-dom';
import Alert from 'antd/lib/alert';
import Badge from 'antd/lib/badge';
import Card from 'antd/lib/card';
import { Col, Row } from 'antd/lib/grid';
import Spin from 'antd/lib/spin';
import Statistic from 'antd/lib/statistic';
import Table from 'antd/lib/table';
import Tag from 'antd/lib/tag';

import { Qc3dJob } from '../api';
import useSummary from '../use-summary';

const STAGES = ['annotation', 'validation', 'acceptance'];

function JobsTable({ jobs }: { jobs: Qc3dJob[] }): JSX.Element {
    const columns = [
        {
            title: 'Job',
            dataIndex: 'id',
            render: (id: number) => <Link to={`/qc3d/jobs/${id}`}>{`#${id}`}</Link>,
        },
        { title: 'Task', dataIndex: 'task_name' },
        { title: 'Stage', dataIndex: 'stage', render: (stage: string) => <Tag>{stage}</Tag> },
        { title: 'Người làm', dataIndex: 'assignee', render: (name: string | null) => name ?? '—' },
        {
            title: 'Lỗi mở',
            dataIndex: 'open_issues',
            sorter: (a: Qc3dJob, b: Qc3dJob) => (a.open_issues ?? -1) - (b.open_issues ?? -1),
            render: (count: number | null) => (count === null ?
                <Tag>chưa QC</Tag> : <Badge count={count} showZero color={count ? 'orange' : 'green'} overflowCount={999} />),
        },
        {
            title: 'QC gần nhất',
            dataIndex: 'last_qc_date',
            render: (date: string | null) => (date ? new Date(date).toLocaleString() : '—'),
        },
    ];

    return (
        <Table<Qc3dJob> rowKey='id' size='small' columns={columns} dataSource={jobs} pagination={{ pageSize: 10 }} />
    );
}

export function ReviewPage(): JSX.Element {
    const { summary, error, loading } = useSummary();

    if (loading) return <Spin size='large' />;
    if (error || !summary) {
        return <Alert type='error' message='Không tải được dữ liệu QC 3D' description={error} />;
    }

    const queue = summary.recent_jobs
        .filter((job) => job.open_issues === null || job.open_issues > 0)
        .sort((a, b) => (b.open_issues ?? -1) - (a.open_issues ?? -1));

    return (
        <Card title='Hàng đợi review: job còn lỗi mở hoặc chưa QC'>
            <JobsTable jobs={queue} />
        </Card>
    );
}

export default function DashboardPage(): JSX.Element {
    const { summary, error, loading } = useSummary();

    if (loading) return <Spin size='large' />;
    if (error || !summary) {
        return <Alert type='error' message='Không tải được dữ liệu QC 3D' description={error} />;
    }

    const openIssues = summary.recent_jobs.reduce((sum, job) => sum + (job.open_issues ?? 0), 0);

    return (
        <>
            <Row gutter={16}>
                <Col span={4}><Card><Statistic title='Task 3D' value={summary.tasks_3d} /></Card></Col>
                <Col span={4}><Card><Statistic title='Job 3D' value={summary.jobs_3d} /></Card></Col>
                <Col span={4}><Card><Statistic title='Lỗi mở' value={openIssues} /></Card></Col>
                {STAGES.map((stage) => (
                    <Col span={4} key={stage}>
                        <Card><Statistic title={stage} value={summary.jobs_3d_by_stage[stage] ?? 0} /></Card>
                    </Col>
                ))}
            </Row>
            <Card title='Job 3D cập nhật gần nhất' className='cvat-qc3d-recent-jobs' style={{ marginTop: 16 }}>
                <JobsTable jobs={summary.recent_jobs} />
            </Card>
        </>
    );
}
