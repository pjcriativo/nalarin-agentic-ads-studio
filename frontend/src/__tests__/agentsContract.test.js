/**
 * The in-memory API the agents UI tests run on must return every field the screens read, exactly like the real backend. The same
 * agentsContract.json is checked against the real API in backend/tests/unit/test_agents_frontend_contract.py, and agentsCatalog.json
 * (the agent/tool texts the fake serves) is compared there with the real registry, so neither side can drift without a red test.
 */
import contract from './agentsContract.json';
import catalog from './agentsCatalog.json';
import { createFakeBackend, memoryStorage } from './fakeBackend';

const API = 'http://localhost:8000/api/v1';

describe('fake agents backend honours the shared contract', () => {
    it('returns every field the dashboard reads, and never a forbidden one', async () => {
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
        const answers = [];

        const agents = await call('/agents');
        answers.push(agents);
        expect(agents).toHaveLength(6);
        agents.forEach((agent) => { has(agent, 'agent'); has(agent.limits.effective, 'agentLimits'); has(agent.limits.defaults, 'agentLimits'); has(agent.routing, 'agentRouting'); });
        const supervisor = await call('/agents/supervisor');
        answers.push(supervisor);
        has(supervisor, 'agent');
        expect(supervisor.allowed_tools).toEqual([]);
        const tools = await call('/agents/tools');
        answers.push(tools);
        tools.available.forEach((tool) => has(tool, 'tool'));
        tools.planned.forEach((tool) => has(tool, 'plannedTool'));

        const created = await call('/agents/tasks', 'POST', { objective: 'Plan search ads for the widget', idempotency_key: 'ui-contract-1' });
        has(created, 'task');
        has(created.usage, 'taskUsage');
        const { agents: { seed, state } } = backend;
        seed.start(state, 'org-a1', created.id);
        seed.step(state, 'org-a1', created.id, {});
        seed.finish(state, 'org-a1', created.id, 'completed', { result: { kind: 'single', agent: 'product_analyst', output: { summary: 's' } } });
        const listed = await call('/agents/tasks');
        answers.push(listed);
        listed.forEach((task) => { has(task, 'task'); has(task.usage, 'taskUsage'); });
        const detail = await call(`/agents/tasks/${created.id}`);
        answers.push(detail);
        has(detail, 'task');
        has(detail, 'taskDetail');
        has(detail.progress, 'progress');
        const steps = await call(`/agents/tasks/${created.id}/steps`);
        answers.push(steps);
        expect(steps.length).toBeGreaterThan(0);
        steps.forEach((step) => has(step, 'step'));
        const result = await call(`/agents/tasks/${created.id}/result`);
        answers.push(result);
        has(result, 'result');
        const worker = await call('/agents/worker/status');
        answers.push(worker);
        has(worker, 'workerStatus');
        has(worker.queue, 'workerQueue');

        const keys = new Set();
        const walk = (value) => { if (value && typeof value === 'object') { Object.keys(value).forEach((k) => { keys.add(k); walk(value[k]); }); } };
        walk(answers);
        expect(contract.forbidden.filter((field) => keys.has(field))).toEqual([]);
    });

    it('serves the snapshot of the real registry (six specialists, a tool-less supervisor, read-only tools only)', () => {
        expect(catalog.agents.map((a) => a.key)).toEqual(['product_analyst', 'market_researcher', 'search_ads_strategist', 'ad_copywriter', 'policy_reviewer', 'performance_analyst']);
        expect(catalog.supervisor.allowed_tools).toEqual([]);
        expect(catalog.tools.every((tool) => tool.side_effect === 'none')).toBe(true);
        expect(catalog.planned.map((tool) => tool.name).sort()).toEqual(['google_ads.apply_change_proposal', 'google_ads.draft_campaign']);
    });
});
