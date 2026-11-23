import React, { useCallback, useEffect, useRef, useState } from 'react';
import { Link } from 'react-router-dom';
import { useTranslation } from 'react-i18next';
import { Ban, CircleAlert, ServerCrash } from 'lucide-react';
import { useAuth } from '../../context/AuthContext';
import { useOrganization } from '../../context/OrganizationContext';
import { useToast } from '../../context/ToastContext';
import { ERROR_NEXT_STEP, errorKind, isActive, isFinal, normalizeTask, progressPercent, costShare } from '../../lib/agentsApi';
import usePolling from '../../lib/usePolling';
import useBackendText from '../../lib/useBackendText';
import { formatDateTime } from '../../i18n/format';
import ConfirmationModal from '../ConfirmationModal';
import ResultView from './ResultView';
import StepsTimeline from './StepsTimeline';
import { Badge, CARD, DANGER_BUTTON, StatusBadge, elapsedSeconds, formatCount, formatDuration, formatUsd } from './ui';

const POLL_MS = 2500;

function ProgressBar({ percent, label, tone = 'amber', indeterminate = false }) {
    const color = { amber: 'bg-amber-500', red: 'bg-red-500', green: 'bg-green-600' }[tone];
    return (
        <div
            role="progressbar"
            aria-label={label}
            aria-valuemin={0}
            aria-valuemax={100}
            {...(indeterminate ? { 'aria-busy': true } : { 'aria-valuenow': percent })}
            className="h-2 w-full bg-gray-200 rounded-full overflow-hidden"
        >
            <div className={`h-full ${color} ${indeterminate ? 'w-1/3 animate-pulse motion-reduce:animate-none' : 'transition-[width] duration-500 motion-reduce:transition-none'}`} style={indeterminate ? undefined : { width: `${percent}%` }} />
        </div>
    );
}

