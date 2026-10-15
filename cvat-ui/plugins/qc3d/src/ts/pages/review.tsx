// Copyright (C) CVAT.ai Corporation
//
// SPDX-License-Identifier: MIT

// Review queue: jobs waiting for / in QC review and jobs sent back; a side panel to take the review step.

import React, {
    useCallback, useEffect, useMemo, useState,
} from 'react';
import { Link } from 'react-router-dom';
import {
    ExportOutlined, FileSearchOutlined, ReloadOutlined, SearchOutlined,
} from '@ant-design/icons';
import Alert from 'antd/lib/alert';
import Button from 'antd/lib/button';
import Card from 'antd/lib/card';
import Drawer from 'antd/lib/drawer';
import Empty from 'antd/lib/empty';
import Input from 'antd/lib/input';
import Segmented from 'antd/lib/segmented';
import Space from 'antd/lib/space';
import Spin from 'antd/lib/spin';
import Table from 'antd/lib/table';

import {
    BoardJob, errorText, getBoard, getWorkflow, WorkflowState, WorkflowStatus,
} from '../api';
import {
    StatusBadge, WorkflowActions, WorkflowHistory, WorkflowSteps,
} from '../workflow-ui';
import { QcOpen } from './board';

type Queue = 'review' | 'returned';
const QUEUES: Record<Queue, WorkflowStatus[]> = { review: ['in_review', 'waiting_review'], returned: ['returned'] };

function waited(value: string): string {
    const hours = (Date.now() - new Date(value).getTime()) / 3600000;
    if (Number.isNaN(hours)) return '—';
    return hours < 1 ? `${Math.max(1, Math.round(hours * 60))} phút` : (hours < 48 && `${Math.round(hours)} giờ`) || `${Math.round(hours / 24)} ngày`;
}

function ReviewDrawer({ jobId, onClose, onChanged }: { jobId: number | null; onClose: () => void; onChanged: () => void }): JSX.Element {
    const [state, setState] = useState<WorkflowState | null>(null);
    const [error, setError] = useState<string | null>(null);
    useEffect(() => {
        setState(null);
        setError(null);
        if (jobId) getWorkflow(jobId).then(setState).catch((err) => setError(errorText(err)));
    }, [jobId]);
    const qc = state?.qc;
    return (
        <Drawer
            open={!!jobId}
            width={520}
            onClose={onClose}
            title={state ? (
                <Space><span>{`Job #${state.job.id}`}</span><StatusBadge status={state.status} /></Space>
            ) : 'Job'}
            extra={state && (
                <Space>
                    <Link to={`/qc3d/jobs/${state.job.id}`}><Button icon={<FileSearchOutlined />}>Báo cáo QC</Button></Link>
                    <Link to={`/tasks/${state.job.task_id}/jobs/${state.job.id}`}><Button icon={<ExportOutlined />}>Gán nhãn</Button></Link>
                </Space>
            )}
        >
            {error && <Alert type='error' showIcon message='Không tải được job' description={error} />}
            {!state && !error && <Spin />}
            {state && qc && (
                <div className='qc3d-review-drawer'>
                    <p className='qc3d-cell-secondary'>{`${state.job.task_name} · ${state.job.frames} frame · người gán nhãn: ${state.job.assignee?.username ?? '—'}`}</p>
                    <WorkflowSteps status={state.status} />
                    <div className='qc3d-review-qc'>
                        <div><span>Lần QC</span><strong>{qc.run_date ? new Date(qc.run_date).toLocaleString('vi-VN') : 'Chưa chạy'}</strong></div>
                        <div><span>Còn mở</span><QcOpen open={qc.run_id ? qc.open : null} /></div>
                        <div><span>Đã sửa / giữ nguyên</span><strong>{`${qc.fixed} / ${qc.waived}`}</strong></div>
                    </div>
                    {qc.stale_reason && (
                        <Alert type='warning' showIcon message={`QC chưa dùng được để duyệt: ${qc.stale_reason}`} description='Mở báo cáo QC và bấm Chạy lại QC cho toàn bộ job.' />
                    )}
                    <WorkflowActions
                        jobId={state.job.id}
                        actions={state.actions}
                        onDone={(next) => { setState(next); onChanged(); }}
                    />
                    <h4>Lịch sử</h4>
                    <WorkflowHistory events={state.history} />
                </div>
            )}
        </Drawer>
    );
}

