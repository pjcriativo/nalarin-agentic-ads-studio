import React, { useState } from 'react';
import { useTranslation } from 'react-i18next';
import { ChevronRight, ChevronLeft, Sparkles, Check, Image, FileText, Briefcase, Package, Users } from 'lucide-react';
import { useBrands } from '../context/BrandContext';
import { useToast } from '../context/ToastContext';
import { useAuth } from '../context/AuthContext';
import ImageTemplateSelector from '../components/ImageTemplateSelector';
import BrandSelectionStep from '../components/steps/BrandSelectionStep';
import ProductSelectionStep from '../components/steps/ProductSelectionStep';
import ProfileSelectionStep from '../components/steps/ProfileSelectionStep';

const API_URL = import.meta.env.VITE_API_URL || 'http://localhost:8000/api/v1';

export default function AdRemix() {
    const { t } = useTranslation('library');
    const { brands, customerProfiles } = useBrands();
    const { showError } = useToast();
    const { authFetch } = useAuth();
    const [currentStep, setCurrentStep] = useState(1);
    const [loading, setLoading] = useState(false);
    const [blueprint, setBlueprint] = useState(null);
    const [adConcept, setAdConcept] = useState(null);

    const [wizardData, setWizardData] = useState({
        template: null,
        brand: null,
        product: null,
        profile: null,
        campaignDetails: {
            offer: '',
            urgency: '',
            messaging: ''
        }
    });

    const steps = [
        { id: 1, name: t('adRemix.steps.template'), icon: Image },
        { id: 2, name: t('adRemix.steps.brand'), icon: Briefcase },
        { id: 3, name: t('adRemix.steps.product'), icon: Package },
        { id: 4, name: t('adRemix.steps.profile'), icon: Users },
        { id: 5, name: t('adRemix.steps.campaign'), icon: FileText },
        { id: 6, name: t('adRemix.steps.review'), icon: Check }
    ];

    const updateData = (field, value) => {
        setWizardData(prev => ({ ...prev, [field]: value }));
    };

    const updateCampaignDetails = (field, value) => {
        setWizardData(prev => ({
            ...prev,
            campaignDetails: { ...prev.campaignDetails, [field]: value }
        }));
    };

    const handleDeconstruct = async () => {
        setLoading(true);
        try {
            const response = await authFetch(`${API_URL}/ad-remix/deconstruct`, {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ template_id: wizardData.template.id })
            });

            if (!response.ok) throw new Error(t('adRemix.deconstructionFailed'));

            const data = await response.json();
            setBlueprint(data);
        } catch (error) {
            console.error('Deconstruction error:', error);
            showError(t('adRemix.failedToDeconstructTemplatePlease'));
        } finally {
            setLoading(false);
        }
    };

    const handleReconstruct = async () => {
        setLoading(true);
        try {
            const response = await authFetch(`${API_URL}/ad-remix/reconstruct`, {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({
                    template_id: wizardData.template.id,
                    brand_id: wizardData.brand.id,
                    product_id: wizardData.product.id,
                    profile_id: wizardData.profile.id,
                    campaign_offer: wizardData.campaignDetails.offer,
                    campaign_urgency: wizardData.campaignDetails.urgency,
                    campaign_messaging: wizardData.campaignDetails.messaging
                })
            });

            if (!response.ok) throw new Error(t('adRemix.reconstructionFailed'));

            const data = await response.json();
            setAdConcept(data);
            setCurrentStep(7); // Move to results step
        } catch (error) {
            console.error('Reconstruction error:', error);
            showError(t('adRemix.failedToReconstructAdPlease'));
        } finally {
            setLoading(false);
        }
    };

    return (
        <div className="max-w-5xl mx-auto">
            {/* Header */}
            <div className="mb-8">
                <h1 className="text-3xl font-bold text-gray-900 flex items-center gap-3">
                    <Sparkles size={32} className="text-purple-600" />
                    {t('adRemix.adRemixEngine')}
                </h1>
                <p className="text-gray-600 mt-1">{t('adRemix.deconstructWinningAdsAndReconstruct')}</p>
            </div>

            {/* Progress Steps */}
            <div className="mb-8 bg-white rounded-xl shadow-sm border border-gray-200 p-6">
                <div className="flex items-center justify-between relative">
                    <div className="absolute left-0 top-1/2 transform -translate-y-1/2 w-full h-1 bg-gray-200 -z-10"></div>
                    {steps.map((step) => {
                        const Icon = step.icon;
                        const isActive = step.id === currentStep;
                        const isCompleted = step.id < currentStep;

                        return (
                            <div
                                key={step.id}
                                className="flex flex-col items-center bg-white px-2"
                            >
                                <div
                                    className={`w-10 h-10 rounded-full flex items-center justify-center mb-2 transition-all ${isActive ? 'bg-purple-600 text-white scale-110 shadow-md' :
                                        isCompleted ? 'bg-green-500 text-white' :
                                            'bg-gray-200 text-gray-500'
                                        }`}
                                >
                                    {isCompleted ? <Check size={20} /> : <Icon size={20} />}
                                </div>
                                <span className={`text-xs font-medium ${isActive ? 'text-purple-600' : 'text-gray-500'}`}>
                                    {step.name}
                                </span>
                            </div>
                        );
                    })}</div>
            </div>

            {/* Step Content */}
            <div className="bg-white rounded-xl shadow-sm border border-gray-200 p-8 min-h-[500px]">
                {loading && (
                    <div className="absolute inset-0 bg-white/80 backdrop-blur-sm z-50 flex flex-col items-center justify-center rounded-xl">
                        <div className="w-16 h-16 border-4 border-purple-200 border-t-purple-600 rounded-full animate-spin mb-4"></div>
                        <h3 className="text-xl font-bold text-gray-900">
                            {currentStep === 1 ? t('adRemix.analyzingTemplateStructure') : t('adRemix.generatingYourAdConcept')}
                        </h3>
                    </div>
                )}

                {/* Step 1: Template Selection */}
                {currentStep === 1 && (
                    <div>
                        <h3 className="text-xl font-bold mb-4">{t('adRemix.selectAWinningTemplateTo')}</h3>
                        <p className="text-gray-600 mb-6">{t('adRemix.chooseAnAdTemplateTo')}</p>
                        <ImageTemplateSelector
                            onSelect={(template) => {
                                updateData('template', template);
                                setCurrentStep(2);
                            }}
                            onClose={() => { }}
                            embedded={true}
                        />
                    </div>
                )}

                {/* Step 2: Brand Selection */}
                {currentStep === 2 && (
                    <BrandSelectionStep
                        brands={brands}
                        selectedBrand={wizardData.brand}
                        onSelect={(brand) => {
                            updateData('brand', brand);
                            setCurrentStep(3);
                        }}
                    />
                )}

                {/* Step 3: Product Selection */}
                {currentStep === 3 && (
                    <ProductSelectionStep
                        products={wizardData.brand?.products || []}
                        selectedProduct={wizardData.product}
                        useProductShots={false}
                        onSelect={(product) => {
                            updateData('product', product);
                            setCurrentStep(4);
                        }}
                        onToggleProductShots={() => { }}
                    />
                )}

                {/* Step 4: Profile Selection */}
                {currentStep === 4 && (
                    <ProfileSelectionStep
                        profiles={customerProfiles.filter(p => wizardData.brand?.profileIds?.includes(p.id))}
                        selectedProfile={wizardData.profile}
                        onSelect={(profile) => {
                            updateData('profile', profile);
                            setCurrentStep(5);
                        }}
                    />
                )}

                {/* Step 5: Campaign Details */}
                {currentStep === 5 && (
                    <div>
                        <h3 className="text-xl font-bold mb-4">{t('adRemix.campaignDetails')}</h3>
                        <p className="text-gray-600 mb-6">{t('adRemix.provideDetailsToCustomizeYour')}</p>

                        <div className="max-w-2xl space-y-4">
                            <div>
                                <label className="block text-sm font-medium text-gray-700 mb-2">
                                    {t('adRemix.offerPromotion')}
                                </label>
                                <input
                                    type="text"
                                    value={wizardData.campaignDetails.offer}
                                    onChange={(e) => updateCampaignDetails('offer', e.target.value)}
                                    placeholder={t('adRemix.eG50OffBlack')}
                                    className="w-full px-4 py-3 border border-gray-300 rounded-lg focus:ring-2 focus:ring-purple-600 focus:border-transparent"
                                />
                            </div>

                            <div>
                                <label className="block text-sm font-medium text-gray-700 mb-2">
                                    {t('adRemix.urgencyTiming')}
                                </label>
                                <input
                                    type="text"
                                    value={wizardData.campaignDetails.urgency}
                                    onChange={(e) => updateCampaignDetails('urgency', e.target.value)}
                                    placeholder={t('adRemix.eGLimitedTimeEnds')}
                                    className="w-full px-4 py-3 border border-gray-300 rounded-lg focus:ring-2 focus:ring-purple-600 focus:border-transparent"
                                />
                            </div>

                            <div>
                                <label className="block text-sm font-medium text-gray-700 mb-2">
                                    {t('adRemix.keyMessaging')}
                                </label>
                                <textarea
                                    value={wizardData.campaignDetails.messaging}
                                    onChange={(e) => updateCampaignDetails('messaging', e.target.value)}
                                    placeholder={t('adRemix.eGScienceBackedResults')}
                                    rows={3}
                                    className="w-full px-4 py-3 border border-gray-300 rounded-lg focus:ring-2 focus:ring-purple-600 focus:border-transparent"
                                />
                            </div>
                        </div>
                    </div>
                )}

                {/* Step 6: Review & Generate */}
                {currentStep === 6 && (
                    <div>
                        <h3 className="text-xl font-bold mb-4">{t('adRemix.reviewGenerate')}</h3>
                        <p className="text-gray-600 mb-6">{t('adRemix.reviewYourSelectionsAndGenerate')}</p>

                        <div className="space-y-4 max-w-2xl">
                            <div className="bg-gray-50 p-4 rounded-lg">
                                <h4 className="font-bold text-gray-900 mb-2">{t('adRemix.template')}</h4>
                                <p className="text-gray-700">{wizardData.template?.name}</p>
                            </div>

                            <div className="bg-gray-50 p-4 rounded-lg">
                                <h4 className="font-bold text-gray-900 mb-2">{t('adRemix.brand')}</h4>
                                <p className="text-gray-700">{wizardData.brand?.name}</p>
                            </div>

                            <div className="bg-gray-50 p-4 rounded-lg">
                                <h4 className="font-bold text-gray-900 mb-2">{t('adRemix.product')}</h4>
                                <p className="text-gray-700">{wizardData.product?.name}</p>
                            </div>

                            <div className="bg-gray-50 p-4 rounded-lg">
                                <h4 className="font-bold text-gray-900 mb-2">{t('adRemix.audience')}</h4>
                                <p className="text-gray-700">{wizardData.profile?.name}</p>
                            </div>

                            <div className="bg-gray-50 p-4 rounded-lg">
                                <h4 className="font-bold text-gray-900 mb-2">{t('adRemix.campaign')}</h4>
                                <p className="text-gray-700"><strong>{t('adRemix.offer')}</strong> {wizardData.campaignDetails.offer}</p>
                                {wizardData.campaignDetails.urgency && (
                                    <p className="text-gray-700"><strong>{t('adRemix.urgency')}</strong> {wizardData.campaignDetails.urgency}</p>
                                )}
                                <p className="text-gray-700"><strong>{t('adRemix.messaging')}</strong> {wizardData.campaignDetails.messaging}</p>
                            </div>
                        </div>
                    </div>
                )}

                {/* Step 7: Results */}
                {currentStep === 7 && adConcept && (
                    <div>
                        <div className="text-center mb-8">
                            <div className="w-20 h-20 bg-green-100 text-green-600 rounded-full flex items-center justify-center mx-auto mb-4">
                                <Check size={40} />
                            </div>
                            <h2 className="text-3xl font-bold text-gray-900 mb-2">{t('adRemix.adConceptGenerated')}</h2>
                            <p className="text-gray-600">{t('adRemix.yourRemixedAdConceptIs')}</p>
                        </div>

                        <div className="space-y-6 max-w-3xl mx-auto">
                            <div className="bg-purple-50 border-2 border-purple-200 rounded-xl p-6">
                                <h4 className="font-bold text-purple-900 mb-3 flex items-center gap-2">
                                    <FileText size={20} />
                                    {t('adRemix.headline')}
                                </h4>
                                <p className="text-lg font-bold text-gray-900">{adConcept.headline_remix}</p>
                            </div>

                            <div className="bg-blue-50 border-2 border-blue-200 rounded-xl p-6">
                                <h4 className="font-bold text-blue-900 mb-3">{t('adRemix.bodyCopy')}</h4>
                                <p className="text-gray-700 whitespace-pre-line">{adConcept.body_copy}</p>
                            </div>

                            <div className="bg-green-50 border-2 border-green-200 rounded-xl p-6">
                                <h4 className="font-bold text-green-900 mb-3">{t('adRemix.callToAction')}</h4>
                                <button className="px-6 py-3 bg-green-600 text-white rounded-lg font-bold">
                                    {adConcept.cta_button}
                                </button>
                            </div>

                            <div className="bg-amber-50 border-2 border-amber-200 rounded-xl p-6">
                                <h4 className="font-bold text-amber-900 mb-3 flex items-center gap-2">
                                    <Image size={20} />
                                    {t('adRemix.visualDescription')}
                                </h4>
                                <p className="text-gray-700">{adConcept.visual_description}</p>
                            </div>

                            <div className="bg-gray-50 border-2 border-gray-200 rounded-xl p-6">
                                <h4 className="font-bold text-gray-900 mb-3 flex items-center gap-2">
                                    <Sparkles size={20} />
                                    {t('adRemix.imageGenerationPrompt')}
                                </h4>
                                <p className="text-sm text-gray-700 font-mono bg-white p-4 rounded border border-gray-300">
                                    {adConcept.image_generation_prompt}
                                </p>
                            </div>
                        </div>
                    </div>
                )}
            </div>

            {/* Navigation */}
            <div className="mt-6 flex items-center justify-between">
                <div></div>
                <div className="flex gap-3">
                    {currentStep > 1 && currentStep < 7 && (
                        <button
                            onClick={() => setCurrentStep(currentStep - 1)}
                            className="flex items-center gap-2 px-6 py-3 bg-gray-100 text-gray-700 rounded-lg hover:bg-gray-200 font-medium"
                        >
                            <ChevronLeft size={20} />
                            {t('adRemix.back')}
                        </button>
                    )}

                    {currentStep === 6 && (
                        <button
                            onClick={handleReconstruct}
                            disabled={!wizardData.campaignDetails.offer || !wizardData.campaignDetails.messaging}
                            className="flex items-center gap-2 px-8 py-3 bg-gradient-to-r from-purple-600 to-pink-600 text-white rounded-lg hover:from-purple-700 hover:to-pink-700 font-medium shadow-lg disabled:opacity-50 disabled:cursor-not-allowed"
                        >
                            <Sparkles size={20} />
                            {t('adRemix.generateRemix')}
                        </button>
                    )}

                    {currentStep === 7 && (
                        <button
                            onClick={() => window.location.reload()}
                            className="px-6 py-3 bg-purple-600 text-white rounded-lg hover:bg-purple-700 font-medium"
                        >
                            {t('adRemix.createAnotherRemix')}
                        </button>
                    )}
                </div>
            </div>
        </div>
    );
}
