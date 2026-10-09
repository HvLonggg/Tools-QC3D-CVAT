// Copyright (C) CVAT.ai Corporation
//
// SPDX-License-Identifier: MIT

import { useEffect, useState } from 'react';
import { getSummary, Qc3dSummary } from './api';

export default function useSummary(): {
    summary: Qc3dSummary | null;
    error: string | null;
    loading: boolean;
} {
    const [summary, setSummary] = useState<Qc3dSummary | null>(null);
    const [error, setError] = useState<string | null>(null);

    useEffect(() => {
        let cancelled = false;
        getSummary()
            .then((data) => { if (!cancelled) setSummary(data); })
            .catch((err: Error) => { if (!cancelled) setError(err.message); });
        return () => { cancelled = true; };
    }, []);

    return { summary, error, loading: !summary && !error };
}
