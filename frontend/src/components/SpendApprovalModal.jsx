import React, { useCallback, useEffect, useRef, useState } from 'react';
import { createPortal } from 'react-dom';
import { useTranslation } from 'react-i18next';
import { AlertTriangle, CheckCircle2, Clock, Play, ShieldAlert, X, XCircle } from 'lucide-react';
import useFocusTrap from '../lib/useFocusTrap';
import { useAuth } from '../context/AuthContext';
import { useOrganization } from '../context/OrganizationContext';
import { useToast } from '../context/ToastContext';
import { formatDateTime, formatNumber } from '../i18n/format';
import {
    ENABLE_ACTION,
    PLATFORM,
    effectiveStatus,
    enablePayload,
    findEnableApproval,
    microsToUsd,
    newIdempotencyKey,
    usdToMicros,
} from '../lib/spendApprovals';

const API_URL = import.meta.env.VITE_API_URL || 'http://localhost:8000/api/v1';

const money = (micros) => `$${formatNumber(microsToUsd(micros), { minimumFractionDigits: 2, maximumFractionDigits: 2 })}`;

// Terminal states: a NEW request (new idempotency key) is needed to try again.
const TERMINAL = ['rejected', 'expired', 'consumed'];

/**
 * Human spend approval for ENABLING a Google Ads campaign (the one action that
 * starts real spend). Flow, all enforced by the backend:
 *   request (ads:draft)  ->  approve with a budget cap (HUMAN, ads:spend)  ->  enable with approval_id (ads:spend)
 * The buttons shown here follow the permissions the server reported for the
 * user's role; the server still authorizes every call. Nothing is sent to
 * Google unless an approved, unexpired, unused approval exists.
 */
