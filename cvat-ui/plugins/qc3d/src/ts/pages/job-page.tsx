// Copyright (C) CVAT.ai Corporation
//
// SPDX-License-Identifier: MIT

import React, { useCallback, useEffect, useMemo, useState } from 'react';
import { Link, useParams } from 'react-router-dom';
import Alert from 'antd/lib/alert';
import Button from 'antd/lib/button';
import Card from 'antd/lib/card';
import Descriptions from 'antd/lib/descriptions';
import Input from 'antd/lib/input';
import Modal from 'antd/lib/modal';
import Segmented from 'antd/lib/segmented';
import Space from 'antd/lib/space';
import Spin from 'antd/lib/spin';
import Table from 'antd/lib/table';
import Tag from 'antd/lib/tag';
import Tooltip from 'antd/lib/tooltip';
import notification from 'antd/lib/notification';

import {
    applyFix, cvatObjectLink, decideIssue, getReport, IssueStatus, QcIssue, QcReport, revertFix, runQc, Severity,
} from '../api';

const SEVERITY_COLOR: Record<Severity, string> = { blocker: 'red', major: 'orange', minor: 'blue' };
const STATUS_TEXT: Record<IssueStatus, string> = { open: 'Mở', confirmed: 'Lỗi thật', waived: 'Giữ nguyên' };
const STATUS_COLOR: Record<IssueStatus, string> = { open: 'default', confirmed: 'red', waived: 'green' };

type Filter = 'open' | 'all' | 'confirmed' | 'waived';

