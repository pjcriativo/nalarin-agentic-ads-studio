#!/usr/bin/env node
// Browser end-to-end for the multi-tenant interface. Drives a REAL browser against a running
// build of the app (vite dev/preview) while the backend is a tenant-aware in-memory fake
// (src/__tests__/fakeBackend.js) wired through request interception: the real API and every
// ad platform are never contacted, nothing mutable can reach an account.
//
// Env: APP_URL (default http://localhost:5302), OUT (screenshots dir), LANG_UI (en-US|pt-BR),
//      BROWSER (exe path), PUPPETEER_DIR (dir whose node_modules has puppeteer-core)
// Run: PUPPETEER_DIR="$TEMP/vr" APP_URL=http://localhost:5302 node tests/e2e/multitenant.e2e.mjs
import { createRequire } from 'node:module';
import fs from 'node:fs';
import path from 'node:path';
import { createFakeBackend } from '../../src/__tests__/fakeBackend.js';

const require = createRequire(path.join(process.env.PUPPETEER_DIR || process.cwd(), 'x.js'));
const puppeteer = require('puppeteer-core');

const APP_URL = process.env.APP_URL || 'http://localhost:5302';
const OUT = process.env.OUT || './e2e-out';
const LANG_UI = process.env.LANG_UI || 'en-US';
const API = 'http://localhost:8000/api/v1';
const EXE = process.env.BROWSER || ['C:/Program Files (x86)/Microsoft/Edge/Application/msedge.exe', 'C:/Program Files/Google/Chrome/Application/chrome.exe'].find(fs.existsSync);
const VIEWPORTS = [[1440, 900], [820, 1000], [390, 844]];
if (!EXE) { console.error('No browser found: set BROWSER'); process.exit(2); }
fs.mkdirSync(OUT, { recursive: true });

const results = [];
const check = (name, ok, detail = '') => {
    results.push({ name, ok: !!ok });
    console.log(`${ok ? 'PASS' : 'FAIL'}  ${name}${ok || !detail ? '' : `  -> ${detail}`}`);
};
const sleep = (ms) => new Promise((r) => setTimeout(r, ms));

const browser = await puppeteer.launch({ executablePath: EXE, headless: 'new', args: ['--no-sandbox'] });

/** One isolated browser context + page wired to a (shared) fake backend. */
async function openPage(backend, userKey, [w, h], { selection } = {}) {
    const context = await browser.createBrowserContext();
    const page = await context.newPage();
    await page.setViewport({ width: w, height: h });
    const tokens = userKey ? backend.loginTokens(userKey) : null;
    const userId = userKey ? backend.state.users[userKey].id : null;
    await page.evaluateOnNewDocument((lang, t, uid, sel) => {
        Object.defineProperty(navigator, 'language', { get: () => lang });
        Object.defineProperty(navigator, 'languages', { get: () => [lang] });
        if (!sessionStorage.getItem('__seeded')) { // seed once: a reload must keep what the app stored
            localStorage.setItem('app_language', lang);
            if (t) { localStorage.setItem('accessToken', t.access_token); localStorage.setItem('refreshToken', t.refresh_token); }
            if (sel) localStorage.setItem(`tenant:org:${uid}`, sel);
            sessionStorage.setItem('__seeded', '1');
        }
        document.addEventListener('DOMContentLoaded', () => {
            const s = document.createElement('style');
            s.textContent = '*{animation:none!important;transition:none!important;caret-color:transparent!important}';
            document.head.appendChild(s);
        });
    }, LANG_UI, tokens, userId, selection || null);
    await page.setRequestInterception(true);
    page.on('request', async (request) => {
        const url = request.url();
        if (url.startsWith(API)) {
            const h2 = request.headers();
            const cors = {
                'access-control-allow-origin': h2.origin || '*',
                'access-control-allow-credentials': 'true',
                'access-control-allow-headers': h2['access-control-request-headers'] || '*',
                'access-control-allow-methods': '*',
            };
            if (request.method() === 'OPTIONS') return request.respond({ status: 204, headers: cors });
            const init = { method: request.method(), body: request.postData() || undefined, headers: {} };
            if (h2.authorization) init.headers.Authorization = h2.authorization;
            if (h2['x-organization-id']) init.headers['X-Organization-Id'] = h2['x-organization-id'];
            const res = await backend.fetch(url, init);
            return request.respond({ status: res.status, contentType: 'application/json', headers: cors, body: JSON.stringify(await res.json()) });
        }
        return /^https?:\/\/(localhost|127\.0\.0\.1)/.test(url) ? request.continue() : request.abort();
    });
    return { page, context };
}

