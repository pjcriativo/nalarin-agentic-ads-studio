#!/usr/bin/env node
/**
 * Pixel regression of the APPROVED screens: reference build vs new build, with a mocked API that includes a real organization
 * membership (so the full Layout, sidebar and header render, unlike a tenant-less mock).
 *
 *   MODE=without-agents  the user's role has NO agents:* permission  -> every screen must be IDENTICAL (0 differing pixels)
 *   MODE=with-agents     the role has agents:read/run/manage       -> the ONLY allowed difference is the new "AI Agents"
 *                        sidebar entry: every differing pixel must lie inside the sidebar column (desktop widths).
 *
 * Read-only: the API is mocked by request interception; the real backend is never called, nothing is written.
 * Env: BASE_URL, NEW_URL, OUT, ROUTES (comma list), VIEWPORTS ("WxH,..."), MODE, LANG_UI, BROWSER, PUPPETEER_DIR.
 */
import { createRequire } from 'node:module';
import fs from 'node:fs';
import path from 'node:path';

const require = createRequire(path.join(process.env.PUPPETEER_DIR || process.cwd(), 'x.js'));
const puppeteer = require('puppeteer-core');
const { PNG } = require('pngjs');
const pixelmatch = require('pixelmatch');

const { BASE_URL, NEW_URL, OUT = './vr-out', LANG_UI = 'en-US', MODE = 'without-agents' } = process.env;
const ROUTES = (process.env.ROUTES || '/').split(',');
const VIEWPORTS = (process.env.VIEWPORTS || '1440x900,820x1000,390x844').split(',').map((v) => v.split('x').map(Number));
const EXE = process.env.BROWSER || ['C:/Program Files (x86)/Microsoft/Edge/Application/msedge.exe', 'C:/Program Files/Google/Chrome/Application/chrome.exe'].find(fs.existsSync);
const API = 'http://localhost:8000/api/v1';
const SIDEBAR_WIDTH = 270; // w-64 = 256px, plus the collapse handle that overhangs it
if (!BASE_URL || !NEW_URL || !EXE) { console.error('Set BASE_URL, NEW_URL and a browser executable'); process.exit(2); }
fs.mkdirSync(OUT, { recursive: true });

const BASE_PERMS = ['org:read', 'members:read', 'brands:read', 'brands:write', 'products:read', 'profiles:read', 'ads:read', 'ads:draft', 'templates:read', 'campaigns:read', 'research:read',
    'reports:read', 'connections:read', 'connections:manage', 'ai:read', 'ai:manage', 'ai:use', 'members:manage', 'audit:read'];
const AGENT_PERMS = ['agents:read', 'agents:run', 'agents:manage'];
const perms = MODE === 'with-agents' ? [...BASE_PERMS, ...AGENT_PERMS] : BASE_PERMS;
const user = { id: 1, email: 'demo@example.com', name: 'Demo User', is_active: true, is_superuser: false, roles: [] };
const orgs = [{ id: 'org-1', name: 'Acme Org', slug: 'acme', role: 'owner', permissions: perms }];
const mock = (method, url) => {
    const p = url.replace(API, '').split('?')[0];
    if (method !== 'GET') return {};
    if (p === '/auth/me') return user;
    if (p === '/organizations') return orgs;
    if (p === '/ai/providers') return [{ provider: 'openai', has_active_credential: false }];
    return /s$/.test(p) || /(templates|winning-ads|generated-ads)/.test(p) ? [] : {};
};

