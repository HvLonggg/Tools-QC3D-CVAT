// Copyright (C) CVAT.ai Corporation
//
// SPDX-License-Identifier: MIT

// Shared pieces of the review / acceptance workflow: status badge, steps, action buttons, history.

import React, { useState } from 'react';
import {
    AuditOutlined, CheckCircleOutlined, CheckOutlined, ClockCircleOutlined, EditOutlined, FlagOutlined,
    LockOutlined, PlayCircleOutlined, RollbackOutlined, SafetyCertificateOutlined, SendOutlined, StopOutlined,
    UnlockOutlined,
} from '@ant-design/icons';
import Button from 'antd/lib/button';
import Checkbox from 'antd/lib/checkbox';
import Input from 'antd/lib/input';
import Modal from 'antd/lib/modal';
import Space from 'antd/lib/space';
import Steps from 'antd/lib/steps';
import Timeline from 'antd/lib/timeline';
import Tooltip from 'antd/lib/tooltip';
import notification from 'antd/lib/notification';

import {
    actWorkflow, errorText, WorkflowAction, WorkflowActionName, WorkflowEvent, WorkflowState, WorkflowStatus,
} from './api';

export const STATUS_META: Record<WorkflowStatus, { text: string; color: string; soft: string; icon: JSX.Element }> = {
    in_progress: { text: 'Đang gán nhãn', color: '#315be8', soft: '#edf2ff', icon: <EditOutlined /> },
    returned: { text: 'Bị trả lại', color: '#d9384a', soft: '#fdecee', icon: <RollbackOutlined /> },
    waiting_review: { text: 'Chờ QC', color: '#a86400', soft: '#fff4e0', icon: <ClockCircleOutlined /> },
    in_review: { text: 'Đang QC', color: '#7c4dff', soft: '#f1ecff', icon: <AuditOutlined /> },
    waiting_acceptance: { text: 'Chờ nghiệm thu', color: '#0e7490', soft: '#e3f6f9', icon: <ClockCircleOutlined /> },
    in_acceptance: { text: 'Đang nghiệm thu', color: '#0f766e', soft: '#e1f5f1', icon: <SafetyCertificateOutlined /> },
    accepted: { text: 'Đạt nghiệm thu', color: '#16803c', soft: '#e6f6ea', icon: <CheckCircleOutlined /> },
    signed_off: { text: 'Đã chốt', color: '#1f2937', soft: '#eceef2', icon: <LockOutlined /> },
};
export const STATUS_ORDER = Object.keys(STATUS_META) as WorkflowStatus[];

const ACTION_ICON: Record<WorkflowActionName, JSX.Element> = {
    submit: <SendOutlined />,
    start_review: <PlayCircleOutlined />,
    review_pass: <CheckOutlined />,
    review_return: <RollbackOutlined />,
    start_acceptance: <PlayCircleOutlined />,
    accept: <SafetyCertificateOutlined />,
    reject: <StopOutlined />,
    sign_off: <FlagOutlined />,
    reopen: <UnlockOutlined />,
};
const NEGATIVE: WorkflowActionName[] = ['review_return', 'reject'];
const SECONDARY: WorkflowActionName[] = ['reopen'];

export function StatusBadge({ status, text }: { status: WorkflowStatus; text?: string }): JSX.Element {
    const meta = STATUS_META[status];
    return (
        <span className='qc3d-status-badge' style={{ color: meta.color, background: meta.soft }}>
            {meta.icon}
            {text ?? meta.text}
        </span>
    );
}

/** Four steps of the job's life; the returned state shows on the labeling step. */
export function WorkflowSteps({ status }: { status: WorkflowStatus }): JSX.Element {
    const index = {
        in_progress: 0, returned: 0, waiting_review: 1, in_review: 1, waiting_acceptance: 2, in_acceptance: 2,
        accepted: 3, signed_off: 3,
    }[status];
    const finished = status === 'signed_off';
    const sub: Record<number, string> = {
        0: status === 'returned' ? 'Bị trả lại · cần sửa' : 'Người gán nhãn',
        1: status === 'waiting_review' ? 'Chờ QC nhận' : 'Reviewer kiểm tra',
        2: status === 'waiting_acceptance' ? 'Chờ QA lấy mẫu' : 'QA chấm mẫu',
        3: finished ? 'Đã khoá' : 'Lead chốt',
    };
    return (
        <Steps
            className='qc3d-workflow-steps'
            size='small'
            current={index}
            status={status === 'returned' ? 'error' : (finished && 'finish') || 'process'}
            items={['Gán nhãn', 'QC duyệt', 'Nghiệm thu', 'Chốt'].map((title, i) => ({ title, description: sub[i] }))}
        />
    );
}

interface ActionsProps {
    jobId: number;
    actions: WorkflowAction[];
    onDone: (state: WorkflowState) => void;
    size?: 'small' | 'middle';
    hide?: WorkflowActionName[];
}

