import React, { useRef, useState } from 'react';
import { useTranslation } from 'react-i18next';
import { Loader2, Play } from 'lucide-react';
import { useToast } from '../../context/ToastContext';
import { MAX_OBJECTIVE_CHARS, MIN_OBJECTIVE_CHARS, errorKind, newIdempotencyKey, needsGoogleAds, parseContext } from '../../lib/agentsApi';
import { agentAvailability } from '../../lib/agentAvailability';
import useBackendText from '../../lib/useBackendText';
import { CARD, Field, INPUT_CLASS, PRIMARY_BUTTON } from './ui';

/**
 * Start a task: pick the agent (or let the supervisor plan), say what you want, optionally add context (a JSON object) and a
 * spending cap. Everything is validated here AND by the server. A fixed idempotency key per submitted form means a retry
 * after a network error can never create two tasks.
 */
export default function NewTaskForm({ agents, setup, canRun, agentKey, onAgentChange, agentsApi, onStarted }) {
    const { t } = useTranslation('agents');
    const tx = useBackendText();
    const { showSuccess, showError } = useToast();
    const [objective, setObjective] = useState('');
    const [contextText, setContextText] = useState('');
    const [cost, setCost] = useState('');
    const [busy, setBusy] = useState(false);
    const [touched, setTouched] = useState(false);
    const idempotency = useRef(newIdempotencyKey());

    const selected = agents.find((agent) => agent.key === agentKey) || null;
    const availability = selected ? agentAvailability(selected, setup) : null;
    const trimmed = objective.trim();
    const objectiveError = trimmed.length < MIN_OBJECTIVE_CHARS ? t('form.objectiveShort', { min: MIN_OBJECTIVE_CHARS }) : (trimmed.length > MAX_OBJECTIVE_CHARS ? t('form.objectiveLong', { max: MAX_OBJECTIVE_CHARS }) : null);
    const context = parseContext(contextText);
    const contextError = context.error ? t(`form.context.${context.error}`) : null;
    const costNumber = cost.trim() === '' ? null : Number(cost);
    const costError = costNumber !== null && (!Number.isFinite(costNumber) || costNumber <= 0) ? t('form.costInvalid') : null;
    const invalid = Boolean(objectiveError || contextError || costError);
    const blocked = Boolean(availability?.blocking);

    const warnings = [];
    if (availability?.state === 'disabled') warnings.push('agentDisabled');
    else if (availability?.state === 'noModel' || availability?.state === 'noCredential') warnings.push('noModel');
    if (selected && needsGoogleAds(selected.allowed_tools) && setup.google && !setup.google.connected) warnings.push('noGoogle');
    if (!selected && setup.google && !setup.google.connected) warnings.push('noGoogleSupervised');
    if (setup.worker && !setup.worker.online) warnings.push('noWorker');

    const submit = async (event) => {
        event.preventDefault();
        setTouched(true);
        if (invalid || blocked || !canRun) return;
        setBusy(true);
        try {
            const body = { objective: trimmed, idempotency_key: idempotency.current };
            if (agentKey) body.agent_key = agentKey;
            if (context.value) body.context = context.value;
            if (costNumber !== null) body.max_cost_usd = costNumber;
            const task = await agentsApi.start(body);
            idempotency.current = newIdempotencyKey(); // the next submission is a NEW task
            showSuccess(t('form.started'));
            setObjective('');
            setContextText('');
            setCost('');
            setTouched(false);
            onStarted(task);
        } catch (error) {
            showError(t(`errors.api.${errorKind(error)}`));
        } finally {
            setBusy(false);
        }
    };

    if (!canRun) {
        return <p className={`${CARD} p-4 text-sm text-gray-600`} data-testid="agents-readonly">{t('form.noPermission')}</p>;
    }

    return (
        <form onSubmit={submit} className={`${CARD} p-5 space-y-4`} noValidate aria-labelledby="new-task-heading" data-testid="new-task-form">
            <div>
                <h2 id="new-task-heading" className="text-lg font-bold text-gray-900">{t('form.title')}</h2>
                <p className="text-sm text-gray-600 mt-1">{t('form.subtitle')}</p>
            </div>

            <Field id="task-agent" label={t('form.agent')} hint={agentKey ? undefined : t('form.agentAutoHint')}>
                <select id="task-agent" value={agentKey} onChange={(event) => onAgentChange(event.target.value)} className={INPUT_CLASS}>
                    <option value="">{t('form.agentAuto')}</option>
                    {agents.map((agent) => (
                        <option key={agent.key} value={agent.key}>
                            {tx(`catalog.agent.${agent.key}.name`, agent.name)}{agent.enabled ? '' : ` (${t('availability.disabled')})`}
                        </option>
                    ))}
                </select>
            </Field>

            <Field id="task-objective" label={t('form.objective')} error={touched ? objectiveError : null}
                hint={t('form.objectiveHint', { count: trimmed.length, max: MAX_OBJECTIVE_CHARS })}>
                <textarea id="task-objective" rows={4} value={objective} onChange={(event) => setObjective(event.target.value)} className={INPUT_CLASS} maxLength={MAX_OBJECTIVE_CHARS + 200}
                    placeholder={t('form.objectivePlaceholder')} aria-invalid={touched && Boolean(objectiveError)} aria-describedby={touched && objectiveError ? 'task-objective-error' : 'task-objective-hint'} />
            </Field>

            <Field id="task-context" label={t('form.contextLabel')} error={contextError} hint={t('form.contextHint')}>
                <textarea id="task-context" rows={3} value={contextText} onChange={(event) => setContextText(event.target.value)} className={`${INPUT_CLASS} font-mono text-sm`}
                    placeholder={t('form.contextPlaceholder')} spellCheck={false} aria-invalid={Boolean(contextError)} aria-describedby={contextError ? 'task-context-error' : 'task-context-hint'} />
            </Field>

            <Field id="task-cost" label={t('form.costLabel')} error={costError} hint={t('form.costHint')}>
                <div className="relative max-w-[12rem]">
                    <span className="absolute inset-y-0 left-3 flex items-center text-gray-500" aria-hidden="true">US$</span>
                    <input id="task-cost" type="number" inputMode="decimal" min="0" step="0.01" value={cost} onChange={(event) => setCost(event.target.value)} className={`${INPUT_CLASS} pl-12`}
                        placeholder={t('form.costPlaceholder')} aria-invalid={Boolean(costError)} aria-describedby={costError ? 'task-cost-error' : 'task-cost-hint'} />
                </div>
            </Field>

            {warnings.length > 0 && (
                <ul className="space-y-2" data-testid="form-warnings">
                    {warnings.map((id) => (
                        <li key={id} className="text-sm text-amber-900 bg-amber-50 border border-amber-200 rounded-lg px-3 py-2">{t(`form.warning.${id}`)}</li>
                    ))}
                </ul>
            )}

            <button type="submit" className={PRIMARY_BUTTON} disabled={busy || blocked}>
                {busy ? <Loader2 size={16} className="animate-spin" aria-hidden="true" /> : <Play size={16} aria-hidden="true" />}
                {t('form.submit')}
            </button>
        </form>
    );
}
