import React from 'react';
import { Link } from 'react-router-dom';
import { useTranslation } from 'react-i18next';
import { CircleAlert, TriangleAlert } from 'lucide-react';

const TONE = {
    bad: 'bg-red-50 border-red-200 text-red-900',
    warn: 'bg-amber-50 border-amber-200 text-amber-900',
};

/** Honest setup warnings: worker offline, no AI model/credential, spending limit reached, no Google Ads account. */
export default function ReadinessAlerts({ alerts }) {
    const { t } = useTranslation('agents');
    if (alerts.length === 0) return null;
    return (
        <div className="space-y-3 mb-6" data-testid="agents-alerts">
            {alerts.map((alert) => {
                const Icon = alert.tone === 'bad' ? CircleAlert : TriangleAlert;
                return (
                    <div
                        key={alert.id}
                        role={alert.tone === 'bad' ? 'alert' : 'status'}
                        data-testid={`alert-${alert.id}`}
                        className={`flex items-start gap-3 text-sm border rounded-lg px-4 py-3 ${TONE[alert.tone]}`}
                    >
                        <Icon size={18} className="flex-shrink-0 mt-0.5" aria-hidden="true" />
                        <div className="min-w-0">
                            <p className="font-medium">{t(`alerts.${alert.id}.title`)}</p>
                            <p className="mt-0.5">
                                {t(`alerts.${alert.id}.body`, { count: alert.pending, period: alert.period ? t(`alerts.period.${alert.period}`) : '' })}
                                {alert.link && (
                                    <>
                                        {' '}
                                        <Link to={alert.link} className="font-medium underline underline-offset-2 focus-visible:outline focus-visible:outline-2 focus-visible:outline-amber-600">
                                            {t(`alerts.${alert.id}.link`)}
                                        </Link>
                                    </>
                                )}
                            </p>
                        </div>
                    </div>
                );
            })}
        </div>
    );
}
