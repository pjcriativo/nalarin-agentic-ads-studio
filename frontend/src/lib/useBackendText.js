import { useCallback } from 'react';
import { useTranslation } from 'react-i18next';

/**
 * Text that the backend owns (agent names, descriptions, limitations, tool descriptions) is versioned there, in English.
 * This returns the translation of `key` ONLY while the en-US catalogue still holds EXACTLY the text the backend sent;
 * if the backend text changed (a new agent version) the translation could be stale, so the backend text is shown as
 * received. In en-US it is always the backend text. Nothing is ever machine-translated at runtime.
 */
export default function useBackendText() {
    const { t, i18n } = useTranslation('agents');
    return useCallback((key, backend) => {
        if (backend === null || backend === undefined || backend === '') return backend;
        const english = i18n.getResource('en-US', 'agents', key);
        return english === backend ? t(key) : backend;
    }, [t, i18n]);
}

/** i18n key segment for an API identifier that contains dots (i18next uses dots as separators). */
export const idKey = (id) => String(id).replace(/[^a-zA-Z0-9]+/g, '_');
