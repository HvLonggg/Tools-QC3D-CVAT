// Copyright (C) CVAT.ai Corporation
//
// SPDX-License-Identifier: MIT

import React, {
    useCallback, useEffect, useMemo, useRef, useState,
} from 'react';
import { Link, useParams } from 'react-router-dom';
import {
    ArrowLeftOutlined, ExportOutlined, ReloadOutlined, SearchOutlined, ToolOutlined,
} from '@ant-design/icons';
import Alert from 'antd/lib/alert';
import Button from 'antd/lib/button';
import Card from 'antd/lib/card';
import Descriptions from 'antd/lib/descriptions';
import Input from 'antd/lib/input';
import Modal from 'antd/lib/modal';
import Popconfirm from 'antd/lib/popconfirm';
import Segmented from 'antd/lib/segmented';
import Select from 'antd/lib/select';
import Space from 'antd/lib/space';
import Spin from 'antd/lib/spin';
import Table from 'antd/lib/table';
import Tag from 'antd/lib/tag';
import Tooltip from 'antd/lib/tooltip';
import notification from 'antd/lib/notification';

import {
    applyFix, cvatObjectLink, decideIssue, errorText, getJobGuideline, getJobSettings, getOverview, getReport,
    getWorkflow, IssueStatus, JobGuideline, JobQcSettings, OverviewMap as MapData, QcIssue, QcReport, revertFix, runQc,
    Severity, WorkflowState,
} from '../api';
import OverviewMap from '../overview-map';
import {
    StatusBadge, WorkflowActions, WorkflowHistory, WorkflowSteps,
} from '../workflow-ui';

const range = (lo: number, hi: number): string => `${lo}–${hi}`;

function GuidelineCard({ guideline }: { guideline: JobGuideline }): JSX.Element {
    const { content } = guideline;
    const changed = guideline.last_run_key !== null && guideline.last_run_key !== guideline.key;
    const offRules = Object.entries(content.rules).filter(([, on]) => !on).map(([code]) => code);
    return (
        <Card
            title='Hướng dẫn kiểm tra áp dụng'
            className='qc3d-job-guideline'
            size='small'
            extra={guideline.id !== null && (guideline.source === 'task' || guideline.source === 'project' ||
                guideline.source === 'default') ? (
                    <Link to={`/qc3d/guidelines/${guideline.id}`}>Xem / chỉnh sửa</Link>
                ) : <Link to='/qc3d/guidelines'>Quản lý hướng dẫn</Link>}
        >
            <Space direction='vertical' style={{ width: '100%' }}>
                <Space wrap>
                    <Tag color='blue'>{guideline.source_text}</Tag>
                    <b>{guideline.name}</b>
                    <span style={{ color: '#888' }}>{`bản ${guideline.revision}`}</span>
                </Space>
                {changed && (
                    <Alert type='warning' showIcon message='Guideline đã thay đổi sau lần QC gần nhất: bấm "Chạy QC" để kiểm lại theo guideline mới.' />
                )}
                {guideline.labels_without_class.length > 0 && (
                    <Alert
                        type='warning'
                        showIcon
                        message={`Label của task chưa có trong guideline (không kiểm kích thước / class): ${guideline.labels_without_class.join(', ')}`}
                    />
                )}
                {guideline.classes_without_label.length > 0 && (
                    <Alert
                        type='info'
                        showIcon
                        message={`Class trong guideline chưa có label trên task (không đề xuất đổi sang được): ${guideline.classes_without_label.join(', ')}`}
                    />
                )}
                {guideline.skipped.map((text) => <Alert key={text} type='info' message={`Bỏ qua: ${text}`} />)}
                {guideline.warnings.map((text) => <Alert key={text} type='warning' showIcon message={text} />)}
                <details className='qc3d-job-guideline-details'>
                    <summary>{`Thông số kiểm tra · ${content.classes.length} nhóm nhãn`}</summary>
                    <Space wrap size={4}>
                        {content.classes.map((cls) => (
                            <Tooltip key={cls.name} title={cls.description}>
                                <Tag>
                                    {`${cls.name}: ${cls.size_min.map((lo, i) => range(lo, cls.size_max[i])).join(' × ')} m`}
                                </Tag>
                            </Tooltip>
                        ))}
                    </Space>
                    {(Object.keys(content.thresholds).length > 0 || offRules.length > 0) && (
                        <Space wrap size={4}>
                            {Object.entries(content.thresholds).map(([key, value]) => <Tag key={key} color='purple'>{`${key} = ${value}`}</Tag>)}
                            {offRules.map((code) => <Tag key={code} color='default'>{`${code} tắt`}</Tag>)}
                        </Space>
                    )}
                    {content.notes && (
                        <details>
                            <summary>Nội dung hướng dẫn</summary>
                            <pre style={{ whiteSpace: 'pre-wrap', maxHeight: 300, overflow: 'auto' }}>{content.notes}</pre>
                        </details>
                    )}
                </details>
            </Space>
        </Card>
    );
}

