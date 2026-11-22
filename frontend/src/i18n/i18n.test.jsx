import React from 'react';
import i18next from 'i18next';
import { render, screen, fireEvent, act } from '@testing-library/react';
import { useTranslation } from 'react-i18next';

import i18n, {
    createI18n,
    resources,
    namespaces,
    normalizeLanguage,
    LANGUAGE_STORAGE_KEY,
    SUPPORTED_LANGUAGES,
} from './index';
import LanguageSwitcher from '../components/LanguageSwitcher';
import { translateBackendMessage } from './backendErrors';
import { formatDate, formatNumber, sizeLabel } from './format';
import { validateBrandName, validateTextInput } from '../utils/validation';

// ---- helpers ---------------------------------------------------------------
const store = {};
const wireStorage = (initial = {}) => {
    Object.keys(store).forEach((k) => delete store[k]);
    Object.assign(store, initial);
    localStorage.getItem.mockImplementation((k) => (k in store ? store[k] : null));
    localStorage.setItem.mockImplementation((k, v) => {
        store[k] = String(v);
    });
};
const stubBrowserLanguage = (lang) => {
    vi.spyOn(window.navigator, 'language', 'get').mockReturnValue(lang);
    vi.spyOn(window.navigator, 'languages', 'get').mockReturnValue([lang]);
};
const freshInstance = () => createI18n({ instance: i18next.createInstance(), react: false });

afterEach(async () => {
    vi.restoreAllMocks();
    await act(async () => {
        await i18n.changeLanguage('en-US');
    });
});

// ---- resources -------------------------------------------------------------
describe('translation resources', () => {
    it('ships exactly pt-BR and en-US with the same namespaces', () => {
        expect(Object.keys(resources).sort()).toEqual(['en-US', 'pt-BR']);
        expect(Object.keys(resources['pt-BR']).sort()).toEqual(Object.keys(resources['en-US']).sort());
        expect(namespaces.length).toBeGreaterThanOrEqual(10);
        expect(SUPPORTED_LANGUAGES).toEqual(['pt-BR', 'en-US']);
    });

    it('keeps the original English strings', () => {
        expect(i18n.getFixedT('en-US', 'layout')('menu.winningAds')).toBe('Winning Ads');
        expect(i18n.getFixedT('en-US', 'auth')('login.signIn')).toBe('Sign In');
    });

    it('has Brazilian Portuguese for the UI', () => {
        expect(i18n.getFixedT('pt-BR', 'layout')('menu.winningAds')).toBe('Anúncios vencedores');
        expect(i18n.getFixedT('pt-BR', 'auth')('login.signIn')).toBe('Entrar');
    });

    it('pluralises in both languages', () => {
        const en = i18n.getFixedT('en-US', 'research');
        const pt = i18n.getFixedT('pt-BR', 'research');
        expect(en('research.uniqueAds', { count: 1 })).toBe('1 unique ads'); // original English wording is preserved
        expect(en('research.uniqueAds', { count: 5 })).toBe('5 unique ads');
        expect(pt('research.uniqueAds', { count: 1 })).toBe('1 anúncio único');
        expect(pt('research.uniqueAds', { count: 5 })).toBe('5 anúncios únicos');
    });
});

// ---- detection / persistence -----------------------------------------------
describe('language detection and persistence', () => {
    it('maps browser locales onto the two supported languages', () => {
        expect(normalizeLanguage('pt')).toBe('pt-BR');
        expect(normalizeLanguage('pt-PT')).toBe('pt-BR');
        expect(normalizeLanguage('pt-BR')).toBe('pt-BR');
        expect(normalizeLanguage('en-GB')).toBe('en-US');
        expect(normalizeLanguage('fr-FR')).toBe('en-US');
        expect(normalizeLanguage(undefined)).toBe('en-US');
    });

    it('first visit: uses the browser language (pt) and persists it', () => {
        wireStorage();
        stubBrowserLanguage('pt-BR');
        const inst = freshInstance();
        expect(inst.resolvedLanguage).toBe('pt-BR');
        expect(store[LANGUAGE_STORAGE_KEY]).toBe('pt-BR');
    });

    it('first visit: unsupported browser language falls back to en-US', () => {
        wireStorage();
        stubBrowserLanguage('de-DE');
        expect(freshInstance().resolvedLanguage).toBe('en-US');
    });

    it('returning visit: the stored preference wins over the browser language', () => {
        wireStorage({ [LANGUAGE_STORAGE_KEY]: 'en-US' });
        stubBrowserLanguage('pt-BR');
        expect(freshInstance().resolvedLanguage).toBe('en-US');

        wireStorage({ [LANGUAGE_STORAGE_KEY]: 'pt-BR' });
        stubBrowserLanguage('en-US');
        expect(freshInstance().resolvedLanguage).toBe('pt-BR');
    });

    it('ignores a corrupted stored value', () => {
        wireStorage({ [LANGUAGE_STORAGE_KEY]: 'klingon' });
        stubBrowserLanguage('en-US');
        expect(freshInstance().resolvedLanguage).toBe('en-US');
    });

    it('changeLanguage persists the choice and updates <html lang>', async () => {
        wireStorage();
        stubBrowserLanguage('en-US');
        const inst = freshInstance();
        await inst.changeLanguage('pt-BR');
        expect(store[LANGUAGE_STORAGE_KEY]).toBe('pt-BR');
        expect(document.documentElement.lang).toBe('pt-BR');
    });

    it('falls back to English when a key is missing in pt-BR, and to the key when missing everywhere', async () => {
        const inst = freshInstance();
        inst.addResource('en-US', 'common', 'only.english', 'Only in English');
        await inst.changeLanguage('pt-BR');
        expect(inst.t('only.english', { ns: 'common' })).toBe('Only in English');
        expect(inst.t('does.not.exist', { ns: 'common' })).toBe('does.not.exist');
    });
});

