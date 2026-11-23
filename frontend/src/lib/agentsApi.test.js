import { AgentsApiError, costShare, countDataKinds, createAgentsApi, dataKindOf, errorKind, isActive, isFinal, needsGoogleAds, newIdempotencyKey,
    normalizeTask, parseContext, progressPercent, toolOutcome } from './agentsApi';
import { agentAvailability, setupAlerts } from './agentAvailability';

const response = (status, body) => ({ ok: status < 300, status, json: async () => body });

describe('agents API client', () => {
    it('calls the real endpoints with the right verbs and bodies, and URL-encodes ids', async () => {
        const authFetch = vi.fn(async () => response(200, []));
        const api = createAgentsApi(authFetch);
        await api.agents();
        await api.tasks({ status: 'failed', limit: 50 });
        await api.tasks();
        await api.steps('a/b', 7);
        await api.start({ objective: 'x' });
        await api.cancel('t1');
        await api.saveSettings('product_analyst', { enabled: false });
        const urls = authFetch.mock.calls.map(([url, options]) => `${options?.method || 'GET'} ${url.replace('http://localhost:8000/api/v1', '')}`);
        expect(urls).toEqual([
            'GET /agents', 'GET /agents/tasks?limit=50&status=failed', 'GET /agents/tasks?limit=100', 'GET /agents/tasks/a%2Fb/steps?after=7&limit=500',
            'POST /agents/tasks', 'POST /agents/tasks/t1/cancel', 'PUT /agents/product_analyst/settings',
        ]);
        expect(JSON.parse(authFetch.mock.calls[4][1].body)).toEqual({ objective: 'x' });
    });

    it('turns every failure into an AgentsApiError and classifies it', async () => {
        const failing = (status, detail) => createAgentsApi(async () => response(status, { detail }));
        const kinds = {};
        const cases = [[401], [403], [404], [409, 'This agent is disabled for the organization'], [409, 'The task already finished'], [422], [429], [503], [418]];
        for (const [status, detail] of cases) {
            await failing(status, detail).task('t').catch((error) => {
                expect(error).toBeInstanceOf(AgentsApiError);
                kinds[`${status}${detail ? `:${detail.slice(0, 8)}` : ''}`] = errorKind(error);
            });
        }
        expect(kinds).toEqual({ 401: 'unauthorized', 403: 'forbidden', 404: 'notFound', '409:This age': 'agentDisabled', '409:The task': 'conflict', 422: 'invalid', 429: 'tooManyTasks', 503: 'unavailable', 418: 'unknown' });
        const networkDown = createAgentsApi(async () => { throw new Error('offline'); });
        await networkDown.agents().catch((error) => expect(errorKind(error)).toBe('network'));
        expect(errorKind(new Error('x'))).toBe('network');
    });

    it('a list endpoint that answers something else is an empty list, not a crash', async () => {
        expect(await createAgentsApi(async () => response(200, { not: 'a list' })).agents()).toEqual([]);
    });

    it('googleConnected is null (unknown) when it cannot be read, never a guessed false', async () => {
        expect(await createAgentsApi(async () => response(500, {})).googleConnected()).toBeNull();
        expect(await createAgentsApi(async () => { throw new Error('x'); }).googleConnected()).toBeNull();
        expect(await createAgentsApi(async () => response(200, { connected: false })).googleConnected()).toEqual({ connected: false });
        expect(await createAgentsApi(async () => response(200, { connected: true })).googleConnected()).toEqual({ connected: true });
    });
});

