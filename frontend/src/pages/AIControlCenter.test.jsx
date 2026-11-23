/**
 * AI Control Center in jsdom: the REAL AuthProvider, OrganizationProvider, TenantBoundary, ToastProvider and
 * page against the in-memory, tenant-aware backend (src/__tests__/fakeBackend.js + fakeAiBackend.js).
 * Nothing here reaches a real provider or the real API.
 */
import React from 'react';
import { act, cleanup, fireEvent, render, screen, waitFor, within } from '@testing-library/react';
import { MemoryRouter } from 'react-router-dom';

import i18n from '../i18n';
import { createFakeBackend, memoryStorage } from '../__tests__/fakeBackend';
import { AuthProvider } from '../context/AuthContext';
import { OrganizationProvider, TenantBoundary } from '../context/OrganizationContext';
import { ToastProvider } from '../context/ToastContext';
import OrganizationSwitcher from '../components/OrganizationSwitcher';
import OrganizationPermissionsModal from '../components/OrganizationPermissionsModal';
import AIControlCenter from './AIControlCenter';
import { clearTenant } from '../lib/tenant';
import { budgetState, worstBudget } from '../lib/aiBudget';
import { AiApiError, errorKind } from '../lib/aiApi';

const API = 'http://localhost:8000/api/v1';
const KEY = 'sk-test-ABCDEFGH-1234567890-WXYZ';
const KEY2 = 'sk-test-REPLACEMENT-0987654321-QRST';
let backend;

const Tree = () => (
    <MemoryRouter>
        <ToastProvider>
            <AuthProvider>
                <OrganizationProvider>
                    <TenantBoundary>
                        <OrganizationSwitcher />
                        <AIControlCenter />
                    </TenantBoundary>
                </OrganizationProvider>
            </AuthProvider>
        </ToastProvider>
    </MemoryRouter>
);

const signIn = (userKey) => {
    const tokens = backend.loginTokens(userKey);
    localStorage.setItem('accessToken', tokens.access_token);
    localStorage.setItem('refreshToken', tokens.refresh_token);
};
const mount = async (userKey = 'alice') => {
    signIn(userKey);
    render(<Tree />);
    await screen.findByRole('tablist');
};
const click = async (element) => { await act(async () => { fireEvent.click(element); }); };
const type = async (element, value) => { await act(async () => { fireEvent.change(element, { target: { value } }); }); };
const openTab = async (name) => { await click(screen.getByRole('tab', { name })); };
const button = (name) => screen.getByRole('button', { name });
const dialog = () => within(screen.getByRole('dialog'));
const card = (provider) => within(screen.getByTestId(`provider-${provider}`));
const aiCalls = () => backend.calls().filter((c) => c.path.startsWith('/ai/'));

const addKey = async (provider, key = KEY, { verify = true } = {}) => {
    await click(card(provider).getByRole('button', { name: 'Add API key' }));
    await type(dialog().getByLabelText('API key'), key);
    if (!verify) await click(dialog().getByRole('checkbox'));
    await click(dialog().getByRole('button', { name: 'Save key' }));
    await waitFor(() => expect(screen.queryByRole('dialog')).not.toBeInTheDocument());
};

beforeEach(async () => {
    global.localStorage = memoryStorage();
    clearTenant();
    backend = createFakeBackend();
    global.fetch = backend.fetch;
    await act(async () => { await i18n.changeLanguage('en-US'); });
});
afterEach(() => cleanup());

