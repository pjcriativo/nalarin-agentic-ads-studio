/**
 * Test double of `/api/v1/ai` for the in-memory backend (fakeBackend.js). It follows the real
 * API's contract (backend/app/api/v1/ai.py): per-organization state, `ai:read` / `ai:manage`
 * checks, keys NEVER returned (only the last four characters), generic errors with `X-AI-Error`.
 * Used only by tests; the application never imports it.
 */
const PROVIDERS = ['anthropic', 'gemini', 'openai'];
const uid = (() => { let n = 0; return (prefix) => `${prefix}-${++n}`; })();
const iso = () => new Date().toISOString();

export function createAiState() {
    return {
        orgs: {},            // orgId -> { credentials, models, routes, budgets, logs }
        secrets: {},         // credential id -> the key as typed (so tests can prove it never comes back)
        discoverable: {},    // provider -> [{ model_id, display_name }]
        rejectKeys: new Set(), // keys the "provider" refuses (authentication)
        failTest: false,
        failNext: null,      // { status, kind } for the next /ai call (service unavailable, etc.)
    };
}

const bucket = (state, org) => {
    state.orgs[org] = state.orgs[org] || { credentials: [], models: [], routes: [], budgets: [], logs: [] };
    return state.orgs[org];
};
const credentialView = (c) => ({
    id: c.id, provider: c.provider, label: c.label, status: c.status, last4: c.last4, masked: c.last4 ? `••••${c.last4}` : null,
    created_at: c.created_at, rotated_at: c.rotated_at || null, revoked_at: c.revoked_at || null,
    last_verified_at: c.last_verified_at || null, last_verification_ok: c.last_verification_ok ?? null,
});
const budgetView = (b) => {
    const used = b.spent_usd + b.reserved_usd;
    return { period: b.period, enabled: b.enabled, limit_usd: b.limit_usd, spent_usd: b.spent_usd, reserved_usd: b.reserved_usd, remaining_usd: Math.max(b.limit_usd - used, 0), window_start: iso() };
};
const num = (value) => (value === null || value === undefined || value === '' ? null : Number(value));

