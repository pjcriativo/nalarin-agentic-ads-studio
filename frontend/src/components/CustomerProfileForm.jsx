import React, { useState } from 'react';
import { translateBackendMessage } from '../i18n/backendErrors';
import { useTranslation } from 'react-i18next';
import { X } from 'lucide-react';
import { useToast } from '../context/ToastContext';
import { validateTextInput } from '../utils/validation';

const CustomerProfileForm = ({ onClose, onSave, initialData = null }) => {
    const { t } = useTranslation('brands');
    const { showError } = useToast();
    const [formData, setFormData] = useState(initialData || {
        name: '',
        demographics: '',
        painPoints: '',
        goals: ''
    });

    const handleSubmit = (e) => {
        e.preventDefault();
        try {
            const validatedData = {
                ...formData,
                name: validateTextInput(formData.name, 'Profile Name', 100),
                demographics: validateTextInput(formData.demographics, 'Demographics', 500),
                painPoints: validateTextInput(formData.painPoints, 'Pain Points', 1000),
                goals: validateTextInput(formData.goals, 'Goals', 1000)
            };

            onSave(validatedData);
        } catch (err) {
            showError(translateBackendMessage(err.message));
        }
    };

    return (
        <div className="fixed inset-0 bg-black bg-opacity-50 flex items-center justify-center z-50 p-4" onClick={onClose}>
            <div className="bg-white rounded-xl shadow-xl w-full max-w-lg max-h-[90vh] overflow-y-auto" onClick={e => e.stopPropagation()}>
                <div className="flex justify-between items-center p-6 border-b border-gray-100">
                    <h2 className="text-xl font-bold text-gray-900">
                        {initialData ? t('profileForm.edit') : t('profileForm.add')}
                    </h2>
                    <button onClick={onClose} className="text-gray-500 hover:bg-gray-100 p-2 rounded-full">
                        <X size={20} />
                    </button>
                </div>

                <form onSubmit={handleSubmit} className="p-6 space-y-6">
                    <div>
                        <label className="block text-sm font-medium text-gray-700 mb-1">{t('profileForm.name')}</label>
                        <input
                            required
                            type="text"
                            maxLength={100}
                            value={formData.name}
                            onChange={e => setFormData({ ...formData, name: e.target.value })}
                            className="w-full p-2 border border-gray-300 rounded-lg focus:ring-2 focus:ring-blue-500"
                            placeholder={t('profileForm.namePlaceholder')}
                        />
                    </div>

                    <div>
                        <label className="block text-sm font-medium text-gray-700 mb-1">{t('profiles.demographics')}</label>
                        <textarea
                            required
                            value={formData.demographics}
                            maxLength={500}
                            onChange={e => setFormData({ ...formData, demographics: e.target.value })}
                            className="w-full p-2 border border-gray-300 rounded-lg focus:ring-2 focus:ring-blue-500"
                            rows="3"
                            placeholder={t('profileForm.demographicsPlaceholder')}
                        />
                    </div>

                    <div>
                        <label className="block text-sm font-medium text-gray-700 mb-1">{t('profiles.painPoints')}</label>
                        <textarea
                            required
                            value={formData.painPoints}
                            maxLength={1000}
                            onChange={e => setFormData({ ...formData, painPoints: e.target.value })}
                            className="w-full p-2 border border-gray-300 rounded-lg focus:ring-2 focus:ring-blue-500"
                            rows="3"
                            placeholder={t('profileForm.painPointsPlaceholder')}
                        />
                    </div>

                    <div>
                        <label className="block text-sm font-medium text-gray-700 mb-1">{t('profileForm.goals')}</label>
                        <textarea
                            required
                            value={formData.goals}
                            maxLength={1000}
                            onChange={e => setFormData({ ...formData, goals: e.target.value })}
                            className="w-full p-2 border border-gray-300 rounded-lg focus:ring-2 focus:ring-blue-500"
                            rows="3"
                            placeholder={t('profileForm.goalsPlaceholder')}
                        />
                    </div>

                    <div className="flex justify-end gap-3 pt-4 border-t border-gray-100">
                        <button
                            type="button"
                            onClick={onClose}
                            className="px-4 py-2 text-gray-700 hover:bg-gray-100 rounded-lg"
                        >
                            {t('common.cancel')}
                        </button>
                        <button
                            type="submit"
                            className="px-6 py-2 bg-blue-600 text-white rounded-lg hover:bg-blue-700 font-medium"
                        >
                            {t('profileForm.save')}
                        </button>
                    </div>
                </form>
            </div>
        </div>
    );
};

export default CustomerProfileForm;
