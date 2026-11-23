import React from 'react';
import { useTranslation } from 'react-i18next';
import { Cpu, ShieldAlert, Wrench, Wand2 } from 'lucide-react';
import { toolOutcome } from '../../lib/agentsApi';
import { formatDateTime } from '../../i18n/format';
import { Badge, formatCount, formatUsd } from './ui';

const KIND_ICON = { llm: Cpu, tool: Wrench, system: Wand2 };
const OUTCOME_TONE = { ok: 'good', noData: 'neutral', unavailable: 'warn', error: 'bad' };

/** Compact, readable preview of a stored (already redacted and truncated) input/output. */
function Preview({ label, value }) {
    if (value === null || value === undefined) return null;
    let text;
    try { text = typeof value === 'string' ? value : JSON.stringify(value, null, 2); } catch { text = String(value); }
    return (
        <div className="mt-2">
            <p className="text-xs font-medium text-gray-700">{label}</p>
            <pre className="mt-1 text-xs bg-gray-50 rounded p-2 overflow-x-auto max-h-56 whitespace-pre-wrap break-words">{text.length > 2500 ? `${text.slice(0, 2500)}…` : text}</pre>
        </div>
    );
}

/** What the agent did, step by step: model turns, tool calls (with what each tool really returned), cost and time. */
export default function StepsTimeline({ steps }) {
    const { t } = useTranslation('agents');
    if (steps.length === 0) return <p className="text-sm text-gray-600" data-testid="steps-empty">{t('steps.empty')}</p>;

    return (
        <>
            <ol className="space-y-2" data-testid="steps-timeline">
                {steps.map((step) => {
                    const Icon = KIND_ICON[step.kind] || Wand2;
                    const outcome = toolOutcome(step);
                    const failed = step.status === 'error';
                    return (
                        <li key={step.sequence} className="border border-gray-200 rounded-lg" data-testid={`step-${step.sequence}`}>
                            <details>
                                <summary className="cursor-pointer list-none px-3 py-2 min-h-[44px] flex flex-wrap items-center gap-x-3 gap-y-1">
                                    <Icon size={16} className="text-amber-700 flex-shrink-0" aria-hidden="true" />
                                    {step.name ? <code className="text-sm font-medium text-gray-900 break-all">{step.name}</code> : <span className="text-sm font-medium text-gray-900">{t(`steps.kind.${step.kind}`, { defaultValue: step.kind })}</span>}
                                    <Badge tone="neutral">{t(`steps.kind.${step.kind}`, { defaultValue: step.kind })}</Badge>
                                    {outcome && <Badge tone={OUTCOME_TONE[outcome.state]} data-testid={`outcome-${step.sequence}`}>{t(`steps.outcome.${outcome.state}`)}{outcome.reason ? ` · ${outcome.reason}` : ''}</Badge>}
                                    {!outcome && failed && <Badge tone="bad">{t('steps.failed')}{step.error_kind ? ` · ${step.error_kind}` : ''}</Badge>}
                                    {step.injection_suspected && <Badge tone="warn" icon={ShieldAlert} data-testid={`injection-${step.sequence}`}>{t('steps.injection')}</Badge>}
                                    <span className="text-xs text-gray-500 ml-auto">
                                        {step.input_tokens + step.output_tokens > 0 && `${formatCount(step.input_tokens)}/${formatCount(step.output_tokens)} tok · `}
                                        {step.cost_usd ? `${formatUsd(step.cost_usd)} · ` : ''}
                                        {step.latency_ms !== null && step.latency_ms !== undefined ? `${(step.latency_ms / 1000).toFixed(1)}s` : ''}
                                    </span>
                                </summary>
                                <div className="px-3 pb-3">
                                    <p className="text-xs text-gray-500">#{step.sequence} · {formatDateTime(step.created_at)}{step.model_id ? ` · ${step.provider || ''} ${step.model_id}` : ''}</p>
                                    <Preview label={t('steps.input')} value={step.input} />
                                    <Preview label={t('steps.output')} value={step.output} />
                                </div>
                            </details>
                        </li>
                    );
                })}
            </ol>
            <p className="text-xs text-gray-500 mt-2">{t('steps.privacy')}</p>
        </>
    );
}
