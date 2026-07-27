---
name: nddev-builder
description: Build and review Kilo setup-manager modules with native Kilo surfaces, target isolation, and progressive disclosure.
---

# NDDev Builder Skill

Use this entry skill for Kilo setup-module work. Start here, then open only the
focused reference or additional skill needed for the current artifact family.

## Routing

| Need | Read next |
|---|---|
| Config schema, JSONC, discovery paths | `references/config.md` |
| Permission profiles or sandbox posture | `references/permissions-sandbox.md` |
| Agents and subagents | `references/agents-subagents.md` |
| Agent Skills and portable toolkit layout | `references/skills.md` |
| Slash commands and regular-file adapters | `references/commands.md` |
| Local plugin and hook boundaries | `references/plugins-hooks.md` |
| MCP boundaries | `references/mcp-boundary.md` |
| Provider auth and secret isolation | `references/auth-boundary.md` |
| AGENTS.md and durable context | `references/memory-context.md` |
| Official npm install and runtime launch | `references/install-runtime.md` |
| Legacy migration and validation | `references/migration-validation.md` |

## Working Rules

- Keep public runtime code, setup payloads, contracts, release metadata, and
  public documentation in the public module.
- Keep private tests, fixtures, benchmarks, root operational skills, and durable
  memory in the private harness.
- Do not copy volatile versions, hashes, SHAs, or current release facts into
  instructions. Point to the manager source, public baseline, and build
  metadata that own those facts.
- Never emulate unsupported Kilo surfaces. Use only verified native config,
  file, plugin, command, skill, agent, permission, sandbox, MCP, auth, and
  memory behavior.
- Preserve unmanaged target state and fail closed on ambiguity.
