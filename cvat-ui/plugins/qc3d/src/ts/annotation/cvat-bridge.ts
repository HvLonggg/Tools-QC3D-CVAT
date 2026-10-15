// Copyright (C) CVAT.ai Corporation
//
// SPDX-License-Identifier: MIT

// Glue between the QC panel and the CVAT annotation view (frame, active object, reload, canvas).

import { Store } from 'redux';
import {
    activateObject, changeFrameAsync, fetchAnnotationsAsync, saveAnnotationsAsync, updateAnnotationsAsync,
} from 'actions/annotation-actions';
import { CombinedState } from 'reducers';
import { ObjectState } from 'cvat-core-wrapper';
import { QCMarker } from 'cvat-canvas3d/src/typescript/canvas3dModel';

import { QcIssue, Severity } from '../api';

export const OPEN_COUNT_EVENT = 'qc3d:open-count';

type AppStore = Store<CombinedState> & { dispatch: (action: any) => any };

/** CVAT object of an issue uid ("s:<shape>" or "t:<track>:<frame>") on the current frame. */
export function findState(states: ObjectState[], uid: string): ObjectState | undefined {
    const [kind, id] = uid.split(':');
    const objectType = kind === 't' ? 'track' : 'shape';
    return states.find((state) => state.serverID === +id && state.objectType === objectType);
}

/** Go to the issue frame and select the object the issue is about. */
export async function focusIssue(store: AppStore, issue: Pick<QcIssue, 'uid' | 'frame'>): Promise<boolean> {
    const { annotation } = store.getState();
    const job = annotation.job.instance;
    if (annotation.player.frame.number !== issue.frame) {
        await store.dispatch(changeFrameAsync(issue.frame));
    }
    if (store.getState().annotation.player.frame.number !== issue.frame) return false;
    const state = findState(store.getState().annotation.annotations.states, issue.uid);
    if (!state || state.outside) return false;
    if (state.hidden) {
        state.hidden = false;
        await store.dispatch(updateAnnotationsAsync([state]));
    }
    store.dispatch(activateObject(state.clientID as number, null, null));
    // Frame changes finish before WebGL has loaded the cloud. Wait for canvas readiness,
    // then focus the actual object, rather than zooming an old frame with the same track ID.
    await new Promise<void>((resolve, reject) => {
        let unsubscribe = (): void => {};
        const timeout = window.setTimeout(() => {
            unsubscribe();
            reject(new Error('Chưa tải xong cảnh 3D. Đợi frame hiển thị rồi chọn lại đối tượng.'));
        }, 12000);
        const check = (): void => {
            const current = store.getState().annotation;
            if (current.job.instance !== job ||
                (current.player.frame.number === issue.frame && current.canvas.ready)) {
                window.clearTimeout(timeout);
                unsubscribe();
                resolve();
            }
        };
        unsubscribe = store.subscribe(check);
        check();
    });
    const current = store.getState().annotation;
    if (current.job.instance !== job || current.player.frame.number !== issue.frame) return false;
    const canvas = current.canvas.instance as any;
    if (typeof canvas?.focusObject === 'function') canvas.focusObject(state.clientID, true);
    else if (typeof canvas?.focus === 'function') canvas.focus(state.clientID);
    return true;
}

const linkedFocus = new WeakMap<object, string>();

/** Uid of a CVAT object as QC names it: "s:<shape>" or "t:<track>:<frame>". */
export function uidOf(state: ObjectState, frame: number): string | null {
    if (typeof state.serverID !== 'number') return null; // not saved yet
    return state.objectType === 'track' ? `t:${state.serverID}:${frame}` : `s:${state.serverID}`;
}