/** The workflow actions the server allows now; a reason is asked when needed, then the step is recorded. */
export function WorkflowActions({
    jobId, actions, onDone, size = 'middle', hide = [],
}: ActionsProps): JSX.Element | null {
    const [open, setOpen] = useState<WorkflowAction | null>(null);
    const [note, setNote] = useState('');
    const [override, setOverride] = useState(false);
    const [busy, setBusy] = useState(false);
    const visible = actions.filter((item) => !hide.includes(item.action));
    if (!visible.length) return null;

    const submit = async (): Promise<void> => {
        if (!open) return;
        setBusy(true);
        try {
            const next = await actWorkflow(jobId, open.action, note.trim(), override);
            notification.success({
                message: `${open.label}: xong`,
                description: `Job #${jobId} → ${STATUS_META[next.status].text}`,
                placement: 'bottomRight',
            });
            setOpen(null);
            onDone(next);
        } catch (err: unknown) {
            notification.error({ message: `Không thực hiện được: ${open.label}`, description: errorText(err) });
        } finally {
            setBusy(false);
        }
    };
    const needsNote = !!open && open.note_required;

    return (
        <>
            <Space wrap size={8} className='qc3d-workflow-actions'>
                {visible.map((item) => {
                    const negative = NEGATIVE.includes(item.action);
                    const button = (
                        <Button
                            key={item.action}
                            size={size}
                            icon={ACTION_ICON[item.action]}
                            type={negative || SECONDARY.includes(item.action) ? 'default' : 'primary'}
                            danger={negative}
                            disabled={!item.enabled}
                            onClick={() => { setOpen(item); setNote(''); setOverride(false); }}
                        >
                            {item.label}
                        </Button>
                    );
                    return item.enabled ? button : (
                        <Tooltip key={item.action} title={item.reason}><span>{button}</span></Tooltip>
                    );
                })}
            </Space>
            <Modal
                open={!!open}
                title={open ? `${open.label} · Job #${jobId}` : ''}
                okText={open?.label}
                cancelText='Hủy'
                okButtonProps={{
                    danger: !!open && NEGATIVE.includes(open.action),
                    disabled: (needsNote && !note.trim()) || (!!open?.needs_override && !override),
                    loading: busy,
                }}
                onOk={submit}
                onCancel={() => setOpen(null)}
                destroyOnClose
            >
                {open?.needs_override && (
                    <div className='qc3d-override-box'>
                        <strong>Mẫu chưa đạt tiêu chí nghiệm thu.</strong>
                        <p>Chỉ lead được duyệt vượt; lý do được lưu vào lịch sử job.</p>
                        <Checkbox checked={override} onChange={(event) => setOverride(event.target.checked)}>
                            Tôi chịu trách nhiệm duyệt vượt
                        </Checkbox>
                    </div>
                )}
                <Input.TextArea
                    autoFocus
                    rows={3}
                    maxLength={2000}
                    showCount
                    placeholder={needsNote ? 'Lý do (bắt buộc): lỗi gì, ở frame nào, cần sửa gì' : 'Ghi chú (không bắt buộc)'}
                    aria-label='Ghi chú cho bước quy trình'
                    value={note}
                    onChange={(event) => setNote(event.target.value)}
                />
            </Modal>
        </>
    );
}

function when(value: string): string {
    const date = new Date(value);
    return Number.isNaN(date.getTime()) ? '' : date.toLocaleString('vi-VN', {
        day: '2-digit', month: '2-digit', hour: '2-digit', minute: '2-digit',
    });
}

export function WorkflowHistory({ events }: { events: WorkflowEvent[] }): JSX.Element {
    if (!events.length) return <div className='qc3d-cell-secondary'>Chưa có bước nào được ghi nhận.</div>;
    return (
        <Timeline
            className='qc3d-history'
            items={events.map((event) => {
                const meta = STATUS_META[event.to_status] ?? STATUS_META.in_progress;
                const acceptance = event.metrics.acceptance;
                const open = event.metrics.open;
                return {
                    color: meta.color,
                    dot: <span className='qc3d-history-dot' style={{ color: meta.color }}>{ACTION_ICON[event.action]}</span>,
                    children: (
                        <div className='qc3d-history-item'>
                            <div className='qc3d-history-head'>
                                <strong>{event.label}</strong>
                                <StatusBadge status={event.to_status} />
                                {event.metrics.override && <span className='qc3d-history-flag'>duyệt vượt</span>}
                            </div>
                            <div className='qc3d-cell-secondary'>
                                {`${event.user?.username ?? 'hệ thống'} · ${when(event.date)}`}
                                {open && ` · QC còn mở ${open.blocker}/${open.major}/${open.minor}`}
                                {acceptance?.error_rate !== undefined && acceptance?.error_rate !== null &&
                                    ` · tỷ lệ lỗi mẫu ${(acceptance.error_rate * 100).toFixed(1)}%`}
                            </div>
                            {event.note && <div className='qc3d-history-note'>{event.note}</div>}
                        </div>
                    ),
                };
            })}
        />
    );
}