const settle = async (page, ms = 500) => {
    await page.waitForNetworkIdle({ idleTime: 300, timeout: 15000 }).catch(() => {});
    await sleep(ms);
};
const text = (page) => page.evaluate(() => document.body.innerText);
const clickText = async (page, selector, re) => {
    const ok = await page.evaluate((sel, source) => {
        const el = [...document.querySelectorAll(sel)].find((e) => new RegExp(source, 'i').test(e.textContent));
        if (el) el.click();
        return !!el;
    }, selector, re.source);
    await sleep(250);
    return ok;
};
// The header exists twice (mobile bar / desktop corner); only one is displayed at a given width.
const clickVisible = async (page, selector) => {
    const ok = await page.evaluate((sel) => {
        const el = [...document.querySelectorAll(sel)].find((e) => e.offsetParent !== null);
        if (el) el.click();
        return !!el;
    }, selector);
    await sleep(250);
    return ok;
};
const visibleBox = (page, selector) => page.evaluate((sel) => {
    const el = [...document.querySelectorAll(sel)].find((e) => e.offsetParent !== null);
    if (!el) return null;
    const r = el.getBoundingClientRect();
    return { w: r.width, h: r.height, visible: r.width > 0 && r.right <= window.innerWidth && r.left >= 0 };
}, selector);
const noHorizontalScroll = (page) => page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth + 1);
const shot = (page, name, w) => page.screenshot({ path: path.join(OUT, `${LANG_UI}.${name}.${w}.png`) });
const tenantCalls = (backend) => backend.calls().filter((c) => ['/brands', '/profiles', '/dashboard/stats', '/google-ads/campaigns'].includes(c.path));

