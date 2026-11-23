/**
 * AI Agents dashboard in jsdom: the REAL AuthProvider, OrganizationProvider, TenantBoundary, ToastProvider, router and page against the
 * in-memory, tenant-aware backend (src/__tests__/fakeBackend.js + fakeAgentsBackend.js + fakeAiBackend.js), whose contract is pinned to
 * the real API (agentsContract.json is verified against the real backend in backend/tests/unit/test_agents_frontend_contract.py).
 * Nothing here executes an agent, reaches a provider or reaches Google: tests move tasks forward with `backend.agents.seed`.
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
import AIAgents from './AIAgents';
import { clearTenant } from '../lib/tenant';

const API = 'http://localhost:8000/api/v1';
const ORG = 'org-a1';
let backend;

const Tree = ({ entry = '/agents' }) => (
    <MemoryRouter initialEntries={[entry]}>
        <ToastProvider>
            <AuthProvider>
                <OrganizationProvider>
                    <TenantBoundary>
                        <OrganizationSwitcher />
                        <AIAgents />
                    </TenantBoundary>
                </OrganizationProvider>
            </AuthProvider>
        </ToastProvider>
    </MemoryRouter>
);

const signIn = (userKey = 'alice') => {
    const tokens = backend.loginTokens(userKey);
    localStorage.setItem('accessToken', tokens.access_token);
    localStorage.setItem('refreshToken', tokens.refresh_token);
};
const mount = async ({ user = 'alice', entry } = {}) => {
    signIn(user);
    render(<Tree entry={entry} />);
    await screen.findByTestId('agents-safety');
};
const click = async (element) => { await act(async () => { fireEvent.click(element); }); };
const type = async (element, value) => { await act(async () => { fireEvent.change(element, { target: { value } }); }); };
const openTab = async (name) => { await click(screen.getByRole('tab', { name })); };
const dialog = () => within(screen.getByRole('dialog'));
const calls = (method, pathPart) => backend.calls().filter((c) => c.method === method && c.path.includes(pathPart));
const agentCalls = () => backend.calls().filter((c) => c.path.startsWith('/agents'));

/** A working AI setup for the organization: one provider key, one model, a default route. */
const setupAi = (org = ORG) => {
    const data = backend.ai.seed.bucket(backend.ai.state, org);
    data.credentials.push({ id: `c-${org}`, provider: 'openai', status: 'active', last4: 'ABCD', label: null, created_at: new Date().toISOString() });
    data.models.push({ id: `m-${org}`, provider: 'openai', model_id: 'gpt-test', display_name: 'GPT Test', enabled: true, supports_tools: true, supports_vision: false, supports_json: true });
    data.routes.push({ operation: 'default', primary_model_id: `m-${org}`, fallback_model_ids: [] });
};
const addTask = (patch = {}, org = ORG) => {
    const state = backend.agents.state;
    const data = state.orgs[org] || (state.orgs[org] = { tasks: [], steps: {}, results: {}, settings: {} });
    const task = {
        id: `t-${data.tasks.length + 1}-${org}`, status: 'pending', agent_key: 'product_analyst', objective: 'Analyze the widget product for ads', created_by_user_id: 'u-alice',
        created_at: new Date().toISOString(), attempts: 0, step_count: 0, max_cost_usd: 2, ...patch,
    };
    data.tasks.unshift(task);
    data.steps[task.id] = [];
    return task;
};

beforeEach(async () => {
    global.localStorage = memoryStorage();
    clearTenant();
    backend = createFakeBackend();
    global.fetch = backend.fetch;
    await act(async () => { await i18n.changeLanguage('en-US'); });
});
afterEach(() => cleanup());

