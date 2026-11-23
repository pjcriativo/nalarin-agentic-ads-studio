#!/usr/bin/env node
// Browser end-to-end for the AI Control Center. Drives a REAL browser against a running build of the app
// (vite dev/preview) while the backend is the tenant-aware in-memory fake (src/__tests__/fakeBackend.js +
// fakeAiBackend.js) wired through request interception. No real API, AI provider or ad platform is contacted.
//
// Env: APP_URL (default http://localhost:5302), OUT (screenshots dir), LANG_UI (en-US|pt-BR),
//      BROWSER (exe path), PUPPETEER_DIR (dir whose node_modules has puppeteer-core)
// Run: MSYS_NO_PATHCONV=1 PUPPETEER_DIR="C:/.../vr" APP_URL=http://localhost:5302 LANG_UI=pt-BR node tests/e2e/ai-control-center.e2e.mjs
import { createRequire } from 'node:module';
import fs from 'node:fs';
import path from 'node:path';
import { createFakeBackend } from '../../src/__tests__/fakeBackend.js';

const require = createRequire(path.join(process.env.PUPPETEER_DIR || process.cwd(), 'x.js'));
const puppeteer = require('puppeteer-core');

const APP_URL = process.env.APP_URL || 'http://localhost:5302';
const OUT = process.env.OUT || './e2e-ai-out';
const LANG_UI = process.env.LANG_UI || 'en-US';
const API = 'http://localhost:8000/api/v1';
const EXE = process.env.BROWSER || ['C:/Program Files (x86)/Microsoft/Edge/Application/msedge.exe', 'C:/Program Files/Google/Chrome/Application/chrome.exe'].find(fs.existsSync);
const VIEWPORTS = (process.env.ONLY ? [process.env.ONLY.split('x').map(Number)] : [[1440, 900], [820, 1000], [390, 844]]);
const KEY = 'sk-e2e-SECRETSECRET-00112233-ZZ99';
if (!EXE) { console.error('No browser found: set BROWSER'); process.exit(2); }
fs.mkdirSync(OUT, { recursive: true });

const T = {
    'en-US': { add: 'Add API key', save: 'Save key', models: 'Models', routing: 'Routing', usage: 'Usage and limits', addModel: 'Add model', saveModel: 'Save model', readOnly: 'only administrators', noAccess: "You can't view the AI settings", nav: 'AI Control Center', title: 'AI Control Center' },
    'pt-BR': { add: 'Adicionar chave de API', save: 'Salvar chave', models: 'Modelos', routing: 'Roteamento', usage: 'Consumo e limites', addModel: 'Adicionar modelo', saveModel: 'Salvar modelo', readOnly: 'só administradores', noAccess: 'Você não pode ver as configurações de IA', nav: 'Central de IA', title: 'Central de IA' },
}[LANG_UI];

const results = [];
const check = (name, ok, detail = '') => {
    results.push({ name, ok: !!ok });
    console.log(`${ok ? 'PASS' : 'FAIL'}  ${name}${ok || !detail ? '' : `  -> ${detail}`}`);
};
const sleep = (ms) => new Promise((r) => setTimeout(r, ms));
const browser = await puppeteer.launch({ executablePath: EXE, headless: 'new', args: ['--no-sandbox'] });

