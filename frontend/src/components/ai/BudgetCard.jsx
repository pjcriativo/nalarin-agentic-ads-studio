import React, { useState } from 'react';
import { useTranslation } from 'react-i18next';
import { CircleAlert, CircleCheck, CircleSlash, Loader2, TriangleAlert, Wallet } from 'lucide-react';
import { budgetState } from '../../lib/aiBudget';
import { useToast } from '../../context/ToastContext';
import ConfirmationModal from '../ConfirmationModal';
import { Badge, CARD, DANGER_BUTTON, Dialog, Field, INPUT_CLASS, PRIMARY_BUTTON, SECONDARY_BUTTON, errorMessage, formatUsd } from './ui';

const BAR = { ok: 'bg-green-500', warning: 'bg-amber-500', reached: 'bg-red-600', off: 'bg-gray-300' };

function LimitDialog({ api, period, budget, onClose, onSaved }) {
    const { t } = useTranslation('ai');
    const [value, setValue] = useState(budget ? String(budget.limit_usd) : '');
    const [error, setError] = useState('');
    const [failure, setFailure] = useState('');
    const [saving, setSaving] = useState(false);
    const periodName = t(`budget.period.${period}`);

    const submit = async (event) => {
        event.preventDefault();
        const text = value.trim().replace(',', '.');
        const number = Number(text);
        if (text === '' || !Number.isFinite(number) || number < 0) return setError(t('budget.form.invalid'));
        setError('');
        setSaving(true);
        try {
            await api.saveBudget(period, { limit_usd: text, enabled: budget ? budget.enabled : true });
            await onSaved();
        } catch (failed) {
            setFailure(errorMessage(t, failed));
            setSaving(false);
        }
    };

    return (
        <Dialog title={t('budget.form.title', { period: periodName })} icon={Wallet} onClose={onClose} closeLabel={t('provider.form.close')}>
            <form onSubmit={submit} className="space-y-4" noValidate>
                <Field id={`ai-limit-${period}`} label={t('budget.form.field')} hint={t('budget.form.hint')} error={error}>
                    <input
                        id={`ai-limit-${period}`}
                        type="text"
                        inputMode="decimal"
                        value={value}
                        onChange={(event) => setValue(event.target.value)}
                        aria-invalid={error ? 'true' : undefined}
                        aria-describedby={error ? `ai-limit-${period}-error` : `ai-limit-${period}-hint`}
                        className={INPUT_CLASS}
                    />
                </Field>
                {failure && <p role="alert" className="text-sm text-red-700 bg-red-50 border border-red-200 rounded-lg px-3 py-2">{failure}</p>}
                <div className="flex flex-wrap justify-end gap-3">
                    <button type="button" className={SECONDARY_BUTTON} onClick={onClose}>{t('budget.form.cancel')}</button>
                    <button type="submit" className={PRIMARY_BUTTON} disabled={saving}>
                        {saving && <Loader2 size={16} className="animate-spin" aria-hidden="true" />}
                        {saving ? t('state.saving') : t('budget.form.save')}
                    </button>
                </div>
            </form>
        </Dialog>
    );
}

