// Copyright (C) CVAT.ai Corporation
//
// SPDX-License-Identifier: MIT

// QA acceptance: jobs waiting for / in acceptance, and the sample of frames the QA checks.

import React, {
    useCallback, useEffect, useMemo, useRef, useState,
} from 'react';
import { Link, useHistory, useLocation } from 'react-router-dom';
import {
    CheckCircleFilled, CloseCircleFilled, ExportOutlined, ReloadOutlined, SaveOutlined, SafetyCertificateOutlined,
} from '@ant-design/icons';
import Alert from 'antd/lib/alert';
import Button from 'antd/lib/button';
import Card from 'antd/lib/card';
import Empty from 'antd/lib/empty';
import Input from 'antd/lib/input';
import InputNumber from 'antd/lib/input-number';
import Progress from 'antd/lib/progress';
import Spin from 'antd/lib/spin';
import Table from 'antd/lib/table';
import Tag from 'antd/lib/tag';
import Tooltip from 'antd/lib/tooltip';
import notification from 'antd/lib/notification';

import {
    annotationLink, BoardJob, errorText, getBoard, getWorkflow, recordAcceptanceFrame, WorkflowState,
} from '../api';
import {
    StatusBadge, WorkflowActions, WorkflowHistory, WorkflowSteps,
} from '../workflow-ui';

const pct = (value: number | null | undefined, digits = 1): string => (
    value === null || value === undefined ? '—' : `${(value * 100).toFixed(digits)}%`);

interface Draft { boxes_checked: number | null; errors: number | null; note: string }
interface SampleRow { frame: number }

