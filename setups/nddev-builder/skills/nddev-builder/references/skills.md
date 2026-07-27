# Agent Skills Workflow

Kilo supports Agent Skills from configured `skills.paths`, plus native global
and project skill directories. This setup points `skills.paths` at the
target-owned `skills` directory.

Skill design:

- Use `nddev-builder/SKILL.md` as the routing entry point.
- Keep focused references under `nddev-builder/references/`.
- Add small focused skills only when they reduce routing ambiguity.
- Avoid remote `skills.urls`; they are native but not deterministic for this
  setup.
- Do not copy volatile pins. Reference code-owned baseline and manager files.
