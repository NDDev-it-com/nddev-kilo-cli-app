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

Runtime launch uses the target-owned `bin/kilo`, not `PATH` discovery. Runtime
HOME/TMPDIR/XDG directories must stay real target-owned 0700 directories without
symlink components.