function SampleTable({ state, onSaved }: { state: WorkflowState; onSaved: (next: WorkflowState) => void }): JSX.Element {
    const acceptance = state.acceptance!;
    const editable = state.status === 'in_acceptance' && state.actions.some((a) => a.action === 'accept' || a.action === 'reject');
    const [drafts, setDrafts] = useState<Record<number, Draft>>({});
    const [saving, setSaving] = useState<number | null>(null);
    useEffect(() => setDrafts({}), [acceptance.id]);

    const draftOf = (frame: number): Draft => drafts[frame] ?? {
        boxes_checked: acceptance.results[String(frame)]?.boxes_checked ?? null,
        errors: acceptance.results[String(frame)]?.errors ?? null,
        note: acceptance.results[String(frame)]?.note ?? '',
    };
    const change = (frame: number, patch: Partial<Draft>): void => setDrafts((d) => ({
        ...d, [frame]: { ...draftOf(frame), ...patch },
    }));
    const save = async (frame: number): Promise<void> => {
        const draft = draftOf(frame);
        if (draft.boxes_checked === null || draft.errors === null) return;
        setSaving(frame);
        try {
            const next = await recordAcceptanceFrame(state.job.id, frame, {
                boxes_checked: draft.boxes_checked, errors: draft.errors, note: draft.note.trim(),
            });
            setDrafts((d) => { const copy = { ...d }; delete copy[frame]; return copy; });
            onSaved(next);
        } catch (err: unknown) {
            notification.error({ message: `Không lưu được frame ${frame}`, description: errorText(err) });
        } finally {
            setSaving(null);
        }
    };

    return (
        <Table<SampleRow>
            rowKey='frame'
            size='middle'
            className='qc3d-sample-table'
            dataSource={acceptance.frames.map((frame) => ({ frame }))}
            pagination={false}
            columns={[
                {
                    title: 'Frame mẫu',
                    width: 130,
                    render: (_: unknown, { frame }: SampleRow) => (
                        <Link to={annotationLink(state.job.task_id, state.job.id, frame)} target='_blank'>
                            {`Frame ${frame}`} <ExportOutlined />
                        </Link>
                    ),
                },
                {
                    title: 'Box đã kiểm',
                    width: 140,
                    render: (_: unknown, { frame }: SampleRow) => (
                        <InputNumber
                            min={0}
                            precision={0}
                            disabled={!editable}
                            aria-label={`Số box đã kiểm ở frame ${frame}`}
                            value={draftOf(frame).boxes_checked}
                            onChange={(value) => change(frame, { boxes_checked: value })}
                        />
                    ),
                },
                {
                    title: 'Box lỗi',
                    width: 120,
                    render: (_: unknown, { frame }: SampleRow) => {
                        const draft = draftOf(frame);
                        const invalid = draft.errors !== null && draft.boxes_checked !== null && draft.errors > draft.boxes_checked;
                        return (
                            <InputNumber
                                min={0}
                                precision={0}
                                status={invalid ? 'error' : undefined}
                                disabled={!editable}
                                aria-label={`Số box lỗi ở frame ${frame}`}
                                value={draft.errors}
                                onChange={(value) => change(frame, { errors: value })}
                            />
                        );
                    },
                },
                {
                    title: 'Ghi chú lỗi',
                    render: (_: unknown, { frame }: SampleRow) => (
                        <Input
                            disabled={!editable}
                            placeholder='Ví dụ: Car #1853 thấp 0,4 m'
                            aria-label={`Ghi chú frame ${frame}`}
                            value={draftOf(frame).note}
                            maxLength={1000}
                            onChange={(event) => change(frame, { note: event.target.value })}
                            onPressEnter={() => save(frame)}
                        />
                    ),
                },
                {
                    title: 'Trạng thái',
                    width: 190,
                    render: (_: unknown, { frame }: SampleRow) => {
                        const result = acceptance.results[String(frame)];
                        const draft = draftOf(frame);
                        const dirty = !!drafts[frame];
                        const invalid = draft.boxes_checked === null || draft.errors === null ||
                            draft.errors > draft.boxes_checked;
                        if (dirty || !result) {
                            return editable ? (
                                <Button
                                    size='small'
                                    type={dirty ? 'primary' : 'default'}
                                    icon={<SaveOutlined />}
                                    disabled={invalid}
                                    loading={saving === frame}
                                    onClick={() => save(frame)}
                                >
                                    Lưu
                                </Button>
                            ) : <Tag>Chưa chấm</Tag>;
                        }
                        return (
                            <Tooltip title={result.date ? new Date(result.date).toLocaleString('vi-VN') : undefined}>
                                <span className={result.errors ? 'qc3d-sample-bad' : 'qc3d-sample-ok'}>
                                    {result.errors ? <CloseCircleFilled /> : <CheckCircleFilled />}
                                    {` ${result.errors ? `${result.errors} lỗi` : 'Không lỗi'} · ${result.by ?? ''}`}
                                </span>
                            </Tooltip>
                        );
                    },
                },
            ]}
        />
    );
}

