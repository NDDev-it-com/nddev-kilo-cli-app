# Auth Boundary Workflow

Kilo owns provider authentication and local auth stores. This setup must not
copy, import, export, or back up live provider credentials.

Runtime launch uses sanitized `HOME`, `XDG_CONFIG_HOME`, `XDG_DATA_HOME`,
`XDG_STATE_HOME`, `XDG_CACHE_HOME`, `TMPDIR`, and explicit `KILO_CONFIG`.
Provider API keys, npm tokens, Kilo auth content, cloud tokens, and live config
environment variables are not inherited.

If a user authenticates inside an explicit managed target, that target-owned
state remains user-owned and must not be included in public evidence.
