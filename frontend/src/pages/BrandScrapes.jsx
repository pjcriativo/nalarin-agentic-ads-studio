import React, { useState, useEffect } from 'react';
import { formatDate } from '../i18n/format';
import { useTranslation } from 'react-i18next';
import { translateBackendMessage } from '../i18n/backendErrors';
import { useToast } from '../context/ToastContext';
import { createBrandScrape, getBrandScrapes, getBrandScrape, deleteBrandScrape } from '../api/research';
import { Search, Trash2, ChevronDown, ChevronRight, ExternalLink, Image, Video, Loader2, RefreshCw } from 'lucide-react';

const BrandScrapes = () => {
    const { t } = useTranslation('research');
    const { showSuccess, showError, showInfo } = useToast();
    const [brandName, setBrandName] = useState('');
    const [pageInput, setPageInput] = useState('');

    // Build full URL from page ID, search query, or extract from URL
    const buildPageUrl = (input) => {
        const trimmed = input.trim();
        // If it's just numbers, treat as page ID
        if (/^\d+$/.test(trimmed)) {
            return `https://www.facebook.com/ads/library/?active_status=active&ad_type=all&country=US&media_type=all&view_all_page_id=${trimmed}`;
        }
        // If it's a valid FB Ads Library URL (with view_all_page_id OR search query), use as-is
        if (trimmed.includes('facebook.com/ads/library') && (trimmed.includes('view_all_page_id=') || trimmed.includes('q='))) {
            return trimmed;
        }
        // Try to extract page ID from various FB URL formats
        const pageIdMatch = trimmed.match(/(?:page_id=|pages\/|facebook\.com\/)(\d+)/);
        if (pageIdMatch) {
            return `https://www.facebook.com/ads/library/?active_status=active&ad_type=all&country=US&media_type=all&view_all_page_id=${pageIdMatch[1]}`;
        }
        return null;
    };
    const [scrapes, setScrapes] = useState([]);
    const [loading, setLoading] = useState(false);
    const [expandedScrape, setExpandedScrape] = useState(null);
    const [scrapeDetails, setScrapeDetails] = useState(null);
    const [showDeleteModal, setShowDeleteModal] = useState(false);
    const [scrapeToDelete, setScrapeToDelete] = useState(null);

    useEffect(() => {
        fetchScrapes();
    }, []);

    const fetchScrapes = async () => {
        try {
            const data = await getBrandScrapes();
            setScrapes(Array.isArray(data) ? data : []);
        } catch (error) {
            showError(t('brandScrapes.failedToLoadBrandScrapes'));
            setScrapes([]);
        }
    };

    const handleSubmit = async (e) => {
        e.preventDefault();

        if (!brandName.trim()) {
            showError(t('brandScrapes.pleaseEnterABrandName'));
            return;
        }
        if (!pageInput.trim()) {
            showError(t('brandScrapes.pleaseEnterAFacebookPage'));
            return;
        }

        const pageUrl = buildPageUrl(pageInput);
        if (!pageUrl) {
            showError(t('brandScrapes.invalidInputEnterAPage'));
            return;
        }

        setLoading(true);
        try {
            await createBrandScrape(brandName, pageUrl);
            showSuccess(t('brandScrapes.brandScrapeStartedCheckBack'));
            setBrandName('');
            setPageInput('');
            fetchScrapes();
        } catch (error) {
            const message = translateBackendMessage(error.response?.data?.detail) || t('brandScrapes.failedToStartScrape');
            showError(message);
        } finally {
            setLoading(false);
        }
    };

    const handleExpand = async (scrapeId) => {
        if (expandedScrape === scrapeId) {
            setExpandedScrape(null);
            setScrapeDetails(null);
            return;
        }

        setExpandedScrape(scrapeId);
        try {
            const details = await getBrandScrape(scrapeId);
            // Ensure ads is always an array
            if (details && !Array.isArray(details.ads)) {
                details.ads = [];
            }
            setScrapeDetails(details);
        } catch (error) {
            showError(t('brandScrapes.failedToLoadScrapeDetails'));
            setScrapeDetails(null);
        }
    };

    const confirmDelete = (scrape) => {
        setScrapeToDelete(scrape);
        setShowDeleteModal(true);
    };

    const handleDelete = async () => {
        if (!scrapeToDelete) return;

        try {
            await deleteBrandScrape(scrapeToDelete.id);
            showSuccess(t('brandScrapes.brandScrapeDeleted'));
            setShowDeleteModal(false);
            setScrapeToDelete(null);
            if (expandedScrape === scrapeToDelete.id) {
                setExpandedScrape(null);
                setScrapeDetails(null);
            }
            fetchScrapes();
        } catch (error) {
            showError(t('brandScrapes.failedToDeleteBrandScrape'));
        }
    };

    const getStatusBadge = (status) => {
        const styles = {
            pending: 'bg-yellow-100 text-yellow-800',
            scraping: 'bg-blue-100 text-blue-800',
            completed: 'bg-green-100 text-green-800',
            failed: 'bg-red-100 text-red-800'
        };
        return (
            <span className={`px-2 py-1 text-xs font-medium rounded-full ${styles[status] || 'bg-gray-100 text-gray-800'}`}>
                {t(`brandScrapes.status_${status}`, { defaultValue: status })}
            </span>
        );
    };

    const formatScrapeDate = (dateStr) => {
        return formatDate(dateStr, {
            month: 'short',
            day: 'numeric',
            year: 'numeric',
            hour: '2-digit',
            minute: '2-digit'
        });
    };

    return (
        <div className="space-y-6">
            <div className="flex items-center justify-between">
                <div>
                    <h1 className="text-2xl font-bold text-amber-900">{t('brandScrapes.scrapeBrandAds')}</h1>
                    <p className="text-amber-600 text-sm">{t('brandScrapes.downloadAllAdsFromA')}</p>
                </div>
                <button
                    onClick={fetchScrapes}
                    className="flex items-center gap-2 px-3 py-2 text-sm text-amber-600 hover:text-amber-800 hover:bg-amber-50 rounded-lg"
                >
                    <RefreshCw size={16} />
                    {t('brandScrapes.refresh')}
                </button>
            </div>

            {/* Scrape Form */}
            <div className="bg-white rounded-xl border border-amber-200 p-6">
                <h2 className="text-lg font-semibold text-amber-900 mb-4">{t('brandScrapes.newBrandScrape')}</h2>
                <form onSubmit={handleSubmit} className="space-y-4">
                    <div>
                        <label htmlFor="brandName" className="block text-sm font-medium text-gray-700 mb-1">
                            {t('brandScrapes.brandName')}
                        </label>
                        <input
                            id="brandName"
                            name="brandName"
                            type="text"
                            value={brandName}
                            onChange={(e) => setBrandName(e.target.value)}
                            placeholder={t('brandScrapes.eGNikeAppleEtc')}
                            className="w-full px-4 py-2 border border-gray-300 rounded-lg focus:ring-2 focus:ring-amber-500 focus:border-amber-500"
                        />
                        <p className="text-xs text-gray-500 mt-1">{t('brandScrapes.thisWillBeTheFolder')}</p>
                    </div>
                    <div>
                        <label htmlFor="pageInput" className="block text-sm font-medium text-gray-700 mb-1">
                            {t('brandScrapes.facebookPageIdOrAds')}
                        </label>
                        <input
                            id="pageInput"
                            name="pageInput"
                            type="text"
                            value={pageInput}
                            onChange={(e) => setPageInput(e.target.value)}
                            placeholder={t('brandScrapes.t123456789OrHttpsWwwFacebook')}
                            className="w-full px-4 py-2 border border-gray-300 rounded-lg focus:ring-2 focus:ring-amber-500 focus:border-amber-500"
                        />
                        <p className="text-xs text-gray-500 mt-1">
                            {t('brandScrapes.pasteAPageIdOr')}
                        </p>
                    </div>
                    <button
                        type="submit"
                        disabled={loading}
                        className="flex items-center gap-2 px-6 py-2 bg-amber-600 text-white rounded-lg hover:bg-amber-700 disabled:bg-amber-300 disabled:cursor-not-allowed"
                    >
                        {loading ? (
                            <>
                                <Loader2 size={18} className="animate-spin" />
                                {t('brandScrapes.starting')}
                            </>
                        ) : (
                            <>
                                <Search size={18} />
                                {t('brandScrapes.startScrape')}
                            </>
                        )}
                    </button>
                </form>
            </div>

            {/* Scrapes List */}
            <div className="bg-white rounded-xl border border-amber-200">
                <div className="p-4 border-b border-amber-100">
                    <h2 className="text-lg font-semibold text-amber-900">{t('brandScrapes.brandScrapes')}</h2>
                </div>

                {scrapes.length === 0 ? (
                    <div className="p-8 text-center text-gray-500">
                        {t('brandScrapes.noBrandScrapesYetStart')}
                    </div>
                ) : (
                    <div className="divide-y divide-amber-100">
                        {scrapes.map((scrape) => (
                            <div key={scrape.id}>
                                <div
                                    className="p-4 hover:bg-amber-50 cursor-pointer flex items-center justify-between"
                                    onClick={() => handleExpand(scrape.id)}
                                >
                                    <div className="flex items-center gap-4">
                                        <button className="text-amber-600">
                                            {expandedScrape === scrape.id ? (
                                                <ChevronDown size={20} />
                                            ) : (
                                                <ChevronRight size={20} />
                                            )}
                                        </button>
                                        <div>
                                            <h3 className="font-medium text-gray-900">{scrape.brand_name}</h3>
                                            <p className="text-sm text-gray-500">
                                                {scrape.page_name || t('brandScrapes.pageId', { id: scrape.page_id })}
                                            </p>
                                        </div>
                                    </div>
                                    <div className="flex items-center gap-6">
                                        <div className="text-right">
                                            <p className="text-sm font-medium text-gray-900">
                                                {t('brandScrapes.adsCount', { count: scrape.total_ads })}
                                            </p>
                                            <p className="text-xs text-gray-500">
                                                {t('brandScrapes.mediaFiles', { count: scrape.media_downloaded })}
                                            </p>
                                        </div>
                                        {getStatusBadge(scrape.status)}
                                        <span className="text-xs text-gray-400">
                                            {formatScrapeDate(scrape.created_at)}
                                        </span>
                                        <button
                                            onClick={(e) => {
                                                e.stopPropagation();
                                                confirmDelete(scrape);
                                            }}
                                            className="p-2 text-red-500 hover:bg-red-50 rounded-lg"
                                        >
                                            <Trash2 size={16} />
                                        </button>
                                    </div>
                                </div>

                                {/* Expanded Details */}
                                {expandedScrape === scrape.id && scrapeDetails && (
                                    <div className="px-4 pb-4 bg-amber-50/50">
                                        {scrape.error_message && (
                                            <div className="mb-4 p-3 bg-red-50 border border-red-200 rounded-lg text-sm text-red-700">
                                                {scrape.error_message}
                                            </div>
                                        )}

                                        {scrapeDetails.ads && scrapeDetails.ads.length > 0 ? (
                                            <div className="grid grid-cols-2 md:grid-cols-3 lg:grid-cols-4 gap-4">
                                                {scrapeDetails.ads.map((ad) => (
                                                    <div
                                                        key={ad.id}
                                                        className="bg-white rounded-lg border border-amber-200 overflow-hidden"
                                                    >
                                                        {/* Media Preview */}
                                                        <div className="aspect-video bg-gray-100 relative">
                                                            {ad.media_urls && ad.media_urls.length > 0 ? (
                                                                ad.media_type === 'video' ? (
                                                                    <video
                                                                        src={ad.media_urls[0]}
                                                                        className="w-full h-full object-cover"
                                                                        controls
                                                                    />
                                                                ) : (
                                                                    <img
                                                                        src={ad.media_urls[0]}
                                                                        alt={ad.headline || 'Ad'}
                                                                        className="w-full h-full object-cover"
                                                                    />
                                                                )
                                                            ) : (
                                                                <div className="w-full h-full flex items-center justify-center text-gray-400">
                                                                    <Image size={32} />
                                                                </div>
                                                            )}
                                                            {ad.media_type && (
                                                                <span className="absolute top-2 right-2 px-2 py-0.5 bg-black/60 text-white text-xs rounded">
                                                                    {ad.media_type === 'video' ? (
                                                                        <Video size={12} className="inline mr-1" />
                                                                    ) : (
                                                                        <Image size={12} className="inline mr-1" />
                                                                    )}
                                                                    {ad.media_type}
                                                                </span>
                                                            )}
                                                            {ad.media_urls && ad.media_urls.length > 1 && (
                                                                <span className="absolute bottom-2 right-2 px-2 py-0.5 bg-black/60 text-white text-xs rounded">
                                                                    {t('brandScrapes.more', { count: ad.media_urls.length - 1 })}
                                                                </span>
                                                            )}
                                                        </div>

                                                        {/* Ad Info */}
                                                        <div className="p-3">
                                                            {ad.page_name && (
                                                                <div className="flex items-center gap-1 mb-1">
                                                                    <span className="text-xs font-medium text-indigo-600 truncate">
                                                                        {ad.page_name}
                                                                    </span>
                                                                    {ad.page_link && (
                                                                        <a
                                                                            href={ad.page_link}
                                                                            target="_blank"
                                                                            rel="noopener noreferrer"
                                                                            className="text-indigo-400 hover:text-indigo-600 flex-shrink-0"
                                                                            onClick={(e) => e.stopPropagation()}
                                                                            title={t('brandScrapes.viewAllAdsFromThis')}
                                                                        >
                                                                            <ExternalLink size={10} />
                                                                        </a>
                                                                    )}
                                                                </div>
                                                            )}
                                                            {ad.headline && (
                                                                <p className="text-sm font-medium text-gray-900 line-clamp-2 mb-1">
                                                                    {ad.headline}
                                                                </p>
                                                            )}
                                                            {ad.ad_copy && (
                                                                <p className="text-xs text-gray-500 line-clamp-2 mb-1">
                                                                    {ad.ad_copy}
                                                                </p>
                                                            )}
                                                            {ad.cta_text && (
                                                                <span className="inline-block px-2 py-0.5 text-xs bg-amber-100 text-amber-700 rounded mb-1">
                                                                    {ad.cta_text}
                                                                </span>
                                                            )}
                                                            <div className="mt-2 flex items-center justify-between">
                                                                <span className="text-xs text-gray-400">
                                                                    {ad.start_date || t('brandScrapes.unknownDate')}
                                                                </span>
                                                                {ad.ad_link && (
                                                                    <a
                                                                        href={ad.ad_link}
                                                                        target="_blank"
                                                                        rel="noopener noreferrer"
                                                                        className="text-amber-600 hover:text-amber-800"
                                                                        onClick={(e) => e.stopPropagation()}
                                                                        title={t('brandScrapes.viewAdInLibrary')}
                                                                    >
                                                                        <ExternalLink size={14} />
                                                                    </a>
                                                                )}
                                                            </div>
                                                        </div>
                                                    </div>
                                                ))}
                                            </div>
                                        ) : (
                                            <div className="text-center py-8 text-gray-500">
                                                {scrape.status === 'scraping' ? (
                                                    <div className="flex items-center justify-center gap-2">
                                                        <Loader2 size={20} className="animate-spin" />
                                                        {t('brandScrapes.scrapingInProgress')}
                                                    </div>
                                                ) : (
                                                    t('brandScrapes.noAdsFound')
                                                )}
                                            </div>
                                        )}
                                    </div>
                                )}
                            </div>
                        ))}
                    </div>
                )}
            </div>

            {/* Delete Confirmation Modal */}
            {showDeleteModal && scrapeToDelete && (
                <div className="fixed inset-0 bg-black/50 flex items-center justify-center z-50">
                    <div className="bg-white rounded-xl p-6 max-w-md w-full mx-4">
                        <h3 className="text-lg font-semibold text-gray-900 mb-2">
                            {t('brandScrapes.deleteBrandScrape')}
                        </h3>
                        <p className="text-gray-600 mb-4">
                            {t('brandScrapes.deleteWarning', { ads: scrapeToDelete.total_ads, files: scrapeToDelete.media_downloaded })}
                        </p>
                        <div className="flex gap-3 justify-end">
                            <button
                                onClick={() => {
                                    setShowDeleteModal(false);
                                    setScrapeToDelete(null);
                                }}
                                className="px-4 py-2 text-gray-600 hover:bg-gray-100 rounded-lg"
                            >
                                {t('brandScrapes.cancel')}
                            </button>
                            <button
                                onClick={handleDelete}
                                className="px-4 py-2 bg-red-600 text-white rounded-lg hover:bg-red-700"
                            >
                                {t('brandScrapes.delete')}
                            </button>
                        </div>
                    </div>
                </div>
            )}
        </div>
    );
};

export default BrandScrapes;
