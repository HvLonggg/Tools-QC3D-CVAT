// Copyright (C) CVAT.ai Corporation
//
// SPDX-License-Identifier: MIT

import React, {
    useCallback, useEffect, useMemo, useState,
} from 'react';
import { Link, useHistory } from 'react-router-dom';
import {
    FileTextOutlined, PlusOutlined, ReloadOutlined, SearchOutlined, UploadOutlined,
} from '@ant-design/icons';
import Alert from 'antd/lib/alert';
import Button from 'antd/lib/button';
import Card from 'antd/lib/card';
import Input from 'antd/lib/input';
import Modal from 'antd/lib/modal';
import Select from 'antd/lib/select';
import Space from 'antd/lib/space';
import Spin from 'antd/lib/spin';
import Table from 'antd/lib/table';
import Tag from 'antd/lib/tag';
import Upload from 'antd/lib/upload';
import notification from 'antd/lib/notification';

import {
    errorText, getGuidelines, GuidelineList, GuidelineSummary, parseCvatGuide, parseGuidelineFile, ParsedGuideline,
} from '../api';

export const IMPORT_ACCEPT = '.json,.yaml,.yml,.csv,.md,.markdown,.txt,.docx';

function readAsBase64(file: File): Promise<string> {
    return new Promise((resolve, reject) => {
        const reader = new FileReader();
        reader.onload = () => resolve(String(reader.result).split(',', 2)[1] ?? '');
        reader.onerror = () => reject(reader.error);
        reader.readAsDataURL(file);
    });
}

export async function importGuidelineFile(file: File): Promise<ParsedGuideline> {
    return parseGuidelineFile(file.name, await readAsBase64(file));
}