function Workspace({ jobId, onChanged }: { jobId: number; onChanged: () => void }): JSX.Element {
    const [state, setState] = useState<WorkflowState | null>(null);
    const [error, setError] = useState<string | null>(null);
    const active = useRef(jobId);
    active.current = jobId;
    const load = useCallback(() => {
        setError(null);
        getWorkflow(jobId).then((next) => { if (active.current === jobId) setState(next); })
            .catch((err) => { if (active.current === jobId) setError(errorText(err)); });
    }, [jobId]);
    useEffect(() => { setState(null); load(); }, [load]);

    if (error) return <Alert type='error' showIcon message='Không tải được job' description={error} action={<Button onClick={load}>Thử lại</Button>} />;
    if (!state) return <div className='qc3d-loading-state'><Spin /><span>Đang tải job…</span></div>;

    const { criteria, acceptance } = state;
    const open = acceptance && !acceptance.closed ? acceptance : null;
    const rate = open?.error_rate ?? null;
    const limit = criteria.max_error_rate;
    const overLimit = rate !== null && rate > limit;
    const done = open ? open.checked_frames === open.frames.length : false;
    const update = (next: WorkflowState): void => { setState(next); onChanged(); };

    return (
        <div className='qc3d-qa-workspace'>
            <div className='qc3d-qa-head'>
                <div>
                    <span className='qc3d-job-eyebrow'>NGHIỆM THU JOB</span>
                    <h3>
                        <Link to={`/qc3d/jobs/${state.job.id}`}>{`Job #${state.job.id}`}</Link>
                        <StatusBadge status={state.status} />
                    </h3>
                    <p>{`${state.job.task_name} · ${state.job.frames} frame · người gán nhãn: ${state.job.assignee?.username ?? '—'}`}</p>
                </div>
                <WorkflowActions jobId={state.job.id} actions={state.actions} onDone={update} />
            </div>
            <WorkflowSteps status={state.status} />
            <div className='qc3d-criteria'>
                <span>Tiêu chí theo guideline</span>
                <Tag>{`Mẫu ${pct(criteria.sample_ratio, 0)} frame, tối thiểu ${criteria.min_frames}`}</Tag>
                <Tag>{`Tỷ lệ box lỗi ≤ ${pct(limit)}`}</Tag>
                <Tag>{`Lỗi nghiêm trọng còn mở ≤ ${criteria.max_open_blockers}`}</Tag>
                {state.qc.open.blocker > criteria.max_open_blockers && (
                    <Tag color='red'>{`QC còn ${state.qc.open.blocker} lỗi nghiêm trọng`}</Tag>
                )}
            </div>
            {!open && state.status === 'waiting_acceptance' && (
                <Card className='qc3d-qa-start'>
                    <SafetyCertificateOutlined />
                    <div>
                        <strong>Job đã qua QC, chờ nghiệm thu</strong>
                        <p>{`Bấm "Bắt đầu nghiệm thu" để hệ thống chọn ngẫu nhiên ${Math.min(state.job.frames, Math.max(criteria.min_frames, Math.ceil(criteria.sample_ratio * state.job.frames)))} frame mẫu.`}</p>
                    </div>
                </Card>
            )}
            {open && (
                <>
                    <div className='qc3d-qa-kpis'>
                        <div className='qc3d-kpi'>
                            <span className='qc3d-kpi-label'>Frame đã chấm</span>
                            <span className='qc3d-kpi-value'>{`${open.checked_frames}/${open.frames.length}`}</span>
                            <Progress percent={Math.round((100 * open.checked_frames) / open.frames.length)} size='small' showInfo={false} />
                        </div>
                        <div className='qc3d-kpi'>
                            <span className='qc3d-kpi-label'>Box đã kiểm / lỗi</span>
                            <span className='qc3d-kpi-value'>{`${open.boxes_checked} / ${open.errors}`}</span>
                            <span className='qc3d-stat-caption'>{`Vòng nghiệm thu ${open.round}`}</span>
                        </div>
                        <div className={`qc3d-kpi ${overLimit ? 'qc3d-kpi-bad' : 'qc3d-kpi-good'}`}>
                            <span className='qc3d-kpi-label'>Tỷ lệ box lỗi</span>
                            <span className='qc3d-kpi-value'>{pct(rate)}</span>
                            <div className='qc3d-rate-track' aria-label={`Tỷ lệ lỗi ${pct(rate)}, ngưỡng ${pct(limit)}`}>
                                <div className='qc3d-rate-fill' style={{ width: `${Math.min(100, ((rate ?? 0) / Math.max(limit * 2, 0.001)) * 100)}%` }} />
                                <div className='qc3d-rate-limit' title={`Ngưỡng ${pct(limit)}`} />
                            </div>
                            <span className='qc3d-stat-caption'>{`Ngưỡng ${pct(limit)} (vạch giữa)`}</span>
                        </div>
                        <div className={`qc3d-kpi ${(done && (open.passes ? 'qc3d-kpi-good' : 'qc3d-kpi-bad')) || ''}`}>
                            <span className='qc3d-kpi-label'>Kết luận</span>
                            <span className='qc3d-kpi-value qc3d-kpi-verdict'>
                                {(!done && 'Đang chấm') || (open.passes ? 'Đạt' : 'Không đạt')}
                            </span>
                            <span className='qc3d-stat-caption'>
                                {done ? 'Bấm Đạt / Không đạt để ghi nhận' : `Còn ${open.frames.length - open.checked_frames} frame`}
                            </span>
                        </div>
                    </div>
                    <Card size='small' title='Frame mẫu' className='qc3d-table-card'>
                        <SampleTable state={state} onSaved={update} />
                    </Card>
                </>
            )}
            <Card size='small' title='Lịch sử quy trình' className='qc3d-history-card'>
                <WorkflowHistory events={state.history} />
            </Card>
        </div>
    );
}

