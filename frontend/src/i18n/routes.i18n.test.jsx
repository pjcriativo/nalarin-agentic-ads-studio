/**
 * Route sweep: renders the real <App /> (real router, lazy pages, Layout, toasts)
 * for every route in BOTH languages with the backend mocked, and asserts:
 *   - the page renders without throwing,
 *   - no translation key is missing (i18next missingKey hook, incl. pt-BR),
 *   - no raw key ("ns.key") or un-interpolated "{{placeholder}}" is visible,
 *   - in pt-BR no well-known English UI word is left in text/attributes,
 *   - the flag switcher is present on every authenticated page.
 */
import React from 'react';
import { render, act, waitFor, cleanup, fireEvent } from '@testing-library/react';

import i18n from './index';

// No real network in the sweep (src/api/research.js uses axios directly).
vi.mock('axios', () => {
    const empty = async () => ({ data: [], status: 200 });
    const api = { get: empty, post: empty, put: empty, delete: empty, patch: empty, interceptors: { request: { use: () => {} }, response: { use: () => {} } } };
    return { default: { ...api, create: () => api }, ...api };
});

vi.mock('../context/AuthContext', () => {
    const authFetch = vi.fn(async (url) => {
        const u = String(url);
        if (/\/optimization\/status/.test(u)) {
            return { ok: true, status: 200, json: async () => ({ kill_switch_active: false, auto_enabled: true, allowed_actions: ['pause_campaign'], max_budget_micros: 0, max_daily_spend_micros: 5000000, operating_hours: { start_wib: null, end_wib: null } }) };
        }
        if (/\/brands(\?|$)/.test(u)) {
            return { ok: true, status: 200, json: async () => [{ id: 'b1', name: 'Acme Corp', logo: '', voice: 'Friendly', colors: { primary: '#112233', secondary: '#445566', highlight: '#778899' }, products: [{ id: 'p1', name: 'Glow Serum', description: 'Nice serum', brandId: 'b1', product_shots: [] }], profileIds: ['c1'] }] };
        }
        if (/\/profiles(\?|$)/.test(u)) {
            return { ok: true, status: 200, json: async () => [{ id: 'c1', name: 'Busy Moms', demographics: '25-40, urban', painPoints: 'No time', goals: 'Relax' }] };
        }
        const list = /\/(brands|profiles|products|templates|winning|generated|verticals|searches|users|roles|permissions|styles|prompts|brand-scrapes|blacklist|keyword-blacklist|campaigns|adsets|ads|pixels|pages|ad-accounts)(\?|$|\/)/.test(u);
        return { ok: true, status: 200, json: async () => (list ? [] : {}), text: async () => '' };
    });
    const value = {
        user: { id: 1, name: 'Test User', email: 'test@example.com' },
        isAuthenticated: true,
        loading: false,
        error: null,
        login: vi.fn(),
        register: vi.fn(),
        logout: vi.fn(async () => {}),
        authFetch,
        hasRole: () => true,
        hasPermission: () => true,
        isAdmin: true,
    };
    return { AuthProvider: ({ children }) => children, useAuth: () => value };
});

import App from '../App';

const ROUTES = [
    '/login', '/about', '/privacy', '/terms',
    '/', '/dashboard', '/research', '/research/brand-scrapes', '/research/settings',
    '/build-creatives', '/image-ads', '/video-ads', '/facebook-campaigns', '/google-ads', '/tiktok-ads',
    '/winning-ads', '/generated-ads', '/brands', '/products', '/profiles', '/ad-remix', '/reporting',
    '/settings', '/users',
];
const PUBLIC = new Set(['/login', '/about', '/privacy', '/terms']);

const RAW_KEY = /^[a-z][A-Za-z0-9]*(\.[A-Za-z0-9_]+)+$/;
const ENGLISH_UI = /\b(Loading|Save|Cancel|Delete|Search|Create|Select|Settings|Back|Next|Edit|Add|Remove|Upload|Download|Generate|Failed|Please|Error|Click|Choose|Enter|Brands?|Products?|Campaigns?|Templates?|Profiles?|Overview|Dashboard|Welcome|Account|Retry)\b/;

