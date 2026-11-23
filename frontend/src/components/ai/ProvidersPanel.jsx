import React, { useState } from 'react';
import { useTranslation } from 'react-i18next';
import { CheckCircle2, CircleSlash, KeyRound, Loader2, PlugZap, RefreshCw, ShieldAlert, Trash2 } from 'lucide-react';
import { PROVIDERS } from '../../lib/aiApi';
import { formatDateTime } from '../../i18n/format';
import { useToast } from '../../context/ToastContext';
import ConfirmationModal from '../ConfirmationModal';
import CredentialDialog from './CredentialDialog';
import { Badge, CARD, DANGER_BUTTON, PRIMARY_BUTTON, SECONDARY_BUTTON, credentialFor, errorMessage, providerName } from './ui';

function statusOf(credential, hasActive) {
    if (credential?.status === 'active') {
        if (credential.last_verification_ok === true) return { tone: 'good', icon: CheckCircle2, key: 'verified' };
        if (credential.last_verification_ok === false) return { tone: 'bad', icon: ShieldAlert, key: 'failed' };
        return { tone: 'info', icon: KeyRound, key: 'untested' };
    }
    if (!credential && hasActive) return { tone: 'info', icon: KeyRound, key: 'active' }; // read-only roles only know that a key exists
    if (credential?.status === 'revoked') return { tone: 'neutral', icon: CircleSlash, key: 'revoked' };
    return { tone: 'neutral', icon: CircleSlash, key: 'notConfigured' };
}

export default function ProvidersPanel({ api, providers, credentials, organizationName, canManage, onChanged }) {
    const { t } = useTranslation('ai');
    const { showSuccess, showError } = useToast();
    const [dialog, setDialog] = useState(null); // { provider, credential|null }
    const [busy, setBusy] = useState(null); // `${provider}:test`
    const [toRevoke, setToRevoke] = useState(null);

    const hasActive = (provider) => providers.some((p) => p.provider === provider && p.has_active_credential);

    const test = async (provider, credential) => {
        setBusy(`${provider}:test`);
        try {
            const result = await api.testCredential(credential.id);
            if (result.ok) showSuccess(t('provider.toast.testOk', { provider: providerName(provider) }));
            else showError(t('provider.toast.testFailed', { provider: providerName(provider), reason: t(`errors.${result.error || 'unknown'}`, { defaultValue: t('errors.generic') }) }));
            await onChanged();
        } catch (error) {
            showError(errorMessage(t, error));
        } finally {
            setBusy(null);
        }
    };

    const revoke = async () => {
        const { provider, credential } = toRevoke;
        try {
            await api.revokeCredential(credential.id);
            showSuccess(t('provider.toast.revoked', { provider: providerName(provider) }));
            await onChanged();
        } catch (error) {
            showError(errorMessage(t, error));
        }
    };

    return (
        <section aria-labelledby="ai-providers-heading">
            <h2 id="ai-providers-heading" className="text-lg font-bold text-gray-900">{t('provider.title')}</h2>
            <p className="text-sm text-gray-600 mt-1 max-w-3xl">{t('provider.subtitle')}</p>
            {!canManage && <p className="text-sm text-amber-800 bg-amber-50 border border-amber-200 rounded-lg px-3 py-2 mt-3">{t('provider.onlyAdmins')}</p>}

            <ul className="grid gap-4 md:grid-cols-2 xl:grid-cols-3 mt-4">
                {PROVIDERS.map((provider) => {
                    const credential = credentialFor(credentials, provider);
                    const active = credential?.status === 'active';
                    const status = statusOf(credential, hasActive(provider));
                    const testing = busy === `${provider}:test`;
                    const name = providerName(provider);
                    return (
                        <li key={provider} className={`${CARD} p-5 flex flex-col`} data-testid={`provider-${provider}`}>
                            <div className="flex items-start justify-between gap-3">
                                <h3 className="text-base font-bold text-gray-900">{name}</h3>
                                <Badge tone={status.tone} icon={status.icon} data-testid={`provider-${provider}-status`}>
                                    {t(`provider.status.${status.key}`)}
                                </Badge>
                            </div>

                            <div className="mt-3 text-sm text-gray-600 space-y-1 flex-1">
                                {active ? (
                                    <>
                                        <p className="font-mono text-gray-900" data-testid={`provider-${provider}-last4`}>
                                            {t('provider.keyEnding', { last4: credential.last4 })}
                                        </p>
                                        {credential.label && <p className="break-words">{t('provider.label', { label: credential.label })}</p>}
                                        <p>
                                            {credential.last_verified_at
                                                ? t('provider.lastTested', { date: formatDateTime(credential.last_verified_at) })
                                                : t('provider.neverTested')}
                                        </p>
                                    </>
                                ) : (
                                    !hasActive(provider) && <p>{t('provider.noKey')}</p>
                                )}
                            </div>

                            {canManage && (
                                <div className="mt-4 flex flex-wrap gap-2">
                                    {active ? (
                                        <>
                                            <button type="button" className={SECONDARY_BUTTON} disabled={testing} onClick={() => test(provider, credential)}>
                                                {testing ? <Loader2 size={16} className="animate-spin" aria-hidden="true" /> : <PlugZap size={16} aria-hidden="true" />}
                                                {testing ? t('provider.testing') : t('provider.test')}
                                            </button>
                                            <button type="button" className={SECONDARY_BUTTON} onClick={() => setDialog({ provider, credential })}>
                                                <RefreshCw size={16} aria-hidden="true" />
                                                {t('provider.replace')}
                                            </button>
                                            <button type="button" className={DANGER_BUTTON} onClick={() => setToRevoke({ provider, credential })}>
                                                <Trash2 size={16} aria-hidden="true" />
                                                {t('provider.revoke')}
                                            </button>
                                        </>
                                    ) : (
                                        <button type="button" className={PRIMARY_BUTTON} onClick={() => setDialog({ provider, credential: null })}>
                                            <KeyRound size={16} aria-hidden="true" />
                                            {t('provider.add')}
                                        </button>
                                    )}
                                </div>
                            )}
                            {testing && <p role="status" className="sr-only">{t('provider.testing')}</p>}
                        </li>
                    );
                })}
            </ul>

            {dialog && (
                <CredentialDialog
                    api={api}
                    provider={dialog.provider}
                    credential={dialog.credential}
                    onClose={() => setDialog(null)}
                    onSaved={async (replaced) => {
                        showSuccess(t(replaced ? 'provider.toast.replaced' : 'provider.toast.added', { provider: providerName(dialog.provider) }));
                        setDialog(null);
                        await onChanged();
                    }}
                />
            )}
            <ConfirmationModal
                isOpen={Boolean(toRevoke)}
                onClose={() => setToRevoke(null)}
                onConfirm={revoke}
                title={t('provider.revokeConfirm.title')}
                message={toRevoke ? t('provider.revokeConfirm.message', { provider: providerName(toRevoke.provider), organization: organizationName }) : ''}
                confirmText={t('provider.revokeConfirm.confirm')}
                cancelText={t('provider.form.cancel')}
                icon={ShieldAlert}
            />
        </section>
    );
}