export function applyQcOverlay(store: AppStore, issues: QcIssue[], filters: {
    enabled: boolean;
    severity: 'all' | Severity;
    labels: string[];
}, marked: string[] = []): void {
    const { annotation } = store.getState();
    const canvas = annotation.canvas.instance as any;
    if (!canvas?.configure) return;
    const frame = annotation.player.frame.number;
    const markers = new Map<number, QCMarker>();
    const rank: Record<QCMarker['severity'], number> = { marked: -1, blocker: 0, major: 1, minor: 2 };
    const number = (value: unknown): string => (
        typeof value === 'number' && Number.isFinite(value) ? value.toFixed(2).replace(/\.?0+$/, '') : '—'
    );
    if (filters.enabled) {
        for (const issue of issues) {
            if (issue.frame !== frame || issue.fix_status === 'applied' || issue.status === 'waived' ||
                (filters.labels.length > 0 && !filters.labels.includes(issue.label))) continue;
            const state = findState(annotation.annotations.states, issue.uid);
            if (!state || state.outside) continue;
            const id = state.clientID as number;
            const marker = markers.get(id) ?? {
                clientID: id, label: issue.label, severity: issue.severity, rule: '', message: '', metrics: [],
            };
            if (rank[issue.severity] < rank[marker.severity]) marker.severity = issue.severity;
            marker.rule = [...new Set([...(marker.rule?.split(' · ').filter(Boolean) ?? []), issue.rule])].join(' · ');
            marker.message = [...new Set([marker.message, issue.message].filter(Boolean))].join('\n');
            const { evidence } = issue;
            const metrics: string[] = [];
            if (typeof evidence.points === 'number') {
                metrics.push(`Điểm QC: ${number(evidence.points)}${
                    typeof evidence.required === 'number' ? ` / tối thiểu ${number(evidence.required)}` : ''}`);
            }
            if (typeof evidence.distance === 'number') metrics.push(`Khoảng cách: ${number(evidence.distance)} m`);
            if (typeof evidence.iou === 'number') metrics.push(`IoU: ${number(evidence.iou * 100)}%`);
            if (typeof evidence.bottom_minus_ground === 'number') {
                metrics.push(`Đáy so với mặt đường: ${number(evidence.bottom_minus_ground)} m`);
            }
            if (typeof evidence.speed === 'number') {
                metrics.push(`Vận tốc: ${number(evidence.speed)} / giới hạn ${number(evidence.limit)} m/s`);
            }
            if (typeof evidence.residual === 'number') {
                metrics.push(`Độ nhảy: ${number(evidence.residual)} / ngưỡng ${number(evidence.threshold)} m`);
            }
            if (evidence.gaps && typeof evidence.gaps === 'object') {
                const names: Record<string, string> = { length: 'Dài', width: 'Rộng', height: 'Cao' };
                for (const [key, value] of Object.entries(evidence.gaps)) {
                    if (typeof value === 'number') metrics.push(`Hở ${names[key] ?? key}: ${number(value)} m`);
                }
            }
            marker.metrics = [...new Set([...(marker.metrics ?? []), ...metrics])].slice(0, 5);
            markers.set(id, marker);
        }
    }
    // boxes the reviewer picked for a model fix: always shown, in their own colour
    for (const uid of marked) {
        const state = findState(annotation.annotations.states, uid);
        if (!state || state.outside || (uid.startsWith('t:') && !uid.endsWith(`:${frame}`))) continue;
        const id = state.clientID as number;
        const marker = markers.get(id);
        markers.set(id, {
            clientID: id,
            label: state.label?.name ?? marker?.label ?? '',
            severity: 'marked',
            rule: marker?.rule || 'Đã chọn',
            message: ['Đã chọn để sửa bằng model (tab Autofix)', marker?.message].filter(Boolean).join('\n'),
            metrics: marker?.metrics ?? [],
        });
    }
    canvas.configure({
        qcMarkers: [...markers.values()].filter((marker) => (
            filters.severity === 'all' || marker.severity === filters.severity || marker.severity === 'marked'
        )),
    });
    const params = new URLSearchParams(window.location.search);
    const uid = params.get('qcFocus');
    const linkedFrame = params.has('qcFrame') ? Number(params.get('qcFrame')) : frame;
    if (uid && linkedFrame === frame && /^(s:\d+|t:\d+:\d+)$/.test(uid) &&
        findState(annotation.annotations.states, uid)) {
        const key = `${annotation.job.instance?.id}:${uid}`;
        if (linkedFocus.get(canvas) !== key) {
            linkedFocus.set(canvas, key);
            focusIssue(store, { uid, frame }).catch(() => linkedFocus.delete(canvas));
        }
    }
}

