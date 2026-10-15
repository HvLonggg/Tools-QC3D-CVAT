// Copyright (C) CVAT.ai Corporation
//
// SPDX-License-Identifier: MIT

import React, { useState } from 'react';
import { Link, useHistory } from 'react-router-dom';
import { ArrowRightOutlined, ReloadOutlined, SearchOutlined } from '@ant-design/icons';
import Alert from 'antd/lib/alert';
import Badge from 'antd/lib/badge';
import Button from 'antd/lib/button';
import Card from 'antd/lib/card';
import Empty from 'antd/lib/empty';
import { Col, Row } from 'antd/lib/grid';
import Input from 'antd/lib/input';
import Select from 'antd/lib/select';
import Spin from 'antd/lib/spin';
import Statistic from 'antd/lib/statistic';
import Table from 'antd/lib/table';
import Tag from 'antd/lib/tag';

import { Qc3dJob } from '../api';
import QcAnalyticsPanel from './analytics';
import useSummary from '../use-summary';

const STAGES = [
    { value: 'annotation', label: 'Gán nhãn', color: 'blue' },
    { value: 'validation', label: 'Kiểm tra', color: 'purple' },
    { value: 'acceptance', label: 'Nghiệm thu', color: 'cyan' },
];

function normalizeSearch(value: string): string {
    return value.toLocaleLowerCase('vi-VN').normalize('NFD').replace(/[\u0300-\u036f]/g, '').replace(/đ/g, 'd');
}

function formatDate(value: string | null): string {
    if (!value) return 'Chưa chạy QC';
    const date = new Date(value);
    return Number.isNaN(date.getTime()) ? '—' : date.toLocaleString('vi-VN', {
        day: '2-digit', month: '2-digit', year: 'numeric', hour: '2-digit', minute: '2-digit',
    });
}