const browser = await puppeteer.launch({ executablePath: EXE, headless: 'new', args: ['--no-sandbox'] });
let bad = 0;
try {
    for (const route of ROUTES) {
        for (const [w, h] of VIEWPORTS) {
            const files = {};
            const slug = `${MODE}.${route === '/' ? 'home' : route.slice(1).replace(/\//g, '_')}.${w}x${h}`;
            for (const [label, origin] of [['base', BASE_URL], ['new', NEW_URL]]) {
                const ctx = await browser.createBrowserContext();
                const page = await ctx.newPage();
                await page.setViewport({ width: w, height: h });
                await page.evaluateOnNewDocument((lang) => {
                    Object.defineProperty(navigator, 'language', { get: () => lang });
                    Object.defineProperty(navigator, 'languages', { get: () => [lang] });
                    localStorage.setItem('accessToken', 'demo');
                    localStorage.setItem('app_language', lang);
                    Math.random = () => 0.5;
                    document.addEventListener('DOMContentLoaded', () => {
                        const s = document.createElement('style');
                        s.textContent = '*{animation:none!important;transition:none!important;caret-color:transparent!important}';
                        document.head.appendChild(s);
                    });
                }, LANG_UI);
                await page.setRequestInterception(true);
                page.on('request', (r) => {
                    const u = r.url();
                    if (u.startsWith(API)) {
                        const cors = { 'access-control-allow-origin': r.headers().origin || '*', 'access-control-allow-credentials': 'true',
                            'access-control-allow-headers': r.headers()['access-control-request-headers'] || '*', 'access-control-allow-methods': '*' };
                        return r.method() === 'OPTIONS' ? r.respond({ status: 204, headers: cors })
                            : r.respond({ status: 200, contentType: 'application/json', headers: cors, body: JSON.stringify(mock(r.method(), u)) });
                    }
                    return /^https?:\/\/(localhost|127\.0\.0\.1)/.test(u) ? r.continue() : r.abort();
                });
                await page.goto(origin + route, { waitUntil: 'domcontentloaded' });
                await page.waitForNetworkIdle({ idleTime: 400, timeout: 15000 }).catch(() => {});
                await new Promise((r) => setTimeout(r, 500));
                await page.addStyleTag({ content: 'div:has(> [data-testid^="lang-"]){visibility:hidden!important}' });
                files[label] = path.join(OUT, `${slug}.${label}.png`);
                await page.screenshot({ path: files[label] });
                await ctx.close();
            }
            const A = PNG.sync.read(fs.readFileSync(files.base));
            const B = PNG.sync.read(fs.readFileSync(files.new));
            if (A.width !== B.width || A.height !== B.height) { console.log(`DIFF-SIZE ${route} ${w}x${h}`); bad += 1; continue; }
            const diff = new PNG({ width: A.width, height: A.height });
            const count = pixelmatch(A.data, B.data, diff.data, A.width, A.height, { threshold: 0.1 });
            let box = null;
            if (count) {
                let x0 = A.width; let y0 = A.height; let x1 = 0; let y1 = 0;
                for (let y = 0; y < A.height; y += 1) {
                    for (let x = 0; x < A.width; x += 1) {
                        const i = (y * A.width + x) * 4;
                        if (diff.data[i] === 255 && diff.data[i + 1] === 0) { x0 = Math.min(x0, x); x1 = Math.max(x1, x); y0 = Math.min(y0, y); y1 = Math.max(y1, y); }
                    }
                }
                box = { x0, y0, x1, y1 };
                fs.writeFileSync(path.join(OUT, `${slug}.diff.png`), PNG.sync.write(diff));
            }
            const sidebarOnly = Boolean(box) && box.x1 <= SIDEBAR_WIDTH;
            const ok = MODE === 'without-agents' ? count === 0 : (count === 0 || sidebarOnly);
            if (!ok) bad += 1;
            console.log(`${ok ? 'SAME' : 'DIFF'} ${MODE} ${route} ${w}x${h}: ${count} px${box ? ` (bbox x ${box.x0}-${box.x1}, y ${box.y0}-${box.y1}${sidebarOnly ? ', inside the sidebar' : ', OUTSIDE the sidebar'})` : ''}`);
        }
    }
} finally {
    await browser.close();
}
console.log(bad ? `\n${bad} regression(s)` : '\nNo regression');
process.exit(bad ? 1 : 0);
