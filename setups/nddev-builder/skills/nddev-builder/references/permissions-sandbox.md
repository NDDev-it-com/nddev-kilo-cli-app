# Permission And Sandbox Workflow

Permission profiles are orthogonal to the content setup.

`full-auto`:

- Uses native `permission: "allow"`.
- Sets `sandbox.enabled` to `false`.
- Sets sandbox network to `allow`.
- Launches with managed `kilo run --auto`.
- Must not enable a hidden sandbox or restrictive plugin path.

`safe`:

- Uses native permission actions `ask` and `deny`.
- Keeps sandbox enabled.
- Denies sandbox network and declares no allowed hosts.
- Does not add `--auto`.
- Does not use permission-bypass flags.

Reject user launch arguments that try to override permission, sandbox, config,
home, workspace, agent, model, session, share, attachment, or bypass controls.
