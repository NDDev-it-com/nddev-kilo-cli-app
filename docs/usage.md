# Kilo Code CLI Setup Manager

`nddev-kilo-cli-app` manages one public Kilo setup, `nddev-builder`, in an
explicit target directory. Permission posture is selected with `full-auto` or
`safe` profiles.

Managed setup files include the target Kilo config, `AGENTS.md`, builder
instructions, a progressive Agent Skill toolkit, native agent and command
markdown files, and a target-local local-file plugin.

Lifecycle commands:

```bash
python3 cli-tools/nddev_kilo_cli.py list --json
python3 cli-tools/nddev_kilo_cli.py status --target /absolute/kilo-target --json
python3 cli-tools/nddev_kilo_cli.py plan --target /absolute/kilo-target --json
python3 cli-tools/nddev_kilo_cli.py install --target /absolute/kilo-target --json
python3 cli-tools/nddev_kilo_cli.py switch --profile safe --target /absolute/kilo-target --json
python3 cli-tools/nddev_kilo_cli.py migrate --profile safe --target /absolute/kilo-target --json
python3 cli-tools/nddev_kilo_cli.py install-cli --target /absolute/kilo-target --json
python3 cli-tools/nddev_kilo_cli.py software-status --target /absolute/kilo-target --json
python3 cli-tools/nddev_kilo_cli.py update-cli --target /absolute/kilo-target --json
python3 cli-tools/nddev_kilo_cli.py restore --backup 0 --target /absolute/kilo-target --json
python3 cli-tools/nddev_kilo_cli.py remove-cli --target /absolute/kilo-target --json
python3 cli-tools/nddev_kilo_cli.py remove --target /absolute/kilo-target --json
```

Use `launch` only against an already managed target:

```bash
python3 cli-tools/nddev_kilo_cli.py launch --target /absolute/kilo-target --timeout-seconds 3600 -- "inspect this repository"
```

The child process receives isolated runtime paths and
`KILO_CONFIG=/absolute/kilo-target/xdg-config/kilo/kilo.jsonc`. The manager
requires target-owned `bin/kilo`, validates each runtime HOME/TMPDIR/XDG
directory as a real target-owned 0700 directory without symlink components,
holds a persistent target-internal `fcntl.flock` lock file until the child exits
or timeout cleanup finishes, and keeps the lock parent plus verified
wrapper/native/resource parent directories traversable but non-writable while
the child runs. It revalidates wrapper and native executable inode and digest
immediately before child start, forwards the child exit code, and returns `124`
if the bounded launch timeout expires. This is a write-protected verified-path
handoff under the no-sandbox same-UID boundary, not portable fd execution, and
it does not claim resistance to deliberate same-UID chmod. Lifecycle mutations
fail while launch is running. The default `full-auto` profile uses
`kilo run --auto`; `safe` uses `kilo run` without `--auto`.

User child arguments that try to override managed config, home, permission,
auto, sandbox, working scope, agent, model, session, attached files, remote
attach, share, or permission-bypass flags are rejected.

`remove` deletes every builder-owned path in the code-owned managed file set,
removes the managed stamp, preserves parseable unmanaged config keys, and
preserves unmanaged files. Commented unmanaged JSONC still fails closed instead
of being rewritten.
