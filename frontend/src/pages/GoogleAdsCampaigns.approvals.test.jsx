/**
 * Google Ads page + spend-approval flow, with the REAL providers against the
 * tenant-aware fake backend. The key guarantee under test: nothing that can spend
 * is ever sent to the enable endpoint without an approved, unexpired, unused
 * approval -- and the buttons follow the permissions the server reported for the role.
 */
import React from 'react';
import { act, cleanup, fireEvent, render, screen, waitFor, within } from '@testing-library/react';
import { MemoryRouter } from 'react-router-dom';

import i18n from '../i18n';
import { createFakeBackend, memoryStorage, ROLES } from '../__tests__/fakeBackend';
import { AuthProvider } from '../context/AuthContext';
import { OrganizationProvider, TenantBoundary } from '../context/OrganizationContext';
import { ToastProvider } from '../context/ToastContext';
import OrganizationSwitcher from '../components/OrganizationSwitcher';
import GoogleAdsCampaigns from './GoogleAdsCampaigns';
import { clearTenant } from '../lib/tenant';

let backend;

const Tree = () => (
    <MemoryRouter>
        <ToastProvider>
            <AuthProvider>
                <OrganizationProvider>
                    <TenantBoundary>
                        <OrganizationSwitcher />
                        <GoogleAdsCampaigns />
                    </TenantBoundary>
                </OrganizationProvider>
            </AuthProvider>
        </ToastProvider>
    </MemoryRouter>
);

/** A user with `role` in org-a1; returns the key to sign in with. */
const addMember = (key, role) => {
    backend.state.users[key] = { id: `u-${key}`, email: `${key}@example.com`, name: key, password: 'pw' };
    backend.state.members.push({ user: `u-${key}`, org: 'org-a1', role, status: 'active' });
    return key;
};

const signIn = (key) => {
    const tokens = backend.loginTokens(key);
    localStorage.setItem('accessToken', tokens.access_token);
    localStorage.setItem('refreshToken', tokens.refresh_token);
};

const open = async (key) => {
    signIn(key);
    render(<Tree />);
    await waitFor(() => expect(screen.getByTestId('org-switcher')).toBeInTheDocument());
};

const enableButton = () => screen.findByRole('button', { name: /^Enable$/ });
const dialog = () => screen.findByRole('dialog');
const enableCalls = () => backend.calls().filter((c) => /\/google-ads\/campaigns\/.+\/enable$/.test(c.path));
const approvalPosts = () => backend.calls().filter((c) => c.path === '/optimization/approvals' && c.method === 'POST');
const closeButton = (d) => within(d).getAllByRole('button', { name: /^(Close|Fechar)$/ }).at(-1); // footer button (the X has the same name)
const click = async (el) => { await act(async () => { fireEvent.click(el); }); };
const type = async (el, value) => { await act(async () => { fireEvent.change(el, { target: { value } }); }); };

beforeEach(async () => {
    global.localStorage = memoryStorage();
    clearTenant();
    backend = createFakeBackend();
    global.fetch = backend.fetch;
    await act(async () => { await i18n.changeLanguage('en-US'); });
});
afterEach(() => cleanup());

