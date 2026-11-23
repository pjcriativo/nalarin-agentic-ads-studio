/**
 * Test double of `/api/v1/agents` for the in-memory backend (fakeBackend.js). It follows the REAL API's contract
 * (backend/app/api/v1/agents.py; the shape is pinned by agentsContract.json, which the real backend is also tested against):
 * per-organization state, `agents:read` / `agents:run` / `agents:manage` checks, 404 for another organization's task, 409/422/429 cases.
 * Agent texts come from agentsCatalog.json, a snapshot of the real registry. Used only by tests; the application never imports it.
 * Nothing here executes an agent: tests move tasks forward with `agentsSeed`.
 */
import catalog from './agentsCatalog.json';

const iso = () => new Date().toISOString();
const uid = (() => { let n = 0; return (prefix) => `${prefix}-${++n}`; })();
const FINAL = ['completed', 'failed', 'cancelled'];
const MAX_ACTIVE = 3;

export function createAgentsState() {
    return {
        orgs: {},                 // orgId -> { tasks: [], steps: { taskId: [] }, results: { taskId: {...} }, settings: { key: {...} } }
        worker: { online: true, workers: 1 },
        failNext: null,           // { status, detail, path? } for the next /agents call (optionally only for that exact path)
        holds: [],                // [{ test(path, method), promise }] requests held open until a test releases them
    };
}

const bucket = (state, org) => {
    state.orgs[org] = state.orgs[org] || { tasks: [], steps: {}, results: {}, settings: {} };
    return state.orgs[org];
};

const routeView = (aiState, org, operation) => {
    const data = aiState?.orgs?.[org];
    const routes = data?.routes || [];
    const route = routes.find((r) => r.operation === operation) || routes.find((r) => r.operation === 'default') || null;
    const model = route && data.models.find((m) => m.id === route.primary_model_id);
    return {
        operation, has_route: Boolean(route), inherited_from_default: Boolean(route) && route.operation !== operation,
        primary_model: model ? { provider: model.provider, model_id: model.model_id } : null, fallbacks: route ? (route.fallback_model_ids || []).length : 0,
    };
};

const cardOf = (state, aiState, org, base, isSupervisor) => {
    const setting = bucket(state, org).settings[base.key];
    const effective = { ...base.limits_defaults };
    if (setting?.max_steps) effective.max_steps = setting.max_steps;
    if (setting?.timeout_seconds) effective.timeout_seconds = setting.timeout_seconds;
    return {
        key: base.key, name: base.name, description: base.description, version: base.version, instructions_hash: base.instructions_hash,
        enabled: isSupervisor ? true : (setting ? setting.enabled : true), allowed_tools: [...base.allowed_tools], readiness: base.readiness,
        limitations: [...base.limitations], limits: { defaults: { ...base.limits_defaults }, effective },
        routing: routeView(aiState, org, `agent.${base.key}`), output_schema: isSupervisor ? null : { type: 'object' },
        ...(isSupervisor ? { role: 'supervisor' } : {}),
    };
};

const taskView = (task) => ({
    id: task.id, status: task.status, agent_key: task.agent_key, kind: task.agent_key ? 'single' : 'supervised', objective: task.objective,
    created_by_user_id: task.created_by_user_id, created_at: task.created_at, started_at: task.started_at || null, finished_at: task.finished_at || null,
    attempts: task.attempts, max_attempts: 3, cancel_requested: Boolean(task.cancel_requested), step_count: task.step_count || 0, next_attempt_at: task.next_attempt_at || null,
    usage: { input_tokens: task.input_tokens || 0, output_tokens: task.output_tokens || 0, cost_usd: task.cost_usd || 0, max_cost_usd: task.max_cost_usd ?? 2 },
    error: task.error || null, plan: task.plan || null,
});

