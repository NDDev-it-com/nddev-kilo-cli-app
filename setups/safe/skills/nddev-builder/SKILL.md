---
name: nddev-builder
description: Build and review NDDev setup-manager modules with explicit target safety and public/private boundary discipline.
---

# NDDev Builder Skill

Use this skill when changing an NDDev setup manager. Keep runtime behavior,
setup catalogs, public contracts, and public documentation in the public
module. Keep private fixtures, tests, benchmarks, and harness lanes in the
matching validation slice.

Never apply a setup to live Kilo state. Use temporary HOME, config, runtime,
target, and backup directories for validation.
