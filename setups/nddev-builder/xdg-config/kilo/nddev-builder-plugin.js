const CONTEXT = [
  "## NDDev Builder Context",
  "- Managed setup: nddev-builder.",
  "- The active permission profile is recorded in NDDEV-KILO-CLI-SETUP.json.",
  "- Use AGENTS.md, instructions/nddev-builder.md, and the nddev-builder Skill toolkit.",
  "- Public content lives in the module; private harness validation and durable memory stay outside this target.",
  "- Do not mutate permissions, sandbox, auth, MCP, provider, network, or plugin configuration from this plugin.",
].join("\n")

const server = async () => ({
  "experimental.session.compacting": async (_input, output) => {
    if (!output || !Array.isArray(output.context)) return
    output.context.push(CONTEXT)
  },
})

export default {
  id: "nddev-builder",
  server,
}
