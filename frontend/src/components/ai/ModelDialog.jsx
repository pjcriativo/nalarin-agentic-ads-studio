import React, { useState } from 'react';
import { useTranslation } from 'react-i18next';
import { Boxes, Loader2 } from 'lucide-react';
import { PROVIDERS } from '../../lib/aiApi';
import { Dialog, Field, INPUT_CLASS, PRIMARY_BUTTON, SECONDARY_BUTTON, errorMessage, providerName } from './ui';

const CAPS = [['supports_tools', 'tools'], ['supports_vision', 'vision'], ['supports_json', 'json']];
const PRICE_FIELDS = [
    ['input_price_usd_per_mtok', 'inputPrice'],
    ['output_price_usd_per_mtok', 'outputPrice'],
    ['cached_input_price_usd_per_mtok', 'cachedPrice'],
];

const toText = (value) => (value === null || value === undefined ? '' : String(value));

/** "" -> null (unknown), otherwise the trimmed text; validated by the caller. */
const parsePrice = (text) => {
    const value = text.trim().replace(',', '.');
    if (value === '') return { value: null };
    const number = Number(value);
    return Number.isFinite(number) && number >= 0 ? { value } : { error: true };
};
const parseInteger = (text) => {
    const value = text.trim();
    if (value === '') return { value: null };
    const number = Number(value);
    return Number.isInteger(number) && number > 0 ? { value: number } : { error: true };
};

