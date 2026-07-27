# Install And Runtime Workflow

Use the official Kilo CLI npm channel through an isolated exact global install
of the pinned package into the target-owned npm prefix with lifecycle scripts
disabled.

The manager must:

- Use sanitized npm config, cache, prefix, and HOME.
- Generate and verify a target-owned package lock.
- Verify registry integrity before install.
- Record the vendor postinstall contract as forbidden installer-side behavior.
- Verify installed package identity, native package versions, selected native
  package binding, tree bounds, executable modes, wrapper digest, and runtime
  resource digests without executing target software.
- Never use Bun for Kilo CLI installation.
- Never install into a live global npm prefix.

Runtime launch uses the target-owned `bin/kilo`, not `PATH` discovery. The
manager owns the exact lock, executable handoff, runtime directory, manifest,
and status contract in `cli-tools/nddev_kilo_cli.py`,
`build/manifest.json`, and `config/nddev-contract.json`. Do not copy the
current runtime pins or package matrix into skill prose.