/** Save pending edits first: QC fixes are written on the server and must not be overwritten. */
export async function saveIfNeeded(store: AppStore): Promise<void> {
    const job = store.getState().annotation.job.instance;
    if (job?.annotations.hasUnsavedChanges()) {
        await store.dispatch(saveAnnotationsAsync());
    }
}

/** Reload annotations from the server after fixes were written there. */
export async function reloadAnnotations(store: AppStore): Promise<void> {
    const job = store.getState().annotation.job.instance;
    if (!job) return;
    await job.annotations.clear({ reload: true });
    await store.dispatch(fetchAnnotationsAsync());
}

/** Show only some classes (and optionally only objects with an open issue) on the current frame. */
export async function applyVisibility(
    store: AppStore,
    hiddenLabels: Set<string>,
    onlyFlagged: Set<string> | null,
): Promise<void> {
    const { states } = store.getState().annotation.annotations;
    const changed = states.filter((state) => {
        const flagged = onlyFlagged === null || [...onlyFlagged].some((uid) => findState([state], uid));
        const hidden = hiddenLabels.has(state.label.name) || !flagged;
        if (state.hidden === hidden) return false;
        // eslint-disable-next-line no-param-reassign
        state.hidden = hidden;
        return true;
    });
    if (changed.length) {
        await store.dispatch(updateAnnotationsAsync(changed));
    }
}

export interface ViewerSettings {
    brightness: number; // %
    contrast: number; // %
    pointSize: number; // world units
    colorMode: 'default' | 'height' | 'intensity' | 'range' | 'objects';
    pointFocus: boolean;
    depthShading: boolean;
    roundPoints: boolean;
}

export const DEFAULT_VIEWER: ViewerSettings = {
    brightness: 100,
    contrast: 100,
    pointSize: 0.05,
    colorMode: 'default',
    pointFocus: false,
    depthShading: false,
    roundPoints: true,
};

const VIEWER_KEY = 'qc3d.viewer';

export function loadViewer(): ViewerSettings {
    try {
        const saved = JSON.parse(window.localStorage.getItem(VIEWER_KEY) ?? '{}');
        const bounded = (value: unknown, min: number, max: number, fallback: number): number => (
            typeof value === 'number' && Number.isFinite(value) ? Math.min(max, Math.max(min, value)) : fallback
        );
        return {
            brightness: bounded(saved?.brightness, 50, 250, DEFAULT_VIEWER.brightness),
            contrast: bounded(saved?.contrast, 50, 200, DEFAULT_VIEWER.contrast),
            pointSize: bounded(saved?.pointSize, 0.01, 0.2, DEFAULT_VIEWER.pointSize),
            colorMode: ['default', 'height', 'intensity', 'range', 'objects'].includes(saved?.colorMode) ?
                saved.colorMode : DEFAULT_VIEWER.colorMode,
            pointFocus: saved?.pointFocus === true,
            depthShading: typeof saved?.depthShading === 'boolean' ? saved.depthShading : DEFAULT_VIEWER.depthShading,
            roundPoints: typeof saved?.roundPoints === 'boolean' ? saved.roundPoints : DEFAULT_VIEWER.roundPoints,
        };
    } catch {
        return DEFAULT_VIEWER;
    }
}

export function applyViewer(store: AppStore, viewer: ViewerSettings): void {
    try {
        window.localStorage.setItem(VIEWER_KEY, JSON.stringify(viewer));
    } catch {
        // storage is optional
    }
    const canvas = store.getState().annotation.canvas.instance as any;
    if (!canvas || typeof canvas.html !== 'function') return;
    canvas.configure({
        pointSize: viewer.pointSize,
        pointColorMode: viewer.colorMode,
        pointFocus: viewer.pointFocus,
        pointDepthShading: viewer.depthShading,
        pointRound: viewer.roundPoints,
    });
    const filter = `brightness(${viewer.brightness}%) contrast(${viewer.contrast}%)`;
    for (const element of Object.values(canvas.html() as Record<string, HTMLElement>)) {
        if (element instanceof HTMLElement) {
            element.style.filter = viewer.brightness === 100 && viewer.contrast === 100 ? '' : filter;
        }
    }
}
