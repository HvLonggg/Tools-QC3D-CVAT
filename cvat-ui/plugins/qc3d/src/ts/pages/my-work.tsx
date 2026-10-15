// Copyright (C) CVAT.ai Corporation
//
// SPDX-License-Identifier: MIT

import React, { useCallback, useEffect, useMemo, useState } from 'react';
import { Link } from 'react-router-dom';
import {
    CheckCircleOutlined, ClockCircleOutlined, ExclamationCircleOutlined, ReloadOutlined, RollbackOutlined,
    SearchOutlined, ToolOutlined, UserOutlined,
} from '@ant-design/icons';
import Alert from 'antd/lib/alert';
import Button from 'antd/lib/button';
import Card from 'antd/lib/card';
import Drawer from 'antd/lib/drawer';
import Empty from 'antd/lib/empty';
import { Col, Row } from 'antd/lib/grid';
import Input from 'antd/lib/input';
import Segmented from 'antd/lib/segmented';
import Select from 'antd/lib/select';
import Spin from 'antd/lib/spin';
import Table from 'antd/lib/table';
import Tag from 'antd/lib/tag';
import Timeline from 'antd/lib/timeline';
import Tooltip from 'antd/lib/tooltip';

import {
    errorText, FeedbackIssue, getQuality, getQualityJob, JobFeedback, Person, QualityJob, QualityOverview,
    QualityRating,
} from '../api';

const STATUS_COLOR: Record<string, string> = {
    in_progress: 'blue',
    returned: 'red',
    waiting_review: 'gold',
    in_review: 'purple',
    reviewed: 'cyan',
    in_acceptance: 'geekblue',
    accepted: 'green',
};

const RATING_COLOR: Record<QualityRating['key'], string> = {
    excellent: 'green', good: 'cyan', pass: 'gold', improve: 'red',
};

const OUTCOME: Record<FeedbackIssue['outcome'], { text: string; color: string; icon: JSX.Element }> = {
    confirmed: { text: 'Lỗi đã xác nhận', color: 'red', icon: <ExclamationCircleOutlined /> },
    fixed: { text: 'Đã được sửa', color: 'volcano', icon: <ToolOutlined /> },
    open: { text: 'Chờ QC duyệt', color: 'gold', icon: <ClockCircleOutlined /> },
    waived: { text: 'QC giữ nguyên nhãn', color: 'green', icon: <CheckCircleOutlined /> },
};

const SEVERITY_TEXT: Record<string, string> = { blocker: 'Nghiêm trọng', major: 'Cần xử lý', minor: 'Lưu ý' };

function personName(person: Person | null | undefined): string {
    if (!person) return '—';
    return person.full_name ? `${person.full_name} (${person.username})` : person.username;
}

function formatDate(value: string | null | undefined): string {
    if (!value) return '—';
    const date = new Date(value);
    return Number.isNaN(date.getTime()) ? '—' : date.toLocaleString('vi-VN', {
        day: '2-digit', month: '2-digit', year: 'numeric', hour: '2-digit', minute: '2-digit',
    });
}

function percent(value: number | null | undefined, digits = 1): string {
    return value === null || value === undefined ? '—' : `${(value * 100).toFixed(digits)}%`;
}

function RatingTag({ rating, provisional }: { rating: QualityRating | null; provisional?: boolean }): JSX.Element {
    if (!rating) return <Tag>Chưa có QC</Tag>;
    return (
        <Tooltip title={provisional ? 'Còn lỗi chờ QC duyệt: điểm có thể thay đổi' : undefined}>
            <Tag color={RATING_COLOR[rating.key]}>{`${rating.text}${provisional ? ' · tạm tính' : ''}`}</Tag>
        </Tooltip>
    );
}

/** Horizontal magnitude bars, one series: values read from the labels, the bar only shows proportion. */
function BarList({ items, empty }: { items: { key: string; label: string; value: number; hint?: string }[]; empty: string }):
JSX.Element {
    if (!items.length) return <div className='qc3d-cell-secondary'>{empty}</div>;
    const max = Math.max(...items.map((item) => item.value), 1);
    return (
        <div className='qc3d-barlist' role='list'>
            {items.map((item) => (
                <Tooltip key={item.key} title={item.hint ? `${item.hint}: ${item.value}` : `${item.label}: ${item.value}`}>
                    <div className='qc3d-barlist-row' role='listitem'>
                        <span className='qc3d-barlist-label'>{item.label}</span>
                        <span className='qc3d-barlist-track'>
                            <span className='qc3d-barlist-bar' style={{ width: `${(item.value / max) * 100}%` }} />
                        </span>
                        <span className='qc3d-barlist-value'>{item.value}</span>
                    </div>
                </Tooltip>
            ))}
        </div>
    );
}

