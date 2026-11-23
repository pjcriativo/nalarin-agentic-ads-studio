/**
 * Client of the REAL agents API (`/api/v1/agents`). No mocks, no local fallback, no invented data: when a call fails the
 * screen shows that it failed. The fake backend used by the tests lives in src/__tests__ and is never imported by the app.
 *
 * Privacy: nothing here logs or stores a task objective/context/result in the browser; they live in component state only.
 */
const API_URL = import.meta.env.VITE_API_URL || 'http://localhost:8000/api/v1';
const BASE = `${API_URL}/agents`;
const GOOGLE_CONNECTION = `${API_URL}/google-ads/connection`;

export const FINAL_STATUSES = ['completed', 'failed', 'cancelled'];
export const ACTIVE_STATUSES = ['pending', 'running'];
export const STATUS_FILTERS = ['all', 'pending', 'running', 'completed', 'failed', 'cancelled'];
export const MAX_OBJECTIVE_CHARS = 4000;
export const MIN_OBJECTIVE_CHARS = 5;
export const MAX_CONTEXT_CHARS = 12000;
export const HISTORY_LIMIT = 100;

/** Failure of an API call, reduced to what the UI may act on (never a provider body). */
export class AgentsApiError extends Error {
    constructor(status, detail) {
        super(`Agents API ${status}`);
        this.name = 'AgentsApiError';
        this.status = status;
        this.detail = typeof detail === 'string' ? detail : null; // the server's own sentence, used only to tell known cases apart
    }
}

/** i18n key suffix (agents:errors.api.<kind>) for a failed call. */
export function errorKind(error) {
    if (!(error instanceof AgentsApiError) || error.status === 0) return 'network';
    switch (error.status) {
        case 401: return 'unauthorized';
        case 403: return 'forbidden';
        case 404: return 'notFound';
        case 409: return error.detail && /disabled/i.test(error.detail) ? 'agentDisabled' : 'conflict';
        case 422: return 'invalid';
        case 429: return 'tooManyTasks';
        case 502: case 503: case 504: return 'unavailable';
        default: return 'unknown';
    }
}

const json = (method, value) => ({ method, headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(value) });
const enc = encodeURIComponent;

export function createAgentsApi(authFetch) {
    const call = async (path, options) => {
        let response;
        try {
            response = await authFetch(`${BASE}${path}`, options);
        } catch {
            throw new AgentsApiError(0, null);
        }
        if (!response.ok) {
            let detail = null;
            try { detail = (await response.json())?.detail; } catch { /* no body */ }
            throw new AgentsApiError(response.status, detail);
        }
        return response.json();
    };
    const list = async (path) => {
        const data = await call(path);
        return Array.isArray(data) ? data : [];
    };
    return {
        agents: () => list(''),
        supervisor: () => call('/supervisor'),
        tools: () => call('/tools'),
        workerStatus: () => call('/worker/status'),
        tasks: ({ status, limit = HISTORY_LIMIT } = {}) => list(`/tasks?limit=${limit}${status && status !== 'all' ? `&status=${enc(status)}` : ''}`),
        task: (id) => call(`/tasks/${enc(id)}`),
        steps: (id, after = 0) => list(`/tasks/${enc(id)}/steps?after=${after}&limit=500`),
        result: (id) => call(`/tasks/${enc(id)}/result`),
        start: (body) => call('/tasks', json('POST', body)),
        cancel: (id) => call(`/tasks/${enc(id)}/cancel`, { method: 'POST' }),
        saveSettings: (key, body) => call(`/${enc(key)}/settings`, json('PUT', body)),
        recover: () => call('/recover', { method: 'POST' }),
        /** Is a Google Ads account connected for this organization? null when it cannot be read (an unknown, not a "no"). */
        googleConnected: async () => {
            try {
                const response = await authFetch(GOOGLE_CONNECTION);
                if (!response.ok) return null;
                const data = await response.json();
                return { connected: data?.connected === true };
            } catch {
                return null;
            }
        },
    };
}

// ---------------------------------------------------------------------------------------------- helpers
/** The detail a screen needs from a task whatever the server sent (missing numbers become 0, never invented text). */
export function normalizeTask(raw) {
    const task = raw && typeof raw === 'object' ? raw : {};
    const usage = task.usage && typeof task.usage === 'object' ? task.usage : {};
    return {
        ...task,
        status: task.status || 'pending',
        usage: {
            input_tokens: usage.input_tokens ?? 0, output_tokens: usage.output_tokens ?? 0,
            cost_usd: usage.cost_usd ?? 0, max_cost_usd: usage.max_cost_usd ?? null,
        },
        runs: Array.isArray(task.runs) ? task.runs : [],
        progress: task.progress && typeof task.progress === 'object' ? task.progress : { completed_steps: 0, total_steps: null, final: FINAL_STATUSES.includes(task.status) },
    };
}