// ---- switcher --------------------------------------------------------------
describe('LanguageSwitcher', () => {
    const Sample = () => {
        const { t } = useTranslation('layout');
        return <h1>{t('menu.winningAds')}</h1>;
    };

    it('shows both flags, marks the active one and switches the whole UI without reload', async () => {
        wireStorage();
        await act(async () => {
            await i18n.changeLanguage('en-US');
        });
        render(
            <>
                <LanguageSwitcher />
                <Sample />
            </>,
        );
        const br = screen.getByTestId('lang-pt-BR');
        const us = screen.getByTestId('lang-en-US');
        expect(br).toHaveAttribute('data-flag', '🇧🇷');
        expect(us).toHaveAttribute('data-flag', '🇺🇸');
        expect(br.querySelector('svg')).toBeTruthy(); // vector flags (emoji flags are not drawn on Windows)
        expect(us.querySelector('svg')).toBeTruthy();
        expect(us).toHaveAttribute('aria-pressed', 'true');
        expect(screen.getByRole('heading')).toHaveTextContent('Winning Ads');

        await act(async () => {
            fireEvent.click(br);
        });
        expect(screen.getByRole('heading')).toHaveTextContent('Anúncios vencedores');
        expect(br).toHaveAttribute('aria-pressed', 'true');
        expect(store[LANGUAGE_STORAGE_KEY]).toBe('pt-BR');

        await act(async () => {
            fireEvent.click(us);
        });
        expect(screen.getByRole('heading')).toHaveTextContent('Winning Ads');
        expect(store[LANGUAGE_STORAGE_KEY]).toBe('en-US');
    });

    it('exposes translated accessible names', async () => {
        await act(async () => {
            await i18n.changeLanguage('pt-BR');
        });
        render(<LanguageSwitcher />);
        expect(screen.getByRole('group')).toHaveAttribute('aria-label', 'Idioma');
    });
});

// ---- non-React helpers -----------------------------------------------------
describe('backend errors, validation and formatting', () => {
    it('maps known backend messages to the active language and passes unknown ones through', async () => {
        expect(translateBackendMessage('Incorrect email or password')).toBe('Incorrect email or password');
        expect(translateBackendMessage('Failed to fetch')).toBe('Failed to fetch'); // English is shown verbatim
        await act(async () => {
            await i18n.changeLanguage('pt-BR');
        });
        expect(translateBackendMessage('Incorrect email or password')).toBe('E-mail ou senha incorretos');
        expect(translateBackendMessage('Rate limit exceeded. Try again in 30 seconds.')).toBe(
            'Limite de requisições excedido. Tente novamente em 30 segundos.',
        );
        expect(translateBackendMessage('Failed to fetch')).toMatch(/Erro de rede/);
        // user/provider data is never altered
        expect(translateBackendMessage('Some unmapped Google Ads error 123')).toBe('Some unmapped Google Ads error 123');
        expect(translateBackendMessage(undefined, 'errors:generic')).toBe('Algo deu errado. Tente novamente.');
    });

    it('translates validation errors thrown from utilities', async () => {
        await act(async () => {
            await i18n.changeLanguage('pt-BR');
        });
        expect(() => validateBrandName('')).toThrow('O nome da marca é obrigatório');
        expect(() => validateTextInput('x'.repeat(11), 10, 'Meta')).toThrow('Meta deve ter menos de 10 caracteres');
        await act(async () => {
            await i18n.changeLanguage('en-US');
        });
        expect(() => validateBrandName('')).toThrow('Brand name is required');
    });

    it('formats dates and numbers for the active language', async () => {
        const d = new Date(Date.UTC(2026, 6, 22, 12));
        await act(async () => {
            await i18n.changeLanguage('en-US');
        });
        expect(formatDate(d, { timeZone: 'UTC' })).toBe('7/22/2026');
        expect(formatNumber(1234.5)).toBe('1,234.5');
        await act(async () => {
            await i18n.changeLanguage('pt-BR');
        });
        expect(formatDate(d, { timeZone: 'UTC' })).toBe('22/07/2026');
        expect(formatNumber(1234.5)).toBe('1.234,5');
    });

    it('localises stored ad-size identifiers only for display', async () => {
        await act(async () => {
            await i18n.changeLanguage('pt-BR');
        });
        expect(sizeLabel('Square (Feed/Carousel)')).toBe('Quadrado (Feed/Carrossel)');
        expect(sizeLabel('Custom 1200x628')).toBe('Custom 1200x628');
    });
});
