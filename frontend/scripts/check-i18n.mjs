#!/usr/bin/env node
/**
 * i18n consistency check (no network, no deps).
 *  1. en-US and pt-BR have exactly the same keys per namespace (plural suffixes
 *     _one/_other/... are folded so languages may differ in plural categories).
 *  2. {{placeholders}} match between languages for every key.
 *  3. Every statically-written t('key') / t('ns:key') / i18n.t('ns:key') used in
 *     src/ resolves to an existing key (namespace is taken from the nearest
 *     useTranslation('ns') in the same file, or from an explicit prefix).
 *  4. pt-BR values identical to en-US are listed (informational; some are fine:
 *     acronyms, brand names).
 * Exit code 1 on any error from 1-3.
 */
import fs from 'node:fs';
import path from 'node:path';
import { fileURLToPath } from 'node:url';

const root = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '..');
const localesDir = path.join(root, 'src/i18n/locales');
const LANGS = ['en-US', 'pt-BR'];
const PLURAL = /_(zero|one|two|few|many|other)$/;

const load = (lng) => {
    const out = {};
    for (const f of fs.readdirSync(path.join(localesDir, lng))) {
        if (f.endsWith('.json')) out[f.replace('.json', '')] = JSON.parse(fs.readFileSync(path.join(localesDir, lng, f), 'utf8'));
    }
    return out;
};

const flatten = (obj, prefix = '', acc = {}) => {
    for (const [k, v] of Object.entries(obj)) {
        const key = prefix ? `${prefix}.${k}` : k;
        if (v && typeof v === 'object' && !Array.isArray(v)) flatten(v, key, acc);
        else if (Array.isArray(v)) v.forEach((item, i) => (typeof item === 'object' ? flatten(item, `${key}.${i}`, acc) : (acc[`${key}.${i}`] = item)));
        else acc[key] = v;
    }
    return acc;
};
const fold = (key) => key.replace(PLURAL, '');
const placeholders = (s) => [...String(s).matchAll(/\{\{\s*([\w.]+)\s*\}\}/g)].map((m) => m[1]).filter((p) => p !== 'count' || true).sort().join(',');

const data = Object.fromEntries(LANGS.map((l) => [l, load(l)]));
const errors = [];
const warnings = [];

// 1 + 2: parity
const nsAll = new Set(LANGS.flatMap((l) => Object.keys(data[l])));
for (const ns of nsAll) {
    const flats = LANGS.map((l) => (data[l][ns] ? flatten(data[l][ns]) : null));
    if (flats.some((f) => !f)) { errors.push(`namespace "${ns}" missing in ${LANGS[flats.findIndex((f) => !f)]}`); continue; }
    const folded = flats.map((f) => new Map(Object.entries(f).map(([k, v]) => [fold(k), [k, v]])));
    for (const key of new Set(folded.flatMap((m) => [...m.keys()]))) {
        const present = folded.map((m) => m.get(key));
        if (present.some((p) => !p)) { errors.push(`${ns}:${key} missing in ${LANGS[present.findIndex((p) => !p)]}`); continue; }
        // placeholder parity (ignore {{count}} differences caused by plural forms)
        const sets = present.map(([, v]) => placeholders(v).split(',').filter((p) => p && p !== 'count').join(','));
        if (new Set(sets).size > 1) errors.push(`${ns}:${key} placeholder mismatch (${sets.join(' vs ')})`);
        const [en, pt] = present.map((p) => p[1]);
        if (typeof en === 'string' && en === pt && /[A-Za-z]{4,}/.test(en) && !/^(https?:|[A-Z0-9 /+_\-.:]+$)/.test(en)) warnings.push(`${ns}:${key} identical in both languages: ${JSON.stringify(en).slice(0, 70)}`);
        if (typeof pt === 'string' && pt.trim() === '') errors.push(`${ns}:${key} empty in pt-BR`);
    }
}

// 3: usage check
const en = Object.fromEntries(Object.entries(data['en-US']).map(([ns, d]) => [ns, flatten(d)]));
const has = (ns, key) => {
    const f = en[ns];
    if (!f) return false;
    if (key in f) return true;
    return ['zero', 'one', 'two', 'few', 'many', 'other'].some((s) => `${key}_${s}` in f) || Object.keys(f).some((k) => k.startsWith(`${key}.`));
};
const walk = (dir, acc = []) => {
    for (const e of fs.readdirSync(dir, { withFileTypes: true })) {
        const p = path.join(dir, e.name);
        if (e.isDirectory()) { if (e.name !== 'i18n' && e.name !== '__tests__' && e.name !== 'node_modules') walk(p, acc); }
        else if (/\.(jsx?|tsx?)$/.test(e.name) && !/\.test\./.test(e.name)) acc.push(p);
    }
    return acc;
};
let used = 0;
for (const file of walk(path.join(root, 'src'))) {
    const src = fs.readFileSync(file, 'utf8');
    const rel = path.relative(root, file);
    // namespaces declared in this file, in order of appearance
    const hooks = [...src.matchAll(/useTranslation\(\s*['"]([\w-]+)['"]\s*\)/g)].map((m) => ({ idx: m.index, ns: m.groups?.ns ?? m[1] }));
    const defaultNs = hooks.length ? hooks[0].ns : 'common';
    for (const m of src.matchAll(/(?<![\w.$])(?:i18n\.)?t\(\s*(['"])([\w.:-]+)\1/g)) {
        let key = m[2];
        let ns;
        if (key.includes(':')) [ns, key] = key.split(':');
        else {
            const before = hooks.filter((h) => h.idx < m.index);
            ns = before.length ? before[before.length - 1].ns : defaultNs;
        }
        used++;
        if (!has(ns, key)) errors.push(`${rel}: key not found -> ${ns}:${key}`);
    }
    for (const m of src.matchAll(/i18nKey=["']([\w.:-]+)["'](?:[^>]*?ns=["']([\w-]+)["'])?/g)) {
        const [ns, key] = m[1].includes(':') ? m[1].split(':') : [m[2] || defaultNs, m[1]];
        used++;
        if (!has(ns, key)) errors.push(`${rel}: Trans key not found -> ${ns}:${key}`);
    }
}

console.log(`i18n check: ${nsAll.size} namespaces, ${Object.values(en).reduce((n, f) => n + Object.keys(f).length, 0)} en-US keys, ${used} static usages`);
if (warnings.length) {
    console.log(`\n${warnings.length} value(s) identical in both languages (review: acronyms/brand names are OK):`);
    warnings.slice(0, 40).forEach((w) => console.log('  ~', w));
}
if (errors.length) {
    console.error(`\n${errors.length} error(s):`);
    errors.forEach((e) => console.error('  x', e));
    process.exit(1);
}
console.log('OK - no missing keys, parity and placeholders consistent.');
