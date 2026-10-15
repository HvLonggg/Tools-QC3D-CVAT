// Copyright (C) CVAT.ai Corporation
//
// SPDX-License-Identifier: MIT

import React from 'react';
import { useHistory } from 'react-router-dom';
import { ClockCircleOutlined } from '@ant-design/icons';
import Button from 'antd/lib/button';
import Card from 'antd/lib/card';
import Empty from 'antd/lib/empty';
import Tag from 'antd/lib/tag';

interface Props {
    title: string;
    description: string;
}

export default function PlannedPage({ title, description }: Props): JSX.Element {
    const history = useHistory();

    return (
        <div>
            <div className='qc3d-page-header'>
                <div><h2>{title}</h2><p>Không gian nghiệm thu và đánh giá chất lượng dữ liệu 3D.</p></div>
                <Tag color='blue'>Đang phát triển</Tag>
            </div>
            <Card className='qc3d-empty-card'>
                <Empty image={<ClockCircleOutlined className='qc3d-empty-icon' />} description={(
                    <div>
                        <h3>Tính năng đang được hoàn thiện</h3>
                        <p>{description}</p>
                        <p>Bạn có thể tiếp tục kiểm tra và xử lý lỗi trong hàng đợi review.</p>
                    </div>
                )}
                >
                    <Button type='primary' onClick={() => history.push('/qc3d/review')}>Mở hàng đợi review</Button>
                </Empty>
            </Card>
        </div>
    );
}
