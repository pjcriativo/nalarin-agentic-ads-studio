import React, { useState } from 'react';
import { useTranslation } from 'react-i18next';
import { agentAvailability } from '../../lib/agentAvailability';
import AgentCard from './AgentCard';
import AgentDetailDialog from './AgentDetailDialog';
import AgentSettingsDialog from './AgentSettingsDialog';

/** The supervisor (full width) and the six specialists, each with its real availability for THIS organization. */
export default function AgentsCatalog({ supervisor, agents, tools, setup, canRun, canManage, agentsApi, onRun, onChanged }) {
    const { t } = useTranslation('agents');
    const [details, setDetails] = useState(null);   // { agent, isSupervisor }
    const [settings, setSettings] = useState(null); // agent

    const card = (agent, isSupervisor = false) => (
        <AgentCard
            key={agent.key}
            agent={agent}
            isSupervisor={isSupervisor}
            availability={agentAvailability(agent, setup)}
            canRun={canRun}
            canManage={canManage && !isSupervisor}
            onDetails={() => setDetails({ agent, isSupervisor })}
            onRun={() => onRun(isSupervisor ? '' : agent.key)}
            onSettings={() => setSettings(agent)}
        />
    );

    return (
        <section aria-labelledby="agents-catalog-heading">
            <h2 id="agents-catalog-heading" className="text-lg font-bold text-gray-900">{t('catalog.title')}</h2>
            <p className="text-sm text-gray-600 mt-1 max-w-3xl">{t('catalog.subtitle')}</p>

            {supervisor && <div className="mt-4">{card(supervisor, true)}</div>}
            <div className="mt-4 grid gap-4 md:grid-cols-2 xl:grid-cols-3" data-testid="agents-grid">
                {agents.map((agent) => card(agent))}
            </div>
            {canManage && <p className="text-xs text-gray-500 mt-3">{t('catalog.supervisorNote')}</p>}

            {details && <AgentDetailDialog agent={details.agent} isSupervisor={details.isSupervisor} tools={tools} onClose={() => setDetails(null)} />}
            {settings && <AgentSettingsDialog agent={settings} agentsApi={agentsApi} onClose={() => setSettings(null)} onSaved={onChanged} />}
        </section>
    );
}