export default function ReviewPage(): JSX.Element {
    const [jobs, setJobs] = useState<BoardJob[] | null>(null);
    const [error, setError] = useState<string | null>(null);
    const [loading, setLoading] = useState(false);
    const [queue, setQueue] = useState<Queue>('review');
    const [search, setSearch] = useState('');
    const [open, setOpen] = useState<number | null>(null);

    const load = useCallback(async () => {
        setLoading(true);
        try {
            setJobs((await getBoard()).jobs);
            setError(null);
        } catch (err: unknown) {
            setError(errorText(err));
        } finally {
            setLoading(false);
        }
    }, []);
    useEffect(() => { load(); }, [load]);

    const count = (key: Queue): number => (jobs ?? []).filter((job) => QUEUES[key].includes(job.status)).length;
    const rows = useMemo(() => (jobs ?? [])
        .filter((job) => QUEUES[queue].includes(job.status))
        .filter((job) => !search.trim() || `#${job.id} ${job.task_name} ${job.assignee?.username ?? ''}`.toLowerCase().includes(search.trim().toLowerCase()))
        // in review first, then the jobs that waited longest
        .sort((a, b) => QUEUES[queue].indexOf(a.status) - QUEUES[queue].indexOf(b.status) ||
            new Date(a.updated_date).getTime() - new Date(b.updated_date).getTime()), [jobs, queue, search]);

    if (!jobs && !error) return <div className='qc3d-loading-state'><Spin size='large' /><span>Đang tải hàng đợi review…</span></div>;

    return (
        <div className='qc3d-dashboard'>
            <div className='qc3d-page-header'>
                <div><h2>Hàng đợi review</h2><p>Nhận job đã nộp, chạy QC, rồi cho đạt hoặc trả lại người gán nhãn kèm lý do.</p></div>
                <Button icon={<ReloadOutlined />} onClick={load} loading={loading}>Làm mới</Button>
            </div>
            {error && <Alert type='error' showIcon message='Không tải được hàng đợi' description={error} />}
            <Card className='qc3d-table-card'>
                <div className='qc3d-table-toolbar'>
                    <Segmented
                        value={queue}
                        onChange={(value) => setQueue(value as Queue)}
                        options={[
                            { value: 'review', label: `Cần QC · ${count('review')}` },
                            { value: 'returned', label: `Đã trả lại · ${count('returned')}` },
                        ]}
                    />
                    <Input className='qc3d-search-input' prefix={<SearchOutlined />} allowClear placeholder='Tìm job, task, người…' aria-label='Tìm job' value={search} onChange={(e) => setSearch(e.target.value)} />
                </div>
                <Table<BoardJob>
                    rowKey='id'
                    size='middle'
                    dataSource={rows}
                    scroll={{ x: 900 }}
                    pagination={{ defaultPageSize: 20, hideOnSinglePage: true }}
                    onRow={(job) => ({ onClick: () => setOpen(job.id), className: 'qc3d-row-link' })}
                    locale={{
                        emptyText: <Empty image={Empty.PRESENTED_IMAGE_SIMPLE} description={queue === 'review' ? 'Không có job chờ QC' : 'Không có job bị trả lại'} />,
                    }}
                    columns={[
                        { title: 'Job', dataIndex: 'id', width: 90, render: (id: number) => <strong>{`#${id}`}</strong> },
                        { title: 'Task', dataIndex: 'task_name', render: (name: string, job) => <div><div className='qc3d-cell-primary'>{name}</div><span className='qc3d-cell-secondary'>{`${job.frames} frame`}</span></div> },
                        { title: 'Trạng thái', dataIndex: 'status', width: 160, render: (value: WorkflowStatus) => <StatusBadge status={value} /> },
                        { title: 'Người gán nhãn', width: 150, render: (_, job) => job.assignee?.username ?? '—' },
                        { title: 'Lỗi QC còn mở', width: 150, render: (_, job) => <QcOpen open={job.qc_open} /> },
                        { title: queue === 'review' ? 'Đã chờ' : 'Trả lại', width: 110, render: (_, job) => waited(job.last_action?.date ?? job.updated_date) },
                        { title: '', width: 90, render: (_, job) => <Button type='link' onClick={(event) => { event.stopPropagation(); setOpen(job.id); }}>Duyệt</Button> },
                    ]}
                />
            </Card>
            <ReviewDrawer jobId={open} onClose={() => setOpen(null)} onChanged={load} />
        </div>
    );
}
