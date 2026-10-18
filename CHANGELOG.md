# Changelog

All notable changes to this project will be documented in this file.

## [0.1.0] — 2026-10-18 – Initial Release

- Special agent `agent_netbird`: reads service users, their personal access tokens, peers, networks, network routers and the instance version from the NetBird REST API (read-only). The API token is read from the Checkmk password store (`--token-id`), never from the command line.
- **NetBird API** service: connectivity, partial failures, remaining lifetime of the agent's own token
- **NetBird Token \<user\>/\<token\>** services: remaining lifetime (WARN 14 days, CRIT 7 days) and unused-since checks, "never used" handling, regex filter at discovery, option to ignore long-expired tokens
- **NetBird Network \<name\>** services: routing-peer availability (OK / WARN / CRIT), group routers resolved to peers, offline peers with age of `last_seen` and expired logins
- **NetBird Management Version** service from `GET /api/instance/version`
- Rulesets: special agent, API / token / management version thresholds, token discovery filter
- Metrics and graphs for token days and routing peers
- Check man pages
