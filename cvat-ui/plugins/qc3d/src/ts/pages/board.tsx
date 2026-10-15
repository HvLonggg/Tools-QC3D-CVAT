// Copyright (C) CVAT.ai Corporation
//
// SPDX-License-Identifier: MIT

// Where every 3D job is in the review / acceptance workflow: kanban by status, or a table.

import React, {
    useCallback, useEffect, useMemo, useState,
} from 'react';
import { Link, useHistory } from 'react-router-dom';
import {
    AppstoreOutlined, ReloadOutlined, SearchOutlined, TableOutlined, UserOutlined,
} from '@ant-design/icons';
import Alert from 'antd/lib/alert';
import Button from 'antd/lib/button';
import Card from 'antd/lib/card';
import Empty from 'antd/lib/empty';
import Input from 'antd/lib/input';
import Segmented from 'antd/lib/segmented';
import Select from 'antd/lib/select';
import Spin from 'antd/lib/spin';
import Table from 'antd/lib/table';
import Tooltip from 'antd/lib/tooltip';

import {
    BoardJob, errorText, getBoard, SeverityCounts, WorkflowBoard, WorkflowStatus,
} from '../api';
import { STATUS_META, STATUS_ORDER, StatusBadge } from '../workflow-ui';

const fold = (value: string): string => value.toLocaleLowerCase('vi-VN').normalize('NFD')
    .replace(/[̀-ͯ]/g, '').replace(/đ/g, 'd');

function ago(value: string): string {
    const minutes = Math.round((Date.now() - new Date(value).getTime()) / 60000);
    if (Number.isNaN(minutes)) return '';
    if (minutes < 1) return 'vừa xong';
    if (minutes < 60) return `${minutes} phút trước`;
    if (minutes < 60 * 24) return `${Math.round(minutes / 60)} giờ trước`;
    return `${Math.round(minutes / 1440)} ngày trước`;
}

export function QcOpen({ open }: { open: SeverityCounts | null }): JSX.Element {
    if (!open) return <span className='qc3d-cell-secondary'>Chưa QC</span>;
    if (!open.blocker && !open.major && !open.minor) return <span className='qc3d-qc-clean'>Không còn lỗi mở</span>;
    return (
        <Tooltip title={`Còn mở: ${open.blocker} nghiêm trọng · ${open.major} cần xử lý · ${open.minor} lưu ý`}>
            <span className='qc3d-qc-open'>
                {open.blocker > 0 && <span className='qc3d-dot-blocker'>{open.blocker}</span>}
                {open.major > 0 && <span className='qc3d-dot-major'>{open.major}</span>}
                {open.minor > 0 && <span className='qc3d-dot-minor'>{open.minor}</span>}
            </span>
        </Tooltip>
    );
}

function JobCard({ job }: { job: BoardJob }): JSX.Element {
    return (
        <Link to={`/qc3d/jobs/${job.id}`} className='qc3d-kanban-card'>
            <div className='qc3d-kanban-card-head'>
                <strong>{`#${job.id}`}</strong>
                <span className='qc3d-cell-secondary'>{`${job.frames} frame`}</span>
            </div>
            <div className='qc3d-kanban-task' title={job.task_name}>{job.task_name}</div>
            <div className='qc3d-kanban-meta'>
                <QcOpen open={job.qc_open} />
                {job.error_rate !== null && (
                    <span className='qc3d-cell-secondary'>{`Lỗi mẫu ${(job.error_rate * 100).toFixed(1)}%`}</span>
                )}
            </div>
            <div className='qc3d-kanban-foot'>
                <span><UserOutlined /> {job.assignee?.username ?? 'Chưa phân công'}</span>
                <span title={job.last_action ? `${job.last_action.action} · ${job.last_action.user ?? ''}` : undefined}>
                    {ago(job.last_action?.date ?? job.updated_date)}
                </span>
            </div>
        </Link>
    );
}

