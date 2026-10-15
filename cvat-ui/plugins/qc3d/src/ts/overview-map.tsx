// Copyright (C) CVAT.ai Corporation
//
// SPDX-License-Identifier: MIT

// Bird's-eye overview of a job: ego path, labeled objects and QC issues on one map (SVG, no extra library).
// Positions come from the boxes (ego motion chained frame to frame): an overview, not a survey.

import React, {
    useCallback, useEffect, useMemo, useRef, useState,
} from 'react';
import { useHistory } from 'react-router-dom';
import {
    AimOutlined, CompressOutlined, MinusOutlined, PlusOutlined,
} from '@ant-design/icons';
import Button from 'antd/lib/button';
import Switch from 'antd/lib/switch';
import Tooltip from 'antd/lib/tooltip';

import { annotationLink, OverviewMap as MapData, Severity } from './api';

const SEVERITY_STYLE: Record<Severity, { color: string; text: string; radius: number }> = {
    blocker: { color: '#d9384a', text: 'Nghiêm trọng', radius: 7 },
    major: { color: '#e08a00', text: 'Cần xử lý', radius: 6 },
    minor: { color: '#2875b5', text: 'Lưu ý', radius: 5 },
};
// muted hues that do not compete with the severity colours (red / amber / blue) of the issues
const CLASS_COLORS = ['#8796b3', '#4fae8f', '#a98bd6', '#8f8a6e', '#5fb3bf', '#c48fb6', '#93a86a'];
const HEIGHT = 440;

interface View { x: number; y: number; scale: number } // world point at the centre, pixels per metre

function fit(extent: [number, number, number, number], width: number): View {
    const [x0, y0, x1, y1] = extent;
    const scale = Math.min(width / Math.max(x1 - x0, 1), HEIGHT / Math.max(y1 - y0, 1)) * 0.92;
    return { x: (x0 + x1) / 2, y: (y0 + y1) / 2, scale };
}

function scaleBar(scale: number): { metres: number; pixels: number } {
    const target = 110 / scale;
    const metres = [1, 2, 5, 10, 20, 50, 100, 200, 500].find((m) => m >= target * 0.6) ?? 500;
    return { metres, pixels: metres * scale };
}

