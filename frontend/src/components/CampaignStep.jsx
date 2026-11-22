import React, { useState, useEffect } from 'react';
import { translateBackendMessage } from '../i18n/backendErrors';
import { useTranslation } from 'react-i18next';
import { ChevronRight, ChevronLeft, Check, Loader, Plus } from 'lucide-react';
import { useCampaign } from '../context/CampaignContext';
import { useToast } from '../context/ToastContext';
import { getCampaigns } from '../lib/facebookApi';

const CAMPAIGN_OBJECTIVES = [
    { value: 'OUTCOME_SALES', label: 'Sales - Drive purchases and conversions' },
    { value: 'OUTCOME_TRAFFIC', label: 'Traffic - Send people to your website' },
    { value: 'OUTCOME_LEADS', label: 'Leads - Collect leads for your business' },
    { value: 'OUTCOME_ENGAGEMENT', label: 'Engagement - Get more messages, video views, etc.' },
    { value: 'OUTCOME_AWARENESS', label: 'Awareness - Reach people near your business' },
    { value: 'OUTCOME_APP_PROMOTION', label: 'App Promotion - Get more app installs' }
];

const BID_STRATEGIES = [
    { value: 'LOWEST_COST_WITHOUT_CAP', label: 'Lowest Cost (Highest Volume or Value, No Cap)' },
    { value: 'LOWEST_COST_WITH_BID_CAP', label: 'Lowest Cost with Bid Cap' },
    { value: 'COST_CAP', label: 'Cost Cap (Cost Per Result Goal)' }
];

