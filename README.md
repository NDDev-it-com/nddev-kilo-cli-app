# nddev-kilo-cli-app

NDDev Kilo Code CLI setup manager.

This module installs the public `nddev-builder` Kilo setup into an explicit
isolated target. Permission posture is selected separately with profiles:
`full-auto` is the default, and `safe` is available for human-gated work.

The manager never defaults to the caller's live `~/.config/kilo`, live npm
prefix, live npm config, provider credentials, or auth stores.

## Current Kilo Identity

- Product docs: <https://kilo.ai/docs/code-with-ai/platforms/cli>
- Official source: <https://github.com/Kilo-Org/kilocode>
- Official install channel: `npm install -g @kilocode/cli`
- Managed CLI package: `@kilocode/cli@7.4.16`
- Runtime command used by this module: `kilo`
- Managed config file inside a target: `xdg-config/kilo/kilo.jsonc`

The npm package also exposes the official `kilocode` bin alias, but this module
uses the documented `kilo` command.

## Setup And Profiles

- Setup: `nddev-builder`
- Default profile: `full-auto`
- Safe profile: `safe`
- Legacy setup ids: `safe`, `balanced`, and `full-auto` may be read for
  status, migration, restore, or removal, but never launched.

`full-auto` uses native `permission: "allow"`, sandbox off, unrestricted
network, and managed `kilo run --auto`.

`safe` uses native ask/deny permissions, sandbox on, sandbox network denied, and
no `--auto`.

## Builder Content

The setup ships target-local native Kilo surfaces:

- `AGENTS.md`
- `instructions/nddev-builder.md`
- Agent markdown files under `xdg-config/kilo/agent/`
- Command markdown files under `xdg-config/kilo/command/`
- `skills.paths` pointing at a progressive-disclosure Skill toolkit
- A local file plugin at `xdg-config/kilo/nddev-builder-plugin.js`

The plugin has no imports and only adds deterministic builder context during
native compaction. It does not mutate permissions, sandbox, auth, provider,
network, MCP, or tool behavior.

## Usage

```bash
python3 cli-tools/nddev_kilo_cli.py list --json
python3 cli-tools/nddev_kilo_cli.py plan --target /absolute/kilo-target --json
python3 cli-tools/nddev_kilo_cli.py install --target /absolute/kilo-target --json
python3 cli-tools/nddev_kilo_cli.py switch --profile safe --target /absolute/kilo-target --json
python3 cli-tools/nddev_kilo_cli.py migrate --profile full-auto --target /absolute/kilo-target --json
python3 cli-tools/nddev_kilo_cli.py install-cli --target /absolute/kilo-target --json
python3 cli-tools/nddev_kilo_cli.py software-status --target /absolute/kilo-target --json
python3 cli-tools/nddev_kilo_cli.py update-cli --target /absolute/kilo-target --json
python3 cli-tools/nddev_kilo_cli.py restore --backup 0 --target /absolute/kilo-target --json
python3 cli-tools/nddev_kilo_cli.py remove-cli --target /absolute/kilo-target --json
python3 cli-tools/nddev_kilo_cli.py remove --target /absolute/kilo-target --json
python3 cli-tools/nddev_kilo_cli.py launch --target /absolute/kilo-target --timeout-seconds 3600 -- "implement the task"
```

`install-cli` and `update-cli` run an isolated exact npm global install into the
target-owned prefix with lifecycle scripts disabled. The manager records Kilo's
official postinstall as a forbidden installer-side boundary, verifies registry
and lock metadata, binds a deterministic selected native package directly to
its `bin/kilo`, writes a target-owned wrapper, and records wrapper, native
binary, and resource digests in the software manifest.

`launch` requires a clean active setup and current target-owned CLI software,
then holds the target lifecycle lock while `/absolute/kilo-target/bin/kilo run`
executes and through timeout cleanup. It validates runtime HOME/TMPDIR/XDG
directories as real target-owned 0700 directories, rejects symlink components,
and revalidates wrapper and native executable inode and digest immediately
before child start. Lifecycle mutations fail while launch is running. Only the
`full-auto` profile receives the managed `--auto` flag.

## Safety

The manager requires an explicit absolute target, rejects target symlinks and
managed symlink or hard-link paths, validates target-owned software symlinks,
bounds reads and installed tree scans, records target-bound stamps, rotates ten
target-bound backup slots, detects managed drift before mutation, denies legacy
launches, removes every builder-owned path from the code-owned managed file
set, preserves parseable unmanaged config keys and unmanaged files, bounds child
processes, and rolls back target writes on mutation failure.