export default function AcceptancePage(): JSX.Element {
    const history = useHistory();
    const { search } = useLocation();
    const selected = Number(new URLSearchParams(search).get('job')) || null;
    const [jobs, setJobs] = useState<BoardJob[] | null>(null);
    const [error, setError] = useState<string | null>(null);
    const [loading, setLoading] = useState(false);

    const load = useCallback(async () => {
        setLoading(true);
        try {
            const board = await getBoard();
            setJobs(board.jobs.filter((job) => ['waiting_acceptance', 'in_acceptance', 'accepted'].includes(job.status)));
            setError(null);
        } catch (err: unknown) {
            setError(errorText(err));
        } finally {
            setLoading(false);
        }
    }, []);
    useEffect(() => { load(); }, [load]);

    const queue = useMemo(() => (jobs ?? []).filter((job) => job.status !== 'accepted'), [jobs]);
    const recent = useMemo(() => (jobs ?? []).filter((job) => job.status === 'accepted').slice(0, 5), [jobs]);
    useEffect(() => {
        if (!selected && queue.length) history.replace(`/qc3d/qa?job=${queue[0].id}`);
    }, [queue, selected]);

    return (
        <div className='qc3d-dashboard'>
            <div className='qc3d-page-header'>
                <div><h2>Nghiệm thu QA</h2><p>Lấy mẫu frame, chấm lỗi theo tiêu chí của guideline và kết luận đạt / không đạt.</p></div>
                <Button icon={<ReloadOutlined />} onClick={load} loading={loading}>Làm mới</Button>
            </div>
            {error && <Alert type='error' showIcon message='Không tải được danh sách' description={error} />}
            {!jobs && !error && <div className='qc3d-loading-state'><Spin size='large' /><span>Đang tải…</span></div>}
            {jobs && (
                <div className='qc3d-qa-layout'>
                    <Card className='qc3d-qa-list' title={`Cần nghiệm thu · ${queue.length}`} size='small'>
                        {!queue.length && <Empty image={Empty.PRESENTED_IMAGE_SIMPLE} description='Không có job chờ nghiệm thu' />}
                        {queue.map((job) => (
                            <button
                                key={job.id}
                                type='button'
                                className={`qc3d-qa-item${job.id === selected ? ' qc3d-qa-item-active' : ''}`}
                                onClick={() => history.replace(`/qc3d/qa?job=${job.id}`)}
                            >
                                <div className='qc3d-qa-item-head'>
                                    <strong>{`#${job.id}`}</strong>
                                    <StatusBadge status={job.status} />
                                </div>
                                <div className='qc3d-cell-secondary'>{`${job.task_name} · ${job.frames} frame`}</div>
                                {job.error_rate !== null && <div className='qc3d-cell-secondary'>{`Tỷ lệ lỗi mẫu ${pct(job.error_rate)}`}</div>}
                            </button>
                        ))}
                        {recent.length > 0 && <div className='qc3d-nav-caption qc3d-qa-recent'>ĐÃ ĐẠT GẦN ĐÂY</div>}
                        {recent.map((job) => (
                            <button key={job.id} type='button' className={`qc3d-qa-item qc3d-qa-item-done${job.id === selected ? ' qc3d-qa-item-active' : ''}`} onClick={() => history.replace(`/qc3d/qa?job=${job.id}`)}>
                                <div className='qc3d-qa-item-head'><strong>{`#${job.id}`}</strong><StatusBadge status={job.status} /></div>
                                <div className='qc3d-cell-secondary'>{job.task_name}</div>
                            </button>
                        ))}
                    </Card>
                    <div className='qc3d-qa-main'>
                        {selected ? <Workspace jobId={selected} onChanged={load} /> : (
                            <Card><Empty description='Chọn một job bên trái để nghiệm thu' /></Card>
                        )}
                    </div>
                </div>
            )}
        </div>
    );
}
