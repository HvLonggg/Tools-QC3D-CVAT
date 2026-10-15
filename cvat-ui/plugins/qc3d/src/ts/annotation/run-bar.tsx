// Copyright (C) CVAT.ai Corporation
//
// SPDX-License-Identifier: MIT

import React, { useCallback, useEffect, useRef, useState } from 'react';
import { PlayCircleOutlined } from '@ant-design/icons';
import Button from 'antd/lib/button';
import Progress from 'antd/lib/progress';
import Segmented from 'antd/lib/segmented';
import Tooltip from 'antd/lib/tooltip';
import notification from 'antd/lib/notification';

import { CheckStatus, errorText, getCheckStatus, startCheck } from '../api';

interface Props {
    jobId: number;
    frame: number;
    canRun: boolean;
    guideline?: string;
    // a QC run finished: the panel reloads its report
    onDone: (status: CheckStatus) => void;
    // changes when the panel started a run itself (fix of the marked boxes): follow it
    started?: number;
}

const RUNNING = ['model', 'qc', 'fix'];

function progressOf(status: CheckStatus): { percent: number; text: string } {
    if (status.stage === 'model') {
        const ratio = status.total ? status.done / status.total : 0;
        return { percent: Math.round(5 + 80 * ratio), text: status.total ? `Model ${status.done}/${status.total}` : 'Chuẩn bị' };
    }
    if (status.stage === 'qc') return { percent: 90, text: 'Đang kiểm tra' };
    if (status.stage === 'fix') return { percent: 96, text: 'Đang sửa nhãn đã chọn' };
    if (status.stage === 'done') return { percent: 100, text: `Xong · ${status.issues ?? 0} lỗi` };
    return { percent: 100, text: 'Lỗi' };
}

/** "Run QC" for the whole job or the current frame: model on the frames that miss it, then QC, one progress bar. */
export default function RunBar({
    jobId, frame, canRun, guideline, onDone, started,
}: Props): JSX.Element {
    const [scope, setScope] = useState<'all' | 'frame'>('all');
    const [status, setStatus] = useState<CheckStatus>({ stage: 'idle' });
    const [showDone, setShowDone] = useState(false);
    const wasRunning = useRef(false);

    const poll = useCallback(async (): Promise<void> => {
        try {
            const next = await getCheckStatus(jobId);
            if (wasRunning.current && !RUNNING.includes(next.stage)) {
                if (next.stage === 'done') {
                    setShowDone(true);
                    window.setTimeout(() => setShowDone(false), 4000);
                    onDone(next);
                } else if (next.stage === 'error') {
                    notification.error({ message: 'Không chạy được QC', description: next.error });
                }
            }
            wasRunning.current = RUNNING.includes(next.stage);
            setStatus(next);
        } catch {
            // the bar keeps its last state; the next poll retries
        }
    }, [jobId, onDone]);

    useEffect(() => {
        wasRunning.current = false;
        setStatus({ stage: 'idle' });
        poll();
    }, [jobId]);

    useEffect(() => {
        if (!started) return;
        wasRunning.current = true;
        poll();
    }, [started]);

    const running = RUNNING.includes(status.stage);
    useEffect(() => {
        if (!running) return undefined;
        const timer = window.setInterval(poll, 2000);
        return () => window.clearInterval(timer);
    }, [running, poll]);

    const onRun = async (): Promise<void> => {
        try {
            const next = await startCheck(jobId, scope === 'frame' ? frame : null);
            wasRunning.current = true;
            setStatus(next);
        } catch (err: unknown) {
            notification.error({ message: 'Không chạy được QC', description: errorText(err) });
        }
    };

    const { percent, text } = progressOf(status);
    return (
        <div className='qc3d-runbar'>
            <div className='qc3d-runbar-row'>
                <Segmented
                    size='small'
                    value={scope}
                    disabled={running}
                    onChange={(value) => setScope(value as 'all' | 'frame')}
                    options={[{ value: 'all', label: 'Tất cả' }, { value: 'frame', label: `Frame ${frame}` }]}
                />
                {canRun && (
                    <Tooltip title={guideline ? `Theo guideline: ${guideline}` : undefined}>
                        <Button type='primary' size='small' icon={<PlayCircleOutlined />} loading={running} onClick={onRun}>
                            {running ? text : 'Chạy QC'}
                        </Button>
                    </Tooltip>
                )}
            </div>
            {(running || showDone || status.stage === 'error') && (
                <Progress
                    percent={percent}
                    size='small'
                    showInfo={false}
                    status={status.stage === 'error' ? 'exception' : running ? 'active' : 'success'}
                />
            )}
        </div>
    );
}
