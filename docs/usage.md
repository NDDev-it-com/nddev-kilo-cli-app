# Kilo Code CLI Setup Manager

`nddev-kilo-cli-app` manages Kilo Code CLI setup variants in an explicit target
directory. It writes `config.json`, `instructions/nddev-builder.md`, and
`skills/nddev-builder/SKILL.md`, plus a target-bound ownership stamp.

The manager preserves unmanaged config keys and unmanaged files. A target with
unclaimed managed Kilo keys or builder paths fails closed instead of being
overwritten.

Lifecycle commands:

```bash
python3 cli-tools/nddev_kilo_cli.py list --json
python3 cli-tools/nddev_kilo_cli.py status --target /absolute/kilo-target --json
python3 cli-tools/nddev_kilo_cli.py plan --setup safe --target /absolute/kilo-target --json
python3 cli-tools/nddev_kilo_cli.py install --setup safe --target /absolute/kilo-target --json
python3 cli-tools/nddev_kilo_cli.py switch --setup balanced --target /absolute/kilo-target --json
python3 cli-tools/nddev_kilo_cli.py restore --backup 0 --target /absolute/kilo-target --json
python3 cli-tools/nddev_kilo_cli.py remove --target /absolute/kilo-target --json
```

Use `launch` only against an already managed target:

```bash
python3 cli-tools/nddev_kilo_cli.py launch --target /absolute/kilo-target --timeout-seconds 3600 -- "inspect this repository"
```

The child process receives an isolated runtime environment and
`KILO_CONFIG=/absolute/kilo-target/config.json`. The manager forwards the child
exit code and returns `124` if the bounded launch timeout expires.
