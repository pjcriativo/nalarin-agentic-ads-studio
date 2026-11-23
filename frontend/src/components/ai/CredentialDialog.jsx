import React, { useState } from 'react';
import { useTranslation } from 'react-i18next';
import { KeyRound, Loader2 } from 'lucide-react';
import { Dialog, Field, INPUT_CLASS, PRIMARY_BUTTON, SECONDARY_BUTTON, errorMessage, providerName } from './ui';

const MIN_KEY = 8;
const MAX_KEY = 512;

/**
 * Adds a key (no `credential`) or replaces the current one. The typed key lives ONLY in this
 * component's state: it is sent once, never pre-filled, never logged, and dropped on close.
 */
export default function CredentialDialog({ api, provider, credential, onClose, onSaved }) {
    const { t } = useTranslation('ai');
    const replacing = Boolean(credential);
    const name = providerName(provider);
    const [apiKey, setApiKey] = useState('');
    const [label, setLabel] = useState('');
    const [verify, setVerify] = useState(true);
    const [saving, setSaving] = useState(false);
    const [error, setError] = useState('');
    const [fieldError, setFieldError] = useState('');

    const close = () => {
        setApiKey(''); // never keep a secret around longer than needed
        onClose();
    };

    const submit = async (event) => {
        event.preventDefault();
        const key = apiKey.trim();
        if (key.length < MIN_KEY) return setFieldError(t('provider.form.tooShort'));
        if (key.length > MAX_KEY) return setFieldError(t('provider.form.tooLong'));
        setFieldError('');
        setError('');
        setSaving(true);
        try {
            if (replacing) await api.replaceCredential(credential.id, { api_key: key, verify });
            else await api.createCredential({ provider, api_key: key, label: label.trim() || null, verify });
            setApiKey('');
            await onSaved(replacing);
        } catch (failure) {
            setError(errorMessage(t, failure));
            setSaving(false);
        }
    };

    return (
        <Dialog
            title={t(replacing ? 'provider.form.replaceTitle' : 'provider.form.addTitle', { provider: name })}
            description={t('provider.form.help', { provider: name })}
            icon={KeyRound}
            onClose={close}
            closeLabel={t('provider.form.close')}
        >
            <form onSubmit={submit} className="space-y-4" noValidate>
                <Field id="ai-key" label={t('provider.form.key')} hint={t('provider.form.keyHint')} error={fieldError}>
                    <input
                        id="ai-key"
                        type="password"
                        autoComplete="off"
                        autoCorrect="off"
                        autoCapitalize="off"
                        spellCheck={false}
                        name="ai-api-key"
                        value={apiKey}
                        onChange={(event) => setApiKey(event.target.value)}
                        aria-invalid={fieldError ? 'true' : undefined}
                        aria-describedby={fieldError ? 'ai-key-error' : 'ai-key-hint'}
                        className={`${INPUT_CLASS} font-mono`}
                        maxLength={MAX_KEY + 64}
                    />
                </Field>
                {!replacing && (
                    <Field id="ai-key-label" label={t('provider.form.label')} hint={t('provider.form.labelHint')}>
                        <input id="ai-key-label" type="text" maxLength={80} value={label} onChange={(event) => setLabel(event.target.value)} className={INPUT_CLASS} />
                    </Field>
                )}
                <div>
                    <label className="flex items-start gap-3 min-h-[44px] cursor-pointer">
                        <input type="checkbox" checked={verify} onChange={(event) => setVerify(event.target.checked)} className="mt-1 h-4 w-4 accent-amber-600" aria-describedby="ai-verify-hint" />
                        <span className="text-sm font-medium text-gray-700">{t('provider.form.verify', { provider: name })}</span>
                    </label>
                    <p id="ai-verify-hint" className="text-xs text-gray-500 ml-7">{t('provider.form.verifyHint')}</p>
                </div>
                {error && <p role="alert" className="text-sm text-red-700 bg-red-50 border border-red-200 rounded-lg px-3 py-2">{error}</p>}
                <div className="flex flex-wrap justify-end gap-3 pt-2">
                    <button type="button" className={SECONDARY_BUTTON} onClick={close}>{t('provider.form.cancel')}</button>
                    <button type="submit" className={PRIMARY_BUTTON} disabled={saving}>
                        {saving && <Loader2 size={16} className="animate-spin" aria-hidden="true" />}
                        {saving ? t('state.saving') : t(replacing ? 'provider.form.replaceSave' : 'provider.form.save')}
                    </button>
                </div>
            </form>
        </Dialog>
    );
}