/** Weekly quality score, one series: columns from 80 % to 100 % so small changes stay visible. */
function Trend({ trend }: { trend: QualityOverview['trend'] }): JSX.Element {
    const points = trend.filter((week) => week.score !== null);
    if (!points.length) return <div className='qc3d-cell-secondary'>Chưa có job được QC.</div>;
    const floor = 0.8;
    return (
        <div className='qc3d-trend' role='list' aria-label='Điểm chất lượng theo tuần'>
            {points.map((week) => {
                const height = Math.max(4, ((Math.max(week.score as number, floor) - floor) / (1 - floor)) * 100);
                const label = new Date(week.week).toLocaleDateString('vi-VN', { day: '2-digit', month: '2-digit' });
                return (
                    <Tooltip
                        key={week.week}
                        title={`Tuần từ ${label}: ${percent(week.score)} · ${week.jobs} job · ${week.error_objects}/${week.objects} đối tượng có lỗi`}
                    >
                        <div className='qc3d-trend-col' role='listitem' aria-label={`Tuần ${label}: ${percent(week.score)}`}>
                            <span className='qc3d-trend-value'>{percent(week.score, 0)}</span>
                            <span className='qc3d-trend-bar' style={{ height: `${height}%` }} />
                            <span className='qc3d-trend-label'>{label}</span>
                        </div>
                    </Tooltip>
                );
            })}
        </div>
    );
}

function Reviewers({ people }: { people: QualityJob['reviewers'] }): JSX.Element {
    if (!people.length) return <span className='qc3d-cell-secondary'>Chưa có người QC</span>;
    return (
        <div className='qc3d-reviewers'>
            {people.map((person) => (
                <Tooltip key={person.id} title={person.roles.join(' · ')}>
                    <Tag icon={<UserOutlined />}>{person.full_name || person.username}</Tag>
                </Tooltip>
            ))}
        </div>
    );
}

