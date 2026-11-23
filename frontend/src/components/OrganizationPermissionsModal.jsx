import React, { useEffect, useMemo, useRef, useState } from 'react';
import { createPortal } from 'react-dom';
import { useTranslation } from 'react-i18next';
import { Check, Minus, ShieldCheck, X } from 'lucide-react';
import useFocusTrap from '../lib/useFocusTrap';
import { useAuth } from '../context/AuthContext';
import { useOrganization } from '../context/OrganizationContext';

const API_URL = import.meta.env.VITE_API_URL || 'http://localhost:8000/api/v1';
const ACTION_ORDER = ['read', 'write', 'delete', 'draft', 'publish', 'spend', 'manage'];

/**
 * Read-only matrix of what each organization role is granted, taken VERBATIM from
 * the server (GET /organizations/role-matrix). It explains the interface; it
 * grants nothing: every endpoint authorizes the request on its own.
 */
export default function OrganizationPermissionsModal({ isOpen, onClose }) {
    const { t } = useTranslation('tenant');
    const { authFetch } = useAuth();
    const { activeOrganization, role } = useOrganization();
    const [matrix, setMatrix] = useState(null);
    const [failed, setFailed] = useState(false);
    const closeRef = useRef(null);
    const dialogRef = useRef(null);
    useFocusTrap(dialogRef, isOpen);

    useEffect(() => {
        if (!isOpen) return undefined;
        let cancelled = false;
        (async () => {
            setFailed(false);
            try {
                const response = await authFetch(`${API_URL}/organizations/role-matrix`, { tenant: false });
                if (!response.ok) throw new Error(String(response.status));
                const data = await response.json();
                if (!cancelled) setMatrix(data);
            } catch {
                if (!cancelled) setFailed(true);
            }
        })();
        return () => { cancelled = true; };
    }, [isOpen, authFetch]);

    useEffect(() => {
        if (!isOpen) return undefined;
        closeRef.current?.focus();
        const onKey = (event) => {
            if (event.key === 'Escape') onClose();
        };
        document.addEventListener('keydown', onKey);
        return () => document.removeEventListener('keydown', onKey);
    }, [isOpen, onClose]);

    const groups = useMemo(() => {
        if (!matrix) return [];
        // read -> write -> delete -> draft -> publish -> spend -> manage: from least to most powerful
        const order = (name) => {
            const index = ACTION_ORDER.indexOf(name.split(':')[1]);
            return index === -1 ? ACTION_ORDER.length : index;
        };
        const all = [...new Set(Object.values(matrix.permissions).flat())].sort((a, b) => order(a) - order(b) || a.localeCompare(b));
        const byGroup = new Map();
        for (const name of all) {
            const [resource] = name.split(':');
            if (!byGroup.has(resource)) byGroup.set(resource, []);
            byGroup.get(resource).push(name);
        }
        return [...byGroup.entries()].sort(([a], [b]) => a.localeCompare(b));
    }, [matrix]);

    if (!isOpen) return null;

    const roleLabel = (r) => t(`roles.${r}`, { defaultValue: r });
    const permLabel = (name) => t(`perm.${name.replace(':', '_')}`, { defaultValue: name });

    // Portal: a modal rendered inside a `space-y-*` page container would inherit the sibling margin
    // and leave a strip at the top of the backdrop.
    return createPortal(
        <div className="fixed inset-0 bg-black/50 backdrop-blur-sm flex items-center justify-center z-50 p-4" onClick={onClose}>
            <div
                ref={dialogRef}
                role="dialog"
                aria-modal="true"
                aria-labelledby="org-permissions-title"
                className="bg-white rounded-xl w-full max-w-3xl max-h-[90vh] flex flex-col shadow-xl"
                onClick={(event) => event.stopPropagation()}
            >
                <div className="flex items-start gap-4 p-6 pb-3">
                    <div className="w-12 h-12 rounded-full bg-amber-100 flex items-center justify-center flex-shrink-0">
                        <ShieldCheck className="text-amber-700" size={24} />
                    </div>
                    <div className="flex-1 min-w-0">
                        <h3 id="org-permissions-title" className="text-lg font-bold text-gray-900">{t('permissions.title')}</h3>
                        {activeOrganization && (
                            <p className="text-sm text-gray-600 mt-1">
                                {t('permissions.yourRole', { organization: activeOrganization.name, role: roleLabel(role) })}
                            </p>
                        )}
                        <p className="text-xs text-gray-500 mt-1">{t('permissions.disclaimer')}</p>
                    </div>
                    <button
                        ref={closeRef}
                        type="button"
                        onClick={onClose}
                        aria-label={t('permissions.close')}
                        className="text-gray-400 hover:text-gray-600 p-2 -m-2 rounded-lg focus-visible:outline focus-visible:outline-2 focus-visible:outline-amber-600"
                    >
                        <X size={20} />
                    </button>
                </div>

                <div className="overflow-auto px-6 pb-6">
                    {failed && <p role="alert" className="text-sm text-red-600">{t('permissions.loadFailed')}</p>}
                    {!matrix && !failed && <p role="status" className="text-sm text-gray-500">{t('permissions.loading')}</p>}
                    {matrix && (
                        <table className="w-full text-sm border-collapse" data-testid="role-matrix">
                            <thead>
                                <tr className="border-b border-amber-100 text-left">
                                    <th scope="col" className="py-2 pr-3 font-medium text-gray-600">{t('permissions.capability')}</th>
                                    {matrix.roles.map((r) => (
                                        <th
                                            key={r}
                                            scope="col"
                                            className={`px-2 py-2 text-center font-medium ${r === role ? 'text-amber-900 bg-amber-50' : 'text-gray-600'}`}
                                            aria-current={r === role ? 'true' : undefined}
                                        >
                                            {roleLabel(r)}
                                        </th>
                                    ))}
                                </tr>
                            </thead>
                            <tbody>
                                {groups.map(([resource, names]) => (
                                    <React.Fragment key={resource}>
                                        <tr>
                                            <th scope="rowgroup" colSpan={matrix.roles.length + 1} className="pt-4 pb-1 text-left text-xs font-semibold uppercase tracking-wide text-amber-700">
                                                {t(`perm.group.${resource}`, { defaultValue: resource })}
                                            </th>
                                        </tr>
                                        {names.map((name) => (
                                            <tr key={name} className="border-b border-gray-100" data-testid={`perm-row-${name}`}>
                                                <th scope="row" className="py-1.5 pr-3 text-left font-normal text-gray-700">{permLabel(name)}</th>
                                                {matrix.roles.map((r) => {
                                                    const allowed = matrix.permissions[r]?.includes(name);
                                                    return (
                                                        <td key={r} className={`px-2 py-1.5 text-center ${r === role ? 'bg-amber-50' : ''}`} data-allowed={allowed ? 'true' : 'false'}>
                                                            {allowed
                                                                ? <Check size={16} className="inline text-green-600" aria-hidden="true" />
                                                                : <Minus size={16} className="inline text-gray-300" aria-hidden="true" />}
                                                            <span className="sr-only">{allowed ? t('permissions.allowed') : t('permissions.denied')}</span>
                                                        </td>
                                                    );
                                                })}
                                            </tr>
                                        ))}
                                    </React.Fragment>
                                ))}
                            </tbody>
                        </table>
                    )}
                </div>
            </div>
        </div>,
        document.body,
    );
}
