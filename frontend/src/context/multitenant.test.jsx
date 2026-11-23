/**
 * Multi-tenant front end, end to end inside jsdom: the REAL AuthProvider,
 * OrganizationProvider, TenantBoundary, BrandProvider and OrganizationSwitcher
 * against an in-memory, tenant-aware backend (src/__tests__/fakeBackend.js).
 */
import React from 'react';
import { act, cleanup, fireEvent, render, screen, waitFor, within } from '@testing-library/react';
import { MemoryRouter } from 'react-router-dom';

import i18n from '../i18n';
import { createFakeBackend, memoryStorage } from '../__tests__/fakeBackend';
import { AuthProvider, useAuth } from './AuthContext';
import { BrandProvider, useBrands } from './BrandContext';
import { OrganizationProvider, TenantBoundary } from './OrganizationContext';
import { ToastProvider } from './ToastContext';
import OrganizationSwitcher from '../components/OrganizationSwitcher';
import { clearTenant, getOrganizationId } from '../lib/tenant';

const API = 'http://localhost:8000/api/v1';
let backend;

function Probe() {
    const { brands } = useBrands();
    const { logout, login, authFetch } = useAuth();
    return (
        <div>
            <ul data-testid="brands">{brands.map((b) => <li key={b.id}>{b.name}</li>)}</ul>
            <button onClick={() => logout()}>do-logout</button>
            <button onClick={() => login('bob@example.com', 'pw')}>do-login-bob</button>
            <button onClick={() => authFetch(`${API}/brands`)}>ping</button>
        </div>
    );
}

const Tree = () => (
    <MemoryRouter>
        <ToastProvider>
            <AuthProvider>
                <OrganizationProvider>
                    <TenantBoundary>
                        <BrandProvider>
                            <OrganizationSwitcher />
                            <Probe />
                        </BrandProvider>
                    </TenantBoundary>
                </OrganizationProvider>
            </AuthProvider>
        </ToastProvider>
    </MemoryRouter>
);

const signIn = (userKey, extra = {}) => {
    const tokens = backend.loginTokens(userKey);
    localStorage.setItem('accessToken', tokens.access_token);
    localStorage.setItem('refreshToken', tokens.refresh_token);
    Object.entries(extra).forEach(([k, v]) => localStorage.setItem(k, v));
};

const brandNames = () => within(screen.getByTestId('brands')).queryAllByRole('listitem').map((li) => li.textContent);
const tenantCalls = () => backend.calls().filter((c) => ['/brands', '/profiles', '/dashboard/stats'].includes(c.path));
const chooseOrg = async (name) => {
    await act(async () => { fireEvent.click(screen.getByTestId('org-switcher')); });
    const option = screen.getAllByRole('option').find((o) => o.textContent.includes(name));
    await act(async () => { fireEvent.click(option); });
};

beforeEach(async () => {
    global.localStorage = memoryStorage();
    clearTenant();
    backend = createFakeBackend();
    global.fetch = backend.fetch;
    await act(async () => { await i18n.changeLanguage('en-US'); });
});
afterEach(() => cleanup());

