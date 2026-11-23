import React, { useEffect, useMemo, useState } from 'react';
import { useTranslation } from 'react-i18next';
import { Loader2, Settings2 } from 'lucide-react';
import { useAuth } from '../../context/AuthContext';
import { useOrganization } from '../../context/OrganizationContext';
import { useToast } from '../../context/ToastContext';
import { createAiApi } from '../../lib/aiApi';
import { errorKind } from '../../lib/agentsApi';
import useBackendText from '../../lib/useBackendText';
import { RouteRow } from '../ai/RoutingPanel';
import { Dialog, Field, INPUT_CLASS, PRIMARY_BUTTON, SECONDARY_BUTTON } from './ui';

const STEP_RANGE = [1, 20];
const TIMEOUT_RANGE = [10, 600];
const toInt = (value) => (value === '' || value === null || value === undefined ? null : Number(value));
const outOfRange = (value, [min, max]) => value !== null && (!Number.isInteger(value) || value < min || value > max);

/** Enable/disable one agent, tune its (server-capped) limits and choose its model through the AI Control Center route. */
export default function AgentSettingsDialog({ agent, agentsApi, onClose, onSaved }) {
    const { t } = useTranslation('agents');
    const tx = useBackendText();
    const { authFetch } = useAuth();
    const { can } = useOrganization();
    const { showSuccess, showError } = useToast();
    const aiApi = useMemo(() => createAiApi(authFetch), [authFetch]);
    const canAiRead = can('ai:read');
    const canAiManage = can('ai:manage');

    const customized = (key) => (agent.limits.effective[key] === agent.limits.defaults[key] ? '' : String(agent.limits.effective[key]));
    const [enabled, setEnabled] = useState(agent.enabled);
    const [steps, setSteps] = useState(customized('max_steps'));
    const [timeout, setTimeoutValue] = useState(customized('timeout_seconds'));
    const [busy, setBusy] = useState(false);
    const [models, setModels] = useState(null);
    const [routes, setRoutes] = useState([]);
    const [aiFailed, setAiFailed] = useState(false);
    const [reload, setReload] = useState(0);

    useEffect(() => {
        if (!canAiRead) return undefined;
        let cancelled = false;
        (async () => {
            try {
                const [m, r] = await Promise.all([aiApi.models(), aiApi.routing()]);
                if (!cancelled) { setModels(m); setRoutes(r); setAiFailed(false); }
            } catch {
                if (!cancelled) setAiFailed(true);
            }
        })();
        return () => { cancelled = true; };
    }, [aiApi, canAiRead, reload]);

    const stepsNumber = toInt(steps);
    const timeoutNumber = toInt(timeout);
    const stepsError = outOfRange(stepsNumber, STEP_RANGE) ? t('settings.stepsInvalid', { min: STEP_RANGE[0], max: STEP_RANGE[1] }) : null;
    const timeoutError = outOfRange(timeoutNumber, TIMEOUT_RANGE) ? t('settings.timeoutInvalid', { min: TIMEOUT_RANGE[0], max: TIMEOUT_RANGE[1] }) : null;
    const operation = agent.routing?.operation || `agent.${agent.key}`;
    const route = routes.find((item) => item.operation === operation) || null;
    const name = tx(`catalog.agent.${agent.key}.name`, agent.name);

    const save = async (event) => {
        event.preventDefault();
        if (stepsError || timeoutError) return;
        setBusy(true);
        try {
            await agentsApi.saveSettings(agent.key, { enabled, max_steps: stepsNumber, timeout_seconds: timeoutNumber });
            showSuccess(t('settings.saved'));
            await onSaved();
            onClose();
        } catch (error) {
            showError(t(`errors.api.${errorKind(error)}`));
        } finally {
            setBusy(false);
        }
    };

    return (
        <Dialog title={t('settings.title', { name })} description={t('settings.subtitle')} icon={Settings2} onClose={onClose} closeLabel={t('dialog.close')} wide>
            <form onSubmit={save} className="space-y-5" noValidate>
                <label className="flex items-start gap-3 min-h-[44px]">
                    <input type="checkbox" checked={enabled} onChange={(event) => setEnabled(event.target.checked)} className="mt-1 h-5 w-5 rounded border-gray-300 text-amber-600 focus:ring-amber-500" />
                    <span>
                        <span className="block font-medium text-gray-900">{t('settings.enabled')}</span>
                        <span className="block text-sm text-gray-600">{t('settings.enabledHint')}</span>
                    </span>
                </label>

                <div className="grid gap-4 sm:grid-cols-2">
                    <Field id="agent-max-steps" label={t('settings.maxSteps')} error={stepsError} hint={t('settings.defaultHint', { value: agent.limits.defaults.max_steps })}>
                        <input id="agent-max-steps" type="number" inputMode="numeric" min={STEP_RANGE[0]} max={STEP_RANGE[1]} value={steps} onChange={(event) => setSteps(event.target.value)}
                            placeholder={String(agent.limits.defaults.max_steps)} className={INPUT_CLASS} aria-invalid={Boolean(stepsError)} />
                    </Field>
                    <Field id="agent-timeout" label={t('settings.timeout')} error={timeoutError} hint={t('settings.defaultHint', { value: agent.limits.defaults.timeout_seconds })}>
                        <input id="agent-timeout" type="number" inputMode="numeric" min={TIMEOUT_RANGE[0]} max={TIMEOUT_RANGE[1]} value={timeout} onChange={(event) => setTimeoutValue(event.target.value)}
                            placeholder={String(agent.limits.defaults.timeout_seconds)} className={INPUT_CLASS} aria-invalid={Boolean(timeoutError)} />
                    </Field>
                </div>

                <div className="flex flex-wrap gap-2">
                    <button type="submit" className={PRIMARY_BUTTON} disabled={busy || Boolean(stepsError || timeoutError)}>
                        {busy && <Loader2 size={16} className="animate-spin" aria-hidden="true" />}
                        {t('settings.save')}
                    </button>
                    <button type="button" className="inline-flex items-center justify-center px-4 py-2 min-h-[44px] text-gray-700 hover:bg-gray-100 rounded-lg font-medium border border-gray-200" onClick={onClose}>{t('dialog.cancel')}</button>
                </div>
            </form>

            <section className="mt-6 border-t border-gray-200 pt-4" aria-labelledby="agent-model-route-heading">
                <h4 id="agent-model-route-heading" className="font-bold text-gray-900">{t('settings.modelTitle')}</h4>
                <p className="text-sm text-gray-600 mt-1 mb-3">{t('settings.modelHint')}</p>
                {!canAiRead && <p className="text-sm text-gray-600">{t('settings.modelNoAccess')}</p>}
                {canAiRead && aiFailed && <p role="alert" className="text-sm text-red-700">{t('settings.modelLoadFailed')}</p>}
                {canAiRead && !aiFailed && models === null && <p role="status" className="text-sm text-gray-500">{t('settings.modelLoading')}</p>}
                {canAiRead && models !== null && models.length === 0 && <p className="text-sm text-gray-600">{t('settings.modelNone')}</p>}
                {canAiRead && models !== null && models.length > 0 && (
                    <ul>
                        <RouteRow
                            key={`${route?.primary_model_id || ''}:${(route?.fallback_model_ids || []).join(',')}`}
                            api={aiApi} operation={operation} route={route} models={models} canManage={canAiManage} label={name}
                            onChanged={async () => { setReload((n) => n + 1); await onSaved(); }}
                        />
                    </ul>
                )}
            </section>
        </Dialog>
    );
}