/** Add (`model` null) or edit a catalog entry. `prefill` carries a name confirmed by provider discovery. */
export default function ModelDialog({ api, model, prefill, defaultProvider, onClose, onSaved }) {
    const { t } = useTranslation('ai');
    const editing = Boolean(model);
    const [form, setForm] = useState(() => ({
        provider: model?.provider || prefill?.provider || defaultProvider || PROVIDERS[0],
        model_id: model?.model_id || prefill?.model_id || '',
        display_name: model?.display_name || prefill?.display_name || '',
        enabled: model ? model.enabled : true,
        supports_tools: model?.supports_tools || false,
        supports_vision: model?.supports_vision || false,
        supports_json: model?.supports_json || false,
        context_window: toText(model?.context_window),
        max_output_tokens: toText(model?.max_output_tokens),
        input_price_usd_per_mtok: toText(model?.input_price_usd_per_mtok),
        output_price_usd_per_mtok: toText(model?.output_price_usd_per_mtok),
        cached_input_price_usd_per_mtok: toText(model?.cached_input_price_usd_per_mtok),
    }));
    const [errors, setErrors] = useState({});
    const [failure, setFailure] = useState('');
    const [saving, setSaving] = useState(false);
    const set = (name, value) => setForm((current) => ({ ...current, [name]: value }));

    const submit = async (event) => {
        event.preventDefault();
        const found = {};
        const body = {
            display_name: form.display_name.trim() || null,
            enabled: form.enabled,
            supports_tools: form.supports_tools,
            supports_vision: form.supports_vision,
            supports_json: form.supports_json,
        };
        if (!editing && !form.model_id.trim()) found.model_id = t('model.form.modelIdRequired');
        for (const [field] of PRICE_FIELDS) {
            const parsed = parsePrice(form[field]);
            if (parsed.error) found[field] = t('model.form.invalidNumber');
            else body[field] = parsed.value;
        }
        for (const field of ['context_window', 'max_output_tokens']) {
            const parsed = parseInteger(form[field]);
            if (parsed.error) found[field] = t('model.form.invalidInteger');
            else body[field] = parsed.value;
        }
        setErrors(found);
        if (Object.keys(found).length) return;
        setFailure('');
        setSaving(true);
        try {
            const saved = editing
                ? await api.updateModel(model.id, body)
                : await api.addModel({ ...body, provider: form.provider, model_id: form.model_id.trim() });
            await onSaved(saved, !editing);
        } catch (error) {
            setFailure(errorMessage(t, error));
            setSaving(false);
        }
    };

    const title = editing ? t('model.form.editTitle', { model: model.display_name || model.model_id }) : t('model.form.addTitle');
    const describe = (id, hasError) => (hasError ? `${id}-error` : `${id}-hint`);

    return (
        <Dialog title={title} icon={Boxes} onClose={onClose} closeLabel={t('provider.form.close')} wide>
            <form onSubmit={submit} className="space-y-4" noValidate>
                <div className="grid gap-4 sm:grid-cols-2">
                    <Field id="ai-model-provider" label={t('model.form.provider')}>
                        <select id="ai-model-provider" value={form.provider} disabled={editing} onChange={(event) => set('provider', event.target.value)} className={INPUT_CLASS}>
                            {PROVIDERS.map((provider) => <option key={provider} value={provider}>{providerName(provider)}</option>)}
                        </select>
                    </Field>
                    <Field id="ai-model-id" label={t('model.form.modelId')} hint={t('model.form.modelIdHint')} error={errors.model_id}>
                        <input
                            id="ai-model-id"
                            type="text"
                            autoComplete="off"
                            spellCheck={false}
                            value={form.model_id}
                            disabled={editing}
                            onChange={(event) => set('model_id', event.target.value)}
                            aria-invalid={errors.model_id ? 'true' : undefined}
                            aria-describedby={describe('ai-model-id', errors.model_id)}
                            className={`${INPUT_CLASS} font-mono`}
                            maxLength={200}
                        />
                    </Field>
                </div>
                <Field id="ai-model-name" label={t('model.form.displayName')}>
                    <input id="ai-model-name" type="text" value={form.display_name} onChange={(event) => set('display_name', event.target.value)} className={INPUT_CLASS} maxLength={120} />
                </Field>

                <fieldset>
                    <legend className="text-sm font-medium text-gray-700">{t('model.form.capabilities')}</legend>
                    <p className="text-xs text-gray-500 mb-1">{t('model.form.capabilitiesHint')}</p>
                    <div className="flex flex-wrap gap-x-6">
                        {CAPS.map(([field, key]) => (
                            <label key={field} className="flex items-center gap-2 min-h-[44px] cursor-pointer text-sm text-gray-700">
                                <input type="checkbox" checked={form[field]} onChange={(event) => set(field, event.target.checked)} className="h-4 w-4 accent-amber-600" />
                                {t(`model.capability.${key}`)}
                            </label>
                        ))}
                    </div>
                </fieldset>

                <div className="grid gap-4 sm:grid-cols-2">
                    {[['context_window', 'contextWindow'], ['max_output_tokens', 'maxOutput']].map(([field, key]) => (
                        <Field key={field} id={`ai-model-${field}`} label={t(`model.form.${key}`)} error={errors[field]}>
                            <input
                                id={`ai-model-${field}`}
                                type="text"
                                inputMode="numeric"
                                value={form[field]}
                                onChange={(event) => set(field, event.target.value)}
                                aria-invalid={errors[field] ? 'true' : undefined}
                                aria-describedby={errors[field] ? `ai-model-${field}-error` : undefined}
                                className={INPUT_CLASS}
                            />
                        </Field>
                    ))}
                </div>

                <div className="grid gap-4 sm:grid-cols-3">
                    {PRICE_FIELDS.map(([field, key]) => (
                        <Field key={field} id={`ai-model-${field}`} label={t(`model.form.${key}`)} hint={key === 'cachedPrice' ? t('model.form.cachedHint') : undefined} error={errors[field]}>
                            <input
                                id={`ai-model-${field}`}
                                type="text"
                                inputMode="decimal"
                                value={form[field]}
                                onChange={(event) => set(field, event.target.value)}
                                aria-invalid={errors[field] ? 'true' : undefined}
                                aria-describedby={describe(`ai-model-${field}`, errors[field])}
                                className={INPUT_CLASS}
                            />
                        </Field>
                    ))}
                </div>
                <p className="text-xs text-gray-500 -mt-2">{t('model.form.priceHint')}</p>

                <label className="flex items-center gap-3 min-h-[44px] cursor-pointer text-sm font-medium text-gray-700">
                    <input type="checkbox" checked={form.enabled} onChange={(event) => set('enabled', event.target.checked)} className="h-4 w-4 accent-amber-600" />
                    {t('model.form.enabled')}
                </label>

                {failure && <p role="alert" className="text-sm text-red-700 bg-red-50 border border-red-200 rounded-lg px-3 py-2">{failure}</p>}
                <div className="flex flex-wrap justify-end gap-3 pt-2">
                    <button type="button" className={SECONDARY_BUTTON} onClick={onClose}>{t('model.form.cancel')}</button>
                    <button type="submit" className={PRIMARY_BUTTON} disabled={saving}>
                        {saving && <Loader2 size={16} className="animate-spin" aria-hidden="true" />}
                        {saving ? t('state.saving') : t('model.form.save')}
                    </button>
                </div>
            </form>
        </Dialog>
    );
}
