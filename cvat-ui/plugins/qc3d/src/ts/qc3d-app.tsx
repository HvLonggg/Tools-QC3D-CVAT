// Copyright (C) CVAT.ai Corporation
//
// SPDX-License-Identifier: MIT

import React, { useState } from 'react';
import {
    Link, Route, Switch, useHistory, useLocation,
} from 'react-router-dom';
import {
    AppstoreOutlined, ArrowLeftOutlined, AuditOutlined, CheckSquareOutlined, FileTextOutlined, ProjectOutlined,
    QuestionCircleOutlined, SafetyCertificateOutlined, TagsOutlined, TeamOutlined, TrophyOutlined,
} from '@ant-design/icons';
import Breadcrumb from 'antd/lib/breadcrumb';
import Button from 'antd/lib/button';
import ConfigProvider from 'antd/lib/config-provider';
import Menu from 'antd/lib/menu';
import Modal from 'antd/lib/modal';

import DashboardPage from './pages/dashboard';
import ReviewPage from './pages/review';
import BoardPage from './pages/board';
import AcceptancePage from './pages/acceptance';
import JobPage from './pages/job-page';
import AdminPage from './pages/admin';
import TaxonomyPage from './pages/taxonomy';
import GuidelinesPage from './pages/guidelines';
import GuidelineEditorPage from './pages/guideline-editor';
import MyWorkPage from './pages/my-work';
import useSummary from './use-summary';
import './styles.scss';

// `team`: only for people with a QC role (lead / reviewer / QA) or CVAT admins; data is also enforced by the API
const SECTIONS = [
    { key: '/qc3d', label: 'Tổng quan', icon: <AppstoreOutlined />, team: false },
    { key: '/qc3d/my', label: 'Chất lượng của tôi', icon: <TrophyOutlined />, team: false },
    { key: '/qc3d/board', label: 'Trạng thái job', icon: <ProjectOutlined />, team: false },
    { key: '/qc3d/review', label: 'Hàng đợi review', icon: <AuditOutlined />, team: true },
    { key: '/qc3d/qa', label: 'Nghiệm thu QA', icon: <CheckSquareOutlined />, team: true },
    { key: '/qc3d/guidelines', label: 'Guideline QC', icon: <FileTextOutlined />, team: false },
    { key: '/qc3d/classes', label: 'Bộ class Việt Nam', icon: <TagsOutlined />, team: false },
    { key: '/qc3d/admin', label: 'Quản trị & quyền', icon: <TeamOutlined />, team: true },
];

export default function Qc3dApp(): JSX.Element {
    const history = useHistory();
    const { pathname } = useLocation();
    const [helpOpen, setHelpOpen] = useState(false);
    const { summary } = useSummary();
    const isTeam = !!summary && (summary.is_admin || summary.roles.length > 0);
    const sections = SECTIONS.filter((item) => !item.team || isTeam)
        .map(({ key, label, icon }) => ({ key, label, icon }));
    const isJob = pathname.startsWith('/qc3d/jobs/');
    const selected = isJob ? '/qc3d/board' :
        [...SECTIONS].reverse().find(({ key }) => pathname.startsWith(key))?.key ?? '/qc3d';
    const section = SECTIONS.find(({ key }) => key === selected);

    return (
        <ConfigProvider theme={{ token: { colorPrimary: '#315be8', borderRadius: 10, colorText: '#25324b' } }}>
            <div className='cvat-qc3d-page qc3d-shell'>
                <aside className='qc3d-sidebar' aria-label='Điều hướng QC 3D'>
                    <Link className='qc3d-brand' to='/qc3d'>
                        <span className='qc3d-brand-icon'><SafetyCertificateOutlined /></span>
                        <span>
                            <strong>QC 3D <span>AutoFix</span></strong>
                            <small>Kiểm soát chất lượng gán nhãn</small>
                        </span>
                    </Link>
                    <div className='qc3d-nav-caption'>KHÔNG GIAN LÀM VIỆC</div>
                    <Menu mode='inline' selectedKeys={[selected]} items={sections}
                        onClick={({ key }) => history.push(key)}
                    />
                    <div className='qc3d-sidebar-footer'>
                        <div className='qc3d-workflow-note'>
                            <SafetyCertificateOutlined />
                            <strong>Kiểm tra → Xem trước → Áp dụng</strong>
                            <p>Xem đề xuất sửa và giữ quyền quyết định trên từng đối tượng.</p>
                        </div>
                        <Link to='/tasks'><ArrowLeftOutlined /> Quay về task CVAT</Link>
                    </div>
                </aside>
                <div className='qc3d-workspace'>
                    <header className='qc3d-topbar'>
                        <Breadcrumb items={[
                            { title: <Link to='/qc3d'>QC 3D</Link> },
                            { title: isJob ? 'Báo cáo job' : section?.label },
                            ...(pathname.startsWith('/qc3d/guidelines/') ? [{ title: 'Chỉnh sửa guideline' }] : []),
                        ]}
                        />
                        <Button type='text' icon={<QuestionCircleOutlined />} onClick={() => setHelpOpen(true)}>
                            Hướng dẫn nhanh
                        </Button>
                    </header>
                    <main className='qc3d-content'>
                        <Switch>
                            <Route exact path='/qc3d' component={DashboardPage} />
                            <Route exact path='/qc3d/jobs/:jobId' component={JobPage} />
                            <Route exact path='/qc3d/my' component={MyWorkPage} />
                            <Route exact path='/qc3d/board' component={BoardPage} />
                            <Route exact path='/qc3d/review' component={ReviewPage} />
                            <Route exact path='/qc3d/qa' component={AcceptancePage} />
                            <Route exact path='/qc3d/guidelines' component={GuidelinesPage} />
                            <Route exact path='/qc3d/guidelines/:id' component={GuidelineEditorPage} />
                            <Route exact path='/qc3d/classes' component={TaxonomyPage} />
                            <Route exact path='/qc3d/admin' component={AdminPage} />
                        </Switch>
                    </main>
                </div>
                <Modal open={helpOpen} title='Bắt đầu với QC 3D AutoFix' footer={null}
                    onCancel={() => setHelpOpen(false)}
                >
                    <ol className='qc3d-help-steps'>
                        <li>
                            <strong>Quy trình 3 cấp</strong>
                            <p>Gán nhãn → nộp → QC duyệt (đạt / trả lại) → QA nghiệm thu mẫu → lead chốt. Xem ở Trạng thái job.</p>
                        </li>
                        <li><strong>Chọn job 3D</strong><p>Mở job từ Trạng thái job hoặc Hàng đợi review.</p></li>
                        <li>
                            <strong>Chạy kiểm tra QC</strong>
                            <p>QC dùng guideline của task hoặc project để phát hiện lỗi.</p>
                        </li>
                        <li>
                            <strong>Xem và duyệt lỗi</strong>
                            <p>Lọc lỗi, mở đối tượng trong CVAT, xác nhận hoặc bỏ qua có lý do.</p>
                        </li>
                        <li>
                            <strong>Xem trước AutoFix</strong>
                            <p>
                                Kiểm tra đề xuất, loại trừ class cần giữ nguyên rồi áp dụng.
                                Có thể hoàn tác từng bản sửa.
                            </p>
                        </li>
                    </ol>
                    <p className='qc3d-cell-secondary'>Các thao tác hiện theo vai trò QC và quyền truy cập job của bạn.</p>
                </Modal>
            </div>
        </ConfigProvider>
    );
}