/** One spending window (daily or monthly): usage bar, state, and (for admins) edit / enforce / remove. */
export default function BudgetCard({ api, period, budget, canManage, onChanged }) {
    const { t } = useTranslation('ai');
    const { showSuccess, showError } = useToast();
    const [editing, setEditing] = useState(false);
    const [confirmRemove, setConfirmRemove] = useState(false);
    const [busy, setBusy] = useState(false);
    const periodName = t(`budget.period.${period}`);
    const info = budget ? budgetState(budget) : null;

    const toggle = async () => {
        setBusy(true);
        try {
            await api.saveBudget(period, { limit_usd: String(budget.limit_usd), enabled: !budget.enabled });
            await onChanged();
        } catch (error) {
            showError(errorMessage(t, error));
        } finally {
            setBusy(false);
        }
    };
    const remove = async () => {
        try {
            await api.removeBudget(period);
            showSuccess(t('budget.toast.removed', { period: periodName }));
            await onChanged();
        } catch (error) {
            showError(errorMessage(t, error));
        }
    };

    const status = !info ? null : {
        off: { tone: 'neutral', icon: CircleSlash, text: t('budget.notEnforced') },
        ok: { tone: 'good', icon: CircleCheck, text: t('budget.ok') },
        warning: { tone: 'warn', icon: TriangleAlert, text: t('budget.warning', { percent: info.percent }) },
        reached: { tone: 'bad', icon: CircleAlert, text: t('budget.reached') },
    }[info.state];

    return (
        <li className={`${CARD} p-5`} data-testid={`budget-${period}`}>
            <div className="flex flex-wrap items-center justify-between gap-2">
                <h3 className="font-bold text-gray-900">{periodName}</h3>
                {status && <Badge tone={status.tone} icon={status.icon} data-testid={`budget-${period}-state`}>{status.text}</Badge>}
            </div>

            {!budget ? (
                <p className="text-sm text-gray-600 mt-3">{t('budget.none')}</p>
            ) : (
                <>
                    <div
                        role="progressbar"
                        aria-label={periodName}
                        aria-valuemin={0}
                        aria-valuemax={100}
                        aria-valuenow={info.percent}
                        aria-valuetext={`${info.percent}%`}
                        className="mt-3 h-2.5 rounded-full bg-gray-100 overflow-hidden"
                    >
                        <div className={`h-full rounded-full ${BAR[info.state]}`} style={{ width: `${info.percent}%` }} />
                    </div>
                    <dl className="mt-3 grid grid-cols-2 gap-3 text-sm">
                        {[['spent', budget.spent_usd], ['inFlight', budget.reserved_usd], ['remaining', budget.remaining_usd], ['limit', budget.limit_usd]].map(([key, value]) => (
                            <div key={key}>
                                <dt className="text-xs text-gray-500">{t(`budget.${key}`)}</dt>
                                <dd className="font-medium text-gray-900">{formatUsd(value)}</dd>
                            </div>
                        ))}
                    </dl>
                </>
            )}

            {canManage && (
                <div className="mt-4 flex flex-wrap items-center gap-2">
                    <button type="button" className={budget ? SECONDARY_BUTTON : PRIMARY_BUTTON} onClick={() => setEditing(true)}>
                        {budget ? t('budget.change') : t('budget.set')}
                        <span className="sr-only"> {periodName}</span>
                    </button>
                    {budget && (
                        <>
                            <label className="flex items-center gap-2 min-h-[44px] px-2 cursor-pointer text-sm text-gray-700">
                                <input type="checkbox" checked={budget.enabled} disabled={busy} onChange={toggle} className="h-4 w-4 accent-amber-600" />
                                {t('budget.enforce')}
                                <span className="sr-only"> {periodName}</span>
                            </label>
                            <button type="button" className={DANGER_BUTTON} onClick={() => setConfirmRemove(true)}>
                                {t('budget.remove')}
                                <span className="sr-only"> {periodName}</span>
                            </button>
                        </>
                    )}
                </div>
            )}

            {editing && (
                <LimitDialog
                    api={api}
                    period={period}
                    budget={budget}
                    onClose={() => setEditing(false)}
                    onSaved={async () => {
                        showSuccess(t('budget.toast.saved', { period: periodName }));
                        setEditing(false);
                        await onChanged();
                    }}
                />
            )}
            <ConfirmationModal
                isOpen={confirmRemove}
                onClose={() => setConfirmRemove(false)}
                onConfirm={remove}
                title={t('budget.removeConfirm.title', { period: periodName.toLowerCase() })}
                message={t('budget.removeConfirm.message')}
                confirmText={t('budget.removeConfirm.confirm')}
                cancelText={t('budget.form.cancel')}
            />
        </li>
    );
}
