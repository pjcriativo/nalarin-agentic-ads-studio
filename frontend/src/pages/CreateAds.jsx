import React from 'react';
import { useTranslation } from 'react-i18next';
import { useNavigate } from 'react-router-dom';
import { FileImage, Video, ArrowRight } from 'lucide-react';

export default function CreateAds() {
    const { t } = useTranslation('creatives');
    const navigate = useNavigate();

    return (
        <div className="max-w-5xl mx-auto">
            <div className="mb-8">
                <h1 className="text-3xl font-bold text-gray-900">{t('createAds.createNewAd')}</h1>
                <p className="text-gray-600 mt-2">{t('createAds.selectTheFormatYouWant')}</p>
            </div>

            <div className="grid grid-cols-1 md:grid-cols-2 gap-8">
                {/* Image Ad Card */}
                <button
                    onClick={() => navigate('/image-ads')}
                    className="group relative flex flex-col items-start p-8 bg-white rounded-2xl border-2 border-gray-100 hover:border-amber-200 hover:shadow-xl transition-all duration-300 text-left"
                >
                    <div className="w-16 h-16 bg-amber-100 rounded-2xl flex items-center justify-center mb-6 group-hover:scale-110 transition-transform duration-300">
                        <FileImage size={32} className="text-amber-600" />
                    </div>

                    <h3 className="text-2xl font-bold text-gray-900 mb-3">{t('createAds.imageAd')}</h3>
                    <p className="text-gray-500 mb-8 leading-relaxed">
                        {t('createAds.createHighConvertingStaticImage')}
                    </p>

                    <div className="mt-auto flex items-center gap-2 text-amber-600 font-semibold group-hover:gap-3 transition-all">
                        {t('createAds.startCreating')} <ArrowRight size={20} />
                    </div>
                </button>

                {/* Video Ad Card */}
                <button
                    onClick={() => navigate('/video-ads')}
                    className="group relative flex flex-col items-start p-8 bg-white rounded-2xl border-2 border-gray-100 hover:border-blue-200 hover:shadow-xl transition-all duration-300 text-left"
                >
                    <div className="w-16 h-16 bg-blue-100 rounded-2xl flex items-center justify-center mb-6 group-hover:scale-110 transition-transform duration-300">
                        <Video size={32} className="text-blue-600" />
                    </div>

                    <h3 className="text-2xl font-bold text-gray-900 mb-3">{t('createAds.videoAd')}</h3>
                    <p className="text-gray-500 mb-8 leading-relaxed">
                        {t('createAds.generateEngagingVideoAdsFrom')}
                    </p>

                    <div className="mt-auto flex items-center gap-2 text-blue-600 font-semibold group-hover:gap-3 transition-all">
                        {t('createAds.startCreating')} <ArrowRight size={20} />
                    </div>
                </button>
            </div>
        </div>
    );
}
