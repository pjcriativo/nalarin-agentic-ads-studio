import i18n from './index';

// Locale-aware formatting helpers. They read the active language at call time,
// so components that call them while rendering (and use useTranslation, which
// re-renders on language change) always show the current language.
const lng = () => i18n.resolvedLanguage || i18n.language || 'en-US';

const toDate = (value) => (value instanceof Date ? value : new Date(value));

export const formatDate = (value, options) => toDate(value).toLocaleDateString(lng(), options);
export const formatTime = (value, options) => toDate(value).toLocaleTimeString(lng(), options);
export const formatDateTime = (value, options) => toDate(value).toLocaleString(lng(), options);
export const formatNumber = (value, options) => Number(value).toLocaleString(lng(), options);

/** Localised country name from an ISO region code (falls back to the given name). */
export const countryName = (code, fallback) => {
    try {
        return new Intl.DisplayNames([lng()], { type: 'region' }).of(code) || fallback || code;
    } catch {
        return fallback || code;
    }
};

// Ad size names ("Square (Feed/Carousel)", "Vertical (Feed)", "Story") are stored
// in the database as identifiers; only their on-screen label is localised.
const sizeKey = (name) => {
    const n = String(name || '');
    if (n.startsWith('Square')) return 'square';
    if (n.startsWith('Vertical')) return 'vertical';
    if (n.startsWith('Story')) return 'story';
    return null;
};
export const sizeLabel = (name) => {
    const k = sizeKey(name);
    return k ? i18n.t(`creatives:sizeNames.${k}`, { defaultValue: name }) : name;
};
export const sizeShort = (name) => {
    const k = sizeKey(name);
    return k ? i18n.t(`creatives:sizeShort.${k}`, { defaultValue: name }) : String(name || '').split(' ')[0];
};
export const sizeDescription = (size) => {
    const k = sizeKey(size?.name);
    return k ? i18n.t(`creatives:sizeDesc.${k}`, { defaultValue: size.description }) : size?.description;
};
