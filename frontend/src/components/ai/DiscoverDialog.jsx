import React, { useEffect, useState } from 'react';
import { useTranslation } from 'react-i18next';
import { Loader2, Search } from 'lucide-react';
import { PROVIDERS } from '../../lib/aiApi';
import { Badge, Dialog, INPUT_CLASS, SECONDARY_BUTTON, errorMessage, providerName } from './ui';

/**
 * Lists the model names the PROVIDER itself reports for this organization's key. Only the
 * names are confirmed; capabilities and prices are not, so picking one opens the model form
 * (prefilled with the name) for the administrator to confirm them. Nothing is added silently.
 */
export default function DiscoverDialog({ api, activeProviders, onClose, onPick }) {
    const { t } = useTranslation('ai');
    const usable = PROVIDERS.filter((provider) => activeProviders.has(provider));
    const [provider, setProvider] = useState(usable[0] || PROVIDERS[0]);
    const [state, setState] = useState({ status: usable.length ? 'loading' : 'nokey', items: [], error: '' });
    const hasKey = activeProviders.has(provider);

    useEffect(() => {
        if (!hasKey) return undefined;
        let alive = true;
        api.discoverModels(provider).then(
            (items) => { if (alive) setState({ status: 'ready', items, error: '' }); },
            (error) => { if (alive) setState({ status: 'error', items: [], error: errorMessage(t, error) }); },
        );
        return () => { alive = false; };
    }, [api, provider, hasKey, t]);

    const choose = (next) => {
        setProvider(next);
        setState({ status: activeProviders.has(next) ? 'loading' : 'nokey', items: [], error: '' });
    };

    const name = providerName(provider);
    return (
        <Dialog
            title={t('model.discoverDialog.title', { provider: name })}
            description={t('model.discoverDialog.intro', { provider: name })}
            icon={Search}
            onClose={onClose}
            closeLabel={t('model.discoverDialog.close')}
            wide
        >
            <label htmlFor="ai-discover-provider" className="block text-sm font-medium text-gray-700 mb-1">{t('model.form.provider')}</label>
            <select id="ai-discover-provider" value={provider} onChange={(event) => choose(event.target.value)} className={`${INPUT_CLASS} max-w-xs`}>
                {PROVIDERS.map((value) => <option key={value} value={value}>{providerName(value)}</option>)}
            </select>

            <div className="mt-4" aria-live="polite">
                {state.status === 'loading' && (
                    <p role="status" className="flex items-center gap-2 text-sm text-gray-600">
                        <Loader2 size={16} className="animate-spin" aria-hidden="true" />
                        {t('model.discoverDialog.loading', { provider: name })}
                    </p>
                )}
                {state.status === 'nokey' && <p className="text-sm text-amber-800 bg-amber-50 border border-amber-200 rounded-lg px-3 py-2">{t('model.discoverDialog.needsKey', { provider: name })}</p>}
                {state.status === 'error' && <p role="alert" className="text-sm text-red-700 bg-red-50 border border-red-200 rounded-lg px-3 py-2">{state.error}</p>}
                {state.status === 'ready' && state.items.length === 0 && <p className="text-sm text-gray-600">{t('model.discoverDialog.empty', { provider: name })}</p>}
                {state.status === 'ready' && state.items.length > 0 && (
                    <ul className="divide-y divide-gray-100 border border-gray-200 rounded-lg max-h-[45vh] overflow-auto">
                        {state.items.map((item) => (
                            <li key={item.model_id} className="flex flex-wrap items-center justify-between gap-2 px-3 py-2">
                                <span className="font-mono text-sm text-gray-900 break-all min-w-0">{item.display_name ? `${item.display_name} · ${item.model_id}` : item.model_id}</span>
                                {item.already_added ? (
                                    <Badge tone="neutral">{t('model.discoverDialog.already')}</Badge>
                                ) : (
                                    <button type="button" className={SECONDARY_BUTTON} onClick={() => onPick({ provider, model_id: item.model_id, display_name: item.display_name || '' })}>
                                        {t('model.discoverDialog.use')}
                                        <span className="sr-only">{item.model_id}</span>
                                    </button>
                                )}
                            </li>
                        ))}
                    </ul>
                )}
            </div>
        </Dialog>
    );
}