async function openPage(backend, userKey, [w, h]) {
    const context = await browser.createBrowserContext();
    const page = await context.newPage();
    await page.setViewport({ width: w, height: h });
    const tokens = backend.loginTokens(userKey);
    await page.evaluateOnNewDocument((lang, t) => {
        Object.defineProperty(navigator, 'language', { get: () => lang });
        Object.defineProperty(navigator, 'languages', { get: () => [lang] });
        if (!sessionStorage.getItem('__seeded')) {
            localStorage.setItem('app_language', lang);
            localStorage.setItem('accessToken', t.access_token);
            localStorage.setItem('refreshToken', t.refresh_token);
            sessionStorage.setItem('__seeded', '1');
        }
        document.addEventListener('DOMContentLoaded', () => {
            const s = document.createElement('style');
            s.textContent = '*{animation:none!important;transition:none!important;caret-color:transparent!important}';
            document.head.appendChild(s);
        });
    }, LANG_UI, tokens);
    await page.setRequestInterception(true);
    page.on('request', async (request) => {
        const url = request.url();
        if (url.startsWith(API)) {
            const h2 = request.headers();
            const cors = {
                'access-control-allow-origin': h2.origin || '*',
                'access-control-allow-credentials': 'true',
                'access-control-allow-headers': h2['access-control-request-headers'] || '*',
                'access-control-allow-methods': 'GET,POST,PUT,PATCH,DELETE,OPTIONS', // '*' is not honoured with credentials
                'access-control-expose-headers': 'X-AI-Error',
            };
            if (request.method() === 'OPTIONS') return request.respond({ status: 204, headers: cors });
            const init = { method: request.method(), body: request.postData() || undefined, headers: {} };
            if (h2.authorization) init.headers.Authorization = h2.authorization;
            if (h2['x-organization-id']) init.headers['X-Organization-Id'] = h2['x-organization-id'];
            const res = await backend.fetch(url, init);
            const aiKind = res.headers?.get?.('X-AI-Error');
            return request.respond({ status: res.status, contentType: 'application/json', headers: aiKind ? { ...cors, 'x-ai-error': aiKind } : cors, body: JSON.stringify(await res.json()) });
        }
        return /^https?:\/\/(localhost|127\.0\.0\.1)/.test(url) ? request.continue() : request.abort();
    });
    return { page, context };
}

const settle = async (page, ms = 400) => {
    await page.waitForNetworkIdle({ idleTime: 300, timeout: 15000 }).catch(() => {});
    await sleep(ms);
};
const bodyText = (page) => page.evaluate(() => document.body.innerText);
const clickText = async (page, selector, source, scope = 'document') => {
    const ok = await page.evaluate((sel, src, sc) => {
        const root = sc === 'dialog' ? document.querySelector('[role="dialog"]') : document;
        const el = [...(root || document).querySelectorAll(sel)].find((e) => e.offsetParent !== null && new RegExp(src, 'i').test(`${e.textContent} ${e.getAttribute('aria-label') || ''}`));
        if (el) el.click();
        return !!el;
    }, selector, source, scope);
    await sleep(300);
    return ok;
};
const noHorizontalScroll = (page) => page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth + 1);
const shot = (page, name, w) => page.screenshot({ path: path.join(OUT, `${LANG_UI}.${name}.${w}.png`), fullPage: false });
const setValue = async (page, selector, value) => {
    await page.focus(selector);
    await page.$eval(selector, (el) => { el.value = ''; });
    await page.type(selector, value);
};
const everyButtonNamed = (page) => page.evaluate(() => [...document.querySelectorAll('button')].filter((b) => b.offsetParent !== null).filter((b) => !(b.textContent || '').trim() && !b.getAttribute('aria-label') && !b.getAttribute('title')).length === 0);
// only the AI Control Center's own controls (the header's organization/language switchers are existing chrome)
const smallTargets = (page) => page.evaluate(() => [...document.querySelectorAll('[role="tablist"] button, [role="tabpanel"] button, [role="tabpanel"] select')]
    .filter((e) => e.offsetParent !== null).map((e) => ({ h: Math.round(e.getBoundingClientRect().height), what: `${e.tagName}:${(e.getAttribute('aria-label') || e.textContent || '').trim().slice(0, 30)}` })).filter(({ h }) => h < 36));

