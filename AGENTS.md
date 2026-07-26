# nddev-kilo-cli-app

This public repository contains Kilo Code CLI setup-manager implementation, setup
sources, public contracts, release metadata, and public documentation only.
Private tests, benchmarks, fixtures, and harness profiles belong outside this
repository.

Use only current Kilo Code CLI names and documented surfaces:

- `kilo`
- `KILO_CONFIG`
- `~/.config/kilo/kilo.jsonc`
- `agent.<name>`
- `skills.paths`
- `command.<name>`
- `permission.*`
- `sandbox.*`

Do not use VS Code extension surfaces as this CLI module's runtime contract.
Do not invent marketplace or plugin formats for Kilo Code CLI; the public
contract must keep unsupported native projections explicit and null.
