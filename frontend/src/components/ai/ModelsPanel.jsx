import React, { useState } from 'react';
import { useTranslation } from 'react-i18next';
import { Boxes, Pencil, Plus, Search, Trash2, TriangleAlert } from 'lucide-react';
import { PROVIDERS } from '../../lib/aiApi';
import { useToast } from '../../context/ToastContext';
import ConfirmationModal from '../ConfirmationModal';
import ModelDialog from './ModelDialog';
import DiscoverDialog from './DiscoverDialog';
import { Badge, CARD, DANGER_BUTTON, PRIMARY_BUTTON, SECONDARY_BUTTON, errorMessage, formatUsd, providerName } from './ui';

const CAPABILITIES = [['supports_tools', 'tools'], ['supports_vision', 'vision'], ['supports_json', 'json']];

function Prices({ model, t }) {
    const rows = [['input', model.input_price_usd_per_mtok], ['output', model.output_price_usd_per_mtok], ['cached', model.cached_input_price_usd_per_mtok]];
    return (
        <dl className="grid grid-cols-3 gap-2 text-sm">
            {rows.map(([key, value]) => (
                <div key={key}>
                    <dt className="text-xs text-gray-500">{t(`model.price.${key}`)}</dt>
                    <dd className={value === null ? 'text-gray-400' : 'text-gray-900 font-medium'}>
                        {value === null ? t('model.price.notSet') : formatUsd(value)}
                    </dd>
                </div>
            ))}
        </dl>
    );
}