describe('organization selection and X-Organization-Id', () => {
    it('defaults to the first organization, sends the header on EVERY tenant request and shows only its data', async () => {
        signIn('alice');
        render(<Tree />);
        await waitFor(() => expect(brandNames()).toEqual(['Alpha One Brand']));
        expect(tenantCalls().length).toBeGreaterThan(0);
        expect(tenantCalls().every((c) => c.org === 'org-a1')).toBe(true);
        expect(screen.getByTestId('org-switcher-name')).toHaveTextContent('Acme One');
        expect(localStorage.getItem('tenant:org:u-alice')).toBe('org-a1');
    });

    it('switching organization refetches everything for the new tenant and drops the old data', async () => {
        signIn('alice');
        render(<Tree />);
        await waitFor(() => expect(brandNames()).toEqual(['Alpha One Brand']));
        await chooseOrg('Acme Two');
        await waitFor(() => expect(brandNames()).toEqual(['Alpha Two Brand']));
        expect(screen.queryByText('Alpha One Brand')).not.toBeInTheDocument();
        expect(backend.callsTo('/brands').at(-1).org).toBe('org-a2');
        expect(getOrganizationId()).toBe('org-a2');
        expect(localStorage.getItem('tenant:org:u-alice')).toBe('org-a2');
        expect(screen.getByTestId('org-switcher-name')).toHaveTextContent('Acme Two');
    });

    it('restores the persisted selection after a reload without ever asking for another tenant first', async () => {
        signIn('alice', { 'tenant:org:u-alice': 'org-a2' });
        render(<Tree />);
        await waitFor(() => expect(brandNames()).toEqual(['Alpha Two Brand']));
        expect(tenantCalls().every((c) => c.org === 'org-a2')).toBe(true);
    });

    it('validates a stale persisted selection on the server: unknown organization is cleared with a notice', async () => {
        signIn('alice', { 'tenant:org:u-alice': 'org-b' }); // alice is NOT a member of org-b
        render(<Tree />);
        await waitFor(() => expect(brandNames()).toEqual(['Alpha One Brand']));
        expect(tenantCalls().some((c) => c.org === 'org-b')).toBe(false);
        expect(localStorage.getItem('tenant:org:u-alice')).toBe('org-a1');
        expect(await screen.findByText(/no longer have access to the selected organization/i)).toBeInTheDocument();
    });

    it('a user with no active membership sees a blocking message and no tenant request is made', async () => {
        backend.revoke('u-bob', 'org-b');
        signIn('bob');
        render(<Tree />);
        expect(await screen.findByText('No organization access')).toBeInTheDocument();
        expect(tenantCalls()).toEqual([]);
        expect(screen.queryByTestId('org-switcher')).not.toBeInTheDocument();
    });

    it('a single-organization user still sees the organization and role, with no one to switch to', async () => {
        signIn('bob');
        render(<Tree />);
        await waitFor(() => expect(brandNames()).toEqual(['Beta Brand']));
        await act(async () => { fireEvent.click(screen.getByTestId('org-switcher')); });
        expect(screen.getAllByRole('option')).toHaveLength(1);
        expect(screen.getByText('You belong to a single organization.')).toBeInTheDocument();
        expect(within(screen.getByRole('listbox')).getByText('Owner')).toBeInTheDocument();
    });

    it('the switcher is keyboard operable (arrows, Enter, Escape)', async () => {
        signIn('alice');
        render(<Tree />);
        await waitFor(() => expect(brandNames()).toEqual(['Alpha One Brand']));
        const button = screen.getByTestId('org-switcher');
        await act(async () => { fireEvent.click(button); });
        const list = screen.getByRole('listbox');
        expect(button).toHaveAttribute('aria-expanded', 'true');
        await act(async () => { fireEvent.keyDown(list, { key: 'Escape' }); });
        expect(screen.queryByRole('listbox')).not.toBeInTheDocument();
        await act(async () => { fireEvent.click(button); });
        await act(async () => { fireEvent.keyDown(screen.getByRole('listbox'), { key: 'ArrowDown' }); });
        await act(async () => { fireEvent.keyDown(screen.getByRole('listbox'), { key: 'Enter' }); });
        await waitFor(() => expect(brandNames()).toEqual(['Alpha Two Brand']));
    });
});

describe('no data of the previous tenant survives', () => {
    it('a slow answer for the previous organization that arrives after the switch is ignored', async () => {
        signIn('alice');
        const release = backend.hold('/brands', 'org-a1'); // org-a1's brand list is slow
        render(<Tree />);
        await waitFor(() => expect(screen.getByTestId('org-switcher')).toBeInTheDocument());
        await chooseOrg('Acme Two');
        await waitFor(() => expect(brandNames()).toEqual(['Alpha Two Brand']));
        await act(async () => { release(); await new Promise((r) => setTimeout(r, 30)); });
        expect(brandNames()).toEqual(['Alpha Two Brand']);
        expect(screen.queryByText('Alpha One Brand')).not.toBeInTheDocument();
    });

    it('rapid A -> B -> A switching ends on A with only A data', async () => {
        signIn('alice');
        render(<Tree />);
        await waitFor(() => expect(brandNames()).toEqual(['Alpha One Brand']));
        await chooseOrg('Acme Two');
        await chooseOrg('Acme One');
        await waitFor(() => expect(brandNames()).toEqual(['Alpha One Brand']));
        expect(backend.callsTo('/brands').at(-1).org).toBe('org-a1');
    });

    it('tenant-scoped preferences are not readable from another organization', async () => {
        const { tenantStorage } = await import('../lib/tenant');
        signIn('alice');
        render(<Tree />);
        await waitFor(() => expect(brandNames()).toEqual(['Alpha One Brand']));
        tenantStorage.setItem('lastSelectedAdAccountId', 'act_A1_ONLY');
        await chooseOrg('Acme Two');
        await waitFor(() => expect(brandNames()).toEqual(['Alpha Two Brand']));
        expect(tenantStorage.getItem('lastSelectedAdAccountId')).toBeNull();
    });
});

