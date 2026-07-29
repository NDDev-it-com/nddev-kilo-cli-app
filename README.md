# nddev-kilo-cli-app

NDDev Kilo Code CLI setup manager.

This module installs the public `nddev-builder` Kilo setup into an explicit
isolated target. Permission posture is selected separately with manager
profiles; query the current setup and profile contract with `list --json`.

The manager never defaults to the caller's live `~/.config/kilo`, live npm
prefix, live npm config, provider credentials, or auth stores.

## Current Kilo Identity

- Product docs: <https://kilo.ai/docs/code-with-ai/platforms/cli>
- Official source: <https://github.com/Kilo-Org/kilocode>
- Official install channel: `npm install -g @kilocode/cli`
- Runtime command used by this module: `kilo`

The managed package version, native package matrix, registry integrity, runtime
config location, and launch binary selection are source-owned by
`references/kilo-cli-baseline.json`, `config/nddev-contract.json`, and
`cli-tools/nddev_kilo_cli.py`. Use `software-status --json` and
`status --json` for target-specific state.

Production install and launch support is limited to macOS and Ubuntu glibc
desktop/server hosts. The exact host IDs, unsupported categories, official
floor observation, and upstream optional-package mapping are owned by
`config/nddev-contract.json` and `references/kilo-cli-baseline.json`.

## Setup And Profiles

This public module currently ships the `nddev-builder` setup. The exact active
setup ids, profile ids, legacy classifications, native permission modes,
sandbox/network controls, and launch argv are owned by
`cli-tools/nddev_kilo_cli.py`, `profiles/`, and `config/nddev-contract.json`;
inspect them through `list --json` before switching or migrating a target.

## Builder Content

The setup ships target-local native Kilo builder content: AGENTS instructions,
builder guidance, a progressive Agent Skill toolkit, native agents, commands,
and a local-file plugin. The exact installed file inventory and plugin path are
owned by `setups/nddev-builder/setup.json` and `cli-tools/nddev_kilo_cli.py`;
use `plan --json` or `status --json` to inspect what a target would receive or
currently contains. `plan --json` reports the stable `changed_paths` contract
that setup mutations use; paths are reported only when their target bytes or
presence would change.

The plugin's supported purpose and boundaries are owned by the setup source and
the public contract. It is intended only for deterministic builder context
during native compaction and must not become a hidden permission, sandbox, auth,
provider, network, MCP, or tool-behavior control plane.

## Usage

The full command surface is owned by `cli-tools/nddev_kilo_cli.py`. Common
read-only and lifecycle examples:

```bash
python3 cli-tools/nddev_kilo_cli.py list --json
python3 cli-tools/nddev_kilo_cli.py plan --target /absolute/kilo-target --json
python3 cli-tools/nddev_kilo_cli.py install --target /absolute/kilo-target --json
python3 cli-tools/nddev_kilo_cli.py update --target /absolute/kilo-target --json
python3 cli-tools/nddev_kilo_cli.py software-status --target /absolute/kilo-target --json
python3 cli-tools/nddev_kilo_cli.py launch --target /absolute/kilo-target -- "implement the task"
```

`update` refreshes an already installed setup using the installed setup/profile
identity. Like other setup mutations, it uses the manager's byte-diff
postcondition contract; an identical target is a true no-op.

`install-cli` and `update-cli` maintain target-owned CLI software through the
manager's pinned official-channel install boundary. The volatile package pin,
registry proof, script policy, native package selection, wrapper/resource
contract, and drift checks are owned by `references/kilo-cli-baseline.json`,
`config/nddev-contract.json`, and `cli-tools/nddev_kilo_cli.py`; inspect the
current installed provenance with `software-status --json`.

`launch` requires a clean managed setup and current target-owned CLI software.
It holds lifecycle locks through child completion or timeout cleanup, preserves
the runtime writability Kilo needs, and denies concurrent lifecycle mutations
while the child is running. Exact lock structure, lock ordering, runtime
environment, executable handoff, child argument filtering, and timeout behavior
are owned by `cli-tools/nddev_kilo_cli.py` and summarized in
`config/nddev-contract.json`; inspect target state with `status --json` and
`software-status --json`.

The launch boundary is a manager-verified path handoff under a no-sandbox
same-UID threat model. It does not claim portable file-descriptor execution or
resistance to deliberate same-UID tampering outside the manager-enforced
filesystem boundary.

## Safety

The manager requires an explicit absolute target, isolates auth and provider
state from live user configuration, bounds reads and installed tree scans,
records target-bound ownership, backs up and rolls back mutations, detects drift
before writes, denies unmanaged or legacy launches, and preserves unmanaged
state according to the code-owned parser and managed-file set. Exact path lists,
backup layout, ownership checks, removal behavior, and migration rules are
owned by `cli-tools/nddev_kilo_cli.py`, `setups/nddev-builder/setup.json`, and
`config/nddev-contract.json`; use `plan --json` and `status --json` for the
current machine-readable view.
