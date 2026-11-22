import React, { useState, useEffect } from 'react';
import { useTranslation } from 'react-i18next';
import { ChevronRight, Loader, Sparkles, CheckCircle2 } from 'lucide-react';

const AnalyzeTemplatesStep = ({ selectedTemplate, onNext, onBack }) => {
    const { t } = useTranslation('creatives');
    const [analyzing, setAnalyzing] = useState(true);
    const [analysis, setAnalysis] = useState(null);

    useEffect(() => {
        // Simulate analysis process
        const timer = setTimeout(() => {
            setAnalyzing(false);
            setAnalysis({
                // Keys, not text: resolved at render time so a language switch
                // after the analysis finishes still updates this panel.
                style: selectedTemplate?.style || null,
                tone: 'defaultTone',
                recommendations: ['rec1', 'rec2', 'rec3']
            });
        }, 2000);

        return () => clearTimeout(timer);
    }, [selectedTemplate]);

    return (
        <div>
            <h2 className="text-2xl font-bold mb-2">{t('analyzeTemplatesStep.analyzeTemplate')}</h2>
            <p className="text-gray-600 mb-8">{t('analyzeTemplatesStep.analyzingTheSelectedTemplateTo')}</p>

            <div className="bg-white border border-gray-200 rounded-xl p-8 mb-8">
                <div className="flex items-center gap-6 mb-8">
                    {/* Template Preview */}
                    <div className="w-32 h-32 bg-gray-100 rounded-lg flex items-center justify-center flex-shrink-0 border border-gray-200">
                        {selectedTemplate?.thumbnail ? (
                            <img src={selectedTemplate.thumbnail} alt={selectedTemplate.name} className="w-full h-full object-cover rounded-lg" />
                        ) : (
                            <span className="text-gray-400 text-xs text-center px-2">{selectedTemplate?.name || 'Template'}</span>
                        )}
                    </div>

                    <div className="flex-1">
                        <h3 className="text-xl font-bold text-gray-900 mb-2">{selectedTemplate?.name}</h3>
                        <p className="text-gray-500 text-sm">{selectedTemplate?.description || 'No description available.'}</p>
                    </div>
                </div>

                {analyzing ? (
                    <div className="flex flex-col items-center justify-center py-12 bg-blue-50 rounded-xl">
                        <Loader className="animate-spin text-blue-600 mb-4" size={32} />
                        <p className="text-blue-800 font-medium animate-pulse">{t('analyzeTemplatesStep.analyzingTemplateStructureAndStyle')}</p>
                    </div>
                ) : (
                    <div className="bg-green-50 border border-green-100 rounded-xl p-6 animate-in fade-in duration-500">
                        <div className="flex items-center gap-2 mb-4">
                            <Sparkles className="text-green-600" size={24} />
                            <h4 className="font-bold text-green-900 text-lg">{t('analyzeTemplatesStep.analysisComplete')}</h4>
                        </div>

                        <div className="grid grid-cols-1 md:grid-cols-2 gap-6">
                            <div>
                                <h5 className="font-semibold text-green-800 mb-2">{t('analyzeTemplatesStep.styleProfile')}</h5>
                                <div className="space-y-2">
                                    <div className="flex justify-between text-sm border-b border-green-200 pb-1">
                                        <span className="text-green-700">{t('analyzeTemplatesStep.visualStyle')}</span>
                                        <span className="font-medium text-green-900">{analysis.style || t('common.defaultStyle')}</span>
                                    </div>
                                    <div className="flex justify-between text-sm border-b border-green-200 pb-1">
                                        <span className="text-green-700">{t('analyzeTemplatesStep.recommendedTone')}</span>
                                        <span className="font-medium text-green-900">{t(`common.${analysis.tone}`)}</span>
                                    </div>
                                </div>
                            </div>

                            <div>
                                <h5 className="font-semibold text-green-800 mb-2">{t('analyzeTemplatesStep.keyRecommendations')}</h5>
                                <ul className="space-y-2">
                                    {analysis.recommendations.map((rec, i) => (
                                        <li key={i} className="flex items-start gap-2 text-sm text-green-800">
                                            <CheckCircle2 size={16} className="mt-0.5 flex-shrink-0" />
                                            {t(`common.${rec}`)}
                                        </li>
                                    ))}
                                </ul>
                            </div>
                        </div>
                    </div>
                )}
            </div>

            {/* Navigation */}
            <div className="flex justify-between">
                <button
                    onClick={onBack}
                    className="px-6 py-3 text-gray-600 hover:text-gray-800 font-medium"
                >
                    {t('analyzeTemplatesStep.back')}
                </button>
                <button
                    onClick={onNext}
                    disabled={analyzing}
                    className="flex items-center gap-2 px-6 py-3 bg-blue-600 text-white rounded-lg font-medium hover:bg-blue-700 disabled:bg-gray-300 disabled:cursor-not-allowed"
                >
                    {t('analyzeTemplatesStep.nextStep')} <ChevronRight size={20} />
                </button>
            </div>
        </div>
    );
};

export default AnalyzeTemplatesStep;
