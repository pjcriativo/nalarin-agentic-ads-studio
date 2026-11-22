import { useToast } from '../context/ToastContext';
import { useTranslation } from 'react-i18next';
import React, { useState, useEffect } from 'react';
import { ChevronRight, Upload, X, Loader, Trash2, Film, Image } from 'lucide-react';
import { useCampaign } from '../context/CampaignContext';
import { getPages } from '../lib/facebookApi';

const ALLOWED_IMAGE_TYPES = ['image/jpeg', 'image/jpg', 'image/png', 'image/gif', 'image/webp'];
const ALLOWED_VIDEO_TYPES = ['video/mp4', 'video/quicktime', 'video/x-msvideo', 'video/webm'];

// Facebook CTA types - confirmed working
const CTA_OPTIONS = [
    'LEARN_MORE',
    'SHOP_NOW',
    'SIGN_UP',
    'CONTACT_US',
    'DOWNLOAD',
    'BOOK_NOW',
    'BUY_TICKETS',
    'GET_QUOTE',
    'DONATE_NOW',
];

const AdCreativeStep = ({ onNext, onBack }) => {
    const { t } = useTranslation('campaigns');
    const { showWarning, showError } = useToast();
    const { creativeData, setCreativeData, selectedAdAccount, adsetData } = useCampaign();
    const [pages, setPages] = useState([]);
    const [loadingPages, setLoadingPages] = useState(false);

    const [manualPageEntry, setManualPageEntry] = useState(false);
    const [isDragging, setIsDragging] = useState(false);

    const handleDragEnter = (e) => {
        e.preventDefault();
        e.stopPropagation();
        setIsDragging(true);
    };

    const handleDragOver = (e) => {
        e.preventDefault();
        e.stopPropagation();
        setIsDragging(true);
    };

    const handleDragLeave = (e) => {
        e.preventDefault();
        e.stopPropagation();
        // Only set dragging to false if leaving the drop zone entirely
        if (e.currentTarget.contains(e.relatedTarget)) return;
        setIsDragging(false);
    };

    const handleDrop = (e) => {
        e.preventDefault();
        e.stopPropagation();
        setIsDragging(false);

        const files = Array.from(e.dataTransfer.files);
        if (files.length === 0) return;

        // Filter for images and videos
        const mediaFiles = files.filter(file =>
            ALLOWED_IMAGE_TYPES.includes(file.type) || ALLOWED_VIDEO_TYPES.includes(file.type)
        );

        if (mediaFiles.length === 0) {
            showWarning(t('adCreativeStep.pleaseDropImageOrVideo'));
            return;
        }

        const newCreatives = mediaFiles.map(file => {
            const isVideo = ALLOWED_VIDEO_TYPES.includes(file.type);
            return {
                id: `creative_${Date.now()}_${Math.random().toString(36).substr(2, 9)}`,
                file,
                previewUrl: URL.createObjectURL(file),
                name: file.name,
                mediaType: isVideo ? 'video' : 'image'
            };
        });

        setCreativeData(prev => ({
            ...prev,
            creatives: [...(prev.creatives || []), ...newCreatives]
        }));
    };

    // Prepopulate Creative Name with Ad Set Name if empty
    useEffect(() => {
        if (adsetData?.name && !creativeData.creativeName) {
            handleInputChange('creativeName', adsetData.name);
        }
    }, [adsetData?.name]);

    // Load last used page ID on mount
    useEffect(() => {
        const lastUsedPageId = localStorage.getItem('lastUsedPageId');
        if (lastUsedPageId && !creativeData.pageId) {
            handleInputChange('pageId', lastUsedPageId);
        }
    }, []);

    // Load default URL from local storage for this ad account
    useEffect(() => {
        if (selectedAdAccount && !creativeData.websiteUrl) {
            const savedUrl = localStorage.getItem(`defaultUrl_${selectedAdAccount.id}`);
            if (savedUrl) {
                handleInputChange('websiteUrl', savedUrl);
            }
        }
    }, [selectedAdAccount]);

    // Fetch pages when ad account is selected
    useEffect(() => {
        if (selectedAdAccount) {
            fetchPages();
        }
    }, [selectedAdAccount]);

    const fetchPages = async () => {
        setLoadingPages(true);
        try {
            const fetchedPages = await getPages(selectedAdAccount.id);
            setPages(fetchedPages);

            // If no page is selected and we have pages, select the first one (or the last used one if it exists in the list)
            if (fetchedPages.length > 0 && !creativeData.pageId) {
                const lastUsedPageId = localStorage.getItem('lastUsedPageId');
                const pageToSelect = fetchedPages.find(p => p.id === lastUsedPageId) || fetchedPages[0];
                handlePageSelection(pageToSelect.id, fetchedPages);
            } else if (fetchedPages.length === 0) {
                // If no pages found, default to manual entry so user isn't blocked
                setManualPageEntry(true);
            }
        } catch (error) {
            console.error('Error fetching pages:', error);
            showError(t('adCreativeStep.failedToLoadFacebookPages'));
            setManualPageEntry(true); // Auto-switch to manual entry
        } finally {
            setLoadingPages(false);
        }
    };

    const handlePageSelection = (pageId, currentPages = pages) => {
        const selectedPage = currentPages.find(p => p.id === pageId);
        setCreativeData(prev => ({
            ...prev,
            pageId,
            instagramId: selectedPage ? selectedPage.instagramId : null
        }));
        localStorage.setItem('lastUsedPageId', pageId);
    };

    // Load saved creative fields from local storage for this ad account
    useEffect(() => {
        if (selectedAdAccount) {
            const savedHeadlines = localStorage.getItem(`defaultHeadlines_${selectedAdAccount.id}`);
            const savedBodies = localStorage.getItem(`defaultBodies_${selectedAdAccount.id}`);
            const savedDescription = localStorage.getItem(`defaultDescription_${selectedAdAccount.id}`);
            const savedCta = localStorage.getItem(`defaultCta_${selectedAdAccount.id}`);

            if (savedHeadlines && !creativeData.headlines[0]) {
                try {
                    const parsedHeadlines = JSON.parse(savedHeadlines);
                    if (Array.isArray(parsedHeadlines) && parsedHeadlines.length > 0) {
                        setCreativeData(prev => ({ ...prev, headlines: parsedHeadlines }));
                    }
                } catch (e) { console.error('Error parsing saved headlines', e); }
            }

            if (savedBodies && !creativeData.bodies[0]) {
                try {
                    const parsedBodies = JSON.parse(savedBodies);
                    if (Array.isArray(parsedBodies) && parsedBodies.length > 0) {
                        setCreativeData(prev => ({ ...prev, bodies: parsedBodies }));
                    }
                } catch (e) { console.error('Error parsing saved bodies', e); }
            }

            if (savedDescription && !creativeData.description) {
                setCreativeData(prev => ({ ...prev, description: savedDescription }));
            }

            if (savedCta && !creativeData.cta) {
                setCreativeData(prev => ({ ...prev, cta: savedCta }));
            }
        }
    }, [selectedAdAccount]);

    const handleInputChange = (field, value) => {
        setCreativeData(prev => ({
            ...prev,
            [field]: value,
            // When manually entering a Page ID, clear the instagramId to prevent using Page ID as IG ID
            ...(field === 'pageId' ? { instagramId: null } : {})
        }));

        // Persist page ID
        if (field === 'pageId') {
            localStorage.setItem('lastUsedPageId', value);
        }

        // Persist description
        if (field === 'description' && selectedAdAccount) {
            localStorage.setItem(`defaultDescription_${selectedAdAccount.id}`, value);
        }

        // Persist CTA
        if (field === 'cta' && selectedAdAccount) {
            localStorage.setItem(`defaultCta_${selectedAdAccount.id}`, value);
        }
    };

    const handleBodyChange = (index, value) => {
        const newBodies = [...creativeData.bodies];
        newBodies[index] = value;
        setCreativeData(prev => ({
            ...prev,
            bodies: newBodies
        }));

        if (selectedAdAccount) {
            localStorage.setItem(`defaultBodies_${selectedAdAccount.id}`, JSON.stringify(newBodies));
        }
    };

    const handleHeadlineChange = (index, value) => {
        const newHeadlines = [...creativeData.headlines];
        newHeadlines[index] = value;
        setCreativeData(prev => ({
            ...prev,
            headlines: newHeadlines
        }));

        if (selectedAdAccount) {
            localStorage.setItem(`defaultHeadlines_${selectedAdAccount.id}`, JSON.stringify(newHeadlines));
        }
    };

    const addBodyField = () => {
        if (creativeData.bodies.length < 3) {
            setCreativeData(prev => ({
                ...prev,
                bodies: [...prev.bodies, '']
            }));
        }
    };

    const addHeadlineField = () => {
        if (creativeData.headlines.length < 3) {
            setCreativeData(prev => ({
                ...prev,
                headlines: [...prev.headlines, '']
            }));
        }
    };

    const removeBodyField = (index) => {
        if (creativeData.bodies.length > 1) {
            const newBodies = creativeData.bodies.filter((_, i) => i !== index);
            setCreativeData(prev => ({
                ...prev,
                bodies: newBodies
            }));
        }
    };

    const removeHeadlineField = (index) => {
        if (creativeData.headlines.length > 1) {
            const newHeadlines = creativeData.headlines.filter((_, i) => i !== index);
            setCreativeData(prev => ({
                ...prev,
                headlines: newHeadlines
            }));
        }
    };

    const handleMediaUpload = (e) => {
        const files = Array.from(e.target.files);
        if (files.length === 0) return;

        const newCreatives = files.map(file => {
            const isVideo = ALLOWED_VIDEO_TYPES.includes(file.type);
            return {
                id: `creative_${Date.now()}_${Math.random().toString(36).substr(2, 9)}`,
                file,
                previewUrl: URL.createObjectURL(file),
                name: file.name,
                mediaType: isVideo ? 'video' : 'image'
            };
        });

        setCreativeData(prev => ({
            ...prev,
            creatives: [...(prev.creatives || []), ...newCreatives]
        }));
    };

    const removeCreative = (id) => {
        setCreativeData(prev => ({
            ...prev,
            creatives: prev.creatives.filter(c => c.id !== id)
        }));
    };

    const handleNext = () => {
        // Validate required fields
        if (!creativeData.creativeName) {
            showWarning(t('adCreativeStep.pleaseEnterACreativeName'));
            return;
        }
        if (!creativeData.creatives || creativeData.creatives.length === 0) {
            showWarning(t('adCreativeStep.pleaseUploadAtLeastOne'));
            return;
        }

        // Validate primary text
        if (!creativeData.bodies[0] || !creativeData.bodies[0].trim()) {
            showWarning(t('adCreativeStep.pleaseProvidePrimaryText'));
            return;
        }

        // Validate headline
        if (!creativeData.headlines[0] || !creativeData.headlines[0].trim()) {
            showWarning(t('adCreativeStep.pleaseProvideAHeadline'));
            return;
        }

        if (!creativeData.websiteUrl) {
            showWarning(t('adCreativeStep.pleaseEnterAWebsiteUrl'));
            return;
        }

        // Validate URL format
        try {
            const url = new URL(creativeData.websiteUrl);
            if (!url.protocol.startsWith('http')) {
                showWarning(t('adCreativeStep.pleaseEnterAValidUrl'));
                return;
            }
        } catch {
            showWarning(t('adCreativeStep.pleaseEnterAValidUrl2'));
            return;
        }

        if (!creativeData.pageId) {
            showWarning(t('adCreativeStep.pleaseEnterAFacebookPage'));
            return;
        }

        // Save URL to local storage for this ad account
        if (selectedAdAccount && creativeData.websiteUrl) {
            localStorage.setItem(`defaultUrl_${selectedAdAccount.id}`, creativeData.websiteUrl);
        }

        onNext();
    };

    return (
        <div>
            <h2 className="text-2xl font-bold mb-6">{t('adCreativeStep.adCreativeStandardAds')}</h2>
            <p className="text-gray-600 mb-6">
                {t('adCreativeStep.createStandardAdsWithA')}
            </p>

            <div className="space-y-6">
                {/* Creative Name */}
                <div>
                    <label className="block text-sm font-medium text-gray-700 mb-2">
                        {t('adCreativeStep.creativeName')}
                    </label>
                    <input
                        type="text"
                        value={creativeData.creativeName}
                        onChange={(e) => handleInputChange('creativeName', e.target.value)}
                        placeholder={t('adCreativeStep.summerSaleDynamicCreative')}
                        className="w-full px-4 py-2 border border-gray-300 rounded-lg focus:ring-2 focus:ring-amber-500 focus:border-transparent"
                    />
                </div>

                {/* Facebook Page Selection */}
                <div>
                    <div className="flex justify-between items-center mb-2">
                        <label className="block text-sm font-medium text-gray-700">
                            {t('adCreativeStep.facebookPage')}
                        </label>
                        <button
                            onClick={() => setManualPageEntry(!manualPageEntry)}
                            className="text-xs text-amber-600 hover:text-amber-800 underline"
                        >
                            {manualPageEntry ? t('adCreativeStep.selectFromList') : t('adCreativeStep.enterPageIdManually')}
                        </button>
                    </div>

                    {manualPageEntry ? (
                        <input
                            type="text"
                            value={creativeData.pageId}
                            onChange={(e) => handleInputChange('pageId', e.target.value)}
                            placeholder={t('adCreativeStep.enterFacebookPageIdE')}
                            className="w-full px-4 py-2 border border-gray-300 rounded-lg focus:ring-2 focus:ring-amber-500 focus:border-transparent"
                        />
                    ) : loadingPages ? (
                        <div className="flex items-center gap-2 text-gray-500 py-2">
                            <Loader className="animate-spin" size={20} />
                            <span>{t('adCreativeStep.loadingPages')}</span>
                        </div>
                    ) : (
                        <select
                            value={creativeData.pageId}
                            onChange={(e) => handlePageSelection(e.target.value)}
                            className="w-full px-4 py-2 border border-gray-300 rounded-lg focus:ring-2 focus:ring-amber-500 focus:border-transparent"
                        >
                            <option value="">{t('adCreativeStep.selectAFacebookPage')}</option>
                            {pages.map(page => (
                                <option key={page.id} value={page.id}>
                                    {page.name}
                                </option>
                            ))}
                        </select>
                    )}

                    {!manualPageEntry && pages.length === 0 && !loadingPages && (
                        <div className="mt-2">
                            <p className="text-xs text-red-500 mb-1">
                                {t('adCreativeStep.noPagesFoundPleaseMake')}
                            </p>
                            <button
                                onClick={() => setManualPageEntry(true)}
                                className="text-xs text-amber-600 font-medium hover:underline"
                            >
                                {t('adCreativeStep.enterPageIdManuallyInstead')}
                            </button>
                        </div>
                    )}
                </div>

                {/* Media Upload (Images + Videos) */}
                <div>
                    <label className="block text-sm font-medium text-gray-700 mb-2">
                        {t('adCreativeStep.adMediaImagesOrVideos')}
                    </label>

                    {/* Upload Area */}
                    <div
                        className={`border-2 border-dashed rounded-lg p-8 text-center transition-colors mb-4 ${isDragging ? 'border-amber-500 bg-amber-50' : 'border-gray-300 hover:border-amber-500'
                            }`}
                        onDragEnter={handleDragEnter}
                        onDragOver={handleDragOver}
                        onDragLeave={handleDragLeave}
                        onDrop={handleDrop}
                    >
                        <input
                            type="file"
                            accept="image/*,video/*"
                            multiple
                            onChange={handleMediaUpload}
                            className="hidden"
                            id="ad-media-upload"
                        />
                        <label htmlFor="ad-media-upload" className="cursor-pointer flex flex-col items-center">
                            <div className="flex gap-2 mb-2">
                                <Image className={`${isDragging ? 'text-amber-500' : 'text-gray-400'}`} size={28} />
                                <Film className={`${isDragging ? 'text-amber-500' : 'text-gray-400'}`} size={28} />
                            </div>
                            <span className={`font-medium ${isDragging ? 'text-amber-700' : 'text-gray-600'}`}>
                                {isDragging ? t('adCreativeStep.dropFilesHere') : t('adCreativeStep.clickToUploadImagesOr')}
                            </span>
                            <span className="text-sm text-gray-400 mt-1">{t('adCreativeStep.orDragAndDrop')}</span>
                            <span className="text-xs text-amber-500 mt-2 bg-amber-50 px-2 py-1 rounded">{t('adCreativeStep.supportsMultipleFilesVideosUp')}</span>
                        </label>
                    </div>

                    {/* Media Grid */}
                    {creativeData.creatives && creativeData.creatives.length > 0 && (
                        <div className="grid grid-cols-2 md:grid-cols-3 gap-4 mb-4">
                            {creativeData.creatives.map((creative) => (
                                <div key={creative.id} className="relative group border rounded-lg overflow-hidden aspect-square bg-gray-100">
                                    {creative.mediaType === 'video' ? (
                                        <video
                                            src={creative.previewUrl}
                                            className="w-full h-full object-cover"
                                            muted
                                            playsInline
                                            onMouseEnter={(e) => e.target.play()}
                                            onMouseLeave={(e) => { e.target.pause(); e.target.currentTime = 0; }}
                                        />
                                    ) : (
                                        <img
                                            src={creative.previewUrl}
                                            alt={creative.name}
                                            className="w-full h-full object-cover"
                                        />
                                    )}
                                    {/* Media type badge */}
                                    <div className="absolute top-2 left-2">
                                        {creative.mediaType === 'video' ? (
                                            <span className="bg-purple-600 text-white text-xs px-2 py-1 rounded flex items-center gap-1">
                                                <Film size={12} /> {t('adCreativeStep.video')}
                                            </span>
                                        ) : (
                                            <span className="bg-blue-600 text-white text-xs px-2 py-1 rounded flex items-center gap-1">
                                                <Image size={12} /> {t('adCreativeStep.image')}
                                            </span>
                                        )}
                                    </div>
                                    <div className="absolute inset-0 bg-black bg-opacity-0 group-hover:bg-opacity-30 transition-all flex items-center justify-center opacity-0 group-hover:opacity-100">
                                        <button
                                            onClick={() => removeCreative(creative.id)}
                                            className="p-2 bg-red-500 text-white rounded-full hover:bg-red-600 transform scale-90 hover:scale-100 transition-all"
                                            title={t('adCreativeStep.removeMedia')}
                                        >
                                            <Trash2 size={16} />
                                        </button>
                                    </div>
                                    <div className="absolute bottom-0 left-0 right-0 bg-black bg-opacity-50 text-white text-xs p-1 truncate">
                                        {creative.name}
                                    </div>
                                </div>
                            ))}
                        </div>
                    )}

                    {/* URL Input (Optional fallback) */}
                    <div className="mt-2">
                        <p className="text-sm text-gray-500 mb-1">{t('adCreativeStep.orPasteAMediaUrl')}</p>
                        <input
                            type="text"
                            placeholder={t('adCreativeStep.httpsExampleComImageJpg')}
                            className="w-full px-4 py-2 border border-gray-300 rounded-lg focus:ring-2 focus:ring-amber-500 focus:border-transparent text-sm"
                            onBlur={(e) => {
                                if (e.target.value) {
                                    const url = e.target.value.toLowerCase();
                                    const isVideo = url.endsWith('.mp4') || url.endsWith('.mov') || url.endsWith('.webm') || url.endsWith('.avi');
                                    const newCreative = {
                                        id: `creative_url_${Date.now()}`,
                                        previewUrl: e.target.value,
                                        imageUrl: isVideo ? undefined : e.target.value,
                                        videoUrl: isVideo ? e.target.value : undefined,
                                        name: isVideo ? 'Video from URL' : 'Image from URL', // stored name (also used when naming ads on Facebook)
                                        mediaType: isVideo ? 'video' : 'image'
                                    };
                                    setCreativeData(prev => ({
                                        ...prev,
                                        creatives: [...(prev.creatives || []), newCreative]
                                    }));
                                    e.target.value = ''; // Clear input
                                }
                            }}
                        />
                    </div>
                </div>

                {/* Body Text */}
                <div>
                    <div className="flex items-center justify-between mb-2">
                        <label className="block text-sm font-medium text-gray-700">
                            {t('adCreativeStep.primaryText')}
                        </label>
                        {creativeData.bodies.length < 3 && (
                            <button
                                type="button"
                                onClick={addBodyField}
                                className="text-sm text-amber-600 hover:text-amber-700 font-medium flex items-center gap-1"
                            >
                                <svg className="w-4 h-4" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                                    <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M12 4v16m8-8H4" />
                                </svg>
                                {t('adCreativeStep.addBodyCopy')}
                            </button>
                        )}
                    </div>
                    <div className="space-y-3">
                        {creativeData.bodies.map((body, index) => (
                            <div key={index} className="relative">
                                <textarea
                                    value={body}
                                    onChange={(e) => handleBodyChange(index, e.target.value)}
                                    placeholder={t('adCreativeStep.bodyCopyN', { index: index + 1 })}
                                    rows="3"
                                    className="w-full px-4 py-2 border border-gray-300 rounded-lg focus:ring-2 focus:ring-amber-500 focus:border-transparent"
                                />
                                {index >= 1 && (
                                    <button
                                        type="button"
                                        onClick={() => removeBodyField(index)}
                                        className="absolute top-2 right-2 text-red-500 hover:text-red-700"
                                        title={t('adCreativeStep.removeThisBodyCopy')}
                                    >
                                        <svg className="w-5 h-5" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                                            <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M6 18L18 6M6 6l12 12" />
                                        </svg>
                                    </button>
                                )}
                            </div>
                        ))}
                    </div>
                </div>

                {/* Headline */}
                <div>
                    <div className="flex items-center justify-between mb-2">
                        <label className="block text-sm font-medium text-gray-700">
                            {t('adCreativeStep.headline')}
                        </label>
                        {creativeData.headlines.length < 3 && (
                            <button
                                type="button"
                                onClick={addHeadlineField}
                                className="text-sm text-amber-600 hover:text-amber-700 font-medium flex items-center gap-1"
                            >
                                <svg className="w-4 h-4" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                                    <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M12 4v16m8-8H4" />
                                </svg>
                                {t('adCreativeStep.addHeadline')}
                            </button>
                        )}
                    </div>
                    <div className="space-y-3">
                        {creativeData.headlines.map((headline, index) => (
                            <div key={index} className="relative">
                                <input
                                    type="text"
                                    value={headline}
                                    onChange={(e) => handleHeadlineChange(index, e.target.value)}
                                    placeholder={t('adCreativeStep.headlineN', { index: index + 1 })}
                                    className="w-full px-4 py-2 border border-gray-300 rounded-lg focus:ring-2 focus:ring-amber-500 focus:border-transparent"
                                />
                                {index >= 1 && (
                                    <button
                                        type="button"
                                        onClick={() => removeHeadlineField(index)}
                                        className="absolute top-2 right-2 text-red-500 hover:text-red-700"
                                        title={t('adCreativeStep.removeThisHeadline')}
                                    >
                                        <svg className="w-5 h-5" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                                            <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M6 18L18 6M6 6l12 12" />
                                        </svg>
                                    </button>
                                )}
                            </div>
                        ))}
                    </div>
                </div>

                {/* Description */}
                <div>
                    <label className="block text-sm font-medium text-gray-700 mb-2">
                        {t('adCreativeStep.description')}
                    </label>
                    <input
                        type="text"
                        value={creativeData.description}
                        onChange={(e) => handleInputChange('description', e.target.value)}
                        placeholder={t('adCreativeStep.shopNowAndSave')}
                        className="w-full px-4 py-2 border border-gray-300 rounded-lg focus:ring-2 focus:ring-amber-500 focus:border-transparent"
                    />
                </div>

                {/* Ad Permutation Counter */}
                {creativeData.creatives && creativeData.creatives.length > 0 && (
                    <div className="bg-amber-50 border border-amber-200 rounded-lg p-4">
                        <div className="flex items-center gap-2 text-amber-800">
                            <svg className="w-5 h-5" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                                <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M13 16h-1v-4h-1m1-4h.01M21 12a9 9 0 11-18 0 9 9 0 0118 0z" />
                            </svg>
                            <span className="font-medium">
                                {(() => {
                                    const validHeadlines = creativeData.headlines.filter(h => h && h.trim() !== '').length;
                                    const validBodies = creativeData.bodies.filter(b => b && b.trim() !== '').length;
                                    const totalAds = creativeData.creatives.length * validHeadlines * validBodies;
                                    const imageCount = creativeData.creatives.filter(c => c.mediaType !== 'video').length;
                                    const videoCount = creativeData.creatives.filter(c => c.mediaType === 'video').length;
                                    const mediaDesc = [];
                                    if (imageCount > 0) mediaDesc.push(t('adCreativeStep.imagesN', { count: imageCount }));
                                    if (videoCount > 0) mediaDesc.push(t('adCreativeStep.videosN', { count: videoCount }));
                                    return (
                                        <>
                                            {t('adCreativeStep.adsWillBeCreated', { count: totalAds })}
                                            <span className="text-sm font-normal ml-2">
                                                ({mediaDesc.join(' + ')} × {t('adCreativeStep.headlinesN', { count: validHeadlines })} × {t('adCreativeStep.bodiesN', { count: validBodies })})
                                            </span>
                                        </>
                                    );
                                })()}
                            </span>
                        </div>
                    </div>
                )}

                {/* Call to Action */}
                <div>
                    <label className="block text-sm font-medium text-gray-700 mb-2">
                        {t('adCreativeStep.callToAction')}
                    </label>
                    <select
                        value={creativeData.cta}
                        onChange={(e) => handleInputChange('cta', e.target.value)}
                        className="w-full px-4 py-2 border border-gray-300 rounded-lg focus:ring-2 focus:ring-amber-500 focus:border-transparent"
                    >
                        {CTA_OPTIONS.map(cta => (
                            <option key={cta} value={cta}>{t(`options.cta.${cta}`, { defaultValue: cta.replace(/_/g, ' ') })}</option>
                        ))}
                    </select>
                </div>

                {/* Website URL */}
                <div>
                    <label className="block text-sm font-medium text-gray-700 mb-2">
                        {t('adCreativeStep.websiteUrlLandingPage')}
                    </label>
                    <input
                        type="url"
                        value={creativeData.websiteUrl}
                        onChange={(e) => handleInputChange('websiteUrl', e.target.value)}
                        placeholder={t('adCreativeStep.httpsYourwebsiteComLanding')}
                        className="w-full px-4 py-2 border border-gray-300 rounded-lg focus:ring-2 focus:ring-amber-500 focus:border-transparent"
                    />
                </div>
            </div>

            {/* Navigation */}
            <div className="mt-8 flex justify-between">
                <button
                    onClick={onBack}
                    className="px-6 py-3 text-gray-600 hover:text-gray-800 font-medium"
                >
                    {t('adCreativeStep.back')}
                </button>
                <button
                    onClick={handleNext}
                    className="flex items-center gap-2 px-6 py-3 bg-amber-600 text-white rounded-lg font-medium hover:bg-amber-700"
                >
                    {t('adCreativeStep.nextStep')} <ChevronRight size={20} />
                </button>
            </div>
        </div>
    );
};

export default AdCreativeStep;