export default function JobPage(): JSX.Element {
    const { jobId } = useParams<{ jobId: string }>();
    const [report, setReport] = useState<QcReport | null>(null);
    const [error, setError] = useState<string | null>(null);
    const [running, setRunning] = useState(false);
    const [filter, setFilter] = useState<Filter>('open');
    const [waiving, setWaiving] = useState<QcIssue | null>(null);
    const [reason, setReason] = useState('');

    useEffect(() => {
        getReport(+jobId).then(setReport).catch((err: Error) => setError(err.message));
    }, [jobId]);

    const onRun = useCallback(async () => {
        setRunning(true);
        try {
            const result = await runQc(+jobId);
            setReport(result);
            notification.success({
                message: `QC xong: ${result.issues.length} vấn đề`,
                description: `${result.run?.objects_count} đối tượng, ${result.run?.duration_ms} ms`,
            });
        } catch (err: any) {
            notification.error({ message: 'Không chạy được QC', description: err.message });
        } finally {
            setRunning(false);
        }
    }, [jobId]);

    const onDecide = useCallback(async (issue: QcIssue, status: IssueStatus, decisionReason = '') => {
        try {
            const updated = await decideIssue(issue.id, status, decisionReason);
            setReport((prev) => prev && ({
                ...prev, issues: prev.issues.map((item) => (item.id === updated.id ? updated : item)),
            }));
        } catch (err: any) {
            notification.error({ message: 'Không lưu được quyết định', description: err.message });
        }
    }, []);

    const onFix = useCallback(async (issue: QcIssue, revert: boolean) => {
        try {
            const updated = await (revert ? revertFix(issue.id) : applyFix(issue.id));
            setReport((prev) => prev && ({
                ...prev, issues: prev.issues.map((item) => (item.id === updated.id ? updated : item)),
            }));
            notification.success({
                message: revert ? 'Đã hoàn tác trên CVAT' : 'Đã ghi bản sửa vào CVAT',
                description: issue.proposal?.summary,
            });
        } catch (err: any) {
            notification.error({
                message: revert ? 'Không hoàn tác được' : 'Không áp dụng được',
                description: err?.response?.data?.detail ?? err.message,
            });
        }
    }, []);

    const issues = useMemo(() => (report?.issues ?? []).filter(
        (issue) => filter === 'all' || issue.status === filter,
    ), [report, filter]);

    if (error) return <Alert type='error' message='Không tải được báo cáo QC' description={error} />;
    if (!report) return <Spin size='large' />;

    const counts = (status: IssueStatus): number => report.issues.filter((i) => i.status === status).length;
    const columns = [
        {
            title: 'Mức',
            dataIndex: 'severity',
            width: 90,
            render: (severity: Severity) => <Tag color={SEVERITY_COLOR[severity]}>{severity}</Tag>,
            sorter: (a: QcIssue, b: QcIssue) => a.severity.localeCompare(b.severity),
        },
        { title: 'Rule', dataIndex: 'rule', width: 70 },
        { title: 'Frame', dataIndex: 'frame', width: 70 },
        { title: 'Class', dataIndex: 'label', width: 100 },
        { title: 'Vấn đề', dataIndex: 'message' },
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
                                <Button size='small' type='link' onClick={() => onFix(issue, true)}>Hoàn tác</Button>
                            </Space>
                        ) : (
                            <Button size='small' type='primary' ghost onClick={() => onFix(issue, false)}>
                                {issue.fix_status === 'reverted' ? 'Áp dụng lại' : 'Áp dụng vào CVAT'}
                            </Button>
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
                <Space size='small'>
                    <Link to={cvatObjectLink(report, issue)}>Mở trong CVAT</Link>
                    {issue.status === 'open' ? (
                        <>
                            <Button size='small' danger onClick={() => onDecide(issue, 'confirmed')}>Lỗi thật</Button>
                            <Button size='small' onClick={() => { setWaiving(issue); setReason(''); }}>Giữ nguyên</Button>
                        </>
                    ) : (
                        <Button size='small' type='link' onClick={() => onDecide(issue, 'open')}>Mở lại</Button>
                    )}
                </Space>
            ),
        },
    ];

    return (
        <Space direction='vertical' size='middle' style={{ width: '100%' }}>
            <Card
                title={`QC 3D · Job #${report.job.id} · ${report.job.task_name}`}
                extra={(
                    <Space>
                        <Link to={`/tasks/${report.job.task_id}/jobs/${report.job.id}`}>Mở job trong CVAT</Link>
                        <Button type='primary' loading={running} onClick={onRun}>Chạy QC</Button>
                    </Space>
                )}
            >
                {report.run ? (
                    <Descriptions size='small' column={4}>
                        <Descriptions.Item label='Lần chạy'>{new Date(report.run.created_date).toLocaleString()}</Descriptions.Item>
                        <Descriptions.Item label='Người chạy'>{report.run.created_by ?? '—'}</Descriptions.Item>
                        <Descriptions.Item label='Đối tượng'>{report.run.objects_count}</Descriptions.Item>
                        <Descriptions.Item label='Rule version'>{report.run.rules_version}</Descriptions.Item>
                    </Descriptions>
                ) : <Alert type='info' message='Job chưa chạy QC. Bấm "Chạy QC" để kiểm tra.' />}
                {report.run && (
                    <Space wrap style={{ marginTop: 8 }}>
                        {report.run.coverage.map((cov) => (
                            <Tooltip key={cov.rule} title={cov.reason || `${cov.issues} vấn đề`}>
                                <Tag color={cov.status === 'checked' ? (cov.issues ? 'orange' : 'green') : 'default'}>
                                    {`${cov.rule}: ${cov.status === 'checked' ? cov.issues : 'chưa kiểm tra'}`}
                                </Tag>
                            </Tooltip>
                        ))}
                    </Space>
                )}
            </Card>
            {report.run && (
                <Card
                    title='Danh sách vấn đề'
                    extra={(
                        <Segmented
                            value={filter}
                            onChange={(value) => setFilter(value as Filter)}
                            options={[
                                { label: `Mở (${counts('open')})`, value: 'open' },
                                { label: `Lỗi thật (${counts('confirmed')})`, value: 'confirmed' },
                                { label: `Giữ nguyên (${counts('waived')})`, value: 'waived' },
                                { label: `Tất cả (${report.issues.length})`, value: 'all' },
                            ]}
                        />
                    )}
                >
                    <Table<QcIssue>
                        rowKey='id'
                        size='small'
                        columns={columns}
                        dataSource={issues}
                        pagination={{ pageSize: 20 }}
                    />
                </Card>
            )}
            <Modal
                open={!!waiving}
                title='Giữ nguyên nhãn'
                okText='Lưu'
                okButtonProps={{ disabled: !reason.trim() }}
                onCancel={() => setWaiving(null)}
                onOk={async () => {
                    if (waiving) await onDecide(waiving, 'waived', reason.trim());
                    setWaiving(null);
                }}
            >
                <p>{waiving?.message}</p>
                <Input.TextArea
                    autoFocus
                    rows={3}
                    placeholder='Lý do (bắt buộc), ví dụ: xe bị che khuất, nhãn từ camera'
                    value={reason}
                    onChange={(event) => setReason(event.target.value)}
                />
            </Modal>
        </Space>
    );
}
