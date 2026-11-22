import { useToast } from '../context/ToastContext';
import { useTranslation } from 'react-i18next';
import { translateBackendMessage } from '../i18n/backendErrors';
import React, { useState, useEffect } from 'react';
import { useLocation } from 'react-router-dom';
import { formatDate, formatDateTime, formatNumber, countryName } from '../i18n/format';
import { searchAndSave, getSavedSearches, deleteSavedSearch, getApiUsage, getBlacklist, addToBlacklist, removeFromBlacklist, getKeywordBlacklist, addToKeywordBlacklist, removeFromKeywordBlacklist, getRateLimit, getVerticals, createVertical, getVerticalAggregatedAds, getVerticalPageAds } from '../api/research';

const COUNTRIES = [
    { code: 'US', name: 'United States' },
    { code: 'GB', name: 'United Kingdom' },
    { code: 'DE', name: 'Germany' },
    { code: 'FR', name: 'France' },
    { code: 'CA', name: 'Canada' },
    { code: 'AU', name: 'Australia' },
];

const LIMIT_OPTIONS = [
    { value: 100, label: '100 ads', apiCalls: 1 },
    { value: 300, label: '300 ads', apiCalls: 3 },
    { value: 500, label: '500 ads', apiCalls: 5 },
    { value: 1000, label: '1,000 ads', apiCalls: 10 },
    { value: 2000, label: '2,000 ads', apiCalls: 20 },
    { value: 5000, label: '5,000 ads', apiCalls: 50 },
    { value: 10000, label: '10,000 ads', apiCalls: 100 },
];

