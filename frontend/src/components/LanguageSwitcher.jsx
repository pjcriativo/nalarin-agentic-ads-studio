import React from 'react';
import { useTranslation } from 'react-i18next';

// Inline SVG flags: the 🇧🇷 / 🇺🇸 emoji are not drawn on Windows (they fall back to
// the letters "BR"/"US"), so the flags are vector drawings that look the same everywhere.
const BrazilFlag = () => (
    <svg viewBox="0 0 20 14" width="16" height="11" aria-hidden="true" focusable="false" className="rounded-[2px]">
        <rect width="20" height="14" fill="#009C3B" />
        <path d="M10 1.6 18.2 7 10 12.4 1.8 7z" fill="#FFDF00" />
        <circle cx="10" cy="7" r="3.2" fill="#002776" />
        <path d="M6.9 6.1c2.1-.5 4.4-.2 6.3 1.1" stroke="#fff" strokeWidth=".6" fill="none" />
    </svg>
);

const UsFlag = () => (
    <svg viewBox="0 0 20 14" width="16" height="11" aria-hidden="true" focusable="false" className="rounded-[2px]">
        <rect width="20" height="14" fill="#fff" />
        {[0, 2, 4, 6, 8, 10, 12].map((i) => (
            <rect key={i} y={(i * 14) / 13} width="20" height={14 / 13} fill="#B22234" />
        ))}
        <rect width="9" height={(14 / 13) * 7} fill="#3C3B6E" />
        {[1.5, 3.5, 5.5, 7.5].flatMap((x) => [1.4, 3.2, 5].map((y) => <circle key={`${x}-${y}`} cx={x} cy={y} r=".45" fill="#fff" />))}
    </svg>
);

const OPTIONS = [
    { code: 'pt-BR', emoji: '🇧🇷', Flag: BrazilFlag, labelKey: 'language.pt' },
    { code: 'en-US', emoji: '🇺🇸', Flag: UsFlag, labelKey: 'language.en' },
];

/**
 * Discreet flag toggle. Switching calls i18n.changeLanguage, which re-renders
 * every useTranslation consumer immediately and persists to localStorage via
 * the language detector (no page reload).
 */
export default function LanguageSwitcher({ className = '' }) {
    const { t, i18n } = useTranslation('common');
    const current = i18n.resolvedLanguage || i18n.language;

    return (
        <div
            role="group"
            aria-label={t('language.label')}
            className={`inline-flex items-center gap-0.5 rounded-full border border-amber-200 bg-white/90 p-px shadow-sm ${className}`}
        >
            {OPTIONS.map(({ code, emoji, Flag, labelKey }) => {
                const active = current === code;
                return (
                    <button
                        key={code}
                        type="button"
                        onClick={() => i18n.changeLanguage(code)}
                        aria-pressed={active}
                        aria-label={t(labelKey)}
                        title={t(labelKey)}
                        data-testid={`lang-${code}`}
                        data-flag={emoji}
                        className={`flex h-5 w-6 items-center justify-center rounded-full transition ${active ? 'bg-amber-100 opacity-100' : 'opacity-50 hover:opacity-100'}`}
                    >
                        <Flag />
                    </button>
                );
            })}
        </div>
    );
}
