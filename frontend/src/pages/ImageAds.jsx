import { useToast } from '../context/ToastContext';
import { useTranslation } from 'react-i18next';
import { sizeLabel, sizeShort, sizeDescription } from '../i18n/format';
import { useAuth } from '../context/AuthContext';
import React, { useState, useEffect } from 'react';
import { ChevronRight, ChevronLeft, Check, Briefcase, Package, Users, Image, Hash, FileText, Sparkles, Download, ChevronDown, ChevronUp, Settings, CheckCircle2, ArrowRight } from 'lucide-react';
import { useBrands } from '../context/BrandContext';
import ImageTemplateSelector from '../components/ImageTemplateSelector';
import BrandSelectionStep from '../components/steps/BrandSelectionStep';
import ProductSelectionStep from '../components/steps/ProductSelectionStep';
import ProfileSelectionStep from '../components/steps/ProfileSelectionStep';
import StyleSelector from '../components/StyleSelector';

const API_URL = import.meta.env.VITE_API_URL || 'http://localhost:8000/api/v1';

export default function ImageAds() {
    const { t } = useTranslation('creatives');
    const { brands, customerProfiles } = useBrands();
    const { showError } = useToast();
    const { authFetch } = useAuth();
    const [currentStep, setCurrentStep] = useState(1);
    const [generating, setGenerating] = useState(false);
    const [generatedCopy, setGeneratedCopy] = useState(null);
    const [generatedImages, setGeneratedImages] = useState([]);
    const [selectedCopy, setSelectedCopy] = useState(null);
    const [customImagePrompt, setCustomImagePrompt] = useState('');
    const [templateMode, setTemplateMode] = useState('style'); // 'style' or 'template'

    // Load saved campaign details from localStorage on mount
    const [wizardData, setWizardData] = useState(() => {
        const savedCampaignDetails = localStorage.getItem('imageAds_campaignDetails');
        const defaultCampaignDetails = {
            offer: '',
            urgency: '',
            messaging: ''
        };

        return {
            brand: null,
            product: null,
            profile: null,
            template: null,
            variationCount: 3,
            imageSizes: [{
                name: 'Square (Feed/Carousel)',
                width: 1080,
                height: 1080,
                aspectRatio: '1:1'
            }],
            resolution: '1K',
            campaignDetails: savedCampaignDetails
                ? JSON.parse(savedCampaignDetails)
                : defaultCampaignDetails,
            model: 'nano-banana-pro',
            useProductShots: false
        };
    });

    // Save campaign details to localStorage whenever they change
    useEffect(() => {
        if (wizardData.campaignDetails) {
            localStorage.setItem('imageAds_campaignDetails', JSON.stringify(wizardData.campaignDetails));
        }
    }, [wizardData.campaignDetails]);

    const steps = [
        { id: 1, name: t('imageAds.brand2'), icon: Briefcase },
        { id: 2, name: t('imageAds.product2'), icon: Package },
        { id: 3, name: t('imageAds.profile'), icon: Users },
        { id: 4, name: t('imageAds.template2'), icon: Image },
        { id: 5, name: t('imageAds.variations'), icon: Hash },
        { id: 6, name: t('imageAds.size2'), icon: Image },
        { id: 7, name: t('imageAds.campaign'), icon: FileText },
        { id: 8, name: t('imageAds.review'), icon: Check }
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

    const nextStep = () => {
        if (currentStep < steps.length) {
            setCurrentStep(currentStep + 1);
        }
    };

    const prevStep = () => {
        if (currentStep > 1) {
            setCurrentStep(currentStep - 1);
        }
    };

    const isStepComplete = (stepId) => {
        switch (stepId) {
            case 1: return wizardData.brand !== null;
            case 2: return wizardData.product !== null;
            case 3: return wizardData.profile !== null;
            case 4: return wizardData.template !== null;
            case 5: return wizardData.variationCount >= 1 && wizardData.variationCount <= 10;
            case 6: return wizardData.imageSizes && wizardData.imageSizes.length > 0;
            case 7: return wizardData.campaignDetails.offer && wizardData.campaignDetails.messaging;
            default: return true;
        }
    };

    const canProceed = () => isStepComplete(currentStep);

    const handleStepClick = (stepId) => {
        // Always allow going back
        if (stepId < currentStep) {
            setCurrentStep(stepId);
            return;
        }

        // For going forward, check if all previous steps are complete
        // We check from step 1 up to stepId - 1
        let canNavigate = true;
        for (let i = 1; i < stepId; i++) {
            if (!isStepComplete(i)) {
                canNavigate = false;
                break;
            }
        }

        if (canNavigate) {
            setCurrentStep(stepId);
        }
    };

    const handleGenerate = async () => {
        setGenerating(true);
        try {
            const response = await authFetch(`${API_URL}/copy-generation/generate`, {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify(wizardData)
            });

            if (!response.ok) {
                throw new Error(t('imageAds.copyGenerationFailed'));
            }

            const data = await response.json();
            setGeneratedCopy(data);
            // Move to copy selection step
            setCurrentStep(9);
        } catch (error) {
            console.error('Copy generation error:', error);
            showError(t('imageAds.failedToGenerateCopyPlease'));
        } finally {
            setGenerating(false);
        }
    };

    const handleImageGeneration = async (copy) => {
        setSelectedCopy(copy);
        setGenerating(true);
        try {
            const response = await authFetch(`${API_URL}/generated-ads/generate-image`, {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({
                    template: wizardData.template,
                    brand: wizardData.brand,
                    product: wizardData.product,
                    copy: copy,
                    count: wizardData.variationCount,
                    imageSizes: wizardData.imageSizes,
                    resolution: wizardData.resolution,
                    model: wizardData.model,
                    productShots: wizardData.useProductShots ? wizardData.product?.product_shots : [],
                    useProductImage: wizardData.useProductShots,
                    customPrompt: customImagePrompt
                })
            });

            if (!response.ok) {
                throw new Error(t('imageAds.imageGenerationFailed'));
            }

            const data = await response.json();
            console.log('📸 Image generation response:', data);

            // Generate a unique bundle ID for this set of images
            const bundleId = `bundle_${Date.now()}_${Math.random().toString(36).substr(2, 9)}`;

            // Add bundle ID to images
            const imagesWithBundle = (data.images || []).map(img => ({
                ...img,
                adBundleId: bundleId
            }));

            setGeneratedImages(imagesWithBundle);

            // Save generated ads to database
            try {
                const adsToSave = imagesWithBundle.map(img => ({
                    id: `ga_${Date.now()}_${Math.random().toString(36).substr(2, 9)}`,
                    brandId: wizardData.brand?.id,
                    productId: wizardData.product?.id,
                    templateId: wizardData.template?.id,
                    imageUrl: img.url,
                    headline: copy.headline,
                    body: copy.body,
                    cta: copy.cta,
                    sizeName: img.size,
                    dimensions: img.dimensions,
                    prompt: img.prompt,
                    adBundleId: img.adBundleId
                }));

                const saveResponse = await authFetch(`${API_URL}/generated-ads/batch`, {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify({ ads: adsToSave })
                });

                if (!saveResponse.ok) {
                    throw new Error(t('imageAds.batchSaveFailed', { status: saveResponse.statusText }));
                }

                console.log('✅ Saved generated ads to database with bundle ID:', bundleId);
            } catch (saveError) {
                console.error('Failed to save ads to database:', saveError);
                // Don't fail the whole operation if saving fails
            }

            setCurrentStep(10); // Move to image result step
        } catch (error) {
            console.error('Image generation error:', error);
            showError(t('imageAds.failedToGenerateImagesPlease'));
        } finally {
            setGenerating(false);
        }
    };

    return (
        <div className="max-w-5xl mx-auto">
            {/* Header */}
            <div className="mb-8">
                <h1 className="text-3xl font-bold text-gray-900 flex items-center gap-3">
                    <Image size={32} className="text-amber-600" />
                    {t('imageAds.createImageAds')}
                </h1>
                <p className="text-gray-600 mt-1">{t('imageAds.generateAiPoweredAdsUsing')}</p>
            </div>

            {/* Progress Steps */}
            <div className="mb-8 bg-white rounded-xl shadow-sm border border-gray-200 p-6">
                <div className="flex items-center justify-between relative">
                    <div className="absolute left-0 top-1/2 transform -translate-y-1/2 w-full h-1 bg-gray-200 -z-10"></div>
                    {steps.map((step, index) => {
                        const Icon = step.icon;
                        const isActive = step.id === currentStep;
                        const isCompleted = step.id < currentStep;

                        // Check if this step is clickable (all previous steps are complete)
                        let isClickable = true;
                        for (let i = 1; i < step.id; i++) {
                            if (!isStepComplete(i)) {
                                isClickable = false;
                                break;
                            }
                        }

                        return (
                            <div
                                key={step.id}
                                className={`flex flex-col items-center bg-white px-2 ${isClickable ? 'cursor-pointer group' : 'cursor-not-allowed opacity-60'}`}
                                onClick={() => isClickable && handleStepClick(step.id)}
                            >
                                <div
                                    className={`w-10 h-10 rounded-full flex items-center justify-center mb-2 transition-all ${isActive ? 'bg-amber-600 text-white scale-110 shadow-md' :
                                        isCompleted ? 'bg-green-500 text-white group-hover:bg-green-600' :
                                            'bg-gray-200 text-gray-500 group-hover:bg-gray-300'
                                        }`}
                                >
                                    {isCompleted ? <Check size={20} /> : <Icon size={20} />}
                                </div>
                                <span className={`text-xs font-medium transition-colors ${isActive ? 'text-amber-600' :
                                    isClickable ? 'text-gray-500 group-hover:text-gray-700' : 'text-gray-400'
                                    }`}>
                                    {step.name}
                                </span>
                            </div>
                        );
                    })}
                </div>
            </div>

            {/* Step Content */}
            <div className="bg-white rounded-xl shadow-sm border border-gray-200 p-8 min-h-[500px] relative">
                {/* Loading Overlay */}
                {generating && (
                    <div className="absolute inset-0 bg-white/80 backdrop-blur-sm z-50 flex flex-col items-center justify-center rounded-xl">
                        <div className="w-16 h-16 border-4 border-amber-200 border-t-amber-600 rounded-full animate-spin mb-4"></div>
                        <h3 className="text-xl font-bold text-gray-900">
                            {currentStep === 9 ? t('imageAds.generatingHighConvertingImages') : t('imageAds.generatingAdCopy')}
                        </h3>
                        <p className="text-gray-500 mt-2">{t('imageAds.usingAiToCreateYour')}</p>
                    </div>
                )}

                {/* Step 1: Brand Selection */}
                {currentStep === 1 && (
                    <BrandSelectionStep
                        brands={brands}
                        selectedBrand={wizardData.brand}
                        onSelect={(brand) => {
                            updateData('brand', brand);
                            nextStep();
                        }}
                    />
                )}

                {/* Step 2: Product Selection */}
                {currentStep === 2 && (
                    <ProductSelectionStep
                        products={wizardData.brand?.products || []}
                        selectedProduct={wizardData.product}
                        useProductShots={wizardData.useProductShots}
                        onSelect={(product) => {
                            updateData('product', product);
                            // Reset product shots preference when changing product
                            updateData('useProductShots', false);
                            nextStep();
                        }}
                        onToggleProductShots={(use) => updateData('useProductShots', use)}
                    />
                )}

                {/* Step 3: Profile Selection */}
                {currentStep === 3 && (
                    <ProfileSelectionStep
                        profiles={customerProfiles.filter(p => wizardData.brand?.profileIds?.includes(p.id))}
                        selectedProfile={wizardData.profile}
                        onSelect={(profile) => {
                            updateData('profile', profile);
                            nextStep();
                        }}
                    />
                )}

                {/* Step 4: Template/Style Selection */}
                {currentStep === 4 && (
                    <div>
                        <h3 className="text-xl font-bold mb-4">{t('imageAds.selectATemplateOrStyle')}</h3>
                        <p className="text-gray-600 mb-6">
                            {t('imageAds.chooseAProvenAdStyle')}
                        </p>

                        {/* Mode Toggle */}
                        <div className="flex gap-2 mb-6 bg-gray-100 p-1 rounded-lg w-fit">
                            <button
                                onClick={() => setTemplateMode('style')}
                                className={`px-6 py-2 rounded-md font-medium transition-all ${templateMode === 'style'
                                    ? 'bg-white text-amber-600 shadow-sm'
                                    : 'text-gray-600 hover:text-gray-900'
                                    }`}
                            >
                                <div className="flex items-center gap-2">
                                    <Sparkles size={18} />
                                    {t('imageAds.browseStyles')}
                                </div>
                            </button>
                            <button
                                onClick={() => setTemplateMode('template')}
                                className={`px-6 py-2 rounded-md font-medium transition-all ${templateMode === 'template'
                                    ? 'bg-white text-amber-600 shadow-sm'
                                    : 'text-gray-600 hover:text-gray-900'
                                    }`}
                            >
                                <div className="flex items-center gap-2">
                                    <Image size={18} />
                                    {t('imageAds.browseTemplates')}
                                </div>
                            </button>
                        </div>

                        {/* Conditional Rendering */}
                        {templateMode === 'style' ? (
                            <StyleSelector
                                onSelect={(style) => {
                                    updateData('template', {
                                        type: 'style',
                                        ...style
                                    });
                                    nextStep();
                                }}
                            />
                        ) : (
                            <ImageTemplateSelector
                                onSelect={(template) => {
                                    updateData('template', {
                                        type: 'template',
                                        ...template
                                    });
                                    nextStep();
                                }}
                                onClose={() => { }}
                                embedded={true}
                            />
                        )}
                    </div>
                )}

                {/* Step 5: Variation Count */}
                {currentStep === 5 && (
                    <VariationCountStep
                        count={wizardData.variationCount}
                        onChange={(count) => updateData('variationCount', count)}
                    />
                )}

                {/* Step 6: Image Size Selection */}
                {currentStep === 6 && (
                    <ImageSizeStep
                        selectedSizes={wizardData.imageSizes}
                        onSelect={(sizes) => updateData('imageSizes', sizes)}
                        resolution={wizardData.resolution}
                        onResolutionChange={(res) => updateData('resolution', res)}
                        model={wizardData.model}
                        onModelChange={(m) => updateData('model', m)}
                    />
                )}

                {/* Step 7: Campaign Details */}
                {currentStep === 7 && (
                    <CampaignDetailsStep
                        details={wizardData.campaignDetails}
                        onChange={updateCampaignDetails}
                    />
                )}

                {/* Step 8: Review */}
                {currentStep === 8 && (
                    <ReviewStep wizardData={wizardData} />
                )}

                {/* Step 9: Copy Selection (after generation) */}
                {currentStep === 9 && generatedCopy && (
                    <CopySelectionStep
                        generatedCopy={generatedCopy}
                        wizardData={wizardData}
                        onBack={() => setCurrentStep(8)}
                        onRegenerate={handleGenerate}
                        isRegenerating={generating}
                        onProceed={handleImageGeneration}
                        customImagePrompt={customImagePrompt}
                        setCustomImagePrompt={setCustomImagePrompt}
                        authFetch={authFetch}
                    />
                )}

                {/* Step 10: Image Generation Result */}
                {currentStep === 10 && (
                    generatedImages.length > 0 ? (
                        <ImageGenerationStep
                            generatedImages={generatedImages}
                            wizardData={wizardData}
                            selectedCopy={selectedCopy}
                            onBack={() => setCurrentStep(9)}
                            onRestart={() => window.location.reload()}
                        />
                    ) : (
                        <div className="text-center py-12">
                            <div className="w-16 h-16 bg-red-100 text-red-600 rounded-full flex items-center justify-center mx-auto mb-4">
                                <FileText size={32} />
                            </div>
                            <h3 className="text-xl font-bold text-gray-900 mb-2">{t('imageAds.generationError')}</h3>
                            <p className="text-gray-600 mb-6">{t('imageAds.somethingWentWrongDisplayingThe')}</p>
                            <button
                                onClick={() => setCurrentStep(9)}
                                className="px-6 py-3 bg-gray-200 text-gray-700 rounded-lg hover:bg-gray-300 font-medium transition-colors"
                            >
                                {t('imageAds.goBack')}
                            </button>
                        </div>
                    )
                )}
            </div>

            {/* Footer Actions */}
            {currentStep <= 8 && (
                <div className="mt-6 flex items-center justify-between">
                    <button
                        onClick={prevStep}
                        disabled={currentStep === 1}
                        className={`flex items-center gap-2 px-6 py-3 rounded-lg font-medium transition-colors ${currentStep === 1
                            ? 'bg-gray-100 text-gray-400 cursor-not-allowed'
                            : 'bg-gray-100 text-gray-700 hover:bg-gray-200'
                            }`}
                    >
                        <ChevronLeft size={20} />
                        {t('imageAds.back')}
                    </button>

                    {currentStep === 8 ? (
                        <button
                            onClick={handleGenerate}
                            disabled={!canProceed() || generating}
                            className="flex items-center gap-2 px-8 py-3 bg-amber-600 text-white rounded-lg hover:bg-amber-700 font-medium shadow-lg disabled:opacity-50 disabled:cursor-not-allowed transition-colors"
                        >
                            <Sparkles size={20} className={generating ? 'animate-spin' : ''} />
                            {generating ? t('imageAds.generatingMagic') : t('imageAds.generateAdCopy')}
                        </button>
                    ) : (
                        <button
                            onClick={nextStep}
                            disabled={!canProceed()}
                            className="flex items-center gap-2 px-6 py-3 bg-amber-600 text-white rounded-lg hover:bg-amber-700 font-medium shadow-lg disabled:opacity-50 disabled:cursor-not-allowed transition-colors"
                        >
                            {t('imageAds.continue')}
                                            <ChevronRight size={20} />
                        </button>
                    )}
                </div>
            )}
        </div>
    );
}

// Step Components


function VariationCountStep({ count, onChange }) {
    const { t } = useTranslation('creatives');
    return (
        <div>
            <h3 className="text-xl font-bold mb-4">{t('imageAds.howManyVariations')}</h3>
            <p className="text-gray-600 mb-6">{t('imageAds.chooseHowManyAdVariations')}</p>

            <div className="max-w-md mx-auto">
                <div className="flex items-center gap-4 mb-6">
                    <input
                        type="range"
                        min="1"
                        max="10"
                        value={count}
                        onChange={(e) => onChange(parseInt(e.target.value))}
                        className="flex-1 h-2 bg-gray-200 rounded-lg appearance-none cursor-pointer accent-amber-600"
                    />
                    <div className="w-16 h-16 rounded-full bg-amber-600 text-white flex items-center justify-center text-2xl font-bold">
                        {count}
                    </div>
                </div>

                <div className="bg-amber-50 border border-amber-200 rounded-lg p-4">
                    <p className="text-sm text-amber-800">
                        <strong>{t('imageAds.tip')}</strong> {t('imageAds.moreVariationsGiveYouMore')}
                    </p>
                </div>
            </div>
        </div>
    );
}

function ImageSizeStep({ selectedSizes = [], onSelect, resolution, onResolutionChange, model, onModelChange }) {
    const { t } = useTranslation('creatives');
    const [showAdvanced, setShowAdvanced] = useState(false);

    const sizeOptions = [
        {
            name: 'Square (Feed/Carousel)',
            width: 1080,
            height: 1080,
            aspectRatio: '1:1',
            description: 'Perfect for Facebook Feed and Carousel ads',
            icon: '⬜',
            required: true // Square is always required
        },
        {
            name: 'Vertical (Feed)',
            width: 1080,
            height: 1350,
            aspectRatio: '4:5',
            description: 'Optimized for mobile Facebook Feed',
            icon: '📱',
            required: false
        },
        {
            name: 'Story',
            width: 1080,
            height: 1920,
            aspectRatio: '9:16',
            description: 'Full-screen Facebook and Instagram Stories',
            icon: '📲',
            required: false
        }
    ];

    const toggleSize = (size) => {
        // Prevent unselecting Square (required)
        if (size.required) {
            return;
        }

        const isSelected = selectedSizes.some(s => s.name === size.name);
        if (isSelected) {
            onSelect(selectedSizes.filter(s => s.name !== size.name));
        } else {
            if (selectedSizes.length < 3) {
                onSelect([...selectedSizes, size]);
            }
        }
    };

    return (
        <div>
            <h3 className="text-xl font-bold mb-4">{t('imageAds.selectImageSizesQuality')}</h3>
            <p className="text-gray-600 mb-2">{t('imageAds.squareIsRequiredSelectUp')}</p>
            <p className="text-sm text-amber-600 font-medium mb-6">{t('imageAds.sizesSelected', { count: selectedSizes.length })}</p>

            <div className="grid grid-cols-1 md:grid-cols-3 gap-4 max-w-4xl mx-auto mb-8">
                {sizeOptions.map((size) => {
                    const isSelected = selectedSizes.some(s => s.name === size.name);
                    const isDisabled = !isSelected && selectedSizes.length >= 3;
                    const isRequired = size.required;

                    return (
                        <div
                            key={size.name}
                            onClick={() => !isDisabled && toggleSize(size)}
                            className={`p-6 rounded-xl border-2 transition-all relative ${isRequired
                                ? 'border-amber-600 bg-amber-50 shadow-lg cursor-default'
                                : isSelected
                                    ? 'border-amber-600 bg-amber-50 shadow-lg cursor-pointer'
                                    : isDisabled
                                        ? 'border-gray-200 opacity-50 cursor-not-allowed'
                                        : 'border-gray-200 hover:border-amber-300 hover:shadow-md cursor-pointer'
                                }`}
                        >
                            {/* Required Badge or Checkbox */}
                            {isRequired ? (
                                <div className="absolute top-3 right-3 px-2 py-1 bg-amber-600 text-white text-xs font-bold rounded">
                                    {t('imageAds.required')}
                                </div>
                            ) : (
                                <div className={`absolute top-3 right-3 w-6 h-6 rounded border-2 flex items-center justify-center transition-colors ${isSelected ? 'bg-amber-600 border-amber-600' : 'bg-white border-gray-400'
                                    }`}>
                                    {isSelected && <Check size={14} className="text-white" />}
                                </div>
                            )}

                            <div className="text-4xl mb-3 text-center">{size.icon}</div>
                            <h4 className="font-bold text-gray-900 mb-2 text-center">{sizeLabel(size.name)}</h4>
                            <p className="text-sm text-gray-600 mb-3 text-center">{sizeDescription(size)}</p>
                            <div className="bg-white rounded-lg p-3 border border-gray-200">
                                <div className="flex justify-between text-xs text-gray-500 mb-1">
                                    <span>{t('imageAds.dimensions')}</span>
                                    <span className="font-medium text-gray-900">{size.width}×{size.height}</span>
                                </div>
                                <div className="flex justify-between text-xs text-gray-500">
                                    <span>{t('imageAds.aspectRatio')}</span>
                                    <span className="font-medium text-gray-900">{size.aspectRatio}</span>
                                </div>
                            </div>
                        </div>
                    );
                })}
            </div>

            {/* Advanced Settings Accordion */}
            <div className="max-w-4xl mx-auto mb-8">
                <button
                    onClick={() => setShowAdvanced(!showAdvanced)}
                    className="flex items-center gap-2 text-gray-600 font-medium hover:text-amber-600 transition-colors mb-4"
                >
                    <Settings size={18} />
                    <span>{t('imageAds.advancedSettingsQualityModel')}</span>
                    {showAdvanced ? <ChevronUp size={18} /> : <ChevronDown size={18} />}
                </button>

                {showAdvanced && (
                    <div className="space-y-8 animate-in fade-in slide-in-from-top-4 duration-300">
                        {/* Resolution Selector */}
                        <div className="bg-white rounded-xl shadow-sm border border-gray-200 p-6">
                            <div className="flex items-center gap-2 mb-4">
                                <Sparkles className="text-amber-600" size={20} />
                                <h4 className="font-bold text-gray-900">{t('imageAds.imageQuality')}</h4>
                            </div>
                            <p className="text-sm text-gray-600 mb-4">{t('imageAds.selectTheResolutionForYour')}</p>

                            <div className="grid grid-cols-3 gap-3">
                                {['1K', '2K', '4K'].map((res) => (
                                    <button
                                        key={res}
                                        onClick={() => onResolutionChange(res)}
                                        className={`p-4 rounded-lg border-2 transition-all ${resolution === res
                                            ? 'border-amber-600 bg-amber-50 text-amber-900'
                                            : 'border-gray-200 hover:border-amber-300 text-gray-700'
                                            }`}
                                    >
                                        <div className="font-bold text-lg">{res}</div>
                                        <div className="text-xs mt-1">
                                            {res === '1K' && t('imageAds.res1k')}
                                            {res === '2K' && t('imageAds.res2k')}
                                            {res === '4K' && t('imageAds.res4k')}
                                        </div>
                                    </button>
                                ))}
                            </div>

                            <div className="mt-4 bg-amber-50 border border-amber-200 rounded-lg p-3">
                                <p className="text-sm text-amber-800">
                                    <strong>{t('imageAds.tip')}</strong> {t('imageAds.higherResolutionsProduceBetterQuality')}
                                </p>
                            </div>
                        </div>

                        {/* Model Selection */}
                        <div className="bg-white rounded-xl shadow-sm border border-gray-200 p-6">
                            <h3 className="text-xl font-bold mb-4">{t('imageAds.selectAiModelPrimaryImage')}</h3>
                            <p className="text-gray-600 mb-4">{t('imageAds.chooseTheModelUsedTo')}</p>

                            <div className="grid grid-cols-1 md:grid-cols-2 gap-4">
                                <div
                                    onClick={() => onModelChange('nano-banana-pro')}
                                    className={`p-4 rounded-lg border-2 cursor-pointer transition-all ${model === 'nano-banana-pro'
                                        ? 'border-amber-600 bg-amber-50'
                                        : 'border-gray-200 hover:border-amber-300'
                                        }`}
                                >
                                    <div className="flex items-center justify-between mb-2">
                                        <span className="font-bold text-lg">Nano Banana Pro</span>
                                        {model === 'nano-banana-pro' && <Check className="text-amber-600" size={20} />}
                                    </div>
                                    <p className="text-sm text-gray-600">{t('imageAds.fastEfficientAndGreatFor')}</p>
                                </div>

                                <div
                                    onClick={() => onModelChange('imagen4')}
                                    className={`p-4 rounded-lg border-2 cursor-pointer transition-all ${model === 'imagen4'
                                        ? 'border-amber-600 bg-amber-50'
                                        : 'border-gray-200 hover:border-amber-300'
                                        }`}
                                >
                                    <div className="flex items-center justify-between mb-2">
                                        <span className="font-bold text-lg">Google Imagen 3</span>
                                        {model === 'imagen4' && <Check className="text-amber-600" size={20} />}
                                    </div>
                                    <p className="text-sm text-gray-600">{t('imageAds.highFidelityPhotorealisticQualitySlower')}</p>
                                </div>
                            </div>
                        </div>
                    </div>
                )}
            </div>
        </div>
    );
}

function CampaignDetailsStep({ details, onChange }) {
    const { t } = useTranslation('creatives');
    const offerInputRef = React.useRef(null);

    React.useEffect(() => {
        if (offerInputRef.current) {
            offerInputRef.current.focus();
        }
    }, []);

    return (
        <div>
            <h3 className="text-xl font-bold mb-4">{t('imageAds.campaignDetails')}</h3>
            <p className="text-gray-600 mb-6">{t('imageAds.provideDetailsToCustomizeYour')}</p>

            <div className="max-w-2xl space-y-4">
                <div>
                    <label className="block text-sm font-medium text-gray-700 mb-2">
                        {t('imageAds.offerPromotion')}
                    </label>
                    <input
                        ref={offerInputRef}
                        type="text"
                        value={details.offer}
                        onChange={(e) => onChange('offer', e.target.value)}
                        placeholder={t('imageAds.eG50OffBlack')}
                        className="w-full px-4 py-3 border border-gray-300 rounded-lg focus:ring-2 focus:ring-amber-500 focus:border-transparent"
                    />
                </div>

                <div>
                    <label className="block text-sm font-medium text-gray-700 mb-2">
                        {t('imageAds.urgencyTiming')}
                    </label>
                    <input
                        type="text"
                        value={details.urgency}
                        onChange={(e) => onChange('urgency', e.target.value)}
                        placeholder={t('imageAds.eGLimitedTimeEnds')}
                        className="w-full px-4 py-3 border border-gray-300 rounded-lg focus:ring-2 focus:ring-amber-500 focus:border-transparent"
                    />
                </div>

                <div>
                    <label className="block text-sm font-medium text-gray-700 mb-2">
                        {t('imageAds.keyMessaging')}
                    </label>
                    <textarea
                        value={details.messaging}
                        onChange={(e) => onChange('messaging', e.target.value)}
                        placeholder={t('imageAds.eGScienceBackedResults')}
                        rows={3}
                        className="w-full px-4 py-3 border border-gray-300 rounded-lg focus:ring-2 focus:ring-amber-500 focus:border-transparent"
                    />
                </div>

                <div>
                    <label className="block text-sm font-medium text-gray-700 mb-2">
                        {t('imageAds.visualAngleOptional')}
                    </label>
                    <input
                        type="text"
                        value={details.angle || ''}
                        onChange={(e) => onChange('angle', e.target.value)}
                        placeholder={t('imageAds.eGLowAngleHero')}
                        className="w-full px-4 py-3 border border-gray-300 rounded-lg focus:ring-2 focus:ring-amber-500 focus:border-transparent"
                    />
                    <p className="text-xs text-gray-500 mt-1">
                        {t('imageAds.overridesTheTemplateSDefault')}
                    </p>
                </div>
            </div>
        </div>
    );
}

function ReviewStep({ wizardData }) {
    const { t } = useTranslation('creatives');
    const [showPrompt, setShowPrompt] = useState(false);
    const [customPrompt, setCustomPrompt] = useState('');

    // Build the default prompt
    const buildPrompt = () => {
        const count = wizardData.variationCount || 3;
        return `You are an expert ad copywriter. Generate ${count} variations of ad copy for a Facebook/Instagram ad campaign.

BRAND VOICE: ${wizardData.brand?.voice || 'Professional and friendly'}

PRODUCT: ${wizardData.product?.name}
${wizardData.product?.description ? `Description: ${wizardData.product.description}` : ''}

TARGET AUDIENCE:
- Demographics: ${wizardData.profile?.demographics || 'General audience'}
- Pain Points: ${wizardData.profile?.painPoints || wizardData.profile?.pain_points || 'Common challenges'}
- Goals: ${wizardData.profile?.goals || 'Desired outcomes'}

CAMPAIGN DETAILS:
- Offer: ${wizardData.campaignDetails.offer}
${wizardData.campaignDetails.urgency ? `- Urgency: ${wizardData.campaignDetails.urgency}` : ''}
- Key Messaging: ${wizardData.campaignDetails.messaging}

BODY COPY STYLES (vary across variations):
1. BULLET POINTS WITH EMOJIS: Use 2-4 bullet points with emojis at the start
   - Sometimes use the same emoji (e.g., ✓ ✓ ✓ or ⭐ ⭐ ⭐)
   - Sometimes use mixed emojis (e.g., 🎯 💪 ✨ 🚀)
   - Keep each bullet concise and benefit-focused
   Example: "✓ Save 50% today\n✓ Free shipping\n✓ 30-day guarantee"

2. EMOTIONAL STORYTELLING: Longer narrative that connects emotionally
   - Tell a relatable story or paint a vivid picture
   - Use emotional triggers and sensory details
   - Build desire and urgency through narrative
   - Can be 150-200 characters for story-driven ads
   Example: "Remember that feeling when everything just clicks? When you finally found the solution you've been searching for? That's what our customers experience every day..."

INSTRUCTIONS:
Generate ${count} distinct variations. Mix both body copy styles across variations. Each variation should:
1. Match the brand voice consistently
2. Address the audience's pain points and goals
3. Incorporate the campaign offer and key messaging
4. Be compelling, conversion-focused, and ad-appropriate
5. Keep headlines under 40 characters
6. For bullet-point style: Keep body under 125 characters
7. For storytelling style: Can extend to 200 characters
8. Keep CTAs under 20 characters

Return ONLY valid JSON in this exact format:
{
  "variations": [
    {
      "headline": "Short, punchy headline",
      "body": "Compelling body copy (bullets with emojis OR emotional story)",
      "cta": "Action CTA"
    }
  ]
}`;
    };

    // Initialize prompt when component mounts or data changes
    React.useEffect(() => {
        if (!customPrompt) {
            setCustomPrompt(buildPrompt());
        }
    }, [wizardData]);

    return (
        <div>
            <h3 className="text-xl font-bold mb-4">{t('imageAds.reviewYourSelections')}</h3>
            <p className="text-gray-600 mb-6">{t('imageAds.verifyEverythingLooksCorrectBefore')}</p>

            <div className="grid grid-cols-2 gap-3 max-w-3xl">
                <ReviewItem
                    label={t('imageAds.brand2')}
                    value={wizardData.brand?.name}
                    icon={Briefcase}
                />
                <ReviewItem
                    label={t('imageAds.product2')}
                    value={wizardData.product?.name}
                    icon={Package}
                />
                <ReviewItem
                    label={t('imageAds.targetAudience')}
                    value={wizardData.profile?.name}
                    icon={Users}
                />
                <ReviewItem
                    label={t('imageAds.template2')}
                    value={wizardData.template?.name || wizardData.template?.template_category}
                    icon={Image}
                />
                <ReviewItem
                    label={t('imageAds.variations')}
                    value={t('imageAds.adsCount', { count: wizardData.variationCount })}
                    icon={Hash}
                />
                <ReviewItem
                    label={t('imageAds.imageSizes')}
                    value={t('imageAds.sizesCount', { count: wizardData.imageSizes.length })}
                    icon={Image}
                />
                <ReviewItem
                    label={t('imageAds.offer')}
                    value={wizardData.campaignDetails.offer}
                    icon={FileText}
                />
                <ReviewItem
                    label={t('imageAds.messaging')}
                    value={wizardData.campaignDetails.messaging}
                    icon={FileText}
                />
            </div>

            {/* AI Prompt Accordion */}
            <div className="mt-6 max-w-3xl bg-white rounded-xl shadow-sm border border-gray-200">
                <button
                    onClick={() => setShowPrompt(!showPrompt)}
                    className="w-full p-4 flex items-center justify-between hover:bg-gray-50 transition-colors rounded-xl"
                >
                    <div className="flex items-center gap-2">
                        <Sparkles className="text-amber-600" size={18} />
                        <h4 className="font-bold text-gray-900 text-sm">{t('imageAds.aiCopyGenerationPrompt')}</h4>
                        <span className="text-xs text-gray-500">{t('imageAds.advanced')}</span>
                    </div>
                    <ChevronRight
                        className={`text-gray-400 transition-transform ${showPrompt ? 'rotate-90' : ''}`}
                        size={18}
                    />
                </button>

                {showPrompt && (
                    <div className="px-4 pb-4 border-t border-gray-100">
                        <p className="text-xs text-gray-600 mb-3 mt-3">{t('imageAds.customizeTheAiPromptUsed')}</p>
                        <textarea
                            value={customPrompt}
                            onChange={(e) => setCustomPrompt(e.target.value)}
                            className="w-full h-64 p-3 text-xs font-mono border border-gray-300 rounded-lg focus:ring-2 focus:ring-amber-500 focus:border-transparent resize-none"
                            placeholder={t('imageAds.enterYourCustomPrompt')}
                        />
                        <div className="flex items-center justify-between mt-2">
                            <button
                                onClick={() => setCustomPrompt(buildPrompt())}
                                className="text-xs text-amber-600 hover:text-amber-700 font-medium"
                            >
                                {t('imageAds.resetToDefault')}
                            </button>
                            <span className="text-xs text-gray-500">{t('imageAds.characters', { count: customPrompt.length })}</span>
                        </div>
                    </div>
                )}
            </div>

            <div className="mt-6 bg-gradient-to-r from-amber-50 to-orange-50 border border-amber-200 rounded-lg p-4 max-w-3xl">
                <p className="text-sm text-amber-900">
                    <strong>{t('imageAds.readyToGenerate')}</strong> {t('imageAds.readyToGenerateBody', { count: wizardData.variationCount })}
                </p>
            </div>
        </div>
    );
}

function ReviewItem({ label, value, icon: Icon }) {
    return (
        <div className="flex items-center gap-2 p-3 bg-gray-50 rounded-lg">
            <Icon className="text-amber-600 flex-shrink-0" size={18} />
            <div className="min-w-0">
                <div className="text-xs font-medium text-gray-500">{label}</div>
                <div className="text-sm text-gray-900 truncate">{value}</div>
            </div>
        </div>
    );
}

function CopySelectionStep({ generatedCopy, wizardData, onBack, onRegenerate, isRegenerating, onProceed, customImagePrompt, setCustomImagePrompt, authFetch }) {
    const { t } = useTranslation('creatives');
    const [selectedIndex, setSelectedIndex] = useState(0);
    const [editedCopy, setEditedCopy] = useState(null);
    const [regeneratingField, setRegeneratingField] = useState(null);
    const [showPrompt, setShowPrompt] = useState(false);

    // Build comprehensive prompt (matching backend logic)
    const buildComprehensivePrompt = () => {
        const currentCopy = editedCopy || generatedCopy.variations[selectedIndex];

        // Extract all context
        const productName = wizardData.product?.name || 'Product';
        const productDesc = wizardData.product?.description || '';
        const brandName = wizardData.brand?.name || '';
        const brandVoice = wizardData.brand?.voice || 'Professional';
        const brandColor = wizardData.brand?.colors?.primary || '';

        // Get template metadata
        const templateType = wizardData.template?.type;
        let mood, lighting, composition, designStyle;

        if (templateType === 'style') {
            // Style archetype - has metadata fields
            mood = wizardData.template?.mood || 'Engaging';
            lighting = wizardData.template?.lighting || 'Professional lighting';
            composition = wizardData.template?.composition || 'Balanced';
            designStyle = wizardData.template?.design_style || 'Modern';
        } else {
            // Regular template
            mood = wizardData.template?.mood || 'Engaging';
            lighting = wizardData.template?.lighting || 'Professional lighting';
            composition = wizardData.template?.composition || 'Balanced';
            designStyle = wizardData.template?.design_style || 'Modern';
        }

        // Build comprehensive prompt with Markdown-style sections
        const sections = [];

        // 1. SUBJECT MATTER & COMPOSITION (Priority)
        const subjectSection = [];
        const userAngle = wizardData.campaignDetails?.angle;

        if (userAngle) {
            subjectSection.push(`**Subject Matter:** ${userAngle}`);
        } else {
            const template = wizardData.template;
            if (template) {
                // Combine template subject details
                const details = [];
                if (template.subject_matter) details.push(template.subject_matter);
                if (template.subject) details.push(template.subject);
                if (template.topHalf && template.bottomHalf) details.push(`Top: ${template.topHalf}, Bottom: ${template.bottomHalf}`);
                if (template.leftSide && template.rightSide) details.push(`Left: ${template.leftSide}, Right: ${template.rightSide}`);
                if (template.visualLayout) details.push(`Layout: ${template.visualLayout}`);

                if (details.length > 0) subjectSection.push(`**Subject Matter:** ${details.join('. ')}`);

                // Visual Elements
                const visualElements = [];
                if (template.visual_elements) {
                    const elements = typeof template.visual_elements === 'string' ? template.visual_elements :
                        Array.isArray(template.visual_elements) ? template.visual_elements.join(', ') :
                            JSON.stringify(template.visual_elements);
                    visualElements.push(elements);
                }
                if (template.visualAnchors) {
                    visualElements.push(Array.isArray(template.visualAnchors) ? template.visualAnchors.join(', ') : template.visualAnchors);
                }
                if (template.overlays) {
                    visualElements.push(`Overlays: ${Array.isArray(template.overlays) ? template.overlays.join(', ') : template.overlays}`);
                }

                if (visualElements.length > 0) subjectSection.push(`**Visual Elements:** ${visualElements.join('. ')}`);

                // Special handling for iPhone Note style to prevent overcrowding
                if (template.id === 'iphone-note') {
                    subjectSection.push(`**Constraint:** KEEP TEXT SHORT. Maximum 15 words total. Use short, punchy bullet points for legibility.`);
                }
            }
        }

        if (composition) subjectSection.push(`**Composition:** ${composition}`);
        if (subjectSection.length > 0) sections.push(subjectSection.join('\n'));

        // 2. PRODUCT & BRAND
        const productBrandSection = [];
        productBrandSection.push(`**Product:** ${productName}${productDesc ? ` - ${productDesc}` : ''}`);
        productBrandSection.push(`**Brand:** ${brandName ? `${brandName} (${brandVoice})` : brandVoice}`);
        if (brandColor) productBrandSection.push(`**Primary Color:** ${brandColor}`);
        sections.push(productBrandSection.join('\n'));

        // 3. CONTEXT & COPY
        if (currentCopy?.headline) {
            if (wizardData.template?.id === 'iphone-note') {
                const truncatedHeadline = currentCopy.headline.split(' ').slice(0, 10).join(' ');
                sections.push(`**Context:** Visual representation of a handwritten note based on: "${truncatedHeadline}".
**IMPORTANT:** SUMMARIZE text to under 10 words for legibility.`);
            } else {
                sections.push(`**Context:** Visual representation of "${currentCopy.headline}"`);
            }
        }

        // 4. ART DIRECTION & STYLE
        sections.push(`**Art Direction:**
Mood: ${mood}
Lighting: ${lighting}
Style: ${designStyle}`);

        // 5. QUALITY STANDARDS
        sections.push(`**Quality:** High quality, photorealistic, 4k, advertising standard`);

        // Join with double newlines for clear separation
        return sections.join('\n\n');
    };

    // Auto-populate prompt on mount if empty
    React.useEffect(() => {
        if (!customImagePrompt) {
            setCustomImagePrompt(buildComprehensivePrompt());
        }
    }, []);

    const handleResetPrompt = () => {
        setCustomImagePrompt(buildComprehensivePrompt());
    };

    const currentCopy = editedCopy || generatedCopy.variations[selectedIndex];

    const handleEdit = (field, value) => {
        setEditedCopy({
            ...currentCopy,
            [field]: value
        });
    };

    const handleRegenerateField = async (field) => {
        setRegeneratingField(field);
        try {
            const response = await authFetch(`${API_URL}/copy-generation/regenerate-field`, {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({
                    field,
                    currentValue: currentCopy[field],
                    brand: wizardData.brand,
                    product: wizardData.product,
                    profile: wizardData.profile,
                    template: wizardData.template,
                    campaignDetails: wizardData.campaignDetails
                })
            });

            if (!response.ok) throw new Error(t('imageAds.regenerationFailed'));

            const data = await response.json();
            handleEdit(field, data.newValue);
        } catch (error) {
            console.error('Regeneration error:', error);
        } finally {
            setRegeneratingField(null);
        }
    };

    const handleProceed = async () => {
        // Save selected copy to wizardData or state if needed
        // For now we just pass it to the next step
        onProceed(currentCopy);
    };

    const RegenerateButton = ({ field }) => (
        <button
            onClick={() => handleRegenerateField(field)}
            disabled={regeneratingField === field}
            className="ml-2 p-1 text-gray-400 hover:text-amber-600 hover:bg-amber-50 rounded-full transition-colors"
            title={t('imageAds.regenerateThisField')}
        >
            <Sparkles size={14} className={regeneratingField === field ? 'animate-spin text-amber-600' : ''} />
        </button>
    );

    return (
        <div>
            <h3 className="text-xl font-bold mb-2">{t('imageAds.generatedAdCopy')}</h3>
            <p className="text-gray-600 mb-6">
                {t('imageAds.selectVariationHint', { count: generatedCopy.variations.length })}
            </p>

            {/* Variation Selector */}
            <div className="flex gap-2 mb-6 overflow-x-auto pb-2">
                {generatedCopy.variations.map((_, index) => (
                    <button
                        key={index}
                        onClick={() => {
                            setSelectedIndex(index);
                            setEditedCopy(null);
                        }}
                        className={`px-4 py-2 rounded-lg font-medium whitespace-nowrap transition-all ${selectedIndex === index
                            ? 'bg-amber-600 text-white'
                            : 'bg-gray-100 text-gray-700 hover:bg-gray-200'
                            }`}
                    >
                        {t('imageAds.variationN', { index: index + 1 })}
                    </button>
                ))}
            </div>

            {/* Copy Preview and Edit */}
            <div className="grid grid-cols-1 lg:grid-cols-2 gap-6">
                {/* Edit Form */}
                <div className="space-y-4">
                    <div>
                        <label className="block text-sm font-medium text-gray-700 mb-2 flex items-center">
                            {t('imageAds.headline')}
                            <RegenerateButton field="headline" />
                        </label>
                        <input
                            type="text"
                            value={currentCopy.headline}
                            onChange={(e) => handleEdit('headline', e.target.value)}
                            className="w-full px-4 py-3 border border-gray-300 rounded-lg focus:ring-2 focus:ring-amber-500 focus:border-transparent"
                            placeholder={t('imageAds.enterHeadline')}
                        />
                        <div className="text-xs text-gray-500 mt-1">
                            {currentCopy.headline.length} / 40 characters
                        </div>
                    </div>

                    <div>
                        <label className="block text-sm font-medium text-gray-700 mb-2 flex items-center">
                            {t('imageAds.bodyCopy')}
                            <RegenerateButton field="body" />
                        </label>
                        <textarea
                            value={currentCopy.body}
                            onChange={(e) => handleEdit('body', e.target.value)}
                            rows={3}
                            className="w-full px-4 py-3 border border-gray-300 rounded-lg focus:ring-2 focus:ring-amber-500 focus:border-transparent"
                            placeholder={t('imageAds.enterBodyCopy')}
                        />
                        <div className="text-xs text-gray-500 mt-1">
                            {t('imageAds.bodyLengthHint', { count: currentCopy.body.length })}
                        </div>
                    </div>

                    <div>
                        <label className="block text-sm font-medium text-gray-700 mb-2 flex items-center">
                            {t('imageAds.callToAction')}
                            <RegenerateButton field="cta" />
                        </label>
                        <input
                            type="text"
                            value={currentCopy.cta}
                            onChange={(e) => handleEdit('cta', e.target.value)}
                            className="w-full px-4 py-3 border border-gray-300 rounded-lg focus:ring-2 focus:ring-amber-500 focus:border-transparent"
                            placeholder={t('imageAds.enterCta')}
                        />
                        <div className="text-xs text-gray-500 mt-1">
                            {currentCopy.cta.length} / 20 characters
                        </div>
                    </div>
                </div>

                {/* Preview */}
                <div>
                    <div className="bg-gradient-to-br from-amber-50 to-orange-50 border-2 border-amber-200 rounded-xl p-6">
                        <div className="text-xs font-medium text-amber-600 mb-3">{t('imageAds.preview')}</div>
                        <div className="bg-white rounded-lg p-4 shadow-sm">
                            <div className="font-bold text-lg text-gray-900 mb-2">
                                {currentCopy.headline}
                            </div>
                            <div className="text-gray-700 text-sm mb-4">
                                {currentCopy.body}
                            </div>
                            <button className="w-full bg-amber-600 text-white font-medium py-2 px-4 rounded-lg">
                                {currentCopy.cta}
                            </button>
                        </div>
                        <div className="mt-4 text-xs text-gray-600 space-y-1">
                            <div><strong>{t('imageAds.brand')}</strong> {wizardData.brand.name}</div>
                            <div><strong>{t('imageAds.product')}</strong> {wizardData.product.name}</div>
                            <div><strong>{t('imageAds.template')}</strong> {wizardData.template?.name || 'Custom'}</div>
                        </div>
                    </div>
                </div>
            </div>

            {/* Custom Image Prompt */}
            <div className="mt-8 bg-white rounded-xl shadow-sm border border-gray-200 overflow-hidden">
                <button
                    onClick={() => setShowPrompt(!showPrompt)}
                    className="w-full p-4 bg-gray-50 border-b border-gray-200 flex items-center justify-between hover:bg-gray-100 transition-colors"
                >
                    <div className="flex items-center gap-2">
                        <Sparkles className="text-amber-600" size={18} />
                        <h4 className="font-bold text-gray-900 text-sm">{t('imageAds.customImageGenerationPromptAdvanced')}</h4>
                    </div>
                    {showPrompt ? <ChevronUp size={18} className="text-gray-500" /> : <ChevronDown size={18} className="text-gray-500" />}
                </button>

                {showPrompt && (
                    <div className="p-4 animate-in slide-in-from-top-2 duration-200">
                        <p className="text-xs text-gray-600 mb-3">
                            <strong>{t('imageAds.optional')}</strong> {t('imageAds.overrideTheAiGeneratedPrompt')}
                        </p>
                        <textarea
                            value={customImagePrompt}
                            onChange={(e) => setCustomImagePrompt(e.target.value)}
                            placeholder={t('imageAds.leaveEmptyToAutoGenerate')}
                            className="w-full px-3 py-2 border border-gray-300 rounded-lg focus:ring-2 focus:ring-amber-500 focus:border-transparent text-sm font-mono"
                            rows={4}
                        />
                        <div className="flex items-center justify-between mt-2">
                            <button
                                onClick={handleResetPrompt}
                                className="text-xs text-amber-600 hover:text-amber-700 font-medium"
                            >
                                {t('imageAds.resetToGeneratedPrompt')}
                            </button>
                            <span className="text-xs text-gray-500">
                                {customImagePrompt.length > 0
                                    ? t('imageAds.characters', { count: customImagePrompt.length })
                                    : t('imageAds.autoGenerating')}
                            </span>
                        </div>
                    </div>
                )}
            </div>

            {/* Actions */}
            <div className="mt-6 flex items-center justify-between">
                <button
                    onClick={onBack}
                    className="flex items-center gap-2 px-6 py-3 bg-gray-100 text-gray-700 rounded-lg hover:bg-gray-200 font-medium transition-colors"
                >
                    <ChevronLeft size={20} />
                    {t('imageAds.backToReview')}
                </button>
                <button
                    onClick={handleProceed}
                    className="flex items-center gap-2 px-6 py-3 bg-amber-600 text-white rounded-lg hover:bg-amber-700 font-medium shadow-lg transition-colors"
                >
                    {t('imageAds.generateImage')}
                    <ChevronRight size={20} />
                </button>
            </div>
        </div>
    );
}

function ImageGenerationStep({ generatedImages, wizardData, selectedCopy, onBack, onRestart }) {
    const { t } = useTranslation('creatives');
    const [selectedImageIndex, setSelectedImageIndex] = useState(null);
    const [imgError, setImgError] = useState(false);

    if (!generatedImages || generatedImages.length === 0) {
        return <div className="text-center p-8 text-red-600">{t('imageAds.errorNoImageDataAvailable')}</div>;
    }

    // Filter to show only square images in the main grid
    const squareImages = generatedImages.filter(img => img.size.includes('Square'));

    // If no square images found (fallback), use all images
    const displayImages = squareImages.length > 0 ? squareImages : generatedImages;

    const selectedImage = selectedImageIndex !== null ? displayImages[selectedImageIndex] : null;

    // Get all images in the same bundle as the selected image
    const bundleImages = selectedImage
        ? generatedImages.filter(img => img.adBundleId === selectedImage.adBundleId)
        : [];

    // Current image being viewed in the modal (defaults to the selected square image)
    const [viewedImage, setViewedImage] = useState(null);

    // Update viewed image when selection changes
    React.useEffect(() => {
        if (selectedImage) {
            setViewedImage(selectedImage);
        }
    }, [selectedImage]);

    const displayCopy = selectedCopy || { headline: '', body: '', cta: '' };

    return (
        <div>
            {/* Header */}
            <div className="text-center mb-8">
                <div className="w-20 h-20 bg-green-100 text-green-600 rounded-full flex items-center justify-center mx-auto mb-4">
                    <Check size={40} />
                </div>
                <h2 className="text-3xl font-bold text-gray-900 mb-2">{t('imageAds.adsGeneratedSuccessfully')}</h2>
                <p className="text-gray-600">{t('imageAds.clickOnAnyImageTo')}</p>
            </div>

            {/* Image Tile Gallery (Square Images Only) */}
            <div className="grid grid-cols-2 md:grid-cols-3 lg:grid-cols-4 gap-4 max-w-6xl mx-auto mb-8">
                {displayImages.map((img, index) => (
                    <button
                        key={index}
                        onClick={() => setSelectedImageIndex(index)}
                        className="group relative aspect-square rounded-xl overflow-hidden border-2 border-gray-200 hover:border-amber-600 transition-all hover:shadow-xl hover:scale-105"
                    >
                        <img
                            src={img.url}
                            alt={t('imageAds.adAlt', { index: index + 1 })}
                            className="w-full h-full object-cover"
                        />
                        {/* Overlay on hover */}
                        <div className="absolute inset-0 bg-black/0 group-hover:bg-black/40 transition-all flex items-center justify-center">
                            <div className="opacity-0 group-hover:opacity-100 transition-opacity text-white text-center p-4">
                                <Image size={32} className="mx-auto mb-2" />
                                <p className="text-sm font-medium">{sizeLabel(img.size)}</p>
                                <p className="text-xs">{img.dimensions}</p>
                            </div>
                        </div>
                        {/* Size badge */}
                        <div className="absolute bottom-2 left-2 right-2 bg-white/90 backdrop-blur-sm rounded-lg px-2 py-1 text-xs font-medium text-gray-900 text-center">
                            {sizeLabel(img.size)}
                        </div>
                    </button>
                ))}
            </div>

            {/* Action Buttons */}
            <div className="flex justify-between items-center">
                <button
                    onClick={onBack}
                    className="flex items-center gap-2 px-6 py-3 bg-gray-100 text-gray-700 rounded-lg hover:bg-gray-200 font-medium transition-colors"
                >
                    <ChevronLeft size={20} />
                    {t('imageAds.backToCopy')}
                </button>
                <button
                    onClick={onRestart}
                    className="px-6 py-3 bg-amber-600 text-white rounded-lg hover:bg-amber-700 font-medium shadow-lg transition-colors"
                >
                    {t('imageAds.createNewAd')}
                </button>
            </div>

            {/* Modal for Image Details */}
            {selectedImage && viewedImage && (
                <div
                    className="fixed inset-0 bg-black/50 backdrop-blur-sm z-50 flex items-center justify-center p-4"
                    onClick={() => setSelectedImageIndex(null)}
                >
                    <div
                        className="bg-white rounded-2xl shadow-2xl max-w-5xl w-full max-h-[90vh] overflow-y-auto"
                        onClick={(e) => e.stopPropagation()}
                    >
                        {/* Modal Header */}
                        <div className="sticky top-0 bg-white border-b border-gray-200 px-6 py-4 flex items-center justify-between rounded-t-2xl">
                            <h3 className="text-xl font-bold text-gray-900">{t('imageAds.adDetails')}</h3>
                            <button
                                onClick={() => setSelectedImageIndex(null)}
                                className="w-8 h-8 rounded-full hover:bg-gray-100 flex items-center justify-center transition-colors"
                            >
                                <span className="text-2xl text-gray-500">×</span>
                            </button>
                        </div>

                        {/* Modal Content */}
                        <div className="p-6">
                            <div className="grid grid-cols-1 lg:grid-cols-2 gap-6">
                                {/* Image Preview Section */}
                                <div className="space-y-4">
                                    {/* Main Image */}
                                    <div className="bg-gray-100 rounded-xl overflow-hidden aspect-square flex items-center justify-center">
                                        {imgError ? (
                                            <div className="p-8 text-center text-red-500 bg-red-50">
                                                <p className="font-bold mb-2">{t('imageAds.failedToLoadImage')}</p>
                                            </div>
                                        ) : (
                                            <img
                                                src={viewedImage.url}
                                                alt={t('imageAds.selectedAd')}
                                                className="w-full h-full object-contain"
                                                onError={() => setImgError(true)}
                                            />
                                        )}
                                    </div>

                                    {/* Bundle Thumbnails */}
                                    {bundleImages.length > 1 && (
                                        <div>
                                            <p className="text-sm font-medium text-gray-700 mb-2">{t('imageAds.availableSizes')}</p>
                                            <div className="flex gap-2 overflow-x-auto pb-2">
                                                {bundleImages.map((img, idx) => (
                                                    <button
                                                        key={idx}
                                                        onClick={() => {
                                                            setViewedImage(img);
                                                            setImgError(false);
                                                        }}
                                                        className={`relative w-20 h-20 rounded-lg overflow-hidden border-2 transition-all flex-shrink-0 ${viewedImage.url === img.url
                                                            ? 'border-amber-600 ring-2 ring-amber-200'
                                                            : 'border-gray-200 hover:border-amber-300'
                                                            }`}
                                                    >
                                                        <img
                                                            src={img.url}
                                                            alt={sizeLabel(img.size)}
                                                            className="w-full h-full object-cover"
                                                        />
                                                        <div className="absolute bottom-0 left-0 right-0 bg-black/60 text-white text-[10px] py-0.5 text-center truncate px-1">
                                                            {sizeShort(img.size)}
                                                        </div>
                                                    </button>
                                                ))}
                                            </div>
                                        </div>
                                    )}
                                </div>

                                {/* Details Panel */}
                                <div className="space-y-6">
                                    {/* Ad Copy */}
                                    <div className="bg-amber-50 p-5 rounded-xl border border-amber-200">
                                        <h4 className="font-bold text-gray-900 mb-4 flex items-center gap-2">
                                            <FileText size={20} className="text-amber-600" />
                                            {t('imageAds.adCopy')}
                                        </h4>
                                        <div className="space-y-3">
                                            <div>
                                                <label className="text-xs font-medium text-amber-700 uppercase">{t('imageAds.headline')}</label>
                                                <p className="font-bold text-gray-900 mt-1">{displayCopy.headline}</p>
                                            </div>
                                            <div>
                                                <label className="text-xs font-medium text-amber-700 uppercase">{t('imageAds.bodyText')}</label>
                                                <p className="text-gray-700 text-sm whitespace-pre-line mt-1">{displayCopy.body}</p>
                                            </div>
                                            <div>
                                                <label className="text-xs font-medium text-amber-700 uppercase">{t('imageAds.callToAction')}</label>
                                                <div className="mt-1">
                                                    <span className="inline-block px-3 py-1 bg-amber-600 text-white rounded-full text-sm font-medium">
                                                        {displayCopy.cta}
                                                    </span>
                                                </div>
                                            </div>
                                        </div>
                                    </div>

                                    {/* Image Details */}
                                    <div className="bg-gray-50 p-5 rounded-xl border border-gray-200">
                                        <h4 className="font-bold text-gray-900 mb-4 flex items-center gap-2">
                                            <Image size={20} className="text-gray-600" />
                                            {t('imageAds.imageDetails')}
                                        </h4>
                                        <div className="space-y-2 text-sm">
                                            <div className="flex justify-between">
                                                <span className="text-gray-500">{t('imageAds.size')}</span>
                                                <span className="font-medium text-gray-900">{sizeLabel(viewedImage.size)}</span>
                                            </div>
                                            <div className="flex justify-between">
                                                <span className="text-gray-500">{t('imageAds.dimensions')}</span>
                                                <span className="font-medium text-gray-900">{viewedImage.dimensions}</span>
                                            </div>
                                            <div className="flex justify-between">
                                                <span className="text-gray-500">{t('imageAds.format')}</span>
                                                <span className="font-medium text-gray-900">PNG</span>
                                            </div>
                                        </div>
                                    </div>

                                    {/* Download Button */}
                                    <a
                                        href={viewedImage.url}
                                        download={`ad-${viewedImage.size}-${Date.now()}.png`}
                                        target="_blank"
                                        rel="noopener noreferrer"
                                        className="w-full py-3 bg-amber-600 text-white rounded-lg hover:bg-amber-700 font-bold flex items-center justify-center gap-2 transition-colors"
                                    >
                                        <Download size={20} />
                                        {t('imageAds.downloadImage')}
                                    </a>
                                </div>
                            </div>
                        </div>
                    </div>
                </div>
            )}
        </div>
    );
}