describe('what each role sees on the Google Ads page', () => {
    const expectations = {
        owner: { connect: true, create: true, pauseEnabled: true },
        admin: { connect: true, create: true, pauseEnabled: true },
        manager: { connect: false, create: true, pauseEnabled: false },
        analyst: { connect: false, create: false, pauseEnabled: false },
    };

    it.each(Object.entries(expectations))('%s', async (role, want) => {
        backend.state.campaigns = [{ id: '222', name: 'Live campaign', status: 'ENABLED', impressions: 1, clicks: 1, cost: 1, conversions: 0 }];
        const key = role === 'owner' ? 'alice' : addMember(role, role);
        await open(key);
        await screen.findByText('Live campaign');
        expect(screen.queryByRole('button', { name: /^Disconnect$/ }) !== null).toBe(want.connect);
        expect(screen.queryByRole('button', { name: /Create campaign/ }) !== null).toBe(want.create);
        const pause = screen.getByRole('button', { name: /^Pause$/ });
        expect(!pause.disabled).toBe(want.pauseEnabled);
        if (!want.connect) expect(screen.getByText(/Only organization admins and owners can connect/)).toBeInTheDocument();
    });

    it('viewer: the server refuses the connection list, so the page says so instead of offering to connect', async () => {
        await open(addMember('viewer', 'viewer'));
        expect(await screen.findByTestId('connection-denied')).toBeInTheDocument();
        expect(screen.queryByRole('button', { name: /^Connect$/ })).not.toBeInTheDocument();
        expect(screen.queryByRole('button', { name: /Create campaign/ })).not.toBeInTheDocument();
    });

    it('the same user in two organizations gets two different pages (owner in A1, viewer in A2)', async () => {
        await open('alice');
        await screen.findByRole('button', { name: /Create campaign/ });
        await act(async () => { fireEvent.click(screen.getByTestId('org-switcher')); });
        await click(screen.getAllByRole('option').find((o) => o.textContent.includes('Acme Two')));
        expect(await screen.findByTestId('connection-denied')).toBeInTheDocument();
        expect(screen.queryByRole('button', { name: /Create campaign/ })).not.toBeInTheDocument();
    });
});

