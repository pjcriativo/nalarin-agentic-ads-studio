import fs from 'node:fs';
import path from 'node:path';

// Vite embeds any VITE_* variable that source code references into the public
// bundle. Credentials must never travel that way; they live in the backend.
const SENSITIVE = /(TOKEN|SECRET|PASSWORD|PRIVATE|API_KEY|ANON_KEY|ACCESS_KEY)/;

const walk = (dir) =>
    fs.readdirSync(dir, { withFileTypes: true }).flatMap((entry) => {
        const full = path.join(dir, entry.name);
        if (entry.isDirectory()) return walk(full);
        return /\.(js|jsx|mjs)$/.test(entry.name) ? [full] : [];
    });

describe('public bundle hygiene', () => {
    it('never references a sensitive VITE_ variable in source or config', () => {
        const root = path.resolve(__dirname, '..');
        const files = [
            ...walk(path.join(root, 'src')),
            ...['vite.config.js', 'tailwind.config.js'].map((f) => path.join(root, f)),
        ].filter((f) => !f.endsWith('envLeak.test.js'));
        const offenders = [];
        for (const file of files) {
            const text = fs.readFileSync(file, 'utf8');
            for (const [name] of text.matchAll(/VITE_[A-Z0-9_]+/g)) {
                if (SENSITIVE.test(name)) offenders.push(`${path.relative(root, file)}: ${name}`);
            }
        }
        expect(offenders).toEqual([]);
    });
});
