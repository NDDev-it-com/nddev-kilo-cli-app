# Kilo Code CLI Setup Manager

`nddev-kilo-cli-app` manages one public Kilo setup, `nddev-builder`, in an
explicit target directory. Permission posture is selected with manager profiles;
query the current setup/profile contract with `list --json`.

Managed setup content includes Kilo-native instructions, a progressive Agent
Skill toolkit, agents, commands, and a target-local local-file plugin. The exact
file inventory and native config paths are owned by
`setups/nddev-builder/setup.json` and `cli-tools/nddev_kilo_cli.py`; use
`plan --json` and `status --json` for the current machine-readable view.

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

Launch provides an isolated manager-controlled runtime for the child process,
requires current target-owned CLI software, holds lifecycle locks through child
completion or timeout cleanup, forwards the child exit code, and returns the
documented timeout code when the bounded launch expires. It also keeps the
runtime state Kilo needs writable while denying concurrent lifecycle mutations.

Exact runtime environment variables, directory layout, lock structure, lock
ordering, executable handoff, child argument filtering, timeout behavior, and
profile-to-native-mode mapping are owned by `cli-tools/nddev_kilo_cli.py`,
`profiles/`, and `config/nddev-contract.json`. Inspect live target state with
`status --json` and installed CLI provenance with `software-status --json`.

The launch boundary is a manager-verified path handoff under a no-sandbox
same-UID threat model. It does not claim portable file-descriptor execution or
resistance to deliberate same-UID tampering outside the manager-enforced
filesystem boundary.

`remove` returns a managed target toward unmanaged state by removing
builder-owned artifacts and preserving non-managed state where the manager can
parse it. The exact managed-file set, config preservation rules, and fail-closed
JSONC boundary are owned by `cli-tools/nddev_kilo_cli.py` and
`setups/nddev-builder/setup.json`.