describe('helpers', () => {
    it('normalizeTask fills only what is structurally missing (numbers become 0, no invented text)', () => {
        const task = normalizeTask({ id: 't', status: 'running' });
        expect(task.usage).toEqual({ input_tokens: 0, output_tokens: 0, cost_usd: 0, max_cost_usd: null });
        expect(task.runs).toEqual([]);
        expect(task.progress).toEqual({ completed_steps: 0, total_steps: null, final: false });
        expect(normalizeTask(null).status).toBe('pending');
        expect(isFinal({ status: 'failed' })).toBe(true);
        expect(isActive({ status: 'running' })).toBe(true);
        expect(isActive({ status: 'completed' })).toBe(false);
    });

    it('progress and cost share', () => {
        expect(progressPercent({ completed_steps: 1, total_steps: 4 }, 'running')).toBe(25);
        expect(progressPercent({ completed_steps: 0, total_steps: null }, 'running')).toBeNull();
        expect(progressPercent({ completed_steps: 0, total_steps: null }, 'completed')).toBe(100);
        expect(progressPercent({ completed_steps: 9, total_steps: 4 }, 'running')).toBe(100);
        expect(costShare({ cost_usd: 0.5, max_cost_usd: 2 })).toBe(25);
        expect(costShare({ cost_usd: 5, max_cost_usd: 2 })).toBe(100);
        expect(costShare({ cost_usd: 1, max_cost_usd: null })).toBeNull();
    });

    it('parseContext accepts empty or one JSON object only', () => {
        expect(parseContext('')).toEqual({ value: null, error: null });
        expect(parseContext('  {"a": 1} ')).toEqual({ value: { a: 1 }, error: null });
        expect(parseContext('[1]').error).toBe('notObject');
        expect(parseContext('"text"').error).toBe('notObject');
        expect(parseContext('null').error).toBe('notObject');
        expect(parseContext('{oops').error).toBe('invalidJson');
        expect(parseContext(`{"a":"${'x'.repeat(13000)}"}`).error).toBe('tooLong');
    });

    it('idempotency keys are unique, match the server pattern and fit 64 characters', () => {
        const keys = new Set(Array.from({ length: 50 }, newIdempotencyKey));
        expect(keys.size).toBe(50);
        keys.forEach((key) => expect(key).toMatch(/^[A-Za-z0-9._:-]{1,64}$/));
    });

    it('an unknown data label is a hypothesis, never real', () => {
        expect(['real', 'estimate', 'hypothesis', 'verified', undefined, null].map(dataKindOf)).toEqual(['real', 'estimate', 'hypothesis', 'hypothesis', 'hypothesis', 'hypothesis']);
        expect(countDataKinds({ a: [{ data_kind: 'real' }, { data_kind: 'estimate' }, { data_kind: 'nonsense' }], b: { data_kind: 'hypothesis' } })).toEqual({ real: 1, estimate: 1, hypothesis: 2 });
        expect(countDataKinds(null)).toEqual({ real: 0, estimate: 0, hypothesis: 0 });
    });

    it('needsGoogleAds looks at the tool names', () => {
        expect(needsGoogleAds(['plan.note', 'google_ads.keyword_ideas'])).toBe(true);
        expect(needsGoogleAds(['plan.note', 'web.analyze_page'])).toBe(false);
        expect(needsGoogleAds(undefined)).toBe(false);
    });

    it('toolOutcome reports what a tool really returned', () => {
        const step = (preview, extra = {}) => ({ kind: 'tool', status: 'ok', output: { preview }, ...extra });
        expect(toolOutcome({ kind: 'llm' })).toBeNull();
        expect(toolOutcome(step('{"available": false, "reason": "rate_limited"}'))).toEqual({ state: 'unavailable', reason: 'rate_limited' });
        expect(toolOutcome(step('{"analyzed": false, "reason": "fetch_failed"}'))).toEqual({ state: 'unavailable', reason: 'fetch_failed' });
        expect(toolOutcome(step('{"available": true, "no_data": true}'))).toEqual({ state: 'noData', reason: null });
        expect(toolOutcome(step('{"available": true, "provenance": {"data_kind": "estimate"}, "ideas": [1]}')).kind).toBe('estimate');
        expect(toolOutcome(step('<untrusted> {"available": false, "reason": "x"} </untrusted>')).state).toBe('unavailable');
        expect(toolOutcome(step('truncated {"available": tr')).state).toBe('ok');
        expect(toolOutcome(step('{}', { status: 'error', error_kind: 'tool_failed' }))).toEqual({ state: 'error', reason: 'tool_failed' });
    });
});

describe('agent availability', () => {
    const agent = (patch = {}) => ({ key: 'a', enabled: true, allowed_tools: ['plan.note'], routing: { has_route: true }, ...patch });
    const setup = (patch = {}) => ({ ai: { hasCredential: true, hasModel: true }, google: { connected: true }, worker: { online: true, queue: { pending: 0 } }, ...patch });

    it('priority: disabled, no route, no key, no model, limited by Google, ready', () => {
        expect(agentAvailability(agent({ enabled: false }), setup()).state).toBe('disabled');
        expect(agentAvailability(agent({ routing: { has_route: false } }), setup()).state).toBe('noModel');
        expect(agentAvailability(agent(), setup({ ai: { hasCredential: false, hasModel: true } })).state).toBe('noCredential');
        expect(agentAvailability(agent(), setup({ ai: { hasCredential: true, hasModel: false } })).state).toBe('noModel');
        const google = agent({ allowed_tools: ['google_ads.keyword_ideas'] });
        expect(agentAvailability(google, setup({ google: { connected: false } }))).toMatchObject({ state: 'limited', blocking: false, missing: 'google' });
        expect(agentAvailability(agent(), setup({ google: { connected: false } })).state).toBe('ready'); // does not use Google
        expect(agentAvailability(google, setup()).state).toBe('ready');
    });

    it('an unknown setup is never turned into a problem or into "ok" for the checks it would need', () => {
        const unknown = { ai: null, google: null, worker: null };
        expect(agentAvailability(agent({ allowed_tools: ['google_ads.keyword_ideas'] }), unknown).state).toBe('ready');
        expect(setupAlerts({ setup: unknown, agents: [agent()], budgets: [] })).toEqual([]);
    });

    it('builds the alerts in order of importance', () => {
        const alerts = setupAlerts({
            setup: setup({ worker: { online: false, queue: { pending: 2 } }, ai: { hasCredential: false, hasModel: false }, google: { connected: false } }),
            agents: [agent({ allowed_tools: ['google_ads.get_account_info'] })],
            budgets: [{ period: 'daily', enabled: true, limit_usd: 5, spent_usd: 5, reserved_usd: 0 }],
        });
        expect(alerts.map((a) => a.id)).toEqual(['workerOffline', 'noCredential', 'budgetReached', 'googleMissing']);
        expect(alerts[0]).toMatchObject({ pending: 2 });
        const noRoute = setupAlerts({ setup: setup(), agents: [agent({ routing: { has_route: false } })], budgets: [{ period: 'daily', enabled: false, limit_usd: 5, spent_usd: 9, reserved_usd: 0 }] });
        expect(noRoute.map((a) => a.id)).toEqual(['noRoute']);
    });
});