const textNodes = (root) => {
    const out = [];
    const walker = document.createTreeWalker(root, NodeFilter.SHOW_TEXT);
    let n;
    while ((n = walker.nextNode())) {
        const parent = n.parentElement;
        if (parent && /^(SCRIPT|STYLE)$/.test(parent.tagName)) continue;
        const text = n.nodeValue.replace(/\s+/g, ' ').trim();
        if (text) out.push(text);
    }
    return out;
};
const attrTexts = (root) =>
    [...root.querySelectorAll('[placeholder],[title],[aria-label],[alt]')].flatMap((el) =>
        ['placeholder', 'title', 'aria-label', 'alt'].map((a) => el.getAttribute(a)).filter(Boolean),
    );

const settle = async (container) => {
    await waitFor(() => expect(container.querySelector('h1, h2, h3, button, a')).toBeTruthy(), { timeout: 8000 });
    await act(async () => {
        await new Promise((r) => setTimeout(r, 80));
    });
};

const assertTranslated = (lng, label, missing) => {
    expect(missing, `missing keys on ${label}`).toEqual([]);
    const texts = textNodes(document.body);
    const all = [...texts, ...attrTexts(document.body)];
    expect(all.filter((t) => t.includes('{{')), 'un-interpolated placeholders').toEqual([]);
    expect(texts.filter((t) => RAW_KEY.test(t)), 'raw keys visible').toEqual([]);
    if (lng === 'pt-BR') {
        expect(all.filter((t) => ENGLISH_UI.test(t)), `English UI left on ${label}`).toEqual([]);
        expect(document.documentElement.lang).toBe('pt-BR');
    }
};

const clickByName = async (re) => {
    const el = [...document.querySelectorAll('button, a')].find((b) => re.test(`${b.textContent} ${b.getAttribute('title') || ''} ${b.getAttribute('aria-label') || ''}`));
    expect(el, `control ${re}`).toBeTruthy();
    await act(async () => {
        fireEvent.click(el);
    });
};

describe.each(['en-US', 'pt-BR'])('route sweep (%s)', (lng) => {
    let missing;

    beforeEach(async () => {
        missing = [];
        i18n.options.saveMissing = true;
        i18n.options.missingKeyHandler = (langs, ns, key) => missing.push(`${langs}:${ns}:${key}`);
        await act(async () => {
            await i18n.changeLanguage(lng);
        });
    });
    afterEach(() => {
        cleanup();
        i18n.options.saveMissing = false;
    });

    it.each(ROUTES)('%s renders fully translated', async (route) => {
        window.history.pushState({}, '', route);
        const { container } = render(<App />);
        await settle(container);
        assertTranslated(lng, route, missing);
        if (!PUBLIC.has(route)) {
            expect(document.querySelector('[data-testid="lang-pt-BR"]')).toBeTruthy();
            expect(document.querySelector('[data-testid="lang-en-US"]')).toBeTruthy();
        }
    });

    // Open the modals / forms / confirmations that are not visible on first render.
    it.each([
        ['/brands', /Add Brand|Adicionar marca/],
        ['/brands', /^\s*Delete|Excluir/],
        ['/products', /Add Product|Adicionar produto/],
        ['/profiles', /Add Profile|Adicionar perfil/],
        ['/users', /Add User|Adicionar usuário/],
        ['/research', /New Vertical|Nova vertical/],
        ['/research/settings', /Add Page|Adicionar página/],
        ['/research/settings', /Add Keyword|Adicionar palavra-chave/],
        ['/settings', /Add Style|Adicionar estilo/],
        ['/settings', /Generate with AI|Gerar com IA/],
        ['/image-ads', /Start Creating|Começar a criar|Image|Imagem/],
    ])('%s after clicking %s', async (route, control) => {
        window.history.pushState({}, '', route);
        const { container } = render(<App />);
        await settle(container);
        const hasControl = [...document.querySelectorAll('button, a')].some((b) => control.test(`${b.textContent} ${b.getAttribute('title') || ''} ${b.getAttribute('aria-label') || ''}`));
        if (hasControl) await clickByName(control);
        await act(async () => {
            await new Promise((r) => setTimeout(r, 50));
        });
        assertTranslated(lng, `${route} [${control}]`, missing);
    });
});
