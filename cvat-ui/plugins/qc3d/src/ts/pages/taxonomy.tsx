// Copyright (C) CVAT.ai Corporation
//
// SPDX-License-Identifier: MIT

import React, { useEffect, useState } from 'react';
import { ReloadOutlined, SearchOutlined } from '@ant-design/icons';
import Alert from 'antd/lib/alert';
import Button from 'antd/lib/button';
import Card from 'antd/lib/card';
import Empty from 'antd/lib/empty';
import Input from 'antd/lib/input';
import Spin from 'antd/lib/spin';
import Table from 'antd/lib/table';
import Tag from 'antd/lib/tag';

import { errorText, getTaxonomy, TaxonomyClass } from '../api';

const numberFormat = new Intl.NumberFormat('vi-VN', { maximumFractionDigits: 2 });
const range = (lo: number, hi: number): string => `${numberFormat.format(lo)} – ${numberFormat.format(hi)}`;

function normalizeSearch(value: string): string {
    return value.toLocaleLowerCase('vi-VN').normalize('NFD').replace(/[\u0300-\u036f]/g, '').replace(/đ/g, 'd');
}

export default function TaxonomyPage(): JSX.Element {
    const [data, setData] = useState<{ version: string; classes: TaxonomyClass[] } | null>(null);
    const [error, setError] = useState<string | null>(null);
    const [loading, setLoading] = useState(true);
    const [requestVersion, setRequestVersion] = useState(0);
    const [search, setSearch] = useState('');
    const refresh = (): void => setRequestVersion((version) => version + 1);

    useEffect(() => {
        let cancelled = false;
        setLoading(true);
        setError(null);
        getTaxonomy()
            .then((result) => { if (!cancelled) setData(result); })
            .catch((err: unknown) => { if (!cancelled) setError(errorText(err)); })
            .finally(() => { if (!cancelled) setLoading(false); });
        return () => { cancelled = true; };
    }, [requestVersion]);

    if (loading && !data) {
        return (
            <div className='qc3d-loading-state' role='status'>
                <Spin size='large' /><span>Đang tải bộ class…</span>
            </div>
        );
    }
    if (!data) {
        return (
            <Alert type='error' showIcon message='Không tải được bộ class' description={error}
                action={<Button onClick={refresh} loading={loading}>Thử lại</Button>}
            />
        );
    }

    const query = normalizeSearch(search.trim());
    const filteredClasses = data.classes.filter((item) => (
        normalizeSearch(`${item.name} ${item.name_vi} ${item.description}`).includes(query)
    ));

    const columns = [
        {
            title: 'Class',
            dataIndex: 'name',
            width: 170,
            sorter: (a: TaxonomyClass, b: TaxonomyClass) => a.name.localeCompare(b.name),
            render: (name: string, item: TaxonomyClass) => (
                <div><div className='qc3d-cell-primary'>{item.name_vi}</div><Tag>{name}</Tag></div>
            ),
        },
        { title: 'Đặc điểm nhận diện', dataIndex: 'description', width: 300 },
        {
            title: 'Khoảng kích thước (m)',
            children: [
                { title: 'Dài', render: (c: TaxonomyClass) => range(c.size_min[0], c.size_max[0]), width: 110 },
                { title: 'Rộng', render: (c: TaxonomyClass) => range(c.size_min[1], c.size_max[1]), width: 110 },
                { title: 'Cao', render: (c: TaxonomyClass) => range(c.size_min[2], c.size_max[2]), width: 110 },
            ],
        },
        { title: 'Điểm ở 10 m', dataIndex: 'points_at_10m', width: 100 },
        { title: 'Tốc độ tối đa (m/s)', dataIndex: 'max_speed', width: 140 },
    ];

    return (
        <div>
            <div className='qc3d-page-header'>
                <div>
                    <h2>Bộ class giao thông Việt Nam</h2>
                    <p>Tra cứu class, đặc điểm nhận diện và khoảng kích thước tham khảo.</p>
                </div>
                <Button icon={<ReloadOutlined />} onClick={refresh} loading={loading}>Làm mới</Button>
            </div>
            {error && <Alert type='warning' showIcon message='Chưa cập nhật được bộ class' description={error} />}
            <Card className='qc3d-table-card' title='Thông số tham khảo' extra={<Tag color='blue'>{data.version}</Tag>}>
                <Alert className='qc3d-taxonomy-note'
                    type='info'
                    showIcon
                    message='Kích thước tham khảo cho QC 3D'
                    description={
                        'Rule R06 phát hiện kích thước ngoài khoảng; R11 gợi ý class cần kiểm tra. ' +
                        'QA xác nhận kết quả theo guideline áp dụng cho job.'
                    }
                />
                <div className='qc3d-table-toolbar'>
                    <Input className='qc3d-search-input' prefix={<SearchOutlined />} allowClear
                        placeholder='Tìm class, tên tiếng Việt, đặc điểm…' aria-label='Tìm kiếm class'
                        value={search} onChange={(event) => setSearch(event.target.value)}
                    />
                    <span className='qc3d-table-meta' aria-live='polite'>
                        {`${filteredClasses.length} / ${data.classes.length} class`}
                    </span>
                </div>
                <Table<TaxonomyClass>
                    rowKey='name'
                    size='middle'
                    columns={columns}
                    dataSource={filteredClasses}
                    pagination={false}
                    scroll={{ x: 1100 }}
                    locale={{
                        emptyText: (
                            <Empty image={Empty.PRESENTED_IMAGE_SIMPLE} description='Không tìm thấy class phù hợp.'>
                                {query && <Button onClick={() => setSearch('')}>Xóa tìm kiếm</Button>}
                            </Empty>
                        ),
                    }}
                />
            </Card>
        </div>
    );
}
