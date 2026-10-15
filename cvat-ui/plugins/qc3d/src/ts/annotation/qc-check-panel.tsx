// Copyright (C) CVAT.ai Corporation
//
// SPDX-License-Identifier: MIT

// Switch renders a labelable button; preserve the surrounding clickable label.
/* eslint jsx-a11y/label-has-associated-control: ["error", { "controlComponents": ["Switch"], "depth": 3 }] */

import React, {
    useCallback, useEffect, useMemo, useRef, useState,
} from 'react';
import { useSelector, useStore } from 'react-redux';
import {
    CheckOutlined, CloseOutlined, ReloadOutlined, RollbackOutlined, ThunderboltOutlined, ToolOutlined,
    EyeOutlined, AimOutlined, LeftOutlined, RightOutlined, SearchOutlined, HighlightOutlined,
} from '@ant-design/icons';
import Alert from 'antd/lib/alert';
import Badge from 'antd/lib/badge';
import Button from 'antd/lib/button';
import Empty from 'antd/lib/empty';
import Input from 'antd/lib/input';
import Popconfirm from 'antd/lib/popconfirm';
import Popover from 'antd/lib/popover';
import Segmented from 'antd/lib/segmented';
import Select from 'antd/lib/select';
import Slider from 'antd/lib/slider';
import Spin from 'antd/lib/spin';
import Switch from 'antd/lib/switch';
import Tooltip from 'antd/lib/tooltip';
import notification from 'antd/lib/notification';
import { CombinedState } from 'reducers';

import RunBar from './run-bar';
import {
    applyFix, autofix, AutofixResult, CheckStatus, decideIssue, errorText, getJobSettings, getReport, IssueStatus,
    JobQcSettings, MarkedFixResult, QcIssue, QcReport, revertFix, setAutofixCreate, setExcludedLabels, Severity,
    startCheck,
} from '../api';
import {
    applyQcOverlay, applyViewer, applyVisibility, focusIssue, loadViewer, OPEN_COUNT_EVENT,
    reloadAnnotations, saveIfNeeded, uidOf,
    ViewerSettings, DEFAULT_VIEWER,
} from './cvat-bridge';

type View = 'review' | 'auto' | 'view';
type StatusFilter = 'todo' | 'done' | 'all';

const RULE_NAMES: Record<string, string> = {
    R01: 'Nhãn không hợp lệ',
    R02: 'Trùng / chồng lấn',
    R03: 'Class ngoài schema',
    R04: 'Góc quay',
    R05: 'Ít điểm / lệch',
    R06: 'Kích thước',
    R07: 'Track nhảy',
    R08: 'Vận tốc',
    R09: 'Lơ lửng / chìm',
    R10: 'Không ôm sát',
    R11: 'Sai class',
    R12: 'Khác model',
    R13: 'Ngắn hơn vật',
    R14: 'Người duyệt chọn',
};

/** A box the reviewer picked by eye for a model fix. */
interface MarkedBox {
    uid: string;
    frame: number;
    label: string;
}

const markedKey = (jobId: number): string => `qc3d.marked.${jobId}`;

function loadMarked(jobId: number | undefined): MarkedBox[] {
    if (!jobId) return [];
    try {
        const saved = JSON.parse(window.localStorage.getItem(markedKey(jobId)) ?? '[]');
        return Array.isArray(saved) ? saved.filter((item) => (
            typeof item?.uid === 'string' && Number.isInteger(item?.frame) && typeof item?.label === 'string'
        )).slice(0, 500) : [];
    } catch {
        return [];
    }
}

/** All issues of one object on one frame: the reviewer decides per object, not per rule. */
function PlanRow({
    title, text, warning, onFocus,
}: {
    title: string;
    text: string;
    warning?: boolean;
    onFocus?: () => void;
}): JSX.Element {
    return (
        <div className={`qc3d-plan-item${warning ? ' qc3d-plan-item-warning' : ''}`}>
            <div>
                <b>{title}</b>
                <span>{text}</span>
            </div>
            {onFocus && (
                <Button size='small' type='link' icon={<AimOutlined />} aria-label={`Xem ${title}`} onClick={onFocus} />
            )}
        </div>
    );
}

const COLOR_MODES: { value: ViewerSettings['colorMode']; label: string; hint: string }[] = [
    { value: 'default', label: 'Gốc', hint: 'Màu gốc của file point cloud.' },
    { value: 'objects', label: 'Vật thể', hint: 'Mặt đường xám dịu, vật đứng trên đường tô theo độ cao so với mặt đường: thấy rõ xe, người, cột.' },
    { value: 'height', label: 'Độ cao', hint: 'Tô theo độ cao tuyệt đối (thấp xanh, cao cam).' },
    { value: 'range', label: 'Khoảng cách', hint: 'Gần ấm, xa lạnh: dễ cảm nhận chiều sâu cảnh.' },
    { value: 'intensity', label: 'Cường độ', hint: 'Theo độ phản xạ: vạch kẻ đường, biển số, đèn nổi bật.' },
];

interface ObjectGroup {
    key: string;
    uid: string;
    frame: number;
    label: string;
    severity: QcIssue['severity'];
    issues: QcIssue[];
    proposal: QcIssue | null; // the issue carrying the (shared) proposal
    status: 'todo' | 'confirmed' | 'waived' | 'fixed';
}

const SEVERITY_RANK = { blocker: 0, major: 1, minor: 2 };
const SEVERITY_TEXT = { blocker: 'Nghiêm trọng', major: 'Cần xử lý', minor: 'Lưu ý' };

function groupIssues(issues: QcIssue[]): ObjectGroup[] {
    const groups = new Map<string, ObjectGroup>();
    for (const issue of issues) {
        const key = `${issue.uid}@${issue.frame}`;
        const group = groups.get(key) ?? {
            key,
            uid: issue.uid,
            frame: issue.frame,
            label: issue.label,
            severity: issue.severity,
            issues: [],
            proposal: null,
            status: 'todo',
        };
        group.issues.push(issue);
        if (SEVERITY_RANK[issue.severity] < SEVERITY_RANK[group.severity]) group.severity = issue.severity;
        if (issue.proposal && (!group.proposal || issue.fix_status === 'applied')) group.proposal = issue;
        groups.set(key, group);
    }
    for (const group of groups.values()) {
        if (group.proposal?.fix_status === 'applied') group.status = 'fixed';
        else if (group.issues.every((i) => i.status === 'waived')) group.status = 'waived';
        else if (group.issues.every((i) => i.status !== 'open')) group.status = 'confirmed';
    }
    return [...groups.values()].sort((a, b) => (
        a.frame - b.frame || SEVERITY_RANK[a.severity] - SEVERITY_RANK[b.severity]
    ));
}