function JobFeedbackDrawer({ jobId, userId, onClose }: {
    jobId: number | null;
    userId: number | null;
    onClose: () => void;
}): JSX.Element {
    const [data, setData] = useState<JobFeedback | null>(null);
    const [error, setError] = useState<string | null>(null);
    const [filter, setFilter] = useState<'all' | FeedbackIssue['outcome']>('all');

    useEffect(() => {
        if (jobId === null) return undefined;
        let cancelled = false;
        setData(null);
        setError(null);
        setFilter('all');
        getQualityJob(jobId, userId)
            .then((result) => { if (!cancelled) setData(result); })
            .catch((err: unknown) => { if (!cancelled) setError(errorText(err)); });
        return () => { cancelled = true; };
    }, [jobId, userId]);

    const issues = data ? data.issues.filter((issue) => filter === 'all' || issue.outcome === filter) : [];
    const count = (outcome: FeedbackIssue['outcome']): number => (
        data ? data.issues.filter((issue) => issue.outcome === outcome).length : 0
    );

    return (
        <Drawer
            open={jobId !== null}
            onClose={onClose}
            width={640}
            title={data ? `Job #${data.job.id} · ${data.job.task_name}` : `Job #${jobId ?? ''}`}
            extra={data && (
                <Link to={`/tasks/${data.job.task_id}/jobs/${data.job.id}`}>Mở job trên CVAT</Link>
            )}
        >
            {error && <Alert type='error' showIcon message='Không tải được kết quả QC' description={error} />}
            {!data && !error && <div className='qc3d-loading-state'><Spin /></div>}
            {data && (
                <div className='qc3d-feedback'>
                    <div className='qc3d-feedback-head'>
                        <Tag color={STATUS_COLOR[data.job.review_status]}>{data.job.review_status_text}</Tag>
                        <RatingTag rating={data.rating} provisional={data.open > 0} />
                        <span>{`Điểm ${percent(data.score)}`}</span>
                    </div>
                    <div className='qc3d-feedback-meta'>
                        <div><span>QC gần nhất</span><b>{formatDate(data.last_qc_date)}</b></div>
                        <div><span>Người chạy QC</span><b>{personName(data.qc_by)}</b></div>
                        <div><span>Guideline</span><b>{data.guideline ?? '—'}</b></div>
                        <div><span>Đối tượng có lỗi</span><b>{`${data.error_objects} / ${data.objects}`}</b></div>
                    </div>
                    <div className='qc3d-section-title'>Người QC job này</div>
                    <Reviewers people={data.reviewers} />

                    <div className='qc3d-section-title'>Lỗi QC trả về</div>
                    <Segmented
                        size='small'
                        value={filter}
                        onChange={(value) => setFilter(value as typeof filter)}
                        options={[
                            { value: 'all', label: `Tất cả ${data.issues.length}` },
                            { value: 'confirmed', label: `Lỗi ${count('confirmed')}` },
                            { value: 'fixed', label: `Đã sửa ${count('fixed')}` },
                            { value: 'open', label: `Chờ duyệt ${count('open')}` },
                            { value: 'waived', label: `Giữ nguyên ${count('waived')}` },
                        ]}
                    />
                    {!issues.length && (
                        <Empty image={Empty.PRESENTED_IMAGE_SIMPLE}
                            description={data.issues.length ? 'Không có lỗi ở mục này' : 'QC không phát hiện lỗi'}
                        />
                    )}
                    <div className='qc3d-feedback-list'>
                        {issues.map((issue) => {
                            const outcome = OUTCOME[issue.outcome];
                            return (
                                <div key={issue.id} className={`qc3d-feedback-item qc3d-outcome-${issue.outcome}`}>
                                    <div className='qc3d-feedback-item-head'>
                                        <Tag color={outcome.color} icon={outcome.icon}>{outcome.text}</Tag>
                                        <b>{`${issue.rule} · ${issue.rule_title}`}</b>
                                        <span className='qc3d-cell-secondary'>
                                            {`Frame ${issue.frame} · ${issue.label || '—'} · ${SEVERITY_TEXT[issue.severity] ?? issue.severity}`}
                                        </span>
                                    </div>
                                    <div>{issue.message}</div>
                                    {issue.fix_summary && <div className='qc3d-cell-secondary'>{`Đã sửa: ${issue.fix_summary}`}</div>}
                                    {issue.decided_by && issue.outcome !== 'open' && (
                                        <div className='qc3d-cell-secondary'>
                                            {`${issue.outcome === 'waived' ? 'Giữ nguyên' : 'Xác nhận'} bởi ${personName(issue.decided_by)} · ${formatDate(issue.decided_date)}`}
                                            {issue.decision_reason && ` · “${issue.decision_reason}”`}
                                        </div>
                                    )}
                                    {issue.fixed_by && (
                                        <div className='qc3d-cell-secondary'>
                                            {`Sửa bởi ${personName(issue.fixed_by)} · ${formatDate(issue.fixed_date)}`}
                                        </div>
                                    )}
                                </div>
                            );
                        })}
                    </div>

                    <div className='qc3d-section-title'>Lịch sử giao việc</div>
                    <Timeline
                        items={data.history.map((step) => ({
                            color: step.stage === 'annotation' ? 'blue' : 'purple',
                            children: (
                                <span>
                                    <b>{personName(step.user)}</b>
                                    {` · ${{ annotation: 'Gán nhãn', validation: 'Review', acceptance: 'Nghiệm thu' }[step.stage] ?? step.stage}`}
                                    <span className='qc3d-cell-secondary'>
                                        {` · ${formatDate(step.started_date)}${step.ended_date ? ` → ${formatDate(step.ended_date)}` : ' → nay'}`}
                                    </span>
                                </span>
                            ),
                        }))}
                    />
                </div>
            )}
        </Drawer>
    );
}

