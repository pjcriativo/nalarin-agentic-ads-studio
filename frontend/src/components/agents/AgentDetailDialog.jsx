import React from 'react';
import { useTranslation } from 'react-i18next';
import { Bot, Globe, Network, Wrench } from 'lucide-react';
import useBackendText, { idKey } from '../../lib/useBackendText';
import { Badge, Dialog } from './ui';

const LIMIT_KEYS = ['max_steps', 'max_tool_calls', 'timeout_seconds', 'max_output_tokens'];

/** Everything the server says about one agent: tools (with the outside system each one reads), limits, model route, version. */
export default function AgentDetailDialog({ agent, tools, isSupervisor, onClose }) {
    const { t } = useTranslation('agents');
    const tx = useBackendText();
    const base = `catalog.agent.${agent.key}`;
    const byName = new Map((tools || []).map((tool) => [tool.name, tool]));
    const primary = agent.routing?.primary_model;
    const effective = agent.limits.effective;

    return (
        <Dialog title={tx(`${base}.name`, agent.name)} description={tx(`${base}.description`, agent.description)} icon={isSupervisor ? Network : Bot}
            onClose={onClose} closeLabel={t('dialog.close')} wide>
            <div className="space-y-5 text-sm">
                <section aria-labelledby="agent-tools-heading">
                    <h4 id="agent-tools-heading" className="font-bold text-gray-900 flex items-center gap-2"><Wrench size={16} className="text-amber-600" aria-hidden="true" />{t('detail.tools')}</h4>
                    {agent.allowed_tools.length === 0 ? (
                        <p className="text-gray-600 mt-1">{t('detail.noTools')}</p>
                    ) : (
                        <ul className="mt-2 space-y-2">
                            {agent.allowed_tools.map((name) => {
                                const tool = byName.get(name);
                                return (
                                    <li key={name} className="border border-gray-200 rounded-lg p-3" data-testid={`tool-${name}`}>
                                        <div className="flex flex-wrap items-center gap-2">
                                            <code className="text-xs bg-gray-100 rounded px-1.5 py-0.5 break-all">{name}</code>
                                            {tool?.external_system && <Badge tone="info" icon={Globe}>{t(`detail.external.${tool.external_system}`, { defaultValue: tool.external_system })}</Badge>}
                                            <Badge tone="good">{t('detail.readOnly')}</Badge>
                                        </div>
                                        {tool && <p className="text-gray-600 mt-1">{tx(`catalog.tool.${idKey(name)}`, tool.description)}</p>}
                                        {tool?.requires && <p className="text-xs text-amber-800 mt-1">{t('detail.requires')}</p>}
                                    </li>
                                );
                            })}
                        </ul>
                    )}
                </section>

                <section aria-labelledby="agent-limits-heading">
                    <h4 id="agent-limits-heading" className="font-bold text-gray-900">{t('detail.limits')}</h4>
                    <dl className="mt-2 grid grid-cols-2 sm:grid-cols-4 gap-2">
                        {LIMIT_KEYS.map((key) => (
                            <div key={key} className="bg-gray-50 rounded-lg p-3">
                                <dt className="text-xs text-gray-500">{t(`detail.limit.${key}`)}</dt>
                                <dd className="font-medium text-gray-900">
                                    {effective[key]}
                                    {effective[key] !== agent.limits.defaults[key] && <span className="text-xs text-amber-700"> {t('detail.customized')}</span>}
                                </dd>
                            </div>
                        ))}
                    </dl>
                </section>

                <section aria-labelledby="agent-model-heading">
                    <h4 id="agent-model-heading" className="font-bold text-gray-900">{t('detail.model')}</h4>
                    <p className="text-gray-600 mt-1">
                        {primary
                            ? t(agent.routing.inherited_from_default ? 'detail.modelInherited' : 'detail.modelOwn', { model: primary.model_id, provider: primary.provider })
                            : t('detail.modelNone')}
                        {agent.routing?.fallbacks > 0 && ` ${t('detail.fallbacks', { count: agent.routing.fallbacks })}`}
                    </p>
                    <p className="text-xs text-gray-500 mt-1">{t('detail.operation')}: <code>{agent.routing?.operation}</code></p>
                </section>

                <p className="text-xs text-gray-500">{t('detail.version', { version: agent.version, hash: agent.instructions_hash })}</p>
            </div>
        </Dialog>
    );
}