function JobsTable({ jobs, review = false }: { jobs: Qc3dJob[]; review?: boolean }): JSX.Element {
    const [search, setSearch] = useState('');
    const [stage, setStage] = useState<string>('all');
    const [status, setStatus] = useState<string>('all');
    const query = normalizeSearch(search.trim());
    const filteredJobs = jobs.filter((job) => (
        (!query || normalizeSearch(`#${job.id} #${job.task_id} ${job.task_name} ${job.assignee ?? ''}`)
            .includes(query)) &&
        (stage === 'all' || job.stage === stage) &&
        (status === 'all' ||
            (status === 'unscanned' && job.open_issues === null) ||
            (status === 'issues' && (job.open_issues ?? 0) > 0) ||
            (status === 'clear' && job.open_issues === 0))
    ));
    const hasFilters = Boolean(query) || stage !== 'all' || status !== 'all';
    let emptyDescription = 'Chưa có job 3D để hiển thị.';
    if (review) emptyDescription = 'Không có job gần đây cần review.';
    if (hasFilters) emptyDescription = 'Không có job phù hợp với bộ lọc.';
    const clearFilters = (): void => {
        setSearch('');
        setStage('all');
        setStatus('all');
    };
    const columns = [
        {
            title: 'Job',
            dataIndex: 'id',
            width: 100,
            render: (id: number) => <Link className='qc3d-job-link' to={`/qc3d/jobs/${id}`}>{`#${id}`}</Link>,
        },
        {
            title: 'Task',
            dataIndex: 'task_name',
            render: (name: string, job: Qc3dJob) => (
                <div>
                    <div className='qc3d-cell-primary'>{name}</div>
                    <span className='qc3d-cell-secondary'>{`Task #${job.task_id}`}</span>
                </div>
            ),
        },
        {
            title: 'Giai đoạn',
            dataIndex: 'stage',
            width: 130,
            render: (value: string) => {
                const entry = STAGES.find((item) => item.value === value);
                return <Tag color={entry?.color}>{entry?.label ?? value}</Tag>;
            },
        },
        {
            title: 'Người phụ trách',
            dataIndex: 'assignee',
            width: 160,
            render: (name: string | null) => name ?? <span className='qc3d-cell-secondary'>Chưa phân công</span>,
        },
        {
            title: 'Lỗi mở',
            dataIndex: 'open_issues',
            width: 120,
            sorter: (a: Qc3dJob, b: Qc3dJob) => (a.open_issues ?? -1) - (b.open_issues ?? -1),
            render: (count: number | null) => (count === null ? <Tag>Chưa QC</Tag> : (
                <Badge count={count} showZero color={count ? '#d97706' : '#16a34a'} overflowCount={999} />
            )),
        },
        {
            title: 'QC gần nhất', dataIndex: 'last_qc_date', width: 170, render: formatDate,
        },
        {
            title: '',
            key: 'action',
            width: 108,
            render: (_: unknown, job: Qc3dJob) => (
                <Link aria-label={`Mở báo cáo QC của job ${job.id}`} to={`/qc3d/jobs/${job.id}`}>
                    Xem QC <ArrowRightOutlined />
                </Link>
            ),
        },
    ];

    return (
        <>
            <div className='qc3d-table-toolbar'>
                <Input
                    aria-label='Tìm job, task hoặc người phụ trách'
                    className='qc3d-search-input'
                    placeholder='Tìm job, task, người phụ trách…'
                    prefix={<SearchOutlined />}
                    allowClear
                    value={search}
                    onChange={(event) => setSearch(event.target.value)}
                />
                <Select
                    aria-label='Lọc theo giai đoạn'
                    className='qc3d-filter-select'
                    value={stage}
                    onChange={setStage}
                    options={[{ value: 'all', label: 'Mọi giai đoạn' }, ...STAGES]}
                />
                <Select
                    aria-label='Lọc theo trạng thái QC'
                    className='qc3d-filter-select'
                    value={status}
                    onChange={setStatus}
                    options={[
                        { value: 'all', label: 'Mọi trạng thái QC' },
                        { value: 'issues', label: 'Còn lỗi mở' },
                        { value: 'unscanned', label: 'Chưa chạy QC' },
                        ...(!review ? [{ value: 'clear', label: 'Không còn lỗi mở' }] : []),
                    ]}
                />
                {hasFilters && <Button onClick={clearFilters}>Xóa bộ lọc</Button>}
            </div>
            <div className='qc3d-table-meta' aria-live='polite'>
                {`${filteredJobs.length} / ${jobs.length} job`}
                {review && ' · Ưu tiên job có nhiều lỗi mở'}
            </div>
            <Table<Qc3dJob>
                className='qc3d-jobs-table'
                rowKey='id'
                size='middle'
                columns={columns}
                dataSource={filteredJobs}
                scroll={{ x: 1000 }}
                pagination={{
                    defaultPageSize: 10,
                    showSizeChanger: true,
                    pageSizeOptions: [10, 20, 50],
                    hideOnSinglePage: true,
                }}
                locale={{
                    emptyText: (
                        <Empty
                            image={Empty.PRESENTED_IMAGE_SIMPLE}
                            description={emptyDescription}
                        >
                            {hasFilters ? <Button onClick={clearFilters}>Xóa bộ lọc</Button> :
                                <Link to='/tasks'>Mở danh sách task</Link>}
                        </Empty>
                    ),
                }}
            />
        </>
    );
}

export function ReviewPage(): JSX.Element {
    const {
        summary, error, loading, refresh,
    } = useSummary();

    if (loading && !summary) {
        return (
            <div className='qc3d-loading-state' role='status'>
                <Spin size='large' /><span>Đang tải hàng đợi review…</span>
            </div>
        );
    }
    if (!summary) {
        return (
            <Alert type='error' showIcon message='Không tải được hàng đợi review' description={error}
                action={<Button onClick={refresh} loading={loading}>Thử lại</Button>}
            />
        );
    }

    const queue = summary.recent_jobs
        .filter((job) => job.open_issues === null || job.open_issues > 0)
        .sort((a, b) => (b.open_issues ?? -1) - (a.open_issues ?? -1));

    return (
        <div className='qc3d-dashboard'>
            <div className='qc3d-page-header'>
                <div><h2>Hàng đợi review</h2><p>Kiểm tra các job gần đây còn lỗi mở hoặc chưa chạy QC.</p></div>
                <Button icon={<ReloadOutlined />} onClick={refresh} loading={loading}>Làm mới</Button>
            </div>
            {error && <Alert type='warning' showIcon message='Chưa cập nhật được dữ liệu' description={error} />}
            <Card className='qc3d-table-card' title={`Cần xem xét · ${queue.length} job`}>
                <JobsTable jobs={queue} review />
            </Card>
        </div>
    );
}

