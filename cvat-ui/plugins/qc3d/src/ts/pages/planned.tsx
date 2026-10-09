// Copyright (C) CVAT.ai Corporation
//
// SPDX-License-Identifier: MIT

import React from 'react';
import Card from 'antd/lib/card';
import Empty from 'antd/lib/empty';

interface Props {
    title: string;
    description: string;
}

export default function PlannedPage({ title, description }: Props): JSX.Element {
    return (
        <Card title={title}>
            <Empty description={description} />
        </Card>
    );
}
