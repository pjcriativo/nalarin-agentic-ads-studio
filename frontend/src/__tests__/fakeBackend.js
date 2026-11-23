/**
 * In-memory stand-in for the FastAPI backend, tenant-aware like the real one:
 *  - Bearer token -> user (401 otherwise); tokens can be expired to test refresh.
 *  - X-Organization-Id is validated against memberships (403 "no access", 400 when
 *    the user has several organizations and sends none), exactly like get_current_org.
 *  - Role permissions mirror backend/app/core/tenancy.py ROLE_PERMISSIONS.
 *  - Spend approvals: request (ads:draft) -> approve/reject (ads:spend) -> enable
 *    (ads:spend + consumed approval), including expiry and single use.
 * Nothing here talks to a real service. Every call is recorded in `calls`.
 */
import { enablePayloadHash } from '../lib/spendApprovals.js';
import { aiSeed, createAiState, handleAi } from './fakeAiBackend.js';
import { agentsSeed, createAgentsState, handleAgents } from './fakeAgentsBackend.js';

const VIEWER = ['org:read', 'members:read', 'brands:read', 'products:read', 'profiles:read', 'ads:read', 'templates:read', 'campaigns:read', 'research:read'];
const ANALYST = [...VIEWER, 'reports:read', 'research:write', 'connections:read', 'ai:read', 'agents:read'];
const MANAGER = [...ANALYST, 'brands:write', 'brands:delete', 'products:write', 'products:delete', 'profiles:write', 'profiles:delete', 'ads:write', 'ads:delete', 'templates:write', 'templates:delete', 'campaigns:write', 'campaigns:delete', 'research:delete', 'uploads:write', 'ads:draft', 'ai:use', 'agents:run'];
const ADMIN = [...MANAGER, 'members:manage', 'audit:read', 'connections:manage', 'apikeys:manage', 'ads:publish', 'ai:manage', 'agents:manage'];
const OWNER = [...ADMIN, 'org:manage', 'ads:spend'];
export const ROLE_PERMISSIONS = { owner: OWNER, admin: ADMIN, manager: MANAGER, analyst: ANALYST, viewer: VIEWER };
export const ROLES = ['owner', 'admin', 'manager', 'analyst', 'viewer'];

const brand = (id, name) => ({
    id, name, logo: '', voice: 'Friendly', colors: { primary: '#112233', secondary: '#445566', highlight: '#778899' }, products: [], profileIds: [],
});

const json = (status, body, extraHeaders = {}) => ({
    ok: status >= 200 && status < 300,
    status,
    headers: { get: (name) => extraHeaders[name] ?? null },
    // like a real network answer: a COPY, never a live reference into the server's state
    json: async () => JSON.parse(JSON.stringify(body)),
    clone() { return json(status, body, extraHeaders); },
    text: async () => JSON.stringify(body),
});