export function handleAi({ state, path, method, body, headers, query, userId, tenant, json }) {
    const err = (status, detail, kind) => json(status, { detail }, kind ? { 'X-AI-Error': kind } : {});
    if (state.failNext) {
        const failure = state.failNext;
        state.failNext = null;
        return err(failure.status, 'The AI service failed.', failure.kind);
    }
    const needs = path === '/ai/providers' || (method === 'GET' && /^\/ai\/(models|routing|budgets|usage)/.test(path) && !path.startsWith('/ai/models/discover'))
        ? 'ai:read' : 'ai:manage';
    const t = tenant(userId, headers, needs);
    if (t.error) return t.error;
    const data = bucket(state, t.org);
    let m;

    if (path === '/ai/providers') {
        return json(200, PROVIDERS.map((provider) => ({ provider, has_active_credential: data.credentials.some((c) => c.provider === provider && c.status === 'active') })));
    }

    // ---- credentials
    if (path === '/ai/credentials' && method === 'GET') return json(200, data.credentials.map(credentialView));
    if (path === '/ai/credentials' && method === 'POST') {
        if (!PROVIDERS.includes(body.provider) || typeof body.api_key !== 'string' || body.api_key.length < 8) return err(422, 'Invalid request');
        if (data.credentials.some((c) => c.provider === body.provider && c.status === 'active')) return err(422, 'an active credential already exists');
        if (body.verify && state.rejectKeys.has(body.api_key)) return err(422, 'The provider rejected this API key.', 'authentication');
        const credential = {
            id: uid('cred'), provider: body.provider, label: body.label || null, status: 'active', last4: body.api_key.slice(-4), created_at: iso(),
            last_verified_at: body.verify ? iso() : null, last_verification_ok: body.verify ? true : null,
        };
        state.secrets[credential.id] = body.api_key;
        data.credentials.push(credential);
        return json(201, credentialView(credential));
    }
    if ((m = path.match(/^\/ai\/credentials\/([^/]+)(?:\/(test|rotate))?$/))) {
        const credential = data.credentials.find((c) => c.id === m[1]);
        if (!credential) return err(404, 'Credential not found');
        if (m[2] === 'test' && method === 'POST') {
            if (credential.status !== 'active') return err(409, 'Credential is revoked');
            credential.last_verified_at = iso();
            credential.last_verification_ok = !state.failTest;
            return json(200, { ok: !state.failTest, error: state.failTest ? 'authentication' : null, credential: credentialView(credential) });
        }
        if (m[2] === 'rotate' && method === 'POST') {
            if (typeof body.api_key !== 'string' || body.api_key.length < 8) return err(422, 'Invalid request');
            if (body.verify && state.rejectKeys.has(body.api_key)) return err(422, 'The provider rejected this API key.', 'authentication');
            state.secrets[credential.id] = body.api_key;
            credential.last4 = body.api_key.slice(-4);
            credential.rotated_at = iso();
            credential.last_verification_ok = body.verify ? true : null;
            return json(200, credentialView(credential));
        }
        if (!m[2] && method === 'DELETE') {
            credential.status = 'revoked';
            credential.revoked_at = iso();
            delete state.secrets[credential.id];
            return json(200, credentialView(credential));
        }
    }

    // ---- models
    if (path === '/ai/models/discover') {
        const wanted = query.get('provider');
        if (!data.credentials.some((c) => c.provider === wanted && c.status === 'active')) return err(409, 'AI is not configured', 'not_configured');
        const have = new Set(data.models.filter((x) => x.provider === wanted).map((x) => x.model_id));
        return json(200, (state.discoverable[wanted] || []).map((item) => ({ ...item, already_added: have.has(item.model_id) })));
    }
    if (path === '/ai/models' && method === 'GET') return json(200, data.models);
    if (path === '/ai/models' && method === 'POST') {
        if (!PROVIDERS.includes(body.provider) || !body.model_id) return err(422, 'Invalid request');
        for (const key of ['input_price_usd_per_mtok', 'output_price_usd_per_mtok', 'cached_input_price_usd_per_mtok']) {
            if (num(body[key]) !== null && num(body[key]) < 0) return err(422, 'Invalid price');
        }
        if (data.models.some((x) => x.provider === body.provider && x.model_id === body.model_id)) return err(409, 'Model already registered for this provider');
        const model = {
            id: uid('model'), provider: body.provider, model_id: body.model_id, display_name: body.display_name || null, enabled: body.enabled !== false,
            supports_tools: !!body.supports_tools, supports_vision: !!body.supports_vision, supports_json: !!body.supports_json,
            context_window: body.context_window ?? null, max_output_tokens: body.max_output_tokens ?? null,
            input_price_usd_per_mtok: num(body.input_price_usd_per_mtok), output_price_usd_per_mtok: num(body.output_price_usd_per_mtok),
            cached_input_price_usd_per_mtok: num(body.cached_input_price_usd_per_mtok),
        };
        data.models.push(model);
        return json(201, model);
    }
    if ((m = path.match(/^\/ai\/models\/([^/]+)$/))) {
        const model = data.models.find((x) => x.id === m[1]);
        if (!model) return err(404, 'Model not found');
        if (method === 'PATCH') {
            for (const key of Object.keys(body)) model[key] = typeof body[key] === 'string' && key.includes('price') ? num(body[key]) : body[key];
            return json(200, model);
        }
        if (method === 'DELETE') {
            data.models = data.models.filter((x) => x.id !== model.id);
            data.routes = data.routes.map((r) => ({ ...r, primary_model_id: r.primary_model_id === model.id ? null : r.primary_model_id, fallback_model_ids: r.fallback_model_ids.filter((i) => i !== model.id) }));
            return json(200, { success: true });
        }
    }

    // ---- routing
    if (path === '/ai/routing' && method === 'GET') return json(200, data.routes);
    if ((m = path.match(/^\/ai\/routing\/([^/]+)$/))) {
        if (method === 'PUT') {
            const ids = [body.primary_model_id, ...(body.fallback_model_ids || [])];
            if (new Set(ids).size !== ids.length) return err(422, 'A model can appear only once in a route');
            if (!ids.every((id) => data.models.some((x) => x.id === id))) return err(404, 'Model not found');
            data.routes = data.routes.filter((r) => r.operation !== m[1]);
            const route = { operation: m[1], primary_model_id: body.primary_model_id, fallback_model_ids: body.fallback_model_ids || [] };
            data.routes.push(route);
            return json(200, route);
        }
        if (method === 'DELETE') {
            if (!data.routes.some((r) => r.operation === m[1])) return err(404, 'Route not found');
            data.routes = data.routes.filter((r) => r.operation !== m[1]);
            return json(200, { success: true });
        }
    }

    // ---- budgets
    if (path === '/ai/budgets' && method === 'GET') return json(200, data.budgets.map(budgetView));
    if ((m = path.match(/^\/ai\/budgets\/([^/]+)$/))) {
        if (!['daily', 'monthly'].includes(m[1])) return err(422, 'Period must be daily or monthly');
        if (method === 'PUT') {
            if (num(body.limit_usd) === null || num(body.limit_usd) < 0) return err(422, 'Invalid limit');
            let budget = data.budgets.find((b) => b.period === m[1]);
            if (!budget) { budget = { period: m[1], spent_usd: 0, reserved_usd: 0 }; data.budgets.push(budget); }
            budget.limit_usd = num(body.limit_usd);
            budget.enabled = body.enabled !== false;
            return json(200, budgetView(budget));
        }
        if (method === 'DELETE') {
            if (!data.budgets.some((b) => b.period === m[1])) return err(404, 'Budget not found');
            data.budgets = data.budgets.filter((b) => b.period !== m[1]);
            return json(200, { success: true });
        }
    }

    // ---- usage
    if (path === '/ai/usage/logs') return json(200, [...data.logs].reverse());
    if (path === '/ai/usage/summary') {
        const sum = (key) => data.logs.reduce((n, l) => n + (l[key] || 0), 0);
        const group = (keys) => {
            const rows = new Map();
            for (const l of data.logs) {
                const id = keys.map((k) => l[k]).join('|');
                const row = rows.get(id) || { ...Object.fromEntries(keys.map((k) => [k, l[k]])), requests: 0, input_tokens: 0, output_tokens: 0, cost_usd: 0 };
                row.requests += 1; row.input_tokens += l.input_tokens; row.output_tokens += l.output_tokens; row.cost_usd += l.cost_usd || 0;
                rows.set(id, row);
            }
            return [...rows.values()];
        };
        const days = new Map();
        for (const l of data.logs) {
            const day = l.created_at.slice(0, 10);
            const row = days.get(day) || { date: day, requests: 0, input_tokens: 0, output_tokens: 0, cost_usd: 0 };
            row.requests += 1; row.cost_usd += l.cost_usd || 0;
            days.set(day, row);
        }
        return json(200, {
            days: 30,
            totals: { requests: data.logs.length, input_tokens: sum('input_tokens'), output_tokens: sum('output_tokens'), cached_input_tokens: sum('cached_input_tokens'), cost_usd: sum('cost_usd'), estimated_cost_usd: sum('cost_usd'), errors: data.logs.filter((l) => l.status === 'error').length },
            by_day: [...days.values()], by_model: group(['provider', 'model_id']), by_operation: group(['operation']), by_user: group(['user_id']),
        });
    }
    return err(404, 'Not found');
}

/** Test helpers over the state (what a server-side process would have written). */
export const aiSeed = {
    log(state, org, partial = {}) {
        bucket(state, org).logs.push({
            id: uid('log'), created_at: iso(), request_id: uid('req'), attempt: 1, operation: 'copy.generate', provider: 'openai', model_id: 'test-model',
            user_id: null, status: 'success', error_kind: null, input_tokens: 100, output_tokens: 50, cached_input_tokens: 0, reasoning_tokens: 0,
            estimated_cost_usd: 0.001, cost_usd: 0.001, latency_ms: 420, ...partial,
        });
    },
    budget(state, org, partial) {
        bucket(state, org).budgets.push({ period: 'daily', enabled: true, limit_usd: 10, spent_usd: 0, reserved_usd: 0, ...partial });
    },
    bucket,
};