export default function SpendApprovalModal({ isOpen, campaign, accountId, accountLabel, onClose, onExecuted }) {
    const { t } = useTranslation('approvals');
    const { authFetch } = useAuth();
    const { can, activeOrganization, role } = useOrganization();
    const { showSuccess, showError } = useToast();

    const [approval, setApproval] = useState(null);
    const [loading, setLoading] = useState(true);
    const [busy, setBusy] = useState(false);
    const [errorKey, setErrorKey] = useState(null);
    const [budget, setBudget] = useState('');
    const keyRef = useRef(null);
    const closeRef = useRef(null);
    const dialogRef = useRef(null);
    useFocusTrap(dialogRef, isOpen);

    const status = effectiveStatus(approval);
    const campaignId = campaign?.id;

    const errorFor = (response) => {
        const map = { 400: 'badRequest', 401: 'unauthorized', 403: 'forbidden', 404: 'notFound', 409: 'conflict', 422: 'invalidBudget', 423: 'killSwitch' };
        return map[response.status] || (response.status >= 500 ? 'platform' : 'generic');
    };

    const load = useCallback(async ({ keepError = false } = {}) => {
        setLoading(true);
        if (!keepError) setErrorKey(null);
        try {
            const response = await authFetch(`${API_URL}/optimization/approvals`);
            if (!response.ok) {
                setErrorKey(errorFor(response));
                return;
            }
            const list = await response.json();
            setApproval(findEnableApproval(Array.isArray(list) ? list : [], accountId, campaignId));
        } catch {
            setErrorKey('network');
        } finally {
            setLoading(false);
        }
    }, [authFetch, accountId, campaignId]);

    useEffect(() => {
        if (!isOpen || campaignId == null) return;
        keyRef.current = newIdempotencyKey(campaignId);
        setBudget('');
        setApproval(null);
        load();
    }, [isOpen, campaignId, load]);

    useEffect(() => {
        if (!isOpen) return undefined;
        closeRef.current?.focus();
        const onKey = (event) => {
            if (event.key === 'Escape' && !busy) onClose();
        };
        document.addEventListener('keydown', onKey);
        return () => document.removeEventListener('keydown', onKey);
    }, [isOpen, busy, onClose]);

    if (!isOpen || !campaign) return null;

    const call = async (path, body) => {
        setBusy(true);
        setErrorKey(null);
        try {
            const response = await authFetch(`${API_URL}${path}`, {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify(body),
            });
            if (!response.ok) {
                setErrorKey(errorFor(response));
                showError(t(`errors.${errorFor(response)}`));
                // 404/409: what the dialog shows is stale (decided, used or gone elsewhere): show the truth
                if (response.status === 404 || response.status === 409) await load({ keepError: true });
                return null;
            }
            return await response.json();
        } catch {
            setErrorKey('network');
            showError(t('errors.network'));
            return null;
        } finally {
            setBusy(false);
        }
    };

    const budgetMicros = usdToMicros(budget);
    const budgetValid = Number.isFinite(budgetMicros) && budgetMicros > 0;

    const handleRequest = async () => {
        if (!budgetValid) {
            setErrorKey('invalidBudget');
            return;
        }
        if (approval && TERMINAL.includes(status)) keyRef.current = newIdempotencyKey(campaignId);
        const created = await call('/optimization/approvals', {
            provider: PLATFORM,
            account_id: accountId,
            action: ENABLE_ACTION,
            payload: enablePayload(campaignId),
            budget_micros: budgetMicros,
            idempotency_key: keyRef.current,
        });
        if (created) {
            showSuccess(t('toast.requested'));
            setBudget('');
            await load();
        }
    };

    const handleDecision = async (approve) => {
        const decided = await call(`/optimization/approvals/${approval.id}/${approve ? 'approve' : 'reject'}`,
            approve ? { max_budget_micros: budgetMicros } : { reason: null });
        if (decided) {
            showSuccess(t(approve ? 'toast.approved' : 'toast.rejected'));
            setBudget('');
            await load();
        }
    };

    const handleExecute = async () => {
        const done = await call(`/google-ads/campaigns/${campaignId}/enable`, { confirm: true, approval_id: approval.id });
        if (done) {
            showSuccess(t('toast.enabled'));
            onExecuted?.();
            onClose();
        }
    };

    const canRequest = can('ads:draft');
    const canDecide = can('ads:spend');
    const needsRequest = status === 'none' || TERMINAL.includes(status);

    const tone = {
        pending: 'bg-amber-100 text-amber-800',
        approved: 'bg-green-100 text-green-800',
        rejected: 'bg-red-100 text-red-800',
        expired: 'bg-gray-100 text-gray-700',
        consumed: 'bg-blue-100 text-blue-800',
    }[status] || 'bg-gray-100 text-gray-700';
    const statusBadge = <span data-testid="approval-status" className={`inline-block rounded-full px-2.5 py-0.5 text-xs font-medium ${tone}`}>{t(`status.${status}`)}</span>;

    // Portal: a modal rendered inside a `space-y-*` page container would inherit the sibling margin
    // and leave a strip at the top of the backdrop.
    return createPortal(
        <div className="fixed inset-0 bg-black/50 backdrop-blur-sm flex items-center justify-center z-50 p-4" onClick={() => !busy && onClose()}>
            <div
                ref={dialogRef}
                role="dialog"
                aria-modal="true"
                aria-labelledby="spend-approval-title"
                className="bg-white rounded-xl max-w-lg w-full max-h-[90vh] overflow-y-auto p-6 shadow-xl"
                onClick={(event) => event.stopPropagation()}
            >
                <div className="flex items-start gap-4 mb-5">
                    <div className="w-12 h-12 rounded-full bg-amber-100 flex items-center justify-center flex-shrink-0">
                        <ShieldAlert className="text-amber-700" size={24} />
                    </div>
                    <div className="flex-1 min-w-0">
                        <h3 id="spend-approval-title" className="text-lg font-bold text-gray-900">{t('title')}</h3>
                        <p className="text-sm text-gray-600 mt-1">{t('intro')}</p>
                    </div>
                    <button ref={closeRef} type="button" onClick={onClose} disabled={busy} aria-label={t('close')} className="text-gray-400 hover:text-gray-600 p-2 -m-2 rounded-lg focus-visible:outline focus-visible:outline-2 focus-visible:outline-amber-600">
                        <X size={20} />
                    </button>
                </div>

                <dl className="grid grid-cols-[auto,1fr] gap-x-4 gap-y-1 text-sm bg-amber-50 rounded-lg p-3 mb-4">
                    <dt className="text-gray-500">{t('fields.campaign')}</dt>
                    <dd className="font-medium text-gray-900 break-words">{campaign.name}</dd>
                    <dt className="text-gray-500">{t('fields.account')}</dt>
                    <dd className="text-gray-900 break-words">{accountLabel || accountId}</dd>
                    <dt className="text-gray-500">{t('fields.organization')}</dt>
                    <dd className="text-gray-900 break-words">{activeOrganization?.name} · {t(`roles.${role}`, { defaultValue: role })}</dd>
                </dl>

                {loading ? (
                    <p role="status" className="text-sm text-gray-500">{t('loading')}</p>
                ) : (
                    <div className="space-y-4">
                        {approval && (
                            <div className="rounded-lg border border-gray-200 p-3 text-sm space-y-1" data-testid="approval-details">
                                <div className="flex items-center justify-between gap-2">
                                    <span className="font-medium text-gray-900">{t('current')}</span>
                                    {statusBadge}
                                </div>
                                {approval.requested_budget_micros != null && <p className="text-gray-600">{t('fields.requestedBudget', { amount: money(approval.requested_budget_micros) })}</p>}
                                {approval.max_budget_micros != null && <p className="text-gray-600">{t('fields.budgetCap', { amount: money(approval.max_budget_micros) })}</p>}
                                <p className="text-gray-500">
                                    {approval.requested_by_api_key_id ? t('fields.requestedByBot') : t('fields.requestedByUser')}
                                    {approval.created_at ? ` · ${formatDateTime(approval.created_at)}` : ''}
                                </p>
                                {approval.expires_at && ['pending', 'approved', 'expired'].includes(status) && (
                                    <p className="text-gray-500 flex items-center gap-1"><Clock size={13} aria-hidden="true" />{t('fields.expires', { date: formatDateTime(approval.expires_at) })}</p>
                                )}
                            </div>
                        )}

                        {status === 'consumed' && <p className="text-sm text-gray-700 flex items-start gap-2"><AlertTriangle size={16} className="mt-0.5 text-amber-600 flex-shrink-0" aria-hidden="true" />{t('hint.consumed')}</p>}
                        {status === 'rejected' && <p className="text-sm text-gray-700 flex items-start gap-2"><XCircle size={16} className="mt-0.5 text-red-600 flex-shrink-0" aria-hidden="true" />{t('hint.rejected')}</p>}
                        {status === 'expired' && <p className="text-sm text-gray-700 flex items-start gap-2"><Clock size={16} className="mt-0.5 text-gray-500 flex-shrink-0" aria-hidden="true" />{t('hint.expired')}</p>}

                        {needsRequest && (
                            canRequest ? (
                                <div>
                                    <label htmlFor="spend-budget" className="block text-sm font-medium text-gray-700 mb-1">{t('fields.requestBudgetLabel')}</label>
                                    <input
                                        id="spend-budget"
                                        type="number"
                                        inputMode="decimal"
                                        min="1"
                                        step="0.01"
                                        value={budget}
                                        onChange={(event) => setBudget(event.target.value)}
                                        className="w-full border border-gray-300 rounded-lg px-3 py-2 text-sm focus:ring-2 focus:ring-amber-500 focus:border-transparent"
                                        aria-describedby="spend-budget-help"
                                    />
                                    <p id="spend-budget-help" className="text-xs text-gray-500 mt-1">{t('fields.requestBudgetHelp')}</p>
                                </div>
                            ) : (
                                <p role="note" className="text-sm text-gray-700 flex items-start gap-2"><ShieldAlert size={16} className="mt-0.5 text-amber-600 flex-shrink-0" aria-hidden="true" />{t('hint.cannotRequest', { role: t(`roles.${role}`, { defaultValue: role }) })}</p>
                            )
                        )}

                        {status === 'pending' && (
                            canDecide ? (
                                <div>
                                    <label htmlFor="spend-cap" className="block text-sm font-medium text-gray-700 mb-1">{t('fields.approveCapLabel')}</label>
                                    <input
                                        id="spend-cap"
                                        type="number"
                                        inputMode="decimal"
                                        min="1"
                                        step="0.01"
                                        value={budget}
                                        onChange={(event) => setBudget(event.target.value)}
                                        className="w-full border border-gray-300 rounded-lg px-3 py-2 text-sm focus:ring-2 focus:ring-amber-500 focus:border-transparent"
                                        aria-describedby="spend-cap-help"
                                    />
                                    <p id="spend-cap-help" className="text-xs text-gray-500 mt-1">{t('fields.approveCapHelp')}</p>
                                </div>
                            ) : (
                                <p role="note" className="text-sm text-gray-700 flex items-start gap-2"><Clock size={16} className="mt-0.5 text-amber-600 flex-shrink-0" aria-hidden="true" />{t('hint.waitingOwner')}</p>
                            )
                        )}

                        {status === 'approved' && (
                            canDecide
                                ? <p className="text-sm text-gray-700 flex items-start gap-2"><CheckCircle2 size={16} className="mt-0.5 text-green-600 flex-shrink-0" aria-hidden="true" />{t('hint.readyToEnable')}</p>
                                : <p role="note" className="text-sm text-gray-700 flex items-start gap-2"><Clock size={16} className="mt-0.5 text-amber-600 flex-shrink-0" aria-hidden="true" />{t('hint.approvedWaitingOwner')}</p>
                        )}

                        {errorKey && <p role="alert" data-testid="approval-error" className="text-sm text-red-700 bg-red-50 border border-red-200 rounded-lg px-3 py-2">{t(`errors.${errorKey}`)}</p>}
                    </div>
                )}

                <div className="flex flex-wrap justify-end gap-3 mt-6">
                    <button type="button" onClick={onClose} disabled={busy} className="px-4 py-2 text-gray-700 hover:bg-gray-100 rounded-lg transition-colors font-medium">
                        {t('close')}
                    </button>
                    {!loading && needsRequest && canRequest && (
                        <button type="button" onClick={handleRequest} disabled={busy || !budgetValid} className="px-4 py-2 bg-amber-600 text-white rounded-lg font-medium hover:bg-amber-700 transition-colors shadow-sm disabled:opacity-50 disabled:cursor-not-allowed">
                            {busy ? t('pleaseWait') : t('actions.request')}
                        </button>
                    )}
                    {!loading && status === 'pending' && canDecide && (
                        <>
                            <button type="button" onClick={() => handleDecision(false)} disabled={busy} className="px-4 py-2 text-white bg-red-600 hover:bg-red-700 rounded-lg font-medium transition-colors shadow-sm disabled:opacity-50">
                                {t('actions.reject')}
                            </button>
                            <button type="button" onClick={() => handleDecision(true)} disabled={busy || !budgetValid} className="px-4 py-2 bg-amber-600 text-white rounded-lg font-medium hover:bg-amber-700 transition-colors shadow-sm disabled:opacity-50 disabled:cursor-not-allowed">
                                {busy ? t('pleaseWait') : t('actions.approve')}
                            </button>
                        </>
                    )}
                    {!loading && status === 'approved' && canDecide && (
                        <button type="button" onClick={handleExecute} disabled={busy} className="inline-flex items-center gap-2 px-4 py-2 bg-amber-600 text-white rounded-lg font-medium hover:bg-amber-700 transition-colors shadow-sm disabled:opacity-50">
                            <Play size={16} aria-hidden="true" />
                            {busy ? t('pleaseWait') : t('actions.enable')}
                        </button>
                    )}
                </div>
            </div>
        </div>,
        document.body,
    );
}
