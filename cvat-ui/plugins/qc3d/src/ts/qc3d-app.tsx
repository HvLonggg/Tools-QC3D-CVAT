// Copyright (C) CVAT.ai Corporation
//
// SPDX-License-Identifier: MIT

import React from 'react';
import {
    Route, Switch, useHistory, useLocation,
} from 'react-router-dom';
import Menu from 'antd/lib/menu';

import DashboardPage, { ReviewPage } from './pages/dashboard';
import JobPage from './pages/job-page';
import AdminPage from './pages/admin';
import PlannedPage from './pages/planned';

const SECTIONS = [
    { key: '/qc3d', label: 'Tổng quan' },
    { key: '/qc3d/review', label: 'Review' },
    { key: '/qc3d/qa', label: 'Nghiệm thu QA' },
    { key: '/qc3d/admin', label: 'Quản trị' },
];

export default function Qc3dApp(): JSX.Element {
    const history = useHistory();
    const { pathname } = useLocation();
    const selected = [...SECTIONS].reverse().find(({ key }) => pathname.startsWith(key))?.key ?? '/qc3d';

    return (
        <div className='cvat-qc3d-page' style={{ padding: 24, height: '100%', overflow: 'auto' }}>
            <Menu
                mode='horizontal'
                selectedKeys={[selected]}
                items={SECTIONS}
                onClick={({ key }) => history.push(key)}
                style={{ marginBottom: 16 }}
            />
            <Switch>
                <Route exact path='/qc3d' component={DashboardPage} />
                <Route exact path='/qc3d/jobs/:jobId' component={JobPage} />
                <Route exact path='/qc3d/review' component={ReviewPage} />
                <Route exact path='/qc3d/qa'>
                    <PlannedPage
                        title='Nghiệm thu QA'
                        description='Lấy mẫu 299 đối tượng, kết luận Clopper–Pearson (Sprint 3)'
                    />
                </Route>
                <Route exact path='/qc3d/admin' component={AdminPage} />
            </Switch>
        </div>
    );
}