export default function OverviewMap({ data, jobId }: { data: MapData; jobId: number }): JSX.Element {
    const history = useHistory();
    const box = useRef<HTMLDivElement>(null);
    const [width, setWidth] = useState(900);
    const [view, setView] = useState<View>(() => fit(data.extent, 900));
    const [layers, setLayers] = useState({ path: true, objects: true, onlyOpen: false });
    const [severities, setSeverities] = useState<Record<Severity, boolean>>({ blocker: true, major: true, minor: true });
    const [hover, setHover] = useState<{ x: number; y: number; text: string[] } | null>(null);
    const drag = useRef<{ x: number; y: number; view: View } | null>(null);

    useEffect(() => {
        const element = box.current;
        if (!element) return undefined;
        const observer = new ResizeObserver(([entry]) => setWidth(Math.max(320, entry.contentRect.width)));
        observer.observe(element);
        return () => observer.disconnect();
    }, []);
    const reset = useCallback(() => setView(fit(data.extent, width)), [data, width]);
    const widthStep = Math.round(width / 50); // refit when the card is resized, not on every pixel
    useEffect(() => { reset(); }, [data, widthStep]);

    const classColor = useMemo(() => {
        const names = [...new Set(data.objects.map((o) => o.label))].sort();
        return Object.fromEntries(names.map((name, i) => [name, CLASS_COLORS[i % CLASS_COLORS.length]]));
    }, [data]);
    const issues = data.issues.filter((issue) => severities[issue.severity] &&
        (!layers.onlyOpen || (issue.status === 'open' && issue.fix_status !== 'applied')));
    const counts = (severity: Severity): number => data.issues.filter((i) => i.severity === severity).length;

    // world -> screen
    const sx = (x: number): number => width / 2 + (x - view.x) * view.scale;
    const sy = (y: number): number => HEIGHT / 2 - (y - view.y) * view.scale;
    const zoom = (factor: number, cx = width / 2, cy = HEIGHT / 2): void => setView((v) => {
        const scale = Math.min(80, Math.max(0.3, v.scale * factor));
        const wx = v.x + (cx - width / 2) / v.scale;
        const wy = v.y - (cy - HEIGHT / 2) / v.scale;
        return { scale, x: wx - (cx - width / 2) / scale, y: wy + (cy - HEIGHT / 2) / scale };
    });
    const onWheel = (event: React.WheelEvent): void => {
        const rect = (event.currentTarget as Element).getBoundingClientRect();
        zoom(event.deltaY < 0 ? 1.2 : 1 / 1.2, event.clientX - rect.left, event.clientY - rect.top);
    };
    useEffect(() => {
        // the page must not scroll while the wheel zooms the map
        const element = box.current?.querySelector('svg');
        const stop = (event: WheelEvent): void => event.preventDefault();
        element?.addEventListener('wheel', stop, { passive: false });
        return () => element?.removeEventListener('wheel', stop);
    }, []);
    const bar = scaleBar(view.scale);
    const path = data.trajectory.map((p) => `${sx(p.x).toFixed(1)},${sy(p.y).toFixed(1)}`).join(' ');
    // frame labels along the path, at least 34 px apart on screen (a slow vehicle stacks them otherwise)
    const ticks: typeof data.trajectory = [];
    data.trajectory.forEach((p, i) => {
        const last = ticks[ticks.length - 1];
        const far = !last || Math.hypot(sx(p.x) - sx(last.x), sy(p.y) - sy(last.y)) >= 34;
        if ((i % 5 === 0 || i === data.trajectory.length - 1) && far) ticks.push(p);
    });

    return (
        <div className='qc3d-map'>
            <div className='qc3d-map-toolbar'>
                <div className='qc3d-map-chips'>
                    {(Object.keys(SEVERITY_STYLE) as Severity[]).map((severity) => (
                        <button
                            key={severity}
                            type='button'
                            aria-pressed={severities[severity]}
                            className={`qc3d-map-chip${severities[severity] ? ' qc3d-map-chip-on' : ''}`}
                            style={{ '--chip': SEVERITY_STYLE[severity].color } as React.CSSProperties}
                            onClick={() => setSeverities((s) => ({ ...s, [severity]: !s[severity] }))}
                        >
                            <i />
                            {`${SEVERITY_STYLE[severity].text} · ${counts(severity)}`}
                        </button>
                    ))}
                </div>
                <div className='qc3d-map-switches'>
                    <label><Switch size='small' checked={layers.onlyOpen} onChange={(onlyOpen) => setLayers((l) => ({ ...l, onlyOpen }))} />Chỉ lỗi chưa xử lý</label>
                    <label><Switch size='small' checked={layers.objects} onChange={(objects) => setLayers((l) => ({ ...l, objects }))} />Vật thể</label>
                    <label><Switch size='small' checked={layers.path} onChange={(p) => setLayers((l) => ({ ...l, path: p }))} />Quỹ đạo xe</label>
                </div>
            </div>
            <div className='qc3d-map-canvas' ref={box}>
                <svg
                    width={width}
                    height={HEIGHT}
                    role='img'
                    aria-label={`Bản đồ tổng quan job ${jobId}: ${data.issues.length} vấn đề QC`}
                    onWheel={onWheel}
                    onMouseDown={(event) => { drag.current = { x: event.clientX, y: event.clientY, view }; }}
                    onMouseMove={(event) => {
                        if (!drag.current) return;
                        const { x, y, view: start } = drag.current;
                        setView({ ...start, x: start.x - (event.clientX - x) / start.scale, y: start.y + (event.clientY - y) / start.scale });
                    }}
                    onMouseUp={() => { drag.current = null; }}
                    onMouseLeave={() => { drag.current = null; setHover(null); }}
                >
                    <defs>
                        <pattern id={`qc3d-grid-${jobId}`} width={Math.max(8, 10 * view.scale)} height={Math.max(8, 10 * view.scale)} patternUnits='userSpaceOnUse' x={sx(0)} y={sy(0)}>
                            <path d={`M ${Math.max(8, 10 * view.scale)} 0 L 0 0 0 ${Math.max(8, 10 * view.scale)}`} fill='none' stroke='#e9edf5' strokeWidth='1' />
                        </pattern>
                    </defs>
                    <rect width={width} height={HEIGHT} fill={`url(#qc3d-grid-${jobId})`} />
                    {layers.objects && data.objects.map((o, i) => (
                        // eslint-disable-next-line react/no-array-index-key
                        <circle key={i} cx={sx(o.x)} cy={sy(o.y)} r={Math.max(1.4, Math.min(3, view.scale * 0.35))} fill={classColor[o.label]} opacity={0.55} />
                    ))}
                    {layers.path && data.trajectory.length > 1 && (
                        <g className='qc3d-map-path'>
                            <polyline points={path} fill='none' stroke='#315be8' strokeWidth={3} strokeLinejoin='round' strokeLinecap='round' opacity={0.85} />
                            {ticks.map((p) => (
                                <g key={p.frame} transform={`translate(${sx(p.x)},${sy(p.y)})`}>
                                    <circle r={3.5} fill='#fff' stroke='#315be8' strokeWidth={2} />
                                    <text y={-8} textAnchor='middle' className='qc3d-map-tick'>{`f${p.frame}`}</text>
                                </g>
                            ))}
                        </g>
                    )}
                    {issues.map((issue) => {
                        const style = SEVERITY_STYLE[issue.severity];
                        const done = issue.status !== 'open' || issue.fix_status === 'applied';
                        return (
                            <circle
                                key={issue.issue_id}
                                className='qc3d-map-issue'
                                cx={sx(issue.x)}
                                cy={sy(issue.y)}
                                r={style.radius}
                                fill={done ? '#fff' : style.color}
                                stroke={style.color}
                                strokeWidth={2}
                                opacity={done ? 0.65 : 0.92}
                                tabIndex={0}
                                role='button'
                                aria-label={`${issue.rule} ${issue.label} frame ${issue.frame}`}
                                onMouseEnter={(event) => {
                                    const rect = box.current?.getBoundingClientRect();
                                    setHover({
                                        x: event.clientX - (rect?.left ?? 0),
                                        y: event.clientY - (rect?.top ?? 0),
                                        text: [`${issue.label || 'Frame'} · ${issue.rule}`, `${style.text} · frame ${issue.frame}`,
                                            issue.fix_status === 'applied' ? 'Đã sửa' : ({ open: 'Chưa xử lý', confirmed: 'Lỗi thật', waived: 'Giữ nguyên' }[issue.status]),
                                            'Bấm để mở trong CVAT'],
                                    });
                                }}
                                onMouseLeave={() => setHover(null)}
                                onClick={() => history.push(annotationLink(data.task_id, jobId, issue.frame,
                                    issue.uid.startsWith('m:') ? undefined : issue.uid))}
                                onKeyDown={(event) => {
                                    if (event.key === 'Enter') {
                                        history.push(annotationLink(data.task_id, jobId, issue.frame,
                                            issue.uid.startsWith('m:') ? undefined : issue.uid));
                                    }
                                }}
                            />
                        );
                    })}
                    <g transform={`translate(16,${HEIGHT - 22})`} className='qc3d-map-scale'>
                        <rect x={-6} y={-16} width={bar.pixels + 12} height={28} rx={6} fill='rgba(255,255,255,0.88)' />
                        <line x1={0} x2={bar.pixels} y1={4} y2={4} stroke='#25324b' strokeWidth={2} />
                        <line x1={0} x2={0} y1={0} y2={8} stroke='#25324b' strokeWidth={2} />
                        <line x1={bar.pixels} x2={bar.pixels} y1={0} y2={8} stroke='#25324b' strokeWidth={2} />
                        <text x={bar.pixels / 2} y={-3} textAnchor='middle'>{`${bar.metres} m`}</text>
                    </g>
                </svg>
                <div className='qc3d-map-zoom'>
                    <Tooltip title='Phóng to' placement='left'><Button size='small' icon={<PlusOutlined />} aria-label='Phóng to' onClick={() => zoom(1.4)} /></Tooltip>
                    <Tooltip title='Thu nhỏ' placement='left'><Button size='small' icon={<MinusOutlined />} aria-label='Thu nhỏ' onClick={() => zoom(1 / 1.4)} /></Tooltip>
                    <Tooltip title='Vừa khung' placement='left'><Button size='small' icon={<CompressOutlined />} aria-label='Vừa khung' onClick={reset} /></Tooltip>
                    {data.trajectory.length > 0 && (
                        <Tooltip title='Về đầu quỹ đạo' placement='left'>
                            <Button size='small' icon={<AimOutlined />} aria-label='Về đầu quỹ đạo' onClick={() => setView((v) => ({ ...v, x: data.trajectory[0].x, y: data.trajectory[0].y, scale: Math.max(v.scale, 4) }))} />
                        </Tooltip>
                    )}
                </div>
                {hover && (
                    <div className='qc3d-map-tooltip' style={{ left: hover.x + 14, top: hover.y + 10 }}>
                        {hover.text.map((line, i) => <div key={line} className={i ? '' : 'qc3d-map-tooltip-title'}>{line}</div>)}
                    </div>
                )}
            </div>
            <div className='qc3d-map-legend-row'>
                <span className='qc3d-map-legend-item'><i className='qc3d-map-swatch-path' />Quỹ đạo xe (fN: frame)</span>
                {Object.entries(classColor).map(([name, color]) => (
                    <span key={name} className='qc3d-map-legend-item'><i style={{ background: color }} />{name}</span>
                ))}
                <span className='qc3d-map-legend-item'><i className='qc3d-map-swatch-done' />Đã xử lý</span>
                <span className='qc3d-cell-secondary'>Vị trí ước lượng từ các box qua từng frame · cuộn để phóng, kéo để di chuyển</span>
            </div>
        </div>
    );
}
