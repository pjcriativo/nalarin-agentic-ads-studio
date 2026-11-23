/**
 * The in-memory API the UI tests (and the browser E2E) run on must return every field the screen reads, exactly
 * like the real backend. The same aiContract.json is checked against the real API in
 * backend/tests/unit/test_ai_frontend_contract.py, so neither side can drift without a red test.
 */
import contract from './aiContract.json';
import { createFakeBackend } from './fakeBackend';
import { memoryStorage } from './fakeBackend';

const API = 'http://localhost:8000/api/v1';
const KEY = 'sk-contract-KEYKEYKEY-0001';

describe('fake AI backend honours the shared contract', () => {
    it('returns every field the screen reads and never a secret', async () => {
        global.localStorage = memoryStorage();
        const backend = createFakeBackend();
        const tokens = backend.loginTokens('alice');
        const headers = { Authorization: `Bearer ${tokens.access_token}`, 'X-Organization-Id': 'org-a1', 'Content-Type': 'application/json' };
        const call = async (path, method = 'GET', body) => {
            const response = await backend.fetch(`${API}${path}`, { method, headers, body: body ? JSON.stringify(body) : undefined });
            expect(response.status, `${method} ${path}`).toBeLessThan(300);
            return response.json();
        };
        const has = (item, name) => expect(contract[name].filter((field) => !(field in item)), `${name} is missing fields`).toEqual([]);

        backend.ai.state.discoverable.openai = [{ model_id: 'found-model', display_name: null }];
        const created = await call('/ai/credentials', 'POST', { provider: 'openai', api_key: KEY, verify: true });
        has(created, 'credential');
        (await call('/ai/providers')).forEach((p) => has(p, 'providers'));
        const listed = await call('/ai/credentials');
        listed.forEach((c) => has(c, 'credential'));
        const tested = await call(`/ai/credentials/${created.id}/test`, 'POST');
        has(tested, 'credentialTest');
        has(tested.credential, 'credential');
        (await call('/ai/models/discover?provider=openai')).forEach((d) => has(d, 'discovered'));
        const model = await call('/ai/models', 'POST', { provider: 'openai', model_id: 'found-model', input_price_usd_per_mtok: '3', output_price_usd_per_mtok: '15', cached_input_price_usd_per_mtok: '0.3' });
        has(model, 'model');
        (await call('/ai/models')).forEach((m) => has(m, 'model'));
        has(await call('/ai/routing/default', 'PUT', { primary_model_id: model.id, fallback_model_ids: [] }), 'route');
        (await call('/ai/routing')).forEach((r) => has(r, 'route'));
        has(await call('/ai/budgets/daily', 'PUT', { limit_usd: '10' }), 'budget');
        (await call('/ai/budgets')).forEach((b) => has(b, 'budget'));
        backend.ai.seed.log(backend.ai.state, 'org-a1', { user_id: 'u-alice' });
        const summary = await call('/ai/usage/summary');
        has(summary.totals, 'usageTotals');
        [['by_day', 'usageByDay'], ['by_model', 'usageByModel'], ['by_operation', 'usageByOperation'], ['by_user', 'usageByUser']].forEach(([section, name]) => {
            expect(summary[section].length, section).toBeGreaterThan(0);
            summary[section].forEach((row) => has(row, name));
        });
        const logs = await call('/ai/usage/logs');
        logs.forEach((row) => has(row, 'usageLog'));

        const everything = JSON.stringify([created, listed, tested, summary, logs]);
        expect(everything).not.toContain(KEY);
        const keys = new Set();
        const walk = (value) => { if (value && typeof value === 'object') { Object.keys(value).forEach((k) => { keys.add(k); walk(value[k]); }); } };
        walk([created, listed, tested, summary, logs]);
        expect(contract.forbidden.filter((field) => keys.has(field))).toEqual([]);
    });
});
