import React, { useCallback, useEffect, useState } from 'react';
import { useTranslation } from 'react-i18next';
import { ShieldAlert } from 'lucide-react';
import { useAuth } from '../context/AuthContext';
import { formatDateTime, formatNumber } from '../i18n/format';
import { PLATFORM, digits, effectiveStatus, enablePayloadHash, microsToUsd } from '../lib/spendApprovals';

const API_URL = import.meta.env.VITE_API_URL || 'http://localhost:8000/api/v1';

const TONE = {
    pending: 'bg-amber-100 text-amber-800',
    approved: 'bg-green-100 text-green-800',
    rejected: 'bg-red-100 text-red-800',
    expired: 'bg-gray-100 text-gray-700',
    consumed: 'bg-blue-100 text-blue-800',
};

/**
 * Read-only list of the organization's Google Ads spend approvals. Each row is
 * matched to a loaded campaign (by the same payload hash the backend stores) so it
 * can be opened in the approval dialog; unmatched rows stay informational.
 * `refreshKey` changes whenever the parent knows an approval changed.
 */
export default function SpendApprovalsPanel({ accountId, campaigns, onOpen, refreshKey }) {
    const { t } = useTranslation('approvals');
    const { authFetch } = useAuth();
    const [rows, setRows] = useState([]);
    const [loading, setLoading] = useState(true);
    const [failed, setFailed] = useState(false);

    const load = useCallback(async () => {
        setLoading(true);
        setFailed(false);
        try {
            const response = await authFetch(`${API_URL}/optimization/approvals`);
            if (!response.ok) throw new Error(String(response.status));
            const list = await response.json();
            setRows((Array.isArray(list) ? list : []).filter((a) => a.provider === PLATFORM && digits(a.account_id) === digits(accountId)));
        } catch {
            setFailed(true);
        } finally {
            setLoading(false);
        }
    }, [authFetch, accountId]);

    useEffect(() => { load(); }, [load, refreshKey]);

    const byHash = new Map((campaigns || []).map((campaign) => [enablePayloadHash(campaign.id), campaign]));

    return (
        <section aria-labelledby="spend-approvals-heading" className="bg-white rounded-xl border border-gray-200 shadow-sm p-6">
            <h2 id="spend-approvals-heading" className="text-lg font-bold text-gray-900 flex items-center gap-2">
                <ShieldAlert size={20} className="text-amber-600" aria-hidden="true" />
                {t('panel.title')}
            </h2>
            <p className="text-sm text-gray-500 mt-1">{t('panel.subtitle')}</p>

            {loading && <p role="status" className="text-sm text-gray-500 mt-4">{t('loading')}</p>}
            {failed && <p role="alert" className="text-sm text-red-700 mt-4">{t('panel.loadFailed')}</p>}
            {!loading && !failed && rows.length === 0 && <p className="text-sm text-gray-500 mt-4">{t('panel.empty')}</p>}

            {rows.length > 0 && (
                <div className="mt-4 overflow-x-auto">
                    <table className="w-full text-sm" data-testid="approvals-table">
                        <thead>
                            <tr className="text-left text-gray-500 border-b border-gray-200">
                                <th scope="col" className="py-2 pr-3 font-medium">{t('panel.campaign')}</th>
                                <th scope="col" className="py-2 pr-3 font-medium">{t('panel.status')}</th>
                                <th scope="col" className="py-2 pr-3 font-medium text-right">{t('panel.cap')}</th>
                                <th scope="col" className="py-2 font-medium">{t('panel.created')}</th>
                            </tr>
                        </thead>
                        <tbody>
                            {rows.map((approval) => {
                                const campaign = byHash.get(approval.payload_hash);
                                const status = effectiveStatus(approval);
                                const cap = approval.max_budget_micros ?? approval.requested_budget_micros;
                                return (
                                    <tr key={approval.id} className="border-b border-gray-100" data-testid={`approval-row-${approval.id}`}>
                                        <td className="py-2 pr-3 text-gray-900">
                                            {campaign ? (
                                                <button
                                                    type="button"
                                                    onClick={() => onOpen(campaign)}
                                                    className="min-h-11 text-left font-medium text-amber-700 hover:text-amber-900 focus-visible:outline focus-visible:outline-2 focus-visible:outline-amber-600"
                                                >
                                                    {campaign.name}
                                                </button>
                                            ) : t('panel.unknownCampaign')}
                                        </td>
                                        <td className="py-2 pr-3"><span className={`inline-block rounded-full px-2.5 py-0.5 text-xs font-medium ${TONE[status] || TONE.expired}`}>{t(`status.${status}`)}</span></td>
                                        <td className="py-2 pr-3 text-right tabular-nums">{cap != null ? `$${formatNumber(microsToUsd(cap), { minimumFractionDigits: 2, maximumFractionDigits: 2 })}` : '—'}</td>
                                        <td className="py-2 text-gray-500 whitespace-nowrap">{approval.created_at ? formatDateTime(approval.created_at) : '—'}</td>
                                    </tr>
                                );
                            })}
                        </tbody>
                    </table>
                </div>
            )}
        </section>
    );
}