export default function MyWorkPage(): JSX.Element {
    const [userId, setUserId] = useState<number | null>(null);
    const [data, setData] = useState<QualityOverview | null>(null);
    const [error, setError] = useState<string | null>(null);
    const [loading, setLoading] = useState(true);
    const [statusFilter, setStatusFilter] = useState<string | null>(null);
    const [search, setSearch] = useState('');
    const [openJob, setOpenJob] = useState<number | null>(null);

    const load = useCallback(async () => {
        setLoading(true);
        try {
            setData(await getQuality(userId));
            setError(null);
        } catch (err: unknown) {
            setError(errorText(err));
        } finally {
            setLoading(false);
        }
    }, [userId]);

    useEffect(() => { load(); }, [load]);

    const jobs = useMemo(() => {
        if (!data) return [];
        const query = search.trim().toLowerCase();
        return data.jobs.filter((job) => (
            (!statusFilter || job.review_status === statusFilter) &&
            (!query || `#${job.id} ${job.task_name} ${job.project_name ?? ''}`.toLowerCase().includes(query))
        ));
    }, [data, statusFilter, search]);

    if (loading && !data) {
        return <div className='qc3d-loading-state' role='status'><Spin size='large' /><span>Đang tải…</span></div>;
    }
    if (!data) {
        return (
            <Alert type='error' showIcon message='Không tải được chất lượng làm việc' description={error}
                action={<Button onClick={load} loading={loading}>Thử lại</Button>}
            />
        );
    }

    const { summary } = data;
    const self = data.viewer.id === data.subject.id;
    const columns = [
        {
            title: 'Job',
            dataIndex: 'id',
            width: 90,
            render: (id: number) => <Button type='link' onClick={() => setOpenJob(id)}>{`#${id}`}</Button>,
        },
        {
            title: 'Task',
            dataIndex: 'task_name',
            render: (name: string, job: QualityJob) => (
                <div>
                    <div className='qc3d-cell-primary'>{name}</div>
                    <span className='qc3d-cell-secondary'>{job.project_name ?? `Task #${job.task_id}`}</span>
                </div>
            ),
        },
        {
            title: 'Trạng thái',
            dataIndex: 'review_status',
            width: 170,
            render: (value: string, job: QualityJob) => <Tag color={STATUS_COLOR[value]}>{job.review_status_text}</Tag>,
        },
        {
            title: 'Chất lượng',
            dataIndex: 'score',
            width: 170,
            sorter: (a: QualityJob, b: QualityJob) => (a.score ?? -1) - (b.score ?? -1),
            render: (score: number | null, job: QualityJob) => (
                <div>
                    <div className='qc3d-cell-primary'>{percent(score)}</div>
                    <RatingTag rating={job.rating} provisional={job.open > 0} />
                </div>
            ),
        },
        {
            title: 'Lỗi QC',
            key: 'issues',
            width: 210,
            render: (_: unknown, job: QualityJob) => (job.last_qc_date ? (
                <div className='qc3d-issue-counts'>
                    <Tooltip title='Lỗi QC đã xác nhận (kể cả đã được sửa)'><Tag color='red'>{`${job.confirmed} lỗi`}</Tag></Tooltip>
                    <Tooltip title='Chờ QC duyệt'><Tag color='gold'>{`${job.open} chờ`}</Tag></Tooltip>
                    <Tooltip title='QC giữ nguyên nhãn của bạn'><Tag color='green'>{`${job.waived} giữ`}</Tag></Tooltip>
                </div>
            ) : <Tag>Chưa QC</Tag>),
        },
        {
            title: 'Người QC',
            dataIndex: 'reviewers',
            width: 220,
            render: (people: QualityJob['reviewers']) => <Reviewers people={people} />,
        },
        {
            title: 'QC gần nhất', dataIndex: 'last_qc_date', width: 150, render: formatDate,
        },
        {
            title: '',
            key: 'action',
            width: 90,
            render: (_: unknown, job: QualityJob) => <Button size='small' onClick={() => setOpenJob(job.id)}>Xem lỗi</Button>,
        },
    ];

    const statusIcon: Record<string, JSX.Element> = {
        returned: <RollbackOutlined />, waiting_review: <ClockCircleOutlined />, accepted: <CheckCircleOutlined />,
    };

    return (
        <div className='qc3d-dashboard qc3d-my-work'>
            <div className='qc3d-page-header'>
                <div>
                    <h2>{self ? 'Chất lượng làm việc của tôi' : `Chất lượng làm việc · ${personName(data.subject)}`}</h2>
                    <p>
                        Các job 3D đã gán nhãn, trạng thái review, lỗi QC trả về và ai đã QC. Chỉ lỗi được QC xác nhận
                        mới tính vào điểm; lỗi QC giữ nguyên nhãn không bị trừ.
                    </p>
                </div>
                <div className='qc3d-page-actions'>
                    {data.viewer.can_view_team && (
                        <Select
                            showSearch
                            style={{ minWidth: 240 }}
                            aria-label='Chọn người cần xem'
                            value={userId ?? data.viewer.id}
                            optionFilterProp='label'
                            onChange={(value: number) => setUserId(value === data.viewer.id ? null : value)}
                            options={[
                                { value: data.viewer.id, label: `Tôi (${data.viewer.username})` },
                                ...data.people.filter((person) => person.id !== data.viewer.id).map((person) => ({
                                    value: person.id, label: personName(person),
                                })),
                            ]}
                        />
                    )}
                    <Button icon={<ReloadOutlined />} onClick={load} loading={loading}>Làm mới</Button>
                </div>
            </div>
            {error && <Alert type='warning' showIcon message='Chưa cập nhật được dữ liệu' description={error} />}

            <Row gutter={[16, 16]}>
                <Col xs={24} md={12} xl={6}>
                    <Card className='qc3d-stat-card qc3d-stat-accent'>
                        <div className='qc3d-kpi-label'>Điểm chất lượng</div>
                        <div className='qc3d-kpi-value'>{percent(summary.score)}</div>
                        <RatingTag rating={summary.rating} provisional={summary.provisional} />
                        <div className='qc3d-stat-caption'>Tỉ lệ đối tượng không có lỗi được xác nhận</div>
                    </Card>
                </Col>
                <Col xs={24} md={12} xl={6}>
                    <Card className='qc3d-stat-card'>
                        <div className='qc3d-kpi-label'>Job đã làm</div>
                        <div className='qc3d-kpi-value'>{summary.jobs}</div>
                        <div className='qc3d-stat-caption'>{`${summary.checked_jobs} job đã chạy QC · ${summary.objects} đối tượng được kiểm`}</div>
                    </Card>
                </Col>
                <Col xs={24} md={12} xl={6}>
                    <Card className='qc3d-stat-card'>
                        <div className='qc3d-kpi-label'>Lỗi QC xác nhận</div>
                        <div className='qc3d-kpi-value'>{summary.confirmed}</div>
                        <div className='qc3d-stat-caption'>
                            {`${summary.error_objects} đối tượng có lỗi · ${summary.fixed} đã được sửa`}
                        </div>
                    </Card>
                </Col>
                <Col xs={24} md={12} xl={6}>
                    <Card className='qc3d-stat-card'>
                        <div className='qc3d-kpi-label'>Chờ QC duyệt</div>
                        <div className='qc3d-kpi-value'>{summary.open}</div>
                        <div className='qc3d-stat-caption'>
                            {`QC giữ nguyên ${summary.waived} · tỉ lệ cảnh báo đúng ${percent(summary.confirm_rate, 0)}`}
                        </div>
                    </Card>
                </Col>
            </Row>

            <div className='qc3d-status-strip' role='group' aria-label='Lọc theo trạng thái review'>
                {data.status_counts.map((status) => (
                    <button
                        type='button'
                        key={status.key}
                        aria-pressed={statusFilter === status.key}
                        className={`qc3d-status-chip${statusFilter === status.key ? ' qc3d-status-active' : ''}`}
                        onClick={() => setStatusFilter(statusFilter === status.key ? null : status.key)}
                    >
                        <Tag color={STATUS_COLOR[status.key]} icon={statusIcon[status.key]}>{status.text}</Tag>
                        <b>{status.count}</b>
                    </button>
                ))}
            </div>

            <Row gutter={[16, 16]}>
                <Col xs={24} lg={8}>
                    <Card title='Lỗi thường gặp' className='qc3d-chart-card'>
                        <BarList
                            empty='Chưa có lỗi được xác nhận.'
                            items={data.by_rule.map((item) => ({
                                key: item.rule, label: `${item.rule} · ${item.title}`, value: item.count, hint: item.title,
                            }))}
                        />
                    </Card>
                </Col>
                <Col xs={24} lg={8}>
                    <Card title='Lỗi theo class' className='qc3d-chart-card'>
                        <BarList
                            empty='Chưa có lỗi được xác nhận.'
                            items={data.by_label.map((item) => ({ key: item.label, label: item.label, value: item.count }))}
                        />
                    </Card>
                </Col>
                <Col xs={24} lg={8}>
                    <Card title='Điểm chất lượng theo tuần' className='qc3d-chart-card'>
                        <Trend trend={data.trend} />
                    </Card>
                </Col>
            </Row>

            <Card
                className='qc3d-table-card'
                title={`Job đã gán nhãn · ${jobs.length}${statusFilter ? ` / ${data.jobs.length}` : ''}`}
                extra={(
                    <Input
                        allowClear
                        prefix={<SearchOutlined />}
                        placeholder='Tìm job, task, project'
                        aria-label='Tìm job đã làm'
                        value={search}
                        onChange={(event) => setSearch(event.target.value)}
                        style={{ width: 240 }}
                    />
                )}
            >
                <Table<QualityJob>
                    rowKey='id'
                    size='middle'
                    columns={columns}
                    dataSource={jobs}
                    scroll={{ x: 1150 }}
                    pagination={{ defaultPageSize: 10, hideOnSinglePage: true, showSizeChanger: true }}
                    locale={{
                        emptyText: (
                            <Empty
                                image={Empty.PRESENTED_IMAGE_SIMPLE}
                                description={data.jobs.length ? 'Không có job phù hợp bộ lọc' :
                                    'Chưa có job 3D nào được giao cho bạn ở bước gán nhãn'}
                            />
                        ),
                    }}
                />
            </Card>

            <JobFeedbackDrawer jobId={openJob} userId={userId} onClose={() => setOpenJob(null)} />
        </div>
    );
}