export function handleAgents({ state, aiState, path, method, body, headers, query, userId, tenant, json }) {
    const err = (status, detail) => json(status, { detail });
    if (state.failNext && (!state.failNext.path || state.failNext.path === path)) {
        const failure = state.failNext;
        state.failNext = null;
        return err(failure.status, failure.detail || 'The service failed.');
    }
    const hold = state.holds.find((h) => h.test(path, method));
    const respond = async (response) => {
        if (hold) await hold.promise;
        return response;
    };
    const needs = (() => {
        if (method === 'POST' && (path === '/agents/tasks' || /\/cancel$/.test(path))) return 'agents:run';
        if (method === 'PUT' || path === '/agents/recover') return 'agents:manage';
        return 'agents:read';
    })();
    const t = tenant(userId, headers, needs);
    if (t.error) return t.error;
    const data = bucket(state, t.org);
    const bases = catalog.agents;
    let m;

    if (path === '/agents' && method === 'GET') return respond(json(200, bases.map((b) => cardOf(state, aiState, t.org, b, false))));
    if (path === '/agents/supervisor') return respond(json(200, cardOf(state, aiState, t.org, catalog.supervisor, true)));
    if (path === '/agents/tools') return respond(json(200, { available: catalog.tools, planned: catalog.planned }));
    if (path === '/agents/worker/status') {
        const mine = data.tasks;
        const pending = mine.filter((x) => x.status === 'pending');
        const oldest = pending.length ? Math.max(0, Math.round((Date.now() - new Date(pending[pending.length - 1].created_at).getTime()) / 1000)) : null;
        return respond(json(200, {
            online: state.worker.online, workers: state.worker.online ? state.worker.workers : 0, online_window_seconds: 45,
            queue: { pending: pending.length, running: mine.filter((x) => x.status === 'running').length, oldest_pending_seconds: oldest },
        }));
    }

    if ((m = path.match(/^\/agents\/([^/]+)\/settings$/)) && method === 'PUT') {
        const base = bases.find((b) => b.key === m[1]);
        if (!base) return err(404, 'Agent not found');
        const ok = (v, lo, hi) => v === null || v === undefined || (Number.isInteger(v) && v >= lo && v <= hi);
        if (typeof body.enabled !== 'boolean' || !ok(body.max_steps, 1, 20) || !ok(body.timeout_seconds, 10, 600)) return err(422, 'Invalid request');
        data.settings[base.key] = { enabled: body.enabled, max_steps: body.max_steps ?? null, timeout_seconds: body.timeout_seconds ?? null };
        return respond(json(200, { agent_key: base.key, ...data.settings[base.key] }));
    }

    if (path === '/agents/tasks' && method === 'POST') {
        const objective = String(body.objective || '').trim();
        if (objective.length < 5 || objective.length > 4000) return err(422, 'The objective must have 5 to 4000 characters');
        if (body.agent_key) {
            const base = bases.find((b) => b.key === body.agent_key);
            if (!base) return err(404, 'Agent not found');
            if (data.settings[base.key] && data.settings[base.key].enabled === false) return err(409, 'This agent is disabled for the organization');
        }
        if (body.idempotency_key) {
            const existing = data.tasks.find((x) => x.idempotency_key === body.idempotency_key);
            if (existing) return json(201, taskView(existing));
        }
        if (data.tasks.filter((x) => x.status === 'pending' || x.status === 'running').length >= MAX_ACTIVE) return err(429, 'Too many tasks are running for this organization; wait for one to finish');
        const task = {
            id: uid('task'), status: 'pending', agent_key: body.agent_key || null, objective, created_by_user_id: userId, created_at: iso(), attempts: 0, step_count: 0,
            idempotency_key: body.idempotency_key || null, max_cost_usd: body.max_cost_usd ?? 2, context: body.context || null,
        };
        data.tasks.unshift(task);
        data.steps[task.id] = [];
        return respond(json(201, taskView(task)));
    }
    if (path === '/agents/tasks' && method === 'GET') {
        let rows = data.tasks;
        if (query.get('status')) rows = rows.filter((x) => x.status === query.get('status'));
        return respond(json(200, rows.slice(0, Number(query.get('limit') || 25)).map(taskView)));
    }
    if ((m = path.match(/^\/agents\/tasks\/([^/]+)(?:\/(steps|result|cancel))?$/))) {
        const task = data.tasks.find((x) => x.id === m[1]);
        if (!task) return err(404, 'Task not found');
        if (!m[2] && method === 'GET') {
            const runs = task.runs || [];
            const total = task.plan ? task.plan.steps.length : (task.agent_key ? 1 : null);
            const done = new Set(runs.filter((r) => r.status === 'completed').map((r) => r.plan_step_id || r.id)).size;
            return respond(json(200, { ...taskView(task), runs, progress: { completed_steps: done, total_steps: total, final: FINAL.includes(task.status) } }));
        }
        if (m[2] === 'steps') {
            const after = Number(query.get('after') || 0);
            return respond(json(200, (data.steps[task.id] || []).filter((s) => s.sequence > after)));
        }
        if (m[2] === 'result') {
            if (!FINAL.includes(task.status)) return err(409, 'The task has not finished yet');
            return respond(json(200, {
                id: task.id, status: task.status, result: data.results[task.id] ?? null, error: task.error || null,
                disclaimer: 'Results are model-generated proposals for human review. Nothing was published or spent.',
            }));
        }
        if (m[2] === 'cancel' && method === 'POST') {
            const role = t.role;
            if (task.created_by_user_id !== userId && !['owner', 'admin'].includes(role)) return err(403, 'Only the person who started the task or an administrator can cancel it');
            if (FINAL.includes(task.status)) return err(409, 'The task already finished');
            task.cancel_requested = true;
            if (task.status === 'pending') { task.status = 'cancelled'; task.finished_at = iso(); task.error = { kind: 'cancelled', message: 'cancelled' }; }
            return respond(json(200, { id: task.id, status: task.status, cancel_requested: true }));
        }
    }
    if (path === '/agents/recover' && method === 'POST') return json(200, { requeued_or_failed: [], queued: data.tasks.filter((x) => x.status === 'pending').length });
    return err(404, 'Not found');
}

