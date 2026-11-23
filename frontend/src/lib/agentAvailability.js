import { needsGoogleAds } from './agentsApi';

/**
 * What the dashboard knows about the organization's setup. Every field is `null` when it could not be read (no permission
 * or the call failed): an unknown is NEVER turned into "ok" or into a problem, the check is simply not made.
 *   ai:      { hasCredential: bool, hasModel: bool } | null           (from /ai/providers + /ai/models)
 *   google:  { connected: bool } | null                               (from /google-ads/connection)
 *   worker:  { online: bool, workers: number, queue: {...} } | null   (from /agents/worker/status)
 */
export const UNKNOWN_SETUP = { ai: null, google: null, worker: null };

/**
 * Real availability of ONE agent for this organization, in priority order. `limited` means it runs but a data source it
 * uses is missing, so its answers will be hypotheses / "unavailable" for that source (it never fabricates the data).
 */
export function agentAvailability(agent, setup = UNKNOWN_SETUP) {
    if (!agent.enabled) return { state: 'disabled', blocking: true };
    if (agent.routing && agent.routing.has_route === false) return { state: 'noModel', blocking: true };
    if (setup.ai && !setup.ai.hasCredential) return { state: 'noCredential', blocking: true };
    if (setup.ai && !setup.ai.hasModel) return { state: 'noModel', blocking: true };
    if (needsGoogleAds(agent.allowed_tools) && setup.google && !setup.google.connected) return { state: 'limited', blocking: false, missing: 'google' };
    return { state: 'ready', blocking: false };
}

export const AVAILABILITY_TONE = { ready: 'good', limited: 'warn', disabled: 'neutral', noModel: 'bad', noCredential: 'bad' };

/** Banners for the top of the page, most important first. Each is { id, tone, link? } ; text comes from i18n by id. */
export function setupAlerts({ setup, agents, budgets }) {
    const alerts = [];
    if (setup.worker && !setup.worker.online) {
        alerts.push({ id: 'workerOffline', tone: 'bad', pending: setup.worker.queue?.pending || 0 });
    }
    if (setup.ai && !setup.ai.hasCredential) alerts.push({ id: 'noCredential', tone: 'bad', link: '/ai' });
    else if (setup.ai && !setup.ai.hasModel) alerts.push({ id: 'noModel', tone: 'bad', link: '/ai' });
    else if (agents.some((a) => a.routing && a.routing.has_route === false && a.enabled)) alerts.push({ id: 'noRoute', tone: 'warn', link: '/ai' });
    const reached = (budgets || []).find((b) => b.enabled && b.limit_usd > 0 && b.spent_usd + b.reserved_usd >= b.limit_usd);
    if (reached) alerts.push({ id: 'budgetReached', tone: 'bad', link: '/ai', period: reached.period });
    if (setup.google && !setup.google.connected && agents.some((a) => a.enabled && needsGoogleAds(a.allowed_tools))) {
        alerts.push({ id: 'googleMissing', tone: 'warn', link: '/google-ads' });
    }
    return alerts;
}