try {
    for (const [w, h] of VIEWPORTS) {
        const backend = createFakeBackend();
        backend.setRole('u-alice', 'org-a2', 'analyst');
        backend.ai.seed.budget(backend.ai.state, 'org-a1', { period: 'daily', limit_usd: 10, spent_usd: 8.6 });
        backend.ai.seed.budget(backend.ai.state, 'org-a1', { period: 'monthly', limit_usd: 100, spent_usd: 12.5 });
        for (let i = 0; i < 4; i++) backend.ai.seed.log(backend.ai.state, 'org-a1', { operation: i % 2 ? 'ad_remix.reconstruct' : 'copy.generate', model_id: 'sample-model', input_tokens: 1200 + i * 100, output_tokens: 400, cached_input_tokens: i * 200, cost_usd: 0.0123 + i * 0.004, user_id: 'u-alice' });
        backend.ai.seed.log(backend.ai.state, 'org-a1', { status: 'error', error_kind: 'rate_limit', cost_usd: null, input_tokens: 0, output_tokens: 0 });
        backend.ai.state.discoverable.openai = [{ model_id: 'sample-model', display_name: null }, { model_id: 'sample-model-mini', display_name: null }];

        // ------------------------------------------------------------ admin flow (owner of Acme One)
        const { page, context } = await openPage(backend, 'alice', [w, h]);
        if (process.env.DEBUG_E2E) page.on('console', (m) => console.log('CONSOLE:', m.text().slice(0, 200))).on('pageerror', (e) => console.log('PAGEERROR:', String(e).slice(0, 300)));
        await page.goto(`${APP_URL}/ai`, { waitUntil: 'domcontentloaded' });
        await settle(page);
        let text = await bodyText(page);
        if (process.env.DEBUG_LAYOUT) console.log('LAYOUT', JSON.stringify(await page.evaluate(() => { const r = (e) => { const b = e.getBoundingClientRect(); return [Math.round(b.left), Math.round(b.width)]; }; const a = document.querySelector('aside'); return { aside: r(a), asideStyle: [getComputedStyle(a).position, getComputedStyle(a).display, getComputedStyle(a).transform], main: r(document.querySelector('main')), body: [innerWidth, document.documentElement.clientWidth] }; })));
        if (process.env.DEBUG_E2E) console.log('BODY:', text.slice(0, 500), 'CALLS:', JSON.stringify(backend.state.calls.slice(-8).map((c) => `${c.method} ${c.path} ${c.org}`)));
        check(`@${w} page renders its title and tabs`, text.includes(T.title) && (await page.$$('[role="tab"]')).length === 4);
        check(`@${w} budget alert is shown (daily 86%)`, !!(await page.$('[data-testid="ai-budget-alert"]')));
        await shot(page, '1-providers-empty', w);
        check(`@${w} no horizontal page scroll (providers)`, await noHorizontalScroll(page));

        // add a key through the UI
        await page.evaluate((label) => [...document.querySelectorAll('[data-testid="provider-openai"] button')].find((b) => b.textContent.includes(label))?.click(), T.add);
        await sleep(300);
        await page.waitForSelector('#ai-key');
        await setValue(page, '#ai-key', KEY);
        await shot(page, '2-key-dialog', w);
        check(`@${w} key field is a password input with autocomplete off`, await page.$eval('#ai-key', (el) => el.type === 'password' && el.autocomplete === 'off'));
        check(`@${w} dialog fits the viewport`, await page.evaluate(() => { const r = document.querySelector('[role="dialog"]').getBoundingClientRect(); return r.left >= 0 && r.right <= innerWidth && r.top >= 0 && r.bottom <= innerHeight + 1; }));
        await clickText(page, 'button', T.save, 'dialog');
        await settle(page);
        text = await bodyText(page);
        const html = await page.content();
        const storage = await page.evaluate(() => JSON.stringify([localStorage, sessionStorage]));
        check(`@${w} only the last four characters are shown`, text.includes('ZZ99') && !text.includes('SECRETSECRET'));
        check(`@${w} the key is nowhere in the DOM, storage or later API answers`, !html.includes(KEY) && !storage.includes('SECRETSECRET') && !JSON.stringify(backend.state.calls.filter((c) => c.method === 'GET')).includes('SECRETSECRET'));
        await shot(page, '3-provider-added', w);

        // models
        await clickText(page, '[role="tab"]', T.models);
        await settle(page, 200);
        await clickText(page, 'button', T.addModel);
        await page.waitForSelector('#ai-model-id');
        await setValue(page, '#ai-model-id', 'sample-model');
        await setValue(page, '#ai-model-input_price_usd_per_mtok', '3');
        await setValue(page, '#ai-model-output_price_usd_per_mtok', '15');
        await setValue(page, '#ai-model-cached_input_price_usd_per_mtok', '0.30');
        await shot(page, '4-model-dialog', w);
        await clickText(page, 'button', T.saveModel, 'dialog');
        await settle(page);
        check(`@${w} model appears in the catalog with its prices`, await page.evaluate(() => /3[.,]00/.test(document.body.innerText) && /0[.,]30/.test(document.body.innerText)));
        check(`@${w} no horizontal page scroll (models)`, await noHorizontalScroll(page));
        await shot(page, '5-models', w);

        // routing
        await clickText(page, '[role="tab"]', T.routing);
        await settle(page, 200);
        const modelId = backend.ai.state.orgs['org-a1'].models[0].id;
        await page.select('#ai-route-default-primary', modelId);
        await sleep(250);
        await clickText(page, 'button', LANG_UI === 'pt-BR' ? 'Salvar rota' : 'Save route');
        await settle(page, 300);
        check(`@${w} route saved on the server`, backend.ai.state.orgs['org-a1'].routes.length === 1);
        check(`@${w} no horizontal page scroll (routing)`, await noHorizontalScroll(page));
        await shot(page, '6-routing', w);

        // usage
        await clickText(page, '[role="tab"]', T.usage);
        await settle(page, 400);
        text = await bodyText(page);
        check(`@${w} usage shows requests, USD cost and limits`, /0[.,]0\d/.test(text) && !!(await page.$('[data-testid="budget-daily"]')));
        check(`@${w} no horizontal page scroll (usage)`, await noHorizontalScroll(page));
        await shot(page, '7-usage', w);
        check(`@${w} every visible button has an accessible name`, await everyButtonNamed(page));
        check(`@${w} no tiny tap targets in the content area`, (await smallTargets(page)).length === 0, JSON.stringify(await smallTargets(page)));

        // tablist keyboard navigation
        await page.focus('[role="tab"][aria-selected="true"]');
        await page.keyboard.press('ArrowLeft');
        await sleep(200);
        check(`@${w} arrow keys move between tabs`, await page.evaluate(() => document.activeElement?.getAttribute('role') === 'tab' && document.activeElement.getAttribute('aria-selected') === 'true'));

        // sidebar link (desktop drawer is off-canvas on mobile, so only assert on the DOM)
        check(`@${w} the sidebar links to the AI Control Center`, await page.evaluate((label) => [...document.querySelectorAll('aside a')].some((a) => a.textContent.includes(label)), T.nav));

        // ------------------------------------------------------------ organization switch: read-only role, nothing carried over
        await page.evaluate(() => localStorage.setItem('tenant:org:u-alice', 'org-a2'));
        await page.goto(`${APP_URL}/ai`, { waitUntil: 'domcontentloaded' });
        await settle(page);
        text = await bodyText(page);
        check(`@${w} other organization: read-only notice, no key/model of the first one`, text.toLowerCase().includes(T.readOnly) && !text.includes('ZZ99') && !text.includes('sample-model'));
        check(`@${w} analyst gets no management buttons`, await page.evaluate((label) => ![...document.querySelectorAll('button')].some((b) => b.textContent.includes(label)), T.add));
        await shot(page, '8-readonly-org', w);
        await context.close();

        // ------------------------------------------------------------ viewer: no access, no sidebar link, no AI request
        const viewer = await openPage(backend, 'alice', [w, h]);
        backend.setRole('u-alice', 'org-a1', 'viewer');
        backend.state.calls.length = 0;
        await viewer.page.goto(`${APP_URL}/ai`, { waitUntil: 'domcontentloaded' });
        await settle(viewer.page);
        text = await bodyText(viewer.page);
        check(`@${w} viewer sees the no-access message`, text.includes(T.noAccess));
        check(`@${w} viewer triggers no /ai request`, backend.state.calls.every((c) => !c.path.startsWith('/ai/')));
        check(`@${w} viewer has no sidebar entry`, await viewer.page.evaluate((label) => ![...document.querySelectorAll('aside a')].some((a) => a.textContent.includes(label)), T.nav));
        await shot(viewer.page, '9-viewer', w);
        await viewer.context.close();

        // ------------------------------------------------------------ service unavailable
        backend.setRole('u-alice', 'org-a1', 'owner');
        const down = await openPage(backend, 'alice', [w, h]);
        backend.ai.state.failNext = { status: 503, kind: 'unavailable' };
        await down.page.goto(`${APP_URL}/ai`, { waitUntil: 'domcontentloaded' });
        await settle(down.page);
        check(`@${w} unavailable service shows a retry state`, !!(await down.page.$('[role="alert"] button')));
        await shot(down.page, '10-unavailable', w);
        await down.context.close();
    }
} finally {
    await browser.close();
}

const failed = results.filter((r) => !r.ok);
console.log(`\n${results.length - failed.length}/${results.length} checks passed (${LANG_UI}); screenshots in ${path.resolve(OUT)}`);
process.exit(failed.length ? 1 : 0);
