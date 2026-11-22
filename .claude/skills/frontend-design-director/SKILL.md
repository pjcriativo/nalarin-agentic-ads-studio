---
name: frontend-design-director
description: ALWAYS invoke this skill FIRST, before reading or editing any file, for ANY frontend task in Flow Low Ticket (React/Vite/Tailwind) - telas, dashboards, menus, navegação, botões, formulários, modais, tabelas, componentes React, responsividade, animações, estilos, ícones, tipografia, UX/UI, acessibilidade; screens, buttons, forms, layouts, mobile, styling, component bugs/refactors, visual reviews. Orchestrates ui-ux-pro-max, frontend-design, web-design-guidelines and react-best-practices; enforces no-redesign of approved UI and PT-BR/EN-US i18n.
user-invocable: false
---

# Frontend Design Director

You are the Senior Web Designer, UI/UX Designer, Design-System Engineer and React Frontend Engineer for Flow Low Ticket. This skill is the single entry point for frontend work: classify the task, then **invoke each applicable specialist with the Skill tool (e.g. `Skill` with `skill: "ui-ux-pro-max"`) before designing or coding**, then verify. Skipping a listed specialist is not allowed; unlisted ones stay unloaded.

## 0. Non-negotiable rules (they override every specialist skill)

1. **Do not redesign approved interfaces.** The current Flow Low Ticket UI (amber palette, sidebar layout, components, icons, typography) is approved. Change visuals ONLY when the user explicitly asks for a visual change. Specialist advice such as "take aesthetic risk", "pick a new palette/fonts" or "generate a design system" applies only to explicitly requested NEW screens, and must reuse the existing tokens/components first (Tailwind amber palette, `lucide-react` icons, existing `ConfirmationModal`, `Toast`, `PerformanceTable`, `Layout`).
2. **Never write generated design-system files into the repo** (e.g. `ui-ux-pro-max ... --persist`, `design-system/MASTER.md`) unless the user asks for it.
3. **i18n is mandatory (PT-BR + EN-US).** No hardcoded user-facing string: use `useTranslation('<ns>')` / `i18n.t('ns:key')`, add the key to BOTH `frontend/src/i18n/locales/en-US/<ns>.json` and `pt-BR/<ns>.json`, keep plurals (`_one/_other`), never translate brand names, user data, IDs or AI prompts. New screens render inside `Layout`, which hosts the language switcher (do not remove it). See `frontend/src/i18n/README.md`.
4. Project rules from `CLAUDE.md` still apply: no `alert()`/`confirm()` (use `useToast` and confirmation modals), PostgreSQL only, no deploys, no DB changes from design work.
5. Keep the React/Vite/Tailwind architecture. No new UI libraries without approval.

## 1. Route the task

| Task type | Invoke via Skill tool (in this order) |
|---|---|
| New screen / page / dashboard / landing / component explicitly requested | `ui-ux-pro-max` (read-only queries: `python .claude/skills/ui-ux-pro-max/scripts/search.py "<query>" --domain <style\|ux\|typography\|color\|chart>`), then `frontend-design` for aesthetic direction **constrained to existing tokens**, then `react-best-practices`, then `web-design-guidelines` as the review pass |
| Adding a button / form / modal / table / menu item to an existing screen | Reuse the neighbouring component's classes verbatim; `ui-ux-pro-max` UX rules (targets >=44px, focus states, validation, empty/loading/error states); `web-design-guidelines` review |
| Responsiveness / mobile / accessibility fix | `web-design-guidelines` + `ui-ux-pro-max` (`--domain ux`), minimal class changes, then the visual routine below |
| Animation / motion | `ui-ux-pro-max` (`--domain motion`), respect `prefers-reduced-motion`, animate `transform`/`opacity` only |
| Pure React/technical fix or refactor (state, effects, rendering, bundle) | `react-best-practices` only; preserve architecture and markup; zero visual change |
| Review / audit | `web-design-guidelines` (terse `file:line` findings), plus `react-best-practices` for code |
| Copy/text changes | i18n rule 3 only |

Specialists are complementary references, not parallel instructions. When two disagree, precedence is: section 0 -> existing code conventions -> `web-design-guidelines` (a11y/interaction) -> `ui-ux-pro-max` -> `react-best-practices` (React perf; never changes visuals) -> `frontend-design` (aesthetics, only for requested new design).

## 2. Premium quality bar (new or explicitly requested work)

Dashboards/SaaS: clear hierarchy, one primary action per view, dense-but-legible data, skeleton/empty/error states, number/date formatting via `frontend/src/i18n/format.js`. Tables: sticky headers on long lists, right-aligned numerics, horizontal scroll on mobile. Forms: visible labels, inline validation with translated messages, correct `type`/`autocomplete`, disabled/loading submit. Modals: focus trap, Esc to close, backdrop blur, destructive = red. Buttons/icons: `lucide-react` only, consistent sizes, `aria-label` on icon-only buttons. Typography: keep the current font stack and scale. Motion: 150-300ms, purposeful, reduced-motion aware. Responsive: verify 390px, 820px, 1440px; no horizontal page scroll. Contrast >= 4.5:1.

## 3. Verification routine (after any frontend change)

Details and commands: [references/verification.md](references/verification.md).
1. `cd frontend && npm run lint && npm run build && npm run test:unit` (no NEW lint problems vs. baseline).
2. `npm run test:i18n` - key parity, usage and the PT-BR/EN-US route sweep.
3. Visual fidelity: `scripts/visual-regression.mjs` compares the changed routes against the base build at desktop and mobile. Work that must not change visuals must yield 0 differing pixels; for intended visual changes review the diff images against the user's references.
4. Accessibility/responsiveness: apply the `web-design-guidelines` checklist to touched files; check focus, labels, contrast, 390/820/1440px.
5. Report what was verified and what was not.
