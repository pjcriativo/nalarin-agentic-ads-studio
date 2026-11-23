import React, { useState } from 'react';
import { useTranslation } from 'react-i18next';
import { ArrowDown, ArrowUp, Loader2, Plus, Route, X } from 'lucide-react';
import { MAX_FALLBACKS, OPERATIONS } from '../../lib/aiApi';
import { useToast } from '../../context/ToastContext';
import { Badge, CARD, INPUT_CLASS, OPERATION_KEY, PRIMARY_BUTTON, SECONDARY_BUTTON, errorMessage, providerName } from './ui';

const sameList = (a, b) => a.length === b.length && a.every((value, index) => value === b[index]);
const ICON_BUTTON = 'inline-flex items-center justify-center min-w-[44px] min-h-[44px] rounded-lg text-gray-600 hover:bg-gray-100 disabled:opacity-40 disabled:cursor-not-allowed focus-visible:outline focus-visible:outline-2 focus-visible:outline-amber-600';

export function RouteRow({ api, operation, route, models, canManage, onChanged, label }) {
    const { t } = useTranslation('ai');
    const { showSuccess, showError } = useToast();
    const savedPrimary = route?.primary_model_id || '';
    const savedFallbacks = route?.fallback_model_ids || [];
    const [primary, setPrimary] = useState(savedPrimary);
    const [fallbacks, setFallbacks] = useState(savedFallbacks);
    const [pending, setPending] = useState('');
    const [busy, setBusy] = useState(false);

    const byId = new Map(models.map((model) => [model.id, model]));
    const nameOf = (id) => {
        const model = byId.get(id);
        return model ? (model.display_name || model.model_id) : id;
    };
    const optionLabel = (model) => `${model.display_name || model.model_id} · ${providerName(model.provider)}${model.enabled ? '' : ` (${t('routing.disabledModel')})`}`;

    const dirty = primary !== savedPrimary || !sameList(fallbacks, savedFallbacks);
    const taken = new Set([primary, ...fallbacks]);
    const addable = models.filter((model) => !taken.has(model.id));
    const id = `ai-route-${operation.replace(/[^a-z0-9]/gi, '-')}`;
    const operationLabel = label || (OPERATION_KEY[operation] ? t(`routing.operation.${OPERATION_KEY[operation]}`) : operation); // other API operation names are shown as-is

    const move = (index, delta) => {
        const next = [...fallbacks];
        [next[index], next[index + delta]] = [next[index + delta], next[index]];
        setFallbacks(next);
    };

    const changePrimary = (value) => {
        setPrimary(value);
        setFallbacks((current) => current.filter((fallback) => fallback !== value)); // never both main and fallback
    };

    const save = async () => {
        setBusy(true);
        try {
            await api.saveRoute(operation, { primary_model_id: primary, fallback_model_ids: fallbacks });
            showSuccess(t('routing.toast.saved'));
            await onChanged();
        } catch (error) {
            showError(errorMessage(t, error));
        } finally {
            setBusy(false);
        }
    };
    const clear = async () => {
        setBusy(true);
        try {
            await api.clearRoute(operation);
            setPrimary('');
            setFallbacks([]);
            showSuccess(t('routing.toast.cleared'));
            await onChanged();
        } catch (error) {
            showError(errorMessage(t, error));
        } finally {
            setBusy(false);
        }
    };

    return (
        <li className={`${CARD} p-4`} data-testid={`route-${operation}`}>
            <div className="flex flex-wrap items-center justify-between gap-2">
                <h3 className="font-bold text-gray-900">{operationLabel}</h3>
                {dirty && <Badge tone="warn">{t('routing.unsaved')}</Badge>}
            </div>
            {!route && <p className="text-sm text-gray-500 mt-1">{t(operation === 'default' ? 'routing.implicit' : 'routing.usesDefault')}</p>}

            <div className="mt-3 grid gap-4 md:grid-cols-2">
                <div>
                    <label htmlFor={`${id}-primary`} className="block text-sm font-medium text-gray-700 mb-1">{t('routing.primary')}</label>
                    <select id={`${id}-primary`} value={primary} disabled={!canManage || busy} onChange={(event) => changePrimary(event.target.value)} className={INPUT_CLASS}>
                        <option value="">{t('routing.choose')}</option>
                        {models.map((model) => <option key={model.id} value={model.id}>{optionLabel(model)}</option>)}
                    </select>
                </div>

                <div>
                    <p id={`${id}-fallbacks`} className="block text-sm font-medium text-gray-700 mb-1">{t('routing.fallbacks')}</p>
                    {fallbacks.length === 0 ? (
                        <p className="text-sm text-gray-500">{t('routing.noFallbacks')}</p>
                    ) : (
                        <ol aria-labelledby={`${id}-fallbacks`} className="space-y-1">
                            {fallbacks.map((modelId, index) => (
                                <li key={modelId} className="flex items-center gap-1 rounded-lg border border-gray-200 pl-3">
                                    <span className="flex-1 text-sm text-gray-900 break-all">{index + 1}. {nameOf(modelId)}</span>
                                    {canManage && (
                                        <>
                                            <button type="button" className={ICON_BUTTON} disabled={index === 0 || busy} onClick={() => move(index, -1)} aria-label={t('routing.moveUp', { model: nameOf(modelId) })}><ArrowUp size={16} aria-hidden="true" /></button>
                                            <button type="button" className={ICON_BUTTON} disabled={index === fallbacks.length - 1 || busy} onClick={() => move(index, 1)} aria-label={t('routing.moveDown', { model: nameOf(modelId) })}><ArrowDown size={16} aria-hidden="true" /></button>
                                            <button type="button" className={ICON_BUTTON} disabled={busy} onClick={() => setFallbacks(fallbacks.filter((value) => value !== modelId))} aria-label={t('routing.removeFallback', { model: nameOf(modelId) })}><X size={16} aria-hidden="true" /></button>
                                        </>
                                    )}
                                </li>
                            ))}
                        </ol>
                    )}
                    {canManage && primary && (
                        fallbacks.length >= MAX_FALLBACKS ? (
                            <p className="text-xs text-gray-500 mt-2">{t('routing.maxFallbacks', { count: MAX_FALLBACKS })}</p>
                        ) : addable.length > 0 && (
                            <div className="flex gap-2 mt-2">
                                <select aria-label={t('routing.addFallback')} value={pending} onChange={(event) => setPending(event.target.value)} className={INPUT_CLASS}>
                                    <option value="">{t('routing.choose')}</option>
                                    {addable.map((model) => <option key={model.id} value={model.id}>{optionLabel(model)}</option>)}
                                </select>
                                <button type="button" className={SECONDARY_BUTTON} disabled={!pending} onClick={() => { setFallbacks([...fallbacks, pending]); setPending(''); }}>
                                    <Plus size={16} aria-hidden="true" />
                                    {t('routing.addFallback')}
                                </button>
                            </div>
                        )
                    )}
                </div>
            </div>

            {canManage && (
                <div className="mt-4 flex flex-wrap gap-2">
                    <button type="button" className={PRIMARY_BUTTON} disabled={!dirty || !primary || busy} onClick={save}>
                        {busy && <Loader2 size={16} className="animate-spin" aria-hidden="true" />}
                        {t('routing.save')}
                    </button>
                    {route && <button type="button" className={SECONDARY_BUTTON} disabled={busy} onClick={clear}>{t('routing.clear')}</button>}
                </div>
            )}
        </li>
    );
}

