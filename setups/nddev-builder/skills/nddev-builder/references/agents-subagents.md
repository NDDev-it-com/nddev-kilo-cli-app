# Agents And Subagents Workflow

Use native Kilo agent markdown files under the target config directory:

- `xdg-config/kilo/agent/nddev-builder.md`
- `xdg-config/kilo/agent/nddev-reviewer.md`

The builder agent is available in all modes. The reviewer is a subagent for
focused reviews. Do not rely on unverified agent-manager requirements or
marketplace behavior. Keep model, provider, and auth selection user-owned unless
the module contract explicitly adds a managed field.
