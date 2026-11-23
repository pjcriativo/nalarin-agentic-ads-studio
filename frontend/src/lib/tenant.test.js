import { memoryStorage } from '../__tests__/fakeBackend';
import { enablePayloadHash, effectiveStatus, findEnableApproval, sha256Hex, usdToMicros } from './spendApprovals';
import {
    bindUser, clearTenant, getEpoch, getOrganizationId, isAccessLostMessage, onAccessLost,
    reportAccessLost, setOrganizationId, subscribe, tenantHeaders, tenantStorage,
} from './tenant';

beforeEach(() => {
    global.localStorage = memoryStorage();
    clearTenant();
});

describe('tenant store', () => {
    it('sends no organization header until one is selected, then always sends it', () => {
        expect(tenantHeaders()).toEqual({});
        bindUser('u1');
        setOrganizationId('org-1');
        expect(tenantHeaders()).toEqual({ 'X-Organization-Id': 'org-1' });
    });

    it('persists the selection per user and restores it for that user only', () => {
        bindUser('u1');
        setOrganizationId('org-1');
        bindUser('u2');
        expect(getOrganizationId()).toBeNull(); // another user never inherits it
        setOrganizationId('org-9');
        bindUser('u1');
        expect(getOrganizationId()).toBe('org-1');
        bindUser('u2');
        expect(getOrganizationId()).toBe('org-9');
    });

    it('bumps the epoch on user and organization changes, not on no-ops', () => {
        const start = getEpoch();
        bindUser('u1');
        const afterUser = getEpoch();
        expect(afterUser).toBeGreaterThan(start);
        bindUser('u1');
        setOrganizationId('org-1');
        const afterOrg = getEpoch();
        expect(afterOrg).toBeGreaterThan(afterUser);
        setOrganizationId('org-1');
        expect(getEpoch()).toBe(afterOrg);
    });

    it('logout clears the selection, every persisted selection and tenant-scoped preferences', () => {
        bindUser('u1');
        setOrganizationId('org-1');
        tenantStorage.setItem('draft', 'secret');
        localStorage.setItem('preferred_view_mode', 'grid'); // plain UI preference survives
        clearTenant();
        expect(getOrganizationId()).toBeNull();
        expect(tenantHeaders()).toEqual({});
        bindUser('u1');
        expect(getOrganizationId()).toBeNull(); // not restored after logout
        expect(localStorage.getItem('preferred_view_mode')).toBe('grid');
        expect([...localStorage._data.keys()].filter((k) => k.startsWith('tenant:'))).toEqual([]);
    });

    it('namespaces preferences by user AND organization', () => {
        bindUser('u1');
        setOrganizationId('org-1');
        tenantStorage.setItem('lastAccount', 'acct-A');
        setOrganizationId('org-2');
        expect(tenantStorage.getItem('lastAccount')).toBeNull(); // org-2 cannot see org-1's value
        tenantStorage.setItem('lastAccount', 'acct-B');
        setOrganizationId('org-1');
        expect(tenantStorage.getItem('lastAccount')).toBe('acct-A');
        bindUser('u2');
        setOrganizationId('org-1');
        expect(tenantStorage.getItem('lastAccount')).toBeNull(); // another user of the same org neither
    });

    it('stores nothing without a selected organization', () => {
        bindUser('u1');
        tenantStorage.setItem('x', 'y');
        expect(tenantStorage.getItem('x')).toBeNull();
        expect([...localStorage._data.keys()].filter((k) => k.startsWith('tenant:data:'))).toEqual([]);
    });

    it('notifies subscribers and access-lost listeners', () => {
        const seen = vi.fn();
        const lost = vi.fn();
        const off = subscribe(seen);
        const offLost = onAccessLost(lost);
        bindUser('u1');
        setOrganizationId('org-1');
        reportAccessLost();
        expect(seen).toHaveBeenLastCalledWith(expect.objectContaining({ orgId: 'org-1', userId: 'u1' }));
        expect(lost).toHaveBeenCalledTimes(1);
        off();
        offLost();
        setOrganizationId('org-2');
        reportAccessLost();
        expect(lost).toHaveBeenCalledTimes(1);
    });

    it('recognizes the backend messages that mean "no access to this organization"', () => {
        expect(isAccessLostMessage('You do not have access to this organization')).toBe(true);
        expect(isAccessLostMessage('No active organization membership')).toBe(true);
        expect(isAccessLostMessage('Several organizations: send the X-Organization-Id header')).toBe(true);
        expect(isAccessLostMessage('Insufficient permissions in this organization')).toBe(false);
        expect(isAccessLostMessage(undefined)).toBe(false);
    });
});

describe('spend approval helpers', () => {
    it('computes SHA-256 exactly (known vectors, UTF-8)', () => {
        expect(sha256Hex('abc')).toBe('ba7816bf8f01cfea414140de5dae2223b00361a396177a9cb410ff61f20015ad');
        expect(sha256Hex('ação–€')).toBe('92b1ee795971cdb6dcfc36a21e6530d26493f618d5092999a22ea82fede2d223');
        expect(sha256Hex('')).toBe('e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855');
    });

    it('produces the same payload hash as the backend canonical_payload_hash', () => {
        // python: sha256(json.dumps({"campaign_id": "123"}, sort_keys=True, separators=(",", ":")))
        expect(enablePayloadHash('123')).toBe('cf4d9ba784e28d4bf3769725ced28978b4f30db89ef3c8764f965b104e6768e2');
        expect(enablePayloadHash(123)).toBe(enablePayloadHash('123'));
    });

    it('finds the newest approval of the same account and campaign only', () => {
        const hash = enablePayloadHash('7');
        const list = [
            { id: 'new', provider: 'google_ads', action: 'campaign.enable', account_id: '1111111111', payload_hash: hash },
            { id: 'old', provider: 'google_ads', action: 'campaign.enable', account_id: '1111111111', payload_hash: hash },
            { id: 'other-campaign', provider: 'google_ads', action: 'campaign.enable', account_id: '1111111111', payload_hash: enablePayloadHash('8') },
            { id: 'other-account', provider: 'google_ads', action: 'campaign.enable', account_id: '2222222222', payload_hash: hash },
            { id: 'other-platform', provider: 'meta', action: 'campaign.enable', account_id: '1111111111', payload_hash: hash },
        ];
        expect(findEnableApproval(list, '111-111-1111', '7').id).toBe('new');
        expect(findEnableApproval(list, '3333333333', '7')).toBeNull();
        expect(findEnableApproval([], '1111111111', '7')).toBeNull();
    });

    it('never shows an expired pending/approved approval as usable', () => {
        const past = new Date(Date.now() - 1000).toISOString();
        const future = new Date(Date.now() + 60000).toISOString();
        expect(effectiveStatus({ status: 'pending', expires_at: past })).toBe('expired');
        expect(effectiveStatus({ status: 'approved', expires_at: past })).toBe('expired');
        expect(effectiveStatus({ status: 'approved', expires_at: future })).toBe('approved');
        expect(effectiveStatus({ status: 'consumed', expires_at: past })).toBe('consumed');
        expect(effectiveStatus(null)).toBe('none');
    });

    it('converts USD to micros without float drift', () => {
        expect(usdToMicros('12.34')).toBe(12_340_000);
        expect(usdToMicros('0.07')).toBe(70_000);
    });
});
