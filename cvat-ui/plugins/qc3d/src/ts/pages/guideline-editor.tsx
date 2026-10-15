// Copyright (C) CVAT.ai Corporation
//
// SPDX-License-Identifier: MIT

// Ant Design controls render native inputs associated with these htmlFor labels.
/* eslint jsx-a11y/label-has-associated-control: ["error", { "assert": "either" }] */

import React, { useCallback, useEffect, useState } from 'react';
import {
    Prompt, useHistory, useLocation, useParams,
} from 'react-router-dom';
import {
    ArrowLeftOutlined, PlusOutlined, ReloadOutlined, SaveOutlined, ThunderboltOutlined, UploadOutlined,
} from '@ant-design/icons';
import Alert from 'antd/lib/alert';
import Button from 'antd/lib/button';
import Card from 'antd/lib/card';
import Col from 'antd/lib/col';
import Input from 'antd/lib/input';
import InputNumber from 'antd/lib/input-number';
import Modal from 'antd/lib/modal';
import Row from 'antd/lib/row';
import Select from 'antd/lib/select';
import Space from 'antd/lib/space';
import Spin from 'antd/lib/spin';
import Switch from 'antd/lib/switch';
import Table from 'antd/lib/table';
import Tabs from 'antd/lib/tabs';
import Upload from 'antd/lib/upload';
import notification from 'antd/lib/notification';

import {
    AcceptanceCriteria, compileGuideline, createGuideline, deleteGuideline, errorText, getGuideline, getGuidelines,
    GuidelineAutofix,
    GuidelineClass, GuidelineContent, GuidelineInput, GuidelineList, ParsedGuideline, updateGuideline,
} from '../api';
import { IMPORT_ACCEPT, importGuidelineFile } from './guidelines';

const AXES = ['Dài', 'Rộng', 'Cao'];

const EMPTY: GuidelineInput = {
    name: '',
    description: '',
    is_default: false,
    source_name: '',
    projects: [],
    tasks: [],
    content: {
        format: 1, classes: [], thresholds: {}, rules: {}, notes: '',
    },
};