export function createFakeBackend() {
    const state = {
        users: {
            alice: { id: 'u-alice', email: 'alice@example.com', name: 'Alice', password: 'pw' },
            bob: { id: 'u-bob', email: 'bob@example.com', name: 'Bob', password: 'pw' },
        },
        orgs: {
            a1: { id: 'org-a1', name: 'Acme One', slug: 'acme-one' },
            a2: { id: 'org-a2', name: 'Acme Two', slug: 'acme-two' },
            b: { id: 'org-b', name: 'Beta Corp', slug: 'beta' },
        },
        members: [
            { user: 'u-alice', org: 'org-a1', role: 'owner', status: 'active' },
            { user: 'u-alice', org: 'org-a2', role: 'viewer', status: 'active' },
            { user: 'u-bob', org: 'org-b', role: 'owner', status: 'active' },
        ],
        brands: {
            'org-a1': [brand('br-a1', 'Alpha One Brand')],
            'org-a2': [brand('br-a2', 'Alpha Two Brand')],
            'org-b': [brand('br-b', 'Beta Brand')],
        },
        approvals: [],
        campaigns: [{ id: '111', name: 'Brand search', status: 'PAUSED', impressions: 10, clicks: 1, cost: 1, conversions: 0 }],
        tokens: {},
        refreshTokens: {},
        calls: [],
        ai: createAiState(),
        agents: createAgentsState(),
        googleConnected: true,
        deferred: [],
        enabledCampaigns: [],
        seq: 0,
    };

    const issue = (userId) => {
        state.seq += 1;
        const access = `at-${userId}-${state.seq}`;
        const refresh = `rt-${userId}-${state.seq}`;
        state.tokens[access] = userId;
        state.refreshTokens[refresh] = userId;
        return { access_token: access, refresh_token: refresh };
    };

    const membershipsOf = (userId) => state.members.filter((m) => m.user === userId && m.status === 'active');
    const permsOf = (role) => [...ROLE_PERMISSIONS[role]].sort();

    /** Mirrors get_current_org + require_org_permission. Returns {error} or {org, role, perms}. */
    const tenant = (userId, headers, permission) => {
        const mine = membershipsOf(userId);
        let membership;
        if (headers['X-Organization-Id']) {
            membership = mine.find((m) => m.org === headers['X-Organization-Id']);
            if (!membership) return { error: json(403, { detail: 'You do not have access to this organization' }) };
        } else {
            if (mine.length === 0) return { error: json(403, { detail: 'No active organization membership' }) };
            if (mine.length > 1) return { error: json(400, { detail: 'Several organizations: send the X-Organization-Id header' }) };
            [membership] = mine;
        }
        if (permission && !ROLE_PERMISSIONS[membership.role].includes(permission)) {
            return { error: json(403, { detail: 'Insufficient permissions in this organization' }) };
        }
        return { org: membership.org, role: membership.role, userId };
    };

    const expiredNow = (a) => new Date(a.expires_at).getTime() <= Date.now();

    const handle = async (rawUrl, init = {}) => {
        const url = new URL(rawUrl);
        const path = url.pathname.replace(/^\/api\/v1/, '');
        const method = (init.method || 'GET').toUpperCase();
        const headers = Object.fromEntries(Object.entries(init.headers || {}));
        const body = init.body ? JSON.parse(init.body) : undefined;
        const call = { method, path, org: headers['X-Organization-Id'] || null, auth: headers.Authorization || null, body };
        state.calls.push(call);

        // ---- auth endpoints
        if (path === '/auth/login/json') {
            const user = Object.values(state.users).find((u) => u.email === body.email && u.password === body.password);
            return user ? json(200, issue(user.id)) : json(401, { detail: 'Incorrect email or password' });
        }
        if (path === '/auth/refresh') {
            const userId = state.refreshTokens[body.refresh_token];
            return userId ? json(200, issue(userId)) : json(401, { detail: 'Invalid refresh token' });
        }
        if (path === '/auth/logout') return json(200, {});

        const userId = state.tokens[(headers.Authorization || '').replace('Bearer ', '')];
        if (!userId) return json(401, { detail: 'Could not validate credentials' });
        const user = Object.values(state.users).find((u) => u.id === userId);

        if (path === '/auth/me') return json(200, { id: user.id, email: user.email, name: user.name, is_active: true, is_superuser: false, roles: [] });

        // ---- organizations (no organization header needed)
        if (path === '/organizations' && method === 'GET') {
            return json(200, membershipsOf(userId).map((m) => ({
                id: m.org, name: state.orgs[Object.keys(state.orgs).find((k) => state.orgs[k].id === m.org)].name,
                slug: 'x', role: m.role, permissions: permsOf(m.role),
            })));
        }
        if (path === '/organizations/role-matrix') {
            return json(200, { roles: ROLES, permissions: Object.fromEntries(ROLES.map((r) => [r, permsOf(r)])) });
        }

        // concurrency hook: a test can hold a request open and release it later
        const hold = state.deferred.find((d) => d.path === path && d.org === (headers['X-Organization-Id'] || null));
        const respond = async (response) => {
            if (hold) await hold.promise;
            return response;
        };

        // ---- tenant data
        if (path === '/brands') {
            const t = tenant(userId, headers, 'brands:read');
            return respond(t.error || json(200, state.brands[t.org] || []));
        }
        if (path === '/profiles') {
            const t = tenant(userId, headers, 'profiles:read');
            return respond(t.error || json(200, []));
        }
        if (path === '/dashboard/stats') {
            const t = tenant(userId, headers);
            return respond(t.error || json(200, { brands_count: (state.brands[t.org] || []).length, products_count: 0, generated_ads_count: 0, templates_count: 0, campaigns_count: 0 }));
        }

        // ---- Google Ads
        if (path === '/google-ads/connection') {
            const t = tenant(userId, headers, 'connections:read');
            if (!t.error && state.googleConnected === false) return json(200, { connected: false });
            return t.error || json(200, { connected: true, customer_id: '1111111111', account_name: `Account of ${t.org}`, connected_at: '2026-01-01T00:00:00Z' });
        }
        if (path === '/google-ads/connections') {
            const t = tenant(userId, headers, 'connections:read');
            return t.error || json(200, { connections: [{ customer_id: '1111111111', account_name: 'Account', selected: true }] });
        }
        if (path === '/google-ads/campaigns' && method === 'GET') {
            const t = tenant(userId, headers, 'ads:read');
            return t.error || json(200, { customer_id: '1111111111', campaigns: state.campaigns });
        }
        const enableMatch = path.match(/^\/google-ads\/campaigns\/([^/]+)\/enable$/);
        if (enableMatch && method === 'POST') {
            const t = tenant(userId, headers, 'ads:spend');
            if (t.error) return t.error;
            const approval = state.approvals.find((a) => a.id === body.approval_id && a.organization_id === t.org);
            if (!approval) return json(403, { detail: 'Spend refused: unknown approval' });
            if (approval.status === 'consumed') return json(403, { detail: 'Spend refused: approval is consumed' });
            if (approval.status !== 'approved' || expiredNow(approval)) return json(403, { detail: `Spend refused: approval is ${approval.status}` });
            if (approval.payload_hash !== enablePayloadHash(enableMatch[1])) return json(403, { detail: 'Spend refused: approval does not match' });
            approval.status = 'consumed';
            state.enabledCampaigns.push(enableMatch[1]);
            return json(200, { campaign_id: enableMatch[1], status: 'ENABLED' });
        }

        // ---- approvals
        if (path === '/optimization/approvals' && method === 'GET') {
            const t = tenant(userId, headers, 'ads:read');
            return t.error || json(200, state.approvals.filter((a) => a.organization_id === t.org).slice().reverse());
        }
        if (path === '/optimization/approvals' && method === 'POST') {
            const t = tenant(userId, headers, 'ads:draft');
            if (t.error) return t.error;
            const existing = state.approvals.find((a) => a.organization_id === t.org && a.idempotency_key === body.idempotency_key);
            if (existing) return json(201, existing);
            const created = {
                id: `ap-${state.approvals.length + 1}`, organization_id: t.org, provider: body.provider, account_id: body.account_id,
                action: body.action, payload_hash: enablePayloadHash(body.payload.campaign_id), status: 'pending',
                requested_budget_micros: body.budget_micros, max_budget_micros: null, idempotency_key: body.idempotency_key,
                requested_by_user_id: userId, requested_by_api_key_id: null, approved_by_user_id: null,
                created_at: new Date().toISOString(), expires_at: new Date(Date.now() + 30 * 60 * 1000).toISOString(),
            };
            state.approvals.push(created);
            return json(201, created);
        }
        const decide = path.match(/^\/optimization\/approvals\/([^/]+)\/(approve|reject)$/);
        if (decide && method === 'POST') {
            const t = tenant(userId, headers, 'ads:spend');
            if (t.error) return t.error;
            const approval = state.approvals.find((a) => a.id === decide[1] && a.organization_id === t.org);
            if (!approval) return json(404, { detail: 'Approval not found' });
            if (approval.status !== 'pending') return json(409, { detail: `Approval is already ${approval.status}` });
            if (decide[2] === 'approve') {
                if (!body.max_budget_micros || body.max_budget_micros < (approval.requested_budget_micros || 0)) return json(422, { detail: 'budget' });
                approval.max_budget_micros = body.max_budget_micros;
                approval.status = 'approved';
            } else {
                approval.status = 'rejected';
            }
            approval.approved_by_user_id = userId;
            return json(200, approval);
        }

        // ---- AI Agents (/api/v1/agents)
        if (path.startsWith('/agents')) {
            return respond(handleAgents({ state: state.agents, aiState: state.ai, path, method, body, headers, query: url.searchParams, userId, tenant, json }));
        }
        // ---- AI Control Center (/api/v1/ai)
        if (path.startsWith('/ai/')) {
            return respond(handleAi({ state: state.ai, path, method, body, headers, query: url.searchParams, userId, tenant, json }));
        }

        // endpoints only needed so pages that load them do not show error toasts in e2e screenshots
        if (path === '/optimization/status') {
            return json(200, { kill_switch_active: false, engine_active: false, auto_enabled: false, allowed_actions: [], max_budget_micros: null, max_daily_spend_micros: null, max_actions_per_day: null, operating_hours: { start: null, end: null, timezone: 'America/Sao_Paulo' } });
        }
        if (path === '/overview') return json(200, { campaigns: [], errors: {} });
        if (['/facebook/connection', '/tiktok-ads/connection'].includes(path)) return json(200, { connected: false });
        if (['/facebook/connections', '/tiktok-ads/connections'].includes(path)) return json(200, { connections: [] });

        return json(404, { detail: 'Not found' });
    };

    const fetchImpl = (url, init) => handle(url, init); // plain function: also usable outside vitest (e2e)

    return {
        state,
        fetch: vitestSpy(fetchImpl),
        calls: () => state.calls,
        callsTo: (path) => state.calls.filter((c) => c.path === path),
        /** Hold the next answers of GET path (for that organization) until release() is called. */
        hold(path, org) {
            let release;
            const promise = new Promise((resolve) => { release = resolve; });
            state.deferred.push({ path, org, promise });
            return () => { release(); state.deferred = state.deferred.filter((d) => d.promise !== promise); };
        },
        expireAccessTokens() { state.tokens = {}; },
        revoke(userId, orgId) {
            const m = state.members.find((x) => x.user === userId && x.org === orgId);
            if (m) m.status = 'revoked';
        },
        setRole(userId, orgId, role) {
            state.members.find((x) => x.user === userId && x.org === orgId).role = role;
        },
        loginTokens: (userKey) => issue(state.users[userKey].id),
        setGoogleConnected(value) { state.googleConnected = value; },
        ai: { state: state.ai, seed: aiSeed },
        agents: { state: state.agents, seed: agentsSeed },
    };
}

/** Real in-memory localStorage (setup.js replaces the global one with vi.fn stubs). */
export function memoryStorage(initial = {}) {
    const data = new Map(Object.entries(initial));
    return {
        getItem: (k) => (data.has(k) ? data.get(k) : null),
        setItem: (k, v) => { data.set(k, String(v)); },
        removeItem: (k) => { data.delete(k); },
        clear: () => data.clear(),
        key: (i) => [...data.keys()][i] ?? null,
        get length() { return data.size; },
        _data: data,
    };
}

// vitest's spy when running under vitest, the plain function otherwise (e2e in Node)
function vitestSpy(fn) {
    return typeof vi !== 'undefined' ? vi.fn(fn) : fn;
}