const CampaignStep = ({ onNext, onBack }) => {
    const { t } = useTranslation('campaigns');
    const { campaignData, setCampaignData, selectedAdAccount } = useCampaign();
    const { showError, showWarning } = useToast();
    const [mode, setMode] = useState('new'); // 'new' or 'existing'
    const [existingCampaigns, setExistingCampaigns] = useState([]);
    const [selectedCampaign, setSelectedCampaign] = useState(null);
    const [loading] = useState(false);
    const [loadingCampaigns, setLoadingCampaigns] = useState(false);

    useEffect(() => {
        // Fetch campaigns when switching to existing mode
        if (mode === 'existing' && selectedAdAccount) {
            fetchExistingCampaigns();
        }
    }, [mode, selectedAdAccount]);

    const fetchExistingCampaigns = async () => {
        if (!selectedAdAccount) return;

        setLoadingCampaigns(true);
        try {
            const campaigns = await getCampaigns(selectedAdAccount.id);
            setExistingCampaigns(campaigns);
        } catch (error) {
            console.error('Error fetching campaigns:', error);
            showError(t('campaignStep.errorFetchingCampaignsMessage', { message: translateBackendMessage(error.message) }));
        } finally {
            setLoadingCampaigns(false);
        }
    };

    const handleSelectExisting = (campaign) => {
        setSelectedCampaign(campaign);

        const dailyBudget = campaign.dailyBudget ? parseInt(campaign.dailyBudget) / 100 : 0;
        const lifetimeBudget = campaign.lifetimeBudget ? parseInt(campaign.lifetimeBudget) / 100 : 0;

        // CBO campaigns have budget set at campaign level
        // ABO campaigns have budget set at ad set level (campaign budget is 0 or null)
        const isCBO = dailyBudget > 0 || lifetimeBudget > 0;

        setCampaignData({
            ...campaign,
            budgetType: isCBO ? 'CBO' : 'ABO',
            dailyBudget: dailyBudget,
            bidStrategy: campaign.bid_strategy || '',
            fbCampaignId: campaign.id,
            isExisting: true
        });
    };

    const handleInputChange = (field, value) => {
        setCampaignData(prev => ({
            ...prev,
            [field]: value,
            isExisting: false
        }));
    };

    const handleNext = async () => {
        if (mode === 'existing' && !selectedCampaign) {
            showWarning(t('campaignStep.pleaseSelectACampaign'));
            return;
        }

        if (mode === 'existing') {
            // For existing campaigns, we just use the selected data
            // No need to call API or create anything new
            // The data is already set in handleSelectExisting
        }

        if (mode === 'new') {
            if (!campaignData.name || !campaignData.objective) {
                showWarning(t('campaignStep.pleaseFillInAllRequired'));
                return;
            }

            if (campaignData.budgetType === 'CBO' && (!campaignData.dailyBudget || campaignData.dailyBudget <= 0)) {
                showWarning(t('campaignStep.pleaseEnterAValidDaily'));
                return;
            }

            // Validate Bid Amount if strategy requires it (for CBO campaigns)
            if (campaignData.budgetType === 'CBO' &&
                (campaignData.bidStrategy === 'LOWEST_COST_WITH_BID_CAP' || campaignData.bidStrategy === 'COST_CAP') &&
                (!campaignData.bidAmount || campaignData.bidAmount <= 0)) {
                showWarning(t('campaignStep.pleaseEnterAValidBid'));
                return;
            }

            // For new campaigns, we just prepare the data
            // The actual creation happens in the final step (BulkAdCreation)
            const id = `camp_${Date.now()}`;
            setCampaignData(prev => ({ ...prev, id }));
        }

        onNext();
    };

    return (
        <div>
            <h2 className="text-2xl font-bold mb-6">{t('campaignStep.campaignSetup')}</h2>

            {/* Mode Toggle */}
            <div className="flex gap-4 mb-6">
                <button
                    onClick={() => {
                        setMode('new');
                        setCampaignData(prev => ({
                            ...prev,
                            isExisting: false,
                            fbCampaignId: null
                        }));
                    }}
                    className={`flex-1 p-4 rounded-xl border-2 transition-all ${mode === 'new'
                        ? 'border-amber-600 bg-amber-50'
                        : 'border-gray-200 hover:border-amber-300'
                        }`}
                >
                    <Plus className="mx-auto mb-2" size={24} />
                    <div className="font-semibold">{t('campaignStep.createNewCampaign')}</div>
                </button>
                <button
                    onClick={() => setMode('existing')}
                    className={`flex-1 p-4 rounded-xl border-2 transition-all ${mode === 'existing'
                        ? 'border-amber-600 bg-amber-50'
                        : 'border-gray-200 hover:border-amber-300'
                        }`}
                >
                    <Check className="mx-auto mb-2" size={24} />
                    <div className="font-semibold">{t('campaignStep.useExistingCampaign')}</div>
                </button>
            </div>

            {/* Existing Campaigns List */}
            {mode === 'existing' && (
                <div className="space-y-4 mb-6">
                    {/* Existing Campaigns */}
                    <div>
                        <h3 className="font-semibold text-gray-700 mb-3">{t('campaignStep.selectACampaign')}</h3>
                        {loadingCampaigns ? (
                            <div className="flex items-center justify-center gap-2 text-gray-500 py-8">
                                <Loader className="animate-spin" size={20} />
                                <span>{t('campaignStep.loadingCampaignsFromFacebook')}</span>
                            </div>
                        ) : existingCampaigns.length === 0 ? (
                            <p className="text-gray-500 text-center py-8">{t('campaignStep.noCampaignsFoundInThis')}</p>
                        ) : (
                            existingCampaigns.map(campaign => (
                                <div
                                    key={campaign.id}
                                    onClick={() => handleSelectExisting(campaign)}
                                    className={`p-4 rounded-xl border-2 cursor-pointer transition-all mb-2 ${selectedCampaign?.id === campaign.id
                                        ? 'border-amber-600 bg-amber-50'
                                        : 'border-gray-200 hover:border-amber-300'
                                        }`}
                                >
                                    <div className="flex justify-between items-start">
                                        <div className="flex-1">
                                            <div className="flex items-center gap-2">
                                                <div className="font-bold text-gray-900">{campaign.name}</div>
                                                <span className={`px-2 py-0.5 rounded text-xs font-medium ${campaign.status === 'ACTIVE' ? 'bg-green-100 text-green-700' :
                                                    campaign.status === 'PAUSED' ? 'bg-yellow-100 text-yellow-700' :
                                                        'bg-gray-100 text-gray-700'
                                                    }`}>
                                                    {campaign.status}
                                                </span>
                                            </div>
                                            <div className="text-sm text-gray-500 mt-1">
                                                <span className="font-medium text-gray-700">
                                                    {(campaign.dailyBudget || campaign.lifetimeBudget) ? 'CBO' : 'ABO'}
                                                </span>
                                                {' • '}{campaign.objective}
                                                {campaign.dailyBudget && ` • Daily: $${(parseInt(campaign.dailyBudget) / 100).toFixed(2)}`}
                                                {campaign.lifetimeBudget && ` • Lifetime: $${(parseInt(campaign.lifetimeBudget) / 100).toFixed(2)}`}
                                            </div>
                                        </div>
                                        {selectedCampaign?.id === campaign.id && (
                                            <Check className="text-amber-600" size={20} />
                                        )}
                                    </div>
                                </div>
                            ))
                        )}
                    </div>
                </div>
            )}

            {/* New Campaign Form */}
            {mode === 'new' && (
                <div className="space-y-4">
                    <div>
                        <label className="block text-sm font-medium text-gray-700 mb-2">
                            {t('campaignStep.campaignName')}
                        </label>
                        <input
                            type="text"
                            value={campaignData.name}
                            onChange={(e) => handleInputChange('name', e.target.value)}
                            placeholder={t('campaignStep.summerSaleCampaign')}
                            className="w-full px-4 py-2 border border-gray-300 rounded-lg focus:ring-2 focus:ring-amber-500 focus:border-transparent"
                        />
                    </div>

                    <div>
                        <label className="block text-sm font-medium text-gray-700 mb-2">
                            {t('campaignStep.campaignObjective')}
                        </label>
                        <select
                            value={campaignData.objective}
                            onChange={(e) => handleInputChange('objective', e.target.value)}
                            className="w-full px-4 py-2 border border-gray-300 rounded-lg focus:ring-2 focus:ring-amber-500 focus:border-transparent"
                        >
                            <option value="">{t('campaignStep.selectObjective')}</option>
                            {CAMPAIGN_OBJECTIVES.map(obj => (
                                <option key={obj.value} value={obj.value}>{t(`options.objective.${obj.value}`, { defaultValue: obj.label })}</option>
                            ))}
                        </select>
                    </div>

                    <div>
                        <label className="block text-sm font-medium text-gray-700 mb-2">
                            {t('campaignStep.budgetType')}
                        </label>
                        <div className="grid grid-cols-2 gap-4">
                            <button
                                type="button"
                                onClick={() => handleInputChange('budgetType', 'ABO')}
                                className={`p-3 rounded-lg border-2 transition-all ${campaignData.budgetType === 'ABO'
                                    ? 'border-amber-600 bg-amber-50'
                                    : 'border-gray-200 hover:border-amber-300'
                                    }`}
                            >
                                <div className="font-semibold">{t('campaignStep.abo')}</div>
                                <div className="text-xs text-gray-500">{t('campaignStep.adSetBudget')}</div>
                            </button>
                            <button
                                type="button"
                                onClick={() => handleInputChange('budgetType', 'CBO')}
                                className={`p-3 rounded-lg border-2 transition-all ${campaignData.budgetType === 'CBO'
                                    ? 'border-amber-600 bg-amber-50'
                                    : 'border-gray-200 hover:border-amber-300'
                                    }`}
                            >
                                <div className="font-semibold">{t('campaignStep.cbo')}</div>
                                <div className="text-xs text-gray-500">{t('campaignStep.campaignBudget')}</div>
                            </button>
                        </div>
                    </div>

                    {campaignData.budgetType === 'CBO' && (
                        <>
                            <div>
                                <label className="block text-sm font-medium text-gray-700 mb-2">
                                    {t('campaignStep.dailyBudgetUsd')}
                                </label>
                                <div className="relative">
                                    <div className="absolute inset-y-0 left-0 pl-3 flex items-center pointer-events-none">
                                        <span className="text-gray-500">$</span>
                                    </div>
                                    <input
                                        type="number"
                                        value={campaignData.dailyBudget || ''}
                                        onChange={(e) => handleInputChange('dailyBudget', parseInt(e.target.value) || 0)}
                                        placeholder="100"
                                        min="1"
                                        step="1"
                                        className="w-full pl-7 pr-4 py-2 border border-gray-300 rounded-lg focus:ring-2 focus:ring-amber-500 focus:border-transparent"
                                    />
                                </div>
                            </div>

                            <div>
                                <label className="block text-sm font-medium text-gray-700 mb-2">
                                    {t('campaignStep.bidStrategy')}
                                </label>
                                <select
                                    value={campaignData.bidStrategy}
                                    onChange={(e) => handleInputChange('bidStrategy', e.target.value)}
                                    className="w-full px-4 py-2 border border-gray-300 rounded-lg focus:ring-2 focus:ring-amber-500 focus:border-transparent"
                                >
                                    <option value="">{t('campaignStep.selectBidStrategy')}</option>
                                    {BID_STRATEGIES.map(strategy => (
                                        <option key={strategy.value} value={strategy.value}>{t(`options.campaignBid.${strategy.value}`, { defaultValue: strategy.label })}</option>
                                    ))}
                                </select>
                            </div>

                            {/* Bid Amount - Required for Cost Cap and Bid Cap strategies */}
                            {(campaignData.bidStrategy === 'COST_CAP' || campaignData.bidStrategy === 'LOWEST_COST_WITH_BID_CAP') && (
                                <div>
                                    <label className="block text-sm font-medium text-gray-700 mb-2">
                                        {campaignData.bidStrategy === 'COST_CAP' ? t('campaignStep.costCapAmountUsd') : t('campaignStep.bidCapAmountUsd')} *
                                    </label>
                                    <div className="relative">
                                        <div className="absolute inset-y-0 left-0 pl-3 flex items-center pointer-events-none">
                                            <span className="text-gray-500">$</span>
                                        </div>
                                        <input
                                            type="number"
                                            value={campaignData.bidAmount || ''}
                                            onChange={(e) => handleInputChange('bidAmount', parseFloat(e.target.value) || 0)}
                                            placeholder="10.00"
                                            min="0.01"
                                            step="0.01"
                                            className="w-full pl-7 pr-4 py-2 border border-gray-300 rounded-lg focus:ring-2 focus:ring-amber-500 focus:border-transparent"
                                        />
                                    </div>
                                    <p className="text-xs text-gray-500 mt-1">
                                        {campaignData.bidStrategy === 'COST_CAP'
                                            ? t('campaignStep.maximumAverageCostPerResult') : t('campaignStep.maximumBidAmountForEach')}
                                    </p>
                                </div>
                            )}
                        </>
                    )}
                </div>
            )}

            {/* Navigation */}
            <div className="mt-8 flex justify-between">
                {onBack && (
                    <button
                        onClick={onBack}
                        className="px-6 py-3 text-gray-600 hover:text-gray-800 font-medium"
                    >
                        {t('campaignStep.back')}
                    </button>
                )}
                <button
                    onClick={handleNext}
                    disabled={loading}
                    className="ml-auto flex items-center gap-2 px-6 py-3 bg-amber-600 text-white rounded-lg font-medium hover:bg-amber-700 disabled:bg-gray-300 disabled:cursor-not-allowed"
                >
                    {loading ? t('campaignStep.saving') : t('campaignStep.nextStep')} <ChevronRight size={20} />
                </button>
            </div>
        </div>
    );
};

export default CampaignStep;