describe('spend approval to enable a campaign', () => {
    it('clicking Enable only opens the dialog: nothing is sent to the enable endpoint', async () => {
        await open('alice');
        await click(await enableButton());
        const d = await dialog();
        expect(within(d).getByText(/starts real spend/i)).toBeInTheDocument();
        expect(enableCalls()).toEqual([]);
        expect(approvalPosts()).toEqual([]);
    });

    it('request -> approve with a budget cap -> enable, in order, and the enable call carries the approval id', async () => {
        await open('alice');
        await click(await enableButton());
        const d = await dialog();

        // 1. request
        await type(within(d).getByLabelText(/Daily budget to request/), '5');
        await click(within(d).getByRole('button', { name: 'Request approval' }));
        await waitFor(() => expect(within(d).getByTestId('approval-status')).toHaveTextContent('Pending approval'));
        expect(approvalPosts()).toHaveLength(1);
        expect(approvalPosts()[0].body).toMatchObject({ provider: 'google_ads', action: 'campaign.enable', budget_micros: 5_000_000, payload: { campaign_id: '111' } });
        expect(approvalPosts()[0].body.idempotency_key).toMatch(/^enable:111:/);
        expect(enableCalls()).toEqual([]);

        // 2. approve with a cap
        await type(within(d).getByLabelText(/Budget cap you approve/), '6');
        await click(within(d).getByRole('button', { name: 'Approve' }));
        await waitFor(() => expect(within(d).getByTestId('approval-status')).toHaveTextContent('Approved'));
        expect(backend.state.approvals[0]).toMatchObject({ status: 'approved', max_budget_micros: 6_000_000, approved_by_user_id: 'u-alice' });
        expect(enableCalls()).toEqual([]);

        // 3. enable with the approval
        await click(within(d).getByRole('button', { name: 'Enable campaign' }));
        await waitFor(() => expect(enableCalls()).toHaveLength(1));
        expect(enableCalls()[0].body).toEqual({ confirm: true, approval_id: backend.state.approvals[0].id });
        expect(backend.state.enabledCampaigns).toEqual(['111']);
        await waitFor(() => expect(screen.queryByRole('dialog')).not.toBeInTheDocument());
    });

    it('a manager can REQUEST but sees no approve/enable controls and the server is never asked to enable', async () => {
        await open(addMember('mgr', 'manager'));
        await click(await enableButton());
        const d = await dialog();
        await type(within(d).getByLabelText(/Daily budget to request/), '3');
        await click(within(d).getByRole('button', { name: 'Request approval' }));
        await waitFor(() => expect(within(d).getByTestId('approval-status')).toHaveTextContent('Pending approval'));
        expect(within(d).getByText(/Waiting for an owner/)).toBeInTheDocument();
        expect(within(d).queryByRole('button', { name: 'Approve' })).not.toBeInTheDocument();
        expect(within(d).queryByRole('button', { name: 'Reject' })).not.toBeInTheDocument();
        expect(within(d).queryByRole('button', { name: 'Enable campaign' })).not.toBeInTheDocument();
        expect(enableCalls()).toEqual([]);
    });

    it('an analyst cannot request: the dialog explains it and offers no request control', async () => {
        await open(addMember('ana', 'analyst'));
        await click(await enableButton());
        const d = await dialog();
        expect(await within(d).findByText(/Your role \(Analyst\) cannot request spend approvals/)).toBeInTheDocument();
        expect(within(d).queryByRole('button', { name: 'Request approval' })).not.toBeInTheDocument();
        expect(approvalPosts()).toEqual([]);
    });

    it('rejection ends the request; a new request uses a NEW idempotency key', async () => {
        await open('alice');
        await click(await enableButton());
        let d = await dialog();
        await type(within(d).getByLabelText(/Daily budget to request/), '5');
        await click(within(d).getByRole('button', { name: 'Request approval' }));
        await waitFor(() => expect(within(d).getByTestId('approval-status')).toHaveTextContent('Pending approval'));
        await click(within(d).getByRole('button', { name: 'Reject' }));
        await waitFor(() => expect(within(d).getByTestId('approval-status')).toHaveTextContent('Rejected'));
        expect(within(d).getByText(/was rejected/)).toBeInTheDocument();
        expect(within(d).queryByRole('button', { name: 'Enable campaign' })).not.toBeInTheDocument();

        d = await dialog();
        await type(within(d).getByLabelText(/Daily budget to request/), '7');
        await click(within(d).getByRole('button', { name: 'Request approval' }));
        await waitFor(() => expect(backend.state.approvals).toHaveLength(2));
        const keys = backend.state.approvals.map((a) => a.idempotency_key);
        expect(new Set(keys).size).toBe(2);
        expect(enableCalls()).toEqual([]);
    });

    it('an expired approval is shown as expired, cannot be approved or used, and a new one can be requested', async () => {
        await open('alice');
        await click(await enableButton());
        let d = await dialog();
        await type(within(d).getByLabelText(/Daily budget to request/), '5');
        await click(within(d).getByRole('button', { name: 'Request approval' }));
        await waitFor(() => expect(backend.state.approvals).toHaveLength(1));
        backend.state.approvals[0].expires_at = new Date(Date.now() - 1000).toISOString();
        await click(closeButton(d));
        await click(await enableButton());
        d = await dialog();
        await waitFor(() => expect(within(d).getByTestId('approval-status')).toHaveTextContent('Expired'));
        expect(within(d).queryByRole('button', { name: 'Approve' })).not.toBeInTheDocument();
        expect(within(d).queryByRole('button', { name: 'Enable campaign' })).not.toBeInTheDocument();
        expect(within(d).getByLabelText(/Daily budget to request/)).toBeInTheDocument();
        expect(enableCalls()).toEqual([]);
    });

    it('an approval that was already used cannot be reused: status "Already used", no enable control, new request offered', async () => {
        await open('alice');
        await click(await enableButton());
        let d = await dialog();
        await type(within(d).getByLabelText(/Daily budget to request/), '5');
        await click(within(d).getByRole('button', { name: 'Request approval' }));
        await waitFor(() => expect(within(d).getByTestId('approval-status')).toHaveTextContent('Pending approval'));
        await type(within(d).getByLabelText(/Budget cap you approve/), '5');
        await click(within(d).getByRole('button', { name: 'Approve' }));
        await waitFor(() => expect(within(d).getByTestId('approval-status')).toHaveTextContent('Approved'));
        await click(within(d).getByRole('button', { name: 'Enable campaign' }));
        await waitFor(() => expect(enableCalls()).toHaveLength(1));

        // the campaign shows as PAUSED again (simulated) and the user tries again
        await click(await enableButton());
        d = await dialog();
        await waitFor(() => expect(within(d).getByTestId('approval-status')).toHaveTextContent('Already used'));
        expect(within(d).getByText(/cannot be reused/)).toBeInTheDocument();
        expect(within(d).queryByRole('button', { name: 'Enable campaign' })).not.toBeInTheDocument();
        expect(enableCalls()).toHaveLength(1);
        // and even forcing the call the server refuses (the fake mirrors the real endpoint)
        const forced = await global.fetch(`${'http://localhost:8000/api/v1'}/google-ads/campaigns/111/enable`, {
            method: 'POST',
            headers: { Authorization: `Bearer ${localStorage.getItem('accessToken')}`, 'X-Organization-Id': 'org-a1' },
            body: JSON.stringify({ confirm: true, approval_id: backend.state.approvals[0].id }),
        });
        expect(forced.status).toBe(403);
        expect(backend.state.enabledCampaigns).toEqual(['111']);
    });

    it('an approval for ANOTHER campaign is not offered for this one', async () => {
        backend.state.campaigns = [
            { id: '111', name: 'Campaign one', status: 'PAUSED', impressions: 1, clicks: 1, cost: 1, conversions: 0 },
            { id: '333', name: 'Campaign three', status: 'PAUSED', impressions: 2, clicks: 1, cost: 1, conversions: 0 },
        ];
        await open('alice');
        await screen.findByText('Campaign one');
        const one = screen.getAllByRole('row').find((r) => r.textContent.includes('Campaign one'));
        await click(within(one).getByRole('button', { name: /^Enable$/ }));
        let d = await dialog();
        await type(within(d).getByLabelText(/Daily budget to request/), '5');
        await click(within(d).getByRole('button', { name: 'Request approval' }));
        await waitFor(() => expect(backend.state.approvals).toHaveLength(1));
        await click(closeButton(d));
        const other = screen.getAllByRole('button', { name: /^Enable$/ });
        // open the OTHER campaign: it must start from "no request"
        const rows = screen.getAllByRole('row');
        const three = rows.find((r) => r.textContent.includes('Campaign three'));
        await click(within(three).getByRole('button', { name: /^Enable$/ }));
        d = await dialog();
        expect(within(d).queryByTestId('approval-status')).not.toBeInTheDocument();
        expect(within(d).getByLabelText(/Daily budget to request/)).toBeInTheDocument();
        expect(other.length).toBeGreaterThan(0);
    });

    it('server refusals are shown translated and nothing is enabled (403 on approve after the role lost ads:spend)', async () => {
        await open('alice');
        await click(await enableButton());
        const d = await dialog();
        await type(within(d).getByLabelText(/Daily budget to request/), '5');
        await click(within(d).getByRole('button', { name: 'Request approval' }));
        await waitFor(() => expect(within(d).getByTestId('approval-status')).toHaveTextContent('Pending approval'));
        backend.setRole('u-alice', 'org-a1', 'viewer'); // role changed on the server while the dialog is open
        await type(within(d).getByLabelText(/Budget cap you approve/), '6');
        await click(within(d).getByRole('button', { name: 'Approve' }));
        expect(await within(d).findByTestId('approval-error')).toHaveTextContent(/Not allowed/);
        expect(backend.state.approvals[0].status).toBe('pending');
        expect(enableCalls()).toEqual([]);
    });

    it('a conflict (already decided elsewhere) is reported and the dialog shows the real status', async () => {
        await open('alice');
        await click(await enableButton());
        const d = await dialog();
        await type(within(d).getByLabelText(/Daily budget to request/), '5');
        await click(within(d).getByRole('button', { name: 'Request approval' }));
        await waitFor(() => expect(within(d).getByTestId('approval-status')).toHaveTextContent('Pending approval'));
        backend.state.approvals[0].status = 'rejected'; // someone else rejected it meanwhile
        await type(within(d).getByLabelText(/Budget cap you approve/), '6');
        await click(within(d).getByRole('button', { name: 'Approve' }));
        expect(await within(d).findByTestId('approval-error')).toHaveTextContent(/already decided or used/);
        await waitFor(() => expect(within(d).getByTestId('approval-status')).toHaveTextContent('Rejected'));
        expect(enableCalls()).toEqual([]);
    });

    it('an approval that disappeared (404) is reported and the dialog falls back to a fresh request', async () => {
        await open('alice');
        await click(await enableButton());
        const d = await dialog();
        await type(within(d).getByLabelText(/Daily budget to request/), '5');
        await click(within(d).getByRole('button', { name: 'Request approval' }));
        await waitFor(() => expect(within(d).getByTestId('approval-status')).toHaveTextContent('Pending approval'));
        backend.state.approvals.length = 0;
        await type(within(d).getByLabelText(/Budget cap you approve/), '6');
        await click(within(d).getByRole('button', { name: 'Approve' }));
        expect(await within(d).findByTestId('approval-error')).toHaveTextContent(/no longer available/);
        await waitFor(() => expect(within(d).queryByTestId('approval-status')).not.toBeInTheDocument());
        expect(within(d).getByLabelText(/Daily budget to request/)).toBeInTheDocument();
    });

    it('keeps keyboard focus inside the dialog (Tab wraps), closes with Escape and returns focus to the opener', async () => {
        await open('alice');
        const opener = await enableButton();
        opener.focus();
        await click(opener);
        const d = await dialog();
        const focusables = [...d.querySelectorAll('button:not([disabled]),input:not([disabled])')];
        const last = focusables.at(-1);
        const first = focusables[0];
        last.focus();
        await act(async () => { fireEvent.keyDown(document, { key: 'Tab' }); });
        expect(document.activeElement).toBe(first);
        first.focus();
        await act(async () => { fireEvent.keyDown(document, { key: 'Tab', shiftKey: true }); });
        expect(document.activeElement).toBe(last);
        await act(async () => { fireEvent.keyDown(document, { key: 'Escape' }); });
        await waitFor(() => expect(screen.queryByRole('dialog')).not.toBeInTheDocument());
        expect(document.activeElement).toBe(opener);
    });

    it('rejects a budget of zero or less before calling the server', async () => {
        await open('alice');
        await click(await enableButton());
        const d = await dialog();
        const request = within(d).getByRole('button', { name: 'Request approval' });
        expect(request).toBeDisabled();
        await type(within(d).getByLabelText(/Daily budget to request/), '0');
        expect(request).toBeDisabled();
        expect(approvalPosts()).toEqual([]);
    });

    it('the approvals list shows the organization\'s requests and opens the matching campaign', async () => {
        await open('alice');
        await click(await enableButton());
        const d = await dialog();
        await type(within(d).getByLabelText(/Daily budget to request/), '5');
        await click(within(d).getByRole('button', { name: 'Request approval' }));
        await waitFor(() => expect(backend.state.approvals).toHaveLength(1));
        await click(closeButton(d));
        const table = await screen.findByTestId('approvals-table');
        const link = within(table).getByRole('button', { name: 'Brand search' });
        expect(within(table).getByText('Pending approval')).toBeInTheDocument();
        await click(link);
        const d2 = await dialog();
        await waitFor(() => expect(within(d2).getByTestId('approval-status')).toHaveTextContent('Pending approval'));
    });

    it('does not show another organization\'s approvals after switching', async () => {
        await open('alice');
        await click(await enableButton());
        const d = await dialog();
        await type(within(d).getByLabelText(/Daily budget to request/), '5');
        await click(within(d).getByRole('button', { name: 'Request approval' }));
        await waitFor(() => expect(backend.state.approvals).toHaveLength(1));
        await click(closeButton(d));
        await act(async () => { fireEvent.click(screen.getByTestId('org-switcher')); });
        await click(screen.getAllByRole('option').find((o) => o.textContent.includes('Acme Two')));
        await screen.findByTestId('connection-denied');
        expect(screen.queryByTestId('approvals-table')).not.toBeInTheDocument();
    });
});

