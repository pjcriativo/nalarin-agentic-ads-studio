# Installed design skills (project scope)

Installed 2026-11-22 by copying the skill folders from shallow clones of the official repositories (no installer
scripts were executed; scripts were inspected first: stdlib-only Python, no network/subprocess/eval).

| Skill (dir) | Source | Commit | License | Notes |
|---|---|---|---|---|
| `ui-ux-pro-max` | https://github.com/nextlevelbuilder/ui-ux-pro-max-skill (`.claude/skills/ui-ux-pro-max`, v2.15.0) | `6ec2bb58334ca3014573c4cbd4834ee3a40afd9d` | MIT | `scripts/tests/` omitted. `${CLAUDE_PLUGIN_ROOT}/.claude/skills/ui-ux-pro-max` in SKILL.md replaced by the project-relative `.claude/skills/ui-ux-pro-max`. Search is read-only; `--persist` is forbidden by the director. |
| `frontend-design` | https://github.com/anthropics/skills (`skills/frontend-design`) | `e4bb5d960d87126ff3b3a8af1a104b7db67560ba` | Apache-2.0 (LICENSE.txt) | Unmodified. |
| `web-design-guidelines` | https://github.com/vercel-labs/agent-skills (`skills/web-design-guidelines`, v1.1.0) | `5a37a6329fbad3666c3207392a6a7c0110e92efb` | see upstream repo (no LICENSE file in the skill folder) | Unmodified. `references/command.md` is bundled; SHA-256 (LF-normalised) `d246b026...e0234` matches the pinned value in SKILL.md. No network fetch at run time. |
| `react-best-practices` | https://github.com/vercel-labs/agent-skills (`skills/react-best-practices`, v1.0.0, frontmatter name `vercel-react-best-practices`) | `5a37a6329fbad3666c3207392a6a7c0110e92efb` | MIT (SKILL.md frontmatter) | Unmodified. Next.js-specific rules do not apply to this Vite app; the director routes only the React ones. |
| `frontend-design-director` | Written for this project | - | - | Orchestrator, `user-invocable: false`, model invocation enabled. |

To update: re-clone the source, diff against the folder, re-apply the single path edit listed above.
