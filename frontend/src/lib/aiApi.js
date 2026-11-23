/**
 * Client of the REAL AI Gateway administration API (`/api/v1/ai`). No mocks, no local fallback.
 *
 * Secrets: `api_key` only ever travels browser -> server (create / replace). Nothing in the API
 * returns it; the server answers with provider, status and the last four characters only.
 * The caller keeps the typed key in component state for the duration of the dialog and clears it.
 */
const API_URL = import.meta.env.VITE_API_URL || 'http://localhost:8000/api/v1';
const BASE = `${API_URL}/ai`;

export const PROVIDERS = ['anthropic', 'gemini', 'openai'];
// Operations the product runs through the gateway today (data for the routing table; names are API identifiers).
export const OPERATIONS = ['default', 'copy.generate', 'copy.regenerate_field', 'ad_remix.deconstruct', 'ad_remix.reconstruct'];
export const MAX_FALLBACKS = 5;

/** Failure of an API call, reduced to what the UI may act on (never the provider's body). */
export class AiApiError extends Error {
    constructor(status, kind) {
        super(`AI API ${status}${kind ? ` ${kind}` : ''}`);
        this.name = 'AiApiError';
        this.status = status;
        this.kind = kind || null; // X-AI-Error: authentication | rate_limit | budget_exceeded | not_configured ...
    }
}

/** i18n key suffix for an error: the server's kind when it sent one, else derived from the status. */
export function errorKind(error) {
    if (!(error instanceof AiApiError) || error.status === 0) return 'network'; // the request never got an answer
    if (error.kind) return error.kind;
    switch (error.status) {
        case 403: return 'forbidden';
        case 404: return 'notFound';
        case 409: return 'conflict';
        case 422: return 'invalid';
        case 429: return 'rate_limit';
        case 502: case 503: case 504: return 'unavailable';
        default: return 'unknown';
    }
}

const EMPTY_TOTALS = { requests: 0, input_tokens: 0, output_tokens: 0, cached_input_tokens: 0, cost_usd: 0, estimated_cost_usd: 0, errors: 0 };
/** The summary with every field the screen reads present, whatever the server sent. */
export function normalizeSummary(raw) {
    const data = raw && typeof raw === 'object' ? raw : {};
    const rows = (value) => (Array.isArray(value) ? value : []);
    return {
        days: data.days ?? null,
        totals: { ...EMPTY_TOTALS, ...(data.totals && typeof data.totals === 'object' ? data.totals : {}) },
        by_day: rows(data.by_day), by_model: rows(data.by_model), by_operation: rows(data.by_operation), by_user: rows(data.by_user),
    };
}

const json = (value) => ({ method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(value) });
const withMethod = (method, value) => ({ ...json(value), method });

export function createAiApi(authFetch) {
    const call = async (path, options) => {
        let response;
        try {
            response = await authFetch(`${BASE}${path}`, options);
        } catch {
            throw new AiApiError(0, null);
        }
        if (!response.ok) throw new AiApiError(response.status, response.headers?.get?.('X-AI-Error'));
        return response.json();
    };
    const list = async (path) => {
        const data = await call(path);
        return Array.isArray(data) ? data : [];
    };
    const enc = encodeURIComponent;
    return {
        providers: () => list('/providers'),
        credentials: () => list('/credentials'),
        createCredential: (body) => call('/credentials', json(body)),
        testCredential: (id) => call(`/credentials/${enc(id)}/test`, { method: 'POST' }),
        replaceCredential: (id, body) => call(`/credentials/${enc(id)}/rotate`, json(body)),
        revokeCredential: (id) => call(`/credentials/${enc(id)}`, { method: 'DELETE' }),

        models: () => list('/models'),
        discoverModels: (provider) => list(`/models/discover?provider=${enc(provider)}`),
        addModel: (body) => call('/models', json(body)),
        updateModel: (id, body) => call(`/models/${enc(id)}`, withMethod('PATCH', body)),
        removeModel: (id) => call(`/models/${enc(id)}`, { method: 'DELETE' }),

        routing: () => list('/routing'),
        saveRoute: (operation, body) => call(`/routing/${enc(operation)}`, withMethod('PUT', body)),
        clearRoute: (operation) => call(`/routing/${enc(operation)}`, { method: 'DELETE' }),

        budgets: () => list('/budgets'),
        saveBudget: (period, body) => call(`/budgets/${enc(period)}`, withMethod('PUT', body)),
        removeBudget: (period) => call(`/budgets/${enc(period)}`, { method: 'DELETE' }),

        usageSummary: async (days) => normalizeSummary(await call(`/usage/summary?days=${Number(days) || 30}`)),
        usageLogs: (limit = 25) => list(`/usage/logs?limit=${Number(limit) || 25}`),
    };
}