describe('role permissions dialog', () => {
    it('shows the server\'s real matrix for the five roles and marks the current one', async () => {
        await open('alice');
        await act(async () => { fireEvent.click(screen.getByTestId('org-switcher')); });
        await click(screen.getByRole('button', { name: 'View role permissions' }));
        const matrix = await screen.findByTestId('role-matrix');
        const headers = within(matrix).getAllByRole('columnheader').map((h) => h.textContent);
        expect(headers).toEqual(['Capability', ...ROLES.map((r) => r[0].toUpperCase() + r.slice(1))]);
        expect(within(matrix).getByRole('columnheader', { name: 'Owner' })).toHaveAttribute('aria-current', 'true');
        const allowedFor = (perm) => [...within(matrix).getByTestId(`perm-row-${perm}`).querySelectorAll('td')].map((td) => td.dataset.allowed === 'true');
        expect(allowedFor('ads:spend')).toEqual([true, false, false, false, false]); // owner only
        expect(allowedFor('ads:publish')).toEqual([true, true, false, false, false]); // admin and up
        expect(allowedFor('ads:draft')).toEqual([true, true, true, false, false]); // manager and up
        expect(allowedFor('ads:read')).toEqual([true, true, true, true, true]); // everyone
        expect(within(matrix).getAllByText('Allowed', { selector: '.sr-only' }).length).toBeGreaterThan(0);
        expect(screen.getByText(/the server checks every action/i)).toBeInTheDocument();
    });

    it('closes with Escape', async () => {
        await open('alice');
        await act(async () => { fireEvent.click(screen.getByTestId('org-switcher')); });
        await click(screen.getByRole('button', { name: 'View role permissions' }));
        await screen.findByTestId('role-matrix');
        await act(async () => { fireEvent.keyDown(document, { key: 'Escape' }); });
        expect(screen.queryByTestId('role-matrix')).not.toBeInTheDocument();
    });
});