export const isFinal = (task) => FINAL_STATUSES.includes(task?.status);
export const isActive = (task) => ACTIVE_STATUSES.includes(task?.status);

/** 0..100 or null when the total is not known yet (the supervisor has not produced its plan). */
export function progressPercent(progress, status) {
    if (status === 'completed') return 100;
    const total = progress?.total_steps;
    if (!total) return null;
    return Math.min(100, Math.round(((progress.completed_steps || 0) / total) * 100));
}

/** Share of the task's cost cap already used, or null when no cap is known. */
export function costShare(usage) {
    if (!usage || !usage.max_cost_usd) return null;
    return Math.min(100, Math.round((usage.cost_usd / usage.max_cost_usd) * 100));
}

/** Validates the "context" text box: empty is fine, otherwise it must be one JSON object. Returns { value, error }. */
export function parseContext(text) {
    const trimmed = (text || '').trim();
    if (!trimmed) return { value: null, error: null };
    if (trimmed.length > MAX_CONTEXT_CHARS) return { value: null, error: 'tooLong' };
    try {
        const value = JSON.parse(trimmed);
        if (value === null || typeof value !== 'object' || Array.isArray(value)) return { value: null, error: 'notObject' };
        return { value, error: null };
    } catch {
        return { value: null, error: 'invalidJson' };
    }
}

/** The error kinds the backend sends in `error.kind` (docs/etapa-02-03): used to pick the explanation and the next step. */
export const ERROR_NEXT_STEP = {
    ai_not_configured: 'aiSettings', ai_authentication: 'aiSettings', ai_permission: 'aiSettings', ai_not_found: 'aiSettings',
    ai_budget_exceeded: 'aiLimits', ai_quota: 'aiLimits',
    cost_limit: 'raiseCostCap', agent_disabled: 'enableAgent', authorization: 'askAdmin',
};

let counter = 0;
/** A fresh idempotency key: resubmitting the SAME form after a network error can never create two tasks. */
export function newIdempotencyKey() {
    counter += 1;
    const random = globalThis.crypto?.randomUUID ? globalThis.crypto.randomUUID() : `${Date.now().toString(36)}${counter}${Math.random().toString(36).slice(2, 10)}`;
    return `ui-${random}`.slice(0, 64);
}

// Provenance of a figure in a result (backend: app/agents/evidence.py).
export const DATA_KINDS = ['real', 'estimate', 'hypothesis'];
export const DATA_SOURCES = ['google_ads_api', 'google_keyword_planner', 'product_page', 'supplied_context', 'model_knowledge'];
export const dataKindOf = (value) => (DATA_KINDS.includes(value) ? value : 'hypothesis'); // unknown label is never shown as "real"

/** Tools that need a connected Google Ads account of the organization (backend: Tool.requires / name prefix). */
export const needsGoogleAds = (tools) => (tools || []).some((name) => String(name).startsWith('google_ads.'));

/**
 * What a tool step actually returned, from the (redacted, truncated) preview the server stored. `unavailable` = the tool answered
 * that it had NO data (no account, rate limit, error...): the agent was told so and must not have invented any.
 */
export function toolOutcome(step) {
    if (!step || step.kind !== 'tool') return null;
    if (step.status === 'error') return { state: 'error', reason: step.error_kind || null };
    const preview = step.output && typeof step.output === 'object' ? step.output.preview : null;
    let parsed = null;
    if (typeof preview === 'string') {
        const start = preview.indexOf('{');
        const end = preview.lastIndexOf('}');
        if (start >= 0 && end > start) {
            try { parsed = JSON.parse(preview.slice(start, end + 1)); } catch { parsed = null; }
        }
    }
    if (parsed && (parsed.available === false || parsed.analyzed === false || parsed.fetched === false)) {
        return { state: 'unavailable', reason: parsed.reason || null };
    }
    if (parsed && parsed.no_data === true) return { state: 'noData', reason: null };
    return { state: 'ok', reason: null, kind: parsed?.provenance?.data_kind || null };
}

/** Counts of labelled items (data_kind) anywhere in an output, to summarize how much of a result is real / estimate / hypothesis. */
export function countDataKinds(value, counts = { real: 0, estimate: 0, hypothesis: 0 }, depth = 0) {
    if (depth > 8 || value === null || typeof value !== 'object') return counts;
    if (Array.isArray(value)) { value.forEach((item) => countDataKinds(item, counts, depth + 1)); return counts; }
    if (typeof value.data_kind === 'string') counts[dataKindOf(value.data_kind)] += 1;
    Object.values(value).forEach((item) => countDataKinds(item, counts, depth + 1));
    return counts;
}
