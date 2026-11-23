import React from 'react';
import { useTranslation } from 'react-i18next';
import i18n from '../../i18n';
import { CircleAlert, FileJson, Info } from 'lucide-react';
import { countDataKinds } from '../../lib/agentsApi';
import useBackendText from '../../lib/useBackendText';
import { Badge, CARD, DataKindBadge } from './ui';

const MAX_DEPTH = 5;
const SERVER_DISCLAIMER = 'Results are model-generated proposals for human review. Nothing was published or spent.';

/** Known enum words (intent, verdict, severity...) are translated; any other text is shown exactly as the model wrote it. */
const enumText = (t, value) => (typeof value === 'string' && /^[a-z_]{3,24}$/.test(value) && i18n.exists(`agents:result.enum.${value}`) ? t(`result.enum.${value}`) : value);
const humanize = (name) => String(name).replace(/_/g, ' ').replace(/^./, (c) => c.toUpperCase());
const isPrimitive = (value) => value === null || ['string', 'number', 'boolean'].includes(typeof value);

function FieldLabel({ name }) {
    const { t } = useTranslation('agents');
    return t(`result.field.${name}`, { defaultValue: humanize(name) });
}

function MetricChips({ metrics }) {
    if (!Array.isArray(metrics) || metrics.length === 0) return null;
    return (
        <ul className="flex flex-wrap gap-1.5 mt-2" aria-label="metrics">
            {metrics.map((metric, index) => (
                <li key={index} className="text-xs bg-gray-100 text-gray-800 rounded px-2 py-1 break-all" data-testid="metric-chip">
                    <span className="text-gray-500">{metric.entity} · {metric.name}:</span> <strong>{metric.value}</strong>
                </li>
            ))}
        </ul>
    );
}

function SourceLabel({ source }) {
    const { t } = useTranslation('agents');
    return <span className="text-xs text-gray-600">{t(`result.source.${source}`, { defaultValue: source })}</span>;
}

/** Claims with their provenance: real (a tool of this run or the requester's data), Google estimate, or the model's hypothesis. */
function BasisList({ items }) {
    return (
        <ul className="space-y-2">
            {items.map((item, index) => (
                <li key={index} className="border border-gray-200 rounded-lg p-3" data-testid="basis-item">
                    <div className="flex flex-wrap items-center gap-2">
                        <DataKindBadge kind={item.data_kind} />
                        {item.source && <SourceLabel source={item.source} />}
                    </div>
                    <p className="text-sm text-gray-900 mt-1 break-words">{item.statement}</p>
                    <MetricChips metrics={item.metrics} />
                </li>
            ))}
        </ul>
    );
}

function KeywordList({ items }) {
    const { t } = useTranslation('agents');
    return (
        <ul className="space-y-2">
            {items.map((item, index) => (
                <li key={index} className="border border-gray-200 rounded-lg p-3" data-testid="keyword-item">
                    <div className="flex flex-wrap items-center gap-2">
                        <strong className="text-gray-900 break-all">{item.keyword}</strong>
                        <DataKindBadge kind={item.data_kind} />
                        {item.source && <SourceLabel source={item.source} />}
                    </div>
                    <p className="text-xs text-gray-600 mt-1">
                        {enumText(t, item.intent)} · {enumText(t, item.match_type_suggestion)}
                    </p>
                    {item.rationale && <p className="text-sm text-gray-700 mt-1 break-words">{item.rationale}</p>}
                    <MetricChips metrics={item.metrics} />
                </li>
            ))}
        </ul>
    );
}

function Value({ name, value, depth }) {
    const { t } = useTranslation('agents');
    if (value === null || value === undefined || value === '') return null;
    if (isPrimitive(value)) {
        const text = typeof value === 'string' ? enumText(t, value) : String(value);
        return <p className="text-sm text-gray-800 whitespace-pre-line break-words">{text}</p>;
    }
    if (depth >= MAX_DEPTH) return <pre className="text-xs bg-gray-50 rounded p-2 overflow-x-auto">{JSON.stringify(value, null, 1)}</pre>;
    if (Array.isArray(value)) {
        if (value.length === 0) return null;
        if (name === 'data_basis') return <BasisList items={value} />;
        if (name === 'keyword_candidates') return <KeywordList items={value} />;
        if (value.every(isPrimitive)) {
            return <ul className="list-disc pl-5 space-y-1 text-sm text-gray-800">{value.map((item, index) => <li key={index} className="break-words">{String(item)}</li>)}</ul>;
        }
        return (
            <ul className="space-y-2">
                {value.map((item, index) => (
                    <li key={index} className="border border-gray-200 rounded-lg p-3">
                        <Value name={null} value={item} depth={depth + 1} />
                    </li>
                ))}
            </ul>
        );
    }
    return (
        <dl className="space-y-3">
            {Object.entries(value).filter(([, v]) => v !== null && v !== undefined && v !== '' && !(Array.isArray(v) && v.length === 0)).map(([key, v]) => (
                <div key={key}>
                    <dt className="text-sm font-medium text-gray-900"><FieldLabel name={key} /></dt>
                    <dd className="mt-1"><Value name={key} value={v} depth={depth + 1} /></dd>
                </div>
            ))}
        </dl>
    );
}