function parseContent(text: string): GuidelineContent {
    const parsed: unknown = JSON.parse(text);
    const isRecord = (value: unknown): value is Record<string, unknown> => (
        !!value && typeof value === 'object' && !Array.isArray(value)
    );
    const isTriple = (value: unknown): value is [number, number, number] => (
        Array.isArray(value) && value.length === 3 && value.every((n) => typeof n === 'number' && Number.isFinite(n))
    );
    if (!isRecord(parsed) || !Array.isArray(parsed.classes) || !parsed.classes.length) {
        throw new Error('Nội dung phải là object JSON có classes là danh sách chứa ít nhất một nhóm nhãn.');
    }
    const names = new Set<string>();
    const classes = parsed.classes.map((cls, index) => {
        if (!isRecord(cls) || typeof cls.name !== 'string' || !cls.name.trim()) {
            throw new Error(`Nhóm nhãn #${index + 1}: name phải là tên nhãn hợp lệ.`);
        }
        if (names.has(cls.name.trim())) throw new Error(`Tên nhãn bị trùng: ${cls.name}`);
        names.add(cls.name.trim());
        if (!isTriple(cls.size_min) || !isTriple(cls.size_max) ||
            cls.size_min.some((lo, axis) => lo <= 0 || lo > (cls.size_max as number[])[axis])) {
            throw new Error(`${cls.name}: size_min và size_max cần 3 số theo [dài, rộng, cao], với 0 < min ≤ max.`);
        }
        for (const key of ['size_mean', 'size_std']) {
            if (cls[key] !== undefined && (!isTriple(cls[key]) ||
                (key === 'size_std' && (cls[key] as number[]).some((value) => value <= 0)))) {
                throw new Error(`${cls.name}: ${key} cần 3 số hữu hạn; size_std phải lớn hơn 0.`);
            }
        }
        for (const key of ['points_at_10m', 'max_speed']) {
            const value = cls[key];
            if (value !== undefined && (typeof value !== 'number' || !Number.isFinite(value) || value < 0 ||
                (key === 'points_at_10m' && !Number.isInteger(value)))) {
                throw new Error(`${cls.name}: ${key} phải là số không âm; points_at_10m phải là số nguyên.`);
            }
        }
        if ((cls.name_vi !== undefined && typeof cls.name_vi !== 'string') ||
            (cls.description !== undefined && typeof cls.description !== 'string')) {
            throw new Error(`${cls.name}: name_vi và description phải là chuỗi.`);
        }
        return {
            ...cls,
            name: cls.name.trim(),
            name_vi: typeof cls.name_vi === 'string' ? cls.name_vi : '',
            description: typeof cls.description === 'string' ? cls.description : '',
            size_min: cls.size_min,
            size_max: cls.size_max,
        };
    });
    const thresholds = parsed.thresholds ?? {};
    const rules = parsed.rules ?? {};
    if (!isRecord(thresholds) || Object.values(thresholds).some((value) => typeof value !== 'number' || !Number.isFinite(value))) {
        throw new Error('thresholds phải là object chứa các giá trị số hữu hạn.');
    }
    if (!isRecord(rules) || Object.values(rules).some((value) => typeof value !== 'boolean')) {
        throw new Error('rules phải là object chứa giá trị true hoặc false.');
    }
    if (rules.R01 === false) throw new Error('Không được tắt R01 (nhãn không hợp lệ).');
    if (parsed.notes !== undefined && typeof parsed.notes !== 'string') throw new Error('notes phải là chuỗi.');
    const acceptance = parsed.acceptance ?? {};
    if (!isRecord(acceptance) || Object.values(acceptance).some((value) => typeof value !== 'number' || !Number.isFinite(value))) {
        throw new Error('acceptance phải là object chứa các giá trị số hữu hạn.');
    }
    let autofix: GuidelineAutofix | undefined;
    if (parsed.autofix !== undefined) {
        const raw = parsed.autofix;
        if (!isRecord(raw)) throw new Error('autofix phải là object.');
        const exclude = raw.exclude_classes ?? [];
        const fix = raw.fix ?? {};
        if (!Array.isArray(exclude) || exclude.some((name) => typeof name !== 'string')) {
            throw new Error('autofix.exclude_classes phải là danh sách tên class.');
        }
        if (!isRecord(fix) || Object.values(fix).some((value) => typeof value !== 'boolean')) {
            throw new Error('autofix.fix phải là object chứa giá trị true hoặc false.');
        }
        autofix = { exclude_classes: exclude as string[], fix: fix as Record<string, boolean> };
    }
    return {
        format: 1,
        classes: classes as GuidelineClass[],
        thresholds: thresholds as Record<string, number>,
        rules: rules as Record<string, boolean>,
        ...(autofix ? { autofix } : {}),
        ...(Object.keys(acceptance).length ? { acceptance: acceptance as Partial<AcceptanceCriteria> } : {}),
        notes: typeof parsed.notes === 'string' ? parsed.notes : '',
    };
}

// job acceptance by QA: key, label, unit, server limits (qc/guideline.py ACCEPTANCE_LIMITS), default
const ACCEPTANCE_FIELDS: {
    key: keyof AcceptanceCriteria; label: string; percent: boolean; min: number; max: number; fallback: number; hint: string;
}[] = [
    { key: 'sample_ratio', label: 'Tỷ lệ frame lấy mẫu', percent: true, min: 1, max: 100, fallback: 10, hint: 'Phần trăm số frame của job QA chấm' },
    { key: 'min_frames', label: 'Số frame mẫu tối thiểu', percent: false, min: 1, max: 1000, fallback: 5, hint: 'Job ngắn vẫn chấm ít nhất chừng này frame' },
    { key: 'max_error_rate', label: 'Tỷ lệ box lỗi tối đa', percent: true, min: 0, max: 100, fallback: 2, hint: 'Box lỗi / box đã kiểm trên các frame mẫu' },
    { key: 'max_open_blockers', label: 'Lỗi nghiêm trọng còn mở tối đa', percent: false, min: 0, max: 1000, fallback: 0, hint: 'Lỗi QC mức nghiêm trọng chưa xử lý' },
];

