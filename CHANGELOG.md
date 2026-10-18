# Changelog

All notable changes to this project will be documented in this file.

## [0.1.0] — unreleased

- Special agent `agent_netbird`: reads users, personal access tokens, peers, networks and network routers from the NetBird REST API (read-only). The API token is read from the Checkmk password store (`--token-id`), never from the command line.
- **NetBird API** service: connectivity, partial failures, remaining lifetime of the agent's own token
- **NetBird Token \<user\>/\<token\>** services: remaining lifetime and unused-since checks, "never used" handling, regex filter at discovery, option to ignore long-expired tokens
- **NetBird Network \<name\>** services: routing-peer availability (OK / WARN / CRIT), group routers resolved to peers, version check against GitHub latest release / fixed version / highest peer version
- Rulesets: special agent, API / token / network thresholds, token discovery filter
- Metrics and graphs for token days and routing peers
- Check man pages
