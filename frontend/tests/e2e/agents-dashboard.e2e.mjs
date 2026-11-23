#!/usr/bin/env node
/**
 * Browser E2E of the AI Agents dashboard against the REAL stack of a LOCAL development environment:
 *   real frontend (vite) + real FastAPI backend + real PostgreSQL (local disposable container) + the REAL agent worker process.
 * The ONLY simulated part is the AI model: the worker is started through backend/tests/unit/worker_launcher.py, which replaces the AI
 * Gateway with a scripted one, so NO paid call is made, NO provider and NO Google account is contacted, and nothing is published.
 *
 * Needs: Edge/Chrome, puppeteer-core outside the repo (PUPPETEER_DIR), the backend on :8000 (migrated to head, admin user seeded by
 * init_db.py), the frontend dev server, and Python with the backend requirements. NEVER point it at a shared or real environment:
 * it creates a provider key (a fake string), a model, a route and tasks in the organization of the admin user.
 *
 * Env: APP_URL (default http://localhost:5173), API_URL (http://localhost:8000/api/v1), E2E_EMAIL / E2E_PASSWORD, OUT (screenshots),
 *      PUPPETEER_DIR, BROWSER (exe path), PYTHON (default "python"), BACKEND_DIR (default ../../../backend relative to this file).
 */
import { createRequire } from 'node:module';
import { spawn } from 'node:child_process';
import fs from 'node:fs';
import path from 'node:path';
import { fileURLToPath } from 'node:url';

const here = path.dirname(fileURLToPath(import.meta.url));
const require = createRequire(path.join(process.env.PUPPETEER_DIR || process.cwd(), 'x.js'));
const puppeteer = require('puppeteer-core');

const APP = process.env.APP_URL || 'http://localhost:5173';
const API = process.env.API_URL || 'http://localhost:8000/api/v1';
const EMAIL = process.env.E2E_EMAIL || 'admin@example.com';
const PASSWORD = process.env.E2E_PASSWORD || 'admin123';
const OUT = process.env.OUT || path.join(process.cwd(), 'e2e-out');
const PYTHON = process.env.PYTHON || 'python';
const BACKEND = process.env.BACKEND_DIR || path.resolve(here, '../../../backend');
const EXE = process.env.BROWSER || ['C:/Program Files (x86)/Microsoft/Edge/Application/msedge.exe', 'C:/Program Files/Google/Chrome/Application/chrome.exe'].find(fs.existsSync);
if (!EXE) { console.error('No browser found: set BROWSER'); process.exit(2); }
if (!/^https?:\/\/(localhost|127\.0\.0\.1)/.test(APP) || !/^https?:\/\/(localhost|127\.0\.0\.1)/.test(API)) { console.error('Refusing to run against a non-local environment'); process.exit(2); }
fs.mkdirSync(OUT, { recursive: true });

const results = [];
let currentPage = null;
let failures = 0;
const check = (name, ok, detail = '') => {
    results.push({ name, ok });
    console.log(`${ok ? 'PASS' : 'FAIL'}  ${name}${ok ? '' : `  ${detail}`}`);
    if (!ok && currentPage) {   // keep what the screen showed, to diagnose without re-running blind
        failures += 1;
        const base = path.join(OUT, `fail-${failures}`);
        currentPage.screenshot({ path: `${base}.png` }).catch(() => {});
        currentPage.evaluate(() => document.body.innerText).then((t) => fs.writeFileSync(`${base}.txt`, `${name}

${t}`)).catch(() => {});
    }
};
const sleep = (ms) => new Promise((r) => setTimeout(r, ms));