describe('session lifecycle', () => {
    it('logout clears the selection, every persisted selection and stops sending the header', async () => {
        signIn('alice');
        render(<Tree />);
        await waitFor(() => expect(brandNames()).toEqual(['Alpha One Brand']));
        await act(async () => { fireEvent.click(screen.getByText('do-logout')); });
        await waitFor(() => expect(getOrganizationId()).toBeNull());
        expect([...localStorage._data.keys()].filter((k) => k.startsWith('tenant:'))).toEqual([]);
        expect(localStorage.getItem('accessToken')).toBeNull();
    });

    it('signing in as a DIFFERENT user in the same tab never reuses the previous user\'s organization', async () => {
        signIn('alice');
        render(<Tree />);
        await waitFor(() => expect(brandNames()).toEqual(['Alpha One Brand']));
        await act(async () => { fireEvent.click(screen.getByText('do-login-bob')); });
        await waitFor(() => expect(brandNames()).toEqual(['Beta Brand']));
        const afterLogin = backend.callsTo('/brands').filter((c) => c.auth && c.auth.includes('u-bob'));
        expect(afterLogin.length).toBeGreaterThan(0);
        expect(afterLogin.every((c) => c.org === 'org-b')).toBe(true);
        expect(screen.queryByText('Alpha One Brand')).not.toBeInTheDocument();
    });

    it('membership revoked while the app is open: the next refusal re-validates, notifies and moves to another organization', async () => {
        signIn('alice');
        render(<Tree />);
        await waitFor(() => expect(brandNames()).toEqual(['Alpha One Brand']));
        backend.revoke('u-alice', 'org-a1');
        await act(async () => { fireEvent.click(screen.getByText('ping')); });
        await waitFor(() => expect(brandNames()).toEqual(['Alpha Two Brand']));
        expect(await screen.findByText(/no longer have access to the selected organization/i)).toBeInTheDocument();
        expect(getOrganizationId()).toBe('org-a2');
        expect(screen.getByTestId('org-switcher-name')).toHaveTextContent('Acme Two');
    });

    it('after the access token expires the refresh-and-retry still carries the CURRENT organization', async () => {
        signIn('alice', { 'tenant:org:u-alice': 'org-a2' });
        render(<Tree />);
        await waitFor(() => expect(brandNames()).toEqual(['Alpha Two Brand']));
        backend.state.calls.length = 0;
        backend.expireAccessTokens();
        await act(async () => { fireEvent.click(screen.getByText('ping')); });
        await waitFor(() => expect(backend.callsTo('/auth/refresh').length).toBe(1));
        await waitFor(() => expect(backend.callsTo('/brands').length).toBeGreaterThanOrEqual(2)); // 401, then the retry
        expect(backend.callsTo('/brands').every((c) => c.org === 'org-a2')).toBe(true); // the 401 AND every retry
        const retry = backend.callsTo('/brands').at(-1);
        expect(retry.auth).not.toBe(backend.callsTo('/brands')[0].auth); // fresh token
    });

    it('a tab regaining focus re-validates membership (revocation noticed without any action)', async () => {
        signIn('alice');
        render(<Tree />);
        await waitFor(() => expect(brandNames()).toEqual(['Alpha One Brand']));
        backend.revoke('u-alice', 'org-a1');
        const realNow = Date.now;
        Date.now = () => realNow() + 60_000; // past the revalidation throttle
        try {
            await act(async () => { document.dispatchEvent(new Event('visibilitychange')); });
            await waitFor(() => expect(brandNames()).toEqual(['Alpha Two Brand']));
        } finally {
            Date.now = realNow;
        }
    });
});
