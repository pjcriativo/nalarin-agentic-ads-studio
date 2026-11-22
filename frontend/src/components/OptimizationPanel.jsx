import React, { useState, useEffect } from 'react';
import { useTranslation } from 'react-i18next';
import { Shield, ShieldAlert, Cpu, CheckCircle2, AlertTriangle, Play, Pause, RefreshCw } from 'lucide-react';
import { useAuth } from '../context/AuthContext';
import { useToast } from '../context/ToastContext';

const API_URL = import.meta.env.VITE_API_URL || 'http://localhost:8000/api/v1';

export default function OptimizationPanel() {
    const { authFetch } = useAuth();
    const { showSuccess, showError } = useToast();
    const { t } = useTranslation('dashboard');

    const [statusData, setStatusData] = useState(null);
    const [loading, setLoading] = useState(true);
    const [evaluating, setEvaluating] = useState(false);
    const [testResult, setTestResult] = useState(null);

    const fetchStatus = async () => {
        setLoading(true);
        try {
            const res = await authFetch(`${API_URL}/optimization/status`);
            if (res.ok) {
                const data = await res.json();
                setStatusData(data);
            } else {
                showError(t('optimization.loadFailed'));
            }
        } catch {
            showError(t('optimization.networkError'));
        } finally {
            setLoading(false);
        }
    };

    useEffect(() => {
        fetchStatus();
    }, []);

    const runSimulationTest = async () => {
        setEvaluating(true);
        setTestResult(null);
        try {
            const res = await authFetch(`${API_URL}/optimization/evaluate`, {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({
                    provider: 'google_ads',
                    account_id: 'test_account',
                    action: 'campaign.pause',
                    payload: { campaign_id: '12345' }
                })
            });
            const data = await res.json();
            setTestResult(data);
            if (data.ok) {
                showSuccess(t('optimization.testPassed'));
            } else {
                showSuccess(t('optimization.testManual', { reason: data.reason }));
            }
        } catch {
            showError(t('optimization.simulationFailed'));
        } finally {
            setEvaluating(false);
        }
    };

    if (loading) {
        return (
            <div className="bg-white rounded-lg border border-gray-200 p-6 flex items-center justify-center text-gray-500">
                <RefreshCw className="w-5 h-5 animate-spin mr-2" /> {t('optimization.loading')}
            </div>
        );
    }

    if (!statusData) return null;

    return (
        <div className="bg-white rounded-lg border border-gray-200 p-6 space-y-6">
            <div className="flex items-center justify-between border-b border-gray-100 pb-4">
                <div className="flex items-center space-x-3">
                    <div className="p-2 bg-indigo-50 rounded-lg text-indigo-600">
                        <Cpu className="w-6 h-6" />
                    </div>
                    <div>
                        <h2 className="text-lg font-semibold text-gray-900">{t('optimization.title')}</h2>
                        <p className="text-sm text-gray-500">{t('optimization.subtitle')}</p>
                    </div>
                </div>

                <div className="flex items-center space-x-2">
                    {statusData.kill_switch_active ? (
                        <span className="inline-flex items-center px-3 py-1 rounded-full text-xs font-medium bg-red-100 text-red-800">
                            <ShieldAlert className="w-3.5 h-3.5 mr-1" /> {t('optimization.killSwitch')}
                        </span>
                    ) : statusData.auto_enabled && statusData.engine_active ? (
                        <span className="inline-flex items-center px-3 py-1 rounded-full text-xs font-medium bg-green-100 text-green-800">
                            <CheckCircle2 className="w-3.5 h-3.5 mr-1" /> {t('optimization.autoOn')}
                        </span>
                    ) : (
                        <span className="inline-flex items-center px-3 py-1 rounded-full text-xs font-medium bg-amber-100 text-amber-800">
                            <Shield className="w-3.5 h-3.5 mr-1" /> {t('optimization.manualOnly')}
                        </span>
                    )}
                </div>
            </div>

            {!statusData.engine_active && (
                <div role="status" className="p-3 bg-amber-50 border border-amber-200 rounded-lg text-sm text-amber-800">
                    {t('optimization.engineInactive')}
                </div>
            )}

            <div className="grid grid-cols-1 md:grid-cols-3 gap-4 text-sm">
                <div className="p-4 bg-gray-50 rounded-lg border border-gray-100">
                    <span className="text-gray-500 block text-xs uppercase font-semibold">{t('optimization.allowedActions')}</span>
                    <div className="mt-2 flex flex-wrap gap-1">
                        {statusData.allowed_actions.map(act => (
                            <span key={act} className="px-2 py-0.5 bg-white border border-gray-200 rounded text-xs text-gray-700 font-mono">
                                {act}
                            </span>
                        ))}
                    </div>
                </div>

                <div className="p-4 bg-gray-50 rounded-lg border border-gray-100">
                    <span className="text-gray-500 block text-xs uppercase font-semibold">{t('optimization.safetyCeilings')}</span>
                    <div className="mt-2 space-y-1 text-xs text-gray-700">
                        <div>{t('optimization.maxBudget')}: <span className="font-semibold">{statusData.max_budget_micros ? `$${(statusData.max_budget_micros / 1000000).toFixed(2)}` : t('optimization.unlimited')}</span></div>
                        <div>{t('optimization.maxDailySpend')}: <span className="font-semibold">{statusData.max_daily_spend_micros ? `$${(statusData.max_daily_spend_micros / 1000000).toFixed(2)}` : t('optimization.unlimited')}</span></div>
                    </div>
                </div>

                <div className="p-4 bg-gray-50 rounded-lg border border-gray-100">
                    <span className="text-gray-500 block text-xs uppercase font-semibold">{t('optimization.operatingWindow')}</span>
                    <div className="mt-2 text-xs text-gray-700">
                        {statusData.operating_hours.start !== null && statusData.operating_hours.end !== null ? (
                            <span>{statusData.operating_hours.start}:00 - {statusData.operating_hours.end}:00 ({statusData.operating_hours.timezone})</span>
                        ) : (
                            <span>{statusData.engine_active ? t('optimization.monitored') : t('optimization.notMonitored')}</span>
                        )}
                    </div>
                </div>
            </div>

            <div className="flex items-center justify-between pt-2">
                <button
                    onClick={runSimulationTest}
                    disabled={evaluating}
                    className="inline-flex items-center px-3.5 py-2 bg-indigo-600 hover:bg-indigo-700 text-white rounded-md text-sm font-medium transition-colors disabled:opacity-50"
                >
                    {evaluating ? <RefreshCw className="w-4 h-4 animate-spin mr-2" /> : <Play className="w-4 h-4 mr-2" />}
                    {t('optimization.simulate')}
                </button>

                {testResult && (
                    <div className="text-xs font-mono px-3 py-1.5 bg-gray-100 rounded border border-gray-200">
                        {t('optimization.verdict')}: <span className={testResult.ok ? "text-green-600 font-bold" : "text-amber-600 font-bold"}>{testResult.mode.toUpperCase()}</span> ({testResult.reason || 'policy-pass'})
                    </div>
                )}
            </div>
        </div>
    );
}
