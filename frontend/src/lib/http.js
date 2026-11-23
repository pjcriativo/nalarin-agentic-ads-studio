/**
 * axios wiring (src/api/*.js uses axios directly): the same guarantees authFetch
 * gives fetch callers.
 *
 *  - Bearer token + `X-Organization-Id` on every request to OUR API (never to a
 *    third-party host: credentials must not leak).
 *  - One refresh-and-retry on 401 (shared with AuthContext through the bridge).
 *  - A response that arrives after the organization/user changed belongs to the
 *    previous tenant and is dropped.
 */
import axios from 'axios';
import { dropStaleResponse, getEpoch, tenantHeaders } from './tenant';

const API_URL = import.meta.env.VITE_API_URL || 'http://localhost:8000/api/v1';

const bridge = { refresh: null };
export const authBridge = bridge;
/** AuthContext registers its shared refresh here (null when unmounted). */
export const registerAuthBridge = (next) => {
    bridge.refresh = next?.refresh || null;
};

const isOurApi = (url) => typeof url === 'string' && (url.startsWith(API_URL) || url.startsWith('/'));

const setHeader = (config, name, value) => {
    if (config.headers && typeof config.headers.set === 'function') config.headers.set(name, value);
    else config.headers = { ...(config.headers || {}), [name]: value };
};

export function installAxiosTenancy(client = axios) {
    if (!client?.interceptors?.request?.use) return;
    client.interceptors.request.use((config) => {
        const url = `${config.baseURL || ''}${config.url || ''}`;
        if (!isOurApi(config.url?.startsWith('http') ? config.url : url)) return config;
        const token = localStorage.getItem('accessToken');
        if (token) setHeader(config, 'Authorization', `Bearer ${token}`);
        for (const [name, value] of Object.entries(tenantHeaders())) setHeader(config, name, value);
        config.metadata = { ...(config.metadata || {}), epoch: getEpoch() };
        return config;
    });
    client.interceptors.response.use(
        (response) => (response.config?.metadata && response.config.metadata.epoch !== getEpoch() ? dropStaleResponse() : response),
        async (error) => {
            const config = error?.config;
            if (config?.metadata && config.metadata.epoch !== getEpoch()) return dropStaleResponse();
            if (error?.response?.status === 401 && config && !config._retried && bridge.refresh && isOurApi(config.url)) {
                config._retried = true;
                const token = await bridge.refresh();
                setHeader(config, 'Authorization', `Bearer ${token}`);
                return client.request(config);
            }
            throw error;
        },
    );
}

installAxiosTenancy();
