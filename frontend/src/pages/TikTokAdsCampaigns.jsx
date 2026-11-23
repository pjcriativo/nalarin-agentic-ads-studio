import React, { useCallback, useEffect, useState } from 'react';
import { useTranslation } from 'react-i18next';
import { translateBackendMessage } from '../i18n/backendErrors';
import { Music2, Plus } from 'lucide-react';
import { useAuth } from '../context/AuthContext';
import { useOrganization } from '../context/OrganizationContext';
import { useToast } from '../context/ToastContext';
import ConnectAccountCard from '../components/ConnectAccountCard';
import ConfirmationModal from '../components/ConfirmationModal';
import PerformanceTable from '../components/PerformanceTable';

const API_URL = import.meta.env.VITE_API_URL || 'http://localhost:8000/api/v1';

const buildCampaignColumns = (t) => [
    { key: 'campaign_name', label: t('columns.campaign'), numeric: false },
    { key: 'impressions', label: t('columns.impressions'), numeric: true },
    { key: 'clicks', label: t('columns.clicks'), numeric: true },
    { key: 'spend', label: t('columns.spend'), numeric: true, format: (value) => `$${Number(value || 0).toFixed(2)}` },
    { key: 'conversion', label: t('columns.conversions'), numeric: true },
];

export default function TikTokAdsCampaigns() {
    const { t } = useTranslation('campaigns');
    const { authFetch } = useAuth();
    const { can } = useOrganization(); // UI hints only: the backend authorizes every call
    const { showError, showSuccess } = useToast();
    const [connection, setConnection] = useState(null);
    const [connections, setConnections] = useState([]);
    const [loadingConnection, setLoadingConnection] = useState(true);
    const [selectingAccount, setSelectingAccount] = useState(false);
    const [selectingId, setSelectingId] = useState(null);
    const [campaigns, setCampaigns] = useState([]);
    const [loadingCampaigns, setLoadingCampaigns] = useState(false);
    const [datePreset, setDatePreset] = useState('last_30d');
    const [showCreateForm, setShowCreateForm] = useState(false);
    const [name, setName] = useState('');
    const [dailyBudget, setDailyBudget] = useState('');
    const [preview, setPreview] = useState(null);

    const loadConnection = useCallback(async () => {
        setLoadingConnection(true);
        try {
            const response = await authFetch(`${API_URL}/tiktok-ads/connection`);
            if (response.ok) setConnection(await response.json());
        } catch {
            showError(t('tikTokAdsCampaigns.failedToLoadTiktokAds'));
        } finally {
            setLoadingConnection(false);
        }
    // eslint-disable-next-line react-hooks/exhaustive-deps -- `t` only formats messages; changing language must not re-fetch data
    }, [authFetch, showError]);

    const loadConnections = useCallback(async () => {
        try {
            const response = await authFetch(`${API_URL}/tiktok-ads/connections`);
            if (response.ok) {
                const data = await response.json();
                const candidates = data.connections || [];
                setConnections(candidates);
                if (candidates.length > 0 && !candidates.some((candidate) => candidate.selected)) {
                    setSelectingAccount(true);
                }
            }
        } catch {
            // account-choices panel is additive; silent on failure
        }
    }, [authFetch]);

    useEffect(() => {
        loadConnection();
        loadConnections();
        const query = new URLSearchParams(window.location.search);
        if (query.get('connected') === '1') {
            showSuccess(t('tikTokAdsCampaigns.tiktokAdsConnected'));
            window.history.replaceState({}, '', window.location.pathname);
        } else if (query.get('select') === '1') {
            setSelectingAccount(true);
            window.history.replaceState({}, '', window.location.pathname);
        }
    // eslint-disable-next-line react-hooks/exhaustive-deps -- `t` only formats messages; changing language must not re-fetch data
    }, [loadConnection, loadConnections, showSuccess]);

    const loadCampaigns = useCallback(async () => {
        if (!connection?.connected) return;
        setLoadingCampaigns(true);
        try {
            const response = await authFetch(`${API_URL}/tiktok-ads/campaigns?date_preset=${datePreset}`);
            if (response.ok) {
                const data = await response.json();
                setCampaigns(data.campaigns || []);
            } else if (response.status !== 404 && response.status !== 503) {
                const error = await response.json().catch(() => ({}));
                showError(translateBackendMessage(error.detail) || t('tikTokAdsCampaigns.failedToLoadTiktokCampaigns'));
            }
        } finally {
            setLoadingCampaigns(false);
        }
    // eslint-disable-next-line react-hooks/exhaustive-deps -- `t` only formats messages; changing language must not re-fetch data
    }, [authFetch, connection?.connected, datePreset, showError]);

    const connect = async () => {
        try {
            const response = await authFetch(`${API_URL}/tiktok-ads/oauth/start`);
            if (!response.ok) {
                const error = await response.json().catch(() => ({}));
                throw new Error(translateBackendMessage(error.detail) || t('tikTokAdsCampaigns.failedToStartTiktokConnection'));
            }
            window.location.href = (await response.json()).oauth_url;
        } catch (error) {
            showError(translateBackendMessage(error.message) || t('tikTokAdsCampaigns.failedToStartTiktokConnection'));
        }
    };

    const disconnect = async () => {
        try {
            const response = await authFetch(`${API_URL}/tiktok-ads/connection`, { method: 'DELETE' });
            if (!response.ok) throw new Error(t('tikTokAdsCampaigns.failedToDisconnectTiktokAds'));
            setConnection({ connected: false });
            setConnections((current) => current.map((candidate) => ({ ...candidate, selected: false })));
            setCampaigns([]);
            showSuccess(t('tikTokAdsCampaigns.tiktokAdsDisconnected'));
        } catch (error) {
            showError(translateBackendMessage(error.message) || t('tikTokAdsCampaigns.failedToDisconnectTiktokAds'));
        }
    };

    const selectAdvertiser = async (advertiserId) => {
        setSelectingId(advertiserId);
        try {
            const response = await authFetch(`${API_URL}/tiktok-ads/connection/select`, {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ advertiser_id: advertiserId }),
            });
            if (!response.ok) {
                const error = await response.json().catch(() => ({}));
                throw new Error(translateBackendMessage(error.detail) || t('tikTokAdsCampaigns.failedToSelectTiktokAdvertiser'));
            }
            setConnection(await response.json());
            setCampaigns([]);
            setConnections((current) => current.map((candidate) => ({
                ...candidate,
                selected: candidate.advertiser_id === advertiserId,
            })));
            setSelectingAccount(false);
            showSuccess(t('tikTokAdsCampaigns.tiktokAdvertiserSelected'));
        } catch (error) {
            showError(translateBackendMessage(error.message) || t('tikTokAdsCampaigns.failedToSelectTiktokAdvertiser'));
        } finally {
            setSelectingId(null);
        }
    };

    const reviewCreate = (event) => {
        event.preventDefault();
        const budget = Number(dailyBudget);
        if (!name.trim() || !Number.isFinite(budget) || budget <= 0) {
            showError(t('tikTokAdsCampaigns.enterACampaignNameAnd'));
            return;
        }
        setPreview({ name: name.trim(), dailyBudget: budget });
    };

    const create = async () => {
        if (!preview) return;
        try {
            const response = await authFetch(`${API_URL}/tiktok-ads/campaigns`, {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ name: preview.name, daily_budget: preview.dailyBudget, confirm: true }),
            });
            if (!response.ok) {
                const error = await response.json().catch(() => ({}));
                throw new Error(translateBackendMessage(error.detail) || t('tikTokAdsCampaigns.failedToCreateTiktokCampaign'));
            }
            showSuccess(t('tikTokAdsCampaigns.tiktokCampaignCreatedPaused'));
            setName(''); setDailyBudget(''); setShowCreateForm(false);
            loadCampaigns();
        } catch (error) {
            showError(translateBackendMessage(error.message) || t('tikTokAdsCampaigns.failedToCreateTiktokCampaign'));
        } finally {
            setPreview(null);
        }
    };

    return (
        <div className="max-w-6xl mx-auto space-y-4 sm:space-y-6">
            <div className="flex flex-col sm:flex-row sm:items-start sm:justify-between gap-3 sm:gap-4">
                <div>
                    <h1 className="text-2xl sm:text-3xl font-bold text-gray-900 mb-1 sm:mb-2 flex items-center gap-3">
                        <Music2 size={28} className="text-amber-600 sm:hidden" />
                        <Music2 size={32} className="text-amber-600 hidden sm:block" />
                        TikTok Ads
                    </h1>
                    <p className="text-sm sm:text-base text-gray-600">{t('tikTokAdsCampaigns.connectATiktokAdvertiserTo')}</p>
                </div>
                {connection?.connected && can('ads:draft') && <button onClick={() => setShowCreateForm((visible) => !visible)} className="inline-flex items-center gap-2 px-4 py-2 bg-amber-600 text-white rounded-lg font-medium hover:bg-amber-700"><Plus size={18} />{t('tikTokAdsCampaigns.createCampaign')}</button>}
            </div>

            {!loadingConnection && (
                <div className="space-y-3">
                    <ConnectAccountCard canManage={can('connections:manage')} platformName="TikTok Ads" icon={Music2} connected={!!connection?.connected} accountLabel={connection?.account_name || connection?.advertiser_id} connectedAt={connection?.connected_at} onConnect={connect} onDisconnect={disconnect} />
                    {connection?.connected && connections.length > 1 && !selectingAccount && (
                        <button type="button" onClick={() => setSelectingAccount(true)} className="text-sm font-medium text-amber-700 hover:text-amber-900 focus-visible:outline focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-amber-600">
                            {t('tikTokAdsCampaigns.changeTiktokAdvertiser')}
                        </button>
                    )}
                </div>
            )}

            {selectingAccount && connections.length > 0 && (
                <section aria-labelledby="tiktok-account-heading" className="border-y border-gray-200 py-5">
                    <div className="flex items-center justify-between gap-4 mb-3">
                        <h2 id="tiktok-account-heading" className="text-lg font-bold text-gray-900">{t('tikTokAdsCampaigns.chooseATiktokAdvertiser')}</h2>
                        {connection?.connected && (
                            <button type="button" onClick={() => setSelectingAccount(false)} className="text-sm text-gray-600 hover:text-gray-900">{t('tikTokAdsCampaigns.cancel')}</button>
                        )}
                    </div>
                    <div className="grid gap-2 sm:grid-cols-2 lg:grid-cols-3">
                        {connections.map((candidate) => (
                            <button
                                key={candidate.advertiser_id}
                                type="button"
                                onClick={() => selectAdvertiser(candidate.advertiser_id)}
                                disabled={selectingId !== null}
                                aria-pressed={candidate.selected}
                                className={`min-h-16 border px-4 py-3 text-left transition-colors focus-visible:outline focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-amber-600 disabled:opacity-60 ${candidate.selected ? 'border-amber-600 bg-amber-50' : 'border-gray-300 bg-white hover:border-amber-500'}`}
                            >
                                <span className="block font-semibold text-gray-900">{candidate.account_name || t('tiktok.advertiserFallback')}</span>
                                <span className="block text-sm text-gray-500">{candidate.advertiser_id}</span>
                                {selectingId === candidate.advertiser_id && <span className="block text-xs text-amber-700 mt-1">{t('tikTokAdsCampaigns.selecting')}</span>}
                            </button>
                        ))}
                    </div>
                </section>
            )}

            {showCreateForm && <form onSubmit={reviewCreate} className="bg-white rounded-xl border border-gray-200 shadow-sm p-4 sm:p-6 space-y-4">
                <h2 className="text-lg font-bold text-gray-900">{t('tikTokAdsCampaigns.newTiktokCampaign')}</h2>
                <div className="grid sm:grid-cols-2 gap-4">
                    <label className="text-sm font-medium text-gray-700">{t('tikTokAdsCampaigns.campaignName')}<input value={name} onChange={(event) => setName(event.target.value)} className="mt-1 w-full border border-gray-300 rounded-lg px-3 py-2" /></label>
                    <label className="text-sm font-medium text-gray-700">{t('tikTokAdsCampaigns.dailyBudgetUsd')}<input type="number" min="1" step="0.01" value={dailyBudget} onChange={(event) => setDailyBudget(event.target.value)} className="mt-1 w-full border border-gray-300 rounded-lg px-3 py-2" /></label>
                </div>
                <p className="text-xs text-gray-500">{t('tikTokAdsCampaigns.theCampaignWillBeCreated')}</p>
                <div className="flex justify-end gap-3"><button type="button" onClick={() => setShowCreateForm(false)} className="px-4 py-2 text-gray-700">{t('tikTokAdsCampaigns.cancel')}</button><button type="submit" className="px-4 py-2 bg-amber-600 text-white rounded-lg">Review &amp; create</button></div>
            </form>}

            {connection?.connected && <PerformanceTable rows={campaigns.map((row) => ({ ...row, id: row.campaign_id }))} columns={buildCampaignColumns(t)} loading={loadingCampaigns} datePreset={datePreset} onDatePresetChange={setDatePreset} emptyMessage={t('tikTokAdsCampaigns.noTiktokCampaignsFoundFor')} />}

            <ConfirmationModal isOpen={!!preview} onClose={() => setPreview(null)} onConfirm={create} title={t('tikTokAdsCampaigns.createThisTiktokCampaign')} message={preview ? `Create "${preview.name}" with a $${preview.dailyBudget.toFixed(2)} daily budget. It will remain PAUSED until explicitly enabled.` : ''} confirmText={t('tikTokAdsCampaigns.createPausedCampaign')} isDestructive={false} icon={Plus} />
        </div>
    );
}
