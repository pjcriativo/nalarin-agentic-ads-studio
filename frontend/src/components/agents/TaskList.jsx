import React from 'react';
import { useTranslation } from 'react-i18next';
import { STATUS_FILTERS, HISTORY_LIMIT } from '../../lib/agentsApi';
import { formatDateTime } from '../../i18n/format';
import useBackendText from '../../lib/useBackendText';
import { CARD, StatusBadge, formatUsd } from './ui';

/** Task history of the active organization with status filters. Filters apply to the server's latest tasks (most recent first). */
export default function TaskList({ tasks, filter, onFilter, counts, selectedId, onSelect, agents, loading }) {
    const { t } = useTranslation('agents');
    const tx = useBackendText();
    const nameOf = (key) => {
        if (!key) return t('list.supervised');
        const agent = agents.find((a) => a.key === key);
        return agent ? tx(`catalog.agent.${key}.name`, agent.name) : key;
    };

    return (
        <section className={`${CARD} p-4`} aria-labelledby="task-list-heading" data-testid="task-list">
            <h2 id="task-list-heading" className="text-lg font-bold text-gray-900">{t('list.title')}</h2>

            <div role="group" aria-label={t('list.filterLabel')} className="mt-3 flex flex-wrap gap-2">
                {STATUS_FILTERS.map((status) => {
                    const active = filter === status;
                    return (
                        <button
                            key={status}
                            type="button"
                            onClick={() => onFilter(status)}
                            aria-pressed={active}
                            data-testid={`filter-${status}`}
                            className={`px-3 min-h-[44px] rounded-full text-sm font-medium border transition-colors focus-visible:outline focus-visible:outline-2 focus-visible:outline-amber-600 ${active ? 'bg-amber-600 text-white border-amber-600' : 'bg-white text-gray-700 border-gray-300 hover:bg-gray-50'}`}
                        >
                            {t(`list.filter.${status}`)}{counts && counts[status] !== undefined ? ` (${counts[status]})` : ''}
                        </button>
                    );
                })}
            </div>

            {loading && <div role="status" aria-label={t('state.loading')} className="flex justify-center py-8"><div className="animate-spin rounded-full h-6 w-6 border-b-2 border-amber-600" /></div>}
            {!loading && tasks.length === 0 && <p className="text-sm text-gray-600 py-6 text-center" data-testid="list-empty">{t(filter === 'all' ? 'list.empty' : 'list.emptyFiltered')}</p>}

            {!loading && tasks.length > 0 && (
                <ul className="mt-3 divide-y divide-gray-100">
                    {tasks.map((task) => {
                        const selected = task.id === selectedId;
                        return (
                            <li key={task.id}>
                                <button
                                    type="button"
                                    onClick={() => onSelect(task.id)}
                                    aria-current={selected ? 'true' : undefined}
                                    data-testid={`task-${task.id}`}
                                    className={`w-full text-left px-3 py-3 min-h-[44px] rounded-lg transition-colors focus-visible:outline focus-visible:outline-2 focus-visible:outline-amber-600 ${selected ? 'bg-amber-50' : 'hover:bg-gray-50'}`}
                                >
                                    <span className="flex flex-wrap items-center gap-2">
                                        <StatusBadge status={task.status} />
                                        <span className="text-sm font-medium text-gray-900">{nameOf(task.agent_key)}</span>
                                    </span>
                                    <span className="block text-sm text-gray-700 mt-1 line-clamp-2 break-words">{task.objective}</span>
                                    <span className="block text-xs text-gray-500 mt-1">
                                        {formatDateTime(task.created_at)} · {formatUsd(task.usage?.cost_usd)}
                                    </span>
                                </button>
                            </li>
                        );
                    })}
                </ul>
            )}
            {!loading && tasks.length >= HISTORY_LIMIT && <p className="text-xs text-gray-500 mt-3">{t('list.limitNote', { count: HISTORY_LIMIT })}</p>}
        </section>
    );
}
