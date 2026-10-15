// Copyright (C) CVAT.ai Corporation
//
// SPDX-License-Identifier: MIT

import React, { useCallback, useEffect, useMemo, useState } from 'react';
import { Link } from 'react-router-dom';
import {
    ArcElement, BarElement, CategoryScale, Chart as ChartJS, ChartOptions, Filler, Legend, LinearScale, LineElement,
    PointElement, Tooltip as ChartTooltip,
} from 'chart.js';
import { Bar, Doughnut, Line } from 'react-chartjs-2';
import { ReloadOutlined } from '@ant-design/icons';
import Alert from 'antd/lib/alert';
import Button from 'antd/lib/button';
import Card from 'antd/lib/card';
import Empty from 'antd/lib/empty';
import { Col, Row } from 'antd/lib/grid';
import Progress from 'antd/lib/progress';
import Segmented from 'antd/lib/segmented';
import Select from 'antd/lib/select';
import Spin from 'antd/lib/spin';
import Table from 'antd/lib/table';
import Tag from 'antd/lib/tag';
import Tooltip from 'antd/lib/tooltip';

import {
    errorText, getAnalytics, getBoard, QcAnalytics, QualityRating, WorkflowBoard,
} from '../api';
import { STATUS_META } from '../workflow-ui';

ChartJS.register(
    ArcElement, BarElement, CategoryScale, Filler, Legend, LinearScale, LineElement, PointElement, ChartTooltip,
);

// One meaning, one colour, on every chart (validated categorical slots; "chờ duyệt" is neutral: undecided)
const COLOR = {
    found: '#2a78d6',
    confirmed: '#eb6834',
    fixed: '#1baf7a',
    waived: '#eda100',
    open: '#a3a29d',
    magnitude: '#2a78d6',
};
const SEVERITY = [
    { key: 'blocker', text: 'Nghiêm trọng', color: '#a3361a' },
    { key: 'major', text: 'Cần xử lý', color: '#eb6834' },
    { key: 'minor', text: 'Lưu ý', color: '#f6b89a' },
] as const;
const TEXT = { primary: '#203149', secondary: '#69788d', grid: '#edf1f7' };
const RATING_COLOR: Record<QualityRating['key'], string> = {
    excellent: 'green', good: 'cyan', pass: 'gold', improve: 'red',
};

const FONT = { family: 'inherit', size: 12 };

function baseOptions<T extends 'line' | 'bar'>(extra: ChartOptions<T> = {} as ChartOptions<T>): ChartOptions<T> {
    return {
        responsive: true,
        maintainAspectRatio: false,
        animation: { duration: 700, easing: 'easeOutQuart' },
        plugins: {
            legend: {
                position: 'top',
                align: 'end',
                labels: { usePointStyle: true, pointStyle: 'circle', boxWidth: 8, color: TEXT.secondary, font: FONT },
            },
            tooltip: {
                backgroundColor: 'rgba(32, 49, 73, 0.94)',
                padding: 10,
                cornerRadius: 8,
                titleFont: { ...FONT, weight: 'bold' },
                bodyFont: FONT,
                usePointStyle: true,
            },
        },
        ...extra,
    } as ChartOptions<T>;
}

function percent(value: number | null | undefined, digits = 1): string {
    return value === null || value === undefined ? '—' : `${(value * 100).toFixed(digits)}%`;
}

function shortDay(day: string): string {
    const [, month, date] = day.split('-');
    return `${date}/${month}`;
}

function Kpi({
    label, value, caption, accent, children,
}: { label: string; value: string | number; caption?: string; accent?: string; children?: React.ReactNode }):
    JSX.Element {
    return (
        <div className='qc3d-kpi' style={accent ? { borderTopColor: accent } : undefined}>
            <span className='qc3d-kpi-label'>{label}</span>
            <span className='qc3d-kpi-value'>{value}</span>
            {children}
            {caption && <span className='qc3d-stat-caption'>{caption}</span>}
        </div>
    );
}

