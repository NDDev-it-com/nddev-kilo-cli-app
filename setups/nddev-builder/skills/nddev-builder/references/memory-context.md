# Memory And Context Workflow

Kilo loads `AGENTS.md` and supports native memory features, but this public
setup does not own live native memory stores.

Use managed `AGENTS.md`, `instructions/nddev-builder.md`, the Skill toolkit, and
the local compaction-context plugin for portable builder context. Root-private
durable memory belongs in the private harness only and must not be copied into
public module targets.