/** How much of a result is real / estimate / hypothesis, and what an UNLABELLED item means. */
function Provenance({ output }) {
    const { t } = useTranslation('agents');
    const counts = countDataKinds(output);
    const total = counts.real + counts.estimate + counts.hypothesis;
    return (
        <div className="bg-gray-50 border border-gray-200 rounded-lg p-3 text-sm" data-testid="provenance-summary">
            <p className="font-medium text-gray-900 flex items-center gap-2"><Info size={16} className="text-amber-600" aria-hidden="true" />{t('result.provenance.title')}</p>
            {total > 0 ? (
                <p className="mt-2 flex flex-wrap gap-2">
                    {['real', 'estimate', 'hypothesis'].map((kind) => (
                        <span key={kind} className="inline-flex items-center gap-1" data-testid={`count-${kind}`}>
                            <DataKindBadge kind={kind} /> <span className="text-gray-800">{counts[kind]}</span>
                        </span>
                    ))}
                </p>
            ) : (
                <p className="mt-2 text-gray-700">{t('result.provenance.none')}</p>
            )}
            <ul className="mt-2 text-xs text-gray-600 space-y-1">
                <li><strong>{t('dataKind.real')}:</strong> {t('result.provenance.real')}</li>
                <li><strong>{t('dataKind.estimate')}:</strong> {t('result.provenance.estimate')}</li>
                <li><strong>{t('dataKind.hypothesis')}:</strong> {t('result.provenance.hypothesis')}</li>
            </ul>
        </div>
    );
}

function Output({ output }) {
    const { t } = useTranslation('agents');
    return (
        <div className="space-y-4">
            <Provenance output={output} />
            <Value name={null} value={output} depth={0} />
            <details className="text-xs text-gray-600">
                <summary className="cursor-pointer inline-flex items-center gap-1 min-h-[44px]"><FileJson size={14} aria-hidden="true" />{t('result.rawJson')}</summary>
                <pre className="mt-2 bg-gray-50 rounded p-3 overflow-x-auto max-h-96">{JSON.stringify(output, null, 2)}</pre>
            </details>
        </div>
    );
}

/**
 * Structured result of a finished task, or the PARTIAL result of one that stopped early. Everything shown comes from the
 * server's stored result; the proposals are labelled as model output for human review and nothing here can publish or spend.
 */
export default function ResultView({ resultResponse, agents }) {
    const { t } = useTranslation('agents');
    const tx = useBackendText();
    if (!resultResponse) return null;
    const data = resultResponse.result;
    const nameOf = (key) => {
        const agent = agents.find((a) => a.key === key);
        return agent ? tx(`catalog.agent.${key}.name`, agent.name) : key;
    };
    const disclaimer = resultResponse.disclaimer === SERVER_DISCLAIMER ? t('result.disclaimer') : resultResponse.disclaimer;

    if (!data || typeof data !== 'object') {
        return <p className="text-sm text-gray-600" data-testid="result-empty">{t(resultResponse.status === 'completed' ? 'result.emptyCompleted' : 'result.noneStored')}</p>;
    }

    const isPartial = data.partial && typeof data.partial === 'object';
    return (
        <div className="space-y-5" data-testid="result-view">
            <p className="text-xs text-gray-600 bg-amber-50 border border-amber-200 rounded-lg px-3 py-2 flex items-start gap-2">
                <CircleAlert size={14} className="mt-0.5 flex-shrink-0 text-amber-700" aria-hidden="true" />{disclaimer}
            </p>

            {isPartial && (
                <div role="status" className="text-sm text-amber-900 bg-amber-50 border border-amber-200 rounded-lg px-3 py-2" data-testid="partial-notice">{t('result.partialNotice')}</div>
            )}

            {data.kind === 'single' && (
                <section aria-label={nameOf(data.agent)}>
                    <h3 className="font-bold text-gray-900 mb-2">{nameOf(data.agent)}</h3>
                    <Output output={data.output} />
                </section>
            )}

            {data.kind === 'supervised' && (
                <>
                    {data.report && (
                        <section aria-labelledby="result-report">
                            <h3 id="result-report" className="font-bold text-gray-900 mb-2">{t('result.report')}</h3>
                            <Output output={data.report} />
                        </section>
                    )}
                    <section aria-labelledby="result-steps">
                        <h3 id="result-steps" className="font-bold text-gray-900 mb-2">{t('result.specialists')}</h3>
                        <div className="space-y-2">
                            {(data.steps || []).map((step) => (
                                <details key={step.id} className={`${CARD} p-3`} data-testid={`result-step-${step.id}`}>
                                    <summary className="cursor-pointer font-medium text-gray-900 min-h-[44px] flex items-center gap-2">{nameOf(step.agent)} <Badge tone="neutral">{step.id}</Badge></summary>
                                    <div className="mt-3">{step.output ? <Output output={step.output} /> : <p className="text-sm text-gray-600">{t('result.stepNoOutput')}</p>}</div>
                                </details>
                            ))}
                        </div>
                    </section>
                </>
            )}

            {isPartial && (
                <section aria-labelledby="result-partial">
                    <h3 id="result-partial" className="font-bold text-gray-900 mb-2">{t('result.partial')}</h3>
                    <div className="space-y-2">
                        {Object.entries(data.partial).map(([stepId, output]) => (
                            <details key={stepId} className={`${CARD} p-3`} data-testid={`partial-step-${stepId}`}>
                                <summary className="cursor-pointer font-medium text-gray-900 min-h-[44px] flex items-center">{t('result.partialStep', { id: stepId })}</summary>
                                <div className="mt-3"><Output output={output} /></div>
                            </details>
                        ))}
                    </div>
                </section>
            )}
        </div>
    );
}

