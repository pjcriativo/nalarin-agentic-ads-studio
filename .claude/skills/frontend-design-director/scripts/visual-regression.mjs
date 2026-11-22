#!/usr/bin/env node
// Pixel-diff two running builds of the app (reference vs new). Read-only: mocks the API by request interception.
// Env: BASE_URL, NEW_URL, OUT, ROUTES (comma list), VIEWPORTS ("WxH,..."), LANG_UI, BROWSER (exe path), PUPPETEER_DIR (dir with node_modules)
import { createRequire } from 'node:module';
import fs from 'node:fs';
import path from 'node:path';

const require = createRequire(path.join(process.env.PUPPETEER_DIR || process.cwd(), 'x.js'));
const puppeteer = require('puppeteer-core');
const { PNG } = require('pngjs');
const pixelmatch = require('pixelmatch');

const { BASE_URL, NEW_URL, OUT = './vr-out', LANG_UI = 'en-US' } = process.env;
const ROUTES = (process.env.ROUTES || '/').split(',');
const VIEWPORTS = (process.env.VIEWPORTS || '1440x900,390x844').split(',').map((v) => v.split('x').map(Number));
const EXE = process.env.BROWSER || ['C:/Program Files (x86)/Microsoft/Edge/Application/msedge.exe', 'C:/Program Files/Google/Chrome/Application/chrome.exe'].find(fs.existsSync);
const API = 'http://localhost:8000/api/v1';
if (!BASE_URL || !NEW_URL || !EXE) {
    console.error('Set BASE_URL, NEW_URL and a browser executable (BROWSER=...)');
    process.exit(2);
}
fs.mkdirSync(OUT, { recursive: true });

const user = { id: 1, email: 'demo@example.com', name: 'Demo User', is_active: true, is_superuser: true, roles: [] };
const mock = (method, url) => {
    const p = url.replace(API, '').split('?')[0];
    if (method !== 'GET') return {};
    if (p === '/auth/me') return user;
    return /s$/.test(p) || /(templates|winning-ads|generated-ads)/.test(p) ? [] : {};
};

const browser = await puppeteer.launch({ executablePath: EXE, headless: 'new', args: ['--no-sandbox'] });
let bad = 0;
try {
    for (const route of ROUTES) {
        for (const [w, h] of VIEWPORTS) {
            const files = {};
            const slug = `${route === '/' ? 'home' : route.slice(1).replace(/\//g, '_')}.${w}x${h}`;
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
                        const cors = {
                            'access-control-allow-origin': r.headers().origin || '*',
                            'access-control-allow-credentials': 'true',
                            'access-control-allow-headers': r.headers()['access-control-request-headers'] || '*',
                            'access-control-allow-methods': '*',
                        };
                        return r.method() === 'OPTIONS'
                            ? r.respond({ status: 204, headers: cors })
                            : r.respond({ status: 200, contentType: 'application/json', headers: cors, body: JSON.stringify(mock(r.method(), u)) });
                    }
                    return /^https?:\/\/(localhost|127\.0\.0\.1)/.test(u) ? r.continue() : r.abort();
                });
                await page.goto(origin + route, { waitUntil: 'domcontentloaded' });
                await page.waitForNetworkIdle({ idleTime: 400, timeout: 15000 }).catch(() => {});
                await new Promise((r) => setTimeout(r, 400));
                // the language switcher is new chrome, not a regression
                await page.addStyleTag({ content: 'div:has(> [data-testid^="lang-"]){visibility:hidden!important}' });
                files[label] = path.join(OUT, `${slug}.${label}.png`);
                await page.screenshot({ path: files[label] });
                await ctx.close();
            }
            const A = PNG.sync.read(fs.readFileSync(files.base));
            const B = PNG.sync.read(fs.readFileSync(files.new));
            if (A.width !== B.width || A.height !== B.height) {
                console.log(`DIFF ${route} ${w}x${h}: size ${A.width}x${A.height} vs ${B.width}x${B.height}`);
                bad++;
                continue;
            }
            const D = new PNG({ width: A.width, height: A.height });
            const n = pixelmatch(A.data, B.data, D.data, A.width, A.height, { threshold: 0.1 });
            if (n) {
                fs.writeFileSync(path.join(OUT, `${slug}.diff.png`), PNG.sync.write(D));
                bad++;
            }
            console.log(`${n ? 'DIFF' : 'SAME'} ${route} ${w}x${h}: ${n} px`);
        }
    }
} finally {
    await browser.close();
}
process.exit(bad ? 1 : 0);
