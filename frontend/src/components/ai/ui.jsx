/* eslint-disable react-refresh/only-export-components -- small shared module: components and helpers used together by the AI panels */
import React, { useEffect, useId, useRef } from 'react';
import { createPortal } from 'react-dom';
import { X } from 'lucide-react';
import { errorKind } from '../../lib/aiApi';
import { formatNumber } from '../../i18n/format';
import useFocusTrap from '../../lib/useFocusTrap';

// Provider names are brand names: never translated.
export const PROVIDER_NAME = { anthropic: 'Anthropic', gemini: 'Google Gemini', openai: 'OpenAI' };
export const providerName = (provider) => PROVIDER_NAME[provider] || provider;

/** The one credential a provider card describes: the active one, else the most recent revoked one. */
export function credentialFor(credentials, provider) {
    const mine = credentials.filter((c) => c.provider === provider);
    return mine.find((c) => c.status === 'active') || mine[mine.length - 1] || null;
}

// i18n keys for operations (API identifiers contain dots, which i18next uses as separators).
export const OPERATION_KEY = {
    default: 'default',
    'copy.generate': 'copyGenerate',
    'copy.regenerate_field': 'copyRegenerateField',
    'ad_remix.deconstruct': 'adRemixDeconstruct',
    'ad_remix.reconstruct': 'adRemixReconstruct',
};

export const INPUT_CLASS = 'w-full p-2 border border-gray-300 rounded-lg focus:ring-2 focus:ring-amber-500 focus:border-amber-500 disabled:bg-gray-50 disabled:text-gray-500';
export const PRIMARY_BUTTON = 'inline-flex items-center justify-center gap-2 px-4 py-2 min-h-[44px] bg-amber-600 text-white rounded-lg font-medium hover:bg-amber-700 transition-colors shadow-sm disabled:opacity-60 disabled:cursor-not-allowed focus-visible:outline focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-amber-600';
export const SECONDARY_BUTTON = 'inline-flex items-center justify-center gap-2 px-4 py-2 min-h-[44px] text-gray-700 hover:bg-gray-100 rounded-lg transition-colors font-medium border border-gray-200 disabled:opacity-60 disabled:cursor-not-allowed focus-visible:outline focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-amber-600';
export const DANGER_BUTTON = 'inline-flex items-center justify-center gap-2 px-4 py-2 min-h-[44px] text-red-700 hover:bg-red-50 rounded-lg transition-colors font-medium border border-red-200 disabled:opacity-60 disabled:cursor-not-allowed focus-visible:outline focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-red-600';
export const CARD = 'bg-white rounded-xl border border-gray-200 shadow-sm';

const TONES = {
    neutral: 'bg-gray-100 text-gray-700',
    good: 'bg-green-100 text-green-800',
    warn: 'bg-amber-100 text-amber-800',
    bad: 'bg-red-100 text-red-800',
    info: 'bg-blue-100 text-blue-800',
};

/** Status pill. Meaning is always in the text (and the icon, when given), never colour alone. */
export function Badge({ tone = 'neutral', icon: Icon, children, ...rest }) {
    return (
        <span className={`inline-flex items-center gap-1 px-2 py-0.5 rounded-full text-xs font-medium ${TONES[tone]}`} {...rest}>
            {Icon && <Icon size={12} aria-hidden="true" />}
            {children}
        </span>
    );
}

/** The user-facing sentence for a failed call (the server's generic kind, never a provider body). */
const GENERIC_ERROR_KEY = 'errors.generic'; // ai namespace (the caller passes its `t`)
export const errorMessage = (t, error) => t(`errors.${errorKind(error)}`, { defaultValue: t(GENERIC_ERROR_KEY) });

const usdFormat = { style: 'currency', currency: 'USD', minimumFractionDigits: 2, maximumFractionDigits: 6 };
export const formatUsd = (value) => (value === null || value === undefined ? '—' : formatNumber(value, usdFormat));
export const formatCount = (value) => formatNumber(value ?? 0);

/** Modal shell shared by the AI dialogs: portal, focus trap, Esc and backdrop close (same look as the role-permissions dialog). */
export function Dialog({ title, description, icon: Icon, onClose, closeLabel, children, wide = false }) {
    const dialogRef = useRef(null);
    const titleId = useId();
    useFocusTrap(dialogRef, true);

    useEffect(() => {
        const onKey = (event) => { if (event.key === 'Escape') onClose(); };
        document.addEventListener('keydown', onKey);
        return () => document.removeEventListener('keydown', onKey);
    }, [onClose]);

    useEffect(() => {
        const first = dialogRef.current?.querySelector('input:not([type=hidden]),select,textarea,button');
        first?.focus();
    }, []);

    return createPortal(
        <div className="fixed inset-0 bg-black/50 backdrop-blur-sm flex items-center justify-center z-50 p-4" onClick={onClose}>
            <div
                ref={dialogRef}
                role="dialog"
                aria-modal="true"
                aria-labelledby={titleId}
                className={`bg-white rounded-xl w-full ${wide ? 'max-w-2xl' : 'max-w-lg'} max-h-[90vh] flex flex-col shadow-xl`}
                onClick={(event) => event.stopPropagation()}
            >
                <div className="flex items-start gap-4 p-6 pb-3">
                    {Icon && (
                        <div className="w-12 h-12 rounded-full bg-amber-100 flex items-center justify-center flex-shrink-0">
                            <Icon className="text-amber-700" size={24} aria-hidden="true" />
                        </div>
                    )}
                    <div className="flex-1 min-w-0">
                        <h3 id={titleId} className="text-lg font-bold text-gray-900 break-words">{title}</h3>
                        {description && <p className="text-sm text-gray-600 mt-1">{description}</p>}
                    </div>
                    <button
                        type="button"
                        onClick={onClose}
                        aria-label={closeLabel}
                        className="text-gray-400 hover:text-gray-600 p-2 -m-2 min-w-[44px] min-h-[44px] flex items-center justify-center rounded-lg focus-visible:outline focus-visible:outline-2 focus-visible:outline-amber-600"
                    >
                        <X size={20} aria-hidden="true" />
                    </button>
                </div>
                <div className="overflow-auto px-6 pb-6">{children}</div>
            </div>
        </div>,
        document.body,
    );
}

/** Labelled form control with optional hint and inline error (error is announced and tied to the control). */
export function Field({ id, label, hint, error, children }) {
    return (
        <div>
            <label htmlFor={id} className="block text-sm font-medium text-gray-700 mb-1">{label}</label>
            {children}
            {hint && !error && <p id={`${id}-hint`} className="text-xs text-gray-500 mt-1">{hint}</p>}
            {error && <p id={`${id}-error`} role="alert" className="text-xs text-red-700 mt-1">{error}</p>}
        </div>
    );
}