export function QcTabLabel(): JSX.Element {
    const [count, setCount] = useState(0);
    useEffect(() => {
        const listener = (event: Event): void => setCount((event as CustomEvent<number>).detail);
        window.addEventListener(OPEN_COUNT_EVENT, listener);
        return () => window.removeEventListener(OPEN_COUNT_EVENT, listener);
    }, []);
    return (
        <span className='qc3d-tab-label'>
            QC Check 3D
            {count > 0 && <Badge count={count} size='small' overflowCount={99} className='qc3d-tab-badge' />}
        </span>
    );
}

function ObjectCard({
    group, active, canDecide, canFix, labelColor, onFocus, onDecide, onFix,
}: {
    group: ObjectGroup;
    active: boolean;
    canDecide: boolean;
    canFix: boolean;
    labelColor: string;
    onFocus(): void;
    onDecide(status: IssueStatus, reason?: string): Promise<boolean>;
    onFix(revert: boolean): Promise<void>;
}): JSX.Element {
    const [reason, setReason] = useState('');
    const [waiveOpen, setWaiveOpen] = useState(false);
    const [busy, setBusy] = useState(false);
    const run = async (action: () => Promise<unknown>): Promise<void> => {
        setBusy(true);
        try { await action(); } finally { setBusy(false); }
    };
    const suggested = group.issues.map((i) => i.evidence?.suggested).find(Boolean) as string | undefined;
    const statusText = {
        todo: 'Cần kiểm', confirmed: 'Lỗi thật', waived: 'Giữ nguyên', fixed: 'Đã sửa',
    }[group.status];

    return (
        <div
            className={`qc3d-card qc3d-sev-${group.severity}${active ? ' qc3d-card-active' : ''} qc3d-status-${group.status}`}
        >
            <button
                type='button'
                className='qc3d-card-focus'
                onClick={onFocus}
                aria-label={`Chọn ${group.label || 'đối tượng'} ${group.uid}, frame ${group.frame}`}
                aria-pressed={active}
            >
                <span className='qc3d-card-head'>
                    <span className='qc3d-label-dot' style={{ background: labelColor }} />
                    <span className='qc3d-card-title'>{group.label || 'Không có nhãn'}</span>
                    <span className={`qc3d-chip qc3d-chip-${group.status}`}>{statusText}</span>
                </span>
                <span className='qc3d-card-meta'>
                    {`Frame ${group.frame} · ${group.uid.startsWith('t:') ? 'Track' : 'Shape'} #${group.uid.split(':')[1]}`}
                    <span className={`qc3d-severity qc3d-severity-${group.severity}`}>
                        {SEVERITY_TEXT[group.severity]}
                    </span>
                </span>
            </button>
            <div className='qc3d-rules'>
                {group.issues.map((issue) => (
                    <Tooltip key={issue.id} title={issue.message}>
                        <span className='qc3d-rule'>{`${issue.rule} ${RULE_NAMES[issue.rule] ?? ''}`}</span>
                    </Tooltip>
                ))}
            </div>
            <div className='qc3d-finding'>{group.issues[0].message}</div>
            {suggested && suggested !== group.label && (
                <div className='qc3d-suggest'>
                    {'Đề xuất class: '}
                    <b>{suggested}</b>
                </div>
            )}
            {group.proposal?.proposal && (
                <div className='qc3d-proposal'>
                    <ToolOutlined />
                    <span>{group.proposal.proposal.summary}</span>
                    {group.proposal.proposal.verified === false && (
                        <Tooltip title={(group.proposal.proposal.checks ?? []).join('; ')}>
                            <span className='qc3d-proposal-warn'>cần xem kỹ</span>
                        </Tooltip>
                    )}
                </div>
            )}
            <div className='qc3d-actions'>
                {canDecide && group.status === 'todo' && (
                    <>
                        <Button size='small' icon={<CheckOutlined />} loading={busy} onClick={() => run(() => onDecide('confirmed'))}>
                            Lỗi thật
                        </Button>
                        <Popover
                            trigger='click'
                            title='Giữ nguyên nhãn'
                            open={waiveOpen}
                            onOpenChange={setWaiveOpen}
                            content={(
                                <div className='qc3d-waive'>
                                    <Input.TextArea
                                        rows={2}
                                        autoFocus
                                        placeholder='Lý do (bắt buộc)'
                                        aria-label='Lý do giữ nguyên nhãn'
                                        value={reason}
                                        onChange={(event) => setReason(event.target.value)}
                                    />
                                    <Button
                                        size='small'
                                        type='primary'
                                        disabled={!reason.trim()}
                                        loading={busy}
                                        onClick={() => run(async () => {
                                            if (await onDecide('waived', reason.trim())) {
                                                setWaiveOpen(false);
                                                setReason('');
                                            }
                                        })}
                                    >
                                        Lưu quyết định
                                    </Button>
                                </div>
                            )}
                        >
                            <Button size='small' icon={<CloseOutlined />} disabled={busy}>Giữ nguyên</Button>
                        </Popover>
                    </>
                )}
                {canDecide && (group.status === 'confirmed' || group.status === 'waived') && (
                    <Button size='small' type='text' loading={busy} onClick={() => run(() => onDecide('open'))}>Mở lại</Button>
                )}
                {canFix && group.proposal?.proposal && group.status !== 'waived' && (
                    group.status === 'fixed' ? (
                        <Button size='small' type='text' icon={<RollbackOutlined />} loading={busy} onClick={() => run(() => onFix(true))}>
                            Hoàn tác
                        </Button>
                    ) : (
                        <Button size='small' type='primary' ghost icon={<ToolOutlined />} loading={busy} onClick={() => run(() => onFix(false))}>
                            {group.proposal.proposal?.kind === 'create' ?
                                `Thêm ${group.proposal.proposal.shapes?.length ?? 0} box` : 'Áp dụng'}
                        </Button>
                    )
                )}
            </div>
        </div>
    );
}

