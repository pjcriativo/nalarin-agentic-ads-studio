import { useToast } from '../context/ToastContext';
import { useTranslation } from 'react-i18next';
import React, { useState, useEffect } from 'react';
import { getBlacklist, addToBlacklist, removeFromBlacklist, getKeywordBlacklist, addToKeywordBlacklist, removeFromKeywordBlacklist } from '../api/research';

const ResearchSettings = () => {
    const { t } = useTranslation('research');
    const { showSuccess, showError } = useToast();
    const [blacklist, setBlacklist] = useState([]);
    const [keywordBlacklist, setKeywordBlacklist] = useState([]);
    const [showBlacklistModal, setShowBlacklistModal] = useState(false);
    const [blacklistPageName, setBlacklistPageName] = useState('');
    const [showKeywordModal, setShowKeywordModal] = useState(false);
    const [blacklistKeyword, setBlacklistKeyword] = useState('');

    const fetchBlacklist = async () => {
        try {
            const data = await getBlacklist();
            setBlacklist(data);
        } catch (error) {
            console.error('Failed to load blacklist', error);
        }
    };

    const fetchKeywordBlacklist = async () => {
        try {
            const data = await getKeywordBlacklist();
            setKeywordBlacklist(data);
        } catch (error) {
            console.error('Failed to load keyword blacklist', error);
        }
    };

    useEffect(() => {
        fetchBlacklist();
        fetchKeywordBlacklist();
        // eslint-disable-next-line react-hooks/exhaustive-deps -- run once on mount
    }, []);

    const handleAddToBlacklist = async (pageName) => {
        if (!pageName) {
            setShowBlacklistModal(true);
            return;
        }

        try {
            await addToBlacklist(pageName);
            showSuccess(t('researchSettings.addedPagenameToBlacklist', { pageName }));
            fetchBlacklist();
            setBlacklistPageName('');
            setShowBlacklistModal(false);
        } catch {
            showError(t('researchSettings.failedToAddToBlacklist'));
        }
    };

    const handleRemoveFromBlacklist = async (id, pageName) => {
        try {
            await removeFromBlacklist(id);
            showSuccess(t('researchSettings.removedPagenameFromBlacklist', { pageName }));
            fetchBlacklist();
        } catch {
            showError(t('researchSettings.failedToRemoveFromBlacklist'));
        }
    };

    const handleAddToKeywordBlacklist = async (keyword) => {
        if (!keyword) {
            setShowKeywordModal(true);
            return;
        }

        try {
            await addToKeywordBlacklist(keyword);
            showSuccess(t('researchSettings.addedKeywordToKeywordBlacklist', { keyword }));
            fetchKeywordBlacklist();
            setBlacklistKeyword('');
            setShowKeywordModal(false);
        } catch {
            showError(t('researchSettings.failedToAddToKeyword'));
        }
    };

    const handleRemoveFromKeywordBlacklist = async (id, keyword) => {
        try {
            await removeFromKeywordBlacklist(id);
            showSuccess(t('researchSettings.removedKeywordFromKeywordBlacklist', { keyword }));
            fetchKeywordBlacklist();
        } catch {
            showError(t('researchSettings.failedToRemoveFromKeyword'));
        }
    };

    return (
        <div className="max-w-6xl mx-auto p-6">
            <h1 className="text-3xl font-bold mb-6">{t('researchSettings.researchSettings')}</h1>

            {/* Page Blacklist Section */}
            <div className="bg-white rounded-lg shadow-sm border border-gray-200 p-6 mb-6">
                <div className="flex justify-between items-center mb-4">
                    <h2 className="text-xl font-semibold">{t('researchSettings.pageBlacklist')}</h2>
                    <button
                        onClick={() => setShowBlacklistModal(true)}
                        className="px-4 py-2 bg-red-600 text-white rounded hover:bg-red-700"
                    >
                        {t('researchSettings.addPage')}
                    </button>
                </div>
                <p className="text-gray-600 mb-4">
                    {t('researchSettings.pagesAddedHereWillBe')}
                </p>

                {blacklist.length === 0 ? (
                    <p className="text-gray-500 text-center py-8">{t('researchSettings.noBlacklistedPages')}</p>
                ) : (
                    <div className="space-y-2">
                        {blacklist.map((item) => (
                            <div key={item.id} className="flex justify-between items-center p-3 bg-gray-50 rounded border border-gray-200">
                                <div>
                                    <p className="font-medium">{item.page_name}</p>
                                    {item.reason && <p className="text-sm text-gray-600">{item.reason}</p>}
                                </div>
                                <button
                                    onClick={() => handleRemoveFromBlacklist(item.id, item.page_name)}
                                    className="px-3 py-1 bg-red-100 text-red-700 rounded hover:bg-red-200 text-sm"
                                >
                                    {t('researchSettings.remove')}
                                </button>
                            </div>
                        ))}
                    </div>
                )}
            </div>

            {/* Keyword Blacklist Section */}
            <div className="bg-white rounded-lg shadow-sm border border-gray-200 p-6">
                <div className="flex justify-between items-center mb-4">
                    <h2 className="text-xl font-semibold">{t('researchSettings.keywordBlacklist')}</h2>
                    <button
                        onClick={() => setShowKeywordModal(true)}
                        className="px-4 py-2 bg-red-600 text-white rounded hover:bg-red-700"
                    >
                        {t('researchSettings.addKeyword')}
                    </button>
                </div>
                <p className="text-gray-600 mb-4">
                    {t('researchSettings.keywordsAddedHereWillFilter')}
                </p>

                {keywordBlacklist.length === 0 ? (
                    <p className="text-gray-500 text-center py-8">{t('researchSettings.noBlacklistedKeywords')}</p>
                ) : (
                    <div className="flex flex-wrap gap-2">
                        {keywordBlacklist.map((item) => (
                            <div key={item.id} className="flex items-center gap-2 px-3 py-2 bg-red-50 border border-red-200 rounded">
                                <span className="font-medium">{item.keyword}</span>
                                <button
                                    onClick={() => handleRemoveFromKeywordBlacklist(item.id, item.keyword)}
                                    className="text-red-600 hover:text-red-800"
                                >
                                    ×
                                </button>
                            </div>
                        ))}
                    </div>
                )}
            </div>

            {/* Page Blacklist Modal */}
            {showBlacklistModal && (
                <div className="fixed inset-0 bg-black bg-opacity-50 flex items-center justify-center z-50">
                    <div className="bg-white rounded-lg p-6 max-w-md w-full mx-4">
                        <h3 className="text-lg font-semibold mb-4">{t('researchSettings.addPageToBlacklist')}</h3>
                        <input
                            type="text"
                            value={blacklistPageName}
                            onChange={(e) => setBlacklistPageName(e.target.value)}
                            placeholder={t('researchSettings.pageName')}
                            className="w-full p-3 border border-gray-300 rounded-lg focus:ring-2 focus:ring-red-500 outline-none mb-4"
                            autoFocus
                        />
                        <div className="flex gap-2">
                            <button
                                onClick={() => {
                                    setShowBlacklistModal(false);
                                    setBlacklistPageName('');
                                }}
                                className="flex-1 px-4 py-2 border border-gray-300 text-gray-700 rounded hover:bg-gray-50"
                            >
                                {t('researchSettings.cancel')}
                            </button>
                            <button
                                onClick={() => handleAddToBlacklist(blacklistPageName)}
                                className="flex-1 px-4 py-2 bg-red-600 text-white rounded hover:bg-red-700"
                            >
                                {t('researchSettings.add')}
                            </button>
                        </div>
                    </div>
                </div>
            )}

            {/* Keyword Blacklist Modal */}
            {showKeywordModal && (
                <div className="fixed inset-0 bg-black bg-opacity-50 flex items-center justify-center z-50">
                    <div className="bg-white rounded-lg p-6 max-w-md w-full mx-4">
                        <h3 className="text-lg font-semibold mb-4">{t('researchSettings.addKeywordToBlacklist')}</h3>
                        <input
                            type="text"
                            value={blacklistKeyword}
                            onChange={(e) => setBlacklistKeyword(e.target.value)}
                            placeholder={t('researchSettings.keyword')}
                            className="w-full p-3 border border-gray-300 rounded-lg focus:ring-2 focus:ring-red-500 outline-none mb-4"
                            autoFocus
                        />
                        <div className="flex gap-2">
                            <button
                                onClick={() => {
                                    setShowKeywordModal(false);
                                    setBlacklistKeyword('');
                                }}
                                className="flex-1 px-4 py-2 border border-gray-300 text-gray-700 rounded hover:bg-gray-50"
                            >
                                {t('researchSettings.cancel')}
                            </button>
                            <button
                                onClick={() => handleAddToKeywordBlacklist(blacklistKeyword)}
                                className="flex-1 px-4 py-2 bg-red-600 text-white rounded hover:bg-red-700"
                            >
                                {t('researchSettings.add')}
                            </button>
                        </div>
                    </div>
                </div>
            )}
        </div>
    );
};

export default ResearchSettings;
