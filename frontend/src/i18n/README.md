# Internationalization (i18n)

Languages: **pt-BR** (Português do Brasil) and **en-US** (English). Stack: `i18next`,
`react-i18next`, `i18next-browser-languagedetector`. Everything is bundled statically —
there is no runtime/automatic translation and no external service.

## How it works

| Piece | File |
|---|---|
| Setup, detection, persistence | [`index.js`](./index.js) |
| Flag switcher (🇧🇷 / 🇺🇸, top-right of the header) | [`../components/LanguageSwitcher.jsx`](../components/LanguageSwitcher.jsx) |
| Backend error mapping | [`backendErrors.js`](./backendErrors.js) |
| Locale-aware dates/numbers, ad-size labels | [`format.js`](./format.js) |
| Built-in ad-style / prompt catalogue labels | [`catalog.js`](./catalog.js) |
| Strings | `locales/<lng>/<namespace>.json` |

* **Detection:** `localStorage["app_language"]` first; the browser language is used only on the
  first visit (any `pt*` → `pt-BR`, everything else → `en-US`) and is then persisted.
* **Switching:** `i18n.changeLanguage()` re-renders every `useTranslation` consumer — no reload.
* **Fallback:** a key missing in one language falls back to `en-US`; a key missing everywhere renders the key.

## Namespaces (one JSON per module)

`common` · `layout` (sidebar/header) · `auth` (login, register, route guards) · `public` (about/privacy/terms) ·
`dashboard` (overview, dashboard, reporting, performance table, optimisation panel) ·
`brands` (brands, products, customer profiles + forms) · `creatives` (image/video ads, wizards, selectors) ·
`campaigns` (Facebook/Google/TikTok campaign flows and wizard steps) · `research` ·
`library` (winning ads, generated ads, ad remix) · `settings` (settings, user management) ·
`errors` (error boundary, backend error messages) · `validation` · `catalog`.

## Adding / changing text

```jsx
const { t } = useTranslation('brands');
<h1>{t('brands.title')}</h1>
showSuccess(t('productForm.saved'));
t('research.uniqueAds', { count })      // plurals: keys `uniqueAds_one` / `uniqueAds_other`
<Trans i18nKey="generatedAds.deleteBundleConfirm" ns="library" count={n} components={{ strong: <strong /> }} />
```

* Outside React use `import i18n from '../i18n'` and `i18n.t('ns:key')`.
* Add the key to **both** `locales/en-US/<ns>.json` and `locales/pt-BR/<ns>.json`.
* Run `npm run i18n:check` — it fails on missing keys, `t('…')` calls that point to unknown keys, and
  `{{placeholder}}` mismatches between languages.
* `npm run test:i18n` also renders every route in both languages and fails on missing keys, raw keys,
  un-interpolated placeholders or leftover English in pt-BR.

## What is deliberately NOT translated

Brand names (Google Ads, Meta, TikTok, Facebook, Nano Banana Pro…), user-created content (brand/product/profile
names, ad copy, custom styles/prompts), data returned by ad platforms and the backend (unknown messages are shown
as received; only an allow-list of known backend messages is mapped in `backendErrors.js`), technical identifiers
(IDs, enum values such as `OUTCOME_SALES`, stored ad-size names, action ids), AI prompt templates sent to the models,
and env-configured white-label strings (`VITE_APP_*`).