export default function GuidelineEditorPage(): JSX.Element {
    const { id } = useParams<{ id: string }>();
    const isNew = id === 'new';
    const history = useHistory();
    const location = useLocation<{ parsed?: ParsedGuideline } | undefined>();
    const [meta, setMeta] = useState<GuidelineList | null>(null);
    const [form, setForm] = useState<GuidelineInput | null>(null);
    const [revision, setRevision] = useState<number | null>(null);
    const [warnings, setWarnings] = useState<string[]>([]);
    const [compiled, setCompiled] = useState<string[]>([]);
    const [compiling, setCompiling] = useState(false);
    const [json, setJson] = useState<string | null>(null);
    const [jsonError, setJsonError] = useState<string | null>(null);
    const [baseline, setBaseline] = useState('');
    const [loading, setLoading] = useState(true);
    const [importing, setImporting] = useState(false);
    const [error, setError] = useState<string | null>(null);
    const [saving, setSaving] = useState(false);

    const load = useCallback(async (): Promise<void> => {
        setLoading(true);
        setError(null);
        try {
            const list = await getGuidelines();
            setMeta(list);
            if (!isNew) {
                const g = await getGuideline(+id);
                setRevision(g.revision);
                const nextForm = {
                    name: g.name,
                    description: g.description,
                    is_default: g.is_default,
                    source_name: g.source_name,
                    content: g.content,
                    projects: g.projects.map((p) => p.id),
                    tasks: g.tasks.map((t) => t.id),
                };
                setForm(nextForm);
                setBaseline(JSON.stringify(nextForm));
                return;
            }
            const parsed = location.state?.parsed;
            setWarnings(parsed?.warnings ?? []);
            setCompiled(parsed?.compiled ?? []);
            const nextForm = {
                ...EMPTY,
                name: parsed ? parsed.source_name.replace(/\.[^.]+$/, '') : 'Guideline mới',
                source_name: parsed?.source_name ?? '',
                content: parsed?.content ?? list.template,
            };
            setForm(nextForm);
            setBaseline(parsed ? '' : JSON.stringify(nextForm));
        } catch (err: any) {
            setError(errorText(err));
        } finally {
            setLoading(false);
        }
    }, [id, isNew, location.state?.parsed]);

    useEffect(() => { setForm(null); setJson(null); setJsonError(null); load(); }, [load]);
    const dirty = !!form && !!meta?.can_manage && (JSON.stringify(form) !== baseline || json !== null);
    useEffect(() => {
        const beforeUnload = (event: BeforeUnloadEvent): void => {
            if (dirty) {
                event.preventDefault();
                // The beforeunload browser API requires setting returnValue.
                // eslint-disable-next-line no-param-reassign
                event.returnValue = '';
            }
        };
        window.addEventListener('beforeunload', beforeUnload);
        return () => window.removeEventListener('beforeunload', beforeUnload);
    }, [dirty]);

    const setContent = useCallback((update: (content: GuidelineContent) => GuidelineContent) => {
        setForm((prev) => prev && { ...prev, content: update(prev.content) });
    }, []);

    const setClass = useCallback((index: number, patch: Partial<GuidelineClass>, sizeChanged = false) => {
        setContent((content) => ({
            ...content,
            classes: content.classes.map((cls, i) => {
                if (i !== index) return cls;
                const next = { ...cls, ...patch };
                if (sizeChanged) {
                    // the size prior follows the new range: recomputed by the server
                    delete next.size_mean;
                    delete next.size_std;
                }
                return next;
            }),
        }));
    }, [setContent]);

    const onSave = useCallback(async () => {
        if (!form || json !== null) return;
        setSaving(true);
        try {
            const saved = isNew ? await createGuideline(form) : await updateGuideline(+id, form);
            notification.success({ message: `Đã lưu guideline (bản ${saved.revision})` });
            setRevision(saved.revision);
            const nextForm = { ...form, content: saved.content };
            setForm(nextForm);
            setBaseline(JSON.stringify(nextForm));
            setWarnings([]);
            if (isNew) history.replace(`/qc3d/guidelines/${saved.id}`);
        } catch (err: any) {
            notification.error({ message: 'Không lưu được guideline', description: errorText(err), duration: 10 });
        } finally {
            setSaving(false);
        }
    }, [form, id, isNew, json]);

    const onDelete = useCallback(() => {
        Modal.confirm({
            title: `Xoá guideline "${form?.name}"?`,
            content: 'Các job đang gán sẽ dùng guideline cấp trên. Kết quả QC cũ vẫn giữ bản chụp guideline.',
            okText: 'Xoá',
            cancelText: 'Hủy',
            okButtonProps: { danger: true },
            onOk: async () => {
                setSaving(true);
                try {
                    await deleteGuideline(+id);
                    history.push('/qc3d/guidelines');
                } catch (err: any) {
                    notification.error({ message: 'Không xoá được', description: errorText(err) });
                } finally {
                    setSaving(false);
                }
            },
        });
    }, [form, id]);

    const onReplaceFromFile = useCallback(async (file: File) => {
        setImporting(true);
        try {
            const parsed = await importGuidelineFile(file);
            setForm((prev) => prev && { ...prev, content: parsed.content, source_name: parsed.source_name });
            setWarnings(parsed.warnings);
            setCompiled(parsed.compiled ?? []);
            setJson(null);
            setJsonError(null);
            notification.success({
                message: `Đã đọc ${file.name}: ${parsed.content.classes.length} class. Bấm Lưu để áp dụng.`,
            });
        } catch (err: any) {
            notification.error({ message: `Không đọc được ${file.name}`, description: errorText(err), duration: 10 });
        } finally {
            setImporting(false);
        }
    }, []);

    const onCompileNotes = async (): Promise<void> => {
        if (!form) return;
        setCompiling(true);
        try {
            const result = await compileGuideline(form.content, form.content.notes);
            setForm((prev) => prev && { ...prev, content: { ...result.content, notes: prev.content.notes } });
            setCompiled(result.compiled);
            setWarnings(result.warnings);
            setJson(null);
            notification.success({
                message: result.compiled.length ?
                    `Đã biên dịch ${result.compiled.length} câu thành quy tắc. Kiểm tra rồi bấm Lưu.` :
                    'Không có câu nào đổi được quy tắc',
            });
        } catch (err: any) {
            notification.error({ message: 'Không biên dịch được', description: errorText(err), duration: 10 });
        } finally {
            setCompiling(false);
        }
    };

    if (error) {
        return (
            <Space direction='vertical' style={{ width: '100%' }}>
                <Alert type='error' showIcon message='Không tải được hướng dẫn' description={error} />
                <Button icon={<ReloadOutlined />} loading={loading} onClick={load}>Thử lại</Button>
            </Space>
        );
    }
    if (!meta || !form) return <div className='qc3d-loading-state'><Spin size='large' />Đang tải nội dung hướng dẫn…</div>;

    const readOnly = !meta.can_manage || saving || importing;
    const { content } = form;

    const sizeColumn = (axis: number): object => ({
        title: `${AXES[axis]} (m)`,
        width: 150,
        render: (_: unknown, cls: GuidelineClass, index: number) => (
            <Space size={2}>
                <InputNumber
                    size='small'
                    min={0.01}
                    step={0.1}
                    style={{ width: 66 }}
                    value={cls.size_min[axis]}
                    aria-label={`${cls.name || `Nhóm ${index + 1}`} ${AXES[axis]} tối thiểu (m)`}
                    disabled={readOnly}
                    onChange={(value) => {
                        if (value === null) return;
                        const sizeMin = [...cls.size_min] as GuidelineClass['size_min'];
                        sizeMin[axis] = Number(value);
                        setClass(index, { size_min: sizeMin }, true);
                    }}
                />
                –
                <InputNumber
                    size='small'
                    min={0.01}
                    step={0.1}
                    style={{ width: 66 }}
                    value={cls.size_max[axis]}
                    aria-label={`${cls.name || `Nhóm ${index + 1}`} ${AXES[axis]} tối đa (m)`}
                    disabled={readOnly}
                    onChange={(value) => {
                        if (value === null) return;
                        const sizeMax = [...cls.size_max] as GuidelineClass['size_max'];
                        sizeMax[axis] = Number(value);
                        setClass(index, { size_max: sizeMax }, true);
                    }}
                />
            </Space>
        ),
    });

    const classColumns = [
        {
            title: 'Class (đúng tên label CVAT)',
            width: 160,
            render: (_: unknown, cls: GuidelineClass, index: number) => (
                <Input size='small' aria-label={`Tên nhãn CVAT của nhóm ${index + 1}`} value={cls.name} disabled={readOnly} onChange={(e) => setClass(index, { name: e.target.value })} />
            ),
        },
        {
            title: 'Tên tiếng Việt',
            width: 160,
            render: (_: unknown, cls: GuidelineClass, index: number) => (
                <Input size='small' aria-label={`Tên tiếng Việt của nhóm ${index + 1}`} value={cls.name_vi} disabled={readOnly} onChange={(e) => setClass(index, { name_vi: e.target.value })} />
            ),
        },
        {
            title: 'Bao gồm',
            render: (_: unknown, cls: GuidelineClass, index: number) => (
                <Input size='small' aria-label={`Mô tả nhóm nhãn ${index + 1}`} value={cls.description} disabled={readOnly} onChange={(e) => setClass(index, { description: e.target.value })} />
            ),
        },
        sizeColumn(0),
        sizeColumn(1),
        sizeColumn(2),
        {
            title: 'Điểm ở 10 m',
            width: 100,
            render: (_: unknown, cls: GuidelineClass, index: number) => (
                <InputNumber
                    size='small'
                    min={0}
                    precision={0}
                    aria-label={`Số điểm ở 10 m của ${cls.name || `nhóm ${index + 1}`}`}
                    value={cls.points_at_10m}
                    disabled={readOnly}
                    onChange={(value) => setClass(index, { points_at_10m: value === null ? undefined : Number(value) })}
                />
            ),
        },
        {
            title: 'Tốc độ max (m/s)',
            width: 110,
            render: (_: unknown, cls: GuidelineClass, index: number) => (
                <InputNumber
                    size='small'
                    min={0}
                    aria-label={`Vận tốc tối đa của ${cls.name || `nhóm ${index + 1}`}`}
                    value={cls.max_speed}
                    disabled={readOnly}
                    onChange={(value) => setClass(index, { max_speed: value === null ? undefined : Number(value) })}
                />
            ),
        },
        {
            width: 50,
            render: (_: unknown, __: GuidelineClass, index: number) => !readOnly && (
                <Button
                    size='small'
                    type='link'
                    danger
                    onClick={() => setContent((c) => ({ ...c, classes: c.classes.filter((___, i) => i !== index) }))}
                >
                    Xoá
                </Button>
            ),
        },
    ];

    const tabs = [
        {
            key: 'classes',
            label: `Nhóm nhãn (${content.classes.length})`,
            children: (
                <>
                    <p className='qc3d-cell-secondary'>Tên nhãn phải trùng với CVAT. Nhập khoảng kích thước dài, rộng, cao theo mét; thông số nâng cao có thể để trống để dùng mặc định.</p>
                    <Table<GuidelineClass>
                        rowKey={(_, index) => String(index)}
                        size='small'
                        columns={classColumns}
                        dataSource={content.classes}
                        pagination={false}
                        locale={{ emptyText: 'Thêm nhóm nhãn để cấu hình kích thước và class.' }}
                        scroll={{ x: 1200 }}
                    />
                    {!readOnly && (
                        <Button
                            style={{ marginTop: 8 }}
                            icon={<PlusOutlined />}
                            onClick={() => setContent((c) => ({
                                ...c,
                                classes: [...c.classes, {
                                    name: '', name_vi: '', description: '', size_min: [1, 0.5, 1], size_max: [2, 1, 2],
                                }],
                            }))}
                        >
                            Thêm nhóm nhãn
                        </Button>
                    )}
                </>
            ),
        },
        {
            key: 'thresholds',
            label: 'Ngưỡng',
            children: (
                <>
                    <p className='qc3d-cell-secondary'>Để trống để dùng ngưỡng mặc định. Giá trị riêng chỉ áp dụng cho hướng dẫn này.</p>
                    <Row gutter={[16, 12]}>
                        {meta.thresholds.map((t) => (
                            <Col xs={24} lg={12} key={t.key}>
                                <div className='qc3d-threshold-option'>
                                    <InputNumber
                                        size='small'
                                        min={t.min}
                                        max={t.max}
                                        step={0.05}
                                        style={{ width: 100 }}
                                        placeholder={String(t.default)}
                                        value={content.thresholds[t.key]}
                                        aria-label={t.label}
                                        disabled={readOnly}
                                        onChange={(value) => setContent((c) => {
                                            const thresholds = { ...c.thresholds };
                                            if (value === null || value === undefined) delete thresholds[t.key];
                                            else thresholds[t.key] = Number(value);
                                            return { ...c, thresholds };
                                        })}
                                    />
                                    <div><div>{t.label}</div><span className='qc3d-cell-secondary'>{`Mặc định ${t.default} · Khoảng ${t.min}–${t.max}`}</span></div>
                                </div>
                            </Col>
                        ))}
                    </Row>
                </>
            ),
        },
        {
            key: 'acceptance',
            label: 'Nghiệm thu',
            children: (
                <>
                    <p className='qc3d-cell-secondary'>
                        Tiêu chí QA dùng khi nghiệm thu job theo hướng dẫn này. Để trống để dùng mặc định.
                    </p>
                    <Row gutter={[16, 12]}>
                        {ACCEPTANCE_FIELDS.map((field) => {
                            const raw = content.acceptance?.[field.key];
                            const shown = raw === undefined ? undefined : Number((field.percent ? raw * 100 : raw).toFixed(2));
                            return (
                                <Col xs={24} lg={12} key={field.key}>
                                    <div className='qc3d-threshold-option'>
                                        <InputNumber
                                            size='small'
                                            min={field.min}
                                            max={field.max}
                                            step={field.percent ? 0.5 : 1}
                                            precision={field.percent ? 2 : 0}
                                            addonAfter={field.percent ? '%' : undefined}
                                            style={{ width: 130 }}
                                            placeholder={String(field.fallback)}
                                            value={shown}
                                            aria-label={field.label}
                                            disabled={readOnly}
                                            onChange={(value) => setContent((c) => {
                                                const next = { ...(c.acceptance ?? {}) };
                                                if (value === null || value === undefined) delete next[field.key];
                                                else next[field.key] = field.percent ? Number(value) / 100 : Number(value);
                                                return { ...c, acceptance: next };
                                            })}
                                        />
                                        <div>
                                            <div>{field.label}</div>
                                            <span className='qc3d-cell-secondary'>
                                                {`${field.hint} · mặc định ${field.fallback}${field.percent ? '%' : ''}`}
                                            </span>
                                        </div>
                                    </div>
                                </Col>
                            );
                        })}
                    </Row>
                </>
            ),
        },
        {
            key: 'rules',
            label: 'Quy tắc QC',
            children: (
                <>
                    <p className='qc3d-cell-secondary'>Bật các quy tắc cần kiểm tra. R01 luôn bật để bảo đảm nhãn hợp lệ.</p>
                    <Row gutter={[16, 12]}>
                        {meta.rules.map((r) => (
                            <Col xs={24} md={12} xl={8} key={r.code}>
                                <div className='qc3d-rule-option'>
                                    <Switch
                                        size='small'
                                        checked={content.rules[r.code] !== false}
                                        aria-label={`Bật quy tắc ${r.code}: ${r.title}`}
                                        disabled={readOnly || r.code === 'R01'}
                                        onChange={(checked) => setContent((c) => {
                                            const rules = { ...c.rules };
                                            if (checked) delete rules[r.code];
                                            else rules[r.code] = false;
                                            return { ...c, rules };
                                        })}
                                    />
                                    <span>{`${r.code} · ${r.title}`}</span>
                                </div>
                            </Col>
                        ))}
                    </Row>
                </>
            ),
        },
        {
            key: 'notes',
            label: 'Hướng dẫn (ngôn ngữ tự nhiên)',
            children: (
                <Space direction='vertical' style={{ width: '100%' }}>
                    <p className='qc3d-cell-secondary'>
                        Viết hướng dẫn bằng câu văn bình thường. Bấm “Biên dịch thành quy tắc” để chuyển các câu
                        quy định thành cấu hình QC và Autofix; câu nào chưa hiểu sẽ được báo lại, không bị đoán.
                    </p>
                    <Input.TextArea
                        rows={14}
                        aria-label='Hướng dẫn gán nhãn bằng ngôn ngữ tự nhiên'
                        disabled={readOnly}
                        placeholder={'Ví dụ:\nÔ tô con dài từ 3 đến 5,5 m, rộng 1,5-2,1 m.\nXe tải cao tối đa 4 m.\n'
                            + 'Đáy box lệch mặt đường không quá 10 cm.\nKhe hở giữa box và vật không quá 20 cm.\n'
                            + 'Hai box chồng lấn tối đa 20%.\nKhông kiểm tra vận tốc.\nKhông tự sửa người đi bộ.\n'
                            + 'Autofix không đổi class.'}
                        value={content.notes}
                        onChange={(e) => setContent((c) => ({ ...c, notes: e.target.value }))}
                    />
                    {!readOnly && (
                        <Button
                            type='primary'
                            ghost
                            icon={<ThunderboltOutlined />}
                            loading={compiling}
                            disabled={!content.notes.trim() || !content.classes.length || json !== null}
                            onClick={onCompileNotes}
                        >
                            Biên dịch thành quy tắc
                        </Button>
                    )}
                </Space>
            ),
        },
        {
            key: 'autofix',
            label: 'Autofix',
            children: (
                <>
                    <p className='qc3d-cell-secondary'>
                        Autofix chỉ sửa trực tiếp nhãn đang có (không tạo box mới) và chỉ ghi bản sửa đã tự qua lại
                        QC theo guideline này. Tắt loại sửa hoặc loại trừ class để chuyển chúng sang QC duyệt tay.
                    </p>
                    <Row gutter={[16, 12]}>
                        {(meta.autofix_kinds ?? []).map((kind) => (
                            <Col xs={24} md={12} key={kind.key}>
                                <div className='qc3d-rule-option'>
                                    <Switch
                                        size='small'
                                        checked={content.autofix?.fix?.[kind.key] !== false}
                                        aria-label={`Autofix: ${kind.title}`}
                                        disabled={readOnly}
                                        onChange={(checked) => setContent((c) => ({
                                            ...c,
                                            autofix: {
                                                exclude_classes: c.autofix?.exclude_classes ?? [],
                                                fix: { ...(c.autofix?.fix ?? {}), [kind.key]: checked },
                                            },
                                        }))}
                                    />
                                    <span>{kind.title}</span>
                                </div>
                            </Col>
                        ))}
                    </Row>
                    <label className='qc3d-form-label' htmlFor='qc3d-autofix-exclude' style={{ marginTop: 16 }}>
                        Class Autofix không được sửa
                    </label>
                    <Select
                        id='qc3d-autofix-exclude'
                        mode='multiple'
                        allowClear
                        style={{ width: '100%' }}
                        disabled={readOnly}
                        placeholder='Chọn class cần QC kiểm tra tay'
                        value={content.autofix?.exclude_classes ?? []}
                        options={content.classes.filter((cls) => cls.name).map((cls) => ({
                            value: cls.name, label: cls.name_vi ? `${cls.name} · ${cls.name_vi}` : cls.name,
                        }))}
                        onChange={(names: string[]) => setContent((c) => ({
                            ...c,
                            autofix: { exclude_classes: names, fix: c.autofix?.fix ?? {} },
                        }))}
                    />
                </>
            ),
        },
        {
            key: 'json',
            label: 'JSON nâng cao',
            children: (
                <Space direction='vertical' style={{ width: '100%' }}>
                    <Alert type='info' showIcon message='Chỉnh cấu trúc đầy đủ bằng JSON' description='Bấm “Dùng JSON này” để kiểm tra và chuyển nội dung vào biểu mẫu, sau đó lưu hướng dẫn. Thay đổi JSON chưa được áp dụng sẽ chặn nút Lưu.' />
                    {jsonError && <Alert type='error' showIcon message='JSON chưa hợp lệ' description={jsonError} />}
                    <Input.TextArea
                        rows={18}
                        style={{ fontFamily: 'monospace' }}
                        value={json ?? JSON.stringify(content, null, 2)}
                        aria-label='Nội dung cấu hình hướng dẫn dạng JSON'
                        disabled={readOnly}
                        onChange={(e) => { setJson(e.target.value); setJsonError(null); }}
                    />
                    {!readOnly && (
                        <Space>
                            <Button
                                disabled={json === null}
                                onClick={() => {
                                    try {
                                        const nextContent = parseContent(json ?? '');
                                        setForm((prev) => prev && { ...prev, content: nextContent });
                                        setJson(null);
                                        setJsonError(null);
                                        notification.success({ message: 'Đã áp dụng JSON vào biểu mẫu. Bấm Lưu để cập nhật hướng dẫn.' });
                                    } catch (err: any) {
                                        setJsonError(err.message);
                                    }
                                }}
                            >
                                Dùng JSON này
                            </Button>
                            <Button disabled={json === null} onClick={() => { setJson(null); setJsonError(null); }}>
                                Bỏ thay đổi JSON
                            </Button>
                        </Space>
                    )}
                </Space>
            ),
        },
    ];

    return (
        <div className='qc3d-guide-editor'>
            <Prompt when={dirty && !saving} message='Bạn có thay đổi chưa lưu. Rời trang và bỏ các thay đổi này?' />
            <div className='qc3d-page-header'>
                <div>
                    <h2>{isNew ? 'Tạo hướng dẫn kiểm tra' : form.name}</h2>
                    <p>{isNew ? 'Thiết lập nhãn và quy tắc QC trước khi áp dụng.' : `Phiên bản ${revision} · ${dirty ? 'Có thay đổi chưa lưu' : 'Đã lưu'}`}</p>
                </div>
                <div className='qc3d-guide-actions'>
                    <Button icon={<ArrowLeftOutlined />} disabled={saving || importing} onClick={() => history.push('/qc3d/guidelines')}>Danh sách</Button>
                    {meta.can_manage && (
                        <>
                            <Upload accept={IMPORT_ACCEPT} disabled={saving || importing} showUploadList={false}
                                beforeUpload={(file) => { onReplaceFromFile(file); return false; }}
                            >
                                <Button icon={<UploadOutlined />} loading={importing} disabled={saving}>
                                    Nhập thay nội dung
                                </Button>
                            </Upload>
                            {!isNew && <Button danger disabled={saving || importing} onClick={onDelete}>Xóa</Button>}
                            <Button type='primary' icon={<SaveOutlined />} loading={saving} disabled={!form.name.trim() || !content.classes.length || json !== null || importing} onClick={onSave}>Lưu hướng dẫn</Button>
                        </>
                    )}
                </div>
            </div>
            <Card>
                {!meta.can_manage && <Alert type='info' showIcon message='Chế độ chỉ đọc. Cần quyền lead / QA hoặc quản trị viên để chỉnh sửa.' style={{ marginBottom: 16 }} />}
                {json !== null && <Alert type='warning' showIcon message='Có bản JSON chưa áp dụng' description='Mở mục JSON nâng cao, chọn “Dùng JSON này” hoặc “Bỏ thay đổi JSON” trước khi lưu.' style={{ marginBottom: 16 }} />}
                {compiled.length > 0 && (
                    <Alert
                        type='success'
                        showIcon
                        closable
                        onClose={() => setCompiled([])}
                        style={{ marginBottom: 16 }}
                        message={`Đã hiểu ${compiled.length} câu quy định`}
                        description={<ul style={{ margin: 0 }}>{compiled.map((line) => <li key={line}>{line}</li>)}</ul>}
                    />
                )}
                {warnings.length > 0 && (
                    <Alert
                        type='warning'
                        showIcon
                        style={{ marginBottom: 16 }}
                        message='Cần xem lại'
                        description={<ul style={{ margin: 0 }}>{warnings.map((w) => <li key={w}>{w}</li>)}</ul>}
                    />
                )}
                <Row gutter={[16, 16]} className='qc3d-guide-settings'>
                    <Col xs={24} md={8}>
                        <label className='qc3d-form-label' htmlFor='qc3d-guide-name'>Tên hướng dẫn *</label>
                        <Input id='qc3d-guide-name' value={form.name} disabled={readOnly} onChange={(e) => setForm({ ...form, name: e.target.value })} />
                    </Col>
                    <Col xs={24} md={16}>
                        <label className='qc3d-form-label' htmlFor='qc3d-guide-description'>Mô tả</label>
                        <Input
                            id='qc3d-guide-description'
                            placeholder='Phạm vi dữ liệu hoặc yêu cầu gán nhãn'
                            value={form.description}
                            disabled={readOnly}
                            onChange={(e) => setForm({ ...form, description: e.target.value })}
                        />
                    </Col>
                    <Col xs={24} lg={10}>
                        <label className='qc3d-form-label' htmlFor='qc3d-guide-projects'>Áp dụng cho project</label>
                        <Select
                            id='qc3d-guide-projects'
                            placeholder='Chọn project 3D'
                            maxTagCount='responsive'
                            mode='multiple'
                            style={{ width: '100%' }}
                            disabled={readOnly}
                            optionFilterProp='label'
                            value={form.projects}
                            onChange={(projects) => setForm({ ...form, projects })}
                            options={meta.targets.projects.map((p) => ({ value: p.id, label: `#${p.id} ${p.name}` }))}
                        />
                    </Col>
                    <Col xs={24} lg={10}>
                        <label className='qc3d-form-label' htmlFor='qc3d-guide-tasks'>Áp dụng cho task · ưu tiên hơn project</label>
                        <Select
                            id='qc3d-guide-tasks'
                            placeholder='Chọn task 3D'
                            maxTagCount='responsive'
                            mode='multiple'
                            style={{ width: '100%' }}
                            disabled={readOnly}
                            optionFilterProp='label'
                            value={form.tasks}
                            onChange={(tasks) => setForm({ ...form, tasks })}
                            options={meta.targets.tasks.map((t) => ({ value: t.id, label: `#${t.id} ${t.name}` }))}
                        />
                    </Col>
                    <Col xs={24} lg={4}>
                        <label className='qc3d-form-label' htmlFor='qc3d-guide-default'>Dùng làm mặc định</label>
                        <Switch id='qc3d-guide-default' checked={form.is_default} disabled={readOnly} onChange={(checked) => setForm({ ...form, is_default: checked })} />
                        <div className='qc3d-cell-secondary'>Áp dụng khi job chưa có hướng dẫn riêng.</div>
                    </Col>
                </Row>
                <Tabs items={tabs} />
            </Card>
        </div>
    );
}
