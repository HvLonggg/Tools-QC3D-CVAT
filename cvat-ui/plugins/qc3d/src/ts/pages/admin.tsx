// Copyright (C) CVAT.ai Corporation
//
// SPDX-License-Identifier: MIT

import React from 'react';
import { ExportOutlined, ReloadOutlined } from '@ant-design/icons';
import Alert from 'antd/lib/alert';
import Button from 'antd/lib/button';
import Card from 'antd/lib/card';
import { Col, Row } from 'antd/lib/grid';
import Spin from 'antd/lib/spin';
import Tag from 'antd/lib/tag';

import useSummary from '../use-summary';

const ROLES = [
    { key: 'lead', title: 'Trưởng nhóm', description: 'Chạy QC, duyệt lỗi, sửa lỗi và quản lý guideline.' },
    { key: 'reviewer', title: 'Reviewer', description: 'Chạy QC, duyệt lỗi và áp dụng hoặc hoàn tác bản sửa.' },
    { key: 'qa', title: 'QA', description: 'Chạy QC, duyệt lỗi và quản lý guideline.' },
    { key: 'ml_engineer', title: 'Kỹ sư ML', description: 'Xem báo cáo và dữ liệu QC của các job được cấp quyền.' },
];

export default function AdminPage(): JSX.Element {
    const {
        summary, error, loading, refresh,
    } = useSummary();

    if (loading && !summary) {
        return (
            <div className='qc3d-loading-state' role='status'>
                <Spin size='large' /><span>Đang tải quyền QC 3D…</span>
            </div>
        );
    }
    if (!summary) {
        return (
            <Alert type='error' showIcon message='Không tải được quyền QC 3D' description={error}
                action={<Button onClick={refresh} loading={loading}>Thử lại</Button>}
            />
        );
    }

    return (
        <div className='qc3d-admin'>
            <div className='qc3d-page-header'>
                <div><h2>Quản trị & phân quyền</h2><p>Hiểu quyền thao tác và quản lý vai trò của nhóm QC 3D.</p></div>
                <Button icon={<ReloadOutlined />} onClick={refresh} loading={loading}>Làm mới</Button>
            </div>
            {error && <Alert type='warning' showIcon message='Chưa cập nhật được quyền' description={error} />}
            <Card className='qc3d-table-card' title='Quyền của bạn'>
                <div className='qc3d-role-summary'>
                    {summary.is_admin && <Tag color='blue'>Quản trị viên CVAT</Tag>}
                    {summary.roles.length ? summary.roles.map((role) => (
                        <Tag key={role} color='cyan'>{ROLES.find((item) => item.key === role)?.title ?? role}</Tag>
                    )) : <Tag>Chưa được gán vai trò QC</Tag>}
                </div>
                {summary.is_admin ? (
                    <>
                        <p>Bạn có đầy đủ quyền QC 3D. Mở trang quản lý để gán vai trò cho thành viên trong nhóm.</p>
                        <Button type='primary' icon={<ExportOutlined />} href='/admin/qc3d/qcmembership/'
                            target='_blank' rel='noopener noreferrer'
                        >
                            Quản lý thành viên QC
                        </Button>
                    </>
                ) : (
                    <Alert type='info' showIcon
                        message='Quản trị viên CVAT có thể gán hoặc thay đổi vai trò QC của bạn.'
                    />
                )}
            </Card>
            <Card className='qc3d-table-card' title='Vai trò & phạm vi thao tác'>
                <Row className='qc3d-role-grid' gutter={[16, 16]}>
                    {ROLES.map(({ key, title, description }) => (
                        <Col xs={24} sm={12} xl={6} key={key}>
                            <div className='qc3d-role-card'>
                                <h3>{title}</h3>
                                <Tag>{key}</Tag>
                                <p>{description}</p>
                            </div>
                        </Col>
                    ))}
                </Row>
                <p className='qc3d-cell-secondary'>
                    Vai trò QC áp dụng trong phạm vi task và job bạn có quyền truy cập trên CVAT.
                </p>
            </Card>
        </div>
    );
}
