// Copyright (C) CVAT.ai Corporation
//
// SPDX-License-Identifier: MIT

import React from 'react';
import { Link, Route, useHistory } from 'react-router-dom';
import Button from 'antd/lib/button';
import { AuditOutlined } from '@ant-design/icons';

import { PluginEntryPoint, ComponentBuilder } from 'components/plugins-entrypoint';
import { DimensionType } from 'cvat-core-wrapper';
import { setCore } from './api';
import Qc3dApp from './qc3d-app';

const PLUGIN_NAME = 'QC 3D AutoFix';

function NavigationButton(): JSX.Element {
    const history = useHistory();
    const active = history.location.pathname.startsWith('/qc3d');

    return (
        <Button
            className={`cvat-header-qc3d-button cvat-header-button${active ? ' cvat-active-header-button' : ''}`}
            type='link'
            href='/qc3d'
            onClick={(event: React.MouseEvent): void => {
                event.preventDefault();
                history.push('/qc3d');
            }}
        >
            QC 3D
        </Button>
    );
}

const userMenuItem = (): object => ({
    key: 'qc3d_admin',
    icon: <AuditOutlined />,
    label: <Link to='/qc3d/admin'>QC 3D: Quản trị</Link>,
});

const jobMenuItem = ({ key, targetProps }: {
    key: number;
    targetProps: { jobInstance: { id: number } };
}): object => ({
    key: `qc3d_job_${key}`,
    label: <Link to={`/qc3d/jobs/${targetProps.jobInstance.id}`}>QC 3D</Link>,
});

const routes = (): JSX.Element => <Route key='qc3d' path='/qc3d' component={Qc3dApp} />;

const builder: ComponentBuilder = ({ core, dispatch, actionCreators }) => {
    setCore(core);

    const components: [string, any, object][] = [
        ['header.navigation.items', NavigationButton, { weight: 10 }],
        ['header.userMenu.items', userMenuItem, { weight: 5 }],
        ['router', routes, {}],
        ['jobActions.items', jobMenuItem, {
            weight: 100,
            shouldBeRendered: (props?: { jobInstance?: { dimension?: string } }) => (
                props?.jobInstance?.dimension === DimensionType.DIMENSION_3D
            ),
        }],
    ];

    for (const [path, component, data] of components) {
        dispatch(actionCreators.addUIComponent(path, component, data));
    }

    return {
        name: PLUGIN_NAME,
        destructor: () => {
            for (const [path, component] of components) {
                dispatch(actionCreators.removeUIComponent(path, component));
            }
            setCore(null);
        },
    };
};

function register(): void {
    if (Object.prototype.hasOwnProperty.call(window, 'cvatUI')) {
        (window as any as { cvatUI: { registerComponent: PluginEntryPoint } })
            .cvatUI.registerComponent(builder);
    }
}

window.addEventListener('plugins.ready', register, { once: true });
