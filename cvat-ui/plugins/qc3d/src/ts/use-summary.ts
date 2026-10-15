// Copyright (C) CVAT.ai Corporation
//
// SPDX-License-Identifier: MIT

import { useCallback, useEffect, useState } from 'react';
import { errorText, getSummary, Qc3dSummary } from './api';

export default function useSummary(): {
    summary: Qc3dSummary | null;
    error: string | null;
    loading: boolean;
    refresh: () => void;
} {
    const [summary, setSummary] = useState<Qc3dSummary | null>(null);
    const [error, setError] = useState<string | null>(null);
    const [loading, setLoading] = useState(true);
    const [requestVersion, setRequestVersion] = useState(0);
    const refresh = useCallback(() => setRequestVersion((version) => version + 1), []);

    useEffect(() => {
        let cancelled = false;
        setLoading(true);
        setError(null);
        getSummary()
            .then((data) => { if (!cancelled) setSummary(data); })
            .catch((err: unknown) => { if (!cancelled) setError(errorText(err)); })
            .finally(() => { if (!cancelled) setLoading(false); });
        return () => { cancelled = true; };
    }, [requestVersion]);

    return {
        summary, error, loading, refresh,
    };
}
