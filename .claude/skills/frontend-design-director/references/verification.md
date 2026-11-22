# Verification routine

## Static / unit
```bash
cd frontend
npm run lint          # baseline after i18n commit: 82 errors / 26 warnings; no NEW problems allowed
npm run build
npm run test:unit     # vitest (includes i18n tests + route sweep)
npm run test:i18n     # key parity/usage + PT-BR/EN-US route sweep
```

## Visual fidelity, responsiveness, regression
`scripts/visual-regression.mjs` needs a local Edge/Chrome and `puppeteer-core pixelmatch@5 pngjs`, installed OUTSIDE the repo:
```bash
mkdir "$TEMP/vr" && cd "$TEMP/vr" && npm init -y && npm i puppeteer-core pixelmatch@5 pngjs
# base = a git worktree of the reference commit, served by vite on another port with its own cacheDir
PUPPETEER_DIR="$TEMP/vr" BASE_URL=http://localhost:5301 NEW_URL=http://localhost:5302 OUT="$TEMP/vr/out" \
  ROUTES="/,/brands,/settings" VIEWPORTS="1440x900,820x1000,390x844" LANG_UI=en-US \
  node .claude/skills/frontend-design-director/scripts/visual-regression.mjs
```
* The backend is mocked by request interception (`http://localhost:8000/api/v1/*`). The real backend is never called.
* The language switcher is hidden in both builds, so pure technical/i18n work must give `0` differing pixels.
* Do NOT run `npm ci --dry-run` (deletes node_modules on this npm version); never kill browsers/dev servers by image name.

## Checklist (UI changes)
- [ ] Same tokens/classes as neighbours; no new colors/fonts without approval
- [ ] EN + PT-BR strings, plurals, no truncation in PT-BR (longer text)
- [ ] Keyboard: tab order, visible focus, Esc closes modals; icon buttons have `aria-label`
- [ ] Empty / loading / error states; toasts instead of `alert()`
- [ ] 390 / 820 / 1440px, no horizontal page scroll, touch targets >= 44px
- [ ] Contrast >= 4.5:1, `prefers-reduced-motion` respected