/** Moves tasks forward like a worker would, for tests. */
export const agentsSeed = {
    task: (state, org, id) => bucket(state, org).tasks.find((x) => x.id === id),
    start(state, org, id, patch = {}) {
        Object.assign(this.task(state, org, id), { status: 'running', started_at: iso(), attempts: 1, ...patch });
    },
    step(state, org, id, step) {
        const steps = bucket(state, org).steps[id];
        const task = this.task(state, org, id);
        const sequence = steps.length + 1;
        steps.push({
            sequence, run_id: step.run_id || null, kind: 'llm', name: 'agent.product_analyst:run', status: 'ok', error_kind: null, input: null, output: null, injection_suspected: false,
            provider: 'openai', model_id: 'm-test', input_tokens: 100, output_tokens: 50, cost_usd: 0.001, latency_ms: 800, created_at: iso(), ...step,
        });
        task.step_count = sequence;
        task.input_tokens = (task.input_tokens || 0) + (steps[sequence - 1].input_tokens || 0);
        task.output_tokens = (task.output_tokens || 0) + (steps[sequence - 1].output_tokens || 0);
        task.cost_usd = Number(((task.cost_usd || 0) + (steps[sequence - 1].cost_usd || 0)).toFixed(6));
    },
    runs(state, org, id, runs) { this.task(state, org, id).runs = runs; },
    plan(state, org, id, plan) { this.task(state, org, id).plan = plan; },
    finish(state, org, id, status, { result, error } = {}) {
        const task = this.task(state, org, id);
        Object.assign(task, { status, finished_at: iso(), error: error || null });
        if (result !== undefined) bucket(state, org).results[id] = result;
    },
    retry(state, org, id, error) { Object.assign(this.task(state, org, id), { status: 'pending', error, next_attempt_at: new Date(Date.now() + 60000).toISOString(), attempts: 1 }); },
};
