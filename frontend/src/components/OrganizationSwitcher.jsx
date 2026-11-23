import React, { useCallback, useEffect, useId, useRef, useState } from 'react';
import { useTranslation } from 'react-i18next';
import { Building2, Check, ChevronDown, ShieldCheck } from 'lucide-react';
import { useOrganization } from '../context/OrganizationContext';
import OrganizationPermissionsModal from './OrganizationPermissionsModal';

/**
 * Organization selector for the header. Same chip look as LanguageSwitcher
 * (amber border, white pill) so it reads as part of the existing chrome.
 * Shows the active organization and the role the SERVER granted; switching only
 * changes which organization the next requests are made for (the backend
 * re-validates membership on every request).
 */
export default function OrganizationSwitcher({ className = '' }) {
    const { t } = useTranslation('tenant');
    const { organizations, activeOrganization, selectOrganization } = useOrganization();
    const [open, setOpen] = useState(false);
    const [showPermissions, setShowPermissions] = useState(false);
    const rootRef = useRef(null);
    const buttonRef = useRef(null);
    const listRef = useRef(null);
    const listId = useId();
    const [focusIndex, setFocusIndex] = useState(0);

    const close = useCallback((restoreFocus = true) => {
        setOpen(false);
        if (restoreFocus) buttonRef.current?.focus();
    }, []);

    useEffect(() => {
        if (open) listRef.current?.focus();
    }, [open]);

    useEffect(() => {
        if (!open) return undefined;
        const onPointer = (event) => {
            if (rootRef.current && !rootRef.current.contains(event.target)) close(false);
        };
        document.addEventListener('mousedown', onPointer);
        return () => document.removeEventListener('mousedown', onPointer);
    }, [open, close]);

    if (!activeOrganization) return null;

    const roleLabel = (role) => t(`roles.${role}`, { defaultValue: role });
    const toggle = () => {
        setFocusIndex(Math.max(0, organizations.findIndex((org) => org.id === activeOrganization.id)));
        setOpen((value) => !value);
    };

    const onListKeyDown = (event) => {
        if (event.key === 'Escape') {
            event.preventDefault();
            close();
        } else if (event.key === 'ArrowDown') {
            event.preventDefault();
            setFocusIndex((i) => Math.min(organizations.length - 1, i + 1));
        } else if (event.key === 'ArrowUp') {
            event.preventDefault();
            setFocusIndex((i) => Math.max(0, i - 1));
        } else if (event.key === 'Enter' || event.key === ' ') {
            event.preventDefault();
            const org = organizations[focusIndex];
            if (org) {
                selectOrganization(org.id);
                close();
            }
        }
    };

    return (
        <div ref={rootRef} className={`relative ${className}`}>
            <button
                ref={buttonRef}
                type="button"
                onClick={toggle}
                aria-haspopup="listbox"
                aria-expanded={open}
                aria-controls={open ? listId : undefined}
                aria-label={t('switcher.label', { name: activeOrganization.name })}
                data-testid="org-switcher"
                className="inline-flex h-10 w-10 md:h-7 md:w-auto md:max-w-[14rem] items-center justify-center md:justify-start gap-1.5 rounded-full border border-amber-200 bg-white/90 md:px-2.5 text-xs font-medium text-amber-900 shadow-sm hover:bg-amber-50 focus-visible:outline focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-amber-600"
            >
                <Building2 size={16} className="text-amber-600 flex-shrink-0" aria-hidden="true" />
                <span className="hidden md:inline truncate" data-testid="org-switcher-name">{activeOrganization.name}</span>
                <ChevronDown size={14} className="hidden md:inline text-amber-600 flex-shrink-0" aria-hidden="true" />
            </button>

            {open && (
                <div className="absolute right-0 mt-2 w-72 max-w-[calc(100vw-1.5rem)] rounded-xl border border-amber-200 bg-white shadow-lg z-50 p-1">
                    <p className="px-3 pt-2 pb-1 text-xs font-medium uppercase tracking-wide text-amber-700">{t('switcher.heading')}</p>
                    <ul
                        id={listId}
                        role="listbox"
                        aria-label={t('switcher.heading')}
                        tabIndex={-1}
                        ref={listRef}
                        onKeyDown={onListKeyDown}
                        className="max-h-64 overflow-y-auto outline-none"
                    >
                        {organizations.map((org, index) => {
                            const selected = org.id === activeOrganization.id;
                            return (
                                <li
                                    key={org.id}
                                    role="option"
                                    aria-selected={selected}
                                    data-testid={`org-option-${org.id}`}
                                    onClick={() => {
                                        selectOrganization(org.id);
                                        close();
                                    }}
                                    onMouseEnter={() => setFocusIndex(index)}
                                    className={`flex min-h-11 cursor-pointer items-center gap-3 rounded-lg px-3 py-2 text-sm ${index === focusIndex ? 'bg-amber-50' : ''} ${selected ? 'font-medium text-amber-900' : 'text-gray-700'}`}
                                >
                                    <span className="min-w-0 flex-1">
                                        <span className="block truncate">{org.name}</span>
                                        <span className="block text-xs text-gray-500">{roleLabel(org.role)}</span>
                                    </span>
                                    {selected && <Check size={16} className="text-amber-600 flex-shrink-0" aria-hidden="true" />}
                                </li>
                            );
                        })}
                    </ul>
                    {organizations.length === 1 && <p className="px-3 py-1 text-xs text-gray-500">{t('switcher.single')}</p>}
                    <button
                        type="button"
                        onClick={() => {
                            setOpen(false);
                            setShowPermissions(true);
                        }}
                        className="mt-1 flex min-h-11 w-full items-center gap-2 rounded-lg border-t border-amber-100 px-3 py-2 text-left text-sm text-amber-800 hover:bg-amber-50 focus-visible:outline focus-visible:outline-2 focus-visible:outline-amber-600"
                    >
                        <ShieldCheck size={16} aria-hidden="true" />
                        {t('switcher.viewPermissions')}
                    </button>
                </div>
            )}

            <OrganizationPermissionsModal isOpen={showPermissions} onClose={() => setShowPermissions(false)} />
        </div>
    );
}