/** One task: live progress, usage and cost, errors with the next step, cancellation, execution steps and the structured result. */
export default function TaskDetail({ taskId, api, agents, setup, onChanged, onClosed }) {
    const { t } = useTranslation('agents');
    const tx = useBackendText();
    const { user } = useAuth();
    const { can } = useOrganization();
    const { showSuccess, showError } = useToast();

    const [task, setTask] = useState(null);
    const [steps, setSteps] = useState([]);
    const [resultResponse, setResultResponse] = useState(null);
    const [failure, setFailure] = useState(null); // notFound | network | ...
    const [confirmCancel, setConfirmCancel] = useState(false);
    const [cancelling, setCancelling] = useState(false);
    const alive = useRef(true);
    const lastSequence = useRef(0);
    const resultLoaded = useRef(false);

    const load = useCallback(async () => {
        try {
            const data = normalizeTask(await api.task(taskId));
            const fresh = await api.steps(taskId, lastSequence.current);
            if (!alive.current) return;
            setTask(data);
            if (fresh.length > 0) {
                lastSequence.current = fresh[fresh.length - 1].sequence;
                setSteps((current) => [...current, ...fresh]);
            }
            setFailure(null);
            if (isFinal(data) && !resultLoaded.current) {
                resultLoaded.current = true;
                try {
                    const response = await api.result(taskId);
                    if (alive.current) setResultResponse(response);
                } catch {
                    resultLoaded.current = false; // try again on the next poll
                }
            }
        } catch (error) {
            if (alive.current) setFailure(errorKind(error));
        }
    }, [api, taskId]);

    useEffect(() => {
        alive.current = true;
        const start = setTimeout(load, 0); // first fetch right after mount (async, so no state is set while the effect runs)
        return () => { alive.current = false; clearTimeout(start); };
    }, [load]);

    usePolling(load, POLL_MS, Boolean(task) && !isFinal(task) && failure !== 'notFound');

    const name = (key) => {
        if (!key) return t('list.supervised');
        const agent = agents.find((a) => a.key === key);
        return agent ? tx(`catalog.agent.${key}.name`, agent.name) : key;
    };

    if (failure === 'notFound') {
        return (
            <div role="alert" className={`${CARD} p-6 text-center`} data-testid="task-notfound">
                <CircleAlert size={28} className="mx-auto text-amber-600" aria-hidden="true" />
                <p className="font-bold text-gray-900 mt-2">{t('detail.notFoundTitle')}</p>
                <p className="text-sm text-gray-600 mt-1">{t('detail.notFoundBody')}</p>
                <button type="button" className="mt-3 inline-flex items-center justify-center px-4 py-2 min-h-[44px] rounded-lg border border-gray-200 hover:bg-gray-50 font-medium" onClick={onClosed}>{t('detail.back')}</button>
            </div>
        );
    }
    if (!task) {
        if (failure) {
            return (
                <div role="alert" className={`${CARD} p-6 text-center`}>
                    <ServerCrash size={28} className="mx-auto text-red-600" aria-hidden="true" />
                    <p className="text-sm text-gray-700 mt-2">{t(`errors.api.${failure}`)}</p>
                    <button type="button" className="mt-3 inline-flex items-center justify-center px-4 py-2 min-h-[44px] rounded-lg border border-gray-200 hover:bg-gray-50 font-medium" onClick={load}>{t('state.retry')}</button>
                </div>
            );
        }
        return <div role="status" aria-label={t('state.loading')} className="flex justify-center py-10"><div className="animate-spin rounded-full h-8 w-8 border-b-2 border-amber-600" /></div>;
    }

    const active = isActive(task);
    const percent = progressPercent(task.progress, task.status);
    const share = costShare(task.usage);
    const canCancel = active && !task.cancel_requested && (can('agents:manage') || (can('agents:run') && task.created_by_user_id === user?.id));
    const waitingForWorker = task.status === 'pending' && setup.worker && !setup.worker.online;
    const nextStep = task.error ? ERROR_NEXT_STEP[task.error.kind] : null;
    const duration = elapsedSeconds(task.started_at, isFinal(task) ? task.finished_at : null);

    const doCancel = async () => {
        setCancelling(true);
        try {
            const answer = await api.cancel(taskId);
            showSuccess(t(answer.status === 'cancelled' ? 'cancel.done' : 'cancel.requested'));
            await load();
            await onChanged();
        } catch (error) {
            showError(t(`errors.api.${errorKind(error)}`));
        } finally {
            setCancelling(false);
        }
    };

    return (
        <article className={`${CARD} p-5 space-y-5`} aria-labelledby="task-detail-heading" data-testid="task-detail">
            <header className="flex flex-wrap items-start justify-between gap-3">
                <div className="min-w-0">
                    <h2 id="task-detail-heading" className="text-lg font-bold text-gray-900 flex flex-wrap items-center gap-2">
                        {name(task.agent_key)} <StatusBadge status={task.status} />
                        {task.cancel_requested && active && <Badge tone="warn">{t('cancel.pending')}</Badge>}
                    </h2>
                    <p className="text-xs text-gray-500 mt-1">{t('detail.created', { date: formatDateTime(task.created_at) })}{duration !== null && ` · ${t('detail.duration', { time: formatDuration(duration) })}`}</p>
                </div>
                {canCancel && (
                    <button type="button" className={DANGER_BUTTON} disabled={cancelling} onClick={() => setConfirmCancel(true)}>
                        <Ban size={16} aria-hidden="true" />
                        {t('cancel.button')}
                    </button>
                )}
            </header>

            <p className="text-sm text-gray-800 whitespace-pre-line break-words bg-gray-50 rounded-lg p-3" data-testid="task-objective">{task.objective}</p>

            {waitingForWorker && <p role="status" className="text-sm text-red-900 bg-red-50 border border-red-200 rounded-lg px-3 py-2" data-testid="waiting-worker">{t('detail.waitingWorker')}</p>}
            {task.status === 'pending' && !waitingForWorker && !task.next_attempt_at && <p role="status" className="text-sm text-gray-600" data-testid="queued-note">{t('detail.queued')}</p>}
            {task.status === 'pending' && task.next_attempt_at && (
                <p role="status" className="text-sm text-amber-900 bg-amber-50 border border-amber-200 rounded-lg px-3 py-2" data-testid="retry-note">
                    {t('detail.retryScheduled', { attempt: task.attempts + 1, max: task.max_attempts, date: formatDateTime(task.next_attempt_at) })}
                </p>
            )}

            <section aria-labelledby="task-progress-heading" className="space-y-3">
                <h3 id="task-progress-heading" className="font-bold text-gray-900">{t('detail.progress')}</h3>
                <div>
                    <div className="flex justify-between text-sm text-gray-700 mb-1">
                        <span>{task.progress.total_steps ? t('detail.stepsOf', { done: task.progress.completed_steps, total: task.progress.total_steps }) : t('detail.planning')}</span>
                        <span>{percent === null ? '' : `${percent}%`}</span>
                    </div>
                    <ProgressBar percent={percent ?? 0} indeterminate={percent === null && active} label={t('detail.progress')} tone={task.status === 'completed' ? 'green' : (task.status === 'failed' ? 'red' : 'amber')} />
                </div>
                <dl className="grid grid-cols-2 sm:grid-cols-4 gap-2" data-testid="task-usage">
                    <div className="bg-gray-50 rounded-lg p-3"><dt className="text-xs text-gray-500">{t('usage.inputTokens')}</dt><dd className="font-medium text-gray-900">{formatCount(task.usage.input_tokens)}</dd></div>
                    <div className="bg-gray-50 rounded-lg p-3"><dt className="text-xs text-gray-500">{t('usage.outputTokens')}</dt><dd className="font-medium text-gray-900">{formatCount(task.usage.output_tokens)}</dd></div>
                    <div className="bg-gray-50 rounded-lg p-3"><dt className="text-xs text-gray-500">{t('usage.cost')}</dt><dd className="font-medium text-gray-900" data-testid="usage-cost">{formatUsd(task.usage.cost_usd)}</dd></div>
                    <div className="bg-gray-50 rounded-lg p-3"><dt className="text-xs text-gray-500">{t('usage.cap')}</dt><dd className="font-medium text-gray-900">{task.usage.max_cost_usd ? formatUsd(task.usage.max_cost_usd) : '—'}</dd></div>
                </dl>
                {share !== null && (
                    <div>
                        <ProgressBar percent={share} label={t('usage.capUsed', { percent: share })} tone={share >= 100 ? 'red' : 'amber'} />
                        <p className="text-xs text-gray-600 mt-1">{t('usage.capUsed', { percent: share })}</p>
                    </div>
                )}
                <p className="text-xs text-gray-500">{t('usage.attempts', { attempt: task.attempts, max: task.max_attempts })}</p>
            </section>

            {task.error && (
                <section role="alert" className="border border-red-200 bg-red-50 rounded-lg p-4" aria-labelledby="task-error-heading" data-testid="task-error">
                    <h3 id="task-error-heading" className="font-bold text-red-900 flex items-center gap-2"><CircleAlert size={18} aria-hidden="true" />{t(`errors.kind.${task.error.kind}`, { defaultValue: t('errors.kind.unknown') })}</h3>
                    {nextStep && (
                        <p className="text-sm text-red-900 mt-1">
                            {t(`errors.next.${nextStep}`)}{' '}
                            {(nextStep === 'aiSettings' || nextStep === 'aiLimits') && can('ai:read') && <Link to="/ai" className="font-medium underline underline-offset-2">{t('errors.next.openAi')}</Link>}
                        </p>
                    )}
                    {task.error.message && <p className="text-xs text-red-800 mt-2 break-words">{t('errors.serverMessage')}: {task.error.message}</p>}
                    {task.status === 'failed' && <p className="text-xs text-red-800 mt-1">{t('errors.partialHint')}</p>}
                </section>
            )}

            <section aria-labelledby="task-runs-heading">
                <h3 id="task-runs-heading" className="font-bold text-gray-900 mb-2">{t('detail.runs')}</h3>
                {task.runs.length === 0 ? <p className="text-sm text-gray-600">{t('detail.noRuns')}</p> : (
                    <ul className="space-y-2" data-testid="task-runs">
                        {task.runs.map((run) => (
                            <li key={run.id} className="flex flex-wrap items-center gap-2 border border-gray-200 rounded-lg px-3 py-2 text-sm">
                                <span className="font-medium text-gray-900">{name(run.agent_key)}</span>
                                {run.plan_step_id && <Badge tone="neutral">{run.plan_step_id}</Badge>}
                                <StatusBadge status={run.status} />
                                <span className="text-xs text-gray-500 ml-auto">{t('detail.runMeta', { count: run.step_count, cost: formatUsd(run.cost_usd) })}</span>
                                {run.error && <span className="w-full text-xs text-red-700">{t(`errors.kind.${run.error.kind}`, { defaultValue: t('errors.kind.unknown') })}</span>}
                            </li>
                        ))}
                    </ul>
                )}
            </section>

            <section aria-labelledby="task-result-heading">
                <h3 id="task-result-heading" className="font-bold text-gray-900 mb-2">{t('detail.result')}</h3>
                {!isFinal(task) && <p className="text-sm text-gray-600" data-testid="result-pending">{t('detail.resultPending')}</p>}
                {isFinal(task) && !resultResponse && <p role="status" className="text-sm text-gray-500">{t('state.loading')}</p>}
                {isFinal(task) && resultResponse && <ResultView resultResponse={resultResponse} agents={agents} />}
            </section>

            <section aria-labelledby="task-steps-heading">
                <h3 id="task-steps-heading" className="font-bold text-gray-900 mb-2">{t('detail.steps', { count: steps.length })}</h3>
                <StepsTimeline steps={steps} />
            </section>

            <ConfirmationModal
                isOpen={confirmCancel}
                onClose={() => setConfirmCancel(false)}
                onConfirm={doCancel}
                title={t('cancel.confirmTitle')}
                message={t(task.status === 'running' ? 'cancel.confirmRunning' : 'cancel.confirmPending')}
                confirmText={t('cancel.confirm')}
                cancelText={t('cancel.keep')}
                icon={Ban}
            />
        </article>
    );
}
