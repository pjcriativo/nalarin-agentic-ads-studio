/* eslint-disable react-refresh/only-export-components -- small shared module: badges and helpers used together by the agents screens */
import React from 'react';
import { useTranslation } from 'react-i18next';
import { Ban, CheckCircle2, CircleAlert, Clock, FlaskConical, Lightbulb, Loader2, Sigma, XCircle } from 'lucide-react';
import { Badge } from '../ai/ui';
import { dataKindOf } from '../../lib/agentsApi';

export { Badge, CARD, Dialog, Field, INPUT_CLASS, PRIMARY_BUTTON, SECONDARY_BUTTON, DANGER_BUTTON, formatUsd, formatCount } from '../ai/ui';

const STATUS = {
    pending: { tone: 'neutral', icon: Clock },
    running: { tone: 'info', icon: Loader2, spin: true },
    completed: { tone: 'good', icon: CheckCircle2 },
    failed: { tone: 'bad', icon: XCircle },
    cancelled: { tone: 'warn', icon: Ban },
};

/** Task status. The meaning is in the word and the icon, never only in the colour. */
export function StatusBadge({ status }) {
    const { t } = useTranslation('agents');
    const entry = STATUS[status] || STATUS.pending;
    const Icon = entry.icon;
    return (
        <Badge tone={entry.tone} data-testid={`status-${status}`}>
            <Icon size={12} className={entry.spin ? 'animate-spin motion-reduce:animate-none' : ''} aria-hidden="true" />
            {t(`status.${STATUS[status] ? status : 'pending'}`)}
        </Badge>
    );
}

const KIND = {
    real: { tone: 'good', icon: CheckCircle2 },
    estimate: { tone: 'info', icon: Sigma },
    hypothesis: { tone: 'warn', icon: Lightbulb },
};

/** Real data / Google estimate / hypothesis. An unknown label is shown as a hypothesis, never as real. */
export function DataKindBadge({ kind }) {
    const { t } = useTranslation('agents');
    const key = dataKindOf(kind);
    const Icon = KIND[key].icon;
    return (
        <Badge tone={KIND[key].tone} data-testid={`kind-${key}`}>
            <Icon size={12} aria-hidden="true" />
            {t(`dataKind.${key}`)}
        </Badge>
    );
}

export const ReadinessIcon = { ok: CheckCircle2, warn: CircleAlert, test: FlaskConical };

/** "1m 05s" style duration from seconds. */
export function formatDuration(seconds) {
    if (seconds === null || seconds === undefined || Number.isNaN(seconds)) return '—';
    const total = Math.max(0, Math.round(seconds));
    const h = Math.floor(total / 3600);
    const m = Math.floor((total % 3600) / 60);
    const s = total % 60;
    if (h) return `${h}h ${String(m).padStart(2, '0')}m`;
    if (m) return `${m}m ${String(s).padStart(2, '0')}s`;
    return `${s}s`;
}

/** Elapsed seconds between two ISO dates (end defaults to now). */
export const elapsedSeconds = (start, end) => {
    if (!start) return null;
    const to = end ? new Date(end).getTime() : Date.now();
    return Math.max(0, (to - new Date(start).getTime()) / 1000);
};