const SEVERITY_COLOR: Record<Severity, string> = { blocker: 'red', major: 'orange', minor: 'blue' };
const SEVERITY_TEXT: Record<Severity, string> = { blocker: 'Nghiêm trọng', major: 'Cần xử lý', minor: 'Lưu ý' };
const SEVERITY_RANK: Record<Severity, number> = { blocker: 0, major: 1, minor: 2 };
const STATUS_TEXT: Record<IssueStatus, string> = { open: 'Cần duyệt', confirmed: 'Lỗi thật', waived: 'Giữ nguyên' };
const STATUS_COLOR: Record<IssueStatus, string> = { open: 'default', confirmed: 'red', waived: 'green' };

type Filter = 'open' | 'all' | 'confirmed' | 'waived' | 'fixed';

export default function JobPage(): JSX.Element {
    const { jobId } = useParams<{ jobId: string }>();
    const [report, setReport] = useState<QcReport | null>(null);
    const [error, setError] = useState<string | null>(null);
    const [running, setRunning] = useState(false);
    const [filter, setFilter] = useState<Filter>('open');
    const [waiving, setWaiving] = useState<QcIssue | null>(null);
    const [reason, setReason] = useState('');
    const [guideline, setGuideline] = useState<JobGuideline | null>(null);
    const [settings, setSettings] = useState<JobQcSettings | null>(null);
    const [loading, setLoading] = useState(true);
    const [pending, setPending] = useState<number[]>([]);
    const [search, setSearch] = useState('');
    const [severityFilter, setSeverityFilter] = useState<Severity | undefined>(undefined);
    const [workflow, setWorkflow] = useState<WorkflowState | null>(null);
    const [overview, setOverview] = useState<MapData | null>(null);
    const [overviewError, setOverviewError] = useState<string | null>(null);
    const activeJobId = useRef(jobId);
    activeJobId.current = jobId;

    const loadWorkflow = useCallback(() => {
        getWorkflow(+jobId).then((next) => {
            if (activeJobId.current === jobId) setWorkflow(next);
        }).catch(() => { if (activeJobId.current === jobId) setWorkflow(null); });
        getOverview(+jobId).then((next) => {
            if (activeJobId.current !== jobId) return;
            setOverview(next);
            setOverviewError(null);
        }).catch((err) => { if (activeJobId.current === jobId) setOverviewError(errorText(err)); });
    }, [jobId]);

    const loadGuideline = useCallback(() => {
        getJobGuideline(+jobId).then((nextGuideline) => {
            if (activeJobId.current === jobId) setGuideline(nextGuideline);
        }).catch(() => { if (activeJobId.current === jobId) setGuideline(null); });
    }, [jobId]);

    const load = useCallback(async (): Promise<void> => {
        setLoading(true);
        setError(null);
        try {
            const [nextReport, nextSettings] = await Promise.all([getReport(+jobId), getJobSettings(+jobId)]);
            if (activeJobId.current !== jobId) return;
            setReport(nextReport);
            setSettings(nextSettings);
        } catch (err: any) {
            if (activeJobId.current === jobId) setError(errorText(err));
        } finally {
            if (activeJobId.current === jobId) setLoading(false);
        }
    }, [jobId]);

    useEffect(() => {
        setReport(null);
        setSettings(null);
        setGuideline(null);
        setFilter('open');
        setSearch('');
        setSeverityFilter(undefined);
        setPending([]);
        setWaiving(null);
        setRunning(false);
        setWorkflow(null);
        setOverview(null);
        load();
        loadGuideline();
        loadWorkflow();
    }, [load, loadGuideline, loadWorkflow]);

    const onRun = useCallback(async () => {
        setRunning(true);
        try {
            const result = await runQc(+jobId);
            if (activeJobId.current !== jobId) return;
            setReport(result);
            loadGuideline();
            loadWorkflow();
            notification.success({
                message: `QC xong: ${result.issues.length} vấn đề`,
                description: `${result.run?.objects_count} đối tượng, ${result.run?.duration_ms} ms`,
            });
        } catch (err: any) {
            notification.error({ message: 'Không chạy được QC', description: errorText(err) });
        } finally {
            if (activeJobId.current === jobId) setRunning(false);
        }
    }, [jobId, loadGuideline]);

    const onDecide = useCallback(async (issue: QcIssue, status: IssueStatus, decisionReason = '') => {
        setPending((ids) => [...ids, issue.id]);
        try {
            const updated = await decideIssue(issue.id, status, decisionReason);
            setReport((prev) => prev && ({
                ...prev, issues: prev.issues.map((item) => (item.id === updated.id ? updated : item)),
            }));
            return true;
        } catch (err: any) {
            notification.error({ message: 'Không lưu được quyết định', description: errorText(err) });
            return false;
        } finally {
            setPending((ids) => ids.filter((id) => id !== issue.id));
        }
    }, []);

    const onFix = useCallback(async (issue: QcIssue, revert: boolean) => {
        setPending((ids) => [...ids, issue.id]);
        try {
            await (revert ? revertFix(issue.id) : applyFix(issue.id));
            // A fix also updates the other findings attached to the same object.
            const nextReport = await getReport(+jobId);
            if (activeJobId.current !== jobId) return;
            setReport(nextReport);
            notification.success({
                message: revert ? 'Đã hoàn tác trên CVAT' : 'Đã ghi bản sửa vào CVAT',
                description: issue.proposal?.summary,
            });
        } catch (err: any) {
            notification.error({
                message: revert ? 'Không hoàn tác được' : 'Không áp dụng được',
                description: errorText(err),
            });
        } finally {
            setPending((ids) => ids.filter((id) => id !== issue.id));
        }
    }, [jobId]);

    const issues = useMemo(() => (report?.issues ?? []).filter(
        (issue) => (
            (filter === 'all' || (filter === 'fixed' ? issue.fix_status === 'applied' : issue.status === filter)) &&
            (!severityFilter || issue.severity === severityFilter) &&
            (!search.trim() || `${issue.label} ${issue.message} ${issue.rule} ${issue.frame} ${issue.uid}`
                .toLocaleLowerCase().includes(search.trim().toLocaleLowerCase()))
        ),
    ), [report, filter, severityFilter, search]);

    if (error) {
        return (
            <Card className='qc3d-job-state'>
                <Alert type='error' showIcon message='Không tải được báo cáo QC' description={error} />
                <Button icon={<ReloadOutlined />} loading={loading} onClick={load}>Thử lại</Button>
            </Card>
        );
    }
    if (!report || !settings) return <div className='qc3d-job-state'><Spin size='large' tip='Đang tải báo cáo kiểm tra…' /></div>;

    const counts = (status: IssueStatus): number => report.issues.filter((i) => i.status === status).length;
    const fixedCount = report.issues.filter((issue) => issue.fix_status === 'applied').length;
    const columns = [
        {
            title: 'Mức độ',
            dataIndex: 'severity',
            width: 130,
            render: (severity: Severity) => <Tag color={SEVERITY_COLOR[severity]}>{SEVERITY_TEXT[severity]}</Tag>,
            sorter: (a: QcIssue, b: QcIssue) => SEVERITY_RANK[a.severity] - SEVERITY_RANK[b.severity],
        },
        { title: 'Rule', dataIndex: 'rule', width: 70 },
        {
            title: 'Frame',
            dataIndex: 'frame',
            width: 80,
            render: (frame: number, issue: QcIssue) => (
                <Link to={cvatObjectLink(report, issue)} aria-label={`Phóng tới đối tượng lỗi ở frame ${frame}`}>
                    {frame} <ExportOutlined />
                </Link>
            ),
        },
        {
            title: 'Nhãn',
            dataIndex: 'label',
            width: 110,
            render: (label: string, issue: QcIssue) => <Link to={cvatObjectLink(report, issue)}>{label}</Link>,
        },
        { title: 'Vấn đề', dataIndex: 'message', width: 280 },
        {
            title: 'Đề xuất sửa',
            width: 260,
            render: (_: unknown, issue: QcIssue) => {
                if (!issue.proposal) return <span style={{ color: '#999' }}>—</span>;
                return (
                    <Space direction='vertical' size={2}>
                        <span>{issue.proposal.summary}</span>
                        {issue.fix_status === 'applied' ? (
                            <Space size='small'>
                                <Tag color='green'>{`Đã sửa · ${issue.fixed_by ?? ''}`}</Tag>
                                {settings.can_fix && (
                                    <Button size='small' type='link' loading={pending.includes(issue.id)} disabled={running} onClick={() => onFix(issue, true)}>Hoàn tác</Button>
                                )}
                            </Space>
                        ) : (
                            settings.can_fix && issue.status !== 'waived' && (
                                <Popconfirm
                                    title='Xóa đối tượng trùng lặp?'
                                    description='Bạn có thể hoàn tác bản sửa này sau khi áp dụng.'
                                    okText='Áp dụng sửa'
                                    cancelText='Hủy'
                                    disabled={issue.proposal.kind !== 'delete'}
                                    onConfirm={() => onFix(issue, false)}
                                >
                                    <Button
                                        size='small'
                                        type='primary'
                                        ghost
                                        icon={<ToolOutlined />}
                                        loading={pending.includes(issue.id)}
                                        disabled={running}
                                        onClick={() => { if (issue.proposal?.kind !== 'delete') onFix(issue, false); }}
                                    >
                                        {issue.fix_status === 'reverted' ? 'Áp dụng lại' : 'Áp dụng sửa'}
                                    </Button>
                                </Popconfirm>
                            )
                        )}
                    </Space>
                );
            },
        },
        {
            title: 'Trạng thái',
            dataIndex: 'status',
            width: 110,
            render: (status: IssueStatus, issue: QcIssue) => (
                <Tooltip title={issue.decision_reason || undefined}>
                    <Tag color={STATUS_COLOR[status]}>{STATUS_TEXT[status]}</Tag>
                </Tooltip>
            ),
        },
        {
            title: 'Thao tác',
            width: 280,
            render: (_: unknown, issue: QcIssue) => (
                <Space size='small' wrap>
                    <Link to={cvatObjectLink(report, issue)}>Mở trong CVAT</Link>
                    {settings.can_decide && issue.status === 'open' ? (
                        <>
                            <Button size='small' loading={pending.includes(issue.id)} disabled={running} onClick={() => onDecide(issue, 'confirmed')}>Lỗi thật</Button>
                            <Button size='small' disabled={running || pending.includes(issue.id)} onClick={() => { setWaiving(issue); setReason(''); }}>Giữ nguyên</Button>
                        </>
                    ) : (
                        settings.can_decide && issue.fix_status !== 'applied' && (
                            <Button size='small' type='link' loading={pending.includes(issue.id)} disabled={running} onClick={() => onDecide(issue, 'open')}>Mở lại</Button>
                        )
                    )}
                </Space>
            ),
        },
    ];

    return (
        <div className='qc3d-job-page'>
            <Link to='/qc3d/review' className='qc3d-job-back'><ArrowLeftOutlined /> Danh sách duyệt</Link>
            <div className='qc3d-job-heading'>
                <div>
                    <span className='qc3d-job-eyebrow'>BÁO CÁO KIỂM TRA CHẤT LƯỢNG</span>
                    <h2>{`Job #${report.job.id}`}</h2>
                    <p>{report.job.task_name}</p>
                </div>
                <Space wrap>
                    <Link to={`/tasks/${report.job.task_id}/jobs/${report.job.id}`}>
                        <Button icon={<ExportOutlined />}>Mở trình gán nhãn</Button>
                    </Link>
                    {settings.can_run && (
                        <Button type='primary' icon={<ReloadOutlined />} loading={running} disabled={pending.length > 0} onClick={onRun}>
                            {report.run ? 'Chạy lại QC' : 'Chạy QC'}
                        </Button>
                    )}
                </Space>
            </div>
            {workflow && (
                <Card className='qc3d-workflow-card'>
                    <div className='qc3d-workflow-top'>
                        <div className='qc3d-workflow-who'>
                            <StatusBadge status={workflow.status} />
                            <span>{`Người phụ trách: ${workflow.job.assignee?.username ?? 'chưa phân công'}`}</span>
                            <span>{`${workflow.job.frames} frame`}</span>
                        </div>
                        <WorkflowActions
                            jobId={workflow.job.id}
                            actions={workflow.actions}
                            onDone={(next) => { setWorkflow(next); load(); }}
                        />
                    </div>
                    <WorkflowSteps status={workflow.status} />
                    {['waiting_review', 'in_review'].includes(workflow.status) && workflow.qc.stale_reason && (
                        <Alert type='warning' showIcon message={`QC chưa sẵn sàng để duyệt: ${workflow.qc.stale_reason}. Bấm Chạy lại QC.`} />
                    )}
                    {['waiting_acceptance', 'in_acceptance'].includes(workflow.status) && (
                        <Alert
                            type='info'
                            showIcon
                            message={workflow.acceptance && !workflow.acceptance.closed ?
                                `Nghiệm thu: đã chấm ${workflow.acceptance.checked_frames}/${workflow.acceptance.frames.length} frame mẫu` :
                                'Job chờ QA lấy mẫu nghiệm thu'}
                            action={<Link to={`/qc3d/qa?job=${workflow.job.id}`}>Mở trang nghiệm thu</Link>}
                        />
                    )}
                </Card>
            )}
            <div className='qc3d-job-metrics'>
                <div><span>Đối tượng đã kiểm tra</span><strong>{report.run?.objects_count ?? '—'}</strong><small>{`Frame ${report.job.start_frame}–${report.job.stop_frame}`}</small></div>
                <div className='qc3d-job-metric-open'><span>Vấn đề cần duyệt</span><strong>{report.run ? counts('open') : '—'}</strong><small>Ưu tiên kiểm tra và xác nhận</small></div>
                <div><span>Vấn đề đã sửa</span><strong>{report.run ? fixedCount : '—'}</strong><small>Có thể hoàn tác từng bản sửa</small></div>
                <div><span>Đã quyết định</span><strong>{report.run ? counts('confirmed') + counts('waived') : '—'}</strong><small>Lỗi thật hoặc giữ nguyên nhãn</small></div>
            </div>
            <Card title='Thông tin lần kiểm tra' className='qc3d-job-run'>
                {report.run ? (
                    <Descriptions size='small' column={4}>
                        <Descriptions.Item label='Lần chạy'>{new Date(report.run.created_date).toLocaleString()}</Descriptions.Item>
                        <Descriptions.Item label='Người chạy'>{report.run.created_by ?? '—'}</Descriptions.Item>
                        <Descriptions.Item label='Đối tượng'>{report.run.objects_count}</Descriptions.Item>
                        <Descriptions.Item label='Phiên bản quy tắc'>{report.run.rules_version}</Descriptions.Item>
                        <Descriptions.Item label='Thời gian xử lý'>{`${(report.run.duration_ms / 1000).toFixed(2)} giây`}</Descriptions.Item>
                        {report.run.guideline?.name && (
                            <Descriptions.Item label='Guideline lúc chạy'>
                                {`${report.run.guideline.name} · bản ${report.run.guideline.revision}`}
                            </Descriptions.Item>
                        )}
                    </Descriptions>
                ) : <Alert type='info' showIcon message='Chưa có báo cáo kiểm tra' description='Chạy QC để kiểm tra nhãn 3D và nhận đề xuất sửa cho từng đối tượng.' />}
                {report.run && (
                    <Space wrap style={{ marginTop: 8 }}>
                        {report.run.coverage.map((cov) => (
                            <Tooltip key={cov.rule} title={cov.reason || `${cov.issues} vấn đề`}>
                                {/* Three colors distinguish unchecked, clear and flagged rules. */}
                                {/* eslint-disable-next-line no-nested-ternary */}
                                <Tag color={cov.status === 'checked' ? (cov.issues ? 'orange' : 'green') : 'default'}>
                                    {`${cov.rule}: ${cov.status === 'checked' ? cov.issues : 'chưa kiểm tra'}`}
                                </Tag>
                            </Tooltip>
                        ))}
                    </Space>
                )}
            </Card>
            <Card
                title='Bản đồ tổng quan job'
                className='qc3d-map-card'
                extra={<span className='qc3d-cell-secondary'>Toàn bộ frame trên một bản đồ</span>}
            >
                {overview && <OverviewMap data={overview} jobId={+jobId} />}
                {!overview && !overviewError && <div className='qc3d-map-loading'><Spin tip='Đang dựng bản đồ…' /></div>}
                {overviewError && <Alert type='error' showIcon message='Không dựng được bản đồ' description={overviewError} />}
            </Card>
            {guideline && <GuidelineCard guideline={guideline} />}
            {report.run && (
                <Card
                    title={`Danh sách vấn đề · ${report.issues.length}`}
                    className='qc3d-job-issues'
                >
                    <div className='qc3d-job-toolbar'>
                        <Segmented
                            value={filter}
                            onChange={(value) => setFilter(value as Filter)}
                            options={[
                                { label: `Cần duyệt (${counts('open')})`, value: 'open' },
                                { label: `Lỗi thật (${counts('confirmed')})`, value: 'confirmed' },
                                { label: `Giữ nguyên (${counts('waived')})`, value: 'waived' },
                                { label: `Đã sửa (${fixedCount})`, value: 'fixed' },
                                { label: `Tất cả (${report.issues.length})`, value: 'all' },
                            ]}
                        />
                        <div className='qc3d-job-search'>
                            <Input
                                prefix={<SearchOutlined />}
                                allowClear
                                placeholder='Tìm nhãn, frame, rule hoặc nội dung'
                                aria-label='Tìm trong danh sách vấn đề'
                                value={search}
                                onChange={(event) => setSearch(event.target.value)}
                            />
                            <Select
                                allowClear
                                placeholder='Mức độ'
                                aria-label='Lọc mức độ vấn đề'
                                value={severityFilter}
                                onChange={setSeverityFilter}
                                options={Object.entries(SEVERITY_TEXT).map(([value, label]) => ({ value, label }))}
                            />
                        </div>
                    </div>
                    <div className='qc3d-job-table-hint'>{`${issues.length} vấn đề phù hợp · Mở trong CVAT để xem đúng frame và đối tượng.`}</div>
                    <Table<QcIssue>
                        rowKey='id'
                        size='small'
                        columns={columns}
                        dataSource={issues}
                        scroll={{ x: 1220 }}
                        locale={{ emptyText: report.issues.length ? 'Không có vấn đề phù hợp bộ lọc.' : 'Không phát hiện vấn đề trong lần kiểm tra này.' }}
                        pagination={{
                            defaultPageSize: 20,
                            showSizeChanger: true,
                            pageSizeOptions: [10, 20, 50],
                            showTotal: (total) => `${total} vấn đề`,
                        }}
                    />
                </Card>
            )}
            {workflow && (
                <Card title='Lịch sử quy trình' className='qc3d-history-card'>
                    <WorkflowHistory events={workflow.history} />
                </Card>
            )}
            <Modal
                open={!!waiving}
                title='Giữ nguyên nhãn'
                okText='Lưu'
                cancelText='Hủy'
                confirmLoading={!!waiving && pending.includes(waiving.id)}
                okButtonProps={{ disabled: !reason.trim() }}
                onCancel={() => setWaiving(null)}
                onOk={async () => {
                    if (waiving && await onDecide(waiving, 'waived', reason.trim())) setWaiving(null);
                }}
            >
                <p>{waiving?.message}</p>
                <Input.TextArea
                    autoFocus
                    rows={3}
                    placeholder='Lý do (bắt buộc), ví dụ: xe bị che khuất, nhãn từ camera'
                    aria-label='Lý do giữ nguyên nhãn'
                    value={reason}
                    onChange={(event) => setReason(event.target.value)}
                />
            </Modal>
        </div>
    );
}
