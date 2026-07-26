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
- Managed config file inside a target: `xdg-config/kilo/kilo.jsonc`

The npm package also exposes the official `kilocode` bin alias, but this
module's public runtime surface follows the documented user command, `kilo`.
Managed software installation is Bun-only:
`bun add --global --exact --trust @kilocode/cli@7.4.16`.

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
python3 cli-tools/nddev_kilo_cli.py install-cli --target /absolute/kilo-target --json
python3 cli-tools/nddev_kilo_cli.py software-status --target /absolute/kilo-target --json
python3 cli-tools/nddev_kilo_cli.py update-cli --target /absolute/kilo-target --json
python3 cli-tools/nddev_kilo_cli.py switch --setup balanced --target /absolute/kilo-target --json
python3 cli-tools/nddev_kilo_cli.py restore --backup 0 --target /absolute/kilo-target --json
python3 cli-tools/nddev_kilo_cli.py remove-cli --target /absolute/kilo-target --json
python3 cli-tools/nddev_kilo_cli.py remove --target /absolute/kilo-target --json
python3 cli-tools/nddev_kilo_cli.py launch --target /absolute/kilo-target --timeout-seconds 3600 -- "implement the task"
```

`launch` requires a clean managed setup and current target-owned CLI software,
then releases the target lock before executing `/absolute/kilo-target/bin/kilo
run`. Only the `full-auto` setup receives the managed `--auto` flag; `safe` and
`balanced` keep native interactive permission prompts. It sets target-owned
`HOME`, `XDG_CONFIG_HOME`, `XDG_DATA_HOME`,
`XDG_STATE_HOME`, `XDG_CACHE_HOME`, `TMPDIR`, and
`KILO_CONFIG=/absolute/kilo-target/xdg-config/kilo/kilo.jsonc`; it does not
inherit provider credential variables or use `PATH` to find Kilo. User-supplied
launch arguments cannot override managed config, home, permission, auto, or
working-scope controls, including model or agent selection, session resume,
attached files, remote attach, share, and permission-bypass flags.

## Safety

The manager requires an explicit absolute target, rejects target symlinks and
managed symlink or hard-link paths, validates target-owned software symlinks,
bounds reads and installed tree scans, records target-bound stamps, rotates ten
target-bound backup slots, detects managed drift before switch and remove
operations, bounds launch processes, and rolls back target writes on mutation
failure.
