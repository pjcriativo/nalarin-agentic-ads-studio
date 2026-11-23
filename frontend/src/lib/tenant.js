/**
 * Tenant (organization) selection store.
 *
 * Module-level on purpose: axios interceptors and plain fetch helpers live
 * outside React and must send the SAME `X-Organization-Id` as the React tree.
 *
 * Rules
 * -----
 * - The selection is persisted per USER (`tenant:org:<userId>`) so a reload keeps
 *   it, but it is only a hint: OrganizationContext validates it against the
 *   server's membership list on every load and clears it when access is gone.
 * - Logout / user change clears everything (state + every persisted selection +
 *   every tenant-namespaced preference).
 * - `epoch` increases on every identity change (user or organization). Requests
 *   remember the epoch they started in; a response that arrives after the epoch
 *   moved belongs to the PREVIOUS tenant and must never reach the UI.
 */
export const ORG_HEADER = 'X-Organization-Id';
const SELECTION_PREFIX = 'tenant:org:';
const STORAGE_PREFIX = 'tenant:data:';

const store = (() => {
    const call = (fn, fallback) => {
        try { return fn(); } catch { return fallback; }
    };
    return {
        get: (key) => call(() => localStorage.getItem(key), null),
        set: (key, value) => call(() => localStorage.setItem(key, value)),
        remove: (key) => call(() => localStorage.removeItem(key)),
        keys: () => call(() => {
            const out = [];
            for (let i = 0; i < localStorage.length; i += 1) out.push(localStorage.key(i));
            return out;
        }, []),
    };
})();

let state = { userId: null, orgId: null, epoch: 0 };
const listeners = new Set();
const accessListeners = new Set();

const notify = () => listeners.forEach((fn) => fn({ ...state }));

export const getOrganizationId = () => state.orgId;
export const getUserId = () => state.userId;
export const getEpoch = () => state.epoch;

export const subscribe = (fn) => {
    listeners.add(fn);
    return () => listeners.delete(fn);
};

/** Called when the authenticated user is known (or becomes unknown). */
export function bindUser(userId) {
    const id = userId == null ? null : String(userId);
    if (state.userId === id) return;
    state = { userId: id, orgId: id ? store.get(SELECTION_PREFIX + id) : null, epoch: state.epoch + 1 };
    notify();
}

/** Select (and persist) an organization. Pass null to clear the selection. */
export function setOrganizationId(orgId, { persist = true } = {}) {
    const id = orgId == null ? null : String(orgId);
    if (state.orgId === id) return;
    state = { ...state, orgId: id, epoch: state.epoch + 1 };
    if (state.userId) {
        if (persist && id) store.set(SELECTION_PREFIX + state.userId, id);
        else store.remove(SELECTION_PREFIX + state.userId);
    }
    notify();
}

/** Logout: forget the user, the selection and every tenant-scoped preference. */
export function clearTenant() {
    for (const key of store.keys()) {
        if (key.startsWith(SELECTION_PREFIX) || key.startsWith(STORAGE_PREFIX)) store.remove(key);
    }
    state = { userId: null, orgId: null, epoch: state.epoch + 1 };
    notify();
}

export const tenantHeaders = () => (state.orgId ? { [ORG_HEADER]: state.orgId } : {});

/** The server said this user has no access to the selected organization (403/400). */
export function reportAccessLost() {
    accessListeners.forEach((fn) => fn());
}
export const onAccessLost = (fn) => {
    accessListeners.add(fn);
    return () => accessListeners.delete(fn);
};

const SCOPED_PATTERNS = [
    /access to this organization/i,
    /No active organization membership/i,
    /Several organizations/i,
];
export const isAccessLostMessage = (detail) => SCOPED_PATTERNS.some((re) => re.test(String(detail || '')));

/**
 * localStorage namespaced by user AND organization, for per-tenant UI
 * preferences/drafts (last ad account, default URL, wizard drafts...). With no
 * selected organization it reads nothing and writes nothing.
 */
const scopedKey = (key) => (state.userId && state.orgId ? `${STORAGE_PREFIX}${state.userId}:${state.orgId}:${key}` : null);
export const tenantStorage = {
    getItem(key) {
        const k = scopedKey(key);
        return k ? store.get(k) : null;
    },
    setItem(key, value) {
        const k = scopedKey(key);
        if (k) store.set(k, value);
    },
    removeItem(key) {
        const k = scopedKey(key);
        if (k) store.remove(k);
    },
};

/** A promise that never settles: used to drop a response that belongs to a previous tenant. */
export const dropStaleResponse = () => new Promise(() => {});
