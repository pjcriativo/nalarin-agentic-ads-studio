import React, { useEffect, useState } from 'react';
import { useTranslation } from 'react-i18next';
import { Activity, Loader2 } from 'lucide-react';
import { formatDate, formatDateTime } from '../../i18n/format';
import BudgetCard from './BudgetCard';
import { Badge, CARD, INPUT_CLASS, OPERATION_KEY, SECONDARY_BUTTON, errorMessage, formatCount, formatUsd, providerName } from './ui';

const RANGES = [7, 30, 90];
const BUDGET_PERIODS = ['daily', 'monthly'];
const shortId = (id) => `…${String(id).slice(-6)}`;

function Kpi({ label, value, testId }) {
    return (
        <div className={`${CARD} p-4`}>
            <dt className="text-xs text-gray-500">{label}</dt>
            <dd className="text-xl font-bold text-gray-900 mt-1 break-words" data-testid={testId}>{value}</dd>
        </div>
    );
}

function Breakdown({ title, rows, labelOf, t }) {
    return (
        <div className={`${CARD} p-4`}>
            <h3 className="font-bold text-gray-900 text-sm">{title}</h3>
            {rows.length === 0 ? <p className="text-sm text-gray-500 mt-2">—</p> : (
                <ul className="mt-2 divide-y divide-gray-100">
                    {rows.map((row, index) => (
                        <li key={`${labelOf(row)}-${index}`} className="py-2 flex items-start justify-between gap-3 text-sm">
                            <span className="min-w-0 break-all text-gray-800">{labelOf(row)}</span>
                            <span className="text-right flex-shrink-0">
                                <span className="block font-medium text-gray-900">{formatUsd(row.cost_usd)}</span>
                                <span className="block text-xs text-gray-500">{t('usage.columns.requests')}: {formatCount(row.requests)}</span>
                            </span>
                        </li>
                    ))}
                </ul>
            )}
        </div>
    );
}

