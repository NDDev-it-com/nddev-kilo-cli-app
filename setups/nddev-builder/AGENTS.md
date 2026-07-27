# NDDev Kilo Builder Target

This target is managed by `nddev-kilo-cli-app`.

Use the managed `nddev-builder` agent, skills, command files, and local plugin
for Kilo setup-module work. Keep runtime code, public setup payloads, public
contracts, release metadata, and public documentation inside the public module.
Keep private tests, fixtures, benchmarks, validation lanes, root durable memory,
and operational harness skills outside this target.

Do not read or mutate live Kilo state, live npm configuration, provider
credentials, unrelated repositories, generated logs, caches, or private harness
material unless the owner explicitly expands scope.

The active setup and profile are recorded in `NDDEV-KILO-CLI-SETUP.json`; treat
that stamp and the manager source as the code-owned source of volatile facts.