export function QcCheckPanel(): JSX.Element {
    const store = useStore() as any;
    const job = useSelector((state: CombinedState) => state.annotation.job.instance);
    const frame = useSelector((state: CombinedState) => state.annotation.player.frame.number);
    const states = useSelector((state: CombinedState) => state.annotation.annotations.states);
    const activatedID = useSelector((state: CombinedState) => state.annotation.annotations.activatedStateID);
    const canvasReady = useSelector((state: CombinedState) => state.annotation.canvas.ready);

    const [view, setView] = useState<View>('review');
    const [report, setReport] = useState<QcReport | null>(null);
    const [settings, setSettings] = useState<JobQcSettings | null>(null);
    const [error, setError] = useState<string | null>(null);
    const [loading, setLoading] = useState(true);
    const [running, setRunning] = useState(false);
    const [statusFilter, setStatusFilter] = useState<StatusFilter>('todo');
    const [classFilter, setClassFilter] = useState<string[]>([]);
    const [severityFilter, setSeverityFilter] = useState<'all' | Severity>('all');
    const [markersEnabled, setMarkersEnabled] = useState(true);
    const [currentFrameOnly, setCurrentFrameOnly] = useState(false);
    const [search, setSearch] = useState('');
    const [plan, setPlan] = useState<AutofixResult | null>(null);
    const [planning, setPlanning] = useState(false);
    const [planError, setPlanError] = useState<string | null>(null);
    const [excluding, setExcluding] = useState(false);
    const [lastResult, setLastResult] = useState<AutofixResult | null>(null);
    const [fixing, setFixing] = useState(false);
    const [viewer, setViewer] = useState<ViewerSettings>(loadViewer);
    const [hiddenLabels, setHiddenLabels] = useState<string[]>([]);
    const [onlyFlagged, setOnlyFlagged] = useState(false);
    // object visibility is only driven by this panel after the reviewer used its filters
    const [visibilityTouched, setVisibilityTouched] = useState(false);
    // boxes picked by eye on the 3D view (Duyệt), fixed by the model from the Autofix tab
    const [picking, setPicking] = useState(false);
    const [marked, setMarked] = useState<MarkedBox[]>([]);
    const [markedResult, setMarkedResult] = useState<MarkedFixResult | null>(null);
    const [fixingMarked, setFixingMarked] = useState(false);
    const [checkStarted, setCheckStarted] = useState(0);
    const lastActivated = useRef<number | null>(null);

    const jobId: number | undefined = job?.id;
    const activeJobId = useRef(jobId);
    activeJobId.current = jobId;
    const labelColors = useMemo(() => Object.fromEntries(
        (job?.labels ?? []).map((label) => [label.name, label.color ?? '#73839c']),
    ), [job]);

    const load = useCallback(async () => {
        if (!jobId) return;
        setLoading(true);
        try {
            const [nextReport, nextSettings] = await Promise.all([getReport(jobId), getJobSettings(jobId)]);
            if (activeJobId.current !== jobId) return;
            setReport(nextReport);
            setSettings(nextSettings);
            setError(null);
        } catch (err: any) {
            if (activeJobId.current === jobId) setError(errorText(err));
        } finally {
            if (activeJobId.current === jobId) setLoading(false);
        }
    }, [jobId]);

    useEffect(() => {
        setReport(null);
        setSettings(null);
        setError(null);
        setPlan(null);
        setLastResult(null);
        setPlanning(false);
        setRunning(false);
        setFixing(false);
        setExcluding(false);
        setPlanError(null);
        setClassFilter([]);
        setSeverityFilter('all');
        setStatusFilter('todo');
        setCurrentFrameOnly(false);
        setSearch('');
        setHiddenLabels([]);
        setOnlyFlagged(false);
        setVisibilityTouched(false);
        setPicking(false);
        setMarked(loadMarked(jobId));
        setMarkedResult(null);
        load();
    }, [load]);

    useEffect(() => {
        if (!jobId) return;
        try {
            window.localStorage.setItem(markedKey(jobId), JSON.stringify(marked));
        } catch {
            // storage is optional
        }
    }, [marked]);

    // picking mode: each box clicked on the 3D view is marked, clicked again unmarked
    useEffect(() => {
        const previous = lastActivated.current;
        lastActivated.current = activatedID;
        if (!picking || activatedID === null || activatedID === previous) return;
        const state = states.find((item: any) => item.clientID === activatedID);
        if (!state) return;
        const uid = uidOf(state, frame);
        if (!uid) {
            notification.info({ message: 'Box chưa được lưu', description: 'Bấm Save rồi chọn lại.' });
            return;
        }
        setMarked((prev) => (prev.some((item) => item.uid === uid) ?
            prev.filter((item) => item.uid !== uid) :
            [...prev, { uid, frame, label: state.label?.name ?? '' }]));
    }, [activatedID, picking]);

    const groups = useMemo(() => groupIssues(report?.issues ?? []), [report]);
    const todoCount = groups.filter((g) => g.status === 'todo').length;
    useEffect(() => {
        window.dispatchEvent(new CustomEvent(OPEN_COUNT_EVENT, { detail: todoCount }));
    }, [todoCount]);

    const visibleGroups = groups.filter((g) => (
        (statusFilter === 'all' || (statusFilter === 'todo' ? g.status === 'todo' : g.status !== 'todo')) &&
        (!classFilter.length || classFilter.includes(g.label)) &&
        (severityFilter === 'all' || g.severity === severityFilter) &&
        (!currentFrameOnly || g.frame === frame) &&
        (!search.trim() || `${g.label} ${g.uid} ${g.frame} ${g.issues.map((i) => `${i.rule} ${i.message}`).join(' ')}`
            .toLocaleLowerCase().includes(search.trim().toLocaleLowerCase()))
    ));
    const activeState = states.find((s: any) => s.clientID === activatedID);
    const isActive = (group: ObjectGroup): boolean => !!activeState && group.frame === frame && (
        activeState.serverID === +group.uid.split(':')[1] &&
        activeState.objectType === (group.uid.startsWith('t:') ? 'track' : 'shape')
    );
    const activeIndex = visibleGroups.findIndex(isActive);
    const groupOf = (uid: string, frame: number): ObjectGroup | undefined => (
        groups.find((group) => group.uid === uid && group.frame === frame)
    );

    const focus = async (group: ObjectGroup): Promise<void> => {
        setHiddenLabels((labels) => labels.filter((label) => label !== group.label));
        if (group.status !== 'todo') setOnlyFlagged(false);
        try {
            if (!await focusIssue(store, group.issues[0])) {
                notification.info({ message: 'Đối tượng không còn trên frame này', description: 'Chạy lại QC để cập nhật báo cáo.' });
            }
        } catch (err: any) {
            notification.error({ message: 'Không mở được đối tượng', description: errorText(err) });
        }
    };
    const resetFilters = (): void => {
        setSearch('');
        setClassFilter([]);
        setSeverityFilter('all');
        setCurrentFrameOnly(false);
        setStatusFilter('todo');
    };

    // keep the 3D view settings applied when frames / canvas change
    useEffect(() => { applyViewer(store, viewer); }, [viewer, frame]);
    useEffect(() => {
        applyQcOverlay(store, report?.issues ?? [], {
            enabled: markersEnabled, severity: severityFilter, labels: classFilter,
        }, marked.map((item) => item.uid));
    }, [report, frame, states, markersEnabled, severityFilter, classFilter, canvasReady, marked]);
    useEffect(() => () => {
        applyQcOverlay(store, [], { enabled: false, severity: 'all', labels: [] });
    }, []);
    useEffect(() => {
        if (!visibilityTouched) return;
        const flagged = onlyFlagged ? new Set(groups.filter((g) => g.frame === frame && g.status === 'todo')
            .map((g) => g.uid)) : null;
        applyVisibility(store, new Set(hiddenLabels), flagged);
    }, [hiddenLabels, onlyFlagged, frame, groups, visibilityTouched, states]);

    const updateIssues = (updated: QcIssue[]): void => {
        const byId = new Map(updated.map((issue) => [issue.id, issue]));
        setReport((prev) => prev && { ...prev, issues: prev.issues.map((issue) => byId.get(issue.id) ?? issue) });
    };

    const onCheckDone = useCallback(async (status: CheckStatus): Promise<void> => {
        setPlan(null);
        setLastResult(null);
        if (status.fixed) {
            const { applied, failed } = status.fixed;
            setMarkedResult(status.fixed);
            const done = new Set(applied.map((item) => item.uid));
            setMarked((prev) => prev.filter((item) => !done.has(item.uid)));
            notification.success({
                message: `Đã sửa ${applied.length} nhãn đã chọn`,
                description: failed.length ? `${failed.length} nhãn chưa sửa được: xem lý do ở tab Autofix` : undefined,
                placement: 'bottomRight',
            });
        }
        await reloadAnnotations(store);
        await load();
    }, [load]);

    const togglePicking = (): void => {
        lastActivated.current = activatedID; // the box selected before does not count as picked
        setPicking((value) => !value);
    };

    const onFixMarked = async (): Promise<void> => {
        if (!jobId || !marked.length) return;
        setFixingMarked(true);
        try {
            await saveIfNeeded(store);
            await startCheck(jobId, null, marked.map((item) => item.uid));
            if (activeJobId.current !== jobId) return;
            setMarkedResult(null);
            setPicking(false);
            setCheckStarted(Date.now());
        } catch (err: any) {
            notification.error({ message: 'Không sửa được nhãn đã chọn', description: errorText(err) });
        } finally {
            if (activeJobId.current === jobId) setFixingMarked(false);
        }
    };

    const onDecide = async (group: ObjectGroup, status: IssueStatus, reason = ''): Promise<boolean> => {
        try {
            const results = await Promise.allSettled(
                group.issues.map((issue) => decideIssue(issue.id, status, reason)),
            );
            updateIssues(results.flatMap((result) => (result.status === 'fulfilled' ? [result.value] : [])));
            const failed = results.find((result) => result.status === 'rejected');
            if (failed?.status === 'rejected') throw failed.reason;
            return true;
        } catch (err: any) {
            notification.error({ message: 'Không lưu được quyết định', description: errorText(err) });
            return false;
        }
    };

    const onFix = async (group: ObjectGroup, revert: boolean): Promise<void> => {
        if (!group.proposal) return;
        try {
            await saveIfNeeded(store);
            await (revert ? revertFix(group.proposal.id) : applyFix(group.proposal.id));
            if (activeJobId.current !== jobId) return;
            await reloadAnnotations(store);
            await load();
            await focusIssue(store, group.proposal);
            notification.success({
                message: revert ? 'Đã hoàn tác' : 'Đã sửa trên CVAT',
                description: group.proposal.proposal?.summary,
                placement: 'bottomRight',
            });
        } catch (err: any) {
            notification.error({ message: revert ? 'Không hoàn tác được' : 'Không sửa được', description: errorText(err) });
        }
    };

    const onCreateToggle = async (create: boolean): Promise<void> => {
        if (!jobId) return;
        setExcluding(true);
        setPlan(null);
        try {
            const nextSettings = await setAutofixCreate(jobId, create);
            if (activeJobId.current !== jobId) return;
            setSettings(nextSettings);
            if (report?.run) {
                const nextPlan = await autofix(jobId, true);
                if (activeJobId.current === jobId) setPlan(nextPlan);
            }
        } catch (err: any) {
            notification.error({ message: 'Không lưu được', description: errorText(err) });
        } finally {
            if (activeJobId.current === jobId) setExcluding(false);
        }
    };

    const onExclude = async (labels: string[]): Promise<void> => {
        if (!jobId) return;
        setExcluding(true);
        setPlan(null);
        setPlanError(null);
        try {
            const nextSettings = await setExcludedLabels(jobId, labels);
            if (activeJobId.current !== jobId) return;
            setSettings(nextSettings);
            if (report?.run) {
                const nextPlan = await autofix(jobId, true);
                if (activeJobId.current === jobId) setPlan(nextPlan);
            }
        } catch (err: any) {
            setPlanError(errorText(err));
            notification.error({ message: 'Không lưu được', description: errorText(err) });
        } finally {
            if (activeJobId.current === jobId) setExcluding(false);
        }
    };

    const loadPlan = useCallback(async (): Promise<void> => {
        if (!jobId) return;
        setPlanning(true);
        setPlan(null);
        setPlanError(null);
        try {
            const nextPlan = await autofix(jobId, true);
            if (activeJobId.current !== jobId) return;
            setPlan(nextPlan);
        } catch (err: any) {
            if (activeJobId.current === jobId) setPlanError(errorText(err));
        } finally {
            if (activeJobId.current === jobId) setPlanning(false);
        }
    }, [jobId, load]);

    useEffect(() => {
        if (view === 'auto' && jobId && report?.run) {
            loadPlan();
        }
    }, [view, report?.run?.id, loadPlan]);

    const onAutofix = async (): Promise<void> => {
        if (!jobId) return;
        setFixing(true);
        try {
            await saveIfNeeded(store);
            const result = await autofix(jobId, false);
            if (activeJobId.current !== jobId) return;
            setLastResult(result);
            await reloadAnnotations(store);
            await load();
            await loadPlan();
            notification.success({
                message: `Đã tự động sửa ${result.applied.length} đối tượng`,
                description: result.failed.length ? `${result.failed.length} đối tượng cần chạy lại QC` : undefined,
                placement: 'bottomRight',
            });
        } catch (err: any) {
            notification.error({ message: 'Không tự động sửa được', description: errorText(err) });
        } finally {
            if (activeJobId.current === jobId) setFixing(false);
        }
    };

    if (!job || (loading && !report)) {
        return (
            <div className='qc3d-panel qc3d-center'><Spin /><span className='qc3d-hint'>Đang tải dữ liệu kiểm tra…</span></div>
        );
    }
    if (error) {
        return (
            <div className='qc3d-panel'>
                <Alert type='error' showIcon message='Không tải được QC' description={error} />
                <Button icon={<ReloadOutlined />} loading={loading} onClick={load}>Thử lại</Button>
            </div>
        );
    }
    if (!report || !settings) return <div className='qc3d-panel qc3d-center'><Spin /></div>;

    const { labels } = settings;
    const fixedCount = groups.filter((g) => g.status === 'fixed').length;
    const doneCount = groups.length - todoCount;

    return (
        <div className='qc3d-panel'>
            <div className='qc3d-summary'>
                <div className='qc3d-stat'>
                    <b>{todoCount}</b>
                    <span>cần kiểm</span>
                </div>
                <div className='qc3d-stat'>
                    <b>{doneCount}</b>
                    <span>đã kiểm</span>
                </div>
                <div className='qc3d-stat'>
                    <b>{fixedCount}</b>
                    <span>đã sửa</span>
                </div>
            </div>
            {jobId !== undefined && (
                <RunBar
                    jobId={jobId}
                    frame={frame}
                    canRun={settings.can_run}
                    guideline={report.run?.guideline?.name}
                    onDone={onCheckDone}
                    started={checkStarted}
                />
            )}
            {report.run?.frames && (
                <div className='qc3d-hint'>{`Kết quả của frame ${report.run.frames.join(', ')}`}</div>
            )}

            <Segmented
                block
                value={view}
                onChange={(value) => setView(value as View)}
                options={[
                    { value: 'review', label: 'Duyệt', icon: <AimOutlined /> },
                    { value: 'auto', label: 'Autofix', icon: <ThunderboltOutlined /> },
                    { value: 'view', label: 'Hiển thị', icon: <EyeOutlined /> },
                ]}
            />

            {view === 'review' && (
                <>
                    <div className={`qc3d-pick${picking ? ' qc3d-pick-on' : ''}`}>
                        <div className='qc3d-pick-head'>
                            <Button
                                size='small'
                                type={picking ? 'primary' : 'default'}
                                icon={<HighlightOutlined />}
                                aria-pressed={picking}
                                onClick={togglePicking}
                            >
                                {picking ? 'Đang chọn nhãn lỗi' : 'Chọn nhãn lỗi'}
                            </Button>
                            {marked.length > 0 && (
                                <Button size='small' type='link' onClick={() => setView('auto')}>
                                    {`Sửa ${marked.length} nhãn ở Autofix`}
                                </Button>
                            )}
                        </div>
                        {picking && (
                            <div className='qc3d-hint'>Bấm box lỗi trên cảnh 3D để chọn, bấm lại để bỏ. Box đã chọn viền tím.</div>
                        )}
                        {marked.length > 0 && (
                            <div className='qc3d-pick-list'>
                                {marked.map((item) => (
                                    <span key={item.uid} className='qc3d-pick-item'>
                                        <button
                                            type='button'
                                            className='qc3d-pick-go'
                                            onClick={() => focusIssue(store, item).catch(() => undefined)}
                                        >
                                            {`${item.label || 'Box'} · f${item.frame}`}
                                        </button>
                                        <CloseOutlined
                                            aria-label={`Bỏ chọn ${item.label} frame ${item.frame}`}
                                            onClick={() => setMarked((prev) => prev.filter((m) => m.uid !== item.uid))}
                                        />
                                    </span>
                                ))}
                                <Button size='small' type='link' danger onClick={() => setMarked([])}>Bỏ hết</Button>
                            </div>
                        )}
                    </div>
                    <div className='qc3d-filters'>
                        <Input
                            size='small'
                            prefix={<SearchOutlined />}
                            allowClear
                            placeholder='Tìm nhãn, frame, rule hoặc đối tượng'
                            aria-label='Tìm đối tượng trong báo cáo QC'
                            value={search}
                            onChange={(event) => setSearch(event.target.value)}
                        />
                        <Segmented
                            size='small'
                            block
                            value={statusFilter}
                            onChange={(value) => setStatusFilter(value as StatusFilter)}
                            options={[
                                { value: 'todo', label: `Cần kiểm ${todoCount}` },
                                { value: 'done', label: `Đã kiểm ${doneCount}` },
                                { value: 'all', label: 'Tất cả' },
                            ]}
                        />
                        <Select
                            size='small'
                            value={severityFilter}
                            aria-label='Lọc mức độ lỗi QC'
                            onChange={setSeverityFilter}
                            options={[
                                { value: 'all', label: 'Mọi mức độ' },
                                ...Object.entries(SEVERITY_TEXT).map(([value, label]) => ({
                                    value,
                                    label: `${label} (${groups.filter((group) => group.severity === value).length})`,
                                })),
                            ]}
                        />
                        <Select
                            size='small'
                            mode='multiple'
                            allowClear
                            maxTagCount='responsive'
                            placeholder='Lọc class'
                            aria-label='Lọc theo nhãn đối tượng'
                            value={classFilter}
                            onChange={setClassFilter}
                            options={labels.map((name) => ({
                                value: name,
                                label: `${name} (${groups.filter((group) => group.label === name).length})`,
                            }))}
                        />
                        <label className='qc3d-switch'>
                            <Switch size='small' checked={currentFrameOnly} onChange={setCurrentFrameOnly} />
                            {`Chỉ frame hiện tại (${frame})`}
                        </label>
                    </div>
                    <div className='qc3d-map-options'>
                        <label className='qc3d-switch'>
                            <Switch size='small' checked={markersEnabled} onChange={setMarkersEnabled} />
                            Cảnh báo & thông số trên cảnh 3D
                        </label>
                        <div className='qc3d-map-legend'>
                            <span className='qc3d-legend-blocker'>Nghiêm trọng</span>
                            <span className='qc3d-legend-major'>Cần xử lý</span>
                            <span className='qc3d-legend-minor'>Lưu ý</span>
                            <span className='qc3d-legend-marked'>Đã chọn</span>
                        </div>
                    </div>
                    {visibleGroups.length > 0 && (
                        <div className='qc3d-review-navigation'>
                            <span aria-live='polite'>
                                {activeIndex >= 0 ? `${activeIndex + 1} / ${visibleGroups.length} đối tượng` :
                                    `${visibleGroups.length} đối tượng`}
                            </span>
                            <div>
                                <Tooltip title='Đối tượng trước'>
                                    <Button
                                        size='small'
                                        icon={<LeftOutlined />}
                                        aria-label='Chọn đối tượng trước'
                                        disabled={activeIndex <= 0}
                                        onClick={() => focus(visibleGroups[activeIndex - 1])}
                                    />
                                </Tooltip>
                                <Tooltip title={activeIndex < 0 ? 'Bắt đầu duyệt' : 'Đối tượng tiếp theo'}>
                                    <Button
                                        size='small'
                                        icon={<RightOutlined />}
                                        aria-label='Chọn đối tượng tiếp theo'
                                        disabled={activeIndex >= visibleGroups.length - 1}
                                        onClick={() => focus(visibleGroups[activeIndex + 1])}
                                    />
                                </Tooltip>
                            </div>
                        </div>
                    )}
                    {!report.run && (
                        <Empty
                            description={<span>Chưa có báo cáo<br /><span className='qc3d-hint'>Bấm Chạy QC.</span></span>}
                            image={Empty.PRESENTED_IMAGE_SIMPLE}
                        />
                    )}
                    {report.run && !visibleGroups.length && (
                        <Empty
                            description={groups.length ? 'Không có đối tượng phù hợp bộ lọc' : 'Không phát hiện vấn đề trong lần kiểm tra này'}
                            image={Empty.PRESENTED_IMAGE_SIMPLE}
                        >
                            {groups.length > 0 && <Button size='small' onClick={resetFilters}>Đặt lại bộ lọc</Button>}
                        </Empty>
                    )}
                    <div className='qc3d-list'>
                        {visibleGroups.map((group) => (
                            <ObjectCard
                                key={group.key}
                                group={group}
                                active={isActive(group)}
                                canDecide={settings.can_decide && !running && !fixing}
                                canFix={settings.can_fix && !running && !fixing}
                                labelColor={labelColors[group.label] ?? '#bfbfbf'}
                                onFocus={() => focus(group)}
                                onDecide={(status, reason) => onDecide(group, status, reason)}
                                onFix={(revert) => onFix(group, revert)}
                            />
                        ))}
                    </div>
                </>
            )}

            {view === 'auto' && (
                <div className='qc3d-auto'>
                    {(marked.length > 0 || markedResult) && (
                        <div className='qc3d-marked-fix'>
                            <div className='qc3d-section-title'>{`Nhãn đã chọn ở tab Duyệt: ${marked.length}`}</div>
                            <div className='qc3d-hint'>Chỉ sửa các nhãn này (model + điểm LiDAR, theo guideline); nhãn khác giữ nguyên.</div>
                            <Button
                                type='primary'
                                block
                                icon={<ThunderboltOutlined />}
                                disabled={!marked.length || !settings.can_fix}
                                loading={fixingMarked}
                                onClick={onFixMarked}
                            >
                                {`Sửa ${marked.length} nhãn đã chọn`}
                            </Button>
                            {markedResult && (
                                <div className='qc3d-marked-result'>
                                    {markedResult.applied.map((item) => (
                                        <div key={`a${item.uid}`} className='qc3d-marked-ok'>
                                            <CheckOutlined />
                                            {` f${item.frame} · ${item.summary}`}
                                        </div>
                                    ))}
                                    {markedResult.failed.map((item) => (
                                        <div key={`f${item.uid}`} className='qc3d-marked-no'>
                                            <CloseOutlined />
                                            {` ${item.frame !== null ? `f${item.frame} · ` : ''}${item.reason}`}
                                        </div>
                                    ))}
                                </div>
                            )}
                        </div>
                    )}
                    {plan?.guideline && (
                        <div className='qc3d-autofix-guideline'>
                            <div className='qc3d-section-title'>{`Theo guideline: ${plan.guideline.name || 'mặc định'}`}</div>
                            <div className='qc3d-kind-chips'>
                                {plan.guideline.allowed_kinds.map((kind) => (
                                    <span key={kind} className='qc3d-kind-chip qc3d-kind-on'>{kind}</span>
                                ))}
                                {plan.guideline.blocked_kinds.map((kind) => (
                                    <span key={kind} className='qc3d-kind-chip qc3d-kind-off'>{`Không: ${kind}`}</span>
                                ))}
                            </div>
                            {plan.guideline.excluded_labels.length > 0 && (
                                <div className='qc3d-hint'>
                                    {`Guideline không cho autofix: ${plan.guideline.excluded_labels.join(', ')}`}
                                </div>
                            )}
                        </div>
                    )}
                    {plan?.stale_reason && (
                        <Alert type='warning' showIcon message={`Kết quả QC đã cũ (${plan.stale_reason}): bấm Chạy QC.`} />
                    )}
                    <div className='qc3d-section-title'>Áp dụng cho class</div>
                    <Segmented
                        block
                        size='small'
                        disabled={!settings.can_decide || excluding || fixing || running || planning}
                        value={settings.autofix_excluded_labels.length ? 'some' : 'all'}
                        onChange={(value) => {
                            if (value === 'all') onExclude([]);
                            else if (labels.length > 1) onExclude(labels.slice(1));  // start from the first class
                        }}
                        options={[{ value: 'all', label: 'Tất cả class' }, { value: 'some', label: 'Chọn class' }]}
                    />
                    {settings.autofix_excluded_labels.length > 0 && (
                        <Select
                            mode='multiple'
                            disabled={!settings.can_decide || excluding || fixing || running || planning}
                            loading={excluding}
                            placeholder='Chọn class Autofix được sửa / thêm'
                            aria-label='Class Autofix được áp dụng'
                            value={labels.filter((name) => !settings.autofix_excluded_labels.includes(name))}
                            onChange={(chosen: string[]) => onExclude(labels.filter((name) => !chosen.includes(name)))}
                            options={labels.map((name) => ({ value: name, label: name }))}
                            style={{ width: '100%' }}
                        />
                    )}
                    <label className='qc3d-switch'>
                        <Switch
                            size='small'
                            checked={settings.autofix_create}
                            disabled={!settings.can_decide || excluding || fixing || running || planning}
                            onChange={onCreateToggle}
                        />
                        <Tooltip title='Chỉ Car, Rider trong 30 m; đáy đặt lên mặt đường, không đè nhãn có sẵn'>
                            <span>Thêm box mới (model)</span>
                        </Tooltip>
                    </label>
                    {!report.run ? (
                        <Alert type='info' showIcon message='Chạy QC trước để có đề xuất sửa.' />
                    ) : (
                        <div className='qc3d-plan'>
                            <div className='qc3d-section-title'>Xem trước bản sửa</div>
                            {planning && <Spin size='small' tip='Đang phân tích đề xuất…' />}
                            {planError && (
                                <Alert
                                    type='error'
                                    showIcon
                                    message='Không tải được kế hoạch sửa'
                                    description={planError}
                                    action={<Button size='small' onClick={loadPlan}>Thử lại</Button>}
                                />
                            )}
                            <div className='qc3d-plan-row'>
                                <span>Sửa box đang có</span>
                                <b>{plan ? `${plan.planned.filter((item) => !item.new_boxes.length).length} đối tượng` : '…'}</b>
                            </div>
                            <div className='qc3d-plan-row'>
                                <span>Thêm box mới</span>
                                <b>{plan ? `${plan.planned.reduce((sum, item) => sum + item.new_boxes.length, 0)} box` : '…'}</b>
                            </div>
                            <div className='qc3d-plan-row'>
                                <span>Để QC duyệt tay</span>
                                <b>{plan ? plan.skipped.length : '…'}</b>
                            </div>
                            {plan && plan.planned.length > 0 && (
                                <details className='qc3d-skipped'>
                                    <summary>{`Danh sách sẽ sửa (${plan.planned.length})`}</summary>
                                    {plan.planned.slice(0, 200).map((item) => {
                                        const group = groupOf(item.uid, item.frame);
                                        return (
                                            <PlanRow
                                                key={item.issue}
                                                title={`F${item.frame} · ${item.label}`}
                                                text={item.summary}
                                                onFocus={group ? () => focus(group) : undefined}
                                            />
                                        );
                                    })}
                                </details>
                            )}
                            {plan && plan.skipped.length > 0 && (
                                <details className='qc3d-skipped'>
                                    <summary>Lý do để QC duyệt tay</summary>
                                    {Object.entries(plan.skipped.reduce((acc: Record<string, number>, item) => {
                                        acc[item.reason] = (acc[item.reason] ?? 0) + 1;
                                        return acc;
                                    }, {})).map(([text, count]) => <div key={text}>{`${count} × ${text}`}</div>)}
                                    {plan.skipped.some((item) => item.checks.length > 0) && (
                                        <div className='qc3d-plan-checks'>
                                            {plan.skipped.filter((item) => item.checks.length > 0).slice(0, 100).map((item) => {
                                                const group = groupOf(item.uid, item.frame);
                                                return (
                                                    <PlanRow
                                                        key={item.issue}
                                                        title={`F${item.frame} · ${item.summary}`}
                                                        text={item.checks.join('; ')}
                                                        warning
                                                        onFocus={group ? () => focus(group) : undefined}
                                                    />
                                                );
                                            })}
                                        </div>
                                    )}
                                </details>
                            )}
                            <Popconfirm
                                title={`Ghi vào CVAT: sửa ${plan?.planned.filter((item) => !item.new_boxes.length).length ?? 0} box, thêm ${plan?.planned.reduce((sum, item) => sum + item.new_boxes.length, 0) ?? 0} box?`}
                                description='Thay đổi chưa lưu sẽ được lưu trước; mỗi bản sửa hoàn tác được ở tab Duyệt.'
                                okText='Autofix'
                                cancelText='Huỷ'
                                disabled={
                                    !plan?.planned.length || !settings.can_fix ||
                                    planning || running || fixing || excluding
                                }
                                onConfirm={onAutofix}
                            >
                                <Button
                                    block
                                    type='primary'
                                    icon={<ThunderboltOutlined />}
                                    loading={fixing}
                                    disabled={
                                        !plan?.planned.length || !settings.can_fix || planning || running || excluding
                                    }
                                >
                                    {plan?.planned.length ? (() => {
                                        const added = plan.planned.reduce((sum, item) => sum + item.new_boxes.length, 0);
                                        const fixed = plan.planned.filter((item) => !item.new_boxes.length).length;
                                        return [fixed ? `sửa ${fixed} box` : '', added ? `thêm ${added} box` : '']
                                            .filter(Boolean).join(', ').replace(/^./, (c) => c.toUpperCase());
                                    })() : 'Không có bản sửa để áp dụng'}
                                </Button>
                            </Popconfirm>
                            <div className='qc3d-hint'>Hoàn tác được trong Duyệt → Đã kiểm.</div>
                            {!settings.can_fix && <div className='qc3d-hint'>Cần quyền lead / reviewer.</div>}
                            {lastResult && (
                                <Alert
                                    type={lastResult.failed.length ? 'warning' : 'success'}
                                    showIcon
                                    message={
                                        `Lần trước: sửa ${lastResult.applied.length}, lỗi ${lastResult.failed.length}`
                                    }
                                    description={lastResult.failed.slice(0, 5).map((f) => (
                                        <div key={f.issue}>{`Frame ${f.frame} ${f.uid}: ${f.reason}`}</div>
                                    ))}
                                />
                            )}
                        </div>
                    )}
                </div>
            )}

            {view === 'view' && (
                <div className='qc3d-viewer'>
                    <div className='qc3d-section-title'>Đám mây điểm</div>
                    <div className='qc3d-point-presets'>
                        <Button size='small' onClick={() => setViewer({
                            ...viewer,
                            pointFocus: true,
                            pointSize: 0.08,
                            colorMode: 'height',
                            brightness: 125,
                            contrast: 110,
                        })}>
                            Kiểm chứng vật thể
                        </Button>
                        <Button size='small' onClick={() => setViewer({
                            ...viewer, pointSize: 0.08, brightness: 125, contrast: 110,
                        })}>
                            Điểm rõ hơn
                        </Button>
                        <Button size='small' type='primary' ghost onClick={() => setViewer({
                            ...viewer,
                            colorMode: 'objects',
                            depthShading: true,
                            roundPoints: true,
                            pointSize: 0.07,
                            brightness: 110,
                            contrast: 105,
                        })}>
                            Nổi bật vật thể
                        </Button>
                    </div>
                    <label className='qc3d-switch'>
                        <Switch size='small' checked={viewer.pointFocus}
                            onChange={(checked) => setViewer({ ...viewer, pointFocus: checked })}
                        />
                        Nổi bật điểm quanh vật đang chọn
                    </label>
                    <div className='qc3d-slider-row'>
                        <span>{`Độ sáng ${viewer.brightness}%`}</span>
                        <Slider min={50} max={250} value={viewer.brightness}
                            onChange={(v: number) => setViewer({ ...viewer, brightness: v })}
                        />
                    </div>
                    <div className='qc3d-slider-row'>
                        <span>{`Tương phản ${viewer.contrast}%`}</span>
                        <Slider min={50} max={200} value={viewer.contrast}
                            onChange={(v: number) => setViewer({ ...viewer, contrast: v })}
                        />
                    </div>
                    <div className='qc3d-slider-row'>
                        <span>{`Cỡ điểm ${viewer.pointSize.toFixed(3)}`}</span>
                        <Slider
                            min={0.01}
                            max={0.2}
                            step={0.005}
                            value={viewer.pointSize}
                            onChange={(v: number) => setViewer({ ...viewer, pointSize: v })}
                        />
                    </div>
                    <div className='qc3d-slider-row'>
                        <span>Tô màu</span>
                        <Segmented
                            size='small'
                            value={viewer.colorMode}
                            onChange={(value) => setViewer({ ...viewer, colorMode: value as ViewerSettings['colorMode'] })}
                            options={COLOR_MODES.map(({ value, label }) => ({ value, label }))}
                        />
                    </div>
                    <div className='qc3d-hint'>{COLOR_MODES.find((mode) => mode.value === viewer.colorMode)?.hint}</div>
                    <label className='qc3d-switch'>
                        <Switch size='small' checked={viewer.depthShading}
                            onChange={(checked) => setViewer({ ...viewer, depthShading: checked })}
                        />
                        Chiều sâu: điểm xa mờ dần, vật gần nổi lên
                    </label>
                    <label className='qc3d-switch'>
                        <Switch size='small' checked={viewer.roundPoints}
                            onChange={(checked) => setViewer({ ...viewer, roundPoints: checked })}
                        />
                        Điểm tròn mịn (ít nhiễu hơn điểm vuông)
                    </label>
                    <label className='qc3d-switch'>
                        <Switch size='small' checked={viewer.guides}
                            onChange={(checked) => setViewer({ ...viewer, guides: checked })}
                        />
                        Vòng khoảng cách 10/20/30 m và lưới mặt đường
                    </label>
                    <Button size='small' type='link' onClick={() => setViewer(DEFAULT_VIEWER)}>Về mặc định</Button>

                    <div className='qc3d-section-title'>Đối tượng</div>
                    <label className='qc3d-switch'>
                        <Switch
                            size='small'
                            checked={onlyFlagged}
                            onChange={(checked) => { setVisibilityTouched(true); setOnlyFlagged(checked); }}
                        />
                        Chỉ hiện đối tượng có lỗi cần kiểm
                    </label>
                    <div className='qc3d-hint'>Ẩn class trên frame (không thay đổi dữ liệu):</div>
                    <div className='qc3d-class-toggles'>
                        {labels.map((name) => {
                            const hidden = hiddenLabels.includes(name);
                            return (
                                <button
                                    type='button'
                                    key={name}
                                    aria-pressed={!hidden}
                                    aria-label={`${hidden ? 'Hiện' : 'Ẩn'} nhãn ${name}`}
                                    className={`qc3d-class-toggle${hidden ? ' qc3d-class-off' : ''}`}
                                    onClick={() => {
                                        setVisibilityTouched(true);
                                        setHiddenLabels(hidden ? hiddenLabels.filter((l) => l !== name) :
                                            [...hiddenLabels, name]);
                                    }}
                                >
                                    <span className='qc3d-label-dot' style={{ background: labelColors[name] ?? '#bfbfbf' }} />
                                    {name}
                                </button>
                            );
                        })}
                    </div>
                    {(hiddenLabels.length > 0 || onlyFlagged) && (
                        <Button size='small' onClick={() => {
                            setVisibilityTouched(true);
                            setHiddenLabels([]);
                            setOnlyFlagged(false);
                        }}>
                            Hiện tất cả đối tượng
                        </Button>
                    )}
                </div>
            )}
        </div>
    );
}