export default function UsagePanel({ api, budgets, canManage, onBudgetsChanged }) {
    const { t } = useTranslation('ai');
    const [days, setDays] = useState(30);
    const [state, setState] = useState({ status: 'loading', summary: null, logs: [], error: '' });
    const [reloadKey, setReloadKey] = useState(0);

    useEffect(() => {
        let cancelled = false;
        (async () => {
            try {
                const [summary, logs] = await Promise.all([api.usageSummary(days), api.usageLogs(25)]);
                if (!cancelled) setState({ status: 'ready', summary, logs, error: '' });
            } catch (error) {
                if (!cancelled) setState({ status: 'error', summary: null, logs: [], error: errorMessage(t, error) });
            }
        })();
        return () => { cancelled = true; };
    }, [api, days, reloadKey, t]);

    const markLoading = () => setState((current) => ({ ...current, status: 'loading', error: '' }));
    const changeRange = (value) => { markLoading(); setDays(value); };
    const retry = () => { markLoading(); setReloadKey((key) => key + 1); };

    const { summary, logs } = state;
    const totals = summary?.totals;
    const byDay = summary?.by_day || [];
    const maxCost = Math.max(0, ...byDay.map((day) => day.cost_usd || 0));
    const operationLabel = (key) => (OPERATION_KEY[key] ? t(`routing.operation.${OPERATION_KEY[key]}`) : key);
    const reasonOf = (kind) => t(`errors.${kind || 'unknown'}`, { defaultValue: t('errors.unknown') });
    const budgetOf = (period) => budgets.find((budget) => budget.period === period) || null;

    return (
        <div className="space-y-8">
            <section aria-labelledby="ai-budgets-heading">
                <h2 id="ai-budgets-heading" className="text-lg font-bold text-gray-900">{t('budget.title')}</h2>
                <p className="text-sm text-gray-600 mt-1 max-w-3xl">{t('budget.subtitle')}</p>
                <ul className="grid gap-4 md:grid-cols-2 mt-4">
                    {BUDGET_PERIODS.map((period) => (
                        <BudgetCard key={period} api={api} period={period} budget={budgetOf(period)} canManage={canManage} onChanged={onBudgetsChanged} />
                    ))}
                </ul>
            </section>

            <section aria-labelledby="ai-usage-heading">
                <div className="flex flex-wrap items-end justify-between gap-3">
                    <h2 id="ai-usage-heading" className="text-lg font-bold text-gray-900 flex items-center gap-2">
                        <Activity size={20} className="text-amber-600" aria-hidden="true" />
                        {t('usage.title')}
                    </h2>
                    <div>
                        <label htmlFor="ai-usage-range" className="block text-xs text-gray-500 mb-1">{t('usage.period')}</label>
                        <select id="ai-usage-range" value={days} onChange={(event) => changeRange(Number(event.target.value))} className={`${INPUT_CLASS} min-h-[44px]`}>
                            {RANGES.map((range) => <option key={range} value={range}>{t('usage.range', { count: range })}</option>)}
                        </select>
                    </div>
                </div>

                {state.status === 'loading' && !summary && (
                    <p role="status" className="flex items-center gap-2 text-sm text-gray-600 mt-4"><Loader2 size={16} className="animate-spin" aria-hidden="true" />{t('state.loading')}</p>
                )}
                {state.status === 'error' && (
                    <div role="alert" className="mt-4 text-sm text-red-700 bg-red-50 border border-red-200 rounded-lg px-3 py-3 flex flex-wrap items-center justify-between gap-2">
                        <span>{state.error}</span>
                        <button type="button" className={SECONDARY_BUTTON} onClick={retry}>{t('state.retry')}</button>
                    </div>
                )}

                {summary && (
                    <div className="mt-4 space-y-6" aria-busy={state.status === 'loading'}>
                        {totals.requests === 0 && logs.length === 0 ? (
                            <p className={`${CARD} p-6 text-sm text-gray-600`}>{t('usage.empty')}</p>
                        ) : (
                            <>
                                <dl className="grid grid-cols-2 lg:grid-cols-3 gap-3">
                                    <Kpi label={t('usage.kpi.requests')} value={formatCount(totals.requests)} testId="kpi-requests" />
                                    <Kpi label={t('usage.kpi.cost')} value={formatUsd(totals.cost_usd)} testId="kpi-cost" />
                                    <Kpi label={t('usage.kpi.errors')} value={formatCount(totals.errors)} testId="kpi-errors" />
                                    <Kpi label={t('usage.kpi.inputTokens')} value={formatCount(totals.input_tokens)} testId="kpi-input" />
                                    <Kpi label={t('usage.kpi.outputTokens')} value={formatCount(totals.output_tokens)} testId="kpi-output" />
                                    <Kpi label={t('usage.kpi.cachedTokens')} value={formatCount(totals.cached_input_tokens)} testId="kpi-cached" />
                                </dl>
                                <p className="text-xs text-gray-500">{t('usage.costNote')}</p>

                                <div className={`${CARD} p-4`}>
                                    <h3 className="font-bold text-gray-900 text-sm">{t('usage.perDay')}</h3>
                                    {byDay.length === 0 || maxCost === 0 ? <p className="text-sm text-gray-500 mt-2">{t('usage.perDayEmpty')}</p> : (
                                        <ul className="mt-3 space-y-1.5">
                                            {byDay.map((day) => (
                                                <li key={day.date} className="flex items-center gap-3 text-sm">
                                                    <span className="w-20 flex-shrink-0 text-gray-600">{formatDate(`${day.date}T12:00:00Z`, { day: '2-digit', month: 'short', timeZone: 'UTC' })}</span>
                                                    <span className="flex-1 h-3 rounded bg-gray-100 overflow-hidden" aria-hidden="true">
                                                        <span className="block h-full rounded bg-amber-500" style={{ width: `${Math.max(2, ((day.cost_usd || 0) / maxCost) * 100)}%` }} />
                                                    </span>
                                                    <span className="w-24 flex-shrink-0 text-right font-medium text-gray-900">{formatUsd(day.cost_usd)}</span>
                                                </li>
                                            ))}
                                        </ul>
                                    )}
                                </div>

                                <div className="grid gap-4 lg:grid-cols-3">
                                    <Breakdown title={t('usage.byModel')} rows={summary.by_model || []} labelOf={(row) => `${providerName(row.provider)} · ${row.model_id}`} t={t} />
                                    <Breakdown title={t('usage.byOperation')} rows={summary.by_operation || []} labelOf={(row) => operationLabel(row.operation)} t={t} />
                                    <Breakdown title={t('usage.byUser')} rows={summary.by_user || []} labelOf={(row) => (row.user_id ? shortId(row.user_id) : t('usage.unknownUser'))} t={t} />
                                </div>

                                <div className={`${CARD} p-4`}>
                                    <h3 className="font-bold text-gray-900 text-sm">{t('usage.history')}</h3>
                                    <div className="overflow-x-auto mt-2">
                                        <table className="w-full text-sm min-w-[640px]" data-testid="usage-history">
                                            <thead>
                                                <tr className="text-left text-xs text-gray-500 border-b border-gray-200">
                                                    {['when', 'task', 'model', 'result', 'tokens', 'cost', 'latency'].map((column) => <th key={column} scope="col" className={`py-2 pr-3 font-medium ${column === 'cost' ? 'text-right' : ''}`}>{t(`usage.columns.${column}`)}</th>)}
                                                </tr>
                                            </thead>
                                            <tbody>
                                                {logs.map((log) => (
                                                    <tr key={log.id} className="border-b border-gray-100 align-top">
                                                        <td className="py-2 pr-3 whitespace-nowrap text-gray-600">{formatDateTime(log.created_at)}</td>
                                                        <td className="py-2 pr-3 text-gray-800">{operationLabel(log.operation)}</td>
                                                        <td className="py-2 pr-3 text-gray-800 break-all">{log.model_id}</td>
                                                        <td className="py-2 pr-3">
                                                            {log.status === 'success'
                                                                ? <Badge tone="good">{t('usage.success')}</Badge>
                                                                : <Badge tone="bad">{t('usage.failure', { reason: reasonOf(log.error_kind) })}</Badge>}
                                                        </td>
                                                        <td className="py-2 pr-3 whitespace-nowrap text-gray-600">{formatCount(log.input_tokens)} / {formatCount(log.output_tokens)}</td>
                                                        <td className="py-2 pr-3 text-right font-medium text-gray-900">{formatUsd(log.cost_usd)}</td>
                                                        <td className="py-2 whitespace-nowrap text-gray-600">{log.latency_ms === null || log.latency_ms === undefined ? '—' : t('usage.latencyMs', { ms: formatCount(log.latency_ms) })}</td>
                                                    </tr>
                                                ))}
                                            </tbody>
                                        </table>
                                    </div>
                                </div>
                            </>
                        )}
                    </div>
                )}
            </section>
        </div>
    );
}
