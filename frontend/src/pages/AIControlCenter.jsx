import React, { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import { useTranslation } from 'react-i18next';
import { Boxes, CircleAlert, CircleCheck, Lock, Route, ServerCrash, Sparkles, TriangleAlert, Wallet, Wrench } from 'lucide-react';
import { useAuth } from '../context/AuthContext';
import { useOrganization } from '../context/OrganizationContext';
import { createAiApi } from '../lib/aiApi';
import { worstBudget } from '../lib/aiBudget';
import ProvidersPanel from '../components/ai/ProvidersPanel';
import ModelsPanel from '../components/ai/ModelsPanel';
import RoutingPanel from '../components/ai/RoutingPanel';
import UsagePanel from '../components/ai/UsagePanel';
import { CARD, SECONDARY_BUTTON } from '../components/ai/ui';

const TABS = [
    { id: 'providers', icon: Wrench },
    { id: 'models', icon: Boxes },
    { id: 'routing', icon: Route },
    { id: 'usage', icon: Wallet },
];
const EMPTY = { providers: [], credentials: [], models: [], routes: [], budgets: [] };

/**
 * AI Control Center: provider keys (BYOK), model catalog, routing and spending/usage for the ACTIVE
 * organization. Every call goes to the real `/api/v1/ai` API with the organization header; the
 * TenantBoundary remounts this page when the organization changes, so nothing carries over.
 * `can()` only decides what is SHOWN: the server authorizes every request on its own.
 */
export default function AIControlCenter() {
    const { t } = useTranslation('ai');
    const { authFetch } = useAuth();
    const { activeOrganization, can } = useOrganization();
    const canRead = can('ai:read');
    const canManage = can('ai:manage');
    const api = useMemo(() => createAiApi(authFetch), [authFetch]);

    const [tab, setTab] = useState('providers');
    const [loadStatus, setStatus] = useState('loading'); // loading | ready | error
    const [reloadKey, setReloadKey] = useState(0);
    const status = canRead ? loadStatus : 'forbidden';
    const [data, setData] = useState(EMPTY);
    const tabRefs = useRef({});

    // Loads everything the page shows. Setting state only AFTER the awaits keeps this an
    // external-system sync (no cascading renders); retry/refresh just bump `reloadKey`.
    useEffect(() => {
        if (!canRead) return undefined;
        let cancelled = false;
        (async () => {
            try {
                const [providers, models, routes, budgets, credentials] = await Promise.all([
                    api.providers(), api.models(), api.routing(), api.budgets(), canManage ? api.credentials() : Promise.resolve([]),
                ]);
                if (cancelled) return;
                setData({ providers, models, routes, budgets, credentials });
                setStatus('ready');
            } catch {
                if (!cancelled) setStatus((current) => (current === 'ready' ? current : 'error')); // a failed silent refresh keeps what is on screen
            }
        })();
        return () => { cancelled = true; };
    }, [api, canRead, canManage, reloadKey]);

    const refresh = useCallback(async () => { setReloadKey((key) => key + 1); }, []);
    const retry = () => { setStatus('loading'); setReloadKey((key) => key + 1); };

    const onTabKey = (event) => {
        const index = TABS.findIndex((item) => item.id === tab);
        let next = null;
        if (event.key === 'ArrowRight') next = TABS[(index + 1) % TABS.length];
        else if (event.key === 'ArrowLeft') next = TABS[(index - 1 + TABS.length) % TABS.length];
        else if (event.key === 'Home') next = TABS[0];
        else if (event.key === 'End') next = TABS[TABS.length - 1];
        if (!next) return;
        event.preventDefault();
        setTab(next.id);
        tabRefs.current[next.id]?.focus();
    };

    const organizationName = activeOrganization?.name || '';
    const activeProviders = data.providers.filter((provider) => provider.has_active_credential).length;
    const enabledModels = data.models.filter((model) => model.enabled).length;
    const worst = worstBudget(data.budgets);
    const budgetActive = data.budgets.some((budget) => budget.enabled);

    let overview = null;
    if (status === 'ready') {
        if (activeProviders === 0) overview = { tone: 'neutral', icon: CircleAlert, text: t('overview.notConfigured') };
        else if (enabledModels === 0) overview = { tone: 'neutral', icon: CircleAlert, text: t('overview.needsModel') };
        else overview = { tone: 'good', icon: CircleCheck, text: t('overview.ready', { count: activeProviders, models: enabledModels }) };
    }
    const TONE = {
        neutral: 'bg-amber-50 border-amber-200 text-amber-900',
        good: 'bg-green-50 border-green-200 text-green-900',
    };

    return (
        <div className="max-w-6xl mx-auto">
            <div className="mb-6">
                <h1 className="text-3xl font-bold text-gray-900 flex items-center gap-3">
                    <Sparkles size={32} className="text-amber-600" aria-hidden="true" />
                    {t('page.title')}
                </h1>
                <p className="text-gray-600 mt-2 max-w-3xl">{t('page.subtitle', { organization: organizationName })}</p>
            </div>

            {status === 'forbidden' && (
                <div role="alert" className={`${CARD} p-8 text-center max-w-xl`}>
                    <Lock size={32} className="mx-auto text-amber-600" aria-hidden="true" />
                    <h2 className="text-lg font-bold text-gray-900 mt-3">{t('page.noAccessTitle')}</h2>
                    <p className="text-sm text-gray-600 mt-2">{t('page.noAccessBody')}</p>
                </div>
            )}

            {status === 'loading' && (
                <div role="status" aria-label={t('state.loading')} className="flex justify-center py-16">
                    <div className="animate-spin rounded-full h-8 w-8 border-b-2 border-amber-600" />
                </div>
            )}

            {status === 'error' && (
                <div role="alert" className={`${CARD} p-8 text-center max-w-xl`}>
                    <ServerCrash size={32} className="mx-auto text-red-600" aria-hidden="true" />
                    <h2 className="text-lg font-bold text-gray-900 mt-3">{t('state.unavailableTitle')}</h2>
                    <p className="text-sm text-gray-600 mt-2">{t('state.unavailableBody')}</p>
                    <button type="button" className={`${SECONDARY_BUTTON} mt-4`} onClick={retry}>{t('state.retry')}</button>
                </div>
            )}

            {status === 'ready' && (
                <>
                    <div className="space-y-3 mb-6">
                        {overview && (
                            <p className={`flex items-start gap-2 text-sm border rounded-lg px-4 py-3 ${TONE[overview.tone]}`} data-testid="ai-overview">
                                <overview.icon size={18} className="flex-shrink-0 mt-0.5" aria-hidden="true" />
                                {overview.text}
                            </p>
                        )}
                        {worst && (
                            <p
                                role={worst.info.state === 'reached' ? 'alert' : 'status'}
                                data-testid="ai-budget-alert"
                                className={`flex items-start gap-2 text-sm border rounded-lg px-4 py-3 ${worst.info.state === 'reached' ? 'bg-red-50 border-red-200 text-red-900' : 'bg-amber-50 border-amber-200 text-amber-900'}`}
                            >
                                {worst.info.state === 'reached' ? <CircleAlert size={18} className="flex-shrink-0 mt-0.5" aria-hidden="true" /> : <TriangleAlert size={18} className="flex-shrink-0 mt-0.5" aria-hidden="true" />}
                                {worst.info.state === 'reached'
                                    ? t('overview.limitReached', { period: t(`budget.period.${worst.budget.period}`).toLowerCase() })
                                    : t('overview.nearLimit', { period: t(`budget.period.${worst.budget.period}`), percent: worst.info.percent })}
                            </p>
                        )}
                        {!canManage && <p className="text-sm text-gray-600 bg-gray-50 border border-gray-200 rounded-lg px-4 py-3" data-testid="ai-readonly">{t('page.readOnly')}</p>}
                    </div>

                    <div className="border-b border-gray-200 mb-6 overflow-x-auto">
                        <div role="tablist" aria-label={t('page.sections')} className="flex min-w-max" onKeyDown={onTabKey}>
                            {TABS.map(({ id, icon: Icon }) => {
                                const selected = tab === id;
                                return (
                                    <button
                                        key={id}
                                        ref={(node) => { tabRefs.current[id] = node; }}
                                        id={`ai-tab-${id}`}
                                        type="button"
                                        role="tab"
                                        aria-selected={selected}
                                        aria-controls={`ai-panel-${id}`}
                                        tabIndex={selected ? 0 : -1}
                                        onClick={() => setTab(id)}
                                        className={`flex items-center gap-2 px-4 sm:px-6 py-3 min-h-[44px] font-medium whitespace-nowrap transition-colors focus-visible:outline focus-visible:outline-2 focus-visible:-outline-offset-2 focus-visible:outline-amber-600 ${selected ? 'text-amber-700 border-b-2 border-amber-600' : 'text-gray-600 hover:text-gray-900'}`}
                                    >
                                        <Icon size={18} aria-hidden="true" />
                                        {t(`page.tab.${id}`)}
                                    </button>
                                );
                            })}
                        </div>
                    </div>

                    <div role="tabpanel" id={`ai-panel-${tab}`} aria-labelledby={`ai-tab-${tab}`} tabIndex={0} className="focus:outline-none">
                        {tab === 'providers' && (
                            <ProvidersPanel api={api} providers={data.providers} credentials={data.credentials} organizationName={organizationName} canManage={canManage} onChanged={refresh} />
                        )}
                        {tab === 'models' && (
                            <ModelsPanel api={api} models={data.models} providers={data.providers} canManage={canManage} budgetActive={budgetActive} onChanged={refresh} />
                        )}
                        {tab === 'routing' && (
                            <RoutingPanel api={api} models={data.models} routes={data.routes} canManage={canManage} onChanged={refresh} />
                        )}
                        {tab === 'usage' && (
                            <UsagePanel api={api} budgets={data.budgets} canManage={canManage} onBudgetsChanged={refresh} />
                        )}
                    </div>
                </>
            )}
        </div>
    );
}