describe('provider keys (BYOK)', () => {
    it('starts empty: not configured, with a clear next step', async () => {
        await mount();
        expect(screen.getByTestId('ai-overview')).toHaveTextContent("AI isn't set up yet");
        for (const provider of ['anthropic', 'gemini', 'openai']) {
            expect(screen.getByTestId(`provider-${provider}-status`)).toHaveTextContent('Not configured');
            expect(card(provider).getByRole('button', { name: 'Add API key' })).toBeInTheDocument();
        }
        expect(screen.getByText('OpenAI')).toBeInTheDocument();
        expect(screen.getByText('Google Gemini')).toBeInTheDocument();
    });

    it('saves a key and shows ONLY its last four characters, never the key', async () => {
        await mount();
        await addKey('openai');
        expect(screen.getByTestId('provider-openai-last4')).toHaveTextContent('Key ending in WXYZ');
        expect(screen.getByTestId('provider-openai-status')).toHaveTextContent('Connection verified');
        expect(document.body.textContent).not.toContain(KEY);
        expect(document.body.textContent).not.toContain('ABCDEFGH');
        // the key travelled once, in the create request, and no later answer carries it
        const writes = aiCalls().filter((c) => c.body?.api_key);
        expect(writes).toHaveLength(1);
        expect(writes[0]).toMatchObject({ method: 'POST', path: '/ai/credentials', org: 'org-a1' });
        for (const path of ['/ai/credentials', '/ai/providers']) {
            const response = await backend.fetch(`${API}${path}`, { headers: { Authorization: `Bearer ${localStorage.getItem('accessToken')}`, 'X-Organization-Id': 'org-a1' } });
            expect(JSON.stringify(await response.json())).not.toContain('ABCDEFGH');
        }
        expect(screen.getByText('OpenAI key saved.')).toBeInTheDocument();
    });

    it('clears the typed key when the dialog is closed and never pre-fills it again', async () => {
        await mount();
        await click(card('anthropic').getByRole('button', { name: 'Add API key' }));
        await type(dialog().getByLabelText('API key'), KEY);
        await click(dialog().getByRole('button', { name: 'Cancel' }));
        await click(card('anthropic').getByRole('button', { name: 'Add API key' }));
        expect(dialog().getByLabelText('API key')).toHaveValue('');
        expect(dialog().getByLabelText('API key')).toHaveAttribute('type', 'password');
        expect(dialog().getByLabelText('API key')).toHaveAttribute('autocomplete', 'off');
    });

    it('validates the key length before calling the server', async () => {
        await mount();
        await click(card('openai').getByRole('button', { name: 'Add API key' }));
        await type(dialog().getByLabelText('API key'), 'short');
        await click(dialog().getByRole('button', { name: 'Save key' }));
        expect(dialog().getByRole('alert')).toHaveTextContent('The key is too short.');
        expect(aiCalls().some((c) => c.method === 'POST')).toBe(false);
    });

    it('shows a safe message when the provider rejects the key and saves nothing', async () => {
        backend.ai.state.rejectKeys.add(KEY);
        await mount();
        await click(card('openai').getByRole('button', { name: 'Add API key' }));
        await type(dialog().getByLabelText('API key'), KEY);
        await click(dialog().getByRole('button', { name: 'Save key' }));
        await waitFor(() => expect(dialog().getByRole('alert')).toHaveTextContent('The provider rejected the API key.'));
        expect(document.body.textContent).not.toContain(KEY);
        await click(dialog().getByRole('button', { name: 'Cancel' }));
        expect(screen.getByTestId('provider-openai-status')).toHaveTextContent('Not configured');
    });

    it('tests the connection and reports success and failure', async () => {
        await mount();
        await addKey('openai', KEY, { verify: false });
        expect(screen.getByTestId('provider-openai-status')).toHaveTextContent('Not tested yet');
        await click(card('openai').getByRole('button', { name: 'Test connection' }));
        await waitFor(() => expect(screen.getByText('OpenAI connection works.')).toBeInTheDocument());
        expect(screen.getByTestId('provider-openai-status')).toHaveTextContent('Connection verified');
        backend.ai.state.failTest = true;
        await click(card('openai').getByRole('button', { name: 'Test connection' }));
        await waitFor(() => expect(screen.getByText('OpenAI connection failed: The provider rejected the API key.')).toBeInTheDocument());
        expect(screen.getByTestId('provider-openai-status')).toHaveTextContent('Verification failed');
    });

    it('replaces a key (new last four) and revokes it after confirmation', async () => {
        await mount();
        await addKey('openai');
        await click(card('openai').getByRole('button', { name: 'Replace key' }));
        await type(dialog().getByLabelText('API key'), KEY2);
        await click(dialog().getByRole('button', { name: 'Replace key' }));
        await waitFor(() => expect(screen.getByTestId('provider-openai-last4')).toHaveTextContent('Key ending in QRST'));
        expect(document.body.textContent).not.toContain('REPLACEMENT');

        await click(card('openai').getByRole('button', { name: 'Revoke key' }));
        expect(screen.getByText('Revoke this key?')).toBeInTheDocument();
        await click(screen.getAllByRole('button', { name: 'Revoke key' }).at(-1)); // the modal's confirm button
        await waitFor(() => expect(screen.getByTestId('provider-openai-status')).toHaveTextContent('Key revoked'));
        expect(card('openai').getByRole('button', { name: 'Add API key' })).toBeInTheDocument();
        expect(Object.keys(backend.ai.state.secrets)).toHaveLength(0); // wiped server-side
    });

    it('does not revoke when the confirmation is cancelled', async () => {
        await mount();
        await addKey('openai');
        await click(card('openai').getByRole('button', { name: 'Revoke key' }));
        await click(screen.getByRole('button', { name: 'Cancel' }));
        expect(screen.getByTestId('provider-openai-status')).toHaveTextContent('Connection verified');
        expect(aiCalls().some((c) => c.method === 'DELETE')).toBe(false);
    });
});