export default function DashboardPage(): JSX.Element {
    const {
        summary, error, loading, refresh,
    } = useSummary();
    const history = useHistory();

    if (loading && !summary) {
        return (
            <div className='qc3d-loading-state' role='status'>
                <Spin size='large' /><span>Đang tải tổng quan QC 3D…</span>
            </div>
        );
    }
    if (!summary) {
        return (
            <Alert type='error' showIcon message='Không tải được dữ liệu QC 3D' description={error}
                action={<Button onClick={refresh} loading={loading}>Thử lại</Button>}
            />
        );
    }

    const openIssues = summary.recent_jobs.reduce((sum, job) => sum + (job.open_issues ?? 0), 0);
    const pendingJobs = summary.recent_jobs.filter((job) => job.open_issues === null || job.open_issues > 0).length;
    const scannedJobs = summary.recent_jobs.filter((job) => job.open_issues !== null).length;

    return (
        <div className='qc3d-dashboard'>
            <div className='qc3d-page-header'>
                <div>
                    <h2>Tổng quan chất lượng 3D</h2>
                    <p>Theo dõi tiến độ, tìm job cần kiểm tra và bắt đầu xử lý lỗi.</p>
                </div>
                <div className='qc3d-page-actions'>
                    <Button icon={<ReloadOutlined />} onClick={refresh} loading={loading}>Làm mới</Button>
                    <Button type='primary' icon={<ArrowRightOutlined />} onClick={() => history.push('/qc3d/review')}>
                        Mở hàng đợi review
                    </Button>
                </div>
            </div>
            {error && <Alert type='warning' showIcon message='Chưa cập nhật được dữ liệu' description={error} />}
            <Row gutter={[16, 16]}>
                <Col xs={24} sm={12} lg={6}>
                    <Card className='qc3d-stat-card qc3d-stat-accent'>
                        <Statistic title='Task 3D' value={summary.tasks_3d} />
                        <span className='qc3d-stat-caption'>Task bạn có quyền truy cập</span>
                    </Card>
                </Col>
                <Col xs={24} sm={12} lg={6}>
                    <Card className='qc3d-stat-card'>
                        <Statistic title='Job 3D' value={summary.jobs_3d} />
                        <span className='qc3d-stat-caption'>Tổng job trong các task 3D</span>
                    </Card>
                </Col>
                <Col xs={24} sm={12} lg={6}>
                    <Card className='qc3d-stat-card qc3d-stat-warning'>
                        <Statistic title='Lỗi mở gần đây' value={openIssues} />
                        <span className='qc3d-stat-caption'>
                            {`Trong ${summary.recent_jobs.length} job cập nhật gần nhất`}
                        </span>
                    </Card>
                </Col>
                <Col xs={24} sm={12} lg={6}>
                    <Card className='qc3d-stat-card'>
                        <Statistic title='Job cần xem xét' value={pendingJobs} />
                        <span className='qc3d-stat-caption'>
                            {`${scannedJobs} / ${summary.recent_jobs.length} job gần đây đã QC`}
                        </span>
                    </Card>
                </Col>
            </Row>
            <div className='qc3d-quick-links'>
                <div className='qc3d-stage-summary'>
                    <span>Tiến độ job</span>
                    {STAGES.map(({ value, label, color }) => (
                        <Tag key={value} color={color}>{`${label}: ${summary.jobs_3d_by_stage[value] ?? 0}`}</Tag>
                    ))}
                </div>
                <Link to='/qc3d/guidelines'>Quản lý guideline <ArrowRightOutlined /></Link>
            </div>
            <QcAnalyticsPanel />
            <Card title='Job 3D cập nhật gần nhất' className='cvat-qc3d-recent-jobs qc3d-table-card'>
                <JobsTable jobs={summary.recent_jobs} />
            </Card>
        </div>
    );
}
