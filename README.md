# nddev-kilo-cli-app

NDDev Kilo Code CLI setup manager.

This module installs one complete Kilo setup into an explicit isolated target.
It never defaults to the caller's live `~/.config/kilo` state and never launches
Kilo during install, switch, restore, or remove operations.

## Current Kilo Identity

- Product docs: <https://kilo.ai/docs/code-with-ai/platforms/cli>
- Official source: <https://github.com/Kilo-Org/kilocode>
- Current CLI package: `@kilocode/cli@7.4.16`
- Runtime command used by this module: `kilo`
- Managed config file inside a target: `config.json`

The npm package also exposes a secondary package bin, but this module's public
runtime surface follows the documented user command, `kilo`.

## Setups

- `safe`: sandbox enabled, sandbox network denied, shell execution asks.
- `balanced`: sandbox enabled, sandbox network denied, narrow development checks
  auto-allowed and other shell commands ask.
- `full-auto`: autonomous native allow profile with sandbox network allowed.

All setups enable `nddev-builder` by default through Kilo's native `agent`,
`skills.paths`, and `command` config surfaces. No marketplace projection is
declared because an official Kilo CLI marketplace format is not proven here.

## Usage

```bash
python3 cli-tools/nddev_kilo_cli.py list --json
python3 cli-tools/nddev_kilo_cli.py plan --setup safe --target /absolute/kilo-target --json
python3 cli-tools/nddev_kilo_cli.py install --setup safe --target /absolute/kilo-target --json
python3 cli-tools/nddev_kilo_cli.py switch --setup balanced --target /absolute/kilo-target --json
python3 cli-tools/nddev_kilo_cli.py restore --backup 0 --target /absolute/kilo-target --json
python3 cli-tools/nddev_kilo_cli.py remove --target /absolute/kilo-target --json
python3 cli-tools/nddev_kilo_cli.py launch --target /absolute/kilo-target --timeout-seconds 3600 -- "implement the task"
```

`launch` sets an isolated child `HOME`, XDG runtime directories, and
`KILO_CONFIG=/absolute/kilo-target/config.json`; it strips provider credential
environment variables before executing `kilo run --auto`, forwards the child
exit code, and returns `124` on timeout.

## Safety

The manager requires an explicit absolute target, rejects target symlinks and
managed symlink or hard-link paths, bounds reads, records target-bound stamps,
rotates ten target-bound backup slots, detects managed drift before switch and
remove operations, bounds launch processes, and rolls back target writes on
mutation failure.
