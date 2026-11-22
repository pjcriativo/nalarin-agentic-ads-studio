import i18next from 'i18next';
import { initReactI18next } from 'react-i18next';
import LanguageDetector from 'i18next-browser-languagedetector';

export const SUPPORTED_LANGUAGES = ['pt-BR', 'en-US'];
export const DEFAULT_LANGUAGE = 'en-US';
export const LANGUAGE_STORAGE_KEY = 'app_language';

// Every src/i18n/locales/<lng>/<namespace>.json is bundled statically (no
// runtime translation service, no network). Adding a JSON file adds a namespace.
const modules = import.meta.glob('./locales/*/*.json', { eager: true });

export const resources = {};
for (const [path, mod] of Object.entries(modules)) {
    const [, lng, file] = path.match(/\.\/locales\/([^/]+)\/([^/]+)\.json$/);
    resources[lng] = resources[lng] || {};
    resources[lng][file] = mod.default ?? mod;
}

export const namespaces = Object.keys(resources[DEFAULT_LANGUAGE] || {});

// Map any browser locale (pt, pt-PT, en-GB, fr...) onto a supported language.
export const normalizeLanguage = (lng) =>
    typeof lng === 'string' && lng.toLowerCase().startsWith('pt') ? 'pt-BR' : 'en-US';

export const buildOptions = (overrides = {}) => ({
    resources,
    ns: namespaces,
    defaultNS: 'common',
    fallbackLng: DEFAULT_LANGUAGE,
    supportedLngs: SUPPORTED_LANGUAGES,
    nonExplicitSupportedLngs: false,
    load: 'currentOnly',
    interpolation: { escapeValue: false },
    returnNull: false,
    react: { useSuspense: false },
    detection: {
        // localStorage wins; the browser language is only consulted on the
        // first visit and is then persisted by the detector's cache.
        order: ['localStorage', 'navigator'],
        lookupLocalStorage: LANGUAGE_STORAGE_KEY,
        caches: ['localStorage'],
        convertDetectedLanguage: normalizeLanguage,
    },
    saveMissing: false,
    missingKeyHandler: (lngs, ns, key) => {
        if (import.meta.env?.DEV) console.warn(`[i18n] missing key ${ns}:${key} (${lngs})`);
    },
    ...overrides,
});

const applyHtmlLang = (lng) => {
    if (typeof document !== 'undefined') document.documentElement.lang = lng;
};

/** Build a fully configured instance (the app uses the default one below). */
export function createI18n({ instance = i18next, react = true, overrides } = {}) {
    instance.use(LanguageDetector);
    if (react) instance.use(initReactI18next);
    instance.init(buildOptions(overrides));
    applyHtmlLang(instance.resolvedLanguage || instance.language);
    instance.on('languageChanged', applyHtmlLang);
    return instance;
}

const i18n = createI18n();

export default i18n;