try {
    // ------------------------------------------------------------------ 1. header on every tenant request
    {
        const backend = createFakeBackend();
        const { page, context } = await openPage(backend, 'alice', [1440, 900]);
        await page.goto(`${APP_URL}/brands`, { waitUntil: 'domcontentloaded' });
        await settle(page);
        const chip = await page.evaluate(() => [...document.querySelectorAll('[data-testid="org-switcher-name"]')].find((e) => e.offsetParent !== null)?.textContent ?? null);
        check('chip shows the active organization', chip === 'Acme One', chip);
        const calls = tenantCalls(backend);
        check('every tenant request carries X-Organization-Id', calls.length > 0 && calls.every((c) => c.org === 'org-a1'), JSON.stringify(calls.map((c) => c.org)));
        check('only the active tenant data is on screen', (await text(page)).includes('Alpha One Brand') && !(await text(page)).includes('Alpha Two Brand'));

        // -------------------------------------------------------------- 2. switch, persistence, reload
        await clickVisible(page, '[data-testid="org-switcher"]');
        await sleep(200);
        check('dropdown lists both organizations with roles', await page.evaluate(() => [...document.querySelectorAll('[role="option"]')].map((o) => o.textContent).join('|')).then((t) => /Acme One/.test(t) && /Acme Two/.test(t) && /Owner|Proprietário/.test(t) && /Viewer|Visualizador/.test(t)));
        await clickVisible(page, '[data-testid="org-option-org-a2"]');
        await settle(page);
        const afterSwitch = await text(page);
        check('after switching only the new tenant data is shown', afterSwitch.includes('Alpha Two Brand') && !afterSwitch.includes('Alpha One Brand'));
        check('persisted per user', await page.evaluate(() => localStorage.getItem('tenant:org:u-alice')) === 'org-a2');
        backend.state.calls.length = 0;
        await page.reload({ waitUntil: 'domcontentloaded' });
        await settle(page);
        check('selection survives a reload and the first tenant request already targets it', tenantCalls(backend).length > 0 && tenantCalls(backend).every((c) => c.org === 'org-a2'));
        check('chip still shows the persisted organization', (await page.evaluate(() => [...document.querySelectorAll('[data-testid="org-switcher-name"]')].find((e) => e.offsetParent !== null)?.textContent)) === 'Acme Two');

        // -------------------------------------------------------------- 3. logout clears everything
        await clickText(page, 'button', /Logout|Sair/);
        await sleep(600);
        const keys = await page.evaluate(() => Object.keys(localStorage));
        check('logout removes tokens and every tenant key', !keys.includes('accessToken') && !keys.some((k) => k.startsWith('tenant:')), keys.join(','));
        await context.close();
    }

    // ------------------------------------------------------------------ 4. a different user in the same browser
    {
        const backend = createFakeBackend();
        const { page, context } = await openPage(backend, 'bob', [1440, 900], { selection: 'org-a1' }); // bob must NOT inherit alice's org id
        await page.goto(`${APP_URL}/brands`, { waitUntil: 'domcontentloaded' });
        await settle(page);
        check("a stale selection from a different user's session is ignored (bob sees only Beta)", tenantCalls(backend).every((c) => c.org !== 'org-a1') && (await text(page)).includes('Beta Brand'));
        await context.close();
    }

    // ------------------------------------------------------------------ 5. membership revoked while open
    {
        const backend = createFakeBackend();
        const { page, context } = await openPage(backend, 'alice', [1440, 900]);
        await page.goto(`${APP_URL}/brands`, { waitUntil: 'domcontentloaded' });
        await settle(page);
        backend.revoke('u-alice', 'org-a1');
        await clickText(page, 'a', /^Dashboard$|^Painel$/);
        await settle(page, 800);
        const t = await text(page);
        check('revocation: user is told and moved to another organization', /no longer have access|não tem mais acesso/i.test(t) && (await page.evaluate(() => [...document.querySelectorAll('[data-testid="org-switcher-name"]')].find((e) => e.offsetParent !== null)?.textContent)) === 'Acme Two');
        await context.close();
    }

    // ------------------------------------------------------------------ 6. Google Ads: approval flow in the browser
    {
        const backend = createFakeBackend();
        const { page, context } = await openPage(backend, 'alice', [1440, 900]);
        await page.goto(`${APP_URL}/google-ads`, { waitUntil: 'domcontentloaded' });
        await settle(page);
        const isEnable = (c) => /\/google-ads\/campaigns\/.+\/enable$/.test(c.path);
        await clickText(page, 'button', /^Enable$|^Ativar$/);
        await sleep(400);
        check('Enable opens the approval dialog and calls nothing', (await text(page)).match(/starts real spend|inicia gasto real/i) && !backend.calls().some(isEnable));
        await page.type('#spend-budget', '5');
        await clickText(page, 'button', /Request approval|Pedir aprovação/);
        await settle(page, 400);
        check('request is persisted as pending (server state)', backend.state.approvals.length === 1 && backend.state.approvals[0].status === 'pending');
        await page.type('#spend-cap', '6');
        await shot(page, 'approval-pending', 1440);
        await clickText(page, 'button', /^Approve$|^Aprovar$/);
        await settle(page, 400);
        check('approved by a human with a budget cap', backend.state.approvals[0].status === 'approved' && backend.state.approvals[0].max_budget_micros === 6_000_000 && !backend.calls().some(isEnable));
        await clickText(page, 'button', /Enable campaign|Ativar campanha/);
        await settle(page, 500);
        const enables = backend.calls().filter(isEnable);
        check('enable is sent once, only now, with the approval id', enables.length === 1 && enables[0].body.approval_id === backend.state.approvals[0].id && backend.state.enabledCampaigns.length === 1);
        await context.close();
    }

    // ------------------------------------------------------------------ 7. RBAC: same user, viewer in the other organization
    {
        const backend = createFakeBackend();
        const { page, context } = await openPage(backend, 'alice', [1440, 900], { selection: 'org-a2' });
        await page.goto(`${APP_URL}/google-ads`, { waitUntil: 'domcontentloaded' });
        await settle(page);
        const denied = await page.$('[data-testid="connection-denied"]');
        const create = await clickText(page, 'button', /Create campaign|Criar campanha/);
        check('viewer role: connections are refused by the server, the page says so and offers no actions', !!denied && !create);
        await context.close();
    }

    // ------------------------------------------------------------------ 8. screenshots + responsive checks (3 viewports)
    for (const [w, h] of VIEWPORTS) {
        const backend = createFakeBackend();
        const { page, context } = await openPage(backend, 'alice', [w, h]);
        await page.goto(`${APP_URL}/`, { waitUntil: 'domcontentloaded' });
        await settle(page);
        await shot(page, 'overview-chip', w);
        check(`no horizontal scroll on overview @${w}`, await noHorizontalScroll(page));
        const box = await visibleBox(page, '[data-testid="org-switcher"]');
        check(`org selector is visible and inside the viewport @${w}`, box && box.visible, JSON.stringify(box));
        if (w <= 420) check('org selector touch target >= 40px on mobile', box && box.w >= 40 && box.h >= 40, JSON.stringify(box));

        await clickVisible(page, '[data-testid="org-switcher"]');
        await sleep(250);
        await shot(page, 'dropdown', w);
        const panel = await page.evaluate(() => { const r = document.querySelector('[role="listbox"]').getBoundingClientRect(); return r.left >= 0 && r.right <= window.innerWidth; });
        check(`dropdown stays inside the viewport @${w}`, panel);
        await clickText(page, 'button', /View role permissions|Ver permissões por papel/);
        await settle(page, 500);
        await shot(page, 'permissions', w);
        check(`permissions dialog lists the five roles @${w}`, await page.evaluate(() => document.querySelectorAll('[data-testid="role-matrix"] thead th').length === 6));
        check(`no horizontal PAGE scroll with the permissions dialog @${w}`, await noHorizontalScroll(page));
        await page.keyboard.press('Escape');
        await sleep(200);

        await page.goto(`${APP_URL}/google-ads`, { waitUntil: 'domcontentloaded' });
        await settle(page);
        await shot(page, 'google', w);
        check(`google ads page has no horizontal page scroll @${w}`, await noHorizontalScroll(page));
        await clickText(page, 'button', /^Enable$|^Ativar$/);
        await sleep(400);
        await shot(page, 'approval-request', w);
        const fits = await page.evaluate(() => { const r = document.querySelector('[role="dialog"]').getBoundingClientRect(); return r.left >= 0 && r.right <= window.innerWidth && r.top >= 0 && r.bottom <= window.innerHeight + 1; });
        check(`approval dialog fits the viewport @${w}`, fits);
        await context.close();
    }
} finally {
    await browser.close();
}

const failed = results.filter((r) => !r.ok);
console.log(`\n${results.length - failed.length}/${results.length} checks passed (${LANG_UI}); screenshots in ${path.resolve(OUT)}`);
process.exit(failed.length ? 1 : 0);
