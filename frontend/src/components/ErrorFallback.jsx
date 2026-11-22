import React from 'react';
import { useTranslation } from 'react-i18next';

export default function ErrorFallback({ onRetry }) {
    const { t } = useTranslation('errors');
    return (
        <div className="min-h-[50vh] flex items-center justify-center p-6">
            <div role="alert" className="bg-white border border-amber-200 rounded-xl shadow-sm p-6 max-w-md w-full">
                <h2 className="text-lg font-bold text-gray-900">{t('boundary.title')}</h2>
                <p className="mt-2 text-sm text-gray-600">
                    {t('boundary.description')}
                </p>
                <button
                    type="button"
                    onClick={onRetry}
                    className="mt-4 px-4 py-2 text-sm font-medium text-white bg-amber-600 rounded-lg hover:bg-amber-700"
                >
                    {t('boundary.retry')}
                </button>
            </div>
        </div>
    );
}