export default function RoutingPanel({ api, models, routes, canManage, onChanged }) {
    const { t } = useTranslation('ai');
    const routeOf = (operation) => routes.find((route) => route.operation === operation) || null;
    // Known operations first; routes for any other operation name the server holds are shown too.
    const operations = [...OPERATIONS, ...routes.map((route) => route.operation).filter((operation) => !OPERATIONS.includes(operation))];

    return (
        <section aria-labelledby="ai-routing-heading">
            <h2 id="ai-routing-heading" className="text-lg font-bold text-gray-900 flex items-center gap-2">
                <Route size={20} className="text-amber-600" aria-hidden="true" />
                {t('routing.title')}
            </h2>
            <p className="text-sm text-gray-600 mt-1 max-w-3xl">{t('routing.subtitle')}</p>
            {models.length === 0 ? (
                <p className={`${CARD} p-6 mt-4 text-sm text-gray-600`}>{t('routing.needsModels')}</p>
            ) : (
                <ul className="mt-4 space-y-3">
                    {operations.map((operation) => {
                        const route = routeOf(operation);
                        // `key` resets the draft whenever the saved route changes (after save/clear/reload)
                        return (
                            <RouteRow
                                key={`${operation}:${route?.primary_model_id || ''}:${(route?.fallback_model_ids || []).join(',')}`}
                                api={api}
                                operation={operation}
                                route={route}
                                models={models}
                                canManage={canManage}
                                onChanged={onChanged}
                            />
                        );
                    })}
                </ul>
            )}
        </section>
    );
}