describe('model catalog', () => {
    const addModelManually = async (id, { input = '3', output = '15', cached = '' } = {}) => {
        await click(button('Add model'));
        await type(dialog().getByLabelText('Model ID'), id);
        await type(dialog().getByLabelText('Input price (USD per 1M tokens)'), input);
        await type(dialog().getByLabelText('Output price (USD per 1M tokens)'), output);
        if (cached) await type(dialog().getByLabelText('Cached input price (USD per 1M tokens)'), cached);
        await click(dialog().getByRole('button', { name: 'Save model' }));
    };

    it('adds a model with capabilities and prices, including a cached-token price', async () => {
        await mount();
        await openTab('Models');
        expect(screen.getByText('No models yet')).toBeInTheDocument();
        await click(button('Add model'));
        await type(dialog().getByLabelText('Model ID'), 'my-model');
        await type(dialog().getByLabelText('Display name (optional)'), 'My model');
        await click(dialog().getByRole('checkbox', { name: 'Vision' }));
        await type(dialog().getByLabelText('Input price (USD per 1M tokens)'), '3');
        await type(dialog().getByLabelText('Output price (USD per 1M tokens)'), '15');
        await type(dialog().getByLabelText('Cached input price (USD per 1M tokens)'), '0.30');
        await click(dialog().getByRole('button', { name: 'Save model' }));
        const row = await screen.findByTestId('model-my-model');
        expect(row).toHaveTextContent('My model');
        expect(row).toHaveTextContent('Vision');
        expect(row).toHaveTextContent('$3.00');
        expect(row).toHaveTextContent('$15.00');
        expect(row).toHaveTextContent('$0.30');
        expect(backend.ai.state.orgs['org-a1'].models[0]).toMatchObject({ supports_vision: true, cached_input_price_usd_per_mtok: 0.3 });
        expect(screen.getByText('My model added.')).toBeInTheDocument();
    });

    it('never invents prices: an empty price stays "not set" and the model is flagged under a spending limit', async () => {
        backend.ai.seed.budget(backend.ai.state, 'org-a1', { period: 'daily', limit_usd: 10 });
        await mount();
        await openTab('Models');
        await addModelManually('no-price', { input: '', output: '' });
        const row = await screen.findByTestId('model-no-price');
        expect(row).toHaveTextContent('not set');
        expect(row).toHaveTextContent('No price set: blocked while a spending limit is active.');
        expect(row).toHaveTextContent('No active key for this provider.');
    });

    it('validates prices and the model id inline', async () => {
        await mount();
        await openTab('Models');
        await click(button('Add model'));
        await click(dialog().getByRole('button', { name: 'Save model' }));
        expect(dialog().getByText('Enter the model ID.')).toBeInTheDocument();
        await type(dialog().getByLabelText('Model ID'), 'x');
        await type(dialog().getByLabelText('Input price (USD per 1M tokens)'), '-1');
        await click(dialog().getByRole('button', { name: 'Save model' }));
        expect(dialog().getByText('Enter a number of 0 or more.')).toBeInTheDocument();
        expect(aiCalls().some((c) => c.method === 'POST')).toBe(false);
    });

    it('reports a duplicate model and keeps the form open', async () => {
        await mount();
        await openTab('Models');
        await addModelManually('dup');
        await screen.findByTestId('model-dup');
        await addModelManually('dup');
        await waitFor(() => expect(dialog().getByRole('alert')).toHaveTextContent('That already exists or isn\'t allowed right now.'));
    });

    it('edits, disables and removes a model (with confirmation)', async () => {
        await mount();
        await openTab('Models');
        await addModelManually('edit-me');
        const row = await screen.findByTestId('model-edit-me');
        await click(within(row).getByRole('switch'));
        await waitFor(() => expect(within(screen.getByTestId('model-edit-me')).getByText('Disabled')).toBeInTheDocument());
        await click(within(screen.getByTestId('model-edit-me')).getByRole('button', { name: /Edit/ }));
        await type(dialog().getByLabelText('Output price (USD per 1M tokens)'), '20');
        await click(dialog().getByRole('button', { name: 'Save model' }));
        await waitFor(() => expect(screen.getByTestId('model-edit-me')).toHaveTextContent('$20.00'));
        await click(within(screen.getByTestId('model-edit-me')).getByRole('button', { name: /Remove/ }));
        expect(screen.getByText('Remove this model?')).toBeInTheDocument();
        await click(screen.getByRole('button', { name: 'Remove model' }));
        await waitFor(() => expect(screen.queryByTestId('model-edit-me')).not.toBeInTheDocument());
    });

    it('discovers models from the provider and opens the form prefilled (capabilities and prices stay manual)', async () => {
        backend.ai.state.discoverable.openai = [{ model_id: 'found-1', display_name: null }, { model_id: 'found-2', display_name: null }];
        await mount();
        await addKey('openai');
        await openTab('Models');
        await click(button('Discover models'));
        await waitFor(() => expect(dialog().getAllByText('found-1').length).toBeGreaterThan(0));
        expect(dialog().getByText(/capabilities and prices aren't/i)).toBeInTheDocument();
        // the dialog opens on the first provider holding an active key: openai
        await click(dialog().getByRole('button', { name: /Add\s*found-1/ }));
        expect(dialog().getByLabelText('Model ID')).toHaveValue('found-1');
        expect(dialog().getByLabelText('Input price (USD per 1M tokens)')).toHaveValue('');
        expect(dialog().getByRole('checkbox', { name: 'Vision' })).not.toBeChecked();
    });

    it('suggests the provider that already has an active key when adding a model', async () => {
        await mount();
        await addKey('openai');
        await openTab('Models');
        await click(button('Add model'));
        expect(dialog().getByLabelText('Provider')).toHaveValue('openai');
    });

    it('says a key is needed before discovering', async () => {
        await mount();
        await openTab('Models');
        await click(button('Discover models'));
        expect(dialog().getByText('Add an active Anthropic key first.')).toBeInTheDocument();
    });
});

describe('routing', () => {
    const seedModels = async (names) => {
        await openTab('Models');
        for (const name of names) {
            await click(button('Add model'));
            await type(dialog().getByLabelText('Model ID'), name);
            await click(dialog().getByRole('button', { name: 'Save model' }));
            await screen.findByTestId(`model-${name}`);
        }
        await openTab('Routing');
    };
    const route = (operation) => within(screen.getByTestId(`route-${operation}`));

    it('asks for a model first when the catalog is empty', async () => {
        await mount();
        await openTab('Routing');
        expect(screen.getByText('Add a model first to create a route.')).toBeInTheDocument();
    });

    it('saves a primary model with ordered fallbacks and explains when fallbacks run', async () => {
        await mount();
        await seedModels(['m-one', 'm-two', 'm-three']);
        expect(screen.getByText(/only when the provider is rate limiting, timing out or unavailable/)).toBeInTheDocument();
        expect(route('copy.generate').getByText('No route set: follows the default route.')).toBeInTheDocument();
        const ids = backend.ai.state.orgs['org-a1'].models.map((m) => m.id);

        await type(route('copy.generate').getByLabelText('Main model'), ids[0]);
        await type(route('copy.generate').getByLabelText('Add fallback'), ids[1]);
        await click(route('copy.generate').getByRole('button', { name: 'Add fallback' }));
        await type(route('copy.generate').getByLabelText('Add fallback'), ids[2]);
        await click(route('copy.generate').getByRole('button', { name: 'Add fallback' }));
        await click(route('copy.generate').getByRole('button', { name: 'Move m-three up' }));
        expect(route('copy.generate').getByText('Unsaved changes')).toBeInTheDocument();
        await click(route('copy.generate').getByRole('button', { name: 'Save route' }));
        await waitFor(() => expect(screen.getByText('Route saved.')).toBeInTheDocument());
        expect(backend.ai.state.orgs['org-a1'].routes[0]).toEqual({ operation: 'copy.generate', primary_model_id: ids[0], fallback_model_ids: [ids[2], ids[1]] });
        await waitFor(() => expect(route('copy.generate').queryByText('Unsaved changes')).not.toBeInTheDocument());
    });

    it('never lets the main model also be a fallback, and clears a route', async () => {
        await mount();
        await seedModels(['m-one', 'm-two']);
        const ids = backend.ai.state.orgs['org-a1'].models.map((m) => m.id);
        await type(route('default').getByLabelText('Main model'), ids[0]);
        await type(route('default').getByLabelText('Add fallback'), ids[1]);
        await click(route('default').getByRole('button', { name: 'Add fallback' }));
        await type(route('default').getByLabelText('Main model'), ids[1]); // m-two becomes main: leaves the fallbacks
        expect(route('default').getByText('No fallbacks.')).toBeInTheDocument();
        await click(route('default').getByRole('button', { name: 'Save route' }));
        await waitFor(() => expect(route('default').getByRole('button', { name: 'Clear route' })).toBeInTheDocument());
        await click(route('default').getByRole('button', { name: 'Clear route' }));
        await waitFor(() => expect(screen.getByText('Route cleared.')).toBeInTheDocument());
        expect(backend.ai.state.orgs['org-a1'].routes).toEqual([]);
    });
});

describe('usage and limits', () => {
    const seedUsage = () => {
        const state = backend.ai.state;
        backend.ai.seed.log(state, 'org-a1', { operation: 'copy.generate', model_id: 'gpt-x', input_tokens: 1000, output_tokens: 500, cached_input_tokens: 800, cost_usd: 0.0105, user_id: 'u-alice' });
        backend.ai.seed.log(state, 'org-a1', { operation: 'ad_remix.reconstruct', model_id: 'gpt-x', cost_usd: 0.002 });
        backend.ai.seed.log(state, 'org-a1', { status: 'error', error_kind: 'rate_limit', input_tokens: 0, output_tokens: 0, cost_usd: null });
    };

    it('shows tokens, USD cost, errors, breakdowns and recent history', async () => {
        seedUsage();
        await mount();
        await openTab('Usage and limits');
        await waitFor(() => expect(screen.getByTestId('kpi-requests')).toHaveTextContent('3'));
        expect(screen.getByTestId('kpi-cost')).toHaveTextContent('$0.0125');
        expect(screen.getByTestId('kpi-input')).toHaveTextContent('1,100');
        expect(screen.getByTestId('kpi-output')).toHaveTextContent('550');
        expect(screen.getByTestId('kpi-cached')).toHaveTextContent('800');
        expect(screen.getByTestId('kpi-errors')).toHaveTextContent('1');
        const history = within(screen.getByTestId('usage-history'));
        expect(history.getByText('Failed: Too many requests. Try again shortly.')).toBeInTheDocument();
        expect(screen.getAllByText('Generate ad copy').length).toBeGreaterThan(0);
        expect(screen.getByText('By model')).toBeInTheDocument();
    });

    it('shows an empty state without requests', async () => {
        await mount();
        await openTab('Usage and limits');
        await waitFor(() => expect(screen.getByText('No AI requests in this period.')).toBeInTheDocument());
        expect(screen.getAllByText('No limit set: spending isn\'t capped.')).toHaveLength(2);
    });

    it('sets, changes, disables and removes a daily limit', async () => {
        await mount();
        await openTab('Usage and limits');
        const daily = () => within(screen.getByTestId('budget-daily'));
        await click(daily().getByRole('button', { name: /Set limit/ }));
        await type(dialog().getByLabelText('Limit (USD)'), 'abc');
        await click(dialog().getByRole('button', { name: 'Save limit' }));
        expect(dialog().getByText('Enter an amount of 0 or more.')).toBeInTheDocument();
        await type(dialog().getByLabelText('Limit (USD)'), '25');
        await click(dialog().getByRole('button', { name: 'Save limit' }));
        await waitFor(() => expect(screen.getByText('Daily limit saved.')).toBeInTheDocument());
        expect(daily().getByTestId('budget-daily-state')).toHaveTextContent('Within the limit');
        expect(backend.ai.state.orgs['org-a1'].budgets[0]).toMatchObject({ period: 'daily', limit_usd: 25, enabled: true });

        await click(daily().getByRole('checkbox', { name: /Enforce this limit/ }));
        await waitFor(() => expect(daily().getByTestId('budget-daily-state')).toHaveTextContent('Not enforced'));
        await click(daily().getByRole('button', { name: /Remove limit/ }));
        await click(screen.getByRole('button', { name: 'Remove limit' }));
        await waitFor(() => expect(screen.getAllByText('No limit set: spending isn\'t capped.')).toHaveLength(2));
    });

    it('warns near the limit and blocks messaging when it is reached', async () => {
        backend.ai.seed.budget(backend.ai.state, 'org-a1', { period: 'daily', limit_usd: 10, spent_usd: 8.5 });
        await mount();
        expect(screen.getByTestId('ai-budget-alert')).toHaveTextContent('Daily spending is at 85% of the limit.');
        expect(screen.getByTestId('ai-budget-alert')).toHaveAttribute('role', 'status');
        cleanup();
        backend.ai.state.orgs['org-a1'].budgets[0].spent_usd = 10;
        render(<Tree />);
        await screen.findByRole('tablist');
        expect(screen.getByTestId('ai-budget-alert')).toHaveTextContent('The daily spending limit has been reached. AI requests are blocked until it resets.');
        expect(screen.getByTestId('ai-budget-alert')).toHaveAttribute('role', 'alert');
        await openTab('Usage and limits');
        expect(screen.getByTestId('budget-daily-state')).toHaveTextContent('Limit reached: requests are blocked');
        expect(screen.getByRole('progressbar', { name: 'Daily' })).toHaveAttribute('aria-valuenow', '100');
    });
});

describe('overview, errors and unavailable service', () => {
    it('reports ready once a key and an enabled model exist', async () => {
        await mount();
        await addKey('openai');
        expect(screen.getByTestId('ai-overview')).toHaveTextContent('connected but no model is enabled');
        await openTab('Models');
        await click(button('Add model'));
        await type(dialog().getByLabelText('Model ID'), 'm');
        await click(dialog().getByRole('button', { name: 'Save model' }));
        await screen.findByTestId('model-m');
        expect(screen.getByTestId('ai-overview')).toHaveTextContent('AI is ready: 1 provider connected, 1 enabled.');
    });

    it('shows "service unavailable" with a retry, then recovers', async () => {
        backend.ai.state.failNext = { status: 503, kind: 'unavailable' };
        signIn('alice');
        render(<Tree />);
        expect(await screen.findByText('The AI service is unavailable')).toBeInTheDocument();
        await click(button('Try again'));
        expect(await screen.findByRole('tablist')).toBeInTheDocument();
    });

    it('turns a failed save into a clear, secret-free message', async () => {
        await mount();
        await openTab('Models');
        await click(button('Add model'));
        await type(dialog().getByLabelText('Model ID'), 'x');
        backend.ai.state.failNext = { status: 429, kind: 'rate_limit' };
        await click(dialog().getByRole('button', { name: 'Save model' }));
        await waitFor(() => expect(dialog().getByRole('alert')).toHaveTextContent('Too many requests. Try again shortly.'));
    });
});

describe('RBAC', () => {
    const roleScreen = async (role) => {
        backend.setRole('u-alice', 'org-a1', role);
        await mount();
    };

    it('analyst: can read everything but gets no management controls', async () => {
        backend.ai.seed.budget(backend.ai.state, 'org-a1', { period: 'daily', limit_usd: 10 });
        await roleScreen('analyst');
        expect(screen.getByTestId('ai-readonly')).toHaveTextContent('only administrators can change it');
        expect(screen.queryByRole('button', { name: 'Add API key' })).not.toBeInTheDocument();
        expect(aiCalls().some((c) => c.path === '/ai/credentials')).toBe(false); // never even asks for the key list
        await openTab('Models');
        expect(screen.queryByRole('button', { name: 'Add model' })).not.toBeInTheDocument();
        await openTab('Routing');
        await openTab('Usage and limits');
        expect(screen.queryByRole('button', { name: /Set limit|Change limit/ })).not.toBeInTheDocument();
    });

    it('manager: still read-only (ai:use is not ai:manage)', async () => {
        await roleScreen('manager');
        expect(screen.getByTestId('ai-readonly')).toBeInTheDocument();
        expect(screen.queryByRole('button', { name: 'Add API key' })).not.toBeInTheDocument();
    });

    it('viewer: no access screen and not a single AI request', async () => {
        backend.setRole('u-alice', 'org-a1', 'viewer');
        signIn('alice');
        render(<Tree />);
        await waitFor(() => expect(screen.getByText("You can't view the AI settings")).toBeInTheDocument());
        expect(screen.queryByRole('tablist')).not.toBeInTheDocument();
        expect(aiCalls()).toEqual([]);
    });

    it('admin: manages', async () => {
        await roleScreen('admin');
        expect(screen.queryByTestId('ai-readonly')).not.toBeInTheDocument();
        expect(card('openai').getByRole('button', { name: 'Add API key' })).toBeInTheDocument();
    });

    it('a server refusal (permission revoked meanwhile) is shown, not swallowed', async () => {
        await mount();
        backend.setRole('u-alice', 'org-a1', 'analyst'); // demoted while the page is open
        await click(card('openai').getByRole('button', { name: 'Add API key' }));
        await type(dialog().getByLabelText('API key'), KEY);
        await click(dialog().getByRole('button', { name: 'Save key' }));
        await waitFor(() => expect(dialog().getByRole('alert')).toHaveTextContent("Your role doesn't allow this action."));
        expect(backend.ai.state.orgs['org-a1']?.credentials || []).toHaveLength(0);
    });
});

describe('organization isolation', () => {
    it('switching organizations drops every value of the previous one and re-reads the new one', async () => {
        backend.setRole('u-alice', 'org-a2', 'admin');
        await mount();
        await addKey('openai');
        await openTab('Models');
        await click(button('Add model'));
        await type(dialog().getByLabelText('Model ID'), 'only-in-one');
        await click(dialog().getByRole('button', { name: 'Save model' }));
        await screen.findByTestId('model-only-in-one');
        backend.ai.seed.budget(backend.ai.state, 'org-a1', { period: 'daily', limit_usd: 10, spent_usd: 9 });

        await act(async () => { fireEvent.click(screen.getByTestId('org-switcher')); });
        await click(screen.getAllByRole('option').find((o) => o.textContent.includes('Acme Two')));
        await waitFor(() => expect(screen.getByTestId('ai-overview')).toHaveTextContent("AI isn't set up yet"));
        expect(screen.queryByTestId('ai-budget-alert')).not.toBeInTheDocument();
        expect(screen.getByTestId('provider-openai-status')).toHaveTextContent('Not configured');
        await openTab('Models');
        expect(screen.queryByTestId('model-only-in-one')).not.toBeInTheDocument();
        expect(document.body.textContent).not.toContain('WXYZ');

        const reads = aiCalls().filter((c) => c.method === 'GET');
        expect(reads.filter((c) => c.org === 'org-a2').length).toBeGreaterThan(0);
        expect(new Set(aiCalls().map((c) => c.org))).toEqual(new Set(['org-a1', 'org-a2']));
        // no request for org-a2 can carry org-a1's data and vice versa: every call names exactly one org
        expect(aiCalls().every((c) => c.org === 'org-a1' || c.org === 'org-a2')).toBe(true);
    });

    it('persists settings across a reload (data comes from the server, not the browser)', async () => {
        await mount();
        await addKey('openai');
        cleanup();
        render(<Tree />);
        await screen.findByRole('tablist');
        expect(screen.getByTestId('provider-openai-last4')).toHaveTextContent('Key ending in WXYZ');
        expect(localStorage._data.size).toBeGreaterThan(0);
        expect([...localStorage._data.values()].join('|')).not.toContain('ABCDEFGH'); // nothing sensitive in browser storage
    });
});

describe('language', () => {
    it('renders fully in pt-BR, including the page chrome', async () => {
        await act(async () => { await i18n.changeLanguage('pt-BR'); });
        await mount();
        expect(screen.getByRole('heading', { level: 1 })).toHaveTextContent('Central de IA');
        expect(screen.getByRole('tab', { name: 'Provedores' })).toBeInTheDocument();
        expect(screen.getAllByRole('button', { name: 'Adicionar chave de API' })).toHaveLength(3);
        expect(screen.getByTestId('ai-overview')).toHaveTextContent('A IA ainda não foi configurada');
        expect(document.body.textContent).not.toMatch(/\bai:[a-z.]+\b|\{\{|undefined/);
    });
});

describe('role matrix labels for ai:* permissions', () => {
    const renderMatrix = async () => {
        signIn('alice');
        render(
            <MemoryRouter>
                <ToastProvider>
                    <AuthProvider>
                        <OrganizationProvider>
                            <TenantBoundary><OrganizationPermissionsModal isOpen onClose={() => {}} /></TenantBoundary>
                        </OrganizationProvider>
                    </AuthProvider>
                </ToastProvider>
            </MemoryRouter>,
        );
        await screen.findByTestId('role-matrix');
    };

    it('en-US: human labels, no raw permission names', async () => {
        await renderMatrix();
        const matrix = within(screen.getByTestId('role-matrix'));
        expect(matrix.getByText('View AI settings, models and usage')).toBeInTheDocument();
        expect(matrix.getByText('Use AI inside features')).toBeInTheDocument();
        expect(matrix.getByText('Manage AI keys, models, routing and limits')).toBeInTheDocument();
        expect(matrix.getByText('AI')).toBeInTheDocument();
        expect(screen.getByTestId('role-matrix').textContent).not.toMatch(/ai:(read|use|manage)/);
    });

    it('pt-BR: the same labels in Portuguese', async () => {
        await act(async () => { await i18n.changeLanguage('pt-BR'); });
        await renderMatrix();
        const matrix = within(screen.getByTestId('role-matrix'));
        expect(matrix.getByText('Ver configurações, modelos e consumo de IA')).toBeInTheDocument();
        expect(matrix.getByText('Usar IA dentro das funcionalidades')).toBeInTheDocument();
        expect(matrix.getByText('Gerenciar chaves, modelos, roteamento e limites de IA')).toBeInTheDocument();
        expect(matrix.getByText('IA')).toBeInTheDocument();
        expect(screen.getByTestId('role-matrix').textContent).not.toMatch(/ai:(read|use|manage)/);
    });

    it('grants follow the server: analyst reads, manager uses, only admin and owner manage', async () => {
        await renderMatrix();
        const allowed = (name) => ['owner', 'admin', 'manager', 'analyst', 'viewer'].map((_, i) => within(screen.getByTestId(`perm-row-${name}`)).getAllByRole('cell')[i].getAttribute('data-allowed'));
        expect(allowed('ai:read')).toEqual(['true', 'true', 'true', 'true', 'false']);
        expect(allowed('ai:use')).toEqual(['true', 'true', 'true', 'false', 'false']);
        expect(allowed('ai:manage')).toEqual(['true', 'true', 'false', 'false', 'false']);
    });
});

describe('pure helpers', () => {
    it('budgetState: ok / warning at 80% / reached / off / zero limit blocks', () => {
        expect(budgetState({ enabled: true, limit_usd: 10, spent_usd: 1, reserved_usd: 0 }).state).toBe('ok');
        expect(budgetState({ enabled: true, limit_usd: 10, spent_usd: 7, reserved_usd: 1 }).state).toBe('warning');
        expect(budgetState({ enabled: true, limit_usd: 10, spent_usd: 9, reserved_usd: 1 })).toMatchObject({ state: 'reached', percent: 100 });
        expect(budgetState({ enabled: false, limit_usd: 10, spent_usd: 99, reserved_usd: 0 }).state).toBe('off');
        expect(budgetState({ enabled: true, limit_usd: 0, spent_usd: 0, reserved_usd: 0 }).state).toBe('reached');
    });

    it('worstBudget prefers a reached limit over a warning and ignores unenforced ones', () => {
        const budgets = [
            { period: 'daily', enabled: true, limit_usd: 10, spent_usd: 8.5, reserved_usd: 0 },
            { period: 'monthly', enabled: true, limit_usd: 10, spent_usd: 10, reserved_usd: 0 },
            { period: 'x', enabled: false, limit_usd: 1, spent_usd: 5, reserved_usd: 0 },
        ];
        expect(worstBudget(budgets).budget.period).toBe('monthly');
        expect(worstBudget([{ period: 'daily', enabled: true, limit_usd: 10, spent_usd: 1, reserved_usd: 0 }])).toBeNull();
    });

    it('errorKind prefers the server kind, then the status, then "network"', () => {
        expect(errorKind(new AiApiError(429, 'budget_exceeded'))).toBe('budget_exceeded');
        expect(errorKind(new AiApiError(403, null))).toBe('forbidden');
        expect(errorKind(new AiApiError(503, null))).toBe('unavailable');
        expect(errorKind(new AiApiError(0, null))).toBe('network');
        expect(errorKind(new AiApiError(500, null))).toBe('unknown');
        expect(errorKind(new Error('x'))).toBe('network');
    });
});
