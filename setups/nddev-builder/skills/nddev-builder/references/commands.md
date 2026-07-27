# Command Adapter Workflow

Use native regular-file command adapters under:

- `xdg-config/kilo/command/nddev-builder.md`
- `xdg-config/kilo/command/nddev-check.md`
- `xdg-config/kilo/command/nddev-migrate.md`

Each command has frontmatter metadata and a deterministic prompt body. Commands
must route to the `nddev-builder` agent and must not start servers, fetch remote
content, mutate auth, or bypass permission profiles.
