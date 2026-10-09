// Copyright (C) CVAT.ai Corporation
//
// SPDX-License-Identifier: MIT

import React from 'react';
import Alert from 'antd/lib/alert';
import Card from 'antd/lib/card';
import Spin from 'antd/lib/spin';
import Tag from 'antd/lib/tag';

import useSummary from '../use-summary';

export default function AdminPage(): JSX.Element {
    const { summary, error, loading } = useSummary();

    if (loading) return <Spin size='large' />;
    if (error || !summary) {
        return <Alert type='error' message='Không tải được dữ liệu QC 3D' description={error} />;
    }

    return (
        <Card title='Quản trị QC 3D'>
            <p>
                {'Vai trò của bạn: '}
                {summary.roles.length ? summary.roles.map((role) => <Tag key={role}>{role}</Tag>) : 'chưa được gán'}
            </p>
            {summary.is_admin ? (
                <p>
                    {'Gán vai trò lead / reviewer / qa / ml_engineer tại '}
                    <a href='/admin/qc3d/qcmembership/' target='_blank' rel='noopener noreferrer'>
                        Django admin, mục Qc memberships
                    </a>
                    {'. Phân việc, tiến độ và nhật ký kiểm toán có ở Sprint 3.'}
                </p>
            ) : (
                <Alert type='info' message='Chỉ admin CVAT mới gán được vai trò QC.' />
            )}
        </Card>
    );
}