function ChartCard({
    title, subtitle, children, empty, height = 260,
}: { title: string; subtitle?: string; children: React.ReactNode; empty?: boolean; height?: number }): JSX.Element {
    return (
        <Card className='qc3d-chart-card' title={<div><div>{title}</div>{subtitle && <small>{subtitle}</small>}</div>}>
            {empty ? <Empty image={Empty.PRESENTED_IMAGE_SIMPLE} description='Chưa có dữ liệu trong khoảng này' /> : (
                <div style={{ position: 'relative', height }}>{children}</div>
            )}
        </Card>
    );
}

export default function QcAnalyticsPanel(): JSX.Element {
    const [days, setDays] = useState(30);
    const [projectId, setProjectId] = useState<number | null>(null);
    const [taskId, setTaskId] = useState<number | null>(null);
    const [data, setData] = useState<QcAnalytics | null>(null);
    const [error, setError] = useState<string | null>(null);
    const [loading, setLoading] = useState(true);

    const load = useCallback(async () => {
        setLoading(true);
        try {
            setData(await getAnalytics(days, projectId, taskId));
            setError(null);
        } catch (err: unknown) {
            setError(errorText(err));
        } finally {
            setLoading(false);
        }
    }, [days, projectId, taskId]);

    useEffect(() => { load(); }, [load]);

    const charts = useMemo(() => {
        if (!data) return null;
        const labels = data.daily.map((item) => shortDay(item.day));
        const line = (key: 'found' | 'confirmed' | 'fixed' | 'waived', label: string, fill = false): object => ({
            label,
            data: data.daily.map((item) => item[key]),
            borderColor: COLOR[key],
            backgroundColor: fill ? 'rgba(42, 120, 214, 0.10)' : COLOR[key],
            fill,
            borderWidth: 2,
            tension: 0.35,
            pointRadius: data.daily.length > 40 ? 0 : 3,
            pointHoverRadius: 5,
            pointBackgroundColor: '#fff',
            pointBorderWidth: 2,
        });
        const rules = data.by_rule.slice(0, 10);
        return {
            activity: {
                labels,
                datasets: [
                    line('found', 'Phát hiện', true),
                    line('confirmed', 'Xác nhận lỗi'),
                    line('fixed', 'Đã sửa'),
                    line('waived', 'Giữ nguyên'),
                ],
            },
            rules: {
                labels: rules.map((rule) => `${rule.rule} · ${rule.title}`),
                datasets: [
                    { key: 'confirmed', label: 'Xác nhận lỗi' },
                    { key: 'waived', label: 'Giữ nguyên' },
                    { key: 'open', label: 'Chờ duyệt' },
                ].map(({ key, label }) => ({
                    label,
                    data: rules.map((rule) => rule[key as 'confirmed' | 'waived' | 'open']),
                    backgroundColor: COLOR[key as 'confirmed' | 'waived' | 'open'],
                    borderColor: '#ffffff',
                    borderWidth: { right: 2 },
                    borderRadius: 4,
                    borderSkipped: false,
                    barThickness: 16,
                })),
            },
            labels: {
                labels: data.by_label.map((item) => item.label),
                datasets: [{
                    label: 'Lỗi xác nhận',
                    data: data.by_label.map((item) => item.count),
                    backgroundColor: COLOR.magnitude,
                    borderRadius: 4,
                    maxBarThickness: 28,
                }],
            },
            severity: {
                labels: SEVERITY.map((item) => item.text),
                datasets: [{
                    data: SEVERITY.map((item) => data.open_by_severity[item.key] ?? 0),
                    backgroundColor: SEVERITY.map((item) => item.color),
                    borderColor: '#ffffff',
                    borderWidth: 2,
                    hoverOffset: 6,
                }],
            },
            statuses: {
                labels: data.status_counts.map((item) => item.text),
                datasets: [{
                    label: 'Job',
                    data: data.status_counts.map((item) => item.count),
                    backgroundColor: COLOR.magnitude,
                    borderRadius: 4,
                    barThickness: 14,
                }],
            },
        };
    }, [data]);

    if (loading && !data) {
        return <div className='qc3d-loading-state' role='status'><Spin /><span>Đang tính số liệu QC…</span></div>;
    }
    if (!data || !charts) {
        return (
            <Alert type='error' showIcon message='Không tải được phân tích QC' description={error}
                action={<Button onClick={load} loading={loading}>Thử lại</Button>}
            />
        );
    }

    const { totals } = data;
    const openTotal = SEVERITY.reduce((sum, item) => sum + (data.open_by_severity[item.key] ?? 0), 0);
    const tasks = data.filters.tasks.filter((task) => projectId === null || task.project_id === projectId);
    const hasActivity = data.daily.some((item) => item.found || item.confirmed || item.fixed || item.waived);

    return (
        <div className='qc3d-analytics'>
            <div className='qc3d-analytics-toolbar'>
                <div>
                    <h3>Phân tích chất lượng QC</h3>
                    <span className='qc3d-cell-secondary'>
                        {`${totals.jobs} job 3D trong phạm vi bạn xem được · ${totals.checked_jobs} job có QC trong ${data.days} ngày`}
                    </span>
                </div>
                <div className='qc3d-analytics-filters'>
                    <Select
                        allowClear
                        placeholder='Mọi project'
                        aria-label='Lọc theo project'
                        style={{ width: 180 }}
                        value={projectId ?? undefined}
                        onChange={(value?: number) => { setProjectId(value ?? null); setTaskId(null); }}
                        options={data.filters.projects.map((p) => ({ value: p.id, label: p.name }))}
                    />
                    <Select
                        allowClear
                        showSearch
                        optionFilterProp='label'
                        placeholder='Mọi task'
                        aria-label='Lọc theo task'
                        style={{ width: 200 }}
                        value={taskId ?? undefined}
                        onChange={(value?: number) => setTaskId(value ?? null)}
                        options={tasks.map((t) => ({ value: t.id, label: `#${t.id} ${t.name}` }))}
                    />
                    <Segmented
                        value={days}
                        onChange={(value) => setDays(Number(value))}
                        options={[
                            { value: 7, label: '7 ngày' },
                            { value: 30, label: '30 ngày' },
                            { value: 90, label: '90 ngày' },
                            { value: 365, label: '1 năm' },
                        ]}
                    />
                    <Button icon={<ReloadOutlined />} onClick={load} loading={loading} aria-label='Làm mới số liệu' />
                </div>
            </div>
            {error && <Alert type='warning' showIcon message='Chưa cập nhật được số liệu' description={error} />}

            <div className='qc3d-kpi-grid'>
                <Kpi label='Điểm chất lượng' value={percent(totals.score)} accent='#2a78d6'
                    caption={`${totals.error_objects} / ${totals.objects} đối tượng có lỗi xác nhận`}
                >
                    {totals.rating && <Tag color={RATING_COLOR[totals.rating.key]}>{totals.rating.text}</Tag>}
                </Kpi>
                <Kpi label='Lỗi phát hiện' value={totals.found} accent={COLOR.found}
                    caption={`${totals.runs} lần chạy QC`}
                />
                <Kpi label='Độ chính xác của QC' value={percent(totals.precision)} accent={COLOR.confirmed}
                    caption={`${totals.confirmed} xác nhận · ${totals.waived} giữ nguyên`}
                />
                <Kpi label='Chờ duyệt' value={totals.open} accent={COLOR.open}
                    caption={totals.median_decision_hours === null ? 'Chưa có quyết định' :
                        `Duyệt trung vị sau ${totals.median_decision_hours} giờ`}
                />
                <Kpi label='Đã sửa' value={totals.fixed} accent={COLOR.fixed}
                    caption={totals.reverted ? `${totals.reverted} bản sửa đã hoàn tác` : 'Không có bản sửa bị hoàn tác'}
                />
            </div>

            <Row gutter={[16, 16]}>
                <Col xs={24} xl={16}>
                    <ChartCard title='Hoạt động QC theo ngày' subtitle='Lỗi phát hiện và quyết định của reviewer'
                        empty={!hasActivity} height={280}
                    >
                        <Line
                            data={charts.activity as never}
                            options={baseOptions<'line'>({
                                interaction: { mode: 'index', intersect: false },
                                scales: {
                                    x: { grid: { display: false }, ticks: { color: TEXT.secondary, font: FONT, maxTicksLimit: 10 } },
                                    y: {
                                        beginAtZero: true,
                                        grid: { color: TEXT.grid },
                                        border: { display: false },
                                        ticks: { color: TEXT.secondary, font: FONT, precision: 0 },
                                    },
                                },
                            })}
                        />
                    </ChartCard>
                </Col>
                <Col xs={24} md={12} xl={8}>
                    <ChartCard title='Lỗi chờ duyệt theo mức độ' empty={!openTotal} height={280}>
                        <Doughnut
                            data={charts.severity}
                            options={{
                                responsive: true,
                                maintainAspectRatio: false,
                                cutout: '68%',
                                animation: { animateRotate: true, duration: 800 },
                                plugins: {
                                    legend: {
                                        position: 'bottom',
                                        labels: { usePointStyle: true, boxWidth: 8, color: TEXT.secondary, font: FONT },
                                    },
                                    tooltip: { backgroundColor: 'rgba(32, 49, 73, 0.94)', padding: 10, cornerRadius: 8 },
                                },
                            }}
                        />
                        <div className='qc3d-doughnut-center' aria-hidden>
                            <b>{openTotal}</b>
                            <span>chờ duyệt</span>
                        </div>
                    </ChartCard>
                </Col>
                <Col xs={24} xl={14}>
                    <ChartCard title='Lỗi theo rule' subtitle='Rule nào hay bị giữ nguyên là rule cần chỉnh ngưỡng'
                        empty={!data.by_rule.length} height={Math.max(220, data.by_rule.slice(0, 10).length * 34 + 60)}
                    >
                        <Bar
                            data={charts.rules as never}
                            options={baseOptions<'bar'>({
                                indexAxis: 'y',
                                interaction: { mode: 'index', intersect: false },
                                scales: {
                                    x: {
                                        stacked: true, beginAtZero: true, grid: { color: TEXT.grid }, border: { display: false },
                                        ticks: { color: TEXT.secondary, font: FONT, precision: 0 },
                                    },
                                    y: { stacked: true, grid: { display: false }, ticks: { color: TEXT.primary, font: FONT } },
                                },
                            })}
                        />
                    </ChartCard>
                </Col>
                <Col xs={24} md={12} xl={10}>
                    <ChartCard title='Lỗi xác nhận theo class' empty={!data.by_label.length} height={260}>
                        <Bar
                            data={charts.labels}
                            options={baseOptions<'bar'>({
                                plugins: { legend: { display: false } },
                                scales: {
                                    x: { grid: { display: false }, ticks: { color: TEXT.primary, font: FONT } },
                                    y: {
                                        beginAtZero: true, grid: { color: TEXT.grid }, border: { display: false },
                                        ticks: { color: TEXT.secondary, font: FONT, precision: 0 },
                                    },
                                },
                            })}
                        />
                    </ChartCard>
                </Col>
                <Col xs={24} md={12} xl={8}>
                    <ChartCard title='Trạng thái job' empty={!totals.jobs} height={260}>
                        <Bar
                            data={charts.statuses}
                            options={baseOptions<'bar'>({
                                indexAxis: 'y',
                                plugins: { legend: { display: false } },
                                scales: {
                                    x: {
                                        beginAtZero: true, grid: { color: TEXT.grid }, border: { display: false },
                                        ticks: { color: TEXT.secondary, font: FONT, precision: 0 },
                                    },
                                    y: { grid: { display: false }, ticks: { color: TEXT.primary, font: FONT } },
                                },
                            })}
                        />
                    </ChartCard>
                </Col>
                <Col xs={24} xl={16}>
                    <Card className='qc3d-chart-card' title='Job cần chú ý'>
                        {!data.attention.length ? (
                            <Empty image={Empty.PRESENTED_IMAGE_SIMPLE} description='Không có job còn lỗi chờ duyệt' />
                        ) : (
                            <div className='qc3d-attention'>
                                {data.attention.map((job) => {
                                    const max = Math.max(...data.attention.map((item) => item.open), 1);
                                    return (
                                        <Link key={job.id} to={`/qc3d/jobs/${job.id}`} className='qc3d-attention-row'>
                                            <span className='qc3d-attention-name'>
                                                <b>{`#${job.id}`}</b>
                                                {` ${job.task_name}`}
                                            </span>
                                            <span className='qc3d-barlist-track'>
                                                <span className='qc3d-barlist-bar' style={{
                                                    width: `${(job.open / max) * 100}%`, background: COLOR.open,
                                                }}
                                                />
                                            </span>
                                            <span className='qc3d-attention-value'>
                                                {job.checked ? `${job.open} chờ duyệt` : <Tag>Chưa QC</Tag>}
                                            </span>
                                        </Link>
                                    );
                                })}
                            </div>
                        )}
                    </Card>
                </Col>
            </Row>

            {(data.annotators || data.reviewers) && (
                <Row gutter={[16, 16]}>
                    {data.annotators && (
                        <Col xs={24} xl={data.reviewers ? 13 : 24}>
                            <Card className='qc3d-chart-card' title='Chất lượng theo người gán nhãn'>
                                <Table
                                    rowKey='id'
                                    size='small'
                                    pagination={{ pageSize: 8, hideOnSinglePage: true }}
                                    dataSource={data.annotators}
                                    locale={{ emptyText: 'Chưa có job được giao ở bước gán nhãn trong khoảng này' }}
                                    columns={[
                                        {
                                            title: 'Người làm',
                                            dataIndex: 'username',
                                            render: (name: string, person) => person.full_name || name,
                                        },
                                        {
                                            title: 'Điểm',
                                            dataIndex: 'score',
                                            width: 200,
                                            sorter: (a, b) => (a.score ?? -1) - (b.score ?? -1),
                                            render: (score: number | null, person) => (
                                                <Tooltip title={`${person.error_objects}/${person.objects} đối tượng có lỗi`}>
                                                    <div className='qc3d-score-cell'>
                                                        <Progress
                                                            percent={score === null ? 0 : Math.round(score * 1000) / 10}
                                                            size='small'
                                                            showInfo={false}
                                                            strokeColor={COLOR.magnitude}
                                                        />
                                                        <span>{percent(score)}</span>
                                                    </div>
                                                </Tooltip>
                                            ),
                                        },
                                        {
                                            title: 'Xếp loại',
                                            dataIndex: 'rating',
                                            width: 120,
                                            render: (value: QualityRating | null) => (
                                                value ? <Tag color={RATING_COLOR[value.key]}>{value.text}</Tag> : '—'
                                            ),
                                        },
                                        { title: 'Job', dataIndex: 'jobs', width: 60 },
                                        { title: 'Lỗi', dataIndex: 'confirmed', width: 60 },
                                        { title: 'Chờ', dataIndex: 'open', width: 60 },
                                    ]}
                                />
                            </Card>
                        </Col>
                    )}
                    {data.reviewers && (
                        <Col xs={24} xl={data.annotators ? 11 : 24}>
                            <Card className='qc3d-chart-card' title='Khối lượng review'>
                                <Table
                                    rowKey='id'
                                    size='small'
                                    pagination={{ pageSize: 8, hideOnSinglePage: true }}
                                    dataSource={data.reviewers}
                                    locale={{ emptyText: 'Chưa có hoạt động review trong khoảng này' }}
                                    columns={[
                                        {
                                            title: 'Reviewer',
                                            dataIndex: 'username',
                                            render: (name: string, person) => person.full_name || name,
                                        },
                                        { title: 'Chạy QC', dataIndex: 'runs', width: 80 },
                                        {
                                            title: 'Quyết định',
                                            dataIndex: 'decisions',
                                            width: 110,
                                            render: (value: number, person) => (
                                                <Tooltip title={`${person.confirmed} xác nhận · ${person.waived} giữ nguyên`}>
                                                    <span>{value}</span>
                                                </Tooltip>
                                            ),
                                        },
                                        { title: 'Sửa nhãn', dataIndex: 'fixes', width: 90 },
                                    ]}
                                />
                            </Card>
                        </Col>
                    )}
                </Row>
            )}
        </div>
    );
}