export default function GuidelinesPage(): JSX.Element {
    const history = useHistory();
    const [data, setData] = useState<GuidelineList | null>(null);
    const [error, setError] = useState<string | null>(null);
    const [importing, setImporting] = useState(false);
    const [cvatGuideOpen, setCvatGuideOpen] = useState(false);
    const [cvatTarget, setCvatTarget] = useState<string | null>(null);
    const [loading, setLoading] = useState(true);
    const [search, setSearch] = useState('');

    const load = useCallback(async (): Promise<void> => {
        setLoading(true);
        setError(null);
        try {
            setData(await getGuidelines());
        } catch (err: any) {
            setError(errorText(err));
        } finally {
            setLoading(false);
        }
    }, []);
    useEffect(() => { load(); }, [load]);

    const results = useMemo(() => {
        const normalize = (value: string): string => value.normalize('NFD').replace(/[\u0300-\u036f]/g, '')
            .toLocaleLowerCase().replace(/đ/g, 'd');
        const query = normalize(search.trim());
        return (data?.results ?? []).filter((guide) => !query || normalize([
            guide.name, guide.description, guide.source_name, ...guide.classes,
            ...guide.tasks.map((target) => target.name), ...guide.projects.map((target) => target.name),
        ].join(' ')).includes(query));
    }, [data, search]);

    const openEditor = useCallback((parsed: ParsedGuideline) => {
        history.push('/qc3d/guidelines/new', { parsed });
    }, [history]);

    const onImportFile = useCallback(async (file: File) => {
        setImporting(true);
        try {
            openEditor(await importGuidelineFile(file));
        } catch (err: any) {
            notification.error({ message: `Không đọc được ${file.name}`, description: errorText(err), duration: 10 });
        } finally {
            setImporting(false);
        }
    }, [openEditor]);

    const onImportCvatGuide = useCallback(async () => {
        if (!cvatTarget) return;
        const [kind, id] = cvatTarget.split(':');
        setImporting(true);
        try {
            openEditor(await parseCvatGuide(kind === 'task' ? { task_id: +id } : { project_id: +id }));
        } catch (err: any) {
            notification.error({ message: 'Không đọc được guide CVAT', description: errorText(err), duration: 10 });
        } finally {
            setImporting(false);
        }
    }, [cvatTarget, openEditor]);

    if (!data && error) {
        return (
            <Space direction='vertical' style={{ width: '100%' }}>
                <Alert type='error' showIcon message='Không tải được danh sách hướng dẫn' description={error} />
                <Button icon={<ReloadOutlined />} loading={loading} onClick={load}>Thử lại</Button>
            </Space>
        );
    }
    if (!data) return <div className='qc3d-loading-state'><Spin size='large' />Đang tải hướng dẫn kiểm tra…</div>;

    const columns = [
        {
            title: 'Hướng dẫn kiểm tra',
            width: 260,
            render: (g: GuidelineSummary) => (
                <Space direction='vertical' size={0}>
                    <Link to={`/qc3d/guidelines/${g.id}`}>{g.name}</Link>
                    {g.description && <span className='qc3d-cell-secondary'>{g.description}</span>}
                </Space>
            ),
        },
        { title: 'Bản', dataIndex: 'revision', width: 60 },
        {
            title: 'Nhóm nhãn',
            width: 230,
            dataIndex: 'classes',
            render: (classes: string[]) => classes.map((name) => <Tag key={name}>{name}</Tag>),
        },
        {
            title: 'Áp dụng cho',
            width: 260,
            render: (g: GuidelineSummary) => (
                <Space wrap size={4}>
                    {g.is_default && <Tag color='gold'>Mặc định</Tag>}
                    {g.projects.map((p) => <Tag key={`p${p.id}`} color='blue'>{`Project: ${p.name}`}</Tag>)}
                    {g.tasks.map((t) => <Tag key={`t${t.id}`} color='purple'>{`Task: ${t.name}`}</Tag>)}
                    {g.hidden_bindings > 0 && <Tag>{`+${g.hidden_bindings} mục khác`}</Tag>}
                    {!g.is_default && !g.projects.length && !g.tasks.length && !g.hidden_bindings && (
                        <span style={{ color: '#999' }}>chưa gán</span>
                    )}
                </Space>
            ),
        },
        { title: 'Nguồn', dataIndex: 'source_name', width: 160 },
        {
            title: 'Cập nhật',
            width: 170,
            render: (g: GuidelineSummary) => `${new Date(g.updated_date).toLocaleString()} · ${g.updated_by ?? ''}`,
        },
    ];

    return (
        <div className='qc3d-guidelines'>
            <div className='qc3d-page-header'>
                <div>
                    <h2>Hướng dẫn kiểm tra</h2>
                    <p>Quản lý nhóm nhãn, kích thước và quy tắc QC theo từng task hoặc project.</p>
                </div>
                <div className='qc3d-page-actions'>
                    <Button icon={<ReloadOutlined />} loading={loading} onClick={load}>Làm mới</Button>
                    {data.can_manage && (
                        <>
                            <Upload
                                accept={IMPORT_ACCEPT}
                                showUploadList={false}
                                disabled={importing}
                                beforeUpload={(file) => { onImportFile(file); return false; }}
                            >
                                <Button icon={<UploadOutlined />} loading={importing}>Nhập từ file</Button>
                            </Upload>
                            <Button icon={<FileTextOutlined />} disabled={importing}
                                onClick={() => setCvatGuideOpen(true)}
                            >
                                Đọc guide CVAT
                            </Button>
                            <Button type='primary' icon={<PlusOutlined />} disabled={importing} onClick={() => history.push('/qc3d/guidelines/new')}>Tạo hướng dẫn</Button>
                        </>
                    )}
                </div>
            </div>
            {error && <Alert type='warning' showIcon message='Không làm mới được danh sách' description={error} />}
            <Card className='qc3d-table-card'>
                <div className='qc3d-table-toolbar'>
                    <Input
                        className='qc3d-search-input'
                        prefix={<SearchOutlined />}
                        allowClear
                        placeholder='Tìm tên hướng dẫn, nhãn, task hoặc project'
                        aria-label='Tìm hướng dẫn kiểm tra'
                        value={search}
                        onChange={(event) => setSearch(event.target.value)}
                    />
                </div>
                <div className='qc3d-table-meta'>{`${results.length} / ${data.results.length} hướng dẫn · Nhập file JSON, YAML, CSV, Markdown, TXT hoặc Word (.docx).`}</div>
                {!data.can_manage && (
                    <Alert type='info' showIcon style={{ marginBottom: 16 }} message='Chế độ chỉ đọc. Lead / QA hoặc quản trị viên có thể chỉnh sửa hướng dẫn.' />
                )}
                <Table<GuidelineSummary>
                    rowKey='id'
                    size='small'
                    columns={columns}
                    dataSource={results}
                    loading={loading}
                    scroll={{ x: 1180 }}
                    pagination={{
                        defaultPageSize: 15, showSizeChanger: true, showTotal: (total) => `${total} hướng dẫn`,
                    }}
                    locale={{ emptyText: search ? 'Không có hướng dẫn phù hợp tìm kiếm.' : 'Chưa có hướng dẫn. QC đang dùng bộ class VN có sẵn.' }}
                />
                <details className='qc3d-guide-help'>
                    <summary>Cách áp dụng hướng dẫn và định dạng nhập</summary>
                    <Alert
                        type='info'
                        showIcon
                        style={{ marginTop: 12 }}
                        message='Guideline quyết định QC kiểm gì: class và kích thước (R06, R11), ngưỡng của từng rule, rule bật/tắt.'
                        description={(
                            <>
                                <div>
                                    {'Thứ tự áp dụng cho một job: guideline gán cho task → guide CVAT của task → '}
                                    {'guideline gán cho project → guide CVAT của project → guideline mặc định → bộ class VN dựng sẵn.'}
                                </div>
                                <div>
                                    {'Guide CVAT (nút "Edit guide" của task/project) được QC tự đọc nếu có bảng: '}
                                    <code>| Class | Dài (m) | Rộng (m) | Cao (m) |</code>
                                    {' với khoảng dạng 2,5–6,2; ngưỡng viết dạng '}
                                    <code>overlap_iou: 0,25</code>
                                    {', tắt rule: '}
                                    <code>R08: tắt</code>
                                    .
                                </div>
                                <div>Import: JSON, YAML, CSV, Markdown, TXT, Word (.docx, đọc bảng trong file).</div>
                            </>
                        )}
                    />
                </details>
            </Card>
            <Modal
                open={cvatGuideOpen}
                title='Lấy guideline từ guide CVAT'
                okText='Đọc guide'
                cancelText='Hủy'
                okButtonProps={{ disabled: !cvatTarget, loading: importing }}
                onCancel={() => setCvatGuideOpen(false)}
                onOk={onImportCvatGuide}
            >
                <p>Chọn task hoặc project 3D có guide (viết bằng nút &quot;Edit guide&quot; trên CVAT):</p>
                <Select
                    showSearch
                    optionFilterProp='label'
                    style={{ width: '100%' }}
                    placeholder='Task / project'
                    aria-label='Chọn task hoặc project có guide CVAT'
                    value={cvatTarget ?? undefined}
                    onChange={setCvatTarget}
                    options={[
                        {
                            label: 'Project',
                            options: data.targets.projects.map((p) => ({
                                value: `project:${p.id}`, label: `#${p.id} ${p.name}`,
                            })),
                        },
                        {
                            label: 'Task',
                            options: data.targets.tasks.map((t) => ({
                                value: `task:${t.id}`, label: `#${t.id} ${t.name}`,
                            })),
                        },
                    ]}
                />
            </Modal>
        </div>
    );
}
