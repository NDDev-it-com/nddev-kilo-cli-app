# Changelog

## [0.3.0]

- Correct the runtime baseline schema to 3, the public contract version to 3,
  and the build manifest schema to 2.
- Keep release metadata in lockstep at 0.3.0.
- Migration: consumers that validate public metadata must accept baseline and
  contract schema 3 and build manifest schema 2 before upgrading.

## 0.2.0

- Replace setup variants with one `nddev-builder` setup and orthogonal
  `full-auto` and `safe` permission profiles.
- Use isolated exact npm global installation for `@kilocode/cli`.
- Add native local builder plugin, agent files, command files, canonical
  `AGENTS.md`, and progressive-disclosure builder skills.
- Preserve legacy setup state for status, migration, restore, and removal while
  denying legacy launch.

## 0.1.0

- Add target-explicit Kilo Code CLI setup manager.
- Add `safe`, `balanced`, and `full-auto` setup variants.
- Add native `nddev-builder` projection with Kilo agent, skills, and command
  config surfaces.
- Add public contract, manifest, runtime baseline, validator, and shared-CI
  workflow callers.