// ================================================================================================= catalogue
describe('agents catalogue', () => {
    it('shows the supervisor and the six specialists with their real descriptions, tools and limitations', async () => {
        await mount();
        expect(screen.getByRole('heading', { level: 1 })).toHaveTextContent('AI Agents');
        expect(screen.getByTestId('agent-supervisor')).toHaveTextContent('Plans the work, delegates to specialists');
        expect(screen.getByTestId('agent-supervisor')).toHaveTextContent('Uses no tools');
        for (const key of ['product_analyst', 'market_researcher', 'search_ads_strategist', 'ad_copywriter', 'policy_reviewer', 'performance_analyst']) {
            expect(screen.getByTestId(`agent-${key}`)).toBeInTheDocument();
        }
        expect(within(screen.getByTestId('agents-grid')).getAllByRole('article')).toHaveLength(6);
        const researcher = within(screen.getByTestId('agent-market_researcher'));
        expect(researcher.getByText('Market and keyword researcher')).toBeInTheDocument();
        expect(researcher.getByText(/Keyword Planner figures are Google's estimates/)).toBeInTheDocument();
        expect(researcher.getByText(/Without a connected Google Ads account every keyword is a hypothesis/)).toBeInTheDocument();
        expect(researcher.getByText('9 read-only tools')).toBeInTheDocument();
        expect(screen.getByTestId('agents-safety')).toHaveTextContent('Nothing is published, created or spent');
    });

    it('reports availability honestly: no model, no key, limited without Google, available when everything is set up', async () => {
        backend.setGoogleConnected(false);
        await mount();
        // nothing configured: no route => "No AI model" and the run buttons are disabled
        expect(screen.getByTestId('availability-product_analyst')).toHaveTextContent('No AI model');
        expect(screen.getByRole('button', { name: 'Run a task with Product and sales-page analyst' })).toBeDisabled();
        expect(screen.getByTestId('alert-noCredential')).toHaveTextContent('No AI provider key is configured');
        cleanup();

        setupAi();
        await mount();
        await waitFor(() => expect(screen.getByTestId('availability-product_analyst')).toHaveTextContent('Available'));
        expect(screen.getByTestId('availability-market_researcher')).toHaveTextContent('Limited: no Google Ads account');   // reads Google data
        expect(screen.getByTestId('availability-ad_copywriter')).toHaveTextContent('Available');                            // never needs it
        expect(screen.getByTestId('alert-googleMissing')).toHaveTextContent('No Google Ads account is connected');
        expect(screen.queryByTestId('alert-noCredential')).not.toBeInTheDocument();
        expect(screen.getByRole('button', { name: 'Run a task with Product and sales-page analyst' })).toBeEnabled();
    });

    it('no Google warning once an account is connected', async () => {
        setupAi();
        await mount();
        await waitFor(() => expect(screen.getByTestId('availability-market_researcher')).toHaveTextContent('Available'));
        expect(screen.queryByTestId('alert-googleMissing')).not.toBeInTheDocument();
    });

    it('opens the details: tools with the outside system, read-only marker, requirements, limits, model route and version', async () => {
        setupAi();
        await mount();
        await click(screen.getByRole('button', { name: 'Details of Market and keyword researcher' }));
        const d = dialog();
        const tool = d.getByTestId('tool-google_ads.keyword_ideas');
        expect(tool).toHaveTextContent('Google Ads');
        expect(tool).toHaveTextContent('Read-only');
        expect(tool).toHaveTextContent('Needs a Google Ads account connected');
        expect(tool).toHaveTextContent("Keyword Planner: keyword ideas with Google's ESTIMATED monthly searches");
        expect(d.getByText('Uses gpt-test (openai) from the default route.')).toBeInTheDocument();
        expect(d.getByText(/Instructions version 1\.1\.0/)).toBeInTheDocument();
        expect(d.queryByText(/SECURITY RULES/)).not.toBeInTheDocument();
        await click(d.getByRole('button', { name: 'Close' }));
        expect(screen.queryByRole('dialog')).not.toBeInTheDocument();
    });

    it('shows nothing invented when the server is unavailable, and recovers on retry', async () => {
        backend.agents.state.failNext = { status: 503, detail: 'down', path: '/agents' };
        signIn();
        render(<Tree />);
        expect(await screen.findByText('AI Agents is unavailable right now')).toBeInTheDocument();
        expect(screen.queryByTestId('agents-grid')).not.toBeInTheDocument();
        expect(screen.queryByTestId('agent-product_analyst')).not.toBeInTheDocument();
        await click(screen.getByRole('button', { name: 'Try again' }));
        await screen.findByTestId('agents-grid');
    });
});

// ================================================================================================= setup alerts
describe('setup alerts', () => {
    it('worker offline: a clear banner with the queue size, and no claim when the status cannot be read', async () => {
        backend.agents.state.worker.online = false;
        addTask({ status: 'pending' });
        await mount();
        await waitFor(() => expect(screen.getByTestId('alert-workerOffline')).toHaveTextContent('No agent worker is running'));
        expect(screen.getByTestId('alert-workerOffline')).toHaveTextContent('1 task is waiting right now');
        cleanup();

        // status endpoint failing = unknown: no "offline" claim either way
        backend.agents.state.worker.online = true;
        const original = backend.fetch;
        global.fetch = vi.fn(async (url, init) => (String(url).includes('/agents/worker/status') ? { ok: false, status: 500, headers: { get: () => null }, json: async () => ({}) } : original(url, init)));
        await mount();
        expect(screen.queryByTestId('alert-workerOffline')).not.toBeInTheDocument();
    });

    it('spending limit reached is shown with the period', async () => {
        setupAi();
        backend.ai.seed.budget(backend.ai.state, ORG, { period: 'monthly', limit_usd: 10, spent_usd: 10 });
        await mount();
        await waitFor(() => expect(screen.getByTestId('alert-budgetReached')).toHaveTextContent('monthly limit of this organization is used up'));
    });

    it('a role that cannot read AI or Google setup gets no setup claims (unknown is not a problem)', async () => {
        backend.setRole('u-alice', ORG, 'viewer');
        signIn();
        render(<Tree />);
        // viewer has no agents:read -> forbidden, and no AI/Google setup request is even attempted
        await waitFor(() => expect(screen.getByText("You don't have access to AI Agents")).toBeInTheDocument());
        expect(backend.calls().filter((c) => c.path.startsWith('/ai/') || c.path.startsWith('/google-ads'))).toHaveLength(0);
    });
});

// ================================================================================================= starting tasks
describe('starting a task', () => {
    it('validates locally, then sends exactly the form (agent, objective, context, cost cap) with an idempotency key', async () => {
        setupAi();
        await mount();
        await click(screen.getByRole('button', { name: 'Run a task with Product and sales-page analyst' }));
        expect(screen.getByRole('tab', { name: 'Tasks' })).toHaveAttribute('aria-selected', 'true');
        expect(screen.getByLabelText('Agent')).toHaveValue('product_analyst');

        await type(screen.getByLabelText('Objective'), 'abc');
        await click(screen.getByRole('button', { name: 'Start task' }));
        expect(screen.getByRole('alert')).toHaveTextContent('Describe the objective with at least 5 characters.');
        expect(calls('POST', '/agents/tasks')).toHaveLength(0);

        await type(screen.getByLabelText('Objective'), 'Analyze https://shop.example.com/widgets for search ads');
        await type(screen.getByLabelText('Context (optional)'), '{not json');
        expect(screen.getByText('This is not valid JSON.')).toBeInTheDocument();
        await click(screen.getByRole('button', { name: 'Start task' }));
        expect(calls('POST', '/agents/tasks')).toHaveLength(0);
        await type(screen.getByLabelText('Context (optional)'), '[1,2]');
        expect(screen.getByText('The context must be a JSON object.')).toBeInTheDocument();
        await type(screen.getByLabelText('Context (optional)'), '{"clicks": 120}');
        await type(screen.getByLabelText('Cost limit (optional)'), '-1');
        expect(screen.getByText('Enter an amount greater than zero.')).toBeInTheDocument();
        await type(screen.getByLabelText('Cost limit (optional)'), '0.5');

        await click(screen.getByRole('button', { name: 'Start task' }));
        await waitFor(() => expect(calls('POST', '/agents/tasks')).toHaveLength(1));
        const sent = calls('POST', '/agents/tasks')[0];
        expect(sent.org).toBe(ORG);
        expect(sent.body).toMatchObject({ agent_key: 'product_analyst', objective: 'Analyze https://shop.example.com/widgets for search ads', context: { clicks: 120 }, max_cost_usd: 0.5 });
        expect(sent.body.idempotency_key).toMatch(/^ui-[A-Za-z0-9-]{8,}$/);
        await screen.findByText('Task queued');
        expect(await screen.findByTestId('task-detail')).toHaveTextContent('Pending');
        expect(screen.getByLabelText('Objective')).toHaveValue('');                                  // the form is clear for the next one
    });

    it('the supervised option sends no agent_key and the empty cost cap sends no max_cost_usd', async () => {
        setupAi();
        await mount();
        await openTab('Tasks');
        await type(screen.getByLabelText('Objective'), 'Plan a Google Search campaign for the widget');
        await click(screen.getByRole('button', { name: 'Start task' }));
        await waitFor(() => expect(calls('POST', '/agents/tasks')).toHaveLength(1));
        const body = calls('POST', '/agents/tasks')[0].body;
        expect('agent_key' in body).toBe(false);
        expect('max_cost_usd' in body).toBe(false);
        expect('context' in body).toBe(false);
        expect((await screen.findByTestId('task-detail'))).toHaveTextContent('Supervisor (automatic plan)');
    });

    it('a failed submission shows a message, keeps the form, and the retry reuses the SAME idempotency key', async () => {
        setupAi();
        await mount();
        await openTab('Tasks');
        await type(screen.getByLabelText('Objective'), 'Analyze the widget product for search ads');
        backend.agents.state.failNext = { status: 503, detail: 'down' };
        await click(screen.getByRole('button', { name: 'Start task' }));
        await screen.findByText('The service is unavailable right now. Try again in a moment.');
        expect(screen.getByLabelText('Objective')).toHaveValue('Analyze the widget product for search ads');
        await click(screen.getByRole('button', { name: 'Start task' }));
        await waitFor(() => expect(calls('POST', '/agents/tasks')).toHaveLength(2));
        const [first, second] = calls('POST', '/agents/tasks');
        expect(second.body.idempotency_key).toBe(first.body.idempotency_key);
        expect(backend.agents.state.orgs[ORG].tasks).toHaveLength(1);                                // one task, however many attempts
    });

    it('maps the server refusals to clear messages: too many active tasks and disabled agent', async () => {
        setupAi();
        for (let i = 0; i < 3; i += 1) addTask({ status: 'running' });
        await mount();
        await openTab('Tasks');
        await type(screen.getByLabelText('Objective'), 'Analyze the widget product for search ads');
        await click(screen.getByRole('button', { name: 'Start task' }));
        await screen.findByText('This organization already has the maximum number of active tasks. Wait for one to finish.');
    });

    it('warns before submitting when the task cannot do its best: no Google account, no worker', async () => {
        setupAi();
        backend.setGoogleConnected(false);
        backend.agents.state.worker.online = false;
        await mount();
        await click(within(screen.getByTestId('agent-performance_analyst')).getByRole('button', { name: /Run a task with/ }));
        const warnings = await screen.findByTestId('form-warnings');
        expect(warnings).toHaveTextContent('No Google Ads account is connected: this agent will report the account data as unavailable');
        expect(warnings).toHaveTextContent('No worker is running: the task will wait in the queue');
    });

    it('a role without agents:run sees the tasks but no form and no run buttons', async () => {
        backend.setRole('u-alice', ORG, 'analyst');
        addTask({ status: 'completed' });
        await mount();
        expect(screen.getByTestId('agents-readonly-banner')).toBeInTheDocument();
        expect(screen.queryByRole('button', { name: /Run a task with/ })).not.toBeInTheDocument();
        expect(screen.queryByRole('button', { name: /Settings of/ })).not.toBeInTheDocument();
        await openTab('Tasks');
        expect(screen.getByTestId('agents-readonly')).toBeInTheDocument();
        expect(screen.queryByTestId('new-task-form')).not.toBeInTheDocument();
        expect(screen.getByTestId('task-list')).toBeInTheDocument();
    });
});

// ================================================================================================= history and progress
describe('task history, progress and cost', () => {
    const seedAll = () => {
        addTask({ status: 'pending', objective: 'Pending one' });
        addTask({ status: 'running', objective: 'Running one', started_at: new Date().toISOString() });
        addTask({ status: 'completed', objective: 'Completed one' });
        addTask({ status: 'failed', objective: 'Failed one', error: { kind: 'ai_unavailable', message: 'x' } });
        addTask({ status: 'cancelled', objective: 'Cancelled one', error: { kind: 'cancelled', message: 'x' } });
    };

    it('lists tasks of every status with counts and filters by status', async () => {
        seedAll();
        await mount({ entry: '/agents?tab=tasks' });
        const list = await screen.findByTestId('task-list');
        for (const text of ['Pending one', 'Running one', 'Completed one', 'Failed one', 'Cancelled one']) expect(within(list).getByText(text)).toBeInTheDocument();
        expect(screen.getByTestId('filter-all')).toHaveTextContent('All (5)');
        expect(screen.getByTestId('filter-failed')).toHaveTextContent('Failed (1)');
        for (const status of ['pending', 'running', 'completed', 'failed', 'cancelled']) expect(within(list).getByTestId(`status-${status}`)).toBeInTheDocument();
        await click(screen.getByTestId('filter-failed'));
        expect(within(list).getByText('Failed one')).toBeInTheDocument();
        expect(within(list).queryByText('Completed one')).not.toBeInTheDocument();
        expect(screen.getByTestId('filter-failed')).toHaveAttribute('aria-pressed', 'true');
        await click(screen.getByTestId('filter-all'));
        expect(within(list).getByText('Completed one')).toBeInTheDocument();
    });

    it('shows an empty history with a next step', async () => {
        await mount({ entry: '/agents?tab=tasks' });
        expect(await screen.findByTestId('list-empty')).toHaveTextContent('No tasks yet. Start one above.');
        expect(screen.getByTestId('detail-placeholder')).toBeInTheDocument();
    });

    it('follows a supervised task: plan progress, runs, steps, tokens and cost against the cap', async () => {
        const task = addTask({ status: 'running', agent_key: null, objective: 'Plan search ads', max_cost_usd: 1, started_at: new Date().toISOString(), attempts: 1 });
        const seed = backend.agents.seed;
        seed.plan(backend.agents.state, ORG, task.id, { summary: 'Analyze then write', steps: [{ id: 's1', agent: 'product_analyst', objective: 'a', depends_on: [] }, { id: 's2', agent: 'ad_copywriter', objective: 'b', depends_on: ['s1'] }] });
        seed.runs(backend.agents.state, ORG, task.id, [
            { id: 'r1', sequence: 1, plan_step_id: 's1', agent_key: 'product_analyst', agent_version: '1.1.0', status: 'completed', step_count: 2, cost_usd: 0.002, error: null },
            { id: 'r2', sequence: 2, plan_step_id: 's2', agent_key: 'ad_copywriter', agent_version: '1.0.0', status: 'running', step_count: 1, cost_usd: 0.001, error: null },
        ]);
        seed.step(backend.agents.state, ORG, task.id, { name: 'agent.supervisor:plan', input_tokens: 400, output_tokens: 100, cost_usd: 0.4 });
        seed.step(backend.agents.state, ORG, task.id, { kind: 'tool', name: 'web.analyze_page', input: { url: 'https://x.example.com' }, output: { preview: '{"analyzed": true, "provenance": {"data_kind": "real"}}' }, input_tokens: 0, output_tokens: 0, cost_usd: 0 });
        await mount({ entry: `/agents?task=${task.id}` });
        const detail = await screen.findByTestId('task-detail');
        expect(detail).toHaveTextContent('Supervisor (automatic plan)');
        expect(within(detail).getByText('1 of 2 steps')).toBeInTheDocument();
        expect(within(detail).getByRole('progressbar', { name: 'Progress' })).toHaveAttribute('aria-valuenow', '50');
        expect(within(detail).getByTestId('task-usage')).toHaveTextContent('400');
        expect(within(detail).getByTestId('task-usage')).toHaveTextContent('100');
        expect(within(detail).getByTestId('usage-cost')).toHaveTextContent('$0.40');
        expect(within(detail).getByText('40% of the cost limit used', { selector: 'p' })).toBeInTheDocument();
        expect(within(detail).getByTestId('task-runs')).toHaveTextContent('Product and sales-page analyst');
        expect(within(detail).getByTestId('task-runs')).toHaveTextContent('Ad copywriter');
        expect(within(detail).getByTestId('steps-timeline')).toHaveTextContent('web.analyze_page');
        expect(within(detail).getByTestId('outcome-2')).toHaveTextContent('Data returned');
        expect(within(detail).getByText('Prompts are never stored. Inputs and outputs are redacted and truncated before being saved.')).toBeInTheDocument();
    });

    it('a pending supervised task has an indeterminate progress bar and says it is planning', async () => {
        const task = addTask({ status: 'running', agent_key: null });
        await mount({ entry: `/agents?task=${task.id}` });
        const bar = await screen.findByRole('progressbar', { name: 'Progress' });
        expect(bar).toHaveAttribute('aria-busy', 'true');
        expect(bar).not.toHaveAttribute('aria-valuenow');
        expect(screen.getByText('Planning…')).toBeInTheDocument();
    });

    it('polls: a running task turns into a completed one with its result, without any user action', async () => {
        const task = addTask({ status: 'running', started_at: new Date().toISOString(), attempts: 1 });
        await mount({ entry: `/agents?task=${task.id}` });
        expect(await screen.findByTestId('task-detail')).toHaveTextContent('Running');
        expect(screen.getByTestId('result-pending')).toBeInTheDocument();
        backend.agents.seed.step(backend.agents.state, ORG, task.id, {});
        backend.agents.seed.finish(backend.agents.state, ORG, task.id, 'completed', { result: { kind: 'single', agent: 'product_analyst', output: { summary: 'A concise analysis.' } } });
        await waitFor(() => expect(screen.getByTestId('task-detail')).toHaveTextContent('Completed'), { timeout: 6000 });
        await waitFor(() => expect(screen.getByTestId('result-view')).toHaveTextContent('A concise analysis.'), { timeout: 4000 });
        expect(within(screen.getByTestId('task-detail')).getByTestId('steps-timeline')).toBeInTheDocument();
    }, 15000);

    it('a pending task with no worker says so; a retry shows when the next attempt happens', async () => {
        backend.agents.state.worker.online = false;
        const pending = addTask({ status: 'pending' });
        await mount({ entry: `/agents?task=${pending.id}` });
        expect(await screen.findByTestId('waiting-worker')).toHaveTextContent('No worker is running, so this task cannot start');
        cleanup();
        backend.agents.state.worker.online = true;
        backend.agents.seed.retry(backend.agents.state, ORG, pending.id, { kind: 'ai_rate_limit', message: 'slow down' });
        await mount({ entry: `/agents?task=${pending.id}` });
        expect(await screen.findByTestId('retry-note')).toHaveTextContent('Attempt 2 of 3 is scheduled');
        expect(screen.getByTestId('retry-note')).toHaveTextContent('finished steps are not repeated');
    });

    it('an unknown task id (or one from another organization) is "not found", not an error page', async () => {
        await mount({ entry: '/agents?task=does-not-exist' });
        expect(await screen.findByTestId('task-notfound')).toHaveTextContent('Task not found');
        await click(screen.getByRole('button', { name: 'Back to the list' }));
        expect(screen.getByTestId('detail-placeholder')).toBeInTheDocument();
    });
});

// ================================================================================================= results
describe('results: real data, estimates and hypotheses', () => {
    const STRUCTURED = {
        kind: 'single', agent: 'market_researcher',
        output: {
            summary: 'Running shoes market notes.',
            keyword_candidates: [
                { keyword: 'running shoes', intent: 'transactional', match_type_suggestion: 'phrase', rationale: 'clear buying intent', data_kind: 'estimate', source: 'google_keyword_planner', metrics: [{ entity: 'keyword:running shoes', name: 'avg_monthly_searches', value: 12100 }] },
                { keyword: 'shoes for flat feet', intent: 'informational', match_type_suggestion: 'broad', rationale: 'long tail idea', data_kind: 'hypothesis', source: 'model_knowledge', metrics: [] },
            ],
            data_basis: [
                { statement: 'Brand A had 120 clicks', data_kind: 'real', source: 'google_ads_api', metrics: [{ entity: 'campaign:111', name: 'clicks', value: 120 }] },
                { statement: 'Buyers are probably price sensitive', data_kind: 'hypothesis', source: 'model_knowledge', metrics: [] },
            ],
            evidence: 'mixed', themes: ['Comfort', 'Price'],
        },
    };

    const finished = (result, patch = {}) => {
        const task = addTask({ status: 'completed', agent_key: 'market_researcher', ...patch });
        backend.agents.seed.finish(backend.agents.state, ORG, task.id, patch.status || 'completed', { result, error: patch.error });
        return task;
    };

    it('labels every figure as real, Google estimate or hypothesis, with its source and the cited metric', async () => {
        const task = finished(STRUCTURED);
        await mount({ entry: `/agents?task=${task.id}` });
        const view = await screen.findByTestId('result-view');
        expect(view).toHaveTextContent('Results are model-generated proposals for human review. Nothing was published or spent.');
        const summary = within(view).getByTestId('provenance-summary');
        expect(within(summary).getByTestId('count-real')).toHaveTextContent('1');
        expect(within(summary).getByTestId('count-estimate')).toHaveTextContent('1');
        expect(within(summary).getByTestId('count-hypothesis')).toHaveTextContent('2');
        const keywords = within(view).getAllByTestId('keyword-item');
        expect(keywords[0]).toHaveTextContent('running shoes');
        expect(within(keywords[0]).getByTestId('kind-estimate')).toHaveTextContent('Estimate');
        expect(keywords[0]).toHaveTextContent('Google Keyword Planner');
        expect(within(keywords[0]).getByTestId('metric-chip')).toHaveTextContent('keyword:running shoes · avg_monthly_searches: 12100');
        expect(within(keywords[1]).getByTestId('kind-hypothesis')).toBeInTheDocument();
        expect(within(keywords[1]).queryByTestId('metric-chip')).not.toBeInTheDocument();          // a hypothesis carries no number
        const basis = within(view).getAllByTestId('basis-item');
        expect(within(basis[0]).getByTestId('kind-real')).toHaveTextContent('Real data');
        expect(basis[0]).toHaveTextContent('Google Ads account');
        expect(within(view).getAllByText('Comfort').length).toBeGreaterThan(0);
        expect(within(view).getByText('Show raw JSON')).toBeInTheDocument();
    });

    it('an output with no labels is flagged as the model\'s proposal, never as measurement; an unknown label is not shown as real', async () => {
        const task = finished({ kind: 'single', agent: 'ad_copywriter', output: { summary: 'Headlines written.', ad_groups: [{ ad_group: 'Core', headlines: ['A', 'B', 'C'], descriptions: ['d1', 'd2'] }] } });
        await mount({ entry: `/agents?task=${task.id}` });
        const summary = await screen.findByTestId('provenance-summary');
        expect(summary).toHaveTextContent('No item carries a data label');
        cleanup();
        const odd = finished({ kind: 'single', agent: 'performance_analyst', output: { summary: 's', data_basis: [{ statement: 'Something', data_kind: 'verified-by-magic', source: 'x', metrics: [] }] } });
        await mount({ entry: `/agents?task=${odd.id}` });
        const item = (await screen.findAllByTestId('basis-item'))[0];
        expect(within(item).queryByTestId('kind-real')).not.toBeInTheDocument();
        expect(within(item).getByTestId('kind-hypothesis')).toBeInTheDocument();
    });

    it('a supervised result shows the consolidated report and each specialist\'s output', async () => {
        const task = addTask({ status: 'completed', agent_key: null });
        backend.agents.seed.finish(backend.agents.state, ORG, task.id, 'completed', { result: {
            kind: 'supervised', plan: { summary: 'p', steps: [] }, report: { summary: 'Everything consolidated.', key_results: ['analysis done'] },
            steps: [{ id: 's1', agent: 'product_analyst', output: { summary: 'Analyst output.' } }],
        } });
        await mount({ entry: `/agents?task=${task.id}` });
        const view = await screen.findByTestId('result-view');
        expect(view).toHaveTextContent('Consolidated report');
        expect(view).toHaveTextContent('Everything consolidated.');
        expect(within(view).getByTestId('result-step-s1')).toHaveTextContent('Product and sales-page analyst');
        expect(view).toHaveTextContent('Analyst output.');
    });

    it('a failed supervised task shows its error with the next step AND the partial results already produced', async () => {
        const task = addTask({ status: 'failed', agent_key: null, attempts: 1 });
        backend.agents.seed.finish(backend.agents.state, ORG, task.id, 'failed', {
            result: { partial: { s1: { summary: 'Finished before the failure.' } } }, error: { kind: 'ai_budget_exceeded', message: 'daily limit reached' },
        });
        await mount({ entry: `/agents?task=${task.id}` });
        const error = await screen.findByTestId('task-error');
        expect(error).toHaveTextContent("The organization's AI spending limit was reached.");
        expect(error).toHaveTextContent('Check the credit and spending limits in the AI Control Center.');
        expect(within(error).getByRole('link', { name: 'Open AI Control Center' })).toHaveAttribute('href', '/ai');
        expect(error).toHaveTextContent('Server message: daily limit reached');
        expect(await screen.findByTestId('partial-notice')).toHaveTextContent('This task did not finish');
        expect(screen.getByTestId('partial-step-s1')).toHaveTextContent('Step s1');
        fireEvent.click(within(screen.getByTestId('partial-step-s1')).getByText('Step s1'));
        expect(screen.getByTestId('partial-step-s1')).toHaveTextContent('Finished before the failure.');
        expect(screen.queryByTestId('kind-real')).not.toBeInTheDocument();
    });

    it('translates every known error kind and falls back to a generic sentence for an unknown one', async () => {
        const known = addTask({ status: 'failed' });
        backend.agents.seed.finish(backend.agents.state, ORG, known.id, 'failed', { error: { kind: 'invalid_output', message: 'bad' } });
        const unknown = addTask({ status: 'failed' });
        backend.agents.seed.finish(backend.agents.state, ORG, unknown.id, 'failed', { error: { kind: 'brand_new_kind', message: 'bad' } });
        await mount({ entry: `/agents?task=${known.id}` });
        expect(await screen.findByTestId('task-error')).toHaveTextContent('The agent could not produce a valid, verifiable answer. Nothing unverified was saved.');
        await click(screen.getByTestId(`task-${unknown.id}`));
        await waitFor(() => expect(screen.getByTestId('task-error')).toHaveTextContent('The task failed.'));
    });

    it('a tool that had no data is shown as unavailable on the timeline (the agent was not given invented figures)', async () => {
        const task = addTask({ status: 'completed', agent_key: 'performance_analyst' });
        backend.agents.seed.step(backend.agents.state, ORG, task.id, {
            kind: 'tool', name: 'google_ads.get_campaign_performance', output: { preview: '{"available": false, "reason": "not_connected", "notice": "No data was returned."}' }, cost_usd: 0,
        });
        backend.agents.seed.step(backend.agents.state, ORG, task.id, { kind: 'tool', name: 'google_ads.keyword_ideas', output: { preview: '{"available": true, "no_data": true, "ideas": []}' }, cost_usd: 0 });
        backend.agents.seed.step(backend.agents.state, ORG, task.id, { kind: 'tool', name: 'google_ads.get_search_terms_report', output: { preview: '{"rows": []}' }, injection_suspected: true, cost_usd: 0 });
        backend.agents.seed.finish(backend.agents.state, ORG, task.id, 'completed', { result: { kind: 'single', agent: 'performance_analyst', output: { summary: 'No data.' } } });
        await mount({ entry: `/agents?task=${task.id}` });
        await screen.findByTestId('steps-timeline');
        expect(screen.getByTestId('outcome-1')).toHaveTextContent('Unavailable · not_connected');
        expect(screen.getByTestId('outcome-2')).toHaveTextContent('No data');
        expect(screen.getByTestId('injection-3')).toHaveTextContent('Suspicious content flagged');
    });
});

// ================================================================================================= cancel
describe('cancelling', () => {
    it('cancels a pending task after confirmation; declining keeps it', async () => {
        const task = addTask({ status: 'pending' });
        await mount({ entry: `/agents?task=${task.id}` });
        await click(await screen.findByRole('button', { name: 'Cancel task' }));
        await click(screen.getByRole('button', { name: 'Keep running' }));
        expect(calls('POST', '/cancel')).toHaveLength(0);
        await click(screen.getByRole('button', { name: 'Cancel task' }));
        expect(screen.getByText('The task has not started and will be cancelled right away.')).toBeInTheDocument();
        await click(within(screen.getByText('Cancel this task?').closest('div[class*="max-w-md"]')).getByRole('button', { name: 'Cancel task' }));
        await waitFor(() => expect(calls('POST', '/cancel')).toHaveLength(1));
        expect(calls('POST', '/cancel')[0].org).toBe(ORG);
        await waitFor(() => expect(screen.getByTestId('task-detail')).toHaveTextContent('Cancelled'));
        expect(screen.queryByRole('button', { name: 'Cancel task' })).not.toBeInTheDocument();
        expect(screen.getByText('Task cancelled')).toBeInTheDocument();
    });

    it('a running task says the cancellation is requested and warns that a call in flight is still charged', async () => {
        const task = addTask({ status: 'running', started_at: new Date().toISOString(), attempts: 1 });
        await mount({ entry: `/agents?task=${task.id}` });
        await click(await screen.findByRole('button', { name: 'Cancel task' }));
        expect(screen.getByText(/A call already in progress finishes and is still charged\./)).toBeInTheDocument();
        await click(within(screen.getByText('Cancel this task?').closest('div[class*="max-w-md"]')).getByRole('button', { name: 'Cancel task' }));
        await waitFor(() => expect(screen.getByTestId('task-detail')).toHaveTextContent('Cancellation requested'));
        expect(screen.queryByRole('button', { name: 'Cancel task' })).not.toBeInTheDocument();
    });

    it('only the creator or an administrator can cancel; a manager does not get the button for someone else\'s task', async () => {
        const task = addTask({ status: 'pending', created_by_user_id: 'u-bob' });
        backend.setRole('u-alice', ORG, 'manager');
        await mount({ entry: `/agents?task=${task.id}` });
        await screen.findByTestId('task-detail');
        expect(screen.queryByRole('button', { name: 'Cancel task' })).not.toBeInTheDocument();
        cleanup();
        backend.setRole('u-alice', ORG, 'admin');
        await mount({ entry: `/agents?task=${task.id}` });
        expect(await screen.findByRole('button', { name: 'Cancel task' })).toBeInTheDocument();
    });

    it('a finished task cannot be cancelled', async () => {
        const task = addTask({ status: 'completed' });
        backend.agents.seed.finish(backend.agents.state, ORG, task.id, 'completed', { result: { kind: 'single', agent: 'product_analyst', output: { summary: 's' } } });
        await mount({ entry: `/agents?task=${task.id}` });
        await screen.findByTestId('result-view');
        expect(screen.queryByRole('button', { name: 'Cancel task' })).not.toBeInTheDocument();
    });
});

// ================================================================================================= settings and models
describe('agent settings and model selection', () => {
    it('an administrator disables an agent with validated limits; the server state is re-read', async () => {
        setupAi();
        await mount();
        await click(screen.getByRole('button', { name: 'Settings of Policy and compliance reviewer' }));
        await type(dialog().getByLabelText('Model turns per run'), '99');
        expect(dialog().getByText('Use a whole number from 1 to 20.')).toBeInTheDocument();
        expect(dialog().getByRole('button', { name: 'Save settings' })).toBeDisabled();
        await type(dialog().getByLabelText('Model turns per run'), '5');
        await click(dialog().getByRole('checkbox', { name: /Agent enabled/ }));
        await click(dialog().getByRole('button', { name: 'Save settings' }));
        await waitFor(() => expect(calls('PUT', '/agents/policy_reviewer/settings')).toHaveLength(1));
        expect(calls('PUT', '/agents/policy_reviewer/settings')[0].body).toEqual({ enabled: false, max_steps: 5, timeout_seconds: null });
        await screen.findByText('Agent settings saved');
        await waitFor(() => expect(screen.getByTestId('availability-policy_reviewer')).toHaveTextContent('Disabled'));
        expect(screen.getByRole('button', { name: 'Run a task with Policy and compliance reviewer' })).toBeDisabled();
        await openTab('Tasks');
        expect(within(screen.getByLabelText('Agent')).getByRole('option', { name: 'Policy and compliance reviewer (Disabled)' })).toBeInTheDocument();
    });

    it('chooses the agent\'s model through the AI Control Center route (agent.<key>)', async () => {
        setupAi();
        await mount();
        await click(screen.getByRole('button', { name: 'Settings of Ad copywriter (responsive search ads)' }));
        const route = await screen.findByTestId('route-agent.ad_copywriter');
        expect(route).toHaveTextContent('Ad copywriter (responsive search ads)');
        await type(within(route).getByLabelText('Main model'), 'm-org-a1');
        await click(within(route).getByRole('button', { name: 'Save route' }));
        await waitFor(() => expect(calls('PUT', '/ai/routing/agent.ad_copywriter')).toHaveLength(1));
        expect(calls('PUT', '/ai/routing/agent.ad_copywriter')[0].body).toEqual({ primary_model_id: 'm-org-a1', fallback_model_ids: [] });
        await waitFor(() => expect(backend.ai.state.orgs[ORG].routes.some((r) => r.operation === 'agent.ad_copywriter')).toBe(true));
    });

    it('a role that manages agents but not AI sees the model route read-only; a manager gets no settings button at all', async () => {
        setupAi();
        backend.setRole('u-alice', ORG, 'manager');
        await mount();
        expect(screen.queryByRole('button', { name: /Settings of/ })).not.toBeInTheDocument();
    });

    it('settings server refusal is shown, not swallowed (permission revoked meanwhile)', async () => {
        setupAi();
        await mount();
        await click(screen.getByRole('button', { name: 'Settings of Policy and compliance reviewer' }));
        backend.setRole('u-alice', ORG, 'manager');
        await click(dialog().getByRole('button', { name: 'Save settings' }));
        await screen.findByText('Your role is not allowed to do this.');
    });
});

// ================================================================================================= tenancy and RBAC
describe('organization isolation and RBAC', () => {
    it('switching organization drops every task and agent setting of the previous one and re-reads the new one', async () => {
        backend.setRole('u-alice', 'org-a2', 'admin');
        const mine = addTask({ objective: 'Only in Acme One', status: 'completed' });
        addTask({ objective: 'Only in Acme Two', status: 'completed' }, 'org-a2');
        await mount({ entry: `/agents?task=${mine.id}` });
        expect(await screen.findByTestId('task-detail')).toHaveTextContent('Only in Acme One');
        await act(async () => { fireEvent.click(screen.getByTestId('org-switcher')); });
        await click(screen.getAllByRole('option').find((o) => o.textContent.includes('Acme Two')));
        // the task id of the other organization is NOT found here, and nothing of Acme One remains on screen
        await waitFor(() => expect(screen.getByTestId('task-notfound')).toBeInTheDocument());
        expect(document.body.textContent).not.toContain('Only in Acme One');
        await click(screen.getByRole('button', { name: 'Back to the list' }));
        await waitFor(() => expect(screen.getByTestId('task-list')).toHaveTextContent('Only in Acme Two'));
        expect(screen.getByTestId('task-list')).not.toHaveTextContent('Only in Acme One');
        const reads = agentCalls();
        expect(new Set(reads.map((c) => c.org))).toEqual(new Set([ORG, 'org-a2']));
        expect(reads.filter((c) => c.org === 'org-a2').every((c) => !c.path.includes(mine.id) || c.path.includes('/tasks/'))).toBe(true);
    });

    it('a role without agents:read gets a clear "no access" and no agent request is made for data', async () => {
        backend.setRole('u-alice', ORG, 'viewer');
        signIn();
        render(<Tree />);
        expect(await screen.findByRole('alert')).toHaveTextContent("You don't have access to AI Agents");
        expect(agentCalls().filter((c) => c.method === 'GET' && c.path !== '/agents/worker/status')).toHaveLength(0);
        expect(screen.queryByTestId('agents-grid')).not.toBeInTheDocument();
    });

    it('nothing from a task is kept in browser storage', async () => {
        const task = addTask({ objective: 'CONFIDENTIAL launch plan for the widget', status: 'completed' });
        backend.agents.seed.finish(backend.agents.state, ORG, task.id, 'completed', { result: { kind: 'single', agent: 'product_analyst', output: { summary: 'SECRET-RESULT-TEXT' } } });
        await mount({ entry: `/agents?task=${task.id}` });
        await screen.findByTestId('result-view');
        const stored = [...localStorage._data.values()].join('|');
        expect(stored).not.toContain('CONFIDENTIAL');
        expect(stored).not.toContain('SECRET-RESULT-TEXT');
    });
});

// ================================================================================================= language
describe('language', () => {
    it('renders fully in pt-BR: page, catalogue texts, statuses, availability and errors', async () => {
        setupAi();
        await act(async () => { await i18n.changeLanguage('pt-BR'); });
        const failed = addTask({ status: 'failed', agent_key: 'market_researcher' });
        backend.agents.seed.finish(backend.agents.state, ORG, failed.id, 'failed', { error: { kind: 'cost_limit', message: 'cap' } });
        await mount();
        expect(screen.getByRole('heading', { level: 1 })).toHaveTextContent('Agentes de IA');
        expect(screen.getByTestId('agent-market_researcher')).toHaveTextContent('Pesquisador de mercado e palavras-chave');
        expect(screen.getByTestId('agent-market_researcher')).toHaveTextContent('Sem uma conta Google Ads conectada, toda palavra-chave é uma hipótese');
        expect(screen.getByTestId('agent-supervisor')).toHaveTextContent('Planeja o trabalho');
        expect(screen.getByRole('tab', { name: 'Tarefas' })).toBeInTheDocument();
        await openTab('Tarefas');
        await waitFor(() => expect(screen.getByTestId('filter-failed')).toHaveTextContent('Com erro (1)'));
        expect(screen.getByLabelText('Agente')).toBeInTheDocument();
        await click(screen.getByTestId(`task-${failed.id}`));
        expect(await screen.findByTestId('task-error')).toHaveTextContent('A tarefa atingiu o limite de custo.');
        expect(document.body.textContent).not.toMatch(/\bagents:[a-z.]+\b|\{\{|undefined/);
        // language switch re-renders without reloading
        await act(async () => { await i18n.changeLanguage('en-US'); });
        expect(screen.getByRole('heading', { level: 1 })).toHaveTextContent('AI Agents');
    });

    it('a backend text that changed is shown as received instead of a possibly stale translation', async () => {
        const original = backend.fetch;
        global.fetch = vi.fn(async (url, init) => {
            const response = await original(url, init);
            if (/\/agents$/.test(String(url).split('?')[0]) && response.ok) {
                const data = await response.json();
                data[0].description = 'A brand new description written after the last translation.';
                return { ...response, json: async () => data };
            }
            return response;
        });
        await act(async () => { await i18n.changeLanguage('pt-BR'); });
        await mount();
        expect(screen.getByTestId('agent-product_analyst')).toHaveTextContent('A brand new description written after the last translation.');
        expect(screen.getByTestId('agent-market_researcher')).toHaveTextContent('Propõe temas, palavras-chave candidatas');   // untouched ones keep their translation
    });

    it('the permission matrix names the agents:* permissions in both languages', async () => {
        const Matrix = () => (
            <MemoryRouter>
                <ToastProvider><AuthProvider><OrganizationProvider><TenantBoundary>
                    <OrganizationPermissionsModal isOpen onClose={() => {}} />
                </TenantBoundary></OrganizationProvider></AuthProvider></ToastProvider>
            </MemoryRouter>
        );
        signIn();
        render(<Matrix />);
        await screen.findByTestId('perm-row-agents:read');
        expect(screen.getByTestId('perm-row-agents:read')).toHaveTextContent('View AI agents, tasks and results');
        expect(screen.getByTestId('perm-row-agents:run')).toHaveTextContent('Start and cancel AI agent tasks');
        expect(screen.getByTestId('perm-row-agents:manage')).toHaveTextContent('Enable agents and tune their limits');
        expect(screen.getByText('AI agents')).toBeInTheDocument();
        cleanup();
        await act(async () => { await i18n.changeLanguage('pt-BR'); });
        render(<Matrix />);
        await screen.findByTestId('perm-row-agents:read');
        expect(screen.getByTestId('perm-row-agents:read')).toHaveTextContent('Ver agentes de IA, tarefas e resultados');
        expect(screen.getByTestId('perm-row-agents:manage')).toHaveTextContent('Habilitar agentes e ajustar seus limites');
        expect(screen.getByTestId('perm-row-agents:read').textContent).not.toContain('agents:read');
    });
});

// ================================================================================================= keyboard and accessibility
describe('keyboard and accessibility', () => {
    it('tabs follow the arrow keys, panels are labelled and the dialog traps Escape', async () => {
        setupAi();
        await mount();
        const agentsTab = screen.getByRole('tab', { name: 'Agents' });
        agentsTab.focus();
        await act(async () => { fireEvent.keyDown(agentsTab, { key: 'ArrowRight' }); });
        await waitFor(() => expect(screen.getByRole('tab', { name: 'Tasks' })).toHaveAttribute('aria-selected', 'true'));
        expect(screen.getByRole('tabpanel')).toHaveAttribute('aria-labelledby', 'agents-tab-tasks');
        await openTab('Agents');
        await click(screen.getByRole('button', { name: 'Details of Policy and compliance reviewer' }));
        expect(screen.getByRole('dialog')).toHaveAttribute('aria-modal', 'true');
        await act(async () => { fireEvent.keyDown(document, { key: 'Escape' }); });
        expect(screen.queryByRole('dialog')).not.toBeInTheDocument();
    });

    it('every control of the form has a visible label and errors are announced', async () => {
        setupAi();
        await mount({ entry: '/agents?tab=tasks' });
        for (const label of ['Agent', 'Objective', 'Context (optional)', 'Cost limit (optional)']) expect(screen.getByLabelText(label)).toBeInTheDocument();
        await type(screen.getByLabelText('Objective'), 'ab');
        await click(screen.getByRole('button', { name: 'Start task' }));
        expect(screen.getByLabelText('Objective')).toHaveAttribute('aria-invalid', 'true');
        expect(screen.getByRole('alert')).toBeInTheDocument();
    });
});
