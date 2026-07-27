# Migration And Validation Workflow

Legacy managed states may be read for status, migrated, restored, or removed.
They must never launch.

Migration rules:

- Legacy `safe` maps to setup `nddev-builder` profile `safe`.
- Legacy `full-auto` maps to setup `nddev-builder` profile `full-auto`.
- Legacy `balanced` has no native target profile; require an explicit safe or
  full-auto profile choice.

Validation rules:

- Run public module validators before committing public changes.
- Keep private executable tests, fixtures, benchmarks, and release evidence in
  the private harness.
- Treat missing runtime proof, package-lock drift, schema drift, unmanaged state
  overwrite, or live-state access as failure conditions.
