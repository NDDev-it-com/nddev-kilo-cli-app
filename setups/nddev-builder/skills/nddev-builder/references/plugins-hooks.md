# Plugin And Hook Workflow

Kilo has a native plugin system. This setup activates a target-local file plugin
with config spec `./nddev-builder-plugin.js`, resolved relative to
`xdg-config/kilo/kilo.jsonc`.

Allowed plugin behavior:

- Default export object with id `nddev-builder` and `server`.
- No imports.
- No external packages.
- Only `experimental.session.compacting` to add deterministic builder context.

Forbidden plugin behavior:

- Permission auto-approval or denial.
- Sandbox mutation.
- Auth or provider registration.
- Network access.
- MCP or server emulation.
- Tool registration unless the public contract is explicitly revised.
