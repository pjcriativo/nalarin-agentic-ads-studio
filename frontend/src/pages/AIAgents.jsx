import React, { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import { useSearchParams } from 'react-router-dom';
import { useTranslation } from 'react-i18next';
import { Bot, ClipboardList, Lock, ServerCrash, Users } from 'lucide-react';
import { useAuth } from '../context/AuthContext';
import { useOrganization } from '../context/OrganizationContext';
import { createAiApi } from '../lib/aiApi';
import { createAgentsApi } from '../lib/agentsApi';
import { setupAlerts } from '../lib/agentAvailability';
import usePolling from '../lib/usePolling';
import AgentsCatalog from '../components/agents/AgentsCatalog';
import ReadinessAlerts from '../components/agents/ReadinessAlerts';
import TasksPanel from '../components/agents/TasksPanel';
import { CARD, SECONDARY_BUTTON } from '../components/agents/ui';

const TABS = [
    { id: 'agents', icon: Users },
    { id: 'tasks', icon: ClipboardList },
];
const WORKER_POLL_MS = 15000;

/**
 * AI Agents: the supervisor and six specialists of the ACTIVE organization, a form to start tasks, live progress, cost, history and
 * structured results (real / estimate / hypothesis). Everything comes from the real `/api/v1/agents` API with the organization
 * header; the TenantBoundary remounts this page when the organization changes, so nothing carries over. `can()` only decides
 * what is SHOWN: the server authorizes every call. Setup warnings (no worker, no AI model, no Google Ads account, spending limit)
 * are derived from real endpoints; an unreadable endpoint means "unknown", never a made-up value.
 */
export default function AIAgents() {
    const { t } = useTranslation('agents');
    const { authFetch } = useAuth();
    const { activeOrganization, can } = useOrganization();
    const canRead = can('agents:read');
    const canRun = can('agents:run');
    const canManage = can('agents:manage');
    const canAiRead = can('ai:read');
    const canConnectionsRead = can('connections:read');
    const api = useMemo(() => createAgentsApi(authFetch), [authFetch]);
    const aiApi = useMemo(() => createAiApi(authFetch), [authFetch]);

    const [params, setParams] = useSearchParams();
    const tab = params.get('tab') === 'tasks' || params.get('task') ? 'tasks' : 'agents';
    const selectedId = params.get('task');
    const [agentKey, setAgentKey] = useState('');

    const [loadStatus, setStatus] = useState('loading'); // loading | ready | error
    const status = canRead ? loadStatus : 'forbidden';
    const [reloadKey, setReloadKey] = useState(0);
    const [data, setData] = useState({ agents: [], supervisor: null, tools: [] });
    const [setupState, setSetupState] = useState({ ai: null, google: null, budgets: [] });
    const [worker, setWorker] = useState(null);
    const alive = useRef(true);
    const tabRefs = useRef({});

    const loadWorker = useCallback(async () => {
        try {
            const status_ = await api.workerStatus();
            if (alive.current) setWorker(typeof status_?.online === 'boolean' ? status_ : null); // anything else = unknown
        } catch {
            if (alive.current) setWorker(null); // unknown: no banner, no claim either way
        }
    }, [api]);

    useEffect(() => {
        if (!canRead) return undefined;
        alive.current = true;
        (async () => {
            try {
                const [agents, supervisor, tools] = await Promise.all([
                    api.agents(), api.supervisor().catch(() => null), api.tools().then((r) => r.available || []).catch(() => []),
                ]);
                if (!alive.current) return;
                setData({ agents: agents.filter((agent) => agent && agent.key), supervisor: supervisor && supervisor.key ? supervisor : null, tools });
                setStatus('ready');
            } catch {
                if (alive.current) setStatus((current) => (current === 'ready' ? current : 'error'));
            }
            // organization setup: each part is optional and only read when the role may read it
            const [providers, models, budgets, google] = await Promise.all([
                canAiRead ? aiApi.providers().catch(() => null) : null,
                canAiRead ? aiApi.models().catch(() => null) : null,
                canAiRead ? aiApi.budgets().catch(() => null) : null,
                canConnectionsRead ? api.googleConnected() : null,
            ]);
            if (!alive.current) return;
            setSetupState({
                ai: providers && models ? { hasCredential: providers.some((p) => p.has_active_credential), hasModel: models.some((m) => m.enabled) } : null,
                google,
                budgets: budgets || [],
            });
        })();
        const worker_ = setTimeout(loadWorker, 0);
        return () => { alive.current = false; clearTimeout(worker_); };
    }, [api, aiApi, canRead, canAiRead, canConnectionsRead, reloadKey, loadWorker]);

    usePolling(loadWorker, WORKER_POLL_MS, canRead && status === 'ready');

    const setup = useMemo(() => ({ ai: setupState.ai, google: setupState.google, worker }), [setupState, worker]);
    const alerts = useMemo(() => setupAlerts({ setup, agents: data.agents, budgets: setupState.budgets }), [setup, data.agents, setupState.budgets]);

    const refresh = useCallback(async () => { setReloadKey((key) => key + 1); }, []);
    const retry = () => { setStatus('loading'); setReloadKey((key) => key + 1); };

    const setTab = (next) => {
        const copy = new URLSearchParams(params);
        if (next === 'agents') { copy.delete('tab'); copy.delete('task'); } else copy.set('tab', 'tasks');
        setParams(copy, { replace: true });
    };
    const selectTask = (id) => {
        const copy = new URLSearchParams(params);
        copy.set('tab', 'tasks');
        if (id) copy.set('task', id); else copy.delete('task');
        setParams(copy, { replace: true });
    };
    const runAgent = (key) => { setAgentKey(key); setTab('tasks'); };

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

    return (
        <div className="max-w-7xl mx-auto">
            <div className="mb-6">
                <h1 className="text-3xl font-bold text-gray-900 flex items-center gap-3">
                    <Bot size={32} className="text-amber-600" aria-hidden="true" />
                    {t('page.title')}
                </h1>
                <p className="text-gray-600 mt-2 max-w-3xl">{t('page.subtitle', { organization: activeOrganization?.name || '' })}</p>
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
                    <p className="text-sm text-green-900 bg-green-50 border border-green-200 rounded-lg px-4 py-3 mb-6" data-testid="agents-safety">{t('page.safety')}</p>
                    <ReadinessAlerts alerts={alerts} />
                    {!canRun && <p className="text-sm text-gray-600 bg-gray-50 border border-gray-200 rounded-lg px-4 py-3 mb-6" data-testid="agents-readonly-banner">{t('page.readOnly')}</p>}

                    <div className="border-b border-gray-200 mb-6 overflow-x-auto">
                        <div role="tablist" aria-label={t('page.sections')} className="flex min-w-max" onKeyDown={onTabKey}>
                            {TABS.map(({ id, icon: Icon }) => {
                                const selected = tab === id;
                                return (
                                    <button
                                        key={id}
                                        ref={(node) => { tabRefs.current[id] = node; }}
                                        id={`agents-tab-${id}`}
                                        type="button"
                                        role="tab"
                                        aria-selected={selected}
                                        aria-controls={`agents-panel-${id}`}
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

                    <div role="tabpanel" id={`agents-panel-${tab}`} aria-labelledby={`agents-tab-${tab}`} tabIndex={0} className="focus:outline-none">
                        {tab === 'agents' && (
                            <AgentsCatalog
                                supervisor={data.supervisor} agents={data.agents} tools={data.tools} setup={setup}
                                canRun={canRun} canManage={canManage} agentsApi={api} onRun={runAgent} onChanged={refresh}
                            />
                        )}
                        {tab === 'tasks' && (
                            <TasksPanel
                                api={api} agents={data.agents} setup={setup} canRun={canRun} agentKey={agentKey} onAgentChange={setAgentKey}
                                selectedId={selectedId} onSelect={selectTask} onChanged={loadWorker}
                            />
                        )}
                    </div>
                </>
            )}
        </div>
    );
}
