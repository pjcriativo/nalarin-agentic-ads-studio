/* eslint-disable react-refresh/only-export-components -- context modules intentionally export the provider and its hook together */
import React, { createContext, useCallback, useContext, useEffect, useMemo, useRef, useState } from 'react';
import { useTranslation } from 'react-i18next';
import { Building2, LogOut, RefreshCw } from 'lucide-react';
import { useAuth } from './AuthContext';
import { useToast } from './ToastContext';
import { getOrganizationId, onAccessLost, setOrganizationId, subscribe } from '../lib/tenant';

const API_URL = import.meta.env.VITE_API_URL || 'http://localhost:8000/api/v1';
const REVALIDATE_MS = 15000;

const OrganizationContext = createContext(null);

export const useOrganization = () => {
    const context = useContext(OrganizationContext);
    if (!context) throw new Error('useOrganization must be used within OrganizationProvider');
    return context;
};

/**
 * Organizations of the signed-in user and the ACTIVE one.
 *
 * - The server is the source of truth: the list (with role + permissions) comes
 *   from GET /organizations and is re-validated on load, on tab focus and
 *   whenever a request is answered with "no access to this organization".
 * - The persisted selection is only a hint. If it is no longer in the list the
 *   selection is cleared and the user is told.
 * - `can(permission)` only decides what the UI SHOWS. The backend authorizes every
 *   request on its own; a hidden button is never a security control.
 */
export const OrganizationProvider = ({ children }) => {
    const { user, isAuthenticated, authFetch } = useAuth();
    const { showWarning } = useToast();
    const { t } = useTranslation('tenant');
    const [organizations, setOrganizations] = useState([]);
    const [status, setStatus] = useState('idle'); // idle | loading | ready | none | error
    const [activeId, setActiveId] = useState(getOrganizationId());
    const lastCheck = useRef(0);
    const tRef = useRef(t);
    tRef.current = t;

    useEffect(() => subscribe((s) => setActiveId(s.orgId)), []);

    const load = useCallback(async ({ silent = false } = {}) => {
        if (!isAuthenticated) return;
        lastCheck.current = Date.now();
        if (!silent) setStatus('loading');
        try {
            const response = await authFetch(`${API_URL}/organizations`, { tenant: false });
            if (!response.ok) throw new Error(String(response.status));
            const data = await response.json();
            const list = Array.isArray(data) ? data : [];
            setOrganizations(list);
            if (list.length === 0) {
                setOrganizationId(null);
                setStatus('none');
                return;
            }
            const stored = getOrganizationId();
            if (!(stored && list.some((org) => org.id === stored))) {
                if (stored) showWarning(tRef.current('toast.accessLost'));
                setOrganizationId(list[0].id);
            }
            setStatus('ready');
        } catch {
            if (!silent) setStatus('error');
        }
    }, [authFetch, isAuthenticated, showWarning]);

    // (Re)load whenever the signed-in user changes.
    const userId = user?.id ?? null;
    useEffect(() => {
        if (!isAuthenticated || userId == null) {
            setOrganizations([]);
            setStatus('idle');
            return;
        }
        load();
    // eslint-disable-next-line react-hooks/exhaustive-deps -- reload per user, not per authFetch identity
    }, [isAuthenticated, userId]);

    // A request was refused with "no access to this organization": re-validate now.
    useEffect(() => onAccessLost(() => load({ silent: true })), [load]);

    // Membership can be revoked while the tab is open: re-validate when it regains focus.
    useEffect(() => {
        if (!isAuthenticated) return undefined;
        const onVisible = () => {
            if (document.visibilityState === 'visible' && Date.now() - lastCheck.current > REVALIDATE_MS) load({ silent: true });
        };
        document.addEventListener('visibilitychange', onVisible);
        return () => document.removeEventListener('visibilitychange', onVisible);
    }, [isAuthenticated, load]);

    const activeOrganization = useMemo(
        () => organizations.find((org) => org.id === activeId) || null,
        [organizations, activeId],
    );
    const permissions = useMemo(() => new Set(activeOrganization?.permissions || []), [activeOrganization]);
    const can = useCallback((permission) => permissions.has(permission), [permissions]);

    const selectOrganization = useCallback((id) => {
        if (organizations.some((org) => org.id === id)) setOrganizationId(id);
    }, [organizations]);

    const value = useMemo(() => ({
        organizations,
        activeOrganization,
        role: activeOrganization?.role || null,
        permissions,
        can,
        status,
        selectOrganization,
        reload: load,
    }), [organizations, activeOrganization, permissions, can, status, selectOrganization, load]);

    return <OrganizationContext.Provider value={value}>{children}</OrganizationContext.Provider>;
};

const Centered = ({ children }) => (
    <div className="flex h-screen w-full items-center justify-center bg-gray-50 p-6">{children}</div>
);

/**
 * Renders its subtree only for a validated organization and REMOUNTS it (key)
 * whenever the user or the organization changes: every provider, list, form,
 * cache and in-flight request below belongs to exactly one tenant.
 */
export function TenantBoundary({ children }) {
    const { isAuthenticated, user, logout } = useAuth();
    const { status, activeOrganization, reload } = useOrganization();
    const { t } = useTranslation('tenant');

    if (!isAuthenticated) return children;

    // 'ready' without an active organization is the instant between an identity change and the
    // new selection: never render (or fetch for) a tenant-less tree.
    if (status === 'idle' || status === 'loading' || (status === 'ready' && !activeOrganization)) {
        return (
            <Centered>
                <div role="status" aria-label={t('boundary.loading')} className="animate-spin rounded-full h-8 w-8 border-b-2 border-amber-600" />
            </Centered>
        );
    }
    if (status === 'none' || status === 'error') {
        const isNone = status === 'none';
        return (
            <Centered>
                <div role="alert" className="bg-white rounded-xl border border-amber-200 shadow-sm p-8 max-w-md text-center">
                    <div className="mx-auto mb-4 w-12 h-12 rounded-full bg-amber-50 flex items-center justify-center">
                        <Building2 className="text-amber-600" size={24} />
                    </div>
                    <h2 className="text-lg font-bold text-gray-900">{isNone ? t('boundary.noneTitle') : t('boundary.errorTitle')}</h2>
                    <p className="text-sm text-gray-600 mt-2">{isNone ? t('boundary.noneBody') : t('boundary.errorBody')}</p>
                    <div className="mt-6 flex justify-center gap-3">
                        {!isNone && (
                            <button
                                type="button"
                                onClick={() => reload()}
                                className="inline-flex items-center gap-2 px-4 py-2 bg-amber-600 text-white rounded-lg font-medium hover:bg-amber-700 transition-colors shadow-sm"
                            >
                                <RefreshCw size={16} />
                                {t('boundary.retry')}
                            </button>
                        )}
                        <button
                            type="button"
                            onClick={() => logout()}
                            className="inline-flex items-center gap-2 px-4 py-2 text-gray-700 hover:bg-gray-100 rounded-lg transition-colors font-medium"
                        >
                            <LogOut size={16} />
                            {t('boundary.logout')}
                        </button>
                    </div>
                </div>
            </Centered>
        );
    }
    return <React.Fragment key={`${user?.id}:${activeOrganization?.id}`}>{children}</React.Fragment>;
}
