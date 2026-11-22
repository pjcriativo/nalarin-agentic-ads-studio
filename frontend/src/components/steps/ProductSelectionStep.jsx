import React from 'react';
import { useTranslation } from 'react-i18next';
import { Check, Image } from 'lucide-react';

export default function ProductSelectionStep({ products, selectedProduct, onSelect, useProductShots, onToggleProductShots }) {
    const { t } = useTranslation('creatives');
    return (
        <div>
            <h3 className="text-xl font-bold mb-4">{t('productSelectionStep.selectYourProduct')}</h3>
            <p className="text-gray-600 mb-6">{t('productSelectionStep.chooseTheProductToFeature')}</p>
            {products.length === 0 ? (
                <div className="text-center py-12 text-gray-500">
                    {t('productSelectionStep.noProductsFoundForThis')}
                </div>
            ) : (
                <div className="space-y-3">
                    {products.map(product => (
                        <div
                            key={product.id}
                            className={`p-4 rounded-xl border-2 transition-all ${selectedProduct?.id === product.id
                                ? 'border-amber-600 bg-amber-50'
                                : 'border-gray-200 hover:border-amber-300'
                                }`}
                        >
                            <div
                                onClick={() => onSelect(product)}
                                className="cursor-pointer flex items-center justify-between"
                            >
                                <div>
                                    <div className="font-bold text-gray-900">{product.name}</div>
                                    {product.description && (
                                        <div className="text-sm text-gray-600 mt-1">{product.description}</div>
                                    )}
                                    {product.product_shots && product.product_shots.length > 0 && (
                                        <div className="text-xs text-amber-600 mt-1 font-medium flex items-center gap-1">
                                            <Image size={12} />
                                            {t('common.productShotsAvailable', { count: product.product_shots.length })}
                                        </div>
                                    )}
                                </div>
                                {selectedProduct?.id === product.id && (
                                    <Check className="text-amber-600" size={24} />
                                )}
                            </div>

                            {/* Product Shots Toggle */}
                            {selectedProduct?.id === product.id && product.product_shots && product.product_shots.length > 0 && (
                                <div className="mt-4 pt-4 border-t border-amber-200">
                                    <label className="flex items-center gap-3 cursor-pointer group">
                                        <div className={`w-5 h-5 rounded border flex items-center justify-center transition-colors ${useProductShots ? 'bg-amber-600 border-amber-600' : 'bg-white border-gray-400 group-hover:border-amber-500'}`}>
                                            {useProductShots && <Check size={14} className="text-white" />}
                                        </div>
                                        <input
                                            type="checkbox"
                                            className="hidden"
                                            checked={useProductShots}
                                            onChange={(e) => onToggleProductShots(e.target.checked)}
                                        />
                                        <div>
                                            <span className="text-sm font-medium text-gray-900">{t('productSelectionStep.useProductShots')}</span>
                                            <p className="text-xs text-gray-500">{t('productSelectionStep.includeUploadedProductImagesIn')}</p>
                                        </div>
                                    </label>

                                    {useProductShots && (
                                        <div className="flex gap-2 mt-3 overflow-x-auto pb-2">
                                            {product.product_shots.map((shot, idx) => (
                                                <img key={idx} src={shot} alt={t('productSelectionStep.productShot')} className="w-16 h-16 object-cover rounded-lg border border-gray-200" />
                                            ))}
                                        </div>
                                    )}
                                </div>
                            )}
                        </div>
                    ))}
                </div>
            )}
        </div>
    );
}
