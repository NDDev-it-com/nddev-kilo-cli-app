# MCP Boundary Workflow

Kilo supports local and remote MCP configuration through native `mcp.<name>`
entries. This setup does not provision MCP servers and does not ship MCP config.

If future work adds MCP, it must use native Kilo schema, explicit target-owned
commands or URLs, bounded environment variables, no inherited secrets, and
private validation proving server startup boundaries. Until then, MCP remains
absent and unmanaged user MCP config must be preserved or fail closed.
