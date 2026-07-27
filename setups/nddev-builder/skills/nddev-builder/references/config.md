# Kilo Config Workflow

Kilo reads JSONC config from global, project, explicit `KILO_CONFIG`, config
directory, config content, managed config, and managed-preference layers. This
setup uses an explicit target-owned `KILO_CONFIG` path and sanitized runtime
environment so live user config is not consulted.

For managed config:

- Write strict JSON, which is valid JSONC.
- Keep `$schema` in the target config for editor support.
- Use only verified top-level keys: `default_agent`, `instructions`, `skills`,
  `plugin`, `permission`, `sandbox`, and `experimental`.
- Treat unknown top-level keys as invalid because Kilo's native schema rejects
  them.
- Preserve unmanaged JSON keys when they are parseable as JSON; fail closed
  instead of rewriting commented or otherwise non-canonical unmanaged JSONC.
- Let the manager source and baseline own current versions, integrity values,
  file lists, and parser facts.
