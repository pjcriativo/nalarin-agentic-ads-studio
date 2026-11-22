import i18n from './index';

// Built-in catalogue content (ad style archetypes, prompt names, taxonomy labels)
// is stored in English in src/data/* because it also feeds AI prompts. Only the
// *display* is localised, keyed by the record id. Anything not found in the
// catalogue (e.g. a style created by a user) is shown exactly as written.
export const slugify = (text) =>
    String(text || '')
        .toLowerCase()
        .replace(/[^a-z0-9]+/g, '_')
        .replace(/^_|_$/g, '');

export const categoryLabel = (category) =>
    i18n.t(`catalog:categories.${slugify(category)}`, { defaultValue: category });

export const promptCategoryLabel = (category) =>
    i18n.t(`catalog:promptCategories.${slugify(category)}`, { defaultValue: category });

export const industryLabel = (industry) =>
    i18n.t(`catalog:bestFor.${slugify(industry)}`, { defaultValue: industry });

export const styleText = (style, field) =>
    style?.id ? i18n.t(`catalog:styles.${style.id}.${field}`, { defaultValue: style[field] }) : style?.[field];

export const promptText = (prompt, field) =>
    prompt?.id ? i18n.t(`catalog:prompts.${prompt.id}.${field}`, { defaultValue: prompt[field] }) : prompt?.[field];