// ------------------------------------------------------------------------------------------------ API helpers (seeding only)
let token = null;
let orgId = null;
const api = async (method, p, body) => {
    const response = await fetch(`${API}${p}`, {
        method, headers: { 'Content-Type': 'application/json', Authorization: `Bearer ${token}`, ...(orgId ? { 'X-Organization-Id': orgId } : {}) }, body: body ? JSON.stringify(body) : undefined,
    });
    return { status: response.status, body: await response.json().catch(() => null) };
};
async function seedAi() {
    const login = await fetch(`${API}/auth/login`, { method: 'POST', headers: { 'Content-Type': 'application/x-www-form-urlencoded' }, body: new URLSearchParams({ username: EMAIL, password: PASSWORD }) });
    token = (await login.json()).access_token;
    const orgs = (await api('GET', '/organizations')).body;
    orgId = orgs[0].id;
    const credentials = (await api('GET', '/ai/credentials')).body;
    if (!credentials.some((c) => c.provider === 'openai' && c.status === 'active')) {
        const created = await api('POST', '/ai/credentials', { provider: 'openai', api_key: 'sk-e2e-not-a-real-key-0000000000', verify: false });
        if (created.status >= 300) throw new Error(`could not seed a provider key: ${created.status}`);
    }
    const models = (await api('GET', '/ai/models')).body;
    let model = models.find((m) => m.model_id === 'e2e-scripted-model');
    if (!model) {
        const created = await api('POST', '/ai/models', { provider: 'openai', model_id: 'e2e-scripted-model', input_price_usd_per_mtok: '1', output_price_usd_per_mtok: '1' });
        if (created.status >= 300) throw new Error(`could not seed a model: ${created.status}`);
        model = created.body;
    }
    await api('PUT', '/ai/routing/default', { primary_model_id: model.id, fallback_model_ids: [] });
    // leftovers of a previous (failed) run would fill the organization's active-task cap
    for (const status of ['pending', 'running']) {
        for (const task of (await api('GET', `/agents/tasks?status=${status}&limit=100`)).body || []) await api('POST', `/agents/tasks/${task.id}/cancel`);
    }
    // a worker that was killed a moment ago still counts as online until its last report is older than the online window
    for (let i = 0; i < 60; i += 1) {
        if (!(await api('GET', '/agents/worker/status')).body?.online) return;
        await sleep(1500);
    }
    throw new Error('a worker is still reporting in: stop it before running the E2E');
}

// ------------------------------------------------------------------------------------------------ the scripted worker
let worker = null;
function startWorker(script) {
    worker = spawn(PYTHON, ['-m', 'tests.unit.worker_launcher'], {
        cwd: BACKEND,
        env: { ...process.env, PYTHONUNBUFFERED: '1', WORKER_SCRIPT: JSON.stringify(script), AGENT_WORKER_HEALTH_PORT: '0', AGENT_POLL_INTERVAL_SECONDS: '0.5', AGENT_WORKER_BEAT_SECONDS: '3' },
        stdio: ['ignore', 'pipe', 'pipe'],
    });
    worker.stdout.on('data', () => {});
    worker.stderr.on('data', () => {});
}
const stopWorker = () => { if (worker && worker.exitCode === null) worker.kill(); };

// ------------------------------------------------------------------------------------------------ browser helpers
const text = (page) => page.evaluate(() => document.body.innerText);
const waitForText = async (page, needle, timeout = 30000) => {
    try { await page.waitForFunction((n) => document.body.innerText.includes(n), { timeout }, needle); return true; } catch { return false; }
};
const clickAria = async (page, name, role = 'button') => { await page.locator(`::-p-aria([role="${role}"][name="${name}"])`).setTimeout(10000).click(); };
const noOverflow = (page) => page.evaluate(() => document.scrollingElement.scrollWidth <= window.innerWidth + 1);

const PRODUCT_ANSWER = {
    summary: 'The widget is a durable, mid-priced product. This summary is a scripted E2E answer, not a real analysis.',
    value_propositions: ['Lasts longer than the alternatives (claim to verify)'], claims_to_verify: ['Two-year durability'],
    data_basis: [{ statement: 'Buyers are probably price sensitive', data_kind: 'hypothesis', source: 'model_knowledge', metrics: [] }],
};

