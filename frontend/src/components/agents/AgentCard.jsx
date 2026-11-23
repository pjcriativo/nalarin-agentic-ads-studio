import React from 'react';
import { useTranslation } from 'react-i18next';
import { Bot, ClipboardList, Network, Play, Settings2, ShieldCheck } from 'lucide-react';
import useBackendText from '../../lib/useBackendText';
import { AVAILABILITY_TONE } from '../../lib/agentAvailability';
import { Badge, CARD, PRIMARY_BUTTON, SECONDARY_BUTTON } from './ui';

/** One agent (specialist or supervisor): what it is for, what it can use, whether it is really available, its real limits. */
export default function AgentCard({ agent, availability, isSupervisor = false, canRun, canManage, onDetails, onRun, onSettings }) {
    const { t } = useTranslation('agents');
    const tx = useBackendText();
    const Icon = isSupervisor ? Network : Bot;
    const base = `catalog.agent.${agent.key}`;
    const toolCount = agent.allowed_tools.length;
    const headingId = `agent-${agent.key}-name`;
    const blocked = availability.blocking;
    const name = tx(`${base}.name`, agent.name);

    return (
        <article className={`${CARD} p-5 flex flex-col ${isSupervisor ? 'border-amber-300 bg-amber-50/40' : ''}`} data-testid={`agent-${agent.key}`} aria-labelledby={headingId}>
            <div className="flex items-start gap-3">
                <div className="w-11 h-11 rounded-xl bg-amber-100 flex items-center justify-center flex-shrink-0">
                    <Icon size={22} className="text-amber-700" aria-hidden="true" />
                </div>
                <div className="min-w-0 flex-1">
                    <h3 id={headingId} className="font-bold text-gray-900 leading-snug break-words">{name}</h3>
                    <p className="text-xs text-gray-500 mt-0.5">v{agent.version}</p>
                </div>
                <Badge tone={AVAILABILITY_TONE[availability.state]} data-testid={`availability-${agent.key}`}>{t(`availability.${availability.state}`)}</Badge>
            </div>

            <p className="text-sm text-gray-700 mt-3">{tx(`${base}.description`, agent.description)}</p>

            <dl className="mt-3 text-sm space-y-2">
                <div>
                    <dt className="font-medium text-gray-900">{t('card.readiness')}</dt>
                    <dd className="text-gray-600">{tx(`${base}.readiness`, agent.readiness)}</dd>
                </div>
                {agent.limitations.length > 0 && (
                    <div>
                        <dt className="font-medium text-gray-900">{t('card.limitations')}</dt>
                        <dd>
                            <ul className="list-disc pl-5 text-gray-600 space-y-1">
                                {agent.limitations.map((text, index) => <li key={index}>{tx(`catalog.limitation.${agent.key}.${index}`, text)}</li>)}
                            </ul>
                        </dd>
                    </div>
                )}
            </dl>

            <p className="mt-3 text-sm text-gray-600 flex items-center gap-2">
                <ShieldCheck size={16} className="text-green-700 flex-shrink-0" aria-hidden="true" />
                {isSupervisor ? t('card.noToolsSupervisor') : t('card.toolsReadOnly', { count: toolCount })}
            </p>

            <div className="mt-auto pt-4 flex flex-wrap gap-2">
                <button type="button" className={SECONDARY_BUTTON} onClick={onDetails} aria-label={t('card.detailsFor', { name })}>
                    <ClipboardList size={16} aria-hidden="true" />
                    {t('card.details')}
                </button>
                {canRun && (
                    <button type="button" className={PRIMARY_BUTTON} onClick={onRun} disabled={blocked} title={blocked ? t(`card.cannotRun.${availability.state}`) : undefined}
                        aria-label={t('card.runFor', { name })}>
                        <Play size={16} aria-hidden="true" />
                        {t('card.run')}
                    </button>
                )}
                {canManage && (
                    <button type="button" className={SECONDARY_BUTTON} onClick={onSettings} aria-label={t('card.settingsFor', { name })}>
                        <Settings2 size={16} aria-hidden="true" />
                        {t('card.settings')}
                    </button>
                )}
            </div>
        </article>
    );
}