describe('Portuguese (pt-BR) for every new string', () => {
    const RAW = /\b(approvals|tenant|errors)\.[A-Za-z_.]+\b/;
    it('the approval dialog, the list and the permissions dialog render translated', async () => {
        await act(async () => { await i18n.changeLanguage('pt-BR'); });
        await open('alice');
        expect(await screen.findByText('Aprovações de gasto')).toBeInTheDocument();
        await click(await screen.findByRole('button', { name: 'Ativar' }));
        const d = await dialog();
        expect(within(d).getByText('Aprovação de gasto para ativar uma campanha')).toBeInTheDocument();
        expect(within(d).getByRole('button', { name: 'Pedir aprovação' })).toBeInTheDocument();
        expect(d.textContent).not.toMatch(RAW);
        expect(d.textContent).toMatch(/Proprietário/);
        await click(closeButton(d));
        await act(async () => { fireEvent.click(screen.getByTestId('org-switcher')); });
        await click(screen.getByRole('button', { name: 'Ver permissões por papel' }));
        const matrix = await screen.findByTestId('role-matrix');
        expect(within(matrix).getByText('Aprovar e ativar gastos')).toBeInTheDocument();
        expect(matrix.textContent).not.toMatch(RAW);
        expect(within(matrix).getByRole('columnheader', { name: 'Administrador' })).toBeInTheDocument();
    });
});