const browser = await puppeteer.launch({ executablePath: EXE, headless: 'new', args: ['--no-sandbox'] });
try {
    await seedAi();
    const context = await browser.createBrowserContext();
    const page = await context.newPage();
    currentPage = page;
    page.on('pageerror', (error) => check('no uncaught page error', false, String(error)));
    await page.setViewport({ width: 1440, height: 900 });
    await page.evaluateOnNewDocument(() => { localStorage.setItem('app_language', 'en-US'); });

    // ---- login through the real form
    await page.goto(`${APP}/login`, { waitUntil: 'domcontentloaded' });
    await page.waitForSelector('#email');
    await page.type('#email', EMAIL);
    await page.type('#password', PASSWORD);
    await page.click('button[type=submit]');
    await page.waitForSelector('nav a[href="/agents"]', { timeout: 20000 });
    check('the sidebar has the AI Agents module for a role with agents:read', true);

    // ---- catalogue
    await page.click('nav a[href="/agents"]');
    await page.waitForSelector('[data-testid="agents-grid"]', { timeout: 20000 });
    check('route /agents opens from the sidebar', page.url().endsWith('/agents'));
    check('supervisor + six specialists are listed', (await page.$$('[data-testid="agents-grid"] article')).length === 6 && Boolean(await page.$('[data-testid="agent-supervisor"]')));
    check('real backend texts are rendered (description + limitation of an agent)', (await text(page)).includes('Keyword Planner figures are Google') && (await text(page)).includes('Plans the work, delegates to specialists'));
    check('agents available with a model route; the Google-reading one is limited without an account', await page.waitForFunction(() => document.querySelector('[data-testid="availability-product_analyst"]')?.textContent === 'Available'
        && /Limited/.test(document.querySelector('[data-testid="availability-market_researcher"]')?.textContent || ''), { timeout: 20000 }).then(() => true).catch(() => false));
    check('worker offline is announced (no worker is running yet)', await waitForText(page, 'No agent worker is running', 20000));
    await page.screenshot({ path: path.join(OUT, '01-catalogue-offline-1440.png') });
    check('no horizontal overflow at 1440px', await noOverflow(page));

    // ---- details dialog
    await clickAria(page, 'Details of Market and keyword researcher');
    await page.waitForSelector('[role="dialog"]');
    check('details dialog lists tools with their outside system and the read-only marker', (await text(page)).includes('google_ads.keyword_ideas') && (await text(page)).includes('Read-only'));
    await page.keyboard.press('Escape');

    // ---- task A: queued with no worker, then cancelled from the UI
    await clickAria(page, 'Run a task with Product and sales-page analyst');
    await page.waitForSelector('#task-objective');
    await page.type('#task-objective', 'E2E task A: analyze the widget product');
    await clickAria(page, 'Start task');
    check('a started task is shown as Pending with the honest "no worker" notice', await waitForText(page, 'No worker is running, so this task cannot start'));
    await page.screenshot({ path: path.join(OUT, '02-task-pending-no-worker-1440.png') });
    await sleep(600);   // the page scrolls the opened task into view; let it settle before aiming the click
    await page.click('[data-testid="task-detail"] header button');
    check('cancel asks for confirmation first', await waitForText(page, 'Cancel this task?', 8000));
    await page.locator('div.fixed button.bg-red-600').setTimeout(5000).click();   // the confirmation modal's destructive button
    check('cancelling a queued task (confirmation, real API) ends it Cancelled', await waitForText(page, 'Task cancelled'));
    check('the cancelled task is listed under the Cancelled filter', await (async () => { await page.click('[data-testid="filter-cancelled"]'); await sleep(300); return (await text(page)).includes('E2E task A'); })());
    await page.click('[data-testid="filter-all"]');

    // ---- start the REAL worker with a scripted model: B completes, C is cancelled while running, D fails (non-retryable)
    startWorker([
        { json: PRODUCT_ANSWER },
        { sleep: 6, then: { tools: [['plan.note', { note: 'still working' }]] } },
        { error: 'authentication' },
    ]);
    // task B
    await page.type('#task-objective', 'E2E task B: analyze the widget product');
    await page.select('#task-agent', 'product_analyst');
    await page.type('#task-cost', '0.5');
    await clickAria(page, 'Start task');
    check('task B completes through the real worker and the UI updates by itself', await waitForText(page, 'Completed', 60000));
    check('structured result with provenance is shown', await waitForText(page, 'What the figures in this result are', 10000) && (await text(page)).includes('Hypothesis'));
    check('execution steps and token/cost usage are shown', /Execution steps? \(\d+\)/.test(await text(page)) && Boolean(await page.$('[data-testid="task-usage"]')));
    await page.screenshot({ path: path.join(OUT, '03-task-completed-1440.png'), fullPage: true });
    check('worker alert goes away once a worker reports in', await page.waitForFunction(() => !document.body.innerText.includes('No agent worker is running'), { timeout: 40000 }).then(() => true).catch(() => false));

    // task C: cancel while running
    await page.type('#task-objective', 'E2E task C: a slow one to cancel');
    await page.select('#task-agent', 'product_analyst');
    await clickAria(page, 'Start task');
    check('task C is picked up by the worker (Running)', await page.waitForFunction(() => /Running/.test(document.querySelector('[data-testid="task-detail"]')?.innerText || ''), { timeout: 30000 }).then(() => true).catch(() => false));
    await sleep(600);
    await page.click('[data-testid="task-detail"] header button');
    await page.locator('div.fixed button.bg-red-600').setTimeout(5000).click();
    check('cancelling a running task says it is requested', await waitForText(page, 'Cancellation requested', 15000));
    check('the worker stops it before its next model call and the UI shows Cancelled', await page.waitForFunction(() => /Cancelled/.test(document.querySelector('[data-testid="task-detail"]')?.innerText || ''), { timeout: 45000 }).then(() => true).catch(() => false));

    // task D: a non-retryable AI failure
    await page.type('#task-objective', 'E2E task D: the provider rejects the key');
    await page.select('#task-agent', 'ad_copywriter');
    await clickAria(page, 'Start task');
    check('a failed AI call shows a translated error and the next step (no raw provider text)', await page.waitForFunction(() => {
        const t = document.querySelector('[data-testid="task-error"]')?.innerText || '';
        return t.includes("The AI provider rejected the organization's key.") && t.includes('Check the provider key and the model route');
    }, { timeout: 45000 }).then(() => true).catch(() => false));
    await page.screenshot({ path: path.join(OUT, '04-task-failed-1440.png'), fullPage: true });
    check('history has every status', await (async () => {
        const t = await page.$eval('[data-testid="task-list"]', (e) => e.innerText);
        return /Completed/.test(t) && /Failed/.test(t) && /Cancelled/.test(t);
    })());

    // ---- settings round trip with the real API
    await page.click('[role="tab"]#agents-tab-agents');
    await page.waitForSelector('[data-testid="agents-grid"]');
    await clickAria(page, 'Settings of Policy and compliance reviewer');
    await page.waitForSelector('[role="dialog"]');
    await page.click('[role="dialog"] input[type=checkbox]');
    await clickAria(page, 'Save settings');
    check('disabling an agent is saved and reflected', await page.waitForFunction(() => /Disabled/.test(document.querySelector('[data-testid="availability-policy_reviewer"]')?.textContent || ''), { timeout: 15000 }).then(() => true).catch(() => false));
    await clickAria(page, 'Settings of Policy and compliance reviewer');
    await page.waitForSelector('[role="dialog"]');
    await page.click('[role="dialog"] input[type=checkbox]');
    await clickAria(page, 'Save settings');
    check('and re-enabled', await page.waitForFunction(() => /Available/.test(document.querySelector('[data-testid="availability-policy_reviewer"]')?.textContent || ''), { timeout: 15000 }).then(() => true).catch(() => false));

    // ---- responsiveness: no horizontal scroll at 390 / 820 / 1440 on both tabs
    for (const [w, h] of [[390, 844], [820, 1000], [1440, 900]]) {
        await page.setViewport({ width: w, height: h });
        await page.goto(`${APP}/agents`, { waitUntil: 'networkidle2' });
        await page.waitForSelector('[data-testid="agents-grid"]');
        check(`agents tab fits ${w}px without horizontal scroll`, await noOverflow(page));
        await page.screenshot({ path: path.join(OUT, `05-agents-${w}.png`), fullPage: true });
        await page.goto(`${APP}/agents?tab=tasks`, { waitUntil: 'networkidle2' });
        await page.waitForSelector('[data-testid="task-list"]');
        const first = await page.$('[data-testid="task-list"] ul button');
        if (first) await first.evaluate((el) => el.click());
        await sleep(600);
        check(`tasks tab with a task open fits ${w}px without horizontal scroll`, await noOverflow(page));
        await page.screenshot({ path: path.join(OUT, `06-tasks-${w}.png`), fullPage: true });
    }
    await page.setViewport({ width: 390, height: 844 });
    await page.goto(`${APP}/agents`, { waitUntil: 'networkidle2' });
    check('touch targets of the primary buttons are at least 44px tall at 390px', await page.$$eval('[data-testid="agents-grid"] article button', (buttons) => buttons.every((b) => b.getBoundingClientRect().height >= 43.5)));

    // ---- language
    await page.setViewport({ width: 1440, height: 900 });
    await page.goto(`${APP}/agents`, { waitUntil: 'networkidle2' });
    await page.waitForSelector('[data-testid="lang-pt-BR"]');
    await sleep(500);
    await page.$eval('[data-testid="lang-pt-BR"]', (el) => el.click());
    check('switching to pt-BR translates the module (title, catalogue text, actions)', await waitForText(page, 'Agentes de IA') && (await text(page)).includes('Pesquisador de mercado e palavras-chave') && (await text(page)).includes('Executar tarefa'));
    const body = await text(page);
    check('no raw translation key or placeholder is visible in pt-BR', !/\b(agents:[a-z_.]+|\{\{|undefined)\b/.test(body));
    await page.screenshot({ path: path.join(OUT, '07-agents-pt-BR-1440.png') });
} catch (error) {
    check('E2E run completed without an exception', false, String(error?.stack || error));
} finally {
    stopWorker();
    await browser.close();
}

const failed = results.filter((r) => !r.ok);
console.log(`\n${results.length - failed.length}/${results.length} checks passed`);
process.exit(failed.length ? 1 : 0);
