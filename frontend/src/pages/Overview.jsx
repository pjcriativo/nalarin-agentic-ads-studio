import React, { useState, useEffect, useCallback, useMemo } from 'react';
import { LayoutDashboard, Target, TrendingUp, Music2 } from 'lucide-react';
import { Link } from 'react-router-dom';
import { useTranslation } from 'react-i18next';
import { BarChart, Bar, Cell, XAxis, YAxis, CartesianGrid, Tooltip, Legend, ResponsiveContainer } from 'recharts';
import { useAuth } from '../context/AuthContext';
import { translateBackendMessage } from '../i18n/backendErrors';
import { useToast } from '../context/ToastContext';
import PerformanceTable from '../components/PerformanceTable';
import OptimizationPanel from '../components/OptimizationPanel';

const API_URL = import.meta.env.VITE_API_URL || 'http://localhost:8000/api/v1';

const PLATFORM_COLORS = {
    meta: '#1877F2',
    google: '#F59E0B',
    tiktok: '#111827',
};

const PLATFORM_LABELS = {
    meta: 'Meta',
    google: 'Google Ads',
    tiktok: 'TikTok Ads',
};

const buildOverviewColumns = (t) => [
    { key: 'platform', label: t('columns.platform'), numeric: false, format: (v) => PLATFORM_LABELS[v] || v },
    { key: 'campaign_name', label: t('columns.campaign'), numeric: false },
    { key: 'impressions', label: t('columns.impressions'), numeric: true },
    { key: 'clicks', label: t('columns.clicks'), numeric: true },
    { key: 'spend', label: t('columns.spend'), numeric: true, format: (v) => `$${Number(v).toFixed(2)}` },
    { key: 'conversions', label: t('columns.conversions'), numeric: true },
    { key: 'cpa', label: t('columns.cpa'), numeric: true, format: (v) => (v == null ? '—' : `$${Number(v).toFixed(2)}`) },
];

/**
 * Combined Meta + Google Ads landing page (Sprint 2). Rows come pre-normalized
 * from GET /api/v1/overview -- this component only aggregates spend-by-platform
 * for the chart and renders the flat table. If a platform isn't connected, its
 * `errors` entry is shown as an inline notice instead of hiding the whole page.
 */
