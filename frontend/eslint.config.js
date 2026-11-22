import js from '@eslint/js'
import globals from 'globals'
import reactHooks from 'eslint-plugin-react-hooks'
import reactRefresh from 'eslint-plugin-react-refresh'
import { defineConfig, globalIgnores } from 'eslint/config'

export default defineConfig([
  globalIgnores(['dist']),
  {
    files: ['**/*.{js,jsx}'],
    extends: [
      js.configs.recommended,
      reactHooks.configs.flat.recommended,
      reactRefresh.configs.vite,
    ],
    languageOptions: {
      ecmaVersion: 2020,
      globals: globals.browser,
      parserOptions: {
        ecmaVersion: 'latest',
        ecmaFeatures: { jsx: true },
        sourceType: 'module',
      },
    },
    rules: {
      // Core no-unused-vars cannot see JSX usage (<Icon />), so PascalCase
      // bindings (vars AND destructured props) are exempt, as before for vars.
      'no-unused-vars': ['error', { varsIgnorePattern: '^[A-Z_]', argsIgnorePattern: '^[A-Z_]' }],
    },
  },
  {
    // Node-executed config files.
    files: ['*.config.js', 'vite.config.js', 'tailwind.config.js', 'vitest.config.js'],
    languageOptions: { globals: globals.node },
  },
  {
    // Vitest globals (vitest.config.js sets `globals: true`) for unit tests + setup.
    files: ['**/*.test.{js,jsx}', 'src/__tests__/**/*.{js,jsx}'],
    languageOptions: {
      globals: { ...globals.browser, ...globals.node, vi: 'readonly', describe: 'readonly', it: 'readonly', test: 'readonly', expect: 'readonly', beforeEach: 'readonly', afterEach: 'readonly', beforeAll: 'readonly', afterAll: 'readonly' },
    },
  },
  {
    // Node scripts (i18n consistency check).
    files: ['scripts/**/*.mjs'],
    languageOptions: { globals: globals.node },
  },
])