export default function BoardPage(): JSX.Element {
    const history = useHistory();
    const [board, setBoard] = useState<WorkflowBoard | null>(null);
    const [error, setError] = useState<string | null>(null);
    const [loading, setLoading] = useState(false);
    const [mode, setMode] = useState<'kanban' | 'table'>(() => {
        try { return window.localStorage.getItem('qc3d.board.mode') === 'table' ? 'table' : 'kanban'; } catch { return 'kanban'; }
    });
    const [search, setSearch] = useState('');
    const [task, setTask] = useState<number | null>(null);
    const [assignee, setAssignee] = useState<number | null>(null);
    const [status, setStatus] = useState<WorkflowStatus | null>(null);

    const load = useCallback(async () => {
        setLoading(true);
        try {
            setBoard(await getBoard());
            setError(null);
        } catch (err: unknown) {
            setError(errorText(err));
        } finally {
            setLoading(false);
        }
    }, []);
    useEffect(() => { load(); }, [load]);
    useEffect(() => {
        try { window.localStorage.setItem('qc3d.board.mode', mode); } catch { /* optional */ }
    }, [mode]);

    const jobs = useMemo(() => (board?.jobs ?? []).filter((job) => (
        (!task || job.task_id === task) && (!assignee || job.assignee?.id === assignee) &&
        (!status || job.status === status) &&
        (!search.trim() || fold(`#${job.id} ${job.task_name} ${job.assignee?.username ?? ''}`).includes(fold(search.trim())))
    )), [board, task, assignee, search, status]);
    const tasks = useMemo(() => [...new Map((board?.jobs ?? []).map((j) => [j.task_id, j.task_name])).entries()], [board]);
    const people = useMemo(() => [...new Map((board?.jobs ?? []).filter((j) => j.assignee)
        .map((j) => [j.assignee!.id, j.assignee!.username])).entries()], [board]);
    const filtered = !!(search || task || assignee || status);

    if (!board && !error) return <div className='qc3d-loading-state'><Spin size='large' /><span>Đang tải trạng thái job…</span></div>;

    return (
        <div className='qc3d-dashboard'>
            <div className='qc3d-page-header'>
                <div><h2>Trạng thái job</h2><p>Mỗi job 3D đang ở đâu trong quy trình gán nhãn → QC → nghiệm thu → chốt.</p></div>
                <div className='qc3d-page-actions'>
                    <Segmented
                        value={mode}
                        onChange={(value) => setMode(value as 'kanban' | 'table')}
                        options={[
                            { value: 'kanban', label: 'Kanban', icon: <AppstoreOutlined /> },
                            { value: 'table', label: 'Bảng', icon: <TableOutlined /> },
                        ]}
                    />
                    <Button icon={<ReloadOutlined />} onClick={load} loading={loading}>Làm mới</Button>
                </div>
            </div>
            {error && <Alert type='error' showIcon message='Không tải được trạng thái job' description={error} action={<Button onClick={load}>Thử lại</Button>} />}
            {board && (
                <>
                    <div className='qc3d-pipeline' role='list' aria-label='Số job theo trạng thái'>
                        {board.columns.map((column) => {
                            const meta = STATUS_META[column.status];
                            const active = status === column.status;
                            return (
                                <button
                                    key={column.status}
                                    type='button'
                                    role='listitem'
                                    aria-pressed={active}
                                    className={`qc3d-pipeline-step${active ? ' qc3d-pipeline-active' : ''}`}
                                    style={{ '--step': meta.color, '--soft': meta.soft } as React.CSSProperties}
                                    onClick={() => setStatus(active ? null : column.status)}
                                >
                                    <span className='qc3d-pipeline-icon'>{meta.icon}</span>
                                    <strong>{column.count}</strong>
                                    <span>{column.text}</span>
                                </button>
                            );
                        })}
                    </div>
                    <div className='qc3d-table-toolbar'>
                        <Input
                            className='qc3d-search-input'
                            prefix={<SearchOutlined />}
                            allowClear
                            placeholder='Tìm job, task, người…'
                            aria-label='Tìm job'
                            value={search}
                            onChange={(event) => setSearch(event.target.value)}
                        />
                        <Select
                            className='qc3d-filter-select'
                            allowClear
                            placeholder='Mọi task'
                            aria-label='Lọc theo task'
                            value={task ?? undefined}
                            onChange={(value) => setTask(value ?? null)}
                            options={tasks.map(([id, name]) => ({ value: id, label: `${name} (#${id})` }))}
                        />
                        <Select
                            className='qc3d-filter-select'
                            allowClear
                            placeholder='Mọi người phụ trách'
                            aria-label='Lọc theo người phụ trách'
                            value={assignee ?? undefined}
                            onChange={(value) => setAssignee(value ?? null)}
                            options={people.map(([id, name]) => ({ value: id, label: name }))}
                        />
                        {filtered && (
                            <Button onClick={() => { setSearch(''); setTask(null); setAssignee(null); setStatus(null); }}>Xóa bộ lọc</Button>
                        )}
                        <span className='qc3d-table-meta'>{`${jobs.length} / ${board.jobs.length} job`}</span>
                    </div>
                    {mode === 'kanban' ? (
                        <div className='qc3d-kanban'>
                            {STATUS_ORDER.filter((key) => !status || key === status).map((key) => {
                                const meta = STATUS_META[key];
                                const items = jobs.filter((job) => job.status === key);
                                return (
                                    <section key={key} className='qc3d-kanban-column' aria-label={meta.text}>
                                        <header style={{ borderTopColor: meta.color }}>
                                            <span style={{ color: meta.color }}>{meta.icon}</span>
                                            <strong>{meta.text}</strong>
                                            <span className='qc3d-kanban-count'>{items.length}</span>
                                        </header>
                                        <div className='qc3d-kanban-list'>
                                            {items.map((job) => <JobCard key={job.id} job={job} />)}
                                            {!items.length && <div className='qc3d-kanban-empty'>Trống</div>}
                                        </div>
                                    </section>
                                );
                            })}
                        </div>
                    ) : (
                        <Card className='qc3d-table-card'>
                            <Table<BoardJob>
                                rowKey='id'
                                size='middle'
                                dataSource={jobs}
                                scroll={{ x: 1000 }}
                                onRow={(job) => ({ onClick: () => history.push(`/qc3d/jobs/${job.id}`), className: 'qc3d-row-link' })}
                                pagination={{ defaultPageSize: 20, hideOnSinglePage: true, showSizeChanger: true }}
                                locale={{ emptyText: <Empty image={Empty.PRESENTED_IMAGE_SIMPLE} description='Không có job phù hợp' /> }}
                                columns={[
                                    { title: 'Job', dataIndex: 'id', width: 90, sorter: (a, b) => a.id - b.id, render: (id: number) => <strong>{`#${id}`}</strong> },
                                    {
                                        title: 'Task',
                                        dataIndex: 'task_name',
                                        render: (name: string, job) => (
                                            <div><div className='qc3d-cell-primary'>{name}</div><span className='qc3d-cell-secondary'>{`${job.frames} frame`}</span></div>
                                        ),
                                    },
                                    {
                                        title: 'Trạng thái',
                                        dataIndex: 'status',
                                        width: 170,
                                        sorter: (a, b) => STATUS_ORDER.indexOf(a.status) - STATUS_ORDER.indexOf(b.status),
                                        render: (value: WorkflowStatus) => <StatusBadge status={value} />,
                                    },
                                    { title: 'Người phụ trách', width: 150, render: (_, job) => job.assignee?.username ?? <span className='qc3d-cell-secondary'>Chưa phân công</span> },
                                    {
                                        title: 'Lỗi QC còn mở',
                                        width: 150,
                                        sorter: (a, b) => ((a.qc_open?.blocker ?? -1) * 1000 + (a.qc_open?.major ?? 0)) - ((b.qc_open?.blocker ?? -1) * 1000 + (b.qc_open?.major ?? 0)),
                                        render: (_, job) => <QcOpen open={job.qc_open} />,
                                    },
                                    {
                                        title: 'Lỗi mẫu',
                                        width: 100,
                                        render: (_, job) => (job.error_rate === null ? '—' : `${(job.error_rate * 100).toFixed(1)}%`),
                                    },
                                    {
                                        title: 'Cập nhật',
                                        width: 170,
                                        sorter: (a, b) => new Date(a.updated_date).getTime() - new Date(b.updated_date).getTime(),
                                        defaultSortOrder: 'descend',
                                        render: (_, job) => (
                                            <div>
                                                <div>{ago(job.last_action?.date ?? job.updated_date)}</div>
                                                {job.last_action?.user && <span className='qc3d-cell-secondary'>{job.last_action.user}</span>}
                                            </div>
                                        ),
                                    },
                                ]}
                            />
                        </Card>
                    )}
                </>
            )}
        </div>
    );
}