/** Jobs at each step of the review / acceptance workflow (dashboard). */
export function WorkflowFunnel(): JSX.Element {
    const [board, setBoard] = useState<WorkflowBoard | null>(null);
    const [error, setError] = useState<string | null>(null);
    useEffect(() => {
        getBoard().then(setBoard).catch((err) => setError(errorText(err)));
    }, []);
    if (error) return <Alert type='warning' showIcon message='Chưa tải được quy trình job' description={error} />;
    if (!board) return <Card className='qc3d-chart-card'><Spin /></Card>;

    const count = (status: string): number => board.columns.find((c) => c.status === status)?.count ?? 0;
    const total = board.jobs.length;
    const done = count('accepted') + count('signed_off');
    const returned = count('returned');
    const waiting = count('waiting_review') + count('waiting_acceptance');
    const finished = done + returned;
    const options = baseOptions<'bar'>({
        indexAxis: 'y',
        scales: {
            x: { beginAtZero: true, ticks: { precision: 0, color: TEXT.secondary, font: FONT }, grid: { color: TEXT.grid } },
            y: { ticks: { color: TEXT.primary, font: FONT }, grid: { display: false } },
        },
        plugins: {
            legend: { display: false },
            tooltip: { callbacks: { label: (item) => ` ${item.parsed.x} job` } },
        },
    } as ChartOptions<'bar'>);
    return (
        <Card
            className='qc3d-chart-card qc3d-funnel-card'
            title={<div><div>Quy trình duyệt và nghiệm thu</div><small>Số job 3D ở từng bước · bấm Trạng thái job để xem chi tiết</small></div>}
            extra={<Link to='/qc3d/board'>Trạng thái job</Link>}
        >
            <div className='qc3d-funnel'>
                <div className='qc3d-funnel-kpis'>
                    <Kpi label='Job trong quy trình' value={total} accent='#315be8' caption='Job 3D bạn xem được' />
                    <Kpi label='Đang chờ' value={waiting} accent='#a86400' caption='Chờ QC hoặc chờ nghiệm thu' />
                    <Kpi label='Đạt / đã chốt' value={done} accent='#16803c' caption={finished ? `Tỷ lệ đạt ${percent(done / finished, 0)}` : 'Chưa có kết luận'} />
                    <Kpi label='Bị trả lại' value={returned} accent='#d9384a' caption='Cần người gán nhãn sửa' />
                </div>
                <div className='qc3d-funnel-chart'>
                    {total ? (
                        <Bar
                            options={options}
                            data={{
                                labels: board.columns.map((c) => c.text),
                                datasets: [{
                                    data: board.columns.map((c) => c.count),
                                    backgroundColor: board.columns.map((c) => STATUS_META[c.status].color),
                                    borderRadius: 6,
                                    barThickness: 16,
                                }],
                            }}
                        />
                    ) : <Empty image={Empty.PRESENTED_IMAGE_SIMPLE} description='Chưa có job 3D' />}
                </div>
            </div>
        </Card>
    );
}
