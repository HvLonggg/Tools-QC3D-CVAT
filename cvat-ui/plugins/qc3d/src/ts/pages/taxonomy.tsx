// Copyright (C) CVAT.ai Corporation
//
// SPDX-License-Identifier: MIT

import React, { useEffect, useState } from 'react';
import Alert from 'antd/lib/alert';
import Card from 'antd/lib/card';
import Spin from 'antd/lib/spin';
import Table from 'antd/lib/table';

import { getTaxonomy, TaxonomyClass } from '../api';

const range = (lo: number, hi: number): string => `${lo}–${hi}`;

export default function TaxonomyPage(): JSX.Element {
    const [data, setData] = useState<{ version: string; classes: TaxonomyClass[] } | null>(null);
    const [error, setError] = useState<string | null>(null);

    useEffect(() => {
        getTaxonomy().then(setData).catch((err: Error) => setError(err.message));
    }, []);

    if (error) return <Alert type='error' message='Không tải được bộ class' description={error} />;
    if (!data) return <Spin size='large' />;

    const columns = [
        { title: 'Class', dataIndex: 'name', width: 110 },
        { title: 'Tên', dataIndex: 'name_vi', width: 170 },
        { title: 'Bao gồm', dataIndex: 'description' },
        { title: 'Dài (m)', render: (c: TaxonomyClass) => range(c.size_min[0], c.size_max[0]), width: 90 },
        { title: 'Rộng (m)', render: (c: TaxonomyClass) => range(c.size_min[1], c.size_max[1]), width: 90 },
        { title: 'Cao (m)', render: (c: TaxonomyClass) => range(c.size_min[2], c.size_max[2]), width: 90 },
        { title: 'Điểm ở 10 m', dataIndex: 'points_at_10m', width: 100 },
        { title: 'Tốc độ tối đa (m/s)', dataIndex: 'max_speed', width: 140 },
    ];

    return (
        <Card title={`Bộ class giao thông Việt Nam · ${data.version}`}>
            <Alert
                type='info'
                showIcon
                style={{ marginBottom: 16 }}
                message='Kích thước dùng cho rule R06 (ngoài khoảng) và R11 (nghi sai class). Đề xuất, chờ QA xác nhận theo guideline.'
            />
            <Table<TaxonomyClass>
                rowKey='name'
                size='small'
                columns={columns}
                dataSource={data.classes}
                pagination={false}
            />
        </Card>
    );
}
