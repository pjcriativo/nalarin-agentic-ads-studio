import React, { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import { useTranslation } from 'react-i18next';
import { ServerCrash } from 'lucide-react';
import { isActive } from '../../lib/agentsApi';
import usePolling from '../../lib/usePolling';
import NewTaskForm from './NewTaskForm';
import TaskDetail from './TaskDetail';
import TaskList from './TaskList';
import { CARD, SECONDARY_BUTTON } from './ui';

const FAST_MS = 4000;
const SLOW_MS = 20000;

/**
 * New task + history + live detail for the active organization. The history is the server's latest tasks; filters and counts are
 * computed from it. It polls quickly while something is pending/running and slowly otherwise, and stops when the screen closes.
 */
export default function TasksPanel({ api, agents, setup, canRun, agentKey, onAgentChange, selectedId, onSelect, onChanged }) {
    const { t } = useTranslation('agents');
    const [tasks, setTasks] = useState(null);
    const [failed, setFailed] = useState(false);
    const [filter, setFilter] = useState('all');
    const alive = useRef(true);
    const detailRef = useRef(null);

    const load = useCallback(async () => {
        try {
            const list = await api.tasks();
            if (!alive.current) return;
            setTasks(list);
            setFailed(false);
        } catch {
            if (alive.current) setFailed(true);
        }
    }, [api]);

    useEffect(() => {
        alive.current = true;
        const start = setTimeout(load, 0); // first fetch right after mount (async, so no state is set while the effect runs)
        return () => { alive.current = false; clearTimeout(start); };
    }, [load]);

    const hasActive = (tasks || []).some(isActive);
    usePolling(load, hasActive ? FAST_MS : SLOW_MS, tasks !== null);

    const counts = useMemo(() => {
        const base = { all: 0, pending: 0, running: 0, completed: 0, failed: 0, cancelled: 0 };
        (tasks || []).forEach((task) => { base.all += 1; if (base[task.status] !== undefined) base[task.status] += 1; });
        return base;
    }, [tasks]);
    const visible = (tasks || []).filter((task) => filter === 'all' || task.status === filter);

    const select = (id) => {
        onSelect(id);
        // stacked layout only (below xl the detail sits under the form): bring it into view, clear of the fixed top bar and the toasts
        if (id && typeof window !== 'undefined' && !window.matchMedia?.('(min-width: 1280px)')?.matches) {
            requestAnimationFrame(() => detailRef.current?.scrollIntoView?.({ block: 'start' }));
        }
    };
    const refresh = async () => { await load(); await onChanged?.(); };

    return (
        <div className="grid gap-6 xl:grid-cols-[minmax(0,2fr)_minmax(0,3fr)] items-start">
            <div className={`space-y-6 ${selectedId ? 'order-2 xl:order-1' : ''}`}>
                <NewTaskForm
                    agents={agents} setup={setup} canRun={canRun} agentKey={agentKey} onAgentChange={onAgentChange} agentsApi={api}
                    onStarted={async (task) => { await load(); select(task.id); await onChanged?.(); }}
                />
                {failed && tasks === null ? (
                    <div role="alert" className={`${CARD} p-6 text-center`}>
                        <ServerCrash size={28} className="mx-auto text-red-600" aria-hidden="true" />
                        <p className="font-bold text-gray-900 mt-2">{t('state.unavailableTitle')}</p>
                        <p className="text-sm text-gray-600 mt-1">{t('state.unavailableBody')}</p>
                        <button type="button" className={`${SECONDARY_BUTTON} mt-3`} onClick={load}>{t('state.retry')}</button>
                    </div>
                ) : (
                    <>
                        {failed && <p role="alert" className="text-sm text-red-700 bg-red-50 border border-red-200 rounded-lg px-3 py-2" data-testid="refresh-failed">{t('list.refreshFailed')}</p>}
                        <TaskList tasks={visible} filter={filter} onFilter={setFilter} counts={counts} selectedId={selectedId} onSelect={select} agents={agents} loading={tasks === null} />
                    </>
                )}
            </div>

            <div ref={detailRef} className={`min-w-0 scroll-mt-24 ${selectedId ? 'order-1 xl:order-2' : ''}`}>
                {selectedId ? (
                    <TaskDetail key={selectedId} taskId={selectedId} api={api} agents={agents} setup={setup} onChanged={refresh} onClosed={() => onSelect(null)} />
                ) : (
                    <p className={`${CARD} p-8 text-center text-sm text-gray-600`} data-testid="detail-placeholder">{t('detail.select')}</p>
                )}
            </div>
        </div>
    );
}
