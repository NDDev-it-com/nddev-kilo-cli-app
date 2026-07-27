# Install And Runtime Workflow

Use the official Kilo CLI npm channel through an isolated exact global install
of the pinned package into the target-owned npm prefix. Vendor postinstall is
part of the supported install path and must run.

The manager must:

- Use sanitized npm config, cache, prefix, and HOME.
- Generate and verify a target-owned package lock.
- Verify registry integrity before install.
- Verify installed package identity, postinstall contract, native package
  versions, tree bounds, executable mode, and `kilo --version`.
- Never use Bun for Kilo CLI installation.
- Never install into a live global npm prefix.

Runtime launch uses the target-owned `bin/kilo`, not `PATH` discovery.