const Research = () => {
    const { t } = useTranslation('research');
    const { showSuccess, showError, showInfo } = useToast();
    const location = useLocation();
    const [query, setQuery] = useState('');
    const [country, setCountry] = useState('US');
    const [negativeKeywords, setNegativeKeywords] = useState('');
    const [limit, setLimit] = useState(300);
    const [savedSearches, setSavedSearches] = useState([]);
    const [selectedSearch, setSelectedSearch] = useState(null);
    const [loading, setLoading] = useState(false);
    const [activeTab, setActiveTab] = useState('verticals');
    const [selectedVertical, setSelectedVertical] = useState(null);
    const [apiUsage, setApiUsage] = useState([]);
    const [blacklist, setBlacklist] = useState([]);
    const [showBlacklistModal, setShowBlacklistModal] = useState(false);
    const [blacklistPageName, setBlacklistPageName] = useState('');
    const [keywordBlacklist, setKeywordBlacklist] = useState([]);
    const [showKeywordModal, setShowKeywordModal] = useState(false);
    const [blacklistKeyword, setBlacklistKeyword] = useState('');
    const [rateLimit, setRateLimit] = useState(null);
    const [progressMessage, setProgressMessage] = useState('');
    const [verticals, setVerticals] = useState([]);
    const [showVerticalModal, setShowVerticalModal] = useState(false);
    const [newVerticalName, setNewVerticalName] = useState('');
    const [newVerticalDescription, setNewVerticalDescription] = useState('');
    const [searchType, setSearchType] = useState('one_time');
    const [expandedPages, setExpandedPages] = useState(new Set());
    const [aggregatedAds, setAggregatedAds] = useState([]);
    const [pageAds, setPageAds] = useState({});
    const [verticalTab, setVerticalTab] = useState('aggregated');
    const [aggregatedFilter, setAggregatedFilter] = useState('');

    useEffect(() => {
        // Set activeTab based on route
        if (!selectedVertical) {
            setActiveTab('verticals');
        }
    }, [location.pathname]);

    useEffect(() => {
        fetchVerticals();
        fetchRateLimit();
        if (activeTab === 'saved-searches') {
            fetchSavedSearches();
            fetchApiUsage();
            fetchBlacklist();
            fetchKeywordBlacklist();
        }
        if (activeTab === 'vertical-detail' && selectedVertical) {
            console.log('vertical-detail tab active, fetching data for vertical:', selectedVertical.name);
            fetchBlacklist();
            fetchKeywordBlacklist();
            fetchSavedSearches();
            fetchAggregatedAds();
        }
    }, [activeTab, selectedVertical]);

    const fetchRateLimit = async () => {
        try {
            const data = await getRateLimit();
            setRateLimit(data);
        } catch (error) {
            console.error('Failed to load rate limit', error);
        }
    };

    const fetchVerticals = async () => {
        try {
            const data = await getVerticals();
            setVerticals(Array.isArray(data) ? data : []);
        } catch (error) {
            console.error('Failed to load verticals', error);
            setVerticals([]);
        }
    };

    const fetchAggregatedAds = async () => {
        if (!selectedVertical) {
            console.log('No selected vertical, skipping aggregated ads fetch');
            return;
        }

        try {
            console.log('Fetching aggregated ads for vertical:', selectedVertical.id);
            const data = await getVerticalAggregatedAds(selectedVertical.id);
            console.log('Aggregated ads data:', data);
            setAggregatedAds(Array.isArray(data) ? data : []);
        } catch (error) {
            console.error('Failed to load aggregated ads', error);
            showError(t('research.failedToLoadAggregatedAds'));
            setAggregatedAds([]);
        }
    };

    const togglePageExpansion = async (pageId) => {
        const newExpandedPages = new Set(expandedPages);

        if (expandedPages.has(pageId)) {
            // Collapse
            newExpandedPages.delete(pageId);
            setExpandedPages(newExpandedPages);
        } else {
            // Expand - fetch ads if not already loaded
            newExpandedPages.add(pageId);
            setExpandedPages(newExpandedPages);

            if (!pageAds[pageId]) {
                try {
                    const ads = await getVerticalPageAds(selectedVertical.id, pageId);
                    setPageAds(prev => ({ ...prev, [pageId]: Array.isArray(ads) ? ads : [] }));
                } catch (error) {
                    console.error('Failed to load page ads', error);
                    showError(t('research.failedToLoadAdsFor'));
                    setPageAds(prev => ({ ...prev, [pageId]: [] }));
                }
            }
        }
    };

    const handleCreateVertical = async () => {
        if (!newVerticalName.trim()) {
            showError(t('research.enterVerticalName'));
            return;
        }

        try {
            await createVertical(newVerticalName, newVerticalDescription);
            showSuccess(t('research.createdVerticalNewverticalname', { newVerticalName }));
            setNewVerticalName('');
            setNewVerticalDescription('');
            setShowVerticalModal(false);
            fetchVerticals();
        } catch (error) {
            showError(t('research.failedToCreateVertical'));
        }
    };

    const fetchApiUsage = async () => {
        try {
            const data = await getApiUsage();
            setApiUsage(Array.isArray(data) ? data : []);
        } catch (error) {
            console.error('Failed to load API usage', error);
            setApiUsage([]);
        }
    };

    const fetchBlacklist = async () => {
        try {
            const data = await getBlacklist();
            setBlacklist(Array.isArray(data) ? data : []);
        } catch (error) {
            console.error('Failed to load blacklist', error);
            setBlacklist([]);
        }
    };

    const fetchKeywordBlacklist = async () => {
        try {
            const data = await getKeywordBlacklist();
            setKeywordBlacklist(Array.isArray(data) ? data : []);
        } catch (error) {
            console.error('Failed to load keyword blacklist', error);
            setKeywordBlacklist([]);
        }
    };

    const fetchSavedSearches = async () => {
        try {
            const data = await getSavedSearches();
            setSavedSearches(Array.isArray(data) ? data : []);
        } catch (error) {
            console.error('Failed to load searches', error);
            setSavedSearches([]);
        }
    };

    const handleScrape = async (e) => {
        e.preventDefault();
        if (!query.trim()) {
            showError(t('research.enterSearchTerm'));
            return;
        }

        setLoading(true);
        const apiCalls = LIMIT_OPTIONS.find(o => o.value === limit)?.apiCalls || 1;

        setProgressMessage(t('research.fetchingAds', { count: apiCalls }));
        showInfo(t('research.startingScrape'));

        try {
            const negativeList = negativeKeywords
                .split(',')
                .map(k => k.trim())
                .filter(k => k.length > 0);

            setProgressMessage(t('research.processingAds'));

            const result = await searchAndSave({
                query,
                platform: 'facebook',
                limit,
                country,
                offset: 0,
                exclude_ids: [],
                negative_keywords: negativeList,
                vertical_id: selectedVertical?.id || null,
                search_type: searchType,
                schedule_config: null
            });

            setProgressMessage(t('research.savingAds'));
            showSuccess(t('research.savedAdsCountAdsFrom', { ads_count: result.ads_count }));
            setQuery('');
            setNegativeKeywords('');
            fetchSavedSearches();
            fetchApiUsage();
            fetchRateLimit();
            // Refresh aggregated ads if in a vertical
            if (selectedVertical) {
                fetchAggregatedAds();
                setActiveTab('vertical-detail');
            }
        } catch (error) {
            console.error('Scrape failed', error);
            showError(translateBackendMessage(error.response?.data?.detail) || t('research.scrapeFailedTryAgain'));
        } finally {
            setLoading(false);
            setProgressMessage('');
        }
    };

    const handleDelete = async (searchId) => {
        try {
            await deleteSavedSearch(searchId);
            showSuccess(t('research.searchDeleted'));
            fetchSavedSearches();
            if (selectedSearch?.id === searchId) {
                setSelectedSearch(null);
            }
        } catch (error) {
            console.error('Delete failed', error);
            showError(t('research.failedToDelete'));
        }
    };

    const viewSearch = (search) => {
        // Filter out ads from blacklisted pages and keywords
        const blacklistedPageNames = blacklist.map(b => b.page_name.toLowerCase());
        const blacklistedKeywordsLower = keywordBlacklist.map(k => k.keyword.toLowerCase());

        const filteredAds = search.ads.filter(ad => {
            // Check page blacklist
            if (blacklistedPageNames.includes(ad.brand_name?.toLowerCase())) {
                return false;
            }

            // Check keyword blacklist
            const bodyText = ad.ad_copy?.toLowerCase() || '';
            const titleText = ad.headline?.toLowerCase() || '';
            const captionText = ad.cta_text?.toLowerCase() || '';
            const brandName = ad.brand_name?.toLowerCase() || '';

            for (const keyword of blacklistedKeywordsLower) {
                if (bodyText.includes(keyword) ||
                    titleText.includes(keyword) ||
                    captionText.includes(keyword) ||
                    brandName.includes(keyword)) {
                    return false;
                }
            }

            return true;
        });

        setSelectedSearch({
            ...search,
            ads: filteredAds
        });
        setActiveTab('ads');
    };

    const handleAddToBlacklist = async (pageName) => {
        if (!pageName) {
            setShowBlacklistModal(true);
            return;
        }

        try {
            await addToBlacklist(pageName);
            showSuccess(t('research.addedPagenameToBlacklist', { pageName }));
            fetchBlacklist();
            setBlacklistPageName('');
            setShowBlacklistModal(false);

            // Remove ads from this page from current view
            if (selectedSearch) {
                const filteredAds = selectedSearch.ads.filter(
                    ad => ad.brand_name?.toLowerCase() !== pageName.toLowerCase()
                );
                setSelectedSearch({
                    ...selectedSearch,
                    ads: filteredAds
                });
            }
        } catch (error) {
            const errorMsg = translateBackendMessage(error.response?.data?.detail) || t('research.failedToAddToBlacklist');
            showError(errorMsg);
        }
    };

    const handleRemoveFromBlacklist = async (id, pageName) => {
        try {
            await removeFromBlacklist(id);
            showSuccess(t('research.removedPagenameFromBlacklist', { pageName }));
            fetchBlacklist();
        } catch (error) {
            showError(t('research.failedToRemoveFromBlacklist'));
        }
    };

    const handleAddToKeywordBlacklist = async (keyword) => {
        if (!keyword) {
            setShowKeywordModal(true);
            return;
        }

        try {
            await addToKeywordBlacklist(keyword);
            showSuccess(t('research.addedKeywordToKeywordBlacklist', { keyword }));
            fetchKeywordBlacklist();
            setBlacklistKeyword('');
            setShowKeywordModal(false);

            // Remove ads containing this keyword from current view
            if (selectedSearch) {
                const keywordLower = keyword.toLowerCase();
                const filteredAds = selectedSearch.ads.filter(ad => {
                    const bodyText = ad.ad_copy?.toLowerCase() || '';
                    const titleText = ad.headline?.toLowerCase() || '';
                    const captionText = ad.cta_text?.toLowerCase() || '';
                    const brandName = ad.brand_name?.toLowerCase() || '';

                    return !bodyText.includes(keywordLower) &&
                           !titleText.includes(keywordLower) &&
                           !captionText.includes(keywordLower) &&
                           !brandName.includes(keywordLower);
                });
                setSelectedSearch({
                    ...selectedSearch,
                    ads: filteredAds
                });
            }
        } catch (error) {
            showError(t('research.failedToAddToKeyword'));
        }
    };

    const handleRemoveFromKeywordBlacklist = async (id, keyword) => {
        try {
            await removeFromKeywordBlacklist(id);
            showSuccess(t('research.removedKeywordFromKeywordBlacklist', { keyword }));
            fetchKeywordBlacklist();
        } catch (error) {
            showError(t('research.failedToRemoveFromKeyword'));
        }
    };

    return (
        <div className="container mx-auto px-4 py-8">
            <div className="mb-8">
                <h1 className="text-3xl font-bold text-gray-800">{t('research.adResearch')}</h1>
                <p className="text-gray-600 mt-2">
                    {t('research.scrapeFacebookAdsLibraryAnd')}
                </p>
            </div>


            {/* Verticals Tab */}
            {activeTab === 'verticals' && !selectedVertical && (
                <div className="space-y-6">
                    <div className="flex justify-between items-center">
                        <div>
                            <h2 className="text-2xl font-bold">{t('research.researchVerticals')}</h2>
                            <p className="text-gray-600 mt-1">{t('research.selectAVerticalToView')}</p>
                        </div>
                        <button
                            onClick={() => setShowVerticalModal(true)}
                            className="px-4 py-2 bg-indigo-600 text-white rounded-lg hover:bg-indigo-700 shadow-sm"
                        >
                            {t('research.newVertical')}
                        </button>
                    </div>

                    {verticals.length === 0 ? (
                        <div className="text-center py-16">
                            <div className="max-w-md mx-auto">
                                <h3 className="text-lg font-semibold text-gray-900 mb-2">{t('research.noVerticalsYet')}</h3>
                                <p className="text-gray-600 mb-4">
                                    {t('research.createVerticalsToOrganizeYour')}
                                </p>
                                <button
                                    onClick={() => setShowVerticalModal(true)}
                                    className="px-6 py-3 bg-indigo-600 text-white rounded-lg hover:bg-indigo-700"
                                >
                                    {t('research.createYourFirstVertical')}
                                </button>
                            </div>
                        </div>
                    ) : (
                        <div className="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-3 gap-6">
                            {verticals.map((vertical) => (
                                <button
                                    key={vertical.id}
                                    onClick={() => {
                                        setSelectedVertical(vertical);
                                        setActiveTab('vertical-detail');
                                    }}
                                    className="bg-white p-6 rounded-lg border-2 border-gray-200 shadow-sm hover:border-indigo-500 hover:shadow-md transition-all text-left"
                                >
                                    <h3 className="font-semibold text-xl text-gray-900 mb-2">{vertical.name}</h3>
                                    {vertical.description && (
                                        <p className="text-gray-600 text-sm mb-4">{vertical.description}</p>
                                    )}
                                    <div className="flex items-center justify-between text-sm">
                                        <span className="text-gray-500">
                                            {t('research.createdOn', { date: formatDate(vertical.created_at) })}
                                        </span>
                                        <span className="text-indigo-600 font-medium">{t('research.view')}</span>
                                    </div>
                                </button>
                            ))}
                        </div>
                    )}
                </div>
            )}

            {/* Vertical Detail View */}
            {activeTab === 'vertical-detail' && selectedVertical && (
                <div className="space-y-6">
                    <div className="flex items-center gap-4 mb-6">
                        <button
                            onClick={() => {
                                setSelectedVertical(null);
                                setActiveTab('verticals');
                            }}
                            className="text-gray-600 hover:text-gray-900"
                        >
                            {t('research.backToVerticals')}
                        </button>
                        <div>
                            <h2 className="text-2xl font-bold">{selectedVertical.name}</h2>
                            {selectedVertical.description && (
                                <p className="text-gray-600">{selectedVertical.description}</p>
                            )}
                        </div>
                    </div>

                    {/* Vertical Detail Tabs */}
                    <div className="border-b border-gray-200">
                        <div className="flex gap-4">
                            <button
                                onClick={() => setVerticalTab('aggregated')}
                                className={`px-4 py-2 font-medium border-b-2 transition-colors ${
                                    verticalTab === 'aggregated'
                                        ? 'border-indigo-600 text-indigo-600'
                                        : 'border-transparent text-gray-600 hover:text-gray-900'
                                }`}
                            >
                                {t('research.aggregatedAds')}
                            </button>
                            <button
                                onClick={() => setVerticalTab('search')}
                                className={`px-4 py-2 font-medium border-b-2 transition-colors ${
                                    verticalTab === 'search'
                                        ? 'border-indigo-600 text-indigo-600'
                                        : 'border-transparent text-gray-600 hover:text-gray-900'
                                }`}
                            >
                                {t('research.searchHistory')}
                            </button>
                        </div>
                    </div>

                    {/* Aggregated Ads Tab */}
                    {verticalTab === 'aggregated' && (
                        <div className="bg-white rounded-lg border border-gray-200 p-6">
                            <div className="flex justify-between items-center mb-4">
                                <div>
                                    <h3 className="text-lg font-semibold">{t('research.uniqueAdsByPage')}</h3>
                                    {aggregatedAds.length > 0 && (
                                        <p className="text-sm text-gray-600 mt-1">
                                            {t('research.totalSummary', { ads: aggregatedAds.reduce((sum, page) => sum + page.total_ads, 0), pages: aggregatedAds.length })}
                                        </p>
                                    )}
                                </div>
                                {aggregatedAds.length > 0 && (
                                    <input
                                        type="text"
                                        value={aggregatedFilter}
                                        onChange={(e) => setAggregatedFilter(e.target.value)}
                                        placeholder={t('research.filterByKeyword')}
                                        className="px-4 py-2 border border-gray-300 rounded-lg focus:ring-2 focus:ring-indigo-500 focus:border-indigo-500 outline-none w-64"
                                    />
                                )}
                            </div>
                            {aggregatedAds.length === 0 ? (
                                <p className="text-gray-500 text-center py-8">{t('research.noAdsYetRunA')}</p>
                            ) : (
                                <div className="space-y-2">
                                    {(() => {
                                        // Filter pages by keyword
                                        const filteredPages = aggregatedFilter.trim()
                                            ? aggregatedAds.filter(page =>
                                                page.page_name.toLowerCase().includes(aggregatedFilter.toLowerCase())
                                            )
                                            : aggregatedAds;

                                        return filteredPages.length === 0 ? (
                                            <p className="text-gray-500 text-center py-8">{t('research.noPagesMatchYourFilter')}</p>
                                        ) : filteredPages.map((page) => (
                                        <div key={page.page_id} className="border border-gray-200 rounded-lg overflow-hidden">
                                            <div className="px-4 py-3 bg-gray-50 flex items-center justify-between">
                                                <div className="flex-1 flex items-center gap-3">
                                                    <span
                                                        onClick={() => togglePageExpansion(page.page_id)}
                                                        className="text-gray-600 hover:text-gray-900 transition-transform cursor-pointer select-none"
                                                        style={{ transform: expandedPages.has(page.page_id) ? 'rotate(90deg)' : 'rotate(0deg)' }}
                                                    >
                                                        ▶
                                                    </span>
                                                    <div>
                                                        <h4 className="font-semibold text-gray-900">{page.page_name}</h4>
                                                        <div className="flex items-center gap-4 mt-1 text-sm">
                                                            <span className="text-gray-700 font-medium">
                                                                {t('research.uniqueAds', { count: page.total_ads })}
                                                            </span>
                                                            <div className="flex items-center gap-3 text-xs">
                                                                {page.image_count > 0 && (
                                                                    <span className="inline-flex items-center px-2 py-1 bg-blue-100 text-blue-700 rounded">
                                                                        📷 {t('research.images', { count: page.image_count })}
                                                                    </span>
                                                                )}
                                                                {page.video_count > 0 && (
                                                                    <span className="inline-flex items-center px-2 py-1 bg-purple-100 text-purple-700 rounded">
                                                                        🎥 {t('research.videos', { count: page.video_count })}
                                                                    </span>
                                                                )}
                                                                {page.carousel_count > 0 && (
                                                                    <span className="inline-flex items-center px-2 py-1 bg-green-100 text-green-700 rounded">
                                                                        🎠 {t('research.carousels', { count: page.carousel_count })}
                                                                    </span>
                                                                )}
                                                            </div>
                                                        </div>
                                                    </div>
                                                </div>
                                                <button
                                                    onClick={() => handleAddToBlacklist(page.page_name)}
                                                    className="px-3 py-1 text-sm bg-red-100 text-red-700 rounded hover:bg-red-200"
                                                >
                                                    {t('research.blockPage')}
                                                </button>
                                            </div>

                                            {/* Expanded ads */}
                                            {expandedPages.has(page.page_id) && (
                                                <div className="px-4 py-3 bg-white border-t border-gray-200">
                                                    {!pageAds[page.page_id] ? (
                                                        <div className="text-center py-4 text-gray-500">{t('research.loadingAds')}</div>
                                                    ) : pageAds[page.page_id].length === 0 ? (
                                                        <div className="text-center py-4 text-gray-500">{t('research.noAdsFound')}</div>
                                                    ) : (
                                                        <div className="space-y-3">
                                                            {(() => {
                                                                // Filter ads by keyword when expanded
                                                                const filteredAds = aggregatedFilter.trim()
                                                                    ? pageAds[page.page_id].filter(ad => {
                                                                        const searchText = aggregatedFilter.toLowerCase();
                                                                        return (
                                                                            (ad.headline?.toLowerCase().includes(searchText)) ||
                                                                            (ad.ad_copy?.toLowerCase().includes(searchText)) ||
                                                                            (ad.cta_text?.toLowerCase().includes(searchText)) ||
                                                                            (ad.brand_name?.toLowerCase().includes(searchText))
                                                                        );
                                                                    })
                                                                    : pageAds[page.page_id];

                                                                return filteredAds.length === 0 ? (
                                                                    <div className="text-center py-4 text-gray-500">{t('research.noAdsMatchYourFilter')}</div>
                                                                ) : filteredAds.map((ad) => (
                                                                <div key={ad.id} className="border border-gray-200 rounded p-3 hover:shadow-sm">
                                                                    <div className="flex justify-between items-start mb-2">
                                                                        <div className="flex-1">
                                                                            {ad.headline && (
                                                                                <h5 className="font-semibold text-gray-900">{ad.headline}</h5>
                                                                            )}
                                                                            {ad.ad_copy && (
                                                                                <p className="text-sm text-gray-700 mt-1">{ad.ad_copy}</p>
                                                                            )}
                                                                        </div>
                                                                        {ad.media_type && (
                                                                            <span className="ml-2 px-2 py-1 text-xs bg-gray-100 text-gray-600 rounded">
                                                                                {ad.media_type}
                                                                            </span>
                                                                        )}
                                                                    </div>
                                                                    <div className="flex items-center justify-between text-xs text-gray-500 mt-2">
                                                                        <div className="flex items-center gap-3">
                                                                            {ad.cta_text && (
                                                                                <span className="px-2 py-1 bg-blue-50 text-blue-600 rounded">
                                                                                    {t('research.ctaValue', { value: ad.cta_text })}
                                                                                </span>
                                                                            )}
                                                                            <span className={`px-2 py-1 rounded font-medium ${
                                                                                (ad.seen_count || 1) > 1
                                                                                    ? 'bg-green-100 text-green-700'
                                                                                    : 'bg-gray-100 text-gray-600'
                                                                            }`}>
                                                                                {t('research.seenTimes', { count: ad.seen_count || 1 })}
                                                                            </span>
                                                                        </div>
                                                                        <a
                                                                            href={ad.ad_link}
                                                                            target="_blank"
                                                                            rel="noopener noreferrer"
                                                                            className="text-indigo-600 hover:text-indigo-800"
                                                                        >
                                                                            {t('research.viewAd')}
                                                                        </a>
                                                                    </div>
                                                                </div>
                                                            ))
                                                            })()}
                                                        </div>
                                                    )}
                                                </div>
                                            )}
                                        </div>
                                    ))
                                })()}
                                </div>
                            )}
                        </div>
                    )}

                    {/* Search & History Tab */}
                    {verticalTab === 'search' && (
                        <div className="space-y-6">
                            {/* New Search Form */}
                    <div className="bg-white rounded-lg border border-gray-200 p-6">
                        <h3 className="text-lg font-semibold mb-4">{t('research.newSearch')}</h3>
                        <form onSubmit={handleScrape} className="space-y-4">
                            <div className="flex flex-col sm:flex-row gap-4">
                                <input
                                    type="text"
                                    value={query}
                                    onChange={(e) => setQuery(e.target.value)}
                                    placeholder={t('research.searchKeywordEGFitness')}
                                    className="flex-1 p-3 border border-gray-300 rounded-lg focus:ring-2 focus:ring-indigo-500 focus:border-indigo-500 outline-none"
                                />
                                <select
                                    value={country}
                                    onChange={(e) => setCountry(e.target.value)}
                                    className="p-3 border border-gray-300 rounded-lg focus:ring-2 focus:ring-indigo-500 focus:border-indigo-500 outline-none bg-white"
                                >
                                    {COUNTRIES.map((c) => (
                                        <option key={c.code} value={c.code}>
                                            {countryName(c.code, c.name)}
                                        </option>
                                    ))}
                                </select>
                            </div>
                            <div className="flex flex-col sm:flex-row gap-4">
                                <input
                                    type="text"
                                    value={negativeKeywords}
                                    onChange={(e) => setNegativeKeywords(e.target.value)}
                                    placeholder={t('research.negativeKeywordsCommaSeparated')}
                                    className="flex-1 p-3 border border-gray-300 rounded-lg focus:ring-2 focus:ring-indigo-500 focus:border-indigo-500 outline-none"
                                />
                                <select
                                    value={limit}
                                    onChange={(e) => setLimit(parseInt(e.target.value))}
                                    className="p-3 border border-gray-300 rounded-lg focus:ring-2 focus:ring-indigo-500 focus:border-indigo-500 outline-none bg-white"
                                >
                                    {LIMIT_OPTIONS.map((opt) => (
                                        <option key={opt.value} value={opt.value}>
                                            {t('research.limitAds', { n: formatNumber(opt.value) })}
                                        </option>
                                    ))}
                                </select>
                            </div>
                            <div>
                                <label className="block text-sm font-medium text-gray-700 mb-2">{t('research.searchType')}</label>
                                <select
                                    value={searchType}
                                    onChange={(e) => setSearchType(e.target.value)}
                                    className="w-full p-3 border border-gray-300 rounded-lg focus:ring-2 focus:ring-indigo-500 focus:border-indigo-500 outline-none bg-white"
                                >
                                    <option value="one_time">{t('research.oneTimeSearch')}</option>
                                    <option value="scheduled_daily">{t('research.scheduledDaily')}</option>
                                    <option value="scheduled_weekly">{t('research.scheduledWeekly')}</option>
                                </select>
                            </div>
                            <div className="bg-blue-50 border border-blue-200 rounded-lg p-3 text-sm space-y-2">
                                <div className="flex items-center justify-between">
                                    <span className="text-gray-700">
                                        {t('research.apiCalls')} <strong>{LIMIT_OPTIONS.find(o => o.value === limit)?.apiCalls || 1}</strong>
                                    </span>
                                    <span className="text-gray-500">
                                        {t('research.facebookAdsLibraryApiLimit')}
                                    </span>
                                </div>
                                {rateLimit && (
                                    <div className="flex items-center justify-between pt-2 border-t border-blue-200">
                                        <span className="text-gray-700">
                                            {t('research.rateLimit')} <strong className={rateLimit.remaining < 50 ? 'text-red-600' : 'text-green-600'}>
                                                {rateLimit.remaining}/{rateLimit.limit}
                                            </strong> {t('research.remaining')}
                                        </span>
                                        {rateLimit.reset_in_seconds > 0 && (
                                            <span className="text-gray-500">
                                                {t('research.resetsIn', { minutes: Math.ceil(rateLimit.reset_in_seconds / 60) })}
                                            </span>
                                        )}
                                    </div>
                                )}
                            </div>
                            {loading && progressMessage && (
                                <div className="bg-yellow-50 border border-yellow-200 rounded-lg p-3 text-sm">
                                    <div className="flex items-center gap-2">
                                        <svg className="animate-spin h-5 w-5 text-yellow-600" xmlns="http://www.w3.org/2000/svg" fill="none" viewBox="0 0 24 24">
                                            <circle className="opacity-25" cx="12" cy="12" r="10" stroke="currentColor" strokeWidth="4"></circle>
                                            <path className="opacity-75" fill="currentColor" d="M4 12a8 8 0 018-8V0C5.373 0 0 5.373 0 12h4zm2 5.291A7.962 7.962 0 014 12H0c0 3.042 1.135 5.824 3 7.938l3-2.647z"></path>
                                        </svg>
                                        <span className="text-gray-700">{progressMessage}</span>
                                    </div>
                                </div>
                            )}
                            <button
                                type="submit"
                                disabled={loading}
                                className="w-full px-6 py-3 bg-indigo-600 text-white font-semibold rounded-lg hover:bg-indigo-700 transition-colors disabled:opacity-50"
                            >
                                {loading ? t('research.scraping') : t('research.scrapeSave')}
                            </button>
                        </form>
                    </div>

                            {/* Saved Searches for this vertical */}
                            <div className="bg-white rounded-lg border border-gray-200 p-6">
                                <h3 className="text-lg font-semibold mb-4">{t('research.savedSearches')}</h3>
                                {savedSearches.filter(s => s.vertical_id === selectedVertical.id).length === 0 ? (
                                    <p className="text-gray-500 text-center py-8">{t('research.noSearchesYetForThis')}</p>
                                ) : (
                                    <div className="space-y-4">
                                        {savedSearches.filter(s => s.vertical_id === selectedVertical.id).map((search) => (
                                            <div
                                                key={search.id}
                                                className="border border-gray-200 rounded-lg p-4 hover:shadow-md transition-shadow"
                                            >
                                                <div className="flex justify-between items-start">
                                                    <div className="flex-1">
                                                        <div className="flex items-center gap-2">
                                                            <h3 className="text-lg font-semibold text-gray-800">
                                                                "{search.query}" in {search.country}
                                                            </h3>
                                                            {search.search_type !== 'one_time' && (
                                                                <span className={`px-2 py-1 text-xs rounded ${
                                                                    search.search_type === 'scheduled_daily' ? 'bg-blue-100 text-blue-700' :
                                                                    search.search_type === 'scheduled_weekly' ? 'bg-purple-100 text-purple-700' :
                                                                    'bg-gray-100 text-gray-700'
                                                                }`}>
                                                                    {search.search_type === 'scheduled_daily' ? t('research.daily') :
                                                                     search.search_type === 'scheduled_weekly' ? t('research.weekly') : search.search_type}
                                                                </span>
                                                            )}
                                                        </div>
                                                        <p className="text-sm text-gray-500 mt-1">
                                                            {t('research.adsCount', { n: search.ads?.length || 0 })} • {formatDateTime(search.created_at)}
                                                        </p>
                                                        {(search.ads_requested || search.ads_returned || search.ads_new || search.ads_duplicate) && (
                                                            <p className="text-xs text-gray-400 mt-1">
                                                                {t('research.searchStats', { requested: search.ads_requested || 0, returned: search.ads_returned || 0, added: search.ads_new || 0, duplicates: search.ads_duplicate || 0 })}
                                                            </p>
                                                        )}
                                                        {search.negative_keywords && search.negative_keywords.length > 0 && (
                                                            <p className="text-xs text-gray-400 mt-1">
                                                                {t('research.excluded', { keywords: search.negative_keywords.join(', ') })}
                                                            </p>
                                                        )}
                                                    </div>
                                                    <div className="flex gap-2">
                                                        <button
                                                            onClick={() => viewSearch(search)}
                                                            className="px-4 py-2 text-sm bg-indigo-600 text-white rounded hover:bg-indigo-700"
                                                        >
                                                            {t('research.viewAds')}
                                                        </button>
                                                        <button
                                                            onClick={() => handleDelete(search.id)}
                                                            className="px-4 py-2 text-sm bg-red-600 text-white rounded hover:bg-red-700"
                                                        >
                                                            {t('research.delete')}
                                                        </button>
                                                    </div>
                                                </div>
                                            </div>
                                        ))}
                                    </div>
                                )}
                            </div>
                        </div>
                    )}
                </div>
            )}

            {/* Ads View Tab */}
            {activeTab === 'ads' && selectedVertical && (
                <div className="space-y-4">
                    <div className="flex justify-between items-center">
                        <div>
                            <button
                                onClick={() => {
                                    setSelectedSearch(null);
                                    setActiveTab('vertical-detail');
                                }}
                                className="text-indigo-600 hover:text-indigo-800 mb-2 flex items-center gap-1"
                            >
                                {t('research.backToSearches')}
                            </button>
                            <h2 className="text-2xl font-bold text-gray-800">
                                {t('research.verticalAllAds', { name: selectedVertical.name })}
                            </h2>
                            <p className="text-gray-600 mt-1">
                                {t('research.groupedByPage')}
                            </p>
                        </div>
                    </div>

                    {(() => {
                        // Get all ads from all searches in this vertical
                        const allAds = savedSearches
                            .filter(s => s.vertical_id === selectedVertical.id)
                            .flatMap(search =>
                                (search.ads || []).map(ad => ({
                                    ...ad,
                                    searchQuery: search.query,
                                    searchId: search.id
                                }))
                            );

                        // Filter out blacklisted pages
                        const blacklistedPageNames = blacklist.map(b => b.page_name.toLowerCase());
                        const filteredAds = allAds.filter(ad =>
                            !blacklistedPageNames.includes(ad.brand_name?.toLowerCase())
                        );

                        // Group by page name
                        const adsByPage = filteredAds.reduce((acc, ad) => {
                            const pageName = ad.brand_name || 'Unknown Page'; // grouping/blacklist key (data) - localised only when shown
                            if (!acc[pageName]) {
                                acc[pageName] = [];
                            }
                            acc[pageName].push(ad);
                            return acc;
                        }, {});

                        // Get unique ads per page and collect search tags
                        const pageGroups = Object.entries(adsByPage).map(([pageName, ads]) => {
                            const uniqueAds = {};
                            ads.forEach(ad => {
                                if (!uniqueAds[ad.id]) {
                                    uniqueAds[ad.id] = { ...ad, searches: new Set([ad.searchQuery]) };
                                } else {
                                    uniqueAds[ad.id].searches.add(ad.searchQuery);
                                }
                            });
                            return {
                                pageName,
                                ads: Object.values(uniqueAds),
                                totalAds: Object.keys(uniqueAds).length
                            };
                        }).sort((a, b) => b.totalAds - a.totalAds);

                        const togglePage = (pageName) => {
                            const newExpanded = new Set(expandedPages);
                            if (newExpanded.has(pageName)) {
                                newExpanded.delete(pageName);
                            } else {
                                newExpanded.add(pageName);
                            }
                            setExpandedPages(newExpanded);
                        };

                        return pageGroups.length === 0 ? (
                            <div className="text-center py-16 bg-white rounded-lg border border-gray-200">
                                <p className="text-gray-500">{t('research.noAdsFoundInThis')}</p>
                            </div>
                        ) : (
                            <div className="space-y-2">
                                {pageGroups.map(({ pageName, ads, totalAds }) => (
                                    <div key={pageName} className="bg-white rounded-lg border border-gray-200 overflow-hidden">
                                        <button
                                            onClick={() => togglePage(pageName)}
                                            className="w-full px-4 py-3 flex items-center justify-between hover:bg-gray-50 transition-colors"
                                        >
                                            <div className="flex items-center gap-3">
                                                <span className="text-gray-400">
                                                    {expandedPages.has(pageName) ? '▼' : '▶'}
                                                </span>
                                                <div className="text-left">
                                                    <h3 className="font-semibold text-gray-900">{pageName === 'Unknown Page' ? t('research.unknownPage') : pageName}</h3>
                                                    <p className="text-sm text-gray-500">{t('research.uniqueAds', { count: totalAds })}</p>
                                                </div>
                                            </div>
                                            <button
                                                onClick={(e) => {
                                                    e.stopPropagation();
                                                    handleAddToBlacklist(pageName);
                                                }}
                                                className="px-3 py-1 text-sm bg-red-100 text-red-700 rounded hover:bg-red-200"
                                            >
                                                {t('research.blockPage')}
                                            </button>
                                        </button>

                                        {expandedPages.has(pageName) && (
                                            <div className="border-t border-gray-200">
                                                <table className="min-w-full">
                                                    <thead className="bg-gray-50">
                                                        <tr>
                                                            <th className="px-4 py-2 text-left text-xs font-medium text-gray-500 uppercase">{t('research.headline')}</th>
                                                            <th className="px-4 py-2 text-left text-xs font-medium text-gray-500 uppercase">{t('research.adCopy')}</th>
                                                            <th className="px-4 py-2 text-left text-xs font-medium text-gray-500 uppercase">CTA</th>
                                                            <th className="px-4 py-2 text-left text-xs font-medium text-gray-500 uppercase">{t('research.searches')}</th>
                                                            <th className="px-4 py-2 text-left text-xs font-medium text-gray-500 uppercase">{t('research.seen')}</th>
                                                            <th className="px-4 py-2 text-left text-xs font-medium text-gray-500 uppercase">{t('research.started')}</th>
                                                            <th className="px-4 py-2 text-left text-xs font-medium text-gray-500 uppercase">{t('research.actions')}</th>
                                                        </tr>
                                                    </thead>
                                                    <tbody className="divide-y divide-gray-200">
                                                        {ads.map((ad) => (
                                                            <tr key={ad.id} className="hover:bg-gray-50">
                                                                <td className="px-4 py-2 text-sm text-gray-700 max-w-xs">
                                                                    <div className="line-clamp-2">{ad.headline || '-'}</div>
                                                                </td>
                                                                <td className="px-4 py-2 text-sm text-gray-600 max-w-md">
                                                                    <div className="line-clamp-2">{ad.ad_copy || '-'}</div>
                                                                </td>
                                                                <td className="px-4 py-2 text-sm">
                                                                    {ad.cta_text && (
                                                                        <span className="inline-block px-2 py-1 bg-indigo-100 text-indigo-700 rounded text-xs">
                                                                            {ad.cta_text}
                                                                        </span>
                                                                    )}
                                                                </td>
                                                                <td className="px-4 py-2 text-sm">
                                                                    <div className="flex gap-1 flex-wrap">
                                                                        {Array.from(ad.searches).map((search, idx) => (
                                                                            <span key={idx} className="px-2 py-1 bg-blue-100 text-blue-700 rounded text-xs">
                                                                                {search}
                                                                            </span>
                                                                        ))}
                                                                    </div>
                                                                </td>
                                                                <td className="px-4 py-2 text-sm text-gray-700 text-center">
                                                                    <span className="inline-flex items-center px-2 py-1 bg-green-100 text-green-800 rounded text-xs font-semibold">
                                                                        {ad.seen_count || 1}x
                                                                    </span>
                                                                </td>
                                                                <td className="px-4 py-2 text-sm text-gray-500 whitespace-nowrap">
                                                                    {ad.start_date ? formatDate(ad.start_date) : '-'}
                                                                </td>
                                                                <td className="px-4 py-2 text-sm whitespace-nowrap">
                                                                    <a
                                                                        href={ad.ad_link}
                                                                        target="_blank"
                                                                        rel="noopener noreferrer"
                                                                        className="text-indigo-600 hover:text-indigo-900"
                                                                    >
                                                                        {t('research.view2')}
                                                                    </a>
                                                                </td>
                                                            </tr>
                                                        ))}
                                                    </tbody>
                                                </table>
                                            </div>
                                        )}
                                    </div>
                                ))}
                            </div>
                        );
                    })()}
                </div>
            )}

            {/* Vertical Modal */}
            {showVerticalModal && (
                <div className="fixed inset-0 bg-black bg-opacity-50 flex items-center justify-center z-50">
                    <div className="bg-white rounded-lg p-6 max-w-md w-full mx-4">
                        <h3 className="text-lg font-semibold mb-4">{t('research.addVertical')}</h3>
                        <div className="space-y-4">
                            <input
                                type="text"
                                value={newVerticalName}
                                onChange={(e) => setNewVerticalName(e.target.value)}
                                placeholder={t('research.verticalNameEGLegal')}
                                className="w-full p-3 border border-gray-300 rounded-lg focus:ring-2 focus:ring-indigo-500 outline-none"
                                autoFocus
                            />
                            <textarea
                                value={newVerticalDescription}
                                onChange={(e) => setNewVerticalDescription(e.target.value)}
                                placeholder={t('research.descriptionOptional')}
                                className="w-full p-3 border border-gray-300 rounded-lg focus:ring-2 focus:ring-indigo-500 outline-none"
                                rows="3"
                            />
                        </div>
                        <div className="flex gap-2 mt-6">
                            <button
                                onClick={() => {
                                    setShowVerticalModal(false);
                                    setNewVerticalName('');
                                    setNewVerticalDescription('');
                                }}
                                className="flex-1 px-4 py-2 border border-gray-300 text-gray-700 rounded hover:bg-gray-50"
                            >
                                {t('research.cancel')}
                            </button>
                            <button
                                onClick={handleCreateVertical}
                                className="flex-1 px-4 py-2 bg-indigo-600 text-white rounded hover:bg-indigo-700"
                            >
                                {t('research.create')}
                            </button>
                        </div>
                    </div>
                </div>
            )}
        </div>
    );
};

export default Research;