export default function ModelsPanel({ api, models, providers, canManage, budgetActive, onChanged }) {
    const { t } = useTranslation('ai');
    const { showSuccess, showError } = useToast();
    const [editing, setEditing] = useState(null); // { model } | { prefill } | { }
    const [discovering, setDiscovering] = useState(false);
    const [toRemove, setToRemove] = useState(null);
    const [toggling, setToggling] = useState(null);

    const activeProviders = new Set(providers.filter((p) => p.has_active_credential).map((p) => p.provider));
    const label = (model) => model.display_name || model.model_id;

    const toggle = async (model) => {
        setToggling(model.id);
        try {
            await api.updateModel(model.id, { enabled: !model.enabled });
            await onChanged();
        } catch (error) {
            showError(errorMessage(t, error));
        } finally {
            setToggling(null);
        }
    };

    const remove = async () => {
        const model = toRemove;
        try {
            await api.removeModel(model.id);
            showSuccess(t('model.toast.removed', { model: label(model) }));
            await onChanged();
        } catch (error) {
            showError(errorMessage(t, error));
        }
    };

    return (
        <section aria-labelledby="ai-models-heading">
            <div className="flex flex-wrap items-start justify-between gap-3">
                <div className="min-w-0">
                    <h2 id="ai-models-heading" className="text-lg font-bold text-gray-900">{t('model.title')}</h2>
                    <p className="text-sm text-gray-600 mt-1 max-w-3xl">{t('model.subtitle')}</p>
                </div>
                {canManage && (
                    <div className="flex flex-wrap gap-2">
                        <button type="button" className={SECONDARY_BUTTON} onClick={() => setDiscovering(true)}>
                            <Search size={16} aria-hidden="true" />
                            {t('model.discover')}
                        </button>
                        <button type="button" className={PRIMARY_BUTTON} onClick={() => setEditing({})}>
                            <Plus size={16} aria-hidden="true" />
                            {t('model.add')}
                        </button>
                    </div>
                )}
            </div>
            <p className="text-xs text-gray-500 mt-2">{t('model.price.cachedNote')}</p>

            {models.length === 0 ? (
                <div className={`${CARD} p-8 mt-4 text-center`}>
                    <Boxes size={32} className="mx-auto text-amber-500" aria-hidden="true" />
                    <p className="font-medium text-gray-900 mt-3">{t('model.empty')}</p>
                    <p className="text-sm text-gray-600 mt-1">{t('model.emptyHint')}</p>
                </div>
            ) : (
                <ul className="mt-4 space-y-3">
                    {models.map((model) => {
                        const caps = CAPABILITIES.filter(([field]) => model[field]);
                        const unpriced = model.input_price_usd_per_mtok === null || model.output_price_usd_per_mtok === null;
                        const missingKey = !activeProviders.has(model.provider);
                        return (
                            <li key={model.id} className={`${CARD} p-4`} data-testid={`model-${model.model_id}`}>
                                <div className="flex flex-wrap items-start justify-between gap-3">
                                    <div className="min-w-0">
                                        <p className="font-bold text-gray-900 break-all">{label(model)}</p>
                                        <p className="text-sm text-gray-600 break-all">
                                            {providerName(model.provider)}
                                            {model.display_name && <span className="text-gray-400"> · {model.model_id}</span>}
                                        </p>
                                    </div>
                                    <div className="flex items-center gap-3">
                                        <Badge tone={model.enabled ? 'good' : 'neutral'}>{t(model.enabled ? 'model.enabled' : 'model.disabled')}</Badge>
                                        {canManage && (
                                            <button
                                                type="button"
                                                role="switch"
                                                aria-checked={model.enabled}
                                                aria-label={t('model.toggle', { model: label(model) })}
                                                disabled={toggling === model.id}
                                                onClick={() => toggle(model)}
                                                className="relative inline-flex h-6 w-11 flex-shrink-0 items-center rounded-full transition-colors focus-visible:outline focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-amber-600 disabled:opacity-60 min-h-[24px] data-[on=true]:bg-amber-600 bg-gray-300"
                                                data-on={model.enabled ? 'true' : 'false'}
                                            >
                                                <span className={`inline-block h-5 w-5 transform rounded-full bg-white shadow transition-transform ${model.enabled ? 'translate-x-5' : 'translate-x-0.5'}`} />
                                            </button>
                                        )}
                                    </div>
                                </div>

                                <div className="mt-3 grid gap-3 md:grid-cols-2">
                                    <div>
                                        <p className="text-xs text-gray-500 mb-1">{t('model.columns.capabilities')}</p>
                                        <div className="flex flex-wrap gap-1.5">
                                            {caps.length === 0 ? <Badge>{t('model.capability.none')}</Badge> : caps.map(([, key]) => <Badge key={key} tone="info">{t(`model.capability.${key}`)}</Badge>)}
                                        </div>
                                    </div>
                                    <div>
                                        <p className="text-xs text-gray-500 mb-1">{t('model.columns.prices')}</p>
                                        <Prices model={model} t={t} />
                                    </div>
                                </div>

                                {(missingKey || (unpriced && budgetActive)) && (
                                    <ul className="mt-3 space-y-1">
                                        {missingKey && <li className="flex items-center gap-2 text-sm text-amber-800"><TriangleAlert size={14} aria-hidden="true" />{t('model.noCredential')}</li>}
                                        {unpriced && budgetActive && <li className="flex items-center gap-2 text-sm text-amber-800"><TriangleAlert size={14} aria-hidden="true" />{t('model.unpriced')}</li>}
                                    </ul>
                                )}

                                {canManage && (
                                    <div className="mt-3 flex flex-wrap gap-2">
                                        <button type="button" className={SECONDARY_BUTTON} onClick={() => setEditing({ model })}>
                                            <Pencil size={16} aria-hidden="true" />
                                            {t('model.edit')}
                                            <span className="sr-only">{label(model)}</span>
                                        </button>
                                        <button type="button" className={DANGER_BUTTON} onClick={() => setToRemove(model)}>
                                            <Trash2 size={16} aria-hidden="true" />
                                            {t('model.remove')}
                                            <span className="sr-only">{label(model)}</span>
                                        </button>
                                    </div>
                                )}
                            </li>
                        );
                    })}
                </ul>
            )}

            {editing && (
                <ModelDialog
                    api={api}
                    model={editing.model || null}
                    prefill={editing.prefill || null}
                    defaultProvider={PROVIDERS.find((provider) => activeProviders.has(provider))}
                    onClose={() => setEditing(null)}
                    onSaved={async (saved, created) => {
                        showSuccess(t(created ? 'model.toast.added' : 'model.toast.updated', { model: saved.display_name || saved.model_id }));
                        setEditing(null);
                        await onChanged();
                    }}
                />
            )}
            {discovering && (
                <DiscoverDialog
                    api={api}
                    activeProviders={activeProviders}
                    onClose={() => setDiscovering(false)}
                    onPick={(prefill) => { setDiscovering(false); setEditing({ prefill }); }}
                />
            )}
            <ConfirmationModal
                isOpen={Boolean(toRemove)}
                onClose={() => setToRemove(null)}
                onConfirm={remove}
                title={t('model.removeConfirm.title')}
                message={toRemove ? t('model.removeConfirm.message', { model: label(toRemove) }) : ''}
                confirmText={t('model.removeConfirm.confirm')}
                cancelText={t('model.form.cancel')}
            />
        </section>
    );
}
