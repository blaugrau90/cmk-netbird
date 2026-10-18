# Changelog

All notable changes to this project will be documented in this file.

## [0.4.0] — unreleased

- Removed tokens of regular users: only service-user tokens are monitored. The option "Include tokens of regular users" and the `[user]` item prefix are gone.

## [0.3.0] — unreleased

- Removed the version check of routing peers, including the GitHub release lookup and the reference-version option. The agent no longer needs internet access; the management server version is covered by **NetBird Management Version**. The rule "NetBird network" is gone, as it only held version levels.

## [0.2.0] — unreleased

- Token default levels: WARN at 14 days (was 30), CRIT at 7 days; same for the agent's own token
- New service **NetBird Management Version** (own check, own ruleset) based on `GET /api/instance/version`

## [0.1.0] — 2026-10-18

- Special agent `agent_netbird`: reads users, personal access tokens, peers, networks and network routers from the NetBird REST API (read-only). The API token is read from the Checkmk password store (`--token-id`), never from the command line.
- **NetBird API** service: connectivity, partial failures, remaining lifetime of the agent's own token
- **NetBird Token \<user\>/\<token\>** services: remaining lifetime and unused-since checks, "never used" handling, regex filter at discovery, option to ignore long-expired tokens
- **NetBird Network \<name\>** services: routing-peer availability (OK / WARN / CRIT), group routers resolved to peers, version check against GitHub latest release / fixed version / highest peer version
- **NetBird Management Version** service from `GET /api/instance/version`
- Rulesets: special agent, API / token / network thresholds, token discovery filter
- Metrics and graphs for token days and routing peers
- Check man pages