export default function Overview() {
    const { authFetch } = useAuth();
    const { showError } = useToast();
    const { t } = useTranslation('dashboard');
    const overviewColumns = useMemo(() => buildOverviewColumns(t), [t]);

    const [campaigns, setCampaigns] = useState([]);
    const [errors, setErrors] = useState({});
    const [loading, setLoading] = useState(true);
    const [datePreset, setDatePreset] = useState('last_30d');

    const loadOverview = useCallback(async () => {
        setLoading(true);
        try {
            const response = await authFetch(`${API_URL}/overview?date_preset=${datePreset}`);
            if (response.ok) {
                const data = await response.json();
                setCampaigns(data.campaigns || []);
                setErrors(data.errors || {});
            } else {
                const error = await response.json().catch(() => ({}));
                showError(translateBackendMessage(error.detail, 'dashboard:overview.loadFailed'));
            }
        } catch (error) {
            showError(t('overview.loadFailed'));
        } finally {
            setLoading(false);
        }
    }, [authFetch, datePreset, showError, t]);

    useEffect(() => {
        loadOverview();
    }, [loadOverview]);

    const spendByPlatform = useMemo(() => {
        const totals = {};
        for (const row of campaigns) {
            totals[row.platform] = (totals[row.platform] || 0) + (row.spend || 0);
        }
        return Object.entries(totals).map(([platform, spend]) => ({
            platform: PLATFORM_LABELS[platform] || platform,
            spend: Number(spend.toFixed(2)),
            fill: PLATFORM_COLORS[platform] || '#9CA3AF',
        }));
    }, [campaigns]);

    const errorEntries = Object.entries(errors).filter(([, message]) => !!message);

    return (
        <div className="max-w-6xl mx-auto space-y-4 sm:space-y-6">
            <div>
                <h1 className="text-2xl sm:text-3xl font-bold text-gray-900 mb-1 sm:mb-2 flex items-center gap-3">
                    <LayoutDashboard size={28} className="text-amber-600 sm:hidden" />
                    <LayoutDashboard size={32} className="text-amber-600 hidden sm:block" />
                    {t('overview.title')}
                </h1>
                <p className="text-sm sm:text-base text-gray-600">{t('overview.subtitle')}</p>
            </div>

            {errorEntries.length > 0 && (
                <div className="bg-amber-50 border border-amber-200 rounded-xl p-4 space-y-1">
                    {errorEntries.map(([platform, message]) => (
                        <p key={platform} className="text-sm text-amber-800">
                            <span className="font-semibold">{PLATFORM_LABELS[platform] || platform}:</span> {message}
                        </p>
                    ))}
                </div>
            )}

            <OptimizationPanel />

            {!loading && spendByPlatform.length > 0 && (
                <div className="bg-white rounded-xl border border-gray-200 shadow-sm p-6">
                    <h2 className="text-sm font-semibold text-gray-700 mb-4">{t('overview.spendByPlatform')}</h2>
                    <ResponsiveContainer width="100%" height={240}>
                        <BarChart data={spendByPlatform}>
                            <CartesianGrid strokeDasharray="3 3" vertical={false} />
                            <XAxis dataKey="platform" />
                            <YAxis tickFormatter={(v) => `$${v}`} />
                            <Tooltip formatter={(value) => [`$${value}`, t('columns.spend')]} />
                            <Legend />
                            <Bar dataKey="spend" name={t('columns.spend')} radius={[4, 4, 0, 0]}>
                                {spendByPlatform.map((entry) => (
                                    <Cell key={entry.platform} fill={entry.fill} />
                                ))}
                            </Bar>
                        </BarChart>
                    </ResponsiveContainer>
                </div>
            )}

            {/* Empty state CTA: a fresh account lands here with zero
                connections — give the operator a direct action instead of a
                bare "no campaigns" table. */}
            {!loading && campaigns.length === 0 && (
                <div className="bg-white rounded-xl border border-amber-200 shadow-sm p-6 sm:p-8 text-center">
                    <h2 className="text-lg font-bold text-gray-900">{t('overview.connectTitle')}</h2>
                    <p className="text-sm text-gray-600 mt-2 max-w-md mx-auto">
                        {t('overview.connectBody')}
                    </p>
                    <div className="flex flex-col sm:flex-row gap-3 justify-center mt-5">
                        <Link to="/facebook-campaigns" className="inline-flex items-center justify-center gap-2 px-4 py-2.5 bg-amber-600 text-white rounded-lg font-medium hover:bg-amber-700 transition-colors">
                            <Target size={18} /> {t('overview.connectMeta')}
                        </Link>
                        <Link to="/google-ads" className="inline-flex items-center justify-center gap-2 px-4 py-2.5 border border-amber-300 text-amber-800 rounded-lg font-medium hover:bg-amber-50 transition-colors">
                            <TrendingUp size={18} /> {t('overview.connectGoogle')}
                        </Link>
                        <Link to="/tiktok-ads" className="inline-flex items-center justify-center gap-2 px-4 py-2.5 border border-amber-300 text-amber-800 rounded-lg font-medium hover:bg-amber-50 transition-colors">
                            <Music2 size={18} /> {t('overview.connectTikTok')}
                        </Link>
                    </div>
                </div>
            )}

            <PerformanceTable
                rows={campaigns.map((row, index) => ({ ...row, id: `${row.platform}-${index}` }))}
                columns={overviewColumns}
                loading={loading}
                datePreset={datePreset}
                onDatePresetChange={setDatePreset}
                emptyMessage={campaigns.length === 0 ? '' : t('overview.noCampaignsInRange')}
            />
        </div>
    );
}
