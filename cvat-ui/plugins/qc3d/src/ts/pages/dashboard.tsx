// Copyright (C) CVAT.ai Corporation
//
// SPDX-License-Identifier: MIT

import React from 'react';
import { Link } from 'react-router-dom';
import Alert from 'antd/lib/alert';
import Card from 'antd/lib/card';
import { Col, Row } from 'antd/lib/grid';
import Spin from 'antd/lib/spin';
import Statistic from 'antd/lib/statistic';
import Table from 'antd/lib/table';
import Tag from 'antd/lib/tag';

import { Qc3dJob } from '../api';
import useSummary from '../use-summary';

const STAGES = ['annotation', 'validation', 'acceptance'];

export default function DashboardPage(): JSX.Element {
    const { summary, error, loading } = useSummary();

    if (loading) return <Spin size='large' />;
    if (error || !summary) {
        return <Alert type='error' message='Không tải được dữ liệu QC 3D' description={error} />;
    }

    const columns = [
        {
            title: 'Job',
            dataIndex: 'id',
            render: (id: number) => <Link to={`/qc3d/jobs/${id}`}>{`#${id}`}</Link>,
        },
        { title: 'Task', dataIndex: 'task_name' },
        { title: 'Stage', dataIndex: 'stage', render: (stage: string) => <Tag>{stage}</Tag> },
        { title: 'State', dataIndex: 'state' },
        { title: 'Người làm', dataIndex: 'assignee', render: (name: string | null) => name ?? '—' },
    ];

    return (
        <>
            <Row gutter={16}>
                <Col span={6}><Card><Statistic title='Task 3D' value={summary.tasks_3d} /></Card></Col>
                <Col span={6}><Card><Statistic title='Job 3D' value={summary.jobs_3d} /></Card></Col>
                {STAGES.map((stage) => (
                    <Col span={4} key={stage}>
                        <Card><Statistic title={stage} value={summary.jobs_3d_by_stage[stage] ?? 0} /></Card>
                    </Col>
                ))}
            </Row>
            <Card title='Job 3D cập nhật gần nhất' className='cvat-qc3d-recent-jobs' style={{ marginTop: 16 }}>
                <Table<Qc3dJob>
                    rowKey='id'
                    size='small'
                    columns={columns}
                    dataSource={summary.recent_jobs}
                    pagination={{ pageSize: 10 }}
                />
            </Card>
        </>
    );
}
